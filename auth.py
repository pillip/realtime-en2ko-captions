"""
인증 관련 유틸리티 모듈
Streamlit session_state를 활용한 사용자 인증 및 권한 관리
"""

from collections.abc import Callable
from functools import wraps
from typing import Any

import streamlit as st

from database import get_user_model


def init_session_state():
    """세션 상태 초기화"""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "user" not in st.session_state:
        st.session_state.user = None


def login_user(username: str, password: str) -> bool:
    """사용자 로그인"""
    user_model = get_user_model()
    user = user_model.authenticate(username, password)

    if user:
        st.session_state.authenticated = True
        st.session_state.user = user
        return True

    return False


def logout_user():
    """사용자 로그아웃"""
    st.session_state.authenticated = False
    st.session_state.user = None


def get_current_user() -> dict[str, Any] | None:
    """현재 로그인된 사용자 정보 반환"""
    if not st.session_state.get("authenticated", False):
        return None
    return st.session_state.get("user")


def is_authenticated() -> bool:
    """인증 상태 확인"""
    return st.session_state.get("authenticated", False)


def is_admin() -> bool:
    """관리자 권한 확인"""
    user = get_current_user()
    return user is not None and user.get("role") == "admin"


def is_user_active() -> bool:
    """사용자 활성 상태 확인"""
    user = get_current_user()
    return user is not None and user.get("is_active", False)


def get_user_remaining_seconds() -> int | None:
    """현재 사용자의 남은 사용 가능 시간 조회"""
    user = get_current_user()
    if not user:
        return None

    user_model = get_user_model()
    return user_model.get_remaining_seconds(user["id"])


def check_usage_limit(duration_seconds: int, user_info: dict = None) -> bool:
    """사용량 제한 확인 (사용 가능한지 체크)"""
    # WebSocket에서 사용자 정보가 직접 전달된 경우
    if user_info:
        user_model = get_user_model()
        remaining = user_model.get_remaining_seconds(user_info["id"])

        # 관리자는 사용량 제한 없음
        if user_info.get("role") == "admin":
            return True

        return remaining is not None and remaining >= duration_seconds

    # Streamlit context에서 사용자 정보 가져오기 (기존 방식)
    try:
        remaining = get_user_remaining_seconds()
        if remaining is None:
            return False

        # 관리자는 사용량 제한 없음 (usage_limit_seconds = 0)
        user = get_current_user()
        if user and user.get("role") == "admin":
            return True

        return remaining >= duration_seconds
    except Exception:
        # Streamlit context가 없는 경우 (WebSocket 등)
        return False


def require_auth(func: Callable) -> Callable:
    """인증 필요 데코레이터"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        init_session_state()

        if not is_authenticated():
            st.warning("로그인이 필요합니다.")
            st.stop()

        if not is_user_active():
            st.error("비활성화된 계정입니다. 관리자에게 문의하세요.")
            st.stop()

        return func(*args, **kwargs)

    return wrapper


def require_admin(func: Callable) -> Callable:
    """관리자 권한 필요 데코레이터"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        init_session_state()

        if not is_authenticated():
            st.warning("로그인이 필요합니다.")
            st.stop()

        if not is_admin():
            st.error("관리자 권한이 필요합니다.")
            st.stop()

        return func(*args, **kwargs)

    return wrapper


def display_user_info(show_divider=True):
    """사용자 정보 표시 (사이드바)"""
    user = get_current_user()
    if not user:
        return

    with st.sidebar:
        st.write(f"**아이디**: {user['username']}")
        if user["full_name"]:
            st.write(f"**소속**: {user['full_name']}")

        # # 사용량 정보 (관리자가 아닌 경우)
        # if user["role"] != "admin":
        #     # DB에서 최신 사용량 조회
        #     user_model = get_user_model()
        #     remaining = user_model.get_remaining_seconds(user["id"])

        #     if remaining is not None:
        #         updated_user = user_model.get_user_by_id(user["id"])
        #         total_limit = updated_user["usage_limit_seconds"]
        #         used_seconds = total_limit - remaining

        #         st.write(f"**사용량**: {used_seconds}초 / {total_limit}초")
        #         st.caption("※ 정지 버튼 클릭 시 최신 사용량으로 업데이트됩니다")

        #         # 진행률 바
        #         progress = used_seconds / total_limit if total_limit > 0 else 0
        #         st.progress(min(progress, 1.0))

        #         if remaining <= 0:
        #             st.error("사용량이 초과되었습니다.")
        #         elif remaining <= 300:  # 5분 미만
        #             st.warning(f"남은 시간: {remaining}초")
        #         else:
        #             st.info(f"남은 시간: {remaining}초")
        # else:
        #     st.info("관리자 (무제한 사용)")

        if st.button("로그아웃"):
            logout_user()
            st.rerun()


def display_login_form():
    """로그인 폼 표시"""
    st.title("🔐 로그인")

    with st.form("login_form"):
        username = st.text_input("사용자명")
        password = st.text_input("비밀번호", type="password")
        submit_button = st.form_submit_button("로그인")

        if submit_button:
            if not username or not password:
                st.error("사용자명과 비밀번호를 입력해주세요.")
                return False

            if login_user(username, password):
                st.rerun()
            else:
                st.error("사용자명 또는 비밀번호가 잘못되었습니다.")
                return False

    return False


def update_user_session(user_id: int):
    """세션의 사용자 정보 업데이트 (사용량 변경 등 반영)"""
    if not is_authenticated():
        return

    current_user = get_current_user()
    if not current_user or current_user["id"] != user_id:
        return

    user_model = get_user_model()
    updated_user = user_model.get_user_by_id(user_id)

    if updated_user:
        st.session_state.user = updated_user
