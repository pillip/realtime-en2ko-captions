#!/usr/bin/env python3
"""
저장된 WAV 파일들을 사용해서 AWS Transcribe 테스트
"""

import glob
import json
import os
import wave

import boto3
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()


def translate_with_llm(bedrock_client, text, source_lang, target_lang):
    """Bedrock LLM을 사용한 고품질 컨텍스트 번역"""
    try:
        # 컨텍스트에 맞는 번역 프롬프트 구성
        if target_lang == "ko":
            # 영어 → 한국어
            prompt = f"""다음 영어 텍스트를 자연스러운 한국어로 번역해주세요.
기술 프레젠테이션이나 비즈니스 맥락에서 사용될 실시간 자막입니다.

원문: "{text}"

번역 시 고려사항:
- 자연스럽고 이해하기 쉬운 한국어 사용
- 기술 용어나 회사명은 적절히 처리
- 실시간 자막에 적합한 간결한 표현
- 문화적 뉘앙스 반영

번역 결과만 답변해주세요:"""

        else:
            # 한국어 → 영어
            prompt = f"""다음 한국어 텍스트를 자연스러운 영어로 번역해주세요.
기술 프레젠테이션이나 비즈니스 맥락에서 사용될 실시간 자막입니다.

원문: "{text}"

번역 시 고려사항:
- 자연스럽고 전문적인 영어 사용
- 비즈니스 맥락에 적합한 표현
- 실시간 자막에 적합한 명확한 표현

번역 결과만 답변해주세요:"""

        # Claude 모델 사용 (Bedrock 표준 포맷 - 2025 업데이트)
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 200,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": prompt}]}
                ],
                "temperature": 0.3,
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

        return translated_text

    except Exception as e:
        print(f"    ❌ LLM 번역 실패: {e}")
        return None


def analyze_wav_file(wav_path):
    """WAV 파일 기본 정보 분석"""
    try:
        with wave.open(wav_path, "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            duration = frames / frame_rate

        file_size = os.path.getsize(wav_path)

        print(f"\n🔍 파일 분석: {os.path.basename(wav_path)}")
        print(
            f"  📊 채널: {channels}, 비트: {sample_width * 8}, 샘플레이트: {frame_rate}Hz"
        )
        print(f"  ⏱️ 길이: {duration:.2f}초, 크기: {file_size} bytes")

        return {
            "path": wav_path,
            "channels": channels,
            "sample_width": sample_width,
            "frame_rate": frame_rate,
            "duration": duration,
            "file_size": file_size,
        }
    except Exception as e:
        print(f"❌ 파일 분석 실패: {e}")
        return None


def test_aws_transcribe_multilang(wav_path, info):
    """다중 언어 감지 및 번역 테스트"""
    try:
        print("\n🔄 AWS Transcribe 다중 언어 테스트:")
        print(f"  📂 파일: {os.path.basename(wav_path)}")

        # AWS 클라이언트 초기화
        region_name = os.getenv("AWS_REGION", "ap-northeast-2")

        # Translate 클라이언트 초기화
        translate_client = boto3.client(
            "translate",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=region_name,
        )

        # Bedrock Runtime 클라이언트 초기화 (LLM 통합용)
        try:
            bedrock_client = boto3.client(
                "bedrock-runtime",
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=region_name,
            )
            bedrock_available = True
            print("  🤖 Bedrock LLM 통합 준비 완료")
        except Exception as bedrock_error:
            bedrock_client = None
            bedrock_available = False
            print(f"  ⚠️ Bedrock 연결 실패, 기본 Translate 사용: {bedrock_error}")

        try:
            # AWS Transcribe Streaming 라이브러리 사용
            import asyncio

            from amazon_transcribe.client import TranscribeStreamingClient
            from amazon_transcribe.handlers import TranscriptResultStreamHandler
            from amazon_transcribe.model import TranscriptEvent
        except ImportError:
            print("  ❌ amazon-transcribe 라이브러리가 없습니다.")
            print("  💡 설치: uv add amazon-transcribe")
            return None

        # WAV 파일 읽기
        with open(wav_path, "rb") as f:
            audio_bytes = f.read()

        # WAV 헤더 제거 (44 bytes)
        if audio_bytes.startswith(b"RIFF"):
            audio_data = audio_bytes[44:]  # WAV 헤더 건너뛰기
        else:
            audio_data = audio_bytes

        print(f"  📊 오디오 데이터: {len(audio_data)} bytes")

        # 다중 언어 테스트를 위한 언어 목록 (영어 우선)
        languages_to_test = [
            ("en-US", "영어"),
            ("ko-KR", "한국어"),
            ("zh-CN", "중국어(간체)"),
            ("ja-JP", "일본어"),
        ]

        best_result = None
        best_confidence = 0
        detected_language = "en-US"

        # 언어별로 시도 (영어가 성공하면 다른 언어 건너뛰기)
        for lang_code, lang_name in languages_to_test:
            print(f"  🌐 {lang_name}({lang_code})로 시도 중...")

            # 비동기 처리를 위한 함수
            async def transcribe_audio_with_lang(language_code):
                class MyEventHandler(TranscriptResultStreamHandler):
                    def __init__(self, output_stream):
                        super().__init__(output_stream)
                        self.transcript_text = ""
                        self.confidence_score = 0.0

                    async def handle_transcript_event(
                        self, transcript_event: TranscriptEvent
                    ):
                        results = transcript_event.transcript.results
                        for result in results:
                            if result.alternatives:
                                transcript = result.alternatives[0].transcript
                                confidence = getattr(
                                    result.alternatives[0], "confidence", 0.0
                                )
                                if transcript and not result.is_partial:
                                    self.transcript_text = transcript
                                    self.confidence_score = (
                                        confidence if confidence else 0.8
                                    )  # 기본값
                                    print(
                                        f"    📝 결과: '{transcript}' (신뢰도: {self.confidence_score:.2f})"
                                    )

                # AWS Transcribe Streaming 클라이언트 생성
                client = TranscribeStreamingClient(region=region_name)

                try:
                    # 스트리밍 시작 (언어 코드 변경)
                    stream = await client.start_stream_transcription(
                        language_code=language_code,
                        media_sample_rate_hz=16000,
                        media_encoding="pcm",
                    )

                    # 이벤트 핸들러
                    handler = MyEventHandler(stream.output_stream)

                    # 오디오 데이터를 청크로 나누어 전송
                    chunk_size = 1024
                    for i in range(0, len(audio_data), chunk_size):
                        chunk = audio_data[i : i + chunk_size]
                        await stream.input_stream.send_audio_event(chunk)
                        await asyncio.sleep(0.01)

                    # 스트림 종료
                    await stream.input_stream.end_stream()

                    # 결과 처리 (타임아웃 설정)
                    await asyncio.wait_for(handler.handle_events(), timeout=15.0)

                    return handler.transcript_text, handler.confidence_score

                except TimeoutError:
                    print(f"    ⏰ {lang_name} 타임아웃")
                    return None, 0
                except Exception as e:
                    print(f"    ❌ {lang_name} 오류: {e}")
                    return None, 0

            # 언어별 테스트 실행
            try:
                result, confidence = asyncio.run(transcribe_audio_with_lang(lang_code))
                if result and confidence > best_confidence:
                    best_result = result
                    best_confidence = confidence
                    detected_language = lang_code
                    print(
                        f"    ✅ {lang_name} 최고 결과로 업데이트! (신뢰도: {confidence:.2f})"
                    )

                    # 영어에서 성공하고 신뢰도가 높으면 다른 언어 시도 안함 (비용 절약)
                    if lang_code == "en-US" and confidence > 0.7:
                        print("    🚀 영어 인식 신뢰도가 높아서 다른 언어 시도 생략")
                        break

                elif result:
                    print(
                        f"    ✅ {lang_name} 성공하였으나 기존 결과보다 낮음 (신뢰도: {confidence:.2f})"
                    )
                else:
                    print(f"    ❌ {lang_name} 실패")

            except Exception as e:
                print(f"    💥 {lang_name} 처리 오류: {e}")
                continue

        # 최고 결과 출력 및 번역 처리
        if best_result:
            print("\n  🏆 최종 인식 결과:")
            print(f"    📝 텍스트: '{best_result}'")
            print(f"    🌐 감지 언어: {detected_language}")
            print(f"    📊 신뢰도: {best_confidence:.2f}")

            # 🔄 번역 로직: 한국어 → 영어, 그 외 → 한국어
            try:
                if detected_language == "ko-KR":
                    # 한국어 → 영어 번역 (LLM 우선, 실패시 기본 Translate)
                    print("\n  🔄 한국어 → 영어 번역 중...")

                    translated_text = None
                    if bedrock_available:
                        print("    🤖 LLM 고품질 번역 시도 중...")
                        translated_text = translate_with_llm(
                            bedrock_client, best_result, "ko", "en"
                        )

                    if not translated_text:
                        print("    🔄 기본 AWS Translate 사용...")
                        translate_response = translate_client.translate_text(
                            Text=best_result,
                            SourceLanguageCode="ko",
                            TargetLanguageCode="en",
                        )
                        translated_text = translate_response["TranslatedText"]

                    print(f"  ✅ 번역 완료: '{translated_text}'")

                    return {
                        "original_text": best_result,
                        "original_language": detected_language,
                        "translated_text": translated_text,
                        "translated_language": "en",
                        "confidence": best_confidence,
                    }

                else:
                    # 그 외 언어 → 한국어 번역 (LLM 우선, 실패시 기본 Translate)
                    print(f"\n  🔄 {detected_language} → 한국어 번역 중...")

                    translated_text = None
                    if bedrock_available:
                        print("    🤖 LLM 고품질 번역 시도 중...")
                        source_lang_mapping = {
                            "en-US": "en",
                            "ja-JP": "ja",
                            "zh-CN": "zh",
                        }
                        source_lang = source_lang_mapping.get(detected_language, "en")
                        translated_text = translate_with_llm(
                            bedrock_client, best_result, source_lang, "ko"
                        )

                    if not translated_text:
                        print("    🔄 기본 AWS Translate 사용...")
                        # 언어별 소스 언어 코드 명시적 설정
                        source_lang_mapping = {
                            "en-US": "en",
                            "ja-JP": "ja",
                            "zh-CN": "zh",
                            "es-ES": "es",
                            "fr-FR": "fr",
                            "de-DE": "de",
                        }

                        source_lang = source_lang_mapping.get(detected_language, "auto")

                        translate_response = translate_client.translate_text(
                            Text=best_result,
                            SourceLanguageCode=source_lang,
                            TargetLanguageCode="ko",
                        )
                        translated_text = translate_response["TranslatedText"]

                    print(f"  ✅ 번역 완료: '{translated_text}'")

                    return {
                        "original_text": best_result,
                        "original_language": detected_language,
                        "translated_text": translated_text,
                        "translated_language": "ko",
                        "confidence": best_confidence,
                    }

            except Exception as translate_error:
                print(f"  ❌ 번역 오류: {translate_error}")
                # 번역 실패해도 원본은 반환
                return {
                    "original_text": best_result,
                    "original_language": detected_language,
                    "translated_text": best_result,  # 원본 그대로
                    "translated_language": detected_language,
                    "confidence": best_confidence,
                }
        else:
            print("  ❌ 모든 언어에서 인식 실패")
            return None

    except Exception as e:
        print(f"  💥 AWS Transcribe 오류: {e}")
        print("  💡 확인사항:")
        print(f"    - AWS_REGION: {os.getenv('AWS_REGION', '설정안됨')}")
        print(
            f"    - AWS_ACCESS_KEY_ID: {'설정됨' if os.getenv('AWS_ACCESS_KEY_ID') else '설정안됨'}"
        )
        print(
            f"    - AWS_SECRET_ACCESS_KEY: {'설정됨' if os.getenv('AWS_SECRET_ACCESS_KEY') else '설정안됨'}"
        )
        return None


def test_simple_transcribe_streaming():
    """간단한 AWS Transcribe Streaming 테스트 (app_new.py와 동일한 방식)"""
    print("\n🌊 AWS Transcribe Streaming 테스트:")

    # app_new.py에서 사용하는 동일한 로직으로 테스트
    # 실제로는 WebSocket 연결을 통해 실시간 스트리밍
    print("  ℹ️ 실제 스트리밍 테스트는 app_new.py 실행 상태에서 확인 가능")
    print("  ℹ️ 현재는 저장된 WAV 파일의 배치 처리만 테스트")
    return None


def main():
    """메인 테스트 함수"""
    print("🔬 WAV 파일 다중 언어 AWS Transcribe + 번역 테스트")
    print("=" * 80)

    # audio_debug 디렉토리에서 WAV 파일들 찾기
    wav_files = glob.glob("audio_debug/*.wav")

    if not wav_files:
        print("❌ audio_debug/ 디렉토리에 WAV 파일이 없습니다.")
        return

    # 최신 파일들만 테스트 (최대 3개 - AWS 요금 절약)
    wav_files.sort(key=os.path.getmtime, reverse=True)
    wav_files  #  = wav_files[:3]

    print(f"📂 발견된 파일 수: {len(wav_files)} (최신 3개 테스트)")

    results = []

    for wav_path in wav_files:
        print("\n" + "=" * 80)

        # 파일 정보 분석
        info = analyze_wav_file(wav_path)
        if not info:
            continue

        # AWS Transcribe 다중 언어 테스트
        transcribe_result = test_aws_transcribe_multilang(wav_path, info)

        results.append(
            {
                "file": os.path.basename(wav_path),
                "duration": info["duration"],
                "result": transcribe_result,
            }
        )

    # Streaming 테스트 안내
    test_simple_transcribe_streaming()

    # 결과 요약
    print("\n" + "=" * 80)
    print("📊 테스트 결과 요약:")
    print("-" * 80)

    for result in results:
        status = "✅ 성공" if result["result"] else "❌ 실패"
        print(f"\n🎵 {result['file']} ({result['duration']:.1f}초) - {status}")
        if result["result"]:
            # 다중 언어 결과 출력
            if isinstance(result["result"], dict):
                res = result["result"]
                print(
                    f"   📝 원본: '{res['original_text']}' ({res['original_language']})"
                )
                print(
                    f"   🌐 번역: '{res['translated_text']}' ({res['translated_language']})"
                )
                print(f"   📊 신뢰도: {res['confidence']:.2f}")
            else:
                print(f"   📝 인식 결과: '{result['result']}'")

    # 성공률 계산
    success_count = sum(1 for r in results if r["result"])
    total = len(results)

    print(
        f"\n🎯 전체 성공률: {success_count}/{total} ({success_count / total * 100:.1f}%)"
    )

    if success_count == 0:
        print("\n💡 모든 테스트가 실패했습니다. 확인사항:")
        print("   1. amazon-transcribe 라이브러리 설치: uv add amazon-transcribe")
        print("   2. AWS 자격 증명이 올바르게 설정되어 있는지 확인")
        print("   3. AWS Transcribe 서비스 권한이 있는지 확인")
        print("   4. WAV 파일 품질 및 음성 명확도")
        print("   5. 오디오 파일 포맷 (16kHz, 모노)")
        print("   6. 실시간 스트리밍 테스트는 app_new.py 실행으로 확인")


if __name__ == "__main__":
    main()
