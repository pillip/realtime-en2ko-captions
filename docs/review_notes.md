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
