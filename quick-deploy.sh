#!/bin/bash
# EC2 빠른 배포 스크립트

# Docker 설치 (Ubuntu)
sudo apt update && sudo apt install -y docker.io git
sudo systemctl start docker
sudo usermod -aG docker $USER

# 프로젝트 클론
git clone https://github.com/your-username/realtime-en2ko-captions.git
cd realtime-en2ko-captions

# 이미지 빌드
sudo docker build -t realtime-caption .

# 실행 (환경변수를 직접 입력하세요)
echo "Enter your OpenAI API Key:"
read -s OPENAI_API_KEY

sudo docker run -d \
  --name realtime-caption \
  --restart unless-stopped \
  -p 8501:8501 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  realtime-caption:latest

echo "✅ 배포 완료!"
echo "🌐 접속: http://$(curl -s ifconfig.me):8501"
