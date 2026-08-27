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


def _rule_rgba(css: str, selector: str) -> tuple[float, float, float, float]:
    block = _rule_block(css, selector)
    match = re.search(
        r"color:\s*rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)", block
    )
    assert match is not None, f"{selector} has no rgba() color declaration"
    r, g, b, a = match.groups()
    return (int(r), int(g), int(b), float(a))
