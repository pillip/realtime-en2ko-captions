"""
auth.py 단위 테스트
generate_session_token, check_usage_limit, restore_session_from_cookie,
login_user, logout_user, require_auth, require_admin 등 테스트
"""

from unittest.mock import MagicMock, patch

import pytest


class SessionState(dict):
    """Streamlit session_state 모방: dict + attribute 접근 모두 지원"""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError:
            raise AttributeError(key)


# auth.py는 streamlit을 임포트하므로 mock 처리 필요
@pytest.fixture(autouse=True)
def mock_streamlit(monkeypatch):
    """모든 테스트에서 streamlit과 extra_streamlit_components mock"""
    import sys

    mock_st = MagicMock()
    mock_st.session_state = SessionState()
    mock_esc = MagicMock()

    monkeypatch.setitem(sys.modules, "streamlit", mock_st)
    monkeypatch.setitem(sys.modules, "extra_streamlit_components", mock_esc)

    # auth 모듈이 이미 로드되어 있으면 제거 (재로드를 위해)
    sys.modules.pop("auth", None)

    yield mock_st


@pytest.fixture
def db_path(tmp_path):
    """임시 데이터베이스 경로"""
    return str(tmp_path / "test_auth.db")


@pytest.fixture
def mock_db(db_path):
    """테스트용 데이터베이스"""
    from database import DatabaseManager, User

    db = DatabaseManager(db_path)
    user_model = User(db)
    user_model.create_user(
        username="testuser",
        password="testpass",
        role="user",
        usage_limit_seconds=3600,
    )
    user_model.create_user(
        username="admin",
        password="adminpass",
        role="admin",
        usage_limit_seconds=0,
    )
    return db


@pytest.fixture
def mock_db_with_inactive(mock_db):
    """비활성 사용자가 포함된 데이터베이스"""
    from database import User

    user_model = User(mock_db)
    uid = user_model.create_user(
        username="inactive_user",
        password="inactivepass",
        role="user",
        usage_limit_seconds=3600,
    )
    user_model.update_user(uid, is_active=False)
    return mock_db


@pytest.fixture
def mock_cookie_manager():
    """CookieManager mock"""
    cm = MagicMock()
    cm.get.return_value = None
    cm.set = MagicMock()
    cm.delete = MagicMock()
    return cm


class TestGenerateSessionToken:
    def test_returns_string(self):
        """세션 토큰이 문자열로 반환"""
        from auth import generate_session_token

        token = generate_session_token(1, "testuser")
        assert isinstance(token, str)

    def test_token_length(self):
        """토큰 길이가 32자"""
        from auth import generate_session_token

        token = generate_session_token(1, "testuser")
        assert len(token) == 32

    def test_different_users_different_tokens(self):
        """다른 사용자는 다른 토큰 생성"""
        from auth import generate_session_token

        token1 = generate_session_token(1, "user1")
        token2 = generate_session_token(2, "user2")
        assert token1 != token2

    def test_token_is_hex(self):
        """토큰이 16진수 문자열"""
        from auth import generate_session_token

        token = generate_session_token(1, "testuser")
        # 16진수 변환 가능해야 함
        int(token, 16)


class TestCheckUsageLimit:
    def test_admin_always_allowed(self, mock_db):
        """관리자는 항상 사용 가능"""
        from auth import check_usage_limit

        admin_info = {"id": 2, "username": "admin", "role": "admin"}
        result = check_usage_limit(9999, user_info=admin_info)
        assert result is True

    def test_user_within_limit(self, mock_db):
        """제한 내 사용자 허용"""
        from database import User

        user_model = User(mock_db)
        user = user_model.get_user_by_username("testuser")

        from auth import check_usage_limit

        with patch("auth.get_user_model", return_value=user_model):
            user_info = {"id": user["id"], "username": "testuser", "role": "user"}
            result = check_usage_limit(100, user_info=user_info)
            assert result is True

    def test_user_exceeds_limit(self, mock_db):
        """제한 초과 사용자 거부"""
        from database import User

        user_model = User(mock_db)
        user = user_model.get_user_by_username("testuser")

        # 사용량을 제한에 가깝게 설정
        user_model.add_usage(user["id"], 3500)

        from auth import check_usage_limit

        with patch("auth.get_user_model", return_value=user_model):
            user_info = {"id": user["id"], "username": "testuser", "role": "user"}
            result = check_usage_limit(200, user_info=user_info)
            assert result is False

    def test_user_exact_limit(self, mock_db):
        """정확히 남은 시간만큼 요청 시 허용"""
        from database import User

        user_model = User(mock_db)
        user = user_model.get_user_by_username("testuser")

        user_model.add_usage(user["id"], 3500)

        from auth import check_usage_limit

        with patch("auth.get_user_model", return_value=user_model):
            user_info = {"id": user["id"], "username": "testuser", "role": "user"}
            # 남은 시간: 3600 - 3500 = 100
            result = check_usage_limit(100, user_info=user_info)
            assert result is True


class TestIsAdmin:
    def test_admin_user(self, mock_streamlit):
        """관리자 사용자 확인"""
        mock_streamlit.session_state = SessionState(
            {"authenticated": True, "user": {"role": "admin", "is_active": True}}
        )

        from auth import is_admin

        assert is_admin() is True

    def test_regular_user(self, mock_streamlit):
        """일반 사용자는 관리자 아님"""
        mock_streamlit.session_state = SessionState(
            {"authenticated": True, "user": {"role": "user", "is_active": True}}
        )

        from auth import is_admin

        assert is_admin() is False

    def test_not_authenticated(self, mock_streamlit):
        """미인증 시 관리자 아님"""
        mock_streamlit.session_state = SessionState(
            {"authenticated": False, "user": None}
        )

        from auth import is_admin

        assert is_admin() is False


class TestIsAuthenticated:
    def test_authenticated(self, mock_streamlit):
        """인증된 상태"""
        mock_streamlit.session_state = SessionState({"authenticated": True})

        from auth import is_authenticated

        assert is_authenticated() is True

    def test_not_authenticated(self, mock_streamlit):
        """미인증 상태"""
        mock_streamlit.session_state = SessionState({"authenticated": False})

        from auth import is_authenticated

        assert is_authenticated() is False

    def test_no_key(self, mock_streamlit):
        """authenticated 키 없는 경우"""
        mock_streamlit.session_state = SessionState()

        from auth import is_authenticated

        assert is_authenticated() is False


class TestGetCurrentUser:
    def test_returns_user_when_authenticated(self, mock_streamlit):
        """인증 시 사용자 정보 반환"""
        user_data = {"id": 1, "username": "testuser", "role": "user"}
        mock_streamlit.session_state = SessionState(
            {"authenticated": True, "user": user_data}
        )

        from auth import get_current_user

        result = get_current_user()
        assert result == user_data

    def test_returns_none_when_not_authenticated(self, mock_streamlit):
        """미인증 시 None 반환"""
        mock_streamlit.session_state = SessionState(
            {"authenticated": False, "user": None}
        )

        from auth import get_current_user

        result = get_current_user()
        assert result is None


class TestRestoreSessionFromCookie:
    """restore_session_from_cookie 테스트"""

    def test_valid_cookie_active_user_returns_true(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """유효한 쿠키 + 활성 사용자 -> authenticated=True, returns True"""
        from database import User

        user_model = User(mock_db)
        user = user_model.get_user_by_username("testuser")

        # Cookie format: "user_id:username:token"
        mock_cookie_manager.get.return_value = f"{user['id']}:testuser:faketoken123"

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is True
        assert mock_streamlit.session_state["authenticated"] is True
        assert mock_streamlit.session_state["user"]["username"] == "testuser"

    def test_cookie_for_inactive_user_returns_false(
        self, mock_streamlit, mock_db_with_inactive, mock_cookie_manager
    ):
        """비활성 사용자 쿠키 -> returns False"""
        from database import User

        user_model = User(mock_db_with_inactive)
        inactive = user_model.get_user_by_username("inactive_user")

        mock_cookie_manager.get.return_value = (
            f"{inactive['id']}:inactive_user:faketoken"
        )

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False

    def test_no_cookie_returns_false(self, mock_streamlit, mock_cookie_manager):
        """쿠키 없는 경우 -> returns False"""
        mock_cookie_manager.get.return_value = None

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False

    def test_cookie_for_nonexistent_user_returns_false(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """존재하지 않는 사용자 쿠키 -> returns False"""
        from database import User

        user_model = User(mock_db)
        mock_cookie_manager.get.return_value = "9999:ghost:faketoken"

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False

    def test_cookie_username_mismatch_returns_false(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """쿠키의 username이 DB와 다른 경우 -> returns False"""
        from database import User

        user_model = User(mock_db)
        user = user_model.get_user_by_username("testuser")

        # user_id matches but username is different
        mock_cookie_manager.get.return_value = f"{user['id']}:wrongname:faketoken"

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False

    def test_malformed_cookie_returns_false(self, mock_streamlit, mock_cookie_manager):
        """잘못된 형식의 쿠키 -> returns False (no crash)"""
        mock_cookie_manager.get.return_value = "malformed-cookie-no-colons"

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False


class TestLoginUser:
    """login_user 테스트"""

    def test_valid_credentials_returns_true(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """유효한 자격 증명 -> returns True, session authenticated"""
        from database import User

        user_model = User(mock_db)

        with (
            patch("auth.get_user_model", return_value=user_model),
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
        ):
            from auth import login_user

            result = login_user("testuser", "testpass")

        assert result is True
        assert mock_streamlit.session_state["authenticated"] is True
        assert mock_streamlit.session_state["user"]["username"] == "testuser"

    def test_invalid_password_returns_false(self, mock_streamlit, mock_db):
        """잘못된 비밀번호 -> returns False"""
        from database import User

        user_model = User(mock_db)

        with patch("auth.get_user_model", return_value=user_model):
            from auth import login_user

            result = login_user("testuser", "wrongpassword")

        assert result is False

    def test_nonexistent_user_returns_false(self, mock_streamlit, mock_db):
        """존재하지 않는 사용자 -> returns False"""
        from database import User

        user_model = User(mock_db)

        with patch("auth.get_user_model", return_value=user_model):
            from auth import login_user

            result = login_user("nonexistent", "anypass")

        assert result is False

    def test_login_sets_cookie(self, mock_streamlit, mock_db, mock_cookie_manager):
        """로그인 성공 시 쿠키 설정"""
        from database import User

        user_model = User(mock_db)

        with (
            patch("auth.get_user_model", return_value=user_model),
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
        ):
            from auth import login_user

            login_user("testuser", "testpass")

        mock_cookie_manager.set.assert_called_once()
        call_args = mock_cookie_manager.set.call_args
        assert call_args[0][0] == "user_session"
        assert "testuser" in call_args[0][1]

    def test_login_sets_cookie_restore_attempted(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """로그인 성공 시 cookie_restore_attempted = True"""
        from database import User

        user_model = User(mock_db)

        with (
            patch("auth.get_user_model", return_value=user_model),
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
        ):
            from auth import login_user

            login_user("testuser", "testpass")

        assert mock_streamlit.session_state["cookie_restore_attempted"] is True


class TestLogoutUser:
    """logout_user 테스트"""

    def test_logout_clears_session(self, mock_streamlit, mock_cookie_manager):
        """로그아웃 시 세션 초기화"""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1, "username": "testuser"},
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import logout_user

            logout_user()

        assert mock_streamlit.session_state["authenticated"] is False
        assert mock_streamlit.session_state["user"] is None
        assert mock_streamlit.session_state["cookie_restore_attempted"] is False

    def test_logout_deletes_cookie(self, mock_streamlit, mock_cookie_manager):
        """로그아웃 시 쿠키 삭제"""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1},
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import logout_user

            logout_user()

        mock_cookie_manager.delete.assert_called_once_with("user_session")


class TestRequireAuth:
    """@require_auth 데코레이터 테스트"""

    def test_authenticated_active_user_proceeds(
        self, mock_streamlit, mock_cookie_manager
    ):
        """인증된 활성 사용자 -> 함수 정상 실행"""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1, "role": "user", "is_active": True},
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_auth

            @require_auth
            def protected_func():
                return "success"

            result = protected_func()

        assert result == "success"
        mock_streamlit.warning.assert_not_called()

    def test_unauthenticated_user_gets_warning(
        self, mock_streamlit, mock_cookie_manager
    ):
        """미인증 사용자 -> st.warning 호출 + st.stop 호출"""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": False,
                "user": None,
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_auth

            @require_auth
            def protected_func():
                return "should not reach"

            protected_func()

        mock_streamlit.warning.assert_called_once()
        assert "로그인" in mock_streamlit.warning.call_args[0][0]
        mock_streamlit.stop.assert_called()

    def test_inactive_user_gets_error(self, mock_streamlit, mock_cookie_manager):
        """비활성 사용자 -> st.error 호출 + st.stop 호출"""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1, "role": "user", "is_active": False},
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_auth

            @require_auth
            def protected_func():
                return "should not reach"

            protected_func()

        mock_streamlit.error.assert_called_once()
        assert "비활성" in mock_streamlit.error.call_args[0][0]
        mock_streamlit.stop.assert_called()


class TestRequireAdmin:
    """@require_admin 데코레이터 테스트"""

    def test_admin_user_proceeds(self, mock_streamlit, mock_cookie_manager):
        """관리자 -> 함수 정상 실행"""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 2, "role": "admin", "is_active": True},
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_admin

            @require_admin
            def admin_func():
                return "admin_success"

            result = admin_func()

        assert result == "admin_success"
        mock_streamlit.error.assert_not_called()

    def test_regular_user_gets_error(self, mock_streamlit, mock_cookie_manager):
        """일반 사용자(role='user') -> st.error 호출 + st.stop 호출"""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1, "role": "user", "is_active": True},
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_admin

            @require_admin
            def admin_func():
                return "should not reach"

            admin_func()

        mock_streamlit.error.assert_called_once()
        assert "관리자" in mock_streamlit.error.call_args[0][0]
        mock_streamlit.stop.assert_called()

    def test_unauthenticated_user_gets_warning(
        self, mock_streamlit, mock_cookie_manager
    ):
        """미인증 사용자 -> st.warning 호출 + st.stop 호출"""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": False,
                "user": None,
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_admin

            @require_admin
            def admin_func():
                return "should not reach"

            admin_func()

        mock_streamlit.warning.assert_called_once()
        mock_streamlit.stop.assert_called()
