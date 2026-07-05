"""
외부 서비스 세션 관리 모듈
OpenAI Realtime API 세션 생성
"""

import os
from datetime import datetime, timedelta

import httpx


# AWS 설정 — 함수로 읽어야 load_dotenv() 이후 값을 가져올 수 있음
def get_aws_region():
    return os.getenv("AWS_REGION", "us-east-1")


def get_aws_access_key_id():
    return os.getenv("AWS_ACCESS_KEY_ID")


def get_aws_secret_access_key():
    return os.getenv("AWS_SECRET_ACCESS_KEY")


def get_openai_api_key():
    return os.getenv("OPENAI_KEY")


async def create_openai_session() -> dict:
    """OpenAI Realtime API ephemeral token 생성"""
    api_key = get_openai_api_key()
    if not api_key:
        raise ValueError("OpenAI API 키가 설정되지 않았습니다.")

    try:
        async with httpx.AsyncClient() as client:
            # GA endpoint (2026-05-12: preview /v1/realtime/sessions removed).
            # Payload is nested under `session` with type=realtime; response's
            # ephemeral token is at top-level `value` (ek_... prefix).
            response = await client.post(
                "https://api.openai.com/v1/realtime/client_secrets",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "session": {
                        "type": "realtime",
                        "model": "gpt-realtime",
                        "instructions": (
                            "You are a real-time subtitle translator. "
                            "The user's speech has already been "
                            "transcribed for you; respond with ONLY the "
                            "translated text — no quotes, no apologies, "
                            "no meta-commentary. Translate Korean input "
                            "to English, and any non-Korean input "
                            "(English, Chinese, Vietnamese, etc.) to "
                            "Korean. Preserve proper nouns and technical "
                            "terms in their original script. Keep it "
                            "concise and natural — this appears as a "
                            "live caption line."
                        ),
                        # Text-only responses — browser (webrtc.html) consumes
                        # only response.text.* events; audio responses would
                        # be silently dropped AND rack up audio-output tokens.
                        # Voice config intentionally omitted (unused).
                        "audio": {
                            "input": {
                                "transcription": {"model": "gpt-4o-transcribe"},
                                "turn_detection": {
                                    "type": "server_vad",
                                    "threshold": 0.5,
                                    "prefix_padding_ms": 300,
                                    "silence_duration_ms": 500,
                                },
                            },
                        },
                        "output_modalities": ["text"],
                    },
                },
            )

            if response.status_code not in (200, 201):
                error_text = response.text
                print(f"[OpenAI] API 오류: {response.status_code} - {error_text}")
                # Body in message so callers w/o stdout access see the cause.
                raise Exception(
                    f"OpenAI API 오류 {response.status_code}: {error_text[:500]}"
                )

            session_data = response.json()
            effective_session = session_data.get("session") or {}
            expires_at = datetime.now() + timedelta(minutes=1)

            return {
                "id": effective_session.get("id") or session_data.get("id"),
                "client_secret": session_data.get("value"),
                "expires_at": expires_at.isoformat(),
                "model": effective_session.get("model", "gpt-realtime"),
            }

    except httpx.HTTPError as e:
        print(f"[OpenAI] HTTP 오류: {e}")
        raise Exception(f"OpenAI 세션 생성 실패: {e}") from e
    except Exception as e:
        print(f"[OpenAI] 예상치 못한 오류: {e}")
        raise Exception(f"OpenAI 세션 생성 실패: {e}") from e
