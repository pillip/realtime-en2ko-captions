"""
auth.py 단위 테스트
generate_session_token, check_usage_limit 등 Streamlit 비의존 함수 테스트
"""

from unittest.mock import MagicMock, patch

import pytest


# auth.py는 streamlit을 임포트하므로 mock 처리 필요
@pytest.fixture(autouse=True)
def mock_streamlit(monkeypatch):
    """모든 테스트에서 streamlit과 extra_streamlit_components mock"""
    import sys

    mock_st = MagicMock()
    mock_st.session_state = {}
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
        mock_streamlit.session_state = {
            "authenticated": True,
            "user": {"role": "admin", "is_active": True},
        }

        from auth import is_admin

        assert is_admin() is True

    def test_regular_user(self, mock_streamlit):
        """일반 사용자는 관리자 아님"""
        mock_streamlit.session_state = {
            "authenticated": True,
            "user": {"role": "user", "is_active": True},
        }

        from auth import is_admin

        assert is_admin() is False

    def test_not_authenticated(self, mock_streamlit):
        """미인증 시 관리자 아님"""
        mock_streamlit.session_state = {"authenticated": False, "user": None}

        from auth import is_admin

        assert is_admin() is False


class TestIsAuthenticated:
    def test_authenticated(self, mock_streamlit):
        """인증된 상태"""
        mock_streamlit.session_state = {"authenticated": True}

        from auth import is_authenticated

        assert is_authenticated() is True

    def test_not_authenticated(self, mock_streamlit):
        """미인증 상태"""
        mock_streamlit.session_state = {"authenticated": False}

        from auth import is_authenticated

        assert is_authenticated() is False

    def test_no_key(self, mock_streamlit):
        """authenticated 키 없는 경우"""
        mock_streamlit.session_state = {}

        from auth import is_authenticated

        assert is_authenticated() is False


class TestGetCurrentUser:
    def test_returns_user_when_authenticated(self, mock_streamlit):
        """인증 시 사용자 정보 반환"""
        user_data = {"id": 1, "username": "testuser", "role": "user"}
        mock_streamlit.session_state = {"authenticated": True, "user": user_data}

        from auth import get_current_user

        result = get_current_user()
        assert result == user_data

    def test_returns_none_when_not_authenticated(self, mock_streamlit):
        """미인증 시 None 반환"""
        mock_streamlit.session_state = {"authenticated": False, "user": None}

        from auth import get_current_user

        result = get_current_user()
        assert result is None
