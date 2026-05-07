"""
ISSUE-28: 브라우저 컴포넌트(webrtc.html)에 room_id 전달 및 격리 테스트.

검증 목표 (issues.md AC):
- AC1: WebSocket auth 메시지에 room_id 필드가 포함된다
- AC2: BOOT.room_id 미존재 시 'default' 룸으로 폴백 (하위 호환)
- AC3: 웰컴 화면에 현재 룸 이름이 표시된다 (BOOT.room_name이 있을 때)

또한 RL-014 (hotfix regression test) 정신을 따라, 각 변경된 동작별로
명확한 단일 어서션 테스트를 둔다 — 회귀 시 어느 동작이 깨졌는지 즉시
드러나도록 한다.

테스트 전략은 기존 프로젝트 패턴 (tests/test_viewport_responsive.py,
tests/test_fullscreen.py) 의 string-match 방식을 재사용한다.
"""

from pathlib import Path

WEBRTC_HTML = Path(__file__).parent.parent / "components" / "webrtc.html"


def _read_webrtc() -> str:
    return WEBRTC_HTML.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC1: WebSocket auth 메시지에 room_id 필드가 포함된다
# ---------------------------------------------------------------------------
class TestAuthMessageIncludesRoomId:
    def test_boot_room_id_is_referenced(self):
        """webrtc.html이 BOOT.room_id를 읽어야 한다 (AC1)."""
        html = _read_webrtc()
        assert "BOOT.room_id" in html

    def test_auth_message_contains_room_id_key(self):
        """auth payload에 'room_id' 키가 포함되어야 한다 (AC1).

        단순한 substring으로는 다른 위치의 room_id를 잡을 수 있어 type:'auth'
        블록 인근(약 600자)을 추출해 검증한다.
        """
        html = _read_webrtc()
        idx = html.find("type: 'auth'")
        assert idx != -1, "WebSocket auth 메시지를 찾을 수 없다"
        window = html[idx : idx + 600]
        assert "room_id" in window


# ---------------------------------------------------------------------------
# AC2: BOOT.room_id 미존재 시 'default' 룸으로 폴백 (하위 호환)
# ---------------------------------------------------------------------------
class TestRoomIdDefaultFallback:
    def test_default_fallback_string_present(self):
        """폴백 문자열 'default'가 BOOT.room_id 인근에 존재해야 한다 (AC2).

        구현 형태는 ``BOOT.room_id || 'default'`` 또는 동일한 의미의
        삼항/널 코얼리스가 모두 허용되므로, 인접한 영역에 'default'가
        등장하는지로 확인한다.
        """
        html = _read_webrtc()
        idx = html.find("BOOT.room_id")
        assert idx != -1, "BOOT.room_id 참조가 없다"
        window = html[idx : idx + 80]
        assert "'default'" in window or '"default"' in window


# ---------------------------------------------------------------------------
# AC3: 웰컴 화면에 현재 룸 이름이 표시된다 (BOOT.room_name이 있을 때)
# ---------------------------------------------------------------------------
class TestWelcomeScreenShowsRoomName:
    def test_boot_room_name_is_referenced(self):
        """webrtc.html이 BOOT.room_name을 읽어야 한다 (AC3)."""
        html = _read_webrtc()
        assert "BOOT.room_name" in html

    def test_room_name_used_in_welcome_title(self):
        """웰컴 타이틀이 BOOT.room_name을 합성해 보여줘야 한다 (AC3).

        updateWelcomeTranslationRules() 내에서 title을 결정하므로, 해당
        함수 본문 안에 BOOT.room_name 참조가 있어야 한다.
        """
        html = _read_webrtc()
        marker = "function updateWelcomeTranslationRules"
        start = html.find(marker)
        assert start != -1, "updateWelcomeTranslationRules 함수가 없다"
        body = html[start : start + 2000]
        assert "BOOT.room_name" in body


# ---------------------------------------------------------------------------
# 회귀 보호 — 기존 동작 유지 (RL-014 정신)
# ---------------------------------------------------------------------------
class TestRegressionGuard:
    def test_user_info_still_in_auth(self):
        """기존 user_info 전송 동작이 깨지지 않아야 한다."""
        html = _read_webrtc()
        idx = html.find("type: 'auth'")
        assert idx != -1
        window = html[idx : idx + 600]
        assert "BOOT.user_info" in window

    def test_language_settings_still_in_auth(self):
        """기존 language_settings 전송 동작이 깨지지 않아야 한다 (ISSUE-2)."""
        html = _read_webrtc()
        idx = html.find("type: 'auth'")
        assert idx != -1
        window = html[idx : idx + 600]
        assert "language_settings" in window
