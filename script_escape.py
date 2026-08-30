"""인라인 ``<script>`` 로 들어가는 값의 **단일** 이스케이프 구현 (ISSUE-48).

무대(ISSUE-40)·뷰어(ISSUE-44)·오퍼레이터(ISSUE-48) 세 렌더러가 같은 sink 를
갖는다. 구현이 두 벌이 되는 순간 그 사이의 드리프트가 곧 결함이 되므로
(RL-001, frequency 4) 정의는 여기 하나뿐이고 나머지는 전부 import 한다.

의존성은 표준 라이브러리뿐이다 — ``operator_ui`` 같은 부수효과 없는 순수
모듈이 aiohttp/streamlit 을 끌어오지 않고 재사용할 수 있어야 한다.
"""

from __future__ import annotations

import json
import re
from typing import Any


def json_for_script(obj: Any) -> str:
    """Serialise ``obj`` as a JSON literal that is safe inside an inline <script>.

    ``json.dumps`` escapes neither ``<`` nor ``/``, so a user-controlled string
    containing ``</script>`` closes the script block early and everything after
    it is parsed as markup (RL-016 — the hand-off left by the ISSUE-37 review
    F-2 for the stage page, and by the ISSUE-47 bootstrap for the operator
    component). Escaping ``<`` / ``>`` / ``&`` as ``\\uXXXX`` removes every
    breakout route (script terminator, HTML comment, entity) while keeping the
    output **valid JSON** — ``json.loads`` round-trips it unchanged. U+2028 /
    U+2029 are escaped too: they are legal in JSON strings but were illegal in
    JS string literals before ES2019.

    Use this for **every** value injected into a template's ``<script>`` block,
    scalars included, and pass composite payloads through in **one** call
    rather than field by field — a half-applied helper signals "handled" and is
    more dangerous than none (RL-020). The output is a complete literal, so the
    template must not wrap it in quotes of its own. Values injected into markup
    need :func:`html.escape` instead; SSE frames on the wire need neither and
    use plain ``json.dumps``.
    """
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


# 템플릿 치환의 단일 패스 문법 (RL-021). 연쇄 ``str.replace`` 는 앞 단계가 써
# 넣은 값을 뒤 단계가 다시 읽으므로, 값이 개발자 통제 밖(운영자 자유 입력)인
# 템플릿은 ``PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)`` 형태로
# 한 번에 치환한다. 문법이 두 벌이면 렌더러마다 조용히 갈라진다.
PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
