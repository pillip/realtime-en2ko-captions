"""
Coverage gap tests for websocket_handler.py and auth.py.
Targets uncovered lines identified by testgen scan:
- websocket_handler: _init_translation_clients, _handle_session_request,
  language_update message handling, auth exception branch
- auth: init_session_state, get_user_remaining_seconds
"""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch


# Mock Streamlit before importing modules that depend on it
class SessionState(dict):
    """Streamlit session_state 모방: dict + attribute 접근 모두 지원"""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError:
            raise AttributeError(key) from None


if "streamlit" not in sys.modules:
    _mock_st = MagicMock()
    _mock_st.session_state = SessionState()
    sys.modules["streamlit"] = _mock_st
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ============================================================
# websocket_handler: _init_translation_clients
# ============================================================
class TestInitTranslationClients:
    """_init_translation_clients 테스트"""

    def test_returns_translate_and_bedrock_clients(self):
        """boto3 클라이언트 두 개가 정상 반환"""
        from websocket_handler import _init_translation_clients

        mock_translate = MagicMock()
        mock_bedrock = MagicMock()

        def fake_client(service, **kwargs):
            if service == "translate":
                return mock_translate
            elif service == "bedrock-runtime":
                return mock_bedrock
            return MagicMock()

        with (
            patch("websocket_handler.get_aws_access_key_id", return_value="key"),
            patch("websocket_handler.get_aws_secret_access_key", return_value="secret"),
            patch("websocket_handler.get_aws_region", return_value="us-east-1"),
            patch("websocket_handler.boto3.client", side_effect=fake_client),
        ):
            translate_client, bedrock_client, bedrock_available = (
                _init_translation_clients()
            )

        assert translate_client is mock_translate
        assert bedrock_client is mock_bedrock
        assert bedrock_available is True

    def test_bedrock_unavailable_falls_back(self):
        """Bedrock 초기화 실패 시 bedrock_available=False"""
        from websocket_handler import _init_translation_clients

        mock_translate = MagicMock()

        def fake_client(service, **kwargs):
            if service == "translate":
                return mock_translate
            elif service == "bedrock-runtime":
                raise Exception("Bedrock not available")
            return MagicMock()

        with (
            patch("websocket_handler.get_aws_access_key_id", return_value="key"),
            patch("websocket_handler.get_aws_secret_access_key", return_value="secret"),
            patch("websocket_handler.get_aws_region", return_value="us-east-1"),
            patch("websocket_handler.boto3.client", side_effect=fake_client),
        ):
            translate_client, bedrock_client, bedrock_available = (
                _init_translation_clients()
            )

        assert translate_client is mock_translate
        assert bedrock_client is None
        assert bedrock_available is False


# ============================================================
# websocket_handler: _handle_session_request
# ============================================================
class TestHandleSessionRequest:
    """_handle_session_request 테스트"""

    def test_success_sends_session(self):
        """OpenAI 세션 성공 시 session 데이터 전송"""
        from websocket_handler import _handle_session_request

        ws = AsyncMock()
        mock_session = {"id": "sess_123", "client_secret": "ek_abc"}

        with patch(
            "websocket_handler.create_openai_session",
            new=AsyncMock(return_value=mock_session),
        ):
            asyncio.run(_handle_session_request(ws))

        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "openai_session"
        assert sent["session"]["id"] == "sess_123"

    def test_failure_sends_error(self):
        """OpenAI 세션 실패 시 에러 메시지 전송"""
        from websocket_handler import _handle_session_request

        ws = AsyncMock()

        with patch(
            "websocket_handler.create_openai_session",
            new=AsyncMock(side_effect=Exception("API error")),
        ):
            asyncio.run(_handle_session_request(ws))

        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "error"
        assert "세션 생성 실패" in sent["message"]


# ============================================================
# websocket_handler: language_update in handle_openai_websocket
# ============================================================
class TestLanguageUpdateMessage:
    """handle_openai_websocket의 language_update 메시지 처리 테스트"""

    def test_language_update_changes_settings(self):
        """language_update 메시지로 언어 설정이 변경됨"""
        from websocket_handler import handle_openai_websocket

        ws = AsyncMock()
        ws.remote_address = ("127.0.0.1", 9999)

        messages = [
            json.dumps(
                {"type": "language_update", "input_lang": "zh", "output_lang": "en"}
            ),
        ]

        async def mock_aiter(self):
            for msg in messages:
                yield msg

        ws.__aiter__ = mock_aiter

        mock_user = {
            "id": 1,
            "username": "testuser",
            "role": "user",
            "language_settings": {"input_lang": "auto", "output_lang": "ko"},
        }

        with (
            patch(
                "websocket_handler._authenticate_client",
                new=AsyncMock(return_value=mock_user),
            ),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
        ):
            asyncio.run(handle_openai_websocket(ws))

        # Find the language_updated response
        all_sends = [
            json.loads(call.args[0])
            for call in ws.send.call_args_list
            if isinstance(call.args[0], str)
        ]
        lang_msgs = [m for m in all_sends if m.get("type") == "language_updated"]
        assert len(lang_msgs) == 1
        assert lang_msgs[0]["input_lang"] == "zh"
        assert lang_msgs[0]["output_lang"] == "en"


# ============================================================
# websocket_handler: _authenticate_client exception branch
# ============================================================
class TestAuthenticateClientException:
    """_authenticate_client의 예외 처리 분기 테스트"""

    def test_generic_exception_returns_none(self):
        """일반 예외 발생 시 None 반환"""
        from websocket_handler import _authenticate_client

        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=RuntimeError("connection broken"))

        result = asyncio.run(_authenticate_client(ws))
        assert result is None


# ============================================================
# auth: init_session_state
# ============================================================
class TestInitSessionState:
    """init_session_state 테스트"""

    def test_initializes_default_state(self):
        """기본 세션 상태를 초기화"""
        import auth

        auth.st.session_state = SessionState()

        with patch("auth.restore_session_from_cookie"):
            auth.init_session_state()

        assert auth.st.session_state["authenticated"] is False
        assert auth.st.session_state["user"] is None
        assert auth.st.session_state["cookie_restore_attempted"] is True

    def test_does_not_restore_if_already_authenticated(self):
        """이미 인증된 상태면 쿠키 복원 시도하지 않음"""
        import auth

        auth.st.session_state = SessionState(authenticated=True, user={"id": 1})

        with patch("auth.restore_session_from_cookie") as mock_restore:
            auth.init_session_state()

        mock_restore.assert_not_called()

    def test_does_not_restore_if_already_attempted(self):
        """이미 쿠키 복원을 시도한 경우 다시 시도하지 않음"""
        import auth

        auth.st.session_state = SessionState(
            authenticated=False,
            user=None,
            cookie_restore_attempted=True,
        )

        with patch("auth.restore_session_from_cookie") as mock_restore:
            auth.init_session_state()

        mock_restore.assert_not_called()


# ============================================================
# auth: get_user_remaining_seconds
# ============================================================
class TestGetUserRemainingSeconds:
    """get_user_remaining_seconds 테스트"""

    def test_returns_none_when_no_user(self):
        """로그인하지 않은 경우 None 반환"""
        from auth import get_user_remaining_seconds

        with patch("auth.get_current_user", return_value=None):
            result = get_user_remaining_seconds()

        assert result is None

    def test_returns_remaining_seconds_for_logged_in_user(self):
        """로그인한 사용자의 남은 시간 반환"""
        from auth import get_user_remaining_seconds

        mock_user_model = MagicMock()
        mock_user_model.get_remaining_seconds.return_value = 1800

        with (
            patch(
                "auth.get_current_user",
                return_value={"id": 42, "username": "test"},
            ),
            patch("auth.get_user_model", return_value=mock_user_model),
        ):
            result = get_user_remaining_seconds()

        assert result == 1800
        mock_user_model.get_remaining_seconds.assert_called_once_with(42)
