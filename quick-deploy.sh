#!/bin/bash
# 실시간 자막 시스템 - 빠른 배포 스크립트

set -e

echo "🚀 실시간 자막 시스템 배포 시작..."

# Docker 설치 (Ubuntu)
if ! command -v docker > /dev/null; then
    echo "📦 Docker 설치 중..."
    sudo apt update && sudo apt install -y docker.io docker-compose git curl
    sudo systemctl start docker
    sudo systemctl enable docker
    sudo usermod -aG docker $USER
    echo "⚠️  Docker 그룹 권한을 위해 재로그인이 필요합니다."
fi

# 프로젝트 클론 또는 업데이트
if [ ! -d "realtime-en2ko-captions" ]; then
    echo "📁 프로젝트 클론..."
    git clone https://github.com/your-username/realtime-en2ko-captions.git
    cd realtime-en2ko-captions
else
    echo "📁 프로젝트 업데이트..."
    cd realtime-en2ko-captions
    git pull
fi

# 환경변수 설정
echo "🔑 환경변수 설정..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "⚠️  .env 파일을 생성했습니다. API 키를 설정하세요:"

    echo -n "OpenAI API Key를 입력하세요: "
    read -s OPENAI_KEY
    echo

    echo -n "AWS Access Key ID를 입력하세요: "
    read AWS_ACCESS_KEY_ID

    echo -n "AWS Secret Access Key를 입력하세요: "
    read -s AWS_SECRET_ACCESS_KEY
    echo

    # .env 파일 업데이트
    sed -i "s/your_openai_api_key_here/$OPENAI_KEY/" .env
    sed -i "s/your_aws_access_key_id/$AWS_ACCESS_KEY_ID/" .env
    sed -i "s/your_aws_secret_access_key/$AWS_SECRET_ACCESS_KEY/" .env
fi

# 기존 컨테이너 정리
echo "🧹 기존 컨테이너 정리..."
sudo docker stop realtime-caption 2>/dev/null || true
sudo docker rm realtime-caption 2>/dev/null || true

# Docker Compose로 빌드 및 실행
echo "🔧 애플리케이션 빌드 및 실행..."
sudo docker-compose up -d --build

echo "✅ 배포 완료!"
echo "🌐 접속 URL: http://$(curl -s ifconfig.me):8501"
echo ""
echo "📊 상태 확인: sudo docker-compose ps"
echo "📋 로그 확인: sudo docker-compose logs -f"
echo "🛑 중지: sudo docker-compose down"
