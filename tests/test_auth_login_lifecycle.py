"""
auth.py login + session lifecycle 단위 테스트.

extends 패턴: tests/test_auth_module.py 의 mock_streamlit / mock_db /
mock_cookie_manager fixture 패턴을 그대로 재현해, 다음 함수/데코레이터의
경로별 거동을 검증한다.

- login_user (성공 / 잘못된 비밀번호 / 비활성 사용자)
- restore_session_from_cookie (활성 / 비활성 / malformed / 미존재 user_id)
- logout_user (state + cookie 동시 정리)
- @require_auth (미인증 / 인증)
- @require_admin (role=user 차단 / role=admin 통과)
- @require_admin_or_operator (role=user 차단 / operator 통과 / admin 통과)

RL-002 (server-side 역할 신뢰), RL-006 (clients 에 generic 메시지) 의 테스트
표면을 데코레이터에서 강제한다 — 데코레이터가 `st.error`/`st.stop` 을 부르면서
래핑된 함수를 실행시키지 않아야 함을 확인.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Streamlit / cookie shim — same pattern as tests/test_auth_module.py
# ---------------------------------------------------------------------------
class SessionState(dict):
    """Streamlit session_state shim that supports attribute access."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


@pytest.fixture(autouse=True)
def mock_streamlit(monkeypatch):
    """Stub streamlit + extra_streamlit_components and force auth re-import.

    Same recipe as tests/test_auth_module.py — without this the test file
    can't import auth.py because streamlit's import side effects fail in a
    headless environment.
    """
    import sys

    mock_st = MagicMock()
    mock_st.session_state = SessionState()
    mock_esc = MagicMock()

    monkeypatch.setitem(sys.modules, "streamlit", mock_st)
    monkeypatch.setitem(sys.modules, "extra_streamlit_components", mock_esc)
    sys.modules.pop("auth", None)
    yield mock_st


@pytest.fixture(autouse=True)
def set_session_secret(monkeypatch):
    """Deterministic SESSION_SECRET so HMAC signatures are reproducible."""
    monkeypatch.setenv("SESSION_SECRET", "test-secret-login-lifecycle")


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "auth_lifecycle.db")


@pytest.fixture
def mock_db(db_path):
    """Real SQLite DB with three seed users covering the role matrix."""
    from database import DatabaseManager, User

    db = DatabaseManager(db_path)
    user_model = User(db)
    user_model.create_user(
        username="alice",
        password="alicepass",
        role="user",
        usage_limit_seconds=3600,
    )
    user_model.create_user(
        username="op",
        password="oppass",
        role="operator",
        usage_limit_seconds=0,
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
    from database import User

    user_model = User(mock_db)
    uid = user_model.create_user(
        username="ghost",
        password="ghostpass",
        role="user",
        usage_limit_seconds=3600,
    )
    user_model.update_user(uid, is_active=False)
    return mock_db


@pytest.fixture
def mock_cookie_manager():
    cm = MagicMock()
    cm.get.return_value = None
    cm.set = MagicMock()
    cm.delete = MagicMock()
    return cm


# ---------------------------------------------------------------------------
# login_user
# ---------------------------------------------------------------------------
class TestLoginUserLifecycle:
    """login_user 의 happy-path 및 거부 경로."""

    def test_login_user_valid_credentials(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """올바른 자격 → True, session_state.authenticated=True, 쿠키 set 호출."""
        from database import User

        user_model = User(mock_db)
        with (
            patch("auth.get_user_model", return_value=user_model),
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
        ):
            from auth import login_user

            assert login_user("alice", "alicepass") is True

        assert mock_streamlit.session_state["authenticated"] is True
        assert mock_streamlit.session_state["user"]["username"] == "alice"
        assert mock_streamlit.session_state["cookie_restore_attempted"] is True
        # set_session_cookie -> CookieManager.set
        mock_cookie_manager.set.assert_called_once()
        call_args = mock_cookie_manager.set.call_args
        assert call_args[0][0] == "user_session"
        assert "alice" in call_args[0][1]

    def test_login_user_wrong_password(self, mock_streamlit, mock_db):
        """잘못된 비밀번호 → False, session 변경 없음."""
        from database import User

        user_model = User(mock_db)
        # session_state 가 비어있는 초기 상태에서 시작.
        with patch("auth.get_user_model", return_value=user_model):
            from auth import login_user

            assert login_user("alice", "WRONG") is False

        # 인증 실패 → authenticated 키가 set 되지 않거나 False.
        assert mock_streamlit.session_state.get("authenticated") in (None, False)
        assert mock_streamlit.session_state.get("user") in (None,)

    def test_login_user_inactive_user(
        self, mock_streamlit, mock_db_with_inactive, mock_cookie_manager
    ):
        """비활성 사용자는 authenticate() 가 None 을 반환 → False, 쿠키 미설정."""
        from database import User

        user_model = User(mock_db_with_inactive)
        with (
            patch("auth.get_user_model", return_value=user_model),
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
        ):
            from auth import login_user

            assert login_user("ghost", "ghostpass") is False

        assert mock_streamlit.session_state.get("authenticated") in (None, False)
        # 비활성 사용자에 대해서는 절대 쿠키를 발급하면 안 된다.
        mock_cookie_manager.set.assert_not_called()


# ---------------------------------------------------------------------------
# restore_session_from_cookie
# ---------------------------------------------------------------------------
class TestRestoreSessionFromCookieLifecycle:
    """restore_session_from_cookie 경로 — 정상/오류/조작 케이스."""

    def test_restore_session_from_cookie_valid_active_user(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """유효한 HMAC + 활성 사용자 → True, session 복원."""
        from database import User

        user_model = User(mock_db)
        alice = user_model.get_user_by_username("alice")
        from auth import _sign_cookie

        signed = _sign_cookie(alice["id"], "alice", "tok")
        mock_cookie_manager.get.return_value = signed

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is True
        assert mock_streamlit.session_state["authenticated"] is True
        assert mock_streamlit.session_state["user"]["username"] == "alice"

    def test_restore_session_from_cookie_inactive_user(
        self, mock_streamlit, mock_db_with_inactive, mock_cookie_manager
    ):
        """비활성 사용자 → False (HMAC 검증 통과하더라도)."""
        from database import User

        user_model = User(mock_db_with_inactive)
        ghost = user_model.get_user_by_username("ghost")
        from auth import _sign_cookie

        signed = _sign_cookie(ghost["id"], "ghost", "tok")
        mock_cookie_manager.get.return_value = signed

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False
        # 세션이 복원되지 않았어야 한다.
        assert mock_streamlit.session_state.get("authenticated") in (None, False)

    def test_restore_session_from_cookie_malformed_cookie(
        self, mock_streamlit, mock_cookie_manager
    ):
        """콜론이 없는 잘못된 쿠키 문자열 → False, 예외 미발생."""
        mock_cookie_manager.get.return_value = "definitely-not-valid-cookie-data"

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import restore_session_from_cookie

            # 절대 raise 하면 안 됨 — 단순히 False 반환.
            result = restore_session_from_cookie()

        assert result is False
        assert mock_streamlit.session_state.get("authenticated") in (None, False)

    def test_restore_session_from_cookie_unknown_user_id(
        self, mock_streamlit, mock_db, mock_cookie_manager
    ):
        """HMAC 은 valid 이지만 user_id 가 DB 에 없음 → False."""
        from database import User

        user_model = User(mock_db)
        from auth import _sign_cookie

        signed = _sign_cookie(424242, "phantom", "tok")
        mock_cookie_manager.get.return_value = signed

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=user_model),
        ):
            from auth import restore_session_from_cookie

            result = restore_session_from_cookie()

        assert result is False


# ---------------------------------------------------------------------------
# logout_user
# ---------------------------------------------------------------------------
class TestLogoutUserClearsStateAndCookie:
    """logout_user 는 session 과 쿠키를 둘 다 정리해야 한다."""

    def test_logout_user_clears_state_and_cookie(
        self, mock_streamlit, mock_cookie_manager
    ):
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1, "username": "alice", "role": "user"},
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import logout_user

            logout_user()

        # State cleared
        assert mock_streamlit.session_state["authenticated"] is False
        assert mock_streamlit.session_state["user"] is None
        assert mock_streamlit.session_state["cookie_restore_attempted"] is False
        # Cookie deletion fired
        mock_cookie_manager.delete.assert_called_once_with("user_session")


# ---------------------------------------------------------------------------
# @require_auth
# ---------------------------------------------------------------------------
class TestRequireAuthDecorator:
    """미인증 사용자는 차단되고 인증된 사용자는 통과되어야 한다."""

    def test_require_auth_decorator_unauthenticated_calls_st_warning_and_stop(
        self, mock_streamlit, mock_cookie_manager
    ):
        """미인증 → st.warning + st.stop 호출, 래핑 함수 실행 안 됨.

        Streamlit's real ``st.stop()`` halts execution by raising
        ``StopException`` internally; in test we substitute a side-effect
        that raises so the mock faithfully short-circuits the wrapped
        function (without that, MagicMock returns silently and the next
        line of the decorator runs the wrapped function — masking the bug
        we're trying to detect).
        """
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": False,
                "user": None,
                "cookie_restore_attempted": True,
            }
        )

        class _StopCalled(Exception):
            pass

        mock_streamlit.stop.side_effect = _StopCalled()

        called: list[bool] = []

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_auth

            @require_auth
            def secret_fn():
                called.append(True)
                return "REACHED"

            with pytest.raises(_StopCalled):
                secret_fn()

        # 래핑 함수는 절대 실행되지 않아야 한다.
        assert called == []
        mock_streamlit.warning.assert_called_once()
        mock_streamlit.stop.assert_called()

    def test_require_auth_decorator_authenticated_invokes_wrapped_function(
        self, mock_streamlit, mock_cookie_manager
    ):
        """인증된 활성 사용자 → 래핑 함수 실행 + 반환값 보존."""
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
            def secret_fn(x, y=2):
                return x + y

            result = secret_fn(10)

        assert result == 12
        mock_streamlit.warning.assert_not_called()
        mock_streamlit.error.assert_not_called()


# ---------------------------------------------------------------------------
# @require_admin
# ---------------------------------------------------------------------------
class TestRequireAdminDecorator:
    """role=user 는 막히고 role=admin 만 통과한다."""

    def test_require_admin_decorator_role_user_blocked(
        self, mock_streamlit, mock_cookie_manager
    ):
        """role='user' → st.error + st.stop, 래핑 함수 미실행.

        ``st.stop`` 의 side_effect 로 예외를 주입해 실제 Streamlit 의 halt
        동작을 시뮬레이트한다 (이 트릭이 없으면 MagicMock 이 silent return
        해버려 데코레이터의 차단 효과를 검증할 수 없다).
        """
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1, "role": "user", "is_active": True},
                "cookie_restore_attempted": True,
            }
        )

        class _StopCalled(Exception):
            pass

        mock_streamlit.stop.side_effect = _StopCalled()

        called: list[bool] = []

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_admin

            @require_admin
            def admin_only():
                called.append(True)
                return "ADMIN"

            with pytest.raises(_StopCalled):
                admin_only()

        assert called == []
        mock_streamlit.error.assert_called_once()
        # RL-002: 메시지에 '관리자' 가 포함되어야 한다 (role 신뢰 한계 명확화).
        assert "관리자" in mock_streamlit.error.call_args[0][0]
        mock_streamlit.stop.assert_called()

    def test_require_admin_decorator_role_admin_passes(
        self, mock_streamlit, mock_cookie_manager
    ):
        """role='admin' → 래핑 함수 실행."""
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
            def admin_only():
                return "ADMIN-OK"

            result = admin_only()

        assert result == "ADMIN-OK"
        mock_streamlit.error.assert_not_called()


# ---------------------------------------------------------------------------
# @require_admin_or_operator (ISSUE-29)
# ---------------------------------------------------------------------------
class TestRequireAdminOrOperatorDecorator:
    """admin 또는 operator 만 통과 — role=user 는 차단."""

    def test_require_admin_or_operator_role_user_blocked(
        self, mock_streamlit, mock_cookie_manager
    ):
        """일반 사용자(role='user') → st.error + st.stop, 래핑 함수 미실행.

        ``st.stop`` 에 side_effect 를 주입해 Streamlit halt 를 시뮬레이트.
        """
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1, "role": "user", "is_active": True},
                "cookie_restore_attempted": True,
            }
        )

        class _StopCalled(Exception):
            pass

        mock_streamlit.stop.side_effect = _StopCalled()

        called: list[bool] = []

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_admin_or_operator

            @require_admin_or_operator
            def admin_or_op():
                called.append(True)
                return "REACHED"

            with pytest.raises(_StopCalled):
                admin_or_op()

        assert called == []
        mock_streamlit.error.assert_called_once()
        msg = mock_streamlit.error.call_args[0][0]
        # 메시지에 두 역할 중 하나라도 명시되어야 한다.
        assert "관리자" in msg or "오퍼레이터" in msg
        mock_streamlit.stop.assert_called()

    def test_require_admin_or_operator_role_operator_passes(
        self, mock_streamlit, mock_cookie_manager
    ):
        """operator 역할 → 통과, 래핑 함수 실행."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 3, "role": "operator", "is_active": True},
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_admin_or_operator

            @require_admin_or_operator
            def admin_or_op(arg):
                return f"OK-{arg}"

            result = admin_or_op("operator")

        assert result == "OK-operator"
        mock_streamlit.error.assert_not_called()
        mock_streamlit.warning.assert_not_called()

    def test_require_admin_or_operator_role_admin_passes(
        self, mock_streamlit, mock_cookie_manager
    ):
        """admin 역할 → 통과, 래핑 함수 실행."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 2, "role": "admin", "is_active": True},
                "cookie_restore_attempted": True,
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import require_admin_or_operator

            @require_admin_or_operator
            def admin_or_op():
                return "ADMIN-PASS"

            result = admin_or_op()

        assert result == "ADMIN-PASS"
        mock_streamlit.error.assert_not_called()
        mock_streamlit.warning.assert_not_called()
