"""
순수 함수 단위 테스트
translation.py, websocket_handler.py에서 추출된 함수 직접 임포트하여 검증
"""

import json
import socket
from io import BytesIO
from unittest.mock import MagicMock

from translation import (
    _build_prompt_to_english,
    _build_prompt_to_korean,
    _clean_llm_response,
    detect_language,
    split_into_sentences,
    translate_with_llm,
)
from websocket_handler import find_free_port

# === _build_prompt_to_korean / _build_prompt_to_english Tests ===


class TestBuildPromptToKorean:
    def test_contains_source_text(self):
        """프롬프트에 원본 텍스트 포함"""
        prompt = _build_prompt_to_korean("Hello world", "en")
        assert "Hello world" in prompt

    def test_english_source_uses_correct_label(self):
        """영어 소스일 때 '영어' 라벨 사용"""
        prompt = _build_prompt_to_korean("Hello", "en")
        assert "영어" in prompt

    def test_japanese_source_uses_specific_label(self):
        """일본어 소스일 때 '일본어' 라벨 사용 (generic이 아닌 구체적 언어명)"""
        prompt = _build_prompt_to_korean("Hello", "ja")
        assert "일본어" in prompt

    def test_chinese_source_uses_specific_label(self):
        """중국어 소스일 때 '중국어' 라벨 사용"""
        prompt = _build_prompt_to_korean("Hello", "zh")
        assert "중국어" in prompt

    def test_unknown_source_uses_fallback_label(self):
        """매핑되지 않은 언어 코드는 '원본 언어' 폴백 사용"""
        prompt = _build_prompt_to_korean("Hello", "xx")
        assert "원본 언어" in prompt

    def test_contains_korean_translation_instruction(self):
        """한국어 번역 지시사항 포함"""
        prompt = _build_prompt_to_korean("Hello", "en")
        assert "한국어" in prompt

    def test_contains_guidelines(self):
        """번역 가이드라인 포함"""
        prompt = _build_prompt_to_korean("Hello", "en")
        assert "가이드라인" in prompt


class TestBuildPromptToEnglish:
    def test_contains_source_text(self):
        """프롬프트에 원본 한국어 텍스트 포함"""
        prompt = _build_prompt_to_english("안녕하세요")
        assert "안녕하세요" in prompt

    def test_contains_english_translation_instruction(self):
        """영어 번역 지시사항 포함"""
        prompt = _build_prompt_to_english("안녕하세요")
        assert "English" in prompt or "영어" in prompt

    def test_contains_conference_context(self):
        """컨퍼런스 컨텍스트 포함"""
        prompt = _build_prompt_to_english("안녕하세요")
        assert "컨퍼런스" in prompt

    def test_contains_guidelines(self):
        """번역 가이드라인 포함"""
        prompt = _build_prompt_to_english("안녕하세요")
        assert "가이드라인" in prompt

    def test_different_text_reflected_in_prompt(self):
        """다른 입력 텍스트도 프롬프트에 반영"""
        prompt = _build_prompt_to_english("대한민국 만세")
        assert "대한민국 만세" in prompt


# === find_free_port Tests ===


class TestFindFreePort:
    def test_returns_valid_port(self):
        """유효한 포트 번호 반환"""
        port = find_free_port()
        assert isinstance(port, int)
        assert 1 <= port <= 65535

    def test_port_is_bindable(self):
        """반환된 포트에 실제로 바인딩 가능"""
        port = find_free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", port))

    def test_returns_port_in_range(self):
        """지정된 범위 내의 포트 반환"""
        port = find_free_port(start_port=9000, max_port=9010)
        assert 9000 <= port <= 9010

    def test_falls_back_to_os_assignment(self):
        """지정 범위 포트가 모두 사용 중이면 OS 자동 할당"""
        occupied_sockets = []
        try:
            for p in range(19000, 19003):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    s.bind(("localhost", p))
                    occupied_sockets.append(s)
                except OSError:
                    s.close()

            port = find_free_port(start_port=19000, max_port=19002)
            assert isinstance(port, int)
            assert port > 0
        finally:
            for s in occupied_sockets:
                s.close()


# === detect_language Tests ===


class TestDetectLanguage:
    def test_english_text(self):
        """영어 텍스트 감지"""
        source, target = detect_language("Hello world")
        assert source == "en"
        assert target == "ko"

    def test_korean_text(self):
        """한국어 텍스트 감지"""
        source, target = detect_language("안녕하세요")
        assert source == "ko"
        assert target == "en"

    def test_mixed_text_with_korean(self):
        """한국어 포함 혼합 텍스트"""
        source, target = detect_language("Hello 안녕")
        assert source == "ko"
        assert target == "en"


# === split_into_sentences Tests ===


class TestSplitIntoSentences:
    def test_english_sentences(self):
        """영어 문장 분리"""
        text = "Hello world. How are you? I am fine!"
        result = split_into_sentences(text, language="en")
        assert len(result) == 3
        assert result[0] == "Hello world."
        assert result[1] == "How are you?"
        assert result[2] == "I am fine!"

    def test_english_single_sentence(self):
        """영어 단일 문장"""
        text = "Hello world."
        result = split_into_sentences(text, language="en")
        assert len(result) == 1

    def test_korean_period_sentences(self):
        """한국어 마침표 기준 분리"""
        text = "안녕하세요. 반갑습니다."
        result = split_into_sentences(text, language="ko")
        assert len(result) >= 2
        joined = "".join(result)
        assert "안녕하세요" in joined
        assert "반갑습니다" in joined

    def test_korean_empty_string(self):
        """빈 문자열 처리"""
        result = split_into_sentences("", language="ko")
        assert result == []

    def test_english_empty_string(self):
        """빈 문자열 처리 (영어)"""
        result = split_into_sentences("", language="en")
        assert result == []

    def test_korean_exclamation(self):
        """한국어 느낌표 포함 문장"""
        text = "대단합니다! 정말 좋습니다."
        result = split_into_sentences(text, language="ko")
        assert len(result) >= 2

    def test_english_no_trailing_punctuation(self):
        """마침표 없는 영어 문장"""
        text = "No punctuation here"
        result = split_into_sentences(text, language="en")
        assert len(result) == 1
        assert result[0] == "No punctuation here"

    def test_language_prefix_ko(self):
        """'ko-KR' 같은 접두사 처리"""
        text = "테스트입니다."
        result = split_into_sentences(text, language="ko-KR")
        assert len(result) == 1


# === _clean_llm_response Tests ===


class TestCleanLlmResponse:
    def test_strips_surrounding_quotes(self):
        """따옴표 제거"""
        assert _clean_llm_response('"안녕하세요"') == "안녕하세요"

    def test_extracts_first_line(self):
        """멀티라인에서 첫 줄만 추출"""
        assert _clean_llm_response("첫 줄\n두 번째 줄") == "첫 줄"

    def test_removes_explanation_text(self):
        """설명 텍스트 제거"""
        result = _clean_llm_response("안녕하세요\nThis translation: is natural")
        assert result == "안녕하세요"

    def test_empty_string(self):
        """빈 문자열"""
        assert _clean_llm_response("") == ""


# === translate_with_llm Tests ===


class TestTranslateWithLlm:
    def _make_mock_bedrock(self, response_text):
        """Bedrock 클라이언트 mock 생성 헬퍼"""
        mock_client = MagicMock()
        response_body = json.dumps(
            {"content": [{"type": "text", "text": response_text}]}
        ).encode()
        mock_response = {"body": BytesIO(response_body)}
        mock_client.invoke_model.return_value = mock_response
        return mock_client

    def test_translate_en_to_ko(self):
        """영어 → 한국어 번역 (mocked Bedrock)"""
        mock_client = self._make_mock_bedrock("안녕하세요")
        result = translate_with_llm(mock_client, "Hello", "en", "ko")
        assert result is not None
        assert "안녕하세요" in result
        mock_client.invoke_model.assert_called_once()

    def test_translate_ko_to_en(self):
        """한국어 → 영어 번역 (mocked Bedrock)"""
        mock_client = self._make_mock_bedrock("Hello everyone")
        result = translate_with_llm(mock_client, "안녕하세요", "ko", "en")
        assert result is not None
        assert "Hello everyone" in result

    def test_translate_returns_none_on_failure(self):
        """모든 모델 실패 시 None 반환"""
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = Exception("API Error")
        result = translate_with_llm(mock_client, "Hello", "en", "ko")
        assert result is None

    def test_translate_strips_quotes(self):
        """번역 결과에서 따옴표 제거"""
        mock_client = self._make_mock_bedrock('"안녕하세요"')
        result = translate_with_llm(mock_client, "Hello", "en", "ko")
        assert result == "안녕하세요"

    def test_translate_first_line_only(self):
        """멀티라인 응답에서 첫 번째 줄만 추출"""
        mock_client = self._make_mock_bedrock("안녕하세요\n이것은 추가 설명입니다")
        result = translate_with_llm(mock_client, "Hello", "en", "ko")
        assert result == "안녕하세요"

    def test_translate_model_fallback(self):
        """첫 모델 실패 시 다음 모델로 폴백"""
        mock_client = MagicMock()
        response_body = json.dumps(
            {"content": [{"type": "text", "text": "번역됨"}]}
        ).encode()

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("First model failed")
            return {"body": BytesIO(response_body)}

        mock_client.invoke_model.side_effect = side_effect
        result = translate_with_llm(mock_client, "Hello", "en", "ko")
        assert result == "번역됨"
        assert call_count == 2
