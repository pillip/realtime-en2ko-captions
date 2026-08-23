"""
ISSUE-39 — 관리자 무대 설정 폼 e2e (browser-driven).

단위 테스트로는 닿을 수 없는 AC 들을 실제 브라우저에서 확인한다:

  1. **AC 1** — admin 이 룸 관리 탭을 열면 "🎬 무대 화면 설정" 섹션과 룸
     선택 드롭다운이 실제로 렌더된다. ``_render_admin_room_stage_config`` 를
     직접 호출하는 단위 테스트는 이 섹션이 로그인 → 대시보드 → 탭 경로에
     실제로 연결돼 있는지까지는 증명하지 못한다.
  2. **AC 6 (RL-002)** — 같은 탭을 role="operator" 로 열면 섹션이 DOM 에
     존재조차 하지 않는다. 부재 단언만 있으면 "아무에게도 렌더되지 않는"
     회귀에도 그대로 통과하므로(RL-004), 같은 파일에서 admin 가시성을 함께
     단언해 부재 단언이 판별력을 갖게 한다. 오퍼레이터 탭이 그냥 비어서
     통과하는 경로도 배제한다 — 시드 룸은 오퍼레이터에게 배정돼 있고,
     그 룸의 QR 섹션이 보이는 상태에서만 섹션 부재를 단언한다.
  3. **AC 2** — 저장 후 페이지를 새로고침하면 같은 값이 폼 입력란에 복원된다.
     DB 왕복은 단위 테스트가 보지만, "새 세션에서 위젯이 DB 값으로 다시
     채워지는가"(``_seed_stage_form_state``) 는 브라우저에서만 확인된다.
  4. **AC 7** — 무대 URL(`/stage/{room_id}`) 텍스트와 QR PNG 다운로드 버튼.

덤: 자막 컬럼 비율 라디오가 `1/4` · `1/3` 두 값을 노출하는지.

fixture 는 이 파일 안에 둔다. e2e 게이트가 ``tests/`` 아래의 모든 ``.py``
변경분에 ``def test_*`` 를 요구해서 conftest.py 에는 넣을 수 없다. 대신
로그인 절차 정의는 이 파일 안에서 한 번만 하고(``_open_dashboard_as``),
admin/operator fixture 가 그것을 공유한다 (RL-001).
``e2e`` 마크는 기본 deselect 되어 일반 ``pytest -q`` 실행을 막지 않는다.
"""

from __future__ import annotations

import os
import re
import time

import pytest

from tests.e2e.conftest import (
    TEST_OPERATOR_USERNAME,
    TEST_PASSWORD,
    TEST_USERNAME,
)

pytestmark = pytest.mark.e2e

# 관리자/오퍼레이터 대시보드 (pages/admin_dashboard.py → /admin_dashboard)
_DASHBOARD_PATH = "/admin_dashboard"
_SIDEBAR = '[data-testid="stSidebar"]'
# display_user_info 가 사이드바에 찍는 "**아이디**: <username>".
# text_content() 는 인접 요소를 공백 없이 이어 붙이므로("e2e_admin로그아웃")
# \S+ 로 잡으면 안 되고 계정명에 쓰이는 문자만 받아야 한다.
_SIDEBAR_USERNAME_RE = re.compile(r"아이디\s*[:：]\s*([A-Za-z0-9._@-]+)")

# admin / operator 양쪽에서 같은 로케이터로 검사해야 부재 단언이 의미를 갖는다.
_STAGE_SECTION_HEADING = "🎬 무대 화면 설정"
# 룸이 하나라도 보이는 탭에서만 렌더되는 섹션 — 오퍼레이터 탭이 비어 있지
# 않다는 증거로 쓴다 (admin.py ``_render_room_qr_section``).
_ROOM_QR_HEADING = "📱 룸 QR 코드"

_STAGE_ROOM_ID = "e2estage1"
_STAGE_ROOM_NAME = "E2E 무대 설정 룸"
_EVENT_TITLE = "2026 개발자 콘퍼런스"


# ---------------------------------------------------------------------------
# 로그인 / 계정 전환
# ---------------------------------------------------------------------------
def _sidebar_username(page):
    """사이드바에 표시된 현재 로그인 계정. 로그인 전이면 ``None``.

    ``text_content`` 로 읽어 사이드바가 접혀 있어도 값을 얻는다.
    """
    sidebar = page.locator(_SIDEBAR)
    if sidebar.count() == 0:
        return None
    match = _SIDEBAR_USERNAME_RE.search(sidebar.first.text_content() or "")
    return match.group(1) if match else None


def _login_form_present(page):
    """로그인 폼이 떠 있는지 확인 (Streamlit 렌더 지연을 한 번 더 기다린다)."""
    username_input = page.locator('input[aria-label="사용자명"]')
    if username_input.count() > 0:
        return True
    page.wait_for_timeout(3000)
    return username_input.count() > 0


def _submit_login(page, username):
    """로그인 폼을 채우고 제출한다."""
    page.locator('input[aria-label="사용자명"]').fill(username)
    password_input = page.locator('input[aria-label="비밀번호"]')
    password_input.wait_for(state="visible", timeout=5000)
    password_input.fill(TEST_PASSWORD)
    page.locator('button:has-text("로그인")').click()


def _wait_for_sidebar_username(page, username, attempts=40):
    """사이드바 계정이 ``username`` 이 될 때까지 폴링 (최대 20초)."""
    for _ in range(attempts):
        if _sidebar_username(page) == username:
            return True
        page.wait_for_timeout(500)
    return False


def _logout(page):
    """사이드바 로그아웃 후 로그인 폼이 다시 뜰 때까지 대기."""
    page.locator(f'{_SIDEBAR} button:has-text("로그아웃")').first.click()
    page.wait_for_timeout(2000)


def _open_dashboard_as(page, base_url, username, *, clear_cookies=True):
    """관리자 대시보드를 **정확히** ``username`` 계정으로 연다.

    CookieManager 가 프로세스 전역 싱글톤이라(auth.get_cookie_manager) 새
    브라우저 컨텍스트에서도 직전 계정 세션이 복원될 수 있다. "로그인 폼이
    보이면 로그인" 만으로는 다른 역할로 조용히 실행돼 역할 분기 단언이
    무의미해지므로(RL-002/RL-004), 실제로 어느 계정으로 들어왔는지 확인하고
    다르면 로그아웃 후 다시 로그인한다.

    ``clear_cookies=False`` 는 새로고침(같은 브라우저 세션 유지) 용도다.
    """
    if clear_cookies:
        page.context.clear_cookies()
    page.goto(f"{base_url}{_DASHBOARD_PATH}", wait_until="domcontentloaded")
    # Streamlit은 WebSocket을 사용하므로 networkidle 대신 domcontentloaded 사용
    page.wait_for_timeout(3000)

    for _ in range(2):
        current = _sidebar_username(page)
        if current == username:
            break
        if current is not None:
            _logout(page)
        if _login_form_present(page):
            _submit_login(page, username)
            _wait_for_sidebar_username(page, username)

    landed = _sidebar_username(page)
    assert landed == username, f"의도한 계정으로 로그인하지 못했다 (현재={landed!r})"
    return page


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _room_repo(tmp_db_dir):
    """서버와 같은 DB 파일을 보는 Room 리포지토리."""
    from database import DatabaseManager, Room

    return Room(DatabaseManager(os.path.join(tmp_db_dir, "test.db")))


@pytest.fixture(scope="session")
def stage_room(streamlit_server, _tmp_db_dir):
    """무대 설정 e2e 용 룸 — admin 이 만들고 operator 에게 배정한다.

    UI 폼 조작보다 결정적이므로 conftest 의 operator 계정 시드와 같은
    방식으로 서버와 동일한 DB 파일에 직접 넣는다. operator 배정이 핵심이다 —
    배정하지 않으면 오퍼레이터 탭이 "배정된 룸이 없습니다" 상태가 되어,
    무대 설정 섹션 부재 단언이 "탭이 비어서" 통과해 버린다 (RL-004).

    admin 계정도 여기서 보장한다. ``ADMIN_USERNAME``/``ADMIN_PASSWORD``
    부트스트랩(database.get_db_manager)은 서버의 첫 페이지 렌더 시점에야
    실행되므로, fixture 단계에서는 아직 계정이 없을 수 있다.
    """
    from database import DatabaseManager, Room, User

    dbm = DatabaseManager(os.path.join(_tmp_db_dir, "test.db"))
    user_repo = User(dbm)
    room_repo = Room(dbm)

    if user_repo.get_user_by_username(TEST_USERNAME) is None:
        user_repo.create_user(
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            role="admin",
            usage_limit_seconds=36000,
        )
    admin = user_repo.get_user_by_username(TEST_USERNAME)
    operator = user_repo.get_user_by_username(TEST_OPERATOR_USERNAME)
    assert admin is not None, "admin 계정 시드 실패"
    assert operator is not None, "operator 계정이 시드되지 않았다"

    if room_repo.get_by_id(_STAGE_ROOM_ID) is None:
        room_repo.create(
            room_id=_STAGE_ROOM_ID,
            name=_STAGE_ROOM_NAME,
            created_by=admin["id"],
            operator_id=operator["id"],
        )

    return {"id": _STAGE_ROOM_ID, "name": _STAGE_ROOM_NAME}


@pytest.fixture()
def admin_page(page, streamlit_server, stage_room):
    """admin 계정으로 관리자 대시보드에 로그인한 page."""
    return _open_dashboard_as(page, streamlit_server, TEST_USERNAME)


@pytest.fixture()
def operator_page(page, streamlit_server, stage_room):
    """operator 계정으로 같은 대시보드에 로그인한 page (AC 6)."""
    return _open_dashboard_as(page, streamlit_server, TEST_OPERATOR_USERNAME)


# ---------------------------------------------------------------------------
# 공통 로케이터 헬퍼
# ---------------------------------------------------------------------------
def _open_room_tab(page, label):
    """룸 관리 탭을 선택한다 — 탭을 고르기 전에는 패널이 보이지 않는다."""
    tab = page.get_by_role("tab", name=label)
    tab.wait_for(state="visible", timeout=20000)
    tab.click()
    page.wait_for_timeout(2000)


def _stage_section(page):
    """무대 설정 섹션 제목 로케이터 (존재/가시성 양쪽에 같은 것을 쓴다)."""
    return page.get_by_text(_STAGE_SECTION_HEADING)


def _dashboard_title(page):
    return page.locator("h1").first.inner_text()


def _wait_for_stored_title(tmp_db_dir, expected, attempts=30):
    """저장이 DB 에 반영될 때까지 폴링한다 (고정 sleep 대신)."""
    repo = _room_repo(tmp_db_dir)
    for _ in range(attempts):
        if repo.get_stage_config(_STAGE_ROOM_ID).get("event_title") == expected:
            return True
        time.sleep(0.5)
    return False


class TestStageConfigVisibleToAdmin:
    def test_admin_room_tab_shows_stage_section_with_room_select(
        self, stage_room, admin_page
    ):
        """AC 1 — 관리자 룸 관리 탭에 섹션 + 룸 선택 드롭다운이 표시된다."""
        assert "관리자 대시보드" in _dashboard_title(admin_page)
        _open_room_tab(admin_page, "룸 관리")

        heading = _stage_section(admin_page).first
        heading.wait_for(state="visible", timeout=20000)
        assert heading.inner_text().strip() == _STAGE_SECTION_HEADING

        dropdown = admin_page.locator(
            '[data-testid="stSelectbox"]', has_text="설정할 룸"
        ).first
        assert dropdown.is_visible(), "룸 선택 드롭다운이 보이지 않는다"
        # 드롭다운이 그냥 비어 있는 것이 아니라 시드 룸이 선택돼 있어야 한다.
        label = f"{stage_room['name']} ({stage_room['id']})"
        assert label in (dropdown.text_content() or "")

    def test_admin_sees_stage_url_and_qr_download_button(self, stage_room, admin_page):
        """AC 7 — `{base}/stage/{room_id}` 텍스트와 QR PNG 다운로드 버튼."""
        _open_room_tab(admin_page, "룸 관리")
        _stage_section(admin_page).first.wait_for(state="visible", timeout=20000)

        stage_path = f"/stage/{stage_room['id']}"
        url_code = admin_page.locator("code", has_text=stage_path).first
        url_code.wait_for(state="visible", timeout=10000)
        # 뷰어 QR 섹션의 `/view/` URL 과 섞이지 않았는지까지 확인한다.
        assert stage_path in url_code.inner_text()

        qr_button = admin_page.locator(
            '[data-testid="stDownloadButton"]', has_text="무대 QR PNG"
        ).first
        assert qr_button.is_visible(), "무대 QR PNG 다운로드 버튼이 보이지 않는다"

    def test_caption_ratio_radio_offers_both_supported_ratios(
        self, stage_room, admin_page
    ):
        """자막 컬럼 비율 라디오가 `1/4` 과 `1/3` 두 값을 모두 노출한다."""
        _open_room_tab(admin_page, "룸 관리")
        radio = admin_page.locator(
            '[data-testid="stRadio"]', has_text="자막 컬럼 비율"
        ).first
        radio.wait_for(state="visible", timeout=20000)

        for ratio in ("1/4", "1/3"):
            option = radio.get_by_text(ratio, exact=True).first
            assert option.is_visible(), f"{ratio} 옵션이 라디오에 없다"

    def test_saved_event_title_is_restored_after_refresh(
        self, stage_room, admin_page, streamlit_server, _tmp_db_dir
    ):
        """AC 2 — 저장 뒤 페이지를 새로고침하면 같은 값이 폼 입력란에 복원된다.

        DB 왕복만이 아니라 새 Streamlit 세션에서 위젯이 DB 값으로 다시
        채워지는지(``_seed_stage_form_state``)를 본다 — 단위 테스트는 폼
        위젯을 만들지 않으므로 이 연결을 검증할 수 없다.
        """
        _open_room_tab(admin_page, "룸 관리")

        title_input = admin_page.locator('input[aria-label="행사 타이틀"]')
        title_input.wait_for(state="visible", timeout=20000)
        title_input.fill(_EVENT_TITLE)
        admin_page.get_by_role("button", name="저장", exact=True).first.click()

        persisted = _wait_for_stored_title(_tmp_db_dir, _EVENT_TITLE)
        assert persisted, "저장이 DB 에 반영되지 않았다"

        # 새로고침 = 새 Streamlit 세션. 쿠키는 유지한 채 다시 진입한다.
        _open_dashboard_as(
            admin_page, streamlit_server, TEST_USERNAME, clear_cookies=False
        )
        _open_room_tab(admin_page, "룸 관리")

        restored = admin_page.locator('input[aria-label="행사 타이틀"]')
        restored.wait_for(state="visible", timeout=20000)
        assert restored.input_value() == _EVENT_TITLE


class TestStageConfigHiddenFromOperator:
    def test_operator_room_tab_never_renders_stage_section(
        self, stage_room, operator_page
    ):
        """AC 6 (RL-002) — operator 세션에는 섹션이 렌더되지 않는다.

        먼저 정말 operator 로 들어왔는지, 그리고 그 탭이 비어 있지 않은지를
        확인한다. 둘 중 하나라도 빠지면 아래 부재 단언은 admin 세션이나 빈
        화면에서도 통과해 아무것도 검증하지 못한다 (RL-004).
        """
        assert "오퍼레이터 대시보드" in _dashboard_title(operator_page)
        _open_room_tab(operator_page, "내 룸")

        operator_page.get_by_text(_ROOM_QR_HEADING).first.wait_for(
            state="visible", timeout=20000
        )
        view_code = operator_page.locator(
            "code", has_text=f"/view/{stage_room['id']}"
        ).first
        assert view_code.is_visible(), "오퍼레이터에게 배정된 룸이 보이지 않는다"

        assert _stage_section(operator_page).count() == 0
        assert operator_page.get_by_text("설정할 룸").count() == 0
        # 무대 URL 도 함께 새어 나가지 않아야 한다.
        stage_path = f"/stage/{stage_room['id']}"
        assert operator_page.locator("code", has_text=stage_path).count() == 0
