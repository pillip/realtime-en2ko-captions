# Codebase Structure

## Root Directory
```
realtime-en2ko-captions/
├── app.py                    # Main Streamlit application
├── components/
│   └── webrtc.html          # Browser WebRTC component with JS logic
├── pyproject.toml           # Python project configuration and dependencies
├── requirements.txt         # Docker deployment requirements
├── CLAUDE.md               # Claude Code development guidance
├── README.md               # Project documentation
├── prd.md                  # Product requirements document
├── tasklist.md             # Task tracking document
├── .env.example            # Environment variable template
├── Dockerfile              # Container configuration
├── uv.lock                 # Dependency lock file
├── test_*.py               # Test scripts for various components
├── deploy-*.sh             # Deployment scripts
└── audio_debug/            # Audio debugging files
```

## Core Components

### app.py
- Main Streamlit server application
- Handles AWS credential management
- Serves the WebRTC HTML component
- Manages temporary AWS credentials for browser

### components/webrtc.html
- Complete browser-side implementation
- WebRTC audio capture and streaming
- AWS Transcribe Streaming integration
- Real-time caption rendering and UI
- Audio device selection and management
- Translation request handling

## Key Configuration Files

### pyproject.toml
- Python 3.11+ requirement
- UV package management configuration
- Black and Ruff code quality settings
- Main dependencies: streamlit, boto3, amazon-transcribe

### .pre-commit-config.yaml
- Automated code quality checks
- Ruff linting and formatting
- Black formatting enforcement

## Test Files
- `test_transcribe.py`: Audio transcription testing
- `test_websocket.py`: WebSocket server testing
- `test_token.py`: AWS token validation

## Deployment Files
- `deploy-ec2.sh`: EC2 deployment script
- `deploy-alb.sh`: Application Load Balancer setup
- `quick-deploy.sh`: Quick deployment utility
- `aws-alb-setup.md`: ALB configuration guide