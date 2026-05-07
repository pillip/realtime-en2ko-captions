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

    def test_specific_language_validates_and_proceeds(self, mock_db, user_info):
        """input_lang이 특정 언어이고 감지 결과가 일치하면 번역 진행 (ISSUE-34)"""
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        ws = _make_websocket()
        data = {"text": "Hello world", "audio_duration_seconds": 5}
        lang_settings = {"input_lang": "en", "output_lang": "ja"}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ) as mock_detect,
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

        # detect_language IS called for validation (ISSUE-34)
        mock_detect.assert_called_once()

        sent = _get_sent_messages(ws)
        assert len(sent) == 1
        assert sent[0]["source_language"] == "en"
        assert sent[0]["target_language"] == "ja"

    def test_language_mismatch_blocks_translation(self, mock_db, user_info):
        """input_lang과 감지 언어가 다르면 번역 차단 (ISSUE-34)"""
        from websocket_handler import _handle_transcript

        ws = _make_websocket()
        data = {"text": "Hello world", "audio_duration_seconds": 5}
        lang_settings = {"input_lang": "ko", "output_lang": "en"}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ),
            patch("websocket_handler._translate_text") as mock_translate,
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

        # Translation should NOT be called
        mock_translate.assert_not_called()

        sent = _get_sent_messages(ws)
        mismatch_msgs = [m for m in sent if m.get("type") == "language_mismatch"]
        assert len(mismatch_msgs) == 1
        assert mismatch_msgs[0]["expected"] == "ko"
        assert mismatch_msgs[0]["detected"] == "en"

    def test_zh_mismatch_with_english_text(self, mock_db, user_info):
        """input_lang=zh에 영어 텍스트 → 차단 (ISSUE-34)"""
        from websocket_handler import _handle_transcript

        ws = _make_websocket()
        data = {"text": "Hello world", "audio_duration_seconds": 5}
        lang_settings = {"input_lang": "zh", "output_lang": "ko"}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ),
            patch("websocket_handler._translate_text") as mock_translate,
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

        mock_translate.assert_not_called()
        sent = _get_sent_messages(ws)
        mismatch_msgs = [m for m in sent if m.get("type") == "language_mismatch"]
        assert len(mismatch_msgs) == 1
        assert mismatch_msgs[0]["expected"] == "zh"
        assert mismatch_msgs[0]["detected"] == "en"

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
        """output_lang이 없으면 기본값 'ko' 사용 (감지 일치 시)"""
        from database import UsageLog, User
        from websocket_handler import _handle_transcript

        user_model = User(mock_db)
        usage_log_model = UsageLog(mock_db)

        ws = _make_websocket()
        data = {"text": "Hello world", "audio_duration_seconds": 3}
        lang_settings = {"input_lang": "en"}

        with (
            patch("websocket_handler.check_usage_limit", return_value=True),
            patch(
                "websocket_handler.detect_language",
                return_value=("en", "ko"),
            ),
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
        assert sent[0]["source_language"] == "en"
        assert sent[0]["target_language"] == "ko"


# ============================================================
# _handle_session_request 에러 분기 (lines 624-632) — RL-006
# ============================================================
class TestHandleSessionRequest:
    """_handle_session_request 의 성공/예외 분기 검증."""

    def test_success_sends_openai_session(self):
        """create_openai_session 성공 → openai_session 메시지 전송."""
        from websocket_handler import _handle_session_request

        ws = _make_websocket()

        async def fake_create():
            return {"id": "sess-1", "client_secret": "abc"}

        with patch(
            "websocket_handler.create_openai_session",
            side_effect=fake_create,
        ):
            asyncio.run(_handle_session_request(ws))

        sent = _get_sent_messages(ws)
        assert len(sent) == 1
        assert sent[0]["type"] == "openai_session"
        assert sent[0]["session"] == {"id": "sess-1", "client_secret": "abc"}

    def test_create_session_raises_sends_error_message(self):
        """create_openai_session 가 raise 하면 error 메시지가 전송된다.

        Note: 현재 구현은 RL-006 위반 — error.message 가 ``str(e)`` 를 echo 한다.
        이 테스트는 실제 동작 (메시지 전송 + 메시지 type=error) 만 단언한다.
        """
        from websocket_handler import _handle_session_request

        ws = _make_websocket()

        async def fake_fail():
            raise RuntimeError("OpenAI API 503")

        with patch(
            "websocket_handler.create_openai_session",
            side_effect=fake_fail,
        ):
            asyncio.run(_handle_session_request(ws))

        sent = _get_sent_messages(ws)
        assert len(sent) == 1
        assert sent[0]["type"] == "error"
        # 메시지에 "OpenAI 세션 생성 실패" 라는 도메인 prefix 가 포함돼야 한다.
        assert "OpenAI 세션 생성 실패" in sent[0]["message"]


# ============================================================
# handle_openai_websocket: 메시지 처리 예외 / 정리 (lines 770-792)
# ============================================================
class TestHandleOpenaiWebsocketErrorPaths:
    """handle_openai_websocket 의 외부/내부 예외 분기 + finally 정리 검증."""

    def _make_iterable_ws(self, messages):
        """주어진 list[str|Exception] 을 한 번씩 yield 하는 async websocket mock.

        Exception 인스턴스는 raise 해서 message-loop 내부 try 의 except 분기를
        검증할 수 있게 한다. ``__aiter__`` 는 sync 로 self 를 반환하고 ``__anext__``
        만 coroutine 인 형태 — 기존 test_websocket_handler 에서 사용하는 패턴.
        """
        ws = AsyncMock()
        ws.remote_address = ("127.0.0.1", 12345)
        ws.send = AsyncMock()
        ws.recv = AsyncMock()
        ws.close = AsyncMock()

        idx = {"i": 0}

        async def _anext(self_):
            i = idx["i"]
            idx["i"] += 1
            if i >= len(messages):
                raise StopAsyncIteration
            m = messages[i]
            if isinstance(m, BaseException):
                raise m
            return m

        ws.__aiter__ = lambda self_: self_
        ws.__anext__ = _anext
        return ws

    def test_per_message_exception_sends_generic_error_message(self, mock_db):
        """메시지 처리 중 예외 → generic 메시지 (RL-006: str(e) echo 금지)."""
        from websocket_handler import handle_openai_websocket

        # transcript 메시지를 보내고, _handle_transcript 가 raise 하도록 함.
        ws = self._make_iterable_ws([json.dumps({"type": "transcript", "text": "hi"})])
        mock_user = {
            "id": 1,
            "username": "testuser",
            "role": "user",
            "is_active": True,
            "language_settings": {"input_lang": "auto", "output_lang": "ko"},
            "room_id": None,
        }
        mock_auth = AsyncMock(return_value=mock_user)

        with (
            patch("websocket_handler._authenticate_client", new=mock_auth),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
            patch(
                "websocket_handler._handle_transcript",
                side_effect=RuntimeError("internal/secret/path leaked"),
            ),
        ):
            asyncio.run(handle_openai_websocket(ws))

        sent = _get_sent_messages(ws)
        # connection + error 메시지가 모두 있어야 한다.
        types = [m.get("type") for m in sent]
        assert "connection" in types
        assert "error" in types

        error_msg = next(m for m in sent if m.get("type") == "error")
        # RL-006: 내부 예외 텍스트가 절대 새어 나가서는 안 된다.
        assert "secret/path" not in error_msg["message"]
        assert "RuntimeError" not in error_msg["message"]
        # 우리 generic 메시지여야 한다.
        assert error_msg["message"] == "메시지 처리 중 오류가 발생했습니다."

    def test_outer_exception_during_message_loop_logs_and_cleans_up(self, capsys):
        """메시지 루프 내부에서 raise 가 try 의 outer except 까지 도달하는 케이스.

        - _init_translation_clients 가 raise → outer except → 서버 로그.
        - finally 블록에서 unregister_connection 이 (room_id 가 있다면) 호출된다.
        """
        from websocket_handler import handle_openai_websocket

        ws = self._make_iterable_ws([])
        mock_user = {
            "id": 1,
            "username": "alice",
            "role": "user",
            "is_active": True,
            "language_settings": {"input_lang": "auto", "output_lang": "ko"},
            "room_id": "room-zombie",
        }
        mock_auth = AsyncMock(return_value=mock_user)

        # RoomManager 의 unregister_connection 이 정상 동작하는지 확인.
        mock_room_mgr = MagicMock()
        mock_room_mgr.unregister_connection = MagicMock()

        with (
            patch("websocket_handler._authenticate_client", new=mock_auth),
            patch(
                "websocket_handler._init_translation_clients",
                side_effect=RuntimeError("aws creds missing"),
            ),
            patch("websocket_handler._room_manager", mock_room_mgr),
        ):
            asyncio.run(handle_openai_websocket(ws))

        # outer except 가 서버에 로깅 — RL-006 (서버에는 디테일 OK).
        captured = capsys.readouterr()
        assert "[WebSocket] 연결 오류" in captured.out
        # finally: room_id 가 있으니 unregister 가 호출돼야 한다.
        mock_room_mgr.unregister_connection.assert_called_once_with("room-zombie", ws)

    def test_finally_cleanup_swallows_unregister_exceptions(self, capsys):
        """unregister_connection 이 raise 해도 핸들러 자체는 raise 하지 않는다.

        대신 [Room] 연결 해제 실패 로그가 남아야 한다 (RL-006).
        """
        from websocket_handler import handle_openai_websocket

        ws = self._make_iterable_ws([])
        mock_user = {
            "id": 1,
            "username": "alice",
            "role": "user",
            "is_active": True,
            "language_settings": {"input_lang": "auto", "output_lang": "ko"},
            "room_id": "room-1",
        }
        mock_auth = AsyncMock(return_value=mock_user)

        mock_room_mgr = MagicMock()
        mock_room_mgr.unregister_connection.side_effect = RuntimeError("repo down")

        with (
            patch("websocket_handler._authenticate_client", new=mock_auth),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
            patch("websocket_handler._room_manager", mock_room_mgr),
        ):
            # 절대 raise 해서는 안 됨.
            asyncio.run(handle_openai_websocket(ws))

        captured = capsys.readouterr()
        assert "[Room] 연결 해제 실패" in captured.out

    def test_finally_skips_unregister_when_no_user_info(self):
        """user_info 가 None 이면 finally 의 unregister 분기를 건너뛴다."""
        from websocket_handler import handle_openai_websocket

        ws = self._make_iterable_ws([])
        # auth 가 실패해 None 반환.
        mock_auth = AsyncMock(return_value=None)

        mock_room_mgr = MagicMock()

        with (
            patch("websocket_handler._authenticate_client", new=mock_auth),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
            patch("websocket_handler._room_manager", mock_room_mgr),
        ):
            asyncio.run(handle_openai_websocket(ws))

        # 인증 실패면 unregister 가 호출되면 안 된다.
        mock_room_mgr.unregister_connection.assert_not_called()

    def test_finally_skips_unregister_when_room_id_missing(self):
        """user_info 는 있지만 room_id 가 falsy → unregister 호출 안 함."""
        from websocket_handler import handle_openai_websocket

        ws = self._make_iterable_ws([])
        mock_user = {
            "id": 1,
            "username": "alice",
            "role": "user",
            "is_active": True,
            "language_settings": {"input_lang": "auto", "output_lang": "ko"},
            # room_id 없음.
        }
        mock_auth = AsyncMock(return_value=mock_user)

        mock_room_mgr = MagicMock()

        with (
            patch("websocket_handler._authenticate_client", new=mock_auth),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
            patch("websocket_handler._room_manager", mock_room_mgr),
        ):
            asyncio.run(handle_openai_websocket(ws))

        mock_room_mgr.unregister_connection.assert_not_called()


# ============================================================
# _periodic_room_cleanup_loop (lines 801-822)
# ============================================================
class TestPeriodicRoomCleanupLoop:
    """_periodic_room_cleanup_loop 의 분기 검증.

    asyncio.sleep 을 monkey-patch 해 즉시 다음 사이클로 진입시키고,
    task.cancel() 로 루프를 종료하는 패턴을 사용한다.
    """

    def test_repo_none_skips_cleanup_continues_loop(self):
        """_room_manager._repo == None → 한 사이클 그냥 continue 한 뒤 cancel."""

        async def runner():
            from websocket_handler import _periodic_room_cleanup_loop

            mock_room_mgr = MagicMock()
            mock_room_mgr._repo = None

            sleep_calls = {"n": 0}

            async def _fake_sleep(_seconds):
                # 첫 sleep 만 통과시키고, 두 번째 sleep 에서 raise CancelledError.
                sleep_calls["n"] += 1
                if sleep_calls["n"] >= 2:
                    raise asyncio.CancelledError()

            with (
                patch("websocket_handler._room_manager", mock_room_mgr),
                patch("websocket_handler.asyncio.sleep", new=_fake_sleep),
            ):
                with pytest.raises(asyncio.CancelledError):
                    await _periodic_room_cleanup_loop()

            return sleep_calls["n"]

        n = asyncio.run(runner())
        # 최소 2번 sleep 이 호출됐다 (첫 사이클 + 두 번째 cancel).
        assert n == 2

    def test_cleanup_returns_room_ids_calls_delete_room_for_each(self):
        """cleanup_stale_rooms 가 ['z1','z2'] → delete_room 가 정확히 두 번 호출."""

        async def runner():
            from websocket_handler import _periodic_room_cleanup_loop

            mock_repo = MagicMock()
            # 첫 사이클에 두 개 반환, 이후엔 빈 리스트.
            mock_repo.cleanup_stale_rooms.side_effect = [["z1", "z2"], []]

            mock_room_mgr = MagicMock()
            mock_room_mgr._repo = mock_repo

            sleep_calls = {"n": 0}

            async def _fake_sleep(_seconds):
                sleep_calls["n"] += 1
                if sleep_calls["n"] >= 2:
                    raise asyncio.CancelledError()

            with (
                patch("websocket_handler._room_manager", mock_room_mgr),
                patch("websocket_handler.asyncio.sleep", new=_fake_sleep),
            ):
                with pytest.raises(asyncio.CancelledError):
                    await _periodic_room_cleanup_loop()

            return mock_room_mgr.delete_room.call_args_list

        delete_calls = asyncio.run(runner())
        # 정확히 두 룸이 delete 됐어야 한다 (RL-004: exact equality).
        assert [c.args[0] for c in delete_calls] == ["z1", "z2"]

    def test_cleanup_raises_loop_continues_until_cancelled(self, capsys):
        """cleanup_stale_rooms 가 raise → 로그 후 루프 계속 (실패 격리)."""

        async def runner():
            from websocket_handler import _periodic_room_cleanup_loop

            mock_repo = MagicMock()
            mock_repo.cleanup_stale_rooms.side_effect = RuntimeError("db locked")

            mock_room_mgr = MagicMock()
            mock_room_mgr._repo = mock_repo

            sleep_calls = {"n": 0}

            async def _fake_sleep(_seconds):
                sleep_calls["n"] += 1
                # 두 번 cleanup 실패를 허용하고 세 번째에 cancel.
                if sleep_calls["n"] >= 3:
                    raise asyncio.CancelledError()

            with (
                patch("websocket_handler._room_manager", mock_room_mgr),
                patch("websocket_handler.asyncio.sleep", new=_fake_sleep),
            ):
                with pytest.raises(asyncio.CancelledError):
                    await _periodic_room_cleanup_loop()

            return mock_repo.cleanup_stale_rooms.call_count

        call_count = asyncio.run(runner())
        # 최소 2번 cleanup 가 시도됐어야 한다 — 한 번의 raise 가 루프를 죽이면 안 됨.
        assert call_count == 2
        captured = capsys.readouterr()
        assert "[Room] cleanup loop error" in captured.out

    def test_cancellation_propagates_cleanly(self):
        """CancelledError 는 except 에서 잡히지 않고 전파돼야 한다."""

        async def runner():
            from websocket_handler import _periodic_room_cleanup_loop

            mock_repo = MagicMock()
            mock_repo.cleanup_stale_rooms.return_value = []

            mock_room_mgr = MagicMock()
            mock_room_mgr._repo = mock_repo

            async def _fake_sleep(_seconds):
                raise asyncio.CancelledError()

            with (
                patch("websocket_handler._room_manager", mock_room_mgr),
                patch("websocket_handler.asyncio.sleep", new=_fake_sleep),
            ):
                # CancelledError 가 그대로 bubble up.
                with pytest.raises(asyncio.CancelledError):
                    await _periodic_room_cleanup_loop()

        asyncio.run(runner())


# ============================================================
# Module-level singletons & accessors (lines 47, 52, 64)
# ============================================================
class TestModuleSingletons:
    """get_room_manager / get_broadcast_manager / attach_broadcast_metrics_repo."""

    def test_get_room_manager_returns_module_singleton(self):
        from websocket_handler import _room_manager, get_room_manager

        assert get_room_manager() is _room_manager

    def test_get_broadcast_manager_returns_module_singleton(self):
        from websocket_handler import _broadcast_manager, get_broadcast_manager

        assert get_broadcast_manager() is _broadcast_manager

    def test_attach_broadcast_metrics_repo_wires_repo_into_manager(self):
        """attach_broadcast_metrics_repo 가 BroadcastManager 의 _metrics_repo 를 set."""
        from websocket_handler import (
            _broadcast_manager,
            attach_broadcast_metrics_repo,
        )

        sentinel_repo = MagicMock()
        original = _broadcast_manager._metrics_repo
        try:
            attach_broadcast_metrics_repo(sentinel_repo)
            assert _broadcast_manager._metrics_repo is sentinel_repo
        finally:
            # 다른 테스트에 영향이 없도록 원복.
            _broadcast_manager._metrics_repo = original


# ============================================================
# _translate_secondary (lines 624-632)
# ============================================================
class TestTranslateSecondary:
    """_translate_secondary 는 _translate_text 를 호출해 첫 번째 값(번역문)만 반환."""

    def test_returns_translated_text_from_translate_text(self):
        """_translate_text 가 (text, used_llm) 튜플 → 첫 요소 반환."""
        from websocket_handler import _translate_secondary

        translate_client = MagicMock()
        bedrock_client = MagicMock()

        with patch(
            "websocket_handler._translate_text",
            return_value=("Hello", True),
        ) as mock_tt:
            result = _translate_secondary(
                "안녕",
                "ko",
                "en",
                translate_client,
                bedrock_client,
                bedrock_available=True,
            )

        assert result == "Hello"
        mock_tt.assert_called_once_with(
            "안녕",
            "ko",
            "en",
            translate_client,
            bedrock_client,
            True,
        )

    def test_returns_none_when_translate_text_returns_none(self):
        """_translate_text 가 (None, False) 를 반환하면 None 통과."""
        from websocket_handler import _translate_secondary

        with patch(
            "websocket_handler._translate_text",
            return_value=(None, False),
        ):
            result = _translate_secondary(
                "hi",
                "en",
                "ko",
                MagicMock(),
                None,
                bedrock_available=False,
            )

        assert result is None


# ============================================================
# language_update inside handle_openai_websocket (lines 727-744)
# ============================================================
class TestLanguageUpdateHandling:
    """language_update 메시지로 connection 의 language_settings 가 갱신된다."""

    def test_language_update_message_emits_language_updated_response(self):
        """language_update 메시지 → language_updated 응답이 송신된다."""
        from websocket_handler import handle_openai_websocket

        ws = AsyncMock()
        ws.remote_address = ("127.0.0.1", 12345)
        ws.send = AsyncMock()

        # 한 번의 language_update 메시지를 yield 하는 async iterator.
        idx = {"i": 0}

        async def _anext(self_):
            i = idx["i"]
            idx["i"] += 1
            if i == 0:
                return json.dumps(
                    {
                        "type": "language_update",
                        "input_lang": "en",
                        "output_lang": "ja",
                    }
                )
            raise StopAsyncIteration

        ws.__aiter__ = lambda self_: self_
        ws.__anext__ = _anext

        mock_user = {
            "id": 1,
            "username": "alice",
            "role": "user",
            "is_active": True,
            "language_settings": {"input_lang": "auto", "output_lang": "ko"},
            "room_id": None,
        }

        with (
            patch(
                "websocket_handler._authenticate_client",
                new=AsyncMock(return_value=mock_user),
            ),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
        ):
            asyncio.run(handle_openai_websocket(ws))

        sent = _get_sent_messages(ws)
        # connection 메시지 + language_updated 메시지가 있어야 한다.
        types = [m.get("type") for m in sent]
        assert "language_updated" in types
        updated = next(m for m in sent if m["type"] == "language_updated")
        assert updated["input_lang"] == "en"
        assert updated["output_lang"] == "ja"
