import json
import os

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
    """OpenAI Realtime API에서 에페메럴 세션 토큰을 생성합니다.

    Args:
        model: 사용할 OpenAI 모델명 (예: gpt-4o-mini-realtime-preview)

    Returns:
        dict: 에페메럴 토큰과 세션 정보가 포함된 응답

    Raises:
        requests.exceptions.RequestException: API 호출 실패 시
        ValueError: 잘못된 응답 형식 시
    """
    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("sk-test-"):
        raise ValueError(
            "유효하지 않은 OPENAI_API_KEY입니다. 실제 API 키를 설정해주세요."
        )

    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "realtime=v1",
    }
    body = {
        "model": model,
        "modalities": ["text", "audio"],
        "instructions": "You are a helpful assistant that translates English speech to Korean captions in real-time.",
    }

    try:
        st.write(f"🔄 토큰 요청 중... (모델: {model})")
        r = requests.post(url, headers=headers, json=body, timeout=15)

        if r.status_code == 401:
            raise ValueError(
                "API 키가 유효하지 않습니다. OPENAI_API_KEY를 확인해주세요."
            )
        elif r.status_code == 429:
            raise ValueError("API 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요.")
        elif r.status_code == 400:
            error_detail = r.json().get("error", {}).get("message", "알 수 없는 오류")
            raise ValueError(f"잘못된 요청: {error_detail}")

        r.raise_for_status()
        response_data = r.json()

        # 응답 검증
        if "client_secret" not in response_data:
            raise ValueError("응답에 client_secret이 없습니다.")

        st.write(f"✅ 토큰 발급 성공! (만료: {response_data.get('expires_at', 'N/A')})")
        return response_data

    except requests.exceptions.Timeout as e:
        raise ValueError(
            "API 요청 시간이 초과되었습니다. 네트워크 연결을 확인해주세요."
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise ValueError(
            "OpenAI API에 연결할 수 없습니다. 인터넷 연결을 확인해주세요."
        ) from e
    except requests.exceptions.RequestException as e:
        raise ValueError(f"API 요청 실패: {str(e)}") from e


# --- Session state initialization
if "action" not in st.session_state:
    st.session_state["action"] = "idle"

# --- Handle button clicks
ephemeral = None
if start:
    # Clear previous error messages
    if "error_message" in st.session_state:
        del st.session_state["error_message"]

    try:
        with st.spinner("에페메럴 토큰 생성 중..."):
            ephemeral = create_ephemeral_session(REALTIME_MODEL)
            st.session_state["ephemeral"] = ephemeral
            st.session_state["action"] = "start"
            st.session_state["token_created_at"] = ephemeral.get(
                "expires_at", "Unknown"
            )

        # Success message with detailed info
        expires_at = ephemeral.get("expires_at", "Unknown")
        st.success(f"✅ 에페메럴 토큰 발급 완료! (만료: {expires_at})")
        st.info("🎤 브라우저에서 마이크 권한을 허용하고 오디오 장치를 선택해주세요.")

    except ValueError as e:
        st.error(f"❌ {str(e)}")
        st.session_state["action"] = "error"
        st.session_state["error_message"] = str(e)
    except Exception as e:
        error_msg = f"예상치 못한 오류가 발생했습니다: {str(e)}"
        st.error(f"❌ {error_msg}")
        st.session_state["action"] = "error"
        st.session_state["error_message"] = error_msg
        # Log detailed error for debugging
        st.write("**디버그 정보:**")
        st.code(f"Error type: {type(e).__name__}\nError details: {str(e)}")

elif stop:
    st.session_state["action"] = "stop"
    st.session_state.pop("ephemeral", None)  # Clear token
    st.session_state.pop("error_message", None)  # Clear errors
    st.info("⏹️ 연결을 종료하고 토큰을 삭제했습니다.")
else:
    st.session_state.setdefault("action", "idle")

# --- Main content area
st.markdown("### 🎥 실시간 캡션 뷰어")

# Load and embed WebRTC component
try:
    with open("components/webrtc.html", encoding="utf-8") as f:
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
    st.markdown(
        """
    **사용 방법:**
    1. 시작 버튼 클릭
    2. 브라우저에서 마이크 권한 허용
    3. 오디오 장치 선택
    4. 영어로 말하면 한국어 자막이 실시간으로 표시됩니다
    """
    )
except Exception as e:
    st.error(f"❌ 컴포넌트 로드 실패: {e}")

# Footer
st.markdown("---")
st.markdown("🔧 **Debug Info**")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Status", st.session_state["action"])
with col2:
    st.metric("Model", REALTIME_MODEL)
with col3:
    has_token = "✅" if st.session_state.get("ephemeral") else "❌"
    st.metric("Token", has_token)
with col4:
    token_expires = st.session_state.get("token_created_at", "N/A")
    st.metric("Token Expires", token_expires if token_expires != "Unknown" else "N/A")

# Show error details if any
if st.session_state.get("error_message"):
    with st.expander("❌ 오류 세부정보", expanded=False):
        st.text(st.session_state["error_message"])

# Show token details if available
if st.session_state.get("ephemeral"):
    with st.expander("🔑 토큰 정보", expanded=False):
        token_data = st.session_state["ephemeral"]
        st.json(
            {
                "client_secret_preview": token_data.get("client_secret", {}).get(
                    "value", ""
                )[:20]
                + "...",
                "expires_at": token_data.get("expires_at", "Unknown"),
                "session_id": token_data.get("id", "Unknown"),
            }
        )
