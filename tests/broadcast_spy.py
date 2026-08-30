"""control 발행 횟수를 세는 BroadcastManager 스파이 (ISSUE-53).

`tests/wcag.py` 와 같은 이유로 공용 모듈이다 (RL-001): 인증 경로
(`tests/test_websocket_auth.py`)와 메시지 루프(`tests/test_websocket_handler.py`)
가 **같은** 발행 규칙을 검증하는데, 스파이를 각자 복사해 두면 한쪽만 고쳐지고
그 순간 두 경로의 계약이 갈라진다.

진짜 :class:`sse_broadcast.BroadcastManager` 를 상속한다 — 발행 횟수만 가로채고
상태 머지/큐 주입은 프로덕션 코드가 그대로 돌게 두어야, "발행했다" 는 단언이
실제 fan-out 을 통과한 사실을 뜻한다 (스텁으로 갈아치우면 통과만 하고 아무것도
증명하지 않는다 — RL-004).
"""

from __future__ import annotations

from typing import Any

from sse_broadcast import BroadcastManager


class SpyBroadcastManager(BroadcastManager):
    """`publish_control` 호출을 (room_id, payload) 로 기록한다."""

    def __init__(self) -> None:
        super().__init__()
        self.control_calls: list[tuple[str, dict[str, Any]]] = []

    async def publish_control(self, room_id: str, payload: dict[str, Any]) -> None:
        self.control_calls.append((room_id, dict(payload)))
        await super().publish_control(room_id, payload)

    @property
    def control_count(self) -> int:
        return len(self.control_calls)
