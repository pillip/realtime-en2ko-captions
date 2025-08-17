아래 PRD는 **Streamlit 기반**으로, \*\*옵션 A(브라우저 ↔ OpenAI Realtime 직접 연결)\*\*를 따르되 **에페메럴 토큰을 Streamlit 서버에서 발급**해 보안·호환성을 확보하는 구조입니다. 요구하신 **3가지 핵심 기능**을 중심으로, 실제 구현·운영을 전제로 한 상세 항목을 포함했습니다.

> 핵심 기능
>
> 1. **오디오 인풋 선택**(USB 오디오 인터페이스/기본 마이크 등)
> 2. **시작/정지** 버튼으로 **통역 파이프라인(영어 → 한국어 자막)** on/off
> 3. **정지 시** “영화 크레딧처럼 쌓인 한글 문장” **초기화(리셋)**

---

## 0. 배경 & 목표

* **목표**: 컨퍼런스 현장에서 **영어 발화 → 한국어 자막**을 **저지연**으로 띄우고, 자막은 **크레딧형(아래로 계속 쌓이는)** UI로 표시한다.
* **아키텍처**: 브라우저가 **WebRTC**로 OpenAI **Realtime API**에 직접 접속. 단, 인증은 **Streamlit 서버가 에페메럴 토큰**을 발급해 해결. 이 패턴은 OpenAI 가이드의 권장 흐름과 일치한다. ([OpenAI Platform][1])
* **모델 제안(초기)**: `gpt-4o-mini-realtime-preview`로 시작 → 필요 시 `gpt-4o-realtime-preview`로 승급(정확도 우선). 모델/연결 방식은 WebRTC를 활용(WS는 브라우저에서 인증·헤더 제약이 있어 WebRTC 권장). ([Microsoft Learn][2])

---

## 1. 사용자 & 사용 시나리오

### 1.1 사용자

* **오퍼레이터(스태프)**: 장치 선택, 시작/정지, 화면 공유(뷰어 창) 관리
* **청중**: 대형 스크린에 **한국어 자막**을 읽음

### 1.2 시나리오(단일 세션)

1. 오퍼레이터가 Streamlit 앱 접속 → **오디오 입력 장치 선택**

   * 첫 접근에서 브라우저가 **마이크 권한**을 요청(허용해야 실제 장치 **레이블** 확인 가능). ([MDN Web Docs][3])
2. **시작** 클릭 → 서버에서 **에페메럴 토큰 발급** → 브라우저가 WebRTC로 **OpenAI Realtime**에 연결 → 오디오 송신 및 자막 이벤트 수신. ([OpenAI Platform][4])
3. 자막은 **크레딧형**으로 아래로 쌓이며, 뷰어 창에서 자동 스크롤(맨 아래일 때만)과 사이즈 리사이즈 지원. ([MDN Web Docs][5])
4. **정지** 클릭 → WebRTC 세션 종료, **자막 리스트 초기화**.

---

## 2. 범위(스코프)

* **포함**: 오디오 입력 선택, 시작/정지, 에페메럴 토큰 발급, WebRTC 연결, 한국어 자막 스트리밍 수신/표시(크레딧형), 정지 시 자막 리셋.
* **제외(MVP)**: 녹화/저장(SRT), 용어집 관리 UI, 멀티 룸, 접근 제어/로그인.

---

## 3. 기능 요구사항

### 3.1 오디오 입력 선택

* **장치 나열**: `navigator.mediaDevices.enumerateDevices()`로 **오디오 입력(마이크)** 목록 제공.

  * **주의**: 개인정보 보호로 **권한 허용 전에는 레이블이 비어있음** → 먼저 `getUserMedia({audio:true})`로 권한을 받은 뒤 다시 나열해야 레이블이 채워짐. ([MDN Web Docs][3])
* **장치 고정**: 사용자가 선택한 `deviceId`로 `getUserMedia({audio:{deviceId}})` 재호출해 스트림 획득. 장치 선택은 `st.session_state` 및 브라우저 `localStorage`에 보존(선택 사항). ([WebRTC][6])
* **믹서 라인 입력 프로파일**(권장): `echoCancellation:false`, `noiseSuppression:false`, `autoGainControl:false` (라인 신호 왜곡 방지). 실제 적용 여부는 `MediaTrackSettings`로 확인. ([MDN Web Docs][7])

### 3.2 시작/정지(통역 파이프라인 on/off)

* **시작**:

  1. Streamlit 서버가 `POST /v1/realtime/sessions`로 **에페메럴 토큰** 생성(장기 키는 서버 보관).
  2. 브라우저는 **WebRTC SDP 교환**으로 OpenAI Realtime에 연결하고 **오디오 트랙 publish**.
  3. 세션 **instructions**에 “영어 발화를 **한국어 자막 스타일로** 반환(2줄, 줄당 \~23자, 고유명사 원어 유지)”을 설정.
  4. 모델의 **텍스트/전사 이벤트 스트림**을 받아 **부분→확정** 전이를 처리하여 크레딧 리스트에 append. (예: `conversation.item.input_audio_transcription.completed` 등 전사 이벤트를 활용) ([OpenAI Platform][1], [Microsoft Learn][8])
* **정지**:

  * WebRTC 연결 해제(트랙 정지, RTCPeerConnection close)
  * **자막 리스트 초기화**(크레딧 창 비우기)
  * 상태 뱃지/버튼 상태 업데이트

### 3.3 크레딧형 자막 UI

* **컨테이너**: 스크롤 영역(`overflow:auto`) + **리사이즈 가능**(`resize: both | block | inline`)

  * 브라우저 호환성은 MDN 최신 표를 따름(일부 브라우저에서 제한 가능). ([MDN Web Docs][5])
* **자동 스크롤 규칙**: 사용자가 **맨 아래에 있을 때만** 신규 라인 추가 시 `scrollTop=scrollHeight`로 따라감. 사용자가 위로 스크롤하면 자동 따라가기 **일시 중단**.

  * 스크롤 앵커 튜닝: 필요 시 `overflow-anchor`로 앵커링 제어. ([MDN Web Docs][9])
* **부분(unstable) → 확정(stable)**: 실시간 **delta**를 연회색/이탤릭으로 표시, **완료 이벤트**에서 안정 라인으로 교체 후 리스트에 확정 append. (이벤트 명세/흐름은 Realtime 문서/레퍼런스 참고) ([OpenAI Platform][10], [Microsoft Learn][8])
* **가독 규칙**(내부 가이드): 2줄, 줄당 16\~23자 권장, 의미 단위 줄바꿈, 최소 노출 1초/최대 7초(정책은 화면 크기에 맞춰 조정).

---

## 4. 비기능 요구사항

* **지연(End-to-End)**: 1.5–3.0초 목표(네트워크/브라우저/모델 영향) — WebRTC는 실시간 오디오에 적합. ([Microsoft Learn][2])
* **안정성**: 연결 실패/끊김에 대한 사용자 피드백, 수동 재시작(추가 과제로 자동 재연결)
* **보안**: 장기 OpenAI API 키는 **Streamlit 서버 환경변수**에만 저장, 브라우저에는 **에페메럴 토큰**만 전달. ([OpenAI Platform][11])
* **호환성**: 최신 Chrome/Edge/Safari 우선. `resize`, `overflow-anchor` 일부 제한 주의. ([MDN Web Docs][5])
* **프라이버시**: 음성/자막 **미보관**(MVP), 안내 문구 노출.

---

## 5. 시스템/아키텍처

### 5.1 컴포넌트

* **Streamlit 서버(Python)**

  * **에페메럴 토큰 발급 엔드포인트**(내부 함수 또는 `/api/session`)
  * 환경변수 `OPENAI_API_KEY`로 OpenAI 호출
  * `st.components.v1.html`(또는 `st.html`)로 **JS/HTML 컴포넌트** 임베딩(이 iframe/DOM에서 WebRTC·장치 제어 수행) ([Streamlit Docs][12])
* **브라우저 컴포넌트(JS in Streamlit component)**

  * 장치 나열/선택(`enumerateDevices`), 권한 취득(`getUserMedia`)
  * WebRTC 연결(SDP 교환 시 서버가 발급한 **에페메럴 토큰** 사용) ([OpenAI Platform][4])
  * 자막 이벤트 수신/렌더(크레딧형), 자동 스크롤, 리사이즈 대응(`ResizeObserver`) ([MDN Web Docs][13])

### 5.2 시퀀스(시작)

1. 사용자가 **시작** 클릭
2. Streamlit 서버: `POST https://api.openai.com/v1/realtime/sessions` (헤더: `Authorization: Bearer <server_key>`, `OpenAI-Beta: realtime=v1`) → **client\_secret(에페메럴)** 수신. ([OpenAI Platform][11])
3. 브라우저: 에페메럴 토큰으로 **WebRTC SDP 교환**(`Content-Type: application/sdp`) → RTCPeerConnection 수립 후 오디오 트랙 전송, 데이터채널로 텍스트 이벤트 수신. ([OpenAI Platform][10])
4. 브라우저: delta/complete 이벤트를 받아 UI 갱신(unstable→stable). (전사 이벤트 예시는 Azure 레퍼런스에도 정리돼 있음) ([Microsoft Learn][8])

### 5.3 시퀀스(정지)

* RTCPeerConnection close → 오디오 트랙 stop → **자막 리스트 초기화** → 버튼 상태/뱃지 업데이트

---

## 6. 데이터 모델(프론트/서버)

* **프론트(메모리/세션 상태)**

  * `settings`: `{deviceId, micProfile: "line"|"default", autoScroll: true, fontScale}`
  * `connection`: `{state: "idle"|"connecting"|"connected"|"error", latencyMs?}`
  * `captions`: `[{ id, textKo, state: "unstable"|"stable", startedAt, endedAt? }]`
* **서버(streamlit)**

  * `ephemeral_session`: OpenAI 응답 JSON 그대로 전달(로그에는 민감 정보 최소화)

---

## 7. UI/UX 명세

* **상단 컨트롤 바**

  * **오디오 입력 선택 드롭다운**(갱신 버튼 포함)
  * **시작/정지** 버튼(토글)
  * 상태 뱃지: `연결됨/연결 중/끊김`, p50 지연(ms)
* **크레딧 뷰어**

  * 중앙 정렬, 고대비(검정 배경/흰 텍스트 등), 48–64px 이상(1080p 기준)
  * 컨테이너는 `overflow:auto; resize:both;`로 **마우스 리사이즈** 가능. ([MDN Web Docs][5])
  * **자동 스크롤**: “맨 아래에 있을 때만” 수행. `overflow-anchor`로 화면 흔들림 최소화. ([MDN Web Docs][9])
* **에러/권한 안내**

  * 권한 거부 시 “브라우저 주소창의 카메라/마이크 아이콘에서 권한 허용” 가이드
  * 장치 레이블이 비어있으면 “권한 허용 후 다시 시도” 안내(표준 동작). ([MDN Web Docs][14])

---

## 8. 번역/표시 로직

* **세션 인스트럭션**(예):
  “영어 발화를 **자연스러운 한국어 자막**으로 요약 없이 충실 번역. **2줄 이내**, **줄당 16\~23자** 선호. **고유명사/제품명은 원어 유지**(필요 시 괄호 병기). 문장부호/띄어쓰기 정돈. ‘부분 결과’는 간결히, ‘확정’ 시 매끄럽게 다듬어 제공.”
* **이벤트 처리**

  * 실시간 delta를 \*\*임시 라인(unstable)\*\*로 표시
  * 완료 이벤트에서 \*\*확정 라인(stable)\*\*로 승격 후 리스트에 append
  * (전사 이벤트 예: `conversation.item.input_audio_transcription.completed`) ([Microsoft Learn][8])

---

## 9. 보안 & 프라이버시

* 장기 OpenAI 키는 **서버 환경변수**만, 브라우저에는 **에페메럴 토큰**만 노출. 세션 생성은 `/v1/realtime/sessions` 참조. ([OpenAI Platform][11])
* HTTPS 필수(브라우저 **MediaDevices**는 **보안 컨텍스트** 필요). ([MDN Web Docs][15])
* 기본값: 음성/자막 **미보관**. 화면 하단 고지 문구 제공.

---

## 10. 성능/지연 최적화

* **WebRTC**를 기본으로 사용(브라우저에 최적, 오디오 실시간은 WS보다 WebRTC 권장). ([Microsoft Learn][2])
* 오디오 청크/버퍼는 WebRTC가 관리. 브라우저 오디오 DSP 옵션은 **라인 입력의 경우 OFF** 권장. ([MDN Web Docs][7])
* 크레딧 DOM이 커지면 **가상 리스트** 도입(후속 작업).

---

## 11. 수용 기준(AC)

1. **오디오 입력 선택**

   * 권한 허용 후 **장치 레이블이 보이고**, 특정 장치를 선택해 **입력 소스가 실제로 변경**된다. (선택 장치의 `deviceId` 또는 `MediaTrackSettings`로 확인) ([MDN Web Docs][16])
2. **시작/정지**

   * 시작 시 N초 내(목표 p50<2s) 첫 **한국어 자막**이 표시.
   * 정지 시 WebRTC가 종료되고, **자막 리스트가 즉시 비워진다**.
3. **크레딧형 UI**

   * 컨테이너는 **마우스로 리사이즈** 가능하고, 하단에 있을 때 **자동 스크롤**이 동작한다. (상단 스크롤 시 자동 스크롤 중지) ([MDN Web Docs][5])

---

## 12. 테스트 계획

* **권한/장치**: 권한 거부/승인, 장치 핫스왑(USB 인터페이스 연결/해제), 레이블 표시 전후 흐름 검증(MDN 권장 시나리오). ([MDN Web Docs][3])
* **브라우저별**: Chrome/Edge/Safari(최신), `resize`/`overflow-anchor` 동작 확인. ([MDN Web Docs][5])
* **연결성**: 네트워크 순간 끊김 후 **수동 재시작** 동작 확인.
* **지연 측정**: 입력 타임스탬프 vs 자막 렌더 타임(p50/p95).

---

## 13. 리스크 & 대응

* **장치 레이블 비노출**: 권한 전에는 비어있음 → 권한 요청 후 재나열 UX 제공. ([MDN Web Docs][14])
* **브라우저 제약**: 일부 CSS `resize`/`overflow-anchor` 호환성 상이 → 대체 UX(버튼으로 폰트 확대/축소, 전용 리사이저 핸들) 준비. ([MDN Web Docs][5])
* **세션/토큰 만료**: 에페메럴 토큰은 단기 수명 → 실패 시 **다시 발급 후 재연결** UI 제공. ([OpenAI Platform][11])

---

## 14. 기술 스택 & 주요 API

* **백엔드**: **Streamlit**(Python)

  * **에페메럴 토큰 발급**: 서버에서 `POST /v1/realtime/sessions` 호출 후 JSON 반환. ([OpenAI Platform][11])
  * **컴포넌트 임베딩**: `st.components.v1.html(...)`(또는 `st.html`)로 JS/HTML 삽입. ([Streamlit Docs][12])
* **프런트(JS in component)**:

  * 장치: `navigator.mediaDevices.getUserMedia`, `enumerateDevices`, `MediaTrackSettings` 확인. ([MDN Web Docs][3])
  * 연결: **WebRTC**(RTCPeerConnection, SDP 교환 → OpenAI Realtime). ([OpenAI Platform][10])
  * UI: CSS `resize`, `overflow`, `overflow-anchor`, `ResizeObserver`. ([MDN Web Docs][5])

---

## 15. 인터페이스(계약)

### 15.1 서버 → OpenAI (세션 생성)

* **HTTP**: `POST https://api.openai.com/v1/realtime/sessions`
* **Headers**:

  * `Authorization: Bearer <SERVER_API_KEY>`
  * `Content-Type: application/json`
  * `OpenAI-Beta: realtime=v1`
* **Body(예)**: `{ "model": "gpt-4o-mini-realtime-preview" }`
* **Resp(예)**: `{ "id":"...", "client_secret": { "value":"ek_...", "expires_at":"..." }, ... }`
  *(정확 스키마는 레퍼런스 참조)* ([OpenAI Platform][11])

### 15.2 브라우저 → OpenAI (SDP 교환)

* **HTTP**: `POST https://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview`
* **Headers**:

  * `Authorization: Bearer <EPHEMERAL_TOKEN>`
  * `Content-Type: application/sdp`
  * `OpenAI-Beta: realtime=v1`
* **Body**: `offer.sdp`(문자열) → 응답: `answer.sdp`
* 이후 **RTCPeerConnection** 수립 & **DataChannel**로 이벤트 수신. ([OpenAI Platform][10])

---

## 16. 구현 로드맵(커밋 단위)

**커밋 1 — Streamlit 뼈대 & 에페메럴 발급**

* `.env`에 `OPENAI_API_KEY`
* `st.button("시작")` 클릭 시 서버에서 `POST /v1/realtime/sessions` 호출해 JSON 반환(메모리 저장). ([OpenAI Platform][11])

**커밋 2 — 장치 권한 & 선택 UI**

* 컴포넌트에서 `getUserMedia({audio:true})`로 권한 요청 → `enumerateDevices()`로 마이크 목록 표시(레이블 확인). 선택 시 `deviceId` 저장. ([MDN Web Docs][3])

**커밋 3 — WebRTC 연결 & 자막 스트림**

* RTCPeerConnection 생성, **선택 장치 트랙** 추가 → SDP 오퍼 생성/전송 → `answer` 수신 → 연결.
* 세션 `instructions` 설정(한국어 자막 스타일). 이벤트(delta/complete) 처리 파이프라인 구현. ([OpenAI Platform][10], [Microsoft Learn][8])

**커밋 4 — 크레딧형 뷰어**

* 컨테이너 `overflow:auto; resize:both;` + 자동 스크롤(맨 아래일 때만) + `ResizeObserver`로 폰트/줄 수 반응형. ([MDN Web Docs][5])

**커밋 5 — 정지 & 리셋**

* `정지` 클릭 → RTCPeerConnection/트랙 종료 → **자막 리스트 초기화** → UI 상태 갱신.

**커밋 6 — 안정화**

* 에러 메시지/상태 뱃지, 권한 재요청 UX, 레이트 리밋/토큰 만료 시 재시도 안내.

---

## 17. 운영 체크리스트

* **믹서 라인아웃** USB 인터페이스 연결/레벨 확인
* 브라우저 권한 허용(처음 한 번), 장치 레이블 확인(권한 후 표시). ([MDN Web Docs][14])
* 네트워크(유선 권장), 브라우저 최신 업데이트
* 스크린 해상도/폰트 크기 리허설

---

### 부록 A. 참고 링크

* **OpenAI Realtime 가이드(개요/대화)**: WebRTC/WS, 저지연 상호작용. ([OpenAI Platform][1])
* **Realtime Transcription 가이드**: WebRTC/WS로 전사 세션 생성. ([OpenAI Platform][4])
* **Session(에페메럴) API 레퍼런스**. ([OpenAI Platform][11])
* **Azure 문서(이벤트/모델/연결 예시)**: Realtime WebRTC/WS 참고, 전사 이벤트 명세. ([Microsoft Learn][17])
* **Streamlit 컴포넌트**: `st.components.v1.html`, 커스텀 컴포넌트 개요. ([Streamlit Docs][12])
* **장치·권한/레이블 규칙**: `getUserMedia`, `enumerateDevices`, 레이블은 권한 후 표시. ([MDN Web Docs][3])
* **크레딧 UI 관련 CSS**: `resize`, `overflow`, `overflow-anchor`, `ResizeObserver`. ([MDN Web Docs][5])

---

[1]: https://platform.openai.com/docs/guides/realtime?utm_source=chatgpt.com "Realtime API Beta"
[2]: https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/realtime-audio-websockets?utm_source=chatgpt.com "How to use the GPT-4o Realtime API via WebSockets ..."
[3]: https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia?utm_source=chatgpt.com "MediaDevices: getUserMedia() method - Web APIs | MDN"
[4]: https://platform.openai.com/docs/guides/realtime-transcription?utm_source=chatgpt.com "Realtime transcription - OpenAI API"
[5]: https://developer.mozilla.org/en-US/docs/Web/CSS/resize?utm_source=chatgpt.com "resize - CSS - MDN Web Docs - Mozilla"
[6]: https://webrtc.org/getting-started/media-devices?utm_source=chatgpt.com "Getting started with media devices"
[7]: https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackSettings/echoCancellation?utm_source=chatgpt.com "MediaTrackSettings: echoCancellation property - Web APIs"
[8]: https://learn.microsoft.com/en-us/azure/ai-services/openai/realtime-audio-reference?utm_source=chatgpt.com "Audio events reference - Azure OpenAI"
[9]: https://developer.mozilla.org/en-US/docs/Web/CSS/overflow-anchor?utm_source=chatgpt.com "overflow-anchor - CSS - MDN Web Docs - Mozilla"
[10]: https://platform.openai.com/docs/guides/realtime-conversations?utm_source=chatgpt.com "Realtime conversations - OpenAI API"
[11]: https://platform.openai.com/docs/api-reference/realtime-sessions?utm_source=chatgpt.com "Session tokens"
[12]: https://docs.streamlit.io/develop/api-reference/custom-components/st.components.v1.html?utm_source=chatgpt.com "st.components.v1.html - Streamlit Docs"
[13]: https://developer.mozilla.org/en-US/docs/Web/API/ResizeObserver?utm_source=chatgpt.com "ResizeObserver - Web APIs | MDN"
[14]: https://developer.mozilla.org/en-US/docs/Web/API/MediaDeviceInfo?utm_source=chatgpt.com "MediaDeviceInfo - Web APIs | MDN"
[15]: https://developer.mozilla.org/en-US/docs/Web/API/Navigator/mediaDevices?utm_source=chatgpt.com "Navigator: mediaDevices property - Web APIs | MDN"
[16]: https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/enumerateDevices?utm_source=chatgpt.com "MediaDevices: enumerateDevices() method - Web APIs | MDN"
[17]: https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/realtime-audio-webrtc?utm_source=chatgpt.com "How to use the GPT-4o Realtime API via WebRTC (Preview) - Azure ..."
