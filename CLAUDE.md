# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Real-time multilingual caption system for conference scenarios. Browser connects to AWS Transcribe Streaming for speech recognition and Amazon Translate for translation, with Streamlit server handling AWS credential management for security.

**Critical Architecture Decision**: Direct browser-to-AWS services connection using temporary credentials for minimal latency. Speech recognition via AWS Transcribe Streaming with automatic language detection, followed by contextual translation (Korean ↔ English, Others → Korean).

## Development Commands

```bash
# Project setup
uv init --python 3.11
uv add streamlit boto3 python-dotenv

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
uv export -o requirements.txt     # Export for container
docker build -t realtime-caption .
docker run --rm -p 8501:8501 -e OPENAI_API_KEY=sk-... realtime-caption
```

## Essential Environment Variables

- `AWS_ACCESS_KEY_ID`: Required for AWS service authentication (server-side only)
- `AWS_SECRET_ACCESS_KEY`: Required for AWS service authentication (server-side only)
- `AWS_REGION`: Optional, defaults to `us-east-1`

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

### WebRTC Connection Sequence
1. Server: `POST /v1/realtime/sessions` → ephemeral token
2. Browser: SDP offer creation with audio track
3. Browser: `POST /v1/realtime?model=...` with token + SDP → answer
4. RTCPeerConnection established, DataChannel for caption events

### Caption Event Processing
- Handle both delta (unstable) and completed (stable) events from DataChannel
- Event types vary: `response.text.delta`, `conversation.item.input_audio_transcription.completed`
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
