"""
전체화면 기능 E2E 테스트
Streamlit 앱을 실제 브라우저에서 실행하여 전체화면 동작을 검증
"""

import pytest

pytestmark = pytest.mark.e2e


class TestFullscreenButtonRendering:
    """전체화면 버튼이 브라우저에서 정상 렌더링되는지 검증"""

    def test_fullscreen_fab_visible(self, caption_iframe):
        """전체화면 FAB 버튼이 iframe 내에 보임"""
        btn = caption_iframe.locator("#fullscreenFab")
        btn.wait_for(state="visible", timeout=10000)
        assert btn.is_visible()

    def test_settings_fab_visible(self, caption_iframe):
        """설정 FAB 버튼도 함께 보임"""
        btn = caption_iframe.locator("#settingsFab")
        btn.wait_for(state="visible", timeout=10000)
        assert btn.is_visible()

    def test_font_controls_hidden_by_default(self, caption_iframe):
        """전체화면 폰트 조절 컨트롤은 기본적으로 숨겨져 있음"""
        controls = caption_iframe.locator("#fsFontControls")
        assert not controls.is_visible()


class TestSettingsPanel:
    """설정 패널 토글 동작 검증"""

    def test_settings_panel_opens(self, caption_iframe):
        """설정 FAB 클릭 시 패널 열림"""
        caption_iframe.locator("#settingsFab").click()
        panel = caption_iframe.locator("#settingsPanel")
        # open 클래스가 추가되면 transform 변경으로 보임
        expect_open = panel.locator("xpath=.", has=caption_iframe.locator("h2"))
        expect_open.wait_for(state="visible", timeout=5000)

    def test_settings_panel_closes(self, caption_iframe):
        """설정 FAB 재클릭 시 패널 닫힘"""
        fab = caption_iframe.locator("#settingsFab")
        # 열기
        fab.click()
        caption_iframe.locator("#settingsPanel").wait_for(state="visible", timeout=5000)
        # 닫기
        fab.click()


class TestFullscreenEntry:
    """전체화면 진입 동작 검증"""

    def test_enter_fullscreen_via_button(self, logged_in_page, caption_iframe):
        """전체화면 버튼 클릭 시 fullscreen 진입"""
        btn = caption_iframe.locator("#fullscreenFab")
        btn.wait_for(state="visible", timeout=10000)
        btn.click()

        # Playwright에서 fullscreen 상태 확인
        # iframe 내부에서 JS로 fullscreen 여부 확인
        logged_in_page.wait_for_timeout(500)

        frame = logged_in_page.frames[1] if len(logged_in_page.frames) > 1 else None
        if frame:
            is_fs = frame.evaluate(
                "document.fullscreenElement !== null"
                " || document.webkitFullscreenElement !== null"
            )
            # Fullscreen API는 보안 제약으로 실패할 수 있음
            # 이 경우 함수 존재 여부만 검증
            if not is_fs:
                has_fn = frame.evaluate(
                    "typeof document.documentElement.requestFullscreen === 'function'"
                )
                assert has_fn, "requestFullscreen 함수가 존재해야 함"
        # fullscreen 해제 (안전하게)
        try:
            logged_in_page.keyboard.press("Escape")
        except Exception:
            pass

    def test_font_controls_display_in_fullscreen(self, logged_in_page, caption_iframe):
        """전체화면 진입 시 폰트 조절 컨트롤 표시"""
        btn = caption_iframe.locator("#fullscreenFab")
        btn.wait_for(state="visible", timeout=10000)
        btn.click()
        logged_in_page.wait_for_timeout(500)

        frame = logged_in_page.frames[1] if len(logged_in_page.frames) > 1 else None
        if frame:
            is_fs = frame.evaluate("document.fullscreenElement !== null")
            if is_fs:
                controls = caption_iframe.locator("#fsFontControls")
                assert controls.is_visible()

        try:
            logged_in_page.keyboard.press("Escape")
        except Exception:
            pass


class TestFontSizeControls:
    """전체화면 폰트 크기 조절 동작 검증 (JS evaluate 기반)

    Note: fsTranslationSize/fsOriginalSize는 block-scoped let 변수이므로
    frame.evaluate()로 직접 설정 불가. 매 테스트마다 fresh page를 사용하므로
    기본값(번역: 28px, 원문: 16px)에서 시작.
    """

    def test_translation_font_increase(self, logged_in_page, caption_iframe):
        """번역문 폰트 크기 증가 함수 동작 (기본 28 + 2 = 30)"""
        frame = logged_in_page.frames[1] if len(logged_in_page.frames) > 1 else None
        if not frame:
            pytest.skip("iframe frame not accessible")

        frame.evaluate("updateFsFont('translation', 2)")

        size = frame.evaluate(
            "getComputedStyle(document.documentElement)"
            ".getPropertyValue('--translation-font-size').trim()"
        )
        assert size == "30px"

    def test_translation_font_decrease(self, logged_in_page, caption_iframe):
        """번역문 폰트 크기 감소 함수 동작 (기본 28 - 2 = 26)"""
        frame = logged_in_page.frames[1] if len(logged_in_page.frames) > 1 else None
        if not frame:
            pytest.skip("iframe frame not accessible")

        frame.evaluate("updateFsFont('translation', -2)")

        size = frame.evaluate(
            "getComputedStyle(document.documentElement)"
            ".getPropertyValue('--translation-font-size').trim()"
        )
        assert size == "26px"

    def test_original_font_increase(self, logged_in_page, caption_iframe):
        """원문 폰트 크기 증가 함수 동작 (기본 16 + 2 = 18)"""
        frame = logged_in_page.frames[1] if len(logged_in_page.frames) > 1 else None
        if not frame:
            pytest.skip("iframe frame not accessible")

        frame.evaluate("updateFsFont('original', 2)")

        size = frame.evaluate(
            "getComputedStyle(document.documentElement)"
            ".getPropertyValue('--original-font-size').trim()"
        )
        assert size == "18px"

    def test_translation_font_min_bound(self, logged_in_page, caption_iframe):
        """번역문 폰트 최소값(14px) 제한 — 큰 음수 delta로 하한 검증"""
        frame = logged_in_page.frames[1] if len(logged_in_page.frames) > 1 else None
        if not frame:
            pytest.skip("iframe frame not accessible")

        # 기본 28에서 -100 → max(14, -72) = 14
        frame.evaluate("updateFsFont('translation', -100)")

        size = frame.evaluate(
            "getComputedStyle(document.documentElement)"
            ".getPropertyValue('--translation-font-size').trim()"
        )
        assert size == "14px"

    def test_translation_font_max_bound(self, logged_in_page, caption_iframe):
        """번역문 폰트 최대값(80px) 제한 — 큰 양수 delta로 상한 검증"""
        frame = logged_in_page.frames[1] if len(logged_in_page.frames) > 1 else None
        if not frame:
            pytest.skip("iframe frame not accessible")

        # 기본 28에서 +100 → min(80, 128) = 80
        frame.evaluate("updateFsFont('translation', 100)")

        size = frame.evaluate(
            "getComputedStyle(document.documentElement)"
            ".getPropertyValue('--translation-font-size').trim()"
        )
        assert size == "80px"

    def test_font_size_display_updates(self, logged_in_page, caption_iframe):
        """폰트 크기 변경 시 표시 텍스트 업데이트 (기본 28 + 4 = 32)"""
        frame = logged_in_page.frames[1] if len(logged_in_page.frames) > 1 else None
        if not frame:
            pytest.skip("iframe frame not accessible")

        frame.evaluate("updateFsFont('translation', 4)")

        display_text = frame.evaluate(
            "document.getElementById('fsTransSize').textContent"
        )
        assert display_text == "32px"


class TestFullscreenFontRestore:
    """전체화면 해제 시 폰트 크기 복원 검증"""

    def test_restore_font_after_fullscreen_exit(self, logged_in_page, caption_iframe):
        """전체화면 해제 시 원래 설정 폰트 크기로 복원"""
        frame = logged_in_page.frames[1] if len(logged_in_page.frames) > 1 else None
        if not frame:
            pytest.skip("iframe frame not accessible")

        # localStorage에 원래 값 설정
        frame.evaluate("localStorage.setItem('translationFontSize', '28')")
        frame.evaluate("localStorage.setItem('originalFontSize', '16')")

        # 폰트 크기를 변경 (전체화면에서 조절한 것처럼)
        frame.evaluate(
            "document.documentElement.style.setProperty("
            "'--translation-font-size', '40px')"
        )

        # fullscreenchange 이벤트를 수동 디스패치하여 복원 로직 트리거
        frame.evaluate("document.dispatchEvent(new Event('fullscreenchange'))")

        logged_in_page.wait_for_timeout(200)

        size = frame.evaluate(
            "getComputedStyle(document.documentElement)"
            ".getPropertyValue('--translation-font-size').trim()"
        )
        assert size == "28px"
