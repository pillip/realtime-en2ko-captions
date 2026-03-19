"""
전체화면 모드 관련 테스트
components/webrtc.html에 전체화면 기능이 올바르게 구현되어 있는지 검증
"""

import re
from pathlib import Path

WEBRTC_HTML = Path(__file__).parent.parent / "components" / "webrtc.html"


def _read_html():
    return WEBRTC_HTML.read_text(encoding="utf-8")


# === 전체화면 버튼 존재 확인 ===


class TestFullscreenButton:
    def test_fullscreen_fab_button_exists(self):
        """전체화면 FAB 버튼이 HTML에 존재"""
        html = _read_html()
        assert 'id="fullscreenFab"' in html

    def test_fullscreen_fab_has_class(self):
        """전체화면 버튼에 fullscreen-fab 클래스 존재"""
        html = _read_html()
        assert 'class="fullscreen-fab"' in html


# === 전체화면 폰트 조절 UI 존재 확인 ===


class TestFullscreenFontControls:
    def test_font_controls_container_exists(self):
        """전체화면 폰트 조절 컨테이너 존재"""
        html = _read_html()
        assert 'id="fsFontControls"' in html
        assert 'class="fs-font-controls"' in html

    def test_translation_font_buttons_exist(self):
        """번역문 폰트 크기 +/- 버튼 존재"""
        html = _read_html()
        assert 'id="fsTransPlus"' in html
        assert 'id="fsTransMinus"' in html

    def test_original_font_buttons_exist(self):
        """원문 폰트 크기 +/- 버튼 존재"""
        html = _read_html()
        assert 'id="fsOrigPlus"' in html
        assert 'id="fsOrigMinus"' in html

    def test_font_size_displays_exist(self):
        """폰트 크기 표시 요소 존재"""
        html = _read_html()
        assert 'id="fsTransSize"' in html
        assert 'id="fsOrigSize"' in html

    def test_font_controls_inside_viewer(self):
        """폰트 조절 컨트롤이 #viewer 내부에 위치"""
        html = _read_html()
        viewer_start = html.find('id="viewer"')
        viewer_end = html.find("</div>", html.find("</div>", viewer_start) + 1)
        controls_pos = html.find('id="fsFontControls"')
        assert viewer_start < controls_pos < viewer_end + 500


# === 전체화면 CSS 확인 ===


class TestFullscreenCSS:
    def test_fullscreen_pseudo_class_exists(self):
        """#viewer:fullscreen CSS 규칙 존재"""
        html = _read_html()
        assert "#viewer:fullscreen" in html

    def test_fullscreen_background_black(self):
        """전체화면 시 검은 배경"""
        html = _read_html()
        fullscreen_section = html[html.find("#viewer:fullscreen") :]
        closing_brace = fullscreen_section.find("}")
        rule = fullscreen_section[:closing_brace]
        assert "background: #000" in rule

    def test_fullscreen_height_100vh(self):
        """전체화면 시 100vh 높이"""
        html = _read_html()
        fullscreen_section = html[html.find("#viewer:fullscreen") :]
        closing_brace = fullscreen_section.find("}")
        rule = fullscreen_section[:closing_brace]
        assert "100vh" in rule

    def test_font_controls_hidden_by_default(self):
        """폰트 조절 컨트롤이 기본적으로 숨김 상태"""
        html = _read_html()
        controls_css = html[html.find(".fs-font-controls {") :]
        closing_brace = controls_css.find("}")
        rule = controls_css[:closing_brace]
        assert "display: none" in rule

    def test_font_controls_visible_in_fullscreen(self):
        """전체화면에서 폰트 조절 컨트롤 표시"""
        html = _read_html()
        assert "#viewer:fullscreen .fs-font-controls" in html

    def test_font_controls_low_opacity(self):
        """폰트 조절 컨트롤이 낮은 불투명도"""
        html = _read_html()
        controls_css = html[html.find(".fs-font-controls {") :]
        closing_brace = controls_css.find("}")
        rule = controls_css[:closing_brace]
        assert "opacity: 0.15" in rule

    def test_font_controls_hover_opacity(self):
        """호버 시 불투명도 증가"""
        html = _read_html()
        assert ".fs-font-controls:hover" in html
        hover_section = html[html.find(".fs-font-controls:hover") :]
        closing_brace = hover_section.find("}")
        rule = hover_section[:closing_brace]
        # Check for higher opacity on hover
        opacity_match = re.search(r"opacity:\s*([\d.]+)", rule)
        assert opacity_match is not None
        assert float(opacity_match.group(1)) > 0.5

    def test_webkit_fullscreen_prefix(self):
        """Safari용 -webkit-full-screen 접두사 존재"""
        html = _read_html()
        assert "#viewer:-webkit-full-screen" in html


# === 전체화면 JS 로직 확인 ===


class TestFullscreenJS:
    def test_toggle_fullscreen_function(self):
        """toggleFullscreen 함수 존재"""
        html = _read_html()
        assert "function toggleFullscreen()" in html

    def test_is_fullscreen_function(self):
        """isFullscreen 함수 존재"""
        html = _read_html()
        assert "function isFullscreen()" in html

    def test_update_fs_font_function(self):
        """updateFsFont 함수 존재"""
        html = _read_html()
        assert "function updateFsFont(" in html

    def test_fullscreen_inherits_current_font_size(self):
        """전체화면 진입 시 현재 폰트 크기를 CSS 변수에서 읽어옴"""
        html = _read_html()
        assert "--translation-font-size" in html
        assert "--original-font-size" in html
        assert "getComputedStyle" in html

    def test_fullscreen_exit_restores_settings(self):
        """전체화면 해제 시 localStorage에서 원래 크기 복원"""
        html = _read_html()
        assert "fullscreenchange" in html
        assert "localStorage.getItem('translationFontSize')" in html
        assert "localStorage.getItem('originalFontSize')" in html

    def test_font_size_has_min_max_bounds(self):
        """폰트 크기에 최소/최대 제한 존재"""
        html = _read_html()
        assert "Math.max" in html
        assert "Math.min" in html

    def test_requestfullscreen_called_on_viewer(self):
        """viewer 요소에서 requestFullscreen 호출"""
        html = _read_html()
        assert "viewer.requestFullscreen" in html
