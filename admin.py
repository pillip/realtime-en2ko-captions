"""
관리자 대시보드 페이지
사용자 계정 생성/관리 및 사용량 통계 조회
ISSUE-29: 룸 관리 탭 추가, 오퍼레이터 역할 기반 뷰 분기.
ISSUE-32: 룸별 QR 코드 PNG 다운로드.
"""

import os
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from admin_logic import (
    DEFAULT_ROOM_LIST_STATUSES,
    ROOM_STATUS_FILTER_OPTIONS,
    build_room_metrics_view_data,
    export_room_logs_csv,
    filter_rooms_by_status,
    filter_rooms_for_role,
    format_room_status,
    get_logs_for_operator,
    prepare_room_table_data,
    validate_room_creation_input,
)
from auth import (
    display_user_info,
    get_current_user,
    require_admin_or_operator,
)
from database import (
    InvalidRoomTransition,
    get_room_model,
    get_usage_log_model,
    get_user_model,
)
from qr_generator import build_view_url, make_qr_png


def _resolve_viewer_base_url() -> str:
    """``VIEWER_BASE_URL`` 환경변수 → ``http://localhost:{SSE_PORT}`` 로 fallback.

    ISSUE-32: admin.py 의 룸 관리 탭이 룸별 QR PNG 를 만들 때 가리킬
    뷰어 페이지 base URL 을 결정한다. app.py 와 동일한 규칙으로
    환경변수가 없거나 비어 있으면 안전하게 로컬 SSE 포트를 사용한다.

    RL-006 관점: 잘못된 환경변수가 들어와도 traceback 누설 없이
    호출자가 항상 truthy URL 을 받도록 한다.
    """
    raw = os.getenv("VIEWER_BASE_URL", "").strip()
    if raw:
        return raw
    sse_port = int(os.getenv("SSE_PORT", "8766"))
    return f"http://localhost:{sse_port}"


@require_admin_or_operator
def show_admin_dashboard():
    """관리자/오퍼레이터 대시보드 메인 (ISSUE-29).

    role="user" 와 비인증 사용자는 데코레이터에서 차단된다 (RL-002 —
    페이지 진입은 server-side session 만 신뢰). 표시 콘텐츠는 추가로
    각 탭 내부에서 역할별로 좁힌다 — 데코레이터 통과 ≠ 모든 데이터 접근.
    """
    current_user = get_current_user() or {}
    role = current_user.get("role", "")
    is_role_admin = role == "admin"

    if is_role_admin:
        st.title("🔧 관리자 대시보드")
    else:
        st.title("🎧 오퍼레이터 대시보드")

    display_user_info(show_divider=False)  # 구분선 제거

    user_model = get_user_model()
    usage_log_model = get_usage_log_model()
    room_model = get_room_model()

    # 세션 상태에서 선택된 탭 관리
    if "selected_tab" not in st.session_state:
        st.session_state.selected_tab = 0

    # 메뉴 탭 (오퍼레이터는 사용자 관리/생성 탭 비표시 — 자기 룸과 기록만)
    if is_role_admin:
        tabs = st.tabs(
            [
                "사용자 관리",
                "신규 사용자 생성",
                "사용량 통계",
                "로그 조회",
                "룸 관리",
            ]
        )
        with tabs[0]:
            show_user_management(user_model)
        with tabs[1]:
            success = show_create_user_form(user_model)
            if success:
                st.session_state.selected_tab = 0
                st.rerun()
        with tabs[2]:
            show_usage_statistics(usage_log_model, user_model)
        with tabs[3]:
            show_usage_logs(usage_log_model, user_model)
        with tabs[4]:
            show_room_management(
                room_model=room_model,
                user_model=user_model,
                usage_log_model=usage_log_model,
                current_user=current_user,
            )
    else:
        # 오퍼레이터: 룸 관리 (자기 룸 보기/기록 다운로드) 만 노출
        tabs = st.tabs(["내 룸"])
        with tabs[0]:
            show_room_management(
                room_model=room_model,
                user_model=user_model,
                usage_log_model=usage_log_model,
                current_user=current_user,
            )


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
            format_func=lambda x: (
                f"{next(u['username'] for u in users if u['id'] == x)} (ID: {x})"
            ),
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
                "이 작업은 되돌릴 수 없으며, 해당 사용자의 "
                "모든 사용량 로그도 함께 삭제됩니다."
            )

            col1, col2, col3 = st.columns([1, 1, 2])

            with col1:
                if st.button("🗑️ 삭제", type="primary", use_container_width=True):
                    success = user_model.delete_user(selected_user_id)
                    if success:
                        st.success(
                            f"사용자 '{selected_user['username']}'이(가) "
                            "삭제되었습니다."
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
        _success = st.session_state.user_creation_success
        st.success(
            f"✅ 사용자 '{_success['username']}'이 성공적으로 "
            f"생성되었습니다! (ID: {_success['user_id']})"
        )
        st.info(
            "**로그인 정보**\n\n"
            f"사용자명: `{_success['username']}`\n"
            f"비밀번호: `{_success['password']}`"
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


# ============================================================
# ISSUE-29: 룸 관리 탭 (admin: 전체 / operator: 자기 룸만)
# ============================================================
def show_room_management(
    *,
    room_model,
    user_model,
    usage_log_model,
    current_user,
):
    """룸 관리 탭 진입점.

    server-side 분기 (RL-002):
      - admin: 룸 CRUD/배정/강제종료, 모든 룸의 기록 조회/CSV
      - operator: 본인에게 배정된 룸만 표시, 그 룸의 기록만 조회/CSV

    UI 가 어떤 room_id 를 보내든, ``filter_rooms_for_role`` 와
    ``get_logs_for_operator`` 가 admin_logic 레벨에서 다시 검증하므로
    클라이언트가 다른 룸의 데이터에 접근할 수 없다.
    """
    role = current_user.get("role", "")
    user_id = current_user.get("id")
    is_role_admin = role == "admin"

    # ------------------------------------------------------------------
    # 데이터 로드 — server-side 화이트리스트 적용
    # ------------------------------------------------------------------
    all_rooms = room_model.list_all()
    visible_rooms = filter_rooms_for_role(all_rooms, user_role=role, user_id=user_id)
    users = user_model.get_all_users() if is_role_admin else []
    user_id_to_username = {u["id"]: u["username"] for u in users}
    if not is_role_admin:
        # 오퍼레이터는 사용자 목록 권한이 없으므로 자기 username 만 매핑.
        user_id_to_username = {user_id: current_user.get("username", "-")}

    # ------------------------------------------------------------------
    # 룸 목록 표시
    # ------------------------------------------------------------------
    listed_rooms = visible_rooms
    if is_role_admin:
        st.subheader("🏠 룸 목록")
        # ISSUE-85: 상태 필터 — 기본은 closed 숨김. 종료 룸은 기록
        # 조회/CSV 용도로만 필터에서 명시 선택 시 노출한다.
        selected_statuses = st.multiselect(
            "상태 필터",
            options=list(ROOM_STATUS_FILTER_OPTIONS),
            default=list(DEFAULT_ROOM_LIST_STATUSES),
            format_func=format_room_status,
        )
        listed_rooms = filter_rooms_by_status(visible_rooms, selected_statuses)
    else:
        st.subheader("🏠 내 룸 목록")

    if not listed_rooms:
        if is_role_admin:
            st.info(
                "표시할 룸이 없습니다. 상태 필터를 확인하거나 "
                "아래에서 새 룸을 생성하세요."
            )
        else:
            st.info("배정된 룸이 없습니다. 관리자에게 문의하세요.")
    else:
        rows = prepare_room_table_data(listed_rooms, user_id_to_username)
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # ------------------------------------------------------------------
    # ISSUE-33: 룸별 뷰어 지표 (현재/누적/최대 + 언어별)
    # ------------------------------------------------------------------
    _render_room_viewer_metrics(visible_rooms, room_model)

    # ------------------------------------------------------------------
    # ISSUE-32: 룸별 QR 코드 다운로드 (admin: 전체 / operator: 자기 룸)
    # ------------------------------------------------------------------
    _render_room_qr_section(visible_rooms)

    # ------------------------------------------------------------------
    # 관리자 전용: 룸 생성 / 배정 / 강제 종료
    # ------------------------------------------------------------------
    if is_role_admin:
        _render_admin_room_create(room_model, user_model, current_user)
        _render_admin_assign_operator(room_model, user_model, visible_rooms)
        _render_admin_force_close(room_model, visible_rooms)

    # ------------------------------------------------------------------
    # 룸별 기록 조회 / CSV 다운로드 (admin 전체 / operator 자기 룸)
    # ------------------------------------------------------------------
    _render_room_logs_section(
        room_model=room_model,
        usage_log_model=usage_log_model,
        visible_rooms=visible_rooms,
        role=role,
        user_id=user_id,
    )


def _render_admin_room_create(room_model, user_model, current_user):
    """관리자 룸 생성 폼."""
    st.divider()
    st.subheader("➕ 새 룸 생성")
    with st.form("create_room_form"):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("룸 이름 *", help="회의 이름 (예: A홀 기조연설)")
            input_lang = st.selectbox(
                "입력 언어",
                options=["auto", "en", "ko", "ja", "zh"],
                index=0,
                help="auto = 자동 감지",
            )
        with col2:
            output_lang = st.selectbox(
                "출력 언어",
                options=["ko", "en", "ja", "zh"],
                index=0,
            )
            # 타임아웃 입력은 #86(auto-timeout 제거)과 함께 삭제 —
            # 룸 종료는 아래 '강제 종료' 로 admin 이 수동 수행한다.

        submitted = st.form_submit_button("룸 생성")
        if submitted:
            valid, error = validate_room_creation_input(name)
            if not valid:
                st.error(error)
                return
            try:
                from room_manager import _new_room_id

                room_id = _new_room_id()
                room_model.create(
                    room_id=room_id,
                    name=name.strip(),
                    created_by=current_user["id"],
                    input_lang=input_lang,
                    output_lang=output_lang,
                )
                st.success(f"룸 '{name}' 생성됨 (ID: {room_id})")
                st.rerun()
            except Exception as e:
                # RL-006: 내부 예외는 server-side 만 로그, 사용자에게는 일반 메시지.
                print(f"[Admin] 룸 생성 실패: {e!r}")
                st.error("룸 생성에 실패했습니다.")


def _render_admin_assign_operator(room_model, user_model, visible_rooms):
    """관리자 오퍼레이터 배정 폼."""
    st.divider()
    st.subheader("👤 오퍼레이터 배정")

    if not visible_rooms:
        st.caption("배정할 룸이 없습니다.")
        return

    # 오퍼레이터 후보: role in ('operator', 'admin')
    all_users = user_model.get_all_users()
    operator_candidates = [
        u for u in all_users if u.get("role") in ("operator", "admin")
    ]
    if not operator_candidates:
        st.caption("오퍼레이터로 배정 가능한 사용자가 없습니다.")
        return

    col1, col2 = st.columns(2)
    with col1:
        room_choice = st.selectbox(
            "룸 선택",
            options=[r["id"] for r in visible_rooms],
            format_func=lambda rid: next(
                f"{r['name']} ({rid})" for r in visible_rooms if r["id"] == rid
            ),
            key="assign_room_select",
        )
    with col2:
        op_choice = st.selectbox(
            "오퍼레이터 선택",
            options=[u["id"] for u in operator_candidates],
            format_func=lambda uid: next(
                f"{u['username']} ({u['role']})"
                for u in operator_candidates
                if u["id"] == uid
            ),
            key="assign_op_select",
        )

    if st.button("배정", key="assign_op_button"):
        ok = room_model.assign_operator(room_choice, op_choice)
        if ok:
            st.success("오퍼레이터가 배정되었습니다.")
            st.rerun()
        else:
            st.error("배정에 실패했습니다.")


def _render_admin_force_close(room_model, visible_rooms):
    """관리자 룸 강제 종료."""
    st.divider()
    st.subheader("🛑 룸 강제 종료")
    closeable = [r for r in visible_rooms if r.get("status") != "closed"]
    if not closeable:
        st.caption("종료 대상 룸이 없습니다.")
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        target_room = st.selectbox(
            "종료할 룸",
            options=[r["id"] for r in closeable],
            format_func=lambda rid: next(
                f"{r['name']} ({r.get('status', '')})"
                for r in closeable
                if r["id"] == rid
            ),
            key="force_close_select",
        )
    with col2:
        if st.button("강제 종료", type="primary", key="force_close_button"):
            try:
                ok = room_model.force_close(target_room)
                if ok:
                    st.success("룸이 종료되었습니다.")
                    st.rerun()
                else:
                    st.error("종료할 룸을 찾을 수 없습니다.")
            except InvalidRoomTransition as e:
                # 이론상 force_close 는 idempotent — 도달 시에도 generic.
                print(f"[Admin] 룸 종료 전이 오류: {e!r}")
                st.error("룸 종료에 실패했습니다.")


def _render_room_logs_section(
    *,
    room_model,
    usage_log_model,
    visible_rooms,
    role,
    user_id,
):
    """역할별 룸 로그 조회/CSV 다운로드."""
    st.divider()
    st.subheader("📋 룸별 대화 기록")

    if not visible_rooms:
        st.caption("조회 가능한 룸이 없습니다.")
        return

    selected_room_id = st.selectbox(
        "기록을 조회할 룸",
        options=[r["id"] for r in visible_rooms],
        format_func=lambda rid: next(
            f"{r['name']} ({r['id']})" for r in visible_rooms if r["id"] == rid
        ),
        key="logs_room_select",
    )

    # admin_logic 가 다시 한 번 server-side 권한 검증 (RL-002 방어 레이어).
    logs = get_logs_for_operator(
        usage_log_model=usage_log_model,
        room_model=room_model,
        requested_room_id=selected_room_id,
        user_role=role,
        user_id=user_id,
    )

    if not logs:
        st.info("해당 룸의 대화 기록이 없습니다.")
        return

    # 표 표시 (간략)
    df_rows = []
    for log in logs[:200]:  # 화면 표시는 최근 200개
        metadata = log.get("metadata") or {}
        df_rows.append(
            {
                "ID": log["id"],
                "사용자": log.get("username", log.get("user_id", "-")),
                "원문": metadata.get("source_text", "") if metadata else "",
                "번역문": metadata.get("target_text", "") if metadata else "",
                "시간(초)": log["duration_seconds"],
                "생성일시": log.get("created_at", ""),
            }
        )
    st.dataframe(pd.DataFrame(df_rows), use_container_width=True)

    # CSV 다운로드 — 전체 룸 로그 (200 limit 무시)
    csv_bytes = export_room_logs_csv(logs, selected_room_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    st.download_button(
        label="📥 CSV 다운로드",
        data=csv_bytes,
        file_name=f"room_{selected_room_id}_logs_{timestamp}.csv",
        mime="text/csv",
    )


def _render_room_qr_section(visible_rooms):
    """ISSUE-32: 룸별 QR 코드 PNG 다운로드 섹션.

    - 룸이 없으면 섹션 자체를 렌더하지 않는다 (빈 UI 노이즈 방지).
    - 각 룸마다 ``room_{name}.png`` 파일로 다운로드 가능 — 인쇄/사전 배포용.
    - QR 생성에 실패해도 다른 룸 처리는 계속되도록 per-row try/except.
    - RL-001: ``qr_generator`` 는 import-time 부수효과 없는 순수 모듈.
    - RL-006: 내부 예외는 console 로만 남기고 사용자에게는 generic toast.
    """
    if not visible_rooms:
        return

    st.divider()
    st.subheader("📱 룸 QR 코드")
    st.caption(
        "QR 을 스캔하면 해당 룸의 뷰어 페이지로 이동합니다. "
        "인쇄해서 행사장에 비치하세요."
    )

    base_url = _resolve_viewer_base_url()

    for room in visible_rooms:
        room_id = room.get("id")
        room_name = room.get("name") or room_id or "room"
        if not room_id:
            continue

        try:
            view_url = build_view_url(room_id, base_url)
            png_bytes = make_qr_png(view_url)
        except Exception as e:
            # RL-006: 내부 예외는 server-side 로그만, 사용자에게는 generic.
            print(f"[Admin] 룸 QR 생성 실패 (room={room_id}): {e!r}")
            st.warning(f"'{room_name}' 룸의 QR 코드를 만들 수 없습니다.")
            continue

        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**{room_name}** &nbsp;&nbsp; `{view_url}`")
        with col2:
            # 파일명에 사용 불가능한 문자 (예: 슬래시) 가 들어가지 않도록 sanitize.
            safe_name = (
                "".join(
                    ch if ch.isalnum() or ch in ("-", "_") else "_"
                    for ch in str(room_name)
                )
                or "room"
            )
            st.download_button(
                label="📥 QR PNG",
                data=png_bytes,
                file_name=f"room_{safe_name}.png",
                mime="image/png",
                key=f"qr_dl_{room_id}",
            )


def _render_room_viewer_metrics(visible_rooms, room_model):
    """ISSUE-33: 룸별 뷰어 지표 섹션 (현재/누적/최대 + 언어별).

    구성:
      - 룸이 없으면 섹션 자체를 그리지 않는다 (빈 UI 노이즈 방지).
      - 각 룸마다 4 개의 ``st.metric`` (현재 / 누적 / 최대 / 언어별 라벨).
      - 인메모리 current 는 ``BroadcastManager.get_metrics`` 로,
        DB 누적/peak 는 ``Room.get_viewer_metrics`` 로 조회한다.
        한쪽이 실패하더라도 다른 쪽 표시가 망가지지 않도록 per-row
        try/except 로 격리한다 (RL-006: 내부 디테일을 사용자에게 노출 X).

    a11y (RL-010):
      - 각 ``st.metric`` 의 첫번째 인자는 사람이 읽는 한국어 라벨 — 아이콘만
        있는 버튼이 아니므로 추가 aria-label 은 필요 없다.
      - 언어별 요약은 시각/스크린리더 모두 "한국어 45명, 중국어 12명"
        으로 읽힌다.
    """
    if not visible_rooms:
        return

    # 지연 import — websocket_handler 는 streamlit 환경 변수 등 부수효과가
    # 있으므로 모듈 import-time 비용을 admin 페이지 진입 시로 미룬다.
    try:
        from websocket_handler import get_broadcast_manager

        manager = get_broadcast_manager()
    except Exception as e:
        # RL-006: 내부 예외는 server-side 만 로그, 사용자에게는 generic.
        print(f"[Admin] BroadcastManager 로딩 실패: {e!r}")
        st.warning("뷰어 지표를 불러올 수 없습니다.")
        return

    st.divider()
    st.subheader("📈 뷰어 지표")
    st.caption(
        "현재/누적/최대 뷰어 수와 언어별 분포. 현재 값은 새로고침 시 갱신됩니다."
    )

    for room in visible_rooms:
        rid = room.get("id")
        room_name = room.get("name") or rid or "room"
        if not rid:
            continue

        try:
            in_memory = manager.get_metrics(rid)
            db_metrics = room_model.get_viewer_metrics(rid)
            view = build_room_metrics_view_data(
                in_memory=in_memory, db_metrics=db_metrics
            )
        except Exception as e:
            # 한 룸의 실패가 다른 룸의 표시를 깨지 않도록 격리.
            print(f"[Admin] 룸 지표 조회 실패 (room={rid}): {e!r}")
            st.warning(f"'{room_name}' 룸의 지표를 불러올 수 없습니다.")
            continue

        st.markdown(f"**{room_name}** &nbsp; `{rid}`")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("현재 뷰어", view["current"])
        with col2:
            st.metric("누적 뷰어", view["total"])
        with col3:
            st.metric("최대 동시", view["peak"])
        with col4:
            label = view["by_lang_label"] or "0명"
            # st.metric 의 value 인자에 텍스트가 들어가도 정상 렌더된다.
            st.metric("언어별", label)


if __name__ == "__main__":
    show_admin_dashboard()
