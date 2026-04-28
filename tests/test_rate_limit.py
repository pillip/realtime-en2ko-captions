"""
WebSocket rate limiting 단위 테스트
_check_rate_limit 함수 및 handle_openai_websocket 통합 테스트
"""

import collections
import json
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# websocket_handler -> auth -> streamlit 의존성 mock
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ============================================================
# _check_rate_limit 단위 테스트
# ============================================================
class TestCheckRateLimit:
    """_check_rate_limit 함수 테스트"""

    def test_allows_messages_under_limit(self):
        """제한 이하 메시지는 허용"""
        from websocket_handler import _check_rate_limit

        timestamps = collections.deque()
        for _ in range(5):
            assert _check_rate_limit(timestamps, limit=10) is True
        assert len(timestamps) == 5

    def test_blocks_messages_at_limit(self):
        """제한에 도달하면 차단"""
        from websocket_handler import _check_rate_limit

        timestamps = collections.deque()
        for _ in range(3):
            assert _check_rate_limit(timestamps, limit=3) is True

        # 4th message should be blocked
        assert _check_rate_limit(timestamps, limit=3) is False

    def test_old_timestamps_are_evicted(self):
        """60초 이상 된 타임스탬프는 제거"""
        from websocket_handler import _check_rate_limit

        timestamps = collections.deque()
        old_time = time.time() - 61.0
        timestamps.append(old_time)
        timestamps.append(old_time)
        timestamps.append(old_time)

        assert _check_rate_limit(timestamps, limit=3) is True
        assert len(timestamps) == 1

    def test_sliding_window_allows_after_expiry(self):
        """슬라이딩 윈도우: 오래된 메시지 만료 후 새 메시지 허용"""
        from websocket_handler import _check_rate_limit

        timestamps = collections.deque()
        now = time.time()

        for _ in range(3):
            timestamps.append(now - 59.0)

        assert _check_rate_limit(timestamps, limit=3) is False

        timestamps.clear()
        for _ in range(3):
            timestamps.append(now - 61.0)

        assert _check_rate_limit(timestamps, limit=3) is True

    def test_default_limit_uses_env_var(self):
        """limit=None 시 WS_RATE_LIMIT_PER_MINUTE 사용"""
        from websocket_handler import _check_rate_limit

        timestamps = collections.deque()
        with patch("websocket_handler.WS_RATE_LIMIT_PER_MINUTE", 2):
            assert _check_rate_limit(timestamps) is True
            assert _check_rate_limit(timestamps) is True
            assert _check_rate_limit(timestamps) is False

    def test_exactly_at_boundary(self):
        """정확히 60초 경계의 타임스탬프 처리"""
        from websocket_handler import _check_rate_limit

        timestamps = collections.deque()
        now = time.time()
        timestamps.append(now - 60.0)

        assert _check_rate_limit(timestamps, limit=1) is True
        assert len(timestamps) == 1

    def test_empty_deque_allows_message(self):
        """빈 deque에서 첫 메시지 허용"""
        from websocket_handler import _check_rate_limit

        timestamps = collections.deque()
        assert _check_rate_limit(timestamps, limit=1) is True
        assert len(timestamps) == 1

    def test_31st_message_in_60s_blocked(self):
        """AC: 31st message in 60s is rate_limit_exceeded"""
        from websocket_handler import _check_rate_limit

        timestamps = collections.deque()
        results = []
        for _ in range(31):
            results.append(_check_rate_limit(timestamps, limit=30))

        # First 30 allowed, 31st blocked
        assert all(results[:30])
        assert results[30] is False


# ============================================================
# handle_openai_websocket 레이트 리밋 통합 테스트
# ============================================================
def _make_websocket(messages):
    """테스트용 mock WebSocket (메시지 목록을 순서대로 반환)"""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)

    call_count = 0

    async def mock_anext(self_ws):
        nonlocal call_count
        if call_count < len(messages):
            msg = messages[call_count]
            call_count += 1
            return msg
        raise StopAsyncIteration

    ws.__aiter__ = lambda self_ws: self_ws
    ws.__anext__ = mock_anext
    return ws


def _get_sent_messages(ws):
    """WebSocket send 호출에서 JSON 메시지 추출"""
    return [json.loads(call.args[0]) for call in ws.send.call_args_list]


class TestHandleWebsocketRateLimit:
    """handle_openai_websocket 내 rate limiting 통합 테스트"""

    @pytest.mark.asyncio
    async def test_rate_limited_message_sends_error(self):
        """레이트 리밋 초과 메시지에 error 응답, 번역 미수행"""
        from websocket_handler import handle_openai_websocket

        messages = [
            json.dumps(
                {
                    "type": "transcript",
                    "text": f"Hello {i}",
                    "audio_duration_seconds": 1,
                }
            )
            for i in range(4)
        ]

        ws = _make_websocket(messages)
        mock_user = {
            "id": 1,
            "username": "testuser",
            "role": "user",
            "full_name": None,
            "is_active": 1,
        }
        mock_handle_transcript = AsyncMock()

        with (
            patch(
                "websocket_handler._authenticate_client",
                new=AsyncMock(return_value=mock_user),
            ),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
            patch(
                "websocket_handler._handle_transcript",
                mock_handle_transcript,
            ),
            patch("websocket_handler.WS_RATE_LIMIT_PER_MINUTE", 3),
        ):
            await handle_openai_websocket(ws)

        # _handle_transcript called only 3 times (4th rate-limited)
        assert mock_handle_transcript.call_count == 3

        sent = _get_sent_messages(ws)
        rate_limit_msgs = [m for m in sent if m.get("message") == "rate_limit_exceeded"]
        assert len(rate_limit_msgs) == 1

    @pytest.mark.asyncio
    async def test_non_transcript_messages_bypass_rate_limit(self):
        """transcript 이외 메시지는 레이트 리밋 미적용"""
        from websocket_handler import handle_openai_websocket

        messages = [json.dumps({"type": "request_openai_session"}) for _ in range(5)]

        ws = _make_websocket(messages)
        mock_user = {
            "id": 1,
            "username": "testuser",
            "role": "user",
            "full_name": None,
            "is_active": 1,
        }
        mock_session_handler = AsyncMock()

        with (
            patch(
                "websocket_handler._authenticate_client",
                new=AsyncMock(return_value=mock_user),
            ),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
            patch(
                "websocket_handler._handle_session_request",
                mock_session_handler,
            ),
            patch("websocket_handler.WS_RATE_LIMIT_PER_MINUTE", 3),
        ):
            await handle_openai_websocket(ws)

        assert mock_session_handler.call_count == 5

        sent = _get_sent_messages(ws)
        rate_limit_msgs = [m for m in sent if m.get("message") == "rate_limit_exceeded"]
        assert len(rate_limit_msgs) == 0

    @pytest.mark.asyncio
    async def test_rate_limit_is_per_connection(self):
        """각 연결은 독립적인 레이트 리밋을 가짐"""
        from websocket_handler import handle_openai_websocket

        mock_user = {
            "id": 1,
            "username": "testuser",
            "role": "user",
            "full_name": None,
            "is_active": 1,
        }

        # Connection 1: send 3 messages (fills limit)
        messages1 = [
            json.dumps(
                {
                    "type": "transcript",
                    "text": f"conn1-{i}",
                    "audio_duration_seconds": 1,
                }
            )
            for i in range(3)
        ]
        ws1 = _make_websocket(messages1)
        mock_transcript1 = AsyncMock()

        with (
            patch(
                "websocket_handler._authenticate_client",
                new=AsyncMock(return_value=mock_user),
            ),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
            patch(
                "websocket_handler._handle_transcript",
                mock_transcript1,
            ),
            patch("websocket_handler.WS_RATE_LIMIT_PER_MINUTE", 3),
        ):
            await handle_openai_websocket(ws1)

        # Connection 2: should have fresh limit
        messages2 = [
            json.dumps(
                {
                    "type": "transcript",
                    "text": f"conn2-{i}",
                    "audio_duration_seconds": 1,
                }
            )
            for i in range(3)
        ]
        ws2 = _make_websocket(messages2)
        mock_transcript2 = AsyncMock()

        with (
            patch(
                "websocket_handler._authenticate_client",
                new=AsyncMock(return_value=mock_user),
            ),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), True),
            ),
            patch(
                "websocket_handler._handle_transcript",
                mock_transcript2,
            ),
            patch("websocket_handler.WS_RATE_LIMIT_PER_MINUTE", 3),
        ):
            await handle_openai_websocket(ws2)

        # Both connections handled all 3 messages
        assert mock_transcript1.call_count == 3
        assert mock_transcript2.call_count == 3
