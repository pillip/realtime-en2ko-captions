"""WCAG 2.1 대비 계산 헬퍼 — stage/viewer 대비 테스트 공용 모듈 (RL-001).

원본은 `tests/test_stage_page.py` 안에 있었다. ISSUE-45 가 `components/viewer.html`
에 같은 형태의 대비 테스트를 추가하면서 **복사-붙여넣기 대신** 여기로 뽑았다 —
복사본은 원본과 갈라지고, 갈라진 대비 계산기는 두 페이지에 서로 다른 답을 준다.
바로 그 드리프트(무대는 고쳐지고 뷰어는 실패 값을 그대로 서빙)가 ISSUE-45 를
만든 원인이다.

전부 부작용 없는 순수 함수라 Streamlit import 위험이 없다.
"""

from __future__ import annotations

import re


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
    # 공용 모듈이 된 뒤로는 어느 템플릿의 CSS 인지 여기서 알 수 없다 — 원본의
    # "stage.html" 하드코딩만 파일 중립 문구로 바꿨고, 로직은 그대로다.
    assert match is not None, f"{selector} rule not found in the stylesheet"
    return match.group(1)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    """`#rrggbb` → (r, g, b). 불투명 색은 알파 1.0 으로 대비 계산에 넘긴다."""
    digits = value.strip().lstrip("#")
    assert len(digits) == 6, f"expected a #rrggbb colour, got {value!r}"
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


def _rule_hex(css: str, selector: str, prop: str) -> tuple[int, int, int]:
    """규칙 블록에서 `prop: #rrggbb` 를 뽑는다."""
    block = _rule_block(css, selector)
    match = re.search(rf"(?<![-\w]){re.escape(prop)}:\s*(#[0-9a-fA-F]{{6}})", block)
    assert match is not None, f"{selector} has no `{prop}: #rrggbb` declaration"
    return _hex_rgb(match.group(1))


def custom_properties(css: str, selector: str) -> dict[str, str]:
    """선택자 블록의 `--name: value` 선언들을 dict 로 돌려준다.

    테마를 CSS 커스텀 프로퍼티로 두면 대비 테스트가 규칙에서 리터럴 색을 더
    이상 못 읽는다. 그렇다고 리터럴로 되돌리면 테마마다 규칙을 복제해야 하고,
    복제된 색은 갈라진다 — 이 모듈이 애초에 존재하는 이유와 같은 결함이다
    (RL-001). 그래서 토큰을 읽어 해석하는 쪽을 택한다.
    """
    block = _rule_block(css, selector)
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", block)
    }


def resolve_color(
    value: str, tokens: dict[str, str] | None = None
) -> tuple[float, float, float, float]:
    """`rgba()` / `rgb()` / `#rrggbb` / `var(--x)` 를 (r, g, b, a) 로 해석한다.

    `var(--x, fallback)` 의 fallback 은 **쓰지 않는다** — 토큰이 선언돼 있어야
    한다. 폴백을 조용히 받아 주면 토큰 이름을 틀려도 테스트가 통과한다.
    """
    tokens = tokens or {}
    seen: set[str] = set()
    raw = value.strip()
    while raw.startswith("var("):
        name = raw[4:].split(",")[0].split(")")[0].strip()
        assert name not in seen, f"순환 참조: {name}"
        seen.add(name)
        assert name in tokens, f"선언되지 않은 토큰: {name}"
        raw = tokens[name].strip()

    rgba = re.fullmatch(
        r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", raw
    )
    if rgba:
        r, g, b, a = rgba.groups()
        return (int(r), int(g), int(b), float(a) if a is not None else 1.0)
    if raw.startswith("#"):
        return (*_hex_rgb(raw), 1.0)
    raise AssertionError(f"해석할 수 없는 색: {value!r} → {raw!r}")


def _rule_rgba(
    css: str, selector: str, tokens: dict[str, str] | None = None
) -> tuple[float, float, float, float]:
    """규칙 블록의 `color:` 를 (r, g, b, a) 로 돌려준다.

    `tokens` 를 주면 `color: var(--x)` 도 해석한다. 주지 않으면 종전처럼
    리터럴 `rgba()` 만 받는다 — 기존 호출자(viewer 대비 테스트)의 계약을
    바꾸지 않기 위해서다.
    """
    block = _rule_block(css, selector)
    if tokens is not None:
        match = re.search(r"(?<![-\w])color:\s*([^;]+);", block)
        assert match is not None, f"{selector} has no color declaration"
        return resolve_color(match.group(1), tokens)

    match = re.search(
        r"color:\s*rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)", block
    )
    assert match is not None, f"{selector} has no rgba() color declaration"
    r, g, b, a = match.groups()
    return (int(r), int(g), int(b), float(a))
