"""
`sse_broadcast.build_sse_app` 의 라우트 등록 ↔ `aws-alb-setup.md` 의 ALB 라우팅
규칙 파리티 가드.

**왜 이 파일이 있는가 (실제로 일어난 일):**

`aws-alb-setup.md` 는 2026-08-09 (#105) 에 8766 경로를 `/view/*`, `/stream/*` 두
개로 적었다. 그 뒤 2026-08-23 에 `/branding/{room_id}/{filename}` (#120, ISSUE-38)
과 `/stage/{room_id}` (#121, ISSUE-40) 이 aiohttp 앱에 추가됐지만, 문서도 ALB
리스너 규칙도 갱신되지 않았다. 결과: 무대 합성 뷰 트랙 6개 이슈(ISSUE-37~42)가
전부 `shipped` 인데 **프로덕션에서는 도달 불가**였다.

이 결함이 오래 숨은 이유는 **실패가 에러로 보이지 않기** 때문이다. ALB 기본 규칙이
미등록 경로를 8501 로 보내고 Streamlit 이 HTTP **200 + 자기 앱 셸 HTML** 을 돌려준다.
404 도 500 도 로그도 없다 — 무대 화면은 그냥 빈 화면, 룸 로고는 그냥 깨진 이미지다.
상태 코드만 보는 헬스체크나 스모크 테스트는 이것을 영원히 통과시킨다.

전형적인 RL-001(손복사된 목록을 묶는 테스트 부재)이다. 그래서 이 가드는 문서의
목록을 **읽기만** 하고, 비교 대상인 라우트 집합은 `build_sse_app` 이 실제로 만든
`app.router` 에서 **구조적으로** 뽑는다. 라우트가 추가되면 아무도 이 파일을 고치지
않아도 가드가 깨진다.

**`Guard:` 표기를 일부러 쓰지 않는다.** 그 표기는 `guard_mutations.toml` 카탈로그에
등재된 **보안 크리티컬 프로덕션 코드** 가드의 소유권을 나타내며, `guard_mutations.py`
는 카탈로그에 없는 표기를 양방향으로 오류 처리한다(ISSUE-43). 이 파일이 지키는 것은
프로덕션 코드의 한 줄이 아니라 **문서 ↔ 라우트 등록 파리티**라서 대응하는 코드 뮤테이션이
존재하지 않고, `sse_broadcast.py` 는 `ALLOWED_FILES`(4개 파일)에도 없다. 여기에
`Guard:` 를 붙이면 `mutation` CI job 이 즉시 깨진다 — 친절하게 되붙이지 말 것.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# websocket_handler -> auth -> streamlit 의존성 회피 (다른 테스트와 동일 패턴)
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()

from sse_broadcast import build_sse_app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
ALB_DOC_PATH = REPO_ROOT / "aws-alb-setup.md"

# `/health` 는 ALB 리스너 규칙에 들어가지 않는다 — 타겟그룹 헬스체크가 인스턴스로
# 직접 보내는 경로라서다(문서 §1-2). 리스너 규칙 검증에서 제외하는 것이 맞지만,
# "제외"가 "누락"으로 둔갑하지 않도록 §1 에 헬스체크 경로로 적혀 있는지를 아래
# test_health_route_is_documented_as_a_target_group_check 가 따로 단언한다.
HEALTH_CHECK_ONLY = frozenset({"/health"})


def _sse_route_prefixes() -> frozenset[str]:
    """`build_sse_app` 이 등록한 라우트의 최상위 경로 세그먼트 집합.

    문서는 `/stage/*` 처럼 프리픽스로 적고 ALB 규칙도 프리픽스 패턴을 쓰므로,
    `/stage/{room_id}` → `/stage` 로 줄여서 비교한다.

    라우트 목록을 소스 텍스트에서 regex 로 긁지 않고 **만들어진 app 의 라우터**
    에서 뽑는 것이 핵심이다 — `add_get` 호출 형태가 바뀌어도(루프, 상수 분리,
    서브앱 등) 가드가 눈을 감지 않는다.
    """
    app = build_sse_app(broadcast_manager=MagicMock(), room_repo=MagicMock())

    prefixes = set()
    for route in app.router.routes():
        resource = route.resource
        assert resource is not None, (
            f"라우트 {route!r} 에 resource 가 없다 — 경로를 알 수 없으므로 ALB 규칙과 "
            "대조할 수 없다. 가드를 건너뛰는 것이 아니라 실패시킨다."
        )
        canonical = resource.canonical  # 예: "/stream/{room_id}"
        first_segment = canonical.lstrip("/").split("/", 1)[0]
        assert first_segment, f"라우트 {canonical!r} 의 최상위 세그먼트를 뽑지 못했다"
        assert "{" not in first_segment, (
            f"라우트 {canonical!r} 의 최상위 세그먼트가 변수({first_segment!r})다. "
            "ALB 는 가변 프리픽스를 경로 규칙으로 쓸 수 없으므로 라우팅 설계를 "
            "먼저 정해야 한다 — 이 가드를 느슨하게 고치는 것은 답이 아니다."
        )
        prefixes.add(f"/{first_segment}")

    assert (
        prefixes
    ), "build_sse_app 이 라우트를 하나도 등록하지 않았다 — 가드가 무의미해진다"
    return frozenset(prefixes)


@pytest.fixture(scope="module")
def alb_doc() -> str:
    assert ALB_DOC_PATH.is_file(), f"ALB 배포 가이드가 없다: {ALB_DOC_PATH}"
    return ALB_DOC_PATH.read_text(encoding="utf-8")


def _port_table_viewer_paths(doc: str) -> str:
    """§ 포트 표에서 8766 행의 '경로' 셀 텍스트."""
    for line in doc.splitlines():
        if line.startswith("|") and "8766" in line:
            return line
    pytest.fail(
        "aws-alb-setup.md 포트 표에서 8766 행을 찾지 못했다. 문서 구조가 바뀌었다면 "
        "이 가드의 앵커도 함께 고쳐야 한다 — 앵커를 못 찾는 것은 통과가 아니라 실패다."
    )


def _listener_rule_viewer_paths(doc: str) -> str:
    """HTTPS 리스너 규칙 중 tg-viewer 로 포워드하는 규칙의 경로 조건 텍스트.

    주변 설명 산문이 아니라 **규칙 본문만** 떼어낸다. 그렇지 않으면 "경고 문구에
    경로 이름이 등장한다"는 이유로 가드가 통과해 버린다.
    """
    match = re.search(r"IF Path is(.*?)Forward to \*\*tg-viewer\*\*", doc, re.DOTALL)
    if match is None:
        pytest.fail(
            "aws-alb-setup.md 에서 tg-viewer 로 포워드하는 HTTPS 리스너 규칙을 찾지 "
            "못했다. 문서 구조가 바뀌었다면 이 가드의 앵커도 함께 고쳐야 한다."
        )
    return match.group(1)


def _undocumented(prefixes: frozenset[str], haystack: str) -> list[str]:
    return sorted(p for p in prefixes if p not in haystack)


def test_every_sse_route_appears_in_the_port_table(alb_doc: str) -> None:
    """8766 포트 표 행이 (헬스체크 전용 경로를 뺀) 실제 라우트 전부를 담아야 한다.

    포트 표는 **경로 라우팅** 표다. `/health` 는 도메인 경로로 라우팅되지 않고
    (기본 규칙에 걸려 8501 로 간다) ALB 가 인스턴스로 직접 보내는 헬스체크 경로라
    여기가 아니라 §1-2 에 적힌다 — 그 사실 자체는
    test_health_route_is_documented_as_a_target_group_check 가 단언한다.
    """
    prefixes = _sse_route_prefixes() - HEALTH_CHECK_ONLY
    row = _port_table_viewer_paths(alb_doc)

    missing = _undocumented(prefixes, row)
    assert not missing, (
        f"aws-alb-setup.md 포트 표의 8766 행에 빠진 라우트: {missing}. "
        f"`build_sse_app` 이 등록한 경로는 {sorted(prefixes)} 다. "
        "표를 갱신하고, ALB 리스너 규칙도 실제로 고쳤는지 확인할 것 — 문서만 "
        "고치면 프로덕션은 그대로 Streamlit 셸을 200 으로 돌려준다."
    )


def test_every_sse_route_appears_in_the_https_listener_rules(alb_doc: str) -> None:
    """리스너 규칙이 (헬스체크 전용 경로를 뺀) 실제 라우트 전부를 담아야 한다.

    이 단언이 깨진 채로 배포하면 해당 경로가 조용히 8501 로 흘러간다.
    """
    prefixes = _sse_route_prefixes() - HEALTH_CHECK_ONLY
    rule = _listener_rule_viewer_paths(alb_doc)

    missing = _undocumented(prefixes, rule)
    assert not missing, (
        f"HTTPS 리스너 규칙(→ tg-viewer)에 빠진 라우트: {missing}. "
        f"리스너 규칙이 담아야 하는 경로는 {sorted(prefixes)} 다. "
        "빠진 경로는 ALB 기본 규칙에 걸려 8501(Streamlit)로 가고, 에러가 아니라 "
        "200 + 앱 셸 HTML 이 돌아온다 — 상태 코드로는 절대 드러나지 않는다."
    )


def test_port_table_and_listener_rules_agree(alb_doc: str) -> None:
    """같은 문서 안의 두 손복사 목록이 서로 어긋나지 않아야 한다.

    한쪽만 갱신하는 것이 애초에 이 결함을 만든 드리프트 양상이다.
    """
    prefixes = _sse_route_prefixes() - HEALTH_CHECK_ONLY
    row = _port_table_viewer_paths(alb_doc)
    rule = _listener_rule_viewer_paths(alb_doc)

    in_table_only = sorted(p for p in prefixes if p in row and p not in rule)
    in_rule_only = sorted(p for p in prefixes if p in rule and p not in row)
    assert not in_table_only and not in_rule_only, (
        f"포트 표에만 있는 경로: {in_table_only}, 리스너 규칙에만 있는 경로: "
        f"{in_rule_only}. 두 목록은 같은 집합을 말해야 한다."
    )


def test_health_route_is_documented_as_a_target_group_check(alb_doc: str) -> None:
    """`/health` 를 리스너 규칙 검증에서 빼는 것이 의도된 제외임을 고정한다.

    이 단언이 없으면 `HEALTH_CHECK_ONLY` 가 "검증하지 않는 경로" 를 담는
    쓰레기통으로 자라고, 그때부터 위의 두 테스트는 조용히 범위를 잃는다.
    """
    prefixes = _sse_route_prefixes()
    assert HEALTH_CHECK_ONLY <= prefixes, (
        f"HEALTH_CHECK_ONLY={sorted(HEALTH_CHECK_ONLY)} 에 더 이상 존재하지 않는 "
        f"라우트가 있다. 실제 라우트: {sorted(prefixes)}. 면제 목록을 정리할 것."
    )

    # 8766 타겟그룹 헬스체크 절(§1-2)이 이 경로를 실제로 지정하고 있어야 한다.
    tg_viewer_section = re.search(r"### 1-2\..*?(?=\n### |\n## )", alb_doc, re.DOTALL)
    assert tg_viewer_section is not None, (
        "aws-alb-setup.md 에서 tg-viewer 타겟그룹 절(§1-2)을 찾지 못했다 — "
        "앵커를 못 찾는 것은 통과가 아니라 실패다."
    )
    for path in sorted(HEALTH_CHECK_ONLY):
        assert path in tg_viewer_section.group(0), (
            f"{path} 가 리스너 규칙에서 면제되는 근거는 '타겟그룹 헬스체크 경로'인데, "
            f"§1-2 에 그렇게 적혀 있지 않다. 면제가 누락으로 둔갑하는 중이다."
        )


def test_guard_detects_an_undocumented_route(alb_doc: str) -> None:
    """가드에 변별력이 있는지 — 미등록 라우트를 실제로 잡아내는지 (RL-004).

    위의 테스트들은 문서가 옳을 때 통과한다. 그것만으로는 "무엇이든 통과시키는
    가드" 와 구별되지 않는다. 문서에 없는 가짜 프리픽스를 같은 비교 함수에 넣어
    `missing` 으로 잡히는 것을 확인한다.
    """
    fake = "/totally-undocumented-route"
    assert (
        fake not in alb_doc
    ), "카나리아 프리픽스가 하필 문서에 존재한다 — 다른 이름을 쓸 것"

    row = _port_table_viewer_paths(alb_doc)
    rule = _listener_rule_viewer_paths(alb_doc)

    assert _undocumented(frozenset({fake}), row) == [
        fake
    ], "포트 표 비교가 미등록 경로를 잡지 못했다 — 가드가 무력하다"
    assert _undocumented(frozenset({fake}), rule) == [
        fake
    ], "리스너 규칙 비교가 미등록 경로를 잡지 못했다 — 가드가 무력하다"

    # 그리고 실제 라우트는 (문서가 고쳐진 지금) 잡히지 않아야 한다 — 양방향 변별력.
    real = _sse_route_prefixes() - HEALTH_CHECK_ONLY
    assert (
        _undocumented(real, rule) == []
    ), "실제 라우트가 미등록으로 잡힌다 — 문서 수정이 아직 반영되지 않았다"
