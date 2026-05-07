"""
rooms 테이블 및 Room 모델 단위 테스트 (ISSUE-26)

검증 대상:
- rooms 테이블이 init_database()에서 생성된다
- Room CRUD (생성/조회/목록/삭제)
- 상태 전이 (waiting → active → inactive → active/closed, 강제 종료)
- 잘못된 전이 거부
- 타임아웃 정리 (last_activity 30분 이상 미갱신 → closed)
- 외래 키 무결성 (foreign_keys=ON, created_by 필수)
- 서버 재시작 복원 (RoomManager.hydrate_from_db)
- WebSocket 인증 시 closed 룸 거부 (RL-006: 일반화된 에러 메시지)

Note: SQLite tmp_path 기반 격리. 외부 I/O 없음.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
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
    inactive → closed    (타임아웃 또는 관리자 종료)
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
# Timeout cleanup
# ---------------------------------------------------------------------------
class TestRoomTimeoutCleanup:
    """AC: 30분간 last_activity 미갱신 비활성 룸이 자동으로 closed 처리된다."""

    def _backdate_last_activity(self, db_manager, room_id, minutes_ago):
        ts = (datetime.utcnow() - timedelta(minutes=minutes_ago)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE rooms SET last_activity = ? WHERE id = ?",
                (ts, room_id),
            )
            conn.commit()

    def test_cleanup_closes_inactive_room_past_timeout(
        self, room_model, db_manager, admin_user_id
    ):
        room_model.create(
            room_id="r-stale",
            name="stale",
            created_by=admin_user_id,
            timeout_minutes=30,
        )
        room_model.transition_status("r-stale", "active")
        room_model.transition_status("r-stale", "inactive")
        self._backdate_last_activity(db_manager, "r-stale", 60)

        closed = room_model.cleanup_stale_rooms()
        assert "r-stale" in closed
        assert room_model.get_by_id("r-stale")["status"] == "closed"

    def test_cleanup_closes_active_room_past_timeout(
        self, room_model, db_manager, admin_user_id
    ):
        """Active 룸도 last_activity가 timeout 초과면 closed (좀비 정리)."""
        room_model.create(
            room_id="r-zombie",
            name="zombie",
            created_by=admin_user_id,
            timeout_minutes=30,
        )
        room_model.transition_status("r-zombie", "active")
        self._backdate_last_activity(db_manager, "r-zombie", 90)

        closed = room_model.cleanup_stale_rooms()
        assert "r-zombie" in closed
        assert room_model.get_by_id("r-zombie")["status"] == "closed"

    def test_cleanup_skips_recent_rooms(self, room_model, db_manager, admin_user_id):
        room_model.create(
            room_id="r-fresh",
            name="fresh",
            created_by=admin_user_id,
            timeout_minutes=30,
        )
        room_model.transition_status("r-fresh", "active")
        # last_activity = now, well within timeout
        closed = room_model.cleanup_stale_rooms()
        assert "r-fresh" not in closed
        assert room_model.get_by_id("r-fresh")["status"] == "active"

    def test_cleanup_skips_already_closed(self, room_model, db_manager, admin_user_id):
        room_model.create(room_id="r-done", name="done", created_by=admin_user_id)
        room_model.transition_status("r-done", "closed")
        self._backdate_last_activity(db_manager, "r-done", 999)
        closed = room_model.cleanup_stale_rooms()
        assert "r-done" not in closed

    def test_cleanup_respects_per_room_timeout(
        self, room_model, db_manager, admin_user_id
    ):
        """timeout_minutes=5 인 룸이 10분 stale 이면 정리된다."""
        room_model.create(
            room_id="r-short",
            name="short",
            created_by=admin_user_id,
            timeout_minutes=5,
        )
        room_model.transition_status("r-short", "active")
        self._backdate_last_activity(db_manager, "r-short", 10)
        closed = room_model.cleanup_stale_rooms()
        assert "r-short" in closed


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
