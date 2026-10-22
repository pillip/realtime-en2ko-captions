"""
관리자 대시보드 페이지
"""

import os
import sys

import streamlit as st

# 상위 디렉토리를 path에 추가하여 모듈 import 가능하게 함
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin import show_admin_dashboard

# 페이지 설정
st.set_page_config(page_title="관리자 대시보드", page_icon="🔧", layout="wide")

# 인증 초기화 (쿠키에서 세션 복원 포함)
from auth import (
    display_login_form,
    flush_pending_session_cookie,
    init_session_state,
    is_authenticated,
)

init_session_state()

# 로그인되지 않은 경우 로그인 폼 표시
if not is_authenticated():
    st.title("🔧 관리자 대시보드")
    st.warning("관리자 로그인이 필요합니다.")
    display_login_form()
    st.stop()

# 관리자 대시보드 표시
show_admin_dashboard()

# 로그인 직후 예약된 쿠키 쓰기를 스크립트 끝에서 소비한다 (#165).
# 관리자 페이지에는 캡션 컴포넌트가 없지만, 순서 규칙을 app.py 와 동일하게 둔다.
flush_pending_session_cookie()
