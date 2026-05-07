"""
뷰어 전용 HTML 페이지 + /view/{room_id} 라우트 테스트 (ISSUE-31).

검증 대상:
- viewer.html 정적 마크업: EventSource 호출 코드, 언어 셀렉터, 대기/활성/종료 상태 UI.
- aiohttp /view/{room_id} 핸들러:
    - 정상 룸 → 200 + text/html + 룸 이름/룸 id/언어 목록이 인라인 주입된다.
    - 알 수 없는 룸 → 404 + 친절한 HTML 본문 (RL-006: 내부 detail 노출 금지).
    - closed 룸 → 200 + 종료(ended) 상태가 인라인 마크업으로 active.
- 모바일/접근성 지표: viewport 메타, dvh 단위, lang="ko", aria-label 존재.

외부 네트워크 호출 없이 aiohttp TestClient 만 사용 (test_sse_broadcast.py 패턴).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# websocket_handler -> auth -> streamlit 의존성 회피 (다른 테스트와 동일 패턴)
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ---------------------------------------------------------------------------
# Stub repo (lightweight DB-free fake — same shape as test_sse_broadcast.py)
# ---------------------------------------------------------------------------
class _StubRoomRepo:
    """Minimal fake of database.Room for endpoint tests."""

    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)


# ---------------------------------------------------------------------------
# Static HTML markup tests — viewer.html
# ---------------------------------------------------------------------------
class TestViewerHtmlMarkup:
    """뷰어 페이지 마크업 정적 검증.

    AC ↔ Test mapping (issues.md ISSUE-31 § Tests):
      - "EventSource 연결 코드 존재" → test_event_source_call_present
      - "언어 선택 드롭다운 마크업" → test_language_selector_present
      - "대기/활성/종료 3개 상태" → test_three_state_ui_present
    """

    @pytest.fixture
    def viewer_html(self) -> str:
        # Resolve from project root regardless of pytest cwd.
        path = Path(__file__).resolve().parent.parent / "components" / "viewer.html"
        assert path.exists(), f"viewer.html missing: {path}"
        return path.read_text(encoding="utf-8")

    def test_event_source_call_present(self, viewer_html):
        """EventSource 인스턴스화 + /stream/ 경로 사용."""
        assert "EventSource(" in viewer_html, "EventSource API required"
        assert "/stream/" in viewer_html, "must connect to /stream/{room_id}"

    def test_language_selector_present(self, viewer_html):
        """드롭다운 마크업 (<select>) 과 식별자가 존재한다."""
        assert "<select" in viewer_html, "<select> for language switching"
        # 안정적인 hook 으로 id="lang-select" 노출
        assert 'id="lang-select"' in viewer_html

    def test_three_state_ui_present(self, viewer_html):
        """대기 / 활성 / 종료 세 상태 식별자 존재 (id 또는 data-state)."""
        # state container hooks — implementation uses id="state-waiting" etc.
        assert 'id="state-waiting"' in viewer_html
        assert 'id="state-active"' in viewer_html
        assert 'id="state-ended"' in viewer_html

    def test_korean_state_labels_present(self, viewer_html):
        """한국어 상태 안내 카피 존재 (UX 기획 라벨)."""
        # 정확한 카피는 ux_spec.md 가 없어 issues.md 의 상태 표 라벨을 정본으로 본다.
        assert "잠시 후 자막이 시작됩니다" in viewer_html
        assert "세션이 종료되었습니다" in viewer_html

    def test_viewport_meta_for_mobile(self, viewer_html):
        """모바일 반응형: viewport 메타 태그 존재."""
        assert 'name="viewport"' in viewer_html
        assert "width=device-width" in viewer_html

    def test_dvh_fallback_for_ios_safari(self, viewer_html):
        """RL-011: 100vh 사용 시 100dvh fallback 동반."""
        # dvh 등장 + cascading 으로 vh 보다 뒤에 위치해야 우선 적용된다.
        assert "100dvh" in viewer_html, "RL-011: 100dvh required (iOS Safari)"

    def test_lang_attribute_korean(self, viewer_html):
        """접근성: <html lang='ko'> (RL-010)."""
        # 정확한 인용 부호는 single 또는 double 모두 허용
        assert 'lang="ko"' in viewer_html or "lang='ko'" in viewer_html

    def test_aria_label_on_language_selector(self, viewer_html):
        """접근성: 언어 셀렉터에 aria-label (RL-010)."""
        # aria-label 직접 부여, 또는 연결된 label[for=lang-select] 둘 중 하나.
        assert "aria-label" in viewer_html or 'for="lang-select"' in viewer_html


# ---------------------------------------------------------------------------
# /view/{room_id} HTTP handler — happy paths and error paths
# ---------------------------------------------------------------------------
class TestViewRouteHandler:
    """aiohttp `/view/{room_id}` 핸들러 동작 검증.

    AC ↔ Test mapping:
      - "/view/{room_id} 접속 시 자막 페이지 표시" → test_known_room_returns_html
      - "지원 언어 목록이 드롭다운으로 표시된다" → test_known_room_inlines_output_langs
      - "정상 룸 status='waiting/active' → 페이지 표시" → test_known_room_returns_html
      - "404 시 친절한 에러 페이지" → test_unknown_room_returns_404_friendly
      - "closed 룸 → 종료 화면" → test_closed_room_renders_ended_state
    """

    @pytest.mark.asyncio
    async def test_known_room_returns_html(self):
        """정상 룸 (active) → 200 + Content-Type text/html + 룸 이름/id 주입."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo(
            {
                "room-abc": {
                    "id": "room-abc",
                    "name": "Conference Hall A",
                    "status": "active",
                    "primary_output_lang": "ko",
                    "output_langs": '["ko","en"]',
                }
            }
        )
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/view/room-abc")
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/html")
            body = await resp.text()
            # 룸 이름/id 가 페이지 내에 인라인 주입되어야 한다 (템플릿 변수 치환)
            assert "Conference Hall A" in body
            assert "room-abc" in body

    @pytest.mark.asyncio
    async def test_known_room_inlines_output_langs(self):
        """룸의 output_langs JSON 배열이 페이지에 주입된다."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo(
            {
                "r1": {
                    "id": "r1",
                    "name": "R1",
                    "status": "active",
                    "primary_output_lang": "en",
                    "output_langs": '["ko","en","ja"]',
                }
            }
        )
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/view/r1")
            assert resp.status == 200
            body = await resp.text()
            # 모든 언어가 응답 본문에 등장해야 한다 (드롭다운 옵션 + 초기 데이터).
            for lang in ("ko", "en", "ja"):
                assert lang in body, f"language {lang!r} missing from viewer page"
            # primary_output_lang 도 노출되어 초기 SSE 연결 lang param 으로 사용된다.
            assert '"primary_lang"' in body or "primary_lang" in body

    @pytest.mark.asyncio
    async def test_known_room_waiting_status_renders_waiting_state(self):
        """status='waiting' 룸도 페이지가 표시된다 (시작 전 대기 화면)."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo(
            {
                "r2": {
                    "id": "r2",
                    "name": "Pre-Show",
                    "status": "waiting",
                    "primary_output_lang": "ko",
                    "output_langs": '["ko"]',
                }
            }
        )
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/view/r2")
            assert resp.status == 200
            body = await resp.text()
            assert "Pre-Show" in body
            # 초기 상태가 waiting 임을 클라이언트가 알 수 있어야 한다.
            assert "waiting" in body

    @pytest.mark.asyncio
    async def test_unknown_room_returns_404_friendly(self):
        """알 수 없는 room_id → 404 + 친절한 HTML (RL-006: 내부 detail 비노출)."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo({})
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/view/no-such-room")
            assert resp.status == 404
            body = await resp.text()
            # 친절한 한국어 안내가 노출되어야 한다 (단, 내부 디테일은 비노출).
            assert "Traceback" not in body
            assert "sqlite" not in body.lower()
            assert "exception" not in body.lower()
            # 사용자 친화 메시지: '찾을 수 없' / '존재하지 않' / 'not found'.
            assert (
                "찾을 수 없" in body
                or "존재하지 않" in body
                or "not found" in body.lower()
            )

    @pytest.mark.asyncio
    async def test_closed_room_renders_ended_state(self):
        """closed 룸 → 200 + ended 상태가 인라인으로 활성화된다."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        repo = _StubRoomRepo(
            {
                "rz": {
                    "id": "rz",
                    "name": "Closed Room",
                    "status": "closed",
                    "primary_output_lang": "ko",
                    "output_langs": '["ko"]',
                }
            }
        )
        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=repo)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/view/rz")
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/html")
            body = await resp.text()
            # 종료 카피가 즉시 노출되어야 한다 (DB 조회 결과 인라인 주입).
            assert "세션이 종료되었습니다" in body
            # 초기 상태가 'closed' 또는 'ended' 임을 클라이언트가 알 수 있다.
            assert "closed" in body or '"ended"' in body

    @pytest.mark.asyncio
    async def test_repo_exception_returns_generic_404(self):
        """레포 예외 → 본문에 내부 텍스트 비노출 (RL-006)."""
        from aiohttp.test_utils import TestClient, TestServer

        from sse_broadcast import BroadcastManager, build_sse_app

        class ExplodingRepo:
            def get_by_id(self, room_id):
                raise RuntimeError("DB internal: /private/data/secrets.db locked")

        mgr = BroadcastManager()
        app = build_sse_app(broadcast_manager=mgr, room_repo=ExplodingRepo())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/view/anything")
            assert resp.status in (404, 500)
            body = await resp.text()
            assert "secrets.db" not in body
            assert "RuntimeError" not in body
            assert "Traceback" not in body
