import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-4o-mini-realtime-preview")

st.set_page_config(page_title="Live EN→KO Captions", layout="wide")
st.title("🎤 Live EN→KO Captions (Streamlit + Realtime)")
st.markdown("**실시간 영어 → 한국어 자막 시스템** | WebRTC + OpenAI Realtime API")

# --- Controls (sidebar)
with st.sidebar:
    st.header("🎛️ Controls")
    
    if not OPENAI_API_KEY:
        st.error("⚠️ OPENAI_API_KEY가 설정되지 않았습니다.")
        st.info("환경변수 또는 .env 파일에 OPENAI_API_KEY를 설정해주세요.")
        st.stop()
    
    st.success("✅ API Key 설정됨")
    
    start = st.button("🎯 시작", type="primary", help="실시간 캡션 시작")
    stop = st.button("⏹️ 정지", help="연결 종료 및 캡션 초기화")
    
    st.markdown("---")
    st.subheader("🎚️ Settings")
    st.text_input("모델", value=REALTIME_MODEL, disabled=True)
    
    device_info = st.empty()
    device_info.info("💡 시작 후 오디오 장치를 선택할 수 있습니다.")


def create_ephemeral_session(model: str) -> dict:
    """OpenAI Realtime API에서 에페메럴 세션 토큰을 생성합니다."""
    assert OPENAI_API_KEY, "OPENAI_API_KEY not set"
    
    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "realtime=v1",
    }
    body = {
        "model": model,
        # "voice": "none",  # 음성 출력 사용 안 함
    }
    
    r = requests.post(url, headers=headers, json=body, timeout=10)
    r.raise_for_status()
    return r.json()


# --- Session state initialization
if 'action' not in st.session_state:
    st.session_state['action'] = 'idle'

# --- Handle button clicks
ephemeral = None
if start:
    try:
        with st.spinner("에페메럴 토큰 생성 중..."):
            ephemeral = create_ephemeral_session(REALTIME_MODEL)
            st.session_state["ephemeral"] = ephemeral
            st.session_state["action"] = "start"
        st.success("✅ 에페메럴 토큰 발급 완료! 브라우저에서 마이크 권한을 허용해주세요.")
    except Exception as e:
        st.error(f"❌ 토큰 발급 실패: {e}")
        st.session_state["action"] = "error"
elif stop:
    st.session_state["action"] = "stop"
    st.info("⏹️ 연결을 종료합니다.")
else:
    st.session_state.setdefault("action", "idle")

# --- Main content area
st.markdown("### 🎥 실시간 캡션 뷰어")

# Load and embed WebRTC component
try:
    with open("components/webrtc.html", "r", encoding="utf-8") as f:
        html_template = f.read()
    
    # Prepare payload for the component
    payload = {
        "action": st.session_state["action"],
        "ephemeral": st.session_state.get("ephemeral"),
        "model": REALTIME_MODEL,
    }
    
    # Replace template placeholder with actual data
    html_content = html_template.replace("{{BOOTSTRAP_JSON}}", json.dumps(payload))
    
    # Embed the component
    st.components.v1.html(html_content, height=600, scrolling=True)
    
except FileNotFoundError:
    st.error("❌ WebRTC 컴포넌트 파일을 찾을 수 없습니다. (components/webrtc.html)")
    st.info("👆 **시작** 버튼을 클릭하여 실시간 캡션을 시작하세요.")
    st.markdown("""
    **사용 방법:**
    1. 시작 버튼 클릭
    2. 브라우저에서 마이크 권한 허용
    3. 오디오 장치 선택
    4. 영어로 말하면 한국어 자막이 실시간으로 표시됩니다
    """)
except Exception as e:
    st.error(f"❌ 컴포넌트 로드 실패: {e}")

# Footer
st.markdown("---")
st.markdown("🔧 **Debug Info**")
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Status", st.session_state["action"])
with col2:
    st.metric("Model", REALTIME_MODEL)
with col3:
    has_token = "✅" if st.session_state.get("ephemeral") else "❌"
    st.metric("Token", has_token)