import asyncio
import base64
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
        translated_text = re.sub(r'This translation:.*$', '', translated_text, flags=re.DOTALL | re.IGNORECASE)

        # "Here's a natural..." 패턴 제거
        translated_text = re.sub(r'Here\'s a natural.*?:', '', translated_text, flags=re.DOTALL | re.IGNORECASE)

        # "This [설명]:" 패턴 제거
        translated_text = re.sub(r'This.*?:', '', translated_text, flags=re.DOTALL | re.IGNORECASE)

        # 첫 번째 문장만 추출 (줄바꿈 이전)
        lines = translated_text.split('\n')
        if lines:
            translated_text = lines[0].strip()

        # 따옴표로 둘러싸인 경우 제거
        translated_text = re.sub(r'^["\'](.+)["\']$', r'\1', translated_text)

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


async def transcribe_audio_streaming(audio_bytes, transcribe_client):
    """AWS Transcribe Streaming API를 사용해 실시간 오디오를 텍스트로 변환"""
    try:
        import io

        from amazon_transcribe.client import TranscribeStreamingClient
        from amazon_transcribe.handlers import TranscriptResultStreamHandler
        from amazon_transcribe.model import TranscriptEvent

        # 오디오 데이터 스트림 생성
        io.BytesIO(audio_bytes)

        class MyEventHandler(TranscriptResultStreamHandler):
            def __init__(self, output_stream):
                super().__init__(output_stream)
                self.transcript_text = ""

            async def handle_transcript_event(self, transcript_event: TranscriptEvent):
                results = transcript_event.transcript.results
                for result in results:
                    if result.alternatives:
                        transcript = result.alternatives[0].transcript
                        if transcript and not result.is_partial:
                            self.transcript_text = transcript
                            print(f"[Transcribe] Final transcript: {transcript}")

        # AWS Transcribe Streaming 클라이언트 생성
        transcribe_streaming = TranscribeStreamingClient(region=AWS_REGION)

        # Transcribe 스트리밍 시작 (안정성 향상 파라미터 추가)
        stream = await transcribe_streaming.start_stream_transcription(
            language_code="en-US",
            media_sample_rate_hz=16000,
            media_encoding="pcm",
            enable_partial_results_stabilization=True,
            partial_results_stability="high",
        )

        # 이벤트 핸들러 생성 (stream의 output_stream 전달)
        handler = MyEventHandler(stream.output_stream)

        # 오디오 스트림 생성기
        async def audio_stream_generator():
            # 작은 청크로 나누어 전송
            chunk_size = 1024
            for i in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[i : i + chunk_size]
                yield chunk
                await asyncio.sleep(0.01)  # 작은 딜레이

        print(f"[Transcribe] 스트리밍 시작 - 오디오 크기: {len(audio_bytes)} bytes")

        # 스트림 처리 (타임아웃 설정)
        try:
            # 오디오 전송
            async for chunk in audio_stream_generator():
                await stream.input_stream.send_audio_event(chunk)

            # 스트림 종료
            await stream.input_stream.end_stream()

            # 결과 처리 (타임아웃 설정)
            await asyncio.wait_for(handler.handle_events(), timeout=10.0)
        except TimeoutError:
            print("[Transcribe] 스트리밍 타임아웃")
            return None

        # 결과 반환
        if handler.transcript_text:
            print(f"[Transcribe] 성공: {handler.transcript_text}")
            return handler.transcript_text
        else:
            print("[Transcribe] 인식된 텍스트가 없습니다.")
            return None

    except Exception as e:
        print(f"[Transcribe] 스트리밍 API 오류: {e}")
        return None


# WebSocket을 통한 OpenAI transcript 처리 및 번역
async def handle_transcribe_websocket_openai(websocket):
    """WebSocket을 통해 OpenAI transcript 수신 및 번역 처리"""
    print(f"[WebSocket] 클라이언트 연결: {websocket.remote_address}")

    try:
        # AWS Translate 클라이언트 초기화 (번역만 필요)

        translate_client = boto3.client(
            "translate",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )

        # Bedrock Runtime 클라이언트 초기화 (LLM 통합용)
        try:
            bedrock_client = boto3.client(
                "bedrock-runtime",
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION,
            )
            bedrock_available = True
            print("  🤖 Bedrock LLM 통합 준비 완료")
        except Exception as bedrock_error:
            bedrock_client = None
            bedrock_available = False
            print(f"  ⚠️ Bedrock 연결 실패, 기본 Translate 사용: {bedrock_error}")

        await websocket.send(
            json.dumps(
                {
                    "type": "connection",
                    "status": "connected",
                    "message": "AWS Transcribe + Translate 연결 완료",
                }
            )
        )

        print("[WebSocket] AWS 서비스 연결 완료")

        # 🔧 오디오 버퍼 및 음성 활동 감지(VAD) 관리
        # 두 개의 버퍼 사용: 수집용과 처리용
        audio_buffer = []  # 현재 수집 중인 버퍼
        continuous_audio = []  # 연속 오디오 스트림 (손실 방지)

        # 🎯 간소화된 VAD - AWS Transcribe 내장 VAD 활용
        speech_state = "silent"  # "silent", "speaking" 만 사용

        # 언어 추적 (학습 기반)
        last_detected_language = (
            "en-US"  # 마지막 감지된 언어 (영어 기본 - 컨퍼런스 환경)
        )

        # 🔤 문장 누적 및 지능형 번역
        accumulated_text = ""  # 누적된 전사 텍스트
        pending_sentences = []  # 번역 대기 중인 완성된 문장들
        last_translation_time = time.time()

        # 🎚️ 연속 스트리밍 설정 (문장 단위 처리)
        STREAMING_CHUNK_SIZE = 75  # 75개 청크마다 처리 (약 3초 - 더 빠른 응답)
        MIN_VOLUME_THRESHOLD = 0.005  # 더 낮은 볼륨도 감지 (조용한 발화 캐치)
        SILENCE_DURATION = 0.8  # 0.8초 침묵 후 구간 종료 (더 빠른 분할)
        MIN_BUFFER_SIZE = 15  # 최소 15청크는 모아서 처리 (0.6초)
        accumulated_chunks = 0
        silence_start_time = None

        # 🗑️ 긴 음성 중간 처리 조건들 모두 제거
        # LONG_SPEECH_CHUNKS = 75     # 제거
        # LONG_SPEECH_TIME = 4.5      # 제거
        # MAX_SINGLE_SPEECH = 8.0     # 제거

        # 메시지 처리 루프
        async for message in websocket:
            try:
                data = json.loads(message)

                if data["type"] == "audio_chunk":
                    audio_data = data.get("audio", "")
                    volume = data.get("max_volume", 0)
                    current_time = asyncio.get_event_loop().time()

                    # 🔊 모든 오디오를 연속 버퍼에 추가
                    continuous_audio.append(audio_data)
                    accumulated_chunks += 1

                    # 버퍼 크기 제한 (15초)
                    if len(continuous_audio) > 375:  # 약 15초
                        continuous_audio.pop(0)

                    # 📊 볼륨이 있으면 오디오 버퍼에 추가
                    if volume >= MIN_VOLUME_THRESHOLD:
                        audio_buffer.append(audio_data)
                        silence_start_time = None  # 소리가 있으면 침묵 타이머 리셋

                        # 침묵이었다가 소리가 들어오면
                        if speech_state == "silent":
                            speech_state = "speaking"
                            print(f"[Stream] 🎤 음성 시작 (볼륨: {volume * 100:.1f}%)")
                    else:
                        # 침묵 시작 시간 기록
                        if speech_state == "speaking" and silence_start_time is None:
                            silence_start_time = current_time

                    # 🎯 문장 단위 처리 로직
                    should_process = False

                    # 조건 1: 충분한 청크 + 짧은 침묵 (자연스러운 문장 끝)
                    if (
                        speech_state == "speaking"
                        and len(audio_buffer) >= MIN_BUFFER_SIZE
                        and silence_start_time is not None
                        and current_time - silence_start_time >= 0.5  # 0.5초 짧은 pause
                    ):
                        should_process = True
                        speech_state = "silent"
                        print(
                            f"[Stream] 📝 문장 끝 감지 (버퍼: {len(audio_buffer)}청크, {len(audio_buffer) * 0.04:.1f}초)"
                        )
                        accumulated_chunks = 0

                    # 조건 2: 최대 청크 도달 (긴 문장 처리)
                    elif (
                        accumulated_chunks >= STREAMING_CHUNK_SIZE
                        and len(audio_buffer) >= MIN_BUFFER_SIZE
                    ):
                        should_process = True
                        print(
                            f"[Stream] 📦 최대 길이 도달 → 처리 ({accumulated_chunks}청크)"
                        )
                        accumulated_chunks = 0

                    # 조건 3: 긴 침묵 (말이 완전히 끝남)
                    elif (
                        len(audio_buffer) > 0
                        and silence_start_time is not None
                        and current_time - silence_start_time > SILENCE_DURATION
                    ):
                        should_process = True
                        speech_state = "silent"
                        print(f"[Stream] 🔇 {SILENCE_DURATION}초 침묵 → 세션 종료")

                        # 긴 침묵 시 남은 미완성 텍스트도 강제 번역
                        if pending_sentences or accumulated_text:
                            print("[Stream] 🏁 세션 종료 - 남은 텍스트 강제 번역")
                            if accumulated_text:
                                pending_sentences.append(accumulated_text)
                                accumulated_text = ""

                            if pending_sentences:
                                # 즉시 번역 실행
                                text_to_translate = " ".join(pending_sentences)
                                print(
                                    f"[Translate] 🏁 세션 종료 번역: {text_to_translate}"
                                )

                                try:
                                    # 언어 감지 (마지막 detected_language 사용)
                                    last_lang = (
                                        last_detected_language
                                        if "last_detected_language" in locals()
                                        else "en-US"
                                    )

                                    if last_lang == "ko-KR":
                                        # 한국어 → 영어
                                        if bedrock_available:
                                            translated_text = translate_with_llm(
                                                bedrock_client,
                                                text_to_translate,
                                                "ko",
                                                "en",
                                            )
                                        if not translated_text:
                                            translate_response = (
                                                translate_client.translate_text(
                                                    Text=text_to_translate,
                                                    SourceLanguageCode="ko",
                                                    TargetLanguageCode="en",
                                                )
                                            )
                                            translated_text = translate_response[
                                                "TranslatedText"
                                            ]
                                        target_language = "en"
                                    else:
                                        # 영어 → 한국어
                                        if bedrock_available:
                                            translated_text = translate_with_llm(
                                                bedrock_client,
                                                text_to_translate,
                                                "en",
                                                "ko",
                                            )
                                        if not translated_text:
                                            translate_response = (
                                                translate_client.translate_text(
                                                    Text=text_to_translate,
                                                    SourceLanguageCode="en",
                                                    TargetLanguageCode="ko",
                                                )
                                            )
                                            translated_text = translate_response[
                                                "TranslatedText"
                                            ]
                                        target_language = "ko"

                                    # 결과 전송
                                    await websocket.send(
                                        json.dumps(
                                            {
                                                "type": "transcription_result",
                                                "original_text": text_to_translate,
                                                "translated_text": translated_text,
                                                "source_language": last_lang,
                                                "target_language": target_language,
                                                "used_llm": bedrock_available,
                                                "timestamp": current_time,
                                                "sentence_count": len(
                                                    pending_sentences
                                                ),
                                                "session_end": True,
                                            }
                                        )
                                    )

                                    # 초기화
                                    pending_sentences = []
                                    last_translation_time = current_time

                                except Exception as e:
                                    print(f"[Translate] ❌ 세션 종료 번역 실패: {e}")

                    # 🚀 음성 인식 처리 실행 (원래 로직으로 복원)
                    if should_process:
                        try:
                            # 현재와 동일한 음성 인식 로직 실행
                            combined_bytes = b""
                            for chunk in audio_buffer:
                                chunk_bytes = base64.b64decode(chunk)
                                combined_bytes += chunk_bytes

                            audio_bytes = combined_bytes
                            print(
                                f"[Stream] 🔍 처리 시작 - 청크: {len(audio_buffer)}, 크기: {len(audio_bytes)} bytes"
                            )

                            # 🔧 버퍼 초기화 (다음 수집 준비)
                            audio_buffer.clear()
                            print("[Stream] 🔄 버퍼 초기화, 연속 스트리밍 계속")

                            # 🔍 AWS Transcribe 음성 인식을 백그라운드로 처리
                            if len(audio_bytes) >= 800 and transcribe_available:
                                # 복사본 생성하여 백그라운드 처리
                                audio_copy = bytes(audio_bytes)
                                task_time = current_time

                                # 🔒 동시 실행 제한 (최대 2개)
                                active_tasks = getattr(
                                    handle_transcribe_websocket, "active_tasks", set()
                                )
                                if len(active_tasks) >= 2:
                                    print(
                                        f"[Background] ⚠️ 작업 대기 중 (현재 {len(active_tasks)}개 실행 중)"
                                    )
                                    # 가장 오래된 작업 완료 대기
                                    if active_tasks:
                                        done, pending = await asyncio.wait(
                                            active_tasks,
                                            return_when=asyncio.FIRST_COMPLETED,
                                        )
                                        active_tasks -= done

                                # 백그라운드 태스크 생성
                                async def background_process():
                                    try:
                                        # 🔍 오디오 데이터 상세 분석
                                        print("[Background] 🎯 백그라운드 처리 시작")
                                        print(f"  - 크기: {len(audio_copy)} bytes")
                                        print(f"  - 샘플 수: {len(audio_copy) // 2}")
                                        print(
                                            f"  - 예상 길이: {(len(audio_copy) // 2) / 16000:.2f}초"
                                        )

                                        # 🔍 PCM 데이터 샘플 확인
                                        samples = struct.unpack(
                                            "<" + "h" * (len(audio_copy) // 2),
                                            audio_copy,
                                        )
                                        max_sample = max(
                                            abs(s) for s in samples[:100]
                                        )  # 처음 100개 샘플
                                        print(
                                            f"  - 최대 샘플값: {max_sample} / 32768 = {max_sample / 32768:.3f}"
                                        )

                                        # 🔍 실제 볼륨 계산
                                        rms = (
                                            sum(s * s for s in samples[:1000])
                                            / min(1000, len(samples))
                                        ) ** 0.5
                                        print(
                                            f"  - RMS 볼륨: {rms:.1f} ({rms / 32768 * 100:.1f}%)"
                                        )

                                        # 🎯 영어와 한국어를 동시에 transcribe하고 신뢰도 높은 것 선택
                                        async def transcribe_audio_parallel():
                                            print(
                                                "[Transcribe] 🌐 영어/한국어 동시 인식 시작..."
                                            )

                                            class MyEventHandler(
                                                TranscriptResultStreamHandler
                                            ):
                                                def __init__(
                                                    self, output_stream, language
                                                ):
                                                    super().__init__(output_stream)
                                                    self.transcript_text = ""
                                                    self.confidence_score = 0.0
                                                    self.language = language

                                                async def handle_transcript_event(
                                                    self,
                                                    transcript_event: TranscriptEvent,
                                                ):
                                                    results = (
                                                        transcript_event.transcript.results
                                                    )
                                                    for result in results:
                                                        if result.alternatives:
                                                            alternative = (
                                                                result.alternatives[0]
                                                            )
                                                            transcript = (
                                                                alternative.transcript
                                                            )

                                                            # AWS Transcribe가 제공하는 실제 confidence 값 사용
                                                            # alternatives[0]는 가장 높은 confidence를 가진 대안
                                                            # 각 alternative는 전체 전사에 대한 confidence를 가지지 않음
                                                            # 대신 각 단어(item)별 confidence가 있음

                                                            if (
                                                                transcript
                                                                and not result.is_partial
                                                            ):
                                                                # 🔧 전체 문장 누적하기 (덮어쓰지 않고 연결)
                                                                if self.transcript_text:
                                                                    self.transcript_text += (
                                                                        " " + transcript
                                                                    )
                                                                else:
                                                                    self.transcript_text = (
                                                                        transcript
                                                                    )

                                                                # AWS Transcribe의 실제 confidence 값 사용 시도
                                                                try:
                                                                    # items에서 confidence 추출 (단어별 confidence의 평균)
                                                                    if (
                                                                        hasattr(
                                                                            alternative,
                                                                            "items",
                                                                        )
                                                                        and alternative.items
                                                                    ):
                                                                        confidences = []
                                                                        for (
                                                                            item
                                                                        ) in (
                                                                            alternative.items
                                                                        ):
                                                                            if (
                                                                                hasattr(
                                                                                    item,
                                                                                    "confidence",
                                                                                )
                                                                                and item.confidence
                                                                                is not None
                                                                            ):
                                                                                confidences.append(
                                                                                    float(
                                                                                        item.confidence
                                                                                    )
                                                                                )

                                                                        if confidences:
                                                                            self.confidence_score = sum(
                                                                                confidences
                                                                            ) / len(
                                                                                confidences
                                                                            )
                                                                            print(
                                                                                f"[Transcribe-{self.language}] AWS Confidence: {self.confidence_score:.2f} (단어 {len(confidences)}개)"
                                                                            )
                                                                        else:
                                                                            # confidence가 없으면 자체 계산
                                                                            print(
                                                                                f"[Transcribe-{self.language}] No AWS confidence, using custom calculation"
                                                                            )
                                                                            self.calculate_custom_confidence(
                                                                                transcript
                                                                            )
                                                                    else:
                                                                        # items가 없으면 자체 계산
                                                                        self.calculate_custom_confidence(
                                                                            transcript
                                                                        )
                                                                except Exception as e:
                                                                    print(
                                                                        f"[Transcribe-{self.language}] Confidence extraction error: {e}"
                                                                    )
                                                                    self.calculate_custom_confidence(
                                                                        transcript
                                                                    )

                                                                print(
                                                                    f"[Transcribe-{self.language}] 📝 결과: '{transcript}' (신뢰도: {self.confidence_score:.2f})"
                                                                )

                                                def calculate_custom_confidence(
                                                    self, transcript
                                                ):
                                                    """자체 신뢰도 계산 (AWS confidence가 없을 때 사용)"""
                                                    text_length = len(transcript)
                                                    if self.language == "ko-KR":
                                                        # 한국어: 한글 포함 비율 + 문장 구조 평가
                                                        korean_chars = sum(
                                                            1
                                                            for c in transcript
                                                            if "가" <= c <= "힣"
                                                        )
                                                        korean_ratio = (
                                                            korean_chars
                                                            / max(text_length, 1)
                                                        )

                                                        # 한국어 조사/어미 패턴 체크
                                                        korean_patterns = [
                                                            "는",
                                                            "은",
                                                            "이",
                                                            "가",
                                                            "을",
                                                            "를",
                                                            "에",
                                                            "에서",
                                                            "으로",
                                                            "와",
                                                            "과",
                                                            "의",
                                                            "다",
                                                            "요",
                                                            "습니다",
                                                            "까",
                                                            "죠",
                                                        ]
                                                        pattern_score = sum(
                                                            0.1
                                                            for p in korean_patterns
                                                            if p in transcript
                                                        )

                                                        self.confidence_score = min(
                                                            korean_ratio
                                                            + pattern_score * 0.2,
                                                            1.0,
                                                        )
                                                    else:
                                                        # 영어: 단어 구조와 패턴 평가
                                                        words = transcript.split()
                                                        total_words = len(words)
                                                        valid_word_count = 0
                                                        common_word_count = 0
                                                        invalid_word_count = 0

                                                        # 기본 영어 단어 패턴 체크 (확장)
                                                        common_words = {
                                                            "the",
                                                            "be",
                                                            "to",
                                                            "of",
                                                            "and",
                                                            "a",
                                                            "in",
                                                            "that",
                                                            "have",
                                                            "i",
                                                            "it",
                                                            "for",
                                                            "not",
                                                            "on",
                                                            "with",
                                                            "he",
                                                            "as",
                                                            "you",
                                                            "do",
                                                            "at",
                                                            "this",
                                                            "but",
                                                            "his",
                                                            "by",
                                                            "from",
                                                            "they",
                                                            "we",
                                                            "say",
                                                            "her",
                                                            "she",
                                                            "or",
                                                            "an",
                                                            "will",
                                                            "my",
                                                            "one",
                                                            "all",
                                                            "would",
                                                            "there",
                                                            "their",
                                                            "what",
                                                            "so",
                                                            "up",
                                                            "out",
                                                            "if",
                                                            "about",
                                                            "who",
                                                            "get",
                                                            "which",
                                                            "go",
                                                            "me",
                                                            "when",
                                                            "make",
                                                            "can",
                                                            "like",
                                                            "time",
                                                            "no",
                                                            "just",
                                                            "him",
                                                            "know",
                                                            "take",
                                                            "people",
                                                            "into",
                                                            "year",
                                                            "your",
                                                            "good",
                                                            "some",
                                                            "could",
                                                            "them",
                                                            "see",
                                                            "other",
                                                            "than",
                                                            "then",
                                                            "now",
                                                            "look",
                                                            "only",
                                                            "come",
                                                            "its",
                                                            "over",
                                                            "think",
                                                            "also",
                                                            "back",
                                                            "after",
                                                            "use",
                                                            "two",
                                                            "how",
                                                            "our",
                                                            "work",
                                                            "first",
                                                            "well",
                                                            "way",
                                                            "even",
                                                            "new",
                                                            "want",
                                                            "because",
                                                            "any",
                                                            "these",
                                                            "give",
                                                            "day",
                                                            "most",
                                                            "is",
                                                            "are",
                                                            "was",
                                                            "were",
                                                            "been",
                                                            "has",
                                                            "had",
                                                            "does",
                                                            "did",
                                                            "should",
                                                            "may",
                                                            "might",
                                                            "must",
                                                            "shall",
                                                            "need",
                                                        }

                                                        for word in words:
                                                            word_clean = (
                                                                word.lower().strip(
                                                                    ".,!?;:'\""
                                                                )
                                                            )

                                                            # 빈 문자열 건너뛰기
                                                            if not word_clean:
                                                                continue

                                                            # 일반적인 영어 단어인지 체크
                                                            if (
                                                                word_clean
                                                                in common_words
                                                            ):
                                                                common_word_count += 1
                                                                valid_word_count += 1
                                                            # 알파벳으로만 구성되고 적절한 길이
                                                            elif (
                                                                word_clean.isalpha()
                                                                and 1
                                                                <= len(word_clean)
                                                                <= 20
                                                            ):
                                                                valid_word_count += 1
                                                            # 숫자만 있거나 숫자+알파벳 혼합 (예: 2024, 3rd)
                                                            elif word_clean.isdigit() or (
                                                                any(
                                                                    c.isdigit()
                                                                    for c in word_clean
                                                                )
                                                                and len(word_clean)
                                                                <= 10
                                                            ):
                                                                valid_word_count += (
                                                                    0.5  # 부분 점수
                                                                )
                                                            # 이상한 단어 (너무 길거나 특수문자만)
                                                            else:
                                                                invalid_word_count += 1

                                                        # 신뢰도 계산
                                                        if total_words > 0:
                                                            # 기본 점수: 유효 단어 비율
                                                            base_score = (
                                                                valid_word_count
                                                                / total_words
                                                            )

                                                            # 보너스: 흔한 영어 단어가 많으면 가산점
                                                            common_bonus = min(
                                                                common_word_count
                                                                / total_words
                                                                * 0.3,
                                                                0.3,
                                                            )

                                                            # 페널티: 이상한 단어가 많으면 감점
                                                            invalid_penalty = min(
                                                                invalid_word_count
                                                                / total_words
                                                                * 0.5,
                                                                0.3,
                                                            )

                                                            self.confidence_score = max(
                                                                0.0,
                                                                min(
                                                                    1.0,
                                                                    base_score
                                                                    + common_bonus
                                                                    - invalid_penalty,
                                                                ),
                                                            )

                                                            # 디버깅 정보
                                                            print(
                                                                f"[영어 신뢰도 디버그] 총단어:{total_words}, 유효:{valid_word_count}, 흔한단어:{common_word_count}, 이상:{invalid_word_count}, 점수:{self.confidence_score:.2f}"
                                                            )
                                                        else:
                                                            self.confidence_score = 0.0

                                            async def transcribe_single_language(
                                                language_code,
                                            ):
                                                """단일 언어로 transcribe"""
                                                try:
                                                    client = TranscribeStreamingClient(
                                                        region=AWS_REGION
                                                    )
                                                    stream = await client.start_stream_transcription(
                                                        language_code=language_code,
                                                        media_sample_rate_hz=16000,
                                                        media_encoding="pcm",
                                                        enable_partial_results_stabilization=True,
                                                        partial_results_stability="medium",  # high에서 medium으로 변경 (더 빠른 결과)
                                                        vocabulary_name=None,  # 필요시 사용자 정의 어휘 추가
                                                    )

                                                    handler = MyEventHandler(
                                                        stream.output_stream,
                                                        language_code,
                                                    )

                                                    # 오디오 데이터 전송 (최적화된 청크 크기)
                                                    # AWS 권장: 4KB~8KB 청크
                                                    chunk_size = 4096  # 4KB 청크로 증가
                                                    for i in range(
                                                        0, len(audio_copy), chunk_size
                                                    ):
                                                        chunk = audio_copy[
                                                            i : i + chunk_size
                                                        ]
                                                        # 청크가 너무 작으면 패딩
                                                        if len(
                                                            chunk
                                                        ) < chunk_size and i + chunk_size < len(
                                                            audio_copy
                                                        ):
                                                            chunk = chunk + b"\x00" * (
                                                                chunk_size - len(chunk)
                                                            )
                                                        await stream.input_stream.send_audio_event(
                                                            chunk
                                                        )

                                                    await (
                                                        stream.input_stream.end_stream()
                                                    )
                                                    # 타임아웃 증가로 긴 발화도 처리
                                                    await asyncio.wait_for(
                                                        handler.handle_events(),
                                                        timeout=10.0,
                                                    )

                                                    return {
                                                        "language": language_code,
                                                        "text": handler.transcript_text,
                                                        "confidence": handler.confidence_score,
                                                    }

                                                except Exception as e:
                                                    print(
                                                        f"[Transcribe-{language_code}] ❌ 오류: {e}"
                                                    )
                                                    return {
                                                        "language": language_code,
                                                        "text": "",
                                                        "confidence": 0.0,
                                                    }

                                            # 🚀 마지막 감지 언어 우선 처리 (최적화)
                                            nonlocal last_detected_language

                                            # 마지막 언어 먼저 시도
                                            primary_lang = last_detected_language
                                            secondary_lang = (
                                                "en-US"
                                                if primary_lang == "ko-KR"
                                                else "ko-KR"
                                            )

                                            # 병렬 처리하되 순서 유지
                                            results = await asyncio.gather(
                                                transcribe_single_language(
                                                    primary_lang
                                                ),
                                                transcribe_single_language(
                                                    secondary_lang
                                                ),
                                            )

                                            # 📊 결과 분석 및 선택
                                            primary_result = results[0]
                                            secondary_result = results[1]

                                            # 언어별로 재정렬
                                            if primary_lang == "ko-KR":
                                                ko_result = primary_result
                                                en_result = secondary_result
                                            else:
                                                en_result = primary_result
                                                ko_result = secondary_result

                                            print("[Transcribe] 📊 결과 비교:")
                                            print(
                                                f"  - 영어: '{en_result['text']}' (신뢰도: {en_result['confidence']:.2f})"
                                            )
                                            print(
                                                f"  - 한국어: '{ko_result['text']}' (신뢰도: {ko_result['confidence']:.2f})"
                                            )

                                            # 개선된 언어 선택 로직 (영어 우선, 명확한 한국어는 인정)
                                            # 1. 한국어가 매우 높은 신뢰도(0.8+)면 무조건 한국어
                                            if ko_result["confidence"] > 0.8:
                                                best_result = ko_result
                                            # 2. 영어가 높은 신뢰도(0.7+)이고 한국어가 낮으면(0.5-) 영어
                                            elif (
                                                en_result["confidence"] > 0.7
                                                and ko_result["confidence"] < 0.5
                                            ):
                                                best_result = en_result
                                            # 3. 신뢰도 차이가 크면(0.3+) 높은 쪽 선택
                                            elif (
                                                abs(
                                                    en_result["confidence"]
                                                    - ko_result["confidence"]
                                                )
                                                > 0.3
                                            ):
                                                best_result = max(
                                                    results,
                                                    key=lambda x: x["confidence"],
                                                )
                                            # 4. 둘 중 하나라도 신뢰도가 충분하면 더 높은 것 선택
                                            elif (
                                                en_result["confidence"] > 0.6
                                                or ko_result["confidence"] > 0.6
                                            ):
                                                best_result = max(
                                                    [en_result, ko_result],
                                                    key=lambda x: x["confidence"],
                                                )
                                            # 6. 둘 다 낮은 신뢰도면 영어 기본값
                                            else:
                                                best_result = en_result

                                            # 최종 안전장치: 영어 신뢰도가 너무 낮고(0.6-) 한국어가 높으면(0.7+) 한국어로 변경
                                            if (
                                                best_result == en_result
                                                and en_result["confidence"] < 0.6
                                                and ko_result["confidence"] > 0.7
                                            ):
                                                print(
                                                    f"[Transcribe] ⚠️ 안전장치 발동: 영어({en_result['confidence']:.2f}) → 한국어({ko_result['confidence']:.2f})"
                                                )
                                                best_result = ko_result

                                            if best_result["text"]:
                                                print(
                                                    f"[Transcribe] 🏆 최종 선택: {best_result['language']}"
                                                )
                                                print(
                                                    f"[Transcribe] 📝 인식 결과: '{best_result['text']}'"
                                                )
                                                # 다음 처리를 위해 언어 저장
                                                last_detected_language = best_result[
                                                    "language"
                                                ]
                                                return (
                                                    best_result["text"],
                                                    best_result["language"],
                                                )
                                            else:
                                                return None, None

                                        # 🚀 병렬 처리로 언어 감지
                                        (
                                            transcription_text,
                                            detected_language,
                                        ) = await transcribe_audio_parallel()

                                        if transcription_text:
                                            print(
                                                f"[AWS Transcribe] ✅ 성공: {transcription_text}"
                                            )

                                            # 📝 텍스트 누적
                                            nonlocal accumulated_text, pending_sentences, last_translation_time

                                            # 새로운 텍스트를 누적
                                            if accumulated_text:
                                                accumulated_text += (
                                                    " " + transcription_text
                                                )
                                            else:
                                                accumulated_text = transcription_text

                                            print(
                                                f"[Accumulate] 📊 누적 텍스트: {accumulated_text}"
                                            )

                                            # 문장 단위로 분리
                                            import re

                                            source_lang = (
                                                "ko"
                                                if detected_language == "ko-KR"
                                                else "en"
                                            )
                                            sentences = split_into_sentences(
                                                accumulated_text, source_lang
                                            )

                                            # 완성된 문장과 미완성 텍스트 분리
                                            if len(sentences) > 0:
                                                # 마지막 문장이 ?나 .나 !로 끝나면 완성된 문장
                                                last_sentence = sentences[-1]
                                                is_last_complete = re.search(
                                                    r"[.!?]$|[다요까네군나]$",
                                                    last_sentence.strip(),
                                                )

                                                if is_last_complete:
                                                    # 모든 문장이 완성됨
                                                    pending_sentences.extend(sentences)
                                                    accumulated_text = ""
                                                    print(
                                                        f"[Sentences] ✅ 완성된 문장 ({len(sentences)}개): {sentences}"
                                                    )
                                                elif len(sentences) > 1:
                                                    # 마지막만 미완성
                                                    complete_sentences = sentences[:-1]
                                                    accumulated_text = sentences[-1]
                                                    pending_sentences.extend(
                                                        complete_sentences
                                                    )
                                                    print(
                                                        f"[Sentences] ✅ 완성된 문장 ({len(complete_sentences)}개): {complete_sentences}"
                                                    )
                                                    print(
                                                        f"[Sentences] 🔄 누적 중: {accumulated_text}"
                                                    )
                                                else:
                                                    # 첫 문장도 미완성
                                                    print(
                                                        f"[Sentences] 🔄 계속 누적 중: {accumulated_text}"
                                                    )

                                            # 일정 시간이 지났거나 충분한 문장이 쌓이면 번역 수행
                                            current_time = time.time()
                                            should_translate = False

                                            if pending_sentences:
                                                # 영어: 더 긴 문맥을 위해 2초 대기하거나 2개 이상 문장 대기
                                                # 한국어: 빠른 응답을 위해 0.5초 대기
                                                wait_time = (
                                                    0.5
                                                    if detected_language == "ko-KR"
                                                    else 1.5
                                                )
                                                min_sentences = (
                                                    1
                                                    if detected_language == "ko-KR"
                                                    else 2
                                                )

                                                if (
                                                    len(pending_sentences)
                                                    >= min_sentences
                                                    and (
                                                        current_time
                                                        - last_translation_time
                                                        > wait_time
                                                    )
                                                ) or len(pending_sentences) >= 3:
                                                    should_translate = True

                                                    # 미완성 텍스트가 있으면 함께 번역
                                                    if accumulated_text.strip():
                                                        pending_sentences.append(
                                                            accumulated_text.strip()
                                                        )
                                                        accumulated_text = ""
                                                        print(
                                                            "[Sentences] ✅ 미완성 텍스트 추가하여 번역"
                                                        )

                                            # 오랜 시간 침묵 후 누적된 텍스트가 있으면 강제 번역
                                            # 영어는 더 오래 기다림 (문장이 길어서)
                                            force_wait_time = (
                                                3.0
                                                if detected_language == "ko-KR"
                                                else 8.0
                                            )

                                            if (
                                                not should_translate
                                                and accumulated_text
                                                and (
                                                    current_time - last_translation_time
                                                    > force_wait_time
                                                )
                                            ):
                                                # 미완성 문장도 번역
                                                pending_sentences.append(
                                                    accumulated_text
                                                )
                                                accumulated_text = ""
                                                should_translate = True
                                                print(
                                                    f"[Translate] ⏰ 강제 번역 ({force_wait_time}초 경과)"
                                                )

                                            if should_translate and pending_sentences:
                                                # 번역할 텍스트 준비
                                                text_to_translate = " ".join(
                                                    pending_sentences
                                                )
                                                print(
                                                    f"[Translate] 📚 번역 대상 ({len(pending_sentences)}개 문장): {text_to_translate}"
                                                )

                                                # 🔄 번역 로직: 한국어 → 영어, 그 외 → 한국어
                                                try:
                                                    if detected_language == "ko-KR":
                                                        # 한국어 → 영어 번역 (LLM 우선, 실패시 기본 Translate)
                                                        print(
                                                            "[Translate] 🔄 한국어 → 영어 번역 중..."
                                                        )

                                                        translated_text = None
                                                        used_llm = False
                                                        if bedrock_available:
                                                            print(
                                                                "[Translate] 🤖 LLM 고품질 번역 시도 중..."
                                                            )
                                                            translated_text = (
                                                                translate_with_llm(
                                                                    bedrock_client,
                                                                    text_to_translate,
                                                                    "ko",
                                                                    "en",
                                                                )
                                                            )
                                                            if translated_text:
                                                                used_llm = True

                                                        if not translated_text:
                                                            print(
                                                                "[Translate] 🔄 기본 AWS Translate 사용..."
                                                            )
                                                            translate_response = translate_client.translate_text(
                                                                Text=text_to_translate,
                                                                SourceLanguageCode="ko",
                                                                TargetLanguageCode="en",
                                                            )
                                                            translated_text = (
                                                                translate_response[
                                                                    "TranslatedText"
                                                                ]
                                                            )

                                                        print(
                                                            f"[Translate] ✅ 한국어→영어 완료: {translated_text}"
                                                        )
                                                        target_language = "en"

                                                    else:
                                                        # 그 외 언어 → 한국어 번역 (LLM 우선, 실패시 기본 Translate)
                                                        print(
                                                            f"[Translate] 🔄 {detected_language} → 한국어 번역 중..."
                                                        )

                                                        translated_text = None
                                                        used_llm = False
                                                        if bedrock_available:
                                                            print(
                                                                "[Translate] 🤖 LLM 고품질 번역 시도 중..."
                                                            )
                                                            source_lang_mapping = {
                                                                "en-US": "en",
                                                                "ja-JP": "ja",
                                                                "zh-CN": "zh",
                                                            }
                                                            source_lang = (
                                                                source_lang_mapping.get(
                                                                    detected_language,
                                                                    "en",
                                                                )
                                                            )
                                                            translated_text = (
                                                                translate_with_llm(
                                                                    bedrock_client,
                                                                    text_to_translate,
                                                                    source_lang,
                                                                    "ko",
                                                                )
                                                            )
                                                            if translated_text:
                                                                used_llm = True

                                                        if not translated_text:
                                                            print(
                                                                "[Translate] 🔄 기본 AWS Translate 사용..."
                                                            )
                                                            # 언어별 소스 언어 코드 명시적 설정
                                                            source_lang_mapping = {
                                                                "en-US": "en",
                                                                "ja-JP": "ja",
                                                                "zh-CN": "zh",
                                                                "es-ES": "es",
                                                                "fr-FR": "fr",
                                                                "de-DE": "de",
                                                            }

                                                            source_lang = (
                                                                source_lang_mapping.get(
                                                                    detected_language,
                                                                    "auto",
                                                                )
                                                            )

                                                            translate_response = translate_client.translate_text(
                                                                Text=text_to_translate,
                                                                SourceLanguageCode=source_lang,
                                                                TargetLanguageCode="ko",
                                                            )
                                                            translated_text = (
                                                                translate_response[
                                                                    "TranslatedText"
                                                                ]
                                                            )

                                                        print(
                                                            f"[Translate] ✅ {detected_language}→한국어 완료: {translated_text}"
                                                        )
                                                        target_language = "ko"

                                                    # 🚀 클라이언트로 결과 전송
                                                    await websocket.send(
                                                        json.dumps(
                                                            {
                                                                "type": "transcription_result",
                                                                "original_text": text_to_translate,
                                                                "translated_text": translated_text,
                                                                "source_language": detected_language,
                                                                "target_language": target_language,
                                                                "used_llm": used_llm,
                                                                "timestamp": task_time,
                                                                "sentence_count": len(
                                                                    pending_sentences
                                                                ),
                                                            }
                                                        )
                                                    )

                                                    # 번역 완료 후 초기화
                                                    pending_sentences = []
                                                    last_translation_time = current_time

                                                except Exception as translate_error:
                                                    print(
                                                        f"[Translate] ❌ 번역 오류: {translate_error}"
                                                    )
                                                    # 번역 실패해도 원본 텍스트는 전송
                                                    await websocket.send(
                                                        json.dumps(
                                                            {
                                                                "type": "transcription_result",
                                                                "original_text": text_to_translate,
                                                                "translated_text": text_to_translate,  # 원본 그대로
                                                                "source_language": detected_language
                                                                or "en",
                                                                "target_language": detected_language
                                                                or "en",
                                                                "used_llm": False,
                                                                "timestamp": task_time,
                                                                "sentence_count": len(
                                                                    pending_sentences
                                                                ),
                                                            }
                                                        )
                                                    )
                                                    # 실패해도 초기화
                                                    pending_sentences = []
                                                    last_translation_time = current_time

                                        else:
                                            print(
                                                "[AWS Transcribe] ❌ 인식된 텍스트가 없습니다."
                                            )
                                            transcription_text = None

                                    except Exception as debug_error:
                                        print(
                                            f"[Debug] AWS Transcribe 처리 중 오류: {debug_error}"
                                        )
                                        transcription_text = None

                                # 백그라운드 태스크 실행 및 추적
                                task = asyncio.create_task(background_process())
                                active_tasks.add(task)
                                handle_transcribe_websocket.active_tasks = active_tasks

                                # 완료된 태스크 제거
                                task.add_done_callback(
                                    lambda t: active_tasks.discard(t)
                                )

                                print(
                                    f"[VAD] ✨ 백그라운드 처리 시작 (총 {len(active_tasks)}개 실행 중)"
                                )

                        except Exception as e:
                            print(f"[VAD] ❌ 처리 중 오류: {e}")
                            # 오류 발생시에만 상태 리셋 (정상 처리는 이미 위에서 리셋 완료)

                    # 대기 중 상태 로그 (10초마다)
                    if not hasattr(handle_transcribe_websocket, "last_status_log"):
                        handle_transcribe_websocket.last_status_log = 0
                    if current_time - handle_transcribe_websocket.last_status_log > 10:
                        status = (
                            "🎤 녹음 중" if speech_state == "speaking" else "😴 대기 중"
                        )
                        print(
                            f"[Stream] {status} (버퍼: {len(audio_buffer)}개, 연속: {len(continuous_audio)}개)"
                        )
                        handle_transcribe_websocket.last_status_log = current_time

                elif data["type"] == "translate_text":
                    # 직접 텍스트 번역 요청
                    text = data.get("text", "")
                    if text:
                        try:
                            response = translate_client.translate_text(
                                Text=text,
                                SourceLanguageCode="auto",
                                TargetLanguageCode="ko",
                            )

                            await websocket.send(
                                json.dumps(
                                    {
                                        "type": "translation_result",
                                        "original_text": text,
                                        "translated_text": response["TranslatedText"],
                                        "source_language": response.get(
                                            "SourceLanguageCode", "en"
                                        ),
                                    }
                                )
                            )

                        except Exception as e:
                            await websocket.send(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": f"Translation error: {str(e)}",
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
