"""
뷰어 전용 HTML 페이지 + /view/{room_id} 라우트 테스트 (ISSUE-31).

검증 대상:
- viewer.html 정적 마크업: EventSource 호출 코드, 언어 셀렉터, 대기/활성/종료 상태 UI.
- aiohttp /view/{room_id} 핸들러:
    - 정상 룸 → 200 + text/html + 룸 이름/룸 id/언어 목록이 인라인 주입된다.
    - 알 수 없는 룸 → 404 + 친절한 HTML 본문 (RL-006: 내부 detail 노출 금지).
    - closed 룸 → 200 + 종료(ended) 상태가 인라인 마크업으로 active.
- 모바일/접근성 지표: viewport 메타, dvh 단위, lang="ko", aria-label 존재.
- 스크립트 컨텍스트 이스케이프 파리티 (ISSUE-44): `<script>` 로 들어가는 값은
  전부 `_json_for_script` 리터럴이고, 치환은 단일 패스라 어떤 값도 다른
  플레이스홀더를 팽창시키지 못한다 (RL-020 / RL-021).
- 자막 파이프라인 정적 계약 (ISSUE-46): stage.html 과 동일한 확정/빈 final/
  잔상/DOM sink 규칙.

외부 네트워크 호출 없이 aiohttp TestClient 만 사용 (test_sse_broadcast.py 패턴).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest

# websocket_handler -> auth -> streamlit 의존성 회피 (다른 테스트와 동일 패턴)
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()

_VIEWER_TEMPLATE = Path(__file__).resolve().parent.parent / "components" / "viewer.html"

# 자막 파이프라인 함수 본문 추출 패턴 (ISSUE-46). 스크립트 블록 안의 최상위
# 함수는 4칸 들여쓰기로 닫힌다.
_FINALIZE_BODY = r"function finalizeCaption\(text\) \{(.*?)\n    \}"
_LOCK_LINE_BODY = r"function _lockLine\(\) \{(.*?)\n    \}"
_TW_START_BODY = r"function _twStart\(\) \{(.*?)\n    \}"


@pytest.fixture
def viewer_html() -> str:
    """viewer.html 원문 (pytest cwd 와 무관하게 프로젝트 루트 기준으로 해석)."""
    assert _VIEWER_TEMPLATE.exists(), f"viewer.html missing: {_VIEWER_TEMPLATE}"
    return _VIEWER_TEMPLATE.read_text(encoding="utf-8")


def _js_body(source: str, pattern: str) -> str:
    """`pattern` 이 잡은 함수 본문에서 주석 줄을 걷어낸 코드만 돌려준다.

    주석에 등장하는 식별자가 순서 단언을 오염시키지 않게 한다 — 이식 지점마다
    "stage.html:NNN 과 동일" 주석을 남기는 것이 이 이슈의 요구사항이므로,
    주석은 반드시 코드와 분리해서 봐야 한다.
    """
    match = re.search(pattern, source, re.S)
    assert match is not None, f"no match for {pattern!r} in viewer.html"
    lines = match.group(1).splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("//"))


# 인라인 `<script>` 블록을 조기 종료시키려는 페이로드 (RL-016).
_BREAKOUT_NAME = "</script><script>alert(1)</script>"


# ---------------------------------------------------------------------------
# Stub repo (lightweight DB-free fake — same shape as test_sse_broadcast.py)
# ---------------------------------------------------------------------------
class _StubRoomRepo:
    """Minimal fake of database.Room for endpoint tests."""

    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)


def _room(**overrides: Any) -> dict[str, Any]:
    """Room row shaped like `database.Room.get_by_id` returns it."""
    row = {
        "id": "room-1",
        "name": "Conference Hall A",
        "status": "active",
        "primary_output_lang": "ko",
        "output_langs": '["ko","en"]',
    }
    row.update(overrides)
    return row


async def _get(repo: Any, path: str) -> tuple[int, str, str]:
    """GET `path` against a fresh app; return (status, body, content_type)."""
    from aiohttp.test_utils import TestClient, TestServer

    from sse_broadcast import BroadcastManager, build_sse_app

    app = build_sse_app(broadcast_manager=BroadcastManager(), room_repo=repo)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(path)
        return resp.status, await resp.text(), resp.headers.get("Content-Type", "")


def _script_block(body: str) -> str:
    """부트스트랩 인라인 `<script>` 블록만 잘라낸다 (마크업 자리와 분리)."""
    start = body.index("<script>")
    end = body.index("</script>", start)
    return body[start:end]


def _config_literal(body: str, key: str) -> str:
    """부트스트랩 `CONFIG` 에서 `key:` 의 값 리터럴을 원문 그대로 잘라낸다.

    줄 끝 주석(`initial_state` 줄)을 허용하되 값 자체의 쉼표는 삼키지 않도록
    greedy 매칭 후 마지막 쉼표에서 자른다.
    """
    pattern = rf"^\s*{key}: (.*),\s*(?://.*)?$"
    match = re.search(pattern, _script_block(body), re.M)
    assert match is not None, f"{key} literal missing"
    return match.group(1)


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
        # #112: 대기 문구에서 "자막" 어절 제거 + 언어별 표시.
        assert "잠시 후 시작됩니다" in viewer_html
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


# ---------------------------------------------------------------------------
# ISSUE-44 — script-context escaping parity with _render_stage_html
# ---------------------------------------------------------------------------
class TestViewerScriptContextEscaping:
    """`<script>` 로 들어가는 값의 이스케이프 파리티 (RL-020 / RL-021).

    무대 페이지는 ISSUE-40 에서 이미 고쳐졌다. 뷰어는 출하된 공개 경로인데
    같은 결함을 그대로 서빙 중이었다. 아래 단언은 전부
    `tests/test_stage_page.py::TestStageRouteHandler` 의 미러다.

    AC ↔ Test mapping (issues.md ISSUE-44 § Acceptance Criteria):
      - AC 1 → test_script_scalars_survive_backslashes_and_quotes
      - AC 3 → test_room_name_cannot_expand_another_placeholder
      - AC 4 → test_room_name_ampersand_is_not_double_escaped
      - AC 5 → test_room_name_script_tag_stays_escaped_in_markup
               / test_room_name_cannot_close_script_block
      (AC 2 는 tests/e2e/test_viewer_page_e2e.py, AC 6 은 아래 구조 테스트)
    """

    async def test_script_scalars_survive_backslashes_and_quotes(self):
        """AC 1 — 따옴표 + 끝 백슬래시 room_id 가 손상 없이 살아남는다.

        `html.escape` 는 `\\` 를 건드리지 않아 백슬래시로 끝나는 값이 닫는
        따옴표를 탈출시킨다 — 부트스트랩 전체가 SyntaxError 로 죽는다.
        `"` 는 `&quot;` 로 바뀌어 값 자체가 조용히 손상되고, 그 값이
        `/stream/${encodeURIComponent(CONFIG.room_id)}` URL 조립에 쓰인다.
        """
        room_id = 'q"b\\'
        repo = _StubRoomRepo({room_id: _room(id=room_id)})
        status, body, _ = await _get(repo, "/view/q%22b%5C")
        assert status == 200
        # 스크립트 컨텍스트에 HTML 엔티티가 있으면 안 된다 (raw text 라 디코드 안 됨).
        assert "&quot;" not in _script_block(body)
        assert json.loads(_config_literal(body, "room_id")) == room_id

    @pytest.mark.parametrize(
        "hostile_name",
        [
            "{{OUTPUT_LANGS_JSON}}",
            "{{ROOM_NAME_JSON}}",
            "{{PRIMARY_LANG}}",
            "{{INITIAL_STATE}}",
        ],
    )
    async def test_room_name_cannot_expand_another_placeholder(self, hostile_name):
        """AC 3 (RL-021) — 룸 이름이 다른 플레이스홀더로 팽창하지 않는다.

        연쇄 `str.replace` 는 앞 단계가 써 넣은 값을 뒤 단계가 다시 본다.
        룸 이름은 운영자 자유 입력이므로 치환은 단일 패스여야 한다.
        """
        repo = _StubRoomRepo({"room-1": _room(name=hostile_name)})
        status, body, _ = await _get(repo, "/view/room-1")
        assert status == 200
        assert f"<title>{hostile_name} — 자막 뷰어</title>" in body
        assert f'id="room-name">{hostile_name}</div>' in body
        # 스크립트 자리에도 리터럴 그대로 (팽창하면 ["ko", …] 가 들어온다).
        assert json.loads(_config_literal(body, "room_name")) == hostile_name

    async def test_room_name_ampersand_is_not_double_escaped(self):
        """AC 4 — `A홀 & B홀` 이 대기 화면에 `A홀 &amp; B홀` 로 보이지 않는다.

        스크립트 리터럴을 HTML 이스케이프하면 `textContent` 가 엔티티를
        디코드하지 않아 그대로 노출된다 (뷰어의 이중 이스케이프).
        """
        name = "A홀 & B홀"
        repo = _StubRoomRepo({"room-1": _room(name=name)})
        status, body, _ = await _get(repo, "/view/room-1")
        assert status == 200
        assert json.loads(_config_literal(body, "room_name")) == name
        # 마크업 자리는 반대로 여전히 HTML 이스케이프되어야 한다 (sink 별 이스케이퍼).
        assert "<title>A홀 &amp; B홀 — 자막 뷰어</title>" in body

    async def test_room_name_script_tag_stays_escaped_in_markup(self):
        """AC 5 — 마크업 경로 회귀 가드: `&lt;script&gt;` 가 유지된다."""
        repo = _StubRoomRepo({"room-1": _room(name="<script>alert(1)</script>")})
        status, body, _ = await _get(repo, "/view/room-1")
        assert status == 200
        assert "&lt;script&gt;" in body
        assert "<script>alert(1)</script>" not in body
        # 스크립트 자리는 \uXXXX 로 이스케이프되고 값은 손실 없이 복원된다.
        literal = _config_literal(body, "room_name")
        assert "\\u003cscript\\u003e" in literal
        assert json.loads(literal) == "<script>alert(1)</script>"

    async def test_room_name_cannot_close_script_block(self):
        """AC 5 (RL-016) — `</script>` 페이로드가 인라인 블록을 조기 종료 못 한다.

        `_json_for_script` 대신 맨 `json.dumps` 를 쓰면 이 테스트가 실패한다
        (`</script>` 가 그대로 나가 블록이 하나 더 닫힌다).
        """
        repo = _StubRoomRepo({"room-1": _room(name=_BREAKOUT_NAME)})
        status, body, _ = await _get(repo, "/view/room-1")
        assert status == 200
        assert "</script><script>" not in body
        assert _BREAKOUT_NAME not in body
        template = _VIEWER_TEMPLATE.read_text(encoding="utf-8")
        assert body.count("</script>") == template.count("</script>")
        # 이스케이프된 형태로는 살아 있다 (데이터 손실 없음).
        assert json.loads(_config_literal(body, "room_name")) == _BREAKOUT_NAME

    @pytest.mark.parametrize("key", ["primary_lang", "initial_state"])
    async def test_hostile_lang_and_state_scalars_round_trip(self, key):
        """`primary_lang` / `initial_state` 도 같은 보증을 받는다.

        두 값 모두 `html.escape` 되고 있었다 — room_id 와 완전히 같은 결함이다.
        DB 가 오염되면 이 스칼라만으로도 부트스트랩이 죽는다.
        """
        hostile = 'q"b\\'
        overrides = {
            "primary_lang": {"primary_output_lang": hostile},
            "initial_state": {"status": hostile},
        }[key]
        repo = _StubRoomRepo({"room-1": _room(**overrides)})
        status, body, _ = await _get(repo, "/view/room-1")
        assert status == 200
        assert json.loads(_config_literal(body, key)) == hostile

    async def test_benign_room_still_bootstraps_expected_values(self):
        """대조군 — 정상 룸의 값이 그대로 유지된다 (회귀 방지)."""
        repo = _StubRoomRepo({"room-1": _room(status="waiting")})
        status, body, _ = await _get(repo, "/view/room-1")
        assert status == 200
        assert json.loads(_config_literal(body, "room_id")) == "room-1"
        assert json.loads(_config_literal(body, "room_name")) == "Conference Hall A"
        assert json.loads(_config_literal(body, "primary_lang")) == "ko"
        assert json.loads(_config_literal(body, "initial_state")) == "waiting"


class TestViewerRenderStructure:
    """AC 6 — 치환 구조 자체를 단언한다 (값이 아니라 성질).

    "지금 값이 우연히 안전하다" 와 "구조적으로 안전하다" 를 구분하는 층이다.
    """

    @pytest.fixture
    def viewer_html(self) -> str:
        assert _VIEWER_TEMPLATE.exists(), f"viewer.html missing: {_VIEWER_TEMPLATE}"
        return _VIEWER_TEMPLATE.read_text(encoding="utf-8")

    def test_template_script_block_supplies_no_quotes_of_its_own(self, viewer_html):
        """`_json_for_script` 가 따옴표까지 만든다 — 템플릿이 감싸면 안 된다."""
        block = _script_block(viewer_html)
        for key in (
            "ROOM_ID",
            "ROOM_NAME_JSON",
            "OUTPUT_LANGS_JSON",
            "PRIMARY_LANG",
            "INITIAL_STATE",
        ):
            placeholder = "{{" + key + "}}"
            assert placeholder in block, f"{key} missing from the bootstrap"
            assert '"' + placeholder + '"' not in block, f"{key} still quoted"

    def test_markup_room_name_placeholder_is_distinct_from_the_script_one(
        self, viewer_html
    ):
        """마크업 자리는 `{{ROOM_NAME}}`, 스크립트 자리는 `{{ROOM_NAME_JSON}}`.

        한 이름으로 합치면 두 sink 중 한쪽은 반드시 깨진다 (RL-020).
        """
        block = _script_block(viewer_html)
        assert "{{ROOM_NAME}}" not in block
        markup = viewer_html.replace(block, "")
        assert markup.count("{{ROOM_NAME}}") == 2  # <title> + #room-name
        assert "{{ROOM_NAME_JSON}}" not in markup

    def test_every_template_placeholder_is_mapped(self):
        """매핑이 템플릿의 모든 플레이스홀더를 덮는다.

        엄격 치환(미매핑 키 → KeyError)의 대가로 필요한 테스트다. 렌더가
        raise 하면 `_handle_view` 의 except 가 청중에게 404 를 준다.
        """
        from sse_broadcast import (
            _PLACEHOLDER_RE,
            _VIEWER_TEMPLATE_PATH,
            _render_viewer_html,
        )

        template = _VIEWER_TEMPLATE_PATH.read_text(encoding="utf-8")
        keys = set(_PLACEHOLDER_RE.findall(template))
        assert keys, "template has no placeholders — regex or template drifted"
        body = _render_viewer_html(
            room_id="r",
            room_name="n",
            output_langs=["ko"],
            primary_lang="ko",
            initial_state="waiting",
        )
        left = _PLACEHOLDER_RE.findall(body)
        assert left == [], f"unsubstituted placeholders: {left}"

    def test_unmapped_placeholder_fails_loudly(self, tmp_path, monkeypatch):
        """오타 난 플레이스홀더가 페이지로 새어 나가지 않는다 — KeyError 로 죽는다."""
        import sse_broadcast as _sb

        bogus = tmp_path / "viewer.html"
        bogus.write_text("<p>{{NOPE}}</p>", encoding="utf-8")
        monkeypatch.setattr(_sb, "_VIEWER_TEMPLATE_PATH", bogus)
        with pytest.raises(KeyError):
            _sb._render_viewer_html(
                room_id="r",
                room_name="n",
                output_langs=["ko"],
                primary_lang="ko",
                initial_state="waiting",
            )

    async def test_every_script_value_is_a_json_for_script_literal(self):
        """스크립트로 들어가는 값은 **전부** `_json_for_script` 출력과 바이트 일치.

        "이스케이프됐다" 가 아니라 "그 헬퍼를 통과했다" 를 단언한다 — 두 번째
        이스케이퍼가 생기면 이 테스트가 잡는다.
        """
        from sse_broadcast import _json_for_script, _supported_output_langs

        hostile = 'a"b\\<&> '
        repo = _StubRoomRepo(
            {
                hostile: _room(
                    id=hostile,
                    name=hostile,
                    primary_output_lang=hostile,
                    status=hostile,
                )
            }
        )
        status, body, _ = await _get(repo, "/view/" + quote(hostile, safe=""))
        assert status == 200
        expected = {
            "room_id": _json_for_script(hostile),
            "room_name": _json_for_script(hostile),
            "primary_lang": _json_for_script(hostile),
            "initial_state": _json_for_script(hostile),
            "output_langs": _json_for_script(_supported_output_langs(hostile)),
        }
        actual = {key: _config_literal(body, key) for key in expected}
        assert actual == expected

    def test_substitution_is_single_pass_re_sub(self):
        """구현 형태 자체를 단언한다 — 연쇄 `.replace` 로 되돌아가지 못한다."""
        import inspect

        from sse_broadcast import _render_viewer_html

        source = inspect.getsource(_render_viewer_html)
        body = source.split('"""')[-1]  # docstring 의 서술은 제외
        assert "_PLACEHOLDER_RE.sub(" in body
        assert "lambda" in body
        assert ".replace(" not in body


# ---------------------------------------------------------------------------
# 자막 파이프라인 파리티 — components/viewer.html (ISSUE-46)
# ---------------------------------------------------------------------------
class TestViewerCaptionPipeline:
    """`stage.html`(머지 `cc0681f`) 이 이미 고친 네 결함의 정적 계약.

    ISSUE-41 은 뷰어의 SSE·타자기 로직을 stage 로 **복사**하면서 복사본에서만
    버그를 고쳤다 (RL-001). 여기서 잠그는 것은 그 파리티다 — 값·순서까지 못
    박아 두어야 다음 드리프트가 CI 에서 보인다 (RL-004).

    결함 ↔ Test mapping (issues.md ISSUE-46 § Tests):
      - 결함 1 연속 final 유실   → test_finalize_locks_the_pending_line_first
                                   test_lock_line_writes_the_target_not_a_slice
      - 결함 2 빈 final 공백화   → test_finalize_bails_before_opening_a_blank_line
      - 결함 3 줄어든 partial 잔상 → test_a_shrinking_target_repaints_inside_the_clamp
      - 결함 4 innerHTML / firstChild
                                 → test_no_html_sinks_anywhere_in_the_file
                                   test_caption_and_lang_resets_use_replace_children
                                   test_line_cap_counts_and_removes_elements

    행동 검증은 tests/e2e/test_viewer_page_e2e.py::TestViewerCaptionStream.
    """

    def test_no_html_sinks_anywhere_in_the_file(self, viewer_html):
        """결함 4 — 파일 전역 HTML sink 금지 (tests/test_stage_page.py:467 미러링).

        두 템플릿이 같은 규칙에 대해 서로 다른 답을 갖고 있으면 안 된다.
        """
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            banned = (
                f"{sink} is a markup sink — viewer.html must build DOM with "
                "createElement/textContent only (stage.html 과 동일 규칙)"
            )
            assert sink not in viewer_html, banned

    def test_caption_and_lang_resets_use_replace_children(self, viewer_html):
        """결함 4 — 자막 스택/언어 셀렉터 리셋이 `replaceChildren()` 이다."""
        captions = "clearCaptions() must reset the stack with replaceChildren()"
        assert "captionContainer.replaceChildren()" in viewer_html, captions
        langs = "the language selector must be reset with replaceChildren()"
        assert "langSelect.replaceChildren()" in viewer_html, langs

    def test_line_cap_counts_and_removes_elements(self, viewer_html):
        """결함 4 — 요소 수로 세고 요소를 지운다. 상한은 뷰어의 200 그대로.

        `firstChild` 는 템플릿 들여쓰기가 남긴 공백 텍스트 노드부터 걷어내므로
        `children.length` 카운트와 짝이 맞지 않는다.
        """
        counted = "the DOM cap must count elements (children.length)"
        assert "captionContainer.children.length > MAX_LINES" in viewer_html, counted
        removed = (
            "the cap counts elements but must also remove an *element* — "
            "firstElementChild, not firstChild"
        )
        assert "captionContainer.firstElementChild" in viewer_html, removed
        assert "firstChild" not in viewer_html, removed

        match = re.search(r"const MAX_LINES = (\d+)", viewer_html)
        assert match is not None, "viewer.html must declare `const MAX_LINES = 200`"
        value = int(match.group(1))
        capped = (
            f"MAX_LINES is {value} — the viewer's wide centre column caps at 200; "
            "stage.html's 60 is for its narrow column (ISSUE-46 Scope § Out)"
        )
        assert value == 200, capped

    def test_finalize_locks_the_pending_line_first(self, viewer_html):
        """결함 1 — 앞 라인이 확정 대기 중이면 다음 final 이 먼저 그것을 확정한다.

        이 분기가 없으면 `_ensureCurrentLine()` 이 살아 있는 라인을 재사용하고
        `twTarget` 만 덮어써서 앞 자막이 한 글자도 남기지 못하고 사라진다.
        행동 검증은 e2e `test_back_to_back_finals_keep_both_lines`.
        """
        code = _js_body(viewer_html, _FINALIZE_BODY)
        dropped = (
            "finalizeCaption must lock the pending line before touching twTarget, "
            f"otherwise back-to-back finals drop the first caption: {code!r}"
        )
        assert "if (currentLine && twFinalize) _lockLine();" in code, dropped

    def test_finalize_bails_before_opening_a_blank_line(self, viewer_html):
        """결함 2 — 빈 final 은 라인을 열기 **전에** 빠져나간다 + 순서 고정.

        `_ensureCurrentLine()` 이 대기 문구(`#caption-empty`)를 제거하므로,
        조기 반환이 그 뒤로 밀리면 컬럼이 되돌릴 수 없이 공백화된다
        (tests/test_stage_page.py `test_empty_final_never_opens_a_blank_line`
        미러링). 순서까지 단언하는 이유: ①이 `currentLine` 을 null 로 만들기
        때문에 ②의 `currentLine` 은 in-flight partial 만 가리킨다 — 뒤집으면
        "빈 final 은 진행 중인 라인을 확정하라는 신호" 폴백이 조용히 사라진다.
        """
        code = _js_body(viewer_html, _FINALIZE_BODY)
        bailed = (
            "finalizeCaption must bail out before opening a line when there is "
            f"nothing to show: {code!r}"
        )
        assert "if (!next) return;" in code, bailed

        lock = code.index("if (currentLine && twFinalize) _lockLine();")
        fallback = code.index("const next =")
        bail = code.index("if (!next) return;")
        ensure = code.index("_ensureCurrentLine();")
        ordered = (
            "finalizeCaption's four steps must run in the order lock -> fallback "
            f"-> bail -> open, got offsets {lock}/{fallback}/{bail}/{ensure}: {code!r}"
        )
        assert lock < fallback < bail < ensure, ordered

    def test_lock_line_writes_the_target_not_a_slice(self, viewer_html):
        """결함 1/3 — 확정 경로는 `_lockLine()` 하나이고 `twTarget` 을 통째로 쓴다.

        번역 후처리가 문자열을 깎는 경우가 있어 마지막 슬라이스가 최종본이
        아닐 수 있다 (stage.html `_lockLine` 과 동일).
        """
        code = _js_body(viewer_html, _LOCK_LINE_BODY)
        exact = f"_lockLine must write the full target, got: {code!r}"
        assert "currentLine.textContent = twTarget;" in code, exact
        sliced = f"_lockLine must not lock a slice of the target: {code!r}"
        assert "slice(" not in code, sliced
        # 확정은 라인을 닫고 타이머를 멈추는 것까지가 한 단위다.
        assert "currentLine = null;" in code, code
        assert "_twStop();" in code, code

    def test_lock_line_measures_follow_before_it_writes(self, viewer_html):
        """결함 1 후속 — 확정 스냅이 크레딧 롤 추종을 영구히 꺼뜨리면 안 된다.

        `isUserAtBottom()` 은 `scrollHeight - scrollTop - clientHeight <= 80` 이다.
        `_lockLine()` 은 남은 글자를 한 번에 써넣으므로(연속 final 스냅) 높이가
        80px 이상 뛸 수 있는데, **쓴 뒤에** 재면 방금 늘어난 그 높이가 그대로
        gap 으로 잡혀 "청중이 위로 스크롤했다" 로 오판한다. gap 은 자막이
        쌓일수록 커지기만 하므로 되돌릴 계기가 없다 — 한 번 꺼지면 그 뒤 자막은
        전부 화면 아래로 흘러 다시 보이지 않는다.

        따라서 측정은 반드시 첫 DOM 쓰기보다 앞서야 한다. 존재 여부가 아니라
        **순서**를 못 박는다 (RL-004).
        """
        code = _js_body(viewer_html, _LOCK_LINE_BODY)
        measured = code.find("isUserAtBottom()")
        written = code.find("currentLine.textContent")
        assert measured != -1, f"_lockLine must measure follow state: {code!r}"
        assert written != -1, f"_lockLine must write the target: {code!r}"
        stale = (
            "_lockLine must call isUserAtBottom() BEFORE writing textContent — "
            "measuring after the write latches the credit roll off permanently "
            f"on the back-to-back-final snap: {code!r}"
        )
        assert measured < written, stale
        # 잰 값을 실제로 쓰는지까지 확인한다. 재고 나서 _scrollIfBottom() 을
        # 부르면 다시 재는 것이라 수정이 무의미해진다.
        gated = f"_lockLine must scroll on the pre-measured flag: {code!r}"
        assert "if (follow) _scrollToBottom();" in code, gated
        assert "_scrollIfBottom()" not in code, gated

    def test_typewriter_step_measures_follow_before_it_writes(self, viewer_html):
        """결함 1 후속 — 타자기 스텝도 같은 순서 규칙을 지킨다.

        step 은 `Math.max(2, Math.ceil(gap / 6))` 이라 긴 자막에서는 한 틱에
        수십~수백 자가 들어간다. `_lockLine()` 만 고치고 여기를 두면 같은 방식으로
        추종이 꺼진다.
        """
        code = _js_body(viewer_html, _TW_START_BODY)
        measured = code.find("isUserAtBottom()")
        written = code.find("currentLine.textContent = twTarget.slice(")
        assert measured != -1, f"_twStart must measure follow state: {code!r}"
        assert written != -1, f"_twStart must write a slice: {code!r}"
        stale = (
            "the typewriter step must call isUserAtBottom() BEFORE writing the "
            f"slice, for the same reason as _lockLine: {code!r}"
        )
        assert measured < written, stale
        gated = f"the step must scroll on the pre-measured flag: {code!r}"
        assert "if (follow) _scrollToBottom();" in code, gated

    def test_a_shrinking_target_repaints_inside_the_clamp(self, viewer_html):
        """결함 3 — 목표가 줄면 클램프만 하지 않고 화면을 즉시 다시 그린다.

        클램프만 하면 `gap` 이 0 이 되어 아래 쓰기 분기를 건너뛰고, 화면에는
        더 긴 옛 문자열이 잔상으로 남는다. 빈 분기 본문은 이 단언을 통과하지
        못한다 (RL-004).
        """
        branch = re.search(
            r"if \(twShown > twTarget\.length\) \{([^}]*)\}", viewer_html
        )
        assert branch is not None, (
            "no `if (twShown > twTarget.length) { … }` block — a bare clamp "
            "statement leaves the longer previous text on screen"
        )
        body = branch.group(1)
        clamped = f"the clamp branch must clamp twShown: {body!r}"
        assert "twShown = twTarget.length;" in body, clamped
        repainted = (
            "the clamp branch must repaint the line to the shrunken target, "
            f"otherwise the stale longer string stays on screen: {body!r}"
        )
        assert "currentLine.textContent = twTarget;" in body, repainted

    def test_typewriter_timer_stays_on_setinterval(self, viewer_html):
        """Scope 경계 — 뷰어의 28ms 타이머는 rAF 로 바꾸지 않는다.

        stage 의 rAF 전환 근거는 캡처 `<video>` 와의 프레임 경쟁(NFR-025)이며
        뷰어에는 그 경쟁이 없다. 파리티 포팅이 타이머 방식까지 끌고 오는 것을
        막는 가드다 (issues.md ISSUE-46 Scope § Out).
        """
        assert "setInterval(" in viewer_html
        assert "28);" in viewer_html, "the 28ms typewriter tick must stay"
