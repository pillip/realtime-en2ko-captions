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
# `streamlit run app.py` 가 아니라 `serve.py` 다 (#158).
#
# Streamlit 은 스크립트를 **브라우저 세션 단위**로 실행하므로, `streamlit run`
# 으로 띄우면 아무도 :8501 을 열기 전까지 `app.py` 가 실행되지 않고 뷰어
# SSE(8766)/오퍼레이터 WS(8765) 도 뜨지 않는다. 컨테이너 재시작마다 관객이
# 흰 화면을 보는 원인이었다.
#
# `serve.py` 는 두 서버를 먼저 띄우고 **같은 프로세스에서** Streamlit 을 돌린다.
# 같은 프로세스여야 하는 이유는 WS 핸들러와 SSE 브로드캐스터가 모듈 전역 상태를
# 공유하기 때문이다. 서버 설정은 serve.py 가 STREAMLIT_* 환경변수 기본값으로 준다.
CMD ["python", "serve.py"]
