# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Real-time multilingual caption system for conference scenarios. Browser connects to AWS Transcribe Streaming for speech recognition and Amazon Translate for translation, with Streamlit server handling AWS credential management for security.

**Critical Architecture Decision**: Direct browser-to-AWS services connection using temporary credentials for minimal latency. Speech recognition via AWS Transcribe Streaming with automatic language detection, followed by contextual translation (Korean ↔ English, Others → Korean).

## Development Commands

```bash
# Project setup
uv init --python 3.11
uv add streamlit boto3 python-dotenv aiohttp  # aiohttp powers the SSE viewer broadcast server (ISSUE-30)
uv add 'qrcode[pil]'                            # QR code generation for viewer URLs (ISSUE-32)

# Development workflow
uv sync                           # Install dependencies
uv run streamlit run app.py       # Run development server
uv run pytest -q                  # Run tests

# Code quality
uv run ruff check .               # Check linting issues
uv run ruff check --fix .         # Fix auto-fixable linting issues
uv run black .                    # Apply black formatting
uv run pre-commit run --all-files # Run all pre-commit hooks
uv run pre-commit install         # Install pre-commit hooks (one-time)

# Docker deployment
uv export -o requirements.txt     # Export for container (aiohttp/qrcode 포함 — 의존성 변경 시 재실행)
docker build -t realtime-caption .
docker run --rm -p 8501:8501 -p 8765:8765 -p 8766:8766 \
  -e OPENAI_KEY=sk-... \
  -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... \
  -e VIEWER_BASE_URL=http://<host>:8766 \
  realtime-caption
# 권장: docker-compose up -d --build  (.env 자동 로드, 포트/볼륨 일괄 구성)
```

## Essential Environment Variables

- `AWS_ACCESS_KEY_ID`: Required for AWS service authentication (server-side only)
- `AWS_SECRET_ACCESS_KEY`: Required for AWS service authentication (server-side only)
- `AWS_REGION`: Optional, defaults to `ap-northeast-2` (Seoul). Bedrock translation uses `global.` cross-region inference profiles (translation.py), which are callable from any commercial region — Seoul has no `apac.` profile for the Claude 4.5 generation, so the `global.` prefix is required there.
- `WS_PORT`: Optional, defaults to `8765`. Fixed TCP port for the translation-pipeline WebSocket server (process-wide singleton). Must match the Docker port mapping — dynamic per-session allocation leaked past the mapping (#84).
- `SSE_PORT`: Optional, defaults to `8766`. TCP port for the unauthenticated viewer SSE broadcast server (`/stream/{room_id}`, ISSUE-30).
- `VIEWER_BASE_URL`: Optional, defaults to `http://localhost:{SSE_PORT}`. Base URL embedded into QR codes (ISSUE-32) — set to the public viewer host (e.g. `https://captions.example.com`) in production so attendees scan a reachable URL.

## Core Architecture

**Two-Component System**:
1. **Streamlit Server**: Manages AWS credentials, serves embedded JS component via `st.components.v1.html`
2. **Browser Component**: Handles device selection, AWS SDK integration, real-time caption rendering

**Key Security Pattern**: Long-term AWS keys never reach browser. Server passes temporary credentials for each session.

**Data Flow**: Audio (USB/mic) → AudioWorklet → AWS Transcribe Streaming → Text transcription → Amazon Translate → Multilingual captions → Credit-roll UI

## Critical Implementation Details

### Audio Device Handling
- Browser permission required before device labels visible: `getUserMedia()` first, then `enumerateDevices()`
- Line input optimization: `echoCancellation:false, noiseSuppression:false, autoGainControl:false`
- Device switching requires stream recreation with new `deviceId`

### WebRTC Connection Sequence (Realtime GA, 2026-05-12 이후)
1. Server: `POST /v1/realtime/client_secrets` → ephemeral client secret (`ek_...`, model/세션 설정은 이 시점에 서버가 고정)
2. Browser: SDP offer creation with audio track
3. Browser: `POST /v1/realtime/calls` with `Authorization: Bearer ek_...` + `Content-Type: application/sdp` → answer (구 `POST /v1/realtime?model=...` 은 GA에서 400)
4. RTCPeerConnection established, DataChannel for caption events

### Caption Event Processing
- OpenAI session is **transcription-only** (#100): `turn_detection.create_response=false`, so OpenAI does NOT auto-generate translation responses (they were discarded and wasted tokens). The only DataChannel event the browser acts on is `conversation.item.input_audio_transcription.completed`.
- On that event the browser sends the transcript to the server WebSocket (`/ws` → 8765); **all translation is server-side Bedrock** (translation.py), which also fans out to viewers via SSE. OpenAI produces no `response.*` events.
- Browser MUST NOT send `session.update` — GA session config is fully pinned server-side at client_secret creation (#89)
- UI transition: gray/italic temporary text → confirmed lines appended to scroll

### UI Behavior Requirements
- Credit-roll: auto-scroll only when user at bottom (`scrollTop` detection)
- Resizable container: `resize: both` (browser compatibility varies)
- Stop action: immediate RTCPeerConnection close + caption list reset

## Session Management Instructions for OpenAI Model

Template for session instructions:
```
"영어 발화를 한국어 자막으로 자연스럽게 번역. 2줄/줄당 16~23자, 고유명사 원어 유지."
```

## Browser Compatibility Targets

Chrome/Edge/Safari latest for WebRTC features. Fallback considerations for `resize` CSS property limitations.

## Performance Targets

- First caption: <2 seconds end-to-end latency
- Stable connection for 30+ minute sessions
- Graceful handling of ephemeral token expiration (manual restart in MVP)
