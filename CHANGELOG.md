# Changelog

이 저장소의 주목할 만한 변경을 기록한다. 릴리스 태그를 쓰지 않으므로 **머지된 PR 단위**로 적는다.

## Unreleased

### 2026-10-11

#### Fixed

- **ALB 라우팅에 `/stage/*` 와 `/branding/*` 가 빠져 있었다** — ISSUE-70 · PR #157 · GH #156.

  `aws-alb-setup.md` 는 2026-08-09(#105)에 8766 경로를 `/view/*`, `/stream/*` 둘로 적었고,
  `/branding/{room_id}/{filename}`(#120, ISSUE-38)과 `/stage/{room_id}`(#121, ISSUE-40)은
  2026-08-23 에 추가됐지만 문서도 ALB 규칙도 갱신되지 않았다. 그 결과 **무대 합성 뷰
  트랙(ISSUE-37~42, 6개 전부 shipped)과 룸 로고가 프로덕션에서 도달 불가**였다.

  - **이 결함은 에러로 드러나지 않는다.** 미등록 경로는 ALB 기본 규칙으로 8501 로 가고
    Streamlit 이 HTTP **200 + 앱 셸 HTML**(1522B, `/` 와 동일)을 돌려준다. 404 도 500 도
    로그도 없다 — 무대 화면은 빈 화면, 로고는 깨진 이미지다. 상태 코드만 보는
    헬스체크·스모크 테스트는 영원히 통과한다.
  - 그래서 §6 검증 절차를 "없는 룸으로 **응답 주체를 식별**" 하는 형태로 바꿨다. 전 경로가
    200 에 같은 크기면 그것이 곧 증상이다.
  - `tests/test_alb_routing_parity.py`(5 tests)가 재발을 막는다. 라우트를 소스 regex 로
    긁지 않고 `build_sse_app` 이 만든 **라우터에서 구조적으로** 뽑으므로 `add_get` 호출
    형태가 바뀌어도 눈을 감지 않는다. 수정 전 문서에 걸면 정확히
    `['/branding', '/stage']` 를 호명하며 실패한다 (RL-004).
  - `deploy-alb.sh` 경고를 추가했다 — 이름상 이 문서의 정답 스크립트처럼 보이지만
    `deploy-ec2.sh` 와 똑같이 깨져 있다 (→ ISSUE-72 로 분리).
  - **프로덕션 코드 변경 0줄. 그리고 머지해도 프로덕션은 안 바뀐다** — AWS 콘솔 리스너
    규칙 추가가 수동 작업으로 남는다.

#### Changed

- **`.gitignore` 가 런타임 데이터를 실제로 막는다** — 기존에 `data/app.db` / `data/*.db`
  두 줄만 있어서 **운영 SQLite 백업과 고객 로고가 커밋 가능한 상태**였다.
  `data/app.db.bak.20260608` 은 `.db` 로 끝나지 않아 패턴에 안 걸리고,
  `data/branding/<room_id>/*`(실제 고객 로고 4개)는 아예 대상이 아니었다. `data/` 전체와
  `.worktrees/`, 키트 린터 심볼릭 링크, Claude 런타임 상태를 무시 대상에 넣었다.

- **SSOT 문서를 저장소에 커밋했다** — `issues.md`, `STATUS.md`, `CHANGELOG.md` 와
  `docs/` 9개 파일이 그동안 **untracked** 라 작업 맥북이 유일한 사본이었다. `STATUS.md` 의
  open 이슈 집계도 낡아 있었다 (ISSUE-54 가 shipped 인데 open, ISSUE-60·66~69 누락).

#### Added

- **배포 점검에서 미등록 작업 3건을 이슈로 등재** — ISSUE-70(GH #156),
  **ISSUE-71**(GH #158, SSE/WS 가 `st.session_state` 게이트라 컨테이너 재시작 후 뷰어가
  죽어 있다 — `aws-alb-setup.md` §6 이 "별도 이슈" 라 적어뒀지만 만들어진 적이 없었다),
  **ISSUE-72**(GH #159, 배포 스크립트 2개가 깨진 채 잔존).

### 2026-09-06

#### Changed

- **포매터가 `black` 하나로 통일됐다** — ISSUE-65 · PR #154 · GH #153 · squash merge `748ea8a`.

  `.pre-commit-config.yaml` 에서 `ruff-format` 훅을 제거했다. `ruff` 는 이제 **린터 전용**으로
  남는다 (`- id: ruff`, `args: [--fix]` 그대로 유지).

  - **개발자에게 실제로 달라지는 점**: 줄바꿈된 `assert <cond>, <long msg>` 가 든 파일을 이제
    `SKIP=ruff-format` **없이** 한 번에 커밋할 수 있다. 그동안 `ruff format` 은 *메시지*를,
    `black` 은 *조건*을 괄호로 감싸며 서로의 결과를 되돌렸고(주기 2 순환), 두 훅이 매 실행마다
    **동시에** "files were modified by this hook" 으로 실패해 그런 파일은 커밋이 아예 불가능했다.
    "훅이 고쳐줬으니 다시 커밋하면 된다" 는 통상의 복구가 여기서는 종료하지 않았다.
  - 그래서 여러 이슈 본문과 일부 테스트 주석에 복사돼 있던 **"줄바꿈된 assert 를 피하라"는 회피
    지침은 더 이상 유효하지 않다.** 짧게 줄여 쓴 assert 메시지를 이제 제대로 쓸 수 있다.
  - `pyproject.toml` 의 `[tool.ruff.format]` 테이블을 제거했다 — 훅이 사라지면 아무것도 읽지 않는
    죽은 설정이 된다(RL-007). 그 자리에 재도입을 막는 사유 주석을 남겼다.
  - `CLAUDE.md` 에 "Formatter policy — black only, ruff is a linter" 절을 추가했다.
  - `tests/test_precommit_formatter_policy.py` (6 tests) 가 이 정책을 회귀 가드로 고정한다. 이
    파일은 교착 레이아웃을 **라이브 카나리아**로 들고 있어서, `ruff-format` 훅이 다시 들어오면
    단언이 깨지는 동시에 이 파일 자체가 다시 커밋 불가가 된다.
  - **프로덕션 코드 동작 변경 없음.** 머지 후 main `748ea8a` 실측: `pre-commit run --all-files`
    2회 연속 전 훅 통과 · 수정 파일 0, `black --check .` 97 files unchanged,
    `ruff check .` All checks passed, `pytest -q` 1472 passed / 4 skipped / 198 deselected,
    커버리지 94.35%.
