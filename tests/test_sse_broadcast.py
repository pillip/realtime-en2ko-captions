"""
SSE 브로드캐스트 + viewer 엔드포인트 단위 테스트 (ISSUE-30).

검증 대상:
- BroadcastManager: register_viewer / unregister_viewer / publish / has_viewers
- SSE 엔드포인트:
    - 정상 룸 연결: text/event-stream 헤더, JSON 라인, 메시지 수신
    - 존재하지 않는 룸 → 404
    - closed 상태 룸 → session_end 이벤트 후 종료
- Lazy translation gate: 뷰어 없는 언어는 추가 번역 스킵
- 메인 언어 번역이 추가 언어 번역 sleep 에 블로킹되지 않음
- rooms 테이블 마이그레이션 (primary_output_lang / output_langs) 멱등성
- RL-006: 내부 에러 텍스트가 SSE 응답으로 누설되지 않음

Note: SQLite tmp_path 격리. 외부 네트워크 호출 없음.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

# websocket_handler -> auth -> streamlit 의존성 mock (다른 테스트와 동일 패턴)
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ---------------------------------------------------------------------------
# Fixtures (DB 격리)
# ---------------------------------------------------------------------------
@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "sse.db")


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


# ---------------------------------------------------------------------------
# Migration tests — rooms.primary_output_lang / output_langs (idempotent)
# ---------------------------------------------------------------------------
class TestRoomsMigrationLangs:
    """ISSUE-30: rooms 테이블에 primary_output_lang / output_langs 추가."""

    def test_columns_present_on_fresh_db(self, db_manager):
        """새로 만든 DB 에 두 컬럼이 모두 존재한다."""
        with db_manager.get_connection() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(rooms)")}
        assert "primary_output_lang" in cols
        assert "output_langs" in cols

    def test_defaults_are_correct(self, db_manager, admin_user_id, room_model):
        """기본값: primary_output_lang='ko', output_langs='[\"ko\"]'."""
        room = room_model.create(
            room_id="r1",
            name="Room 1",
            created_by=admin_user_id,
        )
        assert room is not None
        assert room["primary_output_lang"] == "ko"
        # SQLite 는 TEXT 디폴트를 그대로 반환
        assert room["output_langs"] == '["ko"]'

    def test_migration_is_idempotent(self, db_path, admin_user_id):
        """동일 DB 에 init_database() 가 다시 호출되어도 ALTER 가 한 번만 실행된다."""
        # First init already happened via fixture chain through DatabaseManager(path)
        # Run init_database again — should be a silent no-op for the new columns.
        from database import DatabaseManager

        db1 = DatabaseManager(db_path)
        # Touch the migration helper directly multiple times — must not raise.
        db1._migrate_add_room_output_lang_columns()
        db1._migrate_add_room_output_lang_columns()

        with db1.get_connection() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(rooms)")}
        assert "primary_output_lang" in cols
        assert "output_langs" in cols

    def test_legacy_db_without_columns_gets_migrated(self, tmp_path):
        """레거시 (rooms 테이블만 있고 새 컬럼이 없는) DB 도 새 컬럼을 얻는다."""
        db_file = str(tmp_path / "legacy.db")
        # 손수 만든 레거시 rooms 스키마 (ISSUE-26 직후 상태) 시뮬레이션.
        conn = sqlite3.connect(db_file)
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            )
            """
        )
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
        # 시드 데이터 — 이 row 가 마이그레이션 후에도 살아있어야 한다.
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES ('legacyadmin','x')"
        )
        conn.execute(
            "INSERT INTO rooms (id, name, created_by) "
            "VALUES ('legacy-room', 'Legacy', 1)"
        )
        conn.commit()
        conn.close()

        from database import DatabaseManager

        db = DatabaseManager(db_file)
        with db.get_connection() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(rooms)")}
            assert "primary_output_lang" in cols
            assert "output_langs" in cols
            row = conn.execute(
                "SELECT id, primary_output_lang, output_langs FROM rooms "
                "WHERE id='legacy-room'"
            ).fetchone()
            assert row is not None
            # NULL 또는 디폴트 ko 어느 쪽이든 허용 (ALTER ADD COLUMN 의 기본값
            # 적용 동작은 SQLite 버전에 따라 다를 수 있다). 핵심은 컬럼 자체가
            # 존재하고 row 가 그대로 살아있다는 점.
            assert row["id"] == "legacy-room"


# ---------------------------------------------------------------------------
# BroadcastManager — register / unregister / publish / has_viewers
# ---------------------------------------------------------------------------
class TestBroadcastManager:
    @pytest.mark.asyncio
    async def test_register_returns_queue_and_has_viewers_flips(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        assert mgr.has_viewers("r1", "ko") is False

        q = await mgr.register_viewer("r1", "ko")
        try:
            assert q is not None
            assert mgr.has_viewers("r1", "ko") is True
            # Different lang on same room is independent
            assert mgr.has_viewers("r1", "en") is False
        finally:
            await mgr.unregister_viewer("r1", "ko", q)

        # After unregister the channel must be empty again
        assert mgr.has_viewers("r1", "ko") is False

    @pytest.mark.asyncio
    async def test_publish_delivers_to_all_subscribers_of_lang(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        q1 = await mgr.register_viewer("r1", "ko")
        q2 = await mgr.register_viewer("r1", "ko")
        q_en = await mgr.register_viewer("r1", "en")

        payload = {"text": "안녕", "lang": "ko", "timestamp": 1.0}
        await mgr.publish("r1", "ko", payload)

        # Korean subscribers receive
        got1 = await asyncio.wait_for(q1.get(), timeout=0.5)
        got2 = await asyncio.wait_for(q2.get(), timeout=0.5)
        assert got1 == payload
        assert got2 == payload

        # English subscriber must NOT receive a Korean publish
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q_en.get(), timeout=0.05)

        # Cleanup
        await mgr.unregister_viewer("r1", "ko", q1)
        await mgr.unregister_viewer("r1", "ko", q2)
        await mgr.unregister_viewer("r1", "en", q_en)

    @pytest.mark.asyncio
    async def test_publish_with_no_viewers_is_noop(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        # Must not raise even without any viewers
        await mgr.publish("r1", "ko", {"text": "hi"})
        assert mgr.has_viewers("r1", "ko") is False

    @pytest.mark.asyncio
    async def test_unregister_unknown_is_safe(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        # Unregistering a never-registered queue is a no-op, never raises.
        fake_q: asyncio.Queue = asyncio.Queue()
        await mgr.unregister_viewer("ghost", "ko", fake_q)


# ---------------------------------------------------------------------------
# SSE handler — endpoint behaviour (using aiohttp TestClient)
# ---------------------------------------------------------------------------
class TestSSEEndpoint:
    @pytest.mark.asyncio
    async def test_unknown_room_returns_404(self, db_manager):
        """존재하지 않는 룸 → 404 (RL-006: 본문에 내부 에러 누설 금지)."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo({})
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/stream/no-such-room")
            assert resp.status == 404
            text = await resp.text()
            # RL-006 — generic message, no path/exception detail
            assert "Traceback" not in text
            assert "sqlite3" not in text

    @pytest.mark.asyncio
    async def test_closed_room_emits_session_end_then_closes(self, db_manager):
        """closed 상태 룸 → 'session_end' 이벤트 송신 후 연결 종료."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo(
            {
                "r1": {
                    "id": "r1",
                    "status": "closed",
                    "primary_output_lang": "ko",
                    "output_langs": '["ko"]',
                }
            }
        )
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/stream/r1")
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            body = await resp.text()
            # session_end event present in the streamed body
            assert "session_end" in body

    @pytest.mark.asyncio
    async def test_active_room_streams_published_payloads(self, db_manager):
        """publish() 가 SSE 클라이언트에 도달한다 (메인 언어 채널)."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo(
            {
                "r1": {
                    "id": "r1",
                    "status": "active",
                    "primary_output_lang": "ko",
                    "output_langs": '["ko","en"]',
                }
            }
        )
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            # Open SSE connection
            resp = await client.get("/stream/r1?lang=ko")
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")

            # The handler registers a viewer asynchronously; give it a beat.
            await asyncio.sleep(0.05)
            assert mgr.has_viewers("r1", "ko") is True

            # Publish a payload
            payload = {"text": "안녕하세요", "lang": "ko", "timestamp": 1.5}
            await mgr.publish("r1", "ko", payload)

            # Read until we collect a data event (handler may emit a
            # ": connected" keep-alive comment first per SSE spec).
            buf = b""
            deadline = asyncio.get_event_loop().time() + 1.5
            while b"data: " not in buf and asyncio.get_event_loop().time() < deadline:
                chunk = await asyncio.wait_for(resp.content.read(256), timeout=0.5)
                if not chunk:
                    break
                buf += chunk
            text = buf.decode("utf-8")
            assert "data: " in text, text
            # Each SSE event line ends with \n\n
            assert "\n\n" in text
            data_line = text.split("data: ", 1)[1].split("\n\n", 1)[0]
            decoded = json.loads(data_line)
            assert decoded["text"] == "안녕하세요"
            assert decoded["lang"] == "ko"

            resp.close()

    @pytest.mark.asyncio
    async def test_lang_query_param_selects_specific_language_channel(self, db_manager):
        """?lang=ko 파라미터로 특정 언어 자막만 수신할 수 있다 (AC).

        ?lang=en 으로 접속하면 en 채널만 구독하고, ko 채널 publish 는 받지 않는다.
        """
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo(
            {
                "r1": {
                    "id": "r1",
                    "status": "active",
                    "primary_output_lang": "ko",
                    "output_langs": '["ko","en"]',
                }
            }
        )
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/stream/r1?lang=en")
            assert resp.status == 200
            await asyncio.sleep(0.05)
            # 명시적으로 en 채널만 구독되어야 한다
            assert mgr.has_viewers("r1", "en") is True
            assert mgr.has_viewers("r1", "ko") is False
            resp.close()

    @pytest.mark.asyncio
    async def test_lang_param_defaults_to_primary_output_lang(self, db_manager):
        """?lang 생략 시 룸의 primary_output_lang 채널을 구독한다."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo(
            {
                "r1": {
                    "id": "r1",
                    "status": "active",
                    "primary_output_lang": "en",
                    "output_langs": '["ko","en"]',
                }
            }
        )
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/stream/r1")  # no ?lang
            assert resp.status == 200
            await asyncio.sleep(0.05)
            assert mgr.has_viewers("r1", "en") is True
            assert mgr.has_viewers("r1", "ko") is False
            resp.close()


# ---------------------------------------------------------------------------
# Lazy translation + non-blocking main publish
# ---------------------------------------------------------------------------
class TestLazyTranslationAndNonBlocking:
    """ISSUE-30 핵심 AC:
    - 메인 언어 번역이 추가 언어 번역에 블로킹되지 않음
    - 뷰어 없는 추가 언어는 번역을 스킵
    """

    @pytest.mark.asyncio
    async def test_main_publish_not_blocked_by_secondary_translation(self):
        """추가 언어 번역에 sleep(0.5) 을 주입해도 메인 publish 는 즉시 일어난다."""
        from sse_broadcast import (
            BroadcastManager,
            broadcast_translation_for_room,
        )

        mgr = BroadcastManager()
        # 메인 언어 ko, 추가 언어 en.
        room = {
            "id": "r1",
            "primary_output_lang": "ko",
            "output_langs": ["ko", "en"],
        }
        # 메인 + 추가 언어 모두 viewer 등록
        ko_q = await mgr.register_viewer("r1", "ko")
        en_q = await mgr.register_viewer("r1", "en")

        slow_calls: list[str] = []

        async def fake_translate(text: str, src: str, dst: str) -> str:
            if dst == "en":
                slow_calls.append("en-start")
                await asyncio.sleep(0.5)  # secondary lang is artificially slow
                slow_calls.append("en-done")
                return "Hello"
            return "안녕"  # primary path is fast

        translated_main = "안녕"
        start = time.monotonic()
        await broadcast_translation_for_room(
            mgr,
            room,
            primary_translated=translated_main,
            source_text="hi",
            source_lang="en",
            translate_fn=fake_translate,
        )
        # The primary publish must arrive in well under 0.5s — the secondary
        # translation runs as a background task and does not block.
        elapsed_main = time.monotonic() - start
        assert elapsed_main < 0.3, (
            f"primary publish took {elapsed_main:.3f}s, "
            "must not be blocked by secondary"
        )

        # The primary message arrives essentially immediately.
        msg_main = await asyncio.wait_for(ko_q.get(), timeout=0.2)
        assert msg_main["text"] == "안녕"
        assert msg_main["lang"] == "ko"
        assert "timestamp" in msg_main

        # Then the secondary message arrives later.
        msg_secondary = await asyncio.wait_for(en_q.get(), timeout=2.0)
        assert msg_secondary["text"] == "Hello"
        assert msg_secondary["lang"] == "en"
        assert "en-done" in slow_calls

    @pytest.mark.asyncio
    async def test_secondary_translation_skipped_when_no_viewers(self):
        """추가 언어 채널에 viewer 가 없으면 translate_fn 이 호출되지 않는다."""
        from sse_broadcast import (
            BroadcastManager,
            broadcast_translation_for_room,
        )

        mgr = BroadcastManager()
        room = {
            "id": "r1",
            "primary_output_lang": "ko",
            "output_langs": ["ko", "en", "ja"],
        }
        # Only ko has a viewer; en/ja must be skipped.
        ko_q = await mgr.register_viewer("r1", "ko")

        secondary_calls: list[tuple[str, str]] = []

        async def fake_translate(text: str, src: str, dst: str) -> str:
            secondary_calls.append((src, dst))
            return f"[{dst}]{text}"

        await broadcast_translation_for_room(
            mgr,
            room,
            primary_translated="안녕",
            source_text="hi",
            source_lang="en",
            translate_fn=fake_translate,
        )
        # Wait briefly for any background task to either run or be skipped.
        await asyncio.sleep(0.1)

        # No translate_fn calls for secondary languages
        assert secondary_calls == [], (
            f"translate_fn should not be called for secondary langs, "
            f"got {secondary_calls}"
        )

        # Primary still arrived
        msg = await asyncio.wait_for(ko_q.get(), timeout=0.2)
        assert msg["text"] == "안녕"

    @pytest.mark.asyncio
    async def test_secondary_translation_only_for_langs_with_viewers(self):
        """추가 언어 중 viewer 있는 언어만 번역한다."""
        from sse_broadcast import (
            BroadcastManager,
            broadcast_translation_for_room,
        )

        mgr = BroadcastManager()
        room = {
            "id": "r1",
            "primary_output_lang": "ko",
            "output_langs": ["ko", "en", "ja", "zh"],
        }
        await mgr.register_viewer("r1", "ko")
        en_q = await mgr.register_viewer("r1", "en")
        # ja, zh 는 viewer 없음 — 번역 스킵 대상

        secondary_calls: list[str] = []

        async def fake_translate(text: str, src: str, dst: str) -> str:
            secondary_calls.append(dst)
            return f"[{dst}]{text}"

        await broadcast_translation_for_room(
            mgr,
            room,
            primary_translated="안녕",
            source_text="hi",
            source_lang="en",
            translate_fn=fake_translate,
        )

        msg = await asyncio.wait_for(en_q.get(), timeout=2.0)
        assert msg["text"] == "[en]hi"
        # ja, zh 는 호출되지 않음
        assert secondary_calls == ["en"], secondary_calls


# ---------------------------------------------------------------------------
# RL-006 — error path doesn't leak repository exceptions
# ---------------------------------------------------------------------------
class TestNoErrorLeakOnRepoFailure:
    @pytest.mark.asyncio
    async def test_repo_exception_returns_generic_404(self, db_manager):
        """레포 조회 중 예외 발생 시에도 SSE 응답에 내부 텍스트가 노출되지 않는다."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        class ExplodingRepo:
            def get_by_id(self, room_id):
                raise RuntimeError("DB internal: secret/path/to/db.sqlite locked")

        mgr = BroadcastManager()
        app = build_sse_app(
            broadcast_manager=mgr,
            room_repo=ExplodingRepo(),
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/stream/anything")
            assert resp.status in (404, 500)
            body = await resp.text()
            assert "secret/path" not in body
            assert "RuntimeError" not in body
            assert "Traceback" not in body


# ---------------------------------------------------------------------------
# Stub repo (lightweight DB-free fake)
# ---------------------------------------------------------------------------
class _StubRoomRepo:
    """Minimal fake of database.Room for endpoint tests.

    Stores rows keyed by id; mimics .get_by_id() returning dict | None.
    """

    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)


# ---------------------------------------------------------------------------
# publish queue-saturation paths (lines 221-231)
# ---------------------------------------------------------------------------
class TestPublishSaturationPaths:
    """publish 의 QueueFull 처리 분기 — 가장 오래된 메시지 제거 / 더블 saturation."""

    @pytest.mark.asyncio
    async def test_publish_drops_oldest_when_queue_full_and_delivers_new(self):
        """큐가 가득 차면 oldest 1건 삭제 후 새 payload 가 들어간다."""
        from sse_broadcast import BroadcastManager

        # maxsize=1 으로 만들어서 두 번째 publish 가 saturation 분기를 타게 함.
        mgr = BroadcastManager(queue_maxsize=1)
        q = await mgr.register_viewer("r1", "ko")

        first = {"text": "first", "lang": "ko", "timestamp": 1.0}
        second = {"text": "second", "lang": "ko", "timestamp": 2.0}

        await mgr.publish("r1", "ko", first)
        # 두 번째 publish 는 큐 가득 → get_nowait 후 put.
        await mgr.publish("r1", "ko", second)

        # get_nowait 의 결과는 second (oldest 인 first 가 빠진 뒤 second 가 들어감).
        got = q.get_nowait()
        assert got == second
        # 큐는 다시 비어 있어야 한다.
        assert q.qsize() == 0

        await mgr.unregister_viewer("r1", "ko", q)

    @pytest.mark.asyncio
    async def test_publish_double_saturation_drops_message_and_logs(self, capsys):
        """두 번째 put_nowait 도 QueueFull → 메시지 drop + 서버 로그."""
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager(queue_maxsize=1)
        q = await mgr.register_viewer("r1", "ko")
        # 큐를 미리 채워둠.
        q.put_nowait({"text": "preexisting"})

        # put_nowait 를 항상 QueueFull raise — 동시 producer 시뮬레이션.
        original_put = q.put_nowait

        def _always_full(_payload):
            raise asyncio.QueueFull()

        q.put_nowait = _always_full
        # get_nowait 도 QueueEmpty 를 raise 하도록 만들어 swallow 분기를 친다.
        original_get = q.get_nowait

        def _empty(_=None):
            raise asyncio.QueueEmpty()

        q.get_nowait = _empty

        try:
            await mgr.publish("r1", "ko", {"text": "drop-me"})
        finally:
            q.put_nowait = original_put
            q.get_nowait = original_get

        captured = capsys.readouterr()
        # drop 로그가 남아야 한다.
        assert "[SSE] dropping payload" in captured.out
        assert "viewer queue saturated" in captured.out

        await mgr.unregister_viewer("r1", "ko", q)

    @pytest.mark.asyncio
    async def test_publish_recovers_when_get_raises_queue_empty(self):
        """QueueFull 직후 get_nowait 가 QueueEmpty (race) → swallow 후 retry put."""
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager(queue_maxsize=1)
        q = await mgr.register_viewer("r1", "ko")

        # put_nowait 를 첫 호출에서만 QueueFull 을 raise, 그 다음엔 정상 동작.
        original_put = q.put_nowait
        call_count = {"n": 0}
        captured_payloads = []

        def _put(payload):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise asyncio.QueueFull()
            captured_payloads.append(payload)
            return original_put(payload)

        # get_nowait 는 항상 QueueEmpty (race 시뮬레이션).
        original_get = q.get_nowait

        def _empty(_=None):
            raise asyncio.QueueEmpty()

        q.put_nowait = _put
        q.get_nowait = _empty

        try:
            await mgr.publish("r1", "ko", {"text": "racy"})
        finally:
            q.put_nowait = original_put
            q.get_nowait = original_get

        # 두 번째 put 시도가 성공해 payload 가 들어갔어야 한다.
        assert captured_payloads == [{"text": "racy"}]

        await mgr.unregister_viewer("r1", "ko", q)


# ---------------------------------------------------------------------------
# _coerce_output_langs (lines 681-683 + parsing edges)
# ---------------------------------------------------------------------------
class TestCoerceOutputLangs:
    """_coerce_output_langs — JSON / 잘못된 형식 / 빈 입력에 대한 강건성."""

    def test_invalid_json_returns_fallback(self):
        from sse_broadcast import _coerce_output_langs

        assert _coerce_output_langs("not-json", "ko") == ["ko"]
        # 잘못된 JSON 텍스트도 fallback.
        assert _coerce_output_langs("{not-valid", "en") == ["en"]

    def test_json_but_not_list_returns_fallback(self):
        """JSON 으로 디코딩되지만 리스트가 아닌 경우 → fallback."""
        from sse_broadcast import _coerce_output_langs

        assert _coerce_output_langs('"ko"', "ko") == ["ko"]  # 문자열
        assert _coerce_output_langs('{"key": "ko"}', "ko") == ["ko"]  # 객체
        assert _coerce_output_langs("123", "ja") == ["ja"]  # 숫자

    def test_none_returns_fallback(self):
        from sse_broadcast import _coerce_output_langs

        assert _coerce_output_langs(None, "ko") == ["ko"]

    def test_empty_string_returns_fallback(self):
        from sse_broadcast import _coerce_output_langs

        # 빈 문자열은 falsy 이므로 isinstance(raw, str) and raw 를 통과 못함 →
        # 아래 fallback 분기로 떨어진다.
        assert _coerce_output_langs("", "ko") == ["ko"]

    def test_empty_list_returns_empty_list_passthrough(self):
        """빈 list 입력은 list 분기에서 그대로 빈 list 반환 (fallback 미사용).

        주의: _coerce_output_langs 자체는 빈 list 도 그대로 통과시킨다 —
        이는 _coerce_output_langs_list 가 fallback 을 prepend 하는 위쪽 wrapper
        의 역할이다. 이 테스트는 두 함수의 책임 분리를 명확히 한다.
        """
        from sse_broadcast import _coerce_output_langs

        assert _coerce_output_langs([], "ko") == []

    def test_valid_json_list_returns_parsed(self):
        from sse_broadcast import _coerce_output_langs

        assert _coerce_output_langs('["ko","en","ja"]', "ko") == ["ko", "en", "ja"]

    def test_filters_non_string_items(self):
        """list 의 비-문자열 요소는 제외된다 (방어)."""
        from sse_broadcast import _coerce_output_langs

        # JSON 안에 숫자/None 이 섞여 있어도 string 만 추려낸다.
        assert _coerce_output_langs('["ko",1,null,"en"]', "ko") == ["ko", "en"]


class TestCoerceOutputLangsListWithFallback:
    """_coerce_output_langs_list — fallback 을 항상 결과 안에 보존한다."""

    def test_fallback_prepended_when_missing(self):
        from sse_broadcast import _coerce_output_langs_list

        # fallback 'ja' 가 결과에 없으면 맨 앞에 prepend 돼야 한다.
        result = _coerce_output_langs_list('["ko","en"]', "ja")
        assert result == ["ja", "ko", "en"]

    def test_fallback_already_present_not_duplicated(self):
        from sse_broadcast import _coerce_output_langs_list

        result = _coerce_output_langs_list('["ko","en"]', "ko")
        assert result == ["ko", "en"]

    def test_invalid_input_returns_just_fallback(self):
        from sse_broadcast import _coerce_output_langs_list

        assert _coerce_output_langs_list(None, "ko") == ["ko"]
        assert _coerce_output_langs_list("not-json", "ko") == ["ko"]


# ---------------------------------------------------------------------------
# _supported_output_langs — 전역 지원 언어 고정 (#91/#92)
# ---------------------------------------------------------------------------
class TestSupportedOutputLangs:
    """뷰어 언어 목록·번역 대상이 룸 설정 대신 전역 목록을 쓴다."""

    def test_returns_full_supported_list(self):
        from sse_broadcast import _supported_output_langs
        from translation import SUPPORTED_OUTPUT_LANGS

        assert _supported_output_langs("ko") == list(SUPPORTED_OUTPUT_LANGS)

    def test_unknown_primary_is_prepended(self):
        from sse_broadcast import _supported_output_langs

        result = _supported_output_langs("xx")
        assert result[0] == "xx"
        assert "ko" in result and "en" in result

    def test_covers_langs_beyond_default_room_config(self):
        """기본 룸 설정(ko 뿐)이던 시절과 달리 전 언어가 선택 가능해야 한다."""
        from sse_broadcast import _supported_output_langs

        result = _supported_output_langs("ko")
        for lang in ("en", "ja", "zh", "vi"):
            assert lang in result


# ---------------------------------------------------------------------------
# /view/{room_id} (lines 367-399, 394-397)
# ---------------------------------------------------------------------------
class TestHandleView:
    """_handle_view — repo 예외 / 룸 미존재 / 템플릿 렌더 실패 분기."""

    @pytest.mark.asyncio
    async def test_unknown_room_returns_404_html(self):
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo({})
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/view/no-such-room")
            assert resp.status == 404
            text = await resp.text()
            # RL-006: generic friendly body, no internal detail.
            assert "Traceback" not in text
            assert "룸을 찾을 수 없습니다" in text

    @pytest.mark.asyncio
    async def test_repo_exception_returns_404_no_leak(self):
        """repo.get_by_id 가 raise → 404 + generic body (RL-006)."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        class ExplodingRepo:
            def get_by_id(self, room_id):
                raise RuntimeError("DB internal: secret/path/to/db.sqlite locked")

        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=ExplodingRepo())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/view/whatever")
            assert resp.status == 404
            text = await resp.text()
            # 디테일 누설 없음.
            assert "secret/path" not in text
            assert "RuntimeError" not in text
            assert "Traceback" not in text

    @pytest.mark.asyncio
    async def test_template_render_failure_returns_404(self, monkeypatch):
        """_render_viewer_html 이 raise → 404 with generic body, no traceback."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo(
            {
                "r1": {
                    "id": "r1",
                    "status": "active",
                    "primary_output_lang": "ko",
                    "output_langs": '["ko","en"]',
                    "name": "Room 1",
                }
            }
        )

        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)

        # _render_viewer_html 만 패치해 raise 시킴.
        import sse_broadcast as _sb

        def _explode(**_kwargs):
            raise FileNotFoundError("template missing /secret/path/to/viewer.html")

        monkeypatch.setattr(_sb, "_render_viewer_html", _explode)

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/view/r1")
            assert resp.status == 404
            text = await resp.text()
            assert "Traceback" not in text
            assert "secret/path" not in text
            # generic body 가 그대로 나가야 한다.
            assert "룸을 찾을 수 없습니다" in text


# ---------------------------------------------------------------------------
# _handle_health (line 265)
# ---------------------------------------------------------------------------
class TestHandleHealth:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo({})
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/health")
            assert resp.status == 200
            text = await resp.text()
            assert text == "OK"


# ---------------------------------------------------------------------------
# channel_count (line 238)
# ---------------------------------------------------------------------------
class TestChannelCount:
    @pytest.mark.asyncio
    async def test_channel_count_reflects_registered_channels(self):
        from sse_broadcast import BroadcastManager

        mgr = BroadcastManager()
        assert mgr.channel_count() == 0

        q1 = await mgr.register_viewer("r1", "ko")
        q2 = await mgr.register_viewer("r1", "en")
        # 같은 room+lang 에 두 번째 viewer — channel_count 는 변하지 않음.
        q3 = await mgr.register_viewer("r1", "ko")

        assert mgr.channel_count() == 2

        await mgr.unregister_viewer("r1", "ko", q1)
        # 아직 q3 가 ko 채널에 남아 있음.
        assert mgr.channel_count() == 2

        await mgr.unregister_viewer("r1", "ko", q3)
        # ko 채널 비어서 drop.
        assert mgr.channel_count() == 1

        await mgr.unregister_viewer("r1", "en", q2)
        assert mgr.channel_count() == 0


# ---------------------------------------------------------------------------
# run_sse_server exception handling (lines 702-720)
# ---------------------------------------------------------------------------
class TestRunSseServer:
    """run_sse_server 가 모든 startup 예외를 삼키고 로그만 남기는지 검증."""

    def test_app_runner_raises_does_not_propagate(self, capsys, monkeypatch):
        """web.AppRunner 가 raise → 함수는 return, 서버 로그에 에러 기록."""
        # web.AppRunner 자체를 패치해 인스턴스화 시 raise 하도록.
        import sse_broadcast as _sb
        from sse_broadcast import BroadcastManager, run_sse_server

        class _ExplodingRunner:
            def __init__(self, _app):
                raise RuntimeError("runner cannot be created in this env")

        monkeypatch.setattr(_sb.web, "AppRunner", _ExplodingRunner)

        mgr = BroadcastManager()
        repo = _StubRoomRepo({})

        # 절대 raise 하면 안 됨.
        run_sse_server(broadcast_manager=mgr, room_repo=repo, port=0)

        captured = capsys.readouterr()
        assert "[SSE] server failed to start" in captured.out

    def test_event_loop_creation_failure_swallowed(self, capsys, monkeypatch):
        """asyncio.new_event_loop 자체가 raise → 그래도 함수가 return."""
        import sse_broadcast as _sb
        from sse_broadcast import BroadcastManager, run_sse_server

        def _fail_loop():
            raise OSError("no event loop available")

        monkeypatch.setattr(_sb.asyncio, "new_event_loop", _fail_loop)

        mgr = BroadcastManager()
        repo = _StubRoomRepo({})

        run_sse_server(broadcast_manager=mgr, room_repo=repo, port=0)

        captured = capsys.readouterr()
        assert "[SSE] server failed to start" in captured.out


# ---------------------------------------------------------------------------
# _translate_and_publish_secondary error paths (lines 641-661)
# ---------------------------------------------------------------------------
class TestTranslateAndPublishSecondary:
    """_translate_and_publish_secondary — translate_fn / publish 예외 격리 검증."""

    @pytest.mark.asyncio
    async def test_translate_fn_raises_swallowed_no_publish(self, capsys):
        """translate_fn 이 raise → 로그 후 publish 미호출, 함수는 return."""
        from sse_broadcast import BroadcastManager, _translate_and_publish_secondary

        mgr = BroadcastManager()
        q = await mgr.register_viewer("r1", "en")

        async def _failing_translate(text, src, dst):
            raise RuntimeError("bedrock down")

        # 절대 raise 하면 안 됨.
        await _translate_and_publish_secondary(
            mgr,
            "r1",
            source_text="hi",
            source_lang="en",
            target_lang="en",
            translate_fn=_failing_translate,
        )

        captured = capsys.readouterr()
        assert "[SSE] secondary translate failed" in captured.out
        # 큐에는 아무것도 들어가지 않았어야 한다.
        assert q.qsize() == 0
        await mgr.unregister_viewer("r1", "en", q)

    @pytest.mark.asyncio
    async def test_translate_fn_returns_falsy_skips_publish(self):
        """translate_fn 이 None / 빈 문자열 → publish 미호출."""
        from sse_broadcast import BroadcastManager, _translate_and_publish_secondary

        mgr = BroadcastManager()
        q = await mgr.register_viewer("r1", "en")

        async def _translate_returns_none(text, src, dst):
            return None

        await _translate_and_publish_secondary(
            mgr,
            "r1",
            source_text="hi",
            source_lang="en",
            target_lang="en",
            translate_fn=_translate_returns_none,
        )

        # publish 가 호출되지 않았으므로 큐는 비어 있다.
        assert q.qsize() == 0

        # 빈 문자열 케이스도 동일.
        async def _translate_returns_empty(text, src, dst):
            return ""

        await _translate_and_publish_secondary(
            mgr,
            "r1",
            source_text="hi",
            source_lang="en",
            target_lang="en",
            translate_fn=_translate_returns_empty,
        )
        assert q.qsize() == 0

        await mgr.unregister_viewer("r1", "en", q)

    @pytest.mark.asyncio
    async def test_publish_raises_swallowed_in_background_task(
        self, capsys, monkeypatch
    ):
        """manager.publish 가 raise → 로그만, 함수는 raise 하지 않음."""
        from sse_broadcast import BroadcastManager, _translate_and_publish_secondary

        mgr = BroadcastManager()

        async def _publish_explodes(*_args, **_kwargs):
            raise RuntimeError("publish boom")

        # publish 만 패치.
        monkeypatch.setattr(mgr, "publish", _publish_explodes)

        async def _translate_ok(text, src, dst):
            return f"[{dst}]{text}"

        # 절대 raise 하면 안 됨.
        await _translate_and_publish_secondary(
            mgr,
            "r1",
            source_text="hi",
            source_lang="en",
            target_lang="ja",
            translate_fn=_translate_ok,
        )

        captured = capsys.readouterr()
        assert "[SSE] secondary publish failed" in captured.out
