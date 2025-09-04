FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# uv 환경에서 내보낸 requirements 사용
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app

EXPOSE 8501

# OPENAI_API_KEY는 런타임 주입 (-e) 권장
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
