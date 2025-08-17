좋아요. **1주일 내 완성**을 목표로, \*“먼저 돌아가게 만든다”\*에 최적화한 **Streamlit + OpenAI Realtime(WebRTC) + 에페메럴 토큰** 기반의 **실행 가능한 사양 + 작업 리스트**를 드립니다.
(디폴트 언어는 Python, 패키지 관리는 **uv**, 결과물은 **Docker 이미지**로 패키징합니다.)

---

## 0) 최종 아키텍처(가벼움/빠른 결과 우선)

* **Streamlit 서버(파이썬)**: 장기 `OPENAI_API_KEY` 보관 → `POST /v1/realtime/sessions` 호출로 **에페메럴 토큰** 발급 → 토큰을 **동일 Streamlit 페이지에 임베드된 JS 컴포넌트**로 전달
* **브라우저(JS 컴포넌트)**:

  * 권한 요청 → **오디오 인풋(USB/기본 마이크) 선택**
  * **WebRTC**로 OpenAI Realtime에 **직접 연결**(에페메럴 토큰 사용)
  * **영어 음성 → 한국어 자막**(모델 인스트럭션으로 즉시 번역) 스트리밍 수신
  * UI는 **시작/정지** 버튼과 **크레딧형 자막 뷰어**(리사이즈+자동 스크롤)
  * **정지** 시: 세션 종료 + **자막 리스트 초기화(리셋)**
* 선택 모델: **`gpt-4o-mini-realtime-preview`**(비용/지연 우수) → 필요시 \*\*`gpt-4o-realtime-preview`\*\*로 승급
* 에페메럴 토큰 패턴과 WebRTC + SDP 교환 흐름은 OpenAI 공식 가이드 권장 방식입니다. ([OpenAI Platform][1])
* 브라우저에서 장치 레이블은 **마이크 권한 허용 후**에만 안정적으로 노출됩니다. ([MDN Web Docs][2])
* 크레딧형 뷰어는 `overflow:auto`와 `resize`를 사용(브라우저 지원 주의), 스크롤 앵커링 제어로 점프 최소화. ([MDN Web Docs][3])

---

## 1) 기술 스택 (대중적/가벼움 우선)

* **Python 3.11+**, **Streamlit**(앱 서버)
* **requests**(OpenAI 세션 생성 호출)
* **jinja2** (간단 템플릿; 선택) — 없애고 f-string으로 인라인도 가능
* **프런트(JS)**: 순수 JS(ES6) + 최소 CSS (번들러 없이 `st.components.html`에 인라인)
* **uv**(패키지/가상환경) → 로컬 개발
* **Docker**(배포 산출물): uv로 관리한 `pyproject.toml`에서 **requirements.txt export** → **pip install**(컨테이너는 단순화를 위해 pip 사용)
* (테스트/품질: 이후 우선순위 낮음 — 일단 워킹 우선)

---

## 2) 폴더 구조 (MVP)

```
realtime-en2ko-captions/
├─ app.py                         # Streamlit 메인 앱
├─ components/
│  └─ webrtc.html                # 임베디드 JS/HTML/CSS (장치 선택 + WebRTC + 자막 뷰어)
├─ pyproject.toml                # uv/PEP621
├─ .env.example                  # OPENAI_API_KEY 템플릿
├─ Dockerfile
└─ README.md
```

---

## 3) 주요 화면/기능 (AC = 수용 기준 포함)

### 3.1 상단 컨트롤 바

* **오디오 입력 선택 드롭다운**

  * 권한 요청 후 `enumerateDevices()`로 마이크 목록 표시(레이블 보임) ([MDN Web Docs][2])
  * 선택한 `deviceId`로 캡처 스트림 재생성
  * **AC**: USB 오디오 인터페이스/기본 마이크 등 실제 선택·전환 가능

* **시작 / 정지 버튼**

  * 시작: Streamlit 서버에서 **에페메럴 토큰** 생성 → JS로 전달 → **WebRTC 연결** → 2초 내 첫 자막 표시(목표)
  * 정지: RTCPeerConnection 종료 + 자막 리스트 **즉시 초기화**
  * **AC**: 시작 후 실시간으로 자막이 쌓임, 정지 즉시 캡션 리스트가 빈 상태가 됨

* **상태 배지(우측 상단)**

  * `연결됨 / 연결 중 / 끊김`, 간단한 지연(ms) 표기(선택)

### 3.2 크레딧형 자막 뷰어

* `overflow:auto;` + `resize: both;`(마우스로 크기 조절) — 브라우저 호환성 주의 ([MDN Web Docs][3])
* **자동 스크롤**: 사용자가 뷰어 **맨 아래**에 있을 때만 새 라인 추가 시 자동 이동, 위로 스크롤 시 자동 스크롤 일시 중단
* **스크롤 앵커링**: 필요 시 `overflow-anchor`로 점프 최소화 ([MDN Web Docs][4])
* **부분(unstable) → 확정(stable)** 전환: 델타는 연회색/이탤릭, 확정 시 정규 라인으로 append
* **AC**: 드래그로 뷰어 크기 조절 됨, 맨 아래일 때만 부드럽게 따라감, 확정 라인이 계속 쌓임

---

## 4) 환경 변수

* `OPENAI_API_KEY` : 서버(스트림릿) 환경변수. 브라우저로 **절대 전달 금지**
* `REALTIME_MODEL` : 기본 `gpt-4o-mini-realtime-preview`
* (선택) `OPENAI_BASE_URL` : 기본값 사용

---

## 5) API 연동 (서버 ↔ OpenAI)

**세션(에페메럴) 생성**:

* `POST https://api.openai.com/v1/realtime/sessions`
* Headers: `Authorization: Bearer <OPENAI_API_KEY>`, `Content-Type: application/json`, `OpenAI-Beta: realtime=v1`
* Body: `{ "model": "gpt-4o-mini-realtime-preview" }`
* Resp: `{ client_secret: { value:"ek_...", expires_at:"..." }, ... }`
  → 이 값을 컴포넌트에 전달해 브라우저에서 WebRTC **SDP 교환** 수행합니다. ([OpenAI Platform][5])

---

## 6) 작업 지시(1주 플랜) — **가장 빠른 결과 우선**

### Day 1 — 프로젝트 세팅 & 기본 UI ✅

* [x] **uv 초기화**

  * `uv init --python 3.11`
  * `uv add streamlit requests python-dotenv`
* [x] **Streamlit 기본 페이지** (`app.py`)

  * 타이틀/설명 + 버튼 2개(시작/정지) + 오디오 장치 선택 selectbox UI
* [x] **컴포넌트 프레임** (`components/webrtc.html`)

  * 최소 HTML/JS 삽입, Streamlit에서 불러 렌더
* **AC**: ✅ 앱 실행 시 UI 노출, 버튼 클릭/장치 선택 UI가 반응

### Day 2 — 에페메럴 토큰 발급 + 토큰 전달 ✅

* [x] `app.py`에 **세션 생성 함수** 구현: `create_ephemeral_session(model) -> dict`

  * `requests.post("/v1/realtime/sessions")` 호출, 응답 JSON 반환
* [x] **시작 버튼** 누르면 서버에서 토큰 발급 → `webrtc.html`에 **프로퍼티로 전달**

  * `st.components.v1.html(html_string, ...)`에 JSON 직렬화하여 내장
* **AC**: ✅ 시작 클릭 시 토큰 발급 성공/실패 메시지 표시(로그/토스트)

### Day 3 — 장치 권한/선택 & WebRTC 연결 ✅

* [x] 컴포넌트 JS:

  * `navigator.mediaDevices.getUserMedia({audio:true})` 권한 요청
  * `enumerateDevices()`로 **audioinput** 나열(레이블 확인) → Streamlit selectbox와 동기화
  * 선택된 `deviceId`로 `getUserMedia({ audio: { deviceId, echoCancellation:false, noiseSuppression:false, autoGainControl:false }})` 재호출
  * **AC**: ✅ USB/기본 마이크를 실제로 전환 가능, 레이블 보임 ([MDN Web Docs][2])
* [x] WebRTC **SDP 교환**

  * `RTCPeerConnection` 생성 → `createDataChannel('oai-events')`
  * 오디오 트랙 attach → `createOffer()`/`setLocalDescription()`
  * `fetch("https://api.openai.com/v1/realtime?model=...")` (Headers: `Authorization: Bearer <EPHEMERAL>`, `Content-Type: application/sdp`, `OpenAI-Beta: realtime=v1`) → `answer.sdp` 수신 → `setRemoteDescription()`
  * **AC**: ✅ 연결 성공 로그, 데이터채널 open
* [x] **추가 개선사항**:
  * 영어 강제 인식 및 번역 전용 모드 설정
  * 실시간 자막 표시 개선 (unstable → stable 전환)
  * 색상 구분 및 띄어쓰기 처리 최적화

### Day 4 — 자막 스트림 처리(unstable→stable) + 크레딧 뷰어 ✅

* [x] 데이터채널 `message` 이벤트에서 **이벤트 라우팅**

  * `type`이 `response.text.delta` 또는 전사 계열(예: `conversation.item.input_audio_transcription.delta`) 등 **델타 텍스트**를 임시 라인으로 반영
  * `...completed`/`...done`에서 **확정 라인**으로 append
  * (모델 시스템 인스트럭션: ✅ "영어→한국어 자막(줄당 최대 20자), 고유명사 원어 유지, 구어체 번역")
* [x] **크레딧형 뷰어**

  * `overflow:auto; resize:both;` 컨테이너 + **자동 스크롤(맨 아래일 때만)** 구현
  * ✅ `overflow-anchor`로 앵커링 제어, `scroll-behavior: smooth` 적용
* [x] **추가 개선사항**:
  * 한국어 폰트 최적화 ('맑은 고딕' 우선)
  * unstable/stable 시각적 구분 강화 (애니메이션, 그라데이션)
  * 부드러운 스크롤 애니메이션 (`requestAnimationFrame` 활용)
* **AC**: ✅ 사람이 말하면 **한국어 문장**이 아래로 계속 쌓이고, 사용자가 위로 스크롤하면 자동 스크롤이 멈춘다

### Day 5 — 정지/리셋 & 에러 핸들링/지연 배지

* [ ] **정지 버튼**: RTCPeerConnection close + 오디오 트랙 stop + **자막 리스트 초기화**
* [ ] 네트워크/권한/401/429 오류 토스트 처리
* [ ] 지연 배지(간단): 첫 델타 수신까지 시간 측정(p50만 표시)
* **AC**: 정지 즉시 화면이 비워지고, 다시 시작하면 새롭게 쌓인다

### Day 6 — Docker & 문서화

* [ ] **requirements export**: `uv export -o requirements.txt`
* [ ] **Dockerfile** 작성(슬림 이미지 + pip install)
* [ ] README: 설치/실행/도커 빌드/런 가이드
* **AC**: `docker build` / `docker run -e OPENAI_API_KEY=... -p 8501:8501`로 실행 가능

### Day 7 — 리허설 & 버그픽스

* [ ] 실제 \*\*A/V 믹서 라인아웃(USB)\*\*로 테스트(권장 옵션: echo/noise/AGC=false)
* [ ] 30분 연속 시연(토큰 만료 시 수동 재시작 UX 확인)
* [ ] 자잘한 UI/문구 개선

---

## 7) 수용 기준(DoD)

* [ ] 사용자는 **오디오 입력 장치**를 선택/전환할 수 있다(권한 허용 후 레이블 표시).
* [ ] **시작** 클릭 후 **2초 내** 첫 한국어 자막이 표시된다(네트워크 정상 가정).
* [ ] **정지** 클릭 시 연결이 종료되고 **긴 리스트가 즉시 초기화**된다.
* [ ] 크레딧형 뷰어는 **리사이즈**가 가능하고, **맨 아래일 때만** 자동 스크롤이 동작한다.
* [ ] Docker 컨테이너에서 **OPENAI\_API\_KEY 외부 주입**만으로 실행된다.

---

## 8) 핵심 코드 스켈레톤

### `app.py` (요지)

```python
import os, json, requests, streamlit as st

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-4o-mini-realtime-preview")

st.set_page_config(page_title="Live EN→KO Captions", layout="wide")
st.title("Live EN→KO Captions (Streamlit + Realtime)")

# --- Controls (left)
with st.sidebar:
    st.header("Controls")
    start = st.button("시작", type="primary")
    stop = st.button("정지")
    device_id = st.text_input("선택된 장치(deviceId)", value="", help="브라우저 컴포넌트에서 설정됨")

def create_ephemeral_session(model: str) -> dict:
    assert OPENAI_API_KEY, "OPENAI_API_KEY not set"
    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "realtime=v1",
    }
    body = {
        "model": model,
        # "voice": "none", # 음성 출력 사용 안 함
        # 세션 레벨 인스트럭션(optional): 클라에서 session.update 로도 가능
    }
    r = requests.post(url, headers=headers, json=body, timeout=10)
    r.raise_for_status()
    return r.json()

# --- ephemeral token on start
ephemeral = None
if start:
    try:
        ephemeral = create_ephemeral_session(REALTIME_MODEL)
        st.session_state["ephemeral"] = ephemeral
        st.session_state["action"] = "start"
        st.success("에페메럴 토큰 발급 완료")
    except Exception as e:
        st.error(f"토큰 발급 실패: {e}")
elif stop:
    st.session_state["action"] = "stop"
else:
    st.session_state.setdefault("action", "idle")

# --- Load component
with open("components/webrtc.html", "r", encoding="utf-8") as f:
    html = f.read()

payload = {
    "action": st.session_state["action"],
    "ephemeral": st.session_state.get("ephemeral"),
    "model": REALTIME_MODEL,
}
html = html.replace("{{BOOTSTRAP_JSON}}", json.dumps(payload))

st.components.v1.html(html, height=600, scrolling=True)
```

### `components/webrtc.html` (요지)

```html
<!doctype html><html><head>
<meta charset="utf-8" />
<style>
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto; }
  .bar { padding:8px 12px; display:flex; gap:8px; align-items:center; border-bottom:1px solid #ddd; }
  #viewer { height: calc(100vh - 46px); padding: 12px; overflow: auto; resize: both; border-top: 1px solid #eee; }
  .unstable { color:#888; font-style:italic; }
  .stable { color:#111; margin-bottom: 8px; }
  .badge { font-size:12px; color:#666;}
  /* 필요 시 overflow-anchor 제어 */
</style>
</head><body>
<div class="bar">
  <button id="btnPerm">권한요청</button>
  <select id="selMic"></select>
  <span id="status" class="badge">idle</span>
</div>
<div id="viewer"></div>

<script>
const BOOT = {{BOOTSTRAP_JSON}};
let pc, dc, localStream, currentDeviceId=null;

const viewer = document.getElementById('viewer');
const selMic = document.getElementById('selMic');
const statusEl = document.getElementById('status');

function logStatus(s){ statusEl.textContent = s; }

async function ensurePermission() {
  await navigator.mediaDevices.getUserMedia({ audio: true });
}
async function listMics() {
  const devs = await navigator.mediaDevices.enumerateDevices(); // 권한 후 레이블 표시
  selMic.innerHTML = "";
  devs.filter(d => d.kind === "audioinput").forEach(d => {
    const opt = document.createElement('option');
    opt.value = d.deviceId; opt.textContent = d.label || `Mic ${selMic.length+1}`;
    selMic.appendChild(opt);
  });
  if (currentDeviceId) selMic.value = currentDeviceId;
}
async function getStream(deviceId) {
  if (localStream) localStream.getTracks().forEach(t => t.stop());
  currentDeviceId = deviceId || selMic.value || undefined;
  const constraints = { audio: {
    deviceId: currentDeviceId ? { exact: currentDeviceId } : undefined,
    echoCancellation: false, noiseSuppression: false, autoGainControl: false
  }};
  localStream = await navigator.mediaDevices.getUserMedia(constraints);
  return localStream;
}

function appendLine(text, cls="stable"){
  const atBottom = Math.abs(viewer.scrollHeight - viewer.scrollTop - viewer.clientHeight) < 4;
  const div = document.createElement('div');
  div.className = cls;
  div.textContent = text;
  viewer.appendChild(div);
  if (atBottom) viewer.scrollTop = viewer.scrollHeight;
}

function clearViewer(){ viewer.innerHTML = ""; }

async function connectRealtime(ephemeral, model) {
  logStatus('connecting');
  pc = new RTCPeerConnection();
  dc = pc.createDataChannel("oai-events");
  dc.onopen = () => {
    logStatus('connected');
    // 한국어 자막 스타일 인스트럭션
    dc.send(JSON.stringify({
      type: "session.update",
      session: {
        instructions: "영어 발화를 한국어 자막으로 자연스럽게 번역. 2줄/줄당 16~23자, 고유명사 원어 유지.",
      }
    }));
  };
  dc.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      // 다양한 이벤트 타입을 관대하게 처리(델타/완료)
      if (msg.type?.includes("delta")) {
        // response.text.delta or transcription.delta
        const t = msg.delta || msg.text || msg?.response?.output_text || "";
        if (t) appendLine(t, "unstable");
      }
      if (msg.type?.includes("completed") || msg.type?.includes("done")) {
        // 확정 라인으로 교체하여 append
        const t = msg.text || msg?.response?.output_text || "";
        if (t) appendLine(t, "stable");
      }
    } catch(_) {}
  };

  // 오디오 트랙
  const stream = await getStream(currentDeviceId);
  stream.getTracks().forEach(track => pc.addTrack(track, stream));

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  const sdpResponse = await fetch(
    `https://api.openai.com/v1/realtime?model=${encodeURIComponent(model)}`,
    {
      method: "POST",
      body: offer.sdp,
      headers: {
        "Authorization": `Bearer ${ephemeral?.client_secret?.value}`,
        "Content-Type": "application/sdp",
        "OpenAI-Beta": "realtime=v1",
      }
    }
  );
  const answer = { type: "answer", sdp: await sdpResponse.text() };
  await pc.setRemoteDescription(answer);
}

function closeConn(){
  if (dc) { try { dc.close(); } catch(_){} dc = null; }
  if (pc) { try { pc.close(); } catch(_){} pc = null; }
  if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
  logStatus('idle');
}

document.getElementById('btnPerm').onclick = async () => {
  await ensurePermission(); await listMics();
};

selMic.onchange = async (e) => {
  currentDeviceId = e.target.value;
  if (pc) { // 연결 중이면 트랙 교체(간단히 재시작 권장)
    // 간단 MVP: 정지 후 시작 유도
  }
};

// Bootstrap Action
(async () => {
  if (BOOT.action === "start" && BOOT.ephemeral) {
    await ensurePermission(); await listMics();
    await connectRealtime(BOOT.ephemeral, BOOT.model);
  } else if (BOOT.action === "stop") {
    closeConn();
    clearViewer();
  }
})();
</script>
</body></html>
```

> **메모**: 이벤트 타입/필드 이름은 Realtime 릴리즈에 따라 약간 다를 수 있습니다. `type`을 확인해 `delta`/`completed`/`done`을 유연하게 처리하세요. 공식 가이드는 WebRTC + 에페메럴 패턴을 설명합니다. ([OpenAI Platform][1])

---

## 9) Dockerfile (슬림/간단)

> 컨테이너는 **pip**로 설치(단순). 로컬에선 **uv**로 개발합니다.

```dockerfile
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
```

**빌드/실행**

```bash
uv export -o requirements.txt
docker build -t realtime-caption:latest .
docker run --rm -p 8501:8501 -e OPENAI_API_KEY=sk-... realtime-caption:latest
```

---

## 10) 개발 체크리스트

* [ ] **권한 흐름**: 권한 전에는 장치 레이블 비어있음 → `getUserMedia()` 선 호출 후 `enumerateDevices()` 재호출로 레이블 확보 ([MDN Web Docs][2])
* [ ] **라인 입력**: 믹서 라인아웃은 브라우저 DSP를 **OFF**로(AGC/노이즈/에코)
* [ ] **세션/토큰 만료**: 에페메럴은 **단기 만료** — MVP에서는 수동 재시작, 이후 자동 재발급 고려 ([OpenAI Platform][1])
* [ ] **resize 지원**: 일부 브라우저의 `resize` 지원 제한 있음 — 필요 시 추후 커스텀 리사이저로 대체 ([MDN Web Docs][3])
* [ ] **스크롤 점프**: 필요하면 `overflow-anchor`로 제어(브라우저 호환 주의) ([MDN Web Docs][4])

---

## 11) 리스크 & 즉시 대응(워크퍼스트)

| 리스크            | 징후                    | 즉시 대응                                                                     |
| -------------- | --------------------- | ------------------------------------------------------------------------- |
| SDP 교환 실패/CORS | 연결이 `connecting`에서 멈춤 | 에페메럴 토큰 유효성/만료 확인, 헤더(`OpenAI-Beta`, `Content-Type: application/sdp`) 재점검 |
| 장치 레이블 안 보임    | 드롭다운에 빈 이름            | **권한 먼저** 획득 후 재나열(필수) ([MDN Web Docs][2])                                |
| 자막 점프/스크롤 튐    | 새 라인 추가 시 화면 튐        | “맨 아래일 때만” 자동 스크롤, `overflow-anchor` 고려 ([MDN Web Docs][6])               |
| 음질 왜곡/레벨 문제    | 과증폭, 게인 요동            | 믹서에서 레벨 조정, 브라우저 DSP 끄기                                                   |

---

## 12) README에 반드시 포함할 것

* 로컬:

  ```bash
  uv sync
  uv run streamlit run app.py
  ```

  환경 변수: `OPENAI_API_KEY=...`, (선택)`REALTIME_MODEL=...`

* Docker: 위 명령 예시

* **브라우저 권한 안내**: 첫 실행 시 **마이크 권한 허용** 필요

---

## 13) 우선순위 정리

1. **워킹 데모**(오디오 선택+실시간 한글 자막)
2. 정지 시 **즉시 리셋**
3. 기본적인 에러 토스트/상태 표시
4. (후순위) 지연 뱃지/프롬프트 튜닝/가상 스크롤/다국어/로그/배포 자동화

---


[1]: https://platform.openai.com/docs/guides/realtime?utm_source=chatgpt.com "Realtime API Beta"
[2]: https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia?utm_source=chatgpt.com "MediaDevices: getUserMedia() method - Web APIs | MDN"
[3]: https://developer.mozilla.org/en-US/docs/Web/CSS/resize?utm_source=chatgpt.com "resize - CSS - MDN Web Docs - Mozilla"
[4]: https://developer.mozilla.org/en-US/docs/Web/CSS/overflow-anchor?utm_source=chatgpt.com "overflow-anchor - CSS - MDN Web Docs - Mozilla"
[5]: https://platform.openai.com/docs/api-reference/realtime-sessions?utm_source=chatgpt.com "ephemeral API key"
[6]: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_scroll_anchoring/Scroll_anchoring?utm_source=chatgpt.com "Overview of scroll anchoring - CSS - MDN Web Docs"
