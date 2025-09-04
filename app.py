import json
import os

import requests
import streamlit as st
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-4o-realtime-preview")

# 사이드바 상태 관리
if "sidebar_state" not in st.session_state:
    st.session_state["sidebar_state"] = "expanded"  # 기본값을 expanded로

# 페이지 설정 - 사이드바 상태를 동적으로 설정
st.set_page_config(
    page_title="실시간 자막",
    layout="wide",
    initial_sidebar_state=st.session_state["sidebar_state"],  # 동적으로 설정
)

# 전체 페이지 스크롤 방지 + iframe margin 추가
st.markdown(
    """
<style>
    .main > div {
        padding-top: 0rem !important;
        padding-bottom: 0rem !important;
    }

    .stApp {
        overflow: hidden !important;
        height: 100vh !important;
    }

    .main {
        overflow: hidden !important;
        height: 100vh !important;
    }

    /* 🎯 iframe에 top margin 적용 */
    .main iframe {
        height: 85vh !important;
    }

    /* 또는 전체 컨테이너에 여백 */
    .main > div > div {
        padding-top: 5vh !important;
    }
</style>
""",
    unsafe_allow_html=True,
)

# 상위 시스템 관리 패널
with st.sidebar:
    st.header("🏢 시스템 관리")

    if not OPENAI_API_KEY:
        st.error("⚠️ API Key가 설정되지 않았습니다.")
        st.info("💡 .env 파일에 OPENAI_API_KEY를 설정하세요")
        st.stop()

    # 시스템 제어
    st.subheader("🎛️ 시스템 제어")
    col1, col2 = st.columns([1, 1])

    current_status = st.session_state.get("action", "idle")

    with col1:
        start_disabled = current_status in ["start", "starting"]
        start = st.button(
            "🎯 시작", type="primary", use_container_width=True, disabled=start_disabled
        )

    with col2:
        stop_disabled = current_status in ["idle", "stop", "error"]
        stop = st.button("⏹️ 정지", use_container_width=True, disabled=stop_disabled)

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


def create_ephemeral_session(model: str) -> dict:
    """OpenAI Realtime API 세션 생성"""
    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("sk-test-"):
        raise ValueError("유효하지 않은 API 키입니다.")

    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "realtime=v1",
    }
    body = {
        "model": model,
        "modalities": ["text", "audio"],
        "instructions": "Professional English-to-Korean real-time translator.",
    }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=15)
        r.raise_for_status()
        response_data = r.json()

        if "client_secret" not in response_data:
            raise ValueError("토큰 발급에 실패했습니다.")

        return response_data

    except requests.exceptions.RequestException as e:
        raise ValueError(f"API 요청 실패: {str(e)}") from e


# Session state
if "action" not in st.session_state:
    st.session_state["action"] = "idle"

# Handle actions
ephemeral = None
if start:
    try:
        # 사이드바 상태 유지
        st.session_state["sidebar_state"] = "expanded"

        with st.spinner("시작 중..."):
            ephemeral = create_ephemeral_session(REALTIME_MODEL)
            st.session_state["ephemeral"] = ephemeral
            st.session_state["action"] = "start"
            st.rerun()
    except ValueError as e:
        st.error(f"❌ {str(e)}")
        st.session_state["action"] = "error"
        st.session_state["sidebar_state"] = "expanded"  # 에러 시에도 유지
        st.rerun()

elif stop:
    # 사이드바 상태 유지
    st.session_state["sidebar_state"] = "expanded"
    st.session_state["action"] = "stop"
    st.session_state.pop("ephemeral", None)
    st.rerun()

# 메인 캡션 뷰어
try:
    with open("components/webrtc.html", encoding="utf-8") as f:
        html_template = f.read()

    payload = {
        "action": st.session_state["action"],
        "ephemeral": st.session_state.get("ephemeral"),
        "model": REALTIME_MODEL,
    }

    html_content = html_template.replace("{{BOOTSTRAP_JSON}}", json.dumps(payload))
    st.components.v1.html(html_content, height=900, scrolling=False)

except Exception:
    st.error("❌ 시스템을 로드할 수 없습니다.")
