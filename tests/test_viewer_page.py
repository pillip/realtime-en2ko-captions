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
- WCAG AA 대비 + 자막 라이브 리전 (ISSUE-45): 모든 텍스트 색 규칙의 합성 후
  대비가 4.5:1 이상이고, 애니메이션 노드는 `aria-hidden`, 라인 확정 시에만
  갱신되는 `#caption-announcer` 가 라이브 리전을 소유한다.

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

# 대비 계산기는 stage 쪽과 **같은** 모듈을 쓴다 (RL-001). 복사본을 만들면 두
# 페이지가 서로 다른 계산기를 갖게 되고, 그 드리프트가 ISSUE-45 자체의 원인이다.
from tests.wcag import (
    _composite,
    _contrast_ratio,
    _hex_rgb,
    _rule_block,
    _rule_hex,
    _rule_rgba,
)

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

_SCROLL_LOCK_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "components" / "scroll_lock.html"
)

# 라이브 리전 관련 함수 본문 추출 패턴 (ISSUE-45).
_ANNOUNCE_BODY = r"function announce\(text\) \{(.*?)\n    \}"

# ---------------------------------------------------------------------------
# WCAG AA 대비 (ISSUE-45 / RL-018)
# ---------------------------------------------------------------------------
# 페이지가 칠하는 배경. 이 상수들은 "가정" 이 아니라 **검증된 사실** 이어야 한다
# — 아래 test_contrast_backdrops_match_what_the_stylesheet_declares 가
# viewer.html 이 실제로 선언한 값과 대조한다. ISSUE-42 는 `#000000` 을 backdrop
# 으로 가정해 11.42:1 로 통과한 문구가 실제로는 재생 중인 <video> 위에서
# 1.00:1(완전 비가시)로 렌더되는 결함을 냈다 — 대비 수치는 **합성된 색**에 대한
# 주장이고, 테스트의 backdrop 상수가 바로 그 주장이 무너지는 지점이다.
_CANVAS_RGB = (11, 11, 12)  # body { background: #0b0b0c }
_OPTION_RGB = (21, 21, 23)  # #lang-select option { background: #151517 }
_BLACK_RGB = (0, 0, 0)  # 뷰어는 칠하지 않지만 알파 하한 비교용

# 실측 알파 하한 (contrast probe). 캔버스보다 순수 검정 위에서 하한이 **높다**.
_ALPHA_FLOOR_CANVAS = 0.45  # #0b0b0c 위 4.52:1 (0.44 는 4.34:1 로 미달)
_ALPHA_FLOOR_BLACK = 0.46  # #000000 위 4.56:1 (0.45 는 4.43:1 로 미달)

# viewer.html 이 선언하는 **불투명/반투명 배경**의 전부. 새 배경 표면이 하나
# 늘어난다는 것은 곧 어떤 텍스트 아래에 새 backdrop 이 깔린다는 뜻이고, 그러면
# 위 대비 감사 목록을 다시 봐야 한다 (RL-018). 이 목록이 그 재검토를 강제한다.
_KNOWN_BACKGROUNDS = {
    "body": "#0b0b0c",
    ".live-dot": "rgba(255, 255, 255, 0.22)",
    ".live-dot.live": "#4ade80",
    "#lang-select option": "#151517",
    ".listening-indicator span": "rgba(255, 255, 255, 0.3)",
    ".conn-error": "rgba(255, 255, 255, 0.07)",
}

# 텍스트 색 규칙 × 실제 backdrop × 실측 대비. 숫자를 목록에 남겨 두는 것이
# 이 이슈의 명시 요구사항이다 — "보기에 은은하다" 로 알파를 고른 것이 애초의
# 실패 원인이었다 (RL-018).
_TEXT_COLOUR_AUDIT = [
    ("body", _CANVAS_RGB, 19.67),
    (".room-name", _CANVAS_RGB, 5.34),
    (".lang-control label", _CANVAS_RGB, 5.34),
    ("#lang-select", _CANVAS_RGB, 19.67),
    ("#lang-select option", _OPTION_RGB, 18.24),
    (".state-message", _CANVAS_RGB, 12.50),
    (".state-room", _CANVAS_RGB, 5.34),
    ("#state-ended .state-message", _CANVAS_RGB, 7.26),
    (".caption-line", _CANVAS_RGB, 4.52),
    (".caption-container .caption-line:last-child", _CANVAS_RGB, 19.67),
    (".caption-empty", _CANVAS_RGB, 5.34),
    # 캔버스가 아니라 **합성된 알약 배경** 위에서 잰다 (아래 전용 테스트 참조).
    (".conn-error", _composite((255, 255, 255, 0.07), _CANVAS_RGB), 7.85),
]

_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
_RGBA_RE = re.compile(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)")


def _style_sheet(html: str) -> str:
    """`<style>` 블록들을 이어 붙인 CSS (블록이 없으면 빈 문자열).

    `/* … */` 주석은 걷어낸다 — 남겨 두면 규칙 바로 위 주석이 셀렉터 문자열에
    통째로 붙어 `.conn-error` 가 `/* … */ .conn-error` 로 잡힌다.
    """
    css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _declarations(block: str) -> list[tuple[str, str]]:
    """규칙 본문을 `(property, value)` 목록으로.

    exact-name 매칭이라 `transition: color 0.5s ease` 나 `border-top-color`
    같은 이웃 선언이 `color` 감사에 섞이지 않는다 — substring 매칭은 바로
    그렇게 오염된다 (RL-004).

    `/* … */` 는 먼저 걷어낸다. 이 파일은 알파를 고른 근거(대비 수치)를 규칙
    바로 위 주석으로 남기는데, 남겨 두면 그 주석이 뒤따르는 선언과 한 덩어리로
    잘려 `color:` 가 통째로 보이지 않게 된다.
    """
    out = []
    for raw in re.sub(r"/\*.*?\*/", "", block, flags=re.S).split(";"):
        name, sep, value = raw.partition(":")
        if not sep:
            continue
        out.append((name.strip(), value.strip()))
    return out


def _rules(css: str):
    """`selector { … }` 를 순회한다. `@media`/`@keyframes` 래퍼는 건너뛰고
    그 **안쪽** 규칙은 그대로 잡힌다 (평면 정규식이라 래퍼는 매칭에 실패한다)."""
    for match in _RULE_RE.finditer(css):
        selector = " ".join(match.group(1).split())
        if not selector or selector.startswith("@"):
            continue
        yield selector, match.group(2)


def _colour_rules(html: str) -> list[tuple[str, str]]:
    """파일 안의 모든 `color:` 선언을 `(selector, value)` 로 훑는다."""
    return [
        (selector, value)
        for selector, block in _rules(_style_sheet(html))
        for name, value in _declarations(block)
        if name == "color"
    ]


def _rule_colour(css: str, selector: str) -> tuple[float, float, float, float]:
    """규칙 블록의 `color:` 를 (r, g, b, a) 로. `#rrggbb` 는 알파 1.0."""
    block = _rule_block(css, selector)
    values = [value for name, value in _declarations(block) if name == "color"]
    counted = f"{selector} declares {len(values)} `color:` values, expected 1"
    assert len(values) == 1, counted
    if values[0].startswith("#"):
        return (*_hex_rgb(values[0]), 1.0)
    return _rule_rgba(css, selector)


def _element_span(html: str, open_match: re.Match, tag: str) -> str:
    """여는 태그부터 **같은 깊이의** 닫는 태그까지를 잘라낸다.

    문자열 존재 확인(`'id="x"' in html`)은 그 요소가 컨테이너 **안**에 중첩돼
    있어도 통과한다. 자손 여부를 실제로 판정하려면 구간을 잘라야 한다.
    """
    depth = 1
    pattern = re.compile(rf"<(/?){re.escape(tag)}\b[^>]*>", re.I)
    for match in pattern.finditer(html, open_match.end()):
        depth += -1 if match.group(1) else 1
        if depth == 0:
            return html[open_match.start() : match.end()]
    raise AssertionError(f"unbalanced <{tag}> starting at {open_match.start()}")


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


# ---------------------------------------------------------------------------
# WCAG AA 대비 — components/viewer.html + components/scroll_lock.html (ISSUE-45)
# ---------------------------------------------------------------------------
class TestViewerContrast:
    """WCAG 1.4.3 — 어두운 캔버스 위 흐린 텍스트도 4.5:1 이상 (RL-018).

    ISSUE-40 은 무대 페이지에서 이 실패 3건을 수치로 잡아 고쳤는데, 실패한
    알파는 전부 `viewer.html` 에서 복사해 온 값이었다. 무대만 고쳐졌고 뷰어는
    `/view/{room_id}` 로 누구나 접근 가능한 채 실패 값을 계속 서빙했다.

    이 클래스는 대비 수치만 재지 않는다. 대비 수치는 backdrop 상수에 대한
    **주장**이므로, backdrop 가정 자체를 검증된 사실로 바꾸는 구조 가드를 함께
    둔다 (test_contrast_backdrops_match_what_the_stylesheet_declares /
    test_opaque_background_surfaces_are_the_enumerated_set).

    AC ↔ Test mapping (issues.md ISSUE-45 § Tests):
      - "모든 텍스트 색 규칙 4.5:1 이상"
          → test_every_text_colour_rule_meets_wcag_aa_contrast
      - "감사 목록이 파일 전체를 덮는다"
          → test_the_audit_list_covers_every_colour_rule_in_the_file
      - "알파 하한 미만이 남아 있지 않다"
          → test_no_white_alpha_text_rule_sits_below_the_measured_floor
      - "scroll_lock.html 감사 완료"
          → test_scroll_lock_component_declares_no_text_colour
    """

    @pytest.mark.parametrize("selector,backdrop,expected", _TEXT_COLOUR_AUDIT)
    def test_every_text_colour_rule_meets_wcag_aa_contrast(
        self, viewer_html, selector, backdrop, expected
    ):
        """감사 목록의 실측 대비 (`#0b0b0c` 캔버스 기준, 합성 후):

        | 규칙 | 알파 | 대비 |
        |---|---|---|
        | `body` / `#lang-select` / `:last-child` | `#ffffff` | 19.67:1 |
        | `#lang-select option` (on `#151517`) | `#ffffff` | 18.24:1 |
        | `.state-message` | 0.8 | 12.50:1 |
        | `.conn-error` (합성된 알약 위) | 0.65 | 7.85:1 |
        | `#state-ended .state-message` | 0.6 | 7.26:1 |
        | `.room-name` | 0.42 → **0.5** | 4.04 → **5.34:1** |
        | `.state-room` | 0.35 → **0.5** | 3.13 → **5.34:1** |
        | `.caption-empty` | 0.32 → **0.5** | 2.81 → **5.34:1** |
        | `.caption-line` | 0.42 → **0.45** | 4.04 → **4.52:1** |

        판정은 **일반 텍스트 4.5:1** 기준이다. `clamp()` 하한이 large-text
        임계(24px)를 넘느냐로 완화를 노리지 않는다 — `.caption-line` 의 하한이
        정확히 24px 이지만 무대 페이지도 같은 기준으로 0.45 로 올려 출하됐고,
        이 이슈의 절반은 그 파리티를 되찾는 일이다.

        `.caption-line` 만 0.5 가 아니라 0.45 인 이유: 순백(`#ffffff`)인
        `:last-child` 현재 라인과의 시각적 위계 간격을 지키기 위해서다 (AC-4).
        """
        ratio = _contrast_ratio(_rule_colour(viewer_html, selector), backdrop)
        assert ratio >= 4.5, f"{selector} is {ratio:.2f}:1, WCAG AA needs 4.5:1"
        drifted = (
            f"{selector} now measures {ratio:.2f}:1 but the audit table records "
            f"{expected:.2f}:1 — update _TEXT_COLOUR_AUDIT so the numbers on the "
            "record stay true (RL-018: the recorded number IS the evidence)"
        )
        assert round(ratio, 2) == expected, drifted

    def test_the_audit_list_covers_every_colour_rule_in_the_file(self, viewer_html):
        """감사 범위는 "목록에 적은 것" 이 아니라 "파일 안의 모든 텍스트 색" 이다.

        parametrize 목록만 두면 나중에 추가된 규칙이 조용히 감사 밖에 남는다 —
        `.caption-empty` 가 실패 값을 그대로 서빙하던 것과 같은 형태의 누락이다.
        """
        found = {selector for selector, _ in _colour_rules(viewer_html)}
        audited = {selector for selector, _, _ in _TEXT_COLOUR_AUDIT}
        missing = found - audited
        unaudited = (
            f"viewer.html declares `color:` on {sorted(missing)} but the WCAG "
            "audit list does not cover them — every text colour rule must be "
            "measured, not just the ones someone remembered"
        )
        assert not missing, unaudited
        stale = f"_TEXT_COLOUR_AUDIT lists {sorted(audited - found)}, not in the file"
        assert not (audited - found), stale

    def test_no_white_alpha_text_rule_sits_below_the_measured_floor(self, viewer_html):
        """ "다른 페이지에서 알파를 복사해 온다" 는 재발을 막는 스윕 가드.

        규칙별 합성 대비는 위 테스트가 잰다. 여기서는 파일 전체를 훑어 하한
        미만의 흰색 알파가 **하나도** 남지 않았음을 단언한다.
        """
        for selector, value in _colour_rules(viewer_html):
            match = _RGBA_RE.search(value)
            if match is None:
                continue
            r, g, b, alpha = match.groups()
            alpha = float(alpha)
            ratio = _contrast_ratio((int(r), int(g), int(b), alpha), _CANVAS_RGB)
            faint = (
                f"{selector} paints text at alpha {alpha} → {ratio:.2f}:1 on the "
                f"#0b0b0c canvas. The measured AA floor is "
                f"{_ALPHA_FLOOR_CANVAS} (4.52:1) on this canvas and "
                f"{_ALPHA_FLOOR_BLACK} (4.56:1) on pure #000000"
            )
            assert alpha >= _ALPHA_FLOOR_CANVAS, faint

    def test_past_lines_stay_dimmer_than_the_current_line(self, viewer_html):
        """AC-4 — 알파를 올려도 시각적 위계가 뒤집히지 않는다.

        과거 라인(`.caption-line`)은 여전히 순백인 현재 라인
        (`:last-child`)보다 흐려야 한다. `.caption-line` 을 권장값 0.5 가 아니라
        0.45 로 둔 이유가 바로 이 간격이다.
        """
        past = _rule_colour(viewer_html, ".caption-line")
        current = _rule_colour(
            viewer_html, ".caption-container .caption-line:last-child"
        )
        assert current == (255, 255, 255, 1.0), f"current line is {current}"

        past_ratio = _contrast_ratio(past, _CANVAS_RGB)
        current_ratio = _contrast_ratio(current, _CANVAS_RGB)
        inverted = (
            f"past lines read at {past_ratio:.2f}:1 and the current line at "
            f"{current_ratio:.2f}:1 — the past line must stay visibly dimmer, "
            "otherwise the credit roll loses its 'this is the live line' cue"
        )
        assert past_ratio < current_ratio, inverted
        # 그러면서도 AA 는 넘겨야 한다 — 위계를 이유로 미달을 정당화하지 않는다.
        assert past_ratio >= 4.5, f"past lines are {past_ratio:.2f}:1"

    def test_the_alpha_floor_constants_are_the_real_floors(self):
        """하한 상수도 전승이 아니라 계산 결과여야 한다 (RL-018).

        `0.45` / `0.46` 이 "어디선가 들은 값" 으로 굳으면 스윕 가드 전체가
        근거를 잃는다. 한 단계 아래 알파가 실제로 미달임을 함께 못 박는다.
        """
        canvas_ok = _contrast_ratio((255, 255, 255, _ALPHA_FLOOR_CANVAS), _CANVAS_RGB)
        canvas_under = _contrast_ratio((255, 255, 255, 0.44), _CANVAS_RGB)
        assert canvas_ok >= 4.5, f"canvas floor is {canvas_ok:.2f}:1"
        assert canvas_under < 4.5, f"0.44 on the canvas is {canvas_under:.2f}:1"

        black_ok = _contrast_ratio((255, 255, 255, _ALPHA_FLOOR_BLACK), _BLACK_RGB)
        black_under = _contrast_ratio((255, 255, 255, 0.45), _BLACK_RGB)
        assert black_ok >= 4.5, f"black floor is {black_ok:.2f}:1"
        higher = (
            f"0.45 on pure #000000 is {black_under:.2f}:1 — the floor on black is "
            "HIGHER than on the canvas, so a canvas alpha is not transferable"
        )
        assert black_under < 4.5, higher

    def test_contrast_backdrops_match_what_the_stylesheet_declares(self, viewer_html):
        """RL-018 구조 가드 — backdrop 상수는 가정이 아니라 검증된 사실이다.

        ISSUE-42 는 `#000000` 을 backdrop 으로 **가정**해 11.42:1 로 통과한
        문구가 실제로는 재생 중인 `<video>` 위에서 1.00:1(완전 비가시)로
        렌더되는 결함을 냈다. 대비 수치는 합성된 색에 대한 주장이고, 무너지는
        지점은 언제나 backdrop 상수다. 캔버스를 누가 바꾸면 대비 스위트는
        **깨져야** 한다 — 더 이상 렌더되지 않는 색을 상대로 조용히 계속 단언하면
        안 된다.
        """
        declared = _rule_hex(viewer_html, "body", "background")
        drifted = (
            f"body paints {declared} but the contrast suite measures against "
            f"{_CANVAS_RGB} — every ratio in _TEXT_COLOUR_AUDIT is now a claim "
            "about a colour that no longer renders"
        )
        assert declared == _CANVAS_RGB, drifted

        option = _rule_hex(viewer_html, "#lang-select option", "background")
        assert option == _OPTION_RGB, f"#lang-select option paints {option}"

        # `#lang-select` 자신의 흰 글자를 캔버스 위에서 재는 근거. 배경이
        # transparent 라야 조상(body)의 캔버스가 실제 backdrop 이다.
        select = _rule_block(viewer_html, "#lang-select")
        backgrounds = [v for name, v in _declarations(select) if name.startswith("bac")]
        opaque = (
            "#lang-select must keep `background-color: transparent` — its "
            f"#ffffff text is measured against the canvas: {backgrounds}"
        )
        assert "transparent" in backgrounds, opaque

    def test_opaque_background_surfaces_are_the_enumerated_set(self, viewer_html):
        """새 배경 표면은 곧 어떤 텍스트 아래의 새 backdrop 이다 (RL-018).

        반투명이든 불투명이든 배경이 하나 늘어나면 그 위에 얹히는 텍스트의
        대비가 달라진다. 목록을 못 박아 두어야 다음 표면이 감사를 다시 열게
        만든다 — 조용히 지나가면 `.conn-error` 알약 같은 함정이 또 생긴다.
        """
        painted = {}
        for selector, block in _rules(_style_sheet(viewer_html)):
            for name, value in _declarations(block):
                if name not in ("background", "background-color"):
                    continue
                if value in ("none", "transparent"):
                    continue
                if re.search(r",\s*0(?:\.0+)?\s*\)$", value):  # rgba(..., 0)
                    continue
                painted[selector] = value

        added = {k: v for k, v in painted.items() if k not in _KNOWN_BACKGROUNDS}
        new_surface = (
            f"viewer.html grew new painted background(s) {added} — each one is a "
            "new backdrop under some text, so the WCAG audit list has to be "
            "revisited before this guard is updated"
        )
        assert not added, new_surface
        assert painted == _KNOWN_BACKGROUNDS, f"background surfaces drifted: {painted}"

    def test_conn_error_banner_meets_wcag_aa_contrast(self, viewer_html):
        """RL-018 — 반투명 pill 위 텍스트는 **합성 후** 배경으로 계산한다.

        배너 텍스트의 실제 배경은 캔버스가 아니라 캔버스 위에 깔린 반투명
        pill 이다. 밝아진 배경은 밝은 글자의 대비를 **낮추므로**, pill 을
        가정에서 지워 버리면 실제보다 후한 숫자가 나온다. 무대 쪽 대응은
        `tests/test_stage_page.py::test_conn_error_banner_meets_wcag_aa_contrast`.
        """
        block = _rule_block(viewer_html, ".conn-error")
        bg = re.search(
            r"background:\s*rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)", block
        )
        assert bg is not None, ".conn-error needs an rgba() background declaration"
        r, g, b, a = bg.groups()
        pill = _composite((int(r), int(g), int(b), float(a)), _CANVAS_RGB)

        ratio = _contrast_ratio(_rule_rgba(viewer_html, ".conn-error"), pill)
        assert ratio >= 4.5, f".conn-error text is {ratio:.2f}:1, WCAG AA needs 4.5:1"

        # pill 위 하한은 캔버스보다 높다 — 캔버스 알파를 그대로 옮기면 안 된다.
        floor = _contrast_ratio((255, 255, 255, _ALPHA_FLOOR_CANVAS), pill)
        stricter = (
            f"alpha {_ALPHA_FLOOR_CANVAS} on the composited pill is only "
            f"{floor:.2f}:1 — the pill's floor is {_ALPHA_FLOOR_BLACK}, so the "
            "canvas floor is not transferable to this surface"
        )
        assert floor < 4.5, stricter

    def test_scroll_lock_component_declares_no_text_colour(self):
        """`components/scroll_lock.html` 감사 결과를 사실로 고정한다.

        이 컴포넌트는 순수 script/style 이라 텍스트 노드도, `color:` 선언도
        없다. "확인했다" 를 코드에 남기지 않으면 다음 감사 때 또 처음부터
        세어야 한다 — 감사 완료 자체를 테스트로 굳힌다 (issues.md ISSUE-45
        § Scope: "확인 자체를 결과로 남긴다").
        """
        assert _SCROLL_LOCK_TEMPLATE.exists(), f"missing: {_SCROLL_LOCK_TEMPLATE}"
        html = _SCROLL_LOCK_TEMPLATE.read_text(encoding="utf-8")
        rules = _colour_rules(html)
        for selector, value in rules:
            match = _RGBA_RE.search(value)
            if match is None:
                continue
            r, g, b, alpha = match.groups()
            ratio = _contrast_ratio((int(r), int(g), int(b), float(alpha)), _CANVAS_RGB)
            assert ratio >= 4.5, f"{selector} is {ratio:.2f}:1, WCAG AA needs 4.5:1"
        audited = (
            f"scroll_lock.html grew text colour rule(s) {rules} — the recorded "
            "audit fact (zero text colour rules) is stale; measure them and "
            "extend this test"
        )
        assert rules == [], audited


# ---------------------------------------------------------------------------
# 자막 라이브 리전 — components/viewer.html (ISSUE-45, ISSUE-41 FU-3)
# ---------------------------------------------------------------------------
class TestViewerCaptionLiveRegion:
    """RL-019 — 프레임/틱마다 바뀌는 노드는 라이브 리전이 아니다.

    뷰어에는 자막 라이브 리전이 **아예** 없었다. `#state-waiting` 과
    `#state-ended` 만 `aria-live="polite"` 라서 스크린리더 청중은 "잠시 후
    시작됩니다" 와 "세션이 종료되었습니다" 는 듣고 그 사이의 자막 본문은 한
    글자도 듣지 못했다 — 이 페이지의 존재 이유가 바로 그 본문이다.

    패턴은 `components/stage.html`(머지 `cc0681f`) 을 미러링한다. 무대 쪽 대응
    테스트는 `tests/test_stage_page.py::test_animated_node_is_not_the_live_region`.
    행동 검증은 `tests/e2e/test_viewer_page_e2e.py::TestViewerLiveRegion`.
    """

    def test_animated_node_is_not_the_live_region(self, viewer_html):
        """애니메이션 노드는 `aria-hidden`, 별도 announcer 가 `aria-live` 를 갖는다.

        타자기는 `#captionContainer` 에 `.caption-line` 을 append 하고 마지막
        라인의 `textContent` 를 28ms 마다 바꾸며 `MAX_LINES` 로 앞쪽 자식을
        잘라낸다 — 이 노드를 라이브 리전으로 두면 모든 공개 틱과 모든 트리밍이
        낭독된다.
        """
        animated = re.search(r"<div[^>]*\bid=\"captionContainer\"[^>]*>", viewer_html)
        assert animated is not None, "#captionContainer open tag not found"
        animated_tag = animated.group(0)
        flooded = (
            "#captionContainer carries aria-live — the typewriter mutates this "
            f"node every 28ms tick: {animated_tag}"
        )
        assert "aria-live" not in animated_tag, flooded
        exposed = (
            "#captionContainer must be aria-hidden so per-tick reveals are not "
            f"announced: {animated_tag}"
        )
        assert 'aria-hidden="true"' in animated_tag, exposed

        announcer = re.search(
            r"<[a-zA-Z]+[^>]*\bid=\"caption-announcer\"[^>]*>", viewer_html
        )
        orphaned = (
            "no #caption-announcer element — the viewer has no live region at "
            "all, so screen-reader attendees hear zero characters of the "
            "caption body (ISSUE-41 FU-3)"
        )
        assert announcer is not None, orphaned
        announcer_tag = announcer.group(0)
        assert 'aria-live="polite"' in announcer_tag, announcer_tag
        assert 'aria-atomic="true"' in announcer_tag, announcer_tag
        distinct = "the announcer must not be the animated container itself"
        assert announcer_tag != animated_tag, distinct

    def test_exactly_one_caption_live_region_exists(self, viewer_html):
        """AC — 자막 **텍스트**를 받는 라이브 리전은 정확히 1개다.

        `#state-waiting` / `#state-ended` 도 `aria-live="polite"` 지만 자막
        텍스트를 받지 않는다(상태 카피 전용). 이중 낭독은 announcer 가 종료
        문구까지 되풀이할 때 생긴다.
        """
        live_tags = re.findall(r"<[a-zA-Z]+[^>]*\baria-live=[^>]*>", viewer_html)
        ids = [re.search(r'\bid="([^"]+)"', tag) for tag in live_tags]
        owners = sorted(m.group(1) for m in ids if m is not None)
        expected = ["caption-announcer", "state-ended", "state-waiting"]
        drifted = (
            f"aria-live owners are {owners}; expected exactly {expected} — the "
            "two state sections announce their own copy and only "
            "#caption-announcer receives caption text (RL-019)"
        )
        assert owners == expected, drifted
        anonymous = f"an aria-live node has no id: {live_tags}"
        assert len(live_tags) == len(owners), anonymous

    def test_the_announcer_is_not_a_descendant_of_the_hidden_container(
        self, viewer_html
    ):
        """구조 단언 — `aria-hidden` 은 서브트리 전체를 접근성 트리에서 지운다.

        자손으로 넣으면 `aria-live` 를 달아도 되돌릴 수 없다. 문자열 존재
        확인(`'id="caption-announcer"' in html`)만으로는 중첩된 경우도 통과하므로
        컨테이너 구간을 실제로 잘라서 본다 (RL-004).

        `<section class="state">` 안에 있어도 안 된다 — `.state { display: none }`
        이라 첫 payload 전까지 렌더되지 않고, 생성/노출과 같은 틱에 첫 텍스트가
        들어오는 라이브 리전은 스크린리더가 안정적으로 읽지 않는다.
        """
        open_match = re.search(r"<div[^>]*\bid=\"captionContainer\"[^>]*>", viewer_html)
        assert open_match is not None, "#captionContainer open tag not found"
        container = _element_span(viewer_html, open_match, "div")
        nested = (
            "#caption-announcer sits INSIDE #captionContainer, which is "
            "aria-hidden — a descendant cannot undo aria-hidden, so the live "
            f"region would never reach assistive tech: {container[:160]!r}"
        )
        assert 'id="caption-announcer"' not in container, nested

        for section in re.finditer(
            r"<section[^>]*\bclass=\"state\"[^>]*>", viewer_html
        ):
            span = _element_span(viewer_html, section, "section")
            hidden = (
                "#caption-announcer sits inside a <section class='state'>, which "
                "is `display: none` until JS toggles .active — a live region "
                f"revealed in the same tick as its first text is unreliable: "
                f"{section.group(0)}"
            )
            assert 'id="caption-announcer"' not in span, hidden

        main = re.search(r"<div[^>]*\bclass=\"main\"[^>]*>", viewer_html)
        assert main is not None, "<div class='main'> not found"
        span = _element_span(viewer_html, main, "div")
        placed = (
            "#caption-announcer must live in <div class='main'> as a sibling of "
            "the state sections — permanently rendered, permanently armed"
        )
        assert 'id="caption-announcer"' in span, placed

    def test_sr_only_clips_without_a_negative_vertical_margin(self, viewer_html):
        """`.sr-only` 는 `clip-path` 로 잘라낸다 — 구식 `clip` + 음수 margin 금지.

        `display: none` / `visibility: hidden` 계열은 접근성 트리에서도 사라져
        라이브 리전이 죽는다. 음수 세로 margin 은 이 프로젝트가 금지한다
        (RL-012) — 잘라내는 일은 `clip-path` 가 한다.
        """
        block = _rule_block(viewer_html, ".sr-only")
        assert "clip-path: inset(50%)" in block, f".sr-only must clip-path: {block!r}"

        declarations = _declarations(block)
        margins = [(n, v) for n, v in declarations if n == "margin" or "margin-" in n]
        assert margins == [("margin", "0")], f".sr-only margins are {margins}"

        removed = [
            (n, v)
            for n, v in declarations
            if (n == "display" and v == "none") or (n == "visibility" and v == "hidden")
        ]
        gone = (
            f".sr-only uses {removed} — that removes the node from the "
            "accessibility tree too, so the live region would never announce"
        )
        assert removed == [], gone

        # `.sr-only` 는 시각적으로 렌더되지 않으므로 대비 감사 대상이 아니다 —
        # 색 규칙을 아예 두지 않아야 감사 스윕과 충돌하지 않는다.
        colours = [(n, v) for n, v in declarations if n == "color"]
        assert colours == [], f".sr-only must declare no colour: {colours}"

    def test_announce_is_the_only_writer_to_the_live_region(self, viewer_html):
        """라이브 리전에 쓰는 경로는 `announce()` 하나다 (RL-019)."""
        # `=(?!=)` — 대입만 센다. `===` 비교(announce() 의 무변경 가드)는 쓰기가
        # 아니므로 세면 안 된다.
        writes = re.findall(r"announcer\.textContent\s*=(?!=)", viewer_html)
        many = (
            f"{len(writes)} writers touch announcer.textContent — the live "
            "region must have a single writer so every announcement is auditable"
        )
        assert len(writes) == 1, many

        body = _js_body(viewer_html, _ANNOUNCE_BODY)
        assert "announcer.textContent = text;" in body, body
        refired = (
            "announce() must no-op when the text is unchanged, otherwise a live "
            f"region is re-fired with an identical value: {body!r}"
        )
        assert "announcer.textContent === text" in body, refired

    def test_announce_runs_on_the_lock_path_and_never_on_a_tick(self, viewer_html):
        """자막 **텍스트**를 라이브 리전에 쓰는 지점은 라인 확정 하나뿐이다.

        28ms 인터벌 콜백 옆에 `announce()` 를 붙이면 스크린리더가 초당 35회
        갱신으로 범람한다 — RL-019 가 막으려는 실패 그대로다.
        """
        lock = _js_body(viewer_html, _LOCK_LINE_BODY)
        locked = f"_lockLine must announce the finalised line: {lock!r}"
        assert "announce(twTarget);" in lock, locked

        # ISSUE-46 의 순서 규칙은 그대로다: 추종 여부는 어떤 쓰기보다 먼저 잰다.
        measured = lock.find("isUserAtBottom()")
        written = lock.find("currentLine.textContent = twTarget;")
        announced = lock.find("announce(twTarget);")
        ordered = (
            "announce() must come after the follow measurement and after the "
            f"line write, exactly as stage.html does: {lock!r}"
        )
        assert measured < written < announced, ordered

        step = _js_body(viewer_html, _TW_START_BODY)
        flooded = (
            "announce() is called from inside the 28ms setInterval callback — "
            f"that floods the screen reader ~35x/sec (RL-019): {step!r}"
        )
        assert "announce(" not in step, flooded

    def test_state_copy_ownership_covers_the_active_waiting_text(self, viewer_html):
        """UI-7 — `#caption-empty` 대기 문구는 announcer 가 대신 든다.

        `#caption-empty` 는 `aria-hidden` 이 된 `#captionContainer` **안**에
        있어 보조기술에 닿지 않는다. waiting/ended 는 각자 `<section aria-live>`
        가 알리지만, **active 상태에서 대기 문구만 보이는 경우**는 예외다 —
        빈 final(`finalizeCaption("")`) 이 들어오거나 언어 전환이
        `clearCaptions()` 로 문구를 재생성하는 경로가 그렇다.

        행동 검증은 e2e 쪽 `test_the_active_waiting_copy_reaches_the_live_region`
        / `test_language_switch_announces_the_new_waiting_copy`.
        """
        sync = re.search(
            r"function syncStateAnnouncement\(\) \{(.*?)\n    \}", viewer_html, re.S
        )
        unowned = (
            "no syncStateAnnouncement() — nothing owns the active-state waiting "
            "copy, so #caption-empty stays trapped inside the aria-hidden subtree"
        )
        assert sync is not None, unowned

        for caller, pattern in (
            ("setState", r"function setState\(name\) \{(.*?)\n    \}"),
            ("clearCaptions", r"function clearCaptions\(\) \{(.*?)\n    \}"),
            ("applyWaitingText", r"function applyWaitingText\(lang\) \{(.*?)\n    \}"),
        ):
            body = _js_body(viewer_html, pattern)
            missed = (
                f"{caller}() must re-sync the announcer — it is one of the three "
                f"paths that change what the active state is showing: {body!r}"
            )
            assert "syncStateAnnouncement();" in body, missed


# ---------------------------------------------------------------------------
# control 이벤트 수신 — 언어 추종 + 자막 배율 (ISSUE-53, FR-085 / TC-083 · TC-086)
# ---------------------------------------------------------------------------
_VIEWER_CONNECT_BODY = r"function connect\(lang\) \{(.*?)\n    \}"
_SWITCH_LANGUAGE_BODY = r"function switchLanguage\(next\) \{(.*?)\n    \}"
_HANDLE_CONTROL_BODY = r"function handleControl\(payload\) \{(.*?)\n    \}"
_APPLY_SCALE_BODY = r"function applyCaptionScale\(value\) \{(.*?)\n    \}"
_CLEAR_CAPTIONS_BODY = r"function clearCaptions\(\) \{(.*?)\n    \}"


class TestViewerControlChannel:
    """청중이 직접 고른 언어는 control 에 끌려가지 않는다 (헤드라인 규칙)."""

    def test_control_has_its_own_named_listener(self, viewer_html):
        """자막 hot path 에 조건문을 늘리지 않는다 — 분리된 리스너다."""
        assert 'addEventListener("control"' in viewer_html
        body = re.search(
            r'es\.addEventListener\("message", \(ev\) => \{(.*?)\n      \}\);',
            viewer_html,
            re.S,
        )
        assert body is not None, "message 리스너를 찾지 못했다"
        assert "payload.event" not in body.group(1), body.group(1)

    def test_the_dropdown_is_the_only_explicit_selection_signal(self, viewer_html):
        """`langLocked` 는 드롭다운 `change` 에서만 세워진다.

        뷰어는 `?lang=` 을 읽지 않으므로(브라우저 파싱 금지) 명시적 선택의
        신호는 드롭다운 하나뿐이다.
        """
        change = re.search(
            r'langSelect\.addEventListener\("change", \(ev\) => \{(.*?)\n    \}\);',
            viewer_html,
            re.S,
        )
        assert change is not None, "langSelect change 핸들러를 찾지 못했다"
        assert "langLocked = true;" in change.group(1), change.group(1)

        writes = re.findall(r"langLocked\s*=\s*(?!=)", viewer_html)
        assert len(writes) == 2, (
            f"langLocked is assigned {len(writes)} time(s); exactly two are "
            "allowed — the `let` initialiser and the dropdown change handler. "
            "Any other writer means something else can lock or unlock the "
            "attendee's choice"
        )

    def test_the_lock_is_recorded_before_the_no_op_early_return(self, viewer_html):
        """룸 기본값을 **일부러 다시 고른** 청중도 잠긴다.

        `langLocked = true;` 가 `next === currentLang` 조기 반환 **뒤로** 밀리면
        "이미 그 언어였던 사람의 명시적 선택" 만 조용히 기록되지 않는다. 그
        뮤턴트는 리뷰 시점의 전체 스위트(정적 + e2e)를 통과했다 — 규칙이 주석
        으로만 존재했다는 뜻이다 (RL-004). 순서를 오프셋으로 못 박는다.
        """
        change = re.search(
            r'langSelect\.addEventListener\("change", \(ev\) => \{(.*?)\n    \}\);',
            viewer_html,
            re.S,
        )
        assert change is not None, "langSelect change 핸들러를 찾지 못했다"
        body = change.group(1)
        lock_at = body.find("langLocked = true;")
        return_at = body.find("next === currentLang) return;")
        assert lock_at != -1, body
        assert return_at != -1, body
        assert lock_at < return_at, (
            "langLocked must be set BEFORE the same-value early return, got "
            f"offsets {lock_at}/{return_at}: {body!r}"
        )

    def test_the_lock_is_not_persisted_across_sessions(self, viewer_html):
        """`localStorage` 로 잠금을 남기면 다음 세션의 다른 언어까지 따라온다."""
        assert "localStorage" not in viewer_html
        assert "sessionStorage" not in viewer_html

    def test_control_respects_the_lock_before_it_switches(self, viewer_html):
        body = _js_body(viewer_html, _HANDLE_CONTROL_BODY)
        lock_at = body.find("langLocked")
        switch_at = body.find("switchLanguage(")
        assert lock_at != -1, f"handleControl must honour langLocked: {body!r}"
        assert switch_at != -1, f"handleControl must be able to switch: {body!r}"
        assert (
            lock_at < switch_at
        ), f"the langLocked guard must precede the switch: {body!r}"

    def test_control_respects_the_ended_state(self, viewer_html):
        """종료 상태의 뷰어는 재구독하지 않는다.

        범위 밖인 RL-022(`session_end` 가 종료 플래그를 세우지 않는 문제)를
        여기서 고치지는 않되, **새로 붙는 이 리스너**는 실제 종료 상태를 읽고
        스스로 물러난다.
        """
        body = _js_body(viewer_html, _HANDLE_CONTROL_BODY)
        ended_at = body.find("stateNodes.ended")
        switch_at = body.find("switchLanguage(")
        assert ended_at != -1, f"handleControl must check the ended state: {body!r}"
        assert ended_at < switch_at, body

    def test_control_moves_the_dropdown_with_the_subscription(self, viewer_html):
        """구독과 UI 가 갈라지지 않는다 — 표시값도 함께 움직인다."""
        body = _js_body(viewer_html, _HANDLE_CONTROL_BODY)
        assert "langSelect.value = next;" in body, body

    def test_an_unsupported_or_repeated_language_is_a_noop(self, viewer_html):
        body = _js_body(viewer_html, _HANDLE_CONTROL_BODY)
        assert "next === currentLang" in body, body
        assert "includes(next)" in body, body

    def test_switch_language_measures_follow_before_it_writes(self, viewer_html):
        """재구독 경로도 `_lockLine()` 과 같은 순서 규칙을 지킨다.

        스택을 비우고 대기 문구를 다시 그리는 것은 큰 DOM 쓰기다. 쓴 뒤에
        `isUserAtBottom()` 을 물으면 방금 바뀐 높이가 gap 으로 잡혀 크레딧 롤이
        영구히 꺼진다 — 되돌릴 계기가 없는 결함이다.
        """
        body = _js_body(viewer_html, _SWITCH_LANGUAGE_BODY)
        measured = body.find("isUserAtBottom()")
        cleared = body.find("clearCaptions()")
        assert measured != -1, f"switchLanguage must measure follow: {body!r}"
        assert cleared != -1, f"switchLanguage must clear the stack: {body!r}"
        assert measured < cleared, (
            "switchLanguage must call isUserAtBottom() BEFORE it clears the "
            f"caption stack, got offsets {measured}/{cleared}: {body!r}"
        )
        assert (
            "if (follow) _scrollToBottom();" in body
        ), f"switchLanguage must scroll on the pre-measured flag: {body!r}"
        assert "_scrollIfBottom()" not in body, body

    def test_switch_language_runs_the_fixed_order(self, viewer_html):
        body = _js_body(viewer_html, _SWITCH_LANGUAGE_BODY)
        cleared = body.find("clearCaptions()")
        opened = body.find("connect(next)")
        waiting = body.find("applyWaitingText(next)")
        assert cleared < opened < waiting, (
            "switchLanguage must run clear -> connect -> waiting-text, got "
            f"offsets {cleared}/{opened}/{waiting}: {body!r}"
        )

    def test_the_old_stream_closes_before_the_new_one_opens(self, viewer_html):
        body = _js_body(viewer_html, _VIEWER_CONNECT_BODY)
        closed = body.find("closeStream()")
        opened = body.find("new EventSource(")
        assert closed != -1, f"connect must close any live stream first: {body!r}"
        assert (
            closed < opened
        ), f"connect must close before it opens, got {closed}/{opened}: {body!r}"

    def test_clear_captions_resets_the_whole_typewriter_tuple(self, viewer_html):
        """RL-022 — 핸들 하나만 비우고 나머지를 무장한 채 두지 않는다."""
        body = _js_body(viewer_html, _CLEAR_CAPTIONS_BODY)
        for statement in (
            "_twStop();",
            "currentLine = null;",
            'twTarget = "";',
            "twShown = 0;",
            "twFinalize = false;",
        ):
            assert (
                statement in body
            ), f"`{statement}` missing from clearCaptions: {body!r}"
        stop = body.find("_twStop();")
        cleared = body.find("replaceChildren()")
        assert stop < cleared, (
            "the timer must stop before the nodes it writes to are removed, got "
            f"offsets {stop}/{cleared}: {body!r}"
        )

    def test_the_dropdown_and_control_share_one_switch_path(self, viewer_html):
        """재구독 함수는 하나다 — 두 경로가 갈라지면 규칙도 갈라진다 (RL-001)."""
        callers = re.findall(r"(?<!function )\bswitchLanguage\(([^)]*)\)", viewer_html)
        assert sorted(callers) == ["next", "next"], (
            f"switchLanguage is called with {callers}; exactly two call sites "
            "are allowed — the dropdown handler and the control listener"
        )


class TestViewerCaptionScale:
    def test_root_declares_the_scale_default(self, viewer_html):
        block = _rule_block(viewer_html, ":root")
        match = re.search(r"--caption-scale:\s*([^;]+);", block)
        assert match is not None, f":root must declare --caption-scale: {block!r}"
        assert match.group(1).strip() == "1", match.group(1)

    def test_the_scale_multiplies_only_the_caption_line(self, viewer_html):
        line = _rule_block(viewer_html, ".caption-line")
        assert "var(--caption-scale)" in line, line
        assert re.search(
            r"font-size:\s*calc\(clamp\(.*?\)\s*\*\s*var\(--caption-scale\)\)", line
        ), line

        for selector in (".caption-empty", ".state-message", ".conn-error"):
            block = _rule_block(viewer_html, selector)
            assert (
                "--caption-scale" not in block
            ), f"{selector} must not scale with the caption slider: {block!r}"

    def test_caption_typography_survives_the_scale(self, viewer_html):
        block = _rule_block(viewer_html, ".caption-line")
        assert "word-break: keep-all" in block, block
        assert "overflow-wrap: break-word" in block, block

    def test_the_client_clamps_the_scale_to_the_declared_range(self, viewer_html):
        assert "const CAPTION_SCALE_MIN = 0.8;" in viewer_html
        assert "const CAPTION_SCALE_MAX = 1.6;" in viewer_html
        body = _js_body(viewer_html, _APPLY_SCALE_BODY)
        assert "Math.min(CAPTION_SCALE_MAX" in body, body
        assert "Math.max(CAPTION_SCALE_MIN" in body, body
        assert 'typeof value !== "number"' in body, body
        assert "isFinite(value)" in body, body

    def test_the_caption_line_stays_aa_at_the_lower_bound(self, viewer_html):
        """AC — 배율 0.8 에서도 4.5:1 이상 (`tests/wcag.py` 공용 헬퍼 사용)."""
        canvas = _hex_rgb("#0b0b0c")
        ratio = _contrast_ratio(_rule_rgba(viewer_html, ".caption-line"), canvas)
        assert (
            ratio >= 4.5
        ), f".caption-line is {ratio:.2f}:1 at any scale; WCAG AA needs 4.5:1"

    def test_the_sub_one_lower_bound_carries_its_rationale(self, viewer_html):
        block = _rule_block(viewer_html, ":root")
        assert "4.5:1" in block, (
            "the --caption-scale declaration must record why a sub-1.0 lower "
            f"bound is safe: {block!r}"
        )
