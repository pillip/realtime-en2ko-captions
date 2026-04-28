"""
WebSocket handler 단위 테스트
_handle_transcript, _translate_text, _record_usage,
handle_openai_websocket 함수 테스트

Note: _authenticate_client 테스트는
tests/test_websocket_auth.py에 있음.
"""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# websocket_handler -> auth -> streamlit / extra_streamlit_components
# 시스템 Python에 해당 패키지가 없을 수 있으므로 미리 mock 등록
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


@pytest.fixture
def db_path(tmp_path):
    """임시 데이터베이스 경로"""
    return str(tmp_path / "test_ws_handler.db")


@pytest.fixture
def mock_db(db_path):
    """테스트용 데이터베이스 (활성 사용자 포함)"""
    from database import DatabaseManager, User

    db = DatabaseManager(db_path)
    user_model = User(db)

    user_model.create_user(
        username="testuser",
        password="testpass",
        role="user",
        usage_limit_seconds=3600,
    )
    user_model.create_user(
        username="admin",
        password="adminpass",
        role="admin",
        usage_limit_seconds=0,
    )
    return db


@pytest.fixture
def user_info():
    """테스트용 인증된 사용자 정보"""
    return {
        "id": 1,
        "username": "testuser",
        "role": "user",
        "full_name": None,
        "is_active": True,
    }


@pytest.fixture
def admin_info():
    """테스트용 관리자 정보"""
    return {
        "id": 2,
        "username": "admin",
        "role": "admin",
        "full_name": None,
        "is_active": True,
    }


def _make_websocket():
    """테스트용 mock WebSocket 생성"""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _get_sent_messages(ws):
    """WebSocket send 호출에서 JSON 메시지 추출"""
    return [json.loads(call.args[0]) for call in ws.send.call_args_list]


# ============================================================
# _translate_text 테스트
# ============================================================
class TestTranslateText:
    """_translate_text 함수 테스트"""

    def test_bedrock_success_returns_translation_with_used_llm(self):
        """Bedrock 번역 성공 시 translated_text와 used_llm=True 반환"""
        from websocket_handler import _translate_text

        mock_translate_client = MagicMock()
        mock_bedrock_client = MagicMock()

        with patch(
            "websocket_handler.translate_with_llm",
            return_value="안녕하세요",
        ):
            result, used_llm = _translate_text(
                "Hello",
                "en",
                "ko",
                mock_translate_client,
                mock_bedrock_client,
                bedrock_available=True,
            )

        assert result == "안녕하세요"
        assert used_llm is True

    def test_bedrock_fails_falls_back_to_aws_translate(self):
        """Bedrock 실패 시 AWS Translate 폴백"""
        from websocket_handler import _translate_text

        mock_translate_client = MagicMock()
        mock_translate_client.translate_text.return_value = {
            "TranslatedText": "안녕하세요 (AWS)"
        }
        mock_bedrock_client = MagicMock()

        with patch(
            "websocket_handler.translate_with_llm",
            side_effect=Exception("Bedrock error"),
        ):
            result, used_llm = _translate_text(
                "Hello",
                "en",
                "ko",
                mock_translate_client,
                mock_bedrock_client,
                bedrock_available=True,
            )

        assert result == "안녕하세요 (AWS)"
        assert used_llm is False

    def test_both_fail_returns_original_text(self):
        """Bedrock과 AWS Translate 모두 실패 시 원문 반환"""
        from websocket_handler import _translate_text

        mock_translate_client = MagicMock()
        mock_translate_client.translate_text.side_effect = Exception("AWS error")
        mock_bedrock_client = MagicMock()

        with patch(
            "websocket_handler.translate_with_llm",
            return_value=None,
        ):
            result, used_llm = _translate_text(
                "Hello world",
                "en",
                "ko",
                mock_translate_client,
                mock_bedrock_client,
                bedrock_available=True,
            )

        assert result == "Hello world"
        assert used_llm is False

    def test_bedrock_not_available_uses_aws_translate(self):
        """bedrock_available=False 시 AWS Translate 바로 사용"""
        from websocket_handler import _translate_text

        mock_translate_client = MagicMock()
        mock_translate_client.translate_text.return_value = {"TranslatedText": "번역됨"}

        result, used_llm = _translate_text(
            "Hello",
            "en",
            "ko",
            mock_translate_client,
            None,
            bedrock_available=False,
        )

        assert result == "번역됨"
        assert used_llm is False


# ============================================================
# _record_usage 테스트
# ============================================================
class TestRecordUsage:
    """_record_usage 함수 테스트"""

    def test_positive_audio_duration_records_correctly(self, mock_db, user_info):
        """양수 audio_duration은 그대로 기록"""
        from database import UsageLog, User
        from websocket_handler import _record_usage

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        with (
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model", return_value=usage_log_model
            ),
            patch("websocket_handler.update_user_session"),
        ):
            result = _record_usage(
                current_user=user_info,
                audio_duration=10,
                data={"audio_duration_seconds": 10},
                transcript="Hello world",
                translated_text="안녕하세요",
                used_llm=True,
                source_lang="en",
                target_lang="ko",
            )

        assert result == 10

    def test_zero_audio_duration_estimates_from_transcript(self, mock_db, user_info):
        """audio_duration=0 시 max(1, len(transcript)/5.0)으로 추정"""
        from database import UsageLog, User
        from websocket_handler import _record_usage

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        transcript = "Hello world test"  # len=16, 16/5.0=3.2

        with (
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model", return_value=usage_log_model
            ),
            patch("websocket_handler.update_user_session"),
        ):
            result = _record_usage(
                current_user=user_info,
                audio_duration=0,
                data={"audio_duration_seconds": 0},
                transcript=transcript,
                translated_text="안녕하세요 세계 테스트",
                used_llm=False,
                source_lang="en",
                target_lang="ko",
            )

        expected = max(1, len(transcript) / 5.0)
        assert result == expected

    def test_zero_duration_short_text_uses_minimum_1(self, mock_db, user_info):
        """짧은 텍스트(len<5)에서 audio_duration=0이면 최소 1초 사용"""
        from database import UsageLog, User
        from websocket_handler import _record_usage

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        transcript = "Hi"  # len=2, 2/5.0=0.4, max(1, 0.4)=1

        with (
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model", return_value=usage_log_model
            ),
            patch("websocket_handler.update_user_session"),
        ):
            result = _record_usage(
                current_user=user_info,
                audio_duration=0,
                data={"audio_duration_seconds": 0},
                transcript=transcript,
                translated_text="안녕",
                used_llm=False,
                source_lang="en",
                target_lang="ko",
            )

        assert result == 1


# ============================================================
# _handle_transcript 테스트
# ============================================================
class TestHandleTranscript:
    """_handle_transcript 함수 테스트"""

    def test_empty_text_no_translation_or_send(self, user_info):
        """빈 텍스트 → 번역/전송 없이 바로 리턴"""
        from websocket_handler import _handle_transcript

        ws = _make_websocket()
        data = {"text": "", "audio_duration_seconds": 5}

        asyncio.run(
            _handle_transcript(ws, data, user_info, MagicMock(), MagicMock(), True)
        )

        ws.send.assert_not_called()

    def test_usage_limit_exceeded_sends_usage_exceeded(self, mock_db, user_info):
        """사용량 초과 시 usage_exceeded 메시지 전송"""
        from database import User
        from websocket_handler import _handle_transcript

        user_model = User(mock_db)
        # 사용량을 제한에 가깝게 설정
        user_model.add_usage(user_info["id"], 3590)

        ws = _make_websocket()
        data = {"text": "Hello world", "audio_duration_seconds": 20}

        with (
            patch("websocket_handler.check_usage_limit", return_value=False),
            patch("websocket_handler.get_user_model", return_value=user_model),
        ):
            asyncio.run(
                _handle_transcript(ws, data, user_info, MagicMock(), MagicMock(), True)
            )

        sent = _get_sent_messages(ws)
        assert len(sent) == 1
        assert sent[0]["type"] == "usage_exceeded"
        assert "remaining_seconds" in sent[0]

    def test_successful_transcript_sends_transcription_result(self, mock_db, user_info):
        """정상 트랜스크립트 → transcription_result 메시지 전송"""
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        ws = _make_websocket()
        data = {"text": "Hello world", "audio_duration_seconds": 5}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch("websocket_handler.detect_language", return_value=("en", "ko")),
            patch(
                "websocket_handler._translate_text",
                return_value=("안녕하세요", True),
            ),
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model", return_value=usage_log_model
            ),
            patch("websocket_handler.update_user_session"),
        ):
            asyncio.run(
                _handle_transcript(ws, data, user_info, MagicMock(), MagicMock(), True)
            )

        sent = _get_sent_messages(ws)
        assert len(sent) == 1
        assert sent[0]["type"] == "transcription_result"
        assert sent[0]["original_text"] == "Hello world"
        assert sent[0]["translated_text"] == "안녕하세요"
        assert sent[0]["used_llm"] is True
        assert sent[0]["source_language"] == "en"
        assert sent[0]["target_language"] == "ko"

    def test_no_user_info_sends_error(self):
        """user_info가 None이면 에러 메시지 전송"""
        from websocket_handler import _handle_transcript

        ws = _make_websocket()
        data = {"text": "Hello", "audio_duration_seconds": 5}

        asyncio.run(_handle_transcript(ws, data, None, MagicMock(), MagicMock(), True))

        sent = _get_sent_messages(ws)
        assert len(sent) == 1
        assert sent[0]["type"] == "error"
        assert "로그인" in sent[0]["message"]


# ============================================================
# handle_openai_websocket 테스트
# ============================================================
class TestHandleOpenaiWebsocket:
    """handle_openai_websocket 통합 테스트"""

    def test_invalid_json_sends_error(self):
        """유효하지 않은 JSON -> error 메시지 전송"""
        from websocket_handler import handle_openai_websocket

        ws = AsyncMock()
        ws.remote_address = ("127.0.0.1", 12345)
        ws.send = AsyncMock()

        # Make websocket async-iterable: one invalid JSON msg
        ws.__aiter__ = lambda self: self
        call_count = 0

        async def mock_anext(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "not valid json {{{}"
            raise StopAsyncIteration

        ws.__anext__ = mock_anext

        mock_user = {
            "id": 1,
            "username": "testuser",
            "role": "user",
            "full_name": None,
            "is_active": 1,
        }

        mock_auth = AsyncMock(return_value=mock_user)

        with (
            patch(
                "websocket_handler._authenticate_client",
                new=mock_auth,
            ),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(
                    MagicMock(),
                    MagicMock(),
                    True,
                ),
            ),
        ):
            asyncio.run(handle_openai_websocket(ws))

        sent = _get_sent_messages(ws)
        # connection message + error for invalid JSON
        error_msgs = [m for m in sent if m.get("type") == "error"]
        assert len(error_msgs) >= 1
        assert "Invalid JSON" in error_msgs[0]["message"]


# ============================================================
# ISSUE-2: Language settings in _handle_transcript
# ============================================================
class TestHandleTranscriptLanguageSettings:
    """_handle_transcript 언어 설정 전달 테스트 (ISSUE-2)"""

    def test_specific_language_skips_detect(self, mock_db, user_info):
        """input_lang이 특정 언어이면 detect_language를 호출하지 않음"""
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        ws = _make_websocket()
        data = {"text": "Hello world", "audio_duration_seconds": 5}
        lang_settings = {"input_lang": "en", "output_lang": "ja"}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch("websocket_handler.detect_language") as mock_detect,
            patch(
                "websocket_handler._translate_text",
                return_value=("こんにちは", True),
            ),
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model",
                return_value=usage_log_model,
            ),
            patch("websocket_handler.update_user_session"),
        ):
            asyncio.run(
                _handle_transcript(
                    ws,
                    data,
                    user_info,
                    MagicMock(),
                    MagicMock(),
                    True,
                    language_settings=lang_settings,
                )
            )

        # detect_language should NOT be called
        mock_detect.assert_not_called()

        sent = _get_sent_messages(ws)
        assert len(sent) == 1
        assert sent[0]["source_language"] == "en"
        assert sent[0]["target_language"] == "ja"

    def test_auto_input_lang_uses_detect(self, mock_db, user_info):
        """input_lang이 'auto'이면 detect_language()를 사용"""
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        ws = _make_websocket()
        data = {"text": "Hello world", "audio_duration_seconds": 5}
        lang_settings = {"input_lang": "auto", "output_lang": "ko"}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ) as mock_detect,
            patch(
                "websocket_handler._translate_text",
                return_value=("안녕하세요", True),
            ),
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model",
                return_value=usage_log_model,
            ),
            patch("websocket_handler.update_user_session"),
        ):
            asyncio.run(
                _handle_transcript(
                    ws,
                    data,
                    user_info,
                    MagicMock(),
                    MagicMock(),
                    True,
                    language_settings=lang_settings,
                )
            )

        # detect_language SHOULD be called with output_lang (ISSUE-4)
        mock_detect.assert_called_once_with("Hello world", output_lang="ko")

    def test_no_language_settings_uses_detect(self, mock_db, user_info):
        """language_settings가 None이면 detect_language() 사용 (하위 호환)"""
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        ws = _make_websocket()
        data = {"text": "Hello world", "audio_duration_seconds": 5}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ) as mock_detect,
            patch(
                "websocket_handler._translate_text",
                return_value=("안녕하세요", True),
            ),
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model",
                return_value=usage_log_model,
            ),
            patch("websocket_handler.update_user_session"),
        ):
            asyncio.run(
                _handle_transcript(
                    ws,
                    data,
                    user_info,
                    MagicMock(),
                    MagicMock(),
                    True,
                    language_settings=None,
                )
            )

        mock_detect.assert_called_once_with("Hello world", output_lang="ko")

    def test_empty_input_lang_uses_detect(self, mock_db, user_info):
        """input_lang이 빈 문자열이면 detect_language() 사용"""
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        ws = _make_websocket()
        data = {"text": "Hello", "audio_duration_seconds": 3}
        lang_settings = {"input_lang": "", "output_lang": "ko"}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ) as mock_detect,
            patch(
                "websocket_handler._translate_text",
                return_value=("안녕", True),
            ),
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model",
                return_value=usage_log_model,
            ),
            patch("websocket_handler.update_user_session"),
        ):
            asyncio.run(
                _handle_transcript(
                    ws,
                    data,
                    user_info,
                    MagicMock(),
                    MagicMock(),
                    True,
                    language_settings=lang_settings,
                )
            )

        mock_detect.assert_called_once()

    def test_specific_lang_defaults_output_to_ko(self, mock_db, user_info):
        """output_lang이 없으면 기본값 'ko' 사용"""
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        ws = _make_websocket()
        data = {"text": "Bonjour", "audio_duration_seconds": 3}
        lang_settings = {"input_lang": "fr"}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler._translate_text",
                return_value=("안녕하세요", False),
            ),
            patch("websocket_handler.get_user_model", return_value=user_model),
            patch(
                "websocket_handler.get_usage_log_model",
                return_value=usage_log_model,
            ),
            patch("websocket_handler.update_user_session"),
        ):
            asyncio.run(
                _handle_transcript(
                    ws,
                    data,
                    user_info,
                    MagicMock(),
                    MagicMock(),
                    True,
                    language_settings=lang_settings,
                )
            )

        sent = _get_sent_messages(ws)
        assert sent[0]["source_language"] == "fr"
        assert sent[0]["target_language"] == "ko"
