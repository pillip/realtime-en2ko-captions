# AWS ALB + ACM SSL 배포 가이드

실시간 자막 시스템은 **3개의 포트**를 사용하며, ALB가 경로에 따라 각각 다른
타겟그룹으로 라우팅해야 한다. 하나라도 빠지면 오퍼레이터 또는 뷰어가 동작하지
않는다.

| 포트 | 서버 | 경로 | 용도 |
|------|------|------|------|
| 8501 | Streamlit | 기본(그 외 전부) | 오퍼레이터/관리자 UI |
| 8766 | aiohttp SSE | `/view/*`, `/stream/*`, `/stage/*`, `/branding/*` | 뷰어 페이지 + 자막 스트림 + 무대 합성 페이지 + 룸 로고 |
| 8765 | WebSocket | `/ws` | 오퍼레이터 번역 파이프라인 |

> 코드는 이미 이 경로 규약에 맞춰져 있다: 뷰어는 same-origin `/stream/{room}`
> 으로 SSE를 열고(components/viewer.html), 오퍼레이터는 배포 환경에서
> `wss://<도메인>/ws` 로 접속한다(components/webrtc.html).

> ⚠️ **8766 경로 목록은 `sse_broadcast.build_sse_app` 의 라우트 등록과 1:1 이어야 한다.**
> 이 표는 손으로 복사한 목록이라 라우트가 추가되면 조용히 낡는다 (RL-001). 실제로
> 그렇게 됐다 — 이 문서는 2026-08-09(#105)에 `/view`·`/stream` 두 경로 기준으로
> 작성됐고, 2026-08-23 에 `/branding/{room_id}/{filename}`(#120) 과
> `/stage/{room_id}`(#121) 이 추가됐지만 여기도 ALB 규칙도 갱신되지 않아
> **무대 합성 뷰 전체가 프로덕션에서 도달 불가**였다. `tests/test_alb_routing_parity.py`
> 가 이제 이 표와 라우트 등록을 묶어 회귀를 막는다.
>
> **이 누락은 에러로 드러나지 않는다**: ALB 기본 규칙이 해당 요청을 8501 로 보내고
> Streamlit 이 **200 + 자기 앱 셸 HTML** 을 돌려준다. 404 도 500 도 없이 그냥
> 빈 Streamlit 화면이 뜨므로, 아래 §6 의 content-type 검증을 반드시 수행한다.

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
  1. IF Path is `/view/*` OR `/stream/*` OR `/stage/*` OR `/branding/*`
     → Forward to **tg-viewer** (8766)
  2. IF Path is `/ws` → Forward to **tg-ws** (8765)
  3. **Default** → Forward to **tg-streamlit** (8501)

> ALB 규칙 하나에 경로 조건 여러 개를 넣을 수 있다(Path is 에 패턴 4개 입력).
> 규칙을 4개로 쪼개도 동작은 같지만 리스너 규칙 수 할당량만 더 쓴다.
>
> `/stage/*` 와 `/branding/*` 이 빠지면: 무대 화면이 Streamlit 셸을 받아 **빈 화면**,
> 룸 로고는 이미지가 아닌 HTML 을 받아 **깨진 이미지**가 된다. 둘 다 HTTP 200 이다.

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

### ⚠️ `deploy-ec2.sh` 와 `deploy-alb.sh` 를 쓰지 말 것 (둘 다 낡았다)

이름 때문에 `deploy-alb.sh` 가 이 문서의 정답 스크립트처럼 보이지만 **아니다.**
두 스크립트는 스프린트 이전 단일 포트 시절 산물이고, 지금 실행하면 조용히 반쯤
죽은 컨테이너가 뜬다:

| 문제 | 결과 |
|---|---|
| `-p 8501:8501` 만 매핑 | 뷰어 SSE(8766)·오퍼레이터 WS(8765) 도달 불가 |
| `-e OPENAI_API_KEY=...` 를 넘김 | 앱은 **`OPENAI_KEY`** 를 읽는다(`services.py:26`) → 음성 인식 사망 |
| AWS 자격 증명을 안 넘김 | Bedrock 번역 사망 |
| `VIEWER_BASE_URL` 을 안 넘김 | QR 이 `http://localhost:8766` 을 가리켜 뷰어 흰 화면 |

`docker-compose.yml` 은 위 네 가지를 모두 올바르게 처리한다. 두 스크립트는
참고용 잔존물로만 보고, 배포 경로는 compose 하나로 유지한다.

---

## 6. 헬스체크 검증 & 알려진 함정

배포 후 도메인에서 각 경로가 올바른 서버로 가는지 확인:
```bash
curl -s -o /dev/null -w '%{http_code}\n' https://your-domain.com/health          # → 8501(Streamlit)로 감(무관)
curl -sI https://your-domain.com/view/<room_id>    | grep -i content-type          # text/html (뷰어)
curl -sI https://your-domain.com/stream/<room_id>  | grep -i content-type          # text/event-stream (SSE)
curl -sI https://your-domain.com/ws                | head -1                        # HTTP/2 426 (WS 정상)
```

**라우팅 누락은 상태 코드로 드러나지 않으므로, 존재하지 않는 룸으로 "어느 서버가
응답했는지"를 본다** — 8766 이 받으면 서버 고유의 404, 8501 로 흘러가면 Streamlit
셸의 200 이다:
```bash
for p in /view/nonexistent /stream/nonexistent /stage/nonexistent /branding/x/y.png; do
  curl -s -o /dev/null -w "$p -> %{http_code} %{size_download}B\n" "https://your-domain.com$p"
done
# 올바른 구성: /view·/stage → 404 (약 900B 안내 HTML), /stream → 404 (9B "not found"),
#              /branding/x/y.png → 404
# 라우팅 누락: 전부 200 + 동일 크기(Streamlit 셸, 약 1.5KB) — 이 패턴이 곧 증상이다
```
타겟그룹 헬스체크(`/health` on 8766)는 ALB가 인스턴스에 직접 보낸다 — 도메인
경로의 `/health` 가 Streamlit으로 가는 것과는 무관하다.

### ~~lazy-start (재시작 직후 unhealthy)~~ — 해결됨 (#158)

SSE(8766)/WS(8765)는 이제 **컨테이너 부팅 시** 뜬다. 브라우저 접속이 필요 없다.

컨테이너 엔트리포인트가 `streamlit run app.py` 가 아니라 **`python serve.py`**
다. Streamlit 은 스크립트를 브라우저 세션 단위로 실행하므로 `streamlit run` 으로
띄우면 아무도 `:8501` 을 열기 전까지 `app.py` 가 실행되지 않고 두 서버도 뜨지
않았다 — 재시작마다 관객이 흰 화면을 봤다. `serve.py` 는 두 서버를 먼저 띄우고
**같은 프로세스에서** Streamlit 을 돌린다(모듈 전역 상태를 공유해야 하므로
프로세스를 쪼갤 수 없다).

실측: 브라우저 접속 0회 상태에서 8501/8765/8766 전부 LISTEN, `/health` 200,
`/ws` 426.

따라서 **헬스체크 간격을 낮출 필요가 없고**, 재시작 직후 오퍼레이터가 먼저
접속해야 하는 제약도 사라졌다.
