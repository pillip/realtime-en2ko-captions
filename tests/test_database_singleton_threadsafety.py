"""DB 싱글턴 게터가 스레드 안전한가 — #158 부팅 경로가 드러낸 경합.

무슨 일이 있었나
----------------
`serve.py` 가 `start_background_servers()` 를 부르면 WS 스레드가 뜨고, 그
스레드는 `websocket_handler` 안에서 `get_room_model()` 을 부른다 (ISSUE-26
hydration). 거의 동시에 메인 스레드도 SSE 브로드캐스터용으로
`get_room_model()` 을 부른다. 두 호출 모두 `_db_manager is None` 을 보고 각자
`DatabaseManager` 를 만들어 `init_database()` 를 돌렸다.

`init_database()` 의 ALTER TABLE 마이그레이션들은 `PRAGMA table_info` 로
"컬럼이 없으면 추가" 하는 **check-then-act** 다. 두 스레드가 동시에 돌면 둘 다
"없음" 을 보고 둘 다 ALTER 를 쏘고, 진 쪽이 터진다. 프로덕션 2026-10-22 부팅
로그가 정확히 그것이었다::

    [SSE] 서버 스레드 시작 실패: OperationalError('duplicate column name: stage_config')
    [Boot] 백그라운드 서버 일부 기동 실패: ["sse: OperationalError(...)"]

WS(8765)는 떴고 SSE(8766)만 죽어서 `/view/*`·`/stream/*`·`/stage/*`·
`/branding/*` 가 전부 ALB 502 였다 — QR 을 스캔한 관객은 흰 화면.

왜 이전에는 안 터졌나
---------------------
기존 `app.py` 경로에서는 로그인 화면을 그리는 과정에서 `_db_manager` 가 이미
채워진 **뒤에** 스레드가 떴다. `serve.py` 는 완전히 차가운 상태에서 첫 호출이
동시에 들어간다. 게다가 ALTER 가 **실제로 실행될 때만** 경합 창이 열리므로,
컬럼이 이미 다 있는 DB 에서는 이 버그가 보이지 않는다. 그래서 신규 배포 /
CI / 새 개발자 로컬처럼 **스키마가 뒤처진 DB 로 처음 부팅하는 환경**에서만
터진다.

고치는 지점
-----------
마이그레이션의 PRAGMA 게이팅은 그대로 둔다 — `database.py` 주석이 "blindly
running ALTER TABLE twice raises 'duplicate column'" 이라며 try/except 대신
PRAGMA 를 택한 이유를 밝혀 두었고, 거길 try/except 로 바꾸면 무관한 ALTER
실패를 삼킨다. 고칠 곳은 **싱글턴 게터**다: 한 프로세스에서
`init_database()` 는 정확히 한 번만 돌아야 한다.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

# 동시 호출 스레드 수. 2 로도 경합은 나지만, 배리어로 동시에 풀어도 스케줄러가
# 직렬화해 버리는 경우가 있어 넉넉하게 둔다.
THREADS = 8


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """`database` 모듈 싱글턴을 비우고 DB 를 임시 경로로 돌린다.

    `monkeypatch.setattr` 로 전역을 비우므로 테스트가 끝나면 원래 값이
    복원된다 — 다른 테스트의 싱글턴을 오염시키지 않는다.
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))

    import database

    for name in ("_db_manager", "_user_model", "_usage_log_model", "_room_model"):
        monkeypatch.setattr(database, name, None)
    return database


def _run_concurrently(fn, n=THREADS):
    """배리어로 n 개 스레드를 동시에 풀고 (결과, 예외) 를 모아 돌려준다."""
    barrier = threading.Barrier(n)
    results: list = []
    errors: list[BaseException] = []

    def worker():
        barrier.wait()
        try:
            results.append(fn())
        except BaseException as e:  # noqa: BLE001 - 경합이 무엇을 던지든 기록한다
            errors.append(e)

    threads = [threading.Thread(target=worker, name=f"race{i}") for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "스레드가 60초 안에 끝나지 않았다"
    return results, errors


class TestInitRunsExactlyOnce:
    """근본 불변식 — 한 프로세스에서 `init_database()` 는 한 번만 돈다."""

    def test_concurrent_get_db_manager_initialises_once(self, fresh_db, monkeypatch):
        calls: list[str] = []
        real_init = fresh_db.DatabaseManager.init_database

        def counting_init(self):
            calls.append(threading.current_thread().name)
            # check-then-act 창을 명시적으로 벌린다. 실제 init_database 는
            # CREATE TABLE 여러 개 + 마이그레이션 5개를 돌아 충분히 느리지만,
            # 테스트가 그 우연한 느림에 의존하지 않게 한다.
            time.sleep(0.05)
            return real_init(self)

        monkeypatch.setattr(fresh_db.DatabaseManager, "init_database", counting_init)

        managers, errors = _run_concurrently(fresh_db.get_db_manager)

        assert not errors, f"동시 호출이 예외를 냈다: {errors!r}"
        assert len(calls) == 1, (
            f"init_database 가 {len(calls)}번 돌았다 ({calls}). 싱글턴 게터에 락이 "
            "없으면 두 스레드가 각자 DatabaseManager 를 만들고, 마이그레이션의 "
            "PRAGMA table_info check-then-act 가 깨진다 — 프로덕션에서 "
            "duplicate column name: stage_config 로 SSE(8766)가 죽었다 (#158)."
        )
        assert len({id(m) for m in managers}) == 1, (
            "서로 다른 DatabaseManager 인스턴스가 반환됐다 — 싱글턴이 아니다. "
            "같은 DB 파일에 대한 중복 연결 관리자가 생긴다."
        )

    @pytest.mark.parametrize(
        "getter_name", ["get_user_model", "get_usage_log_model", "get_room_model"]
    )
    def test_model_getters_are_singletons_under_concurrency(
        self, fresh_db, getter_name
    ):
        """모델 게터들도 같은 `global ... if None` 패턴이다.

        `get_room_model` 이 바로 프로덕션에서 두 스레드가 동시에 부른 함수다.
        """
        getter = getattr(fresh_db, getter_name)

        models, errors = _run_concurrently(getter)

        assert not errors, f"{getter_name} 동시 호출이 예외를 냈다: {errors!r}"
        assert len({id(m) for m in models}) == 1, (
            f"{getter_name} 이 서로 다른 인스턴스를 돌려줬다 — 락이 없어 여러 "
            "스레드가 각자 생성했다."
        )


class TestPendingMigrationUnderConcurrentBoot:
    """프로덕션 장애의 재현 — ALTER 가 실제로 돌아야 경합 창이 열린다."""

    @staticmethod
    def _columns(db_path) -> set[str]:
        with sqlite3.connect(db_path) as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(rooms)")}

    def test_cold_boot_with_a_missing_column_does_not_duplicate_it(
        self, fresh_db, tmp_path, monkeypatch
    ):
        """스키마가 뒤처진 DB 로 동시 부팅해도 마이그레이션이 깨지지 않는다.

        배포된 컨테이너가 ISSUE-37 이전 코드였으므로 `rooms.stage_config` ALTER
        가 새 코드 **첫 부팅에서 처음** 실행됐다. 컬럼이 이미 있으면 ALTER 가
        아예 안 돌아 이 버그가 숨는다 — 그래서 일부러 떼어낸 뒤 부팅한다.
        """
        db_path = tmp_path / "app.db"

        # 1) 정상 경로로 스키마를 만든다.
        fresh_db.DatabaseManager(str(db_path))
        assert "stage_config" in self._columns(db_path)

        # 2) 컬럼을 떼어 "마이그레이션 대기" 상태를 만든다 (SQLite 3.35+).
        with sqlite3.connect(db_path) as conn:
            conn.execute("ALTER TABLE rooms DROP COLUMN stage_config")
            conn.commit()
        assert "stage_config" not in self._columns(db_path)

        # 3) 차가운 싱글턴으로 동시 부팅 — 프로덕션 serve.py 와 같은 조건.
        monkeypatch.setattr(fresh_db, "_db_manager", None)
        monkeypatch.setattr(fresh_db, "_room_model", None)

        _, errors = _run_concurrently(fresh_db.get_room_model)

        assert not errors, (
            f"동시 부팅이 마이그레이션을 깼다: {errors!r}. 'duplicate column name' "
            "이면 두 스레드가 각자 init_database() 를 돌린 것이다 — 프로덕션에서 "
            "SSE 서버가 이렇게 죽었다 (#158)."
        )
        assert "stage_config" in self._columns(db_path), (
            "마이그레이션이 컬럼을 복구하지 못했다 — 동시 부팅에서 ALTER 가 "
            "유실됐다."
        )
