"""
오퍼레이터 컴포넌트 부트스트랩의 script-context 이스케이프 파리티 (ISSUE-48).

`app.py` 는 `components/webrtc.html` 의 인라인 `<script>` 안에 있는
`const BOOT = {{BOOTSTRAP_JSON}};` 자리에 payload 를 꽂는다. 그 자리는 **raw
text** 라 `json.dumps` 만으로는 `</script>` 를 막지 못한다 — 무대(ISSUE-40)와
뷰어(ISSUE-44)가 이미 겪고 고친 결함이고, 남아 있던 세 번째 렌더러가
오퍼레이터 화면이다 (RL-016 / RL-020).

검증 대상:
- `operator_ui.render_component_html`: 단일 패스 치환 + 공유 이스케이프 헬퍼
- `script_escape.json_for_script`: 저장소 전체에서 **하나뿐인** 정의
- `app.py`: 템플릿 문자열에 대한 맨 `json.dumps` 가 남아 있지 않다

AC ↔ Test mapping (issues.md ISSUE-48 § Acceptance Criteria):
  - AC 3 → TestScriptContextEscaping::test_hostile_payload_round_trips_...
  - AC 4 → TestScriptContextEscaping::test_breakout_room_name_adds_no_script_tag
  - AC 5 → TestSingleEscaperDefinition (전부)
  - AC 6 → TestAppRenderPath (전부)
  - AC 7 → TestE2EFixtureUsesProduction
  (AC 1 / AC 2 는 tests/e2e/test_operator_bootstrap_escaping_e2e.py)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# operator_ui / script_escape 는 streamlit 의존이 없어야 하지만, 임포트 사슬을
# 따라가다 끌려 들어올 수 있으므로 다른 테스트와 동일하게 방어한다.
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
_WEBRTC_TEMPLATE = _REPO_ROOT / "components" / "webrtc.html"
_APP_PY = _REPO_ROOT / "app.py"

# PR #137 코드 리뷰 S-1 이 브라우저에서 실측한 페이로드. 주입 스크립트가
# 실행되는 동시에 `BOOT` 가 죽어 자막이 한 줄도 나오지 않았다.
_BREAKOUT_NAME = "A홀</script><script>window.__pwned=1;</script>"

# 이스케이퍼가 다뤄야 하는 문자를 한 문자열에 모은 것: `<` `>` `&` `"` `\`
# U+2028 U+2029 + 한글. `ensure_ascii=False` 라 한글은 원문 그대로 나간다.
_HOSTILE_TEXT = 'A홀 <&> "q" \\ \u2028 \u2029 </script>'

# script-context 이스케이프의 "정의"를 식별하는 패턴. `<` 를 치환하는 코드가
# 저장소에 두 벌 생기는 순간(RL-001, frequency 4) 이 그물에 걸린다.
_SUBSTITUTION_RE = re.compile(r'\.replace\(\s*"<"')

_SKIP_PARTS = {".venv", ".worktrees", "__pycache__", "tests", "node_modules"}


def _template() -> str:
    """치환 전 원본 템플릿 (개수 비교의 기준선)."""
    assert _WEBRTC_TEMPLATE.exists(), f"webrtc.html missing: {_WEBRTC_TEMPLATE}"
    return _WEBRTC_TEMPLATE.read_text(encoding="utf-8")


def _boot_literal(rendered: str) -> str:
    """렌더 결과에서 `const BOOT = …;` 리터럴을 원문 그대로 잘라낸다.

    JSON 리터럴 안에는 raw 개행이 존재할 수 없으므로(`\\n` 은 두 글자로
    이스케이프된다) 첫 `;\\n` 에서 자르면 값의 세미콜론을 삼키지 않는다.
    """
    match = re.search(r"const BOOT = (.*?);\n", rendered, re.S)
    assert match is not None, "BOOT literal missing from the rendered component"
    return match.group(1)


def _payload(**overrides: Any) -> dict[str, Any]:
    """프로덕션과 **같은 조립 경로**로 만든 부트스트랩 payload (RL-024).

    손으로 만든 dict 를 쓰면 `build_bootstrap_payload` 가 키를 하나 더
    늘려도 이 테스트는 그 필드를 영영 보지 못한다.
    """
    from operator_ui import build_bootstrap_payload

    payload = build_bootstrap_payload(
        action="idle",
        openai_session=None,
        websocket_port=8765,
        user_info={"id": 1, "username": "op1", "role": "operator"},
        room_id="room-42",
        room_name="A홀",
        view_url="http://localhost:8766/view/room-42",
        qr_data_url=None,
        display_mode="caption",
        stage_url=None,
    )
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# AC 3 / AC 4 — script-context 이스케이프
# ---------------------------------------------------------------------------
class TestScriptContextEscaping:
    def test_breakout_room_name_adds_no_script_tag(self):
        """AC 4 — payload 가 `</script>` 를 **하나도** 추가하지 못한다.

        개수를 원본 템플릿과 비교하는 것이 핵심이다. "`</script>` 가 없다" 는
        렌더가 통째로 실패해도 통과하는 공허한 단언이다 (RL-004).
        """
        from operator_ui import render_component_html

        template = _template()
        rendered = render_component_html(template, _payload(room_name=_BREAKOUT_NAME))

        assert rendered.count("</script>") == template.count("</script>")
        assert "</script><script>" not in rendered
        assert "<script>window.__pwned" not in rendered
        assert _BREAKOUT_NAME not in rendered

        # 실행 가능한 형태로는 사라지지만 데이터는 손실 없이 살아 있다.
        literal = _boot_literal(rendered)
        assert "\\u003c/script\\u003e" in literal
        assert json.loads(literal)["room_name"] == _BREAKOUT_NAME

    def test_hostile_payload_round_trips_through_the_boot_literal(self):
        """AC 3 — `<>&"\\` + U+2028/U+2029 + 한글이 원본 dict 로 복원된다.

        필드 하나만 찍어보지 않고 **dict 전체**를 비교한다 — 필드별로 갈라
        처리한 반쪽 구현이 여기서 걸린다 (RL-020).
        """
        from operator_ui import render_component_html

        payload = _payload(
            room_name=_HOSTILE_TEXT,
            view_url="http://localhost:8766/view/" + _HOSTILE_TEXT,
            user_info={"id": 1, "username": _HOSTILE_TEXT, "role": "operator"},
            openai_session={"client_secret": {"value": _HOSTILE_TEXT}},
            stage_url=_HOSTILE_TEXT,
        )
        rendered = render_component_html(_template(), payload)

        assert json.loads(_boot_literal(rendered)) == payload

    def test_boot_literal_is_byte_identical_to_the_shared_helper(self):
        """payload **전체**가 헬퍼를 한 번에 통과했음을 바이트로 못 박는다.

        "이스케이프됐다" 가 아니라 "그 헬퍼를 통과했다" 를 단언한다
        (tests/test_viewer_page.py 의 동명 테스트 미러). 필드별 처리나 두 번째
        이스케이퍼가 생기면 바이트가 어긋난다.
        """
        from operator_ui import render_component_html
        from script_escape import json_for_script

        payload = _payload(room_name=_HOSTILE_TEXT)
        rendered = render_component_html(_template(), payload)

        assert _boot_literal(rendered) == json_for_script(payload)

    def test_raw_breakout_characters_do_not_survive_in_the_literal(self):
        """이스케이프 결과에 raw `<` `>` `&` U+2028/U+2029 가 남지 않는다."""
        from operator_ui import render_component_html

        rendered = render_component_html(_template(), _payload(room_name=_HOSTILE_TEXT))
        literal = _boot_literal(rendered)
        for raw in ("<", ">", "&", "\u2028", "\u2029"):
            assert raw not in literal, f"{raw!r} survived un-escaped in {literal!r}"

    def test_benign_payload_still_bootstraps_expected_values(self):
        """대조군 — 정상 payload 의 값이 그대로 유지된다 (회귀 방지)."""
        from operator_ui import render_component_html

        payload = _payload()
        boot = json.loads(_boot_literal(render_component_html(_template(), payload)))

        assert boot["room_id"] == "room-42"
        assert boot["room_name"] == "A홀"
        assert boot["display_mode"] == "caption"
        assert boot["websocket_port"] == 8765

    def test_hangul_is_emitted_literally_not_as_escape_sequences(self):
        """`ensure_ascii=False` 계약 — 한글이 `\\uXXXX` 로 부풀지 않는다.

        뷰어/무대와 같은 규칙이라는 사실을 값으로 고정한다. 여기서 `True` 로
        되돌아가면 세 렌더러의 출력이 다시 갈라진다.
        """
        from operator_ui import render_component_html

        rendered = render_component_html(_template(), _payload(room_name="A홀"))
        assert '"A홀"' in _boot_literal(rendered)


# ---------------------------------------------------------------------------
# AC 5 — 이스케이프 구현은 저장소에 한 곳뿐이다 (RL-001)
# ---------------------------------------------------------------------------
class TestSingleEscaperDefinition:
    def test_exactly_one_module_defines_the_script_context_substitution(self):
        """AC 5 — `<` 치환의 정의 사이트가 정확히 하나다.

        복사본이 생기면 두 렌더러가 서로 다른 이스케이퍼를 갖게 되고, 그
        드리프트가 애초에 이 이슈를 만든 원인이다.
        """
        scanned: list[str] = []
        hits: list[tuple[str, int]] = []
        for path in sorted(_REPO_ROOT.rglob("*.py")):
            # 저장소 **내부** 경로만 본다. 절대경로로 걸러내면 워크트리 이름
            # 자체가 필터에 걸려 스캔 대상이 통째로 비는 공허한 통과가 된다.
            relative = path.relative_to(_REPO_ROOT)
            if _SKIP_PARTS & set(relative.parts):
                continue
            scanned.append(relative.as_posix())
            found = len(_SUBSTITUTION_RE.findall(path.read_text(encoding="utf-8")))
            if found:
                hits.append((relative.as_posix(), found))

        # RL-004 게이트 — 스캔 대상이 비어 있으면 위 단언은 아무것도 증명하지 못한다.
        assert "sse_broadcast.py" in scanned, f"scan skipped the repo: {scanned}"
        assert hits == [("script_escape.py", 1)], f"escaper definitions: {hits}"

    def test_sse_broadcast_reuses_the_shared_helper_object(self):
        """이름만 남기고 구현은 공유한다 — 동일 객체여야 한다.

        `is` 로 보는 이유: 우연히 같은 문자열을 뱉는 두 번째 구현은 값 비교로
        구분되지 않는다.
        """
        import script_escape
        import sse_broadcast

        assert sse_broadcast._json_for_script is script_escape.json_for_script

    def test_operator_ui_reuses_the_shared_helper_object(self):
        """오퍼레이터 렌더 경로도 **같은** 헬퍼를 import 한다."""
        import operator_ui
        import script_escape

        assert operator_ui.json_for_script is script_escape.json_for_script

    def test_both_renderers_share_one_placeholder_grammar(self):
        """플레이스홀더 문법도 한 정의다 — 두 벌이면 조용히 갈라진다."""
        import operator_ui
        import script_escape
        import sse_broadcast

        assert sse_broadcast._PLACEHOLDER_RE is script_escape.PLACEHOLDER_RE
        assert operator_ui.PLACEHOLDER_RE is script_escape.PLACEHOLDER_RE

    def test_shared_helper_matches_sse_broadcast_for_a_hostile_object(self):
        """헬퍼 파리티 — 뷰어/무대가 받던 것과 **같은** 출력이다."""
        from script_escape import json_for_script
        from sse_broadcast import _json_for_script

        obj = {
            "text": _HOSTILE_TEXT,
            "nested": {"name": _BREAKOUT_NAME},
            "list": [1, None, True, "<&>"],
        }
        out = json_for_script(obj)

        assert out == _json_for_script(obj)
        assert json.loads(out) == obj
        for raw in ("<", ">", "&", "\u2028", "\u2029"):
            assert raw not in out


# ---------------------------------------------------------------------------
# RL-021 — 단일 패스 치환
# ---------------------------------------------------------------------------
class TestSinglePassSubstitution:
    def test_placeholder_shaped_room_name_is_not_re_expanded(self):
        """룸 이름이 `{{BOOTSTRAP_JSON}}` 자체여도 팽창하지 않는다.

        오늘 `webrtc.html` 의 플레이스홀더는 하나뿐이라 연쇄 치환 위험이
        실재하지 않는다. 그래도 단일 패스로 못 박아 두는 이유는, 두 번째
        플레이스홀더가 추가되는 순간 이 성질이 **구조적 사실**이어야 하기
        때문이다 (RL-021).
        """
        from operator_ui import render_component_html

        template = _template()
        token = "{{BOOTSTRAP_JSON}}"
        rendered = render_component_html(template, _payload(room_name=token))

        assert template.count(token) == 1
        assert json.loads(_boot_literal(rendered))["room_name"] == token
        # 템플릿의 슬롯은 소진되고, 남는 하나는 payload 자신의 리터럴뿐이다.
        assert rendered.count(token) == 1

    def test_substitution_is_single_pass_re_sub(self):
        """구현 형태 자체를 단언한다 — 연쇄 `.replace` 로 되돌아가지 못한다."""
        import inspect

        from operator_ui import render_component_html

        body = inspect.getsource(render_component_html).split('"""')[-1]
        assert "PLACEHOLDER_RE.sub(" in body
        assert "lambda" in body
        assert ".replace(" not in body

    def test_every_template_placeholder_is_mapped(self):
        """템플릿의 플레이스홀더 전부가 치환된다 (엄격 치환의 대가)."""
        from operator_ui import render_component_html
        from script_escape import PLACEHOLDER_RE

        template = _template()
        assert set(PLACEHOLDER_RE.findall(template)) == {"BOOTSTRAP_JSON"}

        rendered = render_component_html(template, _payload())
        assert PLACEHOLDER_RE.findall(rendered) == []

    def test_unknown_placeholder_fails_loudly(self):
        """오타 난 플레이스홀더는 페이지로 새지 않고 `KeyError` 로 죽는다.

        `app.py` 의 렌더는 `try/except` → `st.error("시스템을 로드할 수
        없습니다.")` 안에 있으므로 오퍼레이터에게는 내부 텍스트가 아니라
        일반 문구가 간다 (RL-006).
        """
        from operator_ui import render_component_html

        with pytest.raises(KeyError):
            render_component_html("<p>{{UNKNOWN}}</p>", _payload())


# ---------------------------------------------------------------------------
# AC 6 — app.py 에는 로직이 남지 않는다 (RL-005)
# ---------------------------------------------------------------------------
class TestAppRenderPath:
    def test_app_no_longer_dumps_json_into_the_template(self):
        """AC 6 — 템플릿 문자열에 대한 맨 `json.dumps` 호출이 없다."""
        source = _APP_PY.read_text(encoding="utf-8")

        assert "json.dumps" not in source
        assert "{{BOOTSTRAP_JSON}}" not in source
        assert "render_component_html(" in source

    def test_app_drops_the_now_unused_json_import(self):
        """`import json` 은 그 한 줄에서만 쓰였다 — 남기면 죽은 import 다."""
        source = _APP_PY.read_text(encoding="utf-8")

        assert "\nimport json\n" not in source

    def test_render_helper_is_importable_without_streamlit(self):
        """렌더 경로가 순수 함수라 단위 테스트 가능하다 (RL-005).

        `operator_ui` 는 `script_escape`(stdlib 전용)만 새로 끌어온다.
        """
        import operator_ui

        assert not hasattr(operator_ui, "st")
        assert not hasattr(operator_ui, "streamlit")
        assert callable(operator_ui.render_component_html)


# ---------------------------------------------------------------------------
# AC 7 — e2e fixture 가 프로덕션 렌더 함수를 쓴다 (RL-024)
# ---------------------------------------------------------------------------
class TestE2EFixtureUsesProduction:
    def test_stage_mode_fixture_calls_the_production_renderer(self):
        """AC 7 — fixture 가 치환을 자체 재구현하면 프로덕션 수정이 무의미해진다.

        `e2e` 마커라 기본 run 에서 deselect 되므로, 그 fixture 의 형태는 기본
        run 에 남는 이 정적 단언이 지킨다.
        """
        source = (_TESTS_DIR / "e2e" / "test_operator_stage_mode_e2e.py").read_text(
            encoding="utf-8"
        )

        assert "render_component_html(" in source
        assert "{{BOOTSTRAP_JSON}}" not in source
        assert "json.dumps" not in source


# ---------------------------------------------------------------------------
# Out-of-scope 확인 — 룸 이름에는 마크업 싱크가 없다 (이중 이스케이프 금지)
# ---------------------------------------------------------------------------
class TestRoomNameHasNoMarkupSink:
    def test_room_name_reaches_the_dom_only_through_text_content(self):
        """마크업 이스케이프를 **추가하지 않는** 근거를 값으로 고정한다.

        ISSUE-44 가 뷰어에서 겪은 결함이 정확히 이중 이스케이프였다. 룸 이름이
        `innerHTML` 같은 마크업 싱크로 새는 순간 이 판단이 무효가 되므로,
        그때 이 테스트가 먼저 깨져야 한다.
        """
        html = _template()
        uses = re.findall(r"^.*BOOT\.room_name.*$", html, re.M)

        assert len(uses) == 2, f"BOOT.room_name sinks changed: {uses}"
        assert "textContent = BOOT.room_name" in uses[0]
        assert "style.display = BOOT.room_name" in uses[1]
        assert "innerHTML" not in "".join(uses)
