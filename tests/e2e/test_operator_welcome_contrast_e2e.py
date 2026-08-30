"""
ISSUE-49 — 오퍼레이터 웰컴 화면 대비 e2e (browser-driven).

정적 짝은 `tests/test_webrtc_stage_launch.py::TestWelcomeStateContrast` 다.
그쪽은 **스타일시트에 적힌 값**을 읽는다. 여기서는 브라우저가 **실제로 칠하는
값**을 읽는다 — RL-018 이 세 번 물린 이유가 정확히 그 차이다:

  * backdrop 을 가정하지 않고 **관측한다**. `getComputedStyle(...)
    .backgroundColor` 로 body 와 전체화면의 `#viewer` 배경을 직접 읽어
    그 위에서 대비를 계산한다. 상수가 틀리면 여기서 먼저 죽는다.
  * 캐스케이드/상속을 통과한 뒤의 색을 본다. `.hint` 안의 `<span>` 은 자기
    color 규칙이 없어 상속으로만 색을 받는다 — 소스 문자열 검사로는 그
    글자가 무슨 색으로 칠해지는지 알 수 없다.
  * 전체화면을 **진짜로 진입해서** 잰다. `#viewer:fullscreen` 은 배경을
    `#000` 으로 덮으므로 이 화면은 두 배경 위에 래스터라이즈된다.

대비 산술은 `tests/wcag.py` 를 그대로 쓴다 (RL-001 — 세 번째 계산기 금지).
"""

from __future__ import annotations

import pathlib
import re
import sys
from unittest.mock import MagicMock

import pytest

# backdrop 상수는 정적 테스트에서 가져온다. 여기서 다시 적으면 두 벌이 되고,
# 두 벌이 갈라지는 것이 RL-018 그 자체다 (정적 테스트가 이 상수를 파일의 실제
# `background` 선언과 대조해 고정한다).
from tests.test_webrtc_stage_launch import _FULLSCREEN_BACKDROP, _PAGE_BACKDROP
from tests.wcag import _contrast_ratio, _hex_rgb

if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()

pytestmark = pytest.mark.e2e

_WEBRTC_TEMPLATE = (
    pathlib.Path(__file__).resolve().parents[2] / "components" / "webrtc.html"
)

# 웰컴 화면에서 실제로 글자가 칠해지는 노드들. `.hint span` 이 목록에 있는
# 것이 핵심이다 — 오퍼레이터가 이 화면에서 해야 할 유일한 행동("우상단
# 설정에서 마이크를 활성화하세요")을 담은 노드인데 자기 color 규칙이 없다.
_WELCOME_TEXT_NODES = (
    ".welcome-state",
    ".welcome-title",
    ".welcome-room",
    ".welcome-desc",
    ".welcome-state .hint",
    ".welcome-state .hint span",
    ".welcome-rules",
)

_CSS_COLOUR = re.compile(
    r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.]+))?\s*\)"
)


def _parse_css_colour(value: str) -> tuple[float, float, float, float]:
    """`rgb(r, g, b)` / `rgba(r, g, b, a)` → RGBA 4-튜플."""
    match = _CSS_COLOUR.search(value)
    assert match is not None, f"unparseable computed colour {value!r}"
    r, g, b, a = match.groups()
    return (float(r), float(g), float(b), 1.0 if a is None else float(a))


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


@pytest.fixture
def welcome_page(page, tmp_path):
    """부트스트랩 payload 를 주입한 webrtc.html 을 file:// 로 연다.

    ISSUE-48 / RL-024 — 치환은 프로덕션 함수를 그대로 부른다. 여기서
    재구현하면 프로덕션이 고쳐져도 이 파일은 옛 경로를 계속 테스트한다.
    """
    from operator_ui import render_component_html

    html = render_component_html(
        _WEBRTC_TEMPLATE.read_text(encoding="utf-8"), _boot_payload()
    )
    target = tmp_path / "webrtc_welcome_contrast.html"
    target.write_text(html, encoding="utf-8")
    page.goto(target.as_uri(), wait_until="load")
    page.wait_for_timeout(400)
    return page


def _observed_colours(page) -> dict[str, tuple[float, float, float, float]]:
    return {
        selector: _parse_css_colour(
            page.evaluate(
                "s => getComputedStyle(document.querySelector(s)).color", selector
            )
        )
        for selector in _WELCOME_TEXT_NODES
    }


def _enter_fullscreen(page) -> tuple[float, float, float, float]:
    """전체화면에 진입하고 **실제로 칠해진** backdrop 을 관측해 돌려준다."""
    page.locator("#fullscreenFab").click()
    page.wait_for_timeout(800)
    # 양성 대조군 — 진입이 실패하면 아래 단언은 그냥 일반 화면을 두 번 재는
    # 공허한 테스트가 된다 (RL-004).
    assert page.evaluate(
        "document.getElementById('viewer').matches(':fullscreen')"
    ), "the viewer never entered fullscreen — the #000 backdrop was never exercised"
    return _parse_css_colour(
        page.evaluate(
            "getComputedStyle(document.getElementById('viewer')).backgroundColor"
        )
    )


class TestWelcomeContrastAsPainted:
    """AC1 — 두 backdrop 모두에서 4.5:1. backdrop 은 가정이 아니라 관측값이다."""

    def test_page_backdrop_is_painted_opaque_as_the_constant_claims(self, welcome_page):
        """대비 산술의 전제를 브라우저에게 먼저 확인받는다.

        반투명 backdrop 위에서는 합성 결과가 그 아래 무엇이 있느냐에 달려
        있어 대비가 결정 불가능해진다 — RL-018 두 번째 사례가 그 형태다.
        """
        painted = _parse_css_colour(
            welcome_page.evaluate("getComputedStyle(document.body).backgroundColor")
        )
        assert painted[3] == 1.0, f"the page backdrop is not opaque: {painted}"
        assert painted[:3] == tuple(
            float(c) for c in _hex_rgb(_PAGE_BACKDROP)
        ), f"body paints {painted} but the contrast constant says {_PAGE_BACKDROP}"

    @pytest.mark.parametrize("selector", _WELCOME_TEXT_NODES)
    def test_welcome_text_meets_aa_on_the_page_backdrop(self, welcome_page, selector):
        backdrop = _hex_rgb(_PAGE_BACKDROP)
        colour = _observed_colours(welcome_page)[selector]
        ratio = _contrast_ratio(colour, backdrop)
        assert ratio >= 4.5, (
            f"{selector} paints {colour} → {ratio:.2f}:1 on {_PAGE_BACKDROP} — "
            "WCAG AA 1.4.3 needs 4.5:1"
        )

    def test_welcome_text_meets_aa_on_the_fullscreen_backdrop(self, welcome_page):
        """전체화면은 배경을 `#000` 으로 덮는다 — 알파 텍스트는 여기서 더 어둡다.

        `.welcome-room` 이 정확히 이 경계에서 떨어졌다: 일반 화면 4.52:1,
        전체화면 4.43:1.
        """
        painted = _enter_fullscreen(welcome_page)
        assert painted[3] == 1.0, f"the fullscreen backdrop is not opaque: {painted}"
        assert painted[:3] == tuple(
            float(c) for c in _hex_rgb(_FULLSCREEN_BACKDROP)
        ), f"#viewer paints {painted} but the constant says {_FULLSCREEN_BACKDROP}"

        backdrop = _hex_rgb(_FULLSCREEN_BACKDROP)
        failures = []
        for selector, colour in _observed_colours(welcome_page).items():
            ratio = _contrast_ratio(colour, backdrop)
            if ratio < 4.5:
                failures.append(f"{selector} {colour} → {ratio:.2f}:1")
        assert not failures, (
            f"{len(failures)} welcome text node(s) below 4.5:1 on "
            f"{_FULLSCREEN_BACKDROP}: {failures}"
        )


class TestWelcomeColourDoesNotBranchOnBackdrop:
    def test_computed_colours_are_identical_in_and_out_of_fullscreen(
        self, welcome_page
    ):
        """AC — 값 하나가 두 backdrop 을 모두 커버한다.

        예전에는 `#viewer:fullscreen .welcome-state` 가 알파를 0.7 → 0.8 로
        올려 더 어두운 배경에서 잃은 대비를 되메웠다. 불투명 hex 로 바꾼
        지금은 되메울 손실이 없고, 분기가 남아 있으면 "한 값이 두 배경을
        모두 커버한다" 는 성질이 조용히 거짓이 된다.

        정적 테스트는 소스에 그 규칙이 없다는 것까지만 말할 수 있다. 그
        규칙이 **없어서 색이 실제로 같게 칠해진다**는 것은 브라우저만 말할 수
        있다 — 여기가 그 자리다.
        """
        before = _observed_colours(welcome_page)
        _enter_fullscreen(welcome_page)
        after = _observed_colours(welcome_page)

        drifted = {
            selector: (before[selector], after[selector])
            for selector in _WELCOME_TEXT_NODES
            if before[selector] != after[selector]
        }
        assert not drifted, (
            "these welcome text colours change when the backdrop changes, so one "
            f"of the two states was never contrast-checked: {drifted}"
        )
