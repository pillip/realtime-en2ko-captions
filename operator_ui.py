"""
Operator-UI 사이드바를 위한 순수 함수 모듈 (ISSUE-27).

이 모듈에 있는 모든 함수는 부수효과가 없어야 한다 (RL-001 / RL-005):
- streamlit, DB, 네트워크 호출 금지
- 입력 → 출력 매핑만 담당하므로 import-friendly 하다

app.py 사이드바는 이 모듈의 함수를 호출하기만 하고, 자신은 Streamlit
표시 책임만 진다. 이렇게 분리하지 않으면 test에서 streamlit 부수효과
때문에 import-time 의존성이 폭발한다 (RL-001).
"""

from __future__ import annotations

from typing import Any

# 사용자에게 보여줄 룸 상태 라벨. 백엔드 status 코드는 영어이지만
# 오퍼레이터 UI는 한국어로 표기한다 (issues.md AC4).
_ROOM_STATUS_LABELS: dict[str, str] = {
    "waiting": "대기",
    "active": "활성",
    "inactive": "비활성",
    "closed": "종료",
}


def format_room_status_label(status: str) -> str:
    """Map a backend room status code to its Korean UI label.

    Unknown statuses fall back to the raw string so a future status
    addition stays visible in the UI instead of silently rendering as
    empty (which would be a worse UX than a debug-y label).
    """
    return _ROOM_STATUS_LABELS.get(status, status)


def has_assigned_rooms(rooms: list[dict[str, Any]] | None) -> bool:
    """True iff the operator has at least one assigned room.

    Accepts ``None`` (e.g., DB lookup error path) and treats it as the
    empty case so the empty-state message is the safe default branch.
    """
    if not rooms:
        return False
    return True


def build_room_dropdown_options(
    rooms: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build label/id pairs for a Streamlit ``selectbox``.

    Each option contains:
      - ``id``: the room's stable id (sent to bootstrap as ``room_id``)
      - ``label``: human-friendly text including the localized status

    Order is preserved from the input — callers are expected to pass
    rooms in the order they want shown (typically created_at DESC, the
    same order :meth:`Room.list_by_operator` returns).
    """
    options: list[dict[str, str]] = []
    for room in rooms:
        status_label = format_room_status_label(room.get("status", ""))
        name = room.get("name", room.get("id", ""))
        options.append(
            {
                "id": room["id"],
                "label": f"{name} ({status_label})",
            }
        )
    return options


def select_default_room(
    rooms: list[dict[str, Any]],
    last_selected_id: str | None,
) -> str | None:
    """Pick the default room id for the dropdown on render.

    Strategy:
      1. If ``last_selected_id`` is still in ``rooms``, keep it
         (preserves UX across Streamlit reruns).
      2. Otherwise return the first room's id.
      3. Empty list → ``None`` (caller renders empty-state UI).
    """
    if not rooms:
        return None
    ids = [r["id"] for r in rooms]
    if last_selected_id and last_selected_id in ids:
        return last_selected_id
    return ids[0]


def build_bootstrap_payload(
    *,
    action: str,
    openai_session: Any,
    websocket_port: int | None,
    user_info: dict[str, Any] | None,
    room_id: str | None,
    room_name: str | None = None,
    view_url: str | None = None,
    qr_data_url: str | None = None,
) -> dict[str, Any]:
    """Build the dict that ``app.py`` injects into ``components/webrtc.html``.

    Centralizing this in a pure function lets us unit-test that:
      - ISSUE-27 AC2: ``room_id`` is forwarded to the browser bootstrap
      - ISSUE-28 AC3: ``room_name`` is forwarded so the browser welcome
        screen can render "🎙️ {room_name} — 실시간 자막"
      - ISSUE-32: ``view_url`` (인쇄/공유용) 와 ``qr_data_url`` (인라인
        ``<img src>``) 가 함께 전달되어 오퍼레이터 대기 화면에 QR 코드를
        표시한다.
      - all required fields are populated for the existing webrtc.html
      - the result stays JSON-serializable (webrtc.html receives this
        via ``json.dumps``)

    ``room_name`` / ``view_url`` / ``qr_data_url`` 은 keyword-only & 옵셔널
    (defaults to ``None``) so existing call sites and tests that only
    pass ``room_id`` keep working — this preserves backward compat with
    ISSUE-27 / ISSUE-28.

    Use keyword-only args so callers don't accidentally swap argument
    order — this protects the security-sensitive ``user_info`` slot.
    """
    return {
        "action": action,
        "openai_session": openai_session,
        "service": "openai_realtime",
        "websocket_port": websocket_port,
        "user_info": user_info,
        "room_id": room_id,
        "room_name": room_name,
        "view_url": view_url,
        "qr_data_url": qr_data_url,
    }
