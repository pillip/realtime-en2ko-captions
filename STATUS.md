# Project Status

## Scan Summary

- **Date**: 2026-04-28
- **Target**: `/Users/pillip/project/practice/realtime-en2ko-captions` (project root)
- **Documents generated**: 7

| Document | Path | Lines |
|----------|------|-------|
| PRD Digest | `docs/prd_digest.md` | 65 |
| Requirements | `docs/requirements.md` | 334 |
| Architecture | `docs/architecture.md` | 307 |
| UX Spec | `docs/ux_spec.md` | 812 |
| Data Model | `docs/data_model.md` | 143 |
| Test Plan | `docs/test_plan.md` | 334 |
| Issues | `issues.md` | 1077 |

## Confidence Distribution

| Tag | Count |
|-----|-------|
| `[CONFIRMED]` (test-backed / documented) | 207 |
| `[INFERRED]` (code-only evidence) | 185 |
| **Confirmation rate** | **52.8%** |

## Test Coverage Summary

| Module | Tests | Status |
|--------|-------|--------|
| `database.py` | ~35 unit tests | Comprehensive |
| `translation.py` | ~20 unit tests (via test_app.py) | Good — pure functions covered |
| `auth.py` | ~18 unit tests | Partial — login/cookie/decorators untested |
| `components/webrtc.html` | ~23 string-match + ~21 e2e | Fullscreen + 무대 창 opener 두 오리진 e2e (ISSUE-50, PR #149); core WebRTC/captions still untested |
| `websocket_handler.py` | 0 tests | **Critical gap** |
| `services.py` | 1 test (find_free_port) | **Critical gap** |
| `admin.py` | 0 tests | **Gap** |
| `app.py` | 0 tests (untestable) | Structural issue |

## Issue Summary

**Total**: 56 issues (4 feature + 20 improvement + 12 backlog added in subsequent sessions + 6 stage composite view + ISSUE-43 security-guard mutation testing + ISSUE-44/45/46 viewer caption-pipeline parity + ISSUE-47 operator stage-mode selector + ISSUE-48/49/50/51 ISSUE-47 review follow-ups + ISSUE-52/53/54 live presentation control + ISSUE-58/59 ISSUE-43 review follow-ups + ISSUE-61~65 ISSUE-49 review follow-ups)

**Open (2026-10-11 재집계, `issues.md` 의 `Status:` 가 권위)** — todo 14 + backlog 4 = **18 issues**:
- **ISSUE-70 (P0, doing)** ALB 에 `/stage/*`·`/branding/*` 미등록 — PR #157, GH #156
- **ISSUE-71 (P0)** SSE/WS 가 `st.session_state` 게이트라 재시작 후 뷰어 사망 — GH #158
- **ISSUE-72 (P1)** `deploy-ec2.sh`/`deploy-alb.sh` 가 깨진 채 잔존 — GH #159
- **ISSUE-55 (P1)** WS 인증에 비밀이 없다 (공개 도메인에서 원격 악용 가능), **ISSUE-56 (P2)** 룸 소유권, **ISSUE-57 (P2)** 자기 번역 LLM 낭비 — ISSUE-52 리뷰 파생
- **ISSUE-58, ISSUE-59 (P2)** ISSUE-43 리뷰 파생
- **ISSUE-61 (P1), ISSUE-62, ISSUE-63 (P2)** ISSUE-49 리뷰 파생 — `.qr-code-caption` 3.95:1 / 웰컴 가드 파생형 전환 / webrtc.html 잔여 표면
- **ISSUE-64 (P1)** admin e2e 플레이키 — GH #155
- ISSUE-51 (P2) 무대 진입점 평가, ISSUE-60 (P2) 치환 엄격화
- backlog: ISSUE-66~69 (뷰어 종료 플래그 RL-022 / 라이브 리전 이중 쓰기 / `publish_control` 거절 / tri-state 반환)

합계 todo 11.5d + backlog 3d. **의존성은 전부 해소되어 18건 모두 지금 착수 가능**하다
(ISSUE-58/59 의 ISSUE-43, ISSUE-62 의 ISSUE-53 모두 shipped).

> 직전 집계(11 issues)는 낡았다 — **ISSUE-54 는 shipped**(PR #151)인데 open 으로 적혀
> 있었고, ISSUE-60·66~69 가 누락돼 있었다. ISSUE-70~72 는 2026-10-11 배포 점검에서 신규 등록. ISSUE-65 는 shipped (PR #154, squash merge `748ea8a`). **In flight**: ISSUE-50 (`doing`, PR #149 오픈 — GH #148, 두 오리진 opener e2e, **프로덕션 diff 0건인 테스트 전용 PR**). Everything else is `shipped` — **ISSUE-49 포함 (PR #147, squash merge `df99025`, GH #145 closed — 오퍼레이터 웰컴 화면 WCAG AA 보정: 텍스트 전량 불투명 hex 전환 + `#viewer:fullscreen` 색 분기 삭제, 두 backdrop 최저 8.35:1. 후속 ISSUE-61~65 파생)** — **ISSUE-53 포함 (PR #146, squash merge `c8ccd1a`, GH #144 closed — SSE control 채널 + 클라이언트 재구조. ISSUE-54 의 차단이 풀렸다)** — ISSUE-43 포함 (PR #142, squash merge `ee409e7`, GH #139 closed) — ISSUE-48 포함 (PR #143, squash merge `972653b`, GH #140 closed). (ISSUE-3 / ISSUE-4 / ISSUE-24 were listed here as `todo` but their issue blocks read `shipped`; corrected against `issues.md` during ISSUE-48. The Board table in `issues.md` still carries stale Backlog rows for ISSUE-5…35 — `Status:` in each issue block is the authority.)

### Feature Issues (ISSUE-1 to ISSUE-4)
Language selection feature — input/output language dropdowns, WebSocket protocol, translation expansion, auto-detect.

### Improvement Issues by Priority

| Priority | Count | Categories |
|----------|-------|------------|
| P0 | 6 | 3 security, 3 test-gap |
| P1 | 10 | 3 test-gap, 2 schema/data, 2 refactor, 1 fix, 1 performance, 1 ci |
| P2 | 4 | 3 refactor, 1 performance |

### Improvement Issues by Category

| Category | Count | Issues |
|----------|-------|--------|
| Security | 3 | ISSUE-5, ISSUE-9, ISSUE-10 |
| Test Gap | 6 | ISSUE-6, ISSUE-7, ISSUE-8, ISSUE-14, ISSUE-18, ISSUE-22 |
| Tech Debt / Refactor | 5 | ISSUE-15, ISSUE-16, ISSUE-19, ISSUE-20, ISSUE-24 |
| Schema / Data | 2 | ISSUE-12, ISSUE-13 |
| Build Fix | 1 | ISSUE-11 |
| CI/CD | 1 | ISSUE-17 |
| Performance | 2 | ISSUE-21, ISSUE-23 |

### Stage Composite View Issues (ISSUE-37 to ISSUE-42) — added 2026-08-23

발표자료와 번역 자막을 무대 전면 스크린 하나에 합성해 띄우는 기능. 발표자료 소스는
화면 캡처(`getDisplayMedia`)이며 PDF 업로드/페이지 동기화는 Out of scope. 브랜딩
설정 단위는 룸별(`rooms.stage_config`)이고 전역 설정/상속은 Out of scope.

| Issue | 제목 | Priority | Estimate | Depends-On | Status |
|-------|------|----------|----------|------------|--------|
| ISSUE-37 | rooms 무대 설정 스키마 — stage_config JSON 컬럼 및 리포지토리 API | P0 | 0.5d | none | shipped (PR #119) |
| ISSUE-38 | 브랜딩 로고 에셋 업로드 저장 및 정적 서빙 라우트 | P1 | 1d | ISSUE-37 | **shipped (PR #123)** |
| ISSUE-39 | 관리자 룸 관리 탭에 무대 설정 폼 추가 | P1 | 1d | ISSUE-37, ISSUE-38 | **shipped (PR #125)** |
| ISSUE-40 | /stage/{room_id} 무대 합성 페이지 레이아웃 구현 | P1 | 1.5d | ISSUE-37 | shipped (PR #122) |
| ISSUE-41 | 무대 자막 컬럼 — SSE 구독 및 좁은 컬럼 타이포 튜닝 | P1 | 1d | ISSUE-40 | **shipped (PR #127)** |
| ISSUE-42 | 발표자료 화면 캡처 연결 — 프레젠터 조작 비간섭 및 복구 처리 | P1 | 1.5d | ISSUE-40 | **shipped (PR #129)** |

**합계**: 6.5d. Critical path = ISSUE-37 → ISSUE-40 → ISSUE-42 (3.5d).
ISSUE-38/39 (브랜딩 에셋 트랙) 과 ISSUE-40/41/42 (무대 렌더 트랙) 은 ISSUE-37 이후
병렬 진행 가능하다.

**진행**: ISSUE-37 (PR #119) 와 ISSUE-38 (PR #123) shipped — 브랜딩 에셋 트랙에서
`branding_assets.py` (룸별 에셋 저장/열거/삭제, 확장자 화이트리스트 + 매직바이트 검증,
2 MB/파일 · 12개/룸 상한, traversal·심볼릭링크 차단) 와 `branding_routes.py`
(`GET /branding/{room_id}/{filename}`, SSE 서버와 같은 오리진, `Cache-Control: public,
max-age=300`, SVG 에 `default-src 'none'` CSP) 가 main 에 올라갔다. FR-077/FR-078/NFR-028
충족. 이로써 ISSUE-39 (관리자 무대 설정 폼) 의 선행 조건이 모두 해소됐다.

**진행**: ISSUE-41 (PR #127, squash `cc0681f`) shipped — 무대 렌더 트랙에서 `components/stage.html` 의 자막 컬럼이 `/stream/{room_id}` SSE 를 구독하고 `requestAnimationFrame` 기반 타자기 스무딩으로 부분 자막을 렌더한다 (`setInterval` 0건). RL-019 라이브 리전 소유권 이전 완료 — `#caption-container` 는 `aria-hidden="true"`, 라인 확정 시에만 갱신되는 `#caption-announcer`(`aria-live="polite" aria-atomic="true"`) 가 라이브 리전을 소유한다. NFR-025 준수: `keydown`/`keyup`/`preventDefault` 0건으로 프레젠터 리모컨 키 입력을 삼키지 않는다. 좁은 컬럼 타이포 (`clamp(20px, 1.4vw + 8px, 32px)`, `line-height: 1.45`, `word-break: keep-all`, `overflow-wrap: anywhere`) 와 `MAX_LINES = 60` 적용. FR-080 충족. 이로써 ISSUE-42 (화면 캡처) 의 선행 조건이 해소됐다.

**진행**: ISSUE-42 (PR #129, squash `f26f466`) shipped — **무대 합성 뷰 트랙 6개 이슈 전부 완료**. `components/stage.html` 좌측 발표 영역이 `getDisplayMedia({ frameRate: { ideal: 30 }, selfBrowserSurface/surfaceSwitching/systemAudio: "exclude" })` 로 선택한 화면을 `object-fit: contain` 레터박스로 표시한다. 사용자 하드 제약("버벅인다거나 프레젠터로 조작이 안되거나 해서는 안되")을 인수 조건으로 구현했다 — **포커스가 1차 방어선**: 연결 후 컨트롤을 `display: none` 으로 렌더 트리에서 빼고(`pointer-events: none` 은 OS 포커스를 막지 못한다), `cursor: none`, 자동 `focus()`/`requestFullscreen()` 없음, 연결 직후 `blur()` 로 `document.activeElement === document.body`. **keydown 부재가 2차 방어선**: 전역 keydown 등록은 `?debug=1` 리허설 계수기 하나뿐이고 `{ passive: true }` 로 계수만 하며, 파일 전체 `preventDefault`/`stopPropagation`/`keyup` 0건. 복구 경로 — 트랙 `ended` → 타이틀 카드 + "발표자료 다시 연결"(자막 컬럼 무중단), 자기 캡처 휴리스틱 경고 배너, Screen Wake Lock capability guard (RL-008), `NotAllowedError` 는 예외 문자열 없이 일반 안내만 (RL-006). ISSUE-40 의 NFR-025 가드 2건은 **삭제하지 않고 강화**했다 (단언 8 → 20건, 어휘적 금지 → 등록 1건·`{passive:true}`·취소 호출 0건을 세는 구조적 단언 + 행동 기반 e2e). FR-074/075/081, NFR-025/026/027 충족.

**남은 수동 게이트**: NFR-025 의 "비포커스 상태에서 프레임이 계속 갱신된다" 와 부드러움 절반은 canvas `captureStream()` 스텁으로 자동화가 불가능하다(스텁 캔버스는 한 번만 그려져 `currentTime` 이 증가하지 않는다). **실제 프레젠터 리모컨으로 30분 행사장 리허설**이 필수 게이트로 남아 있다 — `docs/test_plan.md` Gap 8 참조. 리허설 판정 기준: `?debug=1` 오버레이에서 발표 앱 포커스 상태로 리모컨 20회 → keydown 카운터 0.

**하드 제약 (인수 조건)**: 캡처가 버벅이거나 프레젠터 리모컨 조작이 막히면 안 된다 —
NFR-025 ~ NFR-027 로 등재되어 ISSUE-41/42 의 AC 에 Given/When/Then 으로 반영되어 있다.

## Skipped Agents

None — all agents invoked (codebase-scanner, scan-analyst, scan-architect, ux-designer, scan-data-modeler, scan-qa-designer, scan-planner).

## Sprint Progress (Iteration 7)

**Shipped**: 22 / 25 issues
**Remaining**: ISSUE-3, ISSUE-4, ISSUE-24

### Recent (Iteration 7)
- ISSUE-2: WebSocket language settings pass-through (PR #35)
- ISSUE-20: Drop legacy salt/hash_type columns (PR #36)
- ISSUE-23: Enable SQLite WAL mode (PR #37)

**Test suite** (current, main @ `ee409e7`): 1319 passed / 1 skipped / 133 deselected, 94.53% coverage
(직전 main `972653b` 기준 1288 passed — ISSUE-43 으로 유닛 +31)
**가드 뮤테이션 게이트**: `uv run python guard_mutations.py` → 20/20 killed, 0 survived, 0 errors (로컬 ~9초, CI `mutation` job 50초)
e2e 별도: `uv run pytest -q -m e2e` 133 passed. Lint: `ruff check` / `black --check` clean.

**Shipped (2026-09-06)**: ISSUE-65 — PR #154 squash merge `748ea8a`, GH #153 closed.
`.pre-commit-config.yaml` 의 `ruff-format` 훅 제거 + `[tool.ruff.format]` 삭제 + `CLAUDE.md`
포매터 정책 명시 + `tests/test_precommit_formatter_policy.py` 회귀 가드 6건(교착 레이아웃을
카나리아로 보유). 프로덕션 코드 동작 변경 0건.
**이제 `black` 이 유일한 포매터이고 `ruff` 는 린터 전용이다** — 줄바꿈된 `assert <cond>, <msg>`
가 든 파일이 `SKIP=` 없이 한 번에 커밋된다. 따라서 다른 이슈 본문에 복사돼 있는 "줄바꿈된
assert 를 피하라" 회피 지침은 **전부 무효**다(본문 정리는 별건으로 진행).
머지 후 main @ `748ea8a` 실측: `uv run pre-commit run --all-files` **2회 연속** 전 훅 통과 ·
수정 파일 0 (AC 1 충족), `black --check .` 97 files unchanged, `ruff check .` All checks passed,
`uv run pytest -q` **1472 passed / 4 skipped / 198 deselected, 커버리지 94.35% (96.04s)**,
`tests/test_precommit_formatter_policy.py` 6 passed. skip 4건 중 3건은 `3a16837` 이 도입한
**브랜치 전용** 소유권 가드로 main 에서는 설계상 skip 이다(회귀 아님).

## Next Steps

0. **ISSUE-43: shipped (PR #142, merge `ee409e7`)** — RL-004(약한 단언, Frequency 10)를 장치로
   막는다. 보안 크리티컬 가드 20건(`branding_assets` 경로 봉인·업로드 검증 15, `branding_routes`
   404 계약 1, `auth` 역할 판정 3, `admin_logic.filter_rooms_for_role` 1)이 각각 **이름이 지정된
   킬 테스트**에 `Guard: <모듈>.<함수>#<가드명>` docstring 으로 묶였고, `guard_mutations.py` +
   `guard_mutations.toml` 이 그 대응을 한 명령으로 재현한다. CI `mutation` job 이 게이트.
   - **실증**: ISSUE-38 당시 형태(심볼릭 링크 대상이 룸 바깥)로 킬 테스트를 되돌리면 `#symlink`
     가드가 `SURVIVED` 로 잡힌다 — 그때 이 도구가 있었다면 수작업 없이 CI 에서 걸렸다.
   - **성질**: 카탈로그 길이만큼 1회 순회하고 종료(후보 생성 없음), 저장소를 tempdir 로 1회 복사해
     **사본만** 변형하므로 작업 트리는 어떤 종료 경로에서도 무결, 허용 목록은 러너에 하드코딩되어
     카탈로그가 넓히지 못한다, 소유 테스트 없는 가드는 `unnamed` 으로 비정상 종료.
   - 프로덕션 코드 변경 0건. 파생: **ISSUE-58**(RL-026 마크업 속성 뮤테이션 클래스 — 무주공산),
     **ISSUE-59**(CI 하드닝 잔여: job 타임아웃, 러너 커버리지 귀속, 허용 목록 확대 판정).

1. **Stage composite view track: 완료** — ISSUE-37/38/39/40/41/42 전부 shipped.
   `/stage/{room_id}` 는 무대 설정 스키마 → 브랜딩 에셋 → 관리자 폼 → 합성 레이아웃 →
   자막 컬럼 → 화면 캡처까지 end-to-end 로 동작한다
2. **행사장 리허설 (수동, 미완)** — 자동화 불가한 NFR-025 절반이 남아 있다. 실제 프레젠터
   리모컨 + 실제 발표 앱으로 30분 세션을 돌리고, `?debug=1` 오버레이로 "발표 앱 포커스
   상태에서 리모컨 20회 → keydown 카운터 0" 을 확인해야 한다. 릴리스 체크리스트 항목
3. **Viewer parity track** (ISSUE-41 리뷰에서 파생): `components/viewer.html` 은 이제
   세 이슈가 함께 소유한다 — ISSUE-44 (스크립트 컨텍스트 이스케이프 파리티),
   **ISSUE-46 (신규, P1)** 자막 파이프라인 결함 파리티(연속 final 유실은 공개
   `/view/{room_id}` 에서 재현되는 데이터 손실), ISSUE-45 (WCAG AA 대비 + 자막
   라이브 리전 부재 — ISSUE-41 UI 리뷰의 FU-3 를 흡수해 `Estimate 0.5d → 1d`,
   `Depends-On: ISSUE-46` 로 개정). **머지 순서: ISSUE-46 → ISSUE-45** — ISSUE-46 이
   `finalizeCaption` 을 재작성하므로 역순이면 ISSUE-45 의 `announce()` 배선이
   조용히 사라진다 (RL-019 의 실종 형태)
4. **Refactor**: ISSUE-24 — Remaining tech debt
5. **ISSUE-52: shipped (PR #141, merge `7c8d4ed`)** — 오퍼레이터가 세션에서 고른 입력/출력 언어가
   `rooms` 행에 기록되어, 그 뒤에 새로 여는 `/stage/{room_id}` · `/view/{room_id}` 가 **실제로 송출 중인**
   언어 채널을 구독한다. 고친 결함은 증상이 없던 종류다 — ko→vi 세션에서 번역은 정상이었고
   (`usage_logs` 에 진짜 베트남어) 무대에는 한국어가 떴다. publish 는 오퍼레이터 `target_lang` 을,
   페이지 렌더는 DB `rooms.primary_output_lang` 을 읽었는데 세션 선택이 DB 에 한 번도 기록되지 않았기
   때문이다. lazy 게이트가 뷰어 있는 언어면 무엇이든 publish 하므로 무대의 `ko` 구독 때문에 서버가
   한국어를 한국어로 "번역" 해 보냈다 — 빈 화면이 아니라 그럴듯한 오답이, 오류도 로그도 없이.
   `database.Room.update_session_languages` 가 `input_lang`/`output_lang`/`primary_output_lang` 을
   **한 UPDATE** 로 함께 쓰고(RL-025 — `primary_output_lang` 이 권위 컬럼임을 docstring 에 명시),
   `websocket_handler` 의 두 지점(`_authenticate_client`, `language_update`)에서 호출한다.
   **`rooms.output_langs` 는 손대지 않는다** — `_supported_output_langs` 가 #91/#92 에서 의도적으로
   그 컬럼을 무시하므로 여기서 동기화하면 뷰어 언어 선택이 룸 설정에 재결합된다. 바이트 동등 단언으로
   고정했다. 스키마 변경 없음. 리뷰는 APPROVE-WITH-NITS(머지 차단 없음)였고, 지적된 항목 중
   **실제로 회귀를 잡지 못하던 가드 두 개**를 PR 안에서 고쳤다: (a) 입력 언어 화이트리스트가
   `webrtc.html` 드롭다운의 손복사본인데 둘을 묶는 테스트가 없었고(RL-001, freq 4→5),
   (b) RL-002 가드 테스트가 두 값이 같은 분기를 몰아 구현을 바꿔도 통과했다(RL-004, freq 8→9).
   뮤테이션 16종 선언 / 16종 kill. 후속 3건을 이슈로 분리했다: **ISSUE-55**(P1, WS 인증에 비밀이 없다 —
   기존 결함이나 ISSUE-52 가 그 채널에 영속 능력을 붙였다), **ISSUE-56**(P2, WS 경로에만 룸 소유권 검사가
   없다), **ISSUE-57**(P2, `source_lang == target_lang` 자기 번역 LLM 낭비 — ISSUE-52 이전에도 이미
   발생하던 것으로 확인).
6. **ISSUE-47: shipped (PR #137)** — 오퍼레이터가 `:8501` 에서 룸과 표시 모드(일반 자막 / 무대 화면)를
   함께 고르고, 무대 화면 모드에서 "무대 화면 열기" 로 `:8766/stage/{room_id}` 를 새 최상위 창으로 연다.
   구현 1단계 실측이 이슈의 설계 근거를 **반증**했다 — Streamlit 컴포넌트 iframe 은 `display-capture` 를
   차단하지 않는다(`allow` 에서 생략된 기능은 기본 허용목록 `self` 로 폴백). 임베드가 실패하는 진짜 이유는
   `/stage/` 가 `:8766` 으로 크로스 오리진이기 때문이고 `allow="display-capture"` 한 줄로 해결된다.
   설계(새 창)는 유지하되 근거를 제품 특성(프로젝터 출력 분리, FR-081 self-capture 위험)으로 교체했고
   FR-082 / NFR-029 를 개정했다. `components/stage.html` 은 한 줄도 바뀌지 않았다 — 순수 SSE 소비자 유지,
   no-pipeline 문자열 가드 추가(RL-001).

7. **ISSUE-47 리뷰에서 파생된 후속 4건** — 코드 리뷰와 UI 리뷰가 **독립적으로 같은 High 결함**을 찾았다
   (무대 버튼이 첫 자막에서 `appendLine()` 의 `.welcome-state` 제거와 함께 사라져 AC4 불성립). PR 안에서
   수정했고, 남은 항목을 이슈로 분리했다:
   - **~~ISSUE-48~~ (P1, security) — shipped, PR #143, merge `972653b`** `{{BOOTSTRAP_JSON}}` 이 raw
     `<script>` 싱크인데 `json.dumps` 는 `<` 를 이스케이프하지 않아, admin 이 지은 룸 이름 하나로
     stored XSS + 부트스트랩 사망이 동시에 일어났다. 이스케이퍼를 의존성 없는 `script_escape.py`
     (`json_for_script`) 로 **추출**해 뷰어/무대/오퍼레이터 네 번째 렌더러까지 단일 구현으로 모았고
     (`sse_broadcast._json_for_script` 는 별칭으로 남아 기존 테스트 무변경),
     `operator_ui.render_component_html` 이 단일 패스 `re.sub` + 엄격 인덱싱으로 치환한다.
     `app.py` 의 맨 `json.dumps` 는 사라졌다 (RL-001 freq 5 / RL-016 / RL-020 / RL-021).
     머지 후 main 에서 재실측: `A홀</script><script>window.__pwned=1;</script>` 룸 이름으로
     `</script>` 개수 1 = 템플릿과 동일, 새 `<script>` 0건, `json.loads(BOOT)` 가 payload 와
     완전 일치(룸 이름 바이트 단위 보존 — 이중 이스케이프 없음). 같은 프로브를 옛 `json.dumps`
     경로에 걸면 `</script>` 3개 + 실행 가능한 breakout 으로 **실패**한다 (RL-004)
   - **~~ISSUE-49~~ (P1, a11y) — shipped, PR #147, merge `df99025`** 웰컴 화면 `.hint` /
     `.welcome-rules` 3.80:1, `.welcome-room` 전체화면 `#000` 에서 4.43:1 이던 것을 전부
     **불투명 hex** 로 바꾸고 `#viewer:fullscreen .welcome-state` 색 분기를 삭제했다 —
     `#c8c8d2`(11.85/12.65) / `#a8a8b3`(8.35/8.92), body·전체화면 **양쪽 최저 8.35:1**.
     불투명이면 backdrop 이 어두워질수록 대비가 올라가 한 값이 두 backdrop 을 모두 덮는다.
     backdrop 상수를 파일의 실제 `background` 선언과 대조하는 구조 가드 3종 동반 (RL-018 Freq 4)
   - **ISSUE-50 (P2, test)** `opened.opener = null` 은 실측상 동작하나 e2e 스텁이 같은 realm 객체를
     돌려줘 커버리지가 0 이다. 두 오리진 fixture 필요 (RL-026)
   - **ISSUE-51 (P2, refactor)** 진입점을 `st.link_button` 으로 옮기면 RL-027 / RL-028 / 팝업·opener 를
     한꺼번에 없애고 ~400줄이 사라진다. 다만 `st.link_button` 이 탭을 여는지 창을 여는지가 NFR-029 의
     프로젝터 요구와 직결되므로 **측정 후 결정** — 구현이 아니라 평가 이슈다

8. **Refactor**: ISSUE-24 — Remaining tech debt

9. **Live presentation control track (ISSUE-52 shipped, ISSUE-53/54 남음)** — 실측된 **무증상 오작동**에서 출발했다.
   오퍼레이터가 입력=한국어 / 출력=베트남어로 세션을 돌렸고 `usage_logs` 에는 `ko → vi`, `used_llm: true`,
   진짜 베트남어 `target_text` 가 남았는데 **무대 화면에는 한국어가 떴다.** 원인은 source of truth 가
   둘이고 서로 다른 값을 말했기 때문이다 — publish 는 오퍼레이터의 세션 설정
   (`websocket_handler.py:686-689`, 주석에 "오퍼레이터 설정이 source-of-truth")을 쓰고,
   페이지 렌더는 `rooms` 행(`sse_broadcast._handle_stage:549`)을 읽는데 **아무도 전자를 후자에
   기록하지 않았다.** 게다가 lazy 게이트가 상황을 빈 화면보다 나쁘게 만들었다: 무대 페이지가
   스스로를 `ko` 뷰어로 등록해 뒀으므로 서버가 한국어를 **한국어로 번역해** `ko` 채널에 실어 보냈다.
   오류도 로그도 없다.

   **ISSUE-52 로 페이지 로드 시점은 해결됐다** (merge `7c8d4ed`) — 이제 `rooms.primary_output_lang` 이
   오퍼레이터 선택을 담고 있어 그 뒤에 새로 여는 무대/뷰어가 옳은 채널을 구독한다. 남은 것은
   **이미 열려 있는 화면의 실시간 추종**(ISSUE-53)과 오퍼레이터 컨트롤(ISSUE-54)이다.

   | Issue | 제목 | Priority | Estimate | Depends-On |
   |-------|------|----------|----------|------------|
   | ~~ISSUE-52~~ | 오퍼레이터 세션 출력 언어를 rooms 행에 기록 — **shipped, PR #141** | P1 | 0.5d | none |
   | ~~ISSUE-53~~ | SSE control 이벤트 채널 — 룸 프레젠테이션 상태 전파 및 재구독 — **shipped, PR #146** | P1 | 1.5d | ISSUE-52 |
   | ISSUE-54 | 오퍼레이터 화면 실시간 컨트롤 — 언어 즉시 반영 + 무대 자막 크기 슬라이더 (**차단 해제됨**) | P1 | 1d | ISSUE-53 ✅ |

   **합계 3d, 선형 critical path** (52 → 53 → 54). ISSUE-52 만으로도 **단독 출하 가능**하며 신고된
   증상(페이지 로드 시점의 잘못된 언어)을 닫는다 — 먼저 머지해 현장 오작동을 멈추는 것이 옳다.

   **고정된 설계 결정 (다시 열지 말 것)**:
   - 폰트 배율은 **`stage_config` 에 영속화하지 않는다.** 관리자 폼이 `normalize → save` 왕복을 하고
     `normalize_stage_config` 는 모르는 키를 조용히 버리므로, 행사장에서 맞춘 값이 관리자가 로고 하나
     바꾸는 순간 로그도 없이 기본값으로 되돌아간다 (RL-024/RL-025). 재로드 문제는 **SSE 연결 직후
     스냅샷 프레임**으로 푼다. 잔여 갭은 하나 — 서버 프로세스 재시작 시 배율이 1.0 으로 돌아간다.
     복구는 슬라이더 한 번. 언어는 ISSUE-52 가 DB 에 쓰므로 재시작에도 살아남는다
   - **ISSUE-47 F-2(RL-028) 의 실패 경로를 타지 않는다 — 확인 완료.** 표시 모드 라디오를 세션 중
     잠근 이유는 그 값이 BOOT payload → 컴포넌트 iframe `srcdoc` 에 들어가 문서를 리마운트하기
     때문이다. 출력 언어 드롭다운은 **이미 컴포넌트 안**(`webrtc.html:645`)에 있고 값은 **이미 열려
     있는 WebSocket** 으로 흐른다(`sendLanguageUpdate`, `:1077-1088`). 새 슬라이더도 같은 규칙이다.
     **두 이슈 모두 BOOT payload 에 필드를 추가하지 않으며**, ISSUE-54 에 반환 키 집합 상등을
     단언하는 회귀 가드가 있다
   - **`rooms.output_langs` 는 건드리지 않는다.** `_supported_output_langs` 가 #91/#92 에서
     의도적으로 이 컬럼을 무시한다 — 여기서 다시 쓰면 뷰어 언어 선택이 룸 설정에 재결합된다.
     ISSUE-52 에 "컬럼 값이 바이트 단위로 그대로" 를 단언하는 AC 가 있다

   **하드 제약**: NFR-025 는 협상 대상이 아니다. `components/stage.html` 에 클릭·포커스 가능한 요소를
   **추가하지 않는다** — 폰트 배율은 SSE 로 도착해 프로그램적으로만 적용된다. ISSUE-40/41/42 가 출하한
   `tests/test_stage_page.py::test_no_interactive_controls`(`:363`) 와
   `::test_no_presenter_keyboard_interference`(`:292`) 는 **수정 없이 통과**해야 하고, 그 두 테스트를
   이번 PR 에서 고쳤다면 그것 자체가 실패 신호다. 강화는 **행동 e2e 로 추가**한다 — control 이벤트를
   실제로 전달한 **뒤** 보이는 클릭 가능 요소 0개와 `document.activeElement === document.body` 를 센다
   (정적 개수 가드는 런타임에 만들어진 노드를 보지 못한다).

   **인접하지만 이 트랙이 고치지 않는 것 (미소유, 기록만)**:
   - **RL-022 가 `viewer.html` 에서 재발했고 주인이 없다** — `:718-729` 의 `session_end` 가
     `currentLine` 만 비우고 종료 플래그를 세우지 않아 늦게 온 프레임 하나가 끝난 세션을 되살린다
     (`stage.html:997` 에는 `sessionEnded` 가드가 있다). ISSUE-53 이 같은 코드 구역에 리스너를
     하나 더 붙이므로 인접성을 명시했다
   - `broadcast_translation_for_room` 이 빈 텍스트를 서버측 가드 없이 publish 한다 (양쪽 클라이언트가 흡수)
   - WS/SSE 데몬 스레드가 `app.py:258-305` 의 `st.session_state` 게이트라, 컨테이너 재시작 후 오퍼레이터가
     `:8501` 을 열기 전까지 `/view/`·`/stage/` 가 죽어 있다
   - primary 가 `vi` 인데 `ko` 뷰어가 있으면 한국어 원문을 `ko` 로 "번역" 하는 LLM 호출이 돈다.
     ISSUE-52 이후 이 경로는 버그가 아니라 정상 기능(한국어 청중이 원문을 본다)이지만
     `source_lang == target_lang` 스킵이라는 낭비 제거가 남아 있다

## 배포 상태 (2026-10-11 점검)

**프로덕션은 라이브다** — `https://translate.andone.agency` (ALB, A 레코드 3 IP).
CI 에 deploy job 이 없으므로 배포는 전부 수동(`git pull` + `docker-compose up -d --build`)이고,
따라서 **"main 에 머지됨" 이 "배포됨" 을 전혀 함의하지 않는다.**

### ALB 경로 라우팅 실측

| 경로 | 응답 | 어느 서버 |
|---|---|---|
| `/view/nonexistent` | 404, 914B HTML | 8766 ✅ |
| `/stream/nonexistent` | 404, 9B `not found` | 8766 ✅ |
| `/ws` | 426 | 8765 ✅ |
| `/stage/nonexistent` | **200, 1522B** | **8501 ❌** |
| `/branding/x/y.png` | **200, 1522B** | **8501 ❌** |

1522B 는 `/` 와 바이트 수가 같은 Streamlit 앱 셸이다. **무대 합성 뷰 트랙(ISSUE-37~42,
6개 전부 shipped)과 룸 로고가 프로덕션 도달 불가** — ISSUE-70. ALB 콘솔 규칙 추가는
**수동 작업으로 남아 있다**; PR #157 은 문서·가드만 고친다.

**이 갭의 성질**: 미등록 경로는 404 가 아니라 HTTP 200 + 앱 셸을 받는다. 에러도 로그도
없고 상태 코드만 보는 헬스체크는 영원히 통과한다. 검증은 "없는 룸으로 응답 주체 식별".

### 확인 불가 / 미해결

- **배포된 컨테이너의 커밋 SHA 미확인** — 작업 맥북에 EC2 SSH 키·호스트 설정이 없다.
  서버에서 `cd realtime-en2ko-captions && git log -1` 로 확인 필요
- **#79(SSE 비인증·rate-limit 부재)의 전제가 충족됐다** — "공개 배포 시 우선순위 상승"
  이 현실이다. 공개 도메인에서 `/view/*`·`/stream/*` 가 무인증으로 열려 있다
- **ISSUE-55 가 라이브 노출이다** — `_authenticate_client` 에 쿠키·토큰·서명이 전혀 없고
  검증이 "id 가 DB 에 있나 / username 이 그 행과 일치하나 / is_active 인가" 뿐이다.
  `/ws` 가 공개 도메인에서 426 을 주므로 id/username 쌍 하나로 WS 에 붙어
  `request_openai_session`(`websocket_handler.py:765`)을 쓰고 모든 뷰어에 임의 자막을
  띄울 수 있다
- **NFR-025 행사장 리허설** 여전히 미완 (자동화 불가, 릴리스 체크리스트 항목)

### main @ `748ea8a` 게이트 실측 (2026-10-11 재측정)

| 게이트 | 결과 |
|---|---|
| ruff / black | clean (170 files) |
| unit | 1472 passed / 4 skipped, 94.35% |
| 가드 뮤테이션 | 20/20 killed, 0 survived |
| e2e | **baseline 미고정** — 전체 2 failed + 5 errors vs 해당 파일 단독 6 passed |

e2e 는 ISSUE-64(#155) 의 순서 의존 플레이키다. 배포 직전에 가장 필요한 신호가
현재 "판단이 필요한 신호" 상태다.

## Last Updated
2026-10-11
