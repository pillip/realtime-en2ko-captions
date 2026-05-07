"""
RoomManager 단위 테스트 (ISSUE-25)

검증 대상:
- 룸 CRUD (생성, 조회, 삭제)
- 같은 서버에서 여러 룸이 동시에 동작할 때 메시지 격리
- 존재하지 않는 룸 ID 접속 시 에러 반환
- 룸 ID 누락 시 기본 룸 ("default") 배정 (하위 호환)

Note: 외부 AWS 호출은 mock 처리.
"""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# websocket_handler -> auth -> streamlit / extra_streamlit_components
# 시스템 Python에 해당 패키지가 없을 수 있으므로 미리 mock 등록
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ---------------------------------------------------------------------------
# RoomManager CRUD
# ---------------------------------------------------------------------------
class TestRoomManagerCrud:
    """RoomManager.create_room / get_room / delete_room"""

    def test_create_room_returns_unique_id(self):
        from room_manager import RoomManager

        rm = RoomManager()
        room_a = rm.create_room()
        room_b = rm.create_room()

        assert room_a.room_id != room_b.room_id
        assert isinstance(room_a.room_id, str)
        assert len(room_a.room_id) > 0

    def test_create_room_with_explicit_id(self):
        from room_manager import RoomManager

        rm = RoomManager()
        room = rm.create_room(room_id="hall-A")

        assert room.room_id == "hall-A"

    def test_create_room_with_duplicate_id_raises(self):
        from room_manager import RoomManager

        rm = RoomManager()
        rm.create_room(room_id="hall-A")
        with pytest.raises(ValueError):
            rm.create_room(room_id="hall-A")

    def test_get_room_returns_state(self):
        from room_manager import RoomManager

        rm = RoomManager()
        created = rm.create_room(room_id="abc123")
        fetched = rm.get_room("abc123")

        assert fetched is created
        assert fetched.room_id == "abc123"

    def test_get_room_unknown_id_returns_none(self):
        from room_manager import RoomManager

        rm = RoomManager()
        assert rm.get_room("missing") is None

    def test_delete_room_removes_state(self):
        from room_manager import RoomManager

        rm = RoomManager()
        rm.create_room(room_id="hall-A")
        deleted = rm.delete_room("hall-A")

        assert deleted is True
        assert rm.get_room("hall-A") is None

    def test_delete_unknown_room_returns_false(self):
        from room_manager import RoomManager

        rm = RoomManager()
        assert rm.delete_room("never-existed") is False

    def test_room_state_has_required_fields(self):
        from room_manager import RoomManager

        rm = RoomManager()
        room = rm.create_room(room_id="abc")

        # Required attributes per Implementation Notes
        assert hasattr(room, "room_id")
        assert hasattr(room, "connections")
        assert hasattr(room, "language_settings")
        assert hasattr(room, "translate_client")
        assert hasattr(room, "bedrock_client")
        assert hasattr(room, "created_at")
        assert hasattr(room, "last_activity")

        # Default language settings
        assert room.language_settings == {
            "input_lang": "auto",
            "output_lang": "ko",
        }

        # connections is a set
        assert isinstance(room.connections, set)
        assert len(room.connections) == 0


# ---------------------------------------------------------------------------
# Connection registration
# ---------------------------------------------------------------------------
class TestRoomManagerConnections:
    """register_connection / unregister_connection"""

    def test_register_connection_adds_to_room(self):
        from room_manager import RoomManager

        rm = RoomManager()
        rm.create_room(room_id="A")
        ws = MagicMock()

        rm.register_connection("A", ws)

        room = rm.get_room("A")
        assert ws in room.connections

    def test_register_connection_unknown_room_raises(self):
        from room_manager import RoomManager

        rm = RoomManager()
        ws = MagicMock()
        with pytest.raises(KeyError):
            rm.register_connection("nope", ws)

    def test_unregister_connection_removes(self):
        from room_manager import RoomManager

        rm = RoomManager()
        rm.create_room(room_id="A")
        ws = MagicMock()
        rm.register_connection("A", ws)

        rm.unregister_connection("A", ws)

        room = rm.get_room("A")
        assert ws not in room.connections

    def test_unregister_unknown_room_is_noop(self):
        from room_manager import RoomManager

        rm = RoomManager()
        ws = MagicMock()
        # Should not raise
        rm.unregister_connection("ghost", ws)

    def test_get_or_create_default_room(self):
        """룸 ID 없이 접속 시 기본 룸 ("default") 사용"""
        from room_manager import DEFAULT_ROOM_ID, RoomManager

        rm = RoomManager()
        room = rm.get_or_create_room(DEFAULT_ROOM_ID)

        assert room.room_id == DEFAULT_ROOM_ID
        # Calling again returns the same room (idempotent)
        same_room = rm.get_or_create_room(DEFAULT_ROOM_ID)
        assert same_room is room


# ---------------------------------------------------------------------------
# Room isolation — different rooms must not share state
# ---------------------------------------------------------------------------
class TestRoomIsolation:
    """서로 다른 룸의 연결이 서로의 메시지를 받지 않아야 한다."""

    def test_connections_are_isolated_between_rooms(self):
        from room_manager import RoomManager

        rm = RoomManager()
        rm.create_room(room_id="A")
        rm.create_room(room_id="B")

        ws_a = MagicMock(name="ws_a")
        ws_b = MagicMock(name="ws_b")
        rm.register_connection("A", ws_a)
        rm.register_connection("B", ws_b)

        room_a = rm.get_room("A")
        room_b = rm.get_room("B")

        assert ws_a in room_a.connections
        assert ws_a not in room_b.connections
        assert ws_b in room_b.connections
        assert ws_b not in room_a.connections

    def test_language_settings_are_per_room(self):
        from room_manager import RoomManager

        rm = RoomManager()
        room_a = rm.create_room(room_id="A")
        room_b = rm.create_room(room_id="B")

        room_a.language_settings["input_lang"] = "en"
        room_a.language_settings["output_lang"] = "ko"

        # Mutating room A must not affect room B
        assert room_b.language_settings["input_lang"] == "auto"
        assert room_b.language_settings["output_lang"] == "ko"

    def test_broadcast_only_targets_room_members(self):
        """broadcast_to_room should only send to that room's connections."""
        from room_manager import RoomManager

        rm = RoomManager()
        rm.create_room(room_id="A")
        rm.create_room(room_id="B")

        ws_a = AsyncMock(name="ws_a")
        ws_b = AsyncMock(name="ws_b")
        rm.register_connection("A", ws_a)
        rm.register_connection("B", ws_b)

        payload = {"type": "test", "value": "hello"}
        asyncio.run(rm.broadcast_to_room("A", payload))

        # ws_a should have been sent to once
        ws_a.send.assert_awaited_once()
        sent_arg = ws_a.send.await_args.args[0]
        assert json.loads(sent_arg) == payload
        # ws_b should NOT have been called
        ws_b.send.assert_not_awaited()

    def test_broadcast_to_unknown_room_is_noop(self):
        from room_manager import RoomManager

        rm = RoomManager()
        # Should not raise
        asyncio.run(rm.broadcast_to_room("ghost", {"type": "x"}))


# ---------------------------------------------------------------------------
# Auth integration: room_id routing in handle_openai_websocket flow
# ---------------------------------------------------------------------------
@pytest.fixture
def db_with_user(tmp_path):
    """임시 DB + 활성 사용자 1명"""
    from database import DatabaseManager, User

    db = DatabaseManager(str(tmp_path / "test_room_manager.db"))
    user_model = User(db)
    user_model.create_user(
        username="operator1",
        password="pw",
        role="user",
        usage_limit_seconds=3600,
    )
    return user_model


def _make_ws(messages):
    """Create an AsyncMock websocket that yields the given messages on recv()."""
    ws = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)
    # recv() returns each message in order; subsequent calls would block indefinitely
    iter_msgs = iter(messages)

    async def _recv():
        try:
            return next(iter_msgs)
        except StopIteration as exc:
            # Mimic a closed connection on subsequent recv()
            raise asyncio.CancelledError() from exc

    ws.recv = _recv
    ws.send = AsyncMock()
    return ws


def _sent_messages(ws):
    return [json.loads(call.args[0]) for call in ws.send.call_args_list]


class TestAuthRoomRouting:
    """auth 메시지에 room_id가 있을 때 RoomManager에 등록되는지 검증."""

    def test_auth_with_room_id_registers_connection(self, db_with_user):
        """auth 메시지에 room_id 포함 -> 해당 룸에 연결이 등록된다."""
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        rm = RoomManager()
        rm.create_room(room_id="hall-A")

        db_user = db_with_user.get_user_by_username("operator1")
        auth_msg = json.dumps(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "operator1",
                    "role": "user",
                },
                "room_id": "hall-A",
                "language_settings": {"input_lang": "en", "output_lang": "ko"},
            }
        )
        ws = AsyncMock()
        ws.remote_address = ("127.0.0.1", 1)
        ws.recv = AsyncMock(return_value=auth_msg)
        ws.send = AsyncMock()

        with patch("websocket_handler.get_user_model", return_value=db_with_user):
            with patch("websocket_handler._room_manager", rm):
                result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["room_id"] == "hall-A"
        # The connection is registered to room hall-A
        assert ws in rm.get_room("hall-A").connections

    def test_auth_without_room_id_uses_default(self, db_with_user):
        """room_id 없이 접속 시 기본 룸 'default'에 배정된다 (하위 호환)."""
        from room_manager import DEFAULT_ROOM_ID, RoomManager
        from websocket_handler import _authenticate_client

        rm = RoomManager()
        # Do NOT create the default room ahead of time — handler should auto-create.

        db_user = db_with_user.get_user_by_username("operator1")
        auth_msg = json.dumps(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "operator1",
                    "role": "user",
                },
            }
        )
        ws = AsyncMock()
        ws.remote_address = ("127.0.0.1", 2)
        ws.recv = AsyncMock(return_value=auth_msg)
        ws.send = AsyncMock()

        with patch("websocket_handler.get_user_model", return_value=db_with_user):
            with patch("websocket_handler._room_manager", rm):
                result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["room_id"] == DEFAULT_ROOM_ID
        assert ws in rm.get_room(DEFAULT_ROOM_ID).connections

    def test_auth_with_unknown_room_id_returns_error(self, db_with_user):
        """존재하지 않는 룸 ID 접속 시 auth_error를 반환하고 None을 리턴한다."""
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        rm = RoomManager()
        # rm has no rooms — every named room is "unknown"
        # We only auto-create the default room; explicit unknown IDs should fail.

        db_user = db_with_user.get_user_by_username("operator1")
        auth_msg = json.dumps(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "operator1",
                    "role": "user",
                },
                "room_id": "non-existent-room",
            }
        )
        ws = AsyncMock()
        ws.remote_address = ("127.0.0.1", 3)
        ws.recv = AsyncMock(return_value=auth_msg)
        ws.send = AsyncMock()

        with patch("websocket_handler.get_user_model", return_value=db_with_user):
            with patch("websocket_handler._room_manager", rm):
                result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _sent_messages(ws)
        # An auth_error must be sent
        assert any(msg.get("type") == "auth_error" for msg in sent)
        # No auth_success on unknown room
        assert all(msg.get("type") != "auth_success" for msg in sent)
