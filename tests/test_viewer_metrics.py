"""
ISSUE-33: 뷰어 지표 수집 및 관리자 대시보드 표시 — 단위 테스트.

검증 대상:
1) `rooms.total_viewers` / `rooms.peak_viewers` 마이그레이션 멱등성 + 기본값
2) `Room.update_viewer_metrics(room_id, total_delta, current)` —
   total_viewers 누적 증가 + peak_viewers max 갱신 (race-safe by max())
3) `Room.get_viewer_metrics(room_id)` — DB 누적/peak 반환 형식
4) `BroadcastManager.register_viewer` / `unregister_viewer` 시
   인메모리 카운트 (전체/언어별) 정확성
5) `BroadcastManager.get_metrics(room_id)` — 인메모리 snapshot 형식
6) DB persistence hook 통합:
   register 시 total_viewers++, peak_viewers = max(peak, current) 갱신
7) admin_logic 헬퍼 (`build_room_metrics_view_data`) — admin.py UI 가
   사용할 row dict 형태 검증 (Streamlit-free 단위 테스트)

설계 메모
---------
- 모든 테스트는 외부 네트워크 호출 없음. SQLite tmp_path 격리.
- BroadcastManager 의 DB persistence hook 은 의존성 주입으로 mock 가능.
  (RoomRepoLike 인터페이스: update_viewer_metrics + get_viewer_metrics)
- RL-006: 내부 예외 (예: DB 일시 장애) 가 SSE 스트림으로 누설되지 않음을
  확인 — register_viewer 가 DB 실패해도 인메모리 큐 등록은 그대로 진행.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock

import pytest

# Streamlit-of-the-app 의존성 mock (admin.py / admin_logic.py 가 streamlit 을
# import-time 로 끌어올 수 있어 다른 테스트 파일과 동일한 패턴을 따른다.
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ---------------------------------------------------------------------------
# Fixtures (DB 격리)
# ---------------------------------------------------------------------------
@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "metrics.db")


@pytest.fixture
def db_manager(db_path):
    from database import DatabaseManager

    return DatabaseManager(db_path)


@pytest.fixture
def admin_user_id(db_manager):
    from database import User

    user_model = User(db_manager)
    return user_model.create_user(
        username="admin1",
        password="pw",
        role="admin",
        usage_limit_seconds=0,
    )


@pytest.fixture
def room_model(db_manager):
    from database import Room

    return Room(db_manager)


@pytest.fixture
def room_id(room_model, admin_user_id):
    room = room_model.create(
        room_id="r-metrics",
        name="Metrics Room",
        created_by=admin_user_id,
    )
    assert room is not None
    return room["id"]


# ---------------------------------------------------------------------------
# 1. Migration: rooms.total_viewers / peak_viewers
# ---------------------------------------------------------------------------
class TestRoomViewerMetricsMigration:
    """AC: rooms 테이블에 total_viewers / peak_viewers 컬럼 추가.

    멱등성: init_database() 가 여러 번 호출되어도 ALTER TABLE 가 한 번만
    실행되며, 중복 ADD COLUMN 으로 인한 OperationalError 가 발생하지 않는다.
    """

    def test_total_viewers_column_exists(self, db_manager):
        with db_manager.get_connection() as conn:
            cur = conn.execute("PRAGMA table_info(rooms)")
            cols = {row["name"] for row in cur.fetchall()}
        assert "total_viewers" in cols, f"total_viewers missing: {cols}"

    def test_peak_viewers_column_exists(self, db_manager):
        with db_manager.get_connection() as conn:
            cur = conn.execute("PRAGMA table_info(rooms)")
            cols = {row["name"] for row in cur.fetchall()}
        assert "peak_viewers" in cols, f"peak_viewers missing: {cols}"

    def test_default_values_are_zero(self, db_manager, admin_user_id, room_model):
        room = room_model.create(
            room_id="r-default",
            name="DefaultZero",
            created_by=admin_user_id,
        )
        assert room is not None
        assert room["total_viewers"] == 0
        assert room["peak_viewers"] == 0

    def test_migration_is_idempotent_on_third_init(self, db_path, admin_user_id):
        """init_database() 를 3 회 호출해도 OperationalError 없이 통과해야 한다.

        ISSUE 본문에서 명시된 보호 사항: PRAGMA table_info → ALTER TABLE 패턴이
        제대로 작동하지 않으면 두 번째 호출에서 'duplicate column' 으로 깨진다.
        """
        from database import DatabaseManager

        # First init already happened via fixture. Run two more.
        DatabaseManager(db_path)
        DatabaseManager(db_path)

        # Schema 상태가 안정적이며 컬럼은 정확히 한 번만 존재.
        dbm = DatabaseManager(db_path)
        with dbm.get_connection() as conn:
            cur = conn.execute("PRAGMA table_info(rooms)")
            col_names = [row["name"] for row in cur.fetchall()]
        assert col_names.count("total_viewers") == 1
        assert col_names.count("peak_viewers") == 1

    def test_legacy_db_without_columns_gets_migrated(self, tmp_path):
        """기존 prod DB (total_viewers/peak_viewers 컬럼 없음) 에 init_database()
        가 호출되면 ALTER TABLE 로 두 컬럼이 추가된다."""
        import sqlite3 as _sql3

        legacy_db = str(tmp_path / "legacy.db")
        # 컬럼 없는 레거시 rooms 테이블을 손수 만든다.
        conn = _sql3.connect(legacy_db)
        conn.row_factory = _sql3.Row
        conn.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                is_active BOOLEAN DEFAULT 1,
                total_usage_seconds INTEGER DEFAULT 0,
                usage_limit_seconds INTEGER DEFAULT 3600,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("INSERT INTO users (username, password_hash) VALUES ('a', 'x')")
        # 구버전 rooms (no total_viewers/peak_viewers/output_langs).
        conn.execute(
            """
            CREATE TABLE rooms (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'waiting',
                input_lang TEXT NOT NULL DEFAULT 'auto',
                output_lang TEXT NOT NULL DEFAULT 'ko',
                created_by INTEGER NOT NULL,
                operator_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP,
                timeout_minutes INTEGER NOT NULL DEFAULT 30,
                closed_at TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            "INSERT INTO rooms (id, name, created_by) VALUES ('legacy-r', 'L', 1)"
        )
        conn.commit()
        conn.close()

        # 이제 정상 init 을 돌리면 두 컬럼이 추가되어야 한다.
        from database import DatabaseManager

        dbm = DatabaseManager(legacy_db)
        with dbm.get_connection() as conn:
            cur = conn.execute("PRAGMA table_info(rooms)")
            cols = {row["name"] for row in cur.fetchall()}
        assert "total_viewers" in cols
        assert "peak_viewers" in cols

        # 기존 row 의 새 컬럼 값은 default (0).
        with dbm.get_connection() as conn:
            row = conn.execute(
                "SELECT total_viewers, peak_viewers FROM rooms WHERE id = ?",
                ("legacy-r",),
            ).fetchone()
        assert row["total_viewers"] == 0
        assert row["peak_viewers"] == 0


# ---------------------------------------------------------------------------
# 2. Room.update_viewer_metrics — DB 카운트 갱신
# ---------------------------------------------------------------------------
class TestRoomUpdateViewerMetrics:
    """AC: total_viewers 단순 increment, peak_viewers 는 max() 로 갱신."""

    def test_increment_total_viewers_by_one(self, room_model, room_id):
        ok = room_model.update_viewer_metrics(room_id, total_delta=1, current=1)
        assert ok is True
        m = room_model.get_viewer_metrics(room_id)
        assert m["total_viewers"] == 1
        assert m["peak_viewers"] == 1

    def test_increment_total_repeatedly(self, room_model, room_id):
        for _ in range(5):
            room_model.update_viewer_metrics(room_id, total_delta=1, current=1)
        m = room_model.get_viewer_metrics(room_id)
        assert m["total_viewers"] == 5

    def test_peak_viewers_uses_max_not_overwrite(self, room_model, room_id):
        """peak_viewers 가 올바르게 갱신되는지 검증 (mock Room model 동작).

        peak 는 항상 max(peak, current). current 가 줄어도 peak 는 유지.
        """
        room_model.update_viewer_metrics(room_id, total_delta=1, current=10)
        room_model.update_viewer_metrics(room_id, total_delta=0, current=3)
        m = room_model.get_viewer_metrics(room_id)
        assert m["peak_viewers"] == 10

    def test_peak_viewers_grows(self, room_model, room_id):
        room_model.update_viewer_metrics(room_id, total_delta=1, current=2)
        room_model.update_viewer_metrics(room_id, total_delta=1, current=5)
        m = room_model.get_viewer_metrics(room_id)
        assert m["peak_viewers"] == 5
        assert m["total_viewers"] == 2

    def test_total_delta_zero_does_not_change_total(self, room_model, room_id):
        """언레지스터 시 (delta=0, current=N) 도 안전하게 호출 가능."""
        room_model.update_viewer_metrics(room_id, total_delta=1, current=1)
        room_model.update_viewer_metrics(room_id, total_delta=0, current=0)
        m = room_model.get_viewer_metrics(room_id)
        assert m["total_viewers"] == 1

    def test_unknown_room_returns_false(self, room_model):
        ok = room_model.update_viewer_metrics("nonexistent", total_delta=1, current=1)
        assert ok is False


class TestRoomGetViewerMetrics:
    """AC: get_viewer_metrics 는 {total_viewers, peak_viewers} 를 반환한다."""

    def test_returns_zero_for_fresh_room(self, room_model, room_id):
        m = room_model.get_viewer_metrics(room_id)
        assert m == {"total_viewers": 0, "peak_viewers": 0}

    def test_unknown_room_returns_none(self, room_model):
        # 존재하지 않는 룸: None (호출자가 명시적으로 핸들링)
        assert room_model.get_viewer_metrics("nope") is None


# ---------------------------------------------------------------------------
# 3. BroadcastManager — 인메모리 카운트 (전체/언어별)
# ---------------------------------------------------------------------------
class TestBroadcastManagerInMemoryCounts:
    """AC: register/unregister 시 전체 카운트 + 언어별 카운트 정확."""

    @pytest.mark.asyncio
    async def test_initial_metrics_are_zero(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        m = mgr.get_metrics("room-x")
        assert m["current"] == 0
        assert m["by_lang"] == {}

    @pytest.mark.asyncio
    async def test_single_register_bumps_current_and_lang(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        await mgr.register_viewer("room-x", "ko")

        m = mgr.get_metrics("room-x")
        assert m["current"] == 1
        assert m["by_lang"] == {"ko": 1}

    @pytest.mark.asyncio
    async def test_multiple_languages_have_independent_counts(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        await mgr.register_viewer("room-x", "ko")
        await mgr.register_viewer("room-x", "ko")
        await mgr.register_viewer("room-x", "zh")

        m = mgr.get_metrics("room-x")
        assert m["current"] == 3
        assert m["by_lang"] == {"ko": 2, "zh": 1}

    @pytest.mark.asyncio
    async def test_unregister_decrements_lang_and_current(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        q_ko = await mgr.register_viewer("room-x", "ko")
        q_zh = await mgr.register_viewer("room-x", "zh")
        await mgr.unregister_viewer("room-x", "ko", q_ko)

        m = mgr.get_metrics("room-x")
        assert m["current"] == 1
        assert m["by_lang"] == {"zh": 1}

        # 마지막 언어까지 빠지면 by_lang 비어있어야 함.
        await mgr.unregister_viewer("room-x", "zh", q_zh)
        m = mgr.get_metrics("room-x")
        assert m["current"] == 0
        assert m["by_lang"] == {}

    @pytest.mark.asyncio
    async def test_metrics_isolated_per_room(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        await mgr.register_viewer("room-A", "ko")
        await mgr.register_viewer("room-B", "ko")
        await mgr.register_viewer("room-B", "ko")

        m_a = mgr.get_metrics("room-A")
        m_b = mgr.get_metrics("room-B")
        assert m_a["current"] == 1
        assert m_b["current"] == 2

    @pytest.mark.asyncio
    async def test_zero_for_unknown_room_is_safe(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        m = mgr.get_metrics("never-touched")
        # 초기화 안 된 룸은 빈 카운트로 응답 — admin.py 에서 "0명" 표시 가능.
        assert m == {"current": 0, "by_lang": {}}

    @pytest.mark.asyncio
    async def test_unregister_unknown_does_not_underflow(self):
        """unregister 가 register 보다 많아도 음수가 되지 않는다."""
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        # 큐가 아예 없는데 unregister
        fake_q: asyncio.Queue = asyncio.Queue()
        await mgr.unregister_viewer("ghost", "ko", fake_q)
        m = mgr.get_metrics("ghost")
        assert m["current"] == 0
        assert m["by_lang"] == {}


# ---------------------------------------------------------------------------
# 4. BroadcastManager + DB persistence hook (통합 in-mem)
# ---------------------------------------------------------------------------
class _FakeRoomRepo:
    """Minimal repo stub for BroadcastManager DB hook tests.

    Only exposes the two methods the manager calls — keeps the test focused
    on hook contract, not full DB surface.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.totals: dict[str, int] = {}
        self.peaks: dict[str, int] = {}

    def update_viewer_metrics(
        self, room_id: str, *, total_delta: int, current: int
    ) -> bool:
        self.calls.append((room_id, total_delta, current))
        self.totals[room_id] = self.totals.get(room_id, 0) + total_delta
        self.peaks[room_id] = max(self.peaks.get(room_id, 0), current)
        return True

    def get_viewer_metrics(self, room_id: str) -> dict[str, int] | None:
        if room_id not in self.totals and room_id not in self.peaks:
            return None
        return {
            "total_viewers": self.totals.get(room_id, 0),
            "peak_viewers": self.peaks.get(room_id, 0),
        }


class TestBroadcastManagerDBHook:
    """AC: register 시 DB 누적/peak 가 갱신되도록 repo 가 호출된다."""

    @pytest.mark.asyncio
    async def test_register_calls_repo_with_delta_one(self):
        from sse_broadcast import BroadcastManager

        repo = _FakeRoomRepo()
        mgr = BroadcastManager(metrics_repo=repo)
        await mgr.register_viewer("rm-1", "ko")

        # 가장 최근 호출이 (rm-1, +1, current=1)
        assert repo.calls[-1] == ("rm-1", 1, 1)
        assert repo.totals["rm-1"] == 1
        assert repo.peaks["rm-1"] == 1

    @pytest.mark.asyncio
    async def test_register_updates_peak_via_max(self):
        from sse_broadcast import BroadcastManager

        repo = _FakeRoomRepo()
        mgr = BroadcastManager(metrics_repo=repo)
        await mgr.register_viewer("rm-2", "ko")
        await mgr.register_viewer("rm-2", "ko")
        await mgr.register_viewer("rm-2", "zh")

        assert repo.totals["rm-2"] == 3
        assert repo.peaks["rm-2"] == 3

        # current 가 줄어도 peak 유지 — repo._FakeRoomRepo 의 max 의미.
        # (BroadcastManager 가 unregister 시 delta=0 + current=축소 호출은
        # 선택적; 현재 contract 은 불러도 무방).

    @pytest.mark.asyncio
    async def test_repo_failure_does_not_break_register(self):
        """RL-006: DB 일시 장애 시에도 SSE 연결은 안전하게 진행되어야 한다."""

        class _FailingRepo:
            def update_viewer_metrics(self, *a, **kw):
                raise RuntimeError("simulated DB error")

            def get_viewer_metrics(self, *a, **kw):
                return None

        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager(metrics_repo=_FailingRepo())
        # register 가 예외 없이 큐를 반환해야 한다.
        q = await mgr.register_viewer("rm-x", "ko")
        assert isinstance(q, asyncio.Queue)
        # 인메모리 카운트는 정상 갱신.
        m = mgr.get_metrics("rm-x")
        assert m["current"] == 1

    @pytest.mark.asyncio
    async def test_no_repo_means_no_db_writes_but_in_memory_works(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()  # no metrics_repo
        await mgr.register_viewer("rm-y", "ko")
        m = mgr.get_metrics("rm-y")
        assert m["current"] == 1


# ---------------------------------------------------------------------------
# 5. admin_logic 헬퍼 (build_room_metrics_view_data)
# ---------------------------------------------------------------------------
class TestBuildRoomMetricsViewData:
    """AC: admin.py 룸 관리 탭에 표시할 row dict 형태를 검증한다.

    Streamlit 위젯 자체는 unit-test 하지 않고, 표시 데이터를 만드는 순수
    함수만 테스트한다 (RL-001 분리 원칙).
    """

    def test_empty_metrics_returns_zeros(self):
        from admin_logic import build_room_metrics_view_data

        data = build_room_metrics_view_data(
            in_memory={"current": 0, "by_lang": {}},
            db_metrics={"total_viewers": 0, "peak_viewers": 0},
        )
        assert data["current"] == 0
        assert data["total"] == 0
        assert data["peak"] == 0
        assert data["by_lang_label"] in ("0명", "")  # 비어있을 때 zero-state 허용

    def test_typical_metrics_render_lang_summary(self):
        from admin_logic import build_room_metrics_view_data

        data = build_room_metrics_view_data(
            in_memory={"current": 57, "by_lang": {"ko": 45, "zh": 12}},
            db_metrics={"total_viewers": 1234, "peak_viewers": 88},
        )
        assert data["current"] == 57
        assert data["total"] == 1234
        assert data["peak"] == 88
        # by_lang_label 은 한국어/중국어 뷰어 수 요약
        assert "45" in data["by_lang_label"]
        assert "12" in data["by_lang_label"]

    def test_none_db_metrics_treated_as_zero(self):
        """get_viewer_metrics 가 None 을 반환해도 안전."""
        from admin_logic import build_room_metrics_view_data

        data = build_room_metrics_view_data(
            in_memory={"current": 3, "by_lang": {"ko": 3}},
            db_metrics=None,
        )
        assert data["current"] == 3
        assert data["total"] == 0
        assert data["peak"] == 0


# ---------------------------------------------------------------------------
# 6. admin.py 표시 코드 (string-match 보조 검증)
# ---------------------------------------------------------------------------
class TestAdminDashboardMetricsWiring:
    """AC: admin.py 가 build_room_metrics_view_data 와 BroadcastManager
    snapshot 을 호출하는 헬퍼를 노출한다.

    UI 렌더는 Streamlit 의존성이 강해 직접 테스트하지 않고, 헬퍼가 import
    가능하며 호출 가능한지만 보장한다 (회귀 방지용 가드 — RL-005 와 일치).
    """

    def test_admin_module_exposes_render_metrics_helper(self):
        import admin

        # ISSUE-33 의 _render_room_viewer_metrics 헬퍼가 존재해야 한다.
        msg = "admin._render_room_viewer_metrics is required by ISSUE-33 wiring"
        assert hasattr(admin, "_render_room_viewer_metrics"), msg

    def test_admin_logic_exposes_build_helper(self):
        import admin_logic

        assert hasattr(admin_logic, "build_room_metrics_view_data")


# ---------------------------------------------------------------------------
# 7. RoomManager hydrate — 누적/peak 보존
# ---------------------------------------------------------------------------
class TestRoomManagerHydrateMetrics:
    """AC: 서버 재시작 시 누적/최대 지표가 DB 에서 복원된다.

    인메모리 current 는 0 부터 시작하지만, DB persist 된 total/peak 는
    Room.get_viewer_metrics(room_id) 로 그대로 조회 가능해야 한다.
    """

    def test_db_metrics_persist_across_room_repo_recreation(
        self, db_path, admin_user_id
    ):
        from database import DatabaseManager, Room

        dbm = DatabaseManager(db_path)
        room_model = Room(dbm)
        room_model.create(room_id="rh", name="Hydrate", created_by=admin_user_id)
        room_model.update_viewer_metrics("rh", total_delta=10, current=4)
        room_model.update_viewer_metrics("rh", total_delta=1, current=7)

        # "재시작" 시뮬레이션: 새 DatabaseManager + Room 인스턴스 — 메모리 상태
        # 와 무관하게 같은 SQLite 파일을 읽는다.
        dbm2 = DatabaseManager(db_path)
        room_model2 = Room(dbm2)
        m = room_model2.get_viewer_metrics("rh")
        assert m["total_viewers"] == 11
        assert m["peak_viewers"] == 7
