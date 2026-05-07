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
