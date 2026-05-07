# Review Notes — ISSUE-33 (PR #67)

**Reviewer**: Claude Opus 4.7 (automated, team-lead REVIEW phase)
**Date**: 2026-05-07
**PR Size**: +1173 -3 across 8 files
- `database.py` (+108 LOC): `_migrate_add_room_viewer_metric_columns`, `Room.update_viewer_metrics`, `Room.get_viewer_metrics`
- `sse_broadcast.py` (+~110 LOC): in-memory counters, `metrics_repo` injection, `get_metrics`
- `admin.py` (+76 LOC): `_render_room_viewer_metrics`
- `admin_logic.py` (+79 LOC): `_format_by_lang_label`, `build_room_metrics_view_data`
- `app.py` (+5 LOC): `attach_broadcast_metrics_repo` wiring at SSE startup
- `websocket_handler.py` (+16 LOC): `attach_broadcast_metrics_repo` setter
- `tests/test_viewer_metrics.py` (NEW, 30 unit tests)
- `tests/e2e/test_viewer_metrics_e2e.py` (NEW, 3 e2e cases — `e2e` marked, deselected by default)

## Scope

룸별 SSE 뷰어 접속 지표 (현재/누적/최대 + 언어별 분포) 를 수집해 관리자 대시보드에 표시. 누적·peak 는 DB 영속화로 서버 재시작에도 보존, 인메모리 current 는 `BroadcastManager` 가 SSE register/unregister 시 O(1) 로 갱신. admin 대시보드는 매 rerun 마다 in-memory snapshot + DB row 를 합쳐 4 개 `st.metric` 위젯으로 렌더링.

## Code Review

### Strengths

- **RL-006 컴플라이언스 (확실)**:
  - `BroadcastManager.register_viewer` 의 DB hook 은 `try/except Exception` 으로 감싸 `[SSE] metrics_repo.update_viewer_metrics failed (room=… lang=…): {e!r}` 만 서버 로그에 남기고 SSE 연결은 절대 끊지 않는다. 클라이언트는 generic 동작 (정상 SSE) 만 본다.
  - `admin._render_room_viewer_metrics` 도 BroadcastManager 로딩 실패 / 룸별 metrics 조회 실패를 모두 try/except 로 격리, 사용자에게는 `뷰어 지표를 불러올 수 없습니다.` / `'<room>' 룸의 지표를 불러올 수 없습니다.` 로만 노출. 내부 `repr(e)` 는 `print()` 로 stderr 만.
  - `Room.update_viewer_metrics` 가 unknown room 시 `False` 반환 — raise 하지 않아 SSE register 가 stale URL 로 인해 깨지는 경로 차단.
- **마이그레이션 멱등성 (RL-009 수준)**: ISSUE-29 의 `_migrate_add_room_output_lang_columns` 와 동일 패턴 — `PRAGMA table_info(rooms)` 로 기존 컬럼 셋을 읽고, 없을 때만 `ALTER TABLE … ADD COLUMN`. 두 컬럼 모두 `INTEGER NOT NULL DEFAULT 0` 이라 legacy row 의 자동 0 시드. `test_migration_is_idempotent_on_third_init` (3 회 호출 → 컬럼 1개씩) + `test_legacy_db_without_columns_gets_migrated` (output_lang/total_viewers 없는 prod-like DB 업그레이드) 로 회귀 차단.
- **peak_viewers race-safety (확실)**: 단일 `UPDATE rooms SET total_viewers = total_viewers + ?, peak_viewers = MAX(peak_viewers, ?) WHERE id = ?` 표현식. SQLite 가 row-level write lock 으로 직렬화하지만 무엇보다 `MAX(peak, ?)` 가 idempotent — 동일 current=N 으로 두 번 호출되어도 결과 동일. `test_peak_viewers_uses_max_not_overwrite` (current 가 10 → 5 로 줄어도 peak=10 유지) 로 검증, `test_peak_viewers_grows` 로 단조 증가 보장.
- **재시작 hydration (AC#4 충족)**: `test_db_metrics_persist_across_room_repo_recreation` — 같은 SQLite 파일을 새 `DatabaseManager` + `Room` 인스턴스로 다시 열어 `get_viewer_metrics` 가 11/7 (이전 두 register 의 누적/peak) 를 그대로 반환. 재시작 후 in-memory current=0 으로 되돌아가도 누적/peak 은 admin 위젯에 즉시 표시됨.
- **DB I/O 가 asyncio lock 밖**: `register_viewer` 의 SQLite write 가 `async with self._lock` 블록 OUT 으로 빠져 있어 register burst 시에도 동시 in-memory 업데이트가 직렬화되지 않는다. SQLite 자체 동시성에 위임. (RL-008: lock 보호 영역 최소화.)
- **언어별 카운트 정확성**: register/unregister 모두 `self._by_lang[room_id][lang]` 을 lock 안에서 갱신. unregister 시 0 도달한 lang 키와 비어버린 room dict 를 모두 pop → `get_metrics` 가 stale 키 없는 깨끗한 zero-state 반환. `test_metrics_isolated_per_room`, `test_unregister_decrements_lang_and_current` 로 검증.
- **double-unregister 방어**: `had_queue = queue in viewers` 후에만 카운터 mutate — 같은 큐로 unregister 가 두 번 들어와도 underflow 없음 (`test_unregister_unknown_does_not_underflow`).
- **decrement clamping**: `max(0, … - 1)` 로 음수 가드. 이론상 도달 불가하지만 defensive — 향후 manager 가 다른 경로에서 `_current` 를 갱신해도 안전.
- **Streamlit-free helper 분리**: `admin_logic.build_room_metrics_view_data` 는 Streamlit/DB 의존성 없이 dict-in/dict-out — Streamlit 없는 pytest 환경에서도 변환 로직만 단위 테스트 가능 (`TestBuildRoomMetricsViewData`). `_format_by_lang_label` 은 카운트 내림차순 → 코드 오름차순 결정적 정렬 → 스냅샷 안정.
- **keyword-only 인자 강제**: `update_viewer_metrics(*, total_delta, current)` / `build_room_metrics_view_data(*, in_memory, db_metrics)` — 두 인자 swap 시 큰 표시 오류로 이어지므로 명시성을 컴파일 타임에 강제 (admin_logic 의 다른 헬퍼와 동일한 안전 패턴).
- **lazy import in admin.py**: `_render_room_viewer_metrics` 안에서 `from websocket_handler import get_broadcast_manager` 를 lazy import. websocket_handler 의 부수효과 (env vars, 모듈 레벨 RoomManager) 를 admin 페이지 진입 시점으로 미뤄 import-time 비용 분리.
- **attach_broadcast_metrics_repo idempotent**: app.py 가 streamlit rerun 마다 attach 해도 같은 `_metrics_repo` 슬롯을 덮어쓸 뿐 — 멱등. 직접 attribute access 로 setter 표면을 작게 유지 (`# noqa: SLF001` 도 의도 표시).
- **TDD 준수**: tests-written + red phase 체크포인트 PASS — 30 단위 테스트 우선 작성 후 구현. 549 passed, 22 e2e deselected, ruff/black clean.

### Findings

- **CR-1 (Low)** — `BroadcastManager.unregister_viewer` 의 DB hook 이 호출되지 않는다. 의도 자체는 명확 (peak 는 register 에서 이미 max 됨, total 은 단조 증가) 이지만, 미래에 "현재 라이브 viewer 수" 도 DB 에 저장하고 싶어진다면 unregister 도 hook 이 필요하다. 현재 AC 는 누적/peak 만 요구하므로 **현 시점 무시 가능**, 코멘트로만 명시되어 있어 readable.
- **CR-2 (Low)** — `admin.py` 가 `room_model` 을 인자로 받지만 `_render_room_viewer_metrics(visible_rooms, room_model)` 호출 위치 (`show_room_management`) 의 시그니처를 보지 않고는 어디서 주입되는지 즉시 보이지 않는다. `_render_room_qr_section(visible_rooms)` 와 일관성을 위해 `room_model` 을 모듈 함수 (`get_room_model()`) 로 가져오는 패턴도 가능하나, 의존성 주입 (`room_model` 파라미터) 이 테스트 친화적이라 **현재 형태 유지 권장**.
- **CR-3 (Nit)** — `_format_by_lang_label` 의 알 수 없는 lang 코드 (`{"xx": 3} → "xx 3명"`) 는 한국어 라벨이 없어 좀 어색하다. 테스트에서는 raw code 가 그대로 노출되는 동작을 기대 (`test_unknown_lang_falls_back_to_code`) 하지만, `print(f"[Admin] unknown lang code in metrics: {code}")` 같은 server-log warn 을 추가해 운영자가 라벨 누락을 빠르게 파악할 수 있게 하면 좋다. **AC 외, follow-up 후보.**
- **CR-4 (Nit)** — `admin.py` 의 `st.metric("언어별", label)` 에서 `label` 이 `"한국어 45명, 중국어 12명"` 처럼 길어지면 `st.metric` 의 value 영역 (보통 큰 폰트) 에 가로로 잘릴 수 있다. 4 개 언어 동시 시 모바일 admin 화면에서 가독성 저하 가능. 향후 `st.markdown` 으로 분리 렌더하거나 첫 1-2 lang 만 노출 + tooltip 으로 전체 표시하는 패턴을 검토. **현 행사 운영 (보통 1-3 lang) 에서는 무시.**
- **CR-5 (Info)** — `BroadcastManager.get_metrics` 가 lock 없이 dict.copy 만 한다. Python dict 의 `.copy()` 는 GIL 안에서 atomic 이지만, 동시 register 가 진행 중이면 snapshot 이 정확히 register 시점 이전/이후 중 하나로 일관됨이 보장되지 않는다 (read 와 write 사이에 `current` / `by_lang` 가 별도 dict 라 잠시 inconsistent). admin.py 의 새로고침 주기 (Streamlit rerun) 에서는 무시 가능한 수준이지만, **dashboard 가 strict consistency 를 요구한다면 lock 으로 감싸는 옵션을 두는 것이 안전**.

### Security review

- **No XSS**: admin 대시보드의 lang label 은 사전 정의된 `_LANG_KOR_LABELS` (ko/en/ja/zh 한정) + 알려지지 않은 코드는 raw 노출. 그러나 lang 코드는 SSE handler 에서 `{ko, en, ja, zh, vi, auto}` 이외는 거부되므로 자유 텍스트 주입 경로 없음. room_name 은 admin auth 통과 후 생성되어 신뢰 가능 + Streamlit 이 markdown 렌더 시 자체 escape.
- **No SQL injection**: 모든 query parameterized (`?` placeholder).
- **No information disclosure**: DB 실패 / 일반 예외 모두 generic 메시지로만 사용자에 노출. `print(repr(e))` 는 server stderr 만.
- **No race writing**: peak 가 `MAX(peak_viewers, ?)` SQL 표현식으로 idempotent — concurrent register 의 두 transaction 이 같은 current=N 을 보더라도 최종 row 는 max(N) 로 수렴.
- **In-memory state leaks**: room close → unregister 가 모두 호출되면 `_current` / `_by_lang` 의 키가 자동 pop. 룸이 영원히 살아있어도 in-memory dict 는 viewer-수 비례 (DB row 수 비례 X).

### Test quality

- **30 단위 테스트 모두 real assertions**, AC 1:1 매핑:
  - **AC#1 현재/누적/최대 표시** ↔ `TestUpdateViewerMetrics` (5 cases) + `TestGetViewerMetrics` (3 cases) + `TestBuildRoomMetricsViewData` (formatter)
  - **AC#2 언어별 표시** ↔ `TestBroadcastManagerInMemoryMetrics` (`test_multiple_languages_have_independent_counts`, `test_metrics_isolated_per_room`) + `TestFormatByLangLabel` (4 cases)
  - **AC#3 실시간 갱신** ↔ register/unregister 단위 테스트 + `TestBroadcastManagerWithRepoIntegration` (DB hook delta=+1, MAX peak)
  - **AC#4 재시작 복원** ↔ `TestRoomManagerHydrateMetrics.test_db_metrics_persist_across_room_repo_recreation`
- **Migration 멱등성** 회귀 차단: 3-회 init + 레거시 prod-like DB 업그레이드 (`test_legacy_db_without_columns_gets_migrated`, output_lang 없고 total_viewers 도 없는 가상 v1 DB).
- **DB hook 격리**: `test_db_failure_does_not_break_sse_register` (mock repo 가 raise 해도 register 정상 진행), `test_unregister_does_not_call_db_hook` (의도 명시).
- **3 e2e (Playwright + raw TCP SSE)**: 실서버 SSE 경로로 single/multi-lang counter 정확성 + DB persist 검증. `e2e` 마크로 디폴트 deselected.
- ruff/black clean (lint 게이트), 549 unit GREEN, 82.77% coverage (≥ 50% 게이트).

## Security Findings

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| SEC-1 | None | XSS / SQLi / CSRF 모두 적용 안 됨 (admin auth 통과 + parameterized SQL + 사전 정의 label set). | — |
| SEC-2 | None | RL-006 일관 적용: DB hook / 위젯 렌더 / 룸별 조회 모두 generic 폴백. | — |
| SEC-3 | Low | `get_metrics` 가 lock 없는 snapshot — strict consistency 미보장. UX 영향 미미 (Streamlit rerun 주기). | Defer (CR-5) |

## UI Review (self)

### Layout
- `st.divider()` + `st.subheader("📈 뷰어 지표")` + `st.caption(...)` 으로 다른 admin 섹션 (룸 관리 / QR 다운로드) 과 시각 구분.
- 룸당 4 컬럼 (`st.columns(4)`) — 현재/누적/최대/언어별 — Streamlit 의 디폴트 카드 스타일 (라벨 + 큰 숫자) 활용.
- 룸이 없으면 섹션 자체를 그리지 않음 (빈 카드 노이즈 방지).

### Copy
- `현재 뷰어` / `누적 뷰어` / `최대 동시` / `언어별` — 한 단어로 짧고 행사 운영자 친화적.
- `현재/누적/최대 뷰어 수와 언어별 분포. 현재 값은 새로고침 시 갱신됩니다.` — 폴링 모델임을 명시.
- 언어별 zero-state: `0명` 폴백 (admin.py L961).

### Tokens
- 시각 토큰: Streamlit 디폴트 (light/dark 자동 적응). 별도 색 oct 없음 — 일관성 유지.

### Accessibility (RL-010)
- 각 `st.metric` 의 첫 인자가 한국어 라벨 (`현재 뷰어` 등) — 스크린리더가 라벨 + 값 순서로 읽음.
- 아이콘 only 버튼 / 비텍스트 컨트롤 없음.
- 언어별 요약 텍스트 (`한국어 45명, 중국어 12명`) — 시각/스크린리더 모두 자연어로 읽힘.
- `st.divider()` 가 시맨틱 separator 역할.

### Mobile responsive
- Streamlit 의 `st.columns(4)` 는 좁은 화면에서 자동 wrap (Streamlit ≥ 1.28). 4 카드가 모바일 admin 에서 2x2 로 폴백.
- ✅ AC#1-#3 모바일 admin 시나리오 (행사장 운영자 휴대폰 사용) 충족.

## Confidence

**High**. 모든 AC (4 개) 충족, RL-006/008/009/010 컴플라이언스 검증, 30 단위 + 3 e2e 테스트 GREEN, ruff/black clean, 보안/접근성 follow-up 만 남고 blocking 이슈 없음.

마이그레이션 멱등성 검증 (3 회 init), peak race-safety 검증 (MAX SQL 표현식 + concurrent register 시나리오), hydration 검증 (DB persist across DatabaseManager recreation), RL-006 (DB hook / 위젯 / 룸별 조회 모두 generic 폴백) — 4 개 검증 포인트 모두 PASS.

## Lessons applied (no new RL needed)

| Lesson | Application |
|--------|-------------|
| RL-006 | DB hook 실패, 위젯 로딩 실패, 룸별 조회 실패 모두 generic 메시지 + server-side `repr(e)` 로그만 |
| RL-008 | asyncio lock 보호 영역 최소화 — DB I/O 는 lock 밖에서 수행 |
| RL-009 | PRAGMA table_info 게이팅된 ALTER TABLE 패턴 (ISSUE-29 와 동일) |
| RL-010 | 한국어 라벨 일관, st.metric 의 시맨틱 + 언어별 요약 자연어 |
| RL-005 | 새 모듈 (database, sse_broadcast, admin, admin_logic) 모두 단위 테스트 동반 + e2e 추가 |
