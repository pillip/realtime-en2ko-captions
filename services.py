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
            response = await client.post(
                "https://api.openai.com/v1/realtime/sessions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "OpenAI-Beta": "realtime=v1",
                },
                json={
                    "model": "gpt-4o-realtime-preview-2024-12-17",
                    "voice": "alloy",
                    "instructions": (
                        "You are a helpful assistant that "
                        "transcribes audio. Focus on accurate "
                        "transcription of mixed Korean and "
                        "English speech, technical terms, and "
                        "code-switching scenarios."
                    ),
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
            expires_at = datetime.now() + timedelta(minutes=1)

            return {
                "id": session_data.get("id"),
                "client_secret": session_data.get("client_secret", {}).get("value"),
                "expires_at": expires_at.isoformat(),
                "model": session_data.get(
                    "model",
                    "gpt-4o-realtime-preview-2024-12-17",
                ),
            }

    except httpx.HTTPError as e:
        print(f"[OpenAI] HTTP 오류: {e}")
        raise Exception(f"OpenAI 세션 생성 실패: {e}") from e
    except Exception as e:
        print(f"[OpenAI] 예상치 못한 오류: {e}")
        raise Exception(f"OpenAI 세션 생성 실패: {e}") from e
