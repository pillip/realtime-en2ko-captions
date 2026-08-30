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

ISSUE-49 가 같은 파일의 **웰컴/대기 화면** 대비를 여기에 합류시켰다. 대비 단언을
한 집에 모아 두는 편이 낫다 — 흩어지면 backdrop 상수가 갈라지고, 갈라진 상수가
RL-018 을 세 번 물리게 한 원인이다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.wcag import (
    _contrast_ratio,
    _hex_rgb,
    _relative_luminance,
    _rule_block,
    _rule_hex,
    _rule_rgba,
)

if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()

_WEBRTC_TEMPLATE = Path(__file__).resolve().parents[1] / "components" / "webrtc.html"

# 무대 컨트롤이 실제로 래스터라이즈되는 배경 — body 의 불투명 배경색이다.
# `.stage-launch` 컨테이너는 자체 배경이 없으므로 이 값이 곧 backdrop 이다.
_PAGE_BACKDROP = "#0b0b0c"

# …단, backdrop 은 하나가 아니다. `#viewer:fullscreen` 은 배경을 #000 으로
# 덮으므로 웰컴 화면이 전체화면에서는 다른 색 위에 래스터라이즈된다. 상수
# 하나만 두면 그 상수가 곧 검증되지 않은 가정이 된다 — RL-018 이 두 번 물린
# 자리가 정확히 그것이다. 두 backdrop 을 모두 돌려 가정을 구조적 사실로 만든다.
_FULLSCREEN_BACKDROP = "#000000"
_ALL_BACKDROPS = (_PAGE_BACKDROP, _FULLSCREEN_BACKDROP)


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

    def test_fullscreen_backdrop_is_the_expected_opaque_colour(self, webrtc_html):
        """두 번째 backdrop 도 고정한다 — 웰컴 화면은 전체화면에서도 렌더된다.

        `_rule_hex` 를 쓰지 않는 이유: 이 규칙만 3자리 축약형(`#000`)을 쓰고
        공용 헬퍼는 `#rrggbb` 만 읽는다. 헬퍼를 축약형까지 받도록 넓히면
        stage/viewer 대비 테스트까지 영향을 받으므로(ISSUE-45 를 만든 드리프트)
        여기서 국소적으로 확인한다.
        """
        block = _rule_block(webrtc_html, "#viewer:fullscreen")
        match = re.search(r"(?<![-\w])background:\s*(#[0-9a-fA-F]{3,6})\b", block)
        assert match is not None, block
        digits = match.group(1).lstrip("#")
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        assert _hex_rgb(f"#{digits}") == _hex_rgb(_FULLSCREEN_BACKDROP), (
            "the fullscreen backdrop changed — every contrast figure below "
            "assumes it"
        )

    @pytest.mark.parametrize("backdrop", _ALL_BACKDROPS)
    def test_hint_text_meets_aa_against_every_backdrop(self, webrtc_html, backdrop):
        fg = _rule_hex(webrtc_html, ".stage-launch-hint", "color")
        ratio = _contrast_ratio((*fg, 1.0), _hex_rgb(backdrop))
        assert ratio >= 4.5, (
            f".stage-launch-hint contrast is {ratio:.2f}:1 against "
            f"{backdrop} — WCAG AA needs 4.5:1"
        )

    @pytest.mark.parametrize("backdrop", _ALL_BACKDROPS)
    def test_fallback_link_meets_aa_against_every_backdrop(self, webrtc_html, backdrop):
        """폴백 링크는 팝업이 차단된 오퍼레이터의 유일한 탈출구다 — 반드시 읽혀야."""
        fg = _rule_hex(webrtc_html, ".stage-launch-fallback", "color")
        ratio = _contrast_ratio((*fg, 1.0), _hex_rgb(backdrop))
        assert ratio >= 4.5, (
            f".stage-launch-fallback contrast is {ratio:.2f}:1 against "
            f"{backdrop} — WCAG AA needs 4.5:1"
        )

    @pytest.mark.parametrize("backdrop", _ALL_BACKDROPS)
    def test_launch_button_surface_meets_non_text_contrast(self, webrtc_html, backdrop):
        """WCAG 2.1 SC 1.4.11 — 버튼의 **경계**도 backdrop 대비 3:1 이 필요하다.

        `.stage-launch-button` 은 테두리 색이 배경색과 같으므로 버튼 모양을
        식별시키는 것은 배경색 하나뿐이다. 글자 대비(4.5:1)가 통과해도 이
        값이 3:1 아래로 내려가면 버튼이 캔버스에 녹아 어디를 눌러야 할지
        보이지 않는다.
        """
        surface = _rule_hex(webrtc_html, ".stage-launch-button", "background")
        ratio = _contrast_ratio((*surface, 1.0), _hex_rgb(backdrop))
        assert ratio >= 3.0, (
            f".stage-launch-button surface is {ratio:.2f}:1 against {backdrop} — "
            "SC 1.4.11 needs 3:1 for the component boundary"
        )

    def test_launch_button_hover_text_still_meets_aa(self, webrtc_html):
        """hover 는 별도 배경색을 쓴다 — 기본 상태만 계산하면 놓친다."""
        fg = _rule_hex(webrtc_html, ".stage-launch-button", "color")
        bg = _rule_hex(webrtc_html, ".stage-launch-button:hover", "background")
        ratio = _contrast_ratio((*fg, 1.0), bg)
        assert ratio >= 4.5, (
            f".stage-launch-button:hover text contrast is {ratio:.2f}:1 against "
            f"{bg} — WCAG AA needs 4.5:1"
        )


# ---------------------------------------------------------------------------
# ISSUE-49 — 웰컴/대기 화면
# ---------------------------------------------------------------------------
# 이 화면의 **텍스트** 색 규칙 전부. 두 규칙이 일부러 빠져 있다:
#   - `.welcome-state .icon` 은 SVG 를 칠하는 비텍스트 색이라 1.4.3 이 아니라
#     1.4.11 소관이고, 이 이슈의 Scope Out 이다.
#   - `.qr-code-caption` 은 `.qr-code-container { background: #ffffff }` 흰 카드
#     위에 래스터라이즈된다 — 아래 두 backdrop 중 어느 쪽도 그 텍스트의
#     backdrop 이 아니므로 같은 표에 넣으면 계산 자체가 틀린다. 별도 후속.
_WELCOME_TEXT_RULES = (
    ".welcome-state",
    ".welcome-state h1, .welcome-title",
    ".welcome-room",
    ".welcome-state p, .welcome-desc",
    ".welcome-state .hint",
    ".welcome-rules",
)

# 위계 단언용 — AC5 가 요구하는 서열의 세 단계.
_WELCOME_TITLE_RULE = ".welcome-state h1, .welcome-title"
_WELCOME_DESC_RULE = ".welcome-state p, .welcome-desc"
_WELCOME_SUPPORT_RULES = (".welcome-state .hint", ".welcome-rules")

_ALPHA_WHITE_COLOUR = re.compile(
    r"(?<![-\w])color:\s*rgba\(\s*255\s*,\s*255\s*,\s*255\s*,"
)

# `:fullscreen` / `:-webkit-full-screen` 로 시작하는 자손 규칙 전체.
# 선택자와 본문을 따로 잡아 "웰컴 선택자에 color 를 다시 선언하는가" 를 본다.
_FULLSCREEN_RULE = re.compile(
    r"(?::fullscreen|:-webkit-full-screen)([^{}]*)\{([^{}]*)\}"
)


def _welcome_text_colour(css: str, selector: str) -> tuple[float, float, float, float]:
    """웰컴 텍스트 규칙의 `color` 를 RGBA 4-튜플로 읽는다.

    hex 와 rgba 를 **둘 다** 읽는 것이 핵심이다. hex 만 읽으면 `.hint` 를
    `rgba(255,255,255,0.4)` 로 되돌린 변이가 "선언이 없다" 로 죽어서 정작
    문제인 `3.80:1` 이라는 수치를 한 번도 말하지 못한다 — 실패 메시지가 값을
    말하지 않으면 다음 사람은 값을 다시 눈대중한다 (RL-004 / RL-018).
    """
    block = _rule_block(css, selector)
    if re.search(r"(?<![-\w])color:\s*#", block):
        return (*_rule_hex(css, selector, "color"), 1.0)
    return _rule_rgba(css, selector)


class TestWelcomeStateContrast:
    """RL-018 (Frequency 3) — 웰컴 화면 텍스트도 두 backdrop 모두에서 AA.

    backdrop 상수 자체는 위 `TestStageLaunchContrast` 의
    `test_body_backdrop_is_the_expected_opaque_colour` /
    `test_fullscreen_backdrop_is_the_expected_opaque_colour` 가 파일의 실제
    `background` 선언과 대조해 고정한다. 여기서 상수를 다시 정의하지 않는
    이유가 그것이다 — 두 벌이 되는 순간 한쪽이 검증되지 않은 가정으로 되돌아간다.
    """

    @pytest.mark.parametrize("selector", _WELCOME_TEXT_RULES)
    @pytest.mark.parametrize("backdrop", _ALL_BACKDROPS)
    def test_welcome_text_meets_aa_against_every_backdrop(
        self, webrtc_html, selector, backdrop
    ):
        """AC1 — large-text(3:1) 완화를 근거로 통과시키지 않는다.

        `.welcome-title` 은 `clamp(26px, 3.4vw, 34px)` 라 큰 텍스트로 **분류될
        수는** 있지만, `clamp()` 의 하한이 실제 뷰포트 폭에서 임계 아래로
        내려갈 수 있다는 걸 ISSUE-45 가 확인했다. 완화가 조용히 적용되지 않는
        경우가 있는 값에 완화를 근거로 삼지 않는다 — 전부 4.5 로 잰다.
        """
        colour = _welcome_text_colour(webrtc_html, selector)
        ratio = _contrast_ratio(colour, _hex_rgb(backdrop))
        assert ratio >= 4.5, (
            f"{selector} contrast is {ratio:.2f}:1 against {backdrop} "
            f"(declared colour {colour}) — WCAG AA 1.4.3 needs 4.5:1"
        )

    @pytest.mark.parametrize("selector", _WELCOME_TEXT_RULES)
    def test_welcome_text_declares_an_opaque_hex_colour(self, webrtc_html, selector):
        """AC4 — 전부 `#rrggbb`.

        불투명이면 backdrop 이 어두워질수록 대비가 **올라간다**. 알파는 반대로
        내려간다(0.45 → `#0b0b0c` 4.52 / `#000` 4.43). 두 backdrop 이 갈라지지
        않는 성질을 값의 형태로 못박는다.
        """
        block = _rule_block(webrtc_html, selector)
        match = re.search(r"(?<![-\w])color:\s*([^;\n]+)", block)
        assert match is not None, f"{selector} declares no color at all"
        declared = match.group(1).strip()
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", declared), (
            f"{selector} declares `color: {declared}` — the welcome block must "
            "use opaque #rrggbb so the two backdrops cannot diverge"
        )

    def test_no_alpha_white_text_colour_remains_in_the_welcome_block(self, webrtc_html):
        """AC4 — `rgba(255, 255, 255, α)` 텍스트 색이 정확히 0건.

        `rgba(0, 0, 0, α)` 는 일부러 걸리지 않는다: `.qr-code-caption` 은 흰
        카드 위에 있어 backdrop 이 다르고 이 이슈의 Scope Out 이다.
        """
        offenders = {
            selector: _ALPHA_WHITE_COLOUR.findall(_rule_block(webrtc_html, selector))
            for selector in _WELCOME_TEXT_RULES
        }
        total = sum(len(hits) for hits in offenders.values())
        assert total == 0, (
            f"{total} alpha-white text colour(s) remain in the welcome block: "
            f"{[s for s, hits in offenders.items() if hits]}"
        )

    def test_fullscreen_rules_do_not_re_declare_welcome_text_colour(self, webrtc_html):
        """AC — backdrop 분기는 되살아나지 못한다.

        원래 `#viewer:fullscreen .welcome-state { color: rgba(255,255,255,0.8) }`
        가 있었다. 그건 알파가 더 어두운 backdrop 에서 잃은 대비를 되메우려는
        보정이었고, 불투명 hex 로 바꾼 지금은 되메울 손실 자체가 없다. 분기가
        다시 생기면 "한 값이 두 backdrop 을 모두 커버한다" 는 성질이 조용히
        무너지므로, 주석이 아니라 검사된 사실로 둔다 (RL-018 Prevention).
        """
        # 주석은 걷어내고 **선언만** 본다. 지운 규칙을 설명하려면 그 규칙을
        # 인용할 수밖에 없는데, 인용문에 걸리면 다음 사람은 가드를 고치는 대신
        # 설명을 지운다 (`test_no_pipeline_call_added_to_the_launch_path` 와
        # 같은 이유). 주석은 아무것도 칠하지 않으므로 스캔 대상이 아니다.
        rules = _FULLSCREEN_RULE.findall(
            re.sub(r"/\*.*?\*/", "", webrtc_html, flags=re.S)
        )
        # 양성 대조군 — 스캐너가 실제로 전체화면 블록을 보고 있음을 먼저 증명한다.
        # 이게 없으면 정규식이 깨져 0건이 되어도 아래 단언이 조용히 통과한다.
        assert any(".caption-container" in sel for sel, _ in rules), (
            f"the fullscreen rule scanner found {len(rules)} rule(s) but none for "
            ".caption-container — the pattern broke, so the guard below is vacuous"
        )
        for selector, body in rules:
            if "welcome" not in selector:
                continue
            assert not re.search(r"(?<![-\w])color:", body), (
                f"a fullscreen rule re-declares text colour for `{selector.strip()}`: "
                f"{{{body.strip()}}} — one opaque value must serve both backdrops"
            )

    def test_welcome_brightness_hierarchy_is_strictly_descending(self, webrtc_html):
        """AC5 — 제목 > 설명 > 보조 텍스트의 밝기 서열이 유지된다.

        대비만 올리면 전부 흰색으로 수렴해 화면의 위계가 사라진다. 접근성
        보정이 디자인을 평평하게 만들지 않았다는 것도 수치로 고정한다.
        """
        luminance = {
            selector: _relative_luminance(
                _welcome_text_colour(webrtc_html, selector)[:3]
            )
            for selector in (
                _WELCOME_TITLE_RULE,
                _WELCOME_DESC_RULE,
                *_WELCOME_SUPPORT_RULES,
            )
        }
        title = luminance[_WELCOME_TITLE_RULE]
        desc = luminance[_WELCOME_DESC_RULE]
        assert title > desc, (
            f"the title ({title:.4f}) is not brighter than the description "
            f"({desc:.4f}) — the welcome hierarchy inverted"
        )
        for selector in _WELCOME_SUPPORT_RULES:
            support = luminance[selector]
            assert desc > support, (
                f"the description ({desc:.4f}) is not brighter than {selector} "
                f"({support:.4f}) — supporting text must stay subordinate"
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
        assert "window.open(stageUrl, '_blank'" in webrtc_html, (
            "the stage URL must open in a new top-level browsing context "
            "(NFR-029) — same-tab navigation would tear down the pipeline"
        )

    def test_open_requests_a_real_window_not_a_tab(self, webrtc_html):
        """NFR-029 — 프로젝터로 옮길 수 있어야 하므로 탭이 아니라 창이어야 한다.

        windowFeatures 를 비워 두면 크롬/엣지/파이어폭스는 새 **탭**을 연다.
        그러면 오퍼레이터가 탭을 창으로 뜯어내는 수고가 남는데, 그건 이
        이슈가 없애려던 바로 그 수고다. 힌트 문구도 "새 창으로 열립니다"라
        약속하고 있으므로 동작과 카피가 어긋나면 안 된다.
        """
        match = re.search(
            r"window\.open\(stageUrl, '_blank'(?:,\s*'([^']*)')?\)", webrtc_html
        )
        assert match is not None, "stage window.open call not found"
        features = match.group(1) or ""
        assert "popup" in features, (
            "window.open passes no windowFeatures, so browsers open a TAB — "
            "NFR-029 needs a separate window that can move to the projector"
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

    def test_exactly_one_launch_block_exists(self, webrtc_html):
        """정확히 하나. 복제본은 곧 드리프트다 (RL-001).

        원래 구현은 정적 welcome-state 와 `clearViewer()` 템플릿 양쪽에 같은
        마크업을 복사해 두 벌 갖고 있었다. 컨트롤을 파괴되지 않는 오버레이
        층으로 옮기면서 복제 자체가 필요 없어졌다 — 두 벌이 갈라질 방법이
        아예 사라지는 편이 두 벌을 비교하는 테스트보다 낫다.
        """
        assert webrtc_html.count('class="stage-launch"') == 1
        assert webrtc_html.count('class="stage-launch-button"') == 1
        assert webrtc_html.count('class="stage-launch-fallback"') == 1
        # JS 셀렉터도 실제로 존재하는지 양성 확인.
        assert "querySelectorAll('[data-stage-launch]')" in webrtc_html

    def test_launch_control_lives_outside_the_caption_container(self, webrtc_html):
        """**이 테스트가 F-1/H-1 의 회귀 가드다.**

        `.stage-launch` 가 `#captionContainer` 안에 있으면, 첫 자막이 도착할 때
        `appendLine()` 이 `.welcome-state` 를 `remove()` 하면서 무대 버튼까지
        함께 지운다. 그러면 발표가 시작된 뒤에는 버튼이 존재하지 않고, 되살릴
        방법은 정지 → 시작뿐 — AC4 가 명시적으로 금지한 룸 재시작이다.

        코드 리뷰와 UI 리뷰가 **독립적으로** 같은 결함을 찾았다.
        행동 기반 짝: `test_button_survives_the_first_caption` (e2e).
        """
        start = webrtc_html.index(
            '<div class="caption-container" id="captionContainer">'
        )
        end = webrtc_html.index("<script>", start)
        container_region = webrtc_html[start:end]
        assert "stage-launch" not in container_region, (
            "the stage-launch control is inside #captionContainer — appendLine() "
            "removes .welcome-state on the first caption and would take the "
            "button with it (AC4)"
        )

    def test_launch_button_hint_is_wired_for_screen_readers(self, webrtc_html):
        """버튼만 읽히면 '새 창이 열린다' 는 사실이 전달되지 않는다.

        `aria-describedby` 가 없으면 스크린리더 사용자는 "무대 화면 열기,
        버튼" 만 듣는다 — 프로젝터로 옮기라는 안내도, 세션이 유지된다는
        안심도 전달되지 않는다.
        """
        assert 'aria-describedby="stage-launch-hint"' in webrtc_html
        assert 'id="stage-launch-hint"' in webrtc_html

    def test_launch_button_keeps_a_visible_focus_ring(self, webrtc_html):
        """RL-010 / RL-026 — `outline: none` 을 썼으면 대체 링을 반드시 남긴다.

        `.stage-launch-button:focus-visible` 은 UA 기본 아웃라인을 끄고
        box-shadow 로 링을 그린다. box-shadow 선언 한 줄만 사라져도 남는 것은
        `outline: none` 뿐이라 **포커스 표시가 통째로 없어진다** — 키보드
        오퍼레이터는 어디에 있는지 알 수 없게 된다. 이건 CSS 선언이라 어떤
        동작 테스트에도 잡히지 않는다(RL-026: 선언적 속성은 두 테스트 스타일이
        구조적으로 모두 놓치는 클래스).

        `tests/test_stage_page.py::test_capture_button_keeps_a_focus_visible_ring`
        와 같은 계약이고, 이 파일이 그 관례를 따르지 않고 있었다.
        """
        ring = _rule_block(webrtc_html, ".stage-launch-button:focus-visible")
        assert "outline:" in ring, ring
        if "outline: none" in ring:
            assert "box-shadow:" in ring, (
                ".stage-launch-button:focus-visible removes the UA outline "
                f"without providing a replacement ring: {ring}"
            )

    def test_focus_ring_is_distinguishable_from_the_backdrop(self, webrtc_html):
        """SC 1.4.11 — 링이 있어도 캔버스에 묻히면 없는 것과 같다.

        링은 box-shadow 라 버튼 **바깥**에 그려진다 → 인접색은 페이지 배경이다.
        반투명이므로 합성 후의 색으로 계산한다 (RL-018).
        """
        ring = _rule_block(webrtc_html, ".stage-launch-button:focus-visible")
        match = re.search(
            r"box-shadow:[^;]*rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)", ring
        )
        assert match is not None, f"no rgba() focus ring colour found: {ring}"
        r, g, b, a = match.groups()
        for backdrop in _ALL_BACKDROPS:
            ratio = _contrast_ratio(
                (int(r), int(g), int(b), float(a)), _hex_rgb(backdrop)
            )
            assert ratio >= 3.0, (
                f"the focus ring composites to {ratio:.2f}:1 against {backdrop} — "
                "SC 1.4.11 needs 3:1 for a focus indicator"
            )

    def test_no_pipeline_call_added_to_the_launch_path(self, webrtc_html):
        """무대 버튼은 파이프라인을 건드리지 않는다 (AC2/AC4).

        `applyStageLaunchToWelcome` 본문 안에 세션 제어 호출이 끼어들면
        무대 창을 여는 것이 캡션 세션을 재시작/종료시킬 수 있다.
        """
        # 경계를 최상위 함수 선언(2칸 들여쓰기)으로 잡는다. 단순히 다음
        # "function " 을 찾으면 화살표 함수가 섞인 본문에서 경계가 밀려
        # 이웃 함수까지 슬라이스에 들어온다 (거짓 양성).
        start = webrtc_html.index("  function applyStageLaunchToWelcome()")
        end = webrtc_html.index("\n  function ", start + 10)
        body = webrtc_html[start:end]
        assert (
            "applyStageLaunchToWelcome" in body and len(body) < 4000
        ), f"function-body slice looks wrong ({len(body)} chars)"
        # 주석은 걷어내고 **코드만** 본다. 설명문에 함수 이름을 언급했다는
        # 이유로 걸리면, 다음 사람은 가드를 고치는 대신 주석을 지우게 된다.
        body = re.sub(r"//[^\n]*", "", body)
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
