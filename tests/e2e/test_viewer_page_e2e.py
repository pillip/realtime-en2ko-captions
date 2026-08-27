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


# ISSUE-44 — 따옴표 + 끝 백슬래시. `html.escape` 는 `\` 를 건드리지 않으므로
# 그대로 JS 문자열 리터럴에 넣으면 닫는 따옴표가 탈출되어 부트스트랩이 죽는다.
_HOSTILE_ROOM_ID = 'q"b\\'
# `&` 는 `html.escape` 로 `&amp;` 가 되는데 `textContent` 는 엔티티를 디코드하지
# 않는다 — 스크립트 컨텍스트에 마크업 이스케이퍼를 쓴 대가 (이중 이스케이프).
_AMPERSAND_NAME = "A홀 & B홀"


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
        # ISSUE-44 — 따옴표 + 끝 백슬래시 id, 앰퍼샌드 이름.
        _HOSTILE_ROOM_ID: {
            "id": _HOSTILE_ROOM_ID,
            "name": _AMPERSAND_NAME,
            "status": "waiting",
            "primary_output_lang": "ko",
            "output_langs": '["ko","en"]',
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

    def test_backslash_room_id_does_not_kill_the_bootstrap(self, page, viewer_server):
        """ISSUE-44 AC 2 — `\\` 로 끝나는 room_id 가 JS 리터럴을 탈출하지 않는다.

        `html.escape` 는 백슬래시를 건드리지 않으므로 그대로 쓰면 스크립트
        블록 전체가 SyntaxError 로 죽는다 — 언어 셀렉터도 상태 전환도 함께
        사라진다. 값 손상(`"` → `&quot;`)까지 브라우저에서 확인한다.
        """
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        resp = page.goto(f"{viewer_server}/view/q%22b%5C", wait_until="load")
        assert resp is not None and resp.status == 200
        assert errors == [], f"bootstrap raised JS errors: {errors}"

        # 1) 부트스트랩이 살아 있고 값이 손상되지 않았다.
        assert page.evaluate("typeof CONFIG !== 'undefined'") is True
        assert page.evaluate("CONFIG.room_id") == _HOSTILE_ROOM_ID

        # 2) 언어 셀렉터가 CONFIG 로부터 실제로 채워졌다 (죽으면 0개).
        options = page.eval_on_selector_all(
            "#lang-select option", "els => els.map(el => el.value)"
        )
        assert options == page.evaluate("CONFIG.output_langs")
        assert len(options) >= 2

        # 3) 상태 전환이 동작한다 — 부트스트랩이 waiting 을 활성화했고,
        #    setState 가 전역에 살아 있어 ended 로 넘어간다.
        assert page.locator("#state-waiting").is_visible() is True
        page.evaluate("setState('ended')")
        assert page.locator("#state-ended").is_visible() is True
        assert page.locator("#state-waiting").is_visible() is False

    def test_ampersand_room_name_is_not_double_escaped(self, page, viewer_server):
        """ISSUE-44 AC 4 — `A홀 & B홀` 이 `A홀 &amp; B홀` 로 보이지 않는다.

        `#room-name` 은 서버 렌더 마크업(엔티티 디코드됨), `#waiting-room` 은
        `CONFIG.room_name` 을 `textContent` 로 쓴 결과(디코드 안 됨)다. 두 노드가
        같은 문자열이어야 sink 별 이스케이퍼가 올바르게 골라진 것이다.
        """
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{viewer_server}/view/q%22b%5C", wait_until="load")
        assert errors == [], f"bootstrap raised JS errors: {errors}"

        assert page.locator("#room-name").inner_text().strip() == _AMPERSAND_NAME
        assert page.locator("#waiting-room").inner_text().strip() == _AMPERSAND_NAME
