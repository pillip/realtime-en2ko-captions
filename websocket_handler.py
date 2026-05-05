"""
WebSocket 서버 및 핸들러 모듈
OpenAI Realtime API 통합, 번역 처리, 사용량 추적
"""

import asyncio
import json
import os
import socket
import time

import boto3
import websockets

from auth import check_usage_limit, update_user_session
from database import get_usage_log_model, get_user_model
from services import (
    create_openai_session,
    get_aws_access_key_id,
    get_aws_region,
    get_aws_secret_access_key,
)
from translation import detect_language, translate_with_llm

# Per-connection rate limit: max messages per minute (sliding window)
WS_RATE_LIMIT_PER_MINUTE = int(os.getenv("WS_RATE_LIMIT_PER_MINUTE", "30"))


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

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            port = s.getsockname()[1]
            print(f"[Port] OS 자동 할당 포트: {port}")
            return port
    except OSError as e:
        raise Exception("사용 가능한 포트를 찾을 수 없습니다") from e


async def _authenticate_client(websocket):
    """WebSocket 클라이언트 인증 처리

    Validates claimed identity against the database (RL-002).
    Never trusts client-supplied role — always uses DB record.
    Rejects inactive users and unknown user IDs.
    """
    try:
        first_message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
        data = json.loads(first_message)
        if data.get("type") == "auth":
            user_info = data.get("user")
            if not user_info or "id" not in user_info:
                print("[Auth] 인증 실패: 사용자 정보 누락")
                await websocket.send(
                    json.dumps(
                        {
                            "type": "auth_error",
                            "message": "사용자 정보가 누락되었습니다.",
                        }
                    )
                )
                return None

            # Validate against database (RL-002: never trust client identity)
            user_model = get_user_model()
            db_user = user_model.get_user_by_id(user_info["id"])

            if db_user is None:
                print(f"[Auth] 인증 실패: 존재하지 않는 사용자 ID {user_info['id']}")
                await websocket.send(
                    json.dumps(
                        {"type": "auth_error", "message": "인증에 실패했습니다."}
                    )
                )
                return None

            # Reject inactive users
            if not db_user["is_active"]:
                print(f"[Auth] 인증 실패: 비활성 사용자 {db_user['username']}")
                await websocket.send(
                    json.dumps(
                        {
                            "type": "auth_error",
                            "message": "비활성화된 계정입니다. 관리자에게 문의하세요.",
                        }
                    )
                )
                return None

            # Validate username matches DB record
            claimed_username = user_info.get("username")
            if claimed_username != db_user["username"]:
                print(
                    f"[Auth] 인증 실패: 사용자명 불일치 "
                    f"(claimed={claimed_username}, db={db_user['username']})"
                )
                await websocket.send(
                    json.dumps(
                        {"type": "auth_error", "message": "인증에 실패했습니다."}
                    )
                )
                return None

            # Build validated user_info from DB (overwrite client-supplied role)
            validated_user = {
                "id": db_user["id"],
                "username": db_user["username"],
                "role": db_user["role"],
                "full_name": db_user.get("full_name"),
                "is_active": db_user["is_active"],
            }

            # Extract language settings from auth message (ISSUE-2)
            language_settings = data.get("language_settings", {})
            validated_user["language_settings"] = {
                "input_lang": language_settings.get("input_lang", "auto"),
                "output_lang": language_settings.get("output_lang", "ko"),
            }

            print(f"[Auth] 사용자 인증 성공: {validated_user['username']}")
            await websocket.send(
                json.dumps({"type": "auth_success", "message": "인증 완료"})
            )
            return validated_user
    except TimeoutError:
        print("[Auth] 인증 정보 수신 타임아웃")
    except Exception as e:
        print(f"[Auth] 인증 처리 오류: {e}")
    return None


def _init_translation_clients():
    """번역용 AWS 클라이언트 초기화"""
    access_key = get_aws_access_key_id()
    secret_key = get_aws_secret_access_key()
    region = get_aws_region()

    translate_client = boto3.client(
        "translate",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )

    bedrock_client = None
    bedrock_available = False
    try:
        bedrock_client = boto3.client(
            "bedrock-runtime",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        bedrock_available = True
        print("  🤖 Bedrock LLM 준비 완료")
    except Exception:
        print("  ⚠️ Bedrock 사용 불가, AWS Translate 사용")

    return translate_client, bedrock_client, bedrock_available


async def _handle_session_request(websocket):
    """OpenAI 세션 요청 처리"""
    try:
        session = await create_openai_session()
        await websocket.send(json.dumps({"type": "openai_session", "session": session}))
        print("[OpenAI] ✅ 세션 생성 완료")
    except Exception as e:
        await websocket.send(
            json.dumps(
                {
                    "type": "error",
                    "message": f"OpenAI 세션 생성 실패: {e!s}",
                }
            )
        )
        print(f"[OpenAI] ❌ 세션 생성 실패: {e}")


def _translate_text(
    transcript,
    source_lang,
    target_lang,
    translate_client,
    bedrock_client,
    bedrock_available,
):
    """텍스트 번역 (LLM 우선, AWS Translate 폴백)"""
    translated_text = None
    used_llm = False

    if bedrock_available:
        try:
            translated_text = translate_with_llm(
                bedrock_client,
                transcript,
                source_lang,
                target_lang,
            )
            if translated_text:
                used_llm = True
                print("[Translate] ✅ LLM 번역 완료")
        except Exception:
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
            translated_text = transcript

    return translated_text, used_llm


def _record_usage(
    current_user,
    audio_duration,
    data,
    transcript,
    translated_text,
    used_llm,
    source_lang,
    target_lang,
):
    """사용량 기록"""
    try:
        user_model = get_user_model()
        usage_log_model = get_usage_log_model()

        if audio_duration <= 0:
            audio_duration = max(1, len(transcript) / 5.0)
            print(f"[Usage] 📏 텍스트 기반 추정: {audio_duration:.1f}초")

        user_model.add_usage(current_user["id"], int(audio_duration))

        usage_log_model.record_usage(
            user_id=current_user["id"],
            action="transcribe",
            duration_seconds=int(audio_duration),
            source_language=source_lang,
            target_language=target_lang,
            metadata={
                "transcript_length": len(transcript),
                "translated_length": (len(translated_text) if translated_text else 0),
                "used_llm": used_llm,
                "estimated": audio_duration != data.get("audio_duration_seconds", 0),
                "source_text": transcript,
                "target_text": translated_text,
            },
        )

        update_user_session(current_user["id"])

        print(
            f"[Usage] ✅ 사용량 기록 - "
            f"User: {current_user['username']}, "
            f"Duration: {audio_duration}초"
        )
        return audio_duration

    except Exception as e:
        print(f"[Usage] ❌ 사용량 기록 실패: {e}")
        return audio_duration


async def _handle_transcript(
    websocket,
    data,
    user_info,
    translate_client,
    bedrock_client,
    bedrock_available,
    language_settings=None,
):
    """트랜스크립트 메시지 처리 (번역 + 사용량)"""
    transcript = data.get("text", "")
    audio_duration = data.get("audio_duration_seconds", 0)

    if not transcript:
        return

    print(f"[OpenAI] 📝 Transcript: {transcript}")
    print(f"[Usage] 🔊 Audio duration: {audio_duration} seconds")

    current_user = user_info
    if not current_user:
        await websocket.send(
            json.dumps(
                {
                    "type": "error",
                    "message": (
                        "로그인이 필요합니다. "
                        "페이지를 새로고침하고 "
                        "다시 로그인해주세요."
                    ),
                }
            )
        )
        return

    if not check_usage_limit(audio_duration, current_user):
        user_model = get_user_model()
        remaining = user_model.get_remaining_seconds(current_user["id"])
        await websocket.send(
            json.dumps(
                {
                    "type": "usage_exceeded",
                    "message": (f"사용량이 초과되었습니다. 남은 시간: {remaining}초"),
                    "remaining_seconds": remaining or 0,
                }
            )
        )
        print(
            f"[Usage] ❌ 사용량 초과 - "
            f"User: {current_user['username']}, "
            f"Remaining: {remaining}초"
        )
        return

    # Use connection-level language settings if available (ISSUE-2)
    lang = language_settings or {}
    input_lang = lang.get("input_lang", "auto")
    output_lang = lang.get("output_lang")

    if input_lang == "auto" or not input_lang:
        # Fall back to auto-detection (ISSUE-4)
        source_lang, target_lang = detect_language(
            transcript, output_lang=output_lang or "ko"
        )
    else:
        # Verify actual language matches the configured input_lang (ISSUE-34)
        detected_lang, _ = detect_language(transcript)
        if detected_lang != input_lang:
            await websocket.send(
                json.dumps(
                    {
                        "type": "language_mismatch",
                        "expected": input_lang,
                        "detected": detected_lang,
                        "text": transcript[:100],
                    }
                )
            )
            print(
                f"[Lang] Language mismatch: "
                f"expected={input_lang}, detected={detected_lang}"
            )
            return
        source_lang = input_lang
        target_lang = output_lang or "ko"

    translated_text, used_llm = _translate_text(
        transcript,
        source_lang,
        target_lang,
        translate_client,
        bedrock_client,
        bedrock_available,
    )

    audio_duration = _record_usage(
        current_user,
        audio_duration,
        data,
        transcript,
        translated_text,
        used_llm,
        source_lang,
        target_lang,
    )

    user_model = get_user_model()
    remaining_seconds = user_model.get_remaining_seconds(current_user["id"])

    await websocket.send(
        json.dumps(
            {
                "type": "transcription_result",
                "original_text": transcript,
                "translated_text": translated_text,
                "source_language": source_lang,
                "target_language": target_lang,
                "used_llm": used_llm,
                "audio_duration": audio_duration,
                "timestamp": time.time(),
                "remaining_seconds": remaining_seconds,
                "user_id": current_user["id"],
            }
        )
    )


def _check_rate_limit(message_timestamps, limit=None):
    """Sliding-window rate limit check for a single connection.

    Args:
        message_timestamps: collections.deque storing timestamps of recent messages.
        limit: Max messages per 60s window.
            Defaults to WS_RATE_LIMIT_PER_MINUTE.

    Returns:
        True if the message is allowed, False if rate limit exceeded.
    """
    if limit is None:
        limit = WS_RATE_LIMIT_PER_MINUTE

    now = time.time()
    window_start = now - 60.0

    # Evict timestamps older than the 60-second window
    while message_timestamps and message_timestamps[0] <= window_start:
        message_timestamps.popleft()

    if len(message_timestamps) >= limit:
        return False

    message_timestamps.append(now)
    return True


async def handle_openai_websocket(websocket):
    """OpenAI Realtime API와 통합된 WebSocket 핸들러"""
    import collections as _collections

    print(f"[WebSocket] OpenAI 모드 - 클라이언트 연결: {websocket.remote_address}")

    user_info = await _authenticate_client(websocket)

    # Per-connection language settings (ISSUE-2)
    # Initialised from auth message; updated by language_update messages.
    language_settings = (
        user_info.get("language_settings", {"input_lang": "auto", "output_lang": "ko"})
        if user_info
        else {"input_lang": "auto", "output_lang": "ko"}
    )

    # Per-connection sliding window for rate limiting
    message_timestamps = _collections.deque()

    try:
        (
            translate_client,
            bedrock_client,
            bedrock_available,
        ) = _init_translation_clients()

        await websocket.send(
            json.dumps(
                {
                    "type": "connection",
                    "status": "connected",
                    "message": "OpenAI Realtime + 번역 서비스 준비",
                }
            )
        )

        async for message in websocket:
            try:
                data = json.loads(message)

                msg_type = data.get("type")

                # Rate limit transcript messages
                if msg_type == "transcript":
                    if not _check_rate_limit(message_timestamps):
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": "rate_limit_exceeded",
                                }
                            )
                        )
                        print(
                            "[RateLimit] Rate limit exceeded for "
                            f"{websocket.remote_address}"
                        )
                        continue

                if msg_type == "request_openai_session":
                    await _handle_session_request(websocket)

                elif msg_type == "language_update":
                    # Live language change without reconnection (ISSUE-2)
                    language_settings["input_lang"] = data.get(
                        "input_lang",
                        language_settings["input_lang"],
                    )
                    language_settings["output_lang"] = data.get(
                        "output_lang",
                        language_settings["output_lang"],
                    )
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "language_updated",
                                "input_lang": language_settings["input_lang"],
                                "output_lang": language_settings["output_lang"],
                            }
                        )
                    )
                    print(
                        f"[Lang] Language updated: "
                        f"{language_settings['input_lang']} -> "
                        f"{language_settings['output_lang']}"
                    )

                elif msg_type == "transcript":
                    await _handle_transcript(
                        websocket,
                        data,
                        user_info,
                        translate_client,
                        bedrock_client,
                        bedrock_available,
                        language_settings=language_settings,
                    )

            except json.JSONDecodeError:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "message": "Invalid JSON format",
                        }
                    )
                )
            except Exception as e:
                print(f"[WebSocket] 메시지 처리 오류: {e}")
                await websocket.send(json.dumps({"type": "error", "message": str(e)}))

    except Exception as e:
        print(f"[WebSocket] 연결 오류: {e}")
    finally:
        print("[WebSocket] 클라이언트 연결 종료")


def start_websocket_server(port_ref):
    """WebSocket 서버 시작 (동적 포트 할당)

    Args:
        port_ref: 포트를 저장할 dict ({"port": None})
    """
    try:
        free_port = find_free_port()
        print(f"[WebSocket] 할당된 포트: {free_port}")
        port_ref["port"] = free_port

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run_server():
            try:
                server = await websockets.serve(
                    handle_openai_websocket,
                    "0.0.0.0",
                    free_port,
                )
                print(
                    f"[WebSocket] 서버 시작 완료 (OpenAI 모드): "
                    f"ws://0.0.0.0:{free_port}"
                )
                await server.wait_closed()
            except Exception as e:
                print(f"[WebSocket] 서버 실행 오류: {e}")
                raise e

        loop.run_until_complete(run_server())
    except Exception as e:
        print(f"[WebSocket] 서버 시작 오류: {e}")
        port_ref["port"] = None
