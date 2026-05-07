"""
admin.py에서 추출한 순수 비즈니스 로직 함수들
Streamlit 의존성 없이 테스트 가능
"""

from __future__ import annotations

import csv
import io
from typing import Any

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
                "입력언어": room.get("input_lang", ""),
                "출력언어": room.get("output_lang", ""),
                "생성자": creator_label,
                "타임아웃(분)": room.get("timeout_minutes", ""),
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


def validate_room_creation_input(
    name: str,
    timeout_minutes: int,
) -> tuple[bool, str]:
    """룸 생성 폼 검증.

    이 함수는 server-side 검증의 일부이며, UI 의 클라이언트 검증을
    통과한 입력에 대해서도 한 번 더 server-side 에서 호출되어야 한다
    (RL-002 — 클라이언트 입력은 신뢰하지 않음).
    """
    name = (name or "").strip()
    if not name:
        return False, "룸 이름은 필수입니다."
    if len(name) > 100:
        return False, "룸 이름은 100자 이하여야 합니다."
    if timeout_minutes < 1:
        return False, "타임아웃은 1분 이상이어야 합니다."
    if timeout_minutes > 1440:
        return False, "타임아웃은 1440분(24시간) 이하여야 합니다."
    return True, ""
