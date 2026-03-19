"""
실시간 자막 서비스 - Streamlit 메인 UI
모든 비즈니스 로직은 별도 모듈에 위치:
- translation.py: 번역 서비스
- services.py: OpenAI/AWS 세션 관리
- websocket_handler.py: WebSocket 서버/핸들러
"""

import asyncio
import json
import os
import threading

import streamlit as st
from dotenv import load_dotenv

from auth import (
    display_login_form,
    display_user_info,
    get_current_user,
    init_session_state,
    is_authenticated,
)
from services import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    create_openai_session,
)
from websocket_handler import start_websocket_server

# Load environment variables
load_dotenv()

# WebSocket 포트를 dict로 관리 (스레드 간 공유)
if "websocket_port_ref" not in st.session_state:
    st.session_state["websocket_port_ref"] = {"port": None}

# 사이드바 상태 관리
if "sidebar_state" not in st.session_state:
    st.session_state["sidebar_state"] = "expanded"

# 페이지 설정
st.set_page_config(
    page_title="실시간 자막",
    layout="wide",
    initial_sidebar_state=st.session_state["sidebar_state"],
)

# 사용자 인증 체크
init_session_state()

if not is_authenticated():
    display_login_form()
    st.stop()

# ALB/프록시 환경에서 HTTPS 지원을 위한 설정
try:
    import streamlit.web.server.server as _st_server  # noqa: E402

    _st_server.ENABLE_XSRF_PROTECTION = True
    os.environ.setdefault("STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION", "true")
except Exception:
    pass

# 전체 페이지 스크롤 방지 (외부 HTML 파일)
try:
    with open("components/scroll_lock.html", encoding="utf-8") as f:
        scroll_lock_html = f.read()
    st.markdown(scroll_lock_html, unsafe_allow_html=True)
except FileNotFoundError:
    pass

# === 사이드바 ===
with st.sidebar:
    st.header("🧑‍💻 유저 정보")
    display_user_info()

    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        st.error("⚠️ AWS 자격 증명이 설정되지 않았습니다.")
        st.info("💡 .env 파일에 AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY를 설정하세요")
        st.stop()

    # 시스템 제어
    st.subheader("🎛️ 시스템 제어")
    col1, col2 = st.columns([1, 1])

    current_status = st.session_state.get("action", "idle")

    with col1:
        start_disabled = current_status in [
            "start",
            "starting",
        ]
        start = st.button(
            "🎯 시작",
            type="primary",
            use_container_width=True,
            disabled=start_disabled,
        )

    with col2:
        stop_disabled = current_status in [
            "idle",
            "stop",
            "error",
        ]
        stop = st.button(
            "⏹️ 정지",
            use_container_width=True,
            disabled=stop_disabled,
        )

    # 시스템 상태
    st.markdown("---")
    st.subheader("📊 시스템 상태")
    status = st.session_state.get("action", "idle")
    if status == "start":
        st.success("🟢 서비스 실행 중")
        st.info("💡 자막 세부 설정은 화면 우상단 ⚙️ 버튼을 클릭하세요")
    elif status == "error":
        st.error("🔴 오류 발생")
    else:
        st.info("🟡 대기 중")

    # WebSocket 포트 정보 표시
    ws_port = st.session_state["websocket_port_ref"]["port"]
    if ws_port:
        st.markdown("---")
        st.subheader("🔗 연결 정보")
        st.code(f"WebSocket: ws://localhost:{ws_port}")
        if (
            st.session_state.get("websocket_thread")
            and st.session_state["websocket_thread"].is_alive()
        ):
            st.success("🟢 WebSocket 서버 실행 중")
        else:
            st.warning("🟡 WebSocket 서버 대기 중")


# === WebSocket 서버 스레드 ===
if (
    "websocket_thread" not in st.session_state
    or not st.session_state["websocket_thread"].is_alive()
):
    if "websocket_thread" in st.session_state:
        if st.session_state["websocket_thread"].is_alive():
            print("[WebSocket] 기존 스레드가 여전히 실행 중입니다.")
        else:
            print("[WebSocket] 기존 스레드 정리 중...")

    print("[WebSocket] 새로운 WebSocket 스레드 시작 중...")
    port_ref = st.session_state["websocket_port_ref"]
    st.session_state["websocket_thread"] = threading.Thread(
        target=start_websocket_server,
        args=(port_ref,),
        daemon=True,
    )
    st.session_state["websocket_thread"].start()
    print("[WebSocket] WebSocket 스레드 시작됨")


# === Session state ===
if "action" not in st.session_state:
    st.session_state["action"] = "idle"

# === Handle actions ===
openai_session = None
if start:
    try:
        st.session_state["sidebar_state"] = "expanded"

        with st.spinner("시작 중..."):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            openai_session = loop.run_until_complete(create_openai_session())
            loop.close()

            st.session_state["openai_session"] = openai_session
            st.session_state["action"] = "start"
            st.rerun()
    except ValueError as e:
        st.error(f"❌ {e!s}")
        st.session_state["action"] = "error"
        st.session_state["sidebar_state"] = "expanded"
        st.rerun()

elif stop:
    st.session_state["sidebar_state"] = "expanded"
    st.session_state["action"] = "stop"
    st.session_state.pop("openai_session", None)
    st.rerun()

# === 메인 캡션 뷰어 ===
try:
    with open("components/webrtc.html", encoding="utf-8") as f:
        html_template = f.read()

    current_user = get_current_user()

    payload = {
        "action": st.session_state["action"],
        "openai_session": st.session_state.get("openai_session"),
        "service": "openai_realtime",
        "websocket_port": st.session_state["websocket_port_ref"]["port"],
        "user_info": current_user,
    }

    html_content = html_template.replace("{{BOOTSTRAP_JSON}}", json.dumps(payload))
    st.components.v1.html(html_content, height=900, scrolling=False)

except Exception:
    st.error("❌ 시스템을 로드할 수 없습니다.")
