"""
services.py 단위 테스트
create_openai_session 함수의 성공/실패 경로 검증
"""

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

from services import create_openai_session

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
