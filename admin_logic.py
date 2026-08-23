"""
admin.py에서 추출한 순수 비즈니스 로직 함수들
Streamlit 의존성 없이 테스트 가능

ISSUE-39: 무대 화면 설정 폼(관리자 전용)의 변환/분류 로직을 추가한다.
검증 규칙 자체는 재구현하지 않는다 — 값 제약은 ``stage_config`` 가,
업로드 제약은 ``branding_assets`` 가 소유하고 여기서는 그 결과를 관리자에게
보여줄 수 있는 형태로 묶어 주기만 한다 (RL-001/RL-005).
RL-006: ValueError 가 아닌 예외의 상세는 서버 콘솔에만 남기고 UI 에는
일반 메시지만 돌려준다.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from typing import Any

from branding_assets import delete_asset, save_asset
from stage_config import LOGO_GROUP_LABELS

# 관리자 대시보드 룸 표시용 한글 라벨 — operator_ui._ROOM_STATUS_LABELS
# 와 동일 정의이지만, admin_logic 은 operator_ui 에 의존하지 않도록 자체
# 사본을 둔다 (양방향 import 의존을 피해 그래프를 단순하게 유지).
_ROOM_STATUS_KOR_LABELS: dict[str, str] = {
    "waiting": "대기",
    "active": "활성",
    "inactive": "비활성",
    "closed": "종료",
}


def _format_room_status(status: str) -> str:
    return _ROOM_STATUS_KOR_LABELS.get(status, status)


# admin 사용자 생성/수정 폼의 역할 selectbox 옵션 (#95).
# operator 는 auth.is_operator / 룸 배정에서 쓰이는 정식 역할이므로 UI 에서
# 반드시 선택 가능해야 한다. 순서 = selectbox 표시 순서.
SELECTABLE_USER_ROLES: tuple[str, ...] = ("user", "operator", "admin")

# 생성 시 사용량 무제한으로 두는 역할 — 세션을 직접 운영하는 권한 역할.
# operator 는 컨퍼런스 장시간 세션을 돌리므로 중간에 한도로 끊기면 안 된다.
UNLIMITED_USAGE_ROLES: frozenset[str] = frozenset({"admin", "operator"})


def role_select_index(role: str) -> int:
    """수정 폼 selectbox 의 초기 index — 미지 역할은 0(user)로 방어."""
    try:
        return SELECTABLE_USER_ROLES.index(role)
    except ValueError:
        return 0


def validate_password(password: str, confirm: str) -> tuple[bool, str]:
    """비밀번호 유효성 검증

    Args:
        password: 입력된 비밀번호
        confirm: 확인용 비밀번호

    Returns:
        (valid, error_message) 튜플. valid가 True이면 error_message는 빈 문자열.
    """
    if not password:
        return False, "비밀번호는 필수입니다."
    if len(password) < 6:
        return False, "비밀번호는 최소 6자 이상이어야 합니다."
    if password != confirm:
        return False, "비밀번호가 일치하지 않습니다."
    return True, ""


def prepare_user_table_data(users: list[dict], get_remaining_seconds_fn) -> list[dict]:
    """사용자 목록을 테이블 표시용 데이터로 변환

    Args:
        users: 사용자 딕셔너리 리스트 (DB 조회 결과)
        get_remaining_seconds_fn: user_id를 받아 남은 초를 반환하는 함수

    Returns:
        테이블 표시용 딕셔너리 리스트
    """
    result = []
    for user in users:
        remaining_seconds = get_remaining_seconds_fn(user["id"])
        remaining_minutes = (
            remaining_seconds / 60 if remaining_seconds is not None else 0
        )

        result.append(
            {
                "ID": user["id"],
                "사용자명": user["username"],
                "소속": user["full_name"] or "-",
                "이메일": user["email"] or "-",
                "역할": user["role"],
                "상태": "활성" if user["is_active"] else "비활성",
                "사용량(초)": user["total_usage_seconds"],
                "제한(초)": user["usage_limit_seconds"],
                "남은시간(분)": f"{remaining_minutes:.1f}",
                "생성일": user["created_at"],
                "최근로그인": user["last_login"] or "-",
            }
        )
    return result


def export_user_logs_csv(logs: list[dict], username: str) -> bytes:
    """사용자 로그를 CSV 바이트로 변환 (BOM 포함, UTF-8)

    Args:
        logs: 로그 딕셔너리 리스트
        username: 사용자명 (파일명에 사용)

    Returns:
        UTF-8 BOM이 포함된 CSV 바이트 데이터
    """
    CSV_HEADERS = [
        "ID",
        "사용자ID",
        "작업",
        "시간(초)",
        "소스언어",
        "대상언어",
        "원문",
        "번역문",
        "생성일시",
        "메타데이터",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS)
    writer.writeheader()

    for log in logs:
        metadata = log.get("metadata", {})
        source_text = metadata.get("source_text", "") if metadata else ""
        target_text = metadata.get("target_text", "") if metadata else ""

        writer.writerow(
            {
                "ID": log["id"],
                "사용자ID": log["user_id"],
                "작업": log["action"],
                "시간(초)": log["duration_seconds"],
                "소스언어": log["source_language"] or "",
                "대상언어": log["target_language"] or "",
                "원문": source_text,
                "번역문": target_text,
                "생성일시": log["created_at"],
                "메타데이터": str(metadata) if metadata else "",
            }
        )

    csv_string = output.getvalue()
    # BOM + UTF-8 encoding
    return b"\xef\xbb\xbf" + csv_string.encode("utf-8")


# ============================================================
# ISSUE-29: 룸 관리 / 역할 기반 뷰 헬퍼 (Streamlit-free)
# ============================================================


def prepare_room_table_data(
    rooms: list[dict[str, Any]],
    user_id_to_username: dict[int, str],
) -> list[dict[str, Any]]:
    """룸 목록을 관리자 테이블 표시용 dict 로 변환한다.

    Args:
        rooms: ``database.Room.list_all()`` 등의 결과 (DB row dict 리스트).
        user_id_to_username: 운영자/생성자 id → username 매핑. 매핑이 없는
            경우(예: 삭제된 사용자)는 "-" 로 표시한다.

    Returns:
        Streamlit dataframe 으로 그대로 전달 가능한 dict 리스트.
    """
    result: list[dict[str, Any]] = []
    for room in rooms:
        operator_id = room.get("operator_id")
        operator_label = (
            user_id_to_username.get(operator_id, "-")
            if operator_id is not None
            else "-"
        )
        creator_id = room.get("created_by")
        creator_label = (
            user_id_to_username.get(creator_id, "-") if creator_id is not None else "-"
        )
        result.append(
            {
                "룸ID": room["id"],
                "이름": room.get("name", ""),
                "상태": _format_room_status(room.get("status", "")),
                "오퍼레이터": operator_label,
                "생성자": creator_label,
                "생성일시": room.get("created_at", ""),
                "마지막활동": room.get("last_activity") or "-",
                "종료일시": room.get("closed_at") or "-",
            }
        )
    return result


def filter_rooms_for_role(
    rooms: list[dict[str, Any]],
    *,
    user_role: str,
    user_id: int,
) -> list[dict[str, Any]]:
    """역할 기반 룸 가시성 필터 (RL-002 server-side enforcement).

    - admin: 모든 룸 노출
    - operator: ``operator_id == user_id`` 인 룸만
    - 기타 (user / 알 수 없는 역할): 빈 리스트 (defensive default)

    keyword-only 인자를 사용해 호출자가 user_id / user_role 슬롯을 실수로
    바꿔치지 못하게 한다 — 인자 순서 실수가 곧 권한 우회로 이어질 수
    있는 함수이므로 명시성을 강제한다.
    """
    if user_role == "admin":
        return list(rooms)
    if user_role == "operator":
        return [r for r in rooms if r.get("operator_id") == user_id]
    return []


# ISSUE-85: 룸 목록 상태 필터 기본값 — closed 는 기본 숨김.
# 룸 종료가 admin 수동 관리로 일원화되면서(#86) 종료 룸이 목록에
# 계속 쌓이므로, 기록 조회 목적일 때만 필터로 명시 선택해 노출한다.
DEFAULT_ROOM_LIST_STATUSES: tuple[str, ...] = ("waiting", "active", "inactive")

# admin.py 의 상태 필터 multiselect 옵션 (표시 순서 고정).
ROOM_STATUS_FILTER_OPTIONS: tuple[str, ...] = (
    "waiting",
    "active",
    "inactive",
    "closed",
)


def format_room_status(status: str) -> str:
    """상태 코드 → 한글 라벨 (필터 UI 의 format_func 용 공개 래퍼)."""
    return _format_room_status(status)


def filter_rooms_by_status(
    rooms: list[dict[str, Any]],
    statuses: list[str] | tuple[str, ...] = DEFAULT_ROOM_LIST_STATUSES,
) -> list[dict[str, Any]]:
    """룸 목록을 status 로 필터링한다 (ISSUE-85, Streamlit-free).

    빈 statuses 는 빈 목록을 돌려준다 — "아무것도 선택 안 함 → 아무것도
    안 보임" 이 multiselect 의 직관과 일치한다.
    """
    allowed = set(statuses)
    return [r for r in rooms if r.get("status") in allowed]


def export_room_logs_csv(logs: list[dict[str, Any]], room_id: str) -> bytes:
    """룸별 로그를 CSV (UTF-8 BOM) 로 변환한다.

    헤더 첫 컬럼은 "룸ID" — 다운로드한 파일이 단일 룸 컨텍스트를
    잃지 않도록 명시한다 (한 사용자가 여러 룸의 CSV 를 비교할 수 있음).
    """
    CSV_HEADERS = [
        "ID",
        "룸ID",
        "사용자ID",
        "사용자명",
        "작업",
        "시간(초)",
        "소스언어",
        "대상언어",
        "원문",
        "번역문",
        "생성일시",
        "메타데이터",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS)
    writer.writeheader()

    for log in logs:
        metadata = log.get("metadata", {})
        source_text = metadata.get("source_text", "") if metadata else ""
        target_text = metadata.get("target_text", "") if metadata else ""

        writer.writerow(
            {
                "ID": log["id"],
                "룸ID": log.get("room_id") or room_id,
                "사용자ID": log.get("user_id", ""),
                "사용자명": log.get("username", ""),
                "작업": log["action"],
                "시간(초)": log["duration_seconds"],
                "소스언어": log.get("source_language") or "",
                "대상언어": log.get("target_language") or "",
                "원문": source_text,
                "번역문": target_text,
                "생성일시": log.get("created_at", ""),
                "메타데이터": str(metadata) if metadata else "",
            }
        )

    csv_string = output.getvalue()
    return b"\xef\xbb\xbf" + csv_string.encode("utf-8")


def get_logs_for_operator(
    *,
    usage_log_model: Any,
    room_model: Any,
    requested_room_id: str,
    user_role: str,
    user_id: int,
) -> list[dict[str, Any]]:
    """역할 검증 후 룸별 로그를 반환한다 (RL-002 트러스트 경계).

    이 함수가 admin/operator 권한을 server-side 에서 다시 한 번 확인하므로,
    UI 가 어떤 room_id 를 직접 보내도 권한이 없는 룸의 로그는 절대
    반환되지 않는다. 다음 두 가지 경로가 모두 막혀 있어야 한다:

      1. operator 가 다른 오퍼레이터의 room_id 를 추측해 query
      2. role="user" 가 admin 페이지의 어느 경로로든 진입한 경우

    Returns:
        결과 row 리스트. 권한 없음, 미존재 룸 등은 모두 빈 리스트로
        반환한다 (RL-006 — 존재 여부를 leaking 하지 않도록 동일한 응답).
    """
    if user_role not in ("admin", "operator"):
        return []

    room = room_model.get_by_id(requested_room_id)
    if room is None:
        return []

    if user_role == "operator" and room.get("operator_id") != user_id:
        # 다른 오퍼레이터의 룸 — 존재 여부를 노출하지 않기 위해 빈
        # 리스트를 반환 (UI 는 "기록 없음" 으로 표시).
        return []

    return usage_log_model.get_logs_by_room(requested_room_id)


# ============================================================
# ISSUE-33: 뷰어 지표 (Streamlit-free 변환 헬퍼)
# ============================================================

# 표시용 한글 라벨 — admin.py 의 metric 위젯이 그대로 사용한다.
# 알려지지 않은 코드는 그대로 노출 (RL-006: fallback 보호).
_LANG_KOR_LABELS: dict[str, str] = {
    "ko": "한국어",
    "en": "영어",
    "ja": "일본어",
    "zh": "중국어",
}


def _format_by_lang_label(by_lang: dict[str, int]) -> str:
    """언어별 카운트 dict 를 사람이 읽는 한 줄 요약으로 변환.

    Examples
    --------
    {} → ""                          (zero-state — admin.py 가 "0명" 으로 폴백)
    {"ko": 45} → "한국어 45명"
    {"ko": 45, "zh": 12} → "한국어 45명, 중국어 12명"
    {"xx": 3} → "xx 3명"             (알 수 없는 코드는 코드 그대로 노출)

    Items 는 카운트 내림차순 → 코드 오름차순으로 정렬해 동일 카운트의
    표시 순서가 deterministic 하다 (스냅샷 테스트가 안정적이다).
    """
    if not by_lang:
        return ""
    items = sorted(by_lang.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(
        f"{_LANG_KOR_LABELS.get(code, code)} {count}명"
        for code, count in items
        if count > 0
    )


def build_room_metrics_view_data(
    *,
    in_memory: dict[str, Any],
    db_metrics: dict[str, int] | None,
) -> dict[str, Any]:
    """admin.py 의 룸별 지표 표시용 정규화된 dict 를 만든다 (ISSUE-33).

    Args:
        in_memory: ``BroadcastManager.get_metrics(room_id)`` 결과.
            ``{"current": int, "by_lang": {lang: int}}`` 모양을 기대.
        db_metrics: ``Room.get_viewer_metrics(room_id)`` 결과 또는 None.
            ``{"total_viewers": int, "peak_viewers": int}`` 모양을 기대.
            None 은 "DB 에 룸이 없거나 한 번도 viewer 가 붙은 적 없음" —
            모두 0 으로 fallback (사용자에게는 zero-state 로만 보인다).

    Returns:
        ``{"current": int, "total": int, "peak": int, "by_lang_label": str}``
        — Streamlit ``st.metric`` 4 개 위젯에 1:1 로 매핑되는 사전.

    keyword-only 인자: 호출자가 in_memory / db_metrics 를 실수로 swap 하면
    ``current`` 와 ``peak`` 가 뒤바뀌는 큰 표시 오류가 생기므로 명시성을
    강제한다 (admin_logic.filter_rooms_for_role 의 동일한 이유).
    """
    current = int(in_memory.get("current", 0) or 0)
    by_lang = in_memory.get("by_lang") or {}
    by_lang_label = _format_by_lang_label(by_lang)

    if db_metrics is None:
        total = 0
        peak = 0
    else:
        total = int(db_metrics.get("total_viewers", 0) or 0)
        peak = int(db_metrics.get("peak_viewers", 0) or 0)

    return {
        "current": current,
        "total": total,
        "peak": peak,
        "by_lang_label": by_lang_label,
    }


def validate_room_creation_input(name: str) -> tuple[bool, str]:
    """룸 생성 폼 검증.

    이 함수는 server-side 검증의 일부이며, UI 의 클라이언트 검증을
    통과한 입력에 대해서도 한 번 더 server-side 에서 호출되어야 한다
    (RL-002 — 클라이언트 입력은 신뢰하지 않음).

    타임아웃 검증은 #86(auto-timeout 제거)과 함께 삭제됨 — 룸 종료는
    admin 수동 관리로 일원화.
    """
    name = (name or "").strip()
    if not name:
        return False, "룸 이름은 필수입니다."
    if len(name) > 100:
        return False, "룸 이름은 100자 이하여야 합니다."
    return True, ""


# ============================================================
# ISSUE-39: 무대 화면 설정 폼 (관리자 전용, Streamlit-free)
# ============================================================

# RL-006: ValueError 가 아닌 예외(디스크 오류 등)에 붙이는 일반 메시지.
# branding_assets 가 OSError 를 변환할 때 쓰는 문구와 같은 표현을 써서
# 관리자가 보는 실패 안내가 경로별로 달라지지 않게 한다.
UPLOAD_FAILED_MESSAGE = "로고 파일을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."

_DELETE_FAILED_MESSAGE = "로고 삭제에 실패했습니다."
_DELETE_CONFIG_ONLY_MESSAGE = "파일이 이미 없어 설정에서만 제거했습니다."
_DELETE_OK_MESSAGE = "로고를 삭제했습니다."


def _clean_text(value: Any) -> str:
    """문자열이 아니면 빈 문자열. 앞뒤 공백 제거 (stage_config 와 같은 규칙)."""
    return value.strip() if isinstance(value, str) else ""


def build_stage_config_from_form(
    *,
    event_title: str,
    event_subtitle: str,
    caption_ratio: str,
    logo_groups: dict[str, list[str]] | None,
) -> dict[str, Any]:
    """폼 입력을 ``stage_config`` 후보 dict 로 조립한다 (검증은 하지 않는다).

    Args:
        event_title: 행사 타이틀 입력값.
        event_subtitle: 행사 부제 입력값.
        caption_ratio: 자막 컬럼 비율 라디오 선택값. **보정하지 않고 그대로**
            담는다 — 잘못된 값은 ``validate_stage_config`` 가 사유와 함께
            거절해야 관리자가 무엇이 틀렸는지 알 수 있다.
        logo_groups: ``{그룹 라벨: [파일명, ...]}``. 알려진 라벨 3종이 항상
            고정 순서로 먼저 오고, 알 수 없는 라벨이 있으면 뒤에 덧붙인다.

    알 수 없는 라벨을 여기서 조용히 지우지 않는 것은 의도다.
    ``normalize_stage_config`` 가 그것을 버릴 때
    :func:`describe_stage_config_drops` 가 관리자에게 알릴 수 있어야 한다.

    Returns:
        ``DEFAULT_STAGE_CONFIG`` 와 같은 키를 가진 새 dict.
    """
    groups = dict(logo_groups or {})
    ordered_labels = list(LOGO_GROUP_LABELS) + [
        label for label in groups if label not in LOGO_GROUP_LABELS
    ]
    return {
        "event_title": _clean_text(event_title),
        "event_subtitle": _clean_text(event_subtitle),
        "caption_ratio": caption_ratio,
        "logo_groups": [
            {"label": label, "assets": list(groups.get(label) or [])}
            for label in ordered_labels
        ],
    }


def partition_uploads(
    room_id: str,
    uploads: Iterable[tuple[str, bytes | None]] | None,
    *,
    save_fn=None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """업로드 목록을 저장 시도해 (수락, 거부) 로 나눈다.

    Args:
        room_id: 대상 룸 id.
        uploads: ``(파일명, 바이트)`` 쌍의 이터러블. Streamlit 의
            ``UploadedFile`` 을 여기까지 끌고 오지 않기 위해 호출자가 미리
            쌍으로 변환한다 (이 모듈은 Streamlit 타입을 모른다).
        save_fn: 저장 함수. 기본값은 ``branding_assets.save_asset``.

    Returns:
        ``(accepted, rejected)``.
        ``accepted`` 는 **실제 저장된 파일명** 목록 — 충돌 회피로 이름이
        바뀔 수 있으므로 config 에는 반드시 이 값을 기록해야 한다.
        ``rejected`` 는 ``(원본 파일명, 사람이 읽는 사유)`` 목록이며, 사유는
        ``save_asset`` 이 만든 한국어 문자열을 그대로 전달한다 (용량/확장자
        /개수 규칙을 admin 계층에서 재구현하지 않기 위해서다).

    한 건이 거부되어도 나머지 업로드는 계속 처리한다.
    """
    save = save_fn or save_asset
    accepted: list[str] = []
    rejected: list[tuple[str, str]] = []

    for filename, data in uploads or []:
        try:
            accepted.append(save(room_id, filename, data))
        except ValueError as e:
            # save_asset 이 관리자에게 그대로 보여줄 수 있는 사유를 만든다.
            rejected.append((filename, str(e)))
        except Exception as e:
            # RL-006: 예상치 못한 예외의 str(e) 는 내부 경로를 담을 수 있다.
            print(f"[Admin] 로고 저장 실패 (room={room_id} name={filename!r}): {e!r}")
            rejected.append((filename, UPLOAD_FAILED_MESSAGE))

    return accepted, rejected


def delete_stage_logo(
    *,
    room_model: Any,
    room_id: str,
    filename: str,
    delete_fn=None,
) -> tuple[bool, str]:
    """로고 파일을 지우고 ``stage_config`` 에서도 제거한다.

    파일 삭제 결과와 **무관하게** config 갱신을 수행한다 — 디스크에서 이미
    사라진 파일이 설정에는 남아 무대 페이지가 깨진 이미지를 참조하는 상태를
    막기 위해서다 (파일만 지우고 config 를 두는 회귀 방지).

    Args:
        room_model: ``get_stage_config`` / ``update_stage_config`` 를 가진 모델.
        room_id: 대상 룸 id.
        filename: 삭제할 파일명.
        delete_fn: 삭제 함수. 기본값은 ``branding_assets.delete_asset``.

    Returns:
        ``(ok, 메시지)``. ``ok`` 는 **설정 갱신 성공 여부** 다 — 파일이 이미
        없었더라도 설정이 정리되었으면 성공으로 본다.
    """
    delete = delete_fn or delete_asset

    try:
        file_removed = bool(delete(room_id, filename))

        config = room_model.get_stage_config(room_id)
        for group in config.get("logo_groups") or []:
            if isinstance(group, dict) and isinstance(group.get("assets"), list):
                group["assets"] = [name for name in group["assets"] if name != filename]

        updated = room_model.update_stage_config(room_id, config)
    except Exception as e:
        # RL-006: DB/파일시스템 예외 상세는 서버 콘솔에만.
        print(f"[Admin] 로고 삭제 실패 (room={room_id} name={filename!r}): {e!r}")
        return False, _DELETE_FAILED_MESSAGE

    if not updated:
        return False, _DELETE_FAILED_MESSAGE
    return True, _DELETE_OK_MESSAGE if file_removed else _DELETE_CONFIG_ONLY_MESSAGE


def describe_stage_config_drops(
    candidate: Any,
    normalized: Any,
) -> list[str]:
    """``normalize_stage_config`` 가 조용히 버린 값을 한국어 경고로 설명한다.

    정규화는 무대 화면이 절대 죽지 않도록 알 수 없는 값을 말없이 폐기한다.
    저장 경로에서는 그 침묵이 곧 "저장한 줄 알았는데 없어진" 상황이 되므로,
    관리자에게 무엇이 빠졌는지 알려 준다 (ISSUE-37 리뷰 F-5).

    Args:
        candidate: 정규화 전 후보 config.
        normalized: ``normalize_stage_config(candidate)`` 결과.

    Returns:
        경고 문자열 목록. 버려진 값이 없으면 빈 리스트.
    """
    if not isinstance(candidate, dict) or not isinstance(normalized, dict):
        return []

    candidate_groups = candidate.get("logo_groups")
    if not isinstance(candidate_groups, list):
        return []

    kept: dict[str, list[Any]] = {}
    for group in normalized.get("logo_groups") or []:
        if isinstance(group, dict) and isinstance(group.get("assets"), list):
            kept[group.get("label")] = group["assets"]

    warnings: list[str] = []
    seen_labels: set[str] = set()
    for group in candidate_groups:
        if not isinstance(group, dict):
            continue
        label = group.get("label")
        assets = group.get("assets") if isinstance(group.get("assets"), list) else []

        if label not in kept or label in seen_labels:
            # 알 수 없는 라벨이거나, 같은 라벨이 중복돼 뒤엣것이 폐기된 경우.
            kind = "중복된" if label in seen_labels else "알 수 없는"
            names = ", ".join(str(asset) for asset in assets)
            detail = (
                f"의 항목이 저장되지 않았습니다: {names}"
                if names
                else "은(는) 저장되지 않았습니다."
            )
            warnings.append(f"{kind} 로고 그룹 '{label}' {detail}")
            continue

        seen_labels.add(label)
        dropped = [asset for asset in assets if asset not in kept[label]]
        if dropped:
            names = ", ".join(str(asset) for asset in dropped)
            warnings.append(f"'{label}' 그룹에서 저장되지 않은 항목: {names}")

    return warnings


def find_asset_drift(
    config: Any,
    disk_names: Iterable[str] | None,
) -> tuple[list[str], list[str]]:
    """``stage_config`` 와 실제 디스크 파일 목록의 불일치를 찾는다.

    Args:
        config: 정규화된 무대 설정 dict.
        disk_names: ``branding_assets.list_assets(room_id)`` 결과.

    Returns:
        ``(missing, orphaned)``.
        ``missing`` — 설정이 참조하지만 디스크에 없는 파일 (무대 페이지에서
        깨진 이미지가 되어 조용히 숨겨진다).
        ``orphaned`` — 업로드는 되었지만 어느 그룹에서도 참조하지 않는 파일
        (룸당 12개 상한만 갉아먹는다).
        둘 다 이름순 정렬 + 중복 제거.
    """
    referenced: set[str] = set()
    groups = config.get("logo_groups") if isinstance(config, dict) else None
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        for name in group.get("assets") or []:
            if isinstance(name, str):
                referenced.add(name)

    on_disk = {name for name in (disk_names or []) if isinstance(name, str)}
    return sorted(referenced - on_disk), sorted(on_disk - referenced)
