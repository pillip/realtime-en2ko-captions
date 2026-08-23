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
import re
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

# 페이지가 칠하는 두 배경. 대비 계산은 합성 후 색으로 한다 (RL-018).
_CANVAS_RGB = (11, 11, 12)  # --canvas: #0b0b0c
_FRAME_RGB = (0, 0, 0)  # .stage-frame / .title-card 배경


# ---------------------------------------------------------------------------
# WCAG 대비 계산 (RL-018) — 알파를 눈대중으로 고르지 않기 위해 수치로 검증한다.
# ---------------------------------------------------------------------------
def _srgb_to_linear(channel: float) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = (_srgb_to_linear(round(c)) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _composite(rgba: tuple[float, float, float, float], backdrop) -> tuple:
    *rgb, alpha = rgba
    return tuple(
        alpha * c + (1 - alpha) * b for c, b in zip(rgb, backdrop, strict=True)
    )


def _contrast_ratio(rgba: tuple[float, float, float, float], backdrop) -> float:
    """알파 합성 후의 전경색과 배경색 사이 WCAG 2.1 명도 대비."""
    fg = _relative_luminance(_composite(rgba, backdrop))
    bg = _relative_luminance(backdrop)
    hi, lo = max(fg, bg), min(fg, bg)
    return (hi + 0.05) / (lo + 0.05)


def _rule_block(css: str, selector: str) -> str:
    """`selector { ... }` 규칙 본문만 잘라낸다.

    파일 전체 substring 매칭은 다른 규칙이 같은 선언을 갖고 있으면 통과해
    버린다 (RL-004). 단언을 규칙 블록으로 한정하기 위한 헬퍼.
    """
    pattern = rf"(?m)^\s*{re.escape(selector)}\s*\{{(.*?)\}}"
    match = re.search(pattern, css, re.S)
    assert match is not None, f"{selector} rule not found in stage.html"
    return match.group(1)


def _rule_rgba(css: str, selector: str) -> tuple[float, float, float, float]:
    block = _rule_block(css, selector)
    match = re.search(
        r"color:\s*rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)", block
    )
    assert match is not None, f"{selector} has no rgba() color declaration"
    r, g, b, a = match.groups()
    return (int(r), int(g), int(b), float(a))


def _vertical_margin_components(longhand: str, value: str) -> list[str]:
    """`margin` 선언에서 세로(top/bottom) 성분만 뽑는다 (RL-012 가드용)."""
    parts = value.split()
    if longhand:  # margin-top / margin-bottom
        return parts[:1]
    if len(parts) == 1:  # margin: a
        return parts
    if len(parts) == 2:  # margin: v h
        return parts[:1]
    return [parts[0], parts[2]]  # margin: t h b  /  t r b l


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
        """16:9 박스 + contain — 잘림/늘어남 없이 레터박스 (AC 3).

        `object-fit: contain` 은 파일 안에 3번 등장하므로(로고 규칙 2개 포함)
        전체 substring 매칭은 `.capture-video` 가 선언을 잃어도 통과한다
        (RL-004). AC 가 말하는 요소로 단언을 한정한다.
        """
        assert "aspect-ratio: 16 / 9" in _rule_block(stage_html, ".stage-frame")
        assert "object-fit: contain" in _rule_block(stage_html, ".capture-video")

    def test_stage_main_declares_an_explicit_column_track(self, stage_html):
        """좌측 컬럼의 암시적 `auto` 트랙은 max-content 로 자란다 (RL-017).

        `grid-template-columns` 를 선언하지 않으면 긴 행사 타이틀/룸 이름이
        트랙을 밀어내 발표 영역이 자막 컬럼 위에 그려진다. 컨테이너의
        `min-width: 0` 은 트랙을 제약하지 못하므로 트랙을 직접 잡아야 한다.
        """
        block = _rule_block(stage_html, ".stage-main")
        assert "grid-template-columns: minmax(0, 1fr);" in block
        assert "grid-template-rows: auto minmax(0, 1fr) auto;" in block

    def test_every_vh_height_has_dvh_fallback(self, stage_html):
        """RL-011: `height: 100vh;` 바로 다음 줄에 `height: 100dvh;`."""
        lines = [line.strip() for line in stage_html.splitlines()]
        vh_lines = [i for i, line in enumerate(lines) if line == "height: 100vh;"]
        assert vh_lines, "full-height rules must exist on the stage page"
        for i in vh_lines:
            nxt = lines[i + 1]
            assert nxt == "height: 100dvh;", f"line {i + 2} needs the dvh fallback"

    def test_no_vertical_margin_on_full_height_elements(self, stage_html):
        """RL-012: 세로 margin 대신 padding 을 쓴다.

        longhand 두 개만 막으면 `margin: 12px 0` 이 그대로 통과해 PR #58 이
        고쳤던 버그가 다시 들어온다. shorthand 의 top/bottom 성분까지 검사한다.
        """
        assert "margin: 0;" in stage_html
        declarations = re.findall(r"\bmargin(-top|-bottom)?\s*:\s*([^;}]+)", stage_html)
        assert declarations, "stage.html must declare margin: 0 somewhere"
        for longhand, value in declarations:
            for component in _vertical_margin_components(longhand, value.strip()):
                assert component == "0", (
                    f"vertical margin '{component}' in "
                    f"`margin{longhand}: {value.strip()}` — use padding (RL-012)"
                )

    def test_left_column_grid_rows(self, stage_html):
        """헤더 / 발표 영역 / 로고 바 3행 + Grid 자식 오버플로 방지.

        `min-height: 0;` 은 파일에 4번 등장하므로 발표 영역 규칙으로 한정한다
        (RL-004).
        """
        main = _rule_block(stage_html, ".stage-main")
        assert "grid-template-rows: auto minmax(0, 1fr) auto;" in main
        assert "min-height: 0;" in _rule_block(stage_html, ".presentation")

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

    def test_logo_filename_never_reaches_the_dom_as_markup(self, stage_html):
        """RL-016 두 번째 링크 — 파일명은 검증되지 않은 채 `<img src>` 로 간다.

        `stage_config._as_logo_groups` 가 경로 검증을 ISSUE-38 에 넘겼으므로,
        렌더러 쪽에서 지켜야 할 불변식은 두 가지다: (1) 파일명을 마크업
        문자열로 조립하지 않는다, (2) URL 컨텍스트이므로 퍼센트 인코딩한다.
        """
        assert "encodeURIComponent(filename)" in stage_html
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            assert sink not in stage_html, f"{sink} makes the filename markup"

    @pytest.mark.parametrize(
        "selector,backdrop",
        [
            (".event-subtitle", _CANVAS_RGB),
            (".title-card-subtitle", _FRAME_RGB),
            (".logo-group-label", _CANVAS_RGB),
            (".caption-line", _CANVAS_RGB),
            (".caption-empty", _CANVAS_RGB),
            (".caption-ended", _CANVAS_RGB),
        ],
    )
    def test_dim_text_meets_wcag_aa_contrast(self, stage_html, selector, backdrop):
        """WCAG 1.4.3 — 어두운 캔버스 위 흐린 텍스트도 4.5:1 이상 (RL-018).

        `clamp()` 하한이 전부 24px 미만이라(자막 20px, 라벨 11px, 빈 상태 14px)
        large-text 3:1 완화를 쓸 수 없다. `#0b0b0c` 위에서 4.5:1 을 넘기는
        흰색 알파 하한은 0.45 다 — 알파를 다른 페이지에서 복사해 오면 이
        테스트가 막는다.
        """
        ratio = _contrast_ratio(_rule_rgba(stage_html, selector), backdrop)
        assert ratio >= 4.5, f"{selector} is {ratio:.2f}:1, WCAG AA needs 4.5:1"

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
# Caption column behaviour — components/stage.html (ISSUE-41)
# ---------------------------------------------------------------------------
class TestStageCaptionColumn:
    """자막 컬럼의 SSE 구독 + 타자기 스무딩 정적 계약 (ISSUE-41).

    AC ↔ Test mapping (issues.md ISSUE-41 § Tests, test_plan TC-061):
      - "requestAnimationFrame 존재 / setInterval 부재"
          → test_typewriter_runs_on_request_animation_frame
      - "MAX_LINES 값이 60"
          → test_caption_dom_cap_is_sixty
      - "프레임당 공개량 상한"
          → test_per_frame_reveal_is_capped
      - "overflow-wrap / word-break 가 자막 라인 규칙에 존재"
          → test_caption_line_wrapping_rules_live_in_the_caption_line_rule
      - "애니메이션 노드가 라이브 리전이 아니다" (RL-019)
          → test_animated_node_is_not_the_live_region
      - "?lang= 로 고정된 언어로 /stream 을 구독"
          → test_stream_subscription_uses_the_bootstrapped_caption_lang
      - "conn-error 배너는 자막 컬럼 하단에만"
          → test_conn_error_banner_lives_inside_the_caption_column
    """

    @pytest.fixture
    def stage_html(self) -> str:
        assert _STAGE_TEMPLATE.exists(), f"stage.html missing: {_STAGE_TEMPLATE}"
        return _STAGE_TEMPLATE.read_text(encoding="utf-8")

    def test_typewriter_runs_on_request_animation_frame(self, stage_html):
        """TC-061 / NFR-025 — 자막 애니메이션은 rAF 로만 돈다.

        무대 페이지에서는 캡처 `<video>` 프레임 렌더와 자막 애니메이션이 같은
        메인 스레드를 공유한다. `setInterval` 은 브라우저 렌더 스케줄과
        무관하게 깨어나므로 캡처가 버벅인다 — viewer.html:444 의
        `setInterval(..., 28)` 을 그대로 이식하면 이 테스트가 막는다.
        """
        assert "requestAnimationFrame(" in stage_html
        assert "cancelAnimationFrame(" in stage_html
        assert "setInterval" not in stage_html, (
            "setInterval-based typewriter timer competes with capture frame "
            "rendering — use requestAnimationFrame (NFR-025)"
        )

    def test_caption_dom_cap_is_sixty(self, stage_html):
        """좁은 컬럼의 DOM 상한은 60 (viewer 의 200 이 아니다)."""
        match = re.search(r"const MAX_LINES = (\d+)", stage_html)
        assert match is not None, "stage.html must declare `const MAX_LINES = 60`"
        value = int(match.group(1))
        assert value == 60, f"MAX_LINES is {value}, the stage column caps at 60"

    def test_per_frame_reveal_is_capped(self, stage_html):
        """프레임 예산 — 큰 Bedrock 청크가 한 프레임을 통째로 잡아먹지 않는다.

        남은 글자수 비례 공개(`Math.ceil(gap / 6)`)만 쓰면 2000자 청크가
        한 프레임에 334자를 그린다. 비례 항을 상한으로 감싸야 한다.
        """
        match = re.search(r"MAX_REVEAL_PER_FRAME = (\d+)", stage_html)
        assert match is not None, (
            "stage.html must declare `const MAX_REVEAL_PER_FRAME = <n>` "
            "to bound the per-frame reveal"
        )
        value = int(match.group(1))
        assert value > 0, f"MAX_REVEAL_PER_FRAME is {value}, must be positive"

        capped = re.search(
            r"Math\.min\(\s*MAX_REVEAL_PER_FRAME\s*,\s*"
            r"Math\.max\(\s*2\s*,\s*Math\.ceil\(gap / 6\)\s*\)\s*\)",
            stage_html,
        )
        detail = (
            "the proportional term Math.ceil(gap / 6) must be wrapped by "
            "Math.min(MAX_REVEAL_PER_FRAME, ...) — an uncapped step blows the "
            "frame budget on a large chunk"
        )
        assert capped is not None, detail

    def test_caption_line_wrapping_rules_live_in_the_caption_line_rule(
        self, stage_html
    ):
        """좁은 컬럼 타이포는 `.caption-line` 규칙이 소유한다 (RL-004).

        기존 ISSUE-40 테스트는 파일 전체 substring 매칭이라 `body` 규칙이
        `word-break: keep-all` 을 갖고 있는 한 `.caption-line` 이 선언을 잃어도
        통과한다. 단언을 AC 가 말하는 규칙 블록으로 한정한다.
        """
        block = _rule_block(stage_html, ".caption-line")
        for declaration in (
            "font-size: clamp(20px, 1.4vw + 8px, 32px);",
            "line-height: 1.45;",
            "word-break: keep-all;",
            "overflow-wrap: anywhere;",
        ):
            assert declaration in block, f"`{declaration}` missing from .caption-line"

    def test_animated_node_is_not_the_live_region(self, stage_html):
        """RL-019 회귀 가드 — 프레임마다 바뀌는 노드는 라이브 리전이 아니다.

        ISSUE-40 은 "레이아웃 셸" 범위였음에도 `#caption-container`
        (stage.html:398) 에 `aria-live="polite"` 를 붙인 채 출하했다. 그런데
        이 이슈는 바로 그 노드에 `.caption-line` 을 append 하고, 마지막 라인의
        `textContent` 를 매 `requestAnimationFrame` 마다 바꾸며, `MAX_LINES`
        로 앞쪽 자식을 잘라낸다 — 속성을 그대로 두면 모든 공개 프레임과 모든
        트리밍이 라이브 리전 변경이 되어 스크린리더가 텍스트로 범람한다.

        따라서 소유권은 이 이슈에 있다: 애니메이션 노드는 `aria-hidden="true"`
        가 되고, 라인 확정 시에만 쓰이는 별도의 `#caption-announcer` 가
        `aria-live` 를 갖는다. 속성을 걷어내는 작업은 리뷰에서 "a11y 후퇴" 로
        보여 조용히 누락되기 쉬우므로 이 테스트가 유일한 자동 방어선이다.
        """
        animated = re.search(r"<div[^>]*\bid=\"caption-container\"[^>]*>", stage_html)
        assert animated is not None, "#caption-container open tag not found"
        animated_tag = animated.group(0)
        flooded = (
            "#caption-container still carries aria-live — the typewriter mutates "
            f"this node every frame: {animated_tag}"
        )
        assert "aria-live" not in animated_tag, flooded
        assert 'aria-hidden="true"' in animated_tag, (
            "#caption-container must be aria-hidden so per-frame reveals are not "
            f"announced: {animated_tag}"
        )

        announcer = re.search(
            r"<[a-zA-Z]+[^>]*\bid=\"caption-announcer\"[^>]*>", stage_html
        )
        assert announcer is not None, (
            "no #caption-announcer element — the live region has no owner once "
            "#caption-container is aria-hidden (RL-019)"
        )
        announcer_tag = announcer.group(0)
        assert 'aria-live="polite"' in announcer_tag, announcer_tag
        assert 'aria-atomic="true"' in announcer_tag, announcer_tag
        distinct = "the announcer must not be the animated container itself"
        assert announcer_tag != animated_tag, distinct

    def test_hidden_tab_stops_the_typewriter_loop(self, stage_html):
        """숨김 탭에서 rAF 가 멈추면 복귀 시 자막이 몰아친다 — 스냅 후 정지."""
        assert 'addEventListener("visibilitychange"' in stage_html
        assert "document.hidden" in stage_html

    def test_conn_error_banner_lives_inside_the_caption_column(self, stage_html):
        """AC — 연결 배너는 자막 컬럼 하단에만 뜨고 발표 영역을 덮지 않는다."""
        aside_start = stage_html.index('<aside class="caption-column"')
        aside_end = stage_html.index("</aside>", aside_start)
        caption_column = stage_html[aside_start:aside_end]
        assert 'id="conn-error"' in caption_column, (
            "#conn-error must live inside <aside class='caption-column'> so it "
            "cannot cover the presentation area"
        )

        main_start = stage_html.index('<main class="stage-main">')
        main_end = stage_html.index("</main>", main_start)
        stage_main = stage_html[main_start:main_end]
        assert 'id="conn-error"' not in stage_main

    def test_conn_error_banner_meets_wcag_aa_contrast(self, stage_html):
        """RL-018 — 반투명 pill 위 텍스트는 **합성 후** 배경으로 계산한다.

        배너 텍스트의 실제 배경은 캔버스가 아니라 캔버스 위에 깔린 반투명
        pill 이다. viewer.html 의 알파를 눈대중으로 옮겨오면 이 테스트가 막는다.
        """
        block = _rule_block(stage_html, ".conn-error")
        bg = re.search(
            r"background:\s*rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)", block
        )
        assert bg is not None, ".conn-error needs an rgba() background declaration"
        r, g, b, a = bg.groups()
        pill = _composite((int(r), int(g), int(b), float(a)), _CANVAS_RGB)

        ratio = _contrast_ratio(_rule_rgba(stage_html, ".conn-error"), pill)
        assert ratio >= 4.5, f".conn-error text is {ratio:.2f}:1, WCAG AA needs 4.5:1"

    def test_stream_subscription_uses_the_bootstrapped_caption_lang(self, stage_html):
        """AC — 언어는 조작 UI 가 아니라 서버가 확정한 `caption_lang` 으로 고정.

        서버(`_handle_stage`)가 `?lang=` 을 검증해 `caption_lang` 으로 내려주므로
        클라이언트는 그 값을 그대로 쓴다 — 브라우저에서 다시 파싱하면 서버가
        거부한 코드가 되살아난다.
        """
        assert "new EventSource(" in stage_html
        assert '"/stream/"' in stage_html
        assert "encodeURIComponent(CONFIG.room_id)" in stage_html
        assert "CONFIG.caption_lang" in stage_html

    def test_waiting_copy_is_localised_per_caption_lang(self, stage_html):
        """대기 문구는 viewer.html 의 `WAITING_MSG` 맵을 재사용한다."""
        match = re.search(r"WAITING_MSG\s*=\s*\{(.*?)\}", stage_html, re.S)
        assert match is not None, "stage.html must declare a WAITING_MSG map"
        entries = match.group(1)
        assert re.search(r"\bko:\s*\"잠시 후 시작됩니다\"", entries), entries
        assert re.search(r"\ben:\s*\"Starting shortly\"", entries), entries

    def test_no_user_scroll_override_controls(self, stage_html):
        """무대 화면에는 조작자가 없다 — 되돌리기 어포던스를 두지 않는다.

        viewer.html 의 `isUserAtBottom()` 분기를 그대로 이식하면 관객이
        만질 수 없는 화면에서 자동 스크롤이 영구히 멈출 수 있다.
        """
        assert "실시간으로" not in stage_html
        assert "isUserAtBottom" not in stage_html


# ---------------------------------------------------------------------------
# /stage/{room_id} HTTP handler
# ---------------------------------------------------------------------------
class TestStageRouteHandler:
    """aiohttp `/stage/{room_id}` 핸들러 동작 검증.

    AC ↔ Test mapping (issues.md ISSUE-40 § AC):
      1. 없는 룸 → 404 친화 페이지          → test_unknown_room_returns_friendly_404
      2. caption_ratio → 25% / 33.333%      → test_quarter_ratio_*, test_third_ratio_*
      4. 빈 event_title → 룸 이름 폴백      → test_empty_event_title_gives_the_client_*
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

    async def test_empty_event_title_gives_the_client_a_room_name_fallback(self):
        """AC 4 — 빈 타이틀이면 `<h1>` 의 룸 이름이 그대로 남는다.

        `{{ROOM_NAME}}` 존재만 단언하면 event_title 유무와 무관하게 늘 통과한다
        (RL-004). 폴백을 성립시키는 세 조건을 모두 확인한다:
        (1) `<h1>` 이 룸 이름을 담고 있고, (2) 주입된 event_title 이 비어 있어
        클라이언트의 `||` 가 그 이름으로 떨어지며, (3) 폴백 표현식이 실제로
        페이지에 살아 있다. 렌더 결과(높이 유지 포함)는 e2e 가 맡는다.
        """
        repo = _StubRoomRepo(
            {"room-1": _room(stage_config=_stage_config(event_title=""))}
        )
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert 'id="event-title">Conference Hall A</h1>' in body
        assert '"event_title": ""' in body
        assert "cfg.event_title || headerTitle.textContent.trim()" in body

    async def test_set_event_title_overrides_the_room_name_client_side(self):
        """위 폴백 테스트의 대조군 — 타이틀이 있으면 그 값이 주입된다."""
        repo = _StubRoomRepo(
            {"room-1": _room(stage_config=_stage_config(event_title="기조연설"))}
        )
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert 'id="event-title">Conference Hall A</h1>' in body
        assert '"event_title": "기조연설"' in body

    @pytest.mark.parametrize(
        "hostile_name", ["{{STAGE_CONFIG_JSON}}", "{{OUTPUT_LANGS_JSON}}"]
    )
    async def test_room_name_cannot_expand_another_placeholder(self, hostile_name):
        """RL-021 — 룸 이름이 다른 플레이스홀더로 팽창하지 않는다.

        연쇄 `str.replace` 는 앞 단계가 써 넣은 값을 뒤 단계가 다시 본다.
        룸 이름은 운영자가 자유롭게 쓰는 텍스트이므로 단일 패스 치환이어야
        한다.
        """
        repo = _StubRoomRepo({"room-1": _room(name=hostile_name)})
        status, body, _ = await _get(repo, "/stage/room-1")
        assert status == 200
        assert f'id="event-title">{hostile_name}</h1>' in body
        assert f"<title>{hostile_name} — 무대 화면</title>" in body

    async def test_script_scalars_survive_backslashes_and_quotes(self):
        """RL-020 — `<script>` 안 스칼라는 JS 리터럴로 직렬화된다.

        `html.escape` 는 `\\` 를 건드리지 않아 백슬래시로 끝나는 값이 닫는
        따옴표를 탈출시킨다 — 부트스트랩 전체가 SyntaxError 로 죽는다.
        `"` 는 `&quot;` 로 바뀌어 값 자체가 조용히 손상된다.
        """
        room_id = 'q"b\\'
        repo = _StubRoomRepo({room_id: _room(id=room_id)})
        status, body, _ = await _get(repo, "/stage/q%22b%5C")
        assert status == 200
        assert "&quot;" not in body  # 스크립트 컨텍스트에 HTML 엔티티 금지
        literal = re.search(r"^\s*room_id: (.*),$", body, re.M)
        assert literal is not None, "room_id literal missing from the bootstrap"
        assert json.loads(literal.group(1)) == room_id

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
