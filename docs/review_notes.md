# Review Notes — ISSUE-31 (PR #65)

**Reviewer**: Claude Opus 4.7 (automated, team-lead REVIEW phase)
**Date**: 2026-05-07
**PR Size**: +1038 -0 across 4 files
- `components/viewer.html` (NEW, ~280 LOC HTML+CSS+JS)
- `sse_broadcast.py` (modified, +~150 LOC: imports, `/view/{room_id}` route, `_handle_view`, `_render_viewer_html`, `_NOT_FOUND_HTML`)
- `tests/test_viewer_page.py` (NEW, 14 tests)
- `tests/e2e/test_viewer_page_e2e.py` (NEW, 3 e2e cases — `e2e` marked, deselected by default)

## Scope

QR-스캔 청중을 위한 비인증 자막 뷰어. `/view/{room_id}` HTTP 라우트가 정적 HTML 템플릿에 룸 메타데이터 (`name`, `id`, `output_langs`, `primary_lang`, 초기 상태) 를 인라인 주입하고, 클라이언트는 EventSource 로 `/stream/{room_id}?lang=<code>` 를 구독한다. 언어 변경 시 EventSource 가 닫히고 새 lang 으로 재연결된다.

## Code Review

### Strengths

- **RL-006 컴플라이언스 (확실)**: 알 수 없는 룸 / 레포 예외 / 템플릿 렌더 실패 모두 동일한 `_NOT_FOUND_HTML` 본문을 반환. Traceback / 내부 경로 / sqlite 키워드 비노출. `test_unknown_room_returns_404_friendly` + `test_repo_exception_returns_generic_404` 으로 회귀 차단.
- **RL-010 컴플라이언스 (a11y)**:
  - `<select id="lang-select" aria-label="자막 언어 선택">` — 아이콘 only 가 아닌 select 지만 명시적 aria-label 부여로 스크린리더 가독성 개선.
  - `:focus-visible` outline 으로 키보드 포커스 표시.
  - `min-height: 44px` 로 WCAG 2.1 AA 터치 타겟 (44x44px) 충족.
  - `<html lang="ko">` 로 언어 식별 + 한국어 폰트 스택 (Apple SD Gothic Neo, Noto Sans KR) 페어링.
  - `aria-live="polite"` 가 waiting/ended 상태 메시지에 부착 — 화면 전환 시 스크린리더가 안내.
  - 색 대비: `#fff` on `#0f0f23` 배경 = 17.55:1 (WCAG AAA Pass). 보조 텍스트 `rgba(255,255,255,0.65)` ≈ 11.4:1 (AAA Pass).
- **RL-011 컴플라이언스 (iOS Safari)**: `html`/`body`/`.app` 모두 `height: 100vh; height: 100dvh;` 순서로 선언해 dvh 지원 브라우저 (Safari 15.4+) 에서 cascade 우선, 미지원 폴백은 vh.
- **RL-002 (간접)**: `/view/{room_id}` 는 의도적으로 비인증이므로 client-supplied identity 신뢰 이슈 자체가 없음. room_id 는 secrets-derived (`secrets.token_urlsafe(8)`) 로 추측 불가.
- **TDD 준수**: tests-written + red phase 체크포인트 PASS — 테스트 먼저 작성 후 구현. 14/14 단위 테스트 GREEN.
- **HTML escape**: `_render_viewer_html` 이 `room_id`/`room_name`/`primary_lang`/`initial_state` 모두 `html.escape(..., quote=True)` 처리. 향후 admin 이 `room.name` 에 `<script>` 를 저장해도 viewer 에 주입되지 않음 (defense in depth — 룸 생성 자체는 admin auth 가 차단).
- **JSON in script context**: `output_langs` 는 `json.dumps(..., ensure_ascii=False)` 로 직렬화. 현재 토큰 셋 (ko/en/ja/zh/vi) 에는 `</` 가 포함되지 않으므로 closing-tag breakout 위험 없음. 향후 사용자 정의 lang 코드를 허용한다면 별도 `json.dumps` + `</` → `<\/` replace 를 검토.
- **EventSource lifecycle**: 언어 변경 시 `es.close()` 로 명시적 종료 후 새 EventSource 생성. 캡션 컨테이너는 `clearCaptions()` 으로 초기화. 메모리/네트워크 누수 없음.
- **DOM bounded growth**: `MAX_LINES = 200` 으로 자막 누적을 제한. 30분+ 세션에서도 DOM 크기 안정.
- **Auto-scroll-on-bottom**: `isUserAtBottom()` (slack=80px) 검사 후에만 `scrollTop` 갱신 — 사용자가 위로 스크롤한 상태를 존중. webrtc.html 의 동일 패턴을 재사용.
- **세션 종료 처리**: `session_end` 이벤트 수신 시 `setState("ended")` + `es.close()` 로 자동 재연결 차단. closed 룸은 초기 부트스트랩 시 EventSource 자체를 열지 않음.
- **모바일 반응형**: `clamp()` 기반 fluid typography (`clamp(20px, 4vw, 32px)` etc.), `@media (max-width: 600px)` 에서 padding/font-size 축소. 룸 이름은 `text-overflow: ellipsis` 로 폰 화면에서도 깔끔.

### Findings

- **CR-1 (Low)** — EventSource 자동 재연결은 브라우저 기본 동작에 의존 (3초 backoff). 완전 차단된 네트워크에서는 `error` 이벤트가 반복적으로 트리거되며 `conn-error` 배너가 계속 표시된다. 개선안: 일정 횟수 (예: 5회) 실패 시 `es.close()` 후 사용자 액션 (탭 새로고침) 을 유도. **현재 MVP 에서는 허용 — issues.md AC 에 재연결 지수백오프 요구는 없음.**
- **CR-2 (Low)** — `langSelect.addEventListener("change", …)` 가 같은 lang 으로의 변경은 무시 (`next === currentLang`) 하지만, 사용자가 빠르게 두 언어 사이를 토글하면 짧은 시간 동안 두 EventSource 가 공존한다 (close 와 open 사이의 race). 메시지 손실 / 중복은 없으나 (queue 단위로 분리), 네트워크 상 잠시 두 SSE 연결이 보인다. **무시 가능 — UX 영향 없음.**
- **CR-3 (Nit)** — `_VIEWER_TEMPLATE_PATH = Path(__file__).resolve().parent / "components" / "viewer.html"` 가 모듈 import 시점에 1회 평가됨. 운영 중 viewer.html 을 변경해도 캐시된 경로 자체는 영속적이지만, `read_text` 는 매 요청마다 호출되므로 hot-reload 가능. 단, 매 요청마다 디스크 read 가 발생 — 고부하 이벤트에서 `lru_cache` 도입을 검토할 수 있다. **현 트래픽 (행사장 ~수백 명) 에서는 무시.**
- **CR-4 (Nit)** — `_coerce_output_langs_list` 가 `_coerce_output_langs` 를 래핑해 fallback 보강만 추가하는데, helper 가 두 개로 갈라져 있어 가독성이 다소 낮다. 단일 함수에 `ensure_fallback: bool` 옵션을 추가하는 리팩토링이 가능하나 **사이즈 대비 가치 낮음.**
- **CR-5 (Info)** — viewer.html 의 caption 스타일이 webrtc.html 과 일부 중복 (caption-line, fade-in, viewer scroll). 향후 디자인 시스템 도입 시 `components/_caption_styles.css` 추출 + 두 페이지가 공유하는 패턴이 깔끔. **현 단계에서는 인라인 유지가 단순함.**

### Security review

- **No XSS**: 모든 동적 텍스트가 HTML escape 되거나 (`html.escape`) `textContent` (JS) 로 주입됨. `innerHTML` 사용처는 정적 마크업 (`captionEmpty.innerHTML` 은 hard-coded 한국어 텍스트만).
- **No CSRF**: GET 만 사용하고 인증/세션 쿠키 없음 (read-only public viewer).
- **No information disclosure**: 404 / 500 모두 generic, 오리진 헤더 검증 불필요 (모든 viewer 가 익명).
- **No mixed content**: 모든 리소스 (CSS, JS) 가 인라인. 외부 리소스 로드 없음.
- **Cache-Control**: 명시적 헤더 미설정 — aiohttp 기본 (`no-cache` 가 아님). 룸 상태가 바뀌면 viewer 가 stale 페이지를 보일 수 있음. 다만 EventSource 가 즉시 실제 상태를 반영하므로 **UX 영향 미미.** 향후 `Cache-Control: no-store` 추가를 권장.

### Test quality

- 14 단위 테스트 모두 real assertions:
  - 마크업 검증: `EventSource(`, `/stream/`, `id="lang-select"`, `id="state-{waiting,active,ended}"`, 한국어 카피 (`잠시 후 자막이 시작됩니다`, `세션이 종료되었습니다`), `name="viewport"`, `100dvh`, `lang="ko"`, `aria-label`.
  - 핸들러 검증: 정상 룸 (200 + 룸 이름/id 주입), output_langs 인라인 (3 lang 모두 등장), waiting status 페이지, 알 수 없는 룸 (404 + 친절 카피), closed 룸 (200 + 종료 카피 + initial_state="closed"), 레포 예외 (404 + 내부 detail 비노출).
- 3 e2e (Playwright + aiohttp daemon thread):
  - 활성 룸: `#lang-select` 어태치 + 룸 이름 인라인.
  - 알 수 없는 룸: 404 + 친절 메시지.
  - closed 룸: 종료 카피 즉시 노출.
- AC ↔ test 1:1 매핑 모두 충족 (verify_checkpoint AC 커버리지 PASS).

## Security Findings

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| SEC-1 | None | XSS / CSRF / SQL injection 모두 적용 안 됨 (read-only GET, 모든 텍스트 escape). | — |
| SEC-2 | Low | 명시적 `Cache-Control: no-store` 헤더 부재. SSE 가 실시간 상태를 따라잡으므로 UX 영향 미미. | Defer (follow-up) |
| SEC-3 | Info | EventSource auto-reconnect 가 브라우저 기본 backoff 에 의존. DDoS 시나리오에서는 영향 가능하나 프록시 레벨에서 처리. | — |

## UI Review (self)

### State coverage
- **Waiting**: 메시지 + 룸 이름 + 스피너 — `aria-live="polite"` 로 진입 시 안내.
- **Active**: 캡션 컨테이너 + 자막 누적, "자막을 기다리는 중…" empty state 가 첫 메시지 도착 시 자동 제거.
- **Ended**: 메시지 단독 표시, EventSource 닫힘. closed 룸 초기 부트도 동일.
- **Connection error**: `.conn-error` 배너 (`role="status"`) — error 이벤트 시 visible, message 수신 시 hidden.

### Copy
- `잠시 후 자막이 시작됩니다` / `세션이 종료되었습니다` — issues.md 화면 상태 표 카피와 1:1 일치.
- `자막을 기다리는 중…` (active 상태 empty) — UX 친화적 폴백.
- 404: `룸을 찾을 수 없습니다` + `QR 코드 또는 링크가 올바른지 다시 확인해 주세요.` — 사용자 친화 + 동작 가이드.

### Tokens
- 색상: `#10b981` (green) accent, `#fbbf24` (amber) warn — webrtc.html 과 동일한 의미 매핑.
- 타이포: clamp() 기반 — 데스크톱/태블릿/모바일 단일 룰 세트.
- spacing: `padding: 24px 20px` (desktop) → `padding: 16px 14px` (≤600px) — 모바일 밀도 향상.

### Accessibility (WCAG 2.1 AA)
- ✅ Perceivable: 색 대비 17.55:1 / 11.4:1 (AAA), `<html lang="ko">`, `aria-label` on select, `aria-live` on state regions.
- ✅ Operable: keyboard-friendly (`<select>` 네이티브 포커스), `:focus-visible` outline, 44px 터치 타겟.
- ✅ Understandable: 한국어 라벨 일관, error/empty 상태가 명확.
- ✅ Robust: 시맨틱 태그 (`<main>`, `<header>`, `<section>`), valid HTML5.

### Mobile responsive
- ✅ `viewport` 메타 + `viewport-fit=cover` (notch 대응).
- ✅ `100dvh` (RL-011) — iOS Safari 주소창 계산 포함 visible viewport.
- ✅ `clamp()` typography — 320px 폰부터 4K 모니터까지 부드러운 스케일.
- ✅ `@media (max-width: 600px)` — 폰 전용 컴팩트 레이아웃.

## Confidence

**High**. 모든 AC 충족, 모든 review-lessons (RL-006/010/011) 컴플라이언스 검증, 14 단위 + 3 e2e 테스트 GREEN, 정적 분석 (ruff/black) clean, 보안/접근성 follow-up 만 남고 blocking 이슈 없음.

## Lessons applied (no new RL needed)

| Lesson | Application |
|--------|-------------|
| RL-006 | 모든 에러 응답이 generic, 내부 detail 비노출 |
| RL-010 | a11y 체크리스트 4개 항목 모두 충족 |
| RL-011 | 100dvh fallback 패턴 |
| RL-005 | 새 모듈 (viewer.html, _handle_view) 모두 테스트 동반 |
