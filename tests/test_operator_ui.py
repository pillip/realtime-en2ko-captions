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

    # ------------------------------------------------------------------
    # ISSUE-28: room_name forwarded so webrtc.html can show "🎙️ 룸 이름 — ..."
    # ------------------------------------------------------------------
    def test_includes_room_name_when_provided(self):
        """ISSUE-28 AC3: webrtc.html이 룸 이름을 표시할 수 있도록 payload에
        ``room_name``이 포함된다.
        """
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="start",
            openai_session=None,
            websocket_port=8765,
            user_info={"id": 1, "username": "op1"},
            room_id="r-1",
            room_name="A홀 기조연설",
        )
        assert payload["room_name"] == "A홀 기조연설"

    def test_room_name_optional_defaults_to_none(self):
        """admin 또는 room 미선택 경로에서는 room_name이 None이어야 한다.

        webrtc.html은 ``BOOT.room_name``의 진위만 보고 분기하므로 None
        허용. ISSUE-27 호환성을 위해 키워드 인자는 선택적이어야 한다.
        """
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=None,
            user_info=None,
            room_id=None,
        )
        # 기본값이 None이거나 키가 없거나 둘 중 하나만 만족해도 무방하다.
        assert payload.get("room_name") is None

    # ------------------------------------------------------------------
    # ISSUE-32: view_url + qr_data_url forwarded so webrtc.html can show
    # the QR code on the welcome screen for unauthenticated viewers.
    # ------------------------------------------------------------------
    def test_includes_view_url_and_qr_data_url_when_provided(self):
        """ISSUE-32: payload 에 ``view_url`` 와 ``qr_data_url`` 이 그대로
        전달되어야 한다 (webrtc.html 인라인 ``<img>`` 가 사용)."""
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=None,
            user_info={"id": 1, "username": "op1"},
            room_id="r-1",
            room_name="A홀",
            view_url="http://localhost:8766/view/r-1",
            qr_data_url="data:image/png;base64,AAAA",
        )
        assert payload["view_url"] == "http://localhost:8766/view/r-1"
        assert payload["qr_data_url"] == "data:image/png;base64,AAAA"

    def test_view_url_and_qr_data_url_default_to_none(self):
        """admin 또는 룸 미선택 경로에서는 두 필드 모두 None — 기존 호출
        지점이 깨지지 않도록 디폴트가 유지되어야 한다."""
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=None,
            user_info=None,
            room_id=None,
        )
        assert payload.get("view_url") is None
        assert payload.get("qr_data_url") is None


# ---------------------------------------------------------------------------
# select_display_mode (ISSUE-47, TC-065)
# ---------------------------------------------------------------------------
class TestSelectDisplayMode:
    """표시 모드 정규화 — `session_state` 왕복의 유일한 진입점.

    이 함수가 순수 함수인 이유는 RL-001 / RL-005 다. `app.py` 에는 테스트가
    0건이라, 모드 결정 로직이 `app.py` 안에 있으면 영원히 검증되지 않는다.
    """

    def test_none_defaults_to_caption(self):
        """AC1 — 오퍼레이터가 한 번도 건드리지 않은 상태의 기본값."""
        from operator_ui import select_display_mode

        assert select_display_mode(None) == "caption"

    def test_missing_key_defaults_to_caption(self):
        """`session_state.get()` 이 빈 문자열을 돌려주는 경로도 기본값."""
        from operator_ui import select_display_mode

        assert select_display_mode("") == "caption"

    def test_unrecognised_legacy_value_normalises_to_caption(self):
        """과거 버전이 남긴 잔여 문자열 → 조용히 기본값으로 정규화.

        여기서 예외를 던지면 `st.radio(index=...)` 가 ValueError 로 죽어
        오퍼레이터 사이드바 전체가 날아간다 (RL-006).
        """
        from operator_ui import select_display_mode

        assert select_display_mode("presenter") == "caption"
        assert select_display_mode("STAGE") == "caption"
        assert select_display_mode("무대 화면") == "caption"

    def test_stage_round_trips_unchanged(self):
        """AC5 — rerun 을 건너 유효한 선택은 그대로 살아남아야 한다."""
        from operator_ui import select_display_mode

        assert select_display_mode("stage") == "stage"

    def test_caption_round_trips_unchanged(self):
        from operator_ui import select_display_mode

        assert select_display_mode("caption") == "caption"

    def test_non_string_input_normalises_to_caption(self):
        """session_state 는 무엇이든 담을 수 있다 — 타입 사고로 죽지 않는다."""
        from operator_ui import select_display_mode

        assert select_display_mode(0) == "caption"
        assert select_display_mode(["stage"]) == "caption"

    def test_value_whose_equality_raises_is_normalised_not_propagated(self):
        """`isinstance` 검사가 실제로 값을 하는 유일한 경로 (RL-006).

        `x in DISPLAY_MODES` 는 튜플 원소와 `==` 비교를 하므로, `__eq__` 가
        폭발하는 객체가 session_state 에 들어 있으면 예외가 사이드바 전체를
        무너뜨린다. 타입을 먼저 거르면 그 비교 자체가 일어나지 않는다.

        (경계 뮤테이션 M3 이 이 구멍을 찾았다: `isinstance` 를 지워도 int/list
        입력만으로는 아무 테스트도 죽지 않았다 — 그 값들은 조용히 False 를
        돌려주기 때문이다.)
        """
        from operator_ui import select_display_mode

        class Hostile:
            def __eq__(self, other):
                raise RuntimeError("session_state 에 남은 이상한 값")

        assert select_display_mode(Hostile()) == "caption"

    def test_display_modes_are_exactly_two(self):
        """모드가 늘어나면 이 테스트가 먼저 깨져 라벨/부트스트랩 갱신을 강제한다."""
        from operator_ui import DISPLAY_MODES

        assert tuple(DISPLAY_MODES) == ("caption", "stage")

    def test_labels_are_korean_and_cover_every_mode(self):
        """사이드바 라벨은 한국어 — 기본값이 '일반 자막' 이어야 AC1 이 성립한다."""
        from operator_ui import DISPLAY_MODES, format_display_mode_label

        assert format_display_mode_label("caption") == "일반 자막"
        assert format_display_mode_label("stage") == "무대 화면"
        for mode in DISPLAY_MODES:
            assert format_display_mode_label(mode).strip()

    def test_unknown_label_falls_back_to_raw_code(self):
        """format_room_status_label 과 같은 관용 — 알 수 없는 값도 빈칸이 되지 않는다."""
        from operator_ui import format_display_mode_label

        assert format_display_mode_label("mystery") == "mystery"


# ---------------------------------------------------------------------------
# build_bootstrap_payload — display_mode / stage_url (ISSUE-47, TC-066)
# ---------------------------------------------------------------------------
class TestBootstrapPayloadDisplayMode:
    def test_stage_mode_and_url_are_forwarded_verbatim(self):
        """AC3 — 두 필드가 브라우저 부트스트랩까지 그대로 실려야 버튼이 뜬다."""
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=None,
            user_info={"id": 1, "username": "op1"},
            room_id="r1",
            display_mode="stage",
            stage_url="http://localhost:8766/stage/r1",
        )
        assert payload["display_mode"] == "stage"
        assert payload["stage_url"] == "http://localhost:8766/stage/r1"

    def test_omitted_kwargs_are_backward_compatible(self):
        """TC-066 — 기존 호출부(ISSUE-27/28/32)는 키워드를 모른다.

        `room_name` / `view_url` / `qr_data_url` 이 그랬던 것과 동일한
        하위호환 패턴이어야 한다.
        """
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=None,
            user_info=None,
            room_id=None,
        )
        assert payload["display_mode"] == "caption"
        assert payload["stage_url"] is None

    def test_payload_stays_json_serializable(self):
        """webrtc.html 은 이 dict 를 json.dumps 로 받는다."""
        import json

        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=None,
            user_info=None,
            room_id="r1",
            display_mode="stage",
            stage_url="http://localhost:8766/stage/r1",
        )
        assert json.loads(json.dumps(payload))["stage_url"] == (
            "http://localhost:8766/stage/r1"
        )

    def test_existing_fields_are_untouched_by_the_new_kwargs(self):
        """AC2 회귀 가드 — 무대 모드가 파이프라인 관련 필드를 바꾸지 않는다."""
        from operator_ui import build_bootstrap_payload

        common = {
            "action": "start",
            "openai_session": {"client_secret": "ek_x"},
            "websocket_port": 8765,
            "user_info": {"id": 7, "username": "op7"},
            "room_id": "r1",
            "room_name": "A홀",
        }
        caption = build_bootstrap_payload(**common)
        stage = build_bootstrap_payload(
            **common, display_mode="stage", stage_url="http://h/stage/r1"
        )
        for key in (
            "action",
            "openai_session",
            "service",
            "websocket_port",
            "user_info",
            "room_id",
            "room_name",
        ):
            assert caption[key] == stage[key], (
                f"{key} differs between caption and stage mode — the display "
                "mode must not touch the caption pipeline (AC2)"
            )


# ---------------------------------------------------------------------------
# build_bootstrap_payload — 키 집합 상등 (ISSUE-54, TC-089, RL-028)
# ---------------------------------------------------------------------------
# ISSUE-47 F-2 가 실측한 사실: 이 payload 가 바뀌면 `render_component_html` 이
# 만든 문자열이 바뀌고, 그 문자열은 `st.components.v1.html` 의 iframe `srcdoc`
# 이다. `srcdoc` 이 바뀌면 Streamlit 1.48.1 은 문서를 통째로 새로 로드한다 —
# `RTCPeerConnection`, 마이크 스트림, 번역 WebSocket, 자막 스크롤백이 전부
# 사라진다. 즉 **여기에 키를 하나 더하는 것은 세션 중 재조정 가능한 상태를
# 하나 더 잃는 것**이다 (RL-028).
#
# 그래서 이 집합은 화이트리스트다. `"caption_scale" not in payload` 로 쓰지
# 않는 이유가 정확히 RL-004 다 — 그 형태는 **이번에 금지한 그 키**만 막고,
# 다음 사람이 `stage_font_scale` 을 넣으면 조용히 통과한다. 상등이어야 새
# 키가 무엇이든 실패한다.
#
# 이 집합을 늘려야 한다고 판단했다면 그것은 테스트 수정이 아니라 **설계
# 결정**이다: 그 상태가 세션 도중 바뀔 수 있는가? 바뀔 수 있다면 payload 가
# 아니라 이미 열려 있는 WebSocket 으로 보내라 (ISSUE-54 가 자막 배율에 대해
# 택한 길). 정말 payload 여야 한다면 세션 중 `disabled` 로 잠가라 (ISSUE-47
# 이 표시 모드에 대해 택한 길). 그 판단을 PR 에 적는 것이 RL-028 의 요구다.
_BOOTSTRAP_KEYS_AS_OF_ISSUE_47 = {
    "action",
    "openai_session",
    "service",
    "websocket_port",
    "user_info",
    "room_id",
    "room_name",
    "view_url",
    "qr_data_url",
    "display_mode",
    "stage_url",
}


class TestBootstrapPayloadKeySetIsFrozen:
    def test_key_set_equals_the_post_issue_47_set(self):
        """TC-089 — 반환 키 집합이 ISSUE-47 이후와 **정확히** 같다."""
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="start",
            openai_session={"client_secret": "ek_x"},
            websocket_port=8765,
            user_info={"id": 1, "username": "op1"},
            room_id="r1",
            room_name="A홀",
            view_url="http://h/view/r1",
            qr_data_url="data:image/png;base64,AAA",
            display_mode="stage",
            stage_url="http://h/stage/r1",
        )
        added = set(payload) - _BOOTSTRAP_KEYS_AS_OF_ISSUE_47
        removed = _BOOTSTRAP_KEYS_AS_OF_ISSUE_47 - set(payload)
        assert not added, (
            f"new BOOT payload key(s) {sorted(added)} — every payload change "
            "rewrites the component iframe's srcdoc and Streamlit reloads the "
            "document, destroying the RTCPeerConnection, the mic stream, the "
            "translation WebSocket and the caption scrollback mid-session "
            "(ISSUE-47 F-2 / RL-028). Send session-adjustable state over the "
            "already-open WebSocket instead."
        )
        assert not removed, (
            f"BOOT payload key(s) {sorted(removed)} disappeared — webrtc.html "
            "reads them from BOOT and will silently fall back to defaults"
        )

    def test_the_set_is_identical_for_the_minimal_call(self):
        """옵셔널 kwarg 를 하나도 주지 않아도 키 집합은 같다.

        `build_bootstrap_payload` 가 값이 `None` 인 키를 빼는 형태로 바뀌면
        브라우저의 `BOOT.stage_url` 이 `undefined` 가 되고, 그 차이는 위
        테스트만으로는 보이지 않는다 — 위는 항상 전부 채워서 부르기 때문이다.
        """
        from operator_ui import build_bootstrap_payload

        payload = build_bootstrap_payload(
            action="idle",
            openai_session=None,
            websocket_port=None,
            user_info=None,
            room_id=None,
        )
        assert set(payload) == _BOOTSTRAP_KEYS_AS_OF_ISSUE_47
