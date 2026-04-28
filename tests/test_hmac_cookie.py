"""
HMAC 쿠키 서명/검증 테스트 (ISSUE-10)
_sign_cookie, _verify_cookie, _get_session_secret,
set_session_cookie, restore_session_from_cookie HMAC 통합 테스트
"""

import os
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


@pytest.fixture(autouse=True)
def set_session_secret(monkeypatch):
    """테스트용 SESSION_SECRET 설정"""
    monkeypatch.setenv("SESSION_SECRET", "test-secret-key-for-hmac-signing")


@pytest.fixture
def db_path(tmp_path):
    """임시 데이터베이스 경로"""
    return str(tmp_path / "test_hmac.db")


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
    return db


@pytest.fixture
def mock_cookie_manager():
    """CookieManager mock"""
    cm = MagicMock()
    cm.get.return_value = None
    cm.set = MagicMock()
    cm.delete = MagicMock()
    return cm


class TestGetSessionSecret:
    """_get_session_secret 테스트"""

    def test_returns_env_var_when_set(self, monkeypatch):
        """SESSION_SECRET 환경변수가 설정된 경우 해당 값 반환"""
        monkeypatch.setenv("SESSION_SECRET", "my-secret")
        from auth import _get_session_secret

        assert _get_session_secret() == "my-secret"

    def test_auto_generates_when_missing(self, monkeypatch):
        """SESSION_SECRET 미설정 시 자동 생성 + 경고"""
        monkeypatch.delenv("SESSION_SECRET", raising=False)
        from auth import _get_session_secret

        with pytest.warns(UserWarning, match="SESSION_SECRET not set"):
            secret = _get_session_secret()

        assert isinstance(secret, str)
        assert len(secret) == 64  # secrets.token_hex(32) -> 64 hex chars

    def test_auto_generated_secret_stored_in_env(self, monkeypatch):
        """자동 생성된 비밀키가 환경변수에 저장"""
        monkeypatch.delenv("SESSION_SECRET", raising=False)
        from auth import _get_session_secret

        with pytest.warns(UserWarning):
            secret = _get_session_secret()

        assert os.environ.get("SESSION_SECRET") == secret


class TestSignCookie:
    """_sign_cookie 테스트"""

    def test_format_has_four_parts(self):
        """서명된 쿠키가 user_id:username:token:hmac 형식"""
        from auth import _sign_cookie

        result = _sign_cookie(1, "testuser", "abc123")
        parts = result.split(":")
        assert len(parts) == 4

    def test_contains_original_data(self):
        """서명된 쿠키에 원본 데이터 포함"""
        from auth import _sign_cookie

        result = _sign_cookie(42, "myuser", "mytoken")
        assert result.startswith("42:myuser:mytoken:")

    def test_hmac_is_hex(self):
        """HMAC 부분이 16진수 문자열"""
        from auth import _sign_cookie

        result = _sign_cookie(1, "testuser", "token123")
        hmac_part = result.rsplit(":", 1)[1]
        # HMAC-SHA256 produces 64 hex chars
        assert len(hmac_part) == 64
        int(hmac_part, 16)  # should not raise

    def test_different_data_different_hmac(self):
        """다른 데이터는 다른 HMAC 생성"""
        from auth import _sign_cookie

        cookie1 = _sign_cookie(1, "user1", "token1")
        cookie2 = _sign_cookie(2, "user2", "token2")
        hmac1 = cookie1.rsplit(":", 1)[1]
        hmac2 = cookie2.rsplit(":", 1)[1]
        assert hmac1 != hmac2

    def test_same_data_same_hmac(self):
        """같은 데이터는 같은 HMAC 생성"""
        from auth import _sign_cookie

        cookie1 = _sign_cookie(1, "user", "token")
        cookie2 = _sign_cookie(1, "user", "token")
        assert cookie1 == cookie2


class TestVerifyCookie:
    """_verify_cookie 테스트"""

    def test_valid_cookie_returns_tuple(self):
        """유효한 쿠키 검증 성공 시 (user_id, username, token) 반환"""
        from auth import _sign_cookie, _verify_cookie

        signed = _sign_cookie(1, "testuser", "mytoken")
        result = _verify_cookie(signed)
        assert result is not None
        user_id, username, token = result
        assert user_id == "1"
        assert username == "testuser"
        assert token == "mytoken"

    def test_tampered_hmac_returns_none(self):
        """HMAC이 변조된 쿠키 -> None 반환"""
        from auth import _sign_cookie, _verify_cookie

        signed = _sign_cookie(1, "testuser", "mytoken")
        # Tamper with the HMAC part
        tampered = signed[:-4] + "dead"
        result = _verify_cookie(tampered)
        assert result is None

    def test_tampered_payload_returns_none(self):
        """payload가 변조된 쿠키 -> None 반환"""
        from auth import _sign_cookie, _verify_cookie

        signed = _sign_cookie(1, "testuser", "mytoken")
        # Replace user_id in payload
        parts = signed.split(":")
        parts[0] = "999"  # tamper user_id
        tampered = ":".join(parts)
        result = _verify_cookie(tampered)
        assert result is None

    def test_missing_hmac_returns_none(self):
        """HMAC 없는 쿠키 -> None 반환"""
        from auth import _verify_cookie

        result = _verify_cookie("1:testuser:token")
        assert result is None

    def test_empty_string_returns_none(self):
        """빈 문자열 -> None 반환"""
        from auth import _verify_cookie

        result = _verify_cookie("")
        assert result is None

    def test_malformed_cookie_returns_none(self):
        """형식이 잘못된 쿠키 -> None 반환 (크래시 없음)"""
        from auth import _verify_cookie

        result = _verify_cookie("totally-invalid")
        assert result is None

    def test_wrong_secret_returns_none(self, monkeypatch):
        """다른 비밀키로 서명된 쿠키 -> None 반환"""
        from auth import _sign_cookie

        signed = _sign_cookie(1, "testuser", "mytoken")

        # Change the secret
        monkeypatch.setenv("SESSION_SECRET", "different-secret-key")

        # Need to re-import to pick up changed secret
        import sys

        sys.modules.pop("auth", None)
        from auth import _verify_cookie

        result = _verify_cookie(signed)
        assert result is None


class TestSetSessionCookieWithHmac:
    """set_session_cookie HMAC 통합 테스트"""

    def test_cookie_includes_hmac_signature(self, mock_cookie_manager):
        """set_session_cookie가 HMAC 서명이 포함된 쿠키를 설정"""
        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import set_session_cookie

            set_session_cookie({"id": 1, "username": "testuser"})

        mock_cookie_manager.set.assert_called_once()
        call_args = mock_cookie_manager.set.call_args
        cookie_value = call_args[0][1]

        # Should have 4 parts: user_id:username:token:hmac
        parts = cookie_value.split(":")
        assert len(parts) == 4
        assert parts[0] == "1"
        assert parts[1] == "testuser"
        # HMAC should be 64 hex chars
        assert len(parts[3]) == 64


class TestRestoreSessionWithHmac:
    """restore_session_from_cookie HMAC 통합 테스트"""

    def test_valid_hmac_cookie_restores_session(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """유효한 HMAC 쿠키 -> 세션 복원 성공"""
        from database import User

        user_model = User(mock_db)
        user = user_model.get_user_by_username("testuser")

        from auth import _sign_cookie

        signed = _sign_cookie(user["id"], "testuser", "faketoken")
        mock_cookie_manager.get.return_value = signed

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is True
        assert mock_streamlit.session_state["authenticated"] is True
        assert mock_streamlit.session_state["user"]["username"] == "testuser"

    def test_tampered_cookie_returns_false(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """변조된 쿠키 -> 세션 복원 실패"""
        from database import User

        user_model = User(mock_db)
        user = user_model.get_user_by_username("testuser")

        from auth import _sign_cookie

        signed = _sign_cookie(user["id"], "testuser", "faketoken")
        # Tamper with the cookie (change user_id)
        parts = signed.split(":")
        parts[0] = "999"
        tampered = ":".join(parts)
        mock_cookie_manager.get.return_value = tampered

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False

    def test_unsigned_cookie_returns_false(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """서명 없는 (구형 형식) 쿠키 -> 세션 복원 실패"""
        from database import User

        user_model = User(mock_db)
        user = user_model.get_user_by_username("testuser")

        # Old format without HMAC: "user_id:username:token"
        mock_cookie_manager.get.return_value = f"{user['id']}:testuser:oldtoken"

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False

    def test_no_cookie_returns_false(self, mock_streamlit, mock_cookie_manager):
        """쿠키 없는 경우 -> False 반환"""
        mock_cookie_manager.get.return_value = None

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False
