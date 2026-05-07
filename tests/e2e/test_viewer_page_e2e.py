"""
ISSUE-31 — 뷰어 페이지 e2e (browser-driven).

aiohttp /view/{room_id} 엔드포인트를 실제 TCP 포트에 띄우고, Playwright
브라우저로 접속해 다음을 검증한다:

  1. 알려진 룸: 페이지가 200 + HTML 로 응답하고, 언어 셀렉터/대기 상태가
     실제 DOM 으로 렌더링된다.
  2. 알 수 없는 룸: 404 본문이 친절한 안내 메시지를 보여주고, 내부 디테일
     (Traceback, 파일 경로) 은 노출하지 않는다 (RL-006).
  3. closed 룸: ended 상태가 즉시 활성화되고 종료 카피가 보인다.

Streamlit 서버가 필요 없는 e2e — aiohttp TestServer 는 session-scope 로
띄워두고 Playwright 가 그 위에 직접 접속한다. fullscreen e2e 와 동일하게
``e2e`` 마크가 기본 deselect 되어 일반 ``pytest -q`` 실행을 막지 않는다.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

# Streamlit 의존성 회피 (다른 테스트와 동일 패턴).
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Stub repo + aiohttp server fixture
# ---------------------------------------------------------------------------
class _StubRoomRepo:
    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)


def _free_port() -> int:
    """Bind to port 0 and immediately release — returns a likely-free port."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def viewer_server():
    """Run aiohttp app in a daemon thread, yielding (base_url, repo)."""
    from aiohttp import web

    from sse_broadcast import BroadcastManager, build_sse_app

    rows = {
        "active-room": {
            "id": "active-room",
            "name": "E2E Active Room",
            "status": "active",
            "primary_output_lang": "ko",
            "output_langs": '["ko","en"]',
        },
        "closed-room": {
            "id": "closed-room",
            "name": "E2E Closed Room",
            "status": "closed",
            "primary_output_lang": "ko",
            "output_langs": '["ko"]',
        },
    }
    repo = _StubRoomRepo(rows)
    mgr = BroadcastManager()
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
    assert started.wait(timeout=5), "aiohttp server failed to start in 5s"

    base_url = f"http://127.0.0.1:{port}"
    yield base_url

    # Teardown
    try:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(timeout=5)
    except Exception:
        pass
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)


# ---------------------------------------------------------------------------
# Browser-driven assertions
# ---------------------------------------------------------------------------
class TestViewerPageInBrowser:
    def test_active_room_renders_language_selector(self, page, viewer_server):
        """활성 룸 → 페이지 200 + #lang-select 가 DOM 에 존재한다."""
        resp = page.goto(f"{viewer_server}/view/active-room", wait_until="load")
        assert resp is not None
        assert resp.status == 200, f"unexpected status: {resp.status}"

        sel = page.locator("#lang-select")
        sel.wait_for(state="attached", timeout=5000)
        assert sel.count() == 1

        # waiting/active 컨테이너 노드도 DOM 에 있어야 한다.
        assert page.locator("#state-waiting").count() == 1
        assert page.locator("#state-active").count() == 1

        # 룸 이름이 화면에 노출된다 (template 주입 검증).
        body_text = page.locator("body").inner_text()
        assert "E2E Active Room" in body_text

    def test_unknown_room_shows_friendly_404(self, page, viewer_server):
        """존재하지 않는 룸 → 404 + 친절한 안내, 내부 디테일 비노출 (RL-006)."""
        resp = page.goto(f"{viewer_server}/view/no-such-room", wait_until="load")
        assert resp is not None
        assert resp.status == 404

        body_text = page.locator("body").inner_text()
        # 사용자 친화 메시지 노출
        assert (
            "찾을 수 없" in body_text
            or "존재하지 않" in body_text
            or "not found" in body_text.lower()
        )
        # 내부 디테일은 비노출
        assert "Traceback" not in body_text
        assert "RuntimeError" not in body_text

    def test_closed_room_shows_ended_copy(self, page, viewer_server):
        """closed 룸 → ended 카피가 즉시 노출된다."""
        resp = page.goto(f"{viewer_server}/view/closed-room", wait_until="load")
        assert resp is not None
        assert resp.status == 200

        body_text = page.locator("body").inner_text()
        assert "세션이 종료되었습니다" in body_text
