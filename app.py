import json
import os

import requests
import streamlit as st
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-4o-realtime-preview")

# 페이지 설정 - 제목 제거하고 깔끔하게
st.set_page_config(
    page_title="실시간 자막",
    layout="wide",
    initial_sidebar_state="collapsed",  # 기본적으로 사이드바 숨김
)

# --- 숨겨진 컨트롤 패널
with st.sidebar:
    st.header("⚙️ 시스템 설정")

    if not OPENAI_API_KEY:
        st.error("API Key가 설정되지 않았습니다.")
        st.stop()

    # 간단한 상태 표시만
    col1, col2 = st.columns([1, 1])
    with col1:
        start = st.button("🎯 시작", type="primary", use_container_width=True)
    with col2:
        stop = st.button("⏹️ 정지", use_container_width=True)


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

        if r.status_code == 401:
            raise ValueError("API 키가 유효하지 않습니다.")
        elif r.status_code == 429:
            raise ValueError("API 요청 한도를 초과했습니다.")
        elif r.status_code == 400:
            error_detail = r.json().get("error", {}).get("message", "알 수 없는 오류")
            raise ValueError(f"잘못된 요청: {error_detail}")

        r.raise_for_status()
        response_data = r.json()

        if "client_secret" not in response_data:
            raise ValueError("토큰 발급에 실패했습니다.")

        return response_data

    except requests.exceptions.Timeout as e:
        raise ValueError("네트워크 연결 시간이 초과되었습니다.") from e
    except requests.exceptions.ConnectionError as e:
        raise ValueError("API 서버에 연결할 수 없습니다.") from e
    except requests.exceptions.RequestException as e:
        raise ValueError(f"API 요청 실패: {str(e)}") from e


# --- Session state initialization
if "action" not in st.session_state:
    st.session_state["action"] = "idle"

# --- Handle button clicks (조용히 처리)
ephemeral = None
if start:
    if "error_message" in st.session_state:
        del st.session_state["error_message"]

    try:
        with st.spinner("연결 중..."):
            ephemeral = create_ephemeral_session(REALTIME_MODEL)
            st.session_state["ephemeral"] = ephemeral
            st.session_state["action"] = "start"

    except ValueError as e:
        st.error(f"❌ {str(e)}")
        st.session_state["action"] = "error"
        st.session_state["error_message"] = str(e)
    except Exception as e:
        st.error("❌ 연결에 실패했습니다.")
        st.session_state["action"] = "error"
        st.session_state["error_message"] = str(e)

elif stop:
    st.session_state["action"] = "stop"
    st.session_state.pop("ephemeral", None)
    st.session_state.pop("error_message", None)
else:
    st.session_state.setdefault("action", "idle")

# --- 메인 캡션 뷰어 (전체 화면)
try:
    with open("components/webrtc.html", encoding="utf-8") as f:
        html_template = f.read()

    payload = {
        "action": st.session_state["action"],
        "ephemeral": st.session_state.get("ephemeral"),
        "model": REALTIME_MODEL,
    }

    html_content = html_template.replace("{{BOOTSTRAP_JSON}}", json.dumps(payload))

    # 전체 높이 사용
    st.components.v1.html(html_content, height=800, scrolling=False)

except FileNotFoundError:
    st.error("❌ 시스템 파일을 찾을 수 없습니다.")
except Exception as e:
    st.error(f"❌ 시스템 로드 실패: {e}")

# 오류가 있을 때만 하단에 간단히 표시
if st.session_state.get("error_message"):
    st.error(f"❌ {st.session_state['error_message']}")
