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
from auth import display_login_form, init_session_state, is_authenticated

init_session_state()

# 로그인되지 않은 경우 로그인 폼 표시
if not is_authenticated():
    st.title("🔧 관리자 대시보드")
    st.warning("관리자 로그인이 필요합니다.")
    display_login_form()
    st.stop()

# 관리자 대시보드 표시
show_admin_dashboard()
