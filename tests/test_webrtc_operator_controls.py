"""
ISSUE-54 — 오퍼레이터 설정 패널의 실시간 컨트롤 정적 검증 (TC-086, TC-089 인접).

행동 기반 짝은 `tests/e2e/test_operator_live_controls_e2e.py` (TC-087/TC-088) 다.
여기서는 브라우저 없이도 못박을 수 있는 계약만 단언한다.

이 파일이 지키는 것 네 가지:

  1. **소유권 경계** — ISSUE-53 이 `components/stage.html` / `components/viewer.html`
     의 control 수신·재구독·`--caption-scale` 을 전부 소유한다. 이 이슈가 그
     두 파일을 한 줄이라도 건드리면 같은 동작에 주인이 둘이 된다.
  2. **전송 경로** — 새 배율은 BOOT payload 가 아니라 이미 열려 있는 WebSocket
     으로 흐른다. payload 를 타면 iframe `srcdoc` 이 바뀌고 Streamlit 이 문서를
     새로 로드해 세션이 죽는다 (ISSUE-47 F-2 / RL-028). 키 집합 상등 단언 자체는
     `tests/test_operator_ui.py::TestBootstrapPayloadKeySetIsFrozen` (TC-089) 에
     있고, 여기서는 **템플릿 쪽 절반** — 컴포넌트가 BOOT 에서 배율을 읽지
     않는다는 사실 — 을 맡는다.
  3. **WCAG AA (TC-086)** — 새 컨트롤이 쓰는 텍스트 색을 **설정 패널의 실제
     배경 `#0e0e10`** 위에서 합성해 계산한다. 뷰어 캔버스 `#0b0b0c` 를 쓰면
     그게 바로 RL-018 이다. 계산 결과는 아래 parametrize 목록에 수치로 남긴다.
  4. **기존 슬라이더 불변** — `#fontSizeSlider` / `#originalSizeSlider` 는
     오퍼레이터 **자기 화면**용이라 대상이 다르다. 새 슬라이더를 넣으면서
     이 둘을 "통합" 하면 오퍼레이터가 자기 화면 글자를 키울 때마다 행사장
     무대 자막이 함께 커진다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.wcag import _contrast_ratio, _hex_rgb, _rule_block, _rule_rgba

if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WEBRTC_TEMPLATE = _REPO_ROOT / "components" / "webrtc.html"

# 설정 패널이 실제로 래스터라이즈되는 배경. `.settings-panel { background:
# #0e0e10 }` — 뷰어 캔버스(`#0b0b0c`) 도 body(`#0a0a0b`) 도 아니다. 패널은
# `position: fixed` + 불투명 배경이라 아래 레이어가 비치지 않는다.
_SETTINGS_PANEL_BACKDROP = _hex_rgb("#0e0e10")

_SLIDER_ID = "stageScaleSlider"
_VALUE_ID = "stageScaleValue"


@pytest.fixture(scope="module")
def html() -> str:
    return _WEBRTC_TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css(html: str) -> str:
    match = re.search(r"<style>(.*?)</style>", html, re.S)
    assert match is not None, "webrtc.html has no <style> block"
    return match.group(1)


def _input_attrs(html: str, element_id: str) -> dict[str, str]:
    """`<input ... id="…" ...>` 의 속성을 dict 로 뽑는다."""
    match = re.search(rf"<input\b[^>]*\bid=\"{re.escape(element_id)}\"[^>]*>", html)
    assert match is not None, f"no <input> with id={element_id!r}"
    return dict(re.findall(r'(\w[\w-]*)="([^"]*)"', match.group(0)))


# ---------------------------------------------------------------------------
# 소유권 경계 — stage.html / viewer.html 변경 0줄
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 제거됨: TestTemplateOwnershipBoundary (ISSUE-53/54 스프린트 간 소유권 가드)
# ---------------------------------------------------------------------------
# `components/stage.html` / `components/viewer.html` 의 브랜치 diff 가 **비어
# 있어야 한다** 고 단언하던 클래스였다. 목적은 ISSUE-54(오퍼레이터 컨트롤)
# 브랜치가 ISSUE-53(무대/뷰어 control 처리) 소유 파일을 건드리지 않게 하는
# 스프린트 간 조율이었다.
#
# **그 질문은 ISSUE-54 가 머지된 시점에 답이 끝났다.** 그런데 가드는 남아
# "어떤 브랜치도 이 두 템플릿을 고칠 수 없다" 는 영구 금지로 작동했다 — 무대
# 화면에 기능을 더하려면 `stage.html` 의 control 핸들러를 고쳐야 하므로, 무대
# 관련 작업은 전부 이 가드에 걸린다. 실제로 무대 테마/발표자료 해제(#178)에서
# 걸렸다.
#
# 소유권이 다시 문제가 되는 스프린트가 오면 그때의 커밋 범위로 다시 세우는 것이
# 맞다. 커밋 범위를 모르는 영구 가드는 "남의 파일을 건드렸나" 가 아니라 "그
# 파일을 건드렸나" 를 묻는다.
# ---------------------------------------------------------------------------
# 전송 경로 — BOOT payload 가 아니라 WebSocket
# ---------------------------------------------------------------------------
class TestScaleTravelsOverTheSocket:
    def test_component_never_reads_the_scale_from_boot(self, html: str):
        """BOOT 에서 배율을 읽으면 그 값은 payload 를 타야만 도착한다.

        `build_bootstrap_payload` 쪽 가드(TC-089)와 짝이다. 서버가 키를 안
        넣어도 컴포넌트가 `BOOT.caption_scale` 을 읽고 있으면, 다음 사람이
        "값이 안 온다" 를 보고 payload 에 키를 더하는 것이 자연스러운 수리가
        된다 — 그 순간 iframe 이 리마운트된다.
        """
        for forbidden in (
            "BOOT.caption_scale",
            "BOOT.stage_caption_scale",
            "BOOT.control",
            "BOOT.primary_lang",
        ):
            assert forbidden not in html, (
                f"{forbidden} is read from the bootstrap payload — session-"
                "adjustable state must travel over the already-open WebSocket, "
                "not the srcdoc (ISSUE-47 F-2 / RL-028)"
            )

    def test_the_slider_sends_a_stage_control_message(self, html: str):
        """서버(ISSUE-53)가 받는 프로토콜 그대로여야 한다."""
        assert "'stage_control'" in html or '"stage_control"' in html
        assert "caption_scale" in html

    def test_the_send_is_guarded_by_an_open_socket(self, html: str):
        """RL-006 — 미연결 상태에서 `send` 를 부르면 예외가 오퍼레이터에게 간다.

        `sendLanguageUpdate()` 가 이미 쓰는 `readyState === WebSocket.OPEN`
        가드와 같은 형태를 요구한다.
        """
        assert re.search(
            r"readyState\s*===\s*WebSocket\.OPEN", html
        ), "no readyState === WebSocket.OPEN guard on the outbound path"

    def test_the_send_is_debounced_rather_than_fired_per_tick(self, html: str):
        """AC — 한 번의 드래그가 수십 건의 control 방송이 되지 않는다.

        표시값은 매 틱 갱신하고 **전송만** 억제한다. 정적으로 확인할 수 있는
        것은 전송 경로에 타이머가 끼어 있다는 사실뿐이라, 실제 개수 단언은
        TC-087 (e2e) 이 맡는다.
        """
        assert "setTimeout" in html and "clearTimeout" in html
        assert re.search(r"STAGE_SCALE_SEND_DELAY_MS\s*=\s*\d+", html), (
            "no named debounce delay for the stage_control send path — the "
            "message count guard (TC-087) needs a single knob to reason about"
        )


# ---------------------------------------------------------------------------
# 슬라이더 마크업
# ---------------------------------------------------------------------------
class TestStageSourceControls:
    """무대 원문 표시/배율 — 보내는 쪽(오퍼레이터) 계약 (#189)."""

    @staticmethod
    def _fn(html: str, name: str) -> str:
        body = re.search(rf"function {name}\([^)]*\) \{{(.*?)\n  \}}", html, re.S)
        assert body is not None, f"{name}() not found"
        return body.group(1)

    def test_show_helpers_never_send(self, html: str):
        """표시 함수는 **전송하지 않는다** — 서버 스냅샷과 ack 가 부르기 때문이다.

        여기서 보내면 서버가 내려준 값이 곧바로 서버로 되돌아가는 무한 왕복이
        된다. `showStageScale` 이 이미 같은 규칙을 지고 있고, 원문 쪽도 같아야
        한다 — 한쪽만 어기면 그 한쪽에서만 왕복이 생긴다.
        """
        for name in ("showStageSourceScale", "showStageShowSource"):
            fn = self._fn(html, name)
            assert "openaiWebSocket.send" not in fn, (name, fn)
            assert "stage_control" not in fn, (name, fn)
            # 직접 보내지 않더라도 **전송 함수를 부르면** 같은 왕복이 생긴다.
            # 리터럴 `send` 만 세는 가드는 그 한 줄을 그대로 통과시킨다.
            calls = re.findall(r"\b(send|queue)Stage\w*\(", fn)
            assert not calls, (name, calls, fn)

    def test_the_toggle_sends_immediately_and_the_slider_debounces(self, html: str):
        """토글은 즉시, 슬라이더는 디바운스.

        체크박스는 연속 입력이 아니므로 디바운스가 지연만 더한다. 슬라이더는
        드래그 한 번에 수십 번 발화하므로 디바운스가 필요하다 — 번역 배율이
        이미 그렇게 돼 있다.
        """
        slider = self._fn(html, "queueStageSourceScale")
        assert "setTimeout" in slider and "STAGE_SCALE_SEND_DELAY_MS" in slider, slider

        toggle = self._fn(html, "sendStageShowSource")
        assert "openaiWebSocket.send" in toggle, toggle
        assert "setTimeout" not in toggle, toggle

    def test_the_toggle_sends_a_real_bool(self, html: str):
        """서버가 `bool` 만 받는다 — `checked` 를 그대로 흘리면 거절될 수 있다."""
        handler = re.search(
            r"stageSourceToggle\.onchange = \(e\) => \{(.*?)\n    \};", html, re.S
        )
        assert handler is not None, "토글 핸들러가 없다"
        assert "!!e.target.checked" in handler.group(1), handler.group(1)

    def test_the_panel_pushes_its_state_when_the_server_knows_nothing(self, html: str):
        """세션 시작 전에 켜 둔 토글이 **무대에 전달된다** (#192).

        이것이 없으면 "원문이 안 보인다" 가 된다. 세션 시작 **전에** 토글을
        켜면 소켓이 없어 전송이 조용히 건너뛰어지고 localStorage 에만 남는다.
        세션을 시작해도 서버는 그 값을 모르니 `auth_success` 에서 복원할 것이
        없고 — **패널은 켜진 채인데 무대는 끝까지 꺼져 있다.** 오퍼레이터
        입장에서는 체크박스가 켜져 있는데 화면에 원문이 없는, 설명할 수 없는
        상태다.

        서버가 아는 값이 있으면 그쪽이 우선이다(룸의 현재 상태가 권위). 모를
        때만 패널 값을 밀어 올린다.
        """
        block = re.search(r"case 'auth_success':(.*?)\n      case ", html, re.S)
        assert block is not None, "auth_success 분기를 찾지 못했다"
        body = block.group(1)

        assert "serverKnowsShowSource" in body, body
        assert "serverKnowsSourceScale" in body, body
        # 서버가 아는 경우에는 밀어 올리지 않는다 — 안 그러면 왕복이 된다.
        assert "!serverKnowsShowSource && pendingStageSource.show" in body, body
        assert "!serverKnowsSourceScale && pendingStageSource.scale" in body, body
        assert "sendStageShowSource(!!stageSourceToggle.checked)" in body, body
        # 한 번 흘려보낸 지시는 비운다 — 재인증마다 다시 쏘면 왕복이 된다.
        assert "pendingStageSource.show = false;" in body, body
        assert "pendingStageSource.scale = false;" in body, body

    def test_only_a_real_gesture_is_pending_not_the_local_fallback(self, html: str):
        """밀어 올리는 것은 **이번 페이지의 조작**뿐이다 (#192).

        localStorage 폴백은 "지난번에 이랬다" 일 뿐이다. 그것을 로드만으로 서버에
        쓰면 공유 룸에서 **다른 오퍼레이터의 설정을 덮어쓴다** — 기존 계약
        (`test_a_room_without_control_state_sends_nothing_on_load`)이 막고 있는
        바로 그 동작이다. 조작 핸들러 안에서만 pending 이 서야 둘이 구분된다.
        """
        assert "const pendingStageSource = { show: false, scale: false };" in html

        toggle = re.search(
            r"stageSourceToggle\.onchange = \(e\) => \{(.*?)\n    \};", html, re.S
        )
        assert toggle is not None
        assert (
            "if (!isTranslationSocketOpen()) pendingStageSource.show = true;"
            in toggle.group(1)
        ), toggle.group(1)

        slider = re.search(
            r"stageSourceSlider\.oninput = \(e\) => \{(.*?)\n    \};", html, re.S
        )
        assert slider is not None
        assert (
            "if (!isTranslationSocketOpen()) pendingStageSource.scale = true;"
            in slider.group(1)
        ), slider.group(1)

        # 복원 경로(loadSettings)는 pending 을 세우지 않는다.
        restore = re.search(r"const loadSettings = \(\) => \{(.*?)\n  \};", html, re.S)
        assert restore is not None
        assert "pendingStageSource" not in restore.group(1), restore.group(1)

    def test_the_stage_source_fields_are_restored_from_the_server_snapshot(
        self, html: str
    ):
        """서버가 들고 있는 값이 localStorage 폴백보다 우선한다.

        `show_source` 는 **boolean** 으로 판정해야 한다 — falsy 로 분기하면
        서버가 "꺼짐" 을 들고 있을 때 그 값이 무시되고, 오퍼레이터 패널만 켜진
        채로 무대와 어긋난다.
        """
        assert "typeof data.control.source_scale === 'number'" in html
        assert "typeof data.control.show_source === 'boolean'" in html
        assert (
            "typeof data.show_source === 'boolean'" in html
        ), "ack 경로에 boolean 판정이 없다"


class TestStageScaleSliderMarkup:
    def test_range_bounds_and_step(self, html: str):
        attrs = _input_attrs(html, _SLIDER_ID)
        assert attrs["type"] == "range"
        assert attrs["min"] == "0.8"
        assert attrs["max"] == "1.6"
        assert attrs["step"] == "0.1"
        assert attrs["value"] in ("1", "1.0"), (
            f"default scale is {attrs['value']!r} — the stage renders at 1.0 "
            "until the operator changes it, so any other default silently "
            "resizes every room's captions on first load"
        )
        assert "slider" in attrs.get("class", "").split()

    def test_the_two_stage_sliders_share_one_range(self, html: str):
        """무대용 슬라이더는 **둘**이고, 둘이 같은 범위를 쓴다 (#189).

        처음에는 "0.8–1.6 range 는 정확히 하나" 였다. 무대 원문 배율이 생기며
        둘이 됐는데, 중요한 것은 개수가 아니라 **두 슬라이더가 같은 범위를
        쓴다**는 사실이다 — 범위가 갈라지면 오퍼레이터가 슬라이더마다 끝값을
        따로 외워야 하고, 서버는 `_coerce_caption_scale` 하나로 둘 다 검증하므로
        UI 만 어긋난 채 조용히 거절당한다.

        px 슬라이더(오퍼레이터 자기 화면용) 둘은 대상이 달라 여기 섞이지 않는다.
        """
        ranges = re.findall(r"<input\b[^>]*type=\"range\"[^>]*>", html)
        assert len(ranges) == 4, (
            f"설정 패널의 슬라이더는 넷이어야 한다 (번역 px, 원문 px, 무대 자막 "
            f"배율, 무대 원문 배율) — {len(ranges)}개 발견"
        )

        stage = {}
        for element_id in ("stageScaleSlider", "stageSourceSlider"):
            tag = next((t for t in ranges if f'id="{element_id}"' in t), None)
            assert tag is not None, f"{element_id} range 입력이 없다"
            stage[element_id] = {
                name: re.search(rf'{name}="([^"]+)"', tag).group(1)
                for name in ("min", "max", "step")
            }

        assert stage["stageScaleSlider"] == stage["stageSourceSlider"], (
            f"무대 슬라이더 둘의 범위가 다르다: {stage!r} — 서버는 둘을 같은 "
            "규칙으로 검증한다"
        )
        assert stage["stageScaleSlider"] == {
            "min": "0.8",
            "max": "1.6",
            "step": "0.1",
        }, stage["stageScaleSlider"]

    @pytest.mark.parametrize(
        ("element_id", "expected"),
        [
            ("fontSizeSlider", {"min": "18", "max": "48", "value": "28", "step": "2"}),
            (
                "originalSizeSlider",
                {"min": "12", "max": "24", "value": "16", "step": "1"},
            ),
        ],
    )
    def test_existing_operator_local_sliders_are_unchanged(
        self, html: str, element_id: str, expected: dict[str, str]
    ):
        """오퍼레이터 자기 화면용 슬라이더는 대상이 달라 통합하지 않는다."""
        attrs = _input_attrs(html, element_id)
        for key, value in expected.items():
            assert attrs[key] == value, (
                f"#{element_id}[{key}] changed to {attrs[key]!r} — this slider "
                "controls the operator's own screen in px, not the stage scale. "
                "Merging the two would resize the venue's captions whenever the "
                "operator adjusts their own monitor."
            )

    def test_the_labels_distinguish_the_two_targets(self, html: str):
        """AC — 두 슬라이더가 나란히 있는 혼동은 라벨로 해결한다."""
        assert "번역 글자 크기" in html
        assert "무대 자막 크기" in html

    def test_the_value_readout_uses_a_multiplier_not_px(self, html: str):
        """무대 자막은 `clamp()` 기반이라 px 로 보여 줄 수 없다."""
        assert "×" in html, "the scale readout must be shown as a multiplier"
        assert re.search(r"toFixed\(1\)\}?×", html) or re.search(
            r"toFixed\(1\)[^\n]*×", html
        ), "the readout should render one decimal place, e.g. 1.4×"


# ---------------------------------------------------------------------------
# 접근성 (RL-010)
# ---------------------------------------------------------------------------
class TestStageScaleAccessibility:
    def test_label_is_programmatically_associated(self, html: str):
        match = re.search(
            rf"<label\b[^>]*\bfor=\"{re.escape(_SLIDER_ID)}\"[^>]*>", html
        )
        assert match is not None, (
            f'no <label for="{_SLIDER_ID}"> — a range input with only a '
            "visually adjacent label announces as 'slider' with no name"
        )

    def test_value_change_is_announced_exactly_once(self, html: str):
        """키보드 사용자는 값 변화를 듣되, **한 번만** 듣는다.

        원래 이 테스트는 값 표시 span 의 `aria-live` 를 요구했다. 그건 틀린
        기제다 — range 컨트롤은 값을 접근성 트리로 직접 노출하므로 조작할 때마다
        이미 낭독된다. 거기에 라이브 리전을 얹으면 같은 값이 두 번 읽힌다.
        ISSUE-49 가 심어 둔 `test_no_new_aria_live_owner`(페이지당 announcer 2개)
        가 실제로 이 충돌을 잡아냈다.

        올바른 기제는 슬라이더의 `aria-valuetext` 다: 낭독은 한 번이고 "1.4배"
        라는 형식도 유지된다. span 은 `aria-hidden` 시각 표시로만 남는다.
        """
        span = re.search(rf"<span\b[^>]*\bid=\"{re.escape(_VALUE_ID)}\"[^>]*>", html)
        assert span is not None, f"no <span id={_VALUE_ID!r}>"
        assert "aria-live" not in span.group(0), (
            "the value readout must not be a live region — the range control "
            "already announces its own value, so this double-reads it (RL-019)"
        )
        assert 'aria-hidden="true"' in span.group(0), (
            "the value readout is a visual mirror of the slider value; leaving "
            "it exposed reads the same number twice in one control"
        )

        slider = re.search(
            rf"<input\b[^>]*\bid=\"{re.escape(_SLIDER_ID)}\"[^>]*>", html
        )
        assert slider is not None, f"no <input id={_SLIDER_ID!r}>"
        assert "aria-valuetext=" in slider.group(0), (
            "the slider carries no aria-valuetext — a keyboard user would hear "
            "the bare number and lose the fact that it is a multiplier"
        )
        # 초기 마크업과 JS 갱신이 같은 형식이어야 한다. 한쪽만 고치면 첫 낭독과
        # 이후 낭독의 단위가 달라지고, 그건 화면에는 보이지 않는다.
        assert 'aria-valuetext="1.0배"' in slider.group(0), slider.group(0)
        assert "setAttribute('aria-valuetext'" in html, (
            "aria-valuetext is set once in html but never updated — after the "
            "first drag it would announce a stale multiplier"
        )

    def test_sliders_have_a_visible_focus_ring(self, css: str):
        """WCAG 2.4.7 — `.slider` 는 `outline: none` 이라 링이 따로 필요하다."""
        assert re.search(r"\.slider:focus-visible\s*\{", css), (
            ".slider sets `outline: none` and declares no :focus-visible rule — "
            "a keyboard-only operator cannot see where focus is"
        )
        block = _rule_block(css, ".slider:focus-visible")
        assert (
            "box-shadow" in block or "outline" in block
        ), f".slider:focus-visible declares no visible indicator: {block!r}"


# ---------------------------------------------------------------------------
# 카피 — 언어 변경의 파급 범위 (ISSUE-53 규칙을 오퍼레이터에게 노출)
# ---------------------------------------------------------------------------
class TestOutputLanguageCopy:
    """오퍼레이터가 모르면 정상 동작을 버그로 신고하게 되는 사실 세 가지."""

    def test_states_that_the_choice_moves_the_room_default(self, html: str):
        assert "무대·뷰어 화면의 기본 자막 언어가 함께 바뀝니다." in html

    def test_states_that_attendees_who_chose_a_language_are_not_dragged(
        self, html: str
    ):
        """ISSUE-53 의 규칙. 이게 화면에 없으면 '뒷줄 화면이 안 바뀐다' 가
        버그 리포트로 올라온다 — 사양대로 동작하는데도."""
        assert "직접 언어를 고른 청중 화면은 그대로 유지됩니다." in html

    def test_states_that_a_pre_session_change_applies_on_start(self, html: str):
        """AC — 시작 전 변경은 단정적인 성공 표시를 하지 않는다."""
        assert "세션을 시작하면 그때 반영됩니다." in html

    def test_the_hint_is_a_sibling_of_the_language_warning(self, html: str):
        """카피가 `.lang-warning` 처럼 조건부로 숨겨지면 아무도 못 읽는다."""
        match = re.search(r"<div class=\"control-hint\" id=\"outputLangHint\">", html)
        assert match is not None, "no always-visible hint under the output language"


# ---------------------------------------------------------------------------
# WCAG AA (TC-086) — 배경은 설정 패널의 #0e0e10
# ---------------------------------------------------------------------------
class TestNewControlContrast:
    def test_the_backdrop_constant_matches_the_stylesheet(self, css: str):
        """배경을 잘못 잡는 것이 RL-018 의 정확한 형태다 — 상수를 CSS 와 묶는다."""
        block = _rule_block(css, ".settings-panel")
        match = re.search(r"background:\s*(#[0-9a-fA-F]{6})", block)
        assert match is not None, ".settings-panel has no hex background"
        assert _hex_rgb(match.group(1)) == _SETTINGS_PANEL_BACKDROP, (
            f".settings-panel background moved to {match.group(1)} — the "
            "contrast numbers below were computed against #0e0e10"
        )

    @pytest.mark.parametrize(
        ("selector", "expected_ratio"),
        [
            # 계산값이지 눈대중이 아니다 (`tests/wcag.py` 로 산출).
            (".control-group label", 5.31),  # rgba(255,255,255,0.5) @12px
            (".control-group span", 6.28),  # rgba(255,255,255,0.55) @13px
            (".control-hint", 8.40),  # rgba(255,255,255,0.65) @12px
        ],
    )
    def test_new_control_text_meets_aa_on_the_settings_panel(
        self, css: str, selector: str, expected_ratio: float
    ):
        rgba = _rule_rgba(css, selector)
        ratio = _contrast_ratio(rgba, _SETTINGS_PANEL_BACKDROP)
        assert ratio >= 4.5, (
            f"{selector} renders at {ratio:.2f}:1 on the settings panel "
            f"(#0e0e10) — WCAG AA needs 4.5:1 for body text"
        )
        assert ratio == pytest.approx(expected_ratio, abs=0.01), (
            f"{selector} now computes to {ratio:.2f}:1, not the recorded "
            f"{expected_ratio}:1 — recompute and update the table rather than "
            "loosening the assertion"
        )

    def test_the_ratio_is_actually_sensitive_to_alpha(self):
        """RL-004 — 알파를 0.3 으로 낮추면 이 계산이 **떨어져야** 한다.

        이 가드가 없으면 `_contrast_ratio` 가 상수를 돌려주도록 망가져도 위
        parametrize 가 전부 통과한다.
        """
        faded = _contrast_ratio((255, 255, 255, 0.3), _SETTINGS_PANEL_BACKDROP)
        assert faded < 4.5, (
            f"alpha 0.3 white computes to {faded:.2f}:1 on #0e0e10 — that is "
            "above AA, so the contrast helper is not reading alpha at all"
        )

    def test_no_ancestor_opacity_multiplies_the_new_control(self, css: str):
        """ISSUE-49 의 발견 — `opacity` 는 자식의 알파를 **곱한다**.

        `.fs-font-controls { opacity: 0.6 }` 안의 텍스트는 `color` 만 봐서는
        실제 렌더 값을 알 수 없었다. 설정 패널 조상 중 하나라도 `opacity` 를
        선언하면 위 수치가 전부 거짓말이 되므로, 그 사실을 여기에 고정한다.
        (렌더된 값 확인은 e2e 의 `test_settings_panel_has_no_inherited_opacity`.)
        """
        for selector in (".settings-panel", "body", "html"):
            if not re.search(rf"(?m)^\s*{re.escape(selector)}\s*\{{", css):
                continue
            block = _rule_block(css, selector)
            assert not re.search(r"(?<![-\w])opacity\s*:", block), (
                f"{selector} declares opacity — it multiplies the alpha of every "
                "descendant, so the composited contrast of the new control is "
                "lower than the color rule suggests (ISSUE-49)"
            )


# ---------------------------------------------------------------------------
# #178: 무대 테마 / 발표자료 연결 해제 — 보내는 쪽 계약
# ---------------------------------------------------------------------------
class TestStageThemeAndDisconnectControls:
    """오퍼레이터 패널이 두 컨트롤을 들고 올바른 프레임을 보낸다.

    무대 페이지에는 조작 UI 가 없으므로(NFR-025/NFR-030) 이 패널이 유일한
    조작 지점이다 — F10 은 무대 창이 포커스를 쥐고 있을 때만 도달하는 보조
    경로다.
    """

    def test_the_panel_ships_both_controls(self, html: str):
        for element_id in ("stageThemeDark", "stageThemeLight", "stageDisconnectBtn"):
            assert f'id="{element_id}"' in html, (
                f"{element_id} 가 설정 패널에 없다 — 무대 테마/해제를 조작할 "
                "지점이 사라진다"
            )

    def test_theme_is_sent_as_a_stage_control_frame(self, html: str):
        """자막 배율과 **같은 경로**를 쓴다 — 새 메시지 타입을 만들지 않는다."""
        body = re.search(r"function sendStageTheme\(value\) \{(.*?)\n  \}", html, re.S)
        assert body is not None, "sendStageTheme() not found"
        fn = body.group(1)
        assert "type: 'stage_control'" in fn, fn
        assert "theme: value" in fn, fn
        assert (
            "isTranslationSocketOpen()" in fn
        ), "세션 미시작 상태에서 전송을 시도한다 — RL-006: 조용히 로컬 표시만"

    def test_disconnect_is_sent_as_a_one_shot_command(self, html: str):
        body = re.search(
            r"function sendPresentationDisconnect\(\) \{(.*?)\n  \}", html, re.S
        )
        assert body is not None, "sendPresentationDisconnect() not found"
        fn = body.group(1)
        assert "type: 'stage_control'" in fn, fn
        assert "presentation: 'disconnect'" in fn, fn
        assert "isTranslationSocketOpen()" in fn, fn

    def test_the_server_snapshot_only_updates_the_display(self, html: str):
        """`showStageTheme` 은 표시만 바꾼다 — 전송하면 무한 왕복이 된다.

        서버 스냅샷(auth_success)과 ack 가 이 함수를 쓴다. 여기서 전송하면
        서버가 보낸 값이 곧바로 서버로 되돌아간다 (`showStageScale` 과 같은 규칙).
        """
        body = re.search(r"function showStageTheme\(value\) \{(.*?)\n  \}", html, re.S)
        assert body is not None, "showStageTheme() not found"
        fn = body.group(1)
        assert ".send(" not in fn, f"showStageTheme() 이 전송한다: {fn!r}"

    def test_disconnect_is_disabled_until_the_session_starts(self, html: str):
        """세션 전에는 버튼을 눌러도 아무 일이 없다 — 눌리지 않게 둔다.

        힌트 문구만 바꾸고 버튼을 살려 두면 오퍼레이터가 "눌렀는데 안 된다" 를
        만난다. `updateStageControlHints` 가 소켓 상태로 disabled 를 관리한다.
        """
        body = re.search(
            r"function updateStageControlHints\(\) \{(.*?)\n  \}", html, re.S
        )
        assert body is not None, "updateStageControlHints() not found"
        fn = body.group(1)
        assert "stageDisconnectBtn.disabled = !open" in fn, fn
