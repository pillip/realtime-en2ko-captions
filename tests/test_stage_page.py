"""
무대 합성 페이지 (`components/stage.html`) + `/stage/{room_id}` 라우트 테스트 (ISSUE-40).

검증 대상 (test_plan.md Gap 8 의 TC-057 ~ TC-059, TC-061 일부):
- `sse_broadcast._json_for_script` : 인라인 ``<script>`` 안에 JSON 리터럴을
  주입할 때 ``</script>`` 브레이크아웃이 불가능한지 (RL-016 / ISSUE-37 리뷰 F-2).
- `stage.html` 정적 마크업: 16:9 레터박스 CSS, dvh 페어링(RL-011), 세로 margin
  부재(RL-012), 프레젠터 키보드 비간섭(NFR-025), 로고 degrade 경로(RL-008).
- aiohttp `/stage/{room_id}` 핸들러: 404 / closed / caption_ratio → 폭 매핑 /
  깨진 stage_config → 기본값 / 이스케이프.

외부 네트워크 호출 없이 aiohttp TestClient 만 사용 (test_viewer_page.py 패턴).
"""

from __future__ import annotations

import json
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

_STAGE_TEMPLATE = Path(__file__).resolve().parent.parent / "components" / "stage.html"

# 브레이크아웃 페이로드 — 이 문자열이 응답 본문에 원본 그대로 나타나면
# 인라인 <script> 블록이 조기 종료된 것이다.
_BREAKOUT_TITLE = "</script><script>alert(1)</script>"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
class _StubRoomRepo:
    """Minimal fake of database.Room for endpoint tests."""

    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)


def _room(**overrides: Any) -> dict[str, Any]:
    """Room row shaped like `database.Room.get_by_id` returns it.

    ``stage_config`` 는 DB 컬럼과 동일하게 **원시 JSON 문자열** 이다 —
    핸들러가 normalize_stage_config 를 거치는지 확인하기 위함.
    """
    row = {
        "id": "room-1",
        "name": "Conference Hall A",
        "status": "active",
        "primary_output_lang": "ko",
        "output_langs": '["ko","en"]',
        "stage_config": "{}",
    }
    row.update(overrides)
    return row


def _stage_config(**overrides: Any) -> str:
    cfg = {
        "event_title": "",
        "event_subtitle": "",
        "caption_ratio": "1/4",
        "logo_groups": [
            {"label": "주최", "assets": []},
            {"label": "주관", "assets": []},
            {"label": "후원", "assets": []},
        ],
    }
    cfg.update(overrides)
    return json.dumps(cfg, ensure_ascii=False)


async def _get(repo: Any, path: str) -> tuple[int, str, str]:
    """GET `path` against a fresh app; return (status, body, content_type)."""
    from aiohttp.test_utils import TestClient, TestServer

    from sse_broadcast import BroadcastManager, build_sse_app

    app = build_sse_app(broadcast_manager=BroadcastManager(), room_repo=repo)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(path)
        return resp.status, await resp.text(), resp.headers.get("Content-Type", "")


# ---------------------------------------------------------------------------
# _json_for_script — the RL-016 hand-off (ISSUE-37 리뷰 F-2)
# ---------------------------------------------------------------------------
class TestJsonForScript:
    """인라인 ``<script>`` 안전 직렬화 헬퍼.

    `json.dumps` 는 ``<`` 도 ``/`` 도 이스케이프하지 않으므로 그대로 쓰면
    ``</script>`` 로 스크립트 블록을 탈출할 수 있다. 헬퍼는 이를 막으면서도
    **여전히 valid JSON** 이어야 한다 (round-trip 으로 검증).
    """

    def test_escapes_script_terminator(self):
        from sse_broadcast import _json_for_script

        out = _json_for_script({"event_title": _BREAKOUT_TITLE})
        assert "</script>" not in out
        assert "<" not in out and ">" not in out
        assert "\\u003c/script\\u003e" in out

    def test_escapes_ampersand(self):
        from sse_broadcast import _json_for_script

        out = _json_for_script({"t": "A홀 & B홀"})
        assert "&" not in out
        assert "\\u0026" in out

    def test_round_trips_through_json_loads(self):
        """이스케이프해도 데이터는 손실 없이 그대로 복원된다."""
        from sse_broadcast import _json_for_script

        payload = {
            "event_title": _BREAKOUT_TITLE,
            "event_subtitle": "a & b > c < d",
            "logo_groups": [{"label": "주최", "assets": ["logo.png"]}],
        }
        assert json.loads(_json_for_script(payload)) == payload

    def test_preserves_non_ascii(self):
        """ensure_ascii=False — 한글이 \\uXXXX 로 뭉개지지 않는다."""
        from sse_broadcast import _json_for_script

        out = _json_for_script({"event_title": "2026 개발자 콘퍼런스"})
        assert "2026 개발자 콘퍼런스" in out

    def test_escapes_js_line_separators(self):
        """U+2028/U+2029 는 JSON 에서는 합법이지만 JS 파서를 깨뜨릴 수 있다."""
        from sse_broadcast import _json_for_script

        out = _json_for_script({"t": "a\u2028b\u2029c"})
        assert "\u2028" not in out and "\u2029" not in out
        assert json.loads(out) == {"t": "a\u2028b\u2029c"}


# ---------------------------------------------------------------------------
# Static markup — components/stage.html
# ---------------------------------------------------------------------------
class TestStageHtmlMarkup:
    """무대 페이지 마크업 정적 검증 (test_viewer_page.py / test_fullscreen.py 스타일).

    AC ↔ Test mapping (issues.md ISSUE-40 § Tests):
      - "aspect-ratio / object-fit: contain / 100dvh 존재" → test_letterbox_css_present,
        test_every_vh_height_has_dvh_fallback
    """

    @pytest.fixture
    def stage_html(self) -> str:
        assert _STAGE_TEMPLATE.exists(), f"stage.html missing: {_STAGE_TEMPLATE}"
        return _STAGE_TEMPLATE.read_text(encoding="utf-8")

    def test_letterbox_css_present(self, stage_html):
        """16:9 박스 + contain — 잘림/늘어남 없이 레터박스 (AC 3)."""
        assert "aspect-ratio: 16 / 9" in stage_html
        assert "object-fit: contain" in stage_html

    def test_every_vh_height_has_dvh_fallback(self, stage_html):
        """RL-011: `height: 100vh;` 바로 다음 줄에 `height: 100dvh;`."""
        lines = [line.strip() for line in stage_html.splitlines()]
        vh_lines = [i for i, line in enumerate(lines) if line == "height: 100vh;"]
        assert vh_lines, "full-height rules must exist on the stage page"
        for i in vh_lines:
            nxt = lines[i + 1]
            assert nxt == "height: 100dvh;", f"line {i + 2} needs the dvh fallback"

    def test_no_vertical_margin_on_full_height_elements(self, stage_html):
        """RL-012: 세로 margin 대신 padding 을 쓴다."""
        assert "margin: 0;" in stage_html
        assert "margin-top" not in stage_html
        assert "margin-bottom" not in stage_html

    def test_left_column_grid_rows(self, stage_html):
        """헤더 / 발표 영역 / 로고 바 3행 + Grid 자식 오버플로 방지."""
        assert "grid-template-rows: auto minmax(0, 1fr) auto;" in stage_html
        assert "min-height: 0;" in stage_html

    def test_caption_column_width_comes_from_css_variable(self, stage_html):
        """2열 Grid 의 우측 폭은 서버가 주입한 CSS 변수로 결정된다 (AC 2)."""
        assert "grid-template-columns: 1fr var(--caption-width);" in stage_html
        assert "--caption-width" in stage_html
        assert 'setProperty("--caption-width"' in stage_html

    def test_template_does_not_hardcode_ratio_widths(self, stage_html):
        """폭 매핑은 서버(Python)에만 존재한다.

        템플릿이 두 값을 모두 갖고 있으면 라우트 테스트가 룸 설정을 구분하지
        못한다 (RL-004: 항상 통과하는 약한 단언 방지).
        """
        assert "25%" not in stage_html
        assert "33.333%" not in stage_html

    def test_all_placeholders_present(self, stage_html):
        for placeholder in (
            "{{ROOM_ID}}",
            "{{ROOM_NAME}}",
            "{{OUTPUT_LANGS_JSON}}",
            "{{PRIMARY_LANG}}",
            "{{INITIAL_STATE}}",
            "{{STAGE_CONFIG_JSON}}",
        ):
            assert placeholder in stage_html, f"{placeholder} missing from stage.html"

    def test_no_presenter_keyboard_interference(self, stage_html):
        """NFR-025 회귀 가드 — 이게 깨지면 행사장에서 프레젠터 리모컨이 죽는다."""
        assert "keydown" not in stage_html
        assert "keyup" not in stage_html
        assert "preventDefault" not in stage_html
        assert "requestFullscreen" not in stage_html
        assert ".focus()" not in stage_html

    def test_no_interactive_controls(self, stage_html):
        """ISSUE-40 무대 페이지에는 조작 UI 가 없다 (캡처 버튼은 ISSUE-42)."""
        assert "<button" not in stage_html
        assert "<select" not in stage_html
        assert "<input" not in stage_html

    def test_logo_images_degrade_and_carry_alt(self, stage_html):
        """RL-008: 없는 에셋은 해당 이미지만 제거. RL-010: alt 는 그룹 라벨 기반."""
        assert 'addEventListener("error"' in stage_html
        assert "/branding/" in stage_html
        assert "img.alt" in stage_html

    def test_state_copy_matches_ux_spec(self, stage_html):
        assert "잠시 후 시작됩니다" in stage_html
        assert "세션이 종료되었습니다" in stage_html

    def test_caption_typography_for_narrow_column(self, stage_html):
        """좁은 컬럼 타이포 (ux_spec: Stage Composite View § 자막 컬럼)."""
        assert "clamp(20px, 1.4vw + 8px, 32px)" in stage_html
        assert "word-break: keep-all;" in stage_html
        assert "overflow-wrap: anywhere;" in stage_html

    def test_lang_attribute_and_viewport_meta(self, stage_html):
        assert 'lang="ko"' in stage_html
        assert 'name="viewport"' in stage_html


# ---------------------------------------------------------------------------
# /stage/{room_id} HTTP handler
# ---------------------------------------------------------------------------
class TestStageRouteHandler:
    """aiohttp `/stage/{room_id}` 핸들러 동작 검증.

    AC ↔ Test mapping (issues.md ISSUE-40 § AC):
      1. 없는 룸 → 404 친화 페이지          → test_unknown_room_returns_friendly_404
      2. caption_ratio → 25% / 33.333%      → test_quarter_ratio_*, test_third_ratio_*
      4. 빈 event_title → 룸 이름 폴백      → test_empty_event_title_falls_back_to_room_name
      5. 로고 전무 → 빈 그룹만 주입         → test_empty_logo_groups_inject_no_assets
      6. `<script>` 이스케이프              → test_room_name_script_tag_is_escaped
      7. `</script>` 브레이크아웃 차단      → test_event_title_cannot_close_script_block
      8. closed 룸 → initial_state=closed   → test_closed_room_bootstraps_closed_state
      9. 깨진 stage_config → 기본값 렌더    → test_malformed_stage_config_renders_defaults
    """

    async def test_unknown_room_returns_friendly_404(self):
        status, body, _ = await _get(_StubRoomRepo({}), "/stage/no-such-room")
        assert status == 404
        assert "룸을 찾을 수 없습니다" in body
        assert "Traceback" not in body
        assert "sqlite" not in body.lower()

    async def test_repo_exception_returns_generic_404(self):
        """RL-006: 내부 예외 문자열이 청중에게 노출되지 않는다."""

        class ExplodingRepo:
            def get_by_id(self, room_id):
                raise RuntimeError("DB internal: /private/data/secrets.db locked")

        status, body, _ = await _get(ExplodingRepo(), "/stage/anything")
        assert status == 404
        assert "secrets.db" not in body
        assert "RuntimeError" not in body
        assert "룸을 찾을 수 없습니다" in body

    async def test_missing_template_returns_friendly_404(self, monkeypatch):
        """템플릿을 읽지 못해도 내부 경로/예외가 아니라 친화 페이지가 나간다 (RL-006)."""
        import sse_broadcast

        monkeypatch.setattr(
            sse_broadcast,
            "_STAGE_TEMPLATE_PATH",
            Path("/nonexistent/private/stage-template.html"),
        )
        repo = _StubRoomRepo({"room-1": _room()})
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 404
        assert "룸을 찾을 수 없습니다" in body
        assert "nonexistent" not in body
        assert "FileNotFoundError" not in body

    async def test_known_room_returns_html(self):
        repo = _StubRoomRepo({"room-1": _room()})
        status, body, ctype = await _get(repo, "/stage/room-1")
        assert status == 200
        assert ctype.startswith("text/html")
        assert "Conference Hall A" in body
        assert "room-1" in body

    async def test_quarter_ratio_renders_25_percent(self):
        repo = _StubRoomRepo(
            {"room-1": _room(stage_config=_stage_config(caption_ratio="1/4"))}
        )
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert "25%" in body
        assert "33.333%" not in body

    async def test_third_ratio_renders_33_percent(self):
        repo = _StubRoomRepo(
            {"room-1": _room(stage_config=_stage_config(caption_ratio="1/3"))}
        )
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert "33.333%" in body
        assert "25%" not in body

    @pytest.mark.parametrize("raw", ["{", "", None, "[]", '{"caption_ratio": "1/2"}'])
    async def test_malformed_stage_config_renders_defaults(self, raw):
        """AC 9 — 깨진 blob 은 500 이 아니라 기본값으로 degrade 한다."""
        repo = _StubRoomRepo({"room-1": _room(stage_config=raw)})
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert "25%" in body
        assert '"caption_ratio": "1/4"' in body

    async def test_missing_stage_config_column_renders_defaults(self):
        """구 스키마 룸 (컬럼 자체가 없음) 도 렌더된다."""
        row = _room()
        del row["stage_config"]
        status, body, _ = await _get(_StubRoomRepo({"room-1": row}), "/stage/room-1")
        assert status == 200
        assert "25%" in body

    async def test_empty_event_title_falls_back_to_room_name(self):
        """AC 4 — 헤더 바가 룸 이름을 대신 표시한다 (서버 렌더 폴백)."""
        repo = _StubRoomRepo(
            {"room-1": _room(stage_config=_stage_config(event_title=""))}
        )
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert 'id="event-title">Conference Hall A</h1>' in body
        assert '"event_title": ""' in body

    async def test_event_title_is_injected_when_set(self):
        repo = _StubRoomRepo(
            {
                "room-1": _room(
                    stage_config=_stage_config(
                        event_title="2026 개발자 콘퍼런스",
                        event_subtitle="A홀 기조연설",
                    )
                )
            }
        )
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert '"event_title": "2026 개발자 콘퍼런스"' in body
        assert '"event_subtitle": "A홀 기조연설"' in body

    async def test_logo_group_assets_are_injected(self):
        repo = _StubRoomRepo(
            {
                "room-1": _room(
                    stage_config=_stage_config(
                        logo_groups=[
                            {"label": "주최", "assets": ["host-1.png"]},
                            {"label": "후원", "assets": ["sponsor-a.svg"]},
                        ]
                    )
                )
            }
        )
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert "host-1.png" in body
        assert "sponsor-a.svg" in body
        # 정규화가 3그룹 고정 순서를 유지한다.
        assert body.index("주최") < body.index("주관") < body.index("후원")

    async def test_empty_logo_groups_inject_no_assets(self):
        """AC 5 — 자산이 하나도 없으면 어떤 파일명도 주입되지 않는다."""
        repo = _StubRoomRepo({"room-1": _room(stage_config=_stage_config())})
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert '"assets": []' in body
        assert ".png" not in body
        assert ".svg" not in body

    async def test_room_name_script_tag_is_escaped(self):
        """AC 6 — 마크업 컨텍스트는 HTML 이스케이프된다."""
        repo = _StubRoomRepo({"room-1": _room(name="<script>alert(1)</script>")})
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert "&lt;script&gt;" in body
        assert "<script>alert(1)</script>" not in body

    async def test_event_title_script_tag_is_escaped(self):
        """AC 6 — 스크립트 컨텍스트는 \\uXXXX 이스케이프된다."""
        repo = _StubRoomRepo(
            {
                "room-1": _room(
                    stage_config=_stage_config(event_title="<script>alert(1)</script>")
                )
            }
        )
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert "\\u003cscript\\u003ealert(1)" in body
        assert "<script>alert(1)</script>" not in body

    async def test_event_title_cannot_close_script_block(self):
        """AC 7 (RL-016) — `</script>` 페이로드가 인라인 블록을 조기 종료하지 못한다.

        `json.dumps` 만 쓰면 이 테스트는 실패한다 (`</script>` 가 그대로 나감).
        """
        repo = _StubRoomRepo(
            {"room-1": _room(stage_config=_stage_config(event_title=_BREAKOUT_TITLE))}
        )
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        # 1) 원본 브레이크아웃 시퀀스가 본문에 없다.
        assert "</script><script>" not in body
        assert _BREAKOUT_TITLE not in body
        # 2) 이스케이프된 형태로는 살아 있다 (데이터 손실 없음).
        assert "\\u003c/script\\u003e\\u003cscript\\u003ealert(1)" in body
        # 3) 페이로드가 스크립트 종료 태그를 하나도 추가하지 못했다.
        template = _STAGE_TEMPLATE.read_text(encoding="utf-8")
        assert body.count("</script>") == template.count("</script>")

    async def test_room_name_cannot_close_script_block(self):
        """룸 이름 경로도 같은 보증을 받는다 (마크업 컨텍스트)."""
        repo = _StubRoomRepo({"room-1": _room(name=_BREAKOUT_TITLE)})
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert "</script><script>" not in body
        template = _STAGE_TEMPLATE.read_text(encoding="utf-8")
        assert body.count("</script>") == template.count("</script>")

    async def test_closed_room_bootstraps_closed_state(self):
        """AC 8 — 종료 룸은 closed 로 부트스트랩되고 종료 카피를 갖고 있다."""
        repo = _StubRoomRepo({"room-1": _room(status="closed")})
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert 'initial_state: "closed"' in body
        assert "세션이 종료되었습니다" in body

    async def test_waiting_room_bootstraps_waiting_state(self):
        repo = _StubRoomRepo({"room-1": _room(status="waiting")})
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert 'initial_state: "waiting"' in body

    async def test_lang_query_fixes_caption_language(self):
        """자막 언어는 조작 UI 가 아니라 ?lang= 로만 지정된다."""
        repo = _StubRoomRepo({"room-1": _room()})
        status, body, _ = await _get(repo, "/stage/room-1?lang=en")
        assert status == 200
        assert 'caption_lang: "en"' in body

    async def test_unsupported_lang_query_falls_back_to_primary(self):
        """신뢰 경계 검증: 지원 목록 밖 코드는 룸 기본 언어로 되돌린다."""
        repo = _StubRoomRepo({"room-1": _room()})
        status, body, _ = await _get(repo, "/stage/room-1?lang=<b>zz")
        assert status == 200
        assert 'caption_lang: "ko"' in body
        assert "zz" not in body
