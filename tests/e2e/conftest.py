"""
E2E 테스트 공통 fixture
Streamlit 서버를 subprocess로 시작하고, 테스트용 DB/계정을 설정
"""

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
import time

import httpx
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# 테스트 계정 정보
TEST_USERNAME = "e2e_admin"
TEST_PASSWORD = "e2e_test_pass_123"
# 캡션 UI 는 operator 역할에서만 렌더된다 (admin 은 관리 전용, #101).
# logged_in_page 는 이 operator 로 로그인해 캡션 컴포넌트를 띄운다.
TEST_OPERATOR_USERNAME = "e2e_operator"

# Streamlit 서버 포트
STREAMLIT_PORT = 8599


@pytest.fixture(scope="session")
def _tmp_db_dir():
    """임시 DB 디렉토리"""
    d = tempfile.mkdtemp(prefix="e2e_db_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def branding_dir(_tmp_db_dir):
    """브랜딩 에셋을 **저장소 작업 트리 밖**으로 격리한다 (ISSUE-64).

    `BRANDING_DIR` 기본값은 `data/branding` 이고 cwd 가 저장소 루트라, 테스트가
    실제 작업 트리에 파일을 쓴다. 두 가지가 문제다.
      1. 병렬 워크트리(`.worktrees/`)가 같은 상대 경로를 공유해 서로의 에셋을
         보고 지운다 — 테스트가 서로를 오염시킨다.
      2. 테스트가 도중에 죽으면 `data/branding/` 에 파일이 남고 커밋 후보로
         올라온다 (실제로 고객 로고가 추적 가능한 상태였다).

    **`autouse=True` 이고 `streamlit_server` 보다 먼저 선다.** 처음엔
    `streamlit_server` 안에서 env 를 설정했는데, `seeded_logo` 는
    `streamlit_server` 에 의존하지 않으므로 픽스처 순서가 보장되지 않았다.
    실측 결과 작업 트리에 `data/branding/e2estage1/` **빈 디렉터리**가 남았다 —
    파일은 tmp 로 갔지만 어떤 호출 경로가 기본 경로를 먼저 만들었다는 뜻이다.
    autouse 로 올려 "어떤 테스트 코드보다 먼저" 를 보장한다.

    `branding_assets` 는 호출마다 `os.getenv` 를 읽으므로(`DB_PATH` 와 같은
    관례) 테스트 프로세스는 `os.environ` 으로, 서버 서브프로세스는 `env` 로
    같은 값을 받는다. 한쪽만 설정하면 서버와 테스트가 **다른 디렉터리**를 보며
    조용히 어긋난다.
    """
    path = os.path.join(_tmp_db_dir, "branding")
    os.makedirs(path, exist_ok=True)
    previous = os.environ.get("BRANDING_DIR")
    os.environ["BRANDING_DIR"] = path

    yield path

    # 세션 밖으로 흘리지 않는다 — 같은 프로세스에서 뒤에 도는 테스트가 사라진
    # tmpdir 을 보게 되면 원인을 찾기 어려운 실패가 된다.
    if previous is None:
        os.environ.pop("BRANDING_DIR", None)
    else:
        os.environ["BRANDING_DIR"] = previous


@pytest.fixture(scope="session")
def streamlit_server(_tmp_db_dir, branding_dir):
    """Streamlit 서버를 시작하고 URL을 반환"""
    db_path = os.path.join(_tmp_db_dir, "test.db")

    env = {
        **os.environ,
        "DB_PATH": db_path,
        "BRANDING_DIR": branding_dir,
        "ADMIN_USERNAME": TEST_USERNAME,
        "ADMIN_PASSWORD": TEST_PASSWORD,
        "ADMIN_EMAIL": "e2e@test.com",
        # AWS 자격 증명 더미값 (서버 시작만 가능하면 됨)
        "AWS_ACCESS_KEY_ID": "test_key",
        "AWS_SECRET_ACCESS_KEY": "test_secret",
    }

    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "streamlit",
            "run",
            "app.py",
            "--server.port",
            str(STREAMLIT_PORT),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    base_url = f"http://localhost:{STREAMLIT_PORT}"

    # 서버 준비 대기 (최대 30초)
    for _ in range(60):
        try:
            r = httpx.get(f"{base_url}/_stcore/health", timeout=1)
            if r.status_code == 200:
                break
        except (httpx.ConnectError, httpx.ReadTimeout):
            pass
        time.sleep(0.5)
    else:
        proc.terminate()
        stdout = proc.stdout.read().decode() if proc.stdout else ""
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        pytest.fail(
            f"Streamlit server failed to start\nstdout: {stdout}\nstderr: {stderr}"
        )

    # 캡션 UI E2E 는 operator 계정으로 수행한다 (#101: admin 은 관리 전용이라
    # 캡션 컴포넌트를 렌더하지 않음). 서버와 동일한 DB 파일에 operator 를
    # 직접 생성한다 — DatabaseManager.__init__ 은 멱등(IF NOT EXISTS)이다.
    try:
        from database import DatabaseManager, User

        dbm = DatabaseManager(db_path)
        user_repo = User(dbm)
        if user_repo.get_user_by_username(TEST_OPERATOR_USERNAME) is None:
            user_repo.create_user(
                username=TEST_OPERATOR_USERNAME,
                password=TEST_PASSWORD,
                role="operator",
                usage_limit_seconds=36000,
            )
    except Exception as e:
        proc.terminate()
        pytest.fail(f"E2E operator 계정 생성 실패: {e!r}")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# 결정적 로그인 하네스 (ISSUE-64)
#
# **이 파일이 로그인을 수행하는 유일한 자리다** (RL-001). 테스트 파일이 각자
# 로그인 절차를 복사하면 반드시 갈라지고, 그중 하나만 고쳐도 다른 쪽은 조용히
# 낡는다.
#
# ## 측정으로 확정한 것 (AC1)
#
# 전체 `-m e2e` 를 돌리며 `logged_in_page` 진입 시점의 사이드바 계정명과 쿠키를
# 찍어 봤다. 16개 호출 전부:
#
#     account='e2e_operator'  login_needed=False  cookies=['_streamlit_xsrf']
#
# 두 가지가 드러났다.
#
# 1. **이슈 본문의 유력 가설(admin 세션 상속)은 이 실행에서 재현되지 않았다.**
#    계정은 내내 `e2e_operator` 였다. 가설을 그대로 적어 두면 다음 사람이 엉뚱한
#    곳을 판다.
# 2. **첫 테스트 이후 로그인이 매번 건너뛰어지고(`login_needed=False`),
#    `user_session` 쿠키가 어떤 컨텍스트에도 없다.** 즉 `get_cookie_manager()`
#    전역 싱글턴이 **이전 브라우저 컨텍스트의 캐시된 쿠키 값**을 돌려주므로
#    서버는 인증된 것으로 보는데, 새 브라우저의 쿠키 항아리는 비어 있다.
#    `set_session_cookie()` 가 호출될 일이 없기 때문이다.
#
# 2번이 두 가지를 동시에 설명한다 — 로그인 폼을 기준으로 분기하는 기존 하네스가
# 왜 "인증됨" 과 "쿠키 없음" 을 구별하지 못했는지, 그리고 왜
# `tests/e2e/test_ws_cookie_handshake_e2e.py`(ISSUE-55 의 전제 실측)가 쿠키를
# 찾지 못해 skip 되는지.
#
# 플레이키 자체는 타이밍이다. `login_needed=False` 면 곧바로 20초 iframe 대기로
# 들어가므로, 서버가 느린 실행에서는 그 대기가 타임아웃하며 **failure 가 아니라
# fixture error** 가 된다 — 이슈 본문이 관측한 형태와 일치한다. 같은 커밋에서
# 198 passed 와 "2 failed + 5 errors" 가 모두 나왔다.
#
# ## 그래서 계약을 바꿨다
#
# `logged_in_page` 가 끝나면 **항상** 다음이 참이다:
#   (a) 사이드바가 `e2e_operator` 를 표시한다
#   (b) `user_session` 쿠키가 이 브라우저 컨텍스트에 실제로 있다
#
# (b) 를 계약에 넣은 이유는 그것이 "진짜 로그인이 일어났다" 의 유일한 증거이고,
# ISSUE-55 의 WS 인증이 바로 그 쿠키에 의존하기 때문이다. 쿠키가 없으면
# 로그아웃 후 **실제로** 로그인한다 — 서버가 인증됨이라고 말하더라도.
#
# 고정 `wait_for_timeout` 은 전부 관측 가능한 상태 대기로 바꿨다.
#
# **Out of scope**: `auth.get_cookie_manager()` 전역 싱글턴 제거. 프로덕션 변경이고
# 보안 영향(새 브라우저 세션이 직전 사용자로 복원)이 있어 별건이다. 이 하네스는
# 그 성질이 있는 채로도 결정적으로 동작한다.
# ---------------------------------------------------------------------------
SIDEBAR = '[data-testid="stSidebar"]'
_ACCOUNT_RE = re.compile(r"아이디\s*[:：]\s*([A-Za-z0-9._@-]+)")

# 로그인 폼 / 사이드바가 Streamlit 리런을 거쳐 나타날 때까지의 상한. 고정 sleep
# 대신 이 값을 상한으로 두고 **관측 가능한 상태**를 기다린다.
_UI_TIMEOUT_MS = 20000

# 컴포넌트 iframe 의 **인라인 스크립트 평가**까지의 상한. UI 상한보다 넉넉한
# 이유는 이 대기가 "Streamlit 리런 → 컴포넌트 마운트 → 1400줄 인라인 스크립트
# 평가" 전체 뒤에 오기 때문이다. GitHub 러너에서 20초가 한 번 부족했다
# (`updateFsFont 미정의` fixture error 1건). 로컬에서는 재현되지 않는다.
_COMPONENT_READY_TIMEOUT_MS = 45000


def sidebar_account(page) -> str | None:
    """사이드바가 표시하는 현재 계정명 (판정 불가면 None).

    `auth.display_user_info` 가 `**아이디**: {username}` 을 쓴다.
    `text_content` 로 읽어 **사이드바가 접혀 있어도** 값을 얻는다 —
    `inner_text` 는 보이지 않는 요소에서 빈 문자열을 준다.
    """
    sidebar = page.locator(SIDEBAR)
    try:
        if sidebar.count() == 0:
            return None
        text = sidebar.first.text_content(timeout=2000) or ""
    except PlaywrightTimeoutError:
        return None
    match = _ACCOUNT_RE.search(text)
    return match.group(1) if match else None


# 이전 이름 — 이 파일 내부 호출용
_sidebar_account = sidebar_account


def _has_session_cookie(page) -> bool:
    return any(c["name"] == "user_session" for c in page.context.cookies())


def _wait_for_app_shell(page) -> None:
    """로그인 폼 또는 로그아웃 버튼 중 **하나**가 나타날 때까지 기다린다.

    둘 중 어느 것도 없는 중간 상태가 Streamlit 리런 도중에 존재하므로, 특정
    하나만 기다리면 반대 상태에서 영원히 기다린다. 고정 sleep 이 하던 역할을
    여기서 상태로 대체한다.
    """
    page.wait_for_function(
        """() => {
            const form = document.querySelector('input[aria-label="사용자명"]');
            const buttons = Array.from(document.querySelectorAll('button'));
            const logout = buttons.some((b) => b.innerText.trim() === '로그아웃');
            return !!form || logout;
        }""",
        timeout=_UI_TIMEOUT_MS,
    )


def _submit_login(page, username: str, password: str) -> None:
    username_input = page.locator('input[aria-label="사용자명"]')
    username_input.wait_for(state="visible", timeout=_UI_TIMEOUT_MS)
    username_input.fill(username)
    password_input = page.locator('input[aria-label="비밀번호"]')
    password_input.wait_for(state="visible", timeout=_UI_TIMEOUT_MS)
    password_input.fill(password)
    page.locator('button:has-text("로그인")').click()


def _logout(page) -> None:
    """로그아웃하고 로그인 폼이 돌아올 때까지 기다린다."""
    logout = page.get_by_role("button", name="로그아웃")
    if logout.count() == 0:
        return
    logout.click()
    page.locator('input[aria-label="사용자명"]').wait_for(
        state="visible", timeout=_UI_TIMEOUT_MS
    )


def ensure_logged_in_as(
    page, username: str, password: str, *, require_cookie: bool = False
) -> None:
    """`username` 으로 **검증된** 세션을 만든다.

    서버가 "이미 인증됨" 이라고 말하더라도 계정이 다르면 로그아웃하고 실제로
    로그인한다. 전역 `CookieManager` 싱글턴이 있는 채로도 **신원이** 결정적이게
    만드는 지점이다.

    `require_cookie=True` 면 이 브라우저 컨텍스트에 `user_session` 쿠키가
    실제로 있을 것까지 요구하고, 없으면 로그아웃 후 재로그인해 쿠키를 심는다.

    **기본값이 `False` 인 이유 — CI 가 가르쳐 줬다.** 처음엔 쿠키 조건을 모든
    테스트에 걸었다. 전역 싱글턴 때문에 새 브라우저 컨텍스트에는 쿠키가 없으므로
    결과적으로 **198개 테스트 전부가 매번 로그아웃 → 재로그인**을 했다. 로컬
    3회 연속 198 passed 였지만 GitHub 러너에서는 그 리런 폭풍이 컴포넌트 iframe
    마운트를 불안정하게 만들어, 스크립트 준비 대기가 45초에도 끝나지 않는
    fixture error 가 났다 (상한을 올려도 줄지 않았다 — 느림이 아니라 churn 이
    원인이다).

    쿠키가 **반드시** 필요한 것은 ISSUE-55 의 전제 실측 3건뿐이다. 그 3건만
    `freshly_logged_in_page` 로 비용을 치르고, 나머지는 신원 검증까지만 한다.
    """
    _wait_for_app_shell(page)

    needs_login = (
        page.locator('input[aria-label="사용자명"]').count() > 0
        or _sidebar_account(page) != username
        or (require_cookie and not _has_session_cookie(page))
    )

    if needs_login:
        if page.locator('input[aria-label="사용자명"]').count() == 0:
            # 인증된 것처럼 보이지만 계정이 다르거나 쿠키가 없다 — 실제 로그인을
            # 강제하기 위해 먼저 로그아웃한다.
            _logout(page)
        _submit_login(page, username, password)

        # 로그인 결과를 관측 가능한 상태로 기다린다 — 사이드바에 계정명이 뜨고
        # 쿠키가 심길 때까지. CookieManager 는 컴포넌트 왕복을 거치므로 계정명이
        # 먼저 보이고 쿠키가 조금 늦게 도착한다.
        page.wait_for_function(
            """(expected) => {
                const sidebar = document.querySelector('[data-testid="stSidebar"]');
                if (!sidebar) return false;
                // `innerText` 가 아니라 `textContent` — 사이드바가 **접혀 있으면**
                // innerText 는 빈 문자열이라 이 대기가 끝나지 않는다.
                // `sidebar_account()` 는 같은 이유로 이미 text_content 를 쓰는데
                // 이 자리만 어긋나 있었다 (require_cookie=True 로 로그인을
                // 강제하는 경로에서 20초 타임아웃으로 드러났다).
                //
                // 문자 클래스도 `\\S+` 가 아니라 `[A-Za-z0-9._@-]+` 여야 한다.
                // textContent 는 인접 요소를 **구분자 없이** 이어 붙이므로
                // ("e2e_operator로그아웃") `\\S+` 는 뒤의 한글까지 삼켜 비교가
                // 영원히 실패한다. innerText 는 블록 사이에 개행을 넣어 줘서
                // `\\S+` 가 우연히 동작했던 것이고, textContent 로 바꾸는 순간
                // 그 우연이 사라진다. 파이썬측 `_ACCOUNT_RE` 와 같은 클래스다.
                const m = sidebar.textContent.match(
                    /아이디\\s*[:：]\\s*([A-Za-z0-9._@-]+)/
                );
                return !!m && m[1] === expected;
            }""",
            arg=username,
            timeout=_UI_TIMEOUT_MS,
        )

    # AC2 — 이 단언은 안정화 **전** 코드에서 실패한다. 측정 당시 모든 컨텍스트가
    # `cookies=['_streamlit_xsrf']` 였으므로 쿠키 조건이 곧바로 깨진다.
    assert _sidebar_account(page) == username, (
        f"로그인 하네스가 {username!r} 가 아닌 "
        f"{_sidebar_account(page)!r} 세션을 돌려줬다"
    )


def _wait_for_component_script(page) -> None:
    """컴포넌트 iframe 의 **인라인 스크립트가 평가될 때까지** 기다린다.

    `iframe` 이 `attached` 인 것만으로는 부족하다. `components/webrtc.html` 은
    `#viewer` 를 `:812` 에, `function updateFsFont` 를 `:1380` 에 두므로 DOM 이
    붙은 시점에 스크립트가 아직 안 돌았을 수 있다.

    **이 대기는 ISSUE-64 가 만든 회귀를 막는다.** 매 테스트에서 로그아웃 →
    재로그인을 하게 되면서 컴포넌트 iframe 이 **새로 마운트**되는데, 기존
    fixture 는 `attached` 까지만 기다렸다. CI(로컬보다 느림)에서
    `test_fullscreen_e2e.py` 5건이 `ReferenceError: updateFsFont is not defined`
    와 `Frame was detached` 로 깨졌다 — 로컬 3회 실행에서는 재현되지 않았고
    CI 가 잡았다.

    준비 신호로 `updateFsFont` 를 쓰는 이유는 그것이 **스크립트의 끝부분**에
    선언된 함수여서, 존재하면 인라인 스크립트가 끝까지 평가됐다는 뜻이기
    때문이다. 함수 선언은 호이스팅되므로 선언 위치까지 평가가 도달하면 바로
    보인다.

    부모 JS 로 `contentWindow` 를 들여다볼 수는 **없다** — srcdoc 프레임의
    origin 이 `"null"`(opaque) 이라 접근이 차단된다. Playwright 의 Frame API 로
    프레임 자신의 컨텍스트에서 확인한다.
    """
    deadline = time.time() + _COMPONENT_READY_TIMEOUT_MS / 1000
    while time.time() < deadline:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                ready = frame.evaluate(
                    "() => typeof updateFsFont === 'function'",
                )
            except Exception:
                # 프레임이 교체되는 중이면 evaluate 가 던진다 — 다시 돈다.
                continue
            if ready:
                return
        time.sleep(0.1)

    raise AssertionError(
        "컴포넌트 iframe 의 스크립트가 준비되지 않았다 (updateFsFont 미정의). "
        "오퍼레이터 화면이 렌더되지 않았거나 스크립트가 깨졌다."
    )


def open_page_as(
    page,
    url: str,
    username: str,
    password: str = TEST_PASSWORD,
    *,
    clear_cookies: bool = True,
):
    """`url` 을 열고 **정확히** `username` 계정으로 들어간다.

    `test_admin_stage_config_e2e.py` 의 `_open_dashboard_as` 가 여기로 승격됐다.
    그 파일은 전역 `CookieManager` 문제를 **로컬에서 이미 해결해 두었는데**,
    `conftest.logged_in_page` 는 그 수정을 받지 못한 채 "로그인 폼이 보이면
    로그인" 에 머물러 있었다 — 정확히 RL-001 이 경고하는 드리프트다. 한쪽만
    고쳐진 사본이 둘 있으면 고쳐지지 않은 쪽에서 플레이키가 계속 난다.

    `clear_cookies=False` 는 새로고침(같은 브라우저 세션 유지) 용도다.
    """
    if clear_cookies:
        page.context.clear_cookies()
    page.goto(url, wait_until="domcontentloaded")
    ensure_logged_in_as(page, username, password)
    return page


@pytest.fixture()
def logged_in_page(page, streamlit_server):
    """`e2e_operator` 로 **검증된** 세션의 Playwright page.

    operator 로 들어가는 이유는 캡션 컴포넌트가 operator 역할에서만 렌더되기
    때문이다 (#101). admin 세션을 물려받으면 iframe 이 영원히 나타나지 않고
    20초 뒤 fixture error 가 된다.
    """
    page.goto(streamlit_server, wait_until="domcontentloaded")
    ensure_logged_in_as(page, TEST_OPERATOR_USERNAME, TEST_PASSWORD)

    # 메인 페이지 로드 대기 (iframe이 나타날 때까지)
    #
    # **`_wait_for_component_script` 를 호출하지 않는다.** 그 게이트는 ISSUE-64
    # 중간 설계가 **모든 테스트에서 로그아웃 → 재로그인**을 하던 시절, 컴포넌트
    # iframe 이 매번 리마운트되며 생긴 `ReferenceError: updateFsFont is not
    # defined` 를 막기 위한 것이었다. 그 churn 은 쿠키 요구를 걷어내며 **원인째**
    # 제거됐는데 게이트만 남아, 원인이 없어진 뒤에도 방어가 **새 실패면**으로
    # 작동했다 — main 에서 `test_fullscreen_e2e.py` 가 45초 대기 초과로 fixture
    # error 를 냈다 (197 passed, 1 error).
    #
    # 원인을 없앤 뒤 방어를 겹쳐 두면, 그 방어가 깨질 때 원래 없던 실패가 생긴다.
    # 게이트 자체는 `freshly_logged_in_page`(리마운트를 실제로 일으키는 경로)에만
    # 남긴다.
    page.locator("iframe").first.wait_for(state="attached", timeout=_UI_TIMEOUT_MS)

    # **teardown 로그아웃을 두지 않는다.**
    #
    # 처음엔 "세션 누수 제거" 목적으로 넣었는데 두 가지 이유로 뺀다.
    #
    # 1. **효과가 중복이다.** `ensure_logged_in_as` 가 진입 시점에 계정을 확인하고
    #    다르면 로그아웃 후 재로그인하므로, 누수가 다음 테스트를 오염시킬 수 없다.
    # 2. **teardown 의 로그아웃이 Streamlit 리런을 일으켜 컴포넌트 iframe 을
    #    분리한다.** 그것이 CI 에서 `test_fullscreen_e2e.py` 2건을
    #    `Frame.evaluate: Frame was detached` 로 깨뜨렸다. 이 스위트에서
    #    iframe 리마운트는 반복적으로 실패의 원천이었다 — 같은 파일이 게이트가
    #    있으면 `updateFsFont 미정의`, 없으면 `Frame was detached` 로 깨졌다.
    #
    # 이득 없는 방어를 위해 리마운트 기회를 늘리지 않는다.
    return page


@pytest.fixture()
def freshly_logged_in_page(page, streamlit_server):
    """`user_session` 쿠키가 **이 브라우저 컨텍스트에 실제로 있는** 세션.

    ISSUE-55 의 전제 실측(WS 핸드셰이크에 쿠키가 실리는가)만 이 fixture 를
    쓴다. 전역 `CookieManager` 싱글턴 때문에 쿠키를 보장하려면 로그아웃 후
    실제 로그인이 필요하고, 그 비용(Streamlit 리런 왕복)을 **198개 전부**에
    물리면 CI 에서 컴포넌트 마운트가 불안정해진다 — 실측으로 확인했다.
    그래서 필요한 소수만 치른다.
    """
    page.goto(streamlit_server, wait_until="domcontentloaded")
    ensure_logged_in_as(
        page, TEST_OPERATOR_USERNAME, TEST_PASSWORD, require_cookie=True
    )
    page.locator("iframe").first.wait_for(state="attached", timeout=_UI_TIMEOUT_MS)
    _wait_for_component_script(page)

    yield page

    with contextlib.suppress(Exception):
        _logout(page)


@pytest.fixture()
def caption_iframe(logged_in_page):
    """로그인 후 캡션 iframe의 FrameLocator 반환"""
    return logged_in_page.frame_locator("iframe").first
