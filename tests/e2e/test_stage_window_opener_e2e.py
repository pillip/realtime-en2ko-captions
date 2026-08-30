"""
ISSUE-50 — 무대 창 opener 차단의 **두 오리진** e2e (browser-driven).

`components/webrtc.html` 의 무대 창 오픈 경로는 `window.open(...)` 이 돌려준
핸들에 `opened.opener = null` 을 써서 무대 창이 오퍼레이터 탭을 조작하지
못하게 만든다. 그 완화가 **실제 크로스 오리진 팝업에서도** 유효한지는 지금까지
어떤 테스트도 보지 못했다:

  - `tests/e2e/test_operator_stage_mode_e2e.py` 는 `window.open` 을 같은 realm 의
    평범한 객체(`{closed, focus, opener}`)로 갈아끼운다. 그 객체에 대한
    `opener` 대입은 브라우저의 크로스 오리진 규칙과 **무관하게 항상 성공**한다.
    (그 파일은 `window.open` 의 **호출 인자**를 검증한다 — 여전히 유효하고 이
    파일이 대체하지 않는다. 둘은 서로 다른 것을 본다.)
  - `tests/test_webrtc_stage_launch.py` 는 `"opened.opener = null" in html` 이라는
    부분 문자열 검사다. 문자열이 파일 어디에 있든, 언제 실행되든 통과한다.

따라서 대입이 클릭의 **동기 실행 구간 밖**으로 밀리는 순간(`setTimeout`,
`await`, 프라미스로 호출되는 헬퍼로 추출) 그것은 진짜 크로스 오리진 접근이
되고, 프로덕션의 `try/catch` 가 그 실패를 `console.debug` 로 조용히 삼킨다.
완화는 무력화되는데 테스트는 전부 초록이다 — RL-026 이 기술한 형태 그대로다.

## 이 파일이 하는 일

두 개의 **진짜 오리진**을 띄운다. `file://` 로는 안 된다 — opaque origin 이라
크로스 오리진 "관계" 자체가 성립하지 않는다. 포트는 오리진 튜플의 일부이므로
포트만 달라도 충분하다:

  - 오리진 A (오퍼레이터): 프로덕션 렌더 경로(`operator_ui.render_component_html`
    + `operator_ui.build_bootstrap_payload`)로 만든 `components/webrtc.html`.
    fixture 가 치환/조립을 자체 구현하면 프로덕션이 고쳐져도 이 파일은 옛
    경로를 계속 테스트한다 (ISSUE-48 / RL-024).
  - 오리진 B (무대): `<div id="stage-stub-ready">` 한 장짜리 최소 스텁. 진짜
    `stage.html` 을 서빙하면 캡처/SSE 부작용이 딸려 들어오는데, 여기서 증명해야
    하는 것은 "팝업이 다른 오리진으로 실제로 내비게이트했다" 뿐이다.

`window.open` 은 **스텁하지 않는다**. 팝업은 `page.expect_popup()` 으로 잡고,
단언은 팝업 **자신의 realm** 에서 실행한다.

## 왜 `popup.evaluate` 는 되고 `opened.opener` 읽기는 안 되는가

`popup.evaluate("window.opener === null")` 의 코드는 팝업 문서 안에서 돈다.
거기서 `window.opener` 를 읽는 것은 자기 자신의 전역을 읽는 **동일 오리진
접근**이라 아무 제약이 없다. 반대로 오퍼레이터 페이지에서 `opened.opener` 를
만지는 것은 크로스 오리진 WindowProxy 에 대한 접근이라 브라우저의 크로스
오리진 규칙을 정면으로 통과해야 한다. **단언을 어느 쪽 realm 에서 하는지가 이
테스트의 전부다** — 스텁을 쓰던 기존 e2e 는 애초에 realm 이 하나뿐이라 이
구분을 만들 수 없었다.

현 구현이 통과하는 이유는 타이밍이다: `window.open` 이 반환하는 순간 새 창의
활성 문서는 아직 오리진을 상속한 `about:blank` 이라 그 대입이 **동일 오리진
쓰기**다. 그래서 이 파일의 값어치는 "지금 초록이다" 가 아니라 "대입이 뒤로
밀리면 빨개진다" 에 있다 — 그 대조는 PR 설명의 뮤테이션 표에 있다.

공허성 방지(RL-004 / RL-026): opener 를 읽기 **전에** 팝업이 정말 무대 오리진의
문서를 띄웠는지 값으로 확인한다. 내비게이션이 일어나지 않았다면 팝업은 여전히
오퍼레이터 오리진의 `about:blank` 이고 그 상태에서 `window.opener === null` 은
"완화가 동작함" 이 아니라 "아직 아무 일도 안 일어남" 을 뜻한다.

`e2e` 마크는 기본 `uv run pytest -q` 에서 deselect 되므로 개발 루프를 막지 않는다.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import re
import socket
import sys
import threading
from unittest.mock import MagicMock

import pytest

# Streamlit 의존성 회피 (다른 e2e 파일과 동일 패턴).
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()

pytestmark = pytest.mark.e2e

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_WEBRTC_TEMPLATE = _REPO_ROOT / "components" / "webrtc.html"

_OPERATOR_PATH = "/operator"
_STAGE_PATH = "/stage-stub"

# 무대 오리진이 서빙하는 최소 문서. 마커 하나면 "다른 오리진의 문서가 실제로
# 로드되었다" 를 증명하기에 충분하다.
_STAGE_STUB_HTML = """<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><title>무대 스텁 (ISSUE-50)</title></head>
<body style="background:#000;color:#fff;font-family:sans-serif">
  <div id="stage-stub-ready">stage stub ready</div>
</body>
</html>
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(app, port: int):
    """aiohttp 앱을 데몬 스레드에 띄우고 ``(base_url, shutdown)`` 을 돌려준다.

    ``tests/e2e/test_stage_page_e2e.py`` 의 fixture 뼈대와 같은 패턴이다.
    """
    from aiohttp import web

    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    started = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", port)
        loop.run_until_complete(site.start())
        started.set()
        loop.run_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(timeout=5), f"aiohttp server on :{port} failed to start in 5s"

    def _shutdown() -> None:
        try:
            asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(timeout=5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)

    return f"http://127.0.0.1:{port}", _shutdown


@pytest.fixture(scope="module")
def stage_origin():
    """오리진 B — 무대 스텁만 서빙한다."""
    from aiohttp import web

    async def _handler(_request):
        return web.Response(text=_STAGE_STUB_HTML, content_type="text/html")

    app = web.Application()
    app.router.add_get(_STAGE_PATH, _handler)

    base_url, shutdown = _serve(app, _free_port())
    yield base_url
    shutdown()


@pytest.fixture(scope="module")
def operator_origin(stage_origin):
    """오리진 A — 무대 URL 이 **오리진 B** 를 가리키는 오퍼레이터 컴포넌트."""
    from aiohttp import web

    from operator_ui import build_bootstrap_payload, render_component_html

    # 프로덕션 조립 경로를 그대로 부른다 (ISSUE-48 / RL-024).
    payload = build_bootstrap_payload(
        action="idle",
        openai_session=None,
        websocket_port=8765,
        user_info={"id": 1, "username": "op1", "role": "operator"},
        room_id="room-50",
        room_name="A홀",
        view_url=f"{stage_origin}/view/room-50",
        qr_data_url=None,
        display_mode="stage",
        stage_url=f"{stage_origin}{_STAGE_PATH}",
    )
    html = render_component_html(_WEBRTC_TEMPLATE.read_text(encoding="utf-8"), payload)

    async def _handler(_request):
        return web.Response(text=html, content_type="text/html")

    app = web.Application()
    app.router.add_get(_OPERATOR_PATH, _handler)

    base_url, shutdown = _serve(app, _free_port())
    yield base_url
    shutdown()


@pytest.fixture()
def operator_page(page, operator_origin):
    """오퍼레이터 문서를 열고 부트스트랩이 끝난 page 를 준다."""
    page.goto(f"{operator_origin}{_OPERATOR_PATH}", wait_until="load")
    page.locator("[data-stage-open]").wait_for(state="visible", timeout=10000)
    return page


def _assert_navigated_to_stage(popup, stage_origin: str) -> None:
    """opener 를 읽기 **전에** 통과해야 하는 공허성 게이트 (RL-004 / RL-026).

    이 게이트가 없으면 팝업이 오퍼레이터 오리진의 `about:blank` 에 머물러 있는
    상태에서도 `window.opener === null` 단언이 통과할 수 있다 — 그때 그 null 은
    "완화가 동작했다" 가 아니라 "크로스 오리진 상황이 아직 만들어지지 않았다"
    를 뜻한다.
    """
    observed_origin = popup.evaluate("location.origin")
    assert observed_origin == stage_origin, (
        f"popup origin is {observed_origin!r}, expected the stage origin "
        f"{stage_origin!r} — the opener assertion below would be vacuous because "
        "no cross-origin situation was ever created"
    )
    marker = popup.locator("#stage-stub-ready")
    marker.wait_for(state="attached", timeout=5000)
    assert marker.count() == 1, (
        "the stage stub document did not load in the popup — the popup is still "
        "on the inherited about:blank, so the opener assertion proves nothing"
    )


# 관측용 스크립트. **각 읽기를 개별적으로 try/catch 한다** — opener 가 끊기지
# 않은 팝업에서 `String(window.opener)` 는 크로스 오리진 WindowProxy 의
# `toString` 을 부르다 SecurityError 로 죽는다. 감싸지 않으면 진단 코드가
# 단언보다 먼저 터져서, 실패 이유가 "opener 가 살아 있다" 가 아니라 정체불명의
# Playwright 오류로 보고된다 (완화가 깨진 바로 그 상황에서).
_DESCRIBE_OPENER = """
(() => {
  const out = { href: location.href };
  try { out.isNull = window.opener === null; } catch (e) { out.isNull = 'threw:' + e.name; }
  try { out.type = typeof window.opener; } catch (e) { out.type = 'threw:' + e.name; }
  try { out.str = String(window.opener); } catch (e) { out.str = 'threw:' + e.name; }
  return out;
})()
"""


def _assert_only_the_operator_page_remains(page) -> None:
    """AC7 — 열린 팝업이 모두 닫혔는지 확인한다 (창 누수 방지)."""
    open_urls = [p.url for p in page.context.pages]
    assert len(open_urls) == 1, f"popups leaked: {open_urls}"


def _report_opener(popup, label: str) -> dict:
    """실제로 관측된 `opener` 값을 기록에 남긴다 (`-s` 로 확인 가능)."""
    described = popup.evaluate(_DESCRIBE_OPENER)
    print(f"[ISSUE-50] {label}: window.opener -> {described}")
    return described


class TestTwoOriginFixture:
    """fixture 자체가 약속한 조건을 먼저 고정한다 — 아니면 아래가 전부 공허하다."""

    def test_the_two_servers_are_genuinely_different_origins(
        self, operator_origin, stage_origin
    ):
        assert operator_origin != stage_origin
        # 포트는 오리진 튜플(scheme, host, port)의 일부다. 둘 다 127.0.0.1 이라도
        # 포트가 다르면 크로스 오리진이다.
        assert operator_origin.rsplit(":", 1)[1] != stage_origin.rsplit(":", 1)[1]
        assert operator_origin.startswith("http://127.0.0.1:")
        assert stage_origin.startswith("http://127.0.0.1:")

    def test_the_operator_page_points_at_the_stage_origin(
        self, operator_page, stage_origin
    ):
        """버튼이 실제로 **다른** 오리진을 열도록 부트스트랩되어 있는가."""
        href = operator_page.locator("[data-stage-fallback]").get_attribute("href")
        assert href == f"{stage_origin}{_STAGE_PATH}", (
            f"the component was bootstrapped with {href!r}; if it pointed back at "
            "the operator origin every cross-origin assertion here would be a lie"
        )
        assert operator_page.evaluate("location.origin") != stage_origin


class TestStagePopupOpenerIsSevered:
    """AC1 / AC2 / AC3 — 진짜 크로스 오리진 팝업에서 opener 가 끊겨 있다."""

    def test_popup_from_the_button_has_no_opener(
        self, operator_page, stage_origin, request
    ):
        with operator_page.expect_popup() as popup_info:
            operator_page.locator("[data-stage-open]").click()
        popup = popup_info.value
        popup.wait_for_load_state("load")

        # 1) 공허성 게이트 먼저 — 순서가 계약이다.
        _assert_navigated_to_stage(popup, stage_origin)

        # 2) 관측값을 기록에 남긴 뒤에 단언한다.
        observed = _report_opener(popup, "window.open path")
        assert popup.evaluate("window.opener === null") is True, (
            "the stage popup still holds a live handle on the operator tab — "
            "anything running on the stage origin could drive the operator "
            f"document through it. observed: {observed!r}"
        )

        # 3) 창 누수 방지 (AC7).
        popup.close()
        assert popup.is_closed()
        _assert_only_the_operator_page_remains(operator_page)

    def test_popup_from_the_fallback_anchor_has_no_opener(
        self, operator_page, stage_origin
    ):
        """AC5 — 팝업 차단 폴백 경로도 같은 계약을 지킨다.

        `.stage-launch-fallback` 은 차단이 감지될 때까지 `display:none` 이다.
        여기서는 **DOM 스타일 한 줄만** 손대서 드러낸다 — `window.open` 을
        갈아끼우는 스텁이 아니라, 이미 존재하는 프로덕션 노드를 보이게만 하는
        조작이다. 클릭 이후의 동작(`target`/`rel` 해석, 창 생성, 내비게이션)은
        전부 브라우저가 한다.
        """
        operator_page.evaluate(
            "document.querySelector('[data-stage-fallback]').style.display = 'block'"
        )
        fallback = operator_page.locator("[data-stage-fallback]")
        fallback.wait_for(state="visible", timeout=5000)

        # `rel="noopener"` 는 opener 관계 자체를 끊으므로 `expect_popup()` 의
        # opener 기반 귀속이 성립하지 않을 수 있다. 컨텍스트 레벨에서 새 페이지를
        # 잡으면 그 차이에 의존하지 않는다.
        with operator_page.context.expect_page() as page_info:
            fallback.click()
        popup = page_info.value
        popup.wait_for_load_state("load")

        _assert_navigated_to_stage(popup, stage_origin)

        observed = _report_opener(popup, "fallback anchor path")
        assert popup.evaluate("window.opener === null") is True, (
            'the fallback anchor lost rel="noopener" — the popup-blocked escape '
            "hatch would hand the stage origin a handle on the operator tab. "
            f"observed: {observed!r}"
        )

        popup.close()
        assert popup.is_closed()
        _assert_only_the_operator_page_remains(operator_page)


class TestThisHarnessDoesNotStubTheBrowser:
    """AC4 — 이 파일이 스텁으로 자기 자신을 공허하게 만들지 않았음을 고정한다.

    검사는 소스 **문자열 grep** 이 아니라 AST 로 한다. 이 파일은 한국어 주석과
    docstring 으로 `window` / `open` 이야기를 잔뜩 하므로, 순진한 grep 은
    주석을 고칠 때마다 흔들리고(거짓 양성) 다음 사람이 가드 대신 주석을
    지우게 만든다.
    """

    @pytest.fixture(scope="class")
    def tree(self) -> ast.Module:
        return ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))

    @staticmethod
    def _live_string_constants(tree: ast.Module) -> list[str]:
        """브라우저로 넘어갈 수 있는 문자열만 — docstring/주석은 뺀다.

        문장(statement) 위치의 벌거벗은 문자열은 docstring 이거나 설명문이고,
        브라우저에 전달될 방법이 없다.
        """
        bare = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in bare
        ]

    def test_no_init_script_is_injected_at_all(self, tree):
        """init script 가 하나도 없으면 팝업 API 를 갈아끼울 자리도 없다."""
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_init_script"
        ]
        assert not calls, (
            f"{len(calls)} init script injection(s) found at line(s) "
            f"{[c.lineno for c in calls]} — this harness must exercise the real "
            "browser popup API, otherwise the opener assertions measure a stub"
        )

    def test_no_string_in_this_file_reassigns_the_popup_api(self, tree):
        """`window` 의 open 을 **대입**으로 덮어쓰는 스크립트가 없다.

        `=(?!=)` 로 대입만 노린다 — `window.opener === null` 같은 **읽기**는
        이 파일의 존재 이유이므로 걸리면 안 된다.
        """
        reassign = re.compile(r"window\.open\s*=(?!=)")
        offenders = [
            value
            for value in self._live_string_constants(tree)
            if reassign.search(value)
        ]
        assert not offenders, f"this file overwrites the popup API: {offenders!r}"

    def test_no_network_interception_fakes_the_stage_document(self, tree):
        """`page.route(...)` 로 무대 문서를 가짜로 채우지 않는다.

        오리진 B 의 응답이 가짜면 "다른 오리진으로 내비게이트했다" 는 게이트가
        네트워크 레벨에서 무력해진다 — 두 오리진은 진짜 소켓이어야 한다.
        """
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"route", "route_from_har"}
        ]
        assert not calls, (
            f"network interception found at line(s) {[c.lineno for c in calls]} — "
            "both origins must be real HTTP servers"
        )
