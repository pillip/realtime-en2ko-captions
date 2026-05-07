"""
ISSUE-36: webrtc.html 뷰포트 반응형 높이 적용 테스트
- webrtc.html body에 90vh 하드코딩이 없는지 확인
- scroll_lock.html에 iframe 100vh 규칙이 존재하는지 확인
- 미디어쿼리에서도 90vh가 제거되었는지 확인
"""

import re
from pathlib import Path

WEBRTC_HTML = Path(__file__).parent.parent / "components" / "webrtc.html"
SCROLL_LOCK_HTML = Path(__file__).parent.parent / "components" / "scroll_lock.html"


def _read_webrtc():
    return WEBRTC_HTML.read_text(encoding="utf-8")


def _read_scroll_lock():
    return SCROLL_LOCK_HTML.read_text(encoding="utf-8")


def _get_body_css():
    html = _read_webrtc()
    m = re.search(r"body\s*\{[^}]*\}", html)
    assert m is not None, "body CSS block not found"
    return m.group(0)


class TestWebrtcBodyHeight:
    """webrtc.html body에서 90vh가 제거되고 100%로 변경되었는지 확인"""

    def test_body_no_90vh_height(self):
        """body 스타일에 height: 90vh가 없어야 한다"""
        body_css = _get_body_css()
        assert "90vh" not in body_css

    def test_body_uses_100_percent_height(self):
        """body 스타일이 height: 100%를 사용해야 한다"""
        body_css = _get_body_css()
        assert "height: 100%" in body_css

    def test_body_uses_100_percent_max_height(self):
        """body 스타일이 max-height: 100%를 사용해야 한다"""
        body_css = _get_body_css()
        assert "max-height: 100%" in body_css


class TestMediaQueryHeight:
    """미디어쿼리에서 body의 90vh 참조가 제거되었는지 확인"""

    def test_max_width_768_no_90vh(self):
        """@media (max-width: 768px) body에 90vh가 없어야 한다"""
        html = _read_webrtc()
        pattern = r"@media\s*\(max-width:\s*768px\)\s*\{(.*?)\n\s*\n"
        m = re.search(pattern, html, re.DOTALL)
        assert m is not None, "max-width: 768px media query not found"
        assert "90vh" not in m.group(1)

    def test_max_height_600_no_90vh(self):
        """@media (max-height: 600px) body에 90vh가 없어야 한다"""
        html = _read_webrtc()
        pattern = r"@media\s*\(max-height:\s*600px\)\s*\{(.*?)\n\s*\n"
        m = re.search(pattern, html, re.DOTALL)
        assert m is not None, "max-height: 600px media query not found"
        assert "90vh" not in m.group(1)


class TestScrollLockIframeHeight:
    """scroll_lock.html에서 iframe을 뷰포트 높이에 맞추는 규칙 확인"""

    def test_iframe_has_100vh_height(self):
        """scroll_lock.html의 .main iframe에 height: 100vh가 있어야 한다"""
        css = _read_scroll_lock()
        assert "height: 100vh" in css

    def test_iframe_height_uses_important(self):
        """iframe height에 !important가 있어야 한다"""
        css = _read_scroll_lock()
        assert "height: 100vh !important" in css


class TestFullscreenUnaffected:
    """전체화면 CSS가 변경되지 않았는지 확인 (regression)"""

    def test_fullscreen_still_uses_100vh(self):
        """#viewer:fullscreen은 여전히 height: 100vh를 사용해야 한다"""
        html = _read_webrtc()
        m = re.search(r"#viewer:fullscreen\s*\{[^}]*\}", html)
        assert m is not None, "#viewer:fullscreen CSS not found"
        assert "height: 100vh" in m.group(0)

    def test_webkit_fullscreen_still_uses_100vh(self):
        """#viewer:-webkit-full-screen은 여전히 100vh를 사용해야 한다"""
        html = _read_webrtc()
        m = re.search(r"#viewer:-webkit-full-screen\s*\{[^}]*\}", html)
        assert m is not None, "-webkit-full-screen CSS not found"
        assert "height: 100vh" in m.group(0)
