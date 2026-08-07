"""
ISSUE-29: 관리자 대시보드 — 룸 관리 및 오퍼레이터 역할 기반 뷰 단위 테스트.

검증 대상 (모두 server-side, RL-002):
- usage_logs.room_id 컬럼 마이그레이션 (멱등)
- UsageLog.record_usage 가 room_id 를 저장한다
- UsageLog.get_user_logs_by_room / get_room_logs 필터링
- Room.force_close (관리자 강제 종료)
- auth.is_operator / is_admin_or_operator / require_admin_or_operator
- admin_logic.prepare_room_table_data — DB → 표시용 dict 변환 (pure)
- admin_logic.filter_rooms_for_role — admin은 전체, operator 는 자기 룸만
- admin_logic.export_room_logs_csv — 룸별 로그 CSV (BOM, 헤더)

Streamlit 의존성은 mock 으로 격리한다 (RL-001 / RL-005). 실제 Streamlit
import 가 일어나면 set_page_config 등의 부수효과가 테스트 컨텍스트를
오염시키므로, 새 admin 헬퍼는 admin_logic.py 에 두고 admin.py 에서
import 한다.
"""

from __future__ import annotations

import csv
import io
import sys
from unittest.mock import MagicMock

import pytest

# auth -> streamlit 의존성 mock (RL-001 — 부수효과 차단)
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def db_manager(tmp_path):
    from database import DatabaseManager

    return DatabaseManager(str(tmp_path / "issue29.db"))


@pytest.fixture
def user_model(db_manager):
    from database import User

    return User(db_manager)


@pytest.fixture
def usage_log_model(db_manager):
    from database import UsageLog

    return UsageLog(db_manager)


@pytest.fixture
def room_model(db_manager):
    from database import Room

    return Room(db_manager)


@pytest.fixture
def admin_user_id(user_model):
    return user_model.create_user(
        username="adm",
        password="pwadmin",
        role="admin",
        usage_limit_seconds=0,
    )


@pytest.fixture
def operator_user_id(user_model):
    return user_model.create_user(
        username="op",
        password="pwop",
        role="operator",
        usage_limit_seconds=3600,
    )


@pytest.fixture
def other_operator_user_id(user_model):
    return user_model.create_user(
        username="op2",
        password="pwop2",
        role="operator",
        usage_limit_seconds=3600,
    )


@pytest.fixture
def regular_user_id(user_model):
    return user_model.create_user(
        username="usr",
        password="pwuser",
        role="user",
        usage_limit_seconds=3600,
    )


# ============================================================
# 1. usage_logs.room_id 컬럼 마이그레이션
# ============================================================
class TestUsageLogsRoomIdMigration:
    """AC: usage_logs.room_id 컬럼 추가 (NULL 허용, 기존 row 호환).

    멱등성: init_database() 가 여러 번 호출되어도 ALTER TABLE 가
    OperationalError 없이 통과해야 한다.
    """

    def test_room_id_column_exists(self, db_manager):
        with db_manager.get_connection() as conn:
            cur = conn.execute("PRAGMA table_info(usage_logs)")
            cols = {row["name"] for row in cur.fetchall()}
        assert "room_id" in cols, f"room_id missing in usage_logs: {cols}"

    def test_room_id_is_nullable(self, db_manager, user_model):
        """기존 row 호환: NULL 가 허용된다."""
        from database import UsageLog

        uid = user_model.create_user(username="legacy", password="pw", role="user")
        log = UsageLog(db_manager)
        # No room_id passed — should still record
        log_id = log.record_usage(
            user_id=uid,
            action="transcribe",
            duration_seconds=5,
        )
        assert log_id is not None

        with db_manager.get_connection() as conn:
            row = conn.execute(
                "SELECT room_id FROM usage_logs WHERE id = ?", (log_id,)
            ).fetchone()
        assert row["room_id"] is None

    def test_migration_is_idempotent(self, db_manager, tmp_path):
        """init_database()를 다시 호출해도 OperationalError 없이 통과."""
        # Re-initialize same DB twice — must not raise
        from database import DatabaseManager

        # Already initialized once via fixture; doing it again should be safe.
        DatabaseManager(db_manager.db_path)
        DatabaseManager(db_manager.db_path)

        with db_manager.get_connection() as conn:
            cur = conn.execute("PRAGMA table_info(usage_logs)")
            cols = [row["name"] for row in cur.fetchall()]
        # Column exists exactly once
        assert cols.count("room_id") == 1


# ============================================================
# 2. UsageLog.record_usage — room_id 저장
# ============================================================
class TestUsageLogRoomIdPersistence:
    """AC: WebSocket 핸들러는 transcript 기록 시 room_id 도 함께 저장한다."""

    def test_record_usage_stores_room_id(
        self, usage_log_model, regular_user_id, room_model, admin_user_id
    ):
        room_model.create(room_id="r-rec", name="Rec", created_by=admin_user_id)
        log_id = usage_log_model.record_usage(
            user_id=regular_user_id,
            action="transcribe",
            duration_seconds=10,
            source_language="en",
            target_language="ko",
            room_id="r-rec",
        )
        assert log_id is not None

        logs = usage_log_model.get_user_logs(regular_user_id)
        assert any(log.get("room_id") == "r-rec" for log in logs)

    def test_record_usage_none_room_id_persists_null(
        self, usage_log_model, regular_user_id
    ):
        """레거시 호출 (room_id 미지정) 도 동작한다."""
        log_id = usage_log_model.record_usage(
            user_id=regular_user_id,
            action="transcribe",
            duration_seconds=3,
        )
        assert log_id is not None
        logs = usage_log_model.get_user_logs(regular_user_id)
        assert logs[0].get("room_id") is None


# ============================================================
# 3. UsageLog.get_logs_by_room — 룸별 로그 조회
# ============================================================
class TestUsageLogGetByRoom:
    """AC: 오퍼레이터가 자기 룸 기록만 조회 가능 (DB 쿼리 단위)."""

    def test_get_logs_by_room_returns_only_that_room(
        self,
        usage_log_model,
        regular_user_id,
        operator_user_id,
        room_model,
        admin_user_id,
    ):
        room_model.create(room_id="r-mine", name="Mine", created_by=admin_user_id)
        room_model.create(room_id="r-other", name="Other", created_by=admin_user_id)

        usage_log_model.record_usage(
            user_id=regular_user_id,
            action="transcribe",
            duration_seconds=1,
            room_id="r-mine",
        )
        usage_log_model.record_usage(
            user_id=operator_user_id,
            action="transcribe",
            duration_seconds=2,
            room_id="r-mine",
        )
        usage_log_model.record_usage(
            user_id=regular_user_id,
            action="transcribe",
            duration_seconds=3,
            room_id="r-other",
        )

        mine = usage_log_model.get_logs_by_room("r-mine")
        ids_room = {log["room_id"] for log in mine}
        assert ids_room == {"r-mine"}
        assert len(mine) == 2

    def test_get_logs_by_room_empty_for_no_logs(self, usage_log_model):
        """존재하지 않는 룸 → 빈 리스트 (KeyError 아님)."""
        result = usage_log_model.get_logs_by_room("ghost-room")
        assert result == []


# ============================================================
# 4. Room.force_close — 관리자 강제 종료
# ============================================================
class TestRoomForceClose:
    """AC: 관리자가 룸을 강제 종료할 수 있다.

    어느 status 에서든 closed 로 전이 가능 (관리자 권한). 이미 closed 인
    경우는 idempotent: True 를 반환한다.
    """

    def test_force_close_active_room(self, room_model, admin_user_id):
        room_model.create(room_id="r-fa", name="A", created_by=admin_user_id)
        room_model.transition_status("r-fa", "active")
        ok = room_model.force_close("r-fa")
        assert ok is True
        assert room_model.get_by_id("r-fa")["status"] == "closed"

    def test_force_close_waiting_room(self, room_model, admin_user_id):
        room_model.create(room_id="r-fw", name="W", created_by=admin_user_id)
        ok = room_model.force_close("r-fw")
        assert ok is True
        assert room_model.get_by_id("r-fw")["status"] == "closed"

    def test_force_close_inactive_room(self, room_model, admin_user_id):
        room_model.create(room_id="r-fi", name="I", created_by=admin_user_id)
        room_model.transition_status("r-fi", "active")
        room_model.transition_status("r-fi", "inactive")
        ok = room_model.force_close("r-fi")
        assert ok is True
        assert room_model.get_by_id("r-fi")["status"] == "closed"

    def test_force_close_already_closed_is_idempotent(self, room_model, admin_user_id):
        room_model.create(room_id="r-fc", name="C", created_by=admin_user_id)
        room_model.transition_status("r-fc", "closed")
        ok = room_model.force_close("r-fc")
        # Idempotent — must not raise InvalidRoomTransition
        assert ok is True

    def test_force_close_unknown_room_returns_false(self, room_model):
        assert room_model.force_close("ghost") is False

    def test_force_close_sets_closed_at(self, room_model, admin_user_id):
        room_model.create(room_id="r-fts", name="T", created_by=admin_user_id)
        room_model.force_close("r-fts")
        room = room_model.get_by_id("r-fts")
        assert room["closed_at"] is not None


# ============================================================
# 5. auth.is_operator / is_admin_or_operator
# ============================================================
class TestAuthRoleHelpers:
    """AC: 역할 헬퍼는 server-side session 만 신뢰한다 (RL-002).

    streamlit session_state 는 mock 으로 주입한다 — Streamlit 실제 import
    부수효과를 피하기 위함.
    """

    def _set_session(self, monkeypatch, *, authenticated=True, role="user"):
        import auth

        monkeypatch.setattr(
            auth.st,
            "session_state",
            {
                "authenticated": authenticated,
                "user": (
                    {
                        "id": 1,
                        "username": "x",
                        "role": role,
                        "is_active": True,
                    }
                    if authenticated
                    else None
                ),
            },
            raising=False,
        )

    def test_is_operator_true_for_operator_role(self, monkeypatch):
        import auth

        self._set_session(monkeypatch, role="operator")
        assert auth.is_operator() is True

    def test_is_operator_false_for_user_role(self, monkeypatch):
        import auth

        self._set_session(monkeypatch, role="user")
        assert auth.is_operator() is False

    def test_is_operator_false_for_admin_role(self, monkeypatch):
        import auth

        self._set_session(monkeypatch, role="admin")
        # admin is NOT operator — caller uses is_admin_or_operator() to check both
        assert auth.is_operator() is False

    def test_is_operator_false_when_not_authenticated(self, monkeypatch):
        import auth

        self._set_session(monkeypatch, authenticated=False)
        assert auth.is_operator() is False

    def test_is_admin_or_operator_true_for_admin(self, monkeypatch):
        import auth

        self._set_session(monkeypatch, role="admin")
        assert auth.is_admin_or_operator() is True

    def test_is_admin_or_operator_true_for_operator(self, monkeypatch):
        import auth

        self._set_session(monkeypatch, role="operator")
        assert auth.is_admin_or_operator() is True

    def test_is_admin_or_operator_false_for_user(self, monkeypatch):
        import auth

        self._set_session(monkeypatch, role="user")
        assert auth.is_admin_or_operator() is False

    def test_is_admin_or_operator_false_when_anonymous(self, monkeypatch):
        import auth

        self._set_session(monkeypatch, authenticated=False)
        assert auth.is_admin_or_operator() is False


class _SessionStateDict(dict):
    """dict + attribute access — Streamlit session_state surrogate.

    The real ``st.session_state`` supports both ``state["key"]`` and
    ``state.key`` access. ``init_session_state`` uses attribute setters,
    so the test surrogate must mimic that semantic.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def __setattr__(self, name, value):
        self[name] = value


class TestRequireAdminOrOperatorDecorator:
    """AC: role="user" admin 페이지 접근이 차단된다 (기존 동작 유지)."""

    def _setup(self, monkeypatch, *, authenticated, role):
        import auth

        # Build a session_state surrogate (dict + attribute access).
        state = _SessionStateDict(
            {
                "authenticated": authenticated,
                "user": (
                    {
                        "id": 1,
                        "username": "x",
                        "role": role,
                        "is_active": True,
                    }
                    if authenticated
                    else None
                ),
                # init_session_state() expects this key to exist or be created.
                "cookie_restore_attempted": True,
            }
        )

        st_mock = MagicMock()
        st_mock.session_state = state
        # st.stop must raise to abort the wrapped function
        st_mock.stop.side_effect = SystemExit
        monkeypatch.setattr(auth, "st", st_mock, raising=False)
        return st_mock

    def test_admin_passes_through(self, monkeypatch):
        import auth

        self._setup(monkeypatch, authenticated=True, role="admin")
        called = []

        @auth.require_admin_or_operator
        def handler():
            called.append(True)
            return "ok"

        assert handler() == "ok"
        assert called == [True]

    def test_operator_passes_through(self, monkeypatch):
        import auth

        self._setup(monkeypatch, authenticated=True, role="operator")
        called = []

        @auth.require_admin_or_operator
        def handler():
            called.append(True)
            return "ok"

        assert handler() == "ok"
        assert called == [True]

    def test_user_role_blocked(self, monkeypatch):
        import auth

        st_mock = self._setup(monkeypatch, authenticated=True, role="user")

        @auth.require_admin_or_operator
        def handler():
            return "should not run"

        with pytest.raises(SystemExit):
            handler()
        # st.error was called — a permission error message
        assert st_mock.error.called

    def test_anonymous_blocked(self, monkeypatch):
        import auth

        st_mock = self._setup(monkeypatch, authenticated=False, role="user")

        @auth.require_admin_or_operator
        def handler():
            return "should not run"

        with pytest.raises(SystemExit):
            handler()
        # Either warning (not authenticated) or error must trigger
        assert st_mock.warning.called or st_mock.error.called


# ============================================================
# 6. admin_logic — pure helpers (RL-001 / RL-005)
# ============================================================
class TestPrepareRoomTableData:
    """DB row → 화면 표시용 dict 변환 (Streamlit-free)."""

    def test_basic_conversion(self):
        from admin_logic import prepare_room_table_data

        rooms = [
            {
                "id": "r-1",
                "name": "A홀",
                "status": "active",
                "input_lang": "auto",
                "output_lang": "ko",
                "created_by": 1,
                "operator_id": 2,
                "created_at": "2026-05-01",
                "last_activity": "2026-05-07 10:00:00",
                "timeout_minutes": 30,
                "closed_at": None,
            }
        ]
        usernames = {1: "admin", 2: "op1"}
        result = prepare_room_table_data(rooms, usernames)

        assert len(result) == 1
        row = result[0]
        assert row["룸ID"] == "r-1"
        assert row["이름"] == "A홀"
        assert row["상태"] == "활성"
        assert row["오퍼레이터"] == "op1"
        assert row["입력언어"] == "auto"
        assert row["출력언어"] == "ko"

    def test_unassigned_operator_displays_dash(self):
        from admin_logic import prepare_room_table_data

        rooms = [
            {
                "id": "r-2",
                "name": "B",
                "status": "waiting",
                "input_lang": "auto",
                "output_lang": "ko",
                "created_by": 1,
                "operator_id": None,
                "created_at": "2026-05-01",
                "last_activity": None,
                "timeout_minutes": 30,
                "closed_at": None,
            }
        ]
        result = prepare_room_table_data(rooms, {1: "admin"})
        assert result[0]["오퍼레이터"] == "-"

    def test_closed_status_label(self):
        from admin_logic import prepare_room_table_data

        rooms = [
            {
                "id": "r-3",
                "name": "C",
                "status": "closed",
                "input_lang": "auto",
                "output_lang": "ko",
                "created_by": 1,
                "operator_id": None,
                "created_at": "2026-05-01",
                "last_activity": None,
                "timeout_minutes": 30,
                "closed_at": "2026-05-02",
            }
        ]
        result = prepare_room_table_data(rooms, {1: "admin"})
        assert result[0]["상태"] == "종료"

    def test_empty_rooms_returns_empty(self):
        from admin_logic import prepare_room_table_data

        assert prepare_room_table_data([], {}) == []


class TestFilterRoomsForRole:
    """AC: admin 은 전체, operator 는 자기 룸만 (server-side, RL-002).

    이 함수는 DB 쿼리 결과를 받아 역할 기반 화이트리스트로 좁힌다.
    UI 레이어가 아닌 admin_logic.py 에서 수행해야 client 가 표시되지
    않는 데이터를 추측할 수 없다.
    """

    def _rooms(self):
        return [
            {"id": "r-A", "name": "A", "operator_id": 10},
            {"id": "r-B", "name": "B", "operator_id": 20},
            {"id": "r-C", "name": "C", "operator_id": None},
            {"id": "r-D", "name": "D", "operator_id": 10},
        ]

    def test_admin_sees_all_rooms(self):
        from admin_logic import filter_rooms_for_role

        result = filter_rooms_for_role(
            self._rooms(),
            user_role="admin",
            user_id=10,
        )
        ids = {r["id"] for r in result}
        assert ids == {"r-A", "r-B", "r-C", "r-D"}

    def test_operator_sees_only_assigned(self):
        from admin_logic import filter_rooms_for_role

        result = filter_rooms_for_role(
            self._rooms(),
            user_role="operator",
            user_id=10,
        )
        ids = {r["id"] for r in result}
        assert ids == {"r-A", "r-D"}

    def test_operator_with_no_rooms(self):
        from admin_logic import filter_rooms_for_role

        result = filter_rooms_for_role(
            self._rooms(),
            user_role="operator",
            user_id=999,
        )
        assert result == []

    def test_unknown_role_returns_empty(self):
        """비인가 역할은 어떤 룸도 보지 못한다 (defensive default)."""
        from admin_logic import filter_rooms_for_role

        result = filter_rooms_for_role(
            self._rooms(),
            user_role="user",
            user_id=10,
        )
        assert result == []


class TestExportRoomLogsCsv:
    """AC: 오퍼레이터가 자기 룸의 대화 기록을 CSV 로 다운로드할 수 있다."""

    def _logs(self):
        return [
            {
                "id": 1,
                "user_id": 10,
                "room_id": "r-K",
                "action": "transcribe",
                "duration_seconds": 30,
                "source_language": "en",
                "target_language": "ko",
                "created_at": "2026-05-07T10:00:00",
                "metadata": {
                    "source_text": "Hello",
                    "target_text": "안녕",
                },
            },
            {
                "id": 2,
                "user_id": 11,
                "room_id": "r-K",
                "action": "transcribe",
                "duration_seconds": 15,
                "source_language": "ko",
                "target_language": "en",
                "created_at": "2026-05-07T10:05:00",
                "metadata": None,
            },
        ]

    def test_csv_starts_with_bom(self):
        from admin_logic import export_room_logs_csv

        result = export_room_logs_csv(self._logs(), room_id="r-K")
        assert result[:3] == b"\xef\xbb\xbf"

    def test_csv_returns_bytes(self):
        from admin_logic import export_room_logs_csv

        result = export_room_logs_csv(self._logs(), room_id="r-K")
        assert isinstance(result, bytes)

    def test_csv_headers_include_room_id(self):
        from admin_logic import export_room_logs_csv

        result = export_room_logs_csv(self._logs(), room_id="r-K")
        text = result[3:].decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        headers = next(reader)
        assert "룸ID" in headers
        assert "원문" in headers
        assert "번역문" in headers

    def test_csv_row_count_matches_logs(self):
        from admin_logic import export_room_logs_csv

        result = export_room_logs_csv(self._logs(), room_id="r-K")
        text = result[3:].decode("utf-8")
        rows = list(csv.reader(io.StringIO(text)))
        assert len(rows) == 3  # 1 header + 2 rows

    def test_csv_data_values(self):
        from admin_logic import export_room_logs_csv

        result = export_room_logs_csv(self._logs(), room_id="r-K")
        text = result[3:].decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert rows[0]["룸ID"] == "r-K"
        assert rows[0]["원문"] == "Hello"
        assert rows[0]["번역문"] == "안녕"

    def test_csv_metadata_none_handled(self):
        from admin_logic import export_room_logs_csv

        result = export_room_logs_csv(self._logs(), room_id="r-K")
        text = result[3:].decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert rows[1]["원문"] == ""
        assert rows[1]["번역문"] == ""

    def test_csv_empty_logs_returns_header_only(self):
        from admin_logic import export_room_logs_csv

        result = export_room_logs_csv([], room_id="r-empty")
        text = result[3:].decode("utf-8")
        rows = list(csv.reader(io.StringIO(text)))
        assert len(rows) == 1


# ============================================================
# 7. UsageLog.get_user_logs / get_all_user_logs include room_id
# ============================================================
class TestUsageLogIncludesRoomId:
    """AC: 기존 조회 API 도 room_id 필드를 노출해야 (오퍼레이터 필터링용)."""

    def test_get_user_logs_includes_room_id(
        self, usage_log_model, regular_user_id, room_model, admin_user_id
    ):
        room_model.create(room_id="r-x", name="X", created_by=admin_user_id)
        usage_log_model.record_usage(
            user_id=regular_user_id,
            action="transcribe",
            duration_seconds=1,
            room_id="r-x",
        )
        logs = usage_log_model.get_user_logs(regular_user_id)
        assert any("room_id" in log for log in logs)

    def test_get_all_user_logs_includes_room_id(
        self, usage_log_model, regular_user_id, room_model, admin_user_id
    ):
        room_model.create(room_id="r-y", name="Y", created_by=admin_user_id)
        usage_log_model.record_usage(
            user_id=regular_user_id,
            action="transcribe",
            duration_seconds=1,
            room_id="r-y",
        )
        logs = usage_log_model.get_all_user_logs(regular_user_id)
        assert any("room_id" in log for log in logs)


# ============================================================
# 8. Cross-role data isolation (server-side)
# ============================================================
class TestOperatorCannotAccessOtherRooms:
    """AC: role="operator" 는 admin 페이지에서 자기 룸 외 데이터 접근 차단.

    server-side 분기는 admin_logic.filter_rooms_for_role 가 담당하지만,
    오퍼레이터가 다른 룸의 room_id 를 직접 query 했을 때도 그 결과가
    admin_logic 레벨에서 거부되어야 한다 (RL-002).
    """

    def test_operator_cannot_access_other_room_logs_via_helper(
        self, usage_log_model, regular_user_id, room_model, admin_user_id
    ):
        from admin_logic import get_logs_for_operator
        from database import User

        # Build operators inline (avoids extending fixture web).
        op_id = User(usage_log_model.db).create_user(
            username="op-iso", password="pw", role="operator"
        )
        another_op_id = User(usage_log_model.db).create_user(
            username="op-iso2", password="pw", role="operator"
        )

        # Operator op_id owns r-mine; r-other belongs to another_op_id.
        room_model.create(
            room_id="r-mine",
            name="Mine",
            created_by=admin_user_id,
            operator_id=op_id,
        )
        room_model.create(
            room_id="r-other",
            name="Other",
            created_by=admin_user_id,
            operator_id=another_op_id,
        )
        usage_log_model.record_usage(
            user_id=regular_user_id,
            action="transcribe",
            duration_seconds=1,
            room_id="r-other",
        )

        # Operator op_id requests logs for r-other → must be empty (denied)
        logs = get_logs_for_operator(
            usage_log_model=usage_log_model,
            room_model=room_model,
            requested_room_id="r-other",
            user_role="operator",
            user_id=op_id,
        )
        assert logs == []

    def test_admin_can_access_any_room_logs_via_helper(
        self, usage_log_model, regular_user_id, room_model, admin_user_id
    ):
        from admin_logic import get_logs_for_operator
        from database import User

        op_id = User(usage_log_model.db).create_user(
            username="op-adm", password="pw", role="operator"
        )
        room_model.create(
            room_id="r-any",
            name="Any",
            created_by=admin_user_id,
            operator_id=op_id,
        )
        usage_log_model.record_usage(
            user_id=regular_user_id,
            action="transcribe",
            duration_seconds=1,
            room_id="r-any",
        )

        logs = get_logs_for_operator(
            usage_log_model=usage_log_model,
            room_model=room_model,
            requested_room_id="r-any",
            user_role="admin",
            user_id=admin_user_id,
        )
        assert len(logs) == 1
        assert logs[0]["room_id"] == "r-any"


def operator_user_id_value(usage_log_model, room_model):
    """Helper to keep test compactness; not imported."""
    return 1


# ============================================================
# 9. WebSocket _record_usage forwards server-side room_id
# ============================================================
class TestWebSocketRecordUsageForwardsRoomId:
    """RL-002: websocket_handler._record_usage MUST use the server-side
    ``current_user["room_id"]`` (set by _authenticate_client) and NOT the
    client-supplied transcript payload.
    """

    def test_record_usage_passes_room_id_from_user_info(self, monkeypatch):
        import websocket_handler

        captured: dict = {}

        class _FakeUserModel:
            def add_usage(self, *_args, **_kwargs):
                pass

        class _FakeLogModel:
            def record_usage(self, **kwargs):
                captured.update(kwargs)
                return 1

        monkeypatch.setattr(
            websocket_handler, "get_user_model", lambda: _FakeUserModel()
        )
        monkeypatch.setattr(
            websocket_handler, "get_usage_log_model", lambda: _FakeLogModel()
        )
        monkeypatch.setattr(websocket_handler, "update_user_session", lambda _x: None)

        current_user = {
            "id": 42,
            "username": "u",
            "room_id": "r-server-side",
        }
        # Client tries to spoof a different room — must be ignored.
        client_payload = {"audio_duration_seconds": 3, "room_id": "r-spoofed"}

        websocket_handler._record_usage(
            current_user=current_user,
            audio_duration=3,
            data=client_payload,
            transcript="hi",
            translated_text="안녕",
            used_llm=False,
            source_lang="en",
            target_lang="ko",
        )

        assert captured.get("room_id") == "r-server-side"
        assert captured["room_id"] != "r-spoofed"


# ============================================================
# ISSUE-85/86: 룸 목록 상태 필터 + auto-timeout 제거
# ============================================================
class TestRoomStatusFilter:
    """admin_logic.filter_rooms_by_status — closed 기본 숨김 (pure)."""

    def _rooms(self):
        return [
            {"id": "r-w", "status": "waiting"},
            {"id": "r-a", "status": "active"},
            {"id": "r-i", "status": "inactive"},
            {"id": "r-c", "status": "closed"},
        ]

    def test_default_excludes_closed(self):
        from admin_logic import filter_rooms_by_status

        ids = [r["id"] for r in filter_rooms_by_status(self._rooms())]
        assert ids == ["r-w", "r-a", "r-i"]

    def test_explicit_closed_only(self):
        from admin_logic import filter_rooms_by_status

        ids = [r["id"] for r in filter_rooms_by_status(self._rooms(), ["closed"])]
        assert ids == ["r-c"]

    def test_empty_statuses_returns_empty(self):
        from admin_logic import filter_rooms_by_status

        assert filter_rooms_by_status(self._rooms(), []) == []

    def test_default_constant_excludes_closed_and_options_cover_all(self):
        from admin_logic import (
            DEFAULT_ROOM_LIST_STATUSES,
            ROOM_STATUS_FILTER_OPTIONS,
        )

        assert "closed" not in DEFAULT_ROOM_LIST_STATUSES
        assert set(ROOM_STATUS_FILTER_OPTIONS) == {
            "waiting",
            "active",
            "inactive",
            "closed",
        }


class TestRoomTableTimeoutRemoved:
    """#86: 타임아웃 컬럼 제거 — 표시용 dict 에 더 이상 없음."""

    def test_table_has_last_activity_but_no_timeout_column(self):
        from admin_logic import prepare_room_table_data

        rows = prepare_room_table_data(
            [
                {
                    "id": "r1",
                    "name": "A홀",
                    "status": "active",
                    "operator_id": None,
                    "created_by": None,
                    "last_activity": "2026-08-07 10:00:00",
                }
            ],
            {},
        )
        assert "타임아웃(분)" not in rows[0]
        assert rows[0]["마지막활동"] == "2026-08-07 10:00:00"


class TestValidateRoomNameOnly:
    """#86: validate_room_creation_input 는 이름만 검증한다."""

    def test_valid_name_passes(self):
        from admin_logic import validate_room_creation_input

        valid, error = validate_room_creation_input("A홀 기조연설")
        assert valid is True
        assert error == ""

    def test_empty_name_fails(self):
        from admin_logic import validate_room_creation_input

        valid, error = validate_room_creation_input("   ")
        assert valid is False
        assert "필수" in error

    def test_long_name_fails(self):
        from admin_logic import validate_room_creation_input

        valid, _ = validate_room_creation_input("x" * 101)
        assert valid is False
