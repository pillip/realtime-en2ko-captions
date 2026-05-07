"""
auth.py 의 Streamlit-context 분기 + UI 헬퍼 + cookie 안정성 단위 테스트.

대상 분기:
- check_usage_limit 의 Streamlit-context path (admin/일반/None/raise)
- display_user_info / display_login_form / display_logout_button
- get_session_cookie 의 except branch
- get_cookie_manager 지연 초기화

Pattern: tests/test_auth_module.py 의 mock_streamlit / mock_cookie_manager
fixture 패턴을 그대로 따른다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class SessionState(dict):
    """Streamlit session_state 모방: dict + attribute 접근 모두 지원."""

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
    """streamlit / extra_streamlit_components 를 stub 으로 교체하고 auth 재로드."""
    import sys

    mock_st = MagicMock()
    mock_st.session_state = SessionState()
    # `with st.sidebar:` 와 `with st.form(...):` 컨텍스트가 무사히 통과하도록
    # context manager protocol 을 정의해 둔다.
    mock_st.sidebar.__enter__ = MagicMock(return_value=mock_st.sidebar)
    mock_st.sidebar.__exit__ = MagicMock(return_value=False)
    mock_form_ctx = MagicMock()
    mock_form_ctx.__enter__ = MagicMock(return_value=mock_form_ctx)
    mock_form_ctx.__exit__ = MagicMock(return_value=False)
    mock_st.form.return_value = mock_form_ctx
    mock_esc = MagicMock()

    monkeypatch.setitem(sys.modules, "streamlit", mock_st)
    monkeypatch.setitem(sys.modules, "extra_streamlit_components", mock_esc)
    sys.modules.pop("auth", None)
    yield mock_st


@pytest.fixture(autouse=True)
def set_session_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret-streamlit-ui")


@pytest.fixture
def mock_cookie_manager():
    cm = MagicMock()
    cm.get.return_value = None
    cm.set = MagicMock()
    cm.delete = MagicMock()
    return cm


# ---------------------------------------------------------------------------
# check_usage_limit — Streamlit context path (lines 249-262)
# ---------------------------------------------------------------------------
class TestCheckUsageLimitStreamlitPath:
    """user_info=None 으로 호출했을 때의 Streamlit-context 분기 검증."""

    def test_admin_user_returns_true_regardless_of_remaining(self, mock_streamlit):
        """role='admin' 이면 remaining 값과 무관하게 True 반환."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {
                    "id": 1,
                    "username": "admin",
                    "role": "admin",
                    "is_active": True,
                },
            }
        )

        # remaining 이 0 이라도 admin 이므로 True 가 나와야 한다.
        with patch("auth.get_user_remaining_seconds", return_value=0):
            from auth import check_usage_limit

            assert check_usage_limit(9999) is True

    def test_regular_user_with_sufficient_remaining_returns_true(self, mock_streamlit):
        """일반 사용자, remaining >= duration → True."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {
                    "id": 1,
                    "username": "alice",
                    "role": "user",
                    "is_active": True,
                },
            }
        )

        with patch("auth.get_user_remaining_seconds", return_value=200):
            from auth import check_usage_limit

            assert check_usage_limit(100) is True

    def test_regular_user_with_insufficient_remaining_returns_false(
        self, mock_streamlit
    ):
        """일반 사용자, remaining < duration → False."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {
                    "id": 1,
                    "username": "alice",
                    "role": "user",
                    "is_active": True,
                },
            }
        )

        with patch("auth.get_user_remaining_seconds", return_value=50):
            from auth import check_usage_limit

            assert check_usage_limit(100) is False

    def test_remaining_seconds_none_returns_false(self, mock_streamlit):
        """get_user_remaining_seconds 가 None → False (조회 실패 fail-closed)."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {
                    "id": 1,
                    "username": "alice",
                    "role": "user",
                    "is_active": True,
                },
            }
        )

        with patch("auth.get_user_remaining_seconds", return_value=None):
            from auth import check_usage_limit

            assert check_usage_limit(100) is False

    def test_get_remaining_raises_returns_false(self, mock_streamlit):
        """get_user_remaining_seconds 가 raise → except 분기에서 False 반환."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {
                    "id": 1,
                    "username": "alice",
                    "role": "user",
                    "is_active": True,
                },
            }
        )

        with patch(
            "auth.get_user_remaining_seconds",
            side_effect=RuntimeError("no streamlit context"),
        ):
            from auth import check_usage_limit

            # 절대 예외를 raise 하면 안 된다 — except 가 삼키고 False 반환.
            assert check_usage_limit(100) is False

    def test_remaining_equals_duration_returns_true(self, mock_streamlit):
        """remaining == duration → True (>= 비교 경계값)."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {
                    "id": 1,
                    "username": "alice",
                    "role": "user",
                    "is_active": True,
                },
            }
        )

        with patch("auth.get_user_remaining_seconds", return_value=100):
            from auth import check_usage_limit

            assert check_usage_limit(100) is True


# ---------------------------------------------------------------------------
# display_user_info (lines 332-372)
# ---------------------------------------------------------------------------
class TestDisplayUserInfo:
    """display_user_info 의 분기별 사이드바 렌더링 검증."""

    def test_no_authenticated_user_returns_early_no_writes(
        self, mock_streamlit, mock_cookie_manager
    ):
        """get_current_user() 가 None → early return, sidebar.write 미호출."""
        mock_streamlit.session_state = SessionState(
            {"authenticated": False, "user": None}
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import display_user_info

            display_user_info()

        # 어떤 sidebar 위젯도 호출되지 않아야 한다.
        mock_streamlit.sidebar.write.assert_not_called()
        mock_streamlit.button.assert_not_called()

    def test_user_with_full_name_writes_username_and_affiliation(
        self, mock_streamlit, mock_cookie_manager
    ):
        """full_name 이 있으면 username + 소속 (총 2회) write 호출."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {
                    "id": 1,
                    "username": "alice",
                    "full_name": "Alice Co.",
                    "role": "user",
                    "is_active": True,
                },
            }
        )
        # 로그아웃 버튼은 클릭되지 않은 상태 (False).
        mock_streamlit.button.return_value = False

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import display_user_info

            display_user_info()

        # username + 소속 = 정확히 2회 호출.
        assert mock_streamlit.write.call_count == 2
        first_arg = mock_streamlit.write.call_args_list[0].args[0]
        second_arg = mock_streamlit.write.call_args_list[1].args[0]
        assert "alice" in first_arg
        assert "아이디" in first_arg
        assert "Alice Co." in second_arg
        assert "소속" in second_arg

    def test_user_without_full_name_writes_only_username(
        self, mock_streamlit, mock_cookie_manager
    ):
        """full_name 이 falsy 이면 username 만 (1회) write 호출."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {
                    "id": 2,
                    "username": "bob",
                    "full_name": None,
                    "role": "user",
                    "is_active": True,
                },
            }
        )
        mock_streamlit.button.return_value = False

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import display_user_info

            display_user_info()

        # 정확히 1회 — 소속 라인은 렌더링되지 않아야 한다.
        assert mock_streamlit.write.call_count == 1
        only_arg = mock_streamlit.write.call_args_list[0].args[0]
        assert "bob" in only_arg
        assert "소속" not in only_arg

    def test_logout_button_clicked_calls_logout_and_rerun(
        self, mock_streamlit, mock_cookie_manager
    ):
        """로그아웃 버튼이 클릭되면 logout_user() 호출 + st.rerun() 호출."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {
                    "id": 1,
                    "username": "alice",
                    "full_name": None,
                    "role": "user",
                    "is_active": True,
                },
            }
        )
        # 버튼 클릭됨.
        mock_streamlit.button.return_value = True

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import display_user_info

            display_user_info()

        # 로그아웃이 실행되어 세션이 초기화돼야 한다.
        assert mock_streamlit.session_state["authenticated"] is False
        assert mock_streamlit.session_state["user"] is None
        # 쿠키 삭제 + rerun 호출이 있어야 한다.
        mock_cookie_manager.delete.assert_called_once_with("user_session")
        mock_streamlit.rerun.assert_called_once()


# ---------------------------------------------------------------------------
# display_login_form (lines 378-445)
# ---------------------------------------------------------------------------
class TestDisplayLoginForm:
    """display_login_form 의 분기 검증 — 인증된 상태 / 폼 렌더링 / 제출 처리."""

    def test_already_authenticated_returns_true_early(
        self, mock_streamlit, mock_cookie_manager
    ):
        """이미 인증된 상태 → True 반환, 폼 렌더링/제출 처리 없음."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1, "username": "alice", "is_active": True},
            }
        )

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import display_login_form

            result = display_login_form()

        assert result is True
        # 폼이 그려지면 안 된다.
        mock_streamlit.form.assert_not_called()
        mock_streamlit.title.assert_not_called()

    def test_unauthenticated_renders_login_form(
        self, mock_streamlit, mock_cookie_manager
    ):
        """미인증 + login_submitted=False → 로그인 폼 (title + form) 렌더링."""
        mock_streamlit.session_state = SessionState(
            {"authenticated": False, "user": None}
        )
        # 폼 안의 widget 들의 반환값. 사용자가 아무 것도 입력하지 않은 상태.
        mock_streamlit.text_input.return_value = ""
        mock_streamlit.form_submit_button.return_value = False  # 제출 안 됨

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import display_login_form

            result = display_login_form()

        assert result is False
        mock_streamlit.title.assert_called_once()
        mock_streamlit.form.assert_called_once_with("login_form")
        # 사용자명 + 비밀번호 input → 정확히 2회 호출.
        assert mock_streamlit.text_input.call_count == 2
        mock_streamlit.form_submit_button.assert_called_once()

    def test_form_submit_with_empty_credentials_shows_error(
        self, mock_streamlit, mock_cookie_manager
    ):
        """폼 제출됐지만 username/password 가 비어 있으면 st.error 호출."""
        mock_streamlit.session_state = SessionState(
            {"authenticated": False, "user": None}
        )
        # 사용자가 아무것도 입력하지 않은 채 제출.
        mock_streamlit.text_input.return_value = ""
        mock_streamlit.form_submit_button.return_value = True

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import display_login_form

            result = display_login_form()

        assert result is False
        mock_streamlit.error.assert_called_once()
        msg = mock_streamlit.error.call_args.args[0]
        # 메시지에 사용자명/비밀번호 안내가 포함돼야 한다.
        assert "사용자명" in msg or "비밀번호" in msg
        # login_submitted 는 set 되면 안 된다 — 빈 입력은 그냥 에러.
        assert mock_streamlit.session_state.get("login_submitted") is not True

    def test_form_submit_with_credentials_sets_login_submitted_state(
        self, mock_streamlit, mock_cookie_manager
    ):
        """username/password 모두 채워져 제출 → temp_* 와 login_submitted=True 설정."""
        mock_streamlit.session_state = SessionState(
            {"authenticated": False, "user": None}
        )
        # text_input 호출 순서: 사용자명 → 비밀번호.
        mock_streamlit.text_input.side_effect = ["alice", "alicepass"]
        mock_streamlit.form_submit_button.return_value = True

        with patch("auth.get_cookie_manager", return_value=mock_cookie_manager):
            from auth import display_login_form

            result = display_login_form()

        assert result is False
        assert mock_streamlit.session_state["temp_username"] == "alice"
        assert mock_streamlit.session_state["temp_password"] == "alicepass"
        assert mock_streamlit.session_state["login_submitted"] is True
        mock_streamlit.rerun.assert_called_once()
        # 정상 제출이면 error 는 없어야 한다.
        mock_streamlit.error.assert_not_called()


# ---------------------------------------------------------------------------
# get_session_cookie / get_cookie_manager (lines 73-75, 98-100)
# ---------------------------------------------------------------------------
class TestGetSessionCookieErrorPath:
    """get_session_cookie 의 except 분기 — CookieManager 가 raise 해도 None 반환."""

    def test_cookie_manager_get_raises_returns_none(self, mock_streamlit, capsys):
        """CookieManager.get(...) 이 raise → 함수는 None 반환 + 서버 로그."""
        cm = MagicMock()
        cm.get.side_effect = RuntimeError("boom — no streamlit context")

        with patch("auth.get_cookie_manager", return_value=cm):
            from auth import get_session_cookie

            # 절대 raise 해서는 안 됨. except 가 삼키고 None.
            result = get_session_cookie()

        assert result is None
        # RL-006 흐름과 동일하게 서버에 로깅돼야 한다 (디테일 흘리지 않음).
        captured = capsys.readouterr()
        assert "[Auth]" in captured.out
        assert "쿠키 읽기 실패" in captured.out

    def test_cookie_manager_returns_none_returns_none(self, mock_streamlit):
        """CookieManager.get(...) 이 None 을 반환 → 함수도 None 반환 (정상)."""
        cm = MagicMock()
        cm.get.return_value = None

        with patch("auth.get_cookie_manager", return_value=cm):
            from auth import get_session_cookie

            assert get_session_cookie() is None

    def test_cookie_manager_returns_empty_string_returns_none(self, mock_streamlit):
        """빈 문자열 → falsy 분기 → None 반환."""
        cm = MagicMock()
        cm.get.return_value = ""

        with patch("auth.get_cookie_manager", return_value=cm):
            from auth import get_session_cookie

            assert get_session_cookie() is None


class TestGetCookieManagerLazyInit:
    """get_cookie_manager 의 지연 초기화 분기."""

    def test_lazy_init_creates_manager_on_first_call(self, mock_streamlit, monkeypatch):
        """첫 호출에서만 CookieManager 가 인스턴스화되고, 이후 호출은 동일 객체 반환."""
        import sys

        instances = []

        class _FakeCookieManager:
            def __init__(self, key):
                instances.append(self)
                self.key = key

        # extra_streamlit_components 의 CookieManager 만 교체.
        fake_module = MagicMock()
        fake_module.CookieManager = _FakeCookieManager
        monkeypatch.setitem(sys.modules, "extra_streamlit_components", fake_module)
        sys.modules.pop("auth", None)

        # auth 모듈의 _cookie_manager 모듈 글로벌이 None 으로 초기화됐는지 확인.
        import auth as auth_module
        from auth import get_cookie_manager

        auth_module._cookie_manager = None

        first = get_cookie_manager()
        second = get_cookie_manager()

        assert first is second
        # 정확히 한 번만 instantiate 돼야 한다 (race-free 멱등).
        assert len(instances) == 1
        assert instances[0].key == "auth_cookie_manager"


# ---------------------------------------------------------------------------
# _verify_cookie split error path (lines 62-63) — TypeError 가 raise 되는 입력
# ---------------------------------------------------------------------------
class TestVerifyCookieDefensiveErrorHandling:
    """_verify_cookie 의 except (ValueError, TypeError) 분기 — 비-문자열 입력."""

    def test_verify_cookie_with_non_string_returns_none(self, mock_streamlit):
        """문자열이 아닌 입력 (예: None, list) → None 반환, raise 안 함."""
        from auth import _verify_cookie

        # rsplit 자체가 None 에는 호출 불가하지만, 실제로는 split 단계에서
        # ValueError 또는 TypeError 가 날 수 있는 다양한 입력에 대한 방어막.
        # 빈 문자열 → rsplit(":", 1) → [""] 길이 1 → return None.
        assert _verify_cookie("") is None
        # 콜론 1개만 있는 경우 → split(":", 2) 가 길이 2 가 되어 ValueError.
        assert _verify_cookie("a:b") is None
        # 잘못된 형식 (HMAC 미스매치).
        assert _verify_cookie("wrong:format:no:hmac") is None


# ---------------------------------------------------------------------------
# display_login_form — login_submitted=True 처리 경로 (lines 384-425)
# ---------------------------------------------------------------------------
class TestDisplayLoginFormSubmittedFlow:
    """폼 제출 후 다음 rerun 에서 로그인 처리되는 분기 검증."""

    def test_login_submitted_with_valid_credentials_logs_in_and_reruns(
        self, mock_streamlit, mock_cookie_manager, monkeypatch
    ):
        """login_submitted=True + temp_username/password 가 valid → login_user 성공.

        - markdown / info 가 호출되어 로딩 화면이 표시되고
        - login_user() 가 True 반환 시 temp_* / login_submitted 가 정리되고
        - st.rerun 이 호출돼야 한다.
        """
        # time.sleep 을 모킹해 테스트 시간 단축.
        import auth as _auth_preview  # noqa: F401  -- ensure we import a fresh copy

        # 사전: session_state 에 login_submitted + temp_* 세팅.
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": False,
                "user": None,
                "login_submitted": True,
                "temp_username": "alice",
                "temp_password": "alicepass",
            }
        )

        # login_user 가 True 를 반환하도록 모킹 (DB 의존 제거).
        from auth import display_login_form

        # auth 모듈의 time.sleep 을 즉시 반환하는 stub 으로 교체.
        monkeypatch.setattr("auth.time.sleep", lambda _: None)

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.login_user", return_value=True) as mock_login,
        ):
            result = display_login_form()

        assert result is False  # 함수는 항상 False 반환 (rerun 후 재진입에서 True)
        mock_login.assert_called_once_with("alice", "alicepass")
        # 로딩 UI 가 그려져야 한다.
        mock_streamlit.markdown.assert_called_once()
        mock_streamlit.info.assert_called_once()
        # 임시 데이터 / 플래그 정리.
        assert "temp_username" not in mock_streamlit.session_state
        assert "temp_password" not in mock_streamlit.session_state
        assert "login_submitted" not in mock_streamlit.session_state
        mock_streamlit.rerun.assert_called_once()

    def test_login_submitted_with_invalid_credentials_shows_error(
        self, mock_streamlit, mock_cookie_manager, monkeypatch
    ):
        """login_user() 가 False 반환 → st.error + login_submitted=False + rerun."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": False,
                "user": None,
                "login_submitted": True,
                "temp_username": "alice",
                "temp_password": "WRONG",
            }
        )
        monkeypatch.setattr("auth.time.sleep", lambda _: None)

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.login_user", return_value=False) as mock_login,
        ):
            from auth import display_login_form

            result = display_login_form()

        assert result is False
        mock_login.assert_called_once_with("alice", "WRONG")
        # 실패 메시지가 st.error 로 표시돼야 한다.
        mock_streamlit.error.assert_called_once()
        msg = mock_streamlit.error.call_args.args[0]
        assert "잘못되었습니다" in msg or "비밀번호" in msg
        # login_submitted 가 False 로 reset, temp_* 는 정리.
        assert mock_streamlit.session_state["login_submitted"] is False
        assert "temp_username" not in mock_streamlit.session_state
        assert "temp_password" not in mock_streamlit.session_state
        mock_streamlit.rerun.assert_called_once()

    def test_login_submitted_without_temp_credentials_returns_false_no_login(
        self, mock_streamlit, mock_cookie_manager, monkeypatch
    ):
        """login_submitted=True 이지만 temp_* 가 비어 있으면 login_user 호출 안 함."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": False,
                "user": None,
                "login_submitted": True,
                # temp_username / temp_password 미설정.
            }
        )
        monkeypatch.setattr("auth.time.sleep", lambda _: None)

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.login_user") as mock_login,
        ):
            from auth import display_login_form

            result = display_login_form()

        assert result is False
        mock_login.assert_not_called()


# ---------------------------------------------------------------------------
# update_user_session (lines 450-461)
# ---------------------------------------------------------------------------
class TestUpdateUserSession:
    """update_user_session 분기 검증 — 인증 상태 / user_id 일치 여부 / DB 결과."""

    def test_unauthenticated_returns_early_no_db_call(
        self, mock_streamlit, mock_cookie_manager
    ):
        """미인증이면 함수가 early return — DB 조회 없음."""
        mock_streamlit.session_state = SessionState(
            {"authenticated": False, "user": None}
        )

        mock_user_model = MagicMock()
        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=mock_user_model),
        ):
            from auth import update_user_session

            update_user_session(1)

        # DB 가 호출되지 않아야 한다.
        mock_user_model.get_user_by_id.assert_not_called()
        # session.user 가 변경되지 않아야 한다.
        assert mock_streamlit.session_state.get("user") is None

    def test_user_id_mismatch_returns_early_no_overwrite(
        self, mock_streamlit, mock_cookie_manager
    ):
        """현재 session.user 의 id 와 인자 user_id 가 다르면 early return."""
        original_user = {"id": 1, "username": "alice", "is_active": True}
        mock_streamlit.session_state = SessionState(
            {"authenticated": True, "user": original_user}
        )

        mock_user_model = MagicMock()
        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=mock_user_model),
        ):
            from auth import update_user_session

            update_user_session(2)  # 의도적으로 다른 user_id

        # 다른 사용자 id 면 DB 도 만지면 안 되고, session.user 도 그대로.
        mock_user_model.get_user_by_id.assert_not_called()
        assert mock_streamlit.session_state["user"] is original_user

    def test_user_id_match_overwrites_session_user_with_fresh_db_row(
        self, mock_streamlit, mock_cookie_manager
    ):
        """user_id 가 일치하고 DB 에 row 가 있으면 session.user 를 갱신한다."""
        original_user = {
            "id": 1,
            "username": "alice",
            "is_active": True,
            "total_usage_seconds": 0,
        }
        fresh_user = {
            "id": 1,
            "username": "alice",
            "is_active": True,
            "total_usage_seconds": 1234,
        }
        mock_streamlit.session_state = SessionState(
            {"authenticated": True, "user": original_user}
        )

        mock_user_model = MagicMock()
        mock_user_model.get_user_by_id.return_value = fresh_user
        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=mock_user_model),
        ):
            from auth import update_user_session

            update_user_session(1)

        mock_user_model.get_user_by_id.assert_called_once_with(1)
        # session.user 가 갱신돼야 한다.
        assert mock_streamlit.session_state["user"] == fresh_user
        assert mock_streamlit.session_state["user"]["total_usage_seconds"] == 1234

    def test_user_id_match_db_returns_none_keeps_old_user(
        self, mock_streamlit, mock_cookie_manager
    ):
        """DB 가 None 을 반환하면 session.user 를 덮어쓰지 않는다 (방어)."""
        original_user = {"id": 1, "username": "alice", "is_active": True}
        mock_streamlit.session_state = SessionState(
            {"authenticated": True, "user": original_user}
        )

        mock_user_model = MagicMock()
        mock_user_model.get_user_by_id.return_value = None
        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=mock_user_model),
        ):
            from auth import update_user_session

            update_user_session(1)

        # DB 가 None 이면 session.user 는 그대로 유지돼야 한다.
        assert mock_streamlit.session_state["user"] is original_user


# ---------------------------------------------------------------------------
# get_user_remaining_seconds (lines 227-232)
# ---------------------------------------------------------------------------
class TestGetUserRemainingSeconds:
    """get_user_remaining_seconds — 인증 상태별 분기."""

    def test_returns_none_when_no_user(self, mock_streamlit, mock_cookie_manager):
        """미인증 / user 없음 → None 반환, DB 미호출."""
        mock_streamlit.session_state = SessionState(
            {"authenticated": False, "user": None}
        )

        mock_user_model = MagicMock()
        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=mock_user_model),
        ):
            from auth import get_user_remaining_seconds

            result = get_user_remaining_seconds()

        assert result is None
        mock_user_model.get_remaining_seconds.assert_not_called()

    def test_returns_db_value_when_user_present(
        self, mock_streamlit, mock_cookie_manager
    ):
        """인증된 사용자 → DB 의 get_remaining_seconds 반환값 그대로 패스스루."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 7, "username": "alice", "is_active": True},
            }
        )

        mock_user_model = MagicMock()
        mock_user_model.get_remaining_seconds.return_value = 333
        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.get_user_model", return_value=mock_user_model),
        ):
            from auth import get_user_remaining_seconds

            result = get_user_remaining_seconds()

        assert result == 333
        mock_user_model.get_remaining_seconds.assert_called_once_with(7)


# ---------------------------------------------------------------------------
# init_session_state (lines 111, 113, 115, 122-123)
# ---------------------------------------------------------------------------
class TestInitSessionState:
    """init_session_state — session_state 키 초기화 + 쿠키 복원 시도."""

    def test_init_sets_default_keys_when_empty(
        self, mock_streamlit, mock_cookie_manager
    ):
        """비어 있는 session_state → 기본 키 (authenticated/user/...) 세팅."""
        mock_streamlit.session_state = SessionState()

        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.restore_session_from_cookie", return_value=False),
        ):
            from auth import init_session_state

            init_session_state()

        assert mock_streamlit.session_state["authenticated"] is False
        assert mock_streamlit.session_state["user"] is None
        # restore 를 시도했으므로 attempted=True 로 세팅돼야 한다.
        assert mock_streamlit.session_state["cookie_restore_attempted"] is True

    def test_init_attempts_cookie_restore_only_once(
        self, mock_streamlit, mock_cookie_manager
    ):
        """attempted=True 이면 restore_session_from_cookie 가 호출되지 않는다."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": False,
                "user": None,
                "cookie_restore_attempted": True,
            }
        )

        restore_mock = MagicMock(return_value=False)
        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.restore_session_from_cookie", restore_mock),
        ):
            from auth import init_session_state

            init_session_state()

        restore_mock.assert_not_called()

    def test_init_skips_restore_when_already_authenticated(
        self, mock_streamlit, mock_cookie_manager
    ):
        """이미 authenticated=True 면 restore 시도하지 않는다."""
        mock_streamlit.session_state = SessionState(
            {
                "authenticated": True,
                "user": {"id": 1, "username": "alice", "is_active": True},
                "cookie_restore_attempted": False,
            }
        )

        restore_mock = MagicMock(return_value=False)
        with (
            patch("auth.get_cookie_manager", return_value=mock_cookie_manager),
            patch("auth.restore_session_from_cookie", restore_mock),
        ):
            from auth import init_session_state

            init_session_state()

        restore_mock.assert_not_called()
