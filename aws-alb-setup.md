# AWS ALB + ACM SSL 배포 가이드

실시간 자막 시스템은 **3개의 포트**를 사용하며, ALB가 경로에 따라 각각 다른
타겟그룹으로 라우팅해야 한다. 하나라도 빠지면 오퍼레이터 또는 뷰어가 동작하지
않는다.

| 포트 | 서버 | 경로 | 용도 |
|------|------|------|------|
| 8501 | Streamlit | 기본(그 외 전부) | 오퍼레이터/관리자 UI |
| 8766 | aiohttp SSE | `/view/*`, `/stream/*` | 뷰어 페이지 + 자막 스트림 |
| 8765 | WebSocket | `/ws` | 오퍼레이터 번역 파이프라인 |

> 코드는 이미 이 경로 규약에 맞춰져 있다: 뷰어는 same-origin `/stream/{room}`
> 으로 SSE를 열고(components/viewer.html), 오퍼레이터는 배포 환경에서
> `wss://<도메인>/ws` 로 접속한다(components/webrtc.html).

---

## 1. Target Group 3개 생성

**EC2 Console → Target Groups → Create target group** 를 3번 반복한다.
공통: Target type `Instances`, VPC = 앱이 있는 VPC, 대상 EC2 인스턴스 등록.

### 1-1. `tg-streamlit` (8501)
- Protocol/Port: `HTTP` / `8501`
- Health check: Protocol `HTTP`, Path **`/_stcore/health`**, Success codes `200`

### 1-2. `tg-viewer` (8766) — 뷰어 SSE
- Protocol/Port: `HTTP` / `8766`
- Health check: Protocol `HTTP`, Path **`/health`** (SSE 서버가 `OK` 200 반환),
  Success codes `200`
  - ⚠️ 기본값 `/` 는 8766에서 404라 unhealthy가 된다. 반드시 `/health` 로 지정.

### 1-3. `tg-ws` (8765) — 오퍼레이터 WebSocket
- Protocol/Port: `HTTP` / `8765`
- Health check: **TCP** (포트 열림만 확인) 권장.
  - raw WebSocket 서버는 HTTP GET에 200을 주지 않는다(426 Upgrade Required).
    HTTP 헬스체크를 쓰려면 Success codes에 `426` 을 포함해야 한다.

---

## 2. ALB 리스너 설정

### HTTP 리스너 (80): HTTPS로 리다이렉트
- Action: Redirect to HTTPS, Port 443, Status code HTTP 301

### HTTPS 리스너 (443)
- Protocol/Port: `HTTPS` / `443`
- SSL certificate: **ACM에서 발급받은 인증서 선택**
- **규칙(위에서부터 우선순위 순)**:
  1. IF Path is `/view/*` OR `/stream/*` → Forward to **tg-viewer** (8766)
  2. IF Path is `/ws` → Forward to **tg-ws** (8765)
  3. **Default** → Forward to **tg-streamlit** (8501)

### idle timeout 상향 (필수)
- ALB 속성 → **Idle timeout: 300초 이상** (기본 60초).
- SSE(뷰어 자막)와 WebSocket(오퍼레이터)은 장시간 연결을 유지하므로, 60초면
  30분 세션 도중 계속 끊긴다.

---

## 3. Route 53 도메인 연결

**Route 53 → Hosted zones → your-domain.com → Create record**
- Record name: (비워두면 root) / Type `A` / Alias `Yes`
- Alias target: Application Load Balancer → ALB 리전 → 해당 ALB 선택

---

## 4. EC2 보안 그룹

**인바운드 규칙** — 3개 포트를 모두, 가급적 **ALB 보안 그룹에서만** 허용:
- `8501` (Streamlit) ← ALB SG
- `8766` (뷰어 SSE) ← ALB SG
- `8765` (오퍼레이터 WS) ← ALB SG
- `22` (SSH) ← 본인 IP만

> 8766/8765 를 빠뜨리면 ALB가 해당 타겟에 도달하지 못해 **unhealthy** 가 된다
> (헬스체크 경로가 맞아도 소용없음 — connection refused).

---

## 5. 애플리케이션 환경변수

프로덕션 컨테이너 `.env` (docker-compose):
```bash
VIEWER_BASE_URL=https://your-domain.com   # 포트 없음. QR이 이 도메인을 가리킴
AWS_REGION=ap-northeast-2
# SSE_PORT / WS_PORT 는 기본값(8766/8765) 사용, docker-compose 가 매핑함
```
- `VIEWER_BASE_URL` 이 IP거나 비어 있으면 QR이 잘못된 주소를 가리켜 뷰어가
  흰 화면이 된다. 반드시 공개 도메인(https, 포트 없음)으로 설정.

배포는 반드시 `docker-compose up -d --build` 사용 (8501/8765/8766 매핑 포함).
구 `deploy-ec2.sh` 는 8501만 열어서 뷰어/WS가 동작하지 않는다.

---

## 6. 헬스체크 검증 & 알려진 함정

배포 후 도메인에서 각 경로가 올바른 서버로 가는지 확인:
```bash
curl -s -o /dev/null -w '%{http_code}\n' https://your-domain.com/health          # → 8501(Streamlit)로 감(무관)
curl -sI https://your-domain.com/view/<room_id>    | grep -i content-type          # text/html (뷰어)
curl -sI https://your-domain.com/stream/<room_id>  | grep -i content-type          # text/event-stream (SSE)
curl -sI https://your-domain.com/ws                | head -1                        # HTTP/2 426 (WS 정상)
```
타겟그룹 헬스체크(`/health` on 8766)는 ALB가 인스턴스에 직접 보낸다 — 도메인
경로의 `/health` 가 Streamlit으로 가는 것과는 무관하다.

### ⚠️ lazy-start (재시작 직후 unhealthy)
SSE(8766)/WS(8765) 서버는 **오퍼레이터가 Streamlit 앱을 처음 열 때** app.py가
기동한다. 따라서 컨테이너 재시작 후 오퍼레이터 접속 전까지 두 포트가 죽어 있어
헬스체크가 unhealthy다. 완화책:
- 임시: 오퍼레이터가 `https://your-domain.com/` 를 한 번 로드 → 스레드 기동 →
  수십 초 내 healthy. (헬스체크 간격을 10~15초, healthy threshold 2로 낮추면
  복귀가 빨라진다.)
- 근본: SSE/WS를 컨테이너 부팅 시 기동하도록 전환 (별도 이슈).
