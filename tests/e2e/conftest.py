"""
E2E 테스트 공통 fixture
Streamlit 서버를 subprocess로 시작하고, 테스트용 DB/계정을 설정
"""

import os
import shutil
import subprocess
import tempfile
import time

import httpx
import pytest

# 테스트 계정 정보
TEST_USERNAME = "e2e_admin"
TEST_PASSWORD = "e2e_test_pass_123"

# Streamlit 서버 포트
STREAMLIT_PORT = 8599


@pytest.fixture(scope="session")
def _tmp_db_dir():
    """임시 DB 디렉토리"""
    d = tempfile.mkdtemp(prefix="e2e_db_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session")
def streamlit_server(_tmp_db_dir):
    """Streamlit 서버를 시작하고 URL을 반환"""
    db_path = os.path.join(_tmp_db_dir, "test.db")
    env = {
        **os.environ,
        "DB_PATH": db_path,
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

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture()
def logged_in_page(page, streamlit_server):
    """로그인 완료된 Playwright page를 반환"""
    page.goto(streamlit_server, wait_until="domcontentloaded")
    # Streamlit은 WebSocket을 사용하므로 networkidle 대신 domcontentloaded 사용
    page.wait_for_timeout(3000)

    # CookieManager 전역 싱글톤으로 인해 첫 로그인 이후
    # 동일 서버의 새 세션에서도 인증 상태가 유지될 수 있음
    # 따라서 로그인 폼 존재 여부를 먼저 확인
    username_input = page.locator('input[aria-label="사용자명"]')
    login_needed = username_input.count() > 0

    if not login_needed:
        # 추가 대기 후 재확인 (Streamlit 렌더링 지연 대응)
        page.wait_for_timeout(3000)
        login_needed = username_input.count() > 0

    if login_needed:
        username_input.fill(TEST_USERNAME)
        password_input = page.locator('input[aria-label="비밀번호"]')
        password_input.wait_for(state="visible", timeout=5000)
        password_input.fill(TEST_PASSWORD)
        page.locator('button:has-text("로그인")').click()

    # 메인 페이지 로드 대기 (iframe이 나타날 때까지)
    page.locator("iframe").first.wait_for(state="attached", timeout=20000)

    return page


@pytest.fixture()
def caption_iframe(logged_in_page):
    """로그인 후 캡션 iframe의 FrameLocator 반환"""
    return logged_in_page.frame_locator("iframe").first
