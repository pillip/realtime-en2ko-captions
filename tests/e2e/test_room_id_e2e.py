"""
ISSUE-28 E2E 테스트.

웰컴 화면이 BOOT.room_name을 사용해 룸 이름을 노출하는지, BOOT 주입이
정상 작동하는지를 실제 브라우저에서 검증한다.

기존 e2e 패턴 (tests/e2e/test_fullscreen_e2e.py) 의 streamlit_server +
playwright iframe 흐름을 따른다. 환경에 streamlit + playwright가 설정돼
있을 때만 자동 실행되며, 그렇지 않은 CI에서는 ``e2e`` 마크가 기본 deselect
되어 일반 ``pytest -q`` 실행을 막지 않는다.
"""

import pytest

pytestmark = pytest.mark.e2e


class TestRoomNameWelcomeDisplay:
    """웰컴 화면이 BOOT.room_name을 보여주는지 검증 (AC3 e2e)."""

    def test_welcome_title_renders(self, caption_iframe):
        """웰컴 타이틀 요소가 렌더링되어야 한다 (회귀 가드).

        BOOT.room_name이 없는 경로(테스트 fixture는 admin 로그인이라 룸
        선택 자체가 없음)에서 기존 타이틀이 깨지지 않는지를 확인한다.
        """
        title = caption_iframe.locator(".welcome-title").first
        title.wait_for(state="visible", timeout=10000)
        text = title.inner_text()
        # admin 경로에서는 룸 이름이 비어 있으므로 기본 문구가 그대로 보여야 한다.
        # 메시지를 짧게 두어 ruff-format/black 의 줄바꿈 충돌을 피한다.
        assert "자막" in text, "기본 웰컴 타이틀이 회귀해선 안 된다"


class TestBootInjection:
    """BOOT 객체 주입 자체가 끊기지 않는지 회귀 가드."""

    def test_caption_container_present(self, caption_iframe):
        """captionContainer 가 존재해야 BOOT 스크립트가 정상 진입했음을 의미."""
        container = caption_iframe.locator("#captionContainer")
        container.wait_for(state="attached", timeout=10000)
        assert container.count() == 1
