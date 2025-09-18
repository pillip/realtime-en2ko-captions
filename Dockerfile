FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true

WORKDIR /app

# 시스템 의존성 설치 (오디오 처리용)
RUN apt-get update && apt-get install -y \
    build-essential \
    libportaudio2 \
    libportaudiocpp0 \
    portaudio19-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# uv 환경에서 내보낸 requirements 사용
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# 애플리케이션 코드 복사
COPY . /app

# 헬스체크 추가
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501 || exit 1

EXPOSE 8501

# 필수 환경변수 체크 및 실행
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", "--server.enableCORS=false"]
