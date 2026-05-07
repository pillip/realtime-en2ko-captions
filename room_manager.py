"""
RoomManager 모듈 (ISSUE-25)

여러 오퍼레이터가 동시에 독립적인 자막 세션을 운영할 수 있도록
WebSocket 연결을 룸 단위로 격리한다.

Design notes:
- 인메모리 dict로 `room_id → RoomState`를 관리한다.
- DB 영속화는 ISSUE-26에서 다룬다.
- 모든 클라이언트-노출 에러 메시지는 generic하게 유지한다 (RL-006).
- 서버는 client-supplied identity를 신뢰하지 않는다 (RL-002):
  room_id는 RoomManager에 사전 등록된 것만 허용하며 실제 연결 등록은
  서버 측에서만 수행된다.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

DEFAULT_ROOM_ID = "default"


def _new_room_id() -> str:
    """Generate a short, URL-safe, unguessable room id."""
    return secrets.token_urlsafe(8)


def _default_language_settings() -> dict[str, str]:
    return {"input_lang": "auto", "output_lang": "ko"}


@dataclass
class RoomState:
    """Per-room runtime state.

    Fields per Implementation Notes (ISSUE-25):
      room_id, connections, language_settings, translate_client,
      bedrock_client, created_at, last_activity
    """

    room_id: str
    connections: set = field(default_factory=set)
    language_settings: dict[str, str] = field(
        default_factory=_default_language_settings
    )
    translate_client: Any | None = None
    bedrock_client: Any | None = None
    bedrock_available: bool = False
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Update last_activity to the current time."""
        self.last_activity = time.time()


class RoomManager:
    """In-memory registry of active rooms keyed by room_id."""

    def __init__(self) -> None:
        self._rooms: dict[str, RoomState] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def create_room(self, room_id: str | None = None) -> RoomState:
        """Create a new room. Generates a unique id when none supplied.

        Raises:
            ValueError: if the supplied room_id already exists.
        """
        if room_id is None:
            # Generate a unique id; retry on the (vanishingly rare) collision.
            for _ in range(8):
                candidate = _new_room_id()
                if candidate not in self._rooms:
                    room_id = candidate
                    break
            else:  # pragma: no cover — extremely unlikely
                raise RuntimeError("Failed to generate a unique room id")

        if room_id in self._rooms:
            raise ValueError(f"Room id already exists: {room_id}")

        state = RoomState(room_id=room_id)
        self._rooms[room_id] = state
        return state

    def get_room(self, room_id: str) -> RoomState | None:
        """Return the room state for room_id, or None if not registered."""
        return self._rooms.get(room_id)

    def get_or_create_room(self, room_id: str) -> RoomState:
        """Return the room for room_id, creating it if missing.

        Used for the default-room fallback so that a brand-new server
        accepts connections without explicit room provisioning.
        """
        room = self._rooms.get(room_id)
        if room is None:
            room = RoomState(room_id=room_id)
            self._rooms[room_id] = room
        return room

    def delete_room(self, room_id: str) -> bool:
        """Remove a room. Returns True if it existed, False otherwise."""
        return self._rooms.pop(room_id, None) is not None

    def list_rooms(self) -> list[str]:
        """Snapshot of registered room ids."""
        return list(self._rooms.keys())

    # ------------------------------------------------------------------
    # Connection registration
    # ------------------------------------------------------------------
    def register_connection(self, room_id: str, websocket: Any) -> None:
        """Add a websocket to the named room.

        Raises:
            KeyError: if the room does not exist.
        """
        room = self._rooms.get(room_id)
        if room is None:
            raise KeyError(room_id)
        room.connections.add(websocket)
        room.touch()

    def unregister_connection(self, room_id: str, websocket: Any) -> None:
        """Remove a websocket from the named room. No-op if room or
        connection is unknown."""
        room = self._rooms.get(room_id)
        if room is None:
            return
        room.connections.discard(websocket)
        room.touch()

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------
    async def broadcast_to_room(self, room_id: str, payload: dict) -> None:
        """Send a JSON-encoded payload to every connection in the room.

        Failed sends are logged and skipped — they should not break the
        broadcast loop. No-op for unknown rooms.
        """
        room = self._rooms.get(room_id)
        if room is None:
            return
        if not room.connections:
            return

        message = json.dumps(payload)
        # Snapshot connections to avoid mutation during iteration
        coros = []
        for ws in list(room.connections):
            coros.append(self._safe_send(ws, message))
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
        room.touch()

    @staticmethod
    async def _safe_send(websocket: Any, message: str) -> None:
        try:
            await websocket.send(message)
        except Exception as e:
            # Log server-side; never propagate raw exception text to clients.
            print(f"[Room] broadcast send failed: {e!r}")
