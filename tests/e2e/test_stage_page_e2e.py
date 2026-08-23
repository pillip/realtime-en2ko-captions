"""
ISSUE-40 — 무대 합성 페이지 e2e (browser-driven).

문자열 매칭으로는 절대 검증할 수 없는 두 가지를 브라우저로 확인한다:

  1. **16:9 레터박스 불변식** — 1920×1080 과 3440×1440 두 뷰포트에서 발표
     영역의 실제 렌더 박스가 16:9 를 유지하고 좌측 컬럼을 넘지 않는다
     (AC 3). 남는 세로 공간은 헤더 바/로고 바가 흡수한다.
  2. **자막 컬럼 폭** — `caption_ratio` 가 실제 픽셀 폭 25% / 33.333% 로
     반영된다 (AC 2).

덤으로 런타임에서만 관찰 가능한 것들: 빈 타이틀의 룸 이름 폴백(AC 4), 로고
전무 시 바 접힘(AC 5), 없는 에셋의 개별 degrade(RL-008), closed 룸의 종료
상태 + EventSource 미개설(AC 8), `</script>` 브레이크아웃 무력화(AC 7).

Streamlit 서버가 필요 없는 e2e — aiohttp 앱을 모듈 스코프로 띄우고 Playwright
가 그 위에 직접 접속한다 (test_viewer_page_e2e.py 패턴). ``e2e`` 마크는 기본
deselect 되어 일반 ``pytest -q`` 실행을 막지 않는다.
"""

from __future__ import annotations

import asyncio
import json
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

# alert() 은 브라우저를 멈추므로, 탈출 성공 여부를 전역 플래그로 관찰한다.
_BREAKOUT_TITLE = "</script><script>window.__pwned=1;</script>"


class _StubRoomRepo:
    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)


def _cfg(**over: Any) -> str:
    cfg = {
        "event_title": "",
        "event_subtitle": "",
        "caption_ratio": "1/4",
        "logo_groups": [
            {"label": label, "assets": []} for label in ("주최", "주관", "후원")
        ],
    }
    cfg.update(over)
    return json.dumps(cfg, ensure_ascii=False)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def stage_server():
    """Run the aiohttp app in a daemon thread, yielding the base URL."""
    from aiohttp import web

    from sse_broadcast import BroadcastManager, build_sse_app

    def _room(room_id: str, **over: Any) -> dict[str, Any]:
        row = {
            "id": room_id,
            "name": "E2E Stage Room",
            "status": "active",
            "primary_output_lang": "ko",
            "output_langs": '["ko","en"]',
            "stage_config": _cfg(),
        }
        row.update(over)
        return row

    rows = {
        "quarter-room": _room(
            "quarter-room",
            stage_config=_cfg(
                event_title="2026 개발자 콘퍼런스",
                event_subtitle="A홀 기조연설",
                caption_ratio="1/4",
                logo_groups=[
                    {"label": "주최", "assets": ["host-1.png"]},
                    {"label": "주관", "assets": []},
                    {"label": "후원", "assets": ["sponsor-a.png", "sponsor-b.png"]},
                ],
            ),
        ),
        "third-room": _room("third-room", stage_config=_cfg(caption_ratio="1/3")),
        "bare-room": _room("bare-room", name="맨몸 룸", stage_config=_cfg()),
        "closed-room": _room("closed-room", status="closed"),
        "xss-room": _room("xss-room", stage_config=_cfg(event_title=_BREAKOUT_TITLE)),
    }

    app = build_sse_app(
        broadcast_manager=BroadcastManager(), room_repo=_StubRoomRepo(rows)
    )

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

    yield f"http://127.0.0.1:{port}"

    try:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(timeout=5)
    except Exception:
        pass
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)


def _box(page, selector: str) -> dict[str, float]:
    node = page.locator(selector)
    node.wait_for(state="attached", timeout=5000)
    box = node.bounding_box()
    assert box is not None, f"{selector} has no layout box"
    return box


class TestStageLayoutInBrowser:
    @pytest.mark.parametrize("width,height", [(1920, 1080), (3440, 1440)])
    def test_presentation_area_keeps_16_9(self, page, stage_server, width, height):
        """AC 3 — 어떤 뷰포트에서도 발표 영역은 16:9 이고 좌측 컬럼을 넘지 않는다."""
        page.set_viewport_size({"width": width, "height": height})
        resp = page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")
        assert resp is not None and resp.status == 200

        frame = _box(page, "#stage-frame")
        area = _box(page, "#presentation")

        ratio = frame["width"] / frame["height"]
        detail = f"frame is {ratio:.3f}:1 at {width}x{height}, expected 16:9"
        assert abs(ratio - 16 / 9) < 0.02, detail
        # 레터박스 = 잘리지 않는다. 프레임이 영역 밖으로 넘치면 잘린 것이다.
        assert frame["width"] <= area["width"] + 1
        assert frame["height"] <= area["height"] + 1
        # 남는 세로 공간은 헤더/로고 바가 흡수한다 (그래서 두 바가 존재한다).
        assert _box(page, "#event-header")["height"] > 0
        assert _box(page, "#logo-bar")["height"] > 0

    @pytest.mark.parametrize(
        "room,expected_fraction", [("quarter-room", 0.25), ("third-room", 0.33333)]
    )
    def test_caption_column_width_follows_ratio(
        self, page, stage_server, room, expected_fraction
    ):
        """AC 2 — caption_ratio 가 실제 픽셀 폭으로 반영된다."""
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/{room}", wait_until="load")

        actual = _box(page, "#caption-column")["width"]
        expected = 1920 * expected_fraction
        assert abs(actual - expected) <= 2, f"column {actual}px, want ~{expected}px"

    def test_empty_title_falls_back_to_room_name_and_keeps_header(
        self, page, stage_server
    ):
        """AC 4 — 빈 타이틀은 룸 이름으로 대체되고 헤더 높이가 유지된다."""
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/bare-room", wait_until="load")

        assert page.locator("#event-title").inner_text().strip() == "맨몸 룸"
        assert _box(page, "#event-header")["height"] >= 60

    def test_empty_logo_groups_collapse_the_bar(self, page, stage_server):
        """AC 5 — 로고가 하나도 없으면 바가 접히고 발표 영역이 그 공간을 갖는다."""
        page.set_viewport_size({"width": 1920, "height": 1080})

        page.goto(f"{stage_server}/stage/bare-room", wait_until="load")
        assert page.locator("#logo-bar").is_visible() is False
        bare_area = _box(page, "#presentation")["height"]

        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")
        assert page.locator("#logo-bar").is_visible() is True
        with_logos_area = _box(page, "#presentation")["height"]

        assert bare_area > with_logos_area

    def test_missing_logo_asset_hides_only_itself(self, page, stage_server):
        """RL-008 — /branding 라우트(ISSUE-38) 부재 시에도 로고 바가 살아남는다."""
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")
        page.wait_for_timeout(500)  # onerror 처리 대기

        # 에셋 3개 모두 404 → 이미지만 사라지고 그룹/바 레이아웃은 유지된다.
        assert page.locator("#logo-bar img").count() == 0
        assert page.locator("#logo-bar .logo-group").count() == 2
        assert page.locator("#logo-bar").is_visible() is True


class TestStageRuntimeStates:
    def test_closed_room_shows_ended_copy_without_opening_sse(self, page, stage_server):
        """AC 8 — 종료 룸은 종료 상태로 시작하고 EventSource 를 열지 않는다."""
        page.add_init_script(
            """
            window.__esCount = 0;
            const Orig = window.EventSource;
            window.EventSource = function (...args) {
              window.__esCount += 1;
              return new Orig(...args);
            };
            """
        )
        page.goto(f"{stage_server}/stage/closed-room", wait_until="load")
        page.wait_for_timeout(300)

        assert page.locator("#caption-ended").is_visible() is True
        assert "세션이 종료되었습니다" in page.locator("#caption-ended").inner_text()
        assert page.locator("#caption-live").is_visible() is False
        assert page.evaluate("window.__esCount") == 0

    def test_script_breakout_payload_does_not_execute(self, page, stage_server):
        """AC 7 — `</script>` 페이로드는 실행되지 않고 문자 그대로 표시된다."""
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{stage_server}/stage/xss-room", wait_until="load")
        page.wait_for_timeout(200)

        assert page.evaluate("window.__pwned === undefined") is True
        assert errors == [], f"stage page raised JS errors: {errors}"
        assert page.locator("#event-title").inner_text().strip() == _BREAKOUT_TITLE
