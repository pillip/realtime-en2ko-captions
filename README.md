# 🎤 실시간 다국어 자막 시스템

OpenAI Realtime API와 AWS Translate를 활용한 실시간 한국어-영어 자막 시스템입니다. 브라우저에서 WebRTC를 통해 OpenAI와 직접 통신하여 저지연 실시간 번역 자막을 제공합니다.

## ✨ 주요 기능

- 🎯 **실시간 음성 인식**: 저지연 실시간 영어/한국어 음성 텍스트 변환
- 🔄 **양방향 번역**: 한국어 ↔ 영어 실시간 번역
- 👥 **사용자 관리**: 개인별 계정 관리 및 사용량 추적
- ⏱️ **사용량 기반 과금**: 실제 음성 길이 기반 정확한 사용량 측정
- 🔧 **관리자 대시보드**: 사용자 생성/관리 및 사용량 통계
- 🎨 **직관적인 UI**: 채팅 UI와 크레딧롤 형태의 자막 표시
- 🎙️ **USB 마이크 지원**: USB 마이크와 시스템 오디오 선택 가능
- ⚙️ **유연한 설정**: 번역 지연시간 및 자막 설정 조정 가능
- 📱 **반응형 UI**: 모바일과 데스크톱 모두 지원

## 🛠️ 기술 스택

- **Backend**: Python, Streamlit, SQLite
- **Frontend**: Vanilla JavaScript, WebRTC
- **AI**: OpenAI Realtime API (gpt-4o-realtime-preview-2024-12-17)
- **Translation**: AWS Translate + Bedrock
- **Authentication**: PBKDF2-SHA256 with Salt
- **Database**: SQLite with usage tracking
- **Deployment**: Docker

## 🚀 빠른 시작

### 🏠 로컬 Docker 배포 (권장)

```bash
# 1. 저장소 클론
git clone <repository-url>
cd realtime-en2ko-captions

# 2. 로컬 배포 스크립트 실행
./local-deploy.sh
```

**스크립트가 자동으로 처리**:
- ✅ Docker 환경 확인
- 🔑 환경변수 설정 (.env 파일 생성 및 API 키 입력)
- 🔧 Docker 이미지 빌드 및 실행
- 🌐 http://localhost:8501 에서 접속 가능

### 🌐 서버 배포 (EC2/VPS)

```bash
# 원클릭 서버 배포
curl -sSL https://raw.githubusercontent.com/your-username/realtime-en2ko-captions/main/quick-deploy.sh | bash
```

## ⚙️ 수동 설정

### 1. 환경변수 설정

```bash
# .env 파일 생성
cp .env.example .env

# API 키 설정
# .env 파일을 편집하여 실제 API 키 입력
```

필수 환경변수:
```bash
# OpenAI 설정 (음성 인식용)
OPENAI_KEY=your_openai_api_key_here

# AWS 설정 (번역 서비스용)
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
AWS_REGION=us-east-1

# 관리자 계정 설정 (초기 설정용)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123!
ADMIN_EMAIL=admin@example.com
ADMIN_FULL_NAME=System Administrator
```

### 2. Docker Compose 실행

```bash
# Docker 환경에서 실행
docker-compose up -d --build

# 상태 확인
docker-compose ps

# 로그 확인
docker-compose logs -f
```

### 3. 로컬 개발 환경 (uv 사용)

```bash
# Python 환경 설정
uv sync

# 개발 서버 실행
uv run streamlit run app.py
```

## 📋 사전 요구사항

### API 키
- **OpenAI API Key**: [OpenAI Platform](https://platform.openai.com/)에서 발급
- **AWS 자격 증명**: AWS Access Key ID/Secret Access Key

### 시스템 요구사항
- **Docker**: Docker Desktop 또는 Docker Engine
- **브라우저**: Chrome, Edge, Safari (WebRTC 지원)
- **네트워크**: 인터넷 연결 (OpenAI, AWS API 호출)

## 🎯 사용 방법

1. **마이크 권한 허용**: 브라우저에서 마이크 접근 권한 허용
2. **오디오 장치 선택**: 설정에서 원하는 마이크/스피커 선택
3. **언어 설정**: 번역 방향 설정 (한→영, 영→한)
4. **자막 시작**: "시작" 버튼 클릭하여 실시간 자막 활성화

## 👥 사용자 관리 시스템

### 🔐 인증 및 로그인

시스템 접속 시 먼저 로그인이 필요합니다:

```
http://localhost:8501/login     # 로그인 페이지
http://localhost:8501/          # 메인 자막 시스템 (로그인 후)
http://localhost:8501/admin     # 관리자 대시보드 (관리자만)
```

### 👑 관리자 기능

관리자는 다음 기능을 사용할 수 있습니다:

1. **사용자 계정 생성**
   - 새로운 사용자 계정 생성
   - 사용 가능 시간 설정 (시간 단위)
   - 사용자 정보 관리 (이름, 이메일, 역할)

2. **사용자 관리**
   - 전체 사용자 목록 조회
   - 사용자별 사용량 및 남은 시간 확인
   - 사용자 정보 수정 및 사용 시간 추가
   - 계정 활성화/비활성화

3. **사용량 통계**
   - 전체/기간별 사용량 통계
   - 언어별 사용 패턴 분석
   - 실시간 사용량 모니터링

4. **로그 관리**
   - 상세한 사용량 로그 조회
   - 사용자별 활동 기록 추적

### ⏱️ 사용량 측정 방식

시스템은 다음과 같이 정확한 사용량을 측정합니다:

1. **실시간 측정**: OpenAI의 `speech_started`/`speech_stopped` 이벤트로 실제 음성 길이 측정
2. **Fallback 추정**: 타이밍 정보가 없을 경우 텍스트 길이 기반 추정 (`텍스트 길이 / 5.0` 초)
3. **실시간 체크**: 매 transcription 완료 시점에 사용량 확인 및 초과 시 즉시 차단
4. **상세 로그**: 모든 사용량이 메타데이터와 함께 데이터베이스에 기록

### 📊 사용량 제한

- **일반 사용자**: 계정별로 설정된 총 사용 가능 시간 (초 단위)
- **관리자**: 무제한 사용 가능
- **실시간 모니터링**: 사이드바에서 실시간 사용량 및 남은 시간 확인
- **자동 차단**: 사용량 초과 시 자동으로 서비스 차단

### 🗄️ 데이터베이스 구조

```sql
-- 사용자 테이블
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,  -- PBKDF2-SHA256
    salt TEXT NOT NULL,
    role TEXT DEFAULT 'user',     -- 'admin' 또는 'user'
    total_usage_seconds INTEGER DEFAULT 0,
    usage_limit_seconds INTEGER DEFAULT 3600,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 사용량 로그 테이블
CREATE TABLE usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,         -- 'transcribe'
    duration_seconds INTEGER NOT NULL,
    source_language TEXT,
    target_language TEXT,
    metadata TEXT,                -- JSON: 추가 정보
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 🔧 설정 옵션

- **번역 지연시간**: 0.5초~3초 조정 가능
- **원문 표시**: 원문과 번역문 동시 표시
- **자막 스타일**: 채팅형/크레딧롤형 선택
- **음성 감도**: 마이크 감도 조절

## 📊 성능 최적화

- **지연 시간**: 평균 1-1.5초 (목표)
- **비용 효율**: AWS 사용량 42% 절약
- **메모리 사용량**: 대폭 감소 (미사용 코드 제거)

## 🛠️ 문제 해결

### 일반적인 문제들

1. **마이크 권한 오류**: 브라우저 설정에서 마이크 권한 확인
2. **Docker 실행 오류**: Docker Desktop 실행 상태 확인
3. **API 연결 오류**: .env 파일의 API 키 확인
4. **포트 충돌**: 8501 포트 사용 중인 프로세스 종료

### 로그 확인

```bash
# Docker 로그 확인
docker-compose logs -f realtime-caption

# 실시간 상태 모니터링
docker-compose ps
```

## 📚 추가 문서

- **[배포 가이드](DEPLOYMENT.md)**: 상세한 배포 및 운영 가이드
- **[개선 사항](IMPROVEMENT_TASKLIST.md)**: 향후 개선 계획
- **[기술 명세](CLAUDE.md)**: 개발자를 위한 기술 문서

## 🤝 기여하기

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'feat: add amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

## 📄 라이선스

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🎉 Thanks

- OpenAI for the Realtime API
- AWS for translation services
- Streamlit for the web framework
- The open source community

---

**🚀 빠른 시작**: `./local-deploy.sh` 실행 후 http://localhost:8501 접속!
