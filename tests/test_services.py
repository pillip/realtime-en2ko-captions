"""
services.py 단위 테스트
create_openai_session, create_aws_session 함수의 성공/실패 경로 검증
"""

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest
from botocore.exceptions import ClientError

from services import create_aws_session, create_openai_session

# ---------------------------------------------------------------------------
# create_openai_session tests
# ---------------------------------------------------------------------------


class TestCreateOpenaiSession:
    """create_openai_session 테스트"""

    def test_missing_openai_key_raises_error(self, monkeypatch):
        """OPENAI_KEY 환경변수 미설정 시 ValueError 발생"""
        monkeypatch.delenv("OPENAI_KEY", raising=False)

        with pytest.raises(ValueError, match="OpenAI API 키"):
            asyncio.run(create_openai_session())

    def test_successful_session_creation(self, monkeypatch):
        """정상 세션 생성 시 id, client_secret, expires_at, model 키 반환"""
        monkeypatch.setenv("OPENAI_KEY", "sk-test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "sess_abc123",
            "client_secret": {"value": "secret_token_xyz"},
            "model": "gpt-4o-realtime-preview-2024-12-17",
        }

        async def mock_post(*args, **kwargs):
            return mock_response

        with patch("services.httpx.AsyncClient") as mock_client_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.post = mock_post
            mock_client_instance.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            result = asyncio.run(create_openai_session())

        assert result["id"] == "sess_abc123"
        assert result["client_secret"] == "secret_token_xyz"
        assert "expires_at" in result
        assert result["model"] == "gpt-4o-realtime-preview-2024-12-17"

    def test_api_returns_401_raises_exception(self, monkeypatch):
        """API가 401 반환 시 Exception 발생"""
        monkeypatch.setenv("OPENAI_KEY", "sk-invalid-key")

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        async def mock_post(*args, **kwargs):
            return mock_response

        with patch("services.httpx.AsyncClient") as mock_client_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.post = mock_post
            mock_client_instance.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            with pytest.raises(Exception, match="OpenAI 세션 생성 실패"):
                asyncio.run(create_openai_session())

    def test_api_returns_500_raises_exception(self, monkeypatch):
        """API가 500 반환 시 Exception 발생"""
        monkeypatch.setenv("OPENAI_KEY", "sk-test-key")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        async def mock_post(*args, **kwargs):
            return mock_response

        with patch("services.httpx.AsyncClient") as mock_client_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.post = mock_post
            mock_client_instance.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            with pytest.raises(Exception, match="OpenAI 세션 생성 실패"):
                asyncio.run(create_openai_session())

    def test_http_error_raises_exception(self, monkeypatch):
        """httpx.HTTPError 발생 시 Exception 으로 래핑"""
        monkeypatch.setenv("OPENAI_KEY", "sk-test-key")

        async def mock_post(*args, **kwargs):
            raise httpx.ConnectError("Connection refused")

        with patch("services.httpx.AsyncClient") as mock_client_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.post = mock_post
            mock_client_instance.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            with pytest.raises(Exception, match="OpenAI 세션 생성 실패"):
                asyncio.run(create_openai_session())

    def test_session_data_missing_fields_returns_none_values(self, monkeypatch):
        """API 응답에 일부 필드가 없어도 None으로 처리하며 에러 없음"""
        monkeypatch.setenv("OPENAI_KEY", "sk-test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}  # empty response

        async def mock_post(*args, **kwargs):
            return mock_response

        with patch("services.httpx.AsyncClient") as mock_client_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.post = mock_post
            mock_client_instance.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            result = asyncio.run(create_openai_session())

        assert result["id"] is None
        assert result["client_secret"] is None
        assert "expires_at" in result
        assert result["model"] == "gpt-4o-realtime-preview-2024-12-17"


# ---------------------------------------------------------------------------
# create_aws_session tests
# ---------------------------------------------------------------------------


class TestCreateAwsSession:
    """create_aws_session 테스트"""

    def test_missing_access_key_raises_error(self, monkeypatch):
        """AWS_ACCESS_KEY_ID 미설정 시 ValueError 발생"""
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")

        with pytest.raises(ValueError, match="AWS 자격 증명"):
            create_aws_session()

    def test_missing_secret_key_raises_error(self, monkeypatch):
        """AWS_SECRET_ACCESS_KEY 미설정 시 ValueError 발생"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key")
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

        with pytest.raises(ValueError, match="AWS 자격 증명"):
            create_aws_session()

    def test_both_keys_missing_raises_error(self, monkeypatch):
        """양쪽 키 모두 미설정 시 ValueError 발생"""
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

        with pytest.raises(ValueError, match="AWS 자격 증명"):
            create_aws_session()

    def test_successful_session_creation(self, monkeypatch):
        """정상 STS 호출 시 access_key_id, region, websocket_url 포함 dict 반환"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret123")
        monkeypatch.setenv("AWS_REGION", "ap-northeast-2")

        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/test",
        }

        with patch("services.boto3.client", return_value=mock_sts):
            result = create_aws_session(websocket_port=9000)

        assert result["access_key_id"] == "AKIATEST"
        assert result["region"] == "ap-northeast-2"
        assert result["websocket_url"] == "ws://localhost:9000"
        assert result["account_id"] == "123456789012"
        assert "secret_access_key" in result

    def test_default_websocket_port(self, monkeypatch):
        """websocket_port 미지정 시 기본 포트 8765 사용"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret123")

        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}

        with patch("services.boto3.client", return_value=mock_sts):
            result = create_aws_session()

        assert result["websocket_url"] == "ws://localhost:8765"

    def test_sts_invalid_credentials_raises_error(self, monkeypatch):
        """STS InvalidUserID.NotFound 시 적절한 ValueError 발생"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAINVALID")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "invalid-secret")

        mock_sts = MagicMock()
        error_response = {
            "Error": {"Code": "InvalidUserID.NotFound", "Message": "Not found"}
        }
        mock_sts.get_caller_identity.side_effect = ClientError(
            error_response, "GetCallerIdentity"
        )

        with patch("services.boto3.client", return_value=mock_sts):
            with pytest.raises(ValueError, match="유효하지 않습니다"):
                create_aws_session()

    def test_sts_token_expired_raises_error(self, monkeypatch):
        """STS TokenRefreshRequired 시 적절한 ValueError 발생"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret123")

        mock_sts = MagicMock()
        error_response = {
            "Error": {"Code": "TokenRefreshRequired", "Message": "Token expired"}
        }
        mock_sts.get_caller_identity.side_effect = ClientError(
            error_response, "GetCallerIdentity"
        )

        with patch("services.boto3.client", return_value=mock_sts):
            with pytest.raises(ValueError, match="토큰이 만료"):
                create_aws_session()

    def test_sts_unknown_error_raises_error(self, monkeypatch):
        """STS 기타 ClientError 시 에러 코드 포함 ValueError 발생"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret123")

        mock_sts = MagicMock()
        error_response = {"Error": {"Code": "AccessDenied", "Message": "Access denied"}}
        mock_sts.get_caller_identity.side_effect = ClientError(
            error_response, "GetCallerIdentity"
        )

        with patch("services.boto3.client", return_value=mock_sts):
            with pytest.raises(ValueError, match="AWS 연결 실패: AccessDenied"):
                create_aws_session()

    def test_openai_available_flag(self, monkeypatch):
        """OPENAI_KEY 설정 여부에 따른 openai_available 플래그 검증"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret123")
        monkeypatch.setenv("OPENAI_KEY", "sk-test")

        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}

        with patch("services.boto3.client", return_value=mock_sts):
            result = create_aws_session()

        assert result["openai_available"] is True

    def test_openai_not_available_flag(self, monkeypatch):
        """OPENAI_KEY 미설정 시 openai_available=False"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret123")
        monkeypatch.delenv("OPENAI_KEY", raising=False)

        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}

        with patch("services.boto3.client", return_value=mock_sts):
            result = create_aws_session()

        assert result["openai_available"] is False

    def test_general_exception_raises_value_error(self, monkeypatch):
        """일반 Exception 발생 시 ValueError로 래핑"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret123")

        with patch(
            "services.boto3.client", side_effect=RuntimeError("Connection failed")
        ):
            with pytest.raises(ValueError, match="AWS 세션 생성 실패"):
                create_aws_session()


# ---------------------------------------------------------------------------
# Helper: AsyncMock for context manager
# ---------------------------------------------------------------------------


class AsyncMock(MagicMock):
    """MagicMock subclass that supports async context manager protocol."""

    async def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False
