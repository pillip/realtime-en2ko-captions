"""
다국어 감지 기능 단위 테스트 (ISSUE-4)
detect_language()의 한국어/중국어/베트남어/영어 감지 및
output_lang 폴백 로직 검증
"""

from translation import detect_language


class TestDetectLanguageKorean:
    """한국어 감지 테스트"""

    def test_korean_text_detected(self):
        """한국어 텍스트는 'ko'로 감지"""
        source, target = detect_language("안녕하세요")
        assert source == "ko"

    def test_korean_default_output_ko_fallback(self):
        """output_lang='ko'이고 detected='ko'이면 target='en' 폴백"""
        source, target = detect_language("안녕하세요", output_lang="ko")
        assert source == "ko"
        assert target == "en"

    def test_korean_output_en(self):
        """output_lang='en'이면 target='en'"""
        source, target = detect_language("안녕하세요", output_lang="en")
        assert source == "ko"
        assert target == "en"

    def test_korean_output_zh(self):
        """output_lang='zh'이면 target='zh'"""
        source, target = detect_language("안녕하세요", output_lang="zh")
        assert source == "ko"
        assert target == "zh"

    def test_korean_output_vi(self):
        """output_lang='vi'이면 target='vi'"""
        source, target = detect_language("안녕하세요", output_lang="vi")
        assert source == "ko"
        assert target == "vi"


class TestDetectLanguageChinese:
    """중국어 감지 테스트"""

    def test_chinese_text_detected(self):
        """중국어 텍스트는 'zh'로 감지"""
        source, target = detect_language("你好世界")
        assert source == "zh"

    def test_chinese_default_output_ko(self):
        """output_lang='ko'이면 target='ko'"""
        source, target = detect_language("你好世界", output_lang="ko")
        assert source == "zh"
        assert target == "ko"

    def test_chinese_output_zh_fallback(self):
        """output_lang='zh'이고 detected='zh'이면 target='en' 폴백"""
        source, target = detect_language("你好世界", output_lang="zh")
        assert source == "zh"
        assert target == "en"

    def test_chinese_output_en(self):
        """output_lang='en'이면 target='en'"""
        source, target = detect_language("你好世界", output_lang="en")
        assert source == "zh"
        assert target == "en"

    def test_chinese_output_vi(self):
        """output_lang='vi'이면 target='vi'"""
        source, target = detect_language("你好世界", output_lang="vi")
        assert source == "zh"
        assert target == "vi"


class TestDetectLanguageVietnamese:
    """베트남어 감지 테스트"""

    def test_vietnamese_text_detected(self):
        """베트남어 특수 문자가 있으면 'vi'로 감지"""
        source, target = detect_language("Xin chào các bạn ơ")
        assert source == "vi"

    def test_vietnamese_special_chars(self):
        """다양한 베트남어 특수 문자 감지"""
        # Test with ư (common Vietnamese char)
        source, _ = detect_language("Tôi là người Việt Nam ư")
        assert source == "vi"

    def test_vietnamese_default_output_ko(self):
        """output_lang='ko'이면 target='ko'"""
        source, target = detect_language("xin chào ơ", output_lang="ko")
        assert source == "vi"
        assert target == "ko"

    def test_vietnamese_output_vi_fallback(self):
        """output_lang='vi'이고 detected='vi'이면 target='en' 폴백"""
        source, target = detect_language("xin chào ơ", output_lang="vi")
        assert source == "vi"
        assert target == "en"

    def test_vietnamese_output_en(self):
        """output_lang='en'이면 target='en'"""
        source, target = detect_language("xin chào ơ", output_lang="en")
        assert source == "vi"
        assert target == "en"

    def test_vietnamese_output_zh(self):
        """output_lang='zh'이면 target='zh'"""
        source, target = detect_language("xin chào ơ", output_lang="zh")
        assert source == "vi"
        assert target == "zh"


class TestDetectLanguageEnglish:
    """영어(기본) 감지 테스트"""

    def test_english_text_detected(self):
        """영어 텍스트는 'en'으로 감지"""
        source, target = detect_language("Hello world")
        assert source == "en"

    def test_english_default_output_ko(self):
        """output_lang='ko'이면 target='ko'"""
        source, target = detect_language("Hello world", output_lang="ko")
        assert source == "en"
        assert target == "ko"

    def test_english_output_en_fallback(self):
        """output_lang='en'이고 detected='en'이면 target='ko' 폴백"""
        source, target = detect_language("Hello world", output_lang="en")
        assert source == "en"
        assert target == "ko"

    def test_english_output_zh(self):
        """output_lang='zh'이면 target='zh'"""
        source, target = detect_language("Hello world", output_lang="zh")
        assert source == "en"
        assert target == "zh"

    def test_english_output_vi(self):
        """output_lang='vi'이면 target='vi'"""
        source, target = detect_language("Hello world", output_lang="vi")
        assert source == "en"
        assert target == "vi"


class TestDetectLanguagePriority:
    """감지 우선순위 테스트"""

    def test_korean_priority_over_chinese(self):
        """한국어가 중국어보다 우선 감지"""
        # Text with both Korean and Chinese characters
        source, _ = detect_language("안녕你好")
        assert source == "ko"

    def test_chinese_priority_over_vietnamese(self):
        """중국어가 베트남어보다 우선 감지 (한국어 없을 때)"""
        # CJK chars take priority over Vietnamese diacritics
        source, _ = detect_language("你好 ơ")
        assert source == "zh"

    def test_backward_compatibility_no_output_lang(self):
        """output_lang 미지정 시 기본값 'ko' 유지"""
        source, target = detect_language("Hello world")
        assert source == "en"
        assert target == "ko"

        source, target = detect_language("안녕하세요")
        assert source == "ko"
        assert target == "en"
