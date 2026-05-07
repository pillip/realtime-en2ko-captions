"""
operator_ui.py 순수 함수 단위 테스트 (ISSUE-27).

ISSUE-27 사이드바에서 사용하는 룸 선택 로직을 import-friendly 모듈에 분리해
테스트 가능하게 만든다 (RL-001, RL-005).

검증 대상:
- format_room_status_label: status 코드 → 사용자에게 보일 한국어 라벨
- select_default_room: 마지막 선택 룸 우선, 없으면 첫 룸 (active > waiting > inactive)
- build_bootstrap_payload: app.py가 webrtc.html에 주입하는 dict 생성
- build_room_dropdown_options: 드롭다운 라벨/내부 id 매핑
- has_assigned_rooms: 빈 상태 안내 분기
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# operator_ui.py 자체는 streamlit 의존이 없어야 하지만, 다른 모듈을 통해 임포트 사
# 슬을 따라가다 streamlit이 끌려 들어올 수 있으므로 안전하게 mock한다.
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ---------------------------------------------------------------------------
# format_room_status_label
# ---------------------------------------------------------------------------
class TestFormatRoomStatusLabel:
    def test_waiting_label(self):
        from operator_ui import format_room_status_label

        assert format_room_status_label("waiting") == "대기"

    def test_active_label(self):
        from operator_ui import format_room_status_label

        assert format_room_status_label("active") == "활성"

    def test_inactive_label(self):
        from operator_ui import format_room_status_label

        assert format_room_status_label("inactive") == "비활성"

    def test_closed_label(self):
        from operator_ui import format_room_status_label

        assert format_room_status_label("closed") == "종료"

    def test_unknown_status_falls_back_to_raw(self):
        """알 수 없는 상태는 원문 그대로 표시 (디버깅 용이성, 빈 라벨 방지)."""
        from operator_ui import format_room_status_label

        assert format_room_status_label("unknown") == "unknown"


# ---------------------------------------------------------------------------
# build_room_dropdown_options
# ---------------------------------------------------------------------------
class TestBuildRoomDropdownOptions:
    def test_empty_list_returns_empty(self):
        from operator_ui import build_room_dropdown_options

        assert build_room_dropdown_options([]) == []

    def test_single_room_label_includes_status(self):
        """라벨에 룸 이름과 상태가 모두 표시되어야 AC '상태 표시'를 만족."""
        from operator_ui import build_room_dropdown_options

        rooms = [{"id": "r-1", "name": "A홀", "status": "active"}]
        opts = build_room_dropdown_options(rooms)
        assert len(opts) == 1
        assert opts[0]["id"] == "r-1"
        assert "A홀" in opts[0]["label"]
        assert "활성" in opts[0]["label"]

    def test_multiple_rooms_preserve_order(self):
        from operator_ui import build_room_dropdown_options

        rooms = [
            {"id": "r-1", "name": "A홀", "status": "active"},
            {"id": "r-2", "name": "B홀", "status": "waiting"},
            {"id": "r-3", "name": "C홀", "status": "inactive"},
        ]
        opts = build_room_dropdown_options(rooms)
        ids = [o["id"] for o in opts]
        assert ids == ["r-1", "r-2", "r-3"]

    def test_label_contains_status_for_each_known_status(self):
        from operator_ui import build_room_dropdown_options

        rooms = [
            {"id": "r-w", "name": "W", "status": "waiting"},
            {"id": "r-a", "name": "A", "status": "active"},
            {"id": "r-i", "name": "I", "status": "inactive"},
        ]
        labels = [o["label"] for o in build_room_dropdown_options(rooms)]
        assert any("대기" in label for label in labels)
        assert any("활성" in label for label in labels)
        assert any("비활성" in label for label in labels)


# ---------------------------------------------------------------------------
# select_default_room
# ---------------------------------------------------------------------------
class TestSelectDefaultRoom:
    def test_empty_rooms_returns_none(self):
        from operator_ui import select_default_room

        assert select_default_room([], None) is None
        assert select_default_room([], "r-1") is None

    def test_last_selected_used_when_still_present(self):
        """마지막 선택이 여전히 목록에 있으면 그것을 사용한다.

        UX: 새로고침 후에도 같은 룸이 선택된 상태로 유지된다.
        """
        from operator_ui import select_default_room

        rooms = [
            {"id": "r-1", "name": "A", "status": "waiting"},
            {"id": "r-2", "name": "B", "status": "active"},
        ]
        assert select_default_room(rooms, "r-2") == "r-2"

    def test_last_selected_ignored_when_missing(self):
        """마지막 선택 룸이 더 이상 배정되지 않았으면 첫 번째 룸 폴백."""
        from operator_ui import select_default_room

        rooms = [
            {"id": "r-1", "name": "A", "status": "waiting"},
            {"id": "r-2", "name": "B", "status": "active"},
        ]
        assert select_default_room(rooms, "r-99") == "r-1"

    def test_no_last_selected_returns_first(self):
        from operator_ui import select_default_room

        rooms = [
            {"id": "r-1", "name": "A", "status": "waiting"},
            {"id": "r-2", "name": "B", "status": "active"},
        ]
        assert select_default_room(rooms, None) == "r-1"


# ---------------------------------------------------------------------------
# has_assigned_rooms
# ---------------------------------------------------------------------------
class TestHasAssignedRooms:
    def test_empty_list_false(self):
        from operator_ui import has_assigned_rooms

        assert has_assigned_rooms([]) is False

    def test_non_empty_list_true(self):
        from operator_ui import has_assigned_rooms

        assert has_assigned_rooms([{"id": "r-1"}]) is True

    def test_none_treated_as_empty(self):
        """DB 에러 등으로 None이 와도 안내 메시지 분기로 수렴."""
        from operator_ui import has_assigned_rooms

        assert has_assigned_rooms(None) is False


# ---------------------------------------------------------------------------
# build_bootstrap_payload
# ---------------------------------------------------------------------------
class TestBuildBootstrapPayload:
    def test_includes_room_id_when_provided(self):
        """ISSUE-27 핵심 AC: bootstrap JSON에 room_id가 포함된다."""
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=8765,
            user_info={"id": 1, "username": "op1"},
            room_id="r-1",
        )
        assert payload["room_id"] == "r-1"

    def test_room_id_omitted_when_none(self):
        """admin이 평소처럼 default 룸을 쓰는 경로 (room_id 없음). RL-006."""
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=8765,
            user_info={"id": 1, "username": "admin"},
            room_id=None,
        )
        # webrtc.html은 BOOT.room_id의 진위만 보면 되므로 키가 None으로
        # 들어가 있는 것은 허용. 다만 명시적인 fallback 동작 보장:
        assert payload.get("room_id") is None

    def test_contains_required_fields(self):
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="start",
            openai_session={"client_secret": "sk-xxx"},
            websocket_port=8765,
            user_info={"id": 1, "username": "op1"},
            room_id="r-1",
        )
        assert payload["action"] == "start"
        assert payload["openai_session"] == {"client_secret": "sk-xxx"}
        assert payload["websocket_port"] == 8765
        assert payload["user_info"]["username"] == "op1"
        assert payload["service"] == "openai_realtime"

    def test_payload_is_json_serializable(self):
        """webrtc.html에 json.dumps로 주입되므로 직렬화 가능해야 한다."""
        import json

        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=None,
            user_info={"id": 1, "username": "op1"},
            room_id="r-1",
        )
        # raises TypeError if non-serializable
        json.dumps(payload)
