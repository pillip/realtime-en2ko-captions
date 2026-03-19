"""
외부 서비스 세션 관리 모듈
OpenAI Realtime API, AWS 세션 생성, 헬스체크 서버
"""

import os
from datetime import datetime, timedelta

import boto3
import httpx
from botocore.exceptions import ClientError


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
        raise Exception(f"OpenAI 세션 생성 실패: {e}")
    except Exception as e:
        print(f"[OpenAI] 예상치 못한 오류: {e}")
        raise Exception(f"OpenAI 세션 생성 실패: {e}")


def create_aws_session(websocket_port=None) -> dict:
    """AWS 임시 credentials 생성"""
    access_key = get_aws_access_key_id()
    secret_key = get_aws_secret_access_key()
    region = get_aws_region()

    if not access_key or not secret_key:
        raise ValueError("AWS 자격 증명이 설정되지 않았습니다.")

    try:
        sts_client = boto3.client(
            "sts",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

        caller_identity = sts_client.get_caller_identity()
        ws_port = websocket_port or 8765

        print(f"[AWS Session] WebSocket 포트: {ws_port}")

        return {
            "access_key_id": access_key,
            "secret_access_key": secret_key,
            "region": region,
            "account_id": caller_identity.get("Account"),
            "websocket_url": f"ws://localhost:{ws_port}",
            "openai_available": bool(get_openai_api_key()),
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
        raise ValueError(f"AWS 세션 생성 실패: {e!s}") from e


def start_health_server(port):
    """ALB 헬스체크용 간단한 HTTP 서버"""
    import http.server
    import socketserver

    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ["/", "/health"]:
                self.send_response(200)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    try:
        with socketserver.TCPServer(("0.0.0.0", port), HealthHandler) as httpd:
            print(f"[Health Check] HTTP 서버 시작: http://0.0.0.0:{port}/health")
            httpd.serve_forever()
    except Exception as e:
        print(f"[Health Check] 서버 오류: {e}")
