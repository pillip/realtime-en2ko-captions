# Real-time Multilingual Caption System

## Purpose
Real-time multilingual caption system for conference scenarios that provides speech recognition and translation capabilities. The system enables browser-to-AWS direct connection for minimal latency transcription and translation.

## Tech Stack
- **Frontend**: HTML/JavaScript with embedded Streamlit components
- **Backend**: Python 3.11+ with Streamlit framework
- **Cloud Services**: AWS Transcribe Streaming, Amazon Translate
- **Audio Processing**: WebRTC, AudioWorklet for real-time audio streaming
- **Package Management**: UV (modern Python package manager)
- **Code Quality**: Ruff (linting), Black (formatting), Pre-commit hooks

## Key Dependencies
- streamlit: Web app framework
- boto3/botocore: AWS SDK for Python
- amazon-transcribe: AWS Transcribe integration
- websockets: WebSocket communication
- python-dotenv: Environment variable management

## Architecture
**Two-Component System**:
1. **Streamlit Server**: Manages AWS credentials, serves embedded JS component via `st.components.v1.html`
2. **Browser Component**: Handles device selection, AWS SDK integration, real-time caption rendering

**Security Pattern**: Long-term AWS keys never reach browser. Server passes temporary credentials for each session.

**Data Flow**: Audio (USB/mic) → AudioWorklet → AWS Transcribe Streaming → Text transcription → Amazon Translate → Multilingual captions → Credit-roll UI