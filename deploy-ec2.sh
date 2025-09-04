#!/bin/bash

# EC2 Ubuntu/Amazon Linux Docker 설치 및 애플리케이션 실행 스크립트

set -e

echo "🚀 EC2 배포 시작..."

# Docker 설치 (Ubuntu)
if command -v apt-get > /dev/null; then
    echo "📦 Docker 설치 (Ubuntu)..."
    sudo apt-get update
    sudo apt-get install -y docker.io docker-compose
    sudo systemctl start docker
    sudo systemctl enable docker
    sudo usermod -aG docker $USER
    echo "✅ Docker 설치 완료"
fi

# Docker 설치 (Amazon Linux)
if command -v yum > /dev/null; then
    echo "📦 Docker 설치 (Amazon Linux)..."
    sudo yum update -y
    sudo yum install -y docker
    sudo systemctl start docker
    sudo systemctl enable docker
    sudo usermod -aG docker ec2-user
    echo "✅ Docker 설치 완료"
fi

# Git 설치 확인
if ! command -v git > /dev/null; then
    if command -v apt-get > /dev/null; then
        sudo apt-get install -y git
    else
        sudo yum install -y git
    fi
fi

# 프로젝트 클론 (이미 존재하면 업데이트)
if [ ! -d "realtime-en2ko-captions" ]; then
    echo "📁 프로젝트 클론..."
    git clone https://github.com/your-username/realtime-en2ko-captions.git
    cd realtime-en2ko-captions
else
    echo "📁 프로젝트 업데이트..."
    cd realtime-en2ko-captions
    git pull
fi

# Docker 이미지 빌드
echo "🔧 Docker 이미지 빌드..."
sudo docker build -t realtime-caption:latest .

# 기존 컨테이너 정리
echo "🧹 기존 컨테이너 정리..."
sudo docker stop realtime-caption 2>/dev/null || true
sudo docker rm realtime-caption 2>/dev/null || true

# 환경변수 파일 생성 안내
if [ ! -f ".env" ]; then
    echo "⚠️  .env 파일이 없습니다. 생성해주세요:"
    echo "OPENAI_API_KEY=your_openai_api_key_here"
    echo "REALTIME_MODEL=gpt-4o-mini-realtime-preview"
    echo ""
    echo "또는 직접 환경변수로 실행:"
    echo "sudo docker run -d --name realtime-caption -p 8501:8501 \\"
    echo "  -e OPENAI_API_KEY=your_key_here \\"
    echo "  realtime-caption:latest"
    exit 1
fi

# .env 파일에서 환경변수 읽기
source .env

# 컨테이너 실행
echo "🚀 애플리케이션 시작..."
sudo docker run -d \
  --name realtime-caption \
  --restart unless-stopped \
  -p 8501:8501 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e REALTIME_MODEL="$REALTIME_MODEL" \
  realtime-caption:latest

echo "✅ 배포 완료!"
echo "🌐 접속 URL: http://$(curl -s ifconfig.me):8501"
echo ""
echo "📊 컨테이너 상태 확인:"
sudo docker ps
echo ""
echo "📋 로그 확인: sudo docker logs realtime-caption"
echo "🛑 중지: sudo docker stop realtime-caption"
