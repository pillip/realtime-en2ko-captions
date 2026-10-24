# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Real-time multilingual caption system for conference scenarios. Browser connects to AWS Transcribe Streaming for speech recognition and Amazon Translate for translation, with Streamlit server handling AWS credential management for security.

**Critical Architecture Decision**: Direct browser-to-AWS services connection using temporary credentials for minimal latency. Speech recognition via AWS Transcribe Streaming with automatic language detection, followed by contextual translation (Korean ↔ English, Others → Korean).

## Development Commands

### 컨테이너 엔트리포인트 — `serve.py` (#158)

Docker 는 `streamlit run app.py` 가 **아니라** `python serve.py` 로 띄운다.
Streamlit 은 스크립트를 브라우저 세션 단위로 실행하므로, `streamlit run` 으로는
아무도 `:8501` 을 열기 전까지 `app.py` 가 실행되지 않고 뷰어 SSE(8766)/오퍼레이터
WS(8765) 도 뜨지 않는다. `serve.py` 는 두 서버를 먼저 띄우고 **같은 프로세스에서**
Streamlit 을 돌린다 — WS 핸들러와 SSE 브로드캐스터가 모듈 전역 상태를 공유하므로
프로세스를 쪼갤 수 없다. 기동 로직은 `background_servers.start_background_servers()`
(프로세스 단위 멱등)에 있고 `app.py` 도 같은 함수를 부르므로 개발 경로
(`streamlit run app.py`)도 그대로 동작한다. `tests/test_background_servers.py` 가
이 부팅 경로를 가드한다 — CMD 를 `streamlit run` 으로 되돌리면 실패한다.

#### 이 경로의 import 는 스크립트 런 **밖**이다 (#174)

`serve.py` 가 `start_background_servers()` 를 부르는 시점에는 Streamlit 의
`ScriptRunContext` 가 없다. Streamlit 의 커스텀 컴포넌트 등록은 이렇게 끝난다:

```python
ctx = get_script_run_ctx()
if ctx is not None:
    get_instance().component_registry.register_component(component)
```

**스크립트 런 안에서만 등록된다.** 밖에서 import 되면 등록이 조용히 건너뛰어지고,
`sys.modules` 캐시 때문에 이후 세션이 같은 모듈을 import 해도
`declare_component()` 는 다시 실행되지 않는다 — 컴포넌트가 **영구 미등록**이 되어
번들이 404 가 된다.

그래서 **커스텀 컴포넌트를 쓰는 모듈은 모듈 레벨에서 import 하지 않는다.**
`auth.py` 의 `CookieManager` 가 그 예이고, `get_cookie_manager()` 안에서
import 한다 (그 함수는 항상 스크립트 런 안에서 불린다).

2026-10-22 배포에서 이게 깨졌을 때의 증상 — **하나의 원인이 두 개로 보였다**:

- `/component/....CookieManager.cookie_manager/index.html` → 404
- `user_session` 쿠키 미발급 → (ISSUE-55) WS 인증 거부
- 오퍼레이터 화면에는 OpenAI 전사(**원문**)만 흘러서 "번역이 고장났다" 로 보임
- 뷰어·무대는 "잠시 후 시작됩니다" 에서 멈춰서 "무대가 고장났다" 로 보임

번역·SSE 팬아웃·무대 렌더·ALB 라우팅은 전부 정상이었다. 진단이 길어진 이유는
`webrtc.html` 이 서버의 `auth_error` 를 `알 수 없는 메시지 타입` 으로 콘솔에만
찍고 화면에 띄우지 않아서다 — 로그인이 끊긴 것과 번역이 고장난 것이 구별되지
않았다.

**왜 테스트가 못 잡았나**: `tests/e2e/conftest.py` 의 서버 fixture 는
`streamlit run app.py` 를 띄운다. 그 경로는 `app.py` 를 런타임 **안에서** 처음
import 하므로 문제가 없고, e2e 198개가 전부 통과한 채로 배포됐다. 게다가
`ensure_logged_in_as` 의 `require_cookie` 기본값이 `False` 라 대부분의 테스트는
쿠키 존재를 아예 보지 않는다. `tests/e2e/test_serve_boot_path_e2e.py` 가
`serve.py` 로 띄워 **평범한 로그인 한 번**이 쿠키를 심는지 검증해 이 구멍을
막는다. 구조적 가드는
`tests/test_background_servers.py::TestBootPathDoesNotPoisonComponentRegistration`.

관련: `serve.py` 의 Streamlit 설정은 `STREAMLIT_*` 환경변수를 읽어
`bootstrap.load_config_options(flag_options=...)` 로 **넘겨야** 적용된다.
환경변수만 세팅하는 것으로는 먹지 않는다 — `STREAMLIT_SERVER_PORT=8597` 을 줘도
8501 에 바인딩하는 것을 실측했다. 8501 이 기본값이라 가려져 있었다.
#### 차가운 부팅은 DB 싱글턴 경합을 드러낸다 (2026-10-22)

`serve.py` 는 `_db_manager` 가 **완전히 비어 있는** 상태에서 WS 스레드를 띄우고
곧바로 메인 스레드에서 `get_room_model()` 을 부른다. WS 스레드도 ISSUE-26
hydration 에서 같은 함수를 부르므로, 락이 없으면 두 스레드가 각자
`DatabaseManager` 를 만들어 `init_database()` 를 돌린다. 그러면 `_migrate_add_*`
들의 `PRAGMA table_info` **check-then-act** 가 깨져 둘 다 `ALTER TABLE` 을 쏘고
진 쪽이 터진다. 첫 배포에서 정확히 이렇게 죽었다:

```
[SSE] 서버 스레드 시작 실패: OperationalError('duplicate column name: stage_config')
```

WS(8765)만 떠서 컨테이너는 "정상" 으로 보였고, `/view/*`·`/stream/*`·`/stage/*`·
`/branding/*` 가 전부 ALB 502 였다. `database.py` 의 `_SINGLETON_LOCK` 이 이걸
막는다 — **마이그레이션 쪽을 try/except 로 바꾸지 말 것.** PRAGMA 게이팅은
무관한 ALTER 실패를 삼키지 않으려는 의도적 선택이라고 같은 파일에 적혀 있다.
고칠 지점은 "한 프로세스에서 `init_database()` 는 한 번만" 이라는 불변식이다.

주의: 이 버그는 **ALTER 가 실제로 실행될 때만** 보인다. 컬럼이 이미 다 있는
DB 에서는 경합 창이 열리지 않는다. 그래서 스키마가 뒤처진 DB 로 처음 부팅하는
환경(신규 배포, CI, 새 개발자 로컬)에서만 터진다.
`tests/test_database_singleton_threadsafety.py` 가 컬럼을 일부러 떼어내 이
조건을 재현한다.

`serve.py` 는 기동 후 `wait_until_ready()` 로 **포트 실제 개방**을 확인하고 한
줄로 판정을 남긴다. 스레드가 떴다는 것과 포트가 열렸다는 것은 다르고, 이번
장애에서 로그에는 판정에 쓸 줄이 없었다. `_supervise()` 는 스레드 **본문**
예외도 `errors` 에 담는다 — `thread.start()` 만 감싸면 스레드 안에서 터진
예외는 excepthook 이 stderr 에 찍고 끝난다.

```bash
# Project setup
uv init --python 3.11
uv add streamlit boto3 python-dotenv aiohttp  # aiohttp powers the SSE viewer broadcast server (ISSUE-30)
uv add 'qrcode[pil]'                            # QR code generation for viewer URLs (ISSUE-32)

# Development workflow
uv sync                           # Install dependencies
uv run streamlit run app.py       # Run development server
uv run pytest -q                  # Run tests

# Code quality
uv run ruff check .               # Check linting issues
uv run ruff check --fix .         # Fix auto-fixable linting issues
uv run black .                    # Apply black formatting
uv run pre-commit run --all-files # Run all pre-commit hooks
uv run pre-commit install         # Install pre-commit hooks (one-time)

# Docker deployment
uv export -o requirements.txt     # Export for container (aiohttp/qrcode 포함 — 의존성 변경 시 재실행)
docker build -t realtime-caption .
docker run --rm -p 8501:8501 -p 8765:8765 -p 8766:8766 \
  -e OPENAI_KEY=sk-... \
  -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... \
  -e VIEWER_BASE_URL=http://<host>:8766 \
  realtime-caption
# 권장: docker-compose up -d --build  (.env 자동 로드, 포트/볼륨 일괄 구성)
#
# `--build` 는 선택이 아니다. 빼면 구 이미지가 그대로 돌아 Dockerfile CMD 변경이
# 반영되지 않는다 — 2026-10-22 배포에서 `streamlit run app.py` 가 계속 돌아
# lazy-start 가 살아 있었고, 두 서버가 안 떠서 ALB 502 였다. 적용 여부는
# `docker inspect realtime-caption --format '{{json .Config.Cmd}}'` 로 확인한다
# (`["python","serve.py"]` 여야 한다).
#
# EC2 의 docker-compose v1.29.2(EOL)는 컨테이너를 *recreate* 할 때
# `KeyError: 'ContainerConfig'` 로 죽는다 — 최신 Docker 데몬이 만든 이미지의
# inspect 출력에 그 키가 없기 때문이다. 우회: 컨테이너를 지우고 create 경로로
# 가면 된다. `./data`·`./logs` 는 호스트 바인드 마운트라 DB/로고/로그가 남는다.
#   docker-compose stop realtime-caption && docker-compose rm -f realtime-caption
#   docker-compose up -d
# 영구 해결은 compose v2 (`sudo apt install docker-compose-v2`) — 단, 행사
# 직전에는 하지 말 것.
```

### 무대 화면의 두 가지 되돌리기 쉬운 제약 (#178)

**1. F10 keydown 은 NFR-025 의 의도된 예외다.** 그 요구사항 문구는 "`?debug=1`
밖에서는 document/window keydown 핸들러를 등록하지 않는다" 이고, 다음 사람이
`tests/test_stage_page.py` 의 "등록 2건" 을 보면 "1건으로 되돌려야 한다" 고
읽을 수 있다. 되돌리지 말 것 — NFR-025 의 load-bearing 부분은 두 가지다:

- 무대 창이 발표 중에 OS 포커스를 쥐지 않는다
- 프레젠터 리모컨 키(화살표/PageUp/PageDown/Space)를 **절대 가로채지 않는다**

F10 리스너는 `{ passive: true }` 라 기본 동작을 취소할 수 없고 `ev.key !== "F10"`
으로 즉시 반환한다. 그 두 성질을 테스트가 구조적으로 못 박고, 행동 증거는
`tests/e2e/.../test_presenter_remote_keys_do_not_disconnect` 다.

**2. 무대 테마는 색 ramp 가 아니라 레이어 모델을 쓴다 — IBM Carbon (#185).**
직접 고른 값으로 두 번(반전 #178 · 미색 #180), Tailwind `zinc` ramp 로 두 번
(너무 밝음 #184 · **엉성함** #185) 퇴짜를 맞았다. **네 번 다 대비 테스트를 전부
통과했다** — 미감 실패는 비율로 잡히지 않는다.

`zinc` → Carbon 으로 옮긴 이유가 핵심이다. `zinc` 는 **색 ramp** 일 뿐이라
"면이 몇 겹인가" 를 말해 주지 않았고, 그래서 화면이 한 덩어리 회색이 됐다.
구체적으로 세 가지가 빠져 있었다:

1. 16:9 패널과 그 바깥 레터박스 여백이 **같은 색**(`.presentation` 과
   `.stage-frame` 이 둘 다 `--frame`)이라 발표 영역의 경계가 아예 없었다.
2. 구분선이 전부 10% 알파라 캔버스와 **1.2:1** — 선언돼 있는데 화면엔 없었다.
3. 칩 배경도 알파라 "떠 있는 면" 이 아니라 얼룩으로 읽혔다.

Carbon 은 background → layer → border-subtle / border-strong 이라는 **레이어
토큰 모델**을 갖고 라이트/다크를 공식 테마 쌍(Gray 10 / Gray 100)으로 정의한다.
위 셋은 전부 그 모델이 이미 답을 가진 문제다. 그래서 값이 아니라 **역할**을
가져왔다.

- 다크는 Carbon **Gray 100** 테마 그대로: background `gray-100 #161616`,
  layer `gray-90`, border-subtle `gray-80`, text `gray-10`, focus `white`.
- 라이트는 Carbon **Gray 10** 테마의 역할을 두되 **단계만 한 칸 내렸다**
  (background `gray-10 → gray-20`, layer `white → gray-10`). Carbon 기본값
  `gray-10` 은 휘도 0.905 로, **눈이 아프다**고 이미 퇴짜를 맞은 밝기다 (#184).
  이 화면은 모니터가 아니라 행사장 프로젝터이고 캔버스와 패널이 벽을 채운다.
- **여백과 패널은 다른 면이다** (#185). 여백은 `--canvas`, 패널은 `--frame` +
  라이트에서만 실선 테두리. 다크 테두리는 **transparent 여야 한다** — 어두운
  방에서 투사면 경계는 드러나면 안 된다. 이 비대칭은 의도다.
- `#000000` 은 팔레트 밖의 **의도된 예외**다 — 16:9 패널은 투사면이고 레터박스
  띠가 화면에서 사라져야 한다. UI 색이 아니다.
- `viewer.html` 은 여전히 손으로 고른 `#0b0b0c` 다. 참석자 휴대폰과 프로젝터는
  나란히 보이는 일이 없어 그대로 두었다 — 뷰어를 Carbon 으로 옮기는 것은 별도
  과제다.

`test_every_colour_comes_from_the_declared_palette` 가 이걸 못 박는다. 비율이
아니라 **출처**를 검사하므로, 다음 사람이 "조금만 따뜻하게" 하려고 팔레트 밖
값을 끼워 넣는 순간 막힌다 — 대비 테스트는 그걸 전부 통과시킨다.

같은 실패군에서 나온, 되돌리면 안 되는 가드 셋:

- **알파를 테마 간에 공유할 수 없고, 캔버스를 내리면 다시 올라간다.**
  `gray-20` 캔버스 위 `gray-100` 0.50(다크의 자막 알파)은 **3.23:1** 로 미달이다.
  라이트 하한은 0.62, 다크 하한은 0.48 이다. 다크 값을 복사해 넣으면 눈에는
  멀쩡해 보이지만 행사장 뒤쪽에서 자막이 안 읽힌다.
- **라이트의 큰 면에는 휘도 상한이 있다** (`--canvas`/`--frame` ≤ 0.82). 대비는
  면이 내뿜는 **빛의 절대량**을 보지 않는다. 0.82 가 Carbon Gray 10 을 그대로
  쓰지 못하게 막는 수치다.
- **16:9 패널도 테마를 타야 한다.** `--frame` 과 캔버스의 대비 ≤ 1.8:1
  (현재 다크 1.16:1 / 라이트 1.29:1). 처음에는 패널만 `#000000` 으로 남겨 화면에
  **검은 구멍**을 냈는데, 패널 안 문구들은 자기 불투명 배경을 가져 대비 테스트가
  전부 통과했다.
- **구분선은 불투명 토큰이어야 한다** (캔버스와 ≥ 1.5:1). 알파 선은 캔버스가
  바뀔 때마다 보이기도 하고 사라지기도 한다.
- **pill 과 포커스 링은 글자 대비로 검사되지 않는다** (WCAG 1.4.11, 3:1).
  밝은 패널 위 밝은 pill 은 글자 16:1 로 통과하면서 pill 자체가 안 보인다.
  포커스 링은 `outline-offset` 때문에 **양쪽이 모두 패널**이라 대비 상대가
  pill 이 아니다 — 고정 `#7cc4ff` 가 밝은 패널 위에서 1.69:1 로 사라졌고(#180),
  Carbon 의 라이트 기본 포커스 `blue-60` 도 `gray-30` 패널 위 2.93:1 로 미달이라
  `blue-70` 을 쓴다 (#185).

`tests/test_stage_page.py` 는 **두 테마를 각각 계산**하고, 라이트가 다크의 토큰을
빠짐없이 덮는지도 따로 본다 — 하나라도 빠지면 라이트 캔버스 위에 흰 글씨(1.1:1)가
되고, 대비 테스트는 **선언된 토큰만** 보기 때문에 누락은 그쪽에 걸리지 않는다.

**로고 바 배경은 테마가 아니라 사람이 고른다 (#182).** 아트워크 한 벌로 밝은
면과 어두운 면을 모두 만족시킬 수 없다 — 흰 로고는 어두운 면에서만, 검정 로고는
밝은 면에서만 읽히고 CSS 로 이미지를 되살릴 방법은 없다. 그래서
`stage_config.logo_bar_tone` ∈ `auto`(면 없음, 기본) / `light`(`gray-10`) /
`dark`(`gray-80`) 를 관리자가 룸 기본값으로 정하고 오퍼레이터가 세션 한정으로
덮어쓴다. **톤을 테마에 묶지 말 것** — 같은 문제를 다른 축으로 옮기는 것뿐이다.

**전제: 로고는 배경이 투명한 파일로 올라온다** (#184). 관리자 업로더가 그렇게
안내하므로, 고른 면이 마크 주위로 그대로 비친다. 흰 배경이 박힌 파일은 어두운 면
위에서 그 네모가 보이는데 CSS 가 고칠 수 있는 문제가 아니라 **자산 문제**다.
한 룸에 흰·검정 로고가 섞이면 어느 톤을 골라도 한쪽은 묻힌다(로고별 자동 처리가
필요하고, 칩이 아니라 아웃라인이 맞다 — 별도 과제).

### Formatter policy — black only, ruff is a linter (ISSUE-65)

**`black` is the sole formatter. `ruff` is used as a linter only.** There is
deliberately **no `ruff-format` hook** in `.pre-commit-config.yaml`, and
deliberately no `[tool.ruff.format]` table in `pyproject.toml`.

Reason: `ruff format` and `black` do not converge on the wrapped
`assert <cond>, <long msg>` layout. `ruff format` parenthesises the *message*,
`black` parenthesises the *condition*, and each reverts the other:

```
ruff format ->  assert cond, (        black ->  assert (
                    msg                             cond
                )                               ), msg
```

Running both makes the two hooks cycle with period 2, so **both** report "files
were modified by this hook" on every run and the file can never be committed
without `SKIP=ruff-format`.

Note the trap: the hooks run in one pass (`ruff-format`, then `black`), so the
cycle completes **inside a single `pre-commit` run** and the file ends
byte-identical to how it started. A "run `--all-files` twice and diff the tree"
convergence check therefore sees nothing wrong — only the hook **exit status**
exposes the deadlock. Any convergence test for this must assert *both* "0 files
modified" and "all hooks passed"; the file check alone is blind here.

That deadlock recurred across four sprints and had
already started shaping how tests were written (artificially short assert
messages) before it was fixed.

This mirrors the global rules §6, which names `black` as the formatter and
`ruff` as the linter. Do not re-add `ruff-format` — the lint config is not
half-finished, it is intentional. `tests/test_precommit_formatter_policy.py`
guards this and carries the conflicting layout as a live canary.

### Large-file hook limit — 1 MB, not the default 500 KB

`check-added-large-files` runs with `--maxkb=1024`. The default 500 KB rejects
`issues.md`, a deliberately large living document (502 KB / 69 issue blocks as of
2026-10-11) that is the project's issue SSOT and belongs in the repo. The raised
limit keeps the hook doing its real job — blocking a stray SQLite file,
screenshot, or binary — while letting the SSOT through.

The proper fix is to split shipped issues into an archive, mirroring
`docs/sprint_state_archive_2026-08-31.md`: 54 of 69 blocks are `shipped`. Once
that happens the limit can come back down. Don't raise it further instead.

Related: `.gitignore` now ignores `data/` wholesale. It previously listed only
`data/app.db` and `data/*.db`, which missed `data/app.db.bak.*` (no `.db` suffix)
and `data/branding/<room_id>/*` (real customer logos) — both were committable.
`data/` is the docker-compose runtime volume; nothing under it belongs in git.

## Essential Environment Variables

- `SESSION_SECRET`: **Recommended in production** (ISSUE-55). HMAC key that signs the
  `user_session` cookie (`auth._sign_cookie`); since ISSUE-55 the WebSocket handshake
  authenticates against that same cookie. If unset, a random secret is generated per
  container start and shared process-wide (Streamlit, WS and SSE threads all read the
  same `os.environ`), so **the running container behaves normally** — what breaks is
  restarts: every previously issued cookie becomes invalid, so the operator must log
  in again before a session can start. In practice that folds into the re-visit
  `:8501` step that lazy-start (ISSUE-71) already requires after a restart.
  Note `docker-compose.yml` must list this var explicitly — compose only forwards
  enumerated keys, so a value in `.env` alone never reaches the container.
  Generate with `python -c "import secrets; print(secrets.token_hex(32))"`.
- `AWS_ACCESS_KEY_ID`: Required for AWS service authentication (server-side only)
- `AWS_SECRET_ACCESS_KEY`: Required for AWS service authentication (server-side only)
- `AWS_REGION`: Optional, defaults to `ap-northeast-2` (Seoul). Bedrock translation uses `global.` cross-region inference profiles (translation.py), which are callable from any commercial region — Seoul has no `apac.` profile for the Claude 4.5 generation, so the `global.` prefix is required there.
- `WS_PORT`: Optional, defaults to `8765`. Fixed TCP port for the translation-pipeline WebSocket server (process-wide singleton). Must match the Docker port mapping — dynamic per-session allocation leaked past the mapping (#84).
- `SSE_PORT`: Optional, defaults to `8766`. TCP port for the unauthenticated viewer SSE broadcast server (`/stream/{room_id}`, ISSUE-30).
- `VIEWER_BASE_URL`: Optional, defaults to `http://localhost:{SSE_PORT}`. Base URL embedded into QR codes (ISSUE-32) — set to the public viewer host (e.g. `https://captions.example.com`) in production so attendees scan a reachable URL.
- `BRANDING_DIR`: Optional, defaults to `data/branding`. Filesystem root for per-room logo assets (`branding_assets.py`, ISSUE-38), served by the SSE server at `GET /branding/{room_id}/{filename}`. The default sits inside the existing `./data` Docker volume, so logos survive container restarts alongside `app.db` — override only if the volume layout changes. Read via `os.getenv` inside each function (never at import time), same convention as `DB_PATH`.

## Core Architecture

**Two-Component System**:
1. **Streamlit Server**: Manages AWS credentials, serves embedded JS component via `st.components.v1.html`
2. **Browser Component**: Handles device selection, AWS SDK integration, real-time caption rendering

**Key Security Pattern**: Long-term AWS keys never reach browser. Server passes temporary credentials for each session.

**Data Flow**: Audio (USB/mic) → AudioWorklet → AWS Transcribe Streaming → Text transcription → Amazon Translate → Multilingual captions → Credit-roll UI

## Critical Implementation Details

### Audio Device Handling
- Browser permission required before device labels visible: `getUserMedia()` first, then `enumerateDevices()`
- Line input optimization: `echoCancellation:false, noiseSuppression:false, autoGainControl:false`
- Device switching requires stream recreation with new `deviceId`

### WebRTC Connection Sequence (Realtime GA, 2026-05-12 이후)
1. Server: `POST /v1/realtime/client_secrets` → ephemeral client secret (`ek_...`, model/세션 설정은 이 시점에 서버가 고정)
2. Browser: SDP offer creation with audio track
3. Browser: `POST /v1/realtime/calls` with `Authorization: Bearer ek_...` + `Content-Type: application/sdp` → answer (구 `POST /v1/realtime?model=...` 은 GA에서 400)
4. RTCPeerConnection established, DataChannel for caption events

### Caption Event Processing
- OpenAI session is **transcription-only** (#100): `turn_detection.create_response=false`, so OpenAI does NOT auto-generate translation responses (they were discarded and wasted tokens). The only DataChannel event the browser acts on is `conversation.item.input_audio_transcription.completed`.
- On that event the browser sends the transcript to the server WebSocket (`/ws` → 8765); **all translation is server-side Bedrock** (translation.py), which also fans out to viewers via SSE. OpenAI produces no `response.*` events.
- Browser MUST NOT send `session.update` — GA session config is fully pinned server-side at client_secret creation (#89)
- UI transition: gray/italic temporary text → confirmed lines appended to scroll

### UI Behavior Requirements
- Credit-roll: auto-scroll only when user at bottom (`scrollTop` detection)
- Resizable container: `resize: both` (browser compatibility varies)
- Stop action: immediate RTCPeerConnection close + caption list reset

## Session Management Instructions for OpenAI Model

Template for session instructions:
```
"영어 발화를 한국어 자막으로 자연스럽게 번역. 2줄/줄당 16~23자, 고유명사 원어 유지."
```

## Browser Compatibility Targets

Chrome/Edge/Safari latest for WebRTC features. Fallback considerations for `resize` CSS property limitations.

## Performance Targets

- First caption: <2 seconds end-to-end latency
- Stable connection for 30+ minute sessions
- Graceful handling of ephemeral token expiration (manual restart in MVP)
