# 🚀 실시간 자막 시스템 배포 가이드

## 📋 개요

AWS Transcribe + Translate + Bedrock 기반 실시간 다중언어 자막 시스템의 배포 가이드입니다.

**시스템 구성**:
- **ASR**: OpenAI Realtime API (한국어/영어 음성 인식)
- **번역**: AWS Translate + Bedrock (번역 품질 향상)
- **인프라**: Docker + Streamlit

---

## 🛠️ 사전 준비

### 1. API 키 준비

#### OpenAI API 키
- [OpenAI Platform](https://platform.openai.com/)에서 API 키 발급
- Realtime API 사용 권한 확인 필요

#### AWS 자격 증명
- AWS Access Key ID, Secret Access Key 준비
- 필요한 권한:
  - `translate:TranslateText`
  - `bedrock:InvokeModel` (선택사항, 번역 품질 향상용)

### 2. 시스템 요구사항

**최소 사양**:
- CPU: 2 코어
- RAM: 4GB
- 디스크: 10GB
- OS: Ubuntu 20.04+ / Amazon Linux 2

**네트워크**:
- 인터넷 연결 필수 (OpenAI, AWS API 호출)
- 포트 8501 개방 (Streamlit 서버)

---

## 🚀 빠른 배포 (권장)

### Ubuntu/Debian에서 원클릭 배포

```bash
# 배포 스크립트 다운로드 및 실행
curl -sSL https://raw.githubusercontent.com/your-username/realtime-en2ko-captions/main/quick-deploy.sh | bash
```

또는 직접 스크립트 실행:

```bash
# 저장소 클론
git clone https://github.com/your-username/realtime-en2ko-captions.git
cd realtime-en2ko-captions

# 배포 스크립트 실행
chmod +x quick-deploy.sh
./quick-deploy.sh
```

스크립트가 자동으로:
1. Docker 설치
2. 환경변수 설정 (.env 파일 생성)
3. 애플리케이션 빌드 및 실행

---

## 🔧 수동 배포

### 1. Docker 설치

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y docker.io docker-compose git curl
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER

# 재로그인 필요
```

### 2. 프로젝트 설정

```bash
# 프로젝트 클론
git clone https://github.com/your-username/realtime-en2ko-captions.git
cd realtime-en2ko-captions

# 환경변수 설정
cp .env.example .env
nano .env  # API 키 입력
```

### 3. Docker Compose로 실행

```bash
# 빌드 및 실행
docker-compose up -d --build

# 상태 확인
docker-compose ps

# 로그 확인
docker-compose logs -f
```

---

## ⚙️ 환경변수 설정

`.env` 파일 설정:

```bash
# AWS 설정 (번역 서비스용)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key

# OpenAI 설정 (음성 인식용)
OPENAI_KEY=your_openai_api_key_here

# 선택적 설정
STREAMLIT_SERVER_PORT=8501
STREAMLIT_SERVER_ADDRESS=0.0.0.0
```

---

## 🌐 프로덕션 배포

### AWS EC2에 배포

1. **EC2 인스턴스 생성**
   - 인스턴스 타입: t3.medium 이상
   - 보안 그룹: 포트 8501, 22 개방
   - 키 페어 설정

2. **EC2 접속 및 배포**
   ```bash
   ssh -i your-key.pem ubuntu@your-ec2-ip
   curl -sSL https://raw.githubusercontent.com/your-username/realtime-en2ko-captions/main/quick-deploy.sh | bash
   ```

3. **접속 확인**
   ```
   http://your-ec2-ip:8501
   ```

### 도메인 연결 (선택사항)

Nginx 프록시 설정:

```bash
# Nginx와 함께 실행
docker-compose --profile with-proxy up -d --build
```

---

## 📊 운영 및 관리

### 상태 확인

```bash
# 컨테이너 상태
docker-compose ps

# 실시간 로그
docker-compose logs -f realtime-caption

# 리소스 사용량
docker stats
```

### 업데이트

```bash
# 최신 코드 가져오기
git pull

# 재빌드 및 재시작
docker-compose up -d --build
```

### 백업 및 복원

```bash
# 로그 백업
docker-compose logs realtime-caption > backup-$(date +%Y%m%d).log

# 설정 백업
tar -czf config-backup.tar.gz .env docker-compose.yml
```

---

## 🛡️ 보안 고려사항

### 1. 방화벽 설정
```bash
# Ubuntu UFW 예시
sudo ufw allow 22    # SSH
sudo ufw allow 8501  # Streamlit
sudo ufw enable
```

### 2. HTTPS 설정 (권장)
- Let's Encrypt 인증서 사용
- Nginx 프록시로 SSL 터미네이션

### 3. 로그 관리
- 로그 순환 설정
- 민감한 정보 마스킹

---

## 🔍 문제 해결

### 일반적인 문제들

1. **Docker 권한 오류**
   ```bash
   sudo usermod -aG docker $USER
   # 재로그인 필요
   ```

2. **포트 충돌**
   ```bash
   # 다른 프로세스가 8501 포트 사용 중인지 확인
   sudo lsof -i :8501
   ```

3. **OpenAI API 오류**
   - API 키 유효성 확인
   - Realtime API 사용 권한 확인
   - 사용량 한도 확인

4. **AWS 연결 오류**
   - AWS 자격 증명 확인
   - IAM 권한 확인
   - 네트워크 연결 확인

### 로그 분석

```bash
# 상세 로그 확인
docker-compose logs -f realtime-caption | grep -E "(ERROR|WARN)"

# 성능 모니터링
docker-compose exec realtime-caption top
```

---

## 📈 성능 최적화

### 1. 리소스 제한 설정

`docker-compose.yml`에 추가:

```yaml
services:
  realtime-caption:
    # ... 기존 설정
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 1G
```

### 2. 로그 순환 설정

```yaml
services:
  realtime-caption:
    # ... 기존 설정
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

---

## 📞 지원

- **GitHub Issues**: 버그 리포트 및 기능 요청
- **문서**: README.md 및 코드 주석 참조
- **로그**: 문제 발생 시 로그 파일 첨부

---

## 🔄 업데이트 이력

- **v1.0**: 초기 배포 버전
- **v1.1**: AWS Transcribe 제거, OpenAI 전용으로 최적화
- **v1.2**: Docker 설정 개선, 배포 자동화

---

**배포 성공을 위한 체크리스트**:
- [ ] API 키 설정 완료
- [ ] Docker 설치 및 권한 설정
- [ ] 포트 8501 개방
- [ ] 환경변수 설정 확인
- [ ] 헬스체크 통과 확인
- [ ] 웹 인터페이스 접속 테스트
