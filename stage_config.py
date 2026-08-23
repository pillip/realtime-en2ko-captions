"""
무대(스테이지) 브랜딩 설정 정규화/검증 모듈 (ISSUE-37).

``rooms.stage_config`` 에 JSON 문자열로 저장되는 룸별 무대 화면 설정을
안전한 dict 로 바꾸고(:func:`normalize_stage_config`), 저장 전에 값 제약을
확인한다(:func:`validate_stage_config`).

설계 원칙
---------
- **Importable + side-effect-free** (RL-001 / RL-005): import 시점에 DB,
  파일시스템, 네트워크, Streamlit 을 일체 건드리지 않는다. 표준 라이브러리
  ``json`` 만 사용하는 순수 함수 모듈이라 관리자 폼(ISSUE-39)과 무대 페이지
  서버 렌더(ISSUE-40)가 그대로 재사용한다.
- **깨진 값은 예외가 아니라 기본값으로** : DB 에는 컬럼 레벨 스키마 강제가
  없어(``usage_logs.metadata`` 와 같은 JSON-in-TEXT 관례) 언제든 NULL·빈
  문자열·깨진 JSON 이 들어올 수 있다. 무대 화면은 행사 중에 절대 죽으면 안
  되므로 읽기 경로는 항상 문서화된 기본값으로 degrade 한다.
- **쓰기 경로는 엄격하게** : :func:`validate_stage_config` 는 신뢰 경계에서
  호출되는 게이트다. 조용히 고쳐 저장하지 않고, 사람이 읽을 수 있는 한국어
  사유와 함께 거절한다.

Public API
----------
- :data:`DEFAULT_STAGE_CONFIG` : 문서화된 기본 설정 (docs/data_model.md).
- :func:`normalize_stage_config` : raw(JSON 문자열 | dict | None) → dict.
- :func:`validate_stage_config` : dict → ``(ok, reason)``.
"""

from __future__ import annotations

import json
from typing import Any

# 자막 컬럼 폭. 무대 페이지에서 각각 25% / 33.333% 로 매핑된다.
CAPTION_RATIOS: tuple[str, ...] = ("1/4", "1/3")
DEFAULT_CAPTION_RATIO: str = CAPTION_RATIOS[0]

# 로고 그룹 라벨 3종 — 고정 순서. 이 순서가 무대 하단 로고 바의 표시 순서다.
LOGO_GROUP_LABELS: tuple[str, ...] = ("주최", "주관", "후원")

EVENT_TITLE_MAX_LEN = 120
EVENT_SUBTITLE_MAX_LEN = 80

# 룸당 로고 파일 개수 상한 (전체 그룹 합산). 로고 바가 무대 세로 공간을
# 잠식하지 않도록 하는 표시 제약이자, 업로드 남용을 막는 상한이다.
MAX_ASSETS_PER_ROOM = 12

# 문서화된 기본 설정 (docs/data_model.md 의 blob shape 와 1:1).
# 주의: 이 상수를 그대로 반환하면 호출자가 in-place 로 오염시킬 수 있으므로
# normalize_stage_config 는 언제나 새 dict/list 를 만들어 돌려준다.
DEFAULT_STAGE_CONFIG: dict[str, Any] = {
    "event_title": "",
    "event_subtitle": "",
    "caption_ratio": DEFAULT_CAPTION_RATIO,
    "logo_groups": [{"label": label, "assets": []} for label in LOGO_GROUP_LABELS],
}


def normalize_stage_config(raw: Any) -> dict[str, Any]:
    """raw 값을 무대 설정 dict 로 정규화한다. 절대 예외를 던지지 않는다.

    허용 입력:
      - JSON 문자열 (``rooms.stage_config`` 컬럼 값)
      - 이미 파싱된 dict (관리자 폼 입력)
      - ``None`` / 그 밖의 타입 → 기본값

    깨진 JSON, 최상위가 object 가 아닌 JSON(``"[]"``), 알 수 없는 키,
    잘못된 ``caption_ratio``, 알 수 없는 로고 라벨은 모두 조용히 폐기되고
    문서화된 기본값으로 대체된다.

    Returns:
        :data:`DEFAULT_STAGE_CONFIG` 와 같은 키를 가진 **새 dict**. 반환값을
        수정해도 모듈 상수나 이후 호출 결과에 영향을 주지 않는다.
    """
    data = _as_mapping(raw)
    return {
        "event_title": _as_text(data.get("event_title")),
        "event_subtitle": _as_text(data.get("event_subtitle")),
        "caption_ratio": _as_caption_ratio(data.get("caption_ratio")),
        "logo_groups": _as_logo_groups(data.get("logo_groups")),
    }


def validate_stage_config(cfg: Any) -> tuple[bool, str]:
    """저장 전 검증. ``(True, "")`` 또는 ``(False, <한국어 사유>)``.

    ``normalize_stage_config`` 와 달리 값을 고치지 않는다. 관리자가 잘못
    입력한 사실을 그대로 알려야 폼에서 되돌릴 수 있기 때문이다. 반대로
    정규화를 거친 설정은 항상 이 검증을 통과한다.

    Note:
        완전한 설정 dict 를 기대한다 — 부분 설정을 검증하려면 먼저
        :func:`normalize_stage_config` 를 통과시켜라. 예를 들어
        ``caption_ratio`` 키가 아예 없으면 거절된다.
    """
    if not isinstance(cfg, dict):
        return False, "무대 설정은 JSON 객체(dict) 형식이어야 합니다."

    ratio = cfg.get("caption_ratio")
    if ratio not in CAPTION_RATIOS:
        allowed = " 또는 ".join(CAPTION_RATIOS)
        return False, f"자막 컬럼 비율은 {allowed} 중 하나여야 합니다."

    for key, label, limit in (
        ("event_title", "행사 타이틀", EVENT_TITLE_MAX_LEN),
        ("event_subtitle", "행사 부제", EVENT_SUBTITLE_MAX_LEN),
    ):
        value = cfg.get(key, "")
        if not isinstance(value, str):
            return False, f"{label}은(는) 문자열이어야 합니다."
        if len(value) > limit:
            return (
                False,
                f"{label}은(는) 최대 {limit}자까지 입력할 수 있습니다 (현재 {len(value)}자).",
            )

    groups = cfg.get("logo_groups", [])
    if not isinstance(groups, list):
        return False, "로고 그룹은 목록(list) 형식이어야 합니다."

    total_assets = sum(
        len(group["assets"])
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("assets"), list)
    )
    if total_assets > MAX_ASSETS_PER_ROOM:
        return False, (
            f"로고 파일은 룸당 최대 {MAX_ASSETS_PER_ROOM}개까지 등록할 수 있습니다 "
            f"(현재 {total_assets}개)."
        )

    return True, ""


# ---------------------------------------------------------------------------
# Internal helpers — 모두 예외를 던지지 않는다.
# ---------------------------------------------------------------------------
def _as_mapping(raw: Any) -> dict[str, Any]:
    """raw 를 dict 로 해석한다. 해석 불가 시 빈 dict.

    호출자(Room.get_stage_config)가 room_id 와 함께 파싱 실패를 로깅할 수
    있도록, 여기서는 로그를 남기지 않고 조용히 기본값 경로를 택한다.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except (json.JSONDecodeError, RecursionError):
            # RecursionError: 깊게 중첩된 JSON('[' * 100000)은 JSONDecodeError
            # 가 아니라 RecursionError 로 터진다. "절대 예외를 던지지 않는다"
            # 계약을 지키려면 함께 잡아야 한다.
            return {}
    return raw if isinstance(raw, dict) else {}


def _as_text(value: Any) -> str:
    """문자열이 아니면 빈 문자열. 앞뒤 공백은 제거한다."""
    return value.strip() if isinstance(value, str) else ""


def _as_caption_ratio(value: Any) -> str:
    return value if value in CAPTION_RATIOS else DEFAULT_CAPTION_RATIO


def _as_logo_groups(value: Any) -> list[dict[str, Any]]:
    """알려진 라벨 3종만 고정 순서로 남긴다.

    - dict 가 아닌 원소, 알 수 없는 라벨은 폐기.
    - 같은 라벨이 중복되면 첫 번째만 채택.
    - ``assets`` 는 파일명(문자열)만 남긴다. 경로 검증/봉인은 에셋을 실제로
      서빙하는 쪽(ISSUE-38)의 책임이다.
    """
    assets_by_label: dict[str, list[str]] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            if label not in LOGO_GROUP_LABELS or label in assets_by_label:
                continue
            assets = item.get("assets")
            assets_by_label[label] = (
                [name for name in assets if isinstance(name, str)]
                if isinstance(assets, list)
                else []
            )

    return [
        {"label": label, "assets": assets_by_label.get(label, [])}
        for label in LOGO_GROUP_LABELS
    ]
