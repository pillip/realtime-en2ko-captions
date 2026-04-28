"""
admin.py에서 추출한 순수 비즈니스 로직 함수들
Streamlit 의존성 없이 테스트 가능
"""

import csv
import io


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
