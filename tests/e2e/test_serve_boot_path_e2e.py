"""프로덕션 부팅 경로(`serve.py`)로 띄워도 로그인이 쿠키를 심는가 (#174).

왜 별도 파일인가
----------------
`conftest.streamlit_server` 는 **`streamlit run app.py`** 를 띄운다. 그런데
컨테이너는 `python serve.py` 로 뜬다 (#158, Dockerfile CMD). 두 경로는 **앱
모듈을 처음 import 하는 시점**이 다르고, 그 차이가 기능을 갈랐다:

- `streamlit run app.py` → 첫 세션이 스크립트 런 **안에서** `app.py` 를 import
- `python serve.py` → `start_background_servers()` 가 스크립트 런 **밖에서**
  `websocket_handler → auth` 를 import

Streamlit 의 `declare_component()` 는 이렇게 끝난다::

    ctx = get_script_run_ctx()
    if ctx is not None:
        get_instance().component_registry.register_component(component)

스크립트 런 밖에서 import 되면 등록이 **조용히** 건너뛰어지고, `sys.modules`
캐시 때문에 이후 세션의 import 가 `declare_component()` 를 다시 실행하지 않는다.
결과는 `CookieManager` 번들 404 → `user_session` 쿠키 미발급 → (ISSUE-55 이후)
WS 인증 거부 → **오퍼레이터가 자막을 송출할 수 없다.** 화면에는 OpenAI 전사
(원문)만 흐르고 뷰어·무대는 "잠시 후 시작됩니다" 에서 멈춘다.

2026-10-22 프로덕션에서 정확히 그렇게 됐고, **e2e 198개는 전부 통과한 채로
배포됐다.** 전부 `streamlit run` 경로만 검증했기 때문이다. 이 파일이 그 구멍을
막는다 — 비용(서버 하나 더 띄우기)을 치르는 이유다.

또 하나: 대부분의 e2e 는 `require_cookie=False` 라 쿠키 존재를 아예 보지 않는다
(`conftest.ensure_logged_in_as`). 여기서는 쿠키가 **단 한 번의 평범한 로그인**으로
심기는지를 본다 — 재시도로 가려지지 않게.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e

_UI_TIMEOUT_MS = 30_000
# `conftest` 의 포트와 겹치면 두 서버가 서로를 밀어낸다.
_PORT = 8597
_WS_PORT = 8797
_SSE_PORT = 8798
_COMPONENT_PATH = (
    "/component/extra_streamlit_components.CookieManager.cookie_manager/index.html"
)
_ADMIN = "serve_probe_admin"
_PASSWORD = "serve_probe_pass_123"


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture(scope="module")
def serve_py_server():
    """`python serve.py` 로 띄운 서버 — 컨테이너와 같은 엔트리포인트."""
    for port in (_PORT, _WS_PORT, _SSE_PORT):
        if not _port_is_free(port):
            pytest.skip(
                f"포트 {port} 가 사용 중이라 serve.py 부팅 경로를 검증할 수 없다"
            )

    tmp = tempfile.mkdtemp(prefix="serve-boot-e2e-")
    env = {
        **os.environ,
        "DB_PATH": os.path.join(tmp, "test.db"),
        "BRANDING_DIR": os.path.join(tmp, "branding"),
        "ADMIN_USERNAME": _ADMIN,
        "ADMIN_PASSWORD": _PASSWORD,
        "ADMIN_EMAIL": "serve-probe@test.com",
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
        "SESSION_SECRET": "0" * 64,
        "WS_PORT": str(_WS_PORT),
        "SSE_PORT": str(_SSE_PORT),
        # serve.py 는 STREAMLIT_* 환경변수로 설정을 받는다 (CLI 플래그가 없다).
        "STREAMLIT_SERVER_PORT": str(_PORT),
        "STREAMLIT_SERVER_HEADLESS": "true",
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    }

    # PIPE 는 쓰지 않는다 — 아무도 읽지 않는 파이프가 64KB 에서 교착한다
    # (conftest.streamlit_server 의 같은 주석 참고).
    log_path = os.path.join(tmp, "serve.log")
    log_file = open(log_path, "wb")  # noqa: SIM115 — 프로세스 수명과 함께 닫는다
    proc = subprocess.Popen(
        ["uv", "run", "python", "serve.py"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://localhost:{_PORT}"
    for _ in range(90):
        if proc.poll() is not None:
            log_file.close()
            with open(log_path, encoding="utf-8", errors="replace") as f:
                pytest.fail(f"serve.py 가 즉시 종료됐다:\n{f.read()[-3000:]}")
        try:
            if httpx.get(f"{base_url}/_stcore/health", timeout=1).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        proc.terminate()
        log_file.close()
        with open(log_path, encoding="utf-8", errors="replace") as f:
            pytest.fail(f"serve.py 가 90초 안에 뜨지 않았다:\n{f.read()[-3000:]}")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log_file.close()


def _login_once(page, base_url: str) -> None:
    """**한 번만** 로그인한다 — 재시도로 결함을 가리지 않기 위해서다."""
    page.goto(base_url, wait_until="domcontentloaded")
    username = page.locator('input[aria-label="사용자명"]').first
    username.wait_for(state="visible", timeout=_UI_TIMEOUT_MS)
    username.fill(_ADMIN)
    page.locator('input[aria-label="비밀번호"]').first.fill(_PASSWORD)
    page.locator('button:has-text("로그인")').first.click()
    page.wait_for_function(
        """() => {
            const s = document.querySelector('[data-testid="stSidebar"]');
            return !!s && /아이디\\s*[:：]\\s*[A-Za-z0-9._@-]+/.test(s.textContent);
        }""",
        timeout=_UI_TIMEOUT_MS,
    )


class TestServePyBootPathIssuesTheSessionCookie:
    """컨테이너 엔트리포인트로 띄웠을 때도 쿠키가 심긴다."""

    def test_one_plain_login_plants_user_session(self, page, serve_py_server):
        _login_once(page, serve_py_server)

        # CookieManager 는 컴포넌트 왕복을 거치므로 계정명보다 늦게 도착한다.
        page.wait_for_function(
            "() => document.cookie.includes('user_session')",
            timeout=_UI_TIMEOUT_MS,
        )

        names = sorted(c["name"] for c in page.context.cookies())
        assert "user_session" in names, (
            f"평범한 로그인 한 번이 user_session 을 심지 못했다 (쿠키: {names}). "
            "ISSUE-55 이후 WS 인증이 이 쿠키에 의존하므로 오퍼레이터는 자막을 "
            "송출할 수 없다 — 화면에는 원문 전사만 흐르고 뷰어·무대는 빈 화면이다."
        )

    def test_cookie_component_bundle_is_served(self, page, serve_py_server):
        """컴포넌트 번들이 404 면 쿠키 쓰기 JS 가 아예 실행되지 않는다.

        이 단언이 위 쿠키 테스트와 **별개로** 필요한 이유: 404 는 원인이고 쿠키
        부재는 결과다. 둘을 따로 보면 다음에 깨질 때 어느 쪽인지 바로 안다.
        """
        _login_once(page, serve_py_server)

        status = page.evaluate(
            "u => fetch(u).then(r => r.status).catch(() => 0)",
            serve_py_server + _COMPONENT_PATH,
        )

        assert status == 200, (
            f"CookieManager 번들이 {status} 다. `declare_component()` 는 스크립트 런 "
            "컨텍스트 밖에서 import 되면 등록을 조용히 건너뛴다 — `auth.py` 가 "
            "`extra_streamlit_components` 를 모듈 레벨에서 import 하면 `serve.py` "
            "부팅에서 그렇게 된다 (#174)."
        )
