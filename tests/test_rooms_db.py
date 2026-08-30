"""
rooms 테이블 및 Room 모델 단위 테스트 (ISSUE-26)

검증 대상:
- rooms 테이블이 init_database()에서 생성된다
- Room CRUD (생성/조회/목록/삭제)
- 상태 전이 (waiting → active → inactive → active/closed, 강제 종료)
- 잘못된 전이 거부
- 외래 키 무결성 (foreign_keys=ON, created_by 필수)
- 서버 재시작 복원 (RoomManager.hydrate_from_db)
- WebSocket 인증 시 closed 룸 거부 (RL-006: 일반화된 에러 메시지)
- 무대 설정 stage_config 컬럼 읽기/쓰기 (ISSUE-37, TC-052)

Note: SQLite tmp_path 기반 격리. 외부 I/O 없음.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# websocket_handler -> auth -> streamlit 의존성 mock
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "rooms.db")


@pytest.fixture
def db_manager(db_path):
    from database import DatabaseManager

    return DatabaseManager(db_path)


@pytest.fixture
def admin_user_id(db_manager):
    """rooms.created_by FK 를 만족시키기 위한 관리자 사용자."""
    from database import User

    user_model = User(db_manager)
    return user_model.create_user(
        username="admin1",
        password="pw",
        role="admin",
        usage_limit_seconds=0,
    )


@pytest.fixture
def operator_user_id(db_manager):
    from database import User

    user_model = User(db_manager)
    return user_model.create_user(
        username="op1",
        password="pw",
        role="user",
        usage_limit_seconds=3600,
    )


@pytest.fixture
def room_model(db_manager):
    from database import Room

    return Room(db_manager)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
class TestRoomsSchema:
    """AC: rooms 테이블이 init_database()에서 생성된다."""

    def test_rooms_table_exists(self, db_manager):
        with db_manager.get_connection() as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='rooms'"
            )
            assert cur.fetchone() is not None

    def test_rooms_columns(self, db_manager):
        """기대 컬럼 셋이 모두 존재한다."""
        expected = {
            "id",
            "name",
            "status",
            "input_lang",
            "output_lang",
            "created_by",
            "operator_id",
            "created_at",
            "last_activity",
            "timeout_minutes",
            "closed_at",
        }
        with db_manager.get_connection() as conn:
            cur = conn.execute("PRAGMA table_info(rooms)")
            actual = {row["name"] for row in cur.fetchall()}
        assert expected.issubset(actual), f"missing: {expected - actual}"

    def test_rooms_id_is_text_primary_key(self, db_manager):
        with db_manager.get_connection() as conn:
            cur = conn.execute("PRAGMA table_info(rooms)")
            cols = {row["name"]: dict(row) for row in cur.fetchall()}
        assert cols["id"]["pk"] == 1
        assert cols["id"]["type"].upper() == "TEXT"

    def test_default_values(self, db_manager, admin_user_id):
        """status, input_lang, output_lang, timeout_minutes 기본값."""
        with db_manager.get_connection() as conn:
            conn.execute(
                "INSERT INTO rooms (id, name, created_by) VALUES (?, ?, ?)",
                ("r1", "A홀", admin_user_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT status, input_lang, output_lang, timeout_minutes "
                "FROM rooms WHERE id=?",
                ("r1",),
            ).fetchone()
        assert row["status"] == "waiting"
        assert row["input_lang"] == "auto"
        assert row["output_lang"] == "ko"
        assert row["timeout_minutes"] == 30


class TestRoomsForeignKeys:
    """FK 제약: created_by, operator_id → users.id (PRAGMA foreign_keys=ON)."""

    def test_created_by_fk_rejects_invalid_user(self, db_manager):
        with db_manager.get_connection() as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO rooms (id, name, created_by) VALUES (?, ?, ?)",
                ("r-bad", "X", 99999),
            )
            conn.commit()

    def test_operator_id_fk_rejects_invalid_user(self, db_manager, admin_user_id):
        with db_manager.get_connection() as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO rooms (id, name, created_by, operator_id) "
                "VALUES (?, ?, ?, ?)",
                ("r-bad-op", "X", admin_user_id, 99999),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Room CRUD
# ---------------------------------------------------------------------------
class TestRoomCrud:
    """AC: Room CRUD."""

    def test_create_returns_row(self, room_model, admin_user_id):
        room = room_model.create(
            room_id="r-1",
            name="A홀 기조연설",
            created_by=admin_user_id,
        )
        assert room is not None
        assert room["id"] == "r-1"
        assert room["name"] == "A홀 기조연설"
        assert room["status"] == "waiting"
        assert room["created_by"] == admin_user_id

    def test_create_with_custom_lang_and_timeout(self, room_model, admin_user_id):
        room = room_model.create(
            room_id="r-2",
            name="B홀",
            created_by=admin_user_id,
            input_lang="en",
            output_lang="ko",
            timeout_minutes=10,
        )
        assert room["input_lang"] == "en"
        assert room["output_lang"] == "ko"
        assert room["timeout_minutes"] == 10

    def test_create_duplicate_id_returns_none(self, room_model, admin_user_id):
        room_model.create(room_id="r-dup", name="X", created_by=admin_user_id)
        # IntegrityError on PK collision is mapped to None (mirrors User.create_user)
        result = room_model.create(room_id="r-dup", name="Y", created_by=admin_user_id)
        assert result is None

    def test_get_by_id_returns_room(self, room_model, admin_user_id):
        room_model.create(room_id="r-3", name="C", created_by=admin_user_id)
        room = room_model.get_by_id("r-3")
        assert room is not None
        assert room["id"] == "r-3"

    def test_get_by_id_unknown_returns_none(self, room_model):
        assert room_model.get_by_id("nope") is None

    def test_list_rooms_returns_all(self, room_model, admin_user_id):
        room_model.create(room_id="r-a", name="A", created_by=admin_user_id)
        room_model.create(room_id="r-b", name="B", created_by=admin_user_id)
        ids = {r["id"] for r in room_model.list_all()}
        assert {"r-a", "r-b"}.issubset(ids)

    def test_list_by_status(self, room_model, admin_user_id):
        room_model.create(room_id="r-w1", name="W1", created_by=admin_user_id)
        room_model.create(room_id="r-w2", name="W2", created_by=admin_user_id)
        room_model.transition_status("r-w1", "active")
        active = room_model.list_by_status(["active"])
        ids = {r["id"] for r in active}
        assert "r-w1" in ids
        assert "r-w2" not in ids

    def test_assign_operator(self, room_model, admin_user_id, operator_user_id):
        room_model.create(room_id="r-op", name="OpRoom", created_by=admin_user_id)
        ok = room_model.assign_operator("r-op", operator_user_id)
        assert ok is True
        room = room_model.get_by_id("r-op")
        assert room["operator_id"] == operator_user_id

    # ------------------------------------------------------------------
    # ISSUE-27: list_by_operator (배정된 룸 조회)
    # ------------------------------------------------------------------
    def test_list_by_operator_returns_assigned_rooms(
        self, room_model, admin_user_id, operator_user_id
    ):
        """오퍼레이터에게 배정된 룸만 반환된다 (AC1)."""
        room_model.create(
            room_id="r-mine-1",
            name="MyRoomA",
            created_by=admin_user_id,
            operator_id=operator_user_id,
        )
        room_model.create(
            room_id="r-mine-2",
            name="MyRoomB",
            created_by=admin_user_id,
            operator_id=operator_user_id,
        )
        # 다른 오퍼레이터에게 배정된 룸 (필터링되어야 함)
        room_model.create(
            room_id="r-other",
            name="OtherRoom",
            created_by=admin_user_id,
            operator_id=admin_user_id,
        )
        # 배정되지 않은 룸 (필터링되어야 함)
        room_model.create(
            room_id="r-unassigned",
            name="Unassigned",
            created_by=admin_user_id,
        )

        rooms = room_model.list_by_operator(operator_user_id)
        ids = {r["id"] for r in rooms}
        assert ids == {"r-mine-1", "r-mine-2"}

    def test_list_by_operator_empty_for_unassigned(
        self, room_model, admin_user_id, operator_user_id
    ):
        """배정된 룸이 없는 오퍼레이터는 빈 리스트를 받는다.

        AC3 (배정된 룸이 없습니다 안내 메시지) 분기 진입 조건.
        """
        room_model.create(
            room_id="r-noop",
            name="No Op",
            created_by=admin_user_id,
        )
        rooms = room_model.list_by_operator(operator_user_id)
        assert rooms == []

    def test_list_by_operator_excludes_closed_rooms(
        self, room_model, admin_user_id, operator_user_id
    ):
        """종료된 룸은 시작/정지 대상이 아니므로 결과에서 제외한다."""
        room_model.create(
            room_id="r-active",
            name="Active",
            created_by=admin_user_id,
            operator_id=operator_user_id,
        )
        room_model.create(
            room_id="r-closed",
            name="Closed",
            created_by=admin_user_id,
            operator_id=operator_user_id,
        )
        room_model.transition_status("r-closed", "closed")

        rooms = room_model.list_by_operator(operator_user_id)
        ids = {r["id"] for r in rooms}
        assert ids == {"r-active"}

    def test_list_by_operator_includes_status_field(
        self, room_model, admin_user_id, operator_user_id
    ):
        """반환 dict는 UI 라벨링에 필요한 status 필드를 포함한다 (AC4)."""
        room_model.create(
            room_id="r-st",
            name="With Status",
            created_by=admin_user_id,
            operator_id=operator_user_id,
        )
        rooms = room_model.list_by_operator(operator_user_id)
        assert len(rooms) == 1
        assert "status" in rooms[0]
        assert "name" in rooms[0]
        assert "id" in rooms[0]


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------
class TestRoomStateTransitions:
    """AC: 상태 전이가 다이어그램대로 동작한다.

    waiting → active     (오퍼레이터가 시작)
    active → inactive    (오퍼레이터가 정지)
    inactive → active    (오퍼레이터가 재시작)
    inactive → closed    (관리자 종료)
    active → closed      (관리자 강제 종료)
    waiting → closed     (관리자 삭제)
    """

    @pytest.fixture
    def room_id(self, room_model, admin_user_id):
        room_model.create(room_id="r-trans", name="T", created_by=admin_user_id)
        return "r-trans"

    def test_waiting_to_active(self, room_model, room_id):
        ok = room_model.transition_status(room_id, "active")
        assert ok is True
        assert room_model.get_by_id(room_id)["status"] == "active"

    def test_active_to_inactive(self, room_model, room_id):
        room_model.transition_status(room_id, "active")
        ok = room_model.transition_status(room_id, "inactive")
        assert ok is True
        assert room_model.get_by_id(room_id)["status"] == "inactive"

    def test_inactive_to_active(self, room_model, room_id):
        room_model.transition_status(room_id, "active")
        room_model.transition_status(room_id, "inactive")
        ok = room_model.transition_status(room_id, "active")
        assert ok is True
        assert room_model.get_by_id(room_id)["status"] == "active"

    def test_inactive_to_closed_sets_closed_at(self, room_model, room_id):
        room_model.transition_status(room_id, "active")
        room_model.transition_status(room_id, "inactive")
        ok = room_model.transition_status(room_id, "closed")
        assert ok is True
        room = room_model.get_by_id(room_id)
        assert room["status"] == "closed"
        assert room["closed_at"] is not None

    def test_active_to_closed(self, room_model, room_id):
        room_model.transition_status(room_id, "active")
        ok = room_model.transition_status(room_id, "closed")
        assert ok is True
        assert room_model.get_by_id(room_id)["status"] == "closed"

    def test_waiting_to_closed(self, room_model, room_id):
        ok = room_model.transition_status(room_id, "closed")
        assert ok is True
        assert room_model.get_by_id(room_id)["status"] == "closed"

    @pytest.mark.parametrize(
        "src,dst",
        [
            ("waiting", "inactive"),  # 비허용
            ("closed", "active"),  # 종료된 룸 재활성 금지
            ("closed", "inactive"),
            ("closed", "waiting"),
            ("active", "waiting"),  # 역방향 금지
        ],
    )
    def test_invalid_transitions_rejected(self, room_model, room_id, src, dst):
        from database import InvalidRoomTransition

        # Force room into 'src' state by walking valid transitions first
        if src == "active":
            room_model.transition_status(room_id, "active")
        elif src == "closed":
            room_model.transition_status(room_id, "closed")
        # waiting is the default
        with pytest.raises(InvalidRoomTransition):
            room_model.transition_status(room_id, dst)

    def test_transition_unknown_room_returns_false(self, room_model):
        assert room_model.transition_status("ghost", "active") is False

    def test_transition_unknown_target_status_raises(self, room_model, room_id):
        from database import InvalidRoomTransition

        with pytest.raises(InvalidRoomTransition):
            room_model.transition_status(room_id, "exploded")

    def test_touch_updates_last_activity(self, room_model, room_id):
        room_model.transition_status(room_id, "active")
        before = room_model.get_by_id(room_id)["last_activity"]
        # Force a measurable delta even if SQLite resolution is 1s
        time.sleep(1.05)
        room_model.touch(room_id)
        after = room_model.get_by_id(room_id)["last_activity"]
        assert before is None or after >= before
        assert after is not None


# ---------------------------------------------------------------------------
# Server-restart restore
# ---------------------------------------------------------------------------
class TestRoomManagerHydrate:
    """AC: 서버 재시작 시 DB의 active/inactive 룸이 RoomManager에 복원된다."""

    def test_hydrate_loads_active_and_inactive(
        self, db_manager, room_model, admin_user_id
    ):
        from room_manager import RoomManager

        room_model.create(room_id="r-act", name="A", created_by=admin_user_id)
        room_model.transition_status("r-act", "active")
        room_model.create(room_id="r-ina", name="I", created_by=admin_user_id)
        room_model.transition_status("r-ina", "active")
        room_model.transition_status("r-ina", "inactive")
        room_model.create(room_id="r-cld", name="C", created_by=admin_user_id)
        room_model.transition_status("r-cld", "closed")
        # waiting room SHOULD also be restored so admins can launch them
        room_model.create(room_id="r-wait", name="W", created_by=admin_user_id)

        rm = RoomManager(room_repository=room_model)
        rm.hydrate_from_db()

        ids = set(rm.list_rooms())
        assert "r-act" in ids
        assert "r-ina" in ids
        assert "r-wait" in ids
        # closed rooms must NOT be hydrated
        assert "r-cld" not in ids

    def test_hydrate_with_no_repo_is_noop(self):
        from room_manager import RoomManager

        rm = RoomManager()  # no repo
        # should not raise
        rm.hydrate_from_db()
        assert rm.list_rooms() == []


# ---------------------------------------------------------------------------
# RoomManager DB integration
# ---------------------------------------------------------------------------
class TestRoomManagerPersistence:
    """RoomManager가 create/close 시 DB에 영속화한다."""

    def test_create_room_persists_when_repo_attached(
        self, db_manager, room_model, admin_user_id
    ):
        from room_manager import RoomManager

        rm = RoomManager(room_repository=room_model)
        rm.create_room(
            room_id="r-persist",
            name="Persist",
            created_by=admin_user_id,
        )
        # In-memory
        assert rm.get_room("r-persist") is not None
        # Persisted
        row = room_model.get_by_id("r-persist")
        assert row is not None
        assert row["name"] == "Persist"
        assert row["created_by"] == admin_user_id

    def test_close_room_persists_status(self, db_manager, room_model, admin_user_id):
        from room_manager import RoomManager

        rm = RoomManager(room_repository=room_model)
        rm.create_room(room_id="r-close", name="X", created_by=admin_user_id)
        rm.close_room("r-close")
        # In-memory removal (no longer accepting connections)
        assert rm.get_room("r-close") is None
        # DB shows closed
        row = room_model.get_by_id("r-close")
        assert row["status"] == "closed"
        assert row["closed_at"] is not None

    def test_create_room_without_repo_still_in_memory(self):
        """No regression: legacy callers without DB repo still work."""
        from room_manager import RoomManager

        rm = RoomManager()
        room = rm.create_room()
        assert rm.get_room(room.room_id) is room


# ---------------------------------------------------------------------------
# WebSocket auth: closed-room rejection (RL-006)
# ---------------------------------------------------------------------------
class TestWebSocketAuthClosedRoom:
    """AC: closed 상태의 룸은 WebSocket 접속이 거부된다 (generic message)."""

    def _make_websocket(self, msg):
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps(msg))
        ws.send = AsyncMock()
        return ws

    def _sent(self, ws):
        return [json.loads(c.args[0]) for c in ws.send.call_args_list]

    def test_closed_room_auth_rejected_with_generic_message(
        self, db_manager, room_model, admin_user_id
    ):
        """RL-002 (DB 검증) + RL-006 (메시지 누설 금지)."""
        import websocket_handler
        from database import User
        from room_manager import RoomManager

        # Create a real validated user
        user_model = User(db_manager)
        user_model.create_user(
            username="op-ws",
            password="pw",
            role="user",
            usage_limit_seconds=3600,
        )
        user_row = user_model.get_user_by_username("op-ws")

        # Create a room and close it (simulating room shut down)
        rm = RoomManager(room_repository=room_model)
        rm.create_room(
            room_id="r-closed-ws",
            name="X",
            created_by=admin_user_id,
        )
        rm.close_room("r-closed-ws")

        ws = self._make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": user_row["id"],
                    "username": "op-ws",
                    "role": "user",
                },
                "room_id": "r-closed-ws",
            }
        )

        with (
            patch.object(websocket_handler, "_room_manager", rm),
            patch("websocket_handler.get_user_model", return_value=user_model),
        ):
            result = asyncio.run(websocket_handler._authenticate_client(ws))

        assert result is None
        sent = self._sent(ws)
        # No auth_success sent
        assert all(m.get("type") != "auth_success" for m in sent)
        # Generic error (RL-006: must NOT reveal "closed" or stack details)
        errors = [m for m in sent if m.get("type") == "auth_error"]
        assert errors, "expected at least one auth_error"
        for err in errors:
            msg = err.get("message", "")
            # Must be generic — no internal status leak
            assert "closed" not in msg.lower()
            assert "exception" not in msg.lower()
            assert "traceback" not in msg.lower()


# ---------------------------------------------------------------------------
# Stage config (ISSUE-37) — TC-052
# ---------------------------------------------------------------------------
@pytest.fixture
def stage_room(room_model, admin_user_id):
    """stage_config 테스트용 룸 하나."""
    room_model.create(room_id="r-stage", name="A홀", created_by=admin_user_id)
    return "r-stage"


def _raw_stage_config(db_manager, room_id):
    """DB 에 저장된 stage_config 원본 문자열을 그대로 읽는다."""
    with db_manager.get_connection() as conn:
        row = conn.execute(
            "SELECT stage_config FROM rooms WHERE id = ?", (room_id,)
        ).fetchone()
    return row["stage_config"] if row else None


def _write_raw_stage_config(db_manager, room_id, value):
    """stage_config 컬럼에 임의의 문자열을 직접 쓴다.

    update_stage_config 는 정규화 후 저장하므로 '정규화되지 않은 블롭' 상태를
    만들 수 없다 — 구버전 빌드/수동 DB 수정/백업 복원이 만들어 내는 실제
    상황을 재현하려면 SQL 로 직접 넣어야 한다.
    """
    with db_manager.get_connection() as conn:
        conn.execute("UPDATE rooms SET stage_config = ? WHERE id = ?", (value, room_id))
        conn.commit()


_VALID_CONFIG = {
    "event_title": "2026 개발자 콘퍼런스 🎉",
    "event_subtitle": "A홀 기조연설",
    "caption_ratio": "1/3",
    "logo_groups": [
        {"label": "주최", "assets": ["host-1.png"]},
        {"label": "주관", "assets": []},
        {"label": "후원", "assets": ["sponsor-a.svg", "sponsor-b.png"]},
    ],
}


class TestRoomStageConfigSchema:
    """AC: stage_config 컬럼이 존재하고 기본값이 '{}' 이다."""

    def test_column_exists(self, db_manager):
        with db_manager.get_connection() as conn:
            cur = conn.execute("PRAGMA table_info(rooms)")
            cols = {row["name"]: dict(row) for row in cur.fetchall()}
        assert "stage_config" in cols
        assert cols["stage_config"]["type"].upper() == "TEXT"
        assert cols["stage_config"]["notnull"] == 1

    def test_new_room_row_defaults_to_empty_json_object(self, room_model, stage_room):
        room = room_model.get_by_id(stage_room)
        assert room["stage_config"] == "{}"


class TestRoomGetStageConfig:
    """AC: NULL / 빈 문자열 / 깨진 JSON → 예외 없이 기본값."""

    def _write_raw(self, db_manager, room_id, value):
        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE rooms SET stage_config = ? WHERE id = ?", (value, room_id)
            )
            conn.commit()

    def test_default_column_value_returns_default_config(self, room_model, stage_room):
        from stage_config import DEFAULT_STAGE_CONFIG

        assert room_model.get_stage_config(stage_room) == DEFAULT_STAGE_CONFIG

    @pytest.mark.parametrize(
        "stored",
        ["", "   ", "{", "[]", "not json"],
        ids=["empty", "whitespace", "broken", "array", "plain-text"],
    )
    def test_unusable_values_degrade_to_default(
        self, db_manager, room_model, stage_room, stored
    ):
        from stage_config import DEFAULT_STAGE_CONFIG

        self._write_raw(db_manager, stage_room, stored)
        assert room_model.get_stage_config(stage_room) == DEFAULT_STAGE_CONFIG

    def test_column_rejects_null(self, db_manager, stage_room):
        """NOT NULL 제약 — NULL 인 룸 자체가 존재할 수 없다.

        AC 가 말하는 "NULL 인 룸" 은 이 제약 덕분에 도달 불가능한 상태이며,
        get_stage_config 의 None 경로는 '존재하지 않는 room_id' 케이스가
        커버한다 (degrade 가 아니라 애초에 막는 쪽이 더 강한 보장이다).
        """
        with pytest.raises(sqlite3.IntegrityError):
            with db_manager.get_connection() as conn:
                conn.execute(
                    "UPDATE rooms SET stage_config = NULL WHERE id = ?", (stage_room,)
                )
                conn.commit()

    def test_broken_json_logs_to_stdout_only(
        self, db_manager, room_model, stage_room, capsys
    ):
        """RL-006: 파싱 실패는 서버 로그로만 남고 예외로 전파되지 않는다."""
        self._write_raw(db_manager, stage_room, '{"event_title": ')
        result = room_model.get_stage_config(stage_room)

        from stage_config import DEFAULT_STAGE_CONFIG

        assert result == DEFAULT_STAGE_CONFIG
        captured = capsys.readouterr().out
        assert "stage_config parse failed" in captured
        assert stage_room in captured

    def test_unknown_room_returns_default_without_raising(self, room_model):
        from stage_config import DEFAULT_STAGE_CONFIG

        assert room_model.get_stage_config("no-such-room") == DEFAULT_STAGE_CONFIG

    def test_deeply_nested_json_degrades_without_raising(
        self, db_manager, room_model, stage_room, capsys
    ):
        """깊게 중첩된 컬럼 값이 무대 페이지를 죽이지 않는다.

        json.loads 가 RecursionError 를 던지는 입력. update_stage_config 는
        정규화 후 저장하므로 이 값을 만들지 않지만, 백업 복원·직접 SQL·향후
        임포트 경로로 컬럼에 들어올 수 있다. 읽기 경로는 어떤 경우에도
        degrade 해야 한다.
        """
        from stage_config import DEFAULT_STAGE_CONFIG

        self._write_raw(db_manager, stage_room, "[" * 100_000)
        assert room_model.get_stage_config(stage_room) == DEFAULT_STAGE_CONFIG
        assert "stage_config parse failed" in capsys.readouterr().out


class TestRoomGetRawStageConfig:
    """정규화 **전** 블롭 접근자 (ISSUE-37 리뷰 F-5).

    관리자 폼은 읽은 설정을 정규화된 형태로 다시 저장한다 — 원본에만 있던
    값(알 수 없는 그룹 라벨, 문자열이 아닌 에셋)은 저장 한 번으로 영구히
    사라진다. 그 왕복 전에 무엇이 사라질지 경고하려면 원본을 읽을 수 있어야
    한다. get_stage_config 와 같은 계약으로 절대 예외를 던지지 않는다.
    """

    def test_returns_values_that_get_stage_config_silently_drops(
        self, db_manager, room_model, stage_room
    ):
        """같은 룸을 두 접근자로 읽어 **차이 자체**를 단언한다 (RL-004)."""
        blob = {
            "event_title": "타이틀",
            "event_subtitle": "부제",
            "caption_ratio": "1/3",
            "logo_groups": [
                {"label": "주최", "assets": ["a.png", 12345]},
                {"label": "협찬", "assets": ["ghost.png"]},
            ],
        }
        _write_raw_stage_config(
            db_manager, stage_room, json.dumps(blob, ensure_ascii=False)
        )

        raw = room_model.get_raw_stage_config(stage_room)
        normalized = room_model.get_stage_config(stage_room)

        # 원본은 손대지 않은 그대로여야 한다.
        assert raw == blob
        assert [g["label"] for g in raw["logo_groups"]] == ["주최", "협찬"]

        # 정규화 쪽에서는 조용히 사라진다 — 이 차이가 F-5 경고의 근거다.
        assert [g["label"] for g in normalized["logo_groups"]] == [
            "주최",
            "주관",
            "후원",
        ]
        assert normalized["logo_groups"][0]["assets"] == ["a.png"]
        assert "ghost.png" not in json.dumps(normalized, ensure_ascii=False)

    def test_default_column_value_returns_empty_mapping(self, room_model, stage_room):
        """기본값 '{}' 은 유효한 JSON — None 이 아니라 빈 dict 로 온다."""
        assert room_model.get_raw_stage_config(stage_room) == {}

    @pytest.mark.parametrize("stored", ["", "   "], ids=["empty", "whitespace"])
    def test_empty_column_returns_none(
        self, db_manager, room_model, stage_room, stored
    ):
        _write_raw_stage_config(db_manager, stage_room, stored)
        assert room_model.get_raw_stage_config(stage_room) is None

    def test_unknown_room_returns_none(self, room_model):
        assert room_model.get_raw_stage_config("no-such-room") is None

    def test_broken_json_returns_none_and_logs_to_stdout_only(
        self, db_manager, room_model, stage_room, capsys
    ):
        """RL-006: 파싱 실패는 서버 로그로만 남고 예외로 전파되지 않는다."""
        _write_raw_stage_config(db_manager, stage_room, "{")
        assert room_model.get_raw_stage_config(stage_room) is None
        captured = capsys.readouterr().out
        assert "stage_config parse failed" in captured
        assert stage_room in captured

    def test_deeply_nested_json_returns_none_without_raising(
        self, db_manager, room_model, stage_room, capsys
    ):
        """RecursionError 도 잡아야 관리자 페이지가 로드 중에 죽지 않는다."""
        _write_raw_stage_config(db_manager, stage_room, "[" * 100_000)
        assert room_model.get_raw_stage_config(stage_room) is None
        assert "stage_config parse failed" in capsys.readouterr().out

    def test_json_array_is_returned_as_is(self, db_manager, room_model, stage_room):
        """dict 가 아닌 블롭도 그대로 준다 — 해석은 호출자 몫이다."""
        _write_raw_stage_config(db_manager, stage_room, "[]")
        assert room_model.get_raw_stage_config(stage_room) == []

    def test_read_does_not_modify_the_stored_blob(
        self, db_manager, room_model, stage_room
    ):
        """읽기 전용 — 접근자가 컬럼을 정규화해서 덮어쓰면 안 된다."""
        stored = '{"logo_groups": [{"label": "협찬", "assets": ["x.png"]}]}'
        _write_raw_stage_config(db_manager, stage_room, stored)

        room_model.get_raw_stage_config(stage_room)
        room_model.get_stage_config(stage_room)

        assert _raw_stage_config(db_manager, stage_room) == stored


class TestRoomUpdateStageConfig:
    """AC: 검증 통과 시에만 저장되며, 왕복 시 값이 정확히 보존된다."""

    def test_round_trip_preserves_korean_and_emoji(self, room_model, stage_room):
        """TC-052 — 원본 dict 와 정확히 동일해야 한다 (RL-004)."""
        assert room_model.update_stage_config(stage_room, _VALID_CONFIG) is True
        assert room_model.get_stage_config(stage_room) == _VALID_CONFIG

    def test_stored_json_is_not_ascii_escaped(self, db_manager, room_model, stage_room):
        """ensure_ascii=False — 한글이 \\uXXXX 로 이스케이프되지 않는다."""
        room_model.update_stage_config(stage_room, _VALID_CONFIG)
        stored = _raw_stage_config(db_manager, stage_room)
        assert "2026 개발자 콘퍼런스 🎉" in stored
        assert "\\u" not in stored
        assert json.loads(stored) == _VALID_CONFIG

    def test_invalid_ratio_rejected_and_row_untouched(
        self, db_manager, room_model, stage_room
    ):
        """AC: caption_ratio='1/2' → False, DB 값 불변."""
        room_model.update_stage_config(stage_room, _VALID_CONFIG)
        before = _raw_stage_config(db_manager, stage_room)

        bad = dict(_VALID_CONFIG, caption_ratio="1/2")
        assert room_model.update_stage_config(stage_room, bad) is False

        assert _raw_stage_config(db_manager, stage_room) == before
        assert room_model.get_stage_config(stage_room) == _VALID_CONFIG

    def test_too_long_title_rejected_and_row_untouched(
        self, db_manager, room_model, stage_room
    ):
        """AC: event_title 121자 → False, DB 값 불변."""
        room_model.update_stage_config(stage_room, _VALID_CONFIG)
        before = _raw_stage_config(db_manager, stage_room)

        bad = dict(_VALID_CONFIG, event_title="가" * 121)
        assert room_model.update_stage_config(stage_room, bad) is False
        assert _raw_stage_config(db_manager, stage_room) == before

    def test_unknown_room_returns_false_without_raising(self, room_model):
        assert room_model.update_stage_config("no-such-room", _VALID_CONFIG) is False

    def test_unknown_labels_are_normalised_before_persisting(
        self, room_model, stage_room
    ):
        """AC7: 알 수 없는 라벨/비-dict 원소는 저장 전에 폐기된다."""
        messy = dict(
            _VALID_CONFIG,
            logo_groups=[
                "문자열",
                {"label": "협찬", "assets": ["ghost.png"]},
                {"label": "후원", "assets": ["sponsor-a.svg"]},
            ],
        )
        assert room_model.update_stage_config(stage_room, messy) is True

        stored = room_model.get_stage_config(stage_room)
        assert [g["label"] for g in stored["logo_groups"]] == ["주최", "주관", "후원"]
        assert stored["logo_groups"][2]["assets"] == ["sponsor-a.svg"]
        assert stored["logo_groups"][0]["assets"] == []

    def test_update_does_not_touch_other_rooms(
        self, room_model, admin_user_id, stage_room
    ):
        room_model.create(room_id="r-other", name="B홀", created_by=admin_user_id)
        room_model.update_stage_config(stage_room, _VALID_CONFIG)

        from stage_config import DEFAULT_STAGE_CONFIG

        assert room_model.get_stage_config("r-other") == DEFAULT_STAGE_CONFIG


# ---------------------------------------------------------------------------
# 세션 언어 기록 — Room.update_session_languages (ISSUE-52, FR-083)
# TC-071 / TC-072
# ---------------------------------------------------------------------------
class _SqlSpyDb:
    """DatabaseManager 래퍼 — 실제로 실행된 SQL 문을 수집한다.

    ``sqlite3.Connection.set_trace_callback`` 은 **실행된 문** 만 부르므로
    "UPDATE 를 0회 실행했다" 를 헬퍼 호출 횟수가 아니라 SQL 수준에서 못 박을
    수 있다 (TC-072). 메서드 호출을 세는 방식은 no-op 가드를 지우고 UPDATE 를
    매번 돌려도 통과하므로 이 이슈에는 쓸 수 없다.
    """

    def __init__(self, inner):
        self._inner = inner
        self.statements: list[str] = []

    @contextlib.contextmanager
    def get_connection(self):
        with self._inner.get_connection() as conn:
            conn.set_trace_callback(self.statements.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)

    @property
    def updates(self) -> list[str]:
        return [s for s in self.statements if s.lstrip().upper().startswith("UPDATE")]


def _lang_columns(db_manager, room_id: str) -> dict[str, str]:
    """언어 관련 4개 컬럼을 DB 에서 **원시값 그대로** 읽는다.

    ``output_langs`` 는 정규화를 거치지 않은 원본 문자열이어야 한다 — 이
    이슈의 핵심 가드가 "바이트 단위로 그대로" 이기 때문.
    """
    with db_manager.get_connection() as conn:
        row = conn.execute(
            "SELECT input_lang, output_lang, primary_output_lang, output_langs "
            "FROM rooms WHERE id = ?",
            (room_id,),
        ).fetchone()
    return dict(row)


@pytest.fixture
def lang_room(room_model, admin_user_id):
    """input_lang='auto', output_lang='ko', primary_output_lang='ko',
    output_langs='["ko"]' 인 사고 재현 룸 (이슈 본문의 실측 행)."""
    room_model.create(room_id="lang-room", name="A홀", created_by=admin_user_id)
    return "lang-room"


class TestUpdateSessionLanguages:
    """오퍼레이터 세션 언어를 rooms 행에 기록한다 (TC-071)."""

    def test_round_trip_writes_all_three_columns(
        self, room_model, db_manager, lang_room
    ):
        """AC1/AC3: ('ko','vi') 기록 후 세 컬럼이 **정확히** 그 값이다."""
        before = _lang_columns(db_manager, lang_room)
        assert before["input_lang"] == "auto"
        assert before["output_lang"] == "ko"
        assert before["primary_output_lang"] == "ko"

        result = room_model.update_session_languages(
            lang_room, input_lang="ko", output_lang="vi"
        )
        assert result is True

        row = room_model.get_by_id(lang_room)
        assert row["input_lang"] == "ko"
        assert row["output_lang"] == "vi"
        assert row["primary_output_lang"] == "vi"

    def test_output_langs_is_byte_identical_after_the_write(
        self, room_model, db_manager, lang_room
    ):
        """AC5: output_langs 는 **손대지 않는다** (#91/#92 재결합 방지).

        `_supported_output_langs` 가 이 컬럼을 의도적으로 무시하므로, 언어
        기록이 "김에 같이 동기화" 하는 순간 뷰어 언어 선택이 룸 설정에 다시
        묶인다. 이 단언이 그 회귀를 잡는 유일한 그물이다 (RL-004).
        """
        before = _lang_columns(db_manager, lang_room)
        assert before["output_langs"] == '["ko"]'

        assert (
            room_model.update_session_languages(
                lang_room, input_lang="ko", output_lang="vi"
            )
            is True
        )

        after = _lang_columns(db_manager, lang_room)
        assert after["output_langs"] == before["output_langs"]
        assert after["output_langs"] == '["ko"]'

    def test_output_langs_survives_when_it_disagrees_with_the_new_lang(
        self, room_model, db_manager, lang_room
    ):
        """기록 언어가 output_langs 에 **없어도** 컬럼은 그대로다.

        공백까지 포함해 바이트 단위로 비교한다 — 재직렬화(`json.dumps`)로
        조용히 정규화되는 경우도 실패시키기 위함.
        """
        stored = '["ko", "en"]'
        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE rooms SET output_langs = ? WHERE id = ?", (stored, lang_room)
            )
            conn.commit()

        assert (
            room_model.update_session_languages(
                lang_room, input_lang="ko", output_lang="vi"
            )
            is True
        )

        assert _lang_columns(db_manager, lang_room)["output_langs"] == stored

    def test_auto_is_a_valid_input_lang(self, room_model, db_manager, lang_room):
        """input 화이트리스트는 출력용과 다르다 — 'auto' 가 정당한 값이다.

        SUPPORTED_OUTPUT_LANGS 를 입력에도 재사용하면 이 테스트가 깨진다.
        """
        assert (
            room_model.update_session_languages(
                lang_room, input_lang="auto", output_lang="en"
            )
            is True
        )

        row = _lang_columns(db_manager, lang_room)
        assert row["input_lang"] == "auto"
        assert row["output_lang"] == "en"
        assert row["primary_output_lang"] == "en"

    def test_unsupported_output_lang_changes_nothing(
        self, room_model, db_manager, lang_room
    ):
        """AC4: 지원 목록 밖 코드는 False + 컬럼 전부 불변, 예외 없음."""
        before = _lang_columns(db_manager, lang_room)

        assert (
            room_model.update_session_languages(
                lang_room, input_lang="ko", output_lang="xx"
            )
            is False
        )

        assert _lang_columns(db_manager, lang_room) == before

    def test_retired_ja_is_not_a_supported_output_lang(
        self, room_model, db_manager, lang_room
    ):
        """'ja' 는 #111 에서 제거됐다 — 리터럴 복사본이면 통과해 버린다.

        검증 목록을 translation.SUPPORTED_OUTPUT_LANGS 에서 import 하지 않고
        손으로 베끼면 옛 목록이 남아 이 테스트가 실패한다 (RL-001).
        """
        from translation import SUPPORTED_OUTPUT_LANGS

        assert "ja" not in SUPPORTED_OUTPUT_LANGS

        before = _lang_columns(db_manager, lang_room)
        assert (
            room_model.update_session_languages(
                lang_room, input_lang="ko", output_lang="ja"
            )
            is False
        )
        assert _lang_columns(db_manager, lang_room) == before

    def test_every_supported_output_lang_is_accepted(
        self, room_model, db_manager, lang_room
    ):
        """화이트리스트가 지나치게 좁아지는 반대 방향의 회귀도 막는다."""
        from translation import SUPPORTED_OUTPUT_LANGS

        for lang in SUPPORTED_OUTPUT_LANGS:
            assert (
                room_model.update_session_languages(
                    lang_room, input_lang="auto", output_lang=lang
                )
                is True
            ), lang
            assert _lang_columns(db_manager, lang_room)["primary_output_lang"] == lang

    def test_unsupported_input_lang_changes_nothing(
        self, room_model, db_manager, lang_room
    ):
        """입력 언어 검증도 출력과 같은 정책 — 거절 시 DB 를 건드리지 않는다."""
        before = _lang_columns(db_manager, lang_room)

        assert (
            room_model.update_session_languages(
                lang_room, input_lang="xx", output_lang="vi"
            )
            is False
        )

        assert _lang_columns(db_manager, lang_room) == before

    def test_unknown_room_id_returns_false_without_raising(self, room_model):
        """AC9: 존재하지 않는 room_id 는 False, 예외 없음."""
        assert (
            room_model.update_session_languages(
                "no-such-room", input_lang="ko", output_lang="vi"
            )
            is False
        )

    def test_write_does_not_touch_last_activity(
        self, room_model, db_manager, lang_room
    ):
        """언어 기록은 last_activity 를 갱신하지 않는다 (부수 효과 없음)."""
        sentinel = "2000-01-01 00:00:00"
        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE rooms SET last_activity = ? WHERE id = ?", (sentinel, lang_room)
            )
            conn.commit()

        assert (
            room_model.update_session_languages(
                lang_room, input_lang="ko", output_lang="vi"
            )
            is True
        )

        with db_manager.get_connection() as conn:
            row = conn.execute(
                "SELECT last_activity FROM rooms WHERE id = ?", (lang_room,)
            ).fetchone()
        assert row["last_activity"] == sentinel

    def test_does_not_touch_other_rooms(self, room_model, db_manager, admin_user_id):
        room_model.create(room_id="r-a", name="A홀", created_by=admin_user_id)
        room_model.create(room_id="r-b", name="B홀", created_by=admin_user_id)

        assert (
            room_model.update_session_languages(
                "r-a", input_lang="ko", output_lang="vi"
            )
            is True
        )

        other = _lang_columns(db_manager, "r-b")
        assert other["primary_output_lang"] == "ko"
        assert other["output_lang"] == "ko"
        assert other["input_lang"] == "auto"


class TestUpdateSessionLanguagesNoOpGuard:
    """AC7: 같은 값이 반복돼도 UPDATE 는 돌지 않는다 (TC-072).

    재접속마다 쓰기가 도는 것을 막는다.
    """

    def test_second_identical_call_issues_zero_update_statements(
        self, db_manager, lang_room
    ):
        from database import Room

        spy = _SqlSpyDb(db_manager)
        model = Room(spy)

        assert (
            model.update_session_languages(lang_room, input_lang="ko", output_lang="vi")
            is True
        )
        assert len(spy.updates) == 1

        spy.statements.clear()
        assert (
            model.update_session_languages(lang_room, input_lang="ko", output_lang="vi")
            is True
        )
        assert spy.updates == []

        # 값이 **바뀌면** 다시 써야 한다 — 항상 no-op 인 구현을 배제한다.
        spy.statements.clear()
        assert (
            model.update_session_languages(lang_room, input_lang="ko", output_lang="en")
            is True
        )
        assert len(spy.updates) == 1
        assert _lang_columns(db_manager, lang_room)["primary_output_lang"] == "en"

    def test_no_op_guard_still_repairs_a_drifted_mirror_column(
        self, db_manager, lang_room
    ):
        """primary_output_lang 만 vi 이고 output_lang 이 ko 로 어긋난 행.

        "요청 언어 == primary_output_lang" 만 보고 스킵하면 두 컬럼이 서로
        다른 값으로 굳는다 — 이 이슈가 고치려는 결함과 같은 모양(RL-025).
        """
        from database import Room

        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE rooms SET primary_output_lang = 'vi' WHERE id = ?",
                (lang_room,),
            )
            conn.commit()

        spy = _SqlSpyDb(db_manager)
        model = Room(spy)
        assert (
            model.update_session_languages(lang_room, input_lang="ko", output_lang="vi")
            is True
        )
        assert len(spy.updates) == 1

        row = _lang_columns(db_manager, lang_room)
        assert row["output_lang"] == "vi"
        assert row["primary_output_lang"] == "vi"
        assert row["input_lang"] == "ko"

    def test_rejected_value_issues_zero_update_statements(self, db_manager, lang_room):
        """검증 실패도 DB 를 건드리지 않는다 — SQL 수준 확인."""
        from database import Room

        spy = _SqlSpyDb(db_manager)
        model = Room(spy)

        assert (
            model.update_session_languages(lang_room, input_lang="ko", output_lang="xx")
            is False
        )
        assert spy.updates == []

    def test_unknown_room_id_issues_zero_update_statements(self, db_manager):
        """AC9: 존재하지 않는 룸에는 UPDATE 를 **시도조차** 하지 않는다.

        반환값만 단언하면 `get_by_id` 조기 반환을 지워도 통과한다 — 0행짜리
        UPDATE 가 돌아 rowcount 0 으로 같은 False 가 나오기 때문이다.
        """
        from database import Room

        spy = _SqlSpyDb(db_manager)
        model = Room(spy)

        assert (
            model.update_session_languages(
                "no-such-room", input_lang="ko", output_lang="vi"
            )
            is False
        )
        assert spy.updates == []


class TestSessionLanguageWhitelistsTrackTheirSources:
    """화이트리스트가 출처와 갈라지면 언어 기록이 조용히 멈춘다 (RL-001).

    `SUPPORTED_OUTPUT_LANGS` 는 import 로 묶여 있지만 입력 목록은 오퍼레이터
    드롭다운(`components/webrtc.html` 의 `#selInputLang`)을 손으로 옮겨 적은
    것이다. 거기에 옵션이 하나 추가되면 그 언어로 도는 세션은 전부 거절되어
    (`update_session_languages` → False) 무대가 이전 언어를 계속 구독한다 —
    로그 한 줄 말고는 증상이 없는, 이 이슈가 고친 결함과 똑같은 모양이다.
    """

    @staticmethod
    def _input_lang_options() -> list[str]:
        """webrtc.html 의 #selInputLang <option value=...> 값을 뽑는다."""
        import re
        from pathlib import Path

        html = (Path(__file__).parent.parent / "components" / "webrtc.html").read_text(
            encoding="utf-8"
        )
        block = re.search(
            r'<select id="selInputLang">(.*?)</select>', html, re.S | re.I
        )
        assert block is not None, "webrtc.html 에서 #selInputLang 를 찾지 못했다"
        return re.findall(r'<option value="([^"]+)"', block.group(1))

    def test_input_whitelist_matches_the_operator_dropdown(self):
        from database import _SESSION_INPUT_LANGS

        assert set(self._input_lang_options()) == set(_SESSION_INPUT_LANGS)

    def test_every_operator_input_option_is_actually_accepted(
        self, room_model, db_manager, lang_room
    ):
        """집합 비교만으로는 부족하다 — 실제로 기록되는지 값으로 확인한다."""
        for lang in self._input_lang_options():
            assert (
                room_model.update_session_languages(
                    lang_room, input_lang=lang, output_lang="vi"
                )
                is True
            ), lang
            assert _lang_columns(db_manager, lang_room)["input_lang"] == lang

    def test_output_whitelist_matches_the_operator_dropdown(self):
        """출력 드롭다운도 같은 계약이다 (#111 에서 en 추가/ja 제거로 싱크)."""
        import re
        from pathlib import Path

        from translation import SUPPORTED_OUTPUT_LANGS

        html = (Path(__file__).parent.parent / "components" / "webrtc.html").read_text(
            encoding="utf-8"
        )
        block = re.search(
            r'<select id="selOutputLang">(.*?)</select>', html, re.S | re.I
        )
        assert block is not None
        options = re.findall(r'<option value="([^"]+)"', block.group(1))
        assert set(options) == set(SUPPORTED_OUTPUT_LANGS)
