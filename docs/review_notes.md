# Review Notes — ISSUE-26 (PR #60)

**Reviewer**: Claude Opus 4.7 (automated, team-lead REVIEW phase)
**Date**: 2026-05-07
**PR Size**: +1055 -12 across 4 files (database.py, room_manager.py, websocket_handler.py, tests/test_rooms_db.py)

## Scope

`rooms` 테이블 + `Room` 모델 + `RoomManager` 영속화 + WebSocket auth closed-room 거부.

## Code Review

### Strengths

- **RL-002 컴플라이언스**: `created_by`/`operator_id` 가 users.id FK 로 강제됨 (`PRAGMA foreign_keys=ON`). 클라이언트가 admin/operator 식별자를 위조해도 INSERT 시점에 차단.
- **RL-006 컴플라이언스**: closed 룸 거부 메시지를 unknown 룸과 동일한 generic 텍스트("요청한 룸을 찾을 수 없습니다.")로 통일. 룸 lifecycle 정보 누설 차단.
- **RL-005 컴플라이언스**: Room 신규 클래스에 39개 단위 테스트 동반 (CRUD/transition/cleanup/hydrate 모두 커버). 추출 후 테스트 부재 패턴 회피.
- **State machine 검증 일관성**: `_ROOM_TRANSITIONS` dict + `InvalidRoomTransition` 예외로 다이어그램 위반을 명시적으로 차단. parametrized 테스트로 invalid 경로 5개 (역방향, closed 재활성 등) 모두 검증.
- **Defense in depth**: 애플리케이션 transition 검증 + DB CHECK 제약 + FK 제약 — 세 단계 방어.
- **TDD 준수**: tests-written, red phase 체크포인트가 모두 PASS — 테스트 먼저 작성 후 구현.
- **Idempotency**: `close_room` 은 이미 닫힌 룸 호출 시 raw exception 을 client 로 전달하지 않고 (RL-006), `cleanup_stale_rooms` 는 동시 close 레이스를 `InvalidRoomTransition` catch 로 처리.

### Findings

- **CR-1 (Low)** — `Room.cleanup_stale_rooms()` 가 직접 호출되는 경우 (admin tool 등) RoomManager 메모리는 stale 상태로 남는다. 다만 `_authenticate_client` 가 DB 상태를 추가 검증하므로 새로운 연결은 차단되며, 백그라운드 cleanup 루프 (`_periodic_room_cleanup_loop`) 는 `delete_room` 으로 메모리도 동기화한다. **현 사용 패턴에서는 안전.** Follow-up 으로 admin DB 도구가 추가될 때 메모리 동기화 hook 을 도입하는 것을 권장.

- **CR-2 (Low)** — `transition_status` 가 `active → inactive` 시점에도 `last_activity = CURRENT_TIMESTAMP` 로 갱신한다. 즉 일시정지 직후의 30분이 cleanup 의 시작점이 된다. 이는 spec ("30분간 last_activity 갱신이 없는 비활성 룸") 의 의도이므로 정상 동작.

- **CR-3 (Nit)** — `Room.create()` 가 INSERT 후 `get_by_id` 로 두 번째 쿼리를 수행. 룸 생성은 저빈도라 무시 가능. 향후 `RETURNING` 절 (SQLite 3.35+) 을 활용해 단일 쿼리로 줄일 수 있음.

- **CR-4 (Nit)** — `cleanup_stale_rooms` 의 SELECT 와 `transition_status` UPDATE 가 별도 connection 에서 실행되어 race window 가 존재하지만, `InvalidRoomTransition` catch 로 처리됨. 단일 트랜잭션화는 `transition_status` 의 검증 일관성을 깨므로 비권장.

### Test quality

- 39 새 테스트 모두 real assertion 보유 (구체값 비교, 상태 검증, 타입 검증).
- Schema 검증: `expected = {...}; assert expected.issubset(actual)` 로 컬럼 확장 시 회귀 방어.
- State transitions: parametrized invalid edges + transition_unknown_room (False return) + transition_unknown_status (raises) 등 negative path 충실.
- Timeout: stale_inactive, stale_active(zombie), recent (skip), already_closed (skip), per-room timeout (5min override) — 5개 시나리오.
- Hydrate: active+inactive+waiting 복원 검증, closed 제외, no-repo no-op.
- WebSocket auth: closed 룸 거부 + 메시지 검사 (`closed`/`exception`/`traceback` 부재 강제).

전체 테스트: 381 passed, 14 deselected (e2e), 83.47% coverage. Lint clean.

## Security Findings

- **SEC-1 (Info)** — `_authenticate_client` 의 새 DB 상태 체크 분기는 repo 가 raise 하면 fail-closed (room_closed=True) 로 거부한다. DB 장애 시 모든 룸 접속이 막히지만, 잘못 열어주는 것보다 안전하다. 모니터링 권장.

- **SEC-2 (Info)** — `_periodic_room_cleanup_loop` 의 sleep interval 은 `ROOM_CLEANUP_INTERVAL_SECONDS` env 로 조정 가능 (기본 60s). 테스트에서 monkey-patch 로 짧게 줄일 수 있어 디버그/스테이징 친화적.

- **SEC-3 (Pass)** — Long-term AWS keys, OpenAI tokens, admin credentials 은 본 변경에 노출되지 않는다.

- **SEC-4 (Pass)** — 모든 SQL 은 parameterised. `list_by_status` 의 `IN (?,?,...)` placeholder 도 안전하게 동적 생성.

## Follow-up suggestions (non-blocking)

1. `database.py` 에 `last_activity` 인덱스 추가 검토 (cleanup_stale_rooms 의 status 인덱스로 충분하지만, 룸 수 ↑ 시 검토).
2. `Room` 모델에 `force_close_all_for_user(operator_id)` 같은 admin 도구 helper 가 필요해질 가능성 — ISSUE-29 시 평가.
3. `_periodic_room_cleanup_loop` 에 prometheus-style 카운터 (closed_count) 노출 고려 — observability 개선.

## RL-002 / RL-006 / RL-005 / RL-004 verdict

- **RL-002 (server-side validation)**: PASS. 클라이언트 입력은 모두 DB 또는 RoomManager 사전 등록과 대조 검증.
- **RL-006 (error message leakage)**: PASS. 모든 client-facing 에러는 generic. 서버 로그에만 raw exception.
- **RL-005 (extract-without-tests)**: PASS. 39 새 테스트.
- **RL-004 (assertion strength)**: PASS. 의미 있는 값 비교, negative path, parametrized invalid edges 포함.

## Verdict

**APPROVED**. 코드 품질, 보안, 테스트 모두 ship 가능한 수준. Critical/High 결함 없음.

---

# Review Notes — ISSUE-27 (PR #61)

**Reviewer**: Claude Opus 4.7 (automated, team-lead REVIEW phase)
**Date**: 2026-05-07
**PR Size**: +534 -11 across 5 files (app.py, database.py, operator_ui.py, tests/test_operator_ui.py, tests/test_rooms_db.py)

## Scope

오퍼레이터 사이드바에 배정된 룸 드롭다운/상태 표시/시작·정지 연결 추가.
bootstrap JSON에 `room_id` 전달 (실제 룸 격리는 ISSUE-28 영역).

## Code Review

### Strengths (RL compliance)

- **RL-001 (importable modules)** — PASS: 사이드바의 분기·기본값·라벨링이
  `operator_ui.py` 로 분리되어 있다. 이 모듈은 streamlit/database를 import
  하지 않아 `tests/test_operator_ui.py` 가 mock 없이 직접 import 한다.
- **RL-005 (extract-with-tests)** — PASS: 신규 5개 함수 + 1개 DB 메서드에
  대해 20개 단위 테스트 동반. extraction 후 테스트 부재 패턴 회피.
- **RL-006 (error leakage)** — PASS: `_load_assigned_rooms_for_user` 의
  except 블록은 `print(f"[Sidebar] 배정된 룸 조회 실패: {e!r}")` 로 서버
  로그에만 기록. 클라이언트 메시지 경로 없음. 동시에 fail-closed (빈
  리스트 → 시작 버튼 비활성) 로 안전하게 동작.
- **RL-010 (a11y)** — PASS: 새 `st.selectbox("룸 선택", ...)` 는 visible
  label + `help="자막을 송출할 룸을 선택하세요"` 보유. 시작 버튼이
  비활성일 때 사유를 `help` 텍스트로 안내 ("배정된 룸이 없어 시작할 수
  없습니다."). 신규 icon-only 컨트롤 없음.
- **RL-004 (assertion strength)** — PASS: dropdown 옵션 테스트는
  `set ==` 정확 비교, payload 테스트는 키별 정확 비교. 약한 assertion
  (`>= 1` 류) 없음.

### Findings

- **CR-1 (Low)** — `auth.logout_user()` 가 `selected_room_id` 를 명시적으로
  pop 하지 않는다. 같은 브라우저에서 다른 사용자가 로그인하면 이전
  사용자의 room id가 session_state에 남아 있다. 그러나
  `select_default_room`이 새 사용자의 room id 목록에 없으면 첫 룸으로
  폴백하므로 **정보 누설/오작동 없음**. 방어 in depth 차원에서 ISSUE-29
  쯤 logout 에 명시적 cleanup 추가를 권장.

- **CR-2 (Nit)** — bootstrap payload에 admin/익명 경로에서도 `room_id` 키가
  `None` 으로 항상 포함된다. webrtc.html 은 `BOOT.room_id` 의 진위만 보고
  결정하므로 동작은 동일. 하위 호환성 유지 차원에서 의도된 디자인.

- **CR-3 (Nit)** — `_load_assigned_rooms_for_user(user)` 가 admin 분기와
  DB 에러 분기 두 가지를 한 함수에 둔다. 가독성은 OK이지만, 향후 admin
  이 자기 룸을 갖는 시나리오 (ISSUE-29 후속) 가 생기면 조건이 늘어날 수
  있다. 변경 시 별도 함수로 쪼개도 좋음.

### Edge cases verified

- **Logout → 다른 사용자 로그인**: `select_default_room`의 `last_selected_id
  in ids` 가드가 정보 누설을 막는다 (CR-1 참조).
- **DB 조회 실패**: `[]` 반환 → 빈 상태 메시지 + 시작 버튼 비활성 (fail-closed).
- **Admin 사용자**: `show_room_section=False` → 룸 섹션 미노출, 기존 default
  룸 동작 유지.
- **active 상태 중 룸이 closed로 전환**: `list_by_operator` 가 closed 제외 →
  다음 rerun에서 드롭다운에서 사라짐. 진행 중인 WS 세션은 ISSUE-26 lifecycle
  로 정리됨.

## Security Findings

없음. 클라이언트→서버 신규 데이터는 `selected_room_id` 문자열 하나뿐이며,
ISSUE-25 에서 도입된 `_authenticate_client` 의 generic 거부 메시지 경로를
그대로 통과한다 (RL-002 + RL-006 보장 유지).

## Test Quality Verification

- 신규 테스트 20건 (rooms_db 4 + operator_ui 16). 빈 함수, pass-only,
  assert 없는 테스트 0건. 단언 갯수: 새 파일 합계 104개 (rooms_db 75 +
  operator_ui 29 — 기존 테스트 포함 카운트).
- 테스트 명명이 AC와 매핑됨 (예: `test_list_by_operator_empty_for_unassigned`
  → AC3, `test_includes_room_id_when_provided` → AC2).
- 405 passed, lint/format clean (`uv run pytest --no-cov -q`,
  `ruff check .`, `black --check .` 모두 통과).

## Follow-up suggestions (non-blocking)

1. **CR-1 fix**: `auth.logout_user()` 에 `st.session_state.pop("selected_room_id", None)` 한 줄 추가. 1줄짜리 cleanup이라 별도 issue로 분리하기 애매; ISSUE-29 (admin 룸 관리) 작업 시 같이 정리.
2. **ISSUE-28 후속**: webrtc.html이 `BOOT.room_id` 를 WS auth 메시지에 포함시키도록 수정. 본 PR은 페이로드까지만 책임진다.
3. (Optional) operator_ui의 함수에 `pyproject.toml` doctest 활성화 검토. 현재는 일반 unit test 로 충분.

## RL-001 / RL-005 / RL-006 / RL-010 / RL-004 verdict

- **RL-001**: PASS. operator_ui.py 부수효과 없음, 직접 import 가능.
- **RL-005**: PASS. extraction PR 에 20건의 동반 테스트.
- **RL-006**: PASS. 신규 client-facing 에러 경로 없음.
- **RL-010**: PASS. 신규 selectbox label 명시, 비활성 사유 help 텍스트.
- **RL-004**: PASS. 정확 비교 위주. 약한 assertion 없음.

## Verdict

**APPROVED**. 코드 품질, 보안, 접근성, 테스트 모두 ship 가능. Critical/High
결함 없음. CR-1은 follow-up으로 충분.
