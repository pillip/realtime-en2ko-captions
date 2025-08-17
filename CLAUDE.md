# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Real-time English-to-Korean caption system for conference scenarios. Browser connects directly to OpenAI Realtime API via WebRTC, with Streamlit server handling ephemeral token generation for security.

**Critical Architecture Decision**: Direct browser-to-OpenAI WebRTC connection (not server-proxied) for minimal latency, following OpenAI's recommended ephemeral token pattern.

## Development Commands

```bash
# Project setup
uv init --python 3.11
uv add streamlit requests python-dotenv

# Development workflow  
uv sync                           # Install dependencies
uv run streamlit run app.py       # Run development server
uv run pytest -q                  # Run tests

# Docker deployment
uv export -o requirements.txt     # Export for container
docker build -t realtime-caption .
docker run --rm -p 8501:8501 -e OPENAI_API_KEY=sk-... realtime-caption
```

## Essential Environment Variables

- `OPENAI_API_KEY`: Required for ephemeral token generation (server-side only)
- `REALTIME_MODEL`: Optional, defaults to `gpt-4o-mini-realtime-preview`

## Core Architecture

**Two-Component System**:
1. **Streamlit Server**: Generates ephemeral tokens, serves embedded JS component via `st.components.v1.html`
2. **Browser Component**: Handles device selection, WebRTC connection, real-time caption rendering

**Key Security Pattern**: Long-term API key never reaches browser. Server issues short-lived ephemeral tokens for each session.

**Data Flow**: Audio (USB/mic) → WebRTC → OpenAI Realtime → DataChannel events → Korean captions → Credit-roll UI

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