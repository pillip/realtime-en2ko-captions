"""
WebSocket _authenticate_client 단위 테스트
DB 검증 로직, 비활성 사용자 거부, 역할 강제 적용 등을 검증
"""

import asyncio
import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.ws_harness import (
    cookie_value_signed_with,
    make_websocket,
    sent_messages,
    signed_cookie_value,
)


@pytest.fixture
def db_path(tmp_path):
    """임시 데이터베이스 경로"""
    return str(tmp_path / "test_ws_auth.db")


@pytest.fixture
def mock_db(db_path):
    """테스트용 데이터베이스 (활성 + 비활성 사용자 포함)"""
    from database import DatabaseManager, User

    db = DatabaseManager(db_path)
    user_model = User(db)

    # Active user with role "user"
    user_model.create_user(
        username="testuser",
        password="testpass",
        role="user",
        usage_limit_seconds=3600,
    )
    # Active admin
    user_model.create_user(
        username="admin",
        password="adminpass",
        role="admin",
        usage_limit_seconds=0,
    )
    # Inactive user
    uid = user_model.create_user(
        username="inactive",
        password="inactivepass",
        role="user",
        usage_limit_seconds=3600,
    )
    user_model.update_user(uid, is_active=False)

    return user_model


# ISSUE-55: 하네스는 `tests/ws_harness.py` 한 곳에만 있다 (RL-001). 쿠키를 붙이는
# 자리가 여기 사본으로 돌아오면 서명 체계가 갈라진다. 인자를 생략하면 auth
# 프레임이 주장하는 신원과 일치하는 **유효 서명 쿠키**가 자동으로 붙으므로,
# 아래 DB 단계 거절 테스트들(존재하지 않는 id / 비활성 / 사용자명 불일치)이
# 쿠키 게이트에 미리 걸리지 않고 원래 의도대로 DB 검증을 계속 검증한다.
_make_websocket = make_websocket
_get_sent_messages = sent_messages


class TestAuthenticateClientDbValidation:
    """_authenticate_client DB 검증 테스트"""

    def test_nonexistent_user_id_returns_none(self, mock_db):
        """존재하지 않는 user_id로 인증 시 None 반환하고 auth_success 미전송"""
        from websocket_handler import _authenticate_client

        ws = _make_websocket(
            {
                "type": "auth",
                "user": {"id": 9999, "username": "ghost", "role": "admin"},
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        assert all(msg.get("type") != "auth_success" for msg in sent)
        assert any(msg.get("type") == "auth_error" for msg in sent)

    def test_role_overwritten_from_db(self, mock_db):
        """클라이언트가 role='admin'을 주장하더라도 DB의 role='user'로 덮어씀"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "testuser",
                    "role": "admin",  # claimed admin
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["role"] == "user"  # DB role, not claimed role

    def test_inactive_user_returns_none(self, mock_db):
        """비활성 사용자(is_active=0) 인증 시 None 반환하고 에러 메시지 전송"""
        from websocket_handler import _authenticate_client

        inactive_user = mock_db.get_user_by_username("inactive")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": inactive_user["id"],
                    "username": "inactive",
                    "role": "user",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        assert any(msg.get("type") == "auth_error" for msg in sent)

    def test_valid_user_returns_validated_info(self, mock_db):
        """유효한 사용자 인증 시 DB 기반 user_info 반환 및 auth_success 전송"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "testuser",
                    "role": "user",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["id"] == db_user["id"]
        assert result["username"] == "testuser"
        assert result["role"] == "user"
        assert result["is_active"] == 1

        sent = _get_sent_messages(ws)
        assert any(msg.get("type") == "auth_success" for msg in sent)

    def test_username_mismatch_returns_none(self, mock_db):
        """user_id는 존재하지만 username이 DB와 다르면 None 반환 (impersonation 방지)"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "impersonator",
                    "role": "user",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        assert any(msg.get("type") == "auth_error" for msg in sent)

    def test_missing_user_info_returns_none(self, mock_db):
        """auth 메시지에 user 정보가 없으면 None 반환"""
        from websocket_handler import _authenticate_client

        ws = _make_websocket({"type": "auth"})

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None

    def test_missing_user_id_returns_none(self, mock_db):
        """user dict에 id 필드가 없으면 None 반환"""
        from websocket_handler import _authenticate_client

        ws = _make_websocket(
            {
                "type": "auth",
                "user": {"username": "testuser", "role": "user"},
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None

    def test_admin_role_preserved_for_real_admin(self, mock_db):
        """실제 관리자의 role은 'admin'으로 유지"""
        from websocket_handler import _authenticate_client

        admin_user = mock_db.get_user_by_username("admin")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": admin_user["id"],
                    "username": "admin",
                    "role": "admin",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["role"] == "admin"

    def test_timeout_returns_none(self):
        """인증 메시지 수신 타임아웃 시 None 반환"""
        from websocket_handler import _authenticate_client

        ws = AsyncMock()
        ws.recv = AsyncMock(side_effect=TimeoutError)

        result = asyncio.run(_authenticate_client(ws))
        assert result is None

    def test_non_auth_message_returns_none(self):
        """auth 타입이 아닌 메시지 수신 시 None 반환"""
        from websocket_handler import _authenticate_client

        ws = _make_websocket({"type": "ping"})

        result = asyncio.run(_authenticate_client(ws))
        assert result is None

    def test_error_message_does_not_leak_internals(self, mock_db):
        """에러 메시지에 내부 정보(DB 사용자명 등)가 노출되지 않음 (RL-006)"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "impersonator",
                    "role": "user",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            asyncio.run(_authenticate_client(ws))

        sent = _get_sent_messages(ws)
        for msg in sent:
            if msg.get("type") == "auth_error":
                # Error message should not contain the real username
                assert "testuser" not in msg.get("message", "")
                # Should not contain user_id
                assert str(db_user["id"]) not in msg.get("message", "")


# === ISSUE-2: Language Settings in Auth ===


class TestAuthenticateClientLanguageSettings:
    """_authenticate_client 언어 설정 전달 테스트 (ISSUE-2)"""

    def test_language_settings_extracted_from_auth(self, mock_db):
        """auth 메시지에 language_settings가 있으면 validated_user에 포함"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "testuser",
                    "role": "user",
                },
                "language_settings": {
                    "input_lang": "en",
                    "output_lang": "ko",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["language_settings"]["input_lang"] == "en"
        assert result["language_settings"]["output_lang"] == "ko"

    def test_language_settings_defaults_when_missing(self, mock_db):
        """auth 메시지에 language_settings가 없으면 기본값(auto/ko) 사용"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "testuser",
                    "role": "user",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["language_settings"]["input_lang"] == "auto"
        assert result["language_settings"]["output_lang"] == "ko"

    def test_language_settings_partial_defaults(self, mock_db):
        """language_settings에 일부 필드만 있으면 나머지는 기본값"""
        from websocket_handler import _authenticate_client

        db_user = mock_db.get_user_by_username("testuser")
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {
                    "id": db_user["id"],
                    "username": "testuser",
                    "role": "user",
                },
                "language_settings": {
                    "input_lang": "ja",
                },
            }
        )

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert result["language_settings"]["input_lang"] == "ja"
        assert result["language_settings"]["output_lang"] == "ko"


# ============================================================
# Room handling branches in _authenticate_client (lines 189-228)
# ============================================================
class TestAuthenticateClientRoomHandling:
    """requested_room_id 분기 — repo 예외 / closed / 메모리 race 시나리오 검증."""

    def _make_ws_with_room(self, mock_db, room_id):
        """testuser 자격으로 특정 room_id 요청하는 auth 메시지를 보내는 ws."""
        from unittest.mock import AsyncMock

        db_user = mock_db.get_user_by_username("testuser")
        return (
            _make_websocket(
                {
                    "type": "auth",
                    "user": {
                        "id": db_user["id"],
                        "username": "testuser",
                        "role": "user",
                    },
                    "room_id": room_id,
                }
            ),
            AsyncMock(),
        )

    def test_unknown_room_id_rejected_with_generic_message(self, mock_db, capsys):
        """존재하지 않는 room_id → auth_error '요청한 룸을 찾을 수 없습니다.' (RL-006).

        room_repo 가 없는 RoomManager 의 기본 in-memory 모드 → get_room()=None →
        room_closed 분기로 가서 거절된다.
        """
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        ws, _ = self._make_ws_with_room(mock_db, "no-such-room")

        # repo 가 없는 빈 RoomManager.
        empty_mgr = RoomManager()

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", empty_mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        # RL-006: 응답에 내부 디테일 누설 없음.
        auth_errors = [m for m in sent if m.get("type") == "auth_error"]
        assert len(auth_errors) == 1
        assert auth_errors[0]["message"] == "요청한 룸을 찾을 수 없습니다."

    def test_repo_exception_treated_as_room_closed(self, mock_db, capsys):
        """repo.get_by_id 가 raise → room_closed=True 로 fail-closed 처리."""
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        ws, _ = self._make_ws_with_room(mock_db, "r1")

        # 메모리에는 룸이 있지만 DB 조회는 실패하는 시나리오.
        class _ExplodingRepo:
            def get_by_id(self, room_id):
                raise RuntimeError("DB internal: locked")

            # RoomManager 의 hydrate_from_db 가 호출하는 다른 메서드는 없어야 함.

        mgr = RoomManager(room_repository=_ExplodingRepo())
        # 메모리에 r1 을 직접 넣어 get_room("r1") 가 not None 이 되게 한다.
        from room_manager import RoomState

        mgr._rooms["r1"] = RoomState(room_id="r1")

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        captured = capsys.readouterr()
        # 내부 에러는 서버 로그로 남아야 한다. repo 예외 처리는 #99 에서
        # RoomManager.adopt_from_db 로 이동했으므로 로그 문자열도 그쪽 것.
        assert "adopt_from_db 조회 실패" in captured.out

        sent = _get_sent_messages(ws)
        auth_errors = [m for m in sent if m.get("type") == "auth_error"]
        assert len(auth_errors) == 1
        # RL-006: generic message — 'DB internal' 같은 디테일이 절대 새면 안 됨.
        assert "DB internal" not in auth_errors[0]["message"]
        assert auth_errors[0]["message"] == "요청한 룸을 찾을 수 없습니다."

    def test_closed_room_status_rejected(self, mock_db):
        """persisted.status == 'closed' → auth_error 거절."""
        from room_manager import RoomManager, RoomState
        from websocket_handler import _authenticate_client

        ws, _ = self._make_ws_with_room(mock_db, "closed-room")

        class _ClosedRepo:
            def get_by_id(self, room_id):
                # 영속화된 상태가 closed 임을 시뮬레이트.
                return {"id": room_id, "status": "closed"}

        mgr = RoomManager(room_repository=_ClosedRepo())
        mgr._rooms["closed-room"] = RoomState(room_id="closed-room")

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = _get_sent_messages(ws)
        auth_errors = [m for m in sent if m.get("type") == "auth_error"]
        assert len(auth_errors) == 1
        assert auth_errors[0]["message"] == "요청한 룸을 찾을 수 없습니다."

    def test_register_connection_keyerror_race_returns_auth_error(self, mock_db):
        """register_connection 이 KeyError → race 분기로 generic 거절."""
        from room_manager import RoomManager, RoomState
        from websocket_handler import _authenticate_client

        ws, _ = self._make_ws_with_room(mock_db, "r1")

        class _GoodRepo:
            def get_by_id(self, room_id):
                return {"id": room_id, "status": "active"}

        mgr = RoomManager(room_repository=_GoodRepo())
        mgr._rooms["r1"] = RoomState(room_id="r1")

        # register_connection 이 KeyError 를 raise 하도록 패치.
        original_register = mgr.register_connection

        def _raising_register(room_id, websocket):
            # 메모리에서 이미 삭제됐다고 가정.
            raise KeyError(room_id)

        mgr.register_connection = _raising_register

        try:
            with (
                patch("websocket_handler.get_user_model", return_value=mock_db),
                patch("websocket_handler._room_manager", mgr),
            ):
                result = asyncio.run(_authenticate_client(ws))
        finally:
            mgr.register_connection = original_register

        assert result is None
        sent = _get_sent_messages(ws)
        auth_errors = [m for m in sent if m.get("type") == "auth_error"]
        assert len(auth_errors) == 1
        assert auth_errors[0]["message"] == "요청한 룸을 찾을 수 없습니다."


# ===========================================================================
# 세션 언어 rooms 행 기록 (ISSUE-52, FR-083 / TC-073)
# ===========================================================================
class _RecordingRoomRepo:
    """database.Room 의 최소 가짜 — 언어 기록 호출을 그대로 붙잡는다."""

    def __init__(self, row=None, raises=None):
        self._row = row or {"id": "r1", "status": "active"}
        self._raises = raises
        self.calls: list[dict] = []

    def get_by_id(self, room_id):
        if room_id == self._row["id"]:
            return dict(self._row)
        return None

    def update_session_languages(self, room_id, *, input_lang, output_lang):
        self.calls.append(
            {
                "room_id": room_id,
                "input_lang": input_lang,
                "output_lang": output_lang,
            }
        )
        if self._raises is not None:
            raise self._raises
        self._row["input_lang"] = input_lang
        self._row["output_lang"] = output_lang
        self._row["primary_output_lang"] = output_lang
        return True


def _auth_ws_for_room(room_id, *, input_lang, output_lang):
    return _make_websocket(
        {
            "type": "auth",
            "user": {"id": 1, "username": "testuser", "role": "user"},
            "room_id": room_id,
            "language_settings": {
                "input_lang": input_lang,
                "output_lang": output_lang,
            },
        }
    )


class TestAuthPersistsSessionLanguages:
    """AC1/AC3: 인증이 끝나면 rooms 행이 오퍼레이터가 고른 언어를 갖는다."""

    def test_auth_records_operator_output_lang_on_the_room_row(self, mock_db):
        """output_lang='vi' → 룸 행의 primary_output_lang 이 'vi' 가 된다.

        이 이슈의 헤드라인 결함: 이전에는 아무것도 기록되지 않아 무대 페이지가
        DB 의 'ko' 를 구독했다.
        """
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        repo = _RecordingRoomRepo(
            {
                "id": "r1",
                "status": "active",
                "input_lang": "auto",
                "output_lang": "ko",
                "primary_output_lang": "ko",
            }
        )
        mgr = RoomManager(room_repository=repo)
        ws = _auth_ws_for_room("r1", input_lang="ko", output_lang="vi")

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert repo.calls == [
            {"room_id": "r1", "input_lang": "ko", "output_lang": "vi"}
        ]
        assert repo.get_by_id("r1")["primary_output_lang"] == "vi"
        assert repo.get_by_id("r1")["output_lang"] == "vi"
        assert repo.get_by_id("r1")["input_lang"] == "ko"

    def test_recorded_room_id_is_the_server_resolved_one(self, mock_db):
        """RL-002: 기록 대상은 클라이언트 payload 가 아니라 서버가 확정한 값.

        **두 값이 실제로 갈리는 분기**를 몰아야 의미가 있다. auth 메시지에
        room_id 를 아예 넣지 않으면 서버는 DEFAULT_ROOM_ID 로 라우팅하므로,
        `data.get("room_id")`(=None)를 쓰는 구현과 `resolved_room_id` 를 쓰는
        구현이 서로 다른 결과를 낸다. room_id 를 보내면서 같은 값을 단언하는
        형태는 두 구현 모두 통과시켜 가드가 되지 못한다 (RL-004).
        """
        from room_manager import DEFAULT_ROOM_ID, RoomManager
        from websocket_handler import _authenticate_client

        repo = _RecordingRoomRepo({"id": DEFAULT_ROOM_ID, "status": "active"})
        mgr = RoomManager(room_repository=repo)
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {"id": 1, "username": "testuser", "role": "user"},
                # room_id 없음 → 서버가 DEFAULT_ROOM_ID 로 확정한다.
                "language_settings": {"input_lang": "ko", "output_lang": "vi"},
            }
        )

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert [c["room_id"] for c in repo.calls] == [DEFAULT_ROOM_ID]
        assert None not in [c["room_id"] for c in repo.calls]

    def test_unsupported_output_lang_does_not_break_auth(self, mock_db):
        """AC4 를 WS 계층에서 확인 — 지원 목록 밖 코드도 세션을 죽이지 않는다.

        DB 계층 거절은 test_rooms_db 가 덮는다. 여기서 보는 것은 그 False 가
        인증 흐름으로 새어 나오지 않는다는 것 — auth_success 는 그대로 나가고
        응답에 검증 사유가 실리지 않는다 (RL-006).
        """
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        class _RejectingRepo(_RecordingRoomRepo):
            def update_session_languages(self, room_id, *, input_lang, output_lang):
                self.calls.append({"room_id": room_id, "output_lang": output_lang})
                return False  # database.Room 의 검증 실패 계약과 동일

        repo = _RejectingRepo({"id": "r1", "status": "active"})
        mgr = RoomManager(room_repository=repo)
        ws = _auth_ws_for_room("r1", input_lang="ko", output_lang="xx")

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert [c["output_lang"] for c in repo.calls] == ["xx"]
        sent = _get_sent_messages(ws)
        assert any(m.get("type") == "auth_success" for m in sent)
        assert all(m.get("type") != "auth_error" for m in sent)
        assert "xx" not in json.dumps(sent, ensure_ascii=False)

    def test_auth_succeeds_when_the_language_write_raises(self, mock_db, capsys):
        """AC8/RL-006: 기록이 터져도 auth_success 는 그대로 나가고 예외 문구는
        클라이언트에 새지 않는다."""
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        repo = _RecordingRoomRepo(
            {"id": "r1", "status": "active"},
            raises=RuntimeError("db is on fire: /secret/path/app.db"),
        )
        mgr = RoomManager(room_repository=repo)
        ws = _auth_ws_for_room("r1", input_lang="ko", output_lang="vi")

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        sent = _get_sent_messages(ws)
        assert any(m.get("type") == "auth_success" for m in sent)
        wire = json.dumps(sent, ensure_ascii=False)
        assert "db is on fire" not in wire
        assert "/secret/path/app.db" not in wire
        # 서버 로그에는 남아야 한다 — 조용한 실패는 이 이슈가 고치는 결함이다.
        assert "db is on fire" in capsys.readouterr().out

    def test_memory_only_mode_skips_the_write_without_raising(self, mock_db):
        """AC9: repo 미주입(기본 룸/메모리 전용) 이면 조용히 스킵한다."""
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        mgr = RoomManager()  # repo 없음
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {"id": 1, "username": "testuser", "role": "user"},
                "language_settings": {"input_lang": "ko", "output_lang": "vi"},
            }
        )

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert any(m.get("type") == "auth_success" for m in _get_sent_messages(ws))


# ===========================================================================
# 언어 기록 성공 시 control 발행 (ISSUE-53, FR-084 / TC-079 앞절반)
# ===========================================================================
class TestAuthPublishesLanguageControl:
    """AC: 기록이 성공하면 control 1회, 스킵되면 0회.

    "스킵" 은 두 가지다 — 검증 실패(repo → False)와 **값이 이미 같은 경우**.
    후자를 함께 단언하지 않으면 "무조건 발행" 구현이 통과한다 (RL-004).
    """

    @staticmethod
    def _row(primary: str) -> dict:
        return {
            "id": "r1",
            "status": "active",
            "input_lang": "auto",
            "output_lang": primary,
            "primary_output_lang": primary,
        }

    @staticmethod
    def _run(mock_db, repo, ws, spy):
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        mgr = RoomManager(room_repository=repo) if repo is not None else RoomManager()
        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", mgr),
            patch("websocket_handler._broadcast_manager", spy),
        ):
            return asyncio.run(_authenticate_client(ws))

    def test_a_changed_language_publishes_exactly_one_control_frame(self, mock_db):
        """ko → vi 기록이 실제로 일어나면 control 이 정확히 1회 나간다."""
        from tests.broadcast_spy import SpyBroadcastManager

        spy = SpyBroadcastManager()
        repo = _RecordingRoomRepo(self._row("ko"))
        ws = _auth_ws_for_room("r1", input_lang="ko", output_lang="vi")

        assert self._run(mock_db, repo, ws, spy) is not None

        assert spy.control_count == 1, spy.control_calls
        room_id, payload = spy.control_calls[0]
        assert room_id == "r1"
        assert payload["event"] == "control"
        assert payload["primary_lang"] == "vi"
        assert spy.get_control("r1") == {"primary_lang": "vi"}

    def test_an_unchanged_language_publishes_nothing(self, mock_db):
        """행이 이미 vi 인데 vi 로 다시 인증하면 발행 0회다.

        재접속마다 control 을 쏘면 이미 보고 있는 화면이 이유 없이 재구독하고
        자막이 한 프레임 끊긴다.
        """
        from tests.broadcast_spy import SpyBroadcastManager

        spy = SpyBroadcastManager()
        repo = _RecordingRoomRepo(self._row("vi"))
        ws = _auth_ws_for_room("r1", input_lang="auto", output_lang="vi")

        assert self._run(mock_db, repo, ws, spy) is not None

        assert spy.control_count == 0, spy.control_calls
        assert spy.get_control("r1") is None

    def test_a_rejected_write_publishes_nothing(self, mock_db):
        """검증 실패(repo → False)면 상태도 발행도 없다."""
        from tests.broadcast_spy import SpyBroadcastManager

        class _RejectingRepo(_RecordingRoomRepo):
            def update_session_languages(self, room_id, *, input_lang, output_lang):
                self.calls.append({"room_id": room_id, "output_lang": output_lang})
                return False

        spy = SpyBroadcastManager()
        repo = _RejectingRepo(self._row("ko"))
        ws = _auth_ws_for_room("r1", input_lang="ko", output_lang="xx")

        assert self._run(mock_db, repo, ws, spy) is not None

        assert [c["output_lang"] for c in repo.calls] == ["xx"]
        assert spy.control_count == 0, spy.control_calls
        assert spy.get_control("r1") is None

    def test_a_raising_write_publishes_nothing_and_still_authenticates(
        self, mock_db, capsys
    ):
        """RL-006 — 기록이 터져도 auth 는 살아 있고 control 은 나가지 않는다."""
        from tests.broadcast_spy import SpyBroadcastManager

        spy = SpyBroadcastManager()
        repo = _RecordingRoomRepo(
            self._row("ko"), raises=RuntimeError("db on fire /secret/app.db")
        )
        ws = _auth_ws_for_room("r1", input_lang="ko", output_lang="vi")

        assert self._run(mock_db, repo, ws, spy) is not None

        assert spy.control_count == 0, spy.control_calls
        sent = _get_sent_messages(ws)
        assert any(m.get("type") == "auth_success" for m in sent)
        wire = json.dumps(sent, ensure_ascii=False)
        assert "db on fire" not in wire
        assert "/secret/app.db" not in wire
        assert "db on fire" in capsys.readouterr().out

    def test_memory_only_mode_publishes_nothing(self, mock_db):
        """repo 미주입이면 기록할 행이 없으므로 발행도 없다."""
        from tests.broadcast_spy import SpyBroadcastManager

        spy = SpyBroadcastManager()
        ws = _make_websocket(
            {
                "type": "auth",
                "user": {"id": 1, "username": "testuser", "role": "user"},
                "language_settings": {"input_lang": "ko", "output_lang": "vi"},
            }
        )

        assert self._run(mock_db, None, ws, spy) is not None

        assert spy.control_count == 0, spy.control_calls

    def test_auth_success_carries_the_current_control_snapshot(self, mock_db):
        """ISSUE-54 가 슬라이더를 서버 값으로 맞추기 위해 소비하는 스냅샷."""
        from tests.broadcast_spy import SpyBroadcastManager

        spy = SpyBroadcastManager()
        spy.set_control("r1", caption_scale=1.4)
        repo = _RecordingRoomRepo(self._row("vi"))
        ws = _auth_ws_for_room("r1", input_lang="auto", output_lang="vi")

        assert self._run(mock_db, repo, ws, spy) is not None

        sent = _get_sent_messages(ws)
        success = next(m for m in sent if m["type"] == "auth_success")
        assert success["control"] == {"caption_scale": 1.4}

    def test_auth_success_snapshot_includes_a_language_set_in_the_same_call(
        self, mock_db
    ):
        """스냅샷은 방금 기록한 언어까지 담는다 — 발행보다 뒤에 조립된다."""
        from tests.broadcast_spy import SpyBroadcastManager

        spy = SpyBroadcastManager()
        repo = _RecordingRoomRepo(self._row("ko"))
        ws = _auth_ws_for_room("r1", input_lang="ko", output_lang="vi")

        assert self._run(mock_db, repo, ws, spy) is not None

        success = next(m for m in _get_sent_messages(ws) if m["type"] == "auth_success")
        assert success["control"] == {"primary_lang": "vi"}

    def test_auth_success_snapshot_is_null_when_the_room_has_no_state(self, mock_db):
        """상태가 없으면 기본값을 지어내지 않는다 — null 이다."""
        from tests.broadcast_spy import SpyBroadcastManager

        spy = SpyBroadcastManager()
        repo = _RecordingRoomRepo(self._row("vi"))
        ws = _auth_ws_for_room("r1", input_lang="auto", output_lang="vi")

        assert self._run(mock_db, repo, ws, spy) is not None

        success = next(m for m in _get_sent_messages(ws) if m["type"] == "auth_success")
        assert success["control"] is None


# ---------------------------------------------------------------------------
# ISSUE-55: 인증은 서버가 발급한 서명 쿠키를 제시할 때만 성립한다
# ---------------------------------------------------------------------------
AUTH_FAILED = "인증에 실패했습니다."


class TestAuthRequiresSignedSessionCookie:
    """ISSUE-55 의 핵심 — 신원 주장만으로는 통과하지 못한다.

    ISSUE-55 이전에는 ① id 가 ``users`` 에 존재 ② ``is_active`` ③ ``username``
    일치 세 가지만 봤다. 비밀번호도 토큰도 쿠키도 Origin 도 검사하지 않았으므로
    **사용자명 하나만 알면 프레임 하나로 인증**됐고, 서버는 ``0.0.0.0:8765`` 에
    바인딩되며 배포 환경에서 ALB 가 ``/ws`` 를 공개한다.
    """

    @staticmethod
    def _frame(user_id=1, username="testuser", role="user"):
        return {
            "type": "auth",
            "user": {"id": user_id, "username": username, "role": role},
        }

    def test_a_valid_identity_without_a_cookie_is_rejected(self, mock_db):
        """유효한 username/user_id 를 알아도 쿠키가 없으면 거절된다.

        이것이 ISSUE-55 가 닫는 구멍이다. 응답은 기존 문구와 **문자열 상등**이어서
        "왜" 실패했는지 구분되지 않는다 (RL-006).
        """
        from websocket_handler import _authenticate_client

        ws = make_websocket(self._frame(), cookie=None)

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        sent = sent_messages(ws)
        assert [m for m in sent if m["type"] == "auth_success"] == []
        errors = [m for m in sent if m["type"] == "auth_error"]
        assert len(errors) == 1, f"auth_error 가 정확히 1건이어야 한다: {sent}"
        assert errors[0]["message"] == AUTH_FAILED

    def test_an_unauthenticated_socket_never_touches_the_user_table(self, mock_db):
        """쿠키 검증이 DB 조회보다 먼저다 — 미인증 소켓은 users 를 때리지 않는다."""
        from websocket_handler import _authenticate_client

        ws = make_websocket(self._frame(), cookie=None)
        probe = MagicMock(wraps=mock_db)

        with patch("websocket_handler.get_user_model", return_value=probe):
            assert asyncio.run(_authenticate_client(ws)) is None

        assert probe.get_user_by_id.call_args_list == [], (
            "미인증 소켓이 users 테이블을 조회했다 — 쿠키 게이트가 DB 조회 "
            "뒤로 밀렸다는 뜻이다"
        )

    @pytest.mark.parametrize(
        "label,cookie",
        [
            # HMAC 변조 — payload 는 그대로, 서명 바이트만 뒤집는다
            ("tampered_hmac", signed_cookie_value(1, "testuser")[:-4] + "dead"),
            # 다른 SESSION_SECRET 으로 서명 — 형식은 완벽하다
            (
                "foreign_secret",
                cookie_value_signed_with("an-entirely-different-secret", 1, "testuser"),
            ),
            # 서명 없는 구형 포맷 (ISSUE-10 이전)
            ("unsigned_legacy", "1:testuser:sometoken"),
            # payload 변조 — 남의 id 로 바꿔치기하면 서명이 깨진다
            (
                "tampered_payload",
                signed_cookie_value(1, "testuser").replace("1:", "2:", 1),
            ),
        ],
    )
    def test_a_cookie_that_is_not_validly_signed_is_rejected(
        self, mock_db, label, cookie
    ):
        """변조·외부 서명·무서명 쿠키는 전부 거절되고 사유는 로그에만 남는다.

        RED 확인: 프로덕션에서 ``_session_user_id_from_handshake`` 호출을 지우면
        네 케이스가 **전부 통과로 뒤집힌다** (RL-004).
        """
        from websocket_handler import _authenticate_client

        ws = make_websocket(self._frame(), cookie=cookie)

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None, f"{label} 쿠키가 통과했다"
        sent = sent_messages(ws)
        assert [m for m in sent if m["type"] == "auth_success"] == []
        assert [m["message"] for m in sent if m["type"] == "auth_error"] == [
            AUTH_FAILED
        ], f"{label}: 응답이 일반 문구와 다르다 — 열거 오라클이 된다"

    def test_the_default_harness_cookie_is_actually_accepted(self, mock_db):
        """양성 대조 — 위 부정 테스트들이 '무엇이든 거절' 때문에 통과하는 게 아니다.

        이 단언이 없으면 서명 형식이 어긋나도 부정 테스트 4건이 전부 초록으로
        남는다. 하네스가 만드는 쿠키가 **실제로 통과하는** 쿠키여야 한다.
        """
        from websocket_handler import _authenticate_client

        ws = make_websocket(self._frame())

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None, (
            "기본 하네스 쿠키가 거절됐다 — ws_harness 의 서명 식이 "
            "auth._sign_cookie 와 갈라졌다"
        )

    def test_a_cookie_for_another_user_is_rejected_with_no_persistent_effect(
        self, mock_db
    ):
        """쿠키 user_id ≠ 프레임 user.id → 거절, 그리고 rooms 쓰기가 0건이다.

        ISSUE-52 가 이 채널에 영속 능력을 붙였으므로, "인증 전에는 어떤 영속
        효과도 없다" 를 **값으로** 단언한다. 게이트만 보고 그 아래 효과를 안
        보면 RL-004 다.
        """
        from room_manager import RoomManager
        from websocket_handler import _authenticate_client

        repo = _RecordingRoomRepo({"id": "r1", "status": "active"})
        ws = make_websocket(
            {
                "type": "auth",
                "user": {"id": 1, "username": "testuser", "role": "user"},
                "room_id": "r1",
                "language_settings": {"input_lang": "ko", "output_lang": "vi"},
            },
            # 쿠키는 user 7 의 것이다 — 프레임은 1 을 주장한다
            cookie=signed_cookie_value(7, "someoneelse"),
        )

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", RoomManager(room_repository=repo)),
        ):
            result = asyncio.run(_authenticate_client(ws))

        assert result is None
        assert repo.calls == [], (
            f"인증 실패가 rooms 행에 썼다: {repo.calls} — ISSUE-52 의 영속 "
            "경로가 인증 게이트 앞에 있다는 뜻이다"
        )

    def test_a_valid_cookie_still_takes_the_role_from_the_db(self, mock_db):
        """유효 쿠키 + role='admin' 주장 → 반환 role 은 DB 값이다 (ISSUE-5 유지).

        쿠키 게이트를 추가하면서 기존 RL-002 성질이 사라지지 않았음을 고정한다.
        """
        from websocket_handler import _authenticate_client

        ws = make_websocket(self._frame(role="admin"))

        with patch("websocket_handler.get_user_model", return_value=mock_db):
            result = asyncio.run(_authenticate_client(ws))

        assert result is not None
        assert (
            result["role"] == "user"
        ), "클라이언트가 주장한 admin 이 통과했다 — ISSUE-5 의 성질이 깨졌다"


class _ScriptedWebSocket:
    """auth 프레임 하나 + 그 뒤 control 류 프레임들을 내주는 fake (ISSUE-55).

    `_authenticate_client` 는 `recv()` 로 첫 프레임을 읽고, 메시지 루프는
    `async for message in websocket` 로 그 뒤를 읽는다. **둘 다** 흉내 내야
    "인증 실패 시 루프에 도달조차 못 한다" 를 끝까지 확인할 수 있다.

    `AsyncMock` 으로는 `__aiter__` 를 깔끔하게 줄 수 없어서 전용 클래스를 쓴다.
    """

    def __init__(self, auth_frame, follow_up_frames, *, cookie):
        self.remote_address = ("127.0.0.1", 9)
        self.sent: list[dict] = []
        self._auth = json.dumps(auth_frame)
        self._follow_ups = [json.dumps(f) for f in follow_up_frames]
        headers = {} if cookie is None else {"Cookie": f"user_session={cookie}"}
        self.request = SimpleNamespace(headers=headers)

    async def recv(self):
        return self._auth

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    def __aiter__(self):
        async def _gen():
            for frame in self._follow_ups:
                yield frame

        return _gen()

    async def close(self):
        return None

    def types(self) -> list[str]:
        return [m.get("type") for m in self.sent]


class TestUnauthenticatedClientsCannotReachIssue53Capabilities:
    """ISSUE-53 이 넓힌 **실시간** 능력이 인증 뒤로 들어갔는지 (AC 마지막 항목).

    ISSUE-53 이전에 미인증 공격자의 능력은 "다음에 여는 페이지" 까지였다. 이후로는
    `language_update` 한 건이 **이미 프로젝터에 떠 있는** 무대 자막 컬럼을 즉시
    재구독시켜 비운다. 무대에는 조작 표면이 0개이고(NFR-025) 새로고침도 접속 시점
    스냅샷이 적대적 상태를 다시 밀어 주므로 현장에서 되돌릴 수단이 없다.

    **이 클래스는 `_authenticate_client` 가 아니라 `handle_openai_websocket` 을
    돌린다.** 앞선 설계에서는 control 류 프레임을 `_authenticate_client` 에 직접
    넣었는데, 그 함수는 `type != "auth"` 를 어차피 무시하므로 게이트를 제거해도
    테스트가 통과했다 — 인증과 무관하게 초록인 **무의미한 단언**이었다 (RL-004).
    실제 경로는 "인증 성공 → 메시지 루프" 이므로 루프 도달 여부를 세야 한다.
    """

    ROOM_ROW = {
        "id": "r1",
        "status": "active",
        "input_lang": "auto",
        "output_lang": "ko",
        "primary_output_lang": "ko",
    }

    FOLLOW_UPS = [
        {"type": "stage_control", "caption_scale": 1.2},
        {"type": "language_update", "output_lang": "vi"},
    ]

    @staticmethod
    def _auth_frame():
        return {
            "type": "auth",
            "user": {"id": 1, "username": "testuser", "role": "user"},
            "room_id": "r1",
            "language_settings": {"input_lang": "auto", "output_lang": "ko"},
        }

    def _run(self, mock_db, repo, ws, spy):
        from room_manager import RoomManager
        from websocket_handler import handle_openai_websocket

        with (
            patch("websocket_handler.get_user_model", return_value=mock_db),
            patch("websocket_handler._room_manager", RoomManager(room_repository=repo)),
            patch("websocket_handler._broadcast_manager", spy),
            patch(
                "websocket_handler._init_translation_clients",
                return_value=(MagicMock(), MagicMock(), False),
            ),
            patch("websocket_handler.check_usage_limit", return_value=(True, 0)),
        ):
            asyncio.run(handle_openai_websocket(ws))

    def test_without_a_cookie_the_message_loop_is_never_reached(self, mock_db):
        """쿠키 없이는 control 발행 0회, 룸 상태 불변, rooms 쓰기 0건이다."""
        from tests.broadcast_spy import SpyBroadcastManager

        spy = SpyBroadcastManager()
        repo = _RecordingRoomRepo(dict(self.ROOM_ROW))
        ws = _ScriptedWebSocket(self._auth_frame(), self.FOLLOW_UPS, cookie=None)

        self._run(mock_db, repo, ws, spy)

        assert (
            "connection" not in ws.types()
        ), f"미인증 소켓이 메시지 루프에 진입했다: {ws.types()}"
        assert (
            spy.control_count == 0
        ), f"미인증 프레임이 control 을 발행했다: {spy.control_calls}"
        assert spy.get_control("r1") is None, "미인증 프레임이 룸 control 상태를 바꿨다"
        assert repo.calls == [], f"미인증 프레임이 rooms 행에 썼다: {repo.calls}"

    def test_with_a_valid_cookie_the_same_frames_do_take_effect(self, mock_db):
        """양성 대조 — 같은 프레임이 인증된 소켓에서는 실제로 효과를 낸다.

        이 단언이 없으면 위의 "0회" 는 "스크립트가 애초에 아무것도 안 했다" 와
        구별되지 않는다. 무의미한 단언을 한 번 만들었으므로 짝을 반드시 둔다.
        """
        from tests.broadcast_spy import SpyBroadcastManager

        spy = SpyBroadcastManager()
        repo = _RecordingRoomRepo(dict(self.ROOM_ROW))
        ws = _ScriptedWebSocket(
            self._auth_frame(),
            self.FOLLOW_UPS,
            cookie=signed_cookie_value(1, "testuser"),
        )

        self._run(mock_db, repo, ws, spy)

        assert (
            "connection" in ws.types()
        ), f"유효 쿠키인데 메시지 루프에 진입하지 못했다: {ws.types()}"
        assert spy.control_count > 0, (
            "인증된 소켓에서도 control 이 0회다 — 스크립트가 루프에 닿지 않았다는 "
            "뜻이고, 그러면 음성 테스트의 '0회' 는 아무것도 증명하지 않는다"
        )


class TestTheRealHandshakeHeaderPath:
    """실제 `websockets` 서버/클라이언트로 헤더 경로가 연결됐는지 확인한다.

    위의 단위 테스트는 전부 fake 소켓을 쓴다. fake 가 `request.headers` 의 모양을
    **틀리게** 흉내 내면 단위 테스트는 전부 초록인데 프로덕션에서는 아무도
    인증하지 못한다 — 그 실패 양상이 가장 비싸다(행사 당일 자막 사망). 이 클래스가
    그 그물이다.

    `websockets` 15.0.1 의 실제 `Headers` 는 **대소문자 구분이 없고**, 핸들러 인자는
    `ServerConnection` 이라 핸드셰이크 헤더가 `connection.request.headers` 에 있다
    (legacy `WebSocketServerProtocol.request_headers` 가 아니다). 그 두 사실을
    여기서 값으로 확인한다.

    외부 네트워크를 쓰지 않는다 — `127.0.0.1` 에 OS 할당 포트로 바인딩한다.
    """

    @staticmethod
    async def _probe(cookie_header):
        """서버가 핸드셰이크에서 유도한 user_id (없으면 None)."""
        import websockets
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve

        from websocket_handler import _session_user_id_from_handshake

        captured = {}

        async def handler(connection):
            captured["user_id"] = _session_user_id_from_handshake(connection)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            headers = {"Cookie": cookie_header} if cookie_header else None
            async with connect(
                f"ws://127.0.0.1:{port}", additional_headers=headers
            ) as client:
                with contextlib.suppress(websockets.ConnectionClosed):
                    await client.recv()

        return captured.get("user_id", "<handler never ran>")

    def test_a_signed_cookie_on_a_real_handshake_is_read(self):
        """실제 핸드셰이크에 실린 서명 쿠키에서 user_id 를 뽑아낸다."""
        cookie = signed_cookie_value(42, "realuser")
        result = asyncio.run(self._probe(f"user_session={cookie}"))
        assert result == "42", (
            f"실제 헤더 경로에서 user_id 를 못 읽었다: {result!r}. "
            "`connection.request.headers` 접근이 틀렸을 가능성이 가장 크다."
        )

    def test_a_real_handshake_without_a_cookie_yields_nothing(self):
        """쿠키 없는 실제 핸드셰이크는 None 이다 — 즉 거절로 이어진다."""
        assert asyncio.run(self._probe(None)) is None

    def test_a_tampered_cookie_on_a_real_handshake_is_rejected(self):
        """실제 핸드셰이크여도 서명이 깨지면 None 이다."""
        cookie = signed_cookie_value(42, "realuser")[:-4] + "dead"
        assert asyncio.run(self._probe(f"user_session={cookie}")) is None

    def test_the_cookie_header_name_is_matched_case_insensitively(self):
        """소문자 `cookie:` 로 보내도 읽힌다 — 실제 `Headers` 의 성질이다.

        브라우저와 프록시가 헤더 이름 대소문자를 보장하지 않으므로, 프로덕션
        코드가 `.get("Cookie")` 한 가지 표기만 보고도 동작해야 한다. fake dict
        하네스로는 이 성질을 **절대** 확인할 수 없다.
        """
        cookie = signed_cookie_value(7, "realuser")

        async def _probe_lowercase():
            import websockets
            from websockets.asyncio.client import connect
            from websockets.asyncio.server import serve

            from websocket_handler import _session_user_id_from_handshake

            captured = {}

            async def handler(connection):
                captured["user_id"] = _session_user_id_from_handshake(connection)

            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(
                    f"ws://127.0.0.1:{port}",
                    additional_headers=[("cookie", f"user_session={cookie}")],
                ) as client:
                    with contextlib.suppress(websockets.ConnectionClosed):
                        await client.recv()
            return captured.get("user_id")

        assert asyncio.run(_probe_lowercase()) == "7"

    def test_the_cookie_is_found_among_other_cookies(self):
        """다른 쿠키가 섞여 있어도 `user_session` 을 찾는다.

        실제 브라우저는 Streamlit 쿠키 등 여러 개를 함께 싣는다. 손으로 split
        하면 틀리는 지점이고, `user_session` 값 자체에 `:` 가 들어 있다.
        """
        cookie = signed_cookie_value(99, "realuser")
        header = f"_streamlit_xsrf=abc; user_session={cookie}; other=1"
        assert asyncio.run(self._probe(header)) == "99"


class TestTheRealBrowserCookieEncoding:
    """브라우저가 저장하는 **percent-encoded** 형태를 받아들이는지 (ISSUE-55).

    이 클래스는 실제 결함을 잡고 나서 추가됐다. 쿠키를 심는 쪽은
    `extra_streamlit_components.CookieManager` 의 JS 이고 `encodeURIComponent`
    를 거치므로, 브라우저 저장값은 구분자 `:` 가 `%3A` 로 바뀐 형태다. e2e 실측:

        user_session=1%3Ae2e_operator%3A<token>%3A<hmac>

    `auth._verify_cookie` 는 `rsplit(":", 1)` 로 서명을 떼므로 디코드하지 않으면
    리터럴 `:` 가 0개여서 **모든 실제 오퍼레이터가 거절된다.** 그런데 단위
    하네스는 서버측 `_sign_cookie` 출력(리터럴 `:`)을 그대로 쓰므로 **이 결함을
    통과시켰다.** fake 가 실제 경로의 모양을 틀리게 흉내 내면 테스트가 전부
    초록인데 프로덕션이 죽는다 — 그 양상을 여기서 고정한다.
    """

    @staticmethod
    def _probe(raw_cookie_value):
        from types import SimpleNamespace

        from websocket_handler import _session_user_id_from_handshake

        ws = SimpleNamespace(
            request=SimpleNamespace(
                headers={"Cookie": f"user_session={raw_cookie_value}"}
            )
        )
        return _session_user_id_from_handshake(ws)

    def test_a_percent_encoded_cookie_is_accepted(self):
        """브라우저 형태(`%3A` 구분자)가 통과한다."""
        plain = signed_cookie_value(11, "e2e_operator")
        encoded = plain.replace(":", "%3A")
        assert "%3A" in encoded and ":" not in encoded
        assert self._probe(encoded) == "11", (
            "percent-encoded 쿠키가 거절됐다 — 실제 브라우저 오퍼레이터가 전원 "
            "인증 불가가 된다. urllib.parse.unquote 가 빠졌는지 확인할 것."
        )

    def test_a_plain_cookie_is_still_accepted(self):
        """서버가 직접 만든 리터럴 `:` 형태도 계속 통과한다 (양방향 호환)."""
        assert self._probe(signed_cookie_value(12, "e2e_operator")) == "12"

    def test_decoding_does_not_rescue_a_tampered_signature(self):
        """디코드를 추가해도 서명 검증이 느슨해지지 않는다.

        `unquote` 를 넣으면서 "무엇이든 통과" 로 바뀌지 않았음을 확인한다 —
        관용성을 늘릴 때마다 가드가 약해지는지 함께 봐야 한다.
        """
        bad = (signed_cookie_value(13, "e2e_operator")[:-4] + "dead").replace(
            ":", "%3A"
        )
        assert self._probe(bad) is None

    def test_an_encoded_cookie_among_others_is_found(self):
        """실제 브라우저가 보내는 형태 그대로 — xsrf 쿠키와 함께 와도 찾는다."""
        from types import SimpleNamespace

        from websocket_handler import _session_user_id_from_handshake

        encoded = signed_cookie_value(14, "e2e_operator").replace(":", "%3A")
        header = (
            "_streamlit_xsrf=2|58da172d|e2fd0ec2c93496672c395adedbf3c7ed|1791730221; "
            f"user_session={encoded}"
        )
        ws = SimpleNamespace(request=SimpleNamespace(headers={"Cookie": header}))
        assert _session_user_id_from_handshake(ws) == "14"
