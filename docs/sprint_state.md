# Sprint State

## Meta
- Started: 2026-09-06
- Iteration: 0 / 20
- Parallel: 3
- Status: running
- Scope: 사용자가 지정한 4건 — 백로그 전체가 아니다. 다른 이슈는 이 스프린트에서 건드리지 않는다.

## Issue Progress
| Issue | Status | Attempts | Last Error | Phase |
|-------|--------|----------|------------|-------|
| ISSUE-65 | done | 0 | — | shipped |
| ISSUE-55 | active | 0 | — | backlog |
| ISSUE-61 | active | 0 | — | backlog |
| ISSUE-64 | active | 0 | — | backlog |

> 세 행은 ISSUE-65 가 `748ea8a` 로 머지되고 main CI 4종이 green 인 것을 확인한 뒤 추가했다
> (iteration 1 완료 시점). 큐 스크립트는 이 표의 행만 후보로 삼으므로, 사용자가 지정하지
> 않은 나머지 백로그 이슈는 선택되지 않는다.

### 세 이슈의 파일 경계 — 병렬 3 이 안전한 근거

| 이슈 | 만지는 파일 |
|---|---|
| ISSUE-55 | `websocket_handler.py`, `auth.py`(재사용만), `tests/test_websocket_auth.py` 외 하네스 4곳, `docs/architecture.md`, `.env.example` |
| ISSUE-61 | `components/webrtc.html` (CSS 한 줄), `tests/test_webrtc_stage_launch.py` |
| ISSUE-64 | `tests/e2e/conftest.py`, `tests/e2e/test_admin_stage_config_e2e.py` |

교집합 0. 다만 **ISSUE-55 와 ISSUE-64 는 둘 다 인증/세션을 건드린다** — 55 는 WS 인증에
쿠키 검증을 추가하고, 64 는 Streamlit 로그인 하네스를 결정적으로 만든다. 코드 경로는
다르지만(WS 스레드 vs Playwright e2e), **ISSUE-55 가 머지되면 e2e 가 새 인증 게이트를
지나야 한다.** 어느 쪽이 먼저 머지되든 뒤에 오는 쪽이 rebase 후 e2e 전체를 다시 돌린다.

## 실행 순서 (오케스트레이터 결정)

**ISSUE-65 를 단독으로 먼저, 그 다음 나머지 셋을 병렬 3.**

ISSUE-65 는 P2 이고 나머지 셋은 P1 이라 우선순위 정렬만 따르면 마지막에 온다. 그것을
뒤집는 이유는 `issues.md:4397` 이 명시한다 — *"다른 네 이슈(ISSUE-61~64)와 파일이 전혀
겹치지 않는다. 이것을 먼저 넣으면 나머지 넷의 구현자가 `SKIP=` 을 쓰지 않아도 된다."*

`ruff-format` ↔ `black` 교착은 직전 세션에서만 **네 번** 커밋을 막았고, 그때마다
`SKIP=ruff-format` 으로 우회했다. ISSUE-55/61/64 셋 다 새 테스트를 많이 쓰므로 같은
교착에 다시 걸린다. 65 를 먼저 닫으면 그 비용이 세 번 사라진다.

코드 의존은 없다 — 순서만 정한 것이고, `Depends-On` 필드는 건드리지 않았다.

## 이 스프린트 시작 시점의 사실

- base: `3a16837` (main, CI 4종 green — Lint / Test / E2E Playwright / Guard mutation)
- 워크트리 0개, `git status` 는 세션 시작부터 있던 미추적 항목만
- 직전 스프린트 기록은 `docs/sprint_state_archive_2026-08-31.md` 로 옮겼다 (476줄)

### ⚠ 이슈 본문의 `docs/sprint_state.md:NNN` 줄 번호는 전부 낡았다

ISSUE-64 와 ISSUE-65 본문이 `docs/sprint_state.md:153`, `:156`, `:157`, `:158`,
`:163`, `:166`, `:207-209` 를 인용하는데, 그 파일은 그 뒤로 계속 자라 줄이 밀렸고
지금은 아카이브로 옮겨졌다. **줄 번호로 찾지 말고 아카이브에서 문자열로 grep 한다.**

| 이슈가 인용한 것 | 실제 위치 (아카이브 기준) | 검색 문자열 |
|---|---|---|
| 포매터 교착의 원인·결론 | `:300`, `:310`, `:351-353` | `ruff-format` |
| "CLAUDE.md §6 이 black 을 지정" 결론 | `:353` | `여전히 담당 이슈 없음` |
| `CookieManager` 프로세스 전역 싱글톤 | `:307` | `직전 로그인 사용자로 복원` |
| `logged_in_page` 가 계정을 확인하지 않는다 | `:464` | `어느 계정으로 들어왔는지 확인하지 않아` |
| e2e baseline 5 failed / 17 errors | `:464` | `에서도` |
| `test_fullscreen_e2e.py` 6 passed / 8 errors | `:302` | `test_fullscreen_e2e.py` |
| ISSUE-50 리뷰의 정정 (선재 파손 아님, 부하 민감 flake) | 아카이브 말미 | `main 에서 깨져 있지 않다` |

`:302` 와 그 뒤 ISSUE-50 리뷰의 **정정**을 반드시 같이 읽어야 한다 — 앞의 기록은
`test_fullscreen_e2e.py` 를 "선재 파손" 으로 적었고 뒤의 리뷰가 그것을 **부하 민감
flake** 로 정정했다. ISSUE-64 는 뒤의 판정 위에 서야 한다.

## Discovered Issues

- [ISSUE-65 implement] **`.githooks/pre-commit` 규칙 훅이 이 저장소에 존재하지 않는다.** ISSUE-65 본문은
  `WATCH_PATTERNS` 훅이 `CLAUDE.md` 동반 수정 없는 커밋을 거부한다고 적고 "이 훅이 의도대로 동작하는 첫
  사례" 라고 했지만, 사실이 아니다. `ls .githooks` 없음 / `git log --all -- .githooks` 비어 있음(한 번도
  추적된 적 없음) / `core.hooksPath` 미설정 / `.git/hooks/pre-commit` 은 pre-commit **프레임워크** 훅.
  실증: 커밋 `16b5e5b` 이 `.pre-commit-config.yaml` 과 `pyproject.toml`(둘 다 WATCH_PATTERNS 대상)을
  `CLAUDE.md` 없이 바꿨는데 **통과했다.** 즉 현재 이 규칙은 아무것도 강제하지 않는다. 훅 생성은 향후 모든
  커밋을 게이팅하므로 별도 이슈로 분리 — ISSUE-65 범위에서 의도적으로 제외했다. (후속 이슈 후보)
- [ISSUE-65 implement] **`implement/test` 체크포인트가 이 저장소에서 구조적으로 통과 불가능하다.**
  `scripts/verify_checkpoint.py:827` 이 전체 스위트를 60s 하드 타임아웃으로 돌리는데 실측 **93s** 다
  (내 변경 제외, `--ignore` 로 확인). exit 124(타임아웃)를 "pytest failed" 로 보고한다. 같은 결함이
  `red` 체크포인트(`:688`)에도 있어 **RED 가 타임아웃 덕분에 거짓 PASS** 로 나온다 — 테스트가 실제로
  실패했는지 구분하지 못한다. 실제 스위트는 green(1475 passed / 94.35%). 킷 쪽 결함. (후속 이슈 후보)
- [ISSUE-65 implement] `ruff.toml` 과 `.prettierrc.json` 이 main 작업본에서 **깨진 심볼릭 링크**다
  (`.claude-kit/linters/` 가 비어 있음). 추적되지 않아 워크트리/CI 에는 없지만, 로컬 `ruff` 설정 탐색이
  CI 와 달라질 수 있다. CI 가 `--config pyproject.toml` 을 명시하는 이유로 보인다. (후속 이슈 후보)
- [ISSUE-65 implement] ISSUE-65 가 머지되면 `issues.md:2654, 2816, 3115, 3249, 3355, 3451, 3537, 3622,
  3818, 3920` 의 "줄바꿈된 `assert cond, msg` 를 피하라" 회피 지침과
  `tests/e2e/test_room_id_e2e.py:31` 의 소스 주석이 **모두 무효**가 된다. 동시 편집 중이라 이 PR 에서는
  손대지 않았다. 다음 스프린트가 이 지침을 복사하지 않도록 정리 필요.
- [ISSUE-65 review] **`pyyaml` 이 선언되지 않은 채 import 된다** (F-2).
  `tests/test_precommit_formatter_policy.py:34` 가 `import yaml` 하지만 `pyproject.toml` 의
  `[project].dependencies` 에도 `[dependency-groups].dev` 에도 없다 — `pre-commit -> pyyaml` 전이
  의존으로만 들어온다(`uv.lock` 확인). 지금 CI 는 green 이지만 `pre-commit` 이 dev 의존에서 빠지면 이
  모듈이 **수집 단계에서** 죽어 파일 전체가 red 가 된다. 수정은 `uv add --dev pyyaml` 한 줄이나
  `uv.lock` 을 건드리므로 리뷰 중 적용하지 않았다. (후속 이슈 후보)
- [ISSUE-65 review] **가드가 훅과 다른 바이너리를 잰다** (F-5, RL-038). `_tool()` 은 venv 의
  black/ruff 를 해석하는데 dev 핀이 `black>=25.1.0` / `ruff>=0.12.9` 로 **상한이 없다.** 반면 훅은
  `.pre-commit-config.yaml` 의 pinned `rev: 25.1.0` / `rev: v0.12.9` 를 격리 환경에서 돌린다. 지금은
  일치하지만 다음 `uv lock --upgrade` 에서 어긋날 수 있고, 그러면 카나리아는 **초록인 채로** 훅이
  실행하지도 않는 포매터를 설명하게 된다. (후속 이슈 후보)
- [ISSUE-65 review] `test_ruff_lint_hook_survives_with_fix` 가 훅 id 를 리터럴 `"ruff"` 로 고정한다
  (F-3). pre-commit 이 이를 `ruff (legacy alias)` 로 보고하며 상류의 현재 id 는 `ruff-check` 다.
  향후 정당한 `ruff-check` 이관이 이 가드를 오탐으로 깨뜨린다. 실패는 요란하고 자명해 위험도는 낮다.
- [ISSUE-65 review] **`docs/review_lessons.md` 에 `Severity` 필드가 아예 없다.** 팀리드 자기점검 규칙은
  "Frequency >= 3 이고 Severity 가 Critical/High" 인 패턴을 예방 이슈로 올리라고 하는데, 36개 항목 중
  Severity 를 가진 것이 하나도 없어 이 규칙이 **평가 자체가 불가능**하다. RL-004(freq 13),
  RL-001(freq 5), RL-018(freq 4) 이 빈도 임계값은 넘는다. 추측으로 이슈를 만들지 않고 보고만 한다.

## Escalations

- [ISSUE-65 implement] `implement/test` 체크포인트가 exit 1 로 남았다 — 단, 원인은 위 Discovered Issues
  의 60s 타임아웃이며 **변경과 무관**하다(변경 없이도 93s 로 초과). 코드 기준으로는 통과: 전체 스위트
  1475 passed / 1 skipped / coverage 94.35% (게이트 60% 대비). 사람 확인 후 킷 체크포인트 수정 필요.
- [ISSUE-65 review] `review/test` 체크포인트도 **같은 결함으로 exit 124** 를 냈다 —
  `scripts/verify_checkpoint.py:815-828` 의 하드코딩된 `timeout=60`. 리뷰 워크트리 실측 **114.02s**
  (CI 동일 커맨드 `uv run pytest -m 'not e2e' --cov=. --cov-fail-under=60`) 로 1475 passed /
  1 skipped / 198 deselected / coverage 94.35% / **exit 0**. 즉 코드 결함이 아니다. 이 단계는 추가로
  프로젝트 venv 가 아니라 맨 `python3 -m pytest` 를 호출하는 두 번째 결함도 있다. implement/red,
  implement/test, review/test 세 단계가 같은 원인으로 신뢰 불가 — 킷 이슈로 묶어 처리 필요.

## Ship 결과 — ISSUE-65 (2026-09-06)

- **머지**: PR #154 squash merge → main `748ea8a`. GH #153 `CLOSED` (COMPLETED).
  머지 판단은 `sprint_queue.py ship-merge-decision` 이 **이 저장소 스크립트에 없어서**
  (서브커맨드는 `next-action` / `validate` 둘뿐) PR 상태로 직접 확인했다 — `state=OPEN`,
  `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`, 4/4 CI green → 재머지 위험 없음.
- **체크포인트 4/4 통과**: `checks` PASS · `merge` PASS · `cleanup` PASS · `smoke` PASS(201s).
  `ship/smoke` 는 `verify_gates.py` 를 거치므로 unit 게이트가 99.2s 를 다 쓰고도 통과했다 —
  implement/test · review/test 를 망가뜨린 하드코딩 `timeout=60` 결함이 **이 단계에는 없다.**
- **머지 후 main `748ea8a` 실측** (AC 검증):
  - `grep -c ruff-format .pre-commit-config.yaml` → **0**, `- id: ruff` → **정확히 1회**
  - `uv run pre-commit run --all-files` **2회 연속** → 둘 다 exit 0, 전 훅 Passed,
    수정 파일 **0** (추적 파일 diff 집합이 실행 전후 동일). **AC 1 충족**
  - `uv run black --check .` exit 0 (97 files unchanged) / `uv run ruff check .` exit 0
  - `uv run pytest -q` exit 0 — **1472 passed / 4 skipped / 198 deselected**, coverage
    **94.35%**, 96.04s
  - `uv run pytest tests/test_precommit_formatter_policy.py -q` exit 0 — **6 passed**, 0.12s
- **skipped 1 → 4 는 회귀가 아니다.** 늘어난 3건은 `tests/test_webrtc_operator_controls.py:115`
  로, `3a16837`(#152) 이 소유권 경계 가드를 **브랜치 전용**으로 바꾸면서 main 에서는 설계상
  skip 된다("HEAD is the merge base"). 브랜치 실측 1475 passed 와 main 실측 1472 passed + 3 skip
  은 같은 집합이다.
- **정리**: 워크트리 제거 + 로컬 브랜치 삭제 완료, 원격 `issue/ISSUE-65-todo` 는 저장소 설정이
  `delete_branch_on_merge=false` 라 머지가 지우지 않아 수동 삭제했다.
- **레지스트리**: `issues.md` ISSUE-65 `doing`→`shipped`(이 저장소의 완료 상태는 `done` 이 아니라
  `shipped` 다 — 기존 53건 전례이자 `sprint_queue.py:201 resolved_phases={"shipped"}`),
  `STATUS.md` in-flight 블록을 shipped 로 전환 + 44행 open 목록에서 ISSUE-65 제거,
  `CHANGELOG.md` **신규 생성**(저장소에 없었다) 후 항목 추가.

## ⚠ "이 구문을 피하라" 회피 지침은 이제 전부 무효다 (다음 스프린트가 복사하지 말 것)

**main `748ea8a` 부로 `ruff-format` 훅은 없다. 줄바꿈된 `assert <cond>, <long msg>` 를
그냥 쓰면 된다. `SKIP=ruff-format` 도, 짧은 assert 메시지도, 회피용 재작성도 더는 필요 없다.**

네 스프린트 동안 이 교착이 열려 있는 채로 각 이슈 본문이 저마다 회피 지침을 복사해 날랐고,
그 지침들이 지금 그대로 남아 있다. **이슈 본문은 동시 편집 중이라 이번 PR 에서 손대지 않았다.**
다음에 이 이슈들을 열어 구현하는 사람은 본문의 그 줄을 **무시**하고, 가능하면 지운다.

### 실제 분포 — 세 번 세었고 세 번 다 달랐다. 줄 번호로 찾지 말 것

| 센 주체 | 보고한 건수 |
|---|---|
| ISSUE-65 본문 (`:4362`) | 10 |
| implement 단계 | 10 (본문을 그대로 인용) |
| review 단계 | 14 |
| **실측 (머지 후 팀리드)** | **19** |

ISSUE-65 본문이 못박은 `issues.md:2654, 2816, 3115, 3249, 3355, 3451, 3537, 3622, 3818, 3920`
는 **이미 전부 밀렸다** — 그 열 개 중 지금 그 줄에 실제로 지침이 있는 것은 하나도 없다.
동시 편집으로 계속 밀리므로 **줄 번호를 다시 적지 않는다.** 대신 이렇게 찾는다:

```bash
grep -n "ruff-format" issues.md   # ISSUE-65 자기 본문(### ISSUE-65 ~ #### Rollback)은 제외하고 본다
```

두 종류가 섞여 있고 처리가 다르다:

- **무조건형 15건** — "`assert cond, (msg)` 레이아웃을 피한다" / "`SKIP=ruff-format` 으로 우회한다".
  지금은 **그냥 틀린 지시**다. 발견 즉시 지운다. (`- 로컬 트랩` 및 `- .pre-commit-config.yaml 의`
  로 시작하는 줄들, 그리고 `pyproject.toml 편집 후 … 한 번 돌려 확인한다` 변형 1건 — 이 변형은
  review 단계 집계에서 빠졌던 것이다.)
- **조건형 4건** — "ISSUE-65 가 인수 — **아직 열려 있으면** …" 형태. 조건이 이미 거짓이 되어
  스스로 해소된다. 무해하므로 급히 고칠 필요는 없다.

### 소스 주석 1건도 같이 무효

`tests/e2e/test_room_id_e2e.py:31` — "메시지를 짧게 두어 ruff-format/black 의 줄바꿈 충돌을
피한다". 이 회피가 **소스 주석으로 굳은** 사례이고, ISSUE-65 가 문제 삼은 "설정 결함이 테스트
코드의 작성 방식을 바꾼다" 의 직접 증거다. 그 파일을 다음에 만질 때 주석을 지운다.
(review 단계 F-4 로도 기록됨, Low.)

### 왜 이 절이 여기 있는가

ISSUE-65 본문 스스로가 남긴 교훈이다 — 원인·해법이 stage 스프린트에서 이미 나왔는데
(`docs/sprint_state_archive_2026-08-31.md:353`) **담당 이슈가 없어** 네 스프린트를 표류했고,
그 사이 회피 지침만 증식했다. 지침을 지우지 않으면 교착이 사라진 뒤에도 사람들이 계속
회피 코드를 쓴다 — 비용은 남고 이유는 사라진 상태가 가장 나쁘다.
