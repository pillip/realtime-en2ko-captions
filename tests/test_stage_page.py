"""
무대 합성 페이지 (`components/stage.html`) + `/stage/{room_id}` 라우트 테스트
(ISSUE-40 레이아웃 셸, ISSUE-41 자막 컬럼).

검증 대상 (test_plan.md Gap 8 의 TC-057 ~ TC-059, TC-061 일부):
- `sse_broadcast._json_for_script` : 인라인 ``<script>`` 안에 JSON 리터럴을
  주입할 때 ``</script>`` 브레이크아웃이 불가능한지 (RL-016 / ISSUE-37 리뷰 F-2).
- `stage.html` 정적 마크업: 16:9 레터박스 CSS, dvh 페어링(RL-011), 세로 margin
  부재(RL-012), 프레젠터 키보드 비간섭(NFR-025), 로고 degrade 경로(RL-008).
- `stage.html` 자막 컬럼 (ISSUE-41): rAF 타자기, `MAX_LINES`, 좁은 컬럼 타이포,
  라이브 리전 소유권(RL-019), 배너 위치와 합성 대비(RL-018).
- `stage.html` 화면 캡처 (ISSUE-42, TC-060 / TC-061): getDisplayMedia 제약,
  클릭 표면 최소화, 트랙 수명주기, Wake Lock capability guard(RL-008),
  `?debug=1` 진단 오버레이의 유일한 keydown 등록.
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

# 대비 계산기는 `tests/wcag.py` 공용 모듈에 있다 (RL-001). ISSUE-45 가
# viewer.html 대비 테스트를 추가하며 여기서 뽑아 갔다 — 두 페이지가 서로 다른
# 계산기를 갖게 되는 것이 애초에 이 결함군을 만든 드리프트다.
from tests.wcag import (
    _composite,
    _contrast_ratio,
    _rule_block,
    _rule_hex,
    _rule_rgba,
)

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

# ISSUE-42 가 출하하는 `<button>` 의 전부. 개수와 id 를 한 곳에 못 박아 두고
# `test_no_interactive_controls` 가 그것과 대조한다 — 무대 화면에 클릭 표면이
# 하나 늘어나는 것은 곧 무대 창이 OS 포커스를 뺏길 경로가 하나 늘어나는 것이다.
_EXPECTED_BUTTON_IDS = ("capture-connect",)

# `?debug=1` 진단 오버레이의 진입 가드. keydown 리스너가 **이 한 줄 뒤에만**
# 등록된다는 사실이 NFR-025 정적 가드의 핵심이다.
_DEBUG_GUARD_LINE = (
    'if (new URLSearchParams(location.search).get("debug") !== "1") return;'
)


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
        """NFR-025 회귀 가드 — 이게 깨지면 행사장에서 프레젠터 리모컨이 죽는다.

        TC-060. ISSUE-40 은 파일 전체에서 `keydown` 이라는 **문자열**을 금지했다.
        `docs/requirements.md` NFR-025 가 `?debug=1` 의 `{ passive: true }` 계수기
        하나를 명시적으로 승인하므로, 규격을 지킨 ISSUE-42 구현은 그 어휘적 금지를
        필연적으로 깨뜨린다. 그래서 **삭제하지 않고 더 좁은 구조적 단언으로 강화**
        한다 — 등록이 정확히 1건이고, 그 1건이 `?debug=1` 가드 블록 안에 있으며,
        `{ passive: true }` 로 등록되고, 기본 동작 취소/전파 중단 호출이 파일 전체에
        0건이라는 것까지 센다. 개수(`== 1` / `== 0`)로 단언하는 이유는 RL-004 다 —
        `in` / `not in` 은 두 번째 리스너가 들어와도 통과한다.

        `requestFullscreen` 과 `.focus()` 금지는 이 이슈를 지나도 의도가 그대로이므로
        **문구 그대로** 남긴다: 둘 다 무대 창으로 OS 포커스를 되돌리는 호출이다.

        행동 기반 짝: `tests/e2e/test_stage_capture_e2e.py::TestPresenterKeys`.
        """
        # (1) 포커스/전체화면 자동 강탈 경로 — ISSUE-40 문구 그대로 유지한다.
        assert "requestFullscreen" not in stage_html
        assert ".focus()" not in stage_html

        # (2) keydown 등록은 정확히 1건이다.
        registrations = list(re.finditer(r'addEventListener\(\s*"keydown"', stage_html))
        assert len(registrations) == 1, (
            f"stage.html registers {len(registrations)} keydown listener(s); "
            "NFR-025 allows exactly one — the ?debug=1 rehearsal counter. Any "
            "other listener means the stage window can eat a presenter remote key"
        )

        # (3) 그 1건은 `?debug=1` 가드 블록 **안**에 있다.
        guard = re.search(
            r"function setupDebugOverlay\(\) \{(.*?)\n    \}", stage_html, re.S
        )
        assert guard is not None, "setupDebugOverlay() not found in stage.html"
        body = guard.group(1)
        first_line = body.strip().splitlines()[0].strip()
        assert first_line == _DEBUG_GUARD_LINE, (
            "setupDebugOverlay() must bail out on the query flag as its very "
            f"first statement, got: {first_line!r}"
        )
        start, end = guard.start(1), guard.end(1)
        assert start < registrations[0].start() < end, (
            "the keydown registration sits outside the ?debug=1 guard block — "
            "it would run on the live event screen"
        )

        # (4) 그 1건은 `{ passive: true }` 로 등록되고 가드 블록을 벗어나지 않는다.
        passive = re.compile(
            r'addEventListener\(\s*"keydown",.*?\{\s*passive:\s*true\s*\}\s*\)',
            re.S,
        ).match(stage_html, registrations[0].start())
        assert passive is not None, (
            "the ?debug=1 keydown counter is not registered with "
            "{ passive: true } — the browser must be told it never cancels"
        )
        assert passive.end() <= end, "the keydown registration escapes the guard"

        # (5) 취소/전파중단 호출과 keyup 은 파일 전체에서 0건이다. 0건이면
        #     "keydown 문맥에 취소 호출이 없다" 가 자명하게 성립한다.
        for banned in ("preventDefault", "stopPropagation", "stopImmediatePropagation"):
            hits = len(re.findall(rf"\b{banned}\b", stage_html))
            assert hits == 0, (
                f"{banned} appears {hits} time(s) in stage.html — the stage page "
                "must never cancel a key the presenter remote emitted (NFR-025)"
            )
        assert len(re.findall(r"\bkeyup\b", stage_html)) == 0
        inline = len(
            re.findall(r"\bonkeydown\b|\bonkeyup\b|\bonkeypress\b", stage_html)
        )
        assert inline == 0, f"{inline} inline key handler attribute(s) in stage.html"

    def test_no_interactive_controls(self, stage_html):
        """무대 화면의 클릭 표면은 ISSUE-42 의 캡처 버튼 하나뿐이다.

        TC-060. ISSUE-40 의 `"<button" not in stage_html` 은 이 이슈의 필수 요소인
        "발표자료 연결" 버튼이 첫날 깨뜨린다. 삭제하지 않고 **개수와 id 를 못 박는**
        형태로 강화한다 — `<button>` 이 하나라도 더 들어오거나 링크/폼 요소가
        생기면 여기서 걸린다. 존재 단언(`not in`)이 아니라 개수 단언인 이유는
        RL-004 다.

        브라우저는 다른 앱에 키를 주입할 수 없다. 실제 불변식은 "무대 창이 OS
        포커스를 쥐지 않는다" 이고, 보이는 클릭 표면 하나하나가 그 전제를 깨뜨릴
        경로다 (NFR-025). 연결 후 이 버튼이 실제로 사라지는지는 e2e 가 본다.
        """
        assert len(re.findall(r"<select\b", stage_html)) == 0
        assert len(re.findall(r"<input\b", stage_html)) == 0
        assert len(re.findall(r"<textarea\b", stage_html)) == 0
        assert len(re.findall(r"<a\s+href", stage_html)) == 0

        buttons = re.findall(r"<button\b[^>]*>", stage_html)
        assert len(buttons) == len(_EXPECTED_BUTTON_IDS), (
            f"stage.html ships {len(buttons)} <button> element(s), ISSUE-42 allows "
            f"exactly {len(_EXPECTED_BUTTON_IDS)} "
            f"({', '.join(_EXPECTED_BUTTON_IDS)}). Every extra clickable surface is "
            f"another route for the stage window to steal OS focus: {buttons}"
        )
        for tag, expected_id in zip(buttons, _EXPECTED_BUTTON_IDS, strict=True):
            has_id = f'id="{expected_id}"' in tag
            assert has_id, f"expected the button id {expected_id!r}, got: {tag}"
            typed = 'type="button"' in tag
            assert typed, f"{tag} lacks type=button — a bare button defaults to submit"

        # 커서를 되돌리는 유일한 복귀 경로는 코너 더블클릭 제스처다. 보이는
        # 어포던스를 두면 그 자체가 상시 클릭 표면이 된다.
        assert 'id="reselect-zone"' in stage_html
        assert 'addEventListener("dblclick"' in stage_html

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
            # ISSUE-42 가 추가한 캡처 상태들. 배경은 전부 **불투명**이라
            # 임의의 캡처 영상 위에서도 대비가 결정적이다.
            # .capture-hint 의 backdrop 이 _FRAME_RGB 가 아닌 이유는
            # test_capture_surfaces_declare_an_opaque_background 를 볼 것 —
            # 이 문구는 재생 중인 <video> **위**에 뜨므로 조상의 #000000 은
            # 실제 배경이 아니다.
            (".capture-hint", _CANVAS_RGB),
            (".capture-warning", _CANVAS_RGB),
            (".handoff-prompt", _CANVAS_RGB),
            (".debug-overlay", _CANVAS_RGB),
        ],
    )
    def test_dim_text_meets_wcag_aa_contrast(self, stage_html, selector, backdrop):
        """WCAG 1.4.3 — 어두운 캔버스 위 흐린 텍스트도 4.5:1 이상 (RL-018).

        `clamp()` 하한이 전부 24px 미만이라(자막 20px, 라벨 11px, 빈 상태 14px)
        large-text 3:1 완화를 쓸 수 없다. ISSUE-42 가 추가한 네 상태도 마찬가지로
        1920px 뷰포트에서 resolve 된 크기가 전부 24px 미만이다 — 눈대중 대신
        수치로 확인한다. `#0b0b0c` 위에서 4.5:1 을 넘기는 흰색 알파 하한은
        0.45 다.
        """
        ratio = _contrast_ratio(_rule_rgba(stage_html, selector), backdrop)
        assert ratio >= 4.5, f"{selector} is {ratio:.2f}:1, WCAG AA needs 4.5:1"

    @pytest.mark.parametrize(
        "selector",
        [".capture-hint", ".capture-warning", ".handoff-prompt", ".debug-overlay"],
    )
    def test_capture_surfaces_declare_an_opaque_background(self, stage_html, selector):
        """캡처 상태 문구는 **자기 배경**을 갖는다 — 조상 배경에 기대지 않는다.

        위 대비 테스트가 통과한다고 해서 화면에서 읽힌다는 보장이 없다. 그 계산은
        backdrop 을 상수로 **가정**하는데, 이 네 문구는 재생 중인 `<video>` 위에
        뜰 수 있고 그 순간 조상의 `#000000` 은 화면에 없다. `.capture-hint` 가
        실제로 그랬다 — 배경 선언이 없어 흰 슬라이드 위에서 1.00:1 로 완전히
        사라졌는데(연결 후 코너 더블클릭 → 재선택 → 선택 취소 경로), 대비
        테스트는 `_FRAME_RGB` 를 가정했기 때문에 11.42:1 로 통과했다.

        그래서 backdrop 가정을 **구조적 사실**로 바꾼다: 네 규칙 모두 알파 1의
        배경을 직접 선언해야 한다. 그러면 위 테스트의 `_CANVAS_RGB` 가정이
        비로소 참이 된다 (RL-018 — 합성된 실제 배경으로 계산할 것).
        """
        block = _rule_block(stage_html, selector)
        match = re.search(r"background:\s*([^;]+);", block)
        assert match is not None, (
            f"{selector} declares no background; its contrast would be decided by "
            "whatever the capture <video> happens to be showing"
        )
        value = match.group(1).strip()
        assert not re.match(r"(?i)rgba?\(", value) or re.search(
            r",\s*1(?:\.0+)?\s*\)$", value
        ), f"{selector} background {value!r} is semi-transparent — alpha must be 1"
        blank = {"none", "transparent"}
        assert value.lower() not in blank, f"{selector} background is {value!r}"

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

    리뷰(PR #127)에서 보강한 가드:
      - 숨김 탭 분기가 비어 있지 않다 (F-5, RL-004)
          → test_hidden_tab_stops_the_typewriter_loop
      - 종료 후 늦은 message 를 막는 최종 상태 플래그 (F-1)
          → test_terminal_state_blocks_late_messages
      - 빈 final 이 빈 라인을 열지 않는다 (F-2)
          → test_empty_final_never_opens_a_blank_line
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
        """숨김 탭에서 rAF 가 멈추면 복귀 시 자막이 몰아친다 — 스냅 후 정지.

        RL-004: 리스너가 "존재한다" 는 단언은 빈 몸통
        (`if (document.hidden) { }`) 도 통과시킨다. 분기 **안** 을 본다.
        행동 검증은 e2e `test_hidden_tab_snaps_the_current_line_instead_of_queueing`.
        """
        assert 'addEventListener("visibilitychange"' in stage_html
        branch = re.search(
            r"if \(document\.hidden\) \{(.*?)\n      \} else \{(.*?)\n      \}",
            stage_html,
            re.S,
        )
        assert branch is not None, "no if (document.hidden) { … } else { … } branch"
        hidden_body, shown_body = branch.group(1), branch.group(2)
        stopped = f"hidden branch never stops the rAF loop: {hidden_body!r}"
        assert "_twStop()" in hidden_body, stopped
        snapped = (
            "hidden branch must snap the in-flight line to its full target, "
            f"otherwise the text avalanches on return: {hidden_body!r}"
        )
        assert "currentLine.textContent = twTarget" in hidden_body, snapped
        rearmed = f"returning to a visible tab never re-arms the loop: {shown_body!r}"
        assert "_twStart()" in shown_body, rearmed

    def test_terminal_state_blocks_late_messages(self, stage_html):
        """종료는 최종 상태다 — 늦게 도착한 message 가 컬럼을 되살리면 안 된다.

        `currentLine = null` 만으로는 message 진입점이 살아 있어서
        `setCaptionState("active")` → 새 라인 → rAF 재시작 → 라이브 리전 쓰기가
        모두 다시 일어난다. 도달 가능성은 필드가 아니라 진입점의 성질이다.
        행동 검증은 e2e `test_message_after_session_end_is_ignored`.
        """
        flagged = "session_end must set a terminal flag, not just clear currentLine"
        assert "sessionEnded = true;" in stage_html, flagged
        gated = "the message handler must early-return once the session has ended"
        assert "if (sessionEnded) return;" in stage_html, gated

    def test_empty_final_never_opens_a_blank_line(self, stage_html):
        """빈 final 이 대기 문구를 지우고 빈 컬럼을 남기면 안 된다.

        `_ensureCurrentLine()` 은 `#caption-empty` 를 제거하므로, 통과시키면
        무대 화면이 되돌릴 수 없는 빈 컬럼이 된다. 행동 검증은 e2e
        `test_empty_final_does_not_blank_the_column`.
        """
        body = re.search(
            r"function finalizeCaption\(text\) \{(.*?)\n    \}", stage_html, re.S
        )
        assert body is not None, "finalizeCaption() not found"
        bailed = (
            "finalizeCaption must bail out before _ensureCurrentLine() when there "
            f"is nothing to show: {body.group(1)!r}"
        )
        assert "if (!next) return;" in body.group(1), bailed

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
# 발표자료 화면 캡처 — components/stage.html (ISSUE-42)
# ---------------------------------------------------------------------------
class TestStageDisplayCapture:
    """getDisplayMedia 캡처의 정적 계약 (test_plan TC-060 / TC-061).

    AC ↔ Test mapping (issues.md ISSUE-42 § Tests):
      - "frameRate / ideal: 30 / selfBrowserSurface / object-fit: contain"
          → test_capture_constraints_are_pinned, test_letterbox_css_present
      - "cursor: none 과 surfaceSwitching 의 값이 exclude"
          → test_cursor_is_hidden_only_while_capture_is_live,
            test_capture_constraints_are_pinned
      - "navigator.wakeLock 접근이 존재 여부 가드 안에 있다" (RL-008)
          → test_wake_lock_sits_behind_a_capability_guard
      - "requestFullscreen() 자동 호출이 없다"
          → test_no_presenter_keyboard_interference (문구 그대로 유지)
      - 트랙 수명주기 (RL-009)
          → test_track_ended_is_registered_exactly_once
      - 내부 예외 문자열 미노출 (RL-006)
          → test_capture_failure_copy_is_a_literal_never_an_exception
      - 부드러움 (NFR-025)
          → test_no_gratuitous_compositing_hints,
            test_caption_column_is_paint_contained
    """

    @pytest.fixture
    def stage_html(self) -> str:
        assert _STAGE_TEMPLATE.exists(), f"stage.html missing: {_STAGE_TEMPLATE}"
        return _STAGE_TEMPLATE.read_text(encoding="utf-8")

    @pytest.fixture
    def constraints(self, stage_html) -> str:
        """`CAPTURE_CONSTRAINTS = { … }` 리터럴 본문만 잘라낸다.

        파일 전체 substring 매칭은 값이 다른 곳에 있어도 통과한다 (RL-004) —
        단언을 실제로 `getDisplayMedia` 에 넘어가는 객체로 한정한다.
        """
        match = re.search(
            r"const CAPTURE_CONSTRAINTS = \{(.*?)\n    \};", stage_html, re.S
        )
        assert match is not None, (
            "stage.html must declare `const CAPTURE_CONSTRAINTS = { … };` so the "
            "constraint object can be asserted as a unit"
        )
        return match.group(1)

    def test_capture_constraints_are_pinned(self, stage_html, constraints):
        """AC — 제약 객체에 프레임레이트·자기캡처·서피스 전환 정책이 모두 박혀 있다.

        `surfaceSwitching: "exclude"` 를 빼면 Chrome 이 "다른 탭 공유" 전환 위젯을
        무대 화면 위에 띄운다 — 클릭 표면이 하나 늘어나고, 그것을 클릭하는 순간
        무대 창이 포커스를 되찾는다 (NFR-025).
        """
        fps = re.search(r"frameRate:\s*\{\s*ideal:\s*30\s*\}", constraints)
        assert fps, constraints
        assert re.search(r'selfBrowserSurface:\s*"exclude"', constraints), constraints
        assert re.search(r'surfaceSwitching:\s*"exclude"', constraints), constraints
        assert re.search(r'systemAudio:\s*"exclude"', constraints), constraints
        assert re.search(r"audio:\s*false", constraints), constraints
        # 실제로 이 객체가 getDisplayMedia 로 간다 (선언만 하고 안 쓰면 무의미).
        assert "getDisplayMedia(CAPTURE_CONSTRAINTS)" in stage_html

    def test_get_display_media_sits_behind_a_capability_guard(self, stage_html):
        """RL-008 — 비보안 컨텍스트에서는 `navigator.mediaDevices` 자체가 없다."""
        assert 'typeof md.getDisplayMedia !== "function"' in stage_html, (
            "getDisplayMedia must be capability-checked; an undefined "
            "navigator.mediaDevices throws a TypeError and kills the page"
        )

    def test_cursor_is_hidden_only_while_capture_is_live(self, stage_html):
        """AC — 연결 중에는 커서가 없고, 컨트롤이 돌아오면 복원된다.

        `cursor: none` 이 무대 루트에 **항상** 걸려 있으면 연결 버튼을 누를 수
        없다. 두 규칙이 짝으로 존재해야 AC 가 성립한다.
        """
        assert "cursor: default;" in _rule_block(stage_html, ".stage")
        assert "cursor: none;" in _rule_block(stage_html, ".stage.capture-live")
        # 클래스 토글이 컨트롤 표시 여부와 같은 함수에서 일어난다 — 두 상태가
        # 어긋나면 커서만 사라진 채 버튼이 남는다.
        assert 'classList.toggle("capture-live"' in stage_html

    def test_controls_leave_the_click_surface_via_display_none(self, stage_html):
        """AC / NFR-025 — `pointer-events: none` 로는 창 포커스를 못 막는다.

        `pointer-events` 는 문서 안의 히트테스트만 바꿀 뿐, OS 가 그 창에 포커스를
        주는 것 자체는 막지 못한다. 컨트롤은 DOM 렌더 트리에서 빠져야 한다.
        """
        hidden_rule = _rule_block(stage_html, ".capture-controls[hidden]")
        assert "display: none;" in hidden_rule, hidden_rule
        controls = _rule_block(stage_html, ".capture-controls")
        assert "pointer-events" not in controls, (
            "the capture controls must be removed with display:none, not merely "
            f"made unclickable: {controls}"
        )

    def test_capture_button_keeps_a_focus_visible_ring(self, stage_html):
        """RL-010 — 클릭 표면을 줄이더라도 키보드 접근성은 해치지 않는다."""
        ring = _rule_block(stage_html, ".capture-button:focus-visible")
        assert "outline:" in ring, ring

    def test_capture_button_meets_wcag_aa_contrast(self, stage_html):
        """RL-018 — 버튼은 hex 불투명 색이므로 알파 합성 없이 직접 계산한다.

        `clamp(15px, 1vw, 22px)` 는 1920px 뷰포트에서 19.2px 로 resolve 되어
        large-text(24px) 완화를 쓸 수 없다 — 4.5:1 이 하한이다.
        """
        block = _rule_block(stage_html, ".capture-button")
        assert "font-size: clamp(15px, 1vw, 22px);" in block, block
        fg = _rule_hex(stage_html, ".capture-button", "color")
        bg = _rule_hex(stage_html, ".capture-button", "background")
        ratio = _contrast_ratio((*fg, 1.0), bg)
        assert ratio >= 4.5, f".capture-button is {ratio:.2f}:1, WCAG AA needs 4.5:1"

    def test_track_ended_is_registered_exactly_once(self, stage_html):
        """RL-009 — 벤더 프리픽스도, 중복 리스너도 두지 않는다.

        FR-075: 트랙이 끝나면 검은 화면이 아니라 타이틀 카드로 되돌아간다.
        """
        ended = re.findall(r'addEventListener\(\s*"ended"', stage_html)
        assert len(ended) == 1, f"{len(ended)} 'ended' listeners registered: {ended}"
        assert "getVideoTracks()[0]" in stage_html
        assert len(re.findall(r"\bonended\b", stage_html)) == 0
        assert len(re.findall(r"webkit[A-Z]", stage_html)) == 0
        # 재선택으로 트랙을 교체할 때 옛 트랙의 리스너를 먼저 떼지 않으면
        # 옛 트랙의 stop() 이 방금 붙인 새 캡처를 도로 철거한다.
        assert 'removeEventListener("ended", onCaptureEnded)' in stage_html

    def test_capture_ended_restores_the_title_card_and_reconnect_action(
        self, stage_html
    ):
        """FR-075 — 폴백 경로가 한 함수 안에서 끝난다 (검은 화면 금지)."""
        body = re.search(
            r"function onCaptureEnded\(\) \{(.*?)\n    \}", stage_html, re.S
        )
        assert body is not None, "onCaptureEnded() not found"
        handler = body.group(1)
        assert "srcObject = null" in handler, handler
        assert "captureVideo.hidden = true" in handler, handler
        assert "titleCard.hidden = false" in handler, handler
        assert "RECONNECT_LABEL" in handler, handler
        assert "_showControls(true)" in handler, handler
        assert "발표자료 다시 연결" in stage_html

    def test_self_capture_heuristic_does_not_terminate_the_stream(self, stage_html):
        """FR-081 — Chromium 밖에서는 `selfBrowserSurface` 가 무시된다.

        휴리스틱은 `displaySurface === "browser"` **그리고** 캡처 해상도가 이 창의
        크기와 일치할 때만 경고한다. `displaySurface` 만 보면 정상적인 다른 탭
        공유도 전부 경고가 뜬다. 그리고 캡처를 강제로 끊지 않는다.
        """
        body = re.search(
            r"function _detectSelfCapture\(track\) \{(.*?)\n    \}", stage_html, re.S
        )
        assert body is not None, "_detectSelfCapture() not found"
        detect = body.group(1)
        assert 'displaySurface !== "browser"' in detect, detect
        assert "innerWidth" in detect and "innerHeight" in detect, detect
        assert "track.stop()" not in detect, (
            "the self-capture banner must guide the operator to reselect, not "
            f"force-terminate the capture (FR-081): {detect}"
        )
        assert "무대 화면이 캡처되었습니다. 발표자료 창을 선택하세요" in stage_html

    def test_wake_lock_sits_behind_a_capability_guard(self, stage_html):
        """NFR-027 / RL-008 — 미지원 브라우저에서 예외 없이 degrade 한다."""
        body = re.search(
            r"async function requestWakeLock\(\) \{(.*?)\n    \}", stage_html, re.S
        )
        assert body is not None, "requestWakeLock() not found"
        wake = body.group(1)
        guard = wake.index('if (!("wakeLock" in navigator)) return;')
        request = wake.index('navigator.wakeLock.request("screen")')
        assert guard < request, (
            "navigator.wakeLock.request must be reached only after the capability "
            f"guard: {wake}"
        )
        guarded = "try {" in wake and "catch" in wake
        assert guarded, f"a rejected wake-lock request must degrade silently: {wake}"

    def test_wake_lock_reuses_the_existing_visibilitychange_listener(self, stage_html):
        """RL-009 — `visibilitychange` 리스너는 여전히 이 페이지에 하나뿐이다."""
        listeners = re.findall(r'addEventListener\(\s*"visibilitychange"', stage_html)
        assert len(listeners) == 1, (
            f"{len(listeners)} visibilitychange listeners — extend the existing "
            "one instead of adding a second (RL-009)"
        )
        branch = re.search(
            r"if \(document\.hidden\) \{.*?\n      \} else \{(.*?)\n      \}",
            stage_html,
            re.S,
        )
        assert branch is not None, "the visibilitychange else-branch was not found"
        shown = branch.group(1)
        assert 'document.visibilityState === "visible"' in shown, shown
        rerequested = "requestWakeLock()" in shown
        assert rerequested, f"the wake lock is dropped when the tab hides: {shown}"

    def test_capture_failure_copy_is_a_literal_never_an_exception(self, stage_html):
        """RL-006 — 내부 예외 문자열이 무대 화면(=관객 화면)에 뜨면 안 된다.

        `_setCaptureError` 가 받는 값은 이 파일 안의 상수뿐이어야 한다.
        """
        for leak in ("e.message", "e.name", "String(e)", "err.message", "e.stack"):
            assert leak not in stage_html, f"{leak} would leak an exception (RL-006)"
        assert "CAPTURE_FAIL_MSG" in stage_html
        calls = re.findall(r"_setCaptureError\(([^)]*)\)", stage_html)
        assert calls, "no _setCaptureError() call sites found"
        allowed = {'""', "CAPTURE_FAIL_MSG", "CAPTURE_UNSUPPORTED_MSG", "message"}
        for arg in calls:
            assert arg.strip() in allowed, (
                f"_setCaptureError({arg}) is not a literal from this file — only "
                f"{sorted(allowed)} may reach the DOM (RL-006)"
            )

    def test_no_gratuitous_compositing_hints(self, stage_html):
        """NFR-025 — 캡처 컨테이너에 불필요한 합성 레이어 힌트를 넣지 않는다."""
        hints = len(re.findall(r"will-change", stage_html))
        assert hints == 0, (
            f"{hints} will-change declaration(s) — promoting the capture container "
            "to its own layer costs VRAM and does not make capture smoother"
        )

    def test_caption_column_is_paint_contained(self, stage_html):
        """NFR-025 — 자막 리페인트가 발표 영역 리페인트를 유발하지 않는다."""
        block = _rule_block(stage_html, ".caption-column")
        assert "contain: layout paint;" in block, block

    def test_the_page_owns_exactly_one_live_region(self, stage_html):
        """RL-019 — 캡처 상태 문구가 두 번째 라이브 리전을 만들지 않는다.

        핸드오프 안내/경고 배너/진단 오버레이는 전부 평문이다. 무대 화면은
        스크린리더 사용자가 없는 프로젝터 출력이고, 라이브 리전을 하나 더 두면
        확정 자막을 읽는 `#caption-announcer` 와 경쟁한다.
        """
        live = re.findall(r'aria-live="([^"]*)"', stage_html)
        assert live == ["polite"], f"expected one aria-live region, found {live}"
        announcer = re.search(
            r"<[a-zA-Z]+[^>]*\bid=\"caption-announcer\"[^>]*>", stage_html
        )
        assert announcer is not None and 'aria-live="polite"' in announcer.group(0)
        # 진단 오버레이는 절대 라이브 리전이 아니다 — 키를 누를 때마다 낭독된다.
        overlay = re.search(
            r"function setupDebugOverlay\(\) \{(.*?)\n    \}", stage_html, re.S
        )
        assert overlay is not None
        for attr in ("aria-live", 'setAttribute("role"', "role="):
            assert attr not in overlay.group(1), overlay.group(1)

    def test_debug_overlay_reports_focus_and_counts_keys(self, stage_html):
        """AC — 리허설 판정 기준은 "발표 앱 포커스 + 리모컨 20회 → 카운터 0"."""
        assert "document.hasFocus()" in stage_html
        assert "이 창 포커스 있음" in stage_html
        assert "이 창 포커스 없음" in stage_html
        assert 'id="debug-key-count"' in stage_html or "debug-key-count" in stage_html
        # focus/blur 로 갱신된다 — 폴링 타이머를 두지 않는다.
        assert 'addEventListener("blur", onWindowBlur)' in stage_html
        assert 'addEventListener("focus", onWindowFocus)' in stage_html

    def test_handoff_prompt_is_one_shot(self, stage_html):
        """AC — 안내는 8초 뒤 또는 창이 포커스를 잃는 즉시 사라진다."""
        assert "발표 앱을 클릭해 포커스를 넘기세요" in stage_html
        match = re.search(r"const HANDOFF_MS = (\d+);", stage_html)
        assert match is not None, "stage.html must declare `const HANDOFF_MS = <n>;`"
        value = int(match.group(1))
        assert 4000 <= value <= 12000, f"HANDOFF_MS is {value}ms, the AC says ~8s"
        blur = re.search(r"function onWindowBlur\(\) \{(.*?)\n    \}", stage_html, re.S)
        assert blur is not None, "onWindowBlur() not found"
        dismissed = "_hideHandoff()" in blur.group(1)
        assert dismissed, "losing focus IS the hand-off succeeding — drop it at once"

    def test_focus_is_returned_to_the_document_body_after_capture_starts(
        self, stage_html
    ):
        """NFR-025 1차 방어선 — 권한 다이얼로그가 남긴 포커스를 되돌린다."""
        assert "document.activeElement?.blur();" in stage_html
        # 되찾는 방향의 호출은 어디에도 없다 (guard test 와 이중으로 막는다).
        assert "window.focus" not in stage_html

    def test_setinterval_stays_absent_and_line_cap_stays_sixty(self, stage_html):
        """ISSUE-41 의 프레임 예산 계약이 이 이슈에서 후퇴하지 않았다 (TC-061)."""
        assert "setInterval" not in stage_html
        assert "requestAnimationFrame(" in stage_html
        match = re.search(r"const MAX_LINES = (\d+)", stage_html)
        assert match is not None and int(match.group(1)) == 60


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
