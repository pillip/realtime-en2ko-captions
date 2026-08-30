"""
ISSUE-47 — 오퍼레이터 무대 모드 e2e (browser-driven).

`components/webrtc.html` 을 **Streamlit 세션 없이 직접** 로드해 "무대 화면 열기"
클릭 경로를 검증한다 (docs/test_plan.md TC-068 ~ TC-070). Streamlit 을 띄우지
않는 이유는 두 가지다:

  1. 검증 대상이 컴포넌트 안의 클릭 핸들러이지 Streamlit 위젯이 아니다.
  2. 실제 두 번째 브라우저 창을 띄우지 않기 위해 `window.open` 을 스텁으로
     갈아끼워야 하는데, `page.add_init_script` 는 문서가 로드되기 전에
     주입되어야 한다.

핵심 단언은 **동기 클릭 경로**다. `window.open` 이 클릭 이벤트의 동기 실행
구간을 벗어나면 대부분의 브라우저가 팝업으로 간주해 차단한다.
"""

from __future__ import annotations

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

_STAGE_URL = "http://localhost:8766/stage/room-42"

# `window.open` 을 호출 인자까지 기록하는 스텁으로 교체한다. 반환값은 테스트가
# `__stageOpenReturnsNull` 로 제어한다 — 팝업 차단 시뮬레이션.
_OPEN_STUB = """
window.__openCalls = [];
window.__stageOpenReturnsNull = %s;
window.open = function (url, target, features) {
  window.__openCalls.push({
    url: url,
    target: target === undefined ? null : target,
    features: features === undefined ? null : features,
  });
  if (window.__stageOpenReturnsNull) return null;
  return { closed: false, focus: function () {}, opener: null };
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


@pytest.fixture
def load_webrtc(page, tmp_path):
    """부트스트랩 payload 를 주입한 webrtc.html 을 file:// 로 연다."""

    def _load(boot: dict, *, open_returns_null: bool = False):
        # ISSUE-48 / RL-024: 치환을 fixture 가 재구현하면 프로덕션이 고쳐져도
        # 이 파일은 옛 경로를 계속 테스트한다. 프로덕션 함수를 그대로 부른다.
        from operator_ui import render_component_html

        html = render_component_html(_WEBRTC_TEMPLATE.read_text(encoding="utf-8"), boot)
        target = tmp_path / "webrtc_under_test.html"
        target.write_text(html, encoding="utf-8")
        page.add_init_script(_OPEN_STUB % ("true" if open_returns_null else "false"))
        page.goto(target.as_uri(), wait_until="load")
        page.wait_for_timeout(600)
        return page

    return _load


class TestStageLaunchButton:
    def test_click_opens_stage_url_in_a_new_top_level_window(self, load_webrtc):
        """TC-068 / AC3 — 정확한 URL 로 `window.open` 이 **한 번** 호출된다."""
        page = load_webrtc(_boot_payload(display_mode="stage", stage_url=_STAGE_URL))
        button = page.locator("[data-stage-open]")
        button.wait_for(state="visible", timeout=5000)
        button.click()
        page.wait_for_timeout(300)

        calls = page.evaluate("window.__openCalls")
        assert len(calls) == 1, f"expected exactly one window.open call, got {calls!r}"
        assert calls[0]["url"] == _STAGE_URL
        assert calls[0]["target"] == "_blank"
        # `window.open(url, '_blank', 'noopener')` 는 사양상 **null 을 반환**한다.
        # 그러면 팝업이 정상으로 열렸는데도 차단 폴백이 뜬다. features 에
        # noopener 를 넣지 않는 것이 이 구현의 계약이다.
        features = calls[0]["features"]
        assert features is None or "noopener" not in str(features), (
            "window.open must not pass 'noopener' in windowFeatures — it forces "
            "a null return and would trigger the popup-blocked fallback on a "
            "perfectly successful open"
        )

    def test_stage_url_is_not_navigated_in_the_same_tab(self, load_webrtc):
        """AC3 — 같은 탭 내비게이션이면 파이프라인이 죽는다."""
        page = load_webrtc(_boot_payload(display_mode="stage", stage_url=_STAGE_URL))
        before = page.url
        page.locator("[data-stage-open]").click()
        page.wait_for_timeout(500)
        assert page.url == before, (
            "clicking 무대 화면 열기 navigated the operator tab — that would "
            "tear down the caption pipeline (AC4)"
        )

    def test_stage_url_is_not_embedded_as_an_iframe(self, load_webrtc):
        """AC3 / NFR-029 — 임베드는 기술적으로 가능하지만 제품상 거부된 선택지다."""
        page = load_webrtc(_boot_payload(display_mode="stage", stage_url=_STAGE_URL))
        page.locator("[data-stage-open]").click()
        page.wait_for_timeout(500)
        srcs = page.evaluate(
            "Array.from(document.querySelectorAll('iframe'))"
            ".map(f => f.getAttribute('src') || '')"
        )
        assert not any("/stage/" in s for s in srcs), (
            f"stage URL was embedded in an iframe: {srcs!r} — NFR-029 requires a "
            "top-level browsing context so it can move to the projector display"
        )

    def test_pipeline_is_untouched_by_opening_the_stage_window(self, load_webrtc):
        """AC4 — 무대 창을 여는 것은 파이프라인 상태를 건드리지 않는다."""
        page = load_webrtc(_boot_payload(display_mode="stage", stage_url=_STAGE_URL))
        page.locator("[data-stage-open]").click()
        page.wait_for_timeout(300)
        # 무대 창을 닫는 상황을 흉내내도 오퍼레이터 문서는 그대로 살아 있다.
        page.evaluate("window.__openCalls.length")
        assert page.evaluate("document.readyState") == "complete"
        # 버튼은 다시 누를 수 있어야 한다 (재접속에 룸 재시작이 필요 없다).
        page.locator("[data-stage-open]").click()
        page.wait_for_timeout(300)
        assert len(page.evaluate("window.__openCalls")) == 2

    def test_button_survives_the_first_caption(self, load_webrtc):
        """AC4 — 자막이 흐르기 시작해도 무대 버튼은 계속 눌러야 한다.

        `appendLine()` 은 첫 자막이 들어오는 순간 `.welcome-state` 를 통째로
        `remove()` 한다. 무대 컨트롤이 그 안에 있으면 발표자가 한 문장만
        말해도 버튼이 DOM 에서 사라지고, 되살리는 유일한 방법이 정지 →
        시작 — AC4 가 명시적으로 금지한 "룸 재시작"이다.

        코드 리뷰 F-1. 경계 뮤테이션 19건 배치가 이걸 놓친 이유는 결함이
        `appendLine()` 안에 있어서다 — 무대 런치 경로만 변이시킨 배치는
        건드릴 이유가 없는 함수였다.
        """
        page = load_webrtc(_boot_payload(display_mode="stage", stage_url=_STAGE_URL))
        assert page.locator("[data-stage-open]").count() == 1

        # 자막 한 줄이 도착한 상황을 실제 렌더 경로로 재현한다.
        page.evaluate("appendLine('첫 자막입니다', 'stable')")
        page.wait_for_timeout(200)

        assert page.locator(".caption-line").count() >= 1, "자막이 실제로 렌더되지 않음"
        assert page.locator("[data-stage-open]").count() == 1, (
            "the 무대 화면 열기 button was destroyed by the first caption — "
            "AC4 requires it to stay available without restarting the room"
        )
        # 사라지지 않았을 뿐 아니라 실제로 눌려야 한다.
        page.locator("[data-stage-open]").click()
        page.wait_for_timeout(200)
        assert len(page.evaluate("window.__openCalls")) == 1

    def test_rebinding_does_not_open_two_windows(self, load_webrtc):
        """한 번의 클릭은 창 하나다 — 두 번 바인딩되어도.

        컨트롤이 지속 노드가 된 뒤로 `applyStageLaunchToWelcome()` 이 두 번
        불리면 같은 버튼에 리스너가 **누적**된다. 예전에는 `clearViewer()` 가
        노드를 통째로 갈아치워서 우연히 안전했을 뿐이라, 이제는 명시적
        가드(`dataset.stageBound`)가 그 역할을 한다.

        이 테스트가 없으면 가드를 지워도 아무도 죽지 않는다(재검증 뮤턴트 N5
        생존). 미래의 리팩터가 호출부를 하나 더 만드는 순간 클릭 한 번에 무대
        창이 두 개 열리는데, 그때 잡아야 할 그물이 여기다.
        """
        page = load_webrtc(_boot_payload(display_mode="stage", stage_url=_STAGE_URL))
        # 호출부가 하나 더 생긴 상황을 그대로 재현한다.
        page.evaluate("applyStageLaunchToWelcome()")
        page.evaluate("applyStageLaunchToWelcome()")
        page.wait_for_timeout(200)

        page.locator("[data-stage-open]").click()
        page.wait_for_timeout(300)
        calls = page.evaluate("window.__openCalls")
        assert len(calls) == 1, (
            f"one click opened {len(calls)} windows — the click listener was "
            f"bound more than once: {calls!r}"
        )


class TestPopupBlockedFallback:
    def test_fallback_link_appears_with_the_same_url(self, load_webrtc):
        """TC-069 / AC7 — 차단되면 같은 자리에 클릭 가능한 링크가 나온다."""
        page = load_webrtc(
            _boot_payload(display_mode="stage", stage_url=_STAGE_URL),
            open_returns_null=True,
        )
        page.locator("[data-stage-open]").click()
        page.wait_for_timeout(400)

        fallback = page.locator("[data-stage-fallback]")
        fallback.wait_for(state="visible", timeout=5000)
        assert fallback.get_attribute("href") == _STAGE_URL
        assert fallback.get_attribute("target") == "_blank"
        rel = fallback.get_attribute("rel") or ""
        assert "noopener" in rel, (
            "the fallback anchor must carry rel=noopener — unlike window.open's "
            "windowFeatures, rel on an <a> does not suppress the navigation"
        )

    def test_fallback_leaks_no_internal_error_text(self, load_webrtc):
        """RL-006 — 내부 예외 문자열/스택이 오퍼레이터 화면에 뜨면 안 된다."""
        page = load_webrtc(
            _boot_payload(display_mode="stage", stage_url=_STAGE_URL),
            open_returns_null=True,
        )
        page.locator("[data-stage-open]").click()
        page.wait_for_timeout(400)

        # RL-004 게이트: 이게 없으면 폴백이 아예 렌더되지 않았을 때도 누출
        # 스캔이 공허하게 통과한다 (빈 화면은 아무것도 누출하지 않는다).
        assert (
            page.locator("[data-stage-fallback]:visible").count() == 1
        ), "the popup-blocked fallback never appeared — the leak scan would be vacuous"

        body_text = page.locator("body").inner_text()
        for leaked in (
            "Traceback",
            "TypeError",
            "ReferenceError",
            "undefined",
            "null",
            "Error:",
            "Exception",
            ".html:",
        ):
            assert leaked not in body_text, (
                f"internal detail {leaked!r} surfaced to the operator (RL-006): "
                f"{body_text!r}"
            )

    def test_fallback_is_absent_until_a_block_actually_happens(self, load_webrtc):
        """차단되지 않았는데 폴백 링크가 보이면 오퍼레이터가 창을 두 번 연다."""
        page = load_webrtc(_boot_payload(display_mode="stage", stage_url=_STAGE_URL))
        assert page.locator("[data-stage-fallback]:visible").count() == 0
        page.locator("[data-stage-open]").click()
        page.wait_for_timeout(400)
        assert (
            page.locator("[data-stage-fallback]:visible").count() == 0
        ), "window.open succeeded but the popup-blocked fallback was shown"


class TestCaptionModeIsUnregressed:
    def test_default_mode_hides_the_stage_control(self, load_webrtc):
        """TC-070 / AC1 — 기본값 `일반 자막` 에서는 무대 컨트롤이 없다."""
        page = load_webrtc(_boot_payload())
        assert page.locator("[data-stage-open]:visible").count() == 0
        assert page.locator("[data-stage-launch]:visible").count() == 0

    def test_bootstrap_without_the_new_keys_is_safe(self, load_webrtc):
        """하위호환 — 예전 payload 모양이어도 컴포넌트가 죽지 않는다."""
        boot = _boot_payload()
        boot.pop("display_mode")
        boot.pop("stage_url")
        page = load_webrtc(boot)
        assert page.locator("[data-stage-open]:visible").count() == 0
        # 웰컴 화면이 정상 렌더된 것을 양성 신호로 확인 (RL-004).
        assert page.locator(".welcome-title").count() >= 1

    def test_caption_mode_with_a_stage_url_still_hides_the_control(self, load_webrtc):
        """AC1 — `stage_url` 이 있어도 모드가 caption 이면 버튼은 없다.

        이 조합이 **실제 앱의 기본 상태**다: `app.py` 의 `_build_stage_url` 은
        룸이 선택되면 표시 모드와 무관하게 URL 을 만들어 payload 에 싣는다.
        따라서 `display_mode` 검사가 무력화되면 일반 자막 모드에서도 무대
        버튼이 튀어나온다 — AC1 이 말하는 바로 그 회귀다.

        (경계 뮤테이션 M10 이 이 구멍을 찾았다: `isStageMode = true` 로 바꿔도
        stage_url 이 없는 기존 테스트들만으로는 아무도 죽지 않았다.)
        """
        page = load_webrtc(_boot_payload(display_mode="caption", stage_url=_STAGE_URL))
        assert page.locator("[data-stage-open]:visible").count() == 0, (
            "the 무대 화면 열기 button appeared in 일반 자막 mode — the "
            "display_mode check is not being honoured (AC1)"
        )
        assert page.locator("[data-stage-launch]:visible").count() == 0

    def test_stage_mode_without_a_room_hides_the_control(self, load_webrtc):
        """룸 미선택이면 stage_url 이 없다 — 버튼을 노출하면 죽은 링크가 된다."""
        page = load_webrtc(_boot_payload(display_mode="stage", stage_url=None))
        assert page.locator("[data-stage-open]:visible").count() == 0

    def test_welcome_screen_still_renders_in_stage_mode(self, load_webrtc):
        """AC2 — 달라지는 것은 무대 버튼 노출뿐, 캡션 UI 는 그대로다."""
        page = load_webrtc(_boot_payload(display_mode="stage", stage_url=_STAGE_URL))
        assert page.locator(".welcome-title").count() >= 1
        assert page.locator("[data-stage-open]").count() == 1
