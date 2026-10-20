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
    """`user_session` 이 이 브라우저 컨텍스트에 있음을 **단언**한다.

    한때 이것은 `pytest.skip` 이었다. 구 로그인 하네스는 전역
    `auth.get_cookie_manager()` 싱글턴 때문에 두 번째 이후 브라우저 컨텍스트에서
    로그인을 건너뛰었고, 그러면 **서버는 인증됨으로 보는데 쿠키 항아리는 비어
    있는** 상태가 됐다. 그 상태의 실패는 "후보 A 의 전제가 깨졌다" 는 잘못된
    결론을 낳으므로(실제로 한 번 그렇게 오독했다) 측정 불가를 실패와 분리했다.

    ISSUE-64(#164)가 `freshly_logged_in_page` 로 쿠키를 보장하면서 그 분리가
    필요 없어졌다. 이제 쿠키 부재는 **하네스 계약 위반**이므로 실패로 다룬다 —
    skip 으로 남겨 두면 하네스가 퇴행해도 조용히 초록이 된다.
    """
    names = [c["name"] for c in page.context.cookies()]
    assert "user_session" in names, (
        f"freshly_logged_in_page 가 쿠키를 보장하지 못했다. 쿠키: {names}. "
        "ISSUE-64 의 require_cookie 경로가 퇴행했는지 확인할 것."
    )


class TestTheBrowserSendsTheSessionCookieOnTheWsHandshake:
    """후보 A 의 전제를 값으로 확인한다."""

    def test_the_session_cookie_has_the_attributes_candidate_a_relies_on(
        self, freshly_logged_in_page
    ):
        """쿠키가 있을 때, 후보 A 가 의존하는 **속성**을 고정한다.

        "쿠키가 존재한다" 만 단언하면 `_require_session_cookie` 의 skip 조건과
        같은 말이 되어 아무것도 증명하지 않는다. 대신 후보 A 의 성립 근거인
        속성들을 못 박는다 — 하나라도 바뀌면 WS 핸드셰이크에 쿠키가 실리지
        않게 될 수 있다.

        실측값(Chromium):
            domain=localhost  path=/  sameSite=Strict  secure=False
            value=1%3Ae2e_operator%3A...   ← percent-encoded
        """
        _require_session_cookie(freshly_logged_in_page)
        cookie = next(
            c
            for c in freshly_logged_in_page.context.cookies()
            if c["name"] == "user_session"
        )

        assert cookie["path"] == "/", (
            f"path 가 '/' 가 아니다: {cookie['path']!r} — WS 핸드셰이크 경로"
            "(`/ws` 또는 `/`)에 쿠키가 실리지 않을 수 있다"
        )
        assert cookie["domain"].lstrip(".") == "localhost", (
            f"domain 이 문서 호스트와 다르다: {cookie['domain']!r}. 쿠키는 포트는 "
            "무시하지만 **호스트는 구분한다** — WS URL 의 호스트가 문서 호스트와 "
            "같아야 한다"
        )
        # Secure 쿠키는 http 로컬에서 전송되지 않는다. 운영(https)에서는 무해하나
        # 로컬 리허설이 조용히 깨지므로 값을 고정한다.
        assert (
            cookie["secure"] is False
        ), "Secure 쿠키는 http 로컬에서 전송되지 않아 로컬 WS 인증이 깨진다"
        # 서버측 `_verify_cookie` 는 리터럴 `:` 로 서명을 떼므로, 저장값이
        # percent-encoded 라는 사실이 곧 `unquote` 가 필요한 이유다.
        assert (
            "%3A" in cookie["value"] or ":" in cookie["value"]
        ), f"쿠키 값 형식이 예상과 다르다: {cookie['value'][:40]!r}"

    def test_a_websocket_opened_from_the_component_iframe_carries_the_cookie(
        self, freshly_logged_in_page
    ):
        """**핵심 실측** — 컴포넌트 iframe 에서 연 WS 가 쿠키를 싣는다.

        여기가 후보 A 의 성립/불성립이 갈리는 지점이다. 실패하면 후보 B
        (서버 발행 토큰 + BOOT 전달)로 가야 하고, 그때는 RL-028 제약이 따라온다.
        """
        _require_session_cookie(freshly_logged_in_page)
        frame = _component_frame(freshly_logged_in_page)
        if frame is None:
            pytest.skip(
                "컴포넌트 iframe 을 찾지 못했다 — 오퍼레이터 화면이 렌더되지 "
                "않았다는 뜻이고, 이는 ISSUE-64 의 로그인 하네스 문제다"
            )

        with _HandshakeRecorder() as rec:
            # **호스트명을 부모에서 받아 넘긴다.** 컴포넌트 iframe 은
            # `about:srcdoc` 이라 `location.hostname` 이 빈 문자열이고
            # `location.origin` 이 `"null"`(opaque) 이다 — 실측. iframe 안에서
            # 호스트를 유도하려 하면 `ws://:1234` 같은 깨진 URL 이 만들어져
            # 연결이 실패하고, 그 실패가 "쿠키가 안 실린다" 로 오독된다.
            host = freshly_logged_in_page.evaluate("() => location.hostname")
            frame.evaluate(
                """([host, port]) => {
                    try {
                        new WebSocket(`ws://${host}:${port}`);
                    } catch (e) {
                        window.__wsError = String(e);
                    }
                }""",
                [host, rec.port],
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

    def test_the_cookie_crosses_the_port_boundary(self, freshly_logged_in_page):
        """쿠키가 포트를 구분하지 않는다는 성질을 명시적으로 고정한다.

        임시 서버는 `:8501` 이 아닌 OS 할당 포트에 떠 있다. 그 포트로 쿠키가
        갔다는 것은 `ws://host:8765` 로도 간다는 뜻이다 — ISSUE-55 가 `:8765`
        에서 쿠키를 읽을 수 있는 근거다.
        """
        _require_session_cookie(freshly_logged_in_page)
        with _HandshakeRecorder() as rec:
            freshly_logged_in_page.evaluate(
                "(port) => { new WebSocket(`ws://${location.hostname}:${port}`); }",
                rec.port,
            )
            assert rec.connected.wait(timeout=10), "WS 가 서버에 도달하지 않았다"
            assert rec.port != 8501
            assert (
                rec.cookie_header and "user_session=" in rec.cookie_header
            ), f"다른 포트로 쿠키가 가지 않았다: {rec.cookie_header!r}"
