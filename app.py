import asyncio
import json
import os
import socket
import threading
import time
from datetime import datetime, timedelta

import boto3
import httpx
import streamlit as st
import websockets
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Global variable to store WebSocket port
WEBSOCKET_PORT = None


def find_free_port(start_port=8765, max_port=8800):
    """동적으로 사용 가능한 포트 찾기"""
    for port in range(start_port, max_port + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("localhost", port))
                print(f"[Port] 포트 {port} 사용 가능")
                return port
        except OSError:
            continue

    # 지정된 범위에서 포트를 찾지 못한 경우, OS에 자동 할당 요청
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))  # 0번 포트로 자동 할당
            port = s.getsockname()[1]
            print(f"[Port] OS 자동 할당 포트: {port}")
            return port
    except OSError:
        raise Exception("사용 가능한 포트를 찾을 수 없습니다")


def split_into_sentences(text, language="ko"):
    """텍스트를 문장 단위로 분리"""
    import re

    if language.startswith("ko"):
        # 한국어 문장 분리 개선
        # 1. 명확한 문장 종결 패턴
        text = re.sub(r"([.!?])([가-힣])", r"\1 \2", text)  # 구두점 뒤 공백 추가

        # 2. 다양한 종결 어미 고려
        # - 평서문: 다, 습니다, 합니다, 입니다, 네요, 군요, 어요, 아요, 에요
        # - 의문문: 까, 까요, 나요, 가요, 을까요, ㄹ까요
        # - 감탄문: 구나, 네, 군
        pattern = r"(?<=[.!?])|(?<=다)(?=[\s])|(?<=요)(?=[\s.!?])|(?<=까)(?=[\s.!?])|(?<=네)(?=[\s.!?])|(?<=군)(?=[\s.!?])|(?<=나)(?=[\s.!?])"
        sentences = re.split(pattern, text)

        # 재결합 및 정리
        result = []
        current = ""
        for sent in sentences:
            current += sent
            # 문장이 종결되었는지 확인
            if re.search(r"[.!?]$|[다요까네군나]$", current.strip()):
                if current.strip():
                    result.append(current.strip())
                current = ""
        # 마지막 미완성 문장
        if current.strip():
            result.append(current.strip())
        return result
    else:
        # 영어 등 문장 분리
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in sentences if s.strip()]


def translate_with_llm(bedrock_client, text, source_lang, target_lang):
    """Bedrock LLM을 사용한 고품질 컨텍스트 번역"""
    try:
        # 컨텍스트에 맞는 번역 프롬프트 구성
        if target_lang == "ko":
            # 다양한 언어 → 한국어
            source_lang_names = {
                "en": "영어",
                "ja": "일본어",
                "zh": "중국어",
                "es": "스페인어",
                "fr": "프랑스어",
                "de": "독일어",
            }
            source_lang_name = source_lang_names.get(source_lang, "원본 언어")

            prompt = f"""다음 {source_lang_name} 텍스트를 청중이 듣기 좋은 자연스러운 한국어로 의역해주세요.
실시간 컨퍼런스/기술발표 자막으로 사용되며, 완전한 직역보다는 의미 전달이 우선입니다.

원문: "{text}"

번역 가이드라인:
- 💡 의미 중심: 원문의 핵심 의미를 자연스럽게 전달
- 🎯 청중 친화적: 듣는 사람이 이해하기 쉬운 한국어 표현
- 🚀 맥락 반영: 기술발표/비즈니스 상황에 맞는 톤앤매너
- ⚡ 간결성: 실시간 자막에 적합한 깔끔한 문장 (최대 2문장)
- 🔧 용어 처리: 기술용어는 한국 개발자들이 실제 사용하는 표현
- 📝 자연스러움: 한국어 어순과 관용표현 우선, 직역 금지

예시 변환:
- "Let me walk you through" → "함께 살펴보겠습니다"
- "It's pretty straightforward" → "사실 꽤 간단합니다"
- "This is game-changing" → "이건 정말 혁신적이에요"
- "That landed differently for me" → "제게는 다르게 다가왔습니다"

번역 결과만 출력하세요 (설명, 주석, 부연설명 일절 금지):

한국어 번역:"""

        else:
            # 한국어 → 영어
            prompt = f"""다음 한국어를 국제 컨퍼런스에서 쓰이는 자연스러운 영어로 의역해주세요.
글로벌 청중을 위한 실시간 자막으로, 직역보다는 의미가 잘 전달되는 것이 중요합니다.

원문: "{text}"

번역 가이드라인:
- 🌍 글로벌 표준: 국제 컨퍼런스에서 실제 쓰이는 자연스러운 영어
- 💼 프로페셔널: 기술발표/비즈니스에 적합한 톤
- 🎯 명확성: 비영어권 청중도 이해하기 쉬운 표현
- ⚡ 간결성: 자막에 적합한 깔끔한 문장
- 🔧 용어 활용: 업계 표준 기술용어 및 표현 사용

예시 변환:
- "이걸 한번 보시면" → "Let's take a look at this"
- "꽤 괜찮은 것 같아요" → "This looks pretty promising"
- "정말 대단한 기술이에요" → "This is truly impressive technology"

번역 결과만 출력하세요 (설명, 주석, 부연설명 일절 금지):

English translation:"""

        # Claude 모델 사용 (Bedrock 표준 포맷 - 2025 업데이트)
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 200,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": prompt}]}
                ],
                "temperature": 0.5,
                "top_p": 0.9,
            }
        )

        # 여러 모델 ID 시도 (안정성 우선)
        model_ids = [
            "anthropic.claude-3-5-sonnet-20240620-v1:0",  # 안정 버전
            "anthropic.claude-3-haiku-20240307-v1:0",  # 빠른 처리
            "anthropic.claude-3-sonnet-20240229-v1:0",  # 백업 버전
        ]

        for model_id in model_ids:
            try:
                response = bedrock_client.invoke_model(
                    modelId=model_id,
                    body=body,
                    contentType="application/json",
                    accept="application/json",
                )
                break  # 성공하면 루프 종료
            except Exception as model_error:
                print(f"    ⚠️ {model_id} 모델 실패: {model_error}")
                if model_id == model_ids[-1]:  # 마지막 모델도 실패하면
                    raise model_error

        response_body = json.loads(response["body"].read())
        translated_text = response_body["content"][0]["text"].strip()

        # 결과 정리 (따옴표나 불필요한 문자 제거)
        translated_text = translated_text.strip("\"'")

        # 설명 텍스트 제거 (정규식으로 번역 결과만 추출)
        import re

        # "This translation:" 이후 설명 제거
        translated_text = re.sub(
            r"This translation:.*$",
            "",
            translated_text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # "Here's a natural..." 패턴 제거
        translated_text = re.sub(
            r"Here\'s a natural.*?:",
            "",
            translated_text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # "This [설명]:" 패턴 제거
        translated_text = re.sub(
            r"This.*?:", "", translated_text, flags=re.DOTALL | re.IGNORECASE
        )

        # 첫 번째 문장만 추출 (줄바꿈 이전)
        lines = translated_text.split("\n")
        if lines:
            translated_text = lines[0].strip()

        # 따옴표로 둘러싸인 경우 제거
        translated_text = re.sub(r'^["\'](.+)["\']$', r"\1", translated_text)

        # 최종 정리
        translated_text = translated_text.strip()

        return translated_text

    except Exception as e:
        print(f"    ❌ LLM 번역 실패: {e}")
        return None


# AWS 설정
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

# OpenAI 설정
OPENAI_API_KEY = os.getenv("OPENAI_KEY")

# 사이드바 상태 관리
if "sidebar_state" not in st.session_state:
    st.session_state["sidebar_state"] = "expanded"

# 페이지 설정
st.set_page_config(
    page_title="실시간 자막",
    layout="wide",
    initial_sidebar_state=st.session_state["sidebar_state"],
)

# ALB/프록시 환경에서 HTTPS 지원을 위한 설정
import streamlit.web.server.server as server

try:
    server.ENABLE_XSRF_PROTECTION = True
    os.environ.setdefault("STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION", "true")
except:
    pass

# 전체 페이지 스크롤 방지 + iframe margin 추가
st.markdown(
    """
<style>
    /* 전체 페이지 높이 제한 */
    html, body {
        height: 100vh !important;
        overflow: hidden !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    .main > div {
        padding-top: 0rem !important;
        padding-bottom: 0rem !important;
        height: 100vh !important;
        overflow: hidden !important;
    }

    .stApp {
        overflow: hidden !important;
        height: 100vh !important;
        max-height: 100vh !important;
    }

    .main {
        overflow: hidden !important;
        height: 100vh !important;
        max-height: 100vh !important;
    }

    /* 🎯 iframe 높이를 더 줄여서 확실히 100vh 안에 맞춤 */
    .main iframe {
        height: 95vh !important;
        max-height: 95vh !important;
    }

    /* 모든 컨테이너 패딩/마진 제거 */
    .main > div > div {
        padding-top: 0rem !important;
        padding-bottom: 0rem !important;
        margin: 0 !important;
    }

    /* Streamlit 기본 여백 완전 제거 */
    .block-container {
        padding-top: 0rem !important;
        padding-bottom: 0rem !important;
        max-height: 100vh !important;
    }

    /* Streamlit 동적 클래스들의 패딩 조정 */
    .stMainBlockContainer {
        padding-top: 5vh !important;
        padding-bottom: 0rem !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
        margin: 0rem !important;
    }
</style>
""",
    unsafe_allow_html=True,
)

# 상위 시스템 관리 패널
with st.sidebar:
    st.header("🏢 시스템 관리")

    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        st.error("⚠️ AWS 자격 증명이 설정되지 않았습니다.")
        st.info("💡 .env 파일에 AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY를 설정하세요")
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

    # WebSocket 포트 정보 표시
    if WEBSOCKET_PORT:
        st.markdown("---")
        st.subheader("🔗 연결 정보")
        st.code(f"WebSocket: ws://localhost:{WEBSOCKET_PORT}")
        if (
            st.session_state.get("websocket_thread")
            and st.session_state["websocket_thread"].is_alive()
        ):
            st.success("🟢 WebSocket 서버 실행 중")
        else:
            st.warning("🟡 WebSocket 서버 대기 중")


async def create_openai_session() -> dict:
    """OpenAI Realtime API ephemeral token 생성"""
    if not OPENAI_API_KEY:
        raise ValueError("OpenAI API 키가 설정되지 않았습니다.")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/realtime/sessions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                    "OpenAI-Beta": "realtime=v1",
                },
                json={
                    "model": "gpt-4o-realtime-preview-2024-12-17",
                    "voice": "alloy",
                    "instructions": "You are a helpful assistant that transcribes audio. Focus on accurate transcription of mixed Korean and English speech, technical terms, and code-switching scenarios.",
                    "input_audio_transcription": {"model": "whisper-1"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                    },
                    "modalities": ["audio", "text"],
                    "temperature": 0.8,
                },
            )

            if response.status_code != 200:
                error_text = response.text
                print(f"[OpenAI] API 오류: {response.status_code} - {error_text}")
                raise Exception(f"OpenAI API 오류: {response.status_code}")

            session_data = response.json()

            # 세션 정보 추출 및 만료 시간 계산
            expires_at = datetime.now() + timedelta(minutes=1)  # 1분 유효

            return {
                "id": session_data.get("id"),
                "client_secret": session_data.get("client_secret", {}).get("value"),
                "expires_at": expires_at.isoformat(),
                "model": session_data.get(
                    "model", "gpt-4o-realtime-preview-2024-12-17"
                ),
            }

    except httpx.HTTPError as e:
        print(f"[OpenAI] HTTP 오류: {e}")
        raise Exception(f"OpenAI 세션 생성 실패: {e}")
    except Exception as e:
        print(f"[OpenAI] 예상치 못한 오류: {e}")
        raise Exception(f"OpenAI 세션 생성 실패: {e}")


def create_aws_session() -> dict:
    """AWS 임시 credentials 생성"""
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        raise ValueError("AWS 자격 증명이 설정되지 않았습니다.")

    try:
        # STS 클라이언트 생성
        sts_client = boto3.client(
            "sts",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )

        # 현재 호출자 ID 확인
        caller_identity = sts_client.get_caller_identity()

        # 동적으로 할당된 WebSocket 서버 포트 사용
        websocket_port = WEBSOCKET_PORT or 8765

        print(f"[AWS Session] WebSocket 포트: {websocket_port}")

        return {
            "access_key_id": AWS_ACCESS_KEY_ID,
            "secret_access_key": AWS_SECRET_ACCESS_KEY,
            "region": AWS_REGION,
            "account_id": caller_identity.get("Account"),
            "websocket_url": f"ws://localhost:{websocket_port}",
            "openai_available": bool(OPENAI_API_KEY),  # OpenAI 사용 가능 여부
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "InvalidUserID.NotFound":
            raise ValueError("AWS 자격 증명이 유효하지 않습니다.")
        elif error_code == "TokenRefreshRequired":
            raise ValueError("AWS 토큰이 만료되었습니다.")
        else:
            raise ValueError(f"AWS 연결 실패: {error_code}")
    except Exception as e:
        raise ValueError(f"AWS 세션 생성 실패: {str(e)}") from e


# AWS Transcribe 함수 제거됨 - OpenAI Realtime API 사용


# 새로운 간단한 OpenAI 전용 WebSocket 핸들러
async def handle_openai_websocket(websocket):
    """OpenAI Realtime API와 통합된 WebSocket 핸들러"""
    print(f"[WebSocket] OpenAI 모드 - 클라이언트 연결: {websocket.remote_address}")

    try:
        # 번역 클라이언트만 초기화
        translate_client = boto3.client(
            "translate",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )

        # Bedrock 클라이언트 (선택적)
        try:
            bedrock_client = boto3.client(
                "bedrock-runtime",
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION,
            )
            bedrock_available = True
            print("  🤖 Bedrock LLM 준비 완료")
        except:
            bedrock_client = None
            bedrock_available = False
            print("  ⚠️ Bedrock 사용 불가, AWS Translate 사용")

        await websocket.send(
            json.dumps(
                {
                    "type": "connection",
                    "status": "connected",
                    "message": "OpenAI Realtime + 번역 서비스 준비",
                }
            )
        )

        # 메시지 처리 루프
        async for message in websocket:
            try:
                data = json.loads(message)

                # OpenAI 세션 요청
                if data["type"] == "request_openai_session":
                    try:
                        session = await create_openai_session()
                        await websocket.send(
                            json.dumps({"type": "openai_session", "session": session})
                        )
                        print("[OpenAI] ✅ 세션 생성 완료")
                    except Exception as e:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": f"OpenAI 세션 생성 실패: {str(e)}",
                                }
                            )
                        )
                        print(f"[OpenAI] ❌ 세션 생성 실패: {e}")

                # OpenAI transcript 수신 및 번역
                elif data["type"] == "transcript":
                    transcript = data.get("text", "")
                    if not transcript:
                        continue

                    print(f"[OpenAI] 📝 Transcript: {transcript}")

                    # 간단한 언어 감지
                    has_korean = any(
                        ord(c) >= 0xAC00 and ord(c) <= 0xD7A3 for c in transcript
                    )

                    if has_korean:
                        source_lang = "ko"
                        target_lang = "en"
                    else:
                        source_lang = "en"
                        target_lang = "ko"

                    # 번역 처리
                    translated_text = None
                    used_llm = False

                    if bedrock_available:
                        try:
                            translated_text = translate_with_llm(
                                bedrock_client, transcript, source_lang, target_lang
                            )
                            if translated_text:
                                used_llm = True
                                print("[Translate] ✅ LLM 번역 완료")
                        except:
                            pass

                    if not translated_text:
                        try:
                            response = translate_client.translate_text(
                                Text=transcript,
                                SourceLanguageCode=source_lang,
                                TargetLanguageCode=target_lang,
                            )
                            translated_text = response["TranslatedText"]
                            print("[Translate] ✅ AWS Translate 완료")
                        except Exception as e:
                            print(f"[Translate] ❌ 번역 실패: {e}")
                            translated_text = transcript  # 실패시 원문 그대로

                    # 결과 전송
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "transcription_result",
                                "original_text": transcript,
                                "translated_text": translated_text,
                                "source_language": source_lang,
                                "target_language": target_lang,
                                "used_llm": used_llm,
                                "timestamp": time.time(),
                            }
                        )
                    )

            except json.JSONDecodeError:
                await websocket.send(
                    json.dumps({"type": "error", "message": "Invalid JSON format"})
                )
            except Exception as e:
                print(f"[WebSocket] 메시지 처리 오류: {e}")
                await websocket.send(json.dumps({"type": "error", "message": str(e)}))

    except Exception as e:
        print(f"[WebSocket] 연결 오류: {e}")
    finally:
        print("[WebSocket] 클라이언트 연결 종료")


def start_websocket_server():
    """WebSocket 서버 시작 (동적 포트 할당)"""
    global WEBSOCKET_PORT
    try:
        # 동적으로 사용 가능한 포트 찾기
        free_port = find_free_port()
        print(f"[WebSocket] 할당된 포트: {free_port}")

        # 글로벌 변수에 포트 저장
        WEBSOCKET_PORT = free_port

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run_server():
            try:
                # OpenAI 모드를 기본으로 사용
                server = await websockets.serve(
                    handle_openai_websocket, "localhost", free_port
                )
                print(
                    f"[WebSocket] 서버 시작 완료 (OpenAI 모드): ws://localhost:{free_port}"
                )

                await server.wait_closed()
            except Exception as e:
                print(f"[WebSocket] 서버 실행 오류: {e}")
                raise e

        loop.run_until_complete(run_server())
    except Exception as e:
        print(f"[WebSocket] 서버 시작 오류: {e}")
        WEBSOCKET_PORT = None


# WebSocket 서버를 별도 스레드에서 실행
if (
    "websocket_thread" not in st.session_state
    or not st.session_state["websocket_thread"].is_alive()
):
    # 기존 스레드가 있다면 정리
    if "websocket_thread" in st.session_state:
        if st.session_state["websocket_thread"].is_alive():
            print("[WebSocket] 기존 스레드가 여전히 실행 중입니다.")
        else:
            print("[WebSocket] 기존 스레드 정리 중...")

    print("[WebSocket] 새로운 WebSocket 스레드 시작 중...")
    st.session_state["websocket_thread"] = threading.Thread(
        target=start_websocket_server, daemon=True
    )
    st.session_state["websocket_thread"].start()
    print("[WebSocket] WebSocket 스레드 시작됨")


# Session state
if "action" not in st.session_state:
    st.session_state["action"] = "idle"

# Handle actions
openai_session = None
if start:
    try:
        # 사이드바 상태 유지
        st.session_state["sidebar_state"] = "expanded"

        with st.spinner("시작 중..."):
            # OpenAI Realtime API 세션 생성
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            openai_session = loop.run_until_complete(create_openai_session())
            loop.close()

            st.session_state["openai_session"] = openai_session
            st.session_state["action"] = "start"
            st.rerun()
    except ValueError as e:
        st.error(f"❌ {str(e)}")
        st.session_state["action"] = "error"
        st.session_state["sidebar_state"] = "expanded"
        st.rerun()

elif stop:
    # 사이드바 상태 유지
    st.session_state["sidebar_state"] = "expanded"
    st.session_state["action"] = "stop"
    st.session_state.pop("openai_session", None)
    st.rerun()

# 메인 캡션 뷰어
try:
    with open("components/webrtc.html", encoding="utf-8") as f:
        html_template = f.read()

    payload = {
        "action": st.session_state["action"],
        "openai_session": st.session_state.get("openai_session"),
        "service": "openai_realtime",
        "websocket_port": WEBSOCKET_PORT,
    }

    html_content = html_template.replace("{{BOOTSTRAP_JSON}}", json.dumps(payload))
    st.components.v1.html(html_content, height=900, scrolling=False)

except Exception:
    st.error("❌ 시스템을 로드할 수 없습니다.")
