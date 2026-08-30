"""
ISSUE-48 — 오퍼레이터 부트스트랩 script-context 이스케이프 e2e (browser-driven).

`components/webrtc.html` 을 **프로덕션 렌더 함수**(`operator_ui.render_component_html`)
로 렌더한 뒤 `file://` 로 직접 로드해, admin 이 입력한 어떤 룸 이름도

  1. 오퍼레이터 오리진에서 스크립트를 실행시키지 못하고 (AC 1),
  2. 부트스트랩을 죽이지 못하며 (AC 1),
  3. 웰컴 화면에 이중 이스케이프되어 보이지 않는다 (AC 2)

는 것을 브라우저에서 값으로 확인한다. 정적 문자열 검사만으로는 "실행되지
않는다" 를 증명하지 못한다 — ISSUE-44 가 백슬래시 케이스에서 정확히 그 차이를
겪었다.

**치환을 테스트에서 재구현하지 않는다** (RL-024): 여기서 `json.dumps` 를 직접
쓰면 프로덕션이 고쳐져도/망가져도 이 파일은 같은 결과를 낸다.

`BOOT` 관측 방법에 대하여: 템플릿의 `const BOOT` 는 `try { … }` 블록 안에 있어
렉시컬 스코프가 그 블록이다. 따라서 `page.evaluate("typeof BOOT")` 는 성공/실패와
무관하게 항상 `"undefined"` 를 돌려주는 **공허한 단언**이 된다 (RL-004). 대신
페이지 자신이 실행하는 `console.log('BOOT data:', BOOT)` 의 인자를 잡아 실제
객체를 읽고, 스크립트 블록이 끝까지 평가됐다는 증거로 호이스팅된 함수 선언이
전역에서 호출 가능한지도 함께 본다.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any, NamedTuple
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

# admin 자유 입력 룸 이름의 적대적 스펙트럼. 한 페이로드만 막는 이스케이퍼는
# 이스케이퍼가 아니다 — 케이스마다 브라우저가 다른 파서 경로를 탄다.
_HOSTILE_ROOM_NAMES = [
    # 1. PR #137 리뷰 S-1 이 실측한 원본 페이로드.
    "A홀</script><script>window.__pwned=1;</script>",
    # 2. 대소문자 + 공백 변형 — HTML 은 종료 태그 이름을 대소문자 구분 없이
    #    인식하고 `>` 앞 공백도 허용한다.
    "A홀</SCRIPT ><script>window.__pwned=1;</script>",
    # 3. HTML 주석 시작으로 파서 상태를 흔드는 변형.
    "A홀<!--</script><script>window.__pwned=1;</script>",
    # 4. 끝 백슬래시 — ISSUE-44 가 "정적 검사가 놓친다" 고 배운 케이스.
    "A홀\\</script><script>window.__pwned=1;</script>",
    # 5. U+2028 line separator — JSON 에서는 합법이지만 ES2019 이전 JS
    #    문자열 리터럴에서는 개행으로 취급됐다.
    "A홀 window.__pwned=1;//",
    # 6. 속성 컨텍스트를 노린 페이로드 (마크업 싱크가 생기면 여기서 걸린다).
    '"><img src=x onerror=window.__pwned=1>',
]

_HOSTILE_IDS = [
    "script-breakout",
    "uppercase-close-tag",
    "html-comment",
    "trailing-backslash",
    "line-separator",
    "attribute-payload",
]

# RL-004 게이트용 대조군. `&` 가 `&amp;` 로 새는 이중 이스케이프를 잡는다.
_BENIGN_ROOM_NAME = "A홀 & B홀"


class _Loaded(NamedTuple):
    page: Any
    errors: list[str]
    console: list[Any]


def _boot_payload(room_name: str) -> dict[str, Any]:
    """프로덕션과 같은 조립 경로로 만든 부트스트랩 payload."""
    from operator_ui import build_bootstrap_payload

    return build_bootstrap_payload(
        action="idle",
        openai_session=None,
        websocket_port=8765,
        user_info={"id": 1, "username": "op1", "role": "operator"},
        room_id="room-42",
        room_name=room_name,
        view_url="http://localhost:8766/view/room-42",
        qr_data_url=None,
        display_mode="caption",
        stage_url=None,
    )


def _boot_object(loaded: _Loaded) -> Any:
    """페이지가 스스로 찍은 `console.log('BOOT data:', BOOT)` 에서 BOOT 을 읽는다."""
    for message in loaded.console:
        if message.text.startswith("BOOT data:"):
            args = message.args
            assert len(args) == 2, f"unexpected console args: {message.text!r}"
            return args[1].json_value()
    return None


@pytest.fixture
def load_operator(page, tmp_path):
    """프로덕션 렌더 함수로 만든 webrtc.html 을 `file://` 로 연다."""

    def _load(room_name: str) -> _Loaded:
        from operator_ui import render_component_html

        html = render_component_html(
            _WEBRTC_TEMPLATE.read_text(encoding="utf-8"), _boot_payload(room_name)
        )
        target = tmp_path / "webrtc_under_test.html"
        target.write_text(html, encoding="utf-8")

        errors: list[str] = []
        console: list[Any] = []
        # 리스너는 goto **이전**에 붙어야 한다 — 부트스트랩 예외는 로드 중에 난다.
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console.append(m))

        page.goto(target.as_uri(), wait_until="load")
        page.wait_for_timeout(600)
        return _Loaded(page=page, errors=errors, console=console)

    return _load


class TestHostileRoomNameCannotExecute:
    @pytest.mark.parametrize("room_name", _HOSTILE_ROOM_NAMES, ids=_HOSTILE_IDS)
    def test_injected_script_does_not_run(self, load_operator, room_name):
        """AC 1 — 주입 스크립트가 오퍼레이터 오리진에서 실행되지 않는다.

        Streamlit 컴포넌트 iframe 샌드박스는 `allow-same-origin` 을 포함하므로
        여기서 실행되는 스크립트는 오퍼레이터 오리진의 쿠키/localStorage 에
        접근할 수 있다.
        """
        loaded = load_operator(room_name)

        assert loaded.page.evaluate("window.__pwned === undefined") is True
        assert loaded.errors == [], f"bootstrap raised JS errors: {loaded.errors}"

    @pytest.mark.parametrize("room_name", _HOSTILE_ROOM_NAMES, ids=_HOSTILE_IDS)
    def test_bootstrap_survives_and_carries_the_exact_room_name(
        self, load_operator, room_name
    ):
        """AC 1 — `BOOT` 이 객체로 살아 있고 값이 손실 없이 도착한다.

        수정 전에는 주입이 스크립트 블록을 조기 종료시켜 `BOOT` 이 아예 만들어
        지지 않았다 — 자막이 한 줄도 나오지 않는 자폭 장애다.
        """
        loaded = load_operator(room_name)

        boot = _boot_object(loaded)
        assert isinstance(boot, dict), f"BOOT never bootstrapped: {boot!r}"
        assert boot["room_name"] == room_name
        assert boot["room_id"] == "room-42"
        # 스크립트 블록이 끝까지 평가됐다는 증거 (함수 선언은 전역으로 호이스팅된다).
        assert loaded.page.evaluate("typeof appendLine") == "function"

    @pytest.mark.parametrize("room_name", _HOSTILE_ROOM_NAMES, ids=_HOSTILE_IDS)
    def test_welcome_room_text_is_the_room_name_verbatim(
        self, load_operator, room_name
    ):
        """AC 2 — `.welcome-room` 의 `textContent` 가 원본과 **정확히** 일치한다.

        룸 이름은 `textContent` 로만 DOM 에 닿으므로 마크업 이스케이프를
        추가하면 안 된다. `&lt;` / `&amp;` 가 화면에 그대로 보이는 것이
        ISSUE-44 가 뷰어에서 겪은 이중 이스케이프 결함이다.
        """
        loaded = load_operator(room_name)

        room_el = loaded.page.locator(".welcome-room").first
        text = room_el.text_content()
        assert text == room_name, f"welcome-room drifted: {text!r} != {room_name!r}"
        assert "&lt;" not in text
        assert "&amp;" not in text


class TestBenignRoomNameIsUnregressed:
    def test_ampersand_room_name_renders_verbatim(self, load_operator):
        """RL-004 게이트 — 적대적 케이스가 아닌 정상 룸도 그대로 보인다.

        이 대조군이 없으면 위 단언들은 "웰컴 화면이 아예 렌더되지 않는다" 는
        상태에서도 통과할 수 있다.
        """
        loaded = load_operator(_BENIGN_ROOM_NAME)

        assert loaded.page.evaluate("window.__pwned === undefined") is True
        assert loaded.errors == []

        boot = _boot_object(loaded)
        assert isinstance(boot, dict), f"BOOT never bootstrapped: {boot!r}"
        assert boot["room_name"] == _BENIGN_ROOM_NAME

        text = loaded.page.locator(".welcome-room").first.text_content()
        assert text == _BENIGN_ROOM_NAME
        assert "&amp;" not in text
        # 웰컴 화면이 실제로 보이는 상태여야 위 단언이 공허하지 않다.
        assert loaded.page.locator(".welcome-room").first.is_visible() is True
