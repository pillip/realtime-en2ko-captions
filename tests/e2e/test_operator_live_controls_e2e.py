"""
ISSUE-54 — 오퍼레이터 실시간 컨트롤 e2e (TC-087, TC-088).

`components/webrtc.html` 을 Streamlit 없이 직접 로드하는 ISSUE-47 하네스를
이어받되, **번역 파이프라인이 실제로 붙은 상태**까지 재현한다. 그러기 위해
브라우저 API 네 가지(`navigator.permissions`, `navigator.mediaDevices`,
`RTCPeerConnection`, `fetch`)와 `WebSocket` 을 스텁으로 갈아끼우고 부트스트랩의
`BOOT.action === 'start'` 경로를 **그대로** 태운다.

소켓을 테스트가 직접 꽂지 않는 이유가 있다. `openaiWebSocket` 은 컴포넌트
`try` 블록 안의 `let` 이라 바깥에서 보이지 않는데, 그걸 보이게 하려고
프로덕션에 테스트 전용 seam 을 뚫으면 **그 seam 이 검증 대상이 되어 버린다** —
진짜 연결 경로가 소켓을 어떻게 얻고 언제 `readyState` 가 OPEN 이 되는지는
아무도 확인하지 않게 된다. 실제 경로를 태우면 `auth` 프레임이 나가는 것까지
덤으로 검증된다.
"""

from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()

pytestmark = pytest.mark.e2e

_WEBRTC_TEMPLATE = (
    pathlib.Path(__file__).resolve().parents[2] / "components" / "webrtc.html"
)

_SLIDER = "#stageScaleSlider"
_VALUE = "#stageScaleValue"

# 슬라이더의 디바운스(150ms)보다 넉넉히 길게 기다린다. 이 값이 디바운스보다
# 짧으면 "전송 0건" 계열 단언이 **아직 안 보냈을 뿐**인 상태를 성공으로 읽는다.
_AFTER_DEBOUNCE_MS = 600


# `initializeOpenAIConnection()` 이 async function 이라 Annex B 블록 호이스팅
# 대상이 아니고 `page.evaluate` 로 부를 수 없다. 그래서 부트스트랩이 스스로
# 연결하도록 필요한 브라우저 API 를 전부 성공 경로로 스텁한다.
_BROWSER_STUBS = """
window.__wsInstances = [];
window.__rtcInstances = [];
window.__consoleErrors = [];

const __origConsoleError = console.error.bind(console);
console.error = function () {
  window.__consoleErrors.push(Array.from(arguments).map(String).join(' '));
  __origConsoleError.apply(null, arguments);
};
window.addEventListener('error', function (e) {
  window.__consoleErrors.push('uncaught: ' + e.message);
});
window.addEventListener('unhandledrejection', function (e) {
  window.__consoleErrors.push('rejection: ' + String(e.reason));
});

function __FakeWebSocket(url) {
  this.url = url;
  this.readyState = 0;            // CONNECTING
  this.sent = [];
  this.onopen = null;
  this.onmessage = null;
  this.onerror = null;
  this.onclose = null;
  window.__wsInstances.push(this);
  const self = this;
  // 실제 소켓처럼 비동기로 열린다 — 동기로 열면 onopen 핸들러가 아직
  // 붙기 전이라 auth 프레임이 조용히 사라진다.
  setTimeout(function () {
    self.readyState = 1;          // OPEN
    if (self.onopen) self.onopen({});
  }, 0);
}
__FakeWebSocket.prototype.send = function (payload) { this.sent.push(payload); };
__FakeWebSocket.prototype.close = function () {
  this.readyState = 3;            // CLOSED
  if (this.onclose) this.onclose({ code: 1000 });
};
__FakeWebSocket.CONNECTING = 0;
__FakeWebSocket.OPEN = 1;
__FakeWebSocket.CLOSING = 2;
__FakeWebSocket.CLOSED = 3;
window.WebSocket = __FakeWebSocket;

function __fakeTrack() { return { kind: 'audio', stop: function () {} }; }
function __fakeStream() {
  return {
    getTracks: function () { return [__fakeTrack()]; },
    getAudioTracks: function () { return [__fakeTrack()]; }
  };
}
Object.defineProperty(navigator, 'permissions', {
  configurable: true,
  value: { query: function () { return Promise.resolve({ state: 'granted' }); } }
});
Object.defineProperty(navigator, 'mediaDevices', {
  configurable: true,
  value: {
    getUserMedia: function () { return Promise.resolve(__fakeStream()); },
    enumerateDevices: function () {
      return Promise.resolve([
        { kind: 'audioinput', deviceId: 'default', label: '테스트 마이크' }
      ]);
    }
  }
});

window.RTCPeerConnection = function () {
  window.__rtcInstances.push(this);
  this.createDataChannel = function () {
    return { close: function () {} };
  };
  this.addTrack = function () {};
  this.createOffer = function () {
    return Promise.resolve({ type: 'offer', sdp: 'v=0\\r\\n' });
  };
  this.setLocalDescription = function () { return Promise.resolve(); };
  this.setRemoteDescription = function () { return Promise.resolve(); };
  this.close = function () {};
};

window.fetch = function () {
  return Promise.resolve({
    ok: true,
    status: 200,
    text: function () { return Promise.resolve('v=0\\r\\n'); }
  });
};
"""


def _boot_payload(**overrides) -> dict:
    payload = {
        "action": "idle",
        "openai_session": None,
        "service": "openai_realtime",
        "websocket_port": 8765,
        "user_info": {"id": 1, "username": "op1", "role": "operator"},
        "room_id": "room-42",
        "room_name": "A홀",
        "view_url": "http://localhost:8766/view/room-42",
        "qr_data_url": None,
        "display_mode": "caption",
        "stage_url": None,
    }
    payload.update(overrides)
    return payload


def _connected_boot(**overrides) -> dict:
    """부트스트랩이 스스로 번역 소켓을 여는 payload."""
    return _boot_payload(
        action="start",
        openai_session={"client_secret": "ek_test"},
        **overrides,
    )


@pytest.fixture
def load_webrtc(page, tmp_path):
    def _load(boot: dict, *, local_scale: str | None = None):
        # ISSUE-48 / RL-024: 치환을 fixture 가 재구현하면 프로덕션이 고쳐져도
        # 이 파일은 옛 경로를 계속 테스트한다. 프로덕션 함수를 그대로 부른다.
        from operator_ui import render_component_html

        html = render_component_html(_WEBRTC_TEMPLATE.read_text(encoding="utf-8"), boot)
        target = tmp_path / "webrtc_under_test.html"
        target.write_text(html, encoding="utf-8")
        page.add_init_script(_BROWSER_STUBS)
        if local_scale is not None:
            # json.dumps 로 JS 리터럴을 만든다 — 값이 그대로 스크립트 문맥에
            # 들어가므로 f-string 보간이 아니라 직렬화가 맞다.
            scale_literal = json.dumps(local_scale)
            page.add_init_script(
                f"try {{ localStorage.setItem('stageCaptionScale', {scale_literal}); }} catch (e) {{}}"
            )
        page.goto(target.as_uri(), wait_until="load")
        page.wait_for_timeout(700)
        return page

    return _load


def _sent(page, index: int = 0) -> list[dict]:
    """`index` 번째 소켓이 실제로 보낸 프레임들."""
    raw = page.evaluate(
        "(i) => (window.__wsInstances[i] ? window.__wsInstances[i].sent : [])", index
    )
    return [json.loads(item) for item in raw]


def _stage_controls(page) -> list[dict]:
    return [m for m in _sent(page) if m.get("type") == "stage_control"]


def _deliver(page, message: dict, index: int = 0) -> None:
    """서버가 보낸 프레임을 실제 `onmessage` 경로로 흘려보낸다."""
    page.evaluate(
        "([i, payload]) => window.__wsInstances[i].onmessage({ data: payload })",
        [index, json.dumps(message)],
    )


def _drag_end_to_end(page) -> None:
    """한 번의 연속 드래그로 슬라이더를 왼쪽 끝에서 오른쪽 끝까지 민다."""
    page.locator("#settingsFab").click()
    page.wait_for_timeout(500)
    box = page.locator(_SLIDER).bounding_box()
    assert box is not None, "the stage scale slider is not laid out"
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 2, y)
    page.mouse.down()
    steps = 24
    for i in range(1, steps + 1):
        page.mouse.move(box["x"] + (box["width"] - 2) * i / steps, y)
        page.wait_for_timeout(20)
    page.mouse.up()


# ---------------------------------------------------------------------------
# TC-087 — 드래그 전송량과 미연결 상태
# ---------------------------------------------------------------------------
class TestSliderOutboundTraffic:
    def test_the_socket_actually_opened(self, load_webrtc):
        """RL-004 게이트 — 아래 단언들이 공허하지 않다는 양성 신호.

        연결이 안 됐다면 "전송이 9건 이하" 는 0건으로 자동 통과한다.
        """
        page = load_webrtc(_connected_boot())
        assert page.evaluate("window.__wsInstances.length") == 1
        assert page.evaluate("window.__wsInstances[0].readyState") == 1
        auth = [m for m in _sent(page) if m.get("type") == "auth"]
        assert len(auth) == 1, f"the real auth frame never went out: {_sent(page)!r}"

    def test_one_drag_sends_a_single_digit_number_of_messages(self, load_webrtc):
        """AC — 틱마다 방송하지 않는다. 개수를 세는 단언으로 못박는다."""
        page = load_webrtc(_connected_boot())
        _drag_end_to_end(page)
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)

        controls = _stage_controls(page)
        assert 1 <= len(controls) < 10, (
            f"one end-to-end drag produced {len(controls)} stage_control "
            f"messages — every one of them fans out to every viewer of the room "
            f"over SSE: {controls!r}"
        )

    def test_the_last_message_carries_the_final_slider_value(self, load_webrtc):
        """디바운스가 **마지막** 값을 보내야 무대가 손 뗀 자리에 멈춘다."""
        page = load_webrtc(_connected_boot())
        _drag_end_to_end(page)
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)

        final_value = float(page.locator(_SLIDER).input_value())
        controls = _stage_controls(page)
        assert controls, "the drag sent nothing at all"
        assert controls[-1]["caption_scale"] == pytest.approx(final_value), (
            f"the last stage_control carried {controls[-1]['caption_scale']} but "
            f"the slider rests at {final_value} — the stage would freeze at an "
            "intermediate size the operator never chose"
        )
        assert final_value == pytest.approx(
            1.6
        ), f"dragging to the right edge landed on {final_value}, not the 1.6 max"

    def test_every_sent_scale_is_inside_the_server_accepted_range(self, load_webrtc):
        """범위를 벗어난 값은 서버(ISSUE-53)가 통째로 거절한다."""
        page = load_webrtc(_connected_boot())
        _drag_end_to_end(page)
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)
        for message in _stage_controls(page):
            assert 0.8 <= message["caption_scale"] <= 1.6, (
                f"sent an out-of-range scale {message['caption_scale']} — the "
                "server rejects it and the operator sees a failure for a value "
                "the slider itself produced"
            )

    def test_the_readout_tracks_the_slider_during_the_drag(self, load_webrtc):
        """전송만 억제하고 표시값은 매 틱 갱신한다."""
        page = load_webrtc(_connected_boot())
        _drag_end_to_end(page)
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)
        assert page.locator(_VALUE).inner_text().strip() == "1.6×"


class TestSliderWithoutASocket:
    """AC — 세션 미시작 상태에서 슬라이더를 움직여도 조용해야 한다 (RL-006)."""

    @staticmethod
    def _operate(page) -> None:
        page.locator("#settingsFab").click()
        page.wait_for_timeout(500)
        page.evaluate("window.__consoleErrors = []")
        page.locator(_SLIDER).fill("1.3")
        page.locator(_SLIDER).press("ArrowUp")
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)

    def test_nothing_is_sent_and_nothing_throws(self, load_webrtc):
        page = load_webrtc(_boot_payload())
        assert page.evaluate("window.__wsInstances.length") == 0, (
            "a socket was opened in idle mode — this test would then be checking "
            "the connected path"
        )
        self._operate(page)

        assert page.evaluate("window.__wsInstances.length") == 0
        assert page.evaluate("window.__consoleErrors") == [], (
            "operating the slider without a session raised something: "
            f"{page.evaluate('window.__consoleErrors')!r}"
        )

    def test_the_value_still_moves_locally(self, load_webrtc):
        """조용한 것과 죽은 것은 다르다 — 표시는 따라와야 한다."""
        page = load_webrtc(_boot_payload())
        self._operate(page)
        assert float(page.locator(_SLIDER).input_value()) == pytest.approx(1.4)
        assert page.locator(_VALUE).inner_text().strip() == "1.4×"

    def test_no_internal_error_text_reaches_the_operator(self, load_webrtc):
        page = load_webrtc(_boot_payload())
        self._operate(page)
        body_text = page.locator("body").inner_text()
        for leaked in (
            "Traceback",
            "TypeError",
            "ReferenceError",
            "InvalidStateError",
            "Error:",
            "Exception",
            "undefined",
            ".html:",
        ):
            assert leaked not in body_text, (
                f"internal detail {leaked!r} surfaced to the operator (RL-006): "
                f"{body_text!r}"
            )

    def test_the_hint_does_not_claim_success_before_the_session_starts(
        self, load_webrtc
    ):
        """AC — 시작 전에는 단정적인 성공 표시를 하지 않는다."""
        page = load_webrtc(_boot_payload())
        page.locator("#settingsFab").click()
        page.wait_for_timeout(500)
        hint = page.locator("#stageScaleHint").inner_text()
        assert "세션을 시작하면" in hint, (
            f"the idle-state hint reads {hint!r} — an operator who is told the "
            "size applied will not understand why the venue screen is unchanged"
        )


# ---------------------------------------------------------------------------
# TC-088 — 서버 스냅샷 우선, ack 되먹임 가드
# ---------------------------------------------------------------------------
class TestServerSnapshotWins:
    def test_auth_snapshot_beats_a_stale_local_value(self, load_webrtc):
        """AC — `localStorage` 1.0 과 서버 1.4 가 부딪히면 서버가 이긴다."""
        page = load_webrtc(_connected_boot(), local_scale="1.0")
        page.locator("#settingsFab").click()
        page.wait_for_timeout(400)
        assert float(page.locator(_SLIDER).input_value()) == pytest.approx(1.0), (
            "the localStorage fallback did not apply, so the assertion below "
            "would pass even if the snapshot handling were deleted"
        )

        _deliver(
            page,
            {
                "type": "auth_success",
                "message": "인증 완료",
                "room_id": "room-42",
                "control": {"primary_lang": "ko", "caption_scale": 1.4},
            },
        )
        page.wait_for_timeout(300)

        assert float(page.locator(_SLIDER).input_value()) == pytest.approx(1.4), (
            "the slider still shows the stale local value — the operator would "
            "believe the room is at 1.0 while the venue renders at 1.4"
        )
        assert page.locator(_VALUE).inner_text().strip() == "1.4×"

    def test_the_snapshot_does_not_echo_back_to_the_server(self, load_webrtc):
        """스냅샷 적용이 전송을 유발하면 서버 ↔ 클라이언트 왕복이 된다."""
        page = load_webrtc(_connected_boot(), local_scale="1.0")
        _deliver(
            page,
            {
                "type": "auth_success",
                "message": "인증 완료",
                "room_id": "room-42",
                "control": {"caption_scale": 1.4},
            },
        )
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)
        assert _stage_controls(page) == [], (
            "applying the server snapshot sent a stage_control back — that is "
            "the feedback loop the issue calls out"
        )

    def test_a_room_without_control_state_sends_nothing_on_load(self, load_webrtc):
        """AC — 상태가 없는 룸은 기본값을 표시하고 아무것도 자동 전송하지 않는다."""
        page = load_webrtc(_connected_boot())
        _deliver(
            page,
            {
                "type": "auth_success",
                "message": "인증 완료",
                "room_id": "room-42",
                "control": None,
            },
        )
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)

        page.locator("#settingsFab").click()
        page.wait_for_timeout(400)
        assert float(page.locator(_SLIDER).input_value()) == pytest.approx(1.0)
        assert _stage_controls(page) == [], (
            "the component announced a scale nobody asked for — on a shared room "
            "that would overwrite another operator's setting at page load"
        )


class TestAckDoesNotLoop:
    def test_ack_sets_the_displayed_value(self, load_webrtc):
        page = load_webrtc(_connected_boot())
        _deliver(page, {"type": "stage_control_ack", "caption_scale": 1.2})
        page.wait_for_timeout(300)
        assert float(page.locator(_SLIDER).input_value()) == pytest.approx(1.2)
        assert page.locator(_VALUE).inner_text().strip() == "1.2×"

    def test_ack_triggers_no_further_outbound_message(self, load_webrtc):
        page = load_webrtc(_connected_boot())
        page.locator("#settingsFab").click()
        page.wait_for_timeout(500)
        page.locator(_SLIDER).fill("1.3")
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)
        before = len(_stage_controls(page))
        assert before >= 1, "the slider never sent anything — the guard is vacuous"

        _deliver(page, {"type": "stage_control_ack", "caption_scale": 1.3})
        _deliver(page, {"type": "stage_control_ack", "caption_scale": 1.3})
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)

        assert len(_stage_controls(page)) == before, (
            "an ack produced another stage_control — each round trip fans out to "
            "every viewer, so a loop here floods the whole room"
        )


# ---------------------------------------------------------------------------
# 세션 중 언어 변경이 파이프라인을 끊지 않는다 (AC1)
# ---------------------------------------------------------------------------
class TestMidSessionLanguageChange:
    @staticmethod
    def _change_output_language(page, value: str) -> None:
        page.locator("#settingsFab").click()
        page.wait_for_timeout(500)
        page.select_option("#selOutputLang", value)
        page.wait_for_timeout(400)

    def test_the_change_travels_over_the_already_open_socket(self, load_webrtc):
        page = load_webrtc(_connected_boot())
        self._change_output_language(page, "vi")
        updates = [m for m in _sent(page) if m.get("type") == "language_update"]
        assert len(updates) == 1, f"expected one language_update, got {updates!r}"
        assert updates[0]["output_lang"] == "vi"

    def test_the_document_is_not_reloaded(self, load_webrtc):
        """AC1 — iframe 리마운트는 세션을 통째로 죽인다 (ISSUE-47 F-2).

        문서가 새로 로드되면 `window` 에 심어 둔 표식이 사라진다. 이건
        "srcdoc 이 바뀌지 않았다" 를 브라우저 쪽에서 관찰한 것이다.
        """
        page = load_webrtc(_connected_boot())
        page.evaluate("window.__sessionToken = 'alive'")
        self._change_output_language(page, "vi")
        assert page.evaluate("window.__sessionToken") == "alive", (
            "the document was re-created by the language change — that is the "
            "srcdoc remount that destroys the RTCPeerConnection and the mic "
            "stream (RL-028)"
        )

    def test_the_socket_and_the_peer_connection_are_not_rebuilt(self, load_webrtc):
        page = load_webrtc(_connected_boot())
        self._change_output_language(page, "vi")
        assert page.evaluate("window.__wsInstances.length") == 1, (
            "a second WebSocket was created — the language change reconnected "
            "the translation pipeline instead of reusing the open socket"
        )
        assert page.evaluate("window.__rtcInstances.length") == 1
        assert page.evaluate("window.__wsInstances[0].readyState") == 1

    def test_the_caption_scrollback_survives(self, load_webrtc):
        """AC1 — 전환 뒤에도 빈 검은 화면이 되지 않는다."""
        page = load_webrtc(_connected_boot())
        page.evaluate("appendLine('첫 번째 문장입니다', 'stable')")
        page.evaluate("appendLine('두 번째 문장입니다', 'stable')")
        page.wait_for_timeout(200)
        before = page.locator(".caption-line").count()
        assert before == 2

        self._change_output_language(page, "vi")
        assert page.locator(".caption-line").count() == before, (
            "the caption scrollback was cleared by a language change — the "
            "operator loses the transcript of everything said so far"
        )

    def test_the_slider_state_survives_the_language_change(self, load_webrtc):
        page = load_webrtc(_connected_boot())
        page.locator("#settingsFab").click()
        page.wait_for_timeout(500)
        page.locator(_SLIDER).fill("1.5")
        page.wait_for_timeout(_AFTER_DEBOUNCE_MS)
        self._change_output_language(page, "vi")
        assert float(page.locator(_SLIDER).input_value()) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# 접근성 — 키보드 (RL-010)
# ---------------------------------------------------------------------------
class TestKeyboardAccess:
    def test_tab_reaches_the_slider_and_arrows_change_it(self, load_webrtc):
        page = load_webrtc(_connected_boot())
        page.locator("#settingsFab").click()
        page.wait_for_timeout(500)

        page.locator(_SLIDER).focus()
        assert page.evaluate("document.activeElement.id") == "stageScaleSlider"

        start = float(page.locator(_SLIDER).input_value())
        page.locator(_SLIDER).press("ArrowRight")
        page.wait_for_timeout(150)
        moved = float(page.locator(_SLIDER).input_value())
        assert moved == pytest.approx(start + 0.1), (
            f"ArrowRight moved the value from {start} to {moved} — a keyboard "
            "operator cannot step the scale"
        )
        assert page.locator(_VALUE).inner_text().strip() == f"{moved:.1f}×"

    def test_the_slider_is_reachable_by_tabbing_from_the_language_select(
        self, load_webrtc
    ):
        """포커스 순서상 도달 불가능한 컨트롤은 없는 것과 같다."""
        page = load_webrtc(_connected_boot())
        page.locator("#settingsFab").click()
        page.wait_for_timeout(500)
        page.locator("#selOutputLang").focus()
        for _ in range(6):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement.id") == "stageScaleSlider":
                return
        pytest.fail(
            "tabbing forward from the output language select never reached the "
            f"stage scale slider (stopped at "
            f"{page.evaluate('document.activeElement.id')!r})"
        )

    def test_the_label_is_bound_to_the_input(self, load_webrtc):
        page = load_webrtc(_connected_boot())
        bound = page.evaluate(
            "() => { const i = document.getElementById('stageScaleSlider');"
            " return i.labels ? Array.from(i.labels).map(l => l.textContent.trim())"
            " : []; }"
        )
        assert "무대 자막 크기" in bound, (
            f"the slider's accessible name comes from {bound!r} — a range input "
            "with no bound label announces as an unnamed slider"
        )


# ---------------------------------------------------------------------------
# 대비 — 합성된 실제 렌더 값 (RL-018 / ISSUE-49)
# ---------------------------------------------------------------------------
class TestRenderedContrast:
    def test_settings_panel_has_no_inherited_opacity(self, load_webrtc):
        """ISSUE-49 — `opacity` 는 자식의 알파를 곱한다.

        `.fs-font-controls { opacity: 0.6 }` 안의 텍스트가 그랬듯, 조상이
        투명도를 걸면 `color` 규칙만 본 대비 계산은 실제 렌더 값보다 높게
        나온다. 정적 테스트는 CSS 선언을 보고, 이쪽은 **계산된 값**을 본다.
        """
        page = load_webrtc(_connected_boot())
        page.locator("#settingsFab").click()
        page.wait_for_timeout(500)
        product = page.evaluate(
            "() => { let n = document.getElementById('stageScaleValue'), p = 1;"
            " while (n) { p *= parseFloat(getComputedStyle(n).opacity); "
            " n = n.parentElement; } return p; }"
        )
        assert product == pytest.approx(1.0), (
            f"the composited opacity along the slider readout's ancestor chain "
            f"is {product} — every contrast number computed from the `color` "
            "rule alone is therefore wrong (ISSUE-49)"
        )

    def test_the_readout_renders_the_expected_colour(self, load_webrtc):
        """정적 테이블의 수치가 **이 요소**에 실제로 적용된다는 연결 고리."""
        page = load_webrtc(_connected_boot())
        page.locator("#settingsFab").click()
        page.wait_for_timeout(500)
        colour = page.evaluate(
            "getComputedStyle(document.getElementById('stageScaleValue')).color"
        )
        assert colour.replace(" ", "") == "rgba(255,255,255,0.55)", (
            f"the readout renders {colour!r}, not the rgba(255,255,255,0.55) the "
            "contrast table was computed from"
        )
