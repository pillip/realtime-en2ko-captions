"""
WebSocket _authenticate_client 단위 테스트
DB 검증 로직, 비활성 사용자 거부, 역할 강제 적용 등을 검증
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def db_path(tmp_path):
    """임시 데이터베이스 경로"""
    return str(tmp_path / "test_ws_auth.db")


@pytest.fixture
def mock_db(db_path):
    """테스트용 데이터베이스 (활성 + 비활성 사용자 포함)"""
    from database import DatabaseManager, User

    db = DatabaseManager(db_path)
    user_model = User(db)

    # Active user with role "user"
    user_model.create_user(
        username="testuser",
        password="testpass",
        role="user",
        usage_limit_seconds=3600,
    )
    # Active admin
    user_model.create_user(
        username="admin",
        password="adminpass",
        role="admin",
        usage_limit_seconds=0,
    )
    # Inactive user
    uid = user_model.create_user(
        username="inactive",
        password="inactivepass",
        role="user",
        usage_limit_seconds=3600,
    )
    user_model.update_user(uid, is_active=False)

    return user_model


def _make_websocket(auth_message):
    """Create a mock websocket that returns the given auth message on recv()."""
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps(auth_message))
    ws.send = AsyncMock()
    return ws


def _get_sent_messages(ws):
    """Extract parsed JSON messages from websocket send calls."""
    return [json.loads(call.args[0]) for call in ws.send.call_args_list]


class TestAuthenticateClientDbValidation:
    """_authenticate_client DB 검증 테스트"""

    def test_nonexistent_user_id_returns_none(self, mock_db):
        """존재하지 않는 user_id로 인증 시 None 반환하고 auth_success 미전송"""
        from websocket_handler import _authenticate_client

        ws = _make_websocket(
            {
                "type": "auth",
                "user": {"id": 9999, "username": "ghost", "role": "admin"},
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        assert all(msg.get("type") != "auth_success" for msg in sent)
        assert any(msg.get("type") == "auth_error" for msg in sent)

    def test_role_overwritten_from_db(self, mock_db):
        """클라이언트가 role='admin'을 주장하더라도 DB의 role='user'로 덮어씀"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "testuser",
                    "role": "admin",  # claimed admin
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["role"] == "user"  # DB role, not claimed role

    def test_inactive_user_returns_none(self, mock_db):
        """비활성 사용자(is_active=0) 인증 시 None 반환하고 에러 메시지 전송"""
        from websocket_handler import _authenticate_client

        inactive_user = mock_db.get_user_by_username("inactive")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": inactive_user["id"],
                    "username": "inactive",
                    "role": "user",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        assert any(msg.get("type") == "auth_error" for msg in sent)

    def test_valid_user_returns_validated_info(self, mock_db):
        """유효한 사용자 인증 시 DB 기반 user_info 반환 및 auth_success 전송"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "testuser",
                    "role": "user",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["id"] == db_user["id"]
        assert result["username"] == "testuser"
        assert result["role"] == "user"
        assert result["is_active"] == 1

        sent = _get_sent_messages(ws)
        assert any(msg.get("type") == "auth_success" for msg in sent)

    def test_username_mismatch_returns_none(self, mock_db):
        """user_id는 존재하지만 username이 DB와 다르면 None 반환 (impersonation 방지)"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "impersonator",
                    "role": "user",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        assert any(msg.get("type") == "auth_error" for msg in sent)

    def test_missing_user_info_returns_none(self, mock_db):
        """auth 메시지에 user 정보가 없으면 None 반환"""
        from websocket_handler import _authenticate_client

        ws = _make_websocket({"type": "auth"})

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None

    def test_missing_user_id_returns_none(self, mock_db):
        """user dict에 id 필드가 없으면 None 반환"""
        from websocket_handler import _authenticate_client

        ws = _make_websocket(
            {
                "type": "auth",
                "user": {"username": "testuser", "role": "user"},
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None

    def test_admin_role_preserved_for_real_admin(self, mock_db):
        """실제 관리자의 role은 'admin'으로 유지"""
        from websocket_handler import _authenticate_client

        admin_user = mock_db.get_user_by_username("admin")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": admin_user["id"],
                    "username": "admin",
                    "role": "admin",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["role"] == "admin"

    def test_timeout_returns_none(self):
        """인증 메시지 수신 타임아웃 시 None 반환"""
        from websocket_handler import _authenticate_client

        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=TimeoutError)

        result = asyncio.run(_authenticate_client(ws))
        assert result is None

    def test_non_auth_message_returns_none(self):
        """auth 타입이 아닌 메시지 수신 시 None 반환"""
        from websocket_handler import _authenticate_client

        ws = _make_websocket({"type": "ping"})

        result = asyncio.run(_authenticate_client(ws))
        assert result is None

    def test_error_message_does_not_leak_internals(self, mock_db):
        """에러 메시지에 내부 정보(DB 사용자명 등)가 노출되지 않음 (RL-006)"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "impersonator",
                    "role": "user",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            asyncio.run(_authenticate_client(ws))

        sent = _get_sent_messages(ws)
        for msg in sent:
            if msg.get("type") == "auth_error":
                # Error message should not contain the real username
                assert "testuser" not in msg.get("message", "")
                # Should not contain user_id
                assert str(db_user["id"]) not in msg.get("message", "")
