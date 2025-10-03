"""
로그인 페이지
사용자 인증을 위한 독립적인 로그인 인터페이스
"""

import streamlit as st

from auth import init_session_state, is_authenticated, login_user


def show_login_page():
    """로그인 페이지 표시"""
    st.set_page_config(
        page_title="실시간 자막 서비스 - 로그인", page_icon="🔐", layout="centered"
    )

    init_session_state()

    # 이미 로그인된 경우 메인 페이지로 리다이렉트
    if is_authenticated():
        st.success("이미 로그인되어 있습니다.")
        st.info(
            "메인 페이지로 이동하거나 브라우저 주소창에서 다른 페이지로 이동하세요."
        )
        return

    # 로그인 페이지 헤더
    st.markdown(
        """
    <div style="text-align: center; padding: 2rem 0;">
        <h1>🎙️ 실시간 자막 서비스</h1>
        <p style="color: #666; font-size: 1.1em;">다국어 실시간 음성 인식 및 번역 서비스</p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # 로그인 폼
    with st.container():
        col1, col2, col3 = st.columns([1, 2, 1])

        with col2:
            st.markdown("### 🔐 로그인")

            with st.form("login_form", clear_on_submit=False):
                username = st.text_input(
                    "사용자명",
                    placeholder="사용자명을 입력하세요",
                    help="관리자로부터 받은 사용자명을 입력하세요",
                )

                password = st.text_input(
                    "비밀번호",
                    type="password",
                    placeholder="비밀번호를 입력하세요",
                    help="관리자로부터 받은 비밀번호를 입력하세요",
                )

                col1, col2 = st.columns(2)

                with col1:
                    login_button = st.form_submit_button(
                        "로그인", use_container_width=True, type="primary"
                    )

                with col2:
                    admin_button = st.form_submit_button(
                        "관리자 페이지", use_container_width=True
                    )

                if login_button:
                    if not username or not password:
                        st.error("사용자명과 비밀번호를 모두 입력해주세요.")
                    else:
                        if login_user(username, password):
                            st.success("로그인 성공!")
                            st.info(
                                "메인 페이지로 이동하려면 브라우저 주소창에서 `/`로 이동하세요."
                            )
                            st.balloons()
                        else:
                            st.error("사용자명 또는 비밀번호가 잘못되었습니다.")

                if admin_button:
                    if not username or not password:
                        st.error("관리자 계정 정보를 입력해주세요.")
                    else:
                        if login_user(username, password):
                            # 관리자 권한 확인
                            from auth import is_admin

                            if is_admin():
                                st.success("관리자 로그인 성공!")
                                st.info(
                                    "관리자 페이지로 이동하려면 브라우저 주소창에서 `/admin`으로 이동하세요."
                                )
                                st.balloons()
                            else:
                                st.error("관리자 권한이 없습니다.")
                        else:
                            st.error("사용자명 또는 비밀번호가 잘못되었습니다.")

    # 서비스 소개
    with st.container():
        st.markdown("---")
        st.markdown("### 📋 서비스 기능")

        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown(
                """
            **🎙️ 실시간 음성 인식**
            - OpenAI Realtime API 활용
            - 다국어 자동 인식
            - 고품질 음성-텍스트 변환
            """
            )

        with col2:
            st.markdown(
                """
            **🌍 실시간 번역**
            - 영어 ↔ 한국어
            - 기타 언어 → 한국어
            - 자연스러운 번역 결과
            """
            )

        with col3:
            st.markdown(
                """
            **⏱️ 사용량 관리**
            - 개인별 사용량 추적
            - 실시간 사용량 모니터링
            - 투명한 과금 시스템
            """
            )

    # 문의 정보
    with st.container():
        st.markdown("---")
        st.markdown(
            """
        <div style="text-align: center; color: #666; padding: 1rem 0;">
            <p>계정이 없으신가요? 관리자에게 문의하여 계정을 발급받으세요.</p>
            <p>🔧 관리자 계정을 가지고 계신 경우, 위의 "관리자 페이지" 버튼을 클릭하세요.</p>
        </div>
        """,
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    show_login_page()
