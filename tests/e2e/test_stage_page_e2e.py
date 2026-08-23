"""
ISSUE-40 / ISSUE-41 — 무대 합성 페이지 e2e (browser-driven).

ISSUE-41 이 추가한 자막 컬럼 스트리밍 검증은 ``TestStageCaptionStream`` 에 있다.

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


# ---------------------------------------------------------------------------
# ISSUE-41 — 자막 컬럼 SSE 구독 / 타자기 스무딩 (TC-062)
# ---------------------------------------------------------------------------
# 진짜 EventSource 를 쓰면 SSE 프레임 타이밍이 테스트에 섞여 flaky 해지고,
# 61건 주입 같은 시나리오를 서버 쪽에서 만들어야 한다. 네트워크 레벨에서
# 생성 URL 만 기록하고 이벤트를 손으로 밀어 넣는 스텁으로 대체한다.
_FAKE_EVENTSOURCE = """
(() => {
  window.__sse = { urls: [], last: null };
  class FakeEventSource {
    constructor(url) {
      this.url = String(url);
      this.readyState = 0;
      this.closed = false;
      this._listeners = {};
      window.__sse.urls.push(this.url);
      window.__sse.last = this;
    }
    addEventListener(type, fn) {
      if (!this._listeners[type]) this._listeners[type] = [];
      this._listeners[type].push(fn);
    }
    removeEventListener(type, fn) {
      const fns = this._listeners[type];
      if (!fns) return;
      const i = fns.indexOf(fn);
      if (i !== -1) fns.splice(i, 1);
    }
    close() {
      this.closed = true;
      this.readyState = 2;
    }
    _emit(type, data) {
      const event = { type: type, data: data };
      for (const fn of (this._listeners[type] || []).slice()) fn(event);
    }
  }
  window.EventSource = FakeEventSource;
  window.__emit = (type, data) => {
    const es = window.__sse.last;
    if (!es) throw new Error("no EventSource was constructed by the stage page");
    es._emit(type, data);
  };
})();
"""


def _fake_eventsource(page) -> None:
    """Replace `window.EventSource` with a recording stub (must precede goto)."""
    page.add_init_script(_FAKE_EVENTSOURCE)


def _settle(page, predicate: str, timeout: int = 4000) -> None:
    """Wait until `predicate` holds, swallowing the timeout.

    On failure we want the test's own assertion to report what the DOM
    actually looks like, not an opaque Playwright timeout.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        page.wait_for_function(predicate, timeout=timeout)
    except PlaywrightTimeout:
        pass


def _lines(page) -> list[str]:
    return page.eval_on_selector_all(
        ".caption-line", "els => els.map(el => el.textContent)"
    )


# rAF 계측 스텁 — 동시에 살아 있는 rAF 콜백의 최대 개수를 센다. 콜백이 실행되기
# **직전**에 감소시키므로, 루프가 스스로 다음 프레임을 예약해도 max 는 1 에 머문다.
# 반면 두 번째 루프가 겹쳐 시작되면 즉시 2 이상이 된다.
_RAF_COUNTER = """
(() => {
  window.__raf = { live: 0, max: 0 };
  const origRAF = window.requestAnimationFrame.bind(window);
  const origCAF = window.cancelAnimationFrame.bind(window);
  window.requestAnimationFrame = function (cb) {
    window.__raf.live += 1;
    if (window.__raf.live > window.__raf.max) window.__raf.max = window.__raf.live;
    return origRAF(function (t) { window.__raf.live -= 1; cb(t); });
  };
  window.cancelAnimationFrame = function (h) {
    if (window.__raf.live > 0) window.__raf.live -= 1;
    return origCAF(h);
  };
})();
"""


def _line_length(page) -> int:
    return page.evaluate(
        """() => {
          const els = document.querySelectorAll(".caption-line");
          return els.length ? els[els.length - 1].textContent.length : 0;
        }"""
    )


def _emit_message(page, text: str, *, partial: bool = False) -> None:
    payload = {"text": text}
    if partial:
        payload["partial"] = True
    page.evaluate(
        "(payload) => window.__emit('message', JSON.stringify(payload))", payload
    )


class TestStageCaptionStream:
    """TC-062 / FR-080 — 자막 컬럼이 viewer 와 같은 스트리밍 동작을 갖는다.

    좌측 발표 영역(ISSUE-40/42)이 이 전환들에 전혀 영향받지 않는다는 것까지
    함께 확인한다 — 자막 상태 전환은 우측 컬럼 안에서만 일어나야 한다.
    """

    def test_sixty_one_finals_trim_to_sixty_lines(self, page, stage_server):
        """TC-062 — DOM 상한 60. 개수와 **값** 을 함께 단언한다 (RL-004).

        `len >= 60` 이나 "마지막 줄에 뭔가 있다" 는 트리밍이 엉뚱한 쪽 자식을
        지워도 통과한다. 살아남은 첫 줄이 2번째 페이로드, 마지막 줄이 61번째
        페이로드여야 앞에서부터 잘렸음이 증명된다.
        """
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        page.evaluate(
            """() => {
              for (let i = 1; i <= 61; i++) {
                window.__emit("message", JSON.stringify({text: "자막 " + i}));
              }
            }"""
        )
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 60 &&
                     els[els.length - 1].textContent === "자막 61";
            }""",
        )

        lines = _lines(page)
        assert len(lines) == 60, f"expected exactly 60 caption lines, got {len(lines)}"
        oldest = f"oldest surviving line is {lines[0]!r}, want the 2nd payload"
        assert lines[0] == "자막 2", oldest
        newest = f"newest line is {lines[-1]!r}, want the 61st payload"
        assert lines[-1] == "자막 61", newest

    def test_three_partials_then_a_final_collapse_to_one_line(self, page, stage_server):
        """TC-062 — partial 청크들은 한 줄로 모이고 final 이 그 줄을 확정한다.

        final 텍스트를 마지막 partial 과 다르게 둔다 — "무언가 표시된다" 만
        보는 약한 단언이 통과하지 못하게 하기 위함이다 (RL-004).
        """
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        final = "안녕하세요 여러분 반갑습니다"
        for partial in ("안녕", "안녕하세요 여러", "안녕하세요 여러분 반갑"):
            _emit_message(page, partial, partial=True)
        _emit_message(page, final)

        _settle(
            page,
            f"""() => {{
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 && els[0].textContent === {json.dumps(final)};
            }}""",
        )

        lines = _lines(page)
        assert len(lines) == 1, f"partials must collapse to one line, got {lines}"
        assert lines[0] == final, f"line reads {lines[0]!r}, want {final!r}"

    def test_only_finalised_lines_reach_the_live_region(self, page, stage_server):
        """RL-019 (행동) — 라이브 리전은 확정된 줄에서만 갱신된다.

        정적 테스트는 속성 위치만 본다. 여기서는 실제로 partial 이 흐르는
        동안 announcer 가 조용한지, 그리고 애니메이션 노드가 런타임 DOM 에서도
        `aria-hidden` 인지를 확인한다.
        """
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        for partial in ("오늘", "오늘 발표를", "오늘 발표를 시작"):
            _emit_message(page, partial, partial=True)
        page.wait_for_timeout(300)

        announced = page.evaluate(
            """() => {
              const el = document.getElementById("caption-announcer");
              return el === null ? null : el.textContent.trim();
            }"""
        )
        detail = (
            f"#caption-announcer holds {announced!r} while only partials have "
            "arrived — the live region must stay silent until a line is final"
        )
        assert announced == "", detail

        final = "오늘 발표를 시작하겠습니다"
        _emit_message(page, final)
        _settle(
            page,
            f"""() => {{
              const el = document.getElementById("caption-announcer");
              return el !== null && el.textContent.trim() === {json.dumps(final)};
            }}""",
        )

        aria = page.evaluate(
            """() => {
              const el = document.getElementById("caption-announcer");
              const box = document.getElementById("caption-container");
              return {
                announced: el === null ? null : el.textContent.trim(),
                hidden: box.getAttribute("aria-hidden"),
                live: box.getAttribute("aria-live"),
              };
            }"""
        )
        assert aria["announced"] == final, f"announcer reads {aria['announced']!r}"
        assert aria["hidden"] == "true", (
            f"#caption-container aria-hidden is {aria['hidden']!r} — the "
            "per-frame typewriter node must not be exposed"
        )
        assert aria["live"] is None, (
            f"#caption-container still carries aria-live={aria['live']!r}; every "
            "reveal frame would be announced (RL-019)"
        )

    def test_session_end_switches_the_column_and_leaves_the_stage_intact(
        self, page, stage_server
    ):
        """AC — 종료는 자막 컬럼 안에서만 일어나고 타자기 루프가 정지한다."""
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        # 루프가 살아 있으면 300ms 안에 눈에 띄게 자랄 만큼 긴 partial.
        _emit_message(page, "가나다라마바사" * 200, partial=True)
        page.wait_for_timeout(100)
        page.evaluate("() => window.__emit('session_end', '')")

        first = page.evaluate(
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length ? els[els.length - 1].textContent : "";
            }"""
        )
        page.wait_for_timeout(300)
        second = page.evaluate(
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length ? els[els.length - 1].textContent : "";
            }"""
        )
        frozen = (
            f"caption text grew from {len(first)} to {len(second)} chars after "
            "session_end — the rAF loop is still running"
        )
        assert len(second) == len(first), frozen

        assert page.locator("#caption-ended").is_visible() is True
        assert "세션이 종료되었습니다" in page.locator("#caption-ended").inner_text()
        assert page.locator("#caption-live").is_visible() is False
        assert page.evaluate("window.__sse.last.closed") is True

        for selector in (
            "#event-header",
            "#presentation",
            "#stage-frame",
            "#title-card",
        ):
            count = page.locator(selector).count()
            assert count == 1, f"{selector} disappeared on session_end (count={count})"
        assert page.locator("#presentation").is_visible() is True
        area = _box(page, "#presentation")
        assert area["width"] > 0 and area["height"] > 0, f"presentation box {area}"

    def test_stream_url_follows_the_query_lang(self, page, stage_server):
        """AC — `/stage/{id}?lang=en` → `/stream/{id}?lang=en`."""
        _fake_eventsource(page)
        page.goto(f"{stage_server}/stage/quarter-room?lang=en", wait_until="load")
        page.wait_for_timeout(300)

        urls = page.evaluate("window.__sse.urls")
        assert urls, "the stage page opened no EventSource"
        assert urls[0] == "/stream/quarter-room?lang=en", f"opened {urls[0]!r}"

    def test_stream_url_falls_back_to_primary_lang(self, page, stage_server):
        """AC — `?lang=` 이 없으면 룸의 primary_output_lang (fixture: ko)."""
        _fake_eventsource(page)
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")
        page.wait_for_timeout(300)

        urls = page.evaluate("window.__sse.urls")
        assert urls, "the stage page opened no EventSource"
        # 형제 테스트와 같은 완전 일치 — endswith 는 룸 id 가 어긋나도 통과한다.
        want = "/stream/quarter-room?lang=ko"
        assert urls[0] == want, f"opened {urls[0]!r}, want {want!r}"

    def test_long_token_wraps_without_horizontal_scroll(self, page, stage_server):
        """AC — 컬럼 폭보다 긴 단일 토큰(예: 긴 URL)이 포함된 자막이 렌더되면
        가로 스크롤바가 생기지 않고 줄바꿈된다.

        `word-break: keep-all` 만 있으면 끊을 수 없는 토큰이 컬럼을 가로로
        밀어낸다 — `overflow-wrap: anywhere` 가 함께 있어야 한다.
        """
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        token = "x" * 300
        _emit_message(page, token)
        _settle(
            page,
            f"""() => {{
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 && els[0].textContent.length === {len(token)};
            }}""",
        )

        metrics = page.evaluate(
            """() => {
              const scroll = document.getElementById("caption-scroll");
              const line = document.querySelector(".caption-line");
              const column = document.getElementById("caption-column");
              return {
                scrollWidth: scroll.scrollWidth,
                clientWidth: scroll.clientWidth,
                lineRight: line === null ? null : line.getBoundingClientRect().right,
                columnRight: column.getBoundingClientRect().right,
              };
            }"""
        )
        assert metrics["lineRight"] is not None, "no .caption-line rendered"
        overflow = (
            f"#caption-scroll scrollWidth {metrics['scrollWidth']} exceeds "
            f"clientWidth {metrics['clientWidth']} — the 300-char token forced a "
            "horizontal scrollbar"
        )
        assert metrics["scrollWidth"] <= metrics["clientWidth"] + 1, overflow
        assert metrics["lineRight"] <= metrics["columnRight"] + 1, (
            f"line right edge {metrics['lineRight']:.1f}px spills past the "
            f"caption column at {metrics['columnRight']:.1f}px"
        )

    def test_connection_banner_sits_at_the_bottom_of_the_caption_column(
        self, page, stage_server
    ):
        """AC — 끊김 배너는 자막 컬럼 안에만 뜨고, 열리면 사라진다."""
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        page.evaluate("() => window.__emit('error', '')")
        page.wait_for_timeout(200)

        assert page.locator("#conn-error").is_visible() is True
        banner = _box(page, "#conn-error")
        column = _box(page, "#caption-column")
        presentation = _box(page, "#presentation")

        contained = (
            f"banner box {banner} is not fully inside the caption column {column}"
        )
        fits_right = banner["x"] + banner["width"] <= column["x"] + column["width"] + 1
        fits_bottom = (
            banner["y"] + banner["height"] <= column["y"] + column["height"] + 1
        )
        assert banner["x"] >= column["x"] - 1, contained
        assert fits_right, contained
        assert banner["y"] >= column["y"] - 1, contained
        assert fits_bottom, contained
        # "하단" 자체를 단언한다 — 격납만 보면 컬럼 **상단**에 붙은 배너도 통과해
        # 첫 자막 줄을 가린다 (RL-004).
        midpoint = column["y"] + column["height"] / 2
        low = (
            f"banner top {banner['y']:.1f}px sits above the column midpoint "
            f"{midpoint:.1f}px — the AC anchors it to the bottom"
        )
        assert banner["y"] > midpoint, low

        deck_right = presentation["x"] + presentation["width"]
        covers = (
            f"banner left edge {banner['x']:.1f}px sits left of the presentation "
            f"area's right edge {deck_right:.1f}px — it covers the deck"
        )
        assert banner["x"] >= deck_right - 1, covers

        page.evaluate("() => window.__emit('open', '')")
        page.wait_for_timeout(200)
        assert page.locator("#conn-error").is_visible() is False

    def test_hidden_tab_snaps_the_current_line_instead_of_queueing(
        self, page, stage_server
    ):
        """숨김 전환 시 현재 줄을 즉시 최종 텍스트로 스냅하고 루프를 멈춘다.

        스냅하지 않으면 rAF 가 정지된 동안 목표 텍스트만 쌓이고, 복귀 순간
        수백 자가 몰아쳐 나온다.
        """
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        text = "무대 자막 스트리밍 " * 40  # 400자 이상
        assert len(text) >= 400

        # 한 번의 evaluate 안에서 처리해 두 동작 사이에 프레임이 끼지 않게 한다.
        page.evaluate(
            """(text) => {
              window.__emit("message", JSON.stringify({text: text, partial: true}));
              Object.defineProperty(document, "hidden", {
                get: () => true, configurable: true,
              });
              document.dispatchEvent(new Event("visibilitychange"));
            }""",
            text,
        )
        snapped = page.evaluate(
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length ? els[els.length - 1].textContent : null;
            }"""
        )
        detail = (
            f"line holds {0 if snapped is None else len(snapped)} of "
            f"{len(text)} chars when the tab went hidden — it must snap to the "
            "full target so nothing avalanches on return"
        )
        assert snapped == text, detail

        page.evaluate(
            """() => {
              Object.defineProperty(document, "hidden", {
                get: () => false, configurable: true,
              });
              document.dispatchEvent(new Event("visibilitychange"));
            }"""
        )
        page.wait_for_timeout(400)
        after = page.evaluate(
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length ? els[els.length - 1].textContent : null;
            }"""
        )
        assert after == text, f"text changed after returning: {after!r}"

    def test_partial_text_is_revealed_gradually_not_in_one_frame(
        self, page, stage_server
    ):
        """AC 1 (타자기 스무딩) 의 유일한 행동 가드 — 중간 상태를 표본한다.

        나머지 자막 테스트는 전부 **종료 상태**만 본다. `_twStep` 을
        `twShown = twTarget.length` 로 바꿔 즉시 전량 공개하게 만들어도 그 테스트들은
        모두 통과하고, 정적 테스트는 `requestAnimationFrame(` 이라는 글자가 파일 어딘가
        있다는 것밖에 증명하지 못한다 (RL-004). 여기서는 청크가 도착한 직후에 화면을
        찍어 **일부만** 나와 있는지 본다.
        """
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        text = "가" * 600
        _emit_message(page, text, partial=True)
        page.wait_for_timeout(60)
        mid = _line_length(page)
        gradual = (
            f"{mid} of {len(text)} chars appeared within ~4 frames — the reveal is "
            "not gradual (MAX_REVEAL_PER_FRAME is 24, so a few dozen chars is the "
            "expected range)"
        )
        assert 0 < mid < len(text), gradual

        _settle(
            page,
            f"""() => {{
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 &&
                     els[0].textContent.length === {len(text)};
            }}""",
        )
        assert _line_length(page) == len(text), "the reveal never caught up"

    def test_a_shrinking_partial_repaints_instead_of_leaving_stale_text(
        self, page, stage_server
    ):
        """짧아진 partial 은 화면을 줄여야 한다 — 옛 긴 문자열이 남으면 안 된다.

        번역 후처리는 문자열을 깎기도 한다. `twShown` 을 클램프만 하면 `gap` 이 0 이
        되어 쓰기 분기를 건너뛰므로, 화면에는 이전의 더 긴 텍스트가 그대로 남는다.
        """
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        long_text = "안녕하세요 반갑습니다 여러분"
        _emit_message(page, long_text, partial=True)
        _settle(
            page,
            f"""() => {{
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 && els[0].textContent === {json.dumps(long_text)};
            }}""",
        )

        _emit_message(page, "안녕", partial=True)
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 && els[0].textContent === "안녕";
            }""",
        )
        stale = f"line still reads {_lines(page)!r} after the target shrank"
        assert _lines(page) == ["안녕"], stale

    def test_only_one_raf_loop_ever_runs(self, page, stage_server):
        """겹쳐 도는 rAF 루프가 없다 — `_twStart` 의 `twRaf` 가드가 유일한 방어선.

        가드를 지우면 partial 이 도착할 때마다 새 루프가 하나씩 더 붙어 세션 내내
        누적되고, 공개 속도가 루프 수만큼 빨라진다. 그런데 최종 상태는 똑같아서
        기존 테스트는 전부 통과한다 (RL-004). rAF 를 계측해 동시 실행 수를 센다.
        """
        _fake_eventsource(page)
        page.add_init_script(_RAF_COUNTER)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        # 한 태스크 안에서 연속 partial — 프레임이 끼지 않으므로 가드가 없으면
        # 12 개의 루프가 동시에 예약된다.
        page.evaluate(
            """() => {
              for (let i = 1; i <= 12; i++) {
                window.__emit("message", JSON.stringify({
                  text: "무대 자막 ".repeat(i * 10), partial: true,
                }));
              }
            }"""
        )
        page.wait_for_timeout(400)
        page.evaluate(
            """() => {
              for (let i = 1; i <= 12; i++) {
                window.__emit("message", JSON.stringify({
                  text: "다음 문장 " + i, partial: true,
                }));
                window.__emit("message", JSON.stringify({text: "확정 " + i}));
              }
            }"""
        )
        page.wait_for_timeout(400)

        raf = page.evaluate("window.__raf")
        overlap = f"{raf['max']} rAF callbacks were live at once — expected 1"
        assert raf["max"] <= 1, overlap
        idle = f"{raf['live']} rAF callbacks still scheduled after settling"
        assert raf["live"] == 0, idle

    def test_message_after_session_end_is_ignored(self, page, stage_server):
        """종료는 최종 상태다 — 늦게 도착한 프레임이 컬럼을 되살리면 안 된다.

        `currentLine = null` 만으로는 message 진입점이 살아 있어서, 한 프레임이
        `setCaptionState("active")` → 새 라인 → rAF 재시작 → 라이브 리전 쓰기를
        모두 되돌린다. 닫힌 소켓에서 벌어지는 일이다.
        """
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        _emit_message(page, "종료 전 마지막 문장")
        _settle(
            page,
            """() => document.querySelectorAll(".caption-line").length === 1""",
        )
        page.evaluate("() => window.__emit('session_end', '')")
        before = _lines(page)

        _emit_message(page, "유령 자막")
        page.wait_for_timeout(300)

        state = page.evaluate(
            """() => ({
              lines: [...document.querySelectorAll(".caption-line")]
                       .map(el => el.textContent),
              endedVisible: !document.getElementById("caption-ended").hidden,
              liveHidden: document.getElementById("caption-live").hidden,
            })"""
        )
        resurrected = (
            f"the ended column accepted a late message: {state} (was {before})"
        )
        assert state["lines"] == before, resurrected
        assert state["endedVisible"] is True, resurrected
        assert state["liveHidden"] is True, resurrected

    def test_empty_final_does_not_blank_the_column(self, page, stage_server):
        """빈 final 이 대기 문구를 지우고 빈 컬럼을 남기면 안 된다.

        `broadcast` 경로에 비어있음 가드가 없고 AWS Translate 는 구두점만 있는
        입력에 `""` 를 돌려준다. 그대로 통과시키면 `_ensureCurrentLine()` 이
        `#caption-empty` 를 제거한 뒤 빈 `.caption-line` 만 쌓여, 행사 중 무대
        화면이 되돌릴 수 없는 빈 컬럼이 된다.
        """
        _fake_eventsource(page)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{stage_server}/stage/quarter-room", wait_until="load")

        for _ in range(3):
            _emit_message(page, "")
        page.wait_for_timeout(300)

        state = page.evaluate(
            """() => ({
              lines: [...document.querySelectorAll(".caption-line")]
                       .map(el => el.textContent),
              placeholder: document.getElementById("caption-empty") === null
                ? null
                : document.getElementById("caption-empty").textContent,
            })"""
        )
        blanked = f"empty finals left the column in {state}"
        assert state["lines"] == [], blanked
        assert state["placeholder"] == "잠시 후 시작됩니다", blanked

        # 진행 중인 partial 이 있을 때의 빈 final 은 그 라인을 확정하는 신호다.
        _emit_message(page, "이어지는 문장입니다", partial=True)
        _emit_message(page, "")
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 &&
                     els[0].textContent === "이어지는 문장입니다";
            }""",
        )
        assert _lines(page) == ["이어지는 문장입니다"]
        announced = page.evaluate(
            "() => document.getElementById('caption-announcer').textContent"
        )
        assert announced == "이어지는 문장입니다", announced

    def test_waiting_state_is_announced_to_screen_readers(self, page, stage_server):
        """RL-019 의 반대쪽 — 대기 문구도 스크린리더에 닿아야 한다.

        `#caption-empty` 는 `aria-hidden="true"` 인 `#caption-container` **안**에
        있으므로 보조기술에는 존재하지 않는다. viewer.html 은 waiting 을 별도
        `aria-live` 섹션으로 두어 공짜로 얻는 접근성인데, 무대 페이지는 한
        서브트리로 합치면서 잃을 수 있는 자리다. 자막이 흐르기 시작하면 announcer
        는 다시 비어야 한다 (자막 텍스트 경로는 `_lockLine()` 하나뿐이다).
        """
        _fake_eventsource(page)
        page.goto(f"{stage_server}/stage/quarter-room?lang=en", wait_until="load")
        page.wait_for_timeout(200)

        state = page.evaluate(
            """() => ({
              announced: document.getElementById("caption-announcer").textContent,
              visible: document.getElementById("caption-empty").textContent,
              hidden: document
                .getElementById("caption-container")
                .getAttribute("aria-hidden"),
            })"""
        )
        silent = (
            "the waiting copy is inside an aria-hidden subtree and the announcer "
            f"is empty — screen readers get silence in the waiting state: {state}"
        )
        assert state["hidden"] == "true", state
        assert state["visible"] == "Starting shortly", state
        assert state["announced"] == "Starting shortly", silent

        _emit_message(page, "first line", partial=True)
        page.wait_for_timeout(200)
        stale = "the waiting copy must clear once captions start flowing"
        announced = page.evaluate(
            "() => document.getElementById('caption-announcer').textContent"
        )
        assert announced == "", stale


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
