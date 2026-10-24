"""
ISSUE-42 — 발표자료 화면 캡처 e2e (browser-driven, test_plan TC-063 / TC-064).

정적 문자열 매칭으로는 절대 증명할 수 없는 **행위 불변식**을 브라우저에서 본다.
가장 중요한 것은 ISSUE-40 의 `test_no_presenter_keyboard_interference` 를 대체하는
행위 가드다 — 어휘적으로 `keydown` 을 금지하는 대신, 실제로 방향키/PageUp/
PageDown/Space 를 디스패치해 **아무도 그 이벤트를 취소하지 않았고, 무대 창이
포커스를 쥐고 있지 않으며, 전체화면으로 튀지 않았다** 는 것을 확인한다.

`navigator.mediaDevices.getDisplayMedia` 는 `add_init_script` 로 스텁한다 — 실제
화면 선택 다이얼로그는 headless 에서 띄울 수 없고, 띄운다면 그 자체가 테스트를
사람 손에 묶는다. 스텁은 canvas `captureStream()` 트랙을 돌려주므로 `<video>` 는
진짜 프레임을 받는다.

RL-022 의 교훈에 따라 스텁은 **관대하게** 만든다: `track.stop()` 은 명세상
`ended` 를 발화하지 않으므로(브라우저의 "공유 중지" 버튼만 발화한다) 테스트가
직접 이벤트를 밀어 넣어 페이지의 가드를 실제로 태운다.

Streamlit 서버가 필요 없는 e2e — aiohttp 앱을 모듈 스코프로 띄우고 Playwright 가
그 위에 직접 접속한다 (test_stage_page_e2e.py 패턴). ``e2e`` 마크는 기본 deselect
되어 일반 ``pytest -q`` 실행을 막지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import re
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

# 거부 경로에 실어 보내는 유일무이한 문자열. 이 값이 DOM 어디에도 나타나지
# 않아야 RL-006 이 지켜진 것이다 — 실제 브라우저는 여기에 내부 경로나 정책
# 이름을 담아 던진다.
_SENTINEL = "SENTINEL-9f2c/private/var/db/credentials.sqlite-LEAK"

# 프레젠터 리모컨이 실제로 보내는 키들.
_PRESENTER_KEYS = ["ArrowRight", "ArrowLeft", "PageDown", "PageUp", " "]


class _StubRoomRepo:
    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)


def _cfg() -> str:
    return json.dumps(
        {
            "event_title": "2026 개발자 콘퍼런스",
            "caption_ratio": "1/4",
            "logo_groups": [
                {"label": label, "assets": []} for label in ("주최", "주관", "후원")
            ],
        },
        ensure_ascii=False,
    )


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def capture_server():
    """Run the aiohttp stage app in a daemon thread, yielding the base URL."""
    from aiohttp import web

    from sse_broadcast import BroadcastManager, build_sse_app

    rows = {
        "capture-room": {
            "id": "capture-room",
            "name": "E2E Capture Room",
            "status": "active",
            "primary_output_lang": "ko",
            "output_langs": '["ko","en"]',
            "stage_config": _cfg(),
        }
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


# ---------------------------------------------------------------------------
# getDisplayMedia 스텁 — 실제 화면 선택 다이얼로그 없이 캡처 경로를 태운다.
# ---------------------------------------------------------------------------
# `window.__capture` 로 테스트가 조종한다:
#   .reject   = {name, message}  → 다음 호출이 그 오류로 거부된다
#   .settings = {...}            → track.getSettings() 에 병합된다 (자기 캡처)
#   .calls    = 페이지가 넘긴 제약 객체들
#   .track    = 마지막으로 넘겨준 비디오 트랙 (테스트가 ended 를 밀어 넣는다)
_CAPTURE_STUB = """
(() => {
  window.__capture = { calls: [], stream: null, track: null,
                       reject: null, settings: null };
  const stub = async (constraints) => {
    window.__capture.calls.push(JSON.parse(JSON.stringify(constraints || {})));
    if (window.__capture.reject) {
      const spec = window.__capture.reject;
      window.__capture.reject = null;
      const err = new Error(spec.message);
      err.name = spec.name;
      throw err;
    }
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 360;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#1d4ed8";
    ctx.fillRect(0, 0, 640, 360);
    const stream = canvas.captureStream(30);
    const track = stream.getVideoTracks()[0];
    if (window.__capture.settings) {
      const extra = window.__capture.settings;
      const orig = track.getSettings.bind(track);
      track.getSettings = () => Object.assign({}, orig(), extra);
    }
    window.__capture.stream = stream;
    window.__capture.track = track;
    return stream;
  };
  if (!navigator.mediaDevices) {
    Object.defineProperty(navigator, "mediaDevices",
                          { value: {}, configurable: true });
  }
  navigator.mediaDevices.getDisplayMedia = stub;
})();
"""

# 진짜 EventSource 는 이 스위트의 관심사가 아니다 — 자막 컬럼이 캡처 전환에
# 영향받지 않는지만 확인하면 되므로, 이벤트를 손으로 밀어 넣는 스텁을 쓴다.
_FAKE_EVENTSOURCE = """
(() => {
  window.__sse = { last: null };
  class FakeEventSource {
    constructor(url) {
      this.url = String(url);
      this._listeners = {};
      window.__sse.last = this;
    }
    addEventListener(type, fn) {
      (this._listeners[type] = this._listeners[type] || []).push(fn);
    }
    removeEventListener() {}
    close() { this.closed = true; }
    _emit(type, data) {
      for (const fn of (this._listeners[type] || []).slice()) fn({type, data});
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


def _open_stage(page, base_url: str, *, query: str = "") -> None:
    """Install the stubs (must precede goto) and load the stage page."""
    page.add_init_script(_CAPTURE_STUB)
    page.add_init_script(_FAKE_EVENTSOURCE)
    page.set_viewport_size({"width": 1600, "height": 900})
    page.goto(f"{base_url}/stage/capture-room{query}", wait_until="load")


def _connect(page) -> None:
    """Click 발표자료 연결 and wait until the capture <video> is *actually playing*.

    `hidden === false` alone is not enough. 스트림을 `<video>` 에 아예 붙이지
    않아도 그 단언은 통과하고, 화면에는 검은 프레임이 남는다 — FR-075/NFR-026 이
    막으려는 바로 그 상태다. 게다가 `test_track_ended_falls_back_to_the_connect_action`
    의 `srcObject === null` 단언까지 **처음부터 null 이라서** 통과해 버린다
    (리뷰 뮤테이션 M35 생존, RL-004 의 전형 — 치운 것이 아니라 놓은 적이 없는데
    통과하는 단언).

    그래서 연결의 정의를 "프레임이 실제로 흐른다" 로 못 박는다: srcObject 가
    붙어 있고, 디코딩된 해상도가 있으며, `currentTime` 이 0 을 넘겼다. 이 함수를
    거의 모든 테스트가 통과하므로 가드가 한 곳에서 전 스위트에 걸린다.

    `currentTime > 0` 은 여기서 쓸 수 없다 — 스텁 canvas 가 정적이라
    `captureStream()` 이 첫 프레임 뒤로 타임라인을 진전시키지 않는다(측정값:
    3초 뒤에도 `currentTime === 0`, `paused === false`, `readyState === 4`).
    대신 재생이 실제로 시작됐음을 뜻하는 `paused === false` + 디코딩된 해상도로
    본다. AC 의 "30초 후 currentTime 증가" 는 움직이는 소스가 필요하므로
    리허설에서 확인한다 (후속 이슈).
    """
    page.locator("#capture-connect").click()
    page.wait_for_function(
        """() => {
          const v = document.getElementById('capture-video');
          return v.hidden === false && v.srcObject !== null
                 && v.videoWidth > 0 && v.readyState >= 2 && v.paused === false;
        }""",
        timeout=5000,
    )


def _visible_clickables(page) -> list[str]:
    """Every *visible* focusable/clickable node, as `tag#id` labels."""
    return page.evaluate(
        """() => {
          const nodes = document.querySelectorAll(
            "button, a, input, select, textarea, [tabindex]"
          );
          return [...nodes].filter((el) => {
            const cs = getComputedStyle(el);
            if (cs.display === "none") return false;
            if (cs.visibility === "hidden") return false;
            if (parseFloat(cs.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
          }).map((el) => el.tagName.toLowerCase() + "#" + (el.id || "?"));
        }"""
    )


def _dispatch_presenter_keys(page) -> dict[str, Any]:
    """Fire the presenter-remote keys at `document` and report the aftermath."""
    return page.evaluate(
        """(keys) => {
          const events = [];
          for (const key of keys) {
            const ev = new KeyboardEvent("keydown", {
              key: key, bubbles: true, cancelable: true,
            });
            document.dispatchEvent(ev);
            events.push({key: key, defaultPrevented: ev.defaultPrevented});
          }
          return {
            events: events,
            activeIsBody: document.activeElement === document.body,
            activeTag: document.activeElement
              ? document.activeElement.tagName.toLowerCase()
              : null,
            fullscreenElement: document.fullscreenElement === null ? null : "set",
          };
        }""",
        _PRESENTER_KEYS,
    )


# ---------------------------------------------------------------------------
# TC-064 / NFR-025 — 프레젠터 조작 비간섭 (행위 기반 가드)
# ---------------------------------------------------------------------------
class TestPresenterKeys:
    """ISSUE-40 의 어휘적 가드를 대체하는 **행위** 가드.

    `preventDefault()` 를 생략한다고 키가 발표 앱으로 전달되지는 않는다 —
    브라우저는 다른 앱에 키를 주입할 수 없다. 실제 요구사항은 무대 창이 OS
    포커스를 쥐지 않는 것이고(그래서 `activeElement === body` 를 함께 본다),
    키를 삼키지 않는 것은 그 전제가 깨졌을 때의 2차 방어선이다.
    """

    def test_presenter_keys_are_never_cancelled_before_capture(
        self, page, capture_server
    ):
        """AC — `?debug=1` 없이 연 무대 페이지는 어떤 키도 취소하지 않는다."""
        _open_stage(page, capture_server)
        state = _dispatch_presenter_keys(page)

        for event in state["events"]:
            assert event["defaultPrevented"] is False, (
                f"{event['key']!r} was cancelled by the stage page — the presenter "
                "remote stops turning slides at the venue (NFR-025)"
            )
        assert state["activeIsBody"] is True, (
            f"document.activeElement is <{state['activeTag']}>, not <body> — a "
            "focused control is holding the window's keyboard focus"
        )
        assert state["fullscreenElement"] is None

    def test_presenter_keys_are_never_cancelled_while_capture_runs(
        self, page, capture_server
    ):
        """AC — 캡처가 진행 중인 상태에서도 같은 보증이 유지된다."""
        _open_stage(page, capture_server)
        _connect(page)
        state = _dispatch_presenter_keys(page)

        for event in state["events"]:
            assert event["defaultPrevented"] is False, event
        assert state["activeIsBody"] is True, (
            f"after capture started the focus sits on <{state['activeTag']}> — the "
            "stage window must hand focus back to the document body"
        )
        assert state["fullscreenElement"] is None, (
            "the page entered fullscreen on its own; that returns OS focus to the "
            "stage window and breaks the presenter remote"
        )


# ---------------------------------------------------------------------------
# TC-063 — 캡처 수명주기 / 복구 (FR-074, FR-075, FR-081, NFR-026)
# ---------------------------------------------------------------------------
class TestStageCaptureLifecycle:
    def test_capture_constraints_reach_get_display_media(self, page, capture_server):
        """AC — 페이지가 실제로 넘긴 제약 객체를 값으로 확인한다 (RL-004)."""
        _open_stage(page, capture_server)
        _connect(page)

        calls = page.evaluate("() => window.__capture.calls")
        assert len(calls) == 1, f"getDisplayMedia called {len(calls)} time(s)"
        assert calls[0] == {
            "video": {"frameRate": {"ideal": 30}},
            "audio": False,
            "selfBrowserSurface": "exclude",
            "surfaceSwitching": "exclude",
            "systemAudio": "exclude",
        }, calls[0]

    def test_capture_video_declares_the_attributes_that_let_it_autoplay(
        self, page, capture_server
    ):
        """Scope — `<video autoplay muted playsinline>` 는 장식이 아니다.

        `muted` 가 없으면 브라우저 자동재생 정책이 재생을 막고, `autoplay` 가
        없으면 아무도 `play()` 를 부르지 않으므로 첫 프레임이 영원히 오지 않는다.
        둘 다 결과는 같다 — 무대 좌측이 검은 화면으로 남는다(FR-075 가 막으려는
        상태). 속성은 마크업에만 존재하므로 어떤 동작 단언에도 걸리지 않아
        리뷰 뮤테이션 M33/M34 가 스위트 전체를 통과했다. DOM 프로퍼티로 본다 —
        문자열 매칭과 달리 오타난 속성은 여기서 False 로 드러난다.
        """
        _open_stage(page, capture_server)
        attrs = page.evaluate(
            """() => {
              const v = document.getElementById("capture-video");
              return {
                autoplay: v.autoplay,
                muted: v.muted,
                playsInline: v.playsInline,
              };
            }"""
        )
        assert attrs == {"autoplay": True, "muted": True, "playsInline": True}, attrs

    def test_focus_returns_to_the_document_body_right_after_capture_starts(
        self, page, capture_server
    ):
        """TC-063 — 버튼이 포커스를 붙잡고 있으면 첫 슬라이드부터 넘어가지 않는다."""
        _open_stage(page, capture_server)
        # 클릭 직후 포커스는 반드시 버튼에 있다 — 대조군을 먼저 확인해 두어야
        # "원래부터 body 였다" 로 통과하는 약한 단언이 되지 않는다 (RL-004).
        page.locator("#capture-connect").focus()
        assert page.evaluate("() => document.activeElement.id") == "capture-connect"

        _connect(page)
        active = page.evaluate(
            """() => ({
              isBody: document.activeElement === document.body,
              id: document.activeElement ? document.activeElement.id : null,
            })"""
        )
        assert active["isBody"] is True, (
            f"focus stayed on #{active['id']} after capture started; the page must "
            "call document.activeElement?.blur() so the operator's next click can "
            "hand OS focus to the presentation app"
        )

    def test_connected_stage_exposes_no_visible_clickable_element(
        self, page, capture_server
    ):
        """AC — 연결 후에는 클릭 가능한 가시 요소가 0개이고 커서도 사라진다."""
        _open_stage(page, capture_server)
        before = _visible_clickables(page)
        assert before == ["button#capture-connect"], (
            f"before connecting the stage should show exactly the connect button, "
            f"got {before}"
        )

        _connect(page)
        after = _visible_clickables(page)
        assert after == [], (
            f"{after} stayed clickable after capture connected — every visible "
            "control is a route for the stage window to take OS focus (NFR-025)"
        )
        cursor = page.evaluate(
            "() => getComputedStyle(document.getElementById('stage-root')).cursor"
        )
        assert cursor == "none", f"stage cursor is {cursor!r}, want 'none'"
        controls = page.evaluate(
            """() => getComputedStyle(
                 document.getElementById('capture-controls')).display"""
        )
        assert controls == "none", (
            f"#capture-controls display is {controls!r}; pointer-events alone does "
            "not stop the OS from giving the window focus"
        )

    def test_handoff_prompt_appears_once_and_auto_dismisses(self, page, capture_server):
        """AC — 연결 직후 1회 표시되고 일정 시간 후 스스로 사라진다.

        권한 다이얼로그를 브라우저에서 조작했으므로 이 순간 브라우저가 포커스를
        쥐고 있다. 그 상태로 발표를 시작하면 첫 슬라이드부터 넘어가지 않는다.
        안내는 그 사실을 알리되 무대 화면에 **영구적으로** 남지는 않아야 한다.
        """
        _open_stage(page, capture_server)
        _connect(page)

        prompt = page.locator("#handoff-prompt")
        assert prompt.is_visible() is True, "no focus hand-off prompt after connecting"
        assert "발표 앱을 클릭해 포커스를 넘기세요" in prompt.inner_text()

        prompt.wait_for(state="hidden", timeout=12000)
        assert prompt.is_visible() is False, (
            "the hand-off prompt is still on screen — it must not stay on the "
            "projector for the whole session"
        )

    def test_window_blur_dismisses_the_handoff_prompt_immediately(
        self, page, capture_server
    ):
        """AC — blur 수신 = 핸드오프 성공 신호이므로 그 즉시 감춘다."""
        _open_stage(page, capture_server)
        _connect(page)
        assert page.locator("#handoff-prompt").is_visible() is True

        page.evaluate("() => window.dispatchEvent(new Event('blur'))")
        page.wait_for_function(
            "() => document.getElementById('handoff-prompt').hidden === true",
            timeout=2000,
        )
        assert page.locator("#handoff-prompt").is_visible() is False

    def test_track_ended_falls_back_to_the_connect_action(self, page, capture_server):
        """TC-063 — "공유 중지" 후 **연결 버튼만** 남고 막다른 길이 아니다.

        원래 FR-075 는 타이틀 카드로 되돌리는 선택이었지만, 미연결 화면은
        이제 프레임 배경 + 연결 버튼뿐이다 (타이틀/로고는 상단 헤더와 하단
        로고 바가 들고 있어 프레임 안에서 중복됐다).

        자막 컬럼이 그 전환에 전혀 영향받지 않는다는 것까지 함께 본다 —
        발표 영역의 사고가 자막을 끊으면 안 된다.
        """
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('message', JSON.stringify({text: '캡처 이전 자막'}))"
        )
        page.wait_for_function(
            "() => document.querySelectorAll('.caption-line').length === 1"
        )
        _connect(page)

        # 브라우저의 "공유 중지" 는 ended 를 발화한다. stop() 만으로는 명세상
        # 발화하지 않으므로 스텁이 직접 밀어 넣는다 (RL-022: 관대한 스텁 + 명시 테스트).
        page.evaluate(
            """() => {
              const track = window.__capture.track;
              track.stop();
              track.dispatchEvent(new Event("ended"));
            }"""
        )
        page.wait_for_function(
            "() => document.getElementById('capture-video').hidden === true",
            timeout=3000,
        )

        state = page.evaluate(
            """() => ({
              titleCardGone: document.getElementById("title-card") === null,
              srcObject: document.getElementById("capture-video").srcObject === null,
              label: document.getElementById("capture-connect").textContent.trim(),
              controls: !document.getElementById("capture-controls").hidden,
              cursor: getComputedStyle(document.getElementById("stage-root")).cursor,
              lines: [...document.querySelectorAll(".caption-line")]
                       .map((el) => el.textContent),
              column: document.getElementById("caption-column") !== null,
            })"""
        )
        assert (
            state["titleCardGone"] is True
        ), "타이틀 카드가 되살아났다 — 미연결 화면은 연결 버튼만이어야 한다"
        assert state["srcObject"] is True, "the ended stream is still attached"
        assert state["label"] == "발표자료 다시 연결", state["label"]
        assert state["controls"] is True, "no reconnect action after the track ended"
        assert state["cursor"] == "default", state["cursor"]
        assert state["column"] is True
        survived = state["lines"] == ["캡처 이전 자막"]
        assert survived, f"the caption column lost its content: {state['lines']}"

        # 그리고 실제로 다시 연결된다 (막다른 길이 아니다).
        _connect(page)
        assert page.locator("#capture-video").is_visible() is True

    def test_reconnect_does_not_tear_itself_down(self, page, capture_server):
        """옛 트랙의 `ended` 리스너를 먼저 떼지 않으면 새 캡처가 즉시 철거된다.

        `track.stop()` 은 명세상 `ended` 를 발화하지 않으므로, 재연결만으로는
        리스너를 떼었는지 여부를 **구별할 수 없다** — 실제로 이 단언들만 두었을
        때 "리스너를 떼지 않는다" 는 뮤턴트가 살아남았다 (RL-004). 그래서 옛
        트랙이 뒤늦게 `ended` 를 발화하는 실제 시나리오(사용자가 옛 소스의
        공유 중지를 나중에 누르는 경우)를 명시적으로 재현한다. 리스너가 남아
        있으면 그 한 발이 방금 붙인 **새** 캡처를 철거한다.
        """
        _open_stage(page, capture_server)
        _connect(page)
        first = page.evaluate(
            "() => { window.__firstTrack = window.__capture.track; return window.__capture.track.id; }"
        )

        page.dblclick("#reselect-zone")
        _connect(page)
        page.wait_for_timeout(300)

        # 옛 트랙의 뒤늦은 ended. 새 캡처는 이 이벤트에 반응하면 안 된다.
        page.evaluate("() => window.__firstTrack.dispatchEvent(new Event('ended'))")
        page.wait_for_timeout(200)

        state = page.evaluate(
            """() => ({
              videoHidden: document.getElementById("capture-video").hidden,
              trackId: window.__capture.track.id,
              live: window.__capture.track.readyState,
            })"""
        )
        assert state["trackId"] != first, "the stub handed back the same track"
        assert state["videoHidden"] is False, (
            "stopping the previous track tore down the new capture — remove the "
            "'ended' listener from the old track before stopping it"
        )
        assert state["live"] == "live"

    def test_corner_double_click_restores_the_controls(self, page, capture_server):
        """AC — 재선택은 지정된 코너 더블클릭이라는 명시적 제스처로만 돌아온다."""
        _open_stage(page, capture_server)
        _connect(page)
        assert _visible_clickables(page) == []

        page.dblclick("#reselect-zone")
        page.wait_for_function(
            "() => document.getElementById('capture-controls').hidden === false",
            timeout=2000,
        )
        state = page.evaluate(
            """() => ({
              clickables: [...document.querySelectorAll("button")]
                .filter((el) => el.getBoundingClientRect().width > 0)
                .map((el) => el.id),
              cursor: getComputedStyle(document.getElementById("stage-root")).cursor,
              label: document.getElementById("capture-connect").textContent.trim(),
              videoHidden: document.getElementById("capture-video").hidden,
            })"""
        )
        assert state["clickables"] == ["capture-connect"], state
        assert state["cursor"] == "default", state["cursor"]
        assert state["label"] == "발표자료 다시 연결", state["label"]
        assert state["videoHidden"] is False, (
            "the reselect gesture must not blank the deck; the capture keeps "
            "playing until a new source is chosen"
        )

    def test_not_allowed_error_leaks_no_exception_text(self, page, capture_server):
        """TC-063 / RL-006 — 내부 예외 문자열이 관객 화면에 뜨면 안 된다."""
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open_stage(page, capture_server)
        page.evaluate(
            "(spec) => { window.__capture.reject = spec; }",
            {"name": "NotAllowedError", "message": _SENTINEL},
        )

        page.locator("#capture-connect").click()
        page.wait_for_function(
            "() => document.getElementById('capture-hint').hidden === false",
            timeout=3000,
        )

        content = page.content()
        assert _SENTINEL not in content, (
            "the rejection's message reached the DOM — the stage screen is the "
            "audience's screen (RL-006)"
        )
        assert "NotAllowedError" not in content, "the exception name reached the DOM"
        assert errors == [], f"the rejection escaped as a page error: {errors}"

        state = page.evaluate(
            """() => ({
              hint: document.getElementById("capture-hint").textContent.trim(),
              titleCardGone: document.getElementById("title-card") === null,
              videoHidden: document.getElementById("capture-video").hidden,
              buttonVisible: !document.getElementById("capture-controls").hidden,
            })"""
        )
        assert (
            state["titleCardGone"] is True
        ), "타이틀 카드는 제거됐다 — 거부 후에도 연결 버튼만 남아야 한다"
        assert state["videoHidden"] is True
        assert state["buttonVisible"] is True, "the operator must be able to retry"
        assert state["hint"], "the rejection produced no guidance at all"
        assert "발표자료" in state["hint"], state["hint"]

    def test_self_capture_warns_without_terminating_the_stream(
        self, page, capture_server
    ):
        """TC-063 / FR-081 — 무한 거울은 경고만 하고 캡처를 끊지 않는다."""
        _open_stage(page, capture_server)
        page.evaluate(
            """() => {
              const dpr = window.devicePixelRatio || 1;
              window.__capture.settings = {
                displaySurface: "browser",
                width: Math.round(window.innerWidth * dpr),
                height: Math.round(window.innerHeight * dpr),
              };
            }"""
        )
        _connect(page)
        page.wait_for_function(
            "() => document.getElementById('capture-warning').hidden === false",
            timeout=3000,
        )

        state = page.evaluate(
            """() => ({
              warning: document.getElementById("capture-warning").textContent.trim(),
              videoHidden: document.getElementById("capture-video").hidden,
              trackState: window.__capture.track.readyState,
              controls: !document.getElementById("capture-controls").hidden,
            })"""
        )
        assert "발표자료 창을 선택하세요" in state["warning"], state["warning"]
        assert state["trackState"] == "live", (
            "the self-capture heuristic force-terminated the capture; FR-081 says "
            "warn and expose a reselect action instead"
        )
        assert state["videoHidden"] is False
        assert state["controls"] is True, "no reselect action next to the warning"

    def test_ordinary_window_capture_raises_no_warning(self, page, capture_server):
        """대조군 — 휴리스틱이 정상 캡처에까지 경고를 띄우면 쓸모가 없다 (RL-004)."""
        _open_stage(page, capture_server)
        page.evaluate(
            """() => {
              window.__capture.settings = {
                displaySurface: "window", width: 1920, height: 1080,
              };
            }"""
        )
        _connect(page)
        page.wait_for_timeout(300)
        assert page.locator("#capture-warning").is_visible() is False

    def test_browser_surface_of_a_different_size_raises_no_warning(
        self, page, capture_server
    ):
        """대조군 2 — 휴리스틱의 **해상도 비교 절반**을 변별하는 유일한 테스트.

        위의 대조군은 `displaySurface: "window"` 라서 첫 줄(`!== "browser"`)에서
        이미 걸러진다 — 해상도 비교를 통째로 `return true` 로 바꿔도 그 테스트는
        초록색이다(리뷰 뮤테이션 M20 생존, RL-004). 실제로 방어해야 하는 상황은
        **다른 브라우저 탭을 정상적으로 캡처하는 경우**다: 발표자료가 Google
        Slides/reveal.js 면 `displaySurface` 는 정상적으로 `"browser"` 이고,
        그때 경고가 뜨면 재선택 컨트롤이 발표 내내 화면에 남아 무대 창이 OS
        포커스를 되찾을 경로가 생긴다 (NFR-025).
        """
        _open_stage(page, capture_server)
        page.evaluate(
            """() => {
              // 같은 'browser' 서피스지만 이 창 크기와 뚜렷하게 다른 해상도.
              window.__capture.settings = {
                displaySurface: "browser",
                width: Math.round(window.innerWidth / 2),
                height: Math.round(window.innerHeight / 3),
              };
            }"""
        )
        _connect(page)
        page.wait_for_timeout(300)

        state = page.evaluate(
            """() => ({
              warning: !document.getElementById("capture-warning").hidden,
              controls: !document.getElementById("capture-controls").hidden,
              cursor: getComputedStyle(document.getElementById("stage-root")).cursor,
            })"""
        )
        assert state["warning"] is False, (
            "a legitimate capture of another browser tab tripped the self-capture "
            "banner; the heuristic must also compare the resolution (FR-081)"
        )
        assert state["controls"] is False, (
            "the false-positive banner left the reselect controls on screen for "
            "the whole talk — a visible click surface takes back OS focus"
        )
        assert state["cursor"] == "none", state["cursor"]


# ---------------------------------------------------------------------------
# TC-064 — `?debug=1` 리허설 진단 오버레이
# ---------------------------------------------------------------------------
class TestStageDebugOverlay:
    def test_overlay_is_absent_without_the_query_flag(self, page, capture_server):
        """AC — 본 행사 화면에는 진단 오버레이가 존재조차 하지 않는다."""
        _open_stage(page, capture_server)
        assert page.locator("#debug-overlay").count() == 0, (
            "the diagnostic overlay rendered without ?debug=1 — it would sit on "
            "the projector during the actual event"
        )
        assert page.locator("#debug-key-count").count() == 0

    def test_overlay_counts_keydowns_without_cancelling_them(
        self, page, capture_server
    ):
        """AC — 카운터는 계수 전용이다. 세 번 누르면 3, 모두 취소되지 않는다."""
        _open_stage(page, capture_server, query="?debug=1")
        assert page.locator("#debug-overlay").count() == 1

        result = page.evaluate(
            """() => {
              const prevented = [];
              for (const key of ["ArrowRight", "PageDown", " "]) {
                const ev = new KeyboardEvent("keydown", {
                  key: key, bubbles: true, cancelable: true,
                });
                document.dispatchEvent(ev);
                prevented.push(ev.defaultPrevented);
              }
              return {
                prevented: prevented,
                count: document.getElementById("debug-key-count").textContent.trim(),
              };
            }"""
        )
        assert result["count"] == "3", (
            f"the rehearsal counter reads {result['count']!r} after 3 keydowns; "
            "the rehearsal criterion (20 presses → 0) depends on it counting every "
            "key the stage window receives"
        )
        assert result["prevented"] == [False, False, False], result["prevented"]

    def test_overlay_tracks_document_has_focus(self, page, capture_server):
        """AC — 포커스 상태가 focus/blur 마다 실시간으로 갱신된다."""
        _open_stage(page, capture_server, query="?debug=1")

        lost = page.evaluate(
            """() => {
              Object.defineProperty(document, "hasFocus", {
                value: () => false, configurable: true,
              });
              window.dispatchEvent(new Event("blur"));
              return document.getElementById("debug-focus").textContent.trim();
            }"""
        )
        assert lost == "이 창 포커스 없음", lost

        regained = page.evaluate(
            """() => {
              Object.defineProperty(document, "hasFocus", {
                value: () => true, configurable: true,
              });
              window.dispatchEvent(new Event("focus"));
              return document.getElementById("debug-focus").textContent.trim();
            }"""
        )
        assert regained == "이 창 포커스 있음", regained

    def test_overlay_is_not_a_live_region(self, page, capture_server):
        """RL-019 — 오버레이가 두 번째 라이브 리전이 되면 자막 낭독과 경쟁한다."""
        _open_stage(page, capture_server, query="?debug=1")
        regions = page.evaluate(
            """() => [...document.querySelectorAll("[aria-live]")]
                       .map((el) => el.id || el.tagName.toLowerCase())"""
        )
        assert regions == ["caption-announcer"], regions


# ---------------------------------------------------------------------------
# #178: 발표자료 연결 해제 (오퍼레이터 control 명령 / F10) + 무대 테마
# ---------------------------------------------------------------------------
def _capture_state(page) -> dict[str, Any]:
    return page.evaluate(
        """() => ({
          videoHidden: document.getElementById("capture-video").hidden,
          srcObject: document.getElementById("capture-video").srcObject === null,
          controls: !document.getElementById("capture-controls").hidden,
          label: document.getElementById("capture-connect").textContent.trim(),
          theme: document.documentElement.getAttribute("data-theme"),
          lines: [...document.querySelectorAll(".caption-line")].map(
            (el) => el.textContent
          ),
        })"""
    )


class TestPresentationDisconnect:
    """해제는 두 경로로 들어온다 — 오퍼레이터 control 명령과 F10.

    둘은 `disconnectPresentation()` **한 함수**로 수렴한다. 두 경로가 갈라지면
    한쪽만 고쳐지는 날이 온다.
    """

    def test_a_control_command_stops_the_capture(self, page, capture_server):
        """오퍼레이터 패널 경로 — 무대 창 포커스가 필요 없는 쪽이다."""
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('message', JSON.stringify({text: '해제 전 자막'}))"
        )
        page.wait_for_function(
            "() => document.querySelectorAll('.caption-line').length === 1"
        )
        _connect(page)

        page.evaluate(
            "() => window.__emit('control', JSON.stringify("
            "{presentation: 'disconnect'}))"
        )
        page.wait_for_function(
            "() => document.getElementById('capture-video').hidden === true",
            timeout=3000,
        )

        state = _capture_state(page)
        assert state["srcObject"] is True, "끊긴 스트림이 아직 붙어 있다"
        assert state["controls"] is True, "재연결 경로가 없다 — 막다른 길이다"
        assert state["label"] == "발표자료 다시 연결", state["label"]
        # 자막 컬럼은 전혀 영향받지 않는다 — 발표 영역의 사고가 자막을 끊으면 안 된다.
        assert state["lines"] == ["해제 전 자막"], state["lines"]

    def test_f10_stops_the_capture(self, page, capture_server):
        """F10 경로 — 무대 창이 포커스를 쥐고 있을 때의 지름길."""
        _open_stage(page, capture_server)
        _connect(page)

        page.keyboard.press("F10")
        page.wait_for_function(
            "() => document.getElementById('capture-video').hidden === true",
            timeout=3000,
        )

        state = _capture_state(page)
        assert state["srcObject"] is True
        assert state["controls"] is True
        assert state["label"] == "발표자료 다시 연결", state["label"]

    def test_f10_before_connecting_does_nothing(self, page, capture_server):
        """연결돼 있지 않으면 아무 일도 없다 (멱등).

        `onCaptureEnded` 를 무조건 부르면 연결 전 화면의 버튼 문구가 "다시 연결"
        로 바뀌어, 한 번도 연결하지 않은 오퍼레이터에게 거짓말을 한다.
        """
        _open_stage(page, capture_server)
        before = _capture_state(page)
        assert before["label"] == "발표자료 연결", before["label"]

        page.keyboard.press("F10")
        page.wait_for_timeout(300)

        assert _capture_state(page)["label"] == "발표자료 연결"

    def test_presenter_remote_keys_do_not_disconnect(self, page, capture_server):
        """**#178 의 핵심 안전 단언.** 리모컨 키는 캡처를 끊지 않는다.

        F10 리스너가 키를 구분하지 않으면 PageDown 한 번에 발표자료가 끊긴다 —
        행사 중 가장 비싼 회귀다. 그리고 그 키들이 취소되지 않는다는 NFR-025 의
        원래 단언도 여기서 함께 본다.
        """
        _open_stage(page, capture_server)
        _connect(page)

        state = _dispatch_presenter_keys(page)
        page.wait_for_timeout(300)

        for event in state["events"]:
            assert (
                event["defaultPrevented"] is False
            ), f"{event['key']!r} was cancelled by the stage page (NFR-025)"
        after = _capture_state(page)
        assert after["videoHidden"] is False, (
            f"리모컨 키가 발표자료를 끊었다 — F10 리스너가 키를 구분하지 않는다: "
            f"{[e['key'] for e in state['events']]}"
        )
        assert after["srcObject"] is False, "스트림이 떨어졌다"


class TestStageThemeControl:
    """테마는 control 로만 바뀐다 — 무대 페이지에 조작 UI 가 없다 (NFR-025)."""

    def test_light_theme_is_applied_from_control(self, page, capture_server):
        _open_stage(page, capture_server)
        assert _capture_state(page)["theme"] is None, "기본은 속성 없음(다크)"

        page.evaluate(
            "() => window.__emit('control', JSON.stringify({theme: 'light'}))"
        )
        page.wait_for_function(
            "() => document.documentElement.getAttribute('data-theme') === 'light'",
            timeout=3000,
        )

        # 캔버스가 실제로 밝아졌는지 — 토큰 선언이 아니라 렌더된 값으로 본다.
        bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        nums = [int(n) for n in re.findall(r"\d+", bg)[:3]]
        assert min(nums) > 200, f"라이트 테마인데 body 배경이 어둡다: {bg}"

    def test_returning_to_dark_removes_the_attribute(self, page, capture_server):
        """다크는 속성을 **지운다** — 토큰이 두 곳에 살면 갈라진다."""
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('control', JSON.stringify({theme: 'light'}))"
        )
        page.wait_for_function(
            "() => document.documentElement.getAttribute('data-theme') === 'light'"
        )

        page.evaluate("() => window.__emit('control', JSON.stringify({theme: 'dark'}))")
        page.wait_for_function(
            "() => document.documentElement.getAttribute('data-theme') === null",
            timeout=3000,
        )

    def test_an_unknown_theme_is_ignored(self, page, capture_server):
        """토큰이 없는 테마를 받으면 화면이 통째로 깨진다 — 무시한다."""
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('control', JSON.stringify({theme: 'sepia'}))"
        )
        page.wait_for_timeout(300)

        assert _capture_state(page)["theme"] is None


class TestStageSourceText:
    """무대 원문 표시 — 실제로 그려지고, 실제로 켜지는가 (#189)."""

    @staticmethod
    def _source_texts(page) -> list[str]:
        return page.eval_on_selector_all(
            ".caption-source", "els => els.map(el => el.textContent)"
        )

    @staticmethod
    def _visible(page) -> bool:
        return page.evaluate(
            "() => { const el = document.querySelector('.caption-source');"
            "  return !!el && getComputedStyle(el).display !== 'none'; }"
        )

    def test_the_source_is_rendered_but_hidden_until_switched_on(
        self, page, capture_server
    ):
        """노드는 기본에서도 채워지고, **보이기만** control 이 정한다.

        켜는 순간 쌓여 있던 줄까지 함께 나타나야 한다 — 렌더 자체를 토글에
        걸면 켠 뒤에 도착한 줄만 원문을 갖는다.
        """
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('message', JSON.stringify("
            "{text: '안녕하세요', source_text: 'Hello there'}))"
        )
        page.wait_for_function(
            "() => document.querySelectorAll('.caption-source').length === 1",
            timeout=3000,
        )
        assert self._source_texts(page) == ["Hello there"]
        assert self._visible(page) is False, "기본은 숨김이어야 한다"

        page.evaluate(
            "() => window.__emit('control', JSON.stringify({show_source: true}))"
        )
        page.wait_for_function(
            "() => document.documentElement.getAttribute('data-source') === 'on'",
            timeout=3000,
        )
        assert self._visible(page) is True, "이미 쌓인 줄이 함께 나타나야 한다"

        page.evaluate(
            "() => window.__emit('control', JSON.stringify({show_source: false}))"
        )
        page.wait_for_function(
            "() => !document.documentElement.hasAttribute('data-source')",
            timeout=3000,
        )
        assert self._visible(page) is False

    def test_the_source_renders_above_the_translation(self, page, capture_server):
        """화면에서 원문이 번역 **위**에 그려진다 (#192).

        DOM 순서만 보면 `column-reverse` 로 뒤집힌 경우를 놓친다 — 실제 렌더
        좌표로 확인한다.
        """
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('message', JSON.stringify("
            "{text: '번역문입니다', source_text: 'this is the source'}))"
        )
        page.evaluate(
            "() => window.__emit('control', JSON.stringify({show_source: true}))"
        )
        page.wait_for_function(
            "() => { const el = document.querySelector('.caption-source');"
            "  return !!el && getComputedStyle(el).display !== 'none'; }",
            timeout=3000,
        )

        tops = page.evaluate(
            "() => { const e = document.querySelector('.caption-entry');"
            "  return [e.querySelector('.caption-source').getBoundingClientRect().top,"
            "          e.querySelector('.caption-line').getBoundingClientRect().top]; }"
        )
        source_top, line_top = tops
        assert source_top < line_top, (
            f"원문이 번역 위에 있지 않다 (원문 top={source_top}, 번역 "
            f"top={line_top})"
        )

    def test_one_utterance_is_one_entry(self, page, capture_server):
        """발화 하나가 entry 하나 — 번역과 원문이 한 묶음으로 산다."""
        _open_stage(page, capture_server)
        for i in range(3):
            page.evaluate(
                "(i) => window.__emit('message', JSON.stringify("
                "{text: '번역 ' + i, source_text: 'source ' + i}))",
                i,
            )
        page.wait_for_function(
            "() => document.querySelectorAll('.caption-entry').length === 3",
            timeout=3000,
        )
        assert self._source_texts(page) == ["source 0", "source 1", "source 2"]
        # 번역 라인이 컨테이너 직계로 새지 않는다.
        assert (
            page.evaluate(
                "() => document.querySelectorAll("
                "'#caption-container > .caption-line').length"
            )
            == 0
        )

    def test_the_two_scales_move_independently(self, page, capture_server):
        """원문 배율이 번역 글자 크기를 건드리지 않는다 — 그 반대도 마찬가지."""
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('message', JSON.stringify("
            "{text: '번역문', source_text: 'source'}))"
        )
        page.evaluate(
            "() => window.__emit('control', JSON.stringify({show_source: true}))"
        )
        page.wait_for_function(
            "() => { const el = document.querySelector('.caption-source');"
            "  return !!el && getComputedStyle(el).display !== 'none'; }",
            timeout=3000,
        )

        def sizes():
            return page.evaluate(
                "() => [getComputedStyle(document.querySelector('.caption-line'))"
                ".fontSize,"
                " getComputedStyle(document.querySelector('.caption-source'))"
                ".fontSize]"
            )

        line_before, source_before = sizes()

        page.evaluate(
            "() => window.__emit('control', JSON.stringify({source_scale: 1.6}))"
        )
        page.wait_for_function(
            "(before) => getComputedStyle(document.querySelector('.caption-source'))"
            ".fontSize !== before",
            arg=source_before,
            timeout=3000,
        )
        line_after, source_after = sizes()
        assert (
            line_after == line_before
        ), f"원문 배율이 번역 크기까지 바꿨다: {line_before} -> {line_after}"
        assert float(source_after[:-2]) > float(source_before[:-2])

        page.evaluate(
            "() => window.__emit('control', JSON.stringify({caption_scale: 1.6}))"
        )
        page.wait_for_function(
            "(before) => getComputedStyle(document.querySelector('.caption-line'))"
            ".fontSize !== before",
            arg=line_before,
            timeout=3000,
        )
        _line_final, source_final = sizes()
        assert source_final == source_after, "번역 배율이 원문 크기까지 바꿨다"

    def test_an_invalid_show_source_does_not_flip_the_screen(
        self, page, capture_server
    ):
        """배율만 담긴 control 하나가 원문을 꺼 버리면 안 된다."""
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('control', JSON.stringify({show_source: true}))"
        )
        page.wait_for_function(
            "() => document.documentElement.getAttribute('data-source') === 'on'"
        )
        page.evaluate(
            "() => window.__emit('control', JSON.stringify({caption_scale: 1.2}))"
        )
        page.wait_for_timeout(300)
        assert (
            page.evaluate("() => document.documentElement.getAttribute('data-source')")
            == "on"
        )


class TestLogoBarToneControl:
    """로고 바 배경을 오퍼레이터가 행사 중에 바꾼다 (#182).

    관리자 설정이 룸의 기본값이고, 이 control 은 그 세션에 한해 덮어쓴다 —
    행사장 프로젝터에서 로고가 묻히는 것을 그 자리에서 고칠 수 있어야 한다.
    """

    @staticmethod
    def _tone(page) -> str:
        return page.evaluate(
            "() => document.getElementById('logo-bar').dataset.tone || 'auto'"
        )

    @pytest.mark.parametrize("tone", ["light", "dark"])
    def test_a_tone_from_control_is_applied(self, page, capture_server, tone):
        _open_stage(page, capture_server)
        assert self._tone(page) == "auto", "룸 기본값이 auto 가 아니다"

        page.evaluate(
            "(t) => window.__emit('control', JSON.stringify({logo_bar_tone: t}))", tone
        )
        page.wait_for_function(
            "(t) => document.getElementById('logo-bar').dataset.tone === t",
            arg=tone,
            timeout=3000,
        )

        # 선언된 면이 실제로 칠해졌는지 — 토큰 선언이 아니라 렌더된 값으로 본다.
        bg = page.evaluate(
            "() => getComputedStyle(document.getElementById('logo-bar')).backgroundColor"
        )
        nums = [int(n) for n in re.findall(r"\d+", bg)[:3]]
        if tone == "light":
            assert min(nums) > 180, f"밝은 톤인데 면이 어둡다: {bg}"
        else:
            assert max(nums) < 90, f"어두운 톤인데 면이 밝다: {bg}"

    def test_returning_to_auto_removes_the_attribute(self, page, capture_server):
        """`auto` 는 속성을 지운다 — 기본 규칙과 값이 두 곳에 살면 안 된다."""
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('control', JSON.stringify({logo_bar_tone: 'dark'}))"
        )
        page.wait_for_function(
            "() => document.getElementById('logo-bar').dataset.tone === 'dark'"
        )

        page.evaluate(
            "() => window.__emit('control', JSON.stringify({logo_bar_tone: 'auto'}))"
        )
        page.wait_for_function(
            "() => !document.getElementById('logo-bar').hasAttribute('data-tone')",
            timeout=3000,
        )

    def test_an_unknown_tone_is_ignored(self, page, capture_server):
        _open_stage(page, capture_server)
        page.evaluate(
            "() => window.__emit('control', JSON.stringify({logo_bar_tone: 'band'}))"
        )
        page.wait_for_timeout(300)
        assert self._tone(page) == "auto"
