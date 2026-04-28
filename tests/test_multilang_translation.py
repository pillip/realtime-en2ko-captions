"""
다국어 번역 기능 단위 테스트 (ISSUE-3)
_build_prompt_to_chinese, _build_prompt_to_vietnamese,
_build_prompt_to_english(source_lang), translate_with_llm 다국어 분기
"""

import json
from io import BytesIO
from unittest.mock import MagicMock

from translation import (
    SOURCE_LANG_NAMES,
    _build_prompt_to_chinese,
    _build_prompt_to_english,
    _build_prompt_to_vietnamese,
    translate_with_llm,
)

# === SOURCE_LANG_NAMES 확장 테스트 ===


class TestSourceLangNames:
    def test_korean_entry_exists(self):
        """한국어 항목이 SOURCE_LANG_NAMES에 존재"""
        assert "ko" in SOURCE_LANG_NAMES
        assert SOURCE_LANG_NAMES["ko"] == "한국어"

    def test_vietnamese_entry_exists(self):
        """베트남어 항목이 SOURCE_LANG_NAMES에 존재"""
        assert "vi" in SOURCE_LANG_NAMES
        assert SOURCE_LANG_NAMES["vi"] == "베트남어"

    def test_all_expected_languages(self):
        """모든 지원 언어가 존재하는지 확인"""
        expected = {"en", "ko", "ja", "zh", "vi", "es", "fr", "de"}
        assert set(SOURCE_LANG_NAMES.keys()) == expected


# === _build_prompt_to_chinese 테스트 ===


class TestBuildPromptToChinese:
    def test_contains_source_text(self):
        """프롬프트에 원본 텍스트 포함"""
        prompt = _build_prompt_to_chinese("Hello world", "en")
        assert "Hello world" in prompt

    def test_english_source_shows_label(self):
        """영어 소스 라벨이 프롬프트에 포함"""
        prompt = _build_prompt_to_chinese("Hello", "en")
        assert SOURCE_LANG_NAMES["en"] in prompt

    def test_korean_source_shows_label(self):
        """한국어 소스 라벨이 프롬프트에 포함"""
        prompt = _build_prompt_to_chinese("안녕하세요", "ko")
        assert SOURCE_LANG_NAMES["ko"] in prompt

    def test_unknown_source_uses_fallback(self):
        """매핑되지 않은 언어는 중국어 폴백 라벨 사용"""
        prompt = _build_prompt_to_chinese("Hello", "xx")
        assert "原始语言" in prompt

    def test_contains_chinese_instructions(self):
        """중국어 번역 지시사항 포함"""
        prompt = _build_prompt_to_chinese("Hello", "en")
        assert "中文" in prompt

    def test_contains_translation_guidelines(self):
        """번역 가이드라인 포함"""
        prompt = _build_prompt_to_chinese("Hello", "en")
        assert "翻译指南" in prompt


# === _build_prompt_to_vietnamese 테스트 ===


class TestBuildPromptToVietnamese:
    def test_contains_source_text(self):
        """프롬프트에 원본 텍스트 포함"""
        prompt = _build_prompt_to_vietnamese("Hello world", "en")
        assert "Hello world" in prompt

    def test_english_source_shows_label(self):
        """영어 소스 라벨이 프롬프트에 포함"""
        prompt = _build_prompt_to_vietnamese("Hello", "en")
        assert SOURCE_LANG_NAMES["en"] in prompt

    def test_korean_source_shows_label(self):
        """한국어 소스 라벨이 프롬프트에 포함"""
        prompt = _build_prompt_to_vietnamese("안녕하세요", "ko")
        assert SOURCE_LANG_NAMES["ko"] in prompt

    def test_unknown_source_uses_fallback(self):
        """매핑되지 않은 언어는 베트남어 폴백 라벨 사용"""
        prompt = _build_prompt_to_vietnamese("Hello", "xx")
        assert "ngon ngu goc" in prompt

    def test_contains_vietnamese_instructions(self):
        """베트남어 번역 지시사항 포함"""
        prompt = _build_prompt_to_vietnamese("Hello", "en")
        assert "tieng Viet" in prompt

    def test_contains_translation_guidelines(self):
        """번역 가이드라인 포함"""
        prompt = _build_prompt_to_vietnamese("Hello", "en")
        assert "Huong dan dich" in prompt


# === _build_prompt_to_english 확장 테스트 ===


class TestBuildPromptToEnglishExtended:
    def test_no_source_lang_defaults_to_korean(self):
        """source_lang=None 시 한국어 기본값 사용"""
        prompt = _build_prompt_to_english("안녕하세요")
        assert "한국어" in prompt

    def test_source_lang_zh_shows_chinese_label(self):
        """source_lang=zh 시 중국어 라벨 사용"""
        prompt = _build_prompt_to_english("你好", source_lang="zh")
        assert "중국어" in prompt

    def test_source_lang_vi_shows_vietnamese_label(self):
        """source_lang=vi 시 베트남어 라벨 사용"""
        prompt = _build_prompt_to_english("Xin chao", source_lang="vi")
        assert "베트남어" in prompt

    def test_source_lang_ko_shows_korean_label(self):
        """source_lang=ko 시 한국어 라벨 사용"""
        prompt = _build_prompt_to_english("안녕하세요", source_lang="ko")
        assert "한국어" in prompt

    def test_unknown_source_lang_uses_fallback(self):
        """알 수 없는 source_lang은 폴백 라벨 사용"""
        prompt = _build_prompt_to_english("text", source_lang="xx")
        assert "원본 언어" in prompt

    def test_contains_source_text(self):
        """프롬프트에 원본 텍스트 포함"""
        prompt = _build_prompt_to_english("你好世界", source_lang="zh")
        assert "你好世界" in prompt


# === translate_with_llm 다국어 분기 테스트 ===


class TestTranslateWithLlmMultilang:
    def _make_mock_bedrock(self, response_text):
        """Bedrock 클라이언트 mock 생성"""
        mock_client = MagicMock()
        response_body = json.dumps(
            {"content": [{"type": "text", "text": response_text}]}
        ).encode()
        mock_response = {"body": BytesIO(response_body)}
        mock_client.invoke_model.return_value = mock_response
        return mock_client

    def test_translate_en_to_zh(self):
        """영어 -> 중국어 번역 (mocked Bedrock)"""
        mock_client = self._make_mock_bedrock("你好世界")
        result = translate_with_llm(mock_client, "Hello world", "en", "zh")
        assert result is not None
        assert "你好世界" in result

    def test_translate_en_to_vi(self):
        """영어 -> 베트남어 번역 (mocked Bedrock)"""
        mock_client = self._make_mock_bedrock("Xin chao the gioi")
        result = translate_with_llm(mock_client, "Hello world", "en", "vi")
        assert result is not None
        assert "Xin chao" in result

    def test_translate_ko_to_zh(self):
        """한국어 -> 중국어 번역"""
        mock_client = self._make_mock_bedrock("你好")
        result = translate_with_llm(mock_client, "안녕하세요", "ko", "zh")
        assert result is not None
        assert "你好" in result

    def test_translate_ko_to_vi(self):
        """한국어 -> 베트남어 번역"""
        mock_client = self._make_mock_bedrock("Xin chao")
        result = translate_with_llm(mock_client, "안녕하세요", "ko", "vi")
        assert result is not None
        assert "Xin chao" in result

    def test_translate_zh_to_en(self):
        """중국어 -> 영어 번역"""
        mock_client = self._make_mock_bedrock("Hello everyone")
        result = translate_with_llm(mock_client, "大家好", "zh", "en")
        assert result is not None
        assert "Hello everyone" in result

    def test_translate_vi_to_ko(self):
        """베트남어 -> 한국어 번역"""
        mock_client = self._make_mock_bedrock("안녕하세요")
        result = translate_with_llm(mock_client, "Xin chao", "vi", "ko")
        assert result is not None
        assert "안녕하세요" in result

    def test_translate_zh_to_ko(self):
        """중국어 -> 한국어 번역"""
        mock_client = self._make_mock_bedrock("안녕하세요")
        result = translate_with_llm(mock_client, "你好", "zh", "ko")
        assert result is not None
        assert "안녕하세요" in result

    def test_translate_vi_to_en(self):
        """베트남어 -> 영어 번역"""
        mock_client = self._make_mock_bedrock("Hello")
        result = translate_with_llm(mock_client, "Xin chao", "vi", "en")
        assert result is not None
        assert "Hello" in result
