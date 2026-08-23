"""
ISSUE-40 — 무대 합성 페이지 e2e (browser-driven).

문자열 매칭으로는 절대 검증할 수 없는 두 가지를 브라우저로 확인한다:

  1. **16:9 레터박스 + 컬럼 격리 불변식** — 1920×1080 과 3440×1440 두
     뷰포트에서, **짧은 콘텐츠와 스키마 상한(120자) 타이틀 양쪽 모두**에서
     발표 영역이 16:9 를 유지하고 자막 컬럼을 침범하지 않는다 (AC 3).
     격납 단언은 프레임의 **부모**(#presentation)가 아니라 **형제 경계**
     (#caption-column 의 좌측 모서리)를 기준으로 한다 — 부모는 자식과 함께
     자라므로 부모 기준 단언은 깨진 레이아웃에서도 통과한다 (RL-004/RL-017).
  2. **자막 컬럼 폭** — `caption_ratio` 가 실제 픽셀 폭 25% / 33.333% 로
     반영된다 (AC 2).

덤으로 런타임에서만 관찰 가능한 것들: 빈 타이틀의 룸 이름 폴백(AC 4), 로고
전무 시 바 접힘(AC 5), 없는 에셋의 degrade + 빈 그룹/바 접힘(RL-008/UI-5),
적대적 로고 파일명이 마크업이 되지 않음(RL-016), closed 룸의 종료 상태 +
EventSource 미개설(AC 8), `</script>` 브레이크아웃 무력화(AC 7), 백슬래시
룸 id 가 부트스트랩을 죽이지 않음(RL-020), 좁은 컬럼 자막 타이포.

Streamlit 서버가 필요 없는 e2e — aiohttp 앱을 모듈 스코프로 띄우고 Playwright
가 그 위에 직접 접속한다 (test_viewer_page_e2e.py 패턴). ``e2e`` 마크는 기본
deselect 되어 일반 ``pytest -q`` 실행을 막지 않는다.
"""

from __future__ import annotations

import asyncio
import base64
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

# 스키마 상한(EVENT_TITLE_MAX_LEN=120)에 정확히 맞춘 실제형 국문 타이틀.
# 짧은 픽스처만 쓰면 intrinsic sizing 결함이 보이지 않는다 (RL-017).
_LONG_TITLE = (
    "제12회 대한민국 인공지능 및 실시간 다국어 자막 기술 국제 콘퍼런스 "
    "2026 서울 코엑스 그랜드볼룸 A홀 기조연설 및 산업 협력 세션 안내드립니다 "
    "— 주최 과학기술정보통신부, 주관 한국지능정보사회진흥원 후원 서울시"
)  # 정확히 120자 = stage_config.EVENT_TITLE_MAX_LEN (쓰기 경로가 허용하는 상한)
# 룸 이름에는 길이 제한이 아예 없다 — JS 없이 서버 렌더만으로 재현되는 경로.
_LONG_ROOM_NAME = _LONG_TITLE

# `/branding/{room_id}/{filename}` 은 ISSUE-38 이 만든다. 그 전까지 모든 로고가
# 404 이므로, 로고가 "뜬" 상태를 봐야 하는 테스트는 네트워크 레벨에서 채운다.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)

# 적대적 로고 파일명 — 문자열 조립으로 DOM 에 들어가면 속성을 탈출한다.
_ATTR_BREAK_ASSET = '" onerror="window.__pwned=1" x="'
_TRAVERSAL_ASSET = "../../../etc/passwd"


def _serve_logos(page, *, only: set[str] | None = None) -> None:
    """`/branding/**` 를 가짜 PNG 로 채운다 (``only`` 밖 파일명은 404)."""

    def _handler(route, request):
        name = request.url.rsplit("/", 1)[-1]
        if only is not None and name not in only:
            route.fulfill(status=404, body="")
            return
        route.fulfill(status=200, content_type="image/png", body=_PNG_1X1)

    page.route("**/branding/**", _handler)


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
        # bare-room 과 헤더 구조가 같고 타이틀만 있는 대조군 (AC 4 높이 비교용).
        "titled-room": _room(
            "titled-room",
            name="맨몸 룸",
            stage_config=_cfg(event_title="2026 개발자 콘퍼런스"),
        ),
        # RL-017 — 스키마 상한 타이틀 / 길이 제한 없는 룸 이름.
        "longtitle-room": _room(
            "longtitle-room",
            stage_config=_cfg(
                event_title=_LONG_TITLE,
                event_subtitle="A홀 기조연설 · 동시통역 한국어/영어 제공",
                logo_groups=[
                    {"label": "주최", "assets": ["host-1.png"]},
                    {"label": "주관", "assets": []},
                    {"label": "후원", "assets": ["sponsor-a.png"]},
                ],
            ),
        ),
        "longname-room": _room(
            "longname-room", name=_LONG_ROOM_NAME, stage_config=_cfg()
        ),
        "closed-room": _room("closed-room", status="closed"),
        "xss-room": _room("xss-room", stage_config=_cfg(event_title=_BREAKOUT_TITLE)),
        "evil-logo-room": _room(
            "evil-logo-room",
            stage_config=_cfg(
                logo_groups=[
                    {"label": "주최", "assets": [_ATTR_BREAK_ASSET]},
                    {"label": "후원", "assets": [_TRAVERSAL_ASSET]},
                ]
            ),
        ),
        # 백슬래시로 끝나는 room_id — html.escape 는 `\` 를 건드리지 않는다.
        'q"b\\': _room('q"b\\'),
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
    # 짧은 콘텐츠 하나만 보면 intrinsic sizing 결함이 통째로 숨는다 (RL-017).
    @pytest.mark.parametrize("width,height", [(1920, 1080), (3440, 1440)])
    @pytest.mark.parametrize(
        "room", ["quarter-room", "longtitle-room", "longname-room"]
    )
    def test_left_column_never_paints_over_the_caption_column(
        self, page, stage_server, room, width, height
    ):
        """AC 3 — 발표 영역/헤더가 자막 컬럼을 침범하지 않는다.

        격납 기준은 **형제 경계**(자막 컬럼의 좌측 모서리)와 뷰포트 폭이다.
        프레임의 부모(#presentation)를 기준으로 삼으면 부모가 자식과 함께
        자라기 때문에 레이아웃이 깨진 상태에서도 통과한다 (RL-004).
        """
        page.set_viewport_size({"width": width, "height": height})
        resp = page.goto(f"{stage_server}/stage/{room}", wait_until="load")
        assert resp is not None and resp.status == 200

        column = _box(page, "#caption-column")
        selectors = ("#event-header", "#event-title", "#presentation", "#stage-frame")
        for selector in selectors:
            box = _box(page, selector)
            right = box["x"] + box["width"]
            where = f"{room} @ {width}x{height}"
            overlap = (
                f"{selector} right edge {right:.1f}px overlaps the caption column "
                f"at {column['x']:.1f}px ({where})"
            )
            assert right <= column["x"] + 1, overlap
            clipped = (
                f"{selector} right edge {right:.1f}px exceeds the {width}px "
                f"viewport ({where})"
            )
            assert right <= width + 1, clipped

    @pytest.mark.parametrize("width,height", [(1920, 1080), (3440, 1440)])
    @pytest.mark.parametrize(
        "room", ["quarter-room", "longtitle-room", "longname-room"]
    )
    def test_presentation_area_keeps_16_9(
        self, page, stage_server, room, width, height
    ):
        """AC 3 — 어떤 뷰포트/콘텐츠에서도 발표 프레임은 정확히 16:9 다."""
        page.set_viewport_size({"width": width, "height": height})
        page.goto(f"{stage_server}/stage/{room}", wait_until="load")

        frame = _box(page, "#stage-frame")
        ratio = frame["width"] / frame["height"]
        detail = f"frame is {ratio:.3f}:1 for {room} at {width}x{height}"
        assert abs(ratio - 16 / 9) < 0.02, detail

    def test_long_title_ellipsizes_instead_of_growing_the_column(
        self, page, stage_server
    ):
        """RL-017 — 트랙이 자라는 대신 `text-overflow: ellipsis` 가 동작한다."""
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/longtitle-room", wait_until="load")

        metrics = page.evaluate(
            """() => {
              const h1 = document.getElementById("event-title");
              return {scroll: h1.scrollWidth, client: h1.clientWidth};
            }"""
        )
        detail = (
            "the 120-char title fits the header box — the fixture is too short "
            "to exercise the overflow path"
        )
        assert metrics["scroll"] > metrics["client"], detail

    @pytest.mark.parametrize("width,height", [(1920, 1080), (3440, 1440)])
    def test_header_and_logo_bars_account_for_the_vertical_slack(
        self, page, stage_server, width, height
    ):
        """AC 3 / ux_spec — 남는 세로 공간의 소재를 실제로 검증한다.

        `header.height > 0` 은 1px 짜리 헤어라인 바도 만족한다 (RL-004).
        검증해야 할 불변식은 두 개다: 좌측 컬럼 높이가 헤더 + 발표 영역 +
        로고 바로 정확히 나뉘고, 두 바가 선언된 최소 높이를 실제로 차지하며,
        16:9 프레임이 그 안에서 레터박스/필러박스 된다는 것.
        """
        page.set_viewport_size({"width": width, "height": height})
        _serve_logos(page)  # ISSUE-38 이전이라 실제 에셋이 없다
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")
        page.wait_for_timeout(300)

        main = _box(page, ".stage-main")
        header = _box(page, "#event-header")
        area = _box(page, "#presentation")
        bar = _box(page, "#logo-bar")
        frame = _box(page, "#stage-frame")

        rows = header["height"] + area["height"] + bar["height"]
        split = (
            f"left column {main['height']:.1f}px != header {header['height']:.1f} "
            f"+ presentation {area['height']:.1f} + logo bar {bar['height']:.1f}"
        )
        assert abs(main["height"] - rows) <= 1, split
        # 선언된 최소 높이 — 헤어라인이 아니라 실제 바다.
        assert header["height"] >= 88, f"header collapsed to {header['height']:.1f}px"
        assert bar["height"] >= 76, f"logo bar collapsed to {bar['height']:.1f}px"
        # 두 바가 좌측 컬럼 높이의 일부를 실제로 가져갔다.
        assert header["height"] + bar["height"] >= 164
        # 프레임은 발표 영역 안에서 레터박스/필러박스 된다 (잘리지 않는다).
        assert frame["width"] <= area["width"] + 1
        assert frame["height"] <= area["height"] + 1

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
        """AC 4 — 빈 타이틀은 룸 이름으로 대체되고 좌측 컬럼이 흔들리지 않는다.

        "헤더 높이 > 0" 이 아니라, 타이틀이 있는 동일 구조의 대조군과 헤더
        박스가 픽셀 단위로 같다는 것을 확인한다 (RL-004).
        """
        page.set_viewport_size({"width": 1920, "height": 1080})

        page.goto(f"{stage_server}/stage/bare-room", wait_until="load")
        assert page.locator("#event-title").inner_text().strip() == "맨몸 룸"
        fallback_header = _box(page, "#event-header")
        fallback_main = _box(page, ".stage-main")

        page.goto(f"{stage_server}/stage/titled-room", wait_until="load")
        assert (
            page.locator("#event-title").inner_text().strip() == "2026 개발자 콘퍼런스"
        )
        titled_header = _box(page, "#event-header")
        titled_main = _box(page, ".stage-main")

        assert abs(fallback_header["height"] - titled_header["height"]) <= 1
        assert abs(fallback_header["width"] - titled_header["width"]) <= 1
        assert abs(fallback_main["width"] - titled_main["width"]) <= 1

    def test_empty_logo_groups_collapse_the_bar(self, page, stage_server):
        """AC 5 — 로고가 하나도 없으면 바가 접히고 발표 영역이 그 공간을 갖는다."""
        page.set_viewport_size({"width": 1920, "height": 1080})
        _serve_logos(page)  # 실제로 뜨는 로고와 비교해야 의미가 있다

        page.goto(f"{stage_server}/stage/bare-room", wait_until="load")
        page.wait_for_timeout(300)
        assert page.locator("#logo-bar").is_visible() is False
        bare_area = _box(page, "#presentation")["height"]

        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")
        page.wait_for_timeout(300)
        assert page.locator("#logo-bar").is_visible() is True
        with_logos_area = _box(page, "#presentation")["height"]

        assert bare_area > with_logos_area

    def test_partial_logo_failure_keeps_the_surviving_group(self, page, stage_server):
        """RL-008 — 없는 에셋은 해당 이미지/그룹만 정리하고 나머지는 남는다."""
        page.set_viewport_size({"width": 1920, "height": 1080})
        _serve_logos(page, only={"host-1.png"})  # 후원 그룹 2장은 404
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")
        page.wait_for_timeout(500)  # onerror 처리 대기

        assert page.locator("#logo-bar img").count() == 1
        assert page.locator("#logo-bar .logo-group").count() == 1
        assert (
            page.locator("#logo-bar .logo-group-label").inner_text().strip() == "주최"
        )
        assert page.locator("#logo-bar").is_visible() is True

    def test_all_logo_assets_failing_collapses_the_whole_bar(self, page, stage_server):
        """UI-5 — 전부 404 면 라벨만 남은 빈 바가 아니라 바 자체가 접힌다.

        `/branding/` 는 ISSUE-38 이 만들므로 지금은 이것이 모든 무대 페이지의
        실제 상태다 — 76px 을 "주최 / 후원" 글자만으로 태울 수는 없다.
        """
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")
        page.wait_for_timeout(500)

        assert page.locator("#logo-bar img").count() == 0
        assert page.locator("#logo-bar .logo-group").count() == 0
        assert page.locator("#logo-bar .logo-group-label").count() == 0
        assert page.locator("#logo-bar").is_visible() is False

    def test_caption_line_typography_renders_in_the_narrow_column(
        self, page, stage_server
    ):
        """좁은 컬럼 타이포가 문자열이 아니라 계산된 스타일로 검증된다 (UI-12).

        ISSUE-40 에는 `.caption-line` 을 만드는 코드 경로가 없으므로 노드를
        직접 주입해 실제 CSS 를 태운다. 끊을 수 없는 긴 토큰이 25% 컬럼을
        가로로 넘치지 않는지도 함께 본다.
        """
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        style = page.evaluate(
            """() => {
              const line = document.createElement("div");
              line.className = "caption-line";
              line.textContent = "실시간 다국어 자막 " + "x".repeat(140);
              document.getElementById("caption-container").appendChild(line);
              const cs = getComputedStyle(line);
              const column = document.getElementById("caption-column");
              return {
                fontSize: parseFloat(cs.fontSize),
                lineHeight: parseFloat(cs.lineHeight),
                wordBreak: cs.wordBreak,
                overflowWrap: cs.overflowWrap,
                scrollWidth: line.scrollWidth,
                clientWidth: line.clientWidth,
                right: line.getBoundingClientRect().right,
                columnRight: column.getBoundingClientRect().right,
              };
            }"""
        )
        # clamp(20px, 1.4vw + 8px, 32px) → 1920 에서는 상한 32px.
        assert style["fontSize"] == pytest.approx(32.0, abs=0.5)
        assert style["lineHeight"] == pytest.approx(32.0 * 1.45, abs=0.6)
        assert style["wordBreak"] == "keep-all"
        assert style["overflowWrap"] == "anywhere"
        assert style["scrollWidth"] <= style["clientWidth"] + 1
        assert style["right"] <= style["columnRight"] + 1


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

    def test_hostile_logo_filename_cannot_become_markup(self, page, stage_server):
        """RL-016 두 번째 링크 — 파일명은 `src` 프로퍼티로만 들어간다.

        `stage_config` 는 파일명을 일부러 검증하지 않고 넘긴다 (ISSUE-38 몫).
        여기서 지켜야 할 것은 "마크업 문자열로 조립하지 않는다" 뿐인데,
        그 보증에 테스트가 하나도 없으면 다음 리팩터링에서 조용히 사라진다.
        """
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _serve_logos(page)  # 이미지가 살아 있어야 src 를 읽을 수 있다

        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/evil-logo-room", wait_until="load")
        page.wait_for_timeout(300)

        assert page.evaluate("window.__pwned === undefined") is True
        assert errors == [], f"stage page raised JS errors: {errors}"

        srcs = page.eval_on_selector_all(
            "#logo-bar img", "els => els.map(el => el.getAttribute('src'))"
        )
        assert len(srcs) == 2, f"expected both logos to render, got {srcs}"
        joined = " ".join(srcs)
        for raw in ('"', "<", ">", "onerror="):
            assert raw not in joined, f"{raw!r} survived un-encoded in {joined!r}"
        assert "%22%20onerror%3D%22window.__pwned%3D1%22" in joined
        # ISSUE-38 인터페이스 노트: traversal 은 %2F 로 인코딩되어 도착한다.
        assert "..%2F..%2F..%2Fetc%2Fpasswd" in joined

    def test_backslash_room_id_does_not_kill_the_bootstrap(self, page, stage_server):
        """RL-020 — `\\` 로 끝나는 room_id 가 JS 문자열 리터럴을 탈출하지 않는다.

        `html.escape` 는 백슬래시를 건드리지 않으므로, 그대로 쓰면 스크립트
        블록 전체가 SyntaxError 로 죽는다 — 자막 컬럼 폭도, 타이틀도, closed
        분기도 함께 사라진다.
        """
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.set_viewport_size({"width": 1920, "height": 1080})
        resp = page.goto(f"{stage_server}/stage/q%22b%5C", wait_until="load")
        assert resp is not None and resp.status == 200

        assert errors == [], f"bootstrap raised JS errors: {errors}"
        assert page.evaluate("typeof CONFIG !== 'undefined'") is True
        assert page.evaluate("CONFIG.room_id") == 'q"b\\'
        # 부트스트랩이 살아 있으면 자막 컬럼 폭이 CSS 폴백이 아니라 설정값이다.
        assert _box(page, "#caption-column")["width"] == pytest.approx(480, abs=2)
