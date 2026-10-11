# Sprint State

## Meta
- Started: 2026-08-31
- Iteration: 6 / 20
- Parallel: 3
- Status: completed

## Issue Progress
| Issue | Status | Attempts | Last Error | Phase |
|-------|--------|----------|------------|-------|
| ISSUE-43 | active | 0 | — | shipped |
| ISSUE-48 | shipped | 0 | — | shipped (GH #140, PR #143, merge 972653b) |
| ISSUE-49 | shipped | 0 | — | shipped (GH #145, PR #147, merge df99025) |
| ISSUE-50 | shipped | 0 | — | shipped (GH #148, PR #149, merge 09fa01a) |
| ISSUE-51 | active | 0 | — | backlog |
| ISSUE-52 | shipped | 0 | — | shipped (GH #138, PR #141, merge 7c8d4ed) |
| ISSUE-53 | shipped | 0 | — | shipped (GH #144, PR #146, merge c8ccd1a) |
| ISSUE-54 | shipped | 3 | API 오류 3회 (오케스트레이터 인계) | shipped (GH #150, PR #151, merge 85c8d44) |

## ISSUE-52 — PIPELINE 전 단계 통과 (implement → review → ship)

**결과**: GH #138 / PR #141 / squash merge `7c8d4ed`. 테스트 1234 → **1268 passed**
(114 deselected), 커버리지 94.17% → **94.26%**. `ruff check` / `black --check` clean.
main 스모크 + CI 3종(Lint / Test / E2E Playwright) 전부 green.

**고친 것**: publish 채널(오퍼레이터 `target_lang`)과 페이지 렌더 채널
(`rooms.primary_output_lang`)이 갈라져, ko→vi 세션이 무대에 한국어를 띄우던 무증상 결함.
`database.Room.update_session_languages` 가 세 컬럼을 한 UPDATE 로 기록하고
`websocket_handler` 의 두 지점(`_authenticate_client`, `language_update`)에서 호출한다.

**가장 위험했던 지점 — `rooms.output_langs`**: `_supported_output_langs` 는 #91/#92 에서
이 컬럼을 **의도적으로** 무시한다. 언어 write-back 을 쓰면서 "김에 같이 동기화" 하는
본능을 따랐다면 그 결정이 무효화됐다. 손대지 않았고, 기록 전후 **바이트 동등**을 단언으로
고정했다(공백 포함 `'["ko", "en"]'` 을 심어 재직렬화도 실패시킨다). 머지된 main 에서 실측:
룸 `output_langs='["ko"]'` 인데 `/view/` 드롭다운은 `["ko", "en", "zh", "vi"]` 전부,
기본 선택 `"vi"`.

**리뷰(APPROVE-WITH-NITS, 머지 차단 0)가 잡은 진짜 문제 두 개** — 둘 다 "테스트가 있는데
회귀를 못 잡는" 형태였고 PR 안에서 고쳤다:
1. `_SESSION_INPUT_LANGS` 가 `webrtc.html` `#selInputLang` 의 손복사본인데 둘을 묶는
   테스트가 없었다. 옵션이 하나 늘면 그 언어 세션이 전부 거절되어 무대가 이전 언어를
   계속 구독한다 — **이 이슈가 고친 결함과 똑같은 모양** (RL-001, freq 4 → 5).
2. RL-002 가드 테스트가 `room_id="r1"` 을 보내고 `"r1"` 을 단언해 두 값이 같은 분기만
   몰았다. 구현을 `data.get("room_id")` 로 바꿔도 통과했다 (RL-004, freq 8 → 9).
   room_id 를 보내지 않아 서버가 `DEFAULT_ROOM_ID` 로 확정하는 분기로 교체.

**뮤테이션**: 선언 범위 16종(diff 내 12 + 호출 지점 2 + 리뷰 수정 검증 2), **16종 kill**.
ISSUE-47 교훈대로 diff 밖 호출 지점도 포함했다. 미커버로 남긴 것:
`_publish_to_viewers` 의 `repo is None` 조기 반환(기존 ISSUE-30 코드, main 에서도 미커버),
`language_update` 를 실 SQLite `Room` 에 통과시키는 단일 테스트.

**체크포인트 실패 1종**: `implement --phase test` 와 `review --phase test` 가 **exit 124**.
스크립트 60초 타임아웃 대 실제 스위트 ~83초 때문이며 테스트 실패가 아니다. 더 긴 예산으로
직접 재확인해 통과를 확정하고 진행했다. 나머지 체크포인트는 전부 PASS.

---

## ISSUE-47 — display-capture 실측 결과 (구현 1단계, PIPELINE 중단 지점)

이슈/FR-082/NFR-029 가 근거로 삼은 전제 **"Streamlit 컴포넌트 iframe 은 `display-capture`
권한을 부여받지 못한다"** 는 **실측 결과 사실이 아니다.** 코드는 작성하지 않고 중단했다
(지시된 "stop and report" 분기).

계측 방법: 동일 브라우저/동일 플래그(Playwright, full Chromium channel)에서 문맥만 바꿔
`document.featurePolicy.allowsFeature('display-capture')` 와 `getDisplayMedia()` 거부
사유를 비교. `allow="display-capture 'none'"` 음성 대조군으로 계측기를 교정했다.

| 문맥 | `allowsFeature('display-capture')` | `getDisplayMedia()` 거부 사유 |
|---|---|---|
| 최상위 문서 (대조군) | `true` | `NotReadableError` (헤드리스 환경 한계) |
| **실제 Streamlit 컴포넌트 iframe** (srcdoc, opaque origin) | **`true`** | `NotReadableError` |
| Streamlit sandbox/allow 속성 복제본 | `true` | `NotReadableError` |
| **음성 대조군** `allow="display-capture 'none'"` | **`false`** | **`NotAllowedError` — disallowed by permissions policy** |
| 교차 오리진 자식, `allow` 없음 (= 순진한 `<iframe src=":8766/stage/…">`) | **`false`** | **`NotAllowedError` — disallowed by permissions policy** |
| 교차 오리진 자식 + `allow="display-capture"` | `true` | `NotReadableError` |
| **전체 임베드 체인**: streamlit srcdoc(opaque) → 교차 오리진 stage + `allow="display-capture"` | **`true`** | `NotReadableError` |

해석:
1. Streamlit 컴포넌트 iframe 은 `display-capture` 를 **차단하지 않는다**. `allow` 속성에
   열거되지 않은 기능은 자기 *기본 allowlist* 로 폴백하고 `display-capture` 의 기본값은
   `self` 이기 때문 — Streamlit 은 `display-capture 'none'` 을 쓴 적이 없다. 스프린트
   지시에서 단서로 본 "`allow-popups-to-escape` 는 있는데 `display-capture` 항목은 없다"
   는 관찰은 사실이지만, 부재는 거부가 아니다.
2. 다만 **순진한 임베드는 실제로 실패한다** — `/stage/{room_id}` 는 :8766 으로 :8501 과
   교차 오리진이고, 교차 오리진 자식의 기본 allowlist `self` 는 매칭되지 않는다. 이슈가
   예측한 증상은 맞았고 **원인 진단만 틀렸다**.
3. 그래서 임베드는 **기술적으로 가능하다** — 중첩 iframe 에 `allow="display-capture"`
   한 줄이면 되고, Streamlit 의 opaque-origin srcdoc 부모를 거치는 실제 체인에서도 위임이
   살아남는 것을 확인했다.

미증명(정직하게 남김): 이 환경은 **최상위 문서에서조차** 비디오 소스를 시작하지 못해
(`NotReadableError`) 캡처가 프레임까지 흐르는 end-to-end 성공은 어느 문맥에서도 확인하지
못했다. 이 한계는 모든 문맥에 대칭이라 권한 게이트 결론은 흔들리지 않지만, "iframe 안에서
캡처가 끝까지 된다" 를 적극적으로 입증한 것은 아니다.

권고: **새 창 설계는 유지하되 근거를 교체한다.** 무대 화면은 프로젝터 출력 표면이고
(`stage.html:54` "커서는 프로젝터 화면의 시각적 오염", `:1145` "무대는 프로젝터 출력"),
`height=900` 컴포넌트 iframe 에 갇히면 보조 디스플레이로 끌어다 전체화면할 수 없다.
게다가 무대 페이지를 오퍼레이터 탭에 임베드하면 그 탭 자체가 캡처 후보가 되어 FR-081
무한거울 위험(`stage.html:830-834`)이 커진다. 즉 결론은 옳고 이유가 틀렸다 — 권한 근거를
지우고 프로젝터/무한거울 근거로 바꿔야 한다.

## ISSUE-50 — IMPLEMENT 완료 (두 오리진 opener e2e)

- Worktree: `.worktrees/issue-ISSUE-50-two-origin-opener-e2e` (유지 — ship 이 브랜치를 필요로 함)
- GH Issue #148 / PR #149 / branch head `b8111d4`
- 산출물: `tests/e2e/test_stage_window_opener_e2e.py` (+422줄, 7 tests). **프로덕션 diff 0건** —
  `git diff origin/main -- components/ operator_ui.py sse_broadcast.py guard_mutations.*` 이 비어 있음.

### 검증 실측
- unit: `uv run pytest -q` → 1319 passed / 1 skipped / 140 deselected (coverage 94.53%)
- e2e: `env -u NODE_OPTIONS uv run pytest -q -m e2e` → 138 passed / 2 errors
- ruff / black: clean (단, 아래 pre-commit 충돌 참조)

### 체크포인트 결과
| phase | exit | 비고 |
|-------|------|------|
| test-plan | 0 | PASS |
| figma | 0 | PASS (Figma URL 없음 → skip) |
| issue | 0 | PASS |
| worktree | 0 | PASS |
| tests-written | 0 | PASS |
| **red** | **1** | **FAIL — 검증기 결함.** `python3 -m pytest -q` 로 실행하는데 addopts 가 `-m 'not e2e'` 라 신규 e2e 파일이 통째로 deselect → exit 0 → "tests passed" 로 오판. 테스트 전용 이슈(프로덕션이 이미 올바름)에서는 구조적으로 통과 불가. 실제 RED 증거는 아래 뮤테이션 3건 |
| code | 0 | PASS |
| test | 0 | PASS (87.9s — 60초 타임아웃 트랩은 발생하지 않음, exit 124 아님) |
| push | 0 | PASS |
| pr | 0 | PASS |
| registry | 0 | PASS |

### 뮤테이션 증명 (RED 대체 증거) — 두 레인
| # | 변이 | 새 e2e | 기존 문자열 가드 |
|---|------|--------|------------------|
| M1 | `setTimeout(..., 0)` 로 이동 | 1 failed / 6 passed (11회 중 10회 kill, 1회 생존 — 레이스) | 23 passed (생존) |
| M2 | `setTimeout(..., 300)` 로 이동 | 1 failed / 6 passed (3/3 결정적 kill) | 23 passed (생존) |
| M3 | 대입 삭제 | 1 failed / 6 passed (kill) | 1 failed / 22 passed (kill) |

M1/M2 관측값: `window.opener -> {isNull: False, str: 'threw:SecurityError'}`.
무변이 관측값: `window.opener -> {isNull: True, str: 'null'}` — 완화는 실제로 동작한다.
매회 원복 후 `git diff --quiet components/webrtc.html` 확인. 커밋되지 않음.

## ISSUE-50 — REVIEW 완료 (APPROVE-WITH-NITS, 머지 차단 0)

- 리뷰어 워크트리: `.worktrees/review-ISSUE-50-code` (구현자 워크트리와 **분리** — 첫 명령으로
  `rev-parse --show-toplevel` 확인 후 진행. 구현자 워크트리는 리뷰 전후 모두 `b8111d4` 클린)
- 브랜치 head: `b8111d4` → **`c513179`** (리뷰 커밋 2건 + origin/main 병합, force-push 없음)
- 최종 PR diff: `tests/e2e/test_stage_window_opener_e2e.py` (+429), `docs/review_notes.md` (+277).
  **프로덕션 diff 0건 유지**, `issues.md`/`STATUS.md` 미커밋.

### 독립 뮤테이션 재측정 (사전 선언 3종, 1배치, 적응적 생성 없음)
| # | 변이 | 새 e2e | 기존 문자열 가드 |
|---|------|--------|------------------|
| M1 | `setTimeout(…, 0)` | **5/5 kill** | 23 passed — **생존** |
| M2 | `setTimeout(…, 300)` | **5/5 kill** | 23 passed — **생존** |
| M3 | 대입 삭제 | **1/1 kill** | 1 failed — kill |

11회 kill 이 전부 `test_popup_from_the_button_has_no_opener` 단일 노드에 정확히 떨어졌다(부수 피해 0).
M1/M2 관측값 `{'isNull': False, 'str': 'threw:SecurityError'}` — `String(window.opener)` 의
SecurityError 가 **두 포트가 실제로 크로스 오리진임을 브라우저가 증명한 직접 증거**다.
PR 이 보고한 M1 1/11 생존은 이번엔 재현되지 않았으나(5/5 kill) ~9% 생존율과 통계적으로 모순 없다.
**M1 을 결정적으로 만들라고 요구하지 않았다** — M1 이 생존한 런에서는 완화가 실제로 동작했고
(opener 가 진짜 null), 이 테스트는 코드 모양이 아니라 **보안 결과**를 단언하기 때문이다. 모양을
단언하게 바꾸는 것은 이 이슈가 없애려던 문자열 가드로 되돌아가는 것이다.

### 공허성 게이트 반증 (이 리뷰의 핵심 산출물)
`stage_url` 을 `about:blank` 로 돌려 크로스 오리진 내비게이션 자체를 없앤 뒤:
- **V1** (게이트 있음) → **exit 1, 게이트 발화.** `popup = <Page url='about:blank'>`
- **V2** (게이트 제거) → **exit 0, 공허한 초록.** `{'href':'about:blank','isNull':True,'str':'null'}`

즉 게이트가 없으면 스위트는 **완벽히 초록이면서 아무것도 증명하지 못한다**. 장식이 아니다.

### e2e baseline 재조정 — 회귀 0, 그리고 "선재 파손" 은 재현되지 않는다
- 브랜치 전체 e2e: **140 passed / 0 errors** (209s, exit 0). 수집 개수는 PR 보고와 정확히 일치
  (133 + 7 = 140 = 138 + 2) — pass/error 분할만 다르다.
- `test_fullscreen_e2e.py` 단독: **14 passed / 0 errors** (92s). PR 이 주장한 main 6 passed /
  8 errors 는 **재현되지 않았다.** 이 파일은 이 브랜치에서 origin/main 과 바이트 동일하므로
  단독 실행이 곧 main 실행이다.
- **결론: 이 브랜치는 e2e 회귀를 0건 도입하며, 파일로 남길 "선재 파손" 도 없다.** 아래
  `[ISSUE-50 implement]` 항목의 "선재적으로 깨져 있다" 는 서술은 근거를 과장한 것이며,
  실제 성질은 **병렬 실행 부하에 민감한 flake**(전부 `logged_in_page` 의 20s 로그인 타임아웃)다.

### 측정 (리뷰어 워크트리)
- unit: **1319 passed / 1 skipped / 140 deselected** → 병합 후 **1340 passed / 150 deselected** (84s)
- 새 e2e 플레이키니스: **7 passed × 3회, 0 flake** (병합·수정 후 3회 더, 총 **6회 연속 초록**)
- `ruff check` / `black --check`: clean (68 files). pre-commit 포맷터 교착 미발생

### 체크포인트 (11/11 통과)
checkout 0 / review 0 / figma-compliance 0(skip) / computed-styles 0(skip) / visual-diff 0(skip) /
structural-match 0(skip) / layout 0(skip) / **ui-review 0 — `UI: false` 로 정상 skip** /
test-quality 0 / **test 0 (88.1s — 문서화된 60s·exit 124 트랩은 발생하지 않았다)** / push 0.

### 리뷰어 in-PR 수정 2건 (Critical/High 0건이라 최소 범위)
| ID | 심각도 | 처리 |
|---|---|---|
| O-1 | Low | `test_no_network_interception_fakes_the_stage_document` 의 **근거가 사실과 다름** — `route(...).fulfill(...)` 응답도 **요청 URL 의 오리진**으로 커밋되므로 origin 게이트를 무력화하지 못한다(오리진은 소켓이 아니라 URL 이 정한다). 테스트는 유지하고 docstring 근거를 "fixture 계약 가드" 로 정정 |
| O-2 | Low | `test_popup_from_the_button_has_no_opener` 의 미사용 `request` 인자 제거 |

미수정(수용): streamlit MagicMock 션트(dead), `marker.count()==1`·`is_closed()` 무의미 단언,
포트 비교 중복, `_DESCRIBE_OPENER` 의 불필요한 try/catch 2개. 순삭 가능 ~19줄(nit 포함 ~36줄),
422줄 중 9% 미만 — **핵심 설계(두 실제 오리진 / 스텁 없음 / 팝업 realm 단언 / 선행 게이트)는 무수정.**

### 가드 카탈로그 결정 — 코드 전수 검증 결과 **유지**
`ALLOWED_FILES` 4개 `.py` 하드코딩(`guard_mutations.py:118-125`, `:440` 에서 거부),
`compile()` 사전검증 Python 전용(`:555`), `_run_pytest` 가 `-m 'not e2e'` 상속(`:380-390` +
`pyproject.toml:90`)해 exit 5 → ERROR(`:594-620`), `mutation` job 에 Playwright 스텝 없음
(`ci.yml:81-113`, `timeout-minutes: 10`). 네 근거 모두 사실이고, **ISSUE-58 스펙이 같은 세
블로커를 이미 문자 그대로 열거**하며 `ALLOWED_FILES` 확대를 In-scope 로 둔다.

## ISSUE-53 — PIPELINE 전 단계 통과 (implement -> review -> ship)

**결과**: GH #144 / PR #146 / squash merge `c8ccd1a`. 스위트 1268 -> **1441 passed**
(1 skipped, 167 deselected, 커버리지 94.09%). `ruff`/`black` clean, CI 4종
(Lint / Test / Guard mutation / E2E Playwright) 전부 green. main 스모크 재확인.
리베이스 후 재실행까지 포함해 검증했고, **squash 결과 트리가 리베이스 트리와 바이트 동일**
(`4ab2ce8`)임을 확인해 force-push 없이 머지했다.

**핵심 규칙 — 명시적으로 언어를 고른 클라이언트는 끌려가지 않는다**: UI 리뷰가 실제 렌더로 4종
전부 재현. `/stage/?lang=ko`(pinned) 는 control 수신 후에도 EventSource URL 이
`?lang=ko` 그대로이고 배율만 32px->44.8px. 드롭다운으로 `en` 을 고른 뷰어는 구독·표시값
둘 다 `en` 유지. 미조작 클라이언트만 `vi` 로 이동하며 드롭다운 표시값도 함께 움직인다.
같은 값 재선택도 명시적 선택으로 계수된다(`langLocked` 가 동일값 조기 반환 **앞**에 있음).

**UI 리뷰가 렌더 계측으로 잡은 High 결함 (F-1)**: `applyCaptionScale()` 이 스크롤 추종
측정 없이 `--caption-scale` 을 써서, 화면에 있던 모든 줄이 리사이즈되며 `scrollHeight` 만
튀고 이후 모든 `_lockLine()` 이 "청중이 위로 스크롤했다" 로 오독했다. 390x844/30줄 실측
57px -> 684px -> 다섯 줄 뒤 1072px, **복구 없음**. 파일 자신이 `:540-546` 에 적어 둔
"한 번 꺼지면 끝이다" 를 순서 규칙이 적용된 적 없는 경로로 우회한 형태 — RL-035 신설.
수정 후 58 -> 0 -> 0. 무대(F-2)도 같은 write 였고 무조건 `_scrollToBottom()` 으로 수정
(무대는 `isUserAtBottom` 파일 전역 금지가 유지된다).

**코드 리뷰 — 이슈 스니펫이 자기 AC 와 모순 (RL-032 신설)**: Implementation Notes 의
`payload.get("event")` 허용목록 스니펫을 문자 그대로 구현하면 `{"event":"control"}` 이
섞인 자막이 실제로 승격되어 AC 를 위반한다. 개발자가 `_ControlEnvelope`(dict 서브클래스)로
발행 주체를 표시하는 쪽을 택했고, 리뷰가 `publish_control` -> `_write_sse_event` 전 구간에
`dict()`/`{**x}`/`.copy()`/JSON 왕복이 없어 타입이 세탁되지 않음을 독립 추적으로 확인했다.
허용목록도 여전히 load-bearing(삭제 시 별도 테스트가 RED).

**뮤테이션**: 개발자 20종 선언/20 kill. 리뷰가 37종으로 재검증해 **2종 생존** — 둘 다
PR 내 수정: (1) `stage_control` 의 `room_id` 를 클라이언트 payload 로 바꿔도 105개 테스트
통과(RL-002 규칙이 주석으로만 존재. ISSUE-56 미해결 상태라 이 한 줄이 유일한 교차 룸 장벽),
(2) `langLocked = true` 를 동일값 조기 반환 뒤로 옮겨도 281개 통과. 둘 다 RL-004(freq 13).

**NFR-025 무대 조작 표면 0 유지**: 정적 계수 전부 불변(`cursor: none` 2->2, keydown 1->1,
preventDefault/stopPropagation/keyup/requestFullscreen/.focus() 전부 0->0, `<input>`/
`<select>`/`<textarea>`/tabindex 0). `test_no_interactive_controls` /
`test_no_presenter_keyboard_interference` **AST 기준 바이트 동일**. 추가로 적대적 payload
8종(`9`, `"크게"`, `null`, `-5`, `{a:1}`, `<img onerror>` 문자열, 타자기 중, session_end 후)을
실제 전달한 뒤 클릭 가능 요소 0 / `activeElement === body` / `fullscreenElement === null`
행동 가드로 확인.

**대비**: 라이브 captureStream 그라디언트를 뒤에 깔고 스크린샷 픽셀에서 샘플링해
배율 0.8/1.0/1.6 전부 `.caption-line` **4.5194:1** (RL-018 의 ISSUE-42 실패 모드 미재현 —
`.caption-column` 이 불투명 grid 형제라 캡처가 자막 아래로 래스터되지 않는다). 1.6x
320/360px 에서 한국어 복합어·70자 URL 모두 `scrollWidth == clientWidth`, 오버플로 0.

**연결 스냅샷이 load-bearing 임이 실측으로 드러났다**: control 을 연속 발행하면 재구독
간극에서 한 프레임이 유실되는데, 스냅샷이 그것을 치유한다. `set_control` 없이 발행하면
유실이 영구다 — 설계 결정 2 는 재로드 대비책일 뿐 아니라 정합성 장치다.

**체크포인트**: `implement/tests-written` 만 exit 1 — `tests/broadcast_spy.py`(RL-001 대로
두 스위트가 공유하는 헬퍼)가 `def test_*` 를 갖지 않아서다. ISSUE-45 의 `tests/wcag.py` 와
동일한 기존 kit 한계이며 이미 Discovered Issues 에 기록돼 있다. 실제 스파이는 진짜
`BroadcastManager` 를 상속해 프로덕션 fan-out 을 그대로 통과시킨다(스텁이 아니다).
나머지 implement/review/ship 체크포인트 전부 PASS. `implement/test` 는 이번엔 exit 0(91.3s).

**동시 작성 충돌 해소**: `docs/review_lessons.md` 에 origin/main(#147)과 워킹트리(#146/#149)가
서로 다른 내용을 붙여 **RL-033/RL-034 ID 가 겹쳤다**. 양쪽 내용을 모두 보존하고 이미 게시된
origin/main 쪽 ID 를 고정한 뒤 나머지를 재번호했다 — ISSUE-53 UI 리뷰 RL-033 -> **RL-035**,
ISSUE-50 에이전트가 동시에 붙인 RL-034 -> **RL-036**. RL-004 Observed-In 은 #146/#147/#149
합집합으로 병합하고 frequency 13. 커밋 `7ae425f`. **ISSUE-50 담당 에이전트는 자기 레슨이
RL-036 으로 재번호된 것을 알아야 한다.**

---


- [ISSUE-53 review] **ISSUE-66 (P2, 1d, fix)** `viewer.html` 종료 상태에 단말 플래그 도입 — RL-022 파리티. `session_end` 가 `currentLine` 만 비우고 종료 플래그를 세우지 않아 늦은 `message` 하나가 끝난 세션을 되살린다. **네 번 기록되고 한 번도 소유되지 않았던 항목**(ISSUE-46 § Out, ISSUE-53 § Out, test_plan, 두 리뷰어)을 드디어 인수. **지금은 도달 불가능한 잠복 결함** — 서버가 라이브 스트림 중간에 `session_end` 를 쓰지 않는다(`sse_broadcast.py:689-707` 이 접속 시 1건 쓰고 `write_eof`). P1 이 아닌 이유가 그것이다.
- [ISSUE-53 review] **ISSUE-67 (P2, 0.5d, fix)** 뷰어 언어 전환 시 라이브 리전 이중 쓰기 — 옛 언어 대기 문구를 먼저 읽는다. MutationObserver 레코드 실측 `['+잠시 후 시작됩니다','-한국어 확정 자막','+Sắp bắt đầu','-잠시 후 시작됩니다']` (무대는 1회, 새 언어로 올바름). 사전 존재 결함이나 **ISSUE-53 이 방아쇠를 바꿨다** — 청중의 드롭다운 조작에서 오퍼레이터의 원격 조작으로. 함정: 관측기를 **콜백 단위로 세면 고치기 전에도 초록**이다(마이크로태스크 배칭). 레코드 단위로 세야 한다.
- [ISSUE-53 review] **ISSUE-68 (P2, 0.5d, fix)** `publish_control` 이 `event` 키 없는 payload 를 `event: message` 로 조용히 강등한다 — 어느 클라이언트도 반응하지 않는 무음 유실. 오늘 프로덕션 호출자는 항상 `build_control_payload` 를 거쳐 도달 불가지만, **ISSUE-53 UI 리뷰어가 하네스를 만들다 여기 걸려 전체 실행 1회분을 썼다** — 잠복 결함의 비용이 0 이 아니라는 실측 근거.
- [ISSUE-53 review] **ISSUE-69 (P2, 1d, refactor)** `Room.update_session_languages` 의 `-> bool` 이 "기록했다" 와 "이미 같았다" 를 한 `True` 로 뭉갠다. ISSUE-53 이 필요로 한 "알릴 변화가 있었는가" 가 그 안에 없어 `_current_primary_lang`(21줄)이 auth 경로에서 **룸 행을 두 번째로 조회**한다. 동작 변경 0건의 리팩터. **가장 미끄러운 지점**: 순수 tri-state 로는 부족하다 — `input_lang` 만 바뀌고 `output_lang` 이 그대로인 경우 `written` 인데 발행은 0회여야 한다. 반환이 `previous_primary_lang` 도 날라야 오늘 동작과 동치다.
- [ISSUE-53 review] ISSUE-55/ISSUE-56 에 증분 노출 실측 표 추가(새 이슈 아님). ISSUE-53 이 붙인 것은 "다음에 여는 페이지" 가 아니라 **이미 열려 있는 화면에 실시간으로 닿는 능력**이다 — `ko`↔`vi` 토글로 30/min 제한 안에서 약 2초마다 무대 자막 컬럼을 비울 수 있고, 무대는 조작 표면이 0개이며 새로고침도 접속 스냅샷에 덮여 **현장 복구 수단이 없다**. 증분 Medium / 밑에 깔린 구멍 High. ISSUE-56 의 P2→P1 승격은 플래너가 판단을 미뤄 플래그만 세웠다(ISSUE-55 가 먼저 착지해야 의미가 있으므로).
- [ISSUE-53 review] ISSUE-54 에 4건 수정(차단 해제됨): ① `stage_control` 의 응답이 둘(`stage_control_ack` / `stage_control_error`)인데 ack 만 적혀 있었다 ② 배율 범위가 **네 번째 손복사본**이 되므로 ISSUE-53 리뷰가 출하한 `test_the_range_is_one_rule_across_the_server_and_both_templates` 를 **확장**해야 한다(새 테스트를 만들면 그 순간 대조가 갈라진다 — RL-025) ③ auth 스냅샷 형태 고정: `control` 은 `dict | null` 이고 dict 여도 **설정된 키만** 담아 `caption_scale` 이 없을 수 있다 ④ 시제/상태 갱신.
- [ISSUE-53 review] ISSUE-64 에 두 번째 독립 실측 추가 — 전체 `-m e2e` 1 프로세스에서 6 failed + 16 errors, 단독 34/34 pass. **두 리뷰가 "단독 실행" 에 대해 정반대를 보고한다**는 사실 자체가 결과의 환경 의존성 증거다. 새 이슈 아님(ISSUE-64 가 이미 소유).
- [ISSUE-53 review] record-only 2건: `_handle_stage`/`_handle_view` 의 요청당 DB 행 1회 읽기(캐시를 넣으면 ISSUE-52/53 이 세운 "페이지 로드 시점에 룸 행이 권위" 규칙에 TTL 상태가 붙는다 — 실측 근거 전에는 손대지 않는 것이 옳다); `_publish_control` 의 도달 불가 `if not room_id` 2줄(리뷰 C-4 가 "상태를 쓰는 함수의 방어 가드" 로 보고만 판정, ISSUE-69 § Out 에 기록해 재제기 방지).
- [ISSUE-53 review] 레슨 에스컬레이션 점검: Frequency ≥ 3 인 RL-001(5) / RL-004(13) / RL-018(4) **모두 담당 이슈 보유** — 각각 ISSUE-48(shipped), ISSUE-43(shipped, 가드 뮤테이션 CI 게이트) + ISSUE-58/59, ISSUE-49(shipped). 신규 예방 이슈 불필요.
- [ISSUE-53 review] **플래너가 지적한 미소유 후속**: `session_end` 를 control fan-out 에 태워 살아 있는 무대 화면이 룸 종료를 알게 하는 변경(ISSUE-53 코드 리뷰 Follow-up 4). **ISSUE-66 과 ISSUE-68 이 그 변경의 두 절반**(클라이언트 무장 / 발행 경로의 큰 소리 실패)이며, 그 이슈가 쓰이면 둘은 "잠복 정리" 에서 **선행 조건**으로 승격된다. 지금 어느 이슈도 담고 있지 않다.
- [ISSUE-53 ship] 사후 테스트 갭 점검: 변경된 소스 4개(`sse_broadcast.py`, `websocket_handler.py`, `components/stage.html`, `components/viewer.html`) 모두 대응 테스트 파일이 존재해 testgen 불필요.
- [ISSUE-48 ship] **shipped** — squash merge `972653b`, GH #140 auto-closed COMPLETED. Merged `980c063` (the post-review head that had main merged in), not the pre-review `a016061`. main 재실측: **1288 passed / 133 deselected / 94.29%** (83s), e2e **133 passed** (3m30s), `ruff check` + `black --check` clean — 브랜치 수치와 동일, ISSUE-52 와의 통합 회귀 0건.
- [ISSUE-48 ship] 스모크를 CI 에 맡기지 않고 main 에서 직접 재현했다. 룸 이름 `A홀</script><script>window.__pwned=1;</script>` 로 `render_component_html` 을 태우면 `</script>` 개수 = 템플릿과 같은 **1**, 새로 열린 `<script>` **0**, `json.loads(BOOT)` 가 payload 와 완전 일치(룸 이름 바이트 단위 보존 — 이중 이스케이프 없음), U+2028/U+2029 라운드트립 정상, 오타 플레이스홀더는 `KeyError`, `sse_broadcast._json_for_script is script_escape.json_for_script` 참. **프로브가 공허하지 않음도 확인** — 같은 입력을 옛 `json.dumps` 경로에 걸면 `</script>` 3개 + 실행 가능한 breakout 이 나온다 (RL-004). `components/stage.html` / `components/viewer.html` 은 `getUserMedia` / `RTCPeerConnection` / `/v1/realtime` / `client_secret` / `OPENAI` **0건** 유지.
- [ISSUE-48 ship] **ISSUE-49 (P1, a11y) unblocked** — 같은 `components/webrtc.html` 을 편집하기 때문에 ISSUE-48 머지까지 의도적으로 붙잡아 둔 이슈다. 머지·워크트리 정리가 끝났으므로 이제 디스패치 가능하다. (이 에이전트는 착수하지 않는다.)
- [ISSUE-48 ship] 워크트리는 ISSUE-48 용으로 **하나만** 남아 있었다 — 리뷰어가 구현자 워크트리를 재사용한 결과(리뷰 단계에서 이미 기록된 오염 사고와 같은 원인)라 리뷰어 전용 워크트리는 처음부터 존재하지 않았다. 제거 완료, 로컬·리모트 브랜치 모두 삭제.
- [ISSUE-48 ship] **로컬 e2e 하네스 결함(레포 무관)** — 이 샌드박스의 `NODE_OPTIONS` 가 존재하지 않는 `/var/folders/.../cmux-claude-node-options/restore-node-options.cjs` 를 preload 해서 Playwright 의 Node 드라이버가 즉사한다(`PlaywrightContextManager has no attribute '_playwright'`, 130 errors in 3.5s). 코드 문제가 아니며 `env -u NODE_OPTIONS uv run pytest -q -m e2e` 로 133 passed. e2e 를 로컬에서 돌리는 후속 에이전트는 이 환경변수를 걷어내야 한다.
- [ISSUE-48 ship] CHANGELOG 항목은 만들지 않았다 — 이 저장소에 `CHANGELOG.md` 가 없다(레지스트리 파일은 `issues.md` / `STATUS.md` 둘뿐). 새로 만드는 것은 ship 범위 밖이라 판단했다.
- [ISSUE-52 review] **ISSUE-55 (P1, security)** WS 인증에 비밀이 없다 — `_authenticate_client` 는 DB 에 존재하는 `{id, username}` 쌍만 확인하고 토큰·비밀번호·쿠키·Origin 을 전혀 보지 않는다. `0.0.0.0:8765` 바인딩. 기존 결함이지만 **ISSUE-52 가 그 채널에 영속 능력을 새로 붙였다**(조작된 auth 프레임 하나가 룸의 자막 채널 언어를 바꾼다). 기본안은 핸드셰이크 쿠키 재사용(`auth._verify_cookie` 재사용, BOOT payload 변경 0건). 잔여 갭: 서명 쿠키에 만료가 없다 — 여전히 아무도 소유하지 않은 항목.
- [ISSUE-52 review] **ISSUE-56 (P2, security)** `_authenticate_client` 가 룸 소유권을 검사하지 않는다 — 인증된 누구나 임의의 non-closed 룸을 잡고 그 언어 컬럼을 덮어쓸 수 있다. `admin_logic.py:342` 에 같은 규칙이 이미 있으나 WS 경로에만 없다(RL-025). **주의**: 그 문장을 글자 그대로 복사하면 안 된다 — `user_role == "operator"` 만 검사하는데 이 저장소의 오퍼레이터 계정은 실제로 `role="user"` 라 복사본은 구멍을 그대로 남긴다. deny-by-default 로 쓴다.
- [ISSUE-52 review] **ISSUE-57 (P2, performance)** `source_lang == target_lang` 자기 번역 LLM 호출 제거. ISSUE-52 Scope 가 "지금 어느 이슈도 담고 있지 않다" 고 기록해 둔 항목의 인수. 플래너 조사로 드러난 사실 두 개: 이 낭비는 **ISSUE-52 이전에도 이미 발생**하고 있었고(기존 테스트 픽스처 `:420`/`:523` 이 정확히 그 형태), 정확성 영향이 음(-)이다 — 같은 언어 "번역" 을 받은 LLM 이 문장을 재작성할 수 있어 청중이 원문이라 믿는 텍스트가 발표자의 말과 달라질 수 있다. 원안은 P3 였으나 이 저장소에 P3 사용 사례가 0건이라 우선순위 어휘를 늘리지 않고 P2 로 넣었다.
- [2026-08-31 setup] Previous sprints (ISSUE-37~42, 44~47) all shipped; their rows were rolled off. ISSUE-52 was dispatched directly by the orchestrator *before* this sprint began and is already in flight — its row starts at `implementing` so the queue does not double-dispatch it. Its own agent advances the row.
- [ISSUE-45 implement] `verify_checkpoint.py:719` classifies **every** changed `.py` under `tests/` as a test file that must define `def test_*`, so the pure-helper module `tests/wcag.py` (mandated by ISSUE-45's Implementation Notes per RL-001) fails the `tests-written` gate with "test files exist but contain no real assertions". The gate cannot express "shared test helper". Needs an issue against the kit checkpoint script.
- [ISSUE-45 implement] An `autotest.py` PostToolUse hook runs the single touched test file under the repo-wide `--cov-fail-under=50` gate, so it reports "Test failed" from single-file coverage (~20%) even when every test passes, and races its own `.coverage` file into `sqlite3.OperationalError`. Misleading, non-blocking. Coverage flags should be dropped from that hook.

- [ISSUE-50 implement] **pre-commit 포맷터 두 개가 서로를 되돌린다 (레포 설정 결함, 후속 이슈 후보 P2)** — `.pre-commit-config.yaml` 이 `ruff-format` (v0.12.9) 과 `black` (25.1.0) 을 **둘 다** 돌리는데, `assert <cond>, <long msg>` 형태에서 둘의 선호가 정반대다. ruff 는 `assert cond, (\n msg \n)`, black 은 `assert (\n cond \n), msg` 로 각각 고쳐서 두 훅이 매 실행마다 "files were modified by this hook" 으로 실패하고 **수렴하지 않는다** — 커밋이 아예 불가능해진다. ISSUE-50 에서는 해당 assert 세 곳을 두 포맷터가 동의하는 형태로 손봐 우회했지만, 다음 사람이 같은 형태를 쓰면 그대로 재발한다. 근본 해결은 포맷터를 하나로 정하는 것(둘 다 유지할 이유가 없다).
- [ISSUE-50 implement] **`verify_checkpoint.py` 의 `implement/red` 는 test-only 이슈에서 구조적으로 통과 불가** — `python3 -m pytest -q` 를 그대로 부르므로 이 레포의 `addopts = "-ra -m 'not e2e' ..."` 를 상속한다. 새 `e2e` 파일은 통째로 deselect → exit 0 → "tests passed but should fail" 로 FAIL. 게다가 프로덕션이 이미 올바른 test-only 이슈에는 "구현 전 빨간 테스트" 라는 개념 자체가 없다. `tests-written` + 뮤테이션 증명으로 대체해야 하며, 검증기가 그 경로를 표현하지 못한다. 위 ISSUE-45 의 `verify_checkpoint.py:719` 건과 같은 계열이라 하나의 kit 이슈로 묶을 수 있다.
- [ISSUE-50 implement] **`tests/e2e/test_fullscreen_e2e.py` 가 이 환경에서 선재적으로 깨져 있다 (후속 이슈 후보 P2)** — 손대지 않은 `main` 에서 단독 실행하면 **6 passed / 8 errors**, 전체 e2e 런에서는 2 errors. 전부 `logged_in_page` → `streamlit_server` 서브프로세스 fixture 에 의존하는 테스트이며 `Locator.wait_for: Timeout 20000ms` (로그인 iframe) 로 죽는다. ISSUE-50 브랜치와 무관함을 main 에서 대조 실행해 확인했다. 다만 **e2e 레인의 baseline 이 "133 passed" 가 아니라 "일부 error 포함" 이라는 뜻**이라, 후속 에이전트가 이 error 를 자기 변경 탓으로 오인할 수 있다. 안정화하거나 명시적으로 skip 처리해야 한다.
- [ISSUE-50 implement] **`guard_mutations.toml` 에 `webrtc.stage_launch#opener_sever` 를 추가하지 않았다 — 의도적이며 ISSUE-58 소유.** 러너 `ALLOWED_FILES` 가 4개 `.py` 로 하드코딩(설계상 카탈로그가 자기 범위를 못 넓히게 함), `run_catalog` 의 `compile()` 사전검증이 Python 전용, `_run_pytest` 가 `-m 'not e2e'` 를 상속해 e2e kill 테스트는 exit 5 → ERROR 오보고, `mutation` CI job 에 Playwright 설치 스텝 없음(20 guards ~9s / timeout 10m). ISSUE-58 시드 카탈로그가 이미 같은 파일의 `webrtc.stage_launch_fallback#rel_noopener` 를 담고 있으므로 자연스러운 집은 그쪽이다. **플래너가 ISSUE-58 에 시드 행을 추가해야 하며, 위 M1~M3 실측이 그 근거다.**

- [carried from stage sprint] Previous sprint ISSUE-37~42 shipped 6/6 (PRs #119, #123, #122, #127, #125, #129); final suite 1139 passed / 88 deselected / 94.16% on `f26f466`. Review artifacts committed as `91e4d15`, closing the `review_notes.md` data-loss path.
- [carried from stage sprint] ISSUE-43 (P2, RL-004 mutation CI gate) left in backlog — test infrastructure, not part of this viewer-parity sprint.
- [carried from stage sprint] `auth.get_cookie_manager()` caches a module-global `CookieManager` shared across all Streamlit sessions — a new browser session can be restored as the previously logged-in user. Pre-existing, unrelated to viewer work, still has no issue.
- [carried from stage sprint] NFR-025's perceptual half is not automatable: a 30-minute venue rehearsal with a real presenter remote (`?debug=1`, 20 presses → keydown counter 0) is a required manual release gate for the stage screen.
- [carried from stage sprint] `verify_checkpoint.py` hardcodes `timeout=60` for pytest against a ~77s suite, so the `test` gate fails unconditionally and the `red` gate reads exit 124 as a false PASS. It also invokes system `python3` (3.12) rather than `uv run` (3.11.13).
- [carried from stage sprint] pre-commit runs both `ruff-format` and `black`; they disagree on wrapped `assert cond, msg` and can deadlock. CLAUDE.md §6 names black, so `ruff-format` should be dropped.
- [carried from stage sprint] Dispatching reviewer and ui-reviewer in parallel against the *same* worktree caused a transient `black` failure and left a live mutant in the shared tree mid-measurement. Each reviewer should get its own worktree.

- [ISSUE-46 implement] `broadcast_translation_for_room` (`sse_broadcast.py:751`) publishes `{"text": primary_translated}` with **no non-empty guard**, and AWS Translate returns `""` for punctuation-only input. Both templates now absorb this client-side, but the server still fans an empty SSE frame to every viewer of every room, and the "empty final = close the line" contract now lives only in two duplicated clients. Needs a server-side issue.
- [ISSUE-46 implement] **RL-022 recurs verbatim in the viewer** and *no issue owns it*: `viewer.html`'s `session_end` handler clears `currentLine` and stops the timer but sets no terminal flag, so one late `message` frame re-enters `setState("active")` and resurrects an ended session. `stage.html` got its `sessionEnded` guard in `cc0681f`; the viewer did not. Explicitly out of ISSUE-46's scope (recorded in its § Out).
- [ISSUE-46 implement] RL-021: `_render_viewer_html` still chains five `str.replace()` calls (`sse_broadcast.py:393-399`), so an operator-controlled room name equal to `{{OUTPUT_LANGS_JSON}}` expands a later placeholder. ISSUE-44 owns the *escaping* half — confirm its scope also covers single-pass `re.sub` substitution rather than opening a duplicate.
- [ISSUE-46 implement] The kit `red` checkpoint cannot be satisfied when it is run after GREEN (it re-runs the now-passing tests and reports "tests passed but should fail"). RED was instead proven directly by checking out `main` in a throwaway worktree, layering only the new test files on the pre-fix `viewer.html`, and observing 7 unit + 5 e2e failures with value-level assertion messages.

- [iteration 3] Sprint completion: ISSUE-44/45/46 (3/3) shipped via PRs #132, #135, #133. Final suite 1193 passed / 100 deselected / 94.15% on `9079f69`.
- [iteration 3] All three team-lead agents stalled on the 600s no-progress watchdog during exploratory mutation campaigns (44 twice, 46 twice, 45 once). Implementations were complete and committed each time; the orchestrator took over review + ship directly. Mutation testing itself kept earning its cost — it surfaced the symlink-guard coverage hole (ISSUE-38 R-01), the never-attached-stream mutant (ISSUE-42 M35), and the scroll-follow latch (ISSUE-46). The failure mode is unbounded candidate generation, not the technique: pre-declare a fixed batch.
- [iteration 3] ISSUE-46's own fix introduced a defect its UI reviewer caught mid-run: `_lockLine()` measured scroll-follow AFTER writing the snapped text, so the height jump read as user scrolling and latched the credit roll off permanently with no recovery path. Fixed in both `_lockLine()` and the typewriter step, with two ordering regression tests proven RED against the pre-fix code.

- [ISSUE-47 implement step 1] **FR-082 와 NFR-029 가 반증된 기술 주장을 근거로 담고 있다.**
  `docs/requirements.md:302` (FR-082) 의 "the Streamlit component sandbox does not grant
  iframes the `display-capture` permission" 과 `:338` (NFR-029) 의 동일 주장은 위 실측으로
  거짓임이 확인됐다. issues.md ISSUE-47 의 "왜 iframe 임베드가 아니라 새 창인가" 문단도
  같은 주장을 담는다. 결론(새 창)은 그대로 두더라도 **근거 문장은 교정해야 한다** — 방치하면
  향후 다른 화면에서 "컴포넌트 iframe 은 미디어 권한을 못 받는다"는 잘못된 일반화로 번진다.
- [ISSUE-47 implement step 1] 실측 부산물로 확인된 별개 사실: `st.components.v1.html` 의
  컴포넌트 iframe 은 `sandbox` 에 `allow-same-origin` 이 있는데도 `srcdoc` 로 렌더되어
  `location.origin === "null"` (opaque origin) 이다. 이번 이슈에는 영향 없지만, 컴포넌트에서
  오리진 의존 기능(스토리지·쿠키·postMessage 오리진 검증)을 쓰려는 향후 작업에 함정이다.
  담당 이슈 없음.
- [ISSUE-47 implement step 1] 알려진 운영 갭 재확인(고치지 않음, ISSUE-47 § Out): WS(8765)/
  SSE(8766) 서버가 `app.py:258-305` 의 `st.session_state` 게이트 데몬 스레드라 컨테이너
  재시작 후 오퍼레이터가 `:8501` 을 열기 전까지 `/view/`, `/stage/` 가 죽어 있다. ISSUE-47 이
  무대 진입점을 사이드바로 끌어올리면 이 갭의 노출 빈도가 올라간다 — 여전히 담당 이슈 없음.

- [ISSUE-47 review] **코드 리뷰와 UI 리뷰가 독립적으로 같은 High 결함을 찾았다** — 무대 버튼이
  `.welcome-state` 안에 있어 첫 자막에서 `appendLine()` 과 함께 사라졌다(측정 1개 → 0개, AC4 불성립).
  19-뮤턴트 배치도 12개 e2e 도 잡지 못했다: 결함이 diff 밖(`appendLine`)에 있었기 때문. 두 리뷰어를
  **별도 워크트리**로 돌린 것이 값을 했다. → RL-027 신설.
- [ISSUE-47 review] 세션 중 표시 모드 토글이 컴포넌트 iframe `srcdoc` 을 바꿔 파이프라인을 무너뜨리는
  것을 코드 리뷰가 Streamlit 1.48.1 에서 측정. 세션 중 라디오 비활성화로 완화, 근본 해법은 ISSUE-51.
  → RL-028 신설.
- [ISSUE-47 review] 후속 이슈 4건 생성: ISSUE-48(P1 script-context XSS), ISSUE-49(P1 웰컴 화면
  WCAG AA), ISSUE-50(P2 두 오리진 opener e2e), ISSUE-51(P2 진입점 st.link_button 평가).
- [ISSUE-47 review] 레슨 에스컬레이션 점검: Frequency ≥ 3 인 RL-001(4) / RL-004(8) / RL-018(3) 모두
  담당 이슈가 있다(각각 ISSUE-48, 기존 backlog ISSUE-43, 신규 ISSUE-49) — 신규 예방 이슈 불필요.
- [ISSUE-47 ship] 사후 테스트 갭 점검: 변경된 소스 3개(`app.py`, `operator_ui.py`,
  `components/webrtc.html`) 모두 대응 테스트가 존재해 testgen 불필요.
- [ISSUE-47 all phases] pre-commit 의 `ruff-format` vs `black` 교착이 재현됐다(줄바꿈된
  `assert cond, msg`). 이전 스프린트가 이미 기록한 그대로이며 `SKIP=ruff-format` 으로 우회했다.
  CLAUDE.md §6 이 black 을 지정하므로 `ruff-format` 훅을 빼는 것이 맞다 — 여전히 담당 이슈 없음.
- [ISSUE-48 implement] script-context 이스케이퍼를 `script_escape.py` 로 추출해 뷰어/무대/오퍼레이터
  세 렌더러가 **같은 객체**를 쓴다 (RL-001 freq 4 종결 경로). `sse_broadcast` 는 `_json_for_script` /
  `_PLACEHOLDER_RE` 이름을 별칭으로 유지해 기존 테스트 10곳이 한 줄도 바뀌지 않았다. PR #143.
- [ISSUE-48 implement] 16-뮤턴트 배치 **16/16 killed**. ISSUE-47 의 교훈대로 diff 바깥의 호출부
  (`app.py` 주입, `sse_broadcast` 별칭, ISSUE-47 e2e fixture)까지 변이 대상에 포함했다.
- [ISSUE-48 implement] 테스트 계획 TC-089(`build_bootstrap_payload` 키 집합 set-equality)는 아직
  **미구현**이다 — ISSUE-53 소관으로 계획서에만 존재한다. ISSUE-48 은 payload 형태를 바꾸지 않아
  영향 없음. 현재 가장 가까운 가드는 `tests/test_operator_ui.py:474-486` 의 튜플 순회다 — 담당 이슈 없음.
- [ISSUE-48 implement] `VIEWER_BASE_URL` 에 스킴 검증이 없어 `javascript:` base URL 이
  `stage_url` / `view_url` 을 거쳐 `window.open` 까지 흐른다 (PR #137 리뷰 S-2). ISSUE-48 스펙이
  명시적으로 Out-of-scope 로 둔 항목이고 배포자 제어 환경변수라 긴급도는 낮지만, 여전히 담당 이슈가
  없다 — Medium. 후속 이슈 필요.
- [ISSUE-48 implement] `implement/red` 체크포인트가 스위트 타임아웃(exit 124)을 RED 신호로 오독한다.
  녹색 코드에서도 PASS 를 찍으므로 이 게이트는 실질 증거가 되지 못한다 — RED 는 별도로 증명했다.
  `implement/test` 게이트도 같은 60s 타임아웃으로 항상 exit 124/rc=1 이다 — 담당 이슈 없음.
- [ISSUE-47 all phases] Playwright 드라이버가 이 환경의 `NODE_OPTIONS` preload 때문에 죽는다.
  모든 e2e 실행에 `env -u NODE_OPTIONS` 가 필요했다. 새 e2e 를 쓰는 사람이 반드시 밟는 함정이라
  기록해 둔다.

- [ISSUE-48 review] R-1 (Medium, **PR 내 수정 완료**) `app.py:491` 의 `except Exception` 이 서버 로그 없이 예외를 삼켜, ISSUE-48 이 엄격 인덱싱의 근거로 내세운 "시끄럽게 실패한다" 가 실제로는 조용한 실패였다. 같은 파일의 `[Sidebar]`/`[QR]`/`[Stage]` 규약과 어긋나 있었다. 한 줄 추가로 수정. 리뷰어 서브에이전트가 독립적으로 같은 결론에 도달.
- [ISSUE-48 review] **후속 이슈 후보 (Low)** — `sse_broadcast._render_stage_html:494` 만 관대한 `values.get(k, m.group(0))` 를 쓰고 `_render_viewer_html:389` / `operator_ui.render_component_html` 는 엄격 인덱싱이다. `script_escape.PLACEHOLDER_RE` 를 공유하는 세 소비자의 실패 정책이 갈린다. 무대 템플릿에 플레이스홀더 오타가 나면 리터럴 `{{FOO}}` 가 `<script>` 안에 남아 SyntaxError 로 무대 부트스트랩이 조용히 죽는다. 보안 영향 없음(값은 재스캔되지 않음). 이 PR 이 만든 결함이 아니라 선행 결함이고, 고치면 diff 가 무대 렌더러로 번져 ISSUE-49 대기를 늘리므로 분리 권고.
- [ISSUE-48 review] Low 4건은 PR diff 를 넓히지 않기 위해 미수정 — 상세는 `docs/review_notes.md` R-2/R-4/R-5/R-6/R-7. 요지: `app.py` 전체에 대한 문자열 부재 단언 2건이 과도하게 넓고, AC5 유니크니스 grep 이 `_SKIP_PARTS` 로 `tests/` 를 통째로 건너뛰어 **테스트 안의 재구현**(이 PR 이 제거한 바로 그 형태)을 못 잡는다. 테스트 쪽 순삭 가능 ~6줄.
- [ISSUE-48 review] **프로세스 재발** — 리뷰어 서브에이전트를 내가 뮤테이션을 돌리던 것과 같은 워크트리로 보내 리뷰어의 첫 실행이 오염됐다. 리뷰어가 mtime 으로 외부 간섭을 진단하고 재실행해 결론에는 영향 없었으나, sprint_state 에 이미 등재된 "각 리뷰어에게 자기 워크트리를" 항목의 두 번째 발생이다. 뮤테이션을 돌리는 리뷰는 워크트리를 하나 더 파야 한다.
- [ISSUE-48 review] ISSUE-52 가 리뷰 도중 main 에 머지되어(7c8d4ed) `docs/review_notes.md` / `docs/review_lessons.md` 말미에서 PR #143 과 충돌했다. main 을 브랜치로 **머지**해 해소(force-push 없음). 양쪽 append 와 ISSUE-52 의 RL-001 freq 5 / RL-004 freq 9 인플레이스 수정 전부 보존 확인. 병합 후 스위트 **1288 passed / 94.29%** — ISSUE-48 과 ISSUE-52 사이 통합 충돌 없음.
- [ISSUE-48 review] `docs/review_lessons.md` 에 **RL-029** 신규 — "시끄럽게 실패하도록 설계했지만 유일한 호출부가 그 신호를 파괴하는 패턴". RL-004(freq 9) / RL-001(freq 5) 은 각각 ISSUE-43(뮤테이션 CI 게이트), 이번 ISSUE-48 이 이미 예방 이슈를 갖고 있어 신규 생성 불필요.
- [ISSUE-48 review R-3 → follow-up] **ISSUE-60** 생성 (P2, platform/fix, `issue/ISSUE-60-stage-render-strict-placeholder`).
  하나의 `PLACEHOLDER_RE` 를 쓰는 세 렌더러 중 `_render_stage_html`(`sse_broadcast.py:494`)만
  관대한 `.get(m.group(1), m.group(0))` 이고 뷰어(`:389`)·오퍼레이터(`operator_ui.py:213`)는
  엄격 `values[...]` 다. 오타 플레이스홀더가 `const CONFIG` 인라인 스크립트로 새어 들어가
  SyntaxError 로 무대 부트스트랩이 **조용히** 죽는다. 보안 영향 없음(주입 경로 아님) — Low.
  ISSUE-48 리뷰가 ISSUE-49 대기 중 diff 확대를 피하려 유예했고, 머지로 그 유예가 끝났다.
  ID 는 동시 작업 중인 다른 에이전트가 58/59 를 선점해 락 안에서 60 으로 확정했다.

- [ISSUE-43 implement] 저장소 루트의 `scripts/` 는 `.claude-kit/scripts` 를 가리키는 **심볼릭 링크**(서브모듈)이고 git 에 추적되지 않는다 — 워크트리와 CI 체크아웃에는 아예 존재하지 않는다. 이슈가 지정한 `scripts/guard_mutations.py` 경로는 쓸 수 없어 러너를 저장소 루트(`guard_mutations.py` / `guard_mutations.toml`)에 두었다. 같은 함정을 밟을 다음 이슈를 위해 기록한다.
- [ISSUE-43 implement] `mutmut` 3.4.0 은 `--paths-to-mutate` 를 제거했고 `setup.cfg` 의 `[mutmut] source_paths` 를 저장소에 **커밋해야만** 기동한다. 1회 설치·1회 실행(300초 상한) 규율대로 4초 만에 `FileNotFoundError` 로 실패했고 디버깅하지 않았다. 실측은 `docs/architecture.md` Tradeoffs 표에 기록.
- [ISSUE-43 review] **리뷰가 High 결함 1건을 실측으로 잡았다 (F-1)** — 러너가 "pytest exit 1 = kill" 을 계약으로 문서화했는데, 이 저장소의 킬 테스트는 전부 **함수 지역 `import`** 를 쓴다. 그래서 컴파일조차 안 되는 뮤턴트가 수집 오류(exit 2)가 아니라 테스트 실패(exit 1)로 나타나 **`killed` 로 오계상되고 종료 코드가 0** 이었다. RL-004 가 그것을 없애려고 만든 도구 안에서 재발한 사례. `compile()` 사전 검사 + import/문법 트레이스백 스크린으로 수정하고 회귀 테스트 2건을 RED 증명 후 추가. → RL-030 신설.
- [ISSUE-43 review] SIGTERM 은 `atexit` 를 돌리지 않아 **살아 있는 뮤턴트가 담긴 샌드박스**가 `$TMPDIR` 에 남았다(SIGINT 는 `finally` 로 정상 정리). 작업 트리는 어떤 경로에서도 오염되지 않으므로 형제 워크트리 위험은 없었지만 docstring 의 "모든 종료 경로" 주장이 거짓이었다. SIGTERM 핸들러 추가. → RL-031 신설.
- [ISSUE-43 review] 샌드박스 복사가 `.env`(OPENAI_KEY/AWS 키)와 `data/`(app.db, 브랜딩 자산)를 `$TMPDIR` 로 복제하고 있었다. `SANDBOX_IGNORE` 에 `.env`, `.env.*`, `data`, `*.db` 추가.
- [ISSUE-43 review] 리뷰어가 **400회 교차 매트릭스**(20 뮤테이션 × 20 바인딩 노드셋)로 변별력을 측정했다: 20/20 kill, **19/20 은 자기 바인딩에만** 죽는다. `resolve_asset_path#containment` 의 TOCTOU 시뮬레이션 테스트가 `#containment` 만 죽이고 `#symlink` 는 죽이지 않음을 확인 — 러너를 만족시키려 만든 가짜가 아니라 정직한 심층 방어 테스트임이 실측으로 확인됐다.
- [ISSUE-43 review] 하네스가 실제로 문다는 결정적 증거: 킬 테스트를 **ISSUE-38 당시의 형태**(심볼릭 링크 대상이 룸 바깥)로 되돌리자 `#symlink` 가드가 `SURVIVED` 로 잡혔다. 즉 이 도구가 있었다면 ISSUE-38 의 원래 결함은 리뷰어의 수작업 없이 CI 에서 걸렸다.
- [ISSUE-43 review] 후속 이슈 2건 생성: **ISSUE-58**(P2 — RL-026 의 마크업 속성 뮤테이션 클래스가 무주공산. RL-026 의 Recommended action 이 ISSUE-43 을 이름으로 지목하는데 ISSUE-43 의 Scope 는 JS/HTML 을 명시적으로 제외했고 그대로 출하됐다), **ISSUE-59**(P2 — `lint`/`test`/`e2e` job 의 `timeout-minutes` 부재, `guard_mutations.py` 의 커버리지 귀속 미결정, 허용 목록 확대 후보 판정).
- [ISSUE-43 review] 레슨 에스컬레이션 점검: Frequency ≥ 3 인 RL-001(5) / RL-004(10) / RL-018(3) 모두 담당 이슈가 있다(ISSUE-48 shipped, ISSUE-43 shipped, ISSUE-49 진행 중) — 신규 예방 이슈 불필요. **RL-004 는 8→10 이다**: ISSUE-48 리뷰와 ISSUE-43 리뷰가 같은 날 독립적으로 각각 8→9 로 올려 리베이스에서 충돌했고, 두 관측이 서로 다른 재발이므로 10 으로 병합했다.
- [ISSUE-43 review] 리뷰어 워크트리를 구현 워크트리와 **분리**한 것이 다시 값을 했다 — 리뷰어가 자기 트리에서 뮤턴트를 살려 두고 측정하는 동안 구현 트리는 무결했다(이전 스프린트가 기록한 공유 트리 오염 사고의 재발 방지).
- [ISSUE-43 ship] `docs/review_lessons.md` 의 **RL-029 번호가 충돌**했다 — ISSUE-48 리뷰(main)와 ISSUE-43 리뷰(브랜치)가 같은 날 각각 RL-029 를 새로 만들었다. main 의 RL-029 를 유지하고 이 브랜치 것을 RL-030 / RL-031 로 재번호했다. 레슨 파일이 append-only 인데 **번호는 중앙에서 배정되지 않아** 동시 리뷰마다 이 충돌이 반복된다 — 담당 이슈 없음.
- [ISSUE-43 ship] 이 환경에서 **force-push 와 `git reset --hard` 가 권한 차단**된다. `origin/main` 리베이스 후 브랜치를 올릴 수 없어, 리베이스 결과에 원격 tip 을 병합해 fast-forward 가능하게 만들었다. 이 저장소는 squash merge 라 그 병합 커밋은 main 에 남지 않았다. 리베이스를 요구하는 지시와 force-push 금지 규칙이 충돌하므로 규율을 정할 필요가 있다.
- [ISSUE-43 ship] `tests/e2e/test_admin_stage_config_e2e.py::TestStageConfigVisibleToAdmin::test_admin_room_tab_shows_stage_section_with_room_select` 가 CI 에서 **플레이키**로 1회 실패했다(20초 동안 `<h3>` 가 hidden 으로 45회 resolve). 이 PR 은 프로덕션 코드를 0줄 바꾸지 않았고 재실행에서 4/4 그린이었다. Streamlit 렌더 타이밍 의존으로 보이며 담당 이슈 없음.
- [ISSUE-43 ship] CI 비용 실측: 새 `mutation` job = **50초**(로컬 러너 8.7~9.4초 + 체크아웃/`uv sync`). `needs: test` 로 `e2e`(4분)와 병렬이라 파이프라인 총 소요는 늘지 않는다. 기존 `lint`/`test`/`e2e` job 정의는 `main` 과 바이트 단위로 동일.
- [ISSUE-43 ship] 사후 테스트 갭 점검: 변경된 소스는 `guard_mutations.py` 하나이고 `tests/test_guard_mutations.py`(15+ 테스트, 97% 커버)가 대응하므로 testgen 불필요.

- [ISSUE-50 review] **RL 번호 충돌 3회차 — 발생했고, 해소됐다 (조치 불필요, 패턴만 기록).**
  main 은 현재 `refs/heads/main` 을 `df99025` 위로 얹는 **interactive rebase 중단 상태**이고
  `docs/review_lessons.md` / `review_notes.md` / `ui_review_notes.md` 세 파일이 `UU` 다. 리뷰가
  락 안에서 계산한 번호(`RL-034`)가 그 사이 `df99025` 로 머지된 **다른 에이전트의 RL-034**
  ("A mutation run silently re-uses a stale `.pyc` …")와 충돌했다 — 충돌 파일에 RL-034 가 2개다.
  **락은 main 작업 트리 동시 쓰기만 막고, 피처 브랜치에서 할당된 뒤 나중에 머지되는 번호는 막지 못한다.**
  **이 리뷰는 남의 리베이스를 건드리지 않고 에스컬레이션만 남겼고**, 그 사이 다른 에이전트가
  `7ae425f (docs(review): ISSUE-53 리뷰·UI 리뷰 노트 및 RL-032/RL-035 기록)` 으로 정확히 그렇게
  해소했다. **최종 확인 완료**: 충돌 마커 0개, RL id 중복 0건, 이 리뷰의 레슨은
  **RL-036** 으로 재번호되어 유일하게 존재하며, RL-004 Observed-In 의 ISSUE-50 항목은 정확히 1회,
  Frequency 는 **13** (11 → 12 이 리뷰 → 13 ISSUE-53 리뷰). 유실 0건.
  **남는 교훈**: 파일 락은 main 작업 트리의 동시 쓰기만 막는다. 피처 브랜치에서 할당된 뒤 나중에
  머지되는 번호는 막지 못하므로, RL 번호는 중앙 배정이 아니면 이 충돌이 계속 재발한다 —
  이미 RL-029(1회차), RL-034(이번)로 두 번 실증됐고 여전히 담당 이슈가 없다.
- [ISSUE-50 review] **`test_fullscreen_e2e.py` 는 main 에서 깨져 있지 않다 — 위 `[ISSUE-50 implement]`
  기록을 정정한다.** 단독 14/14, 전체 140/140 초록. 실제 성질은 병렬 스프린트 부하에 민감한 flake
  (`logged_in_page` 20s 타임아웃)다. 후속 이슈는 "선재 파손 수정" 이 아니라 **"부하 민감 e2e 안정화"**
  로 잡아야 한다 (P2). 다음 에이전트가 error 를 자기 변경 탓으로 오인하지 않도록 이 정정이 중요하다.
- [ISSUE-50 review] **kit 체크포인트 결함 2건 (하나의 kit 이슈로 묶을 것).** (1)
  `verify_checkpoint.py:_find_worktree_path` 가 `git worktree list` **첫 매칭**을 돌려주므로
  ISSUE-50 의 모든 review 체크포인트가 **구현자 워크트리**를 검사했다 — 전용 리뷰 워크트리에 있는
  리뷰어 산출물은 그 게이트에 아예 보이지 않는다. (2) `review/review` 가 이슈 스코프가 아니라
  228KB append-only `review_notes.md` 전체를 정규식으로 훑어, **과거 이슈의 헤더만으로 통과**한다 —
  이번 이슈의 리뷰 누락을 구조적으로 감지할 수 없다. ISSUE-45/48 이 올린 `verify_checkpoint.py`
  건들과 같은 계열.
- [ISSUE-50 review] **후속 이슈 후보 (P2, test infra)** — `tests/e2e/` 헬퍼 중복. `_free_port()` 가
  6개 파일에 복제돼 있고(이 PR 이 6번째), `_serve()` 를 `(base_url, shutdown)` 쌍으로 처음 추출한 것이
  이 PR 이다. 그 모양을 `tests/e2e/conftest.py` 로 승격하면 TOCTOU 포트 할당과 `except Exception: pass`
  셧다운 무음 삼킴을 한 곳에서 정리할 수 있다. AC7 의 미단언 절반(서버 teardown — `shutdown()` 은
  수행하지만 단언하지 않는다)도 여기서 `ConnectionRefusedError` 프로브 ~4줄로 닫을 수 있다.
- [ISSUE-50 review] **ISSUE-58 시드 카탈로그에 2건 반영 요청.** (1) `webrtc.stage_launch#opener_sever`
  신규 행 — 위 M1/M2/M3 실측이 근거. (2) 기존 `webrtc.stage_launch_fallback#rel_noopener` 행의 검증
  레인을 `정적(태그 범위)` → **`e2e DOM 행동`** 으로 상향. 이 PR 의
  `test_popup_from_the_fallback_anchor_has_no_opener` 가 그 속성에 대한 **최초의 행동 킬 테스트**다
  (PR 이 스스로 주장하지 않은 부수 이득).
- [ISSUE-50 review] 레슨 에스컬레이션 점검: Frequency ≥ 3 은 RL-001(5) / RL-004(**13** — 이 리뷰가 11→12, ISSUE-53 리뷰가 →13) /
  RL-018(4). 각각 ISSUE-48(shipped) / ISSUE-43(shipped)+**ISSUE-58(todo)** / ISSUE-49(shipped) 가
  담당한다 — 신규 예방 이슈 불필요. 다만 **저장소 최다 빈도 레슨 RL-004(13)의 살아 있는 담당 이슈가
  ISSUE-58 하나뿐이고 아직 `todo`** 라는 점은 기록해 둔다.
- [ISSUE-50 review] `autotest.py` PostToolUse 훅 결함 2건 재현(이미 등재됨): 단일 e2e 파일을
  레포 전역 `--cov-fail-under=50` 로 돌려 "Test failed" 오보고(실제로는 `-m 'not e2e'` 로 7건 전부
  deselect, 실패 0건), 그리고 `.coverage` 경쟁으로 `sqlite3.OperationalError: table coverage_schema
  already exists`.
- [ISSUE-49 ship] **shipped** — squash merge `df99025`, GH #145 auto-closed COMPLETED. 웰컴/대기 화면 텍스트를 전부 **불투명 hex** 로 전환하고 `#viewer:fullscreen .welcome-state` 색 분기를 삭제했다. 머지된 main 재실측(`tests/wcag.py`): `.welcome-title` `#ffffff` 19.67/21.00, `.welcome-state`·`.welcome-desc` `#c8c8d2` 11.85/12.65, `.welcome-room`·`.hint`·`.welcome-rules` `#a8a8b3` 8.35/8.92 — body(`#0b0b0c`)/전체화면(`#000`) **양쪽 최저 8.35:1**. 밝기 서열(제목>본문>보조) 유지. 스위트 **1340 passed / 1 skipped / 143 deselected / 94.53%**, `ruff`·`black` clean, CI 4종(Lint/Test/E2E/Guard mutation) 전부 green.
- [ISSUE-49 ship] **구조 가드 3종**으로 backdrop 가정을 검사된 사실로 바꿨다(RL-018 Prevention): (a) backdrop 상수를 파일의 실제 `background` 선언과 대조(ISSUE-47 상수 재사용), (b) 웰컴 블록에 `rgba(255,255,255,α)` 텍스트 색 0건, (c) `:fullscreen`/`:-webkit-full-screen` 규칙이 웰컴 선택자의 `color` 를 재선언하지 못함. (c) 는 **양성 대조군**(`.caption-container` 규칙이 실제로 스캔되는지 먼저 단언)을 갖고 있어 정규식이 깨져도 공허하게 통과하지 않는다. 머지 후 실측: 라이브 fullscreen 규칙 6개, 웰컴 색 재선언 **0건**(남은 1건은 삭제를 설명하는 주석).
- [ISSUE-49 ship] `tests/wcag.py` **무수정 재사용 확인** — `git diff` 공집합, `_PAGE_BACKDROP`/`_FULLSCREEN_BACKDROP`/`_ALL_BACKDROPS` 는 ISSUE-47 상수를 import. 세 번째 계산기 없음(RL-001 freq 5). e2e 의 `_parse_css_colour` 는 computed-style 파서라 대비 계산기가 아니다. ISSUE-53 과의 충돌을 피해 `tests/wcag.py` 는 한 줄도 건드리지 않았다.
- [ISSUE-49 ship] **large-text 3:1 완화 의존 0건.** `.welcome-title` 은 `clamp(26px,3.4vw,34px)` 라 큰 텍스트로 분류될 수는 있으나 `#ffffff` 19.67:1 로 완화 없이 통과하며, 모든 단언이 4.5 기준이다. 리뷰가 `#6a6a70`(3.66/3.91 — 3:1 은 넘고 4.5 는 미달) 변이로 확인: 정적 3 + e2e 2 가 **KILL**.
- [ISSUE-49 ship] 회귀 무접촉 확인: ISSUE-47 의 `aria-describedby="stage-launch-hint"`(`webrtc.html:738`) 그대로, `.stage-launch-hint` `#a8a8b3` 은 **재사용**이지 수정이 아니다. ISSUE-48 의 `script_escape` 경로는 diff 에 **0건**이며 새 e2e 픽스처는 프로덕션 `operator_ui.render_component_html` 을 호출한다(RL-024). `viewer.html`/`stage.html` 무접촉.
- [ISSUE-49 review] **두 리뷰어가 각자 워크트리에서 돌았다 — 검증 완료.** `review-ISSUE-49-code` / `review-ISSUE-49-ui` 를 dispatch **전에** 만들고 inode 상이 + 쓰기 격리 프로브(한쪽에 파일을 만들면 나머지 둘에 안 보임)로 확인했다. 두 리뷰어 모두 종료 시 간섭 없음을 독립 보고. 지난 두 번의 사고(ISSUE-48: 리뷰어를 구현자 트리로, ISSUE-42: 측정 중 살아있는 변이)가 재발하지 않았다. **지시만으로는 부족하고 사전 검증이 필요하다**는 것이 확인된 절차다.
- [ISSUE-49 review] 코드 리뷰 **APPROVE-WITH-NITS** (Critical/High 0, PR 내 수정 0), UI 리뷰 **Approve**. UI 리뷰어는 실제 브라우저에서 **진짜 fullscreen** 에 진입해 backdrop 을 가정하지 않고 `getComputedStyle` 로 관측했고, 7개 노드 전부 전체화면 진입 전후 **바이트 동일한 computed color** 를 확인했다 — 삭제된 분기가 관측 가능한 차이를 만들지 않는다는 증명이다.
- [ISSUE-49 review] Medium 2 / Low 3 은 전부 **테스트 강도**에 대한 것이고 출하 코드는 규격을 만족한다 → ISSUE-62 로 이관. F-1: fullscreen 가드가 `if "welcome" not in selector` 라 `#viewer:fullscreen .hint{color:rgba(255,255,255,.4)}` 가 **정적 가드 생존**(e2e 가 잡고 CI 가 e2e 를 게이트로 돌려 그물은 유지). F-2: **새 웰컴 규칙 `.welcome-note` 는 정적·e2e 둘 다 생존** — 양쪽 다 손으로 적은 튜플이라서다. **RL-018 의 재발 벡터 그 자체**이며 직전 ISSUE-45 가 이미 정답 형태(`test_viewer_page.py:1145`, 스타일시트에서 집합을 파생)를 출하해 두었다. F-3: 두 계층 모두 `color` 만 봐서 `opacity` 로 되살린 분기가 안 보인다. F-4: 정적 파서는 규칙의 **첫** `color` 를 읽는데 CSS 는 **마지막** 을 적용 — 중복 후행 `color:#55555c`(2.66:1)가 정적 전량 생존, e2e 만 잡는다.
- [ISSUE-49 review] **RL-034 신규(뮤테이션 도구 자체의 함정)**: 오라클 `4.5`→`3.0` 변이는 **바이트 길이가 같고**, 같은 초 안에 복원하면 `(mtime_seconds, size)` 가 pytest 가 다시 쓴 `.pyc` 헤더와 일치해 CPython 이 캐시를 유효로 판정한다 — 이후 실행이 전부 **약화된 바이트코드**로 돌았다. 리뷰어가 "산술은 3.80 인데 테스트는 PASS" 증상으로 감지, pyc 헤더를 뜯어 진단하고 `PYTHONDONTWRITEBYTECODE=1` + 캐시 퍼지로 10종 전량 재실행했다. **뮤테이션을 돌리는 모든 후속 작업이 밟을 함정이다.**
- [ISSUE-49 review] **RL 번호 충돌을 사전 회피했다.** 내 리뷰어가 RL-032/RL-033 을 썼는데 동시 진행 중이던 ISSUE-53 도 별개의 RL-032 를 쓰고 있었다(양쪽 다 미커밋). RL-032 를 **양보**하고 내 것을 RL-033/RL-034 로 재번호해 어느 쪽이 먼저 머지돼도 충돌이 없게 했다. 결과 확인: 머지된 main 은 RL-033/RL-034(내 것) + RL-032 공백, 이후 다른 에이전트들이 RL-032/RL-035/RL-036 을 정상 점유. **충돌 0건.** 다만 RL-004 는 양쪽이 독립적으로 10→11 로 올렸다 — 두 개의 서로 다른 사례이므로 **나중에 머지하는 쪽이 12 로 조정하고 Observed-In 을 둘 다 보존해야 한다.**
- [ISSUE-49 ship] 후속 이슈 5건 생성 — **ID 61~65 를 락 안에서 스텁으로 선점한 뒤** planner 가 본문을 채웠다(동시 에이전트가 58/59/60 을 가로챈 전례 때문에 예약-후-작성으로 바꿨다). ISSUE-61(P1 `.qr-code-caption` 3.95:1), ISSUE-62(P2 웰컴 가드 파생형 전환, **Depends-On ISSUE-53** — `tests/wcag.py` 머지 순서), ISSUE-63(P2 webrtc.html 잔여 표면), ISSUE-64(P2 admin e2e 플레이키), ISSUE-65(P2 pre-commit 포매터 충돌).
- [ISSUE-49 ship] **리뷰 노트의 수치 오류를 planner 가 잡았다** — `#595959 = 4.54:1` 은 오기이며 실제로는 **7.00:1**, 4.54:1 인 값은 `#767676` 이다(내가 재검산해 확인). 커밋된 `review_notes.md` 는 append-only 규율상 고쳐 쓰지 않았고, 대신 ISSUE-61 이 두 값을 모두 기록하고 "리뷰 노트의 숫자를 그대로 베끼지 말 것" 을 AC 로 못박았다.
- [ISSUE-49 ship] **ISSUE-63 은 "나머지 감사" 가 아니라 이미 측정된 미달 4건이다**(planner 재계산, 내가 검증): `.caption-line.original` rgba(…,0.38) **3.53/3.39**, `.caption-line.stable` rgba(…,0.40) **3.80/3.66**, 그리고 `.fs-font-controls{opacity:0.6}`(`webrtc.html:366`)이 자식 알파를 **곱해** `.fs-font-label` 실효 0.36 → **3.27/3.14**, `.fs-font-size` 실효 0.42 → **4.04/3.94**. opacity 곱셈은 정확히 F-3 의 사각지대다 — `color` 만 보는 가드는 원리적으로 못 잡는다. 같은 자막 역할을 `viewer.html` 은 이미 4.52:1 로 출하 중이라 **페이지 간 드리프트**(RL-001 계열)지 새 디자인 문제가 아니다. webrtc.html 은 backdrop 이 4종 이상(`#0b0b0c`, `#000`, 설정 패널 `#0e0e10`, QR 카드 `#ffffff`)이라 ISSUE-49 의 2-backdrop 상수쌍을 그대로 쓸 수 없다.
- [ISSUE-49 ship] 사후 테스트 갭 점검: 변경된 소스는 `components/webrtc.html` 하나뿐이고 정적(`tests/test_webrtc_stage_launch.py`) + e2e(`tests/e2e/test_operator_welcome_contrast_e2e.py`) 대응 테스트가 모두 존재 → testgen 불필요.
- [ISSUE-49 ship] 체크포인트: review 10종(checkout/review/figma-compliance/computed-styles/visual-diff/structural-match/layout/ui-review/test-quality/push) 전부 PASS. **`review/test` 만 exit 124** — 스크립트 60초 타임아웃 대 실제 스위트 ~88~101초이며 테스트 실패가 아니다(직접 더 긴 예산으로 1340 passed 확인, CI Test job 도 2m12s green). 지시받은 대로 `implement/red` 는 신뢰하지 않고 RED 를 별도 증명했다 — 구현자가 pre-fix 파일에 새 테스트를 걸어 정적 **13 failed**(`.hint` 3.80:1, `.welcome-rules` 3.80:1, `.welcome-room` `#000` 4.43:1), e2e **5 failed** 를 값과 함께 확보했다.
- [ISSUE-49 ship] 뮤테이션: 구현자 **12종 사전 선언 1배치 → 11 kill / 1 생존**, 리뷰어가 독립적으로 **10종 재실행**. 생존한 M8(오라클 `>=4.5`→`>=3.0`)은 리뷰어가 **구조적으로 equivalent** 하다고 판정 — 어떤 테스트도 다른 테스트의 오라클을 관측하지 않으므로 원리적으로 kill 불가이며, 구현자가 댄 "값이 8.35 라 무해" 는 값 의존적이라 틀린 근거다. 미커버로 남긴 것: 브라우저 픽셀 occlusion 샘플링(웰컴 화면 위에 재생 중인 `<video>`/`<canvas>` 가 없어 RL-018 두 번째 사례의 조건 불성립), diff 밖 `viewer.html`/`stage.html` 대비(ISSUE-40/45 소관), `st.radio` 위젯(Streamlit 스타일링 → ISSUE-63).
- [ISSUE-49 ship] 워크트리 4개(구현 1 + 리뷰 2 + 스모크 1) 전부 제거, 로컬·리모트 브랜치 삭제 완료. ISSUE-50/ISSUE-53 워크트리는 다른 에이전트 소유라 손대지 않았다.
- [ISSUE-49 ship] **main 루트 작업트리 주의** — 머지 시점에 ISSUE-53 에이전트의 **미커밋** `docs/review_notes.md`/`docs/review_lessons.md` 편집이 main 루트에 떠 있었다. 같은 두 파일을 내 PR 이 바꿨으므로 main 루트에서 `git pull` 을 하지 않았고(그쪽 작업을 파괴한다) 스모크는 별도 워크트리에서 돌렸다. ISSUE-53 이 pull 할 때 두 파일에서 충돌이 나며, 그때 RL-004 를 12 로 조정해야 한다.
- [ISSUE-49 ship] 기존 플레이키 재확인: `tests/e2e/test_admin_stage_config_e2e.py` 는 **base `ee409e7` 에서도** 5 failed / 17 errors(단독 실행 6 failed)로 이 PR 과 무관하다. planner 가 추정 원인을 하나 더 좁혔다 — `tests/e2e/conftest.py:115 logged_in_page` 가 로그인 폼이 안 보이면 로그인을 건너뛰면서 **어느 계정으로 들어왔는지 확인하지 않아**, 앞선 테스트가 남긴 admin 세션이 프로세스 전역 `CookieManager` 에 남으면 caption iframe 이 없는 admin 대시보드가 떠서 20s `wait_for` 가 픽스처 error 로 터진다. → ISSUE-64.
- [ISSUE-49 ship, 후속 확인] **RL-004 조정은 하류에서 정상 처리됐다.** ISSUE-53(PR #146)이 내 머지 직후 `c8ccd1a` 로 들어오면서 두 파일 충돌을 해소했고, 결과적으로 origin/main 의 RL-004 Frequency 는 **13**, Observed-In 에 PR #141/#142/#146/#147/#149 가 모두 남았다 — 내 PR #147 사례가 삼켜지지 않았다. RL-018 은 **4**, 내 `review_notes.md`/`ui_review_notes.md` 항목도 그대로다. **RL-032 양보 전략이 끝까지 유효했다**: 최종 main 은 RL-030~RL-036 이 구멍·중복 없이 이어지고 RL-032=ISSUE-53, RL-033/034=ISSUE-49, RL-035/036=이후 에이전트로 정확히 나뉘었다. ISSUE-53 머지 후에도 웰컴 색값(`#a8a8b3` 등)은 무손상이다.

## Escalations
**ISSUE-50 리뷰발 활성 에스컬레이션: 없음.** 리뷰 도중 main 에서 관측된 미해결 rebase +
RL-034 번호 충돌은 다른 에이전트가 `7ae425f` 로 해소했고, 이 리뷰가 사후 검증했다
(마커 0 / 중복 0 / 유실 0, 이 리뷰의 레슨은 RL-036). 상세는 Discovered Issues 참조.

없음 — ISSUE-47 은 shipped. 다만 **수동 릴리스 게이트가 하나 남아 있다**: 무대 창이 실제로 탭이
아니라 별도 창으로 열려 보조 디스플레이로 옮겨지는지(NFR-029)는 헤드리스에서 검증할 수 없다.
`popup=yes` 를 넣었지만 브라우저별 실동작은 `:8501` 에서 육안 확인이 필요하고, ISSUE-51 이 그
측정을 산출물로 요구한다. 표시 모드 `st.radio` 위젯 자체의 렌더(좁은 사이드바에서의 `horizontal`
레이아웃·대비·포커스 링)도 UI 리뷰가 미검증으로 남긴 갭이다.
