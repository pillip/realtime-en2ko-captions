"""ISSUE-55 후보 A 의 전제 실측 — 브라우저가 WS 핸드셰이크에 쿠키를 싣는가.

ISSUE-55 는 WS 인증을 `:8501` 에서 설정된 HMAC 서명 쿠키(`user_session`)에
결속한다. 그 설계는 **검증되지 않은 전제** 하나에 올라가 있다:

> 오퍼레이터 컴포넌트는 `st.components.v1.html` 의 `srcdoc` iframe 안에서 돌고,
> 그 안에서 열리는 WebSocket 핸드셰이크에 부모 오리진의 쿠키가 실린다.

이 전제가 깨지는 현실적인 경로가 둘 있다.
  1. Streamlit 이 컴포넌트 iframe 을 sandbox 하면 origin 이 opaque 가 되고
     SameSite 기본값(Lax) 쿠키가 붙지 않는다.
  2. 포트가 다르면(`:8501` → `:8765`) 쿠키가 안 갈 것이라는 오해 — 실제로 쿠키는
     포트를 구분하지 않으므로 가야 하지만, 실측 없이 믿을 근거는 아니다.

`components/webrtc.html` 이 `window.parent.location` 접근을 try/catch 로 감싸
localhost 폴백을 두고 있다는 사실은 이 코드베이스가 두 경우를 모두 만난 적이
있다는 뜻이다. 그래서 이슈 본문이 "먼저 실측할 것 / 확인에 실패하면 후보 B 로
간다" 를 못 박았다.

**이 테스트가 그 실측이다.** 스크린샷 한 장 대신 재현 가능한 자동 검증으로 둔다 —
스크린샷은 다음 사람이 다시 찍을 수 없고, 회귀도 잡지 못한다.

방법: 테스트 프로세스가 임시 `websockets` 서버를 띄우고, **컴포넌트 iframe 안의
JS 컨텍스트**에서 그 서버로 WebSocket 을 연 뒤, 서버가 받은 핸드셰이크 헤더에
`user_session` 이 있는지 본다. 외부 네트워크를 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

pytestmark = pytest.mark.e2e


class _HandshakeRecorder:
    """임시 WS 서버 — 받은 핸드셰이크의 Cookie 헤더를 기록한다."""

    def __init__(self) -> None:
        self.port: int | None = None
        self.cookie_header: str | None = None
        self.connected = threading.Event()
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _HandshakeRecorder:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("임시 WS 서버가 기동하지 않았다")
        return self

    def __exit__(self, *_exc) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        from websockets.asyncio.server import serve

        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()

            async def handler(connection):
                # 실제 `Headers` 는 대소문자를 구분하지 않는다.
                self.cookie_header = connection.request.headers.get("Cookie")
                self.connected.set()

            async with serve(handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                await self._stop.wait()

        asyncio.run(main())


def _component_frame(page):
    """오퍼레이터 컴포넌트(`srcdoc`) iframe 의 Frame 객체.

    `frame_locator` 로는 JS 를 실행할 수 없어 Frame 을 직접 찾는다. 컴포넌트가
    `srcdoc` 으로 렌더되므로 부모 문서가 아닌 프레임 중 본문에 우리 마크업이
    들어 있는 것을 고른다.
    """
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            has_marker = frame.evaluate(
                "() => !!document.getElementById('viewer')"
                " || !!document.querySelector('.welcome-state')"
            )
        except Exception:
            continue
        if has_marker:
            return frame
    return None


def _require_session_cookie(page):
    """`user_session` 이 이 브라우저 컨텍스트에 심어졌는지 — 아니면 skip.

    **왜 실패가 아니라 skip 인가.** `auth.get_cookie_manager()` 가 프로세스 전역
    싱글턴이라, 같은 서버에 두 번째 브라우저 컨텍스트가 붙으면 서버가 "이미
    로그인됨" 으로 보고 `set_session_cookie()` 를 건너뛸 수 있다. 그러면 **새
    컨텍스트의 쿠키 항아리는 비어 있는데** 로그인은 된 상태가 된다 — 이것이
    ISSUE-64(#155)가 측정으로 확정하려는 바로 그 순서 의존성이다.

    그 상태에서 이 파일이 실패하면 "후보 A 의 전제가 깨졌다" 는 **잘못된 결론**을
    낸다. 실제로 이 테스트를 처음 돌릴 때 그 혼동이 일어났고, 단독 프로브로
    확인해 보니 쿠키가 있을 때는 `user_session` 이 핸드셰이크에 **정상적으로
    실렸다.** 그래서 전제가 측정 불가인 경우를 명시적으로 분리한다.

    ISSUE-64 가 로그인 하네스를 결정적으로 만들면 이 skip 은 사라진다.
    """
    names = [c["name"] for c in page.context.cookies()]
    if "user_session" not in names:
        pytest.skip(
            "이 브라우저 컨텍스트에 user_session 쿠키가 없다 — 전역 CookieManager "
            f"싱글턴이 로그인을 건너뛴 경우다 (ISSUE-64/#155). 쿠키: {names}. "
            "후보 A 의 전제가 깨진 것이 아니라 측정이 불가한 상태다."
        )


class TestTheBrowserSendsTheSessionCookieOnTheWsHandshake:
    """후보 A 의 전제를 값으로 확인한다."""

    def test_the_top_level_page_has_the_session_cookie(self, logged_in_page):
        """선행 조건 — 로그인 후 `user_session` 쿠키가 실제로 설정된다.

        이것이 거짓이면 아래 두 테스트의 실패는 "iframe 문제" 가 아니라
        "애초에 쿠키가 없다" 는 뜻이므로, 원인을 가르기 위해 먼저 단언한다.
        """
        cookies = logged_in_page.context.cookies()
        names = [c["name"] for c in cookies]
        assert "user_session" in names, (
            f"로그인 후에도 user_session 쿠키가 없다. 설정된 쿠키: {names}. "
            "CookieManager 가 쿠키를 심지 못했다면 ISSUE-55 의 전제가 아니라 "
            "로그인 흐름 자체를 먼저 봐야 한다."
        )

    def test_a_websocket_opened_from_the_component_iframe_carries_the_cookie(
        self, logged_in_page
    ):
        """**핵심 실측** — 컴포넌트 iframe 에서 연 WS 가 쿠키를 싣는다.

        여기가 후보 A 의 성립/불성립이 갈리는 지점이다. 실패하면 후보 B
        (서버 발행 토큰 + BOOT 전달)로 가야 하고, 그때는 RL-028 제약이 따라온다.
        """
        _require_session_cookie(logged_in_page)
        frame = _component_frame(logged_in_page)
        if frame is None:
            pytest.skip(
                "컴포넌트 iframe 을 찾지 못했다 — 오퍼레이터 화면이 렌더되지 "
                "않았다는 뜻이고, 이는 ISSUE-64 의 로그인 하네스 문제다"
            )

        with _HandshakeRecorder() as rec:
            frame.evaluate(
                """(port) => {
                    try {
                        new WebSocket(`ws://${location.hostname}:${port}`);
                    } catch (e) {
                        window.__wsError = String(e);
                    }
                }""",
                rec.port,
            )
            assert rec.connected.wait(timeout=10), (
                "iframe 에서 연 WebSocket 이 서버에 도달하지 않았다 — "
                "sandbox 로 커넥션 자체가 막혔을 수 있다"
            )

            assert rec.cookie_header is not None, (
                "iframe 에서 연 WS 핸드셰이크에 Cookie 헤더가 전혀 없다. "
                "후보 A 의 전제가 깨졌다 → 후보 B(서버 발행 토큰)로 전환할 것."
            )
            assert "user_session=" in rec.cookie_header, (
                f"Cookie 헤더에 user_session 이 없다: {rec.cookie_header!r}. "
                "후보 A 의 전제가 깨졌다 → 후보 B 로 전환할 것."
            )

    def test_the_cookie_crosses_the_port_boundary(self, logged_in_page):
        """쿠키가 포트를 구분하지 않는다는 성질을 명시적으로 고정한다.

        임시 서버는 `:8501` 이 아닌 OS 할당 포트에 떠 있다. 그 포트로 쿠키가
        갔다는 것은 `ws://host:8765` 로도 간다는 뜻이다 — ISSUE-55 가 `:8765`
        에서 쿠키를 읽을 수 있는 근거다.
        """
        _require_session_cookie(logged_in_page)
        with _HandshakeRecorder() as rec:
            logged_in_page.evaluate(
                "(port) => { new WebSocket(`ws://${location.hostname}:${port}`); }",
                rec.port,
            )
            assert rec.connected.wait(timeout=10), "WS 가 서버에 도달하지 않았다"
            assert rec.port != 8501
            assert (
                rec.cookie_header and "user_session=" in rec.cookie_header
            ), f"다른 포트로 쿠키가 가지 않았다: {rec.cookie_header!r}"
