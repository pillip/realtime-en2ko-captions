"""
인증 관련 유틸리티 모듈
Streamlit session_state를 활용한 사용자 인증 및 권한 관리
extra-streamlit-components의 CookieManager로 세션 관리
"""

import hashlib
import hmac
import os
import secrets
import time
import warnings
from collections.abc import Callable
from functools import wraps
from typing import Any

import streamlit as st
from extra_streamlit_components import CookieManager

from database import get_user_model


def _get_session_secret() -> str:
    """SESSION_SECRET 환경변수를 반환. 없으면 자동 생성 후 경고."""
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        secret = secrets.token_hex(32)
        os.environ["SESSION_SECRET"] = secret
        warnings.warn(
            "SESSION_SECRET not set. Auto-generated a random secret, shared "
            "process-wide (Streamlit, WebSocket and SSE threads all read the "
            "same os.environ), so the current run works normally. What breaks "
            "is restarts: every cookie issued before one becomes invalid, so "
            "the operator must log in again before a session can be started — "
            "and since ISSUE-55 that re-login also gates WebSocket auth. "
            "Set SESSION_SECRET in production.",
            stacklevel=2,
        )
    return secret


def _compute_hmac(data: str) -> str:
    """HMAC-SHA256 서명 계산"""
    secret = _get_session_secret()
    return hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()


def _sign_cookie(user_id: int, username: str, token: str) -> str:
    """쿠키 데이터에 HMAC 서명 추가. 형식: user_id:username:token:hmac"""
    payload = f"{user_id}:{username}:{token}"
    signature = _compute_hmac(payload)
    return f"{payload}:{signature}"


def _verify_cookie(cookie_data: str) -> tuple[str, str, str] | None:
    """HMAC 서명 검증. 성공하면 (user_id, username, token) 반환, 실패하면 None."""
    try:
        parts = cookie_data.rsplit(":", 1)
        if len(parts) != 2:
            return None
        payload, signature = parts
        expected = _compute_hmac(payload)
        if not hmac.compare_digest(signature, expected):
            return None
        user_id, username, token = payload.split(":", 2)
        return user_id, username, token
    except (ValueError, TypeError):
        return None


# CookieManager 는 **세션 단위**로 보관한다 (#165). 모듈 전역 캐시는 금지.
_COOKIE_MANAGER_STATE_KEY = "_auth_cookie_manager"

# 다음 런에서 한 번 더 써야 할 쿠키 값 (#165). `set_session_cookie` 가 예약하고,
# **엔트리포인트 스크립트의 맨 끝**(`app.py` / `pages/admin_dashboard.py`)에서
# `flush_pending_session_cookie()` 가 소비한다.
_PENDING_COOKIE_STATE_KEY = "_auth_pending_cookie"


def get_cookie_manager():
    """현재 Streamlit **세션**의 CookieManager를 반환한다 (지연 초기화).

    **모듈 전역으로 캐시하면 안 된다.** `CookieManager.__init__` 은
    ``getAll`` 컴포넌트를 한 번 렌더하고 그 결과를 ``self.cookies`` 에
    **인스턴스 속성으로 캐시**한다(라이브러리 구현). 따라서 전역으로 공유하면
    모든 세션이 **맨 처음 세션의 쿠키 항아리**를 돌려받는다.

    그 결과가 두 가지였고 둘 다 실측했다 (#165):

    1. **보안** — 새 브라우저가 로그인 없이 직전 사용자 세션으로 복원된다.
       `restore_session_from_cookie()` 가 남의 쿠키 값을 읽기 때문이다.
    2. **기능** — 첫 세션이 아닌 컨텍스트에서는 로그인해도 `user_session`
       쿠키가 그 브라우저에 심기지 않는다. ISSUE-55 이후 WS 인증이 이 쿠키에
       의존하므로, 그 상태의 오퍼레이터는 자막을 **시작할 수 없다**.

    세션 단위로 만들면 각 세션이 자기 `getAll` 결과를 갖는다 — Streamlit 에서
    위젯 key 는 세션 스코프라 같은 key 를 그대로 써도 충돌하지 않는다.
    """
    manager = st.session_state.get(_COOKIE_MANAGER_STATE_KEY)
    if manager is None:
        manager = CookieManager(key="auth_cookie_manager")
        st.session_state[_COOKIE_MANAGER_STATE_KEY] = manager
    return manager


def generate_session_token(user_id: int, username: str) -> str:
    """세션 토큰 생성 (CSPRNG 기반)"""
    return secrets.token_hex(32)


def set_session_cookie(user_info: dict):
    """세션 쿠키 설정 (HMAC 서명 포함, CookieManager 사용).

    **쓰기를 두 번 건다** — 지금 한 번, 다음 런에 한 번 (#165).

    `CookieManager.set()` 은 컴포넌트를 렌더해 브라우저에 "이 쿠키를 써라" 를
    보내는 것이고, 그 지시는 **그 런의 델타가 전달될 때** 비로소 실행된다.
    그런데 이 함수의 호출 경로는 `login_user` ← `display_login_form` 이고,
    거기서는 성공 직후 `st.rerun()` 을 부른다. `st.rerun()` 은 즉시
    `RerunException` 을 던져 **그 런의 델타를 버리므로**, 쿠키 쓰기 지시가
    브라우저에 도달하지 못한다.

    실측(#165): 그 결과 로그인은 됐는데 `user_session` 쿠키만 없는 상태가 되고,
    ISSUE-55 이후 WS 인증이 이 쿠키에 의존하므로 오퍼레이터가 자막을 **시작할 수
    없다**.

    `st.rerun()` 을 걷어내는 쪽도 시도해 봤지만 로그인 UI 흐름이 바뀌어 e2e 가
    광범위하게 깨졌다. 대신 **다음 런에서 한 번 더 쓰도록 예약**한다 —
    `init_session_state()` 가 그 런 시작에 flush 하고, 그 런은 정상 종료하므로
    델타가 전달된다. 같은 값을 두 번 쓰는 것은 무해하다(멱등).
    """
    token = generate_session_token(user_info["id"], user_info["username"])
    signed_cookie = _sign_cookie(user_info["id"], user_info["username"], token)

    # 24시간 유효 (max_age는 초 단위)
    get_cookie_manager().set("user_session", signed_cookie, max_age=86400)
    # 호출자가 곧바로 rerun 해 위 델타가 버려질 경우를 대비한 예약.
    st.session_state[_PENDING_COOKIE_STATE_KEY] = signed_cookie


def flush_pending_session_cookie():
    """예약된 쿠키 쓰기를 **런 시작 시점**에 한 번 실행한다 (#165).

    `init_session_state()` 가 부른다. 런 시작에서 쓰면 그 뒤로 스크립트가
    정상 종료하므로 델타가 브라우저에 전달된다. 예약이 없으면 아무 일도
    하지 않는다.
    """
    pending = st.session_state.pop(_PENDING_COOKIE_STATE_KEY, None)
    if not pending:
        return
    try:
        get_cookie_manager().set("user_session", pending, max_age=86400)
    except Exception as e:  # noqa: BLE001
        # 쿠키 재시도 실패가 로그인 자체를 깨뜨려서는 안 된다 (RL-006).
        print(f"[Auth] 예약된 쿠키 쓰기 실패: {e!r}")


def get_session_cookie() -> str | None:
    """세션 쿠키에서 사용자 정보 가져오기 (CookieManager 사용)"""
    try:
        # get() 메서드로 직접 특정 쿠키 읽기 (더 안정적)
        session_data = get_cookie_manager().get(cookie="user_session")
        return session_data if session_data else None
    except Exception as e:
        print(f"[Auth] 쿠키 읽기 실패: {e}")
        return None


def clear_session_cookie():
    """세션 쿠키 삭제 (CookieManager 사용)"""
    get_cookie_manager().delete("user_session")


def init_session_state():
    """세션 상태 초기화"""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "user" not in st.session_state:
        st.session_state.user = None
    if "cookie_restore_attempted" not in st.session_state:
        st.session_state.cookie_restore_attempted = False

    # 쿠키에서 세션 복원 시도 (한 번만, 로그인 안 된 경우에만)
    if (
        not st.session_state.authenticated
        and not st.session_state.cookie_restore_attempted
    ):
        st.session_state.cookie_restore_attempted = True
        restore_session_from_cookie()


def restore_session_from_cookie():
    """쿠키에서 세션 복원 (HMAC 검증 포함, CookieManager 사용)"""
    session_data = get_session_cookie()

    if session_data:
        try:
            verified = _verify_cookie(session_data)
            if verified is None:
                print("[Auth] 세션 복원 실패: HMAC 검증 실패 (변조 의심)")
                return False

            user_id, username, token = verified
            user_model = get_user_model()
            user = user_model.get_user_by_id(int(user_id))

            if user and user["username"] == username and user["is_active"]:
                st.session_state.authenticated = True
                st.session_state.user = user
                print(f"[Auth] 세션 복원 성공: {username}")
                return True
        except (ValueError, TypeError) as e:
            print(f"[Auth] 세션 복원 실패: {e}")

    return False


def login_user(username: str, password: str) -> bool:
    """사용자 로그인"""
    user_model = get_user_model()
    user = user_model.authenticate(username, password)

    if user:
        st.session_state.authenticated = True
        st.session_state.user = user
        st.session_state.cookie_restore_attempted = True
        # 쿠키 설정
        set_session_cookie(user)
        return True

    return False


def logout_user():
    """사용자 로그아웃"""
    st.session_state.authenticated = False
    st.session_state.user = None
    st.session_state.cookie_restore_attempted = False
    # 쿠키 삭제
    clear_session_cookie()


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


def is_operator() -> bool:
    """오퍼레이터 권한 확인 (ISSUE-29).

    "operator" 는 별도 역할이며 admin 과 겹치지 않는다. admin 도 허용해야
    하는 경우 :func:`is_admin_or_operator` 를 사용한다 — 이 분리로 호출자가
    각각의 권한 의미를 명시적으로 표현할 수 있다 (RL-002 — 역할 신뢰는
    server-side session 에서만).
    """
    user = get_current_user()
    return user is not None and user.get("role") == "operator"


def is_admin_or_operator() -> bool:
    """admin 또는 operator 역할 보유 여부 (ISSUE-29).

    관리자 대시보드는 두 역할 모두 진입 가능하지만 보여줄 데이터는
    호출지점에서 별도로 좁힌다 (admin_logic.filter_rooms_for_role).
    """
    user = get_current_user()
    if user is None:
        return False
    return user.get("role") in ("admin", "operator")


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


def require_admin_or_operator(func: Callable) -> Callable:
    """admin 또는 operator 권한 필요 데코레이터 (ISSUE-29).

    관리자 대시보드 진입 시 두 역할 모두 통과시키되, role="user" 와
    비인증 사용자는 차단한다. 표시 가능한 데이터는 호출자가
    :func:`admin_logic.filter_rooms_for_role` 등을 통해 다시 좁힌다 —
    데코레이터는 페이지 진입 검증만 책임지고, 콘텐츠 가시성은 별도로
    server-side 에서 검증한다 (RL-002).
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        init_session_state()

        if not is_authenticated():
            st.warning("로그인이 필요합니다.")
            st.stop()

        if not is_admin_or_operator():
            st.error("관리자 또는 오퍼레이터 권한이 필요합니다.")
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
    # 이미 인증되었으면 아무것도 표시하지 않음
    if is_authenticated():
        return True

    # CSS로 폼을 숨기는 스타일 (로그인 처리 중일 때)
    if "login_submitted" in st.session_state and st.session_state.login_submitted:
        # 폼을 숨기는 CSS
        st.markdown(
            """
            <style>
            /* 폼 전체 숨기기 */
            [data-testid="stForm"] {
                display: none !important;
            }
            /* 타이틀도 숨기기 */
            h1 {
                display: none !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        # 로딩 메시지 표시
        st.info("로그인 중...")

        # 0.5초 동안 처리 중 메시지 표시
        time.sleep(0.5)

        # 로그인 처리
        username = st.session_state.get("temp_username", "")
        password = st.session_state.get("temp_password", "")

        if username and password:
            if login_user(username, password):
                # 임시 데이터 정리
                st.session_state.pop("temp_username", None)
                st.session_state.pop("temp_password", None)
                st.session_state.pop("login_submitted", None)
                st.rerun()
            else:
                # 로그인 실패 - 상태 초기화
                st.session_state.login_submitted = False
                st.session_state.pop("temp_username", None)
                st.session_state.pop("temp_password", None)
                st.error("사용자명 또는 비밀번호가 잘못되었습니다.")
                time.sleep(1)  # 에러 메시지를 잠시 표시
                st.rerun()
        return False

    # 로그인 폼 표시
    st.title("로그인")

    with st.form("login_form"):
        username = st.text_input("사용자명")
        password = st.text_input("비밀번호", type="password")
        submit_button = st.form_submit_button("로그인")

        if submit_button:
            if not username or not password:
                st.error("사용자명과 비밀번호를 입력해주세요.")
            else:
                # 임시로 데이터 저장하고 상태 변경
                st.session_state.temp_username = username
                st.session_state.temp_password = password
                st.session_state.login_submitted = True
                st.rerun()

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
