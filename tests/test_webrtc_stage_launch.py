"""
ISSUE-47 — `components/webrtc.html` 무대 화면 열기 컨트롤 정적 검증.

행동 기반 짝은 `tests/e2e/test_operator_stage_mode_e2e.py` (TC-068~TC-070) 다.
여기서는 브라우저를 띄우지 않고도 고정할 수 있는 계약만 단언한다:

  1. WCAG AA 대비 (RL-018) — 새로 추가한 오퍼레이터 컨트롤도 예외가 아니다.
     알파/색을 눈대중으로 고르지 않기 위해 합성 후 수치로 계산한다.
  2. 팝업 차단 오판 가드 — `window.open(url, '_blank', 'noopener')` 는 사양상
     항상 null 을 반환하므로, windowFeatures 에 noopener 가 들어가면 정상적으로
     열린 창까지 차단으로 오판한다. 이건 e2e 로도 잡히지만 원인이 한 단어라
     정적으로도 못박아 둔다.
  3. announcer 단일성 — 페이지당 aria-live 소유자를 늘리지 않는다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.wcag import _contrast_ratio, _hex_rgb, _rule_block, _rule_hex

if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()

_WEBRTC_TEMPLATE = Path(__file__).resolve().parents[1] / "components" / "webrtc.html"

# 무대 컨트롤이 실제로 래스터라이즈되는 배경 — body 의 불투명 배경색이다.
# `.stage-launch` 컨테이너는 자체 배경이 없으므로 이 값이 곧 backdrop 이다.
_PAGE_BACKDROP = "#0b0b0c"


@pytest.fixture(scope="module")
def webrtc_html() -> str:
    assert _WEBRTC_TEMPLATE.exists(), f"webrtc.html missing: {_WEBRTC_TEMPLATE}"
    return _WEBRTC_TEMPLATE.read_text(encoding="utf-8")


class TestStageLaunchContrast:
    """RL-018 — 새 컨트롤도 4.5:1 이상이어야 한다."""

    def test_body_backdrop_is_the_expected_opaque_colour(self, webrtc_html):
        """backdrop 이 바뀌면 아래 대비 계산의 전제가 무너진다 — 먼저 고정한다."""
        assert _rule_hex(webrtc_html, "body", "background") == _hex_rgb(_PAGE_BACKDROP)

    def test_launch_button_text_meets_aa(self, webrtc_html):
        """흰 글자 on 버튼 배경 — 버튼은 자체 배경을 갖는다."""
        fg = _rule_hex(webrtc_html, ".stage-launch-button", "color")
        bg = _rule_hex(webrtc_html, ".stage-launch-button", "background")
        ratio = _contrast_ratio((*fg, 1.0), bg)
        assert ratio >= 4.5, (
            f".stage-launch-button text contrast is {ratio:.2f}:1 against its own "
            f"background {bg} — WCAG AA needs 4.5:1"
        )

    def test_hint_text_meets_aa_against_the_page_backdrop(self, webrtc_html):
        fg = _rule_hex(webrtc_html, ".stage-launch-hint", "color")
        ratio = _contrast_ratio((*fg, 1.0), _hex_rgb(_PAGE_BACKDROP))
        assert ratio >= 4.5, (
            f".stage-launch-hint contrast is {ratio:.2f}:1 against "
            f"{_PAGE_BACKDROP} — WCAG AA needs 4.5:1"
        )

    def test_fallback_link_meets_aa_against_the_page_backdrop(self, webrtc_html):
        """폴백 링크는 팝업이 차단된 오퍼레이터의 유일한 탈출구다 — 반드시 읽혀야."""
        fg = _rule_hex(webrtc_html, ".stage-launch-fallback", "color")
        ratio = _contrast_ratio((*fg, 1.0), _hex_rgb(_PAGE_BACKDROP))
        assert ratio >= 4.5, (
            f".stage-launch-fallback contrast is {ratio:.2f}:1 against "
            f"{_PAGE_BACKDROP} — WCAG AA needs 4.5:1"
        )


class TestPopupOpenContract:
    def test_window_open_never_passes_noopener_in_features(self, webrtc_html):
        """`window.open(u, t, 'noopener')` 는 null 을 반환한다 → 차단 오판.

        opener 차단은 반환된 창에서 `opened.opener = null` 로 처리한다.
        """
        calls = re.findall(r"window\.open\(([^)]*)\)", webrtc_html)
        assert calls, "no window.open call found — the launch action disappeared"
        for args in calls:
            assert "noopener" not in args, (
                f"window.open({args}) passes noopener in windowFeatures — that "
                "forces a null return and shows the popup-blocked fallback even "
                "on a successful open"
            )

    def test_stage_url_opens_in_a_new_top_level_target(self, webrtc_html):
        assert "window.open(stageUrl, '_blank')" in webrtc_html, (
            "the stage URL must open in a new top-level browsing context "
            "(NFR-029) — same-tab navigation would tear down the pipeline"
        )

    def test_opener_reference_is_severed(self, webrtc_html):
        assert (
            "opened.opener = null" in webrtc_html
        ), "the stage window keeps a live opener handle on the operator tab"

    def test_fallback_anchor_carries_rel_noopener(self, webrtc_html):
        """`<a>` 의 rel 은 window.open 과 달리 내비게이션을 막지 않는다."""
        block = re.search(r"<a class=\"stage-launch-fallback\"[^>]*>", webrtc_html)
        assert block is not None, "fallback anchor missing"
        assert 'rel="noopener"' in block.group(0)
        assert 'target="_blank"' in block.group(0)


class TestStageLaunchDoesNotRegressThePage:
    def test_no_new_aria_live_owner(self, webrtc_html):
        """페이지당 announcer 는 늘리지 않는다 — 기존 2개(폰트 크기 표시) 그대로.

        폴백 노출은 aria-live 대신 포커스 이동으로 알린다.
        """
        # 속성만 센다 — 맨 문자열 `aria-live` 는 이 파일의 주석에도 등장하므로
        # 그대로 세면 주석을 고칠 때마다 이 가드가 흔들린다.
        assert webrtc_html.count('aria-live="') == 2, (
            "the stage-launch control added an aria-live region; the popup "
            "fallback announces itself via focus instead"
        )

    def test_control_is_hidden_by_default_in_css(self, webrtc_html):
        """AC1 — 기본 표시 모드(일반 자막)에서 무대 컨트롤은 보이지 않는다."""
        assert "display: none" in _rule_block(webrtc_html, ".stage-launch")
        assert "display: none" in _rule_block(webrtc_html, ".stage-launch-fallback")

    def test_launch_markup_exists_in_both_welcome_render_paths(self, webrtc_html):
        """정적 welcome-state 와 clearViewer 재렌더 양쪽에 있어야 한다.

        clearViewer 가 innerHTML 로 화면을 다시 그리므로, 한쪽에만 있으면
        정지 후 무대 버튼이 사라진다.
        """
        # 마크업만 센다 — `data-*` 는 JS 셀렉터에도 등장하므로 맨 문자열을
        # 세면 마크업이 한쪽에서 사라져도 셀렉터가 개수를 채워 통과한다
        # (RL-004). 클래스 속성은 마크업에만 있다.
        assert webrtc_html.count('class="stage-launch"') == 2
        assert webrtc_html.count('class="stage-launch-button"') == 2
        assert webrtc_html.count('class="stage-launch-fallback"') == 2
        # JS 셀렉터도 실제로 존재하는지 양성 확인.
        assert "querySelectorAll('[data-stage-launch]')" in webrtc_html

    def test_no_pipeline_call_added_to_the_launch_path(self, webrtc_html):
        """무대 버튼은 파이프라인을 건드리지 않는다 (AC2/AC4).

        `applyStageLaunchToWelcome` 본문 안에 세션 제어 호출이 끼어들면
        무대 창을 여는 것이 캡션 세션을 재시작/종료시킬 수 있다.
        """
        start = webrtc_html.index("function applyStageLaunchToWelcome()")
        end = webrtc_html.index("function ", start + 10)
        body = webrtc_html[start:end]
        for forbidden in (
            "closeConnection",
            "connectOpenAIRealtime",
            "getUserMedia",
            "RTCPeerConnection",
            "peerConnection",
            "clearViewer",
        ):
            assert forbidden not in body, (
                f"applyStageLaunchToWelcome touches {forbidden} — opening the "
                "stage window must not affect the caption pipeline"
            )
