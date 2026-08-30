"""
WebSocket↔SSE pipeline integration test (ISSUE-30).

Verifies that ``websocket_handler._handle_transcript`` correctly drives the
shared ``BroadcastManager`` so that real SSE viewers connected to
``/stream/{room_id}?lang=<code>`` actually receive the translated payload.

Boundaries
----------
- aiohttp SSE app is spun up on a free TCP port via ``build_sse_app`` and a
  daemon thread (mirrors ``tests/e2e/test_viewer_metrics_e2e.py``).
- WebSocket itself is mocked (``AsyncMock``).
- All translation calls are mocked — no real Bedrock / AWS Translate.
- DB layer mocked via small stub repos to avoid SQLite churn for what is
  fundamentally a publish-fanout test.

Why this test
-------------
- Exercises the join point between the WS publish path
  (``_handle_transcript`` → ``_publish_to_viewers``
  → ``broadcast_translation_for_room``) and the SSE delivery path
  (``_handle_stream``) — both are tested
  separately but a regression at the seam (e.g. wrong room_id, wrong lang
  channel, secondary lang scheduled before primary publish) would not be
  caught by either unit test alone.
- Per RL-005: this is a true integration test that would fail if the
  contract between modules drifts (e.g. handler stops writing into the
  module-level ``_broadcast_manager``).

Each test uses a unique room_id to keep the module-level
``_broadcast_manager`` from cross-talking between tests.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import sys
import threading
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# auth.py / websocket_handler import streamlit indirectly — stub before import.
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------
class _StubRoomRepo:
    """Lightweight fake of ``database.Room`` — only ``get_by_id`` is needed.

    Uses mutable dict storage so individual tests can register their own
    rooms (with isolated room_ids) without sharing state across tests.
    """

    def __init__(self, rows: dict[str, dict[str, Any]] | None = None):
        self._rows: dict[str, dict[str, Any]] = dict(rows or {})

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)

    def add(self, room: dict[str, Any]) -> None:
        self._rows[room["id"]] = room


class _RecordingMetricsRepo:
    """Stub of ``database.Room`` viewer_metrics interface used by SSE register."""

    def __init__(self) -> None:
        self.totals: dict[str, int] = {}
        self.peaks: dict[str, int] = {}

    def update_viewer_metrics(
        self, room_id: str, *, total_delta: int, current: int
    ) -> bool:
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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _open_sse_socket(host: str, port: int, room_id: str, lang: str) -> socket.socket:
    """Open an SSE GET to /stream/{room_id}?lang=<lang> and return the live socket.

    Reads up to the first chunk (the `: connected` keep-alive) so the server
    has finished registering the viewer with the BroadcastManager before the
    test publishes.
    """
    s = socket.create_connection((host, port), timeout=5)
    req = (
        f"GET /stream/{room_id}?lang={lang} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Accept: text/event-stream\r\n"
        "Connection: keep-alive\r\n\r\n"
    )
    s.sendall(req.encode("ascii"))
    # Set short timeout for the initial header/comment read so the test
    # doesn't hang if the server is misbehaving.
    s.settimeout(3.0)
    s.recv(256)  # headers + ": connected\n\n"
    return s


def _read_sse_data_line(sock: socket.socket, timeout: float = 3.0) -> dict[str, Any]:
    """Read from an SSE socket until a `data: ...` JSON event arrives, return it.

    Buffer-based: SSE frames may straddle TCP recv() chunks. We accumulate
    bytes and parse the first complete `data: <json>\\n` line.
    """
    sock.settimeout(timeout)
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = sock.recv(1024)
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
        if b"data: " in buf and b"\n\n" in buf:
            # Find the first complete data: <json>\n event
            idx = buf.find(b"data: ")
            after = buf[idx + len(b"data: ") :]
            end = after.find(b"\n")
            if end == -1:
                continue
            data_line = after[:end].decode("utf-8")
            return json.loads(data_line)
    raise AssertionError(f"No SSE data line received within {timeout}s. Buffer={buf!r}")


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Fixture: aiohttp SSE server in a daemon thread (per-test)
# ---------------------------------------------------------------------------
@pytest.fixture
def sse_server():
    """Spin up the aiohttp SSE app on a free port using the SAME
    BroadcastManager singleton that ``websocket_handler`` uses.

    Yields ``(base_url, mgr, repo, loop)`` where ``mgr`` is the
    module-level singleton (``websocket_handler._broadcast_manager``) and
    ``repo`` is a fresh stub repo each test populates with its own rooms.
    """
    from aiohttp import web

    import websocket_handler

    repo = _StubRoomRepo()
    metrics_repo = _RecordingMetricsRepo()

    # Use the *same* BroadcastManager that websocket_handler._handle_transcript
    # publishes into. Wire metrics_repo onto it so register_viewer side-
    # effects don't crash.
    mgr = websocket_handler._broadcast_manager
    mgr._metrics_repo = metrics_repo  # noqa: SLF001

    # Important: build_sse_app accepts the mgr + repo. The repo is shared
    # with the WS publish path via patching of _room_manager._repo below.
    from sse_broadcast import build_sse_app

    app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
    port = _free_port()
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    started = threading.Event()

    def _serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", port)
        loop.run_until_complete(site.start())
        started.set()
        loop.run_forever()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    assert started.wait(timeout=5), "aiohttp SSE server failed to start"

    base_url = f"http://127.0.0.1:{port}"
    try:
        yield (base_url, mgr, repo, loop)
    finally:
        try:
            asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(timeout=5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "ws_sse_int.db")


@pytest.fixture
def mock_db(db_path):
    """Real SQLite DB so ``_record_usage`` doesn't crash when invoked.

    The test boundaries don't depend on usage_logs content — we just need
    the table to exist so a write doesn't fail.
    """
    from database import DatabaseManager, User

    db = DatabaseManager(db_path)
    user_model = User(db)
    user_model.create_user(
        username="ws_sse_user",
        password="pw",
        role="user",
        usage_limit_seconds=3600,
    )
    return db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_user(user_id: int, room_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "username": "ws_sse_user",
        "role": "user",
        "full_name": None,
        "is_active": True,
        "room_id": room_id,
    }


def _make_room_row(
    room_id: str,
    *,
    primary: str = "ko",
    output_langs: list[str] | None = None,
    status: str = "active",
) -> dict[str, Any]:
    if output_langs is None:
        output_langs = [primary]
    return {
        "id": room_id,
        "name": f"Room {room_id}",
        "status": status,
        "primary_output_lang": primary,
        "output_langs": json.dumps(output_langs),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestWsToSsePipeline:
    """End-to-end (module-boundary) test for WS publish → SSE deliver."""

    def test_handle_transcript_publishes_to_subscribed_sse_viewer(
        self, sse_server, mock_db
    ):
        """Single SSE viewer subscribed to ko receives the translated text.

        ``_handle_transcript`` is invoked with a mocked websocket and a
        mocked ``_translate_text`` that returns "안녕". A real SSE socket
        connected to the same room/lang must receive that text on its
        ``data:`` line.
        """
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        base_url, mgr, repo, loop = sse_server
        host = "127.0.0.1"
        port = int(base_url.rsplit(":", 1)[1])

        # Register a fresh, unique room for this test.
        room_id = f"ws-sse-{uuid.uuid4().hex[:8]}"
        repo.add(_make_room_row(room_id, primary="ko", output_langs=["ko"]))

        # Connect a real SSE viewer subscribed to lang=ko.
        s_ko = _open_sse_socket(host, port, room_id, "ko")

        # Wait for the handler to register the viewer in the BroadcastManager.
        assert _wait_until(lambda: mgr.has_viewers(room_id, "ko"))

        # Run _handle_transcript ON THE SSE SERVER'S EVENT LOOP.
        # The asyncio.Queue instances inside BroadcastManager were created
        # on that loop when the SSE handler called register_viewer; calling
        # publish from a different loop's asyncio.run() lands the put_nowait
        # against a queue bound to a foreign loop, and the consumer never
        # wakes. run_coroutine_threadsafe schedules the coroutine on the
        # correct loop and bridges back via concurrent.futures.Future.
        ws = AsyncMock()
        ws.send = AsyncMock()
        user = _build_user(1, room_id)

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        import websocket_handler

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ),
            patch(
                "websocket_handler._translate_text",
                return_value=("안녕", True),
            ),
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model",
                return_value=usage_log_model,
            ),
            patch("websocket_handler.update_user_session"),
            patch.object(websocket_handler._room_manager, "_repo", repo),
        ):
            fut = asyncio.run_coroutine_threadsafe(
                _handle_transcript(
                    ws,
                    {"text": "Hello", "audio_duration_seconds": 5},
                    user,
                    MagicMock(),
                    MagicMock(),
                    True,
                ),
                loop,
            )
            fut.result(timeout=5)

        try:
            payload = _read_sse_data_line(s_ko, timeout=3.0)
        finally:
            s_ko.close()

        assert payload["text"] == "안녕"
        assert payload["lang"] == "ko"
        assert "timestamp" in payload

        # Operator websocket also got its transcription_result message
        # (sanity — guards against a regression that publishes to SSE but
        # forgets to reply to the operator).
        operator_msgs = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        result_msgs = [
            m for m in operator_msgs if m.get("type") == "transcription_result"
        ]
        assert len(result_msgs) == 1
        assert result_msgs[0]["translated_text"] == "안녕"

    def test_lazy_translation_no_extra_translate_call_for_lang_without_viewers(
        self, sse_server, mock_db
    ):
        """No SSE viewer for 'en' → secondary translate_fn must NOT fire.

        Subscribes a viewer only to ko, configures the room with output
        langs ['ko','en'], and asserts that the secondary path
        (``_translate_secondary``) is never invoked because the en channel
        has zero viewers (lazy gate).
        """
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        base_url, mgr, repo, loop = sse_server
        host = "127.0.0.1"
        port = int(base_url.rsplit(":", 1)[1])

        room_id = f"ws-sse-lazy-{uuid.uuid4().hex[:8]}"
        repo.add(_make_room_row(room_id, primary="ko", output_langs=["ko", "en"]))

        # Only ko has a viewer.
        s_ko = _open_sse_socket(host, port, room_id, "ko")
        assert _wait_until(lambda: mgr.has_viewers(room_id, "ko"))
        assert mgr.has_viewers(room_id, "en") is False

        ws = AsyncMock()
        ws.send = AsyncMock()
        user = _build_user(1, room_id)

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        secondary_calls: list[tuple[str, str]] = []

        def fake_translate_secondary(text, src, dst, *args, **kwargs):
            # If this is invoked at all, the lazy gate failed.
            secondary_calls.append((src, dst))
            return f"[{dst}]{text}"

        import websocket_handler

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ),
            patch(
                "websocket_handler._translate_text",
                return_value=("안녕", True),
            ),
            patch(
                "websocket_handler._translate_secondary",
                side_effect=fake_translate_secondary,
            ),
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model",
                return_value=usage_log_model,
            ),
            patch("websocket_handler.update_user_session"),
            patch.object(websocket_handler._room_manager, "_repo", repo),
        ):
            fut = asyncio.run_coroutine_threadsafe(
                _handle_transcript(
                    ws,
                    {"text": "Hello", "audio_duration_seconds": 5},
                    user,
                    MagicMock(),
                    MagicMock(),
                    True,
                ),
                loop,
            )
            fut.result(timeout=5)

        # Primary publish must have reached the ko viewer.
        try:
            payload = _read_sse_data_line(s_ko, timeout=3.0)
        finally:
            s_ko.close()
        assert payload["text"] == "안녕"
        assert payload["lang"] == "ko"

        # Give any (incorrectly) scheduled secondary tasks a chance to run.
        time.sleep(0.2)
        assert secondary_calls == [], (
            f"Lazy translation gate broken: secondary translate called for "
            f"channel(s) without viewers: {secondary_calls}"
        )

    def test_multi_room_isolation_publish_to_room_a_does_not_reach_room_b(
        self, sse_server, mock_db
    ):
        """Two viewers in different rooms — only target room's viewer gets it."""
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        base_url, mgr, repo, loop = sse_server
        host = "127.0.0.1"
        port = int(base_url.rsplit(":", 1)[1])

        room_a = f"room-a-{uuid.uuid4().hex[:8]}"
        room_b = f"room-b-{uuid.uuid4().hex[:8]}"
        repo.add(_make_room_row(room_a, primary="ko", output_langs=["ko"]))
        repo.add(_make_room_row(room_b, primary="ko", output_langs=["ko"]))

        s_a = _open_sse_socket(host, port, room_a, "ko")
        s_b = _open_sse_socket(host, port, room_b, "ko")

        assert _wait_until(lambda: mgr.has_viewers(room_a, "ko"))
        assert _wait_until(lambda: mgr.has_viewers(room_b, "ko"))

        ws = AsyncMock()
        ws.send = AsyncMock()
        # The user is "in" room A — the publish must only reach room A.
        user = _build_user(1, room_a)

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        import websocket_handler

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ),
            patch(
                "websocket_handler._translate_text",
                return_value=("ROOM_A_PAYLOAD", True),
            ),
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model",
                return_value=usage_log_model,
            ),
            patch("websocket_handler.update_user_session"),
            patch.object(websocket_handler._room_manager, "_repo", repo),
        ):
            fut = asyncio.run_coroutine_threadsafe(
                _handle_transcript(
                    ws,
                    {"text": "Hello", "audio_duration_seconds": 5},
                    user,
                    MagicMock(),
                    MagicMock(),
                    True,
                ),
                loop,
            )
            fut.result(timeout=5)

        # Room A viewer receives it.
        try:
            payload_a = _read_sse_data_line(s_a, timeout=3.0)
            assert payload_a["text"] == "ROOM_A_PAYLOAD"

            # Room B viewer must NOT receive it. We give the server a moment
            # to (incorrectly) deliver it, then assert silence.
            s_b.settimeout(0.5)
            buf = b""
            try:
                while True:
                    chunk = s_b.recv(1024)
                    if not chunk:
                        break
                    buf += chunk
                    if b"data: " in buf:
                        # Found a data line in room B — that's a leak.
                        break
            except TimeoutError:
                pass
            assert b"data: " not in buf, (
                f"Cross-room leak: room_b received a data event from room_a "
                f"publish. buf={buf!r}"
            )
        finally:
            s_a.close()
            s_b.close()


# ---------------------------------------------------------------------------
# ISSUE-52 (FR-083) — publish 채널과 페이지 렌더 채널이 같은 값을 말하는가
#
# 프로덕션 사고 재현 그대로: 오퍼레이터가 ko→vi 로 세션을 돌렸고 번역은
# 정상이었는데 무대 화면에는 한국어가 떴다. publish 는 `target_lang`(오퍼레이터
# 설정)을, 페이지 렌더는 `rooms.primary_output_lang`(DB 행)을 읽었고 아무도
# 둘을 맞춰 주지 않았기 때문이다. 이 테스트는 두 값을 **같은 실행 안에서**
# 비교한다 — 어느 단위 테스트도 이 비교를 하지 않아 사고를 놓쳤다 (RL-005).
# ---------------------------------------------------------------------------
_LANG_DB_COUNTER = {"n": 0}


@pytest.fixture
def real_room_repo(tmp_path):
    """진짜 SQLite + database.Room — 왕복을 스텁으로 대체하지 않는다."""
    from database import DatabaseManager, Room, User

    _LANG_DB_COUNTER["n"] += 1
    db = DatabaseManager(str(tmp_path / f"lang{_LANG_DB_COUNTER['n']}.db"))
    admin_id = User(db).create_user(
        username="admin52", password="pw", role="admin", usage_limit_seconds=0
    )
    room_repo = Room(db)
    return db, room_repo, admin_id


async def _render_stage(room_repo, room_id: str) -> str:
    from aiohttp.test_utils import TestClient, TestServer

    from sse_broadcast import BroadcastManager, build_sse_app

    app = build_sse_app(broadcast_manager=BroadcastManager(), room_repo=room_repo)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/stage/{room_id}")
        assert resp.status == 200
        return await resp.text()


def _bootstrapped_lang(body: str, key: str) -> str:
    """렌더된 부트스트랩에서 페이지가 실제로 열 ?lang= 값을 뽑는다."""
    m = re.search(rf'{key}:\s*"([a-z-]+)"', body)
    assert m is not None, f"{key} 가 부트스트랩에 없다"
    return m.group(1)


class TestPublishChannelMatchesRenderedChannel:
    """AC2 의 통합판 — 기록 → 새 페이지 렌더 → publish 채널 일치."""

    @pytest.mark.asyncio
    async def test_stage_subscribes_to_the_channel_the_server_publishes_on(
        self, real_room_repo
    ):
        import websocket_handler
        from websocket_handler import _publish_to_viewers

        _db, room_repo, admin_id = real_room_repo
        room_id = f"lang-{uuid.uuid4().hex[:8]}"
        room_repo.create(room_id=room_id, name="A홀", created_by=admin_id)

        # 사고 시점의 룸 행: 오퍼레이터 선택이 한 번도 기록되지 않은 상태.
        assert room_repo.get_by_id(room_id)["primary_output_lang"] == "ko"

        # 오퍼레이터가 출력 언어를 vi 로 고르고 세션을 시작한다.
        target_lang = "vi"
        assert (
            room_repo.update_session_languages(
                room_id, input_lang="ko", output_lang=target_lang
            )
            is True
        )

        # 그 **뒤에** 무대 화면을 새로 연다.
        body = await _render_stage(room_repo, room_id)
        rendered_lang = _bootstrapped_lang(body, "caption_lang")

        # 헤드라인 단언: 페이지가 구독할 채널 == 서버가 송출할 채널.
        assert rendered_lang == target_lang

        # 그리고 그 채널로 실제 페이로드가 도달하는지 값으로 확인한다.
        from room_manager import RoomManager

        mgr = websocket_handler._broadcast_manager
        queue = await mgr.register_viewer(room_id, rendered_lang)
        try:
            with patch(
                "websocket_handler._room_manager",
                RoomManager(room_repository=room_repo),
            ):
                await _publish_to_viewers(
                    room_id,
                    primary_translated="Xin chào",
                    source_text="안녕하세요",
                    source_lang="ko",
                    target_lang=target_lang,
                    translate_client=MagicMock(),
                    bedrock_client=MagicMock(),
                    bedrock_available=False,
                )
            payload = queue.get_nowait()
        finally:
            await mgr.unregister_viewer(room_id, rendered_lang, queue)

        assert payload["lang"] == target_lang
        assert payload["text"] == "Xin chào"

    @pytest.mark.asyncio
    async def test_unrecorded_language_reproduces_the_production_mismatch(
        self, real_room_repo
    ):
        """대조군 — 기록을 **하지 않으면** 두 채널이 갈라진다.

        위 테스트가 우연히 통과하는 것이 아니라 write-back 때문에 통과한다는
        것을 보인다. 이 테스트가 깨지면 사고 재현 자체가 성립하지 않는다.
        """
        _db, room_repo, admin_id = real_room_repo
        room_id = f"lang-{uuid.uuid4().hex[:8]}"
        room_repo.create(room_id=room_id, name="A홀", created_by=admin_id)

        body = await _render_stage(room_repo, room_id)
        assert _bootstrapped_lang(body, "caption_lang") == "ko"
        assert _bootstrapped_lang(body, "caption_lang") != "vi"

    @pytest.mark.asyncio
    async def test_write_back_leaves_output_langs_untouched(self, real_room_repo):
        """#91/#92 가드의 통합판 — 진짜 DB 행에서 컬럼이 그대로다."""
        db, room_repo, admin_id = real_room_repo
        room_id = f"lang-{uuid.uuid4().hex[:8]}"
        room_repo.create(room_id=room_id, name="A홀", created_by=admin_id)

        with db.get_connection() as conn:
            before = conn.execute(
                "SELECT output_langs FROM rooms WHERE id = ?", (room_id,)
            ).fetchone()["output_langs"]

        room_repo.update_session_languages(room_id, input_lang="ko", output_lang="vi")

        with db.get_connection() as conn:
            after = conn.execute(
                "SELECT output_langs FROM rooms WHERE id = ?", (room_id,)
            ).fetchone()["output_langs"]

        assert after == before == '["ko"]'

        # 그럼에도 뷰어 언어 목록은 전역 지원 언어 전부여야 한다.
        from sse_broadcast import _supported_output_langs
        from translation import SUPPORTED_OUTPUT_LANGS

        assert _supported_output_langs("vi") == list(SUPPORTED_OUTPUT_LANGS)
