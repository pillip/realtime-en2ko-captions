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


# === ISSUE-2: Language Settings in Auth ===


class TestAuthenticateClientLanguageSettings:
    """_authenticate_client 언어 설정 전달 테스트 (ISSUE-2)"""

    def test_language_settings_extracted_from_auth(self, mock_db):
        """auth 메시지에 language_settings가 있으면 validated_user에 포함"""
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
                "language_settings": {
                    "input_lang": "en",
                    "output_lang": "ko",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["language_settings"]["input_lang"] == "en"
        assert result["language_settings"]["output_lang"] == "ko"

    def test_language_settings_defaults_when_missing(self, mock_db):
        """auth 메시지에 language_settings가 없으면 기본값(auto/ko) 사용"""
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
        assert result["language_settings"]["input_lang"] == "auto"
        assert result["language_settings"]["output_lang"] == "ko"

    def test_language_settings_partial_defaults(self, mock_db):
        """language_settings에 일부 필드만 있으면 나머지는 기본값"""
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
                "language_settings": {
                    "input_lang": "ja",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["language_settings"]["input_lang"] == "ja"
        assert result["language_settings"]["output_lang"] == "ko"


# ============================================================
# Room handling branches in _authenticate_client (lines 189-228)
# ============================================================
class TestAuthenticateClientRoomHandling:
    """requested_room_id 분기 — repo 예외 / closed / 메모리 race 시나리오 검증."""

    def _make_ws_with_room(self, mock_db, room_id):
        """testuser 자격으로 특정 room_id 요청하는 auth 메시지를 보내는 ws."""
        from unittest.mock import AsyncMock

        db_user = mock_db.get_user_by_username("testuser")
        return (
            _make_websocket(
                {
                    "type": "auth",
                    "user": {
                        "id": db_user["id"],
                        "username": "testuser",
                        "role": "user",
                    },
                    "room_id": room_id,
                }
            ),
            AsyncMock(),
        )

    def test_unknown_room_id_rejected_with_generic_message(self, mock_db, capsys):
        """존재하지 않는 room_id → auth_error '요청한 룸을 찾을 수 없습니다.' (RL-006).

        room_repo 가 없는 RoomManager 의 기본 in-memory 모드 → get_room()=None →
        room_closed 분기로 가서 거절된다.
        """
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        ws, _ = self._make_ws_with_room(mock_db, "no-such-room")

        # repo 가 없는 빈 RoomManager.
        empty_mgr = RoomManager()

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", empty_mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        # RL-006: 응답에 내부 디테일 누설 없음.
        auth_errors = [m for m in sent if m.get("type") == "auth_error"]
        assert len(auth_errors) == 1
        assert auth_errors[0]["message"] == "요청한 룸을 찾을 수 없습니다."

    def test_repo_exception_treated_as_room_closed(self, mock_db, capsys):
        """repo.get_by_id 가 raise → room_closed=True 로 fail-closed 처리."""
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        ws, _ = self._make_ws_with_room(mock_db, "r1")

        # 메모리에는 룸이 있지만 DB 조회는 실패하는 시나리오.
        class _ExplodingRepo:
            def get_by_id(self, room_id):
                raise RuntimeError("DB internal: locked")

            # RoomManager 의 hydrate_from_db 가 호출하는 다른 메서드는 없어야 함.

        mgr = RoomManager(room_repository=_ExplodingRepo())
        # 메모리에 r1 을 직접 넣어 get_room("r1") 가 not None 이 되게 한다.
        from room_manager import RoomState

        mgr._rooms["r1"] = RoomState(room_id="r1")

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        captured = capsys.readouterr()
        # 내부 에러는 서버 로그로 남아야 한다. repo 예외 처리는 #99 에서
        # RoomManager.adopt_from_db 로 이동했으므로 로그 문자열도 그쪽 것.
        assert "adopt_from_db 조회 실패" in captured.out

        sent = _get_sent_messages(ws)
        auth_errors = [m for m in sent if m.get("type") == "auth_error"]
        assert len(auth_errors) == 1
        # RL-006: generic message — 'DB internal' 같은 디테일이 절대 새면 안 됨.
        assert "DB internal" not in auth_errors[0]["message"]
        assert auth_errors[0]["message"] == "요청한 룸을 찾을 수 없습니다."

    def test_closed_room_status_rejected(self, mock_db):
        """persisted.status == 'closed' → auth_error 거절."""
        from room_manager import RoomManager, RoomState
        from websocket_handler import _authenticate_client

        ws, _ = self._make_ws_with_room(mock_db, "closed-room")

        class _ClosedRepo:
            def get_by_id(self, room_id):
                # 영속화된 상태가 closed 임을 시뮬레이트.
                return {"id": room_id, "status": "closed"}

        mgr = RoomManager(room_repository=_ClosedRepo())
        mgr._rooms["closed-room"] = RoomState(room_id="closed-room")

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        auth_errors = [m for m in sent if m.get("type") == "auth_error"]
        assert len(auth_errors) == 1
        assert auth_errors[0]["message"] == "요청한 룸을 찾을 수 없습니다."

    def test_register_connection_keyerror_race_returns_auth_error(self, mock_db):
        """register_connection 이 KeyError → race 분기로 generic 거절."""
        from room_manager import RoomManager, RoomState
        from websocket_handler import _authenticate_client

        ws, _ = self._make_ws_with_room(mock_db, "r1")

        class _GoodRepo:
            def get_by_id(self, room_id):
                return {"id": room_id, "status": "active"}

        mgr = RoomManager(room_repository=_GoodRepo())
        mgr._rooms["r1"] = RoomState(room_id="r1")

        # register_connection 이 KeyError 를 raise 하도록 패치.
        original_register = mgr.register_connection

        def _raising_register(room_id, websocket):
            # 메모리에서 이미 삭제됐다고 가정.
            raise KeyError(room_id)

        mgr.register_connection = _raising_register

        try:
            with (
                patch("websocket_handler.get_user_model", return_value=mock_db),
                patch("websocket_handler._room_manager", mgr),
            ):
                result = asyncio.run(_authenticate_client(ws))
        finally:
            mgr.register_connection = original_register

        assert result is None
        sent = _get_sent_messages(ws)
        auth_errors = [m for m in sent if m.get("type") == "auth_error"]
        assert len(auth_errors) == 1
        assert auth_errors[0]["message"] == "요청한 룸을 찾을 수 없습니다."
