#!/bin/bash

# AWS ALB + ACM SSL 환경 배포 스크립트

set -e

echo "🚀 ALB 환경 배포 시작..."

# Docker 설치 (Ubuntu)
if ! command -v docker > /dev/null; then
    echo "📦 Docker 설치..."
    sudo apt-get update
    sudo apt-get install -y docker.io
    sudo systemctl start docker
    sudo systemctl enable docker
    sudo usermod -aG docker $USER
fi

# 프로젝트 준비
if [ ! -d "realtime-en2ko-captions" ]; then
    git clone https://github.com/pillip/realtime-en2ko-captions.git
    cd realtime-en2ko-captions
else
    cd realtime-en2ko-captions
    git pull
fi

# Docker 이미지 빌드
echo "🔧 Docker 이미지 빌드..."
sudo docker build -t realtime-caption:latest .

# 기존 컨테이너 정리
sudo docker stop realtime-caption 2>/dev/null || true
sudo docker rm realtime-caption 2>/dev/null || true

# 환경변수 설정
if [ -z "$OPENAI_API_KEY" ]; then
    echo "⚠️  OPENAI_API_KEY 환경변수를 설정하세요:"
    echo "export OPENAI_API_KEY=your_key_here"
    echo "또는 .env 파일 생성"
    read -p "OpenAI API Key를 입력하세요: " -s OPENAI_API_KEY
    echo
fi

# ALB 환경용 컨테이너 실행
echo "🚀 ALB 환경용 애플리케이션 시작..."
sudo docker run -d \
  --name realtime-caption \
  --restart unless-stopped \
  -p 8501:8501 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e REALTIME_MODEL="${REALTIME_MODEL:-gpt-4o-realtime-preview}" \
  realtime-caption:latest

echo "✅ 배포 완료!"
echo "🔍 컨테이너 상태:"
sudo docker ps | grep realtime-caption

echo ""
echo "🌐 ALB 설정 후 접속:"
echo "   https://your-domain.com"
echo ""
echo "📋 유용한 명령어:"
echo "   로그 확인: sudo docker logs realtime-caption"
echo "   컨테이너 재시작: sudo docker restart realtime-caption"
echo "   중지: sudo docker stop realtime-caption"
