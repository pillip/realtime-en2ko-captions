"""
RoomManager 모듈 (ISSUE-25, ISSUE-26)

여러 오퍼레이터가 동시에 독립적인 자막 세션을 운영할 수 있도록
WebSocket 연결을 룸 단위로 격리한다.

Design notes:
- 인메모리 dict로 `room_id → RoomState`를 관리한다.
- ISSUE-26: 옵션으로 `room_repository`(database.Room)을 주입받아 룸
  생성/종료를 DB에 영속화하며, `hydrate_from_db()` 로 서버 재시작 시
  waiting/active/inactive 룸을 복원한다. 레거시 호출자(테스트, 기본
  룸)와 호환을 위해 repository 가 None 인 경우 메모리 전용으로 동작한다.
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
    """In-memory registry of active rooms keyed by room_id.

    When a `room_repository` (database.Room) is supplied, create_room and
    close_room also persist to DB and hydrate_from_db() restores rooms
    on server start (ISSUE-26).
    """

    def __init__(self, room_repository: Any | None = None) -> None:
        self._rooms: dict[str, RoomState] = {}
        # database.Room | None  — optional persistence layer.
        # Typed as Any to avoid an import cycle with database.py.
        self._repo = room_repository

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def create_room(
        self,
        room_id: str | None = None,
        *,
        name: str | None = None,
        created_by: int | None = None,
        input_lang: str = "auto",
        output_lang: str = "ko",
        timeout_minutes: int = 30,
    ) -> RoomState:
        """Create a new room. Generates a unique id when none supplied.

        When a repository is attached, the room is persisted with the
        supplied `name`/`created_by`. Without a repository these fields
        are ignored (legacy in-memory mode for tests + the default room).

        Raises:
            ValueError: if the supplied room_id already exists in memory.
            ValueError: if persistence is requested but `name` or
                `created_by` is missing.
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

        if self._repo is not None:
            # Persist first so that we never put a room into memory that
            # is not durable. Required fields enforced here so the caller
            # gets a clear error rather than a deferred IntegrityError.
            if name is None or created_by is None:
                raise ValueError(
                    "create_room with persistence requires `name` and `created_by`"
                )
            persisted = self._repo.create(
                room_id=room_id,
                name=name,
                created_by=created_by,
                input_lang=input_lang,
                output_lang=output_lang,
                timeout_minutes=timeout_minutes,
            )
            if persisted is None:
                # PK collision in DB but not in memory — extremely rare,
                # surfaces as ValueError to match the in-memory branch.
                raise ValueError(f"Room id already exists: {room_id}")

        state = RoomState(room_id=room_id)
        if name is not None:
            state.language_settings = {
                "input_lang": input_lang,
                "output_lang": output_lang,
            }
        self._rooms[room_id] = state
        return state

    def get_room(self, room_id: str) -> RoomState | None:
        """Return the room state for room_id, or None if not registered."""
        return self._rooms.get(room_id)

    def get_or_create_room(self, room_id: str) -> RoomState:
        """Return the room for room_id, creating it if missing.

        Used for the default-room fallback so that a brand-new server
        accepts connections without explicit room provisioning. This
        path NEVER touches the repository — the default room is an
        in-memory convenience and not a managed conference room.
        """
        room = self._rooms.get(room_id)
        if room is None:
            room = RoomState(room_id=room_id)
            self._rooms[room_id] = room
        return room

    def delete_room(self, room_id: str) -> bool:
        """Remove a room from memory. Returns True if it existed.

        This is the legacy in-memory-only operation kept for the
        default room and unit tests. For the managed-room lifecycle
        use `close_room()` which also persists the closed status.
        """
        return self._rooms.pop(room_id, None) is not None

    def close_room(self, room_id: str) -> bool:
        """Transition the room to 'closed' (DB) and remove from memory.

        Idempotent: returns False if the room is unknown both in memory
        and in the repository.
        """
        existed = self._rooms.pop(room_id, None) is not None
        if self._repo is not None:
            try:
                # transition_status returns False for unknown rooms and
                # raises InvalidRoomTransition only for impossible edges
                # (e.g. closed → closed). Closed→closed is idempotent.
                self._repo.transition_status(room_id, "closed")
            except Exception as e:  # InvalidRoomTransition or DB error
                # Already-closed rooms hit this branch; treat as success.
                if existed:
                    return True
                # Otherwise, surface as a no-op false but keep the
                # exception detail server-side (RL-006: never propagate
                # raw text to clients; this is a server-side print).
                print(f"[Room] close_room: ignoring repo error: {e!r}")
        return existed

    def list_rooms(self) -> list[str]:
        """Snapshot of registered room ids."""
        return list(self._rooms.keys())

    # ------------------------------------------------------------------
    # Persistence integration (ISSUE-26)
    # ------------------------------------------------------------------
    def hydrate_from_db(self) -> int:
        """Re-load DB rooms with hydratable status into memory.

        Called once at server start so that operators can resume sessions
        after a restart without manual intervention. Closed rooms are
        terminal and intentionally NOT hydrated.

        Returns the number of rooms loaded. No-op if repository missing.
        """
        if self._repo is None:
            return 0
        loaded = 0
        # Source of truth — Room.HYDRATABLE_STATUSES.
        statuses = list(
            getattr(
                self._repo, "HYDRATABLE_STATUSES", ("waiting", "active", "inactive")
            )
        )
        for row in self._repo.list_by_status(statuses):
            rid = row["id"]
            if rid in self._rooms:
                continue
            state = RoomState(room_id=rid)
            state.language_settings = {
                "input_lang": row.get("input_lang") or "auto",
                "output_lang": row.get("output_lang") or "ko",
            }
            self._rooms[rid] = state
            loaded += 1
        return loaded

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
