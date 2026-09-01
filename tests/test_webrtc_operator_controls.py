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
import subprocess
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
class TestTemplateOwnershipBoundary:
    """ISSUE-53 소유 파일을 이 브랜치가 건드리지 않았다.

    diff 를 **git 에게 직접 묻는다**. "control 리스너가 존재한다" 같은 내용
    단언으로는 그 파일에 한 줄을 더한 것을 잡을 수 없다 — 더한 뒤에도 리스너는
    존재하기 때문이다 (RL-004).
    """

    @staticmethod
    def _diff_stat(path: str) -> str:
        merge_base = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if merge_base.returncode != 0:
            pytest.skip("origin/main is unavailable — cannot compute the diff base")
        return subprocess.run(
            ["git", "diff", "--numstat", merge_base.stdout.strip(), "--", path],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    @pytest.mark.parametrize(
        "path", ["components/stage.html", "components/viewer.html"]
    )
    def test_issue_53_owned_templates_are_untouched(self, path: str):
        stat = self._diff_stat(path)
        assert stat == "", (
            f"{path} was modified on this branch ({stat!r}) — ISSUE-53 owns the "
            "stage/viewer control handling. A second owner for the same "
            "behaviour is how the publish channel and the render channel drifted "
            "apart in the first place."
        )

    def test_the_diff_probe_can_actually_see_a_change(self):
        """RL-004 게이트 — 위 단언이 공허하지 않다는 양성 신호.

        `_diff_stat` 이 (경로 오타든 git 호출 실패든) 항상 빈 문자열을 돌려
        준다면 위 두 테스트는 아무것도 검증하지 않는다. 이 브랜치가 확실히
        바꾼 파일 하나를 같은 함수로 물어 **비어 있지 않음**을 확인한다.
        """
        stat = self._diff_stat("components/webrtc.html")
        assert stat != "", (
            "the diff probe reported no change to components/webrtc.html, which "
            "this issue definitely modifies — the ownership guard above is "
            "therefore vacuous"
        )


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

    def test_exactly_one_new_range_input_was_added(self, html: str):
        """이 범위/스텝을 가진 range 입력은 정확히 하나다."""
        ranges = re.findall(r"<input\b[^>]*type=\"range\"[^>]*>", html)
        matching = [
            tag
            for tag in ranges
            if 'min="0.8"' in tag and 'max="1.6"' in tag and 'step="0.1"' in tag
        ]
        assert len(matching) == 1, (
            f"expected exactly one 0.8–1.6 range input, found {len(matching)}: "
            f"{matching!r}"
        )
        assert len(ranges) == 3, (
            f"the settings panel should hold exactly three sliders (translation "
            f"px, original px, stage scale), found {len(ranges)}"
        )

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
            "aria-valuetext is set once in markup but never updated — after the "
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
