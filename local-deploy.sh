#!/bin/bash
# 실시간 자막 시스템 - 로컬에서 Docker 배포 스크립트 (clone 완료 가정)

set -e

echo "🚀 실시간 자막 시스템 로컬 Docker 배포 시작..."

# Docker 설치 확인
if ! command -v docker > /dev/null; then
    echo "❌ Docker가 설치되지 않았습니다."
    echo "Docker Desktop을 설치하고 다시 실행하세요."
    echo "다운로드: https://www.docker.com/products/docker-desktop"
    exit 1
fi

# Docker 실행 확인
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker가 실행되지 않고 있습니다."
    echo "Docker Desktop을 실행하고 다시 시도하세요."
    exit 1
fi

# Docker Compose 설치 확인
if ! command -v docker-compose > /dev/null; then
    echo "❌ Docker Compose가 설치되지 않았습니다."
    echo "Docker Desktop을 최신 버전으로 업데이트하세요."
    exit 1
fi

echo "✅ Docker 환경 확인 완료"

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

    # .env 파일 업데이트 (macOS/Linux 호환)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s/your_openai_api_key_here/$OPENAI_KEY/" .env
        sed -i '' "s/your_aws_access_key_id/$AWS_ACCESS_KEY_ID/" .env
        sed -i '' "s/your_aws_secret_access_key/$AWS_SECRET_ACCESS_KEY/" .env
    else
        # Linux
        sed -i "s/your_openai_api_key_here/$OPENAI_KEY/" .env
        sed -i "s/your_aws_access_key_id/$AWS_ACCESS_KEY_ID/" .env
        sed -i "s/your_aws_secret_access_key/$AWS_SECRET_ACCESS_KEY/" .env
    fi

    echo "✅ 환경변수 설정 완료"
else
    echo "✅ .env 파일이 이미 존재합니다"
fi

# 기존 컨테이너 정리
echo "🧹 기존 컨테이너 정리..."
docker-compose down 2>/dev/null || true

# Docker 이미지 빌드 및 실행
echo "🔧 Docker 이미지 빌드 중..."
docker-compose build --no-cache
docker-compose up -d

# 배포 결과 확인
echo ""
echo "⏳ 컨테이너 시작 대기 중..."
sleep 5

if docker-compose ps | grep -q "Up"; then
    echo "✅ 배포 완료!"
    echo "🌐 접속 URL: http://localhost:8501"
    echo ""
    echo "📊 상태 확인: docker-compose ps"
    echo "📋 로그 확인: docker-compose logs -f"
    echo "🛑 중지: docker-compose down"
    echo ""
    echo "🚀 브라우저에서 http://localhost:8501 로 접속하세요!"
else
    echo "❌ 배포 실패. 로그를 확인하세요:"
    docker-compose logs
    exit 1
fi
