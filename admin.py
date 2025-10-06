"""
관리자 대시보드 페이지
사용자 계정 생성/관리 및 사용량 통계 조회
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from auth import display_user_info, require_admin
from database import get_usage_log_model, get_user_model


@require_admin
def show_admin_dashboard():
    """관리자 대시보드 메인"""
    st.title("🔧 관리자 대시보드")

    display_user_info(show_divider=False)  # 구분선 제거

    user_model = get_user_model()
    usage_log_model = get_usage_log_model()

    # 세션 상태에서 선택된 탭 관리
    if "selected_tab" not in st.session_state:
        st.session_state.selected_tab = 0

    # 메뉴 탭
    tabs = st.tabs(["사용자 관리", "신규 사용자 생성", "사용량 통계", "로그 조회"])

    with tabs[0]:  # 사용자 관리
        show_user_management(user_model)

    with tabs[1]:  # 신규 사용자 생성
        success = show_create_user_form(user_model)
        if success:
            st.session_state.selected_tab = 0  # 사용자 관리 탭으로 이동
            st.rerun()

    with tabs[2]:  # 사용량 통계
        show_usage_statistics(usage_log_model, user_model)

    with tabs[3]:  # 로그 조회
        show_usage_logs(usage_log_model, user_model)


def show_user_management(user_model):
    """사용자 관리 탭"""
    st.subheader("👥 사용자 목록")

    users = user_model.get_all_users()

    if not users:
        st.info("등록된 사용자가 없습니다.")
        return

    # 사용자 목록을 데이터프레임으로 변환
    df_data = []
    for user in users:
        remaining_seconds = user_model.get_remaining_seconds(user["id"])
        remaining_minutes = (
            remaining_seconds / 60 if remaining_seconds is not None else 0
        )

        df_data.append(
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

    df = pd.DataFrame(df_data)
    st.dataframe(df, use_container_width=True)

    # 사용자 수정/삭제 섹션
    st.subheader("✏️ 사용자 정보 수정")

    col1, col2 = st.columns(2)

    with col1:
        selected_user_id = st.selectbox(
            "수정할 사용자 선택",
            options=[user["id"] for user in users],
            format_func=lambda x: f"{next(u['username'] for u in users if u['id'] == x)} (ID: {x})",
        )

    if selected_user_id:
        selected_user = next(user for user in users if user["id"] == selected_user_id)

        with col2:
            st.write(f"**선택된 사용자**: {selected_user['username']}")

        # 수정 폼과 삭제 버튼을 탭으로 분리
        tab1, tab2 = st.tabs(["정보 수정", "사용자 삭제"])

        with tab1:
            with st.form(f"edit_user_{selected_user_id}"):
                col1, col2 = st.columns(2)

                with col1:
                    new_email = st.text_input(
                        "이메일", value=selected_user["email"] or ""
                    )
                    new_full_name = st.text_input(
                        "소속", value=selected_user["full_name"] or ""
                    )
                    new_role = st.selectbox(
                        "역할",
                        options=["user", "admin"],
                        index=0 if selected_user["role"] == "user" else 1,
                    )

                with col2:
                    new_is_active = st.checkbox(
                        "활성 상태", value=selected_user["is_active"]
                    )
                    new_usage_limit = st.number_input(
                        "사용 제한 (초)",
                        min_value=0,
                        value=selected_user["usage_limit_seconds"],
                        step=3600,
                        help="0이면 무제한",
                    )
                    add_usage = st.number_input(
                        "사용량 추가 (초)",
                        min_value=0,
                        value=0,
                        step=3600,
                        help="현재 제한량에 추가할 시간",
                    )

                submit_button = st.form_submit_button("수정 적용")

                if submit_button:
                    # 사용자 정보 업데이트
                    success = user_model.update_user(
                        user_id=selected_user_id,
                        email=new_email if new_email else None,
                        full_name=new_full_name if new_full_name else None,
                        role=new_role,
                        is_active=new_is_active,
                        usage_limit_seconds=new_usage_limit + add_usage,
                    )

                    if success:
                        st.success("사용자 정보가 업데이트되었습니다.")
                        st.rerun()
                    else:
                        st.error("업데이트에 실패했습니다.")

        with tab2:
            st.warning(
                f"⚠️ 사용자 '{selected_user['username']}'을(를) 삭제하시겠습니까?"
            )
            st.write(
                "이 작업은 되돌릴 수 없으며, 해당 사용자의 모든 사용량 로그도 함께 삭제됩니다."
            )

            col1, col2, col3 = st.columns([1, 1, 2])

            with col1:
                if st.button("🗑️ 삭제", type="primary", use_container_width=True):
                    success = user_model.delete_user(selected_user_id)
                    if success:
                        st.success(
                            f"사용자 '{selected_user['username']}'이(가) 삭제되었습니다."
                        )
                        st.rerun()
                    else:
                        st.error("삭제에 실패했습니다.")

            with col2:
                if st.button("취소", use_container_width=True):
                    st.info("삭제가 취소되었습니다.")


def show_create_user_form(user_model):
    """신규 사용자 생성 폼"""
    st.subheader("➕ 신규 사용자 생성")

    # 폼 외부에서 성공 메시지 표시
    if st.session_state.get("user_creation_success"):
        st.success(
            f"✅ 사용자 '{st.session_state.user_creation_success['username']}'이 성공적으로 생성되었습니다! (ID: {st.session_state.user_creation_success['user_id']})"
        )
        st.info(
            f"**로그인 정보**\n\n사용자명: `{st.session_state.user_creation_success['username']}`\n비밀번호: `{st.session_state.user_creation_success['password']}`"
        )
        # 다음 폼 제출을 위해 성공 상태 초기화
        st.session_state.user_creation_success = None

    user_created = False  # 성공 여부 추적

    with st.form("create_user_form"):
        col1, col2 = st.columns(2)

        with col1:
            username = st.text_input("사용자명 *", help="로그인에 사용될 고유한 ID")
            password = st.text_input(
                "비밀번호 *", type="password", help="최소 6자 이상 권장"
            )
            confirm_password = st.text_input("비밀번호 확인 *", type="password")

        with col2:
            email = st.text_input("이메일")
            full_name = st.text_input("소속")
            role = st.selectbox("역할", options=["user", "admin"], index=0)

        usage_limit_hours = st.number_input(
            "사용 가능 시간 (시간)",
            min_value=0.0,
            value=1.0,
            step=0.5,
            help="0이면 무제한 (관리자는 자동으로 무제한)",
        )

        submit_button = st.form_submit_button("사용자 생성")

        if submit_button:
            # 입력값 검증
            if not username or not password:
                st.error("사용자명과 비밀번호는 필수입니다.")
                return

            if password != confirm_password:
                st.error("비밀번호가 일치하지 않습니다.")
                return

            if len(password) < 6:
                st.error("비밀번호는 최소 6자 이상이어야 합니다.")
                return

            # 사용량 제한 계산 (관리자는 무제한)
            usage_limit_seconds = (
                0 if role == "admin" else int(usage_limit_hours * 3600)
            )

            # 사용자 생성
            user_id = user_model.create_user(
                username=username,
                password=password,
                email=email if email else None,
                full_name=full_name if full_name else None,
                role=role,
                usage_limit_seconds=usage_limit_seconds,
            )

            if user_id:
                # 세션 상태에 성공 정보 저장
                st.session_state.user_creation_success = {
                    "username": username,
                    "password": password,
                    "user_id": user_id,
                }
                user_created = True
                st.rerun()
            else:
                st.error(
                    "사용자 생성에 실패했습니다. 사용자명이 이미 존재할 수 있습니다."
                )

    return user_created


def show_usage_statistics(usage_log_model, user_model):
    """사용량 통계 탭"""
    st.subheader("📊 사용량 통계")

    # 사용자 선택
    users = user_model.get_all_users()
    user_options = {"전체": None}
    user_options.update({f"{u['username']} ({u['role']})": u["id"] for u in users})

    col1, col2, col3 = st.columns(3)

    with col1:
        selected_user_label = st.selectbox(
            "사용자 선택", options=list(user_options.keys()), index=0
        )
        selected_user_id = user_options[selected_user_label]

    # 기간 선택
    with col2:
        period = st.selectbox(
            "조회 기간", options=["전체", "최근 7일", "최근 30일", "사용자 지정"]
        )

    start_date = None
    end_date = None

    if period == "최근 7일":
        start_date = datetime.now() - timedelta(days=7)
    elif period == "최근 30일":
        start_date = datetime.now() - timedelta(days=30)
    elif period == "사용자 지정":
        with col3:
            start_date = st.date_input("시작일")
            end_date = st.date_input("종료일")

        if start_date:
            start_date = datetime.combine(start_date, datetime.min.time())
        if end_date:
            end_date = datetime.combine(end_date, datetime.max.time())

    # 통계 조회 (선택된 사용자 ID 포함)
    stats = usage_log_model.get_usage_stats(
        user_id=selected_user_id, start_date=start_date, end_date=end_date
    )

    # 전체 통계 표시
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("총 요청 수", stats["total_sessions"] or 0)

    with col2:
        total_duration = stats["total_duration"] or 0
        st.metric("총 사용 시간", f"{total_duration / 3600:.1f}시간")

    with col3:
        avg_duration = stats["avg_duration"] or 0
        st.metric("평균 요청 길이", f"{avg_duration:.1f}초")

    with col4:
        if stats["first_usage"] and stats["last_usage"]:
            usage_period = (
                datetime.fromisoformat(stats["last_usage"].replace("Z", "+00:00"))
                - datetime.fromisoformat(stats["first_usage"].replace("Z", "+00:00"))
            ).days
            st.metric("사용 기간", f"{usage_period}일")

    # 언어별 통계
    if stats["language_stats"]:
        st.subheader("🌐 언어별 사용 통계")

        lang_data = []
        for lang_stat in stats["language_stats"]:
            lang_data.append(
                {
                    "소스 언어": lang_stat["source_language"] or "미지정",
                    "대상 언어": lang_stat["target_language"] or "미지정",
                    "요청 수": lang_stat["session_count"],
                    "총 시간(초)": lang_stat["total_duration"],
                    "총 시간(분)": f"{lang_stat['total_duration'] / 60:.1f}",
                }
            )

        lang_df = pd.DataFrame(lang_data)
        st.dataframe(lang_df, use_container_width=True)


def show_usage_logs(usage_log_model, user_model):
    """사용량 로그 조회 탭"""
    st.subheader("📋 사용량 로그")

    # 사용자 선택
    users = user_model.get_all_users()
    user_options = {"전체": None}
    user_options.update({f"{u['username']} ({u['role']})": u["id"] for u in users})

    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        selected_user_label = st.selectbox(
            "사용자 선택",
            options=list(user_options.keys()),
            index=0,
            key="log_user_select",
        )
        selected_user_id = user_options[selected_user_label]

    with col2:
        # 페이지네이션 설정
        page_size = st.selectbox("페이지 당 항목 수", [10, 25, 50, 100], index=1)

    with col3:
        # CSV 다운로드 버튼 (특정 사용자 선택 시에만 표시)
        if selected_user_id:
            selected_username = next(
                u["username"] for u in users if u["id"] == selected_user_id
            )
            if st.button("📥 CSV 다운로드", use_container_width=True):
                # 전체 로그 조회
                all_logs = usage_log_model.get_all_user_logs(selected_user_id)

                if all_logs:
                    # CSV 데이터 생성
                    csv_data = []
                    for log in all_logs:
                        # metadata에서 source_text와 target_text 추출
                        metadata = log.get("metadata", {})
                        source_text = (
                            metadata.get("source_text", "") if metadata else ""
                        )
                        target_text = (
                            metadata.get("target_text", "") if metadata else ""
                        )

                        csv_data.append(
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

                    csv_df = pd.DataFrame(csv_data)

                    # CSV로 변환
                    csv_string = csv_df.to_csv(index=False, encoding="utf-8-sig")

                    # 다운로드 버튼
                    st.download_button(
                        label=f"💾 {selected_username}_로그.csv",
                        data=csv_string,
                        file_name=f"{selected_username}_usage_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                else:
                    st.warning("다운로드할 로그가 없습니다.")

    page_number = st.number_input("페이지", min_value=1, value=1) - 1
    offset = page_number * page_size

    # 로그 조회
    if selected_user_id:
        logs = usage_log_model.get_user_logs(
            user_id=selected_user_id, limit=page_size, offset=offset
        )
    else:
        logs = usage_log_model.get_all_logs(limit=page_size, offset=offset)

    if not logs:
        st.info("사용량 로그가 없습니다.")
        return

    # 로그를 데이터프레임으로 변환
    log_data = []
    for log in logs:
        # 사용자별 로그 조회일 때는 username이 없을 수 있음
        log_entry = {
            "ID": log["id"],
            "작업": log["action"],
            "시간(초)": log["duration_seconds"],
            "소스언어": log["source_language"] or "-",
            "대상언어": log["target_language"] or "-",
            "생성일시": log["created_at"],
            "메타데이터": str(log["metadata"]) if log["metadata"] else "-",
        }

        # username이 있는 경우만 추가
        if "username" in log:
            log_entry["사용자"] = log["username"]

        log_data.append(log_entry)

    log_df = pd.DataFrame(log_data)
    st.dataframe(log_df, use_container_width=True)

    # 페이지 정보
    st.write(f"페이지 {page_number + 1} (항목 {offset + 1}-{offset + len(logs)})")


if __name__ == "__main__":
    show_admin_dashboard()
