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
- :func:`normalize_group_title` : 로고 그룹 표시 글자 폴백 (관리자 폼이 공유).
"""

from __future__ import annotations

import json
from typing import Any

# 자막 컬럼 폭. 무대 페이지에서 각각 25% / 33.333% 로 매핑된다.
CAPTION_RATIOS: tuple[str, ...] = ("1/4", "1/3")
DEFAULT_CAPTION_RATIO: str = CAPTION_RATIOS[0]

# 로고 그룹 **식별 키** 3종 — 고정 순서. 이 순서가 무대 하단 로고 바의 표시
# 순서이기도 하다.
#
# **이 값은 화면에 보이는 글자가 아니다.** 표시 글자는 그룹별 `title` 이고
# 관리자가 룸마다 고칠 수 있다 (영문 행사면 "Host"/"Organizer"/"Sponsor").
# 키와 표시 글자를 분리하지 않으면 라벨을 영어로 바꾸는 순간
# `_as_logo_groups` 의 화이트리스트 필터가 그 그룹을 **알 수 없는 라벨로 보고
# 폐기**해 업로드된 로고가 전부 사라진다. 키는 영원히 한국어 3종이고 화면에는
# 나가지 않는다.
LOGO_GROUP_LABELS: tuple[str, ...] = ("주최", "주관", "후원")

# 로고 바 배경 톤 (관리자 선택). 아트워크 한 벌로 두 배경을 모두 만족시킬 수
# 없기 때문에 존재하는 설정이다 — 흰 아트워크는 어두운 면에서, 검정 아트워크는
# 밝은 면에서만 읽힌다. CSS 로 이미지를 되살릴 방법은 없다.
#
#   "auto"  — 면을 깔지 않는다. 로고가 페이지 캔버스에 그대로 얹혀 **테마를
#             따라간다**. 화면이 가장 깔끔하지만 테마를 바꾸면 로고가 뒤집혀
#             묻힌다 (= 이 설정 도입 이전의 동작).
#   "light" — 항상 밝은 카드(zinc-200). 어두운·컬러 아트워크가 읽힌다.
#   "dark"  — 항상 어두운 밴드(zinc-800). 흰 아트워크가 읽힌다.
#
# 기본이 "auto" 인 이유는 `show_logo_labels` 와 같다 — 이 키가 없던 룸의 화면을
# 말없이 바꾸지 않는다.
LOGO_BAR_TONES: tuple[str, ...] = ("auto", "light", "dark")
DEFAULT_LOGO_BAR_TONE: str = LOGO_BAR_TONES[0]

EVENT_TITLE_MAX_LEN = 120

# 로고 그룹 표시 글자 상한. 로고 바 한 칸에 들어가는 짧은 캡션이므로 타이틀과
# 같은 규모가 필요하지 않다 — 길어지면 로고가 밀려 바가 깨진다.
LOGO_GROUP_TITLE_MAX_LEN = 20

# 룸당 로고 파일 개수 상한 (전체 그룹 합산). 로고 바가 무대 세로 공간을
# 잠식하지 않도록 하는 표시 제약이자, 업로드 남용을 막는 상한이다.
MAX_ASSETS_PER_ROOM = 12

# 문서화된 기본 설정 (docs/data_model.md 의 blob shape 와 1:1).
# 주의: 이 상수를 그대로 반환하면 호출자가 in-place 로 오염시킬 수 있으므로
# normalize_stage_config 는 언제나 새 dict/list 를 만들어 돌려준다.
DEFAULT_STAGE_CONFIG: dict[str, Any] = {
    "event_title": "",
    # `event_subtitle` 은 **제거됐다.** 무대 화면에서 부제를 쓰지 않기로 했고,
    # 그걸 렌더하던 요소(`#event-subtitle` / `#card-subtitle`)도 함께 사라졌다.
    # 이전에 저장된 룸의 블롭에는 이 키가 남아 있을 수 있지만
    # `normalize_stage_config` 가 알려진 키로만 새 dict 를 만들므로 다음 정규화에서
    # 조용히 떨어진다 — 마이그레이션은 필요하지 않다.
    "caption_ratio": DEFAULT_CAPTION_RATIO,
    # 로고 바에 "주최/주관/후원" 글자를 띄울지 (관리자 토글).
    # **기본값이 True 인 이유**: 이 키가 도입되기 전에 저장된 룸의
    # `stage_config` 에는 이 키가 없다. 그 룸들은 지금까지 라벨이 보이는 화면을
    # 쓰고 있었으므로, 키 부재를 "끄기" 로 읽으면 **기존 무대 화면이 말없이
    # 바뀐다**. 부재 = 종전 동작으로 둔다.
    "show_logo_labels": True,
    # 로고 바 배경 톤 (LOGO_BAR_TONES 주석 참고). 부재 = 종전 동작("auto").
    "logo_bar_tone": DEFAULT_LOGO_BAR_TONE,
    # `label` 은 바꿀 수 없는 식별 키, `title` 은 관리자가 고치는 표시 글자다
    # (LOGO_GROUP_LABELS 주석 참고). 기본 `title` 은 키와 같아서, 이 키가 도입되기
    # 전에 저장된 룸도 종전과 똑같은 화면을 유지한다.
    "logo_groups": [
        {"label": label, "title": label, "assets": []} for label in LOGO_GROUP_LABELS
    ],
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
        "caption_ratio": _as_caption_ratio(data.get("caption_ratio")),
        "show_logo_labels": _as_show_logo_labels(data.get("show_logo_labels")),
        "logo_bar_tone": _as_logo_bar_tone(data.get("logo_bar_tone")),
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

    title = cfg.get("event_title", "")
    if not isinstance(title, str):
        return False, "행사 타이틀은(는) 문자열이어야 합니다."
    if len(title) > EVENT_TITLE_MAX_LEN:
        return (
            False,
            f"행사 타이틀은(는) 최대 {EVENT_TITLE_MAX_LEN}자까지 입력할 수 "
            f"있습니다 (현재 {len(title)}자).",
        )

    # 키가 **없는** 것은 허용한다 — 이 키 도입 이전에 저장된 설정이 그렇고,
    # `normalize_stage_config` 가 True 로 채운다. 다만 들어 있다면 bool 이어야
    # 한다: 쓰기 경로는 조용히 고치지 않고 사유와 함께 거절한다.
    if "show_logo_labels" in cfg and not isinstance(cfg["show_logo_labels"], bool):
        return False, "로고 라벨 표시 여부는 참/거짓 값이어야 합니다."

    # 같은 이유로 키 부재는 허용하고, 들어 있다면 허용 목록 안이어야 한다.
    if "logo_bar_tone" in cfg and cfg["logo_bar_tone"] not in LOGO_BAR_TONES:
        allowed = " / ".join(LOGO_BAR_TONES)
        return False, f"로고 바 배경 톤은 {allowed} 중 하나여야 합니다."

    groups = cfg.get("logo_groups", [])
    if not isinstance(groups, list):
        return False, "로고 그룹은 목록(list) 형식이어야 합니다."

    # 그룹 표시 글자. `title` 키가 **없는** 것은 허용한다 — 이 키 도입 이전에
    # 저장된 설정이 그렇고 `normalize_stage_config` 가 식별 키로 채운다. 다만
    # 들어 있다면 문자열이어야 하고 상한을 넘지 않아야 한다.
    for group in groups:
        if not isinstance(group, dict) or "title" not in group:
            continue
        group_title = group["title"]
        if not isinstance(group_title, str):
            return False, "로고 그룹 표시 글자는 문자열이어야 합니다."
        if len(group_title) > LOGO_GROUP_TITLE_MAX_LEN:
            return False, (
                f"로고 그룹 표시 글자는 최대 {LOGO_GROUP_TITLE_MAX_LEN}자까지 "
                f"입력할 수 있습니다 (현재 {len(group_title)}자)."
            )

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


def _as_show_logo_labels(raw: Any) -> bool:
    """로고 그룹 라벨 표시 여부를 bool 로 정규화한다. 예외를 던지지 않는다.

    **키가 없으면 True** — 이 키 도입 이전에 저장된 룸을 조용히 바꾸지 않기
    위해서다 (`DEFAULT_STAGE_CONFIG` 주석 참고).

    JSON 왕복을 거치므로 `true/false` 외에 문자열이 들어올 수 있다. 관리자
    폼은 bool 만 보내지만, `rooms.stage_config` 는 컬럼 레벨 스키마가 없는
    JSON-in-TEXT 라 언제든 손으로 들어간 값이 있을 수 있다. 읽기 경로는 항상
    문서화된 기본값으로 degrade 한다.
    """
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("false", "0", "no", "off"):
            return False
        # 알 수 없는 문자열은 종전 동작(표시)으로 degrade 한다.
        return True
    if isinstance(raw, int | float):
        return bool(raw)
    return True


def normalize_group_title(raw: Any, label: str) -> str:
    """그룹 표시 글자를 정규화한다. 비거나 문자열이 아니면 **식별 키로 폴백**.

    빈 문자열로 떨어지게 두면 라벨 토글이 켜진 채로 빈 캡션이 렌더돼 로고 바에
    설명 없는 공백이 생긴다. 키(한국어 기본값)로 폴백하면 이 키가 도입되기 전에
    저장된 룸이 종전 화면을 그대로 유지한다 — 부재 = 종전 동작.

    **공개 함수인 이유**: 관리자 폼 조립(`admin_logic.build_stage_config_from_form`)
    도 같은 폴백을 거쳐야 "폼이 만든 dict == 정규화된 dict" 가 성립한다. 그 성질이
    깨지면 저장된 블롭이 폼 출력과 달라져 왕복 테스트가 의미를 잃는다. 폴백 규칙을
    두 곳에 적어 두면 갈라지므로(RL-001) 정의는 여기 하나다.
    """
    if isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned:
            return cleaned
    return label


def _as_logo_bar_tone(raw: Any) -> str:
    """로고 바 톤을 정규화한다. 알 수 없는 값은 기본값으로 degrade 한다.

    읽기 경로는 예외를 던지지 않는다 — `rooms.stage_config` 는 컬럼 레벨 스키마가
    없는 JSON-in-TEXT 라 손으로 들어간 값이 언제든 있을 수 있고, 무대 화면은 행사
    중에 죽으면 안 된다. 쓰기 경로(`validate_stage_config`)는 반대로 거절한다.
    """
    return raw if raw in LOGO_BAR_TONES else DEFAULT_LOGO_BAR_TONE


def _as_logo_groups(value: Any) -> list[dict[str, Any]]:
    """알려진 라벨 3종만 고정 순서로 남긴다.

    - dict 가 아닌 원소, 알 수 없는 라벨은 폐기.
    - 같은 라벨이 중복되면 첫 번째만 채택.
    - ``assets`` 는 파일명(문자열)만 남긴다. 경로 검증/봉인은 에셋을 실제로
      서빙하는 쪽(ISSUE-38)의 책임이다.
    - ``title`` 은 관리자가 고치는 표시 글자다. 필터링은 **언제나 ``label``**
      기준이다 — 표시 글자로 필터링하면 라벨을 영어로 바꾼 순간 그룹이 알 수
      없는 것으로 폐기돼 업로드된 로고가 전부 사라진다.
    """
    groups_by_label: dict[str, dict[str, Any]] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            if label not in LOGO_GROUP_LABELS or label in groups_by_label:
                continue
            assets = item.get("assets")
            groups_by_label[label] = {
                "title": normalize_group_title(item.get("title"), label),
                "assets": (
                    [name for name in assets if isinstance(name, str)]
                    if isinstance(assets, list)
                    else []
                ),
            }

    return [
        {
            "label": label,
            "title": groups_by_label.get(label, {}).get("title", label),
            "assets": groups_by_label.get(label, {}).get("assets", []),
        }
        for label in LOGO_GROUP_LABELS
    ]
