"""
app.py 단위 테스트
find_free_port, split_into_sentences, translate_with_llm 함수 테스트

app.py는 streamlit 사이드이펙트가 많아 직접 import 불가.
순수 함수만 복제하여 동일 로직을 검증한다.
"""

import json
import re
import socket
from io import BytesIO
from unittest.mock import MagicMock

# === 테스트 대상 함수 복제 (app.py에서 streamlit import 없이) ===


def find_free_port(start_port=8765, max_port=8800):
    """app.py의 find_free_port 함수 복제"""
    for port in range(start_port, max_port + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("localhost", port))
                return port
        except OSError:
            continue

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            port = s.getsockname()[1]
            return port
    except OSError:
        raise Exception("사용 가능한 포트를 찾을 수 없습니다")


def split_into_sentences(text, language="ko"):
    """app.py의 split_into_sentences 함수 복제"""
    if language.startswith("ko"):
        text = re.sub(r"([.!?])([가-힣])", r"\1 \2", text)
        pattern = (
            r"(?<=[.!?])"
            r"|(?<=다)(?=[\s])"
            r"|(?<=요)(?=[\s.!?])"
            r"|(?<=까)(?=[\s.!?])"
            r"|(?<=네)(?=[\s.!?])"
            r"|(?<=군)(?=[\s.!?])"
            r"|(?<=나)(?=[\s.!?])"
        )
        sentences = re.split(pattern, text)

        result = []
        current = ""
        for sent in sentences:
            current += sent
            if re.search(r"[.!?]$|[다요까네군나]$", current.strip()):
                if current.strip():
                    result.append(current.strip())
                current = ""
        if current.strip():
            result.append(current.strip())
        return result
    else:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in sentences if s.strip()]


def translate_with_llm(bedrock_client, text, source_lang, target_lang):
    """app.py의 translate_with_llm 함수 복제 (핵심 로직)"""
    try:
        if target_lang == "ko":
            prompt = f'번역: "{text}"'
        else:
            prompt = f'Translate: "{text}"'

        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 200,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    }
                ],
                "temperature": 0.5,
                "top_p": 0.9,
            }
        )

        model_ids = [
            "anthropic.claude-3-5-sonnet-20240620-v1:0",
            "anthropic.claude-3-haiku-20240307-v1:0",
            "anthropic.claude-3-sonnet-20240229-v1:0",
        ]

        for model_id in model_ids:
            try:
                response = bedrock_client.invoke_model(
                    modelId=model_id,
                    body=body,
                    contentType="application/json",
                    accept="application/json",
                )
                break
            except Exception as model_error:
                if model_id == model_ids[-1]:
                    raise model_error

        response_body = json.loads(response["body"].read())
        translated_text = response_body["content"][0]["text"].strip()
        translated_text = translated_text.strip("\"'")

        # 첫 번째 줄만 추출
        lines = translated_text.split("\n")
        if lines:
            translated_text = lines[0].strip()

        translated_text = re.sub(r'^["\'](.+)["\']$', r"\1", translated_text)
        return translated_text.strip()

    except Exception:
        return None


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
        assert len(result) >= 1
        joined = "".join(result)
        assert "안녕하세요" in joined

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
        assert len(result) >= 1

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
        assert len(result) >= 1


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
