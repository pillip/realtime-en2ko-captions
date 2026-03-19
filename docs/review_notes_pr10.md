# PR #10 Review Notes -- refactor/extract-app-modules

**Reviewer**: Claude Opus 4.6
**Date**: 2026-03-19
**Branch**: `refactor/extract-app-modules`
**Scope**: Structure-only refactoring -- decompose 955-line `app.py` into focused modules
**Changed files**: 5 (932 insertions, 810 deletions)

---

## Code Review

### Overall Assessment

The extraction is well-executed. Module boundaries are clean, the import graph is acyclic (`app.py -> services, websocket_handler -> translation, services, auth, database`), and `translation.py` is fully standalone (zero local imports). The `port_ref` dict pattern is a pragmatic solution for cross-thread port sharing.

### Findings

#### [CR-01] `KeyError` on missing `type` field in WebSocket messages (Pre-existing, Blocking)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/websocket_handler.py`, line 326
**What**: `data["type"]` raises `KeyError` if a client sends a JSON message without a `type` field. The generic `except Exception` on line 348 catches it but leaks the `KeyError` message to the client via `str(e)`.
**Why it matters**: Exposes internal message structure expectations; also, a missing `type` is a normal input edge case, not an exceptional error.
**Fix**: Use `data.get("type")` instead of `data["type"]`.

```python
# Before
if data["type"] == "request_openai_session":

# After
msg_type = data.get("type")
if msg_type == "request_openai_session":
elif msg_type == "transcript":
```

#### [CR-02] Unreachable `return None` in `_invoke_bedrock_with_fallback` (Low, Suggestion)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/translation.py`, line 175
**What**: The `return None` at the end of `_invoke_bedrock_with_fallback` is unreachable because the last loop iteration re-raises the exception.
**Fix**: Remove the dead line or restructure so the function always either returns a response or raises.

#### [CR-03] Dead code: `create_aws_session` and `start_health_server` (Low, Suggestion)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/services.py`, lines 84-148
**What**: Both functions are defined but never called anywhere in the codebase. This was pre-existing in the original `app.py`.
**Why it matters**: Dead code increases maintenance burden and review surface.
**Fix**: Either remove them or mark them with a comment explaining they are for future/external use. Propose as a follow-up issue.

#### [CR-04] `_clean_llm_response` regex `This.*?:` is overly greedy (Pre-existing, Suggestion)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/translation.py`, line 146
**What**: `re.sub(r"This.*?:", "", ...)` with `re.DOTALL | re.IGNORECASE` will strip any text from "this" to the first colon, which could remove valid translation content (e.g., "This is a demo: 123" becomes " 123").
**Why it matters**: Could silently truncate legitimate translations.
**Fix**: Tighten the regex or apply it only before the main content line. Propose as a follow-up issue.

#### [CR-05] No test coverage for any extracted module (Blocking)

**What**: The `tests/` directory is empty. None of the newly extracted modules (`translation.py`, `services.py`, `websocket_handler.py`) have unit tests. The extraction explicitly fixes RL-001 (making functions importable), but the opportunity to add tests was not taken.
**Why it matters**: The primary benefit of this refactoring is testability. Without tests, there is no regression safety net to verify the extraction preserved behavior.
**Fix**: At minimum, add tests for `translation.py` (pure functions with no side effects: `detect_language`, `split_into_sentences`, `_clean_llm_response`, `_build_prompt_to_korean`, `_build_prompt_to_english`). These can be written in under 30 minutes and provide real value. Propose as a blocking follow-up.

#### [CR-06] `FileNotFoundError` silently swallowed for `scroll_lock.html` (Low, Suggestion)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/app.py`, lines 66-71
**What**: If `components/scroll_lock.html` is missing, the error is silently passed. This means a deployment issue (missing file) would produce a degraded UI with no indication of what went wrong.
**Fix**: Log a warning when the file is not found.

#### [CR-07] Event loop lifecycle in `app.py` start handler (Pre-existing, Low)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/app.py`, lines 173-176
**What**: A new event loop is created, used for a single `run_until_complete`, then closed. This pattern works but is fragile -- if `create_openai_session` spawns background tasks, they would be orphaned on `loop.close()`.
**Fix**: Acceptable for MVP. For robustness, consider `asyncio.run()` (Python 3.7+) which handles cleanup more thoroughly.

### Module Boundary Assessment

| Module | Responsibility | Dependencies | Verdict |
|--------|---------------|-------------|---------|
| `app.py` | UI controller, session state, thread management | `auth`, `services`, `websocket_handler` | Clean |
| `translation.py` | Language detection, sentence splitting, LLM translation | None (pure logic) | Excellent |
| `services.py` | AWS/OpenAI credential management, session creation | `boto3`, `httpx` | Clean |
| `websocket_handler.py` | WebSocket server, message routing, transcript handling | `auth`, `database`, `services`, `translation` | Acceptable (hub module) |

`websocket_handler.py` is the heaviest dependency consumer, which is expected as it orchestrates the real-time pipeline.

### Thread Safety Assessment

The `port_ref = {"port": None}` pattern is thread-safe for single-writer (WebSocket thread) / single-reader (Streamlit main thread) under CPython's GIL. Dict value assignment is atomic. This is a correct improvement over the original `global WEBSOCKET_PORT`.

---

## Security Findings

### [SEC-01] Internal exception details leaked to WebSocket clients (Medium)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/websocket_handler.py`, line 350
**What**: `str(e)` is sent directly to the client in error responses. Exception messages can contain internal paths, class names, and stack details.
**Impact**: Information disclosure that aids attackers in understanding server internals.
**Exploit path**: Send malformed messages to the WebSocket endpoint; observe error responses for internal information.
**Pre-existing**: Yes, faithfully extracted from original.
**Fix**: Return generic error messages to clients; log detailed errors server-side only.

```python
# Before
await websocket.send(json.dumps({"type": "error", "message": str(e)}))

# After
print(f"[WebSocket] message handling error: {e}")
await websocket.send(json.dumps({"type": "error", "message": "Internal server error"}))
```

### [SEC-02] WebSocket server binds to 0.0.0.0 without access control (Medium)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/websocket_handler.py`, line 376
**What**: The WebSocket server binds to all interfaces (`0.0.0.0`), making it accessible from any network interface, not just localhost.
**Impact**: In non-containerized deployments, any machine on the same network can connect to the WebSocket server and bypass the Streamlit authentication layer.
**Pre-existing**: Yes (comment in original says "Docker requires 0.0.0.0").
**Fix**: For Docker, bind to `0.0.0.0` only when `DOCKER_ENV` or similar flag is set; default to `127.0.0.1` for local development.

### [SEC-03] `create_aws_session` returns raw AWS secret key in response dict (Medium)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/services.py`, line 104
**What**: `"secret_access_key": AWS_SECRET_ACCESS_KEY` is included in the return value. If this function is ever wired to a client-facing endpoint, long-term AWS credentials would be exposed.
**Impact**: Currently dead code, so no active exploit path. However, the function name and structure suggest it was designed to be called from a session setup handler.
**Pre-existing**: Yes.
**Fix**: Remove `secret_access_key` from the return dict or delete the entire dead function.

### [SEC-04] Client-supplied user identity trusted without server-side validation (Pre-existing, High -- not introduced by this PR)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/websocket_handler.py`, lines 46-63
**What**: `_authenticate_client` accepts whatever the client sends in the `auth` message as the user identity, including `role`. This is documented in RL-002.
**Status**: Pre-existing. The extraction faithfully preserved this vulnerability. Not blocking for this PR but remains the highest-priority security issue in the codebase.

### [SEC-05] `postMessage` listener in scroll_lock.html does not verify origin (Low)

**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/components/scroll_lock.html`, lines 107-118
**What**: The `message` event listener checks `event.data.type` but not `event.origin`. Any page (including cross-origin) can trigger a page reload by posting a `usage_update` message.
**Impact**: Low -- worst case is forcing a page refresh. No data exfiltration.
**Fix**: Add `event.origin` check:

```javascript
if (event.origin !== window.location.origin) return;
```

---

## Severity Summary

| Severity | Count | IDs |
|----------|-------|-----|
| Critical | 0 | -- |
| High | 0 new (1 pre-existing: SEC-04) | -- |
| Medium | 3 | SEC-01, SEC-02, SEC-03 |
| Low | 1 | SEC-05 |

---

## Blocking Issues

1. **CR-01**: `data["type"]` KeyError risk -- simple fix, should be done before merge.
2. **CR-05**: No tests for extracted modules -- at minimum, `translation.py` pure functions should have tests before or immediately after merge.

## Suggested Follow-up Issues

1. Remove dead code (`create_aws_session`, `start_health_server`) or document their intended use.
2. Fix `_clean_llm_response` overly aggressive regex.
3. Implement server-side WebSocket authentication (RL-002).
4. Add origin checking to `postMessage` listener.
5. Add comprehensive tests for `translation.py`, `services.py` (mocked), and `websocket_handler.py` (mocked).

---

## Confidence Rating: **High**

The PR is a straightforward structural extraction with no behavioral changes. Import graph is verified acyclic. Module boundaries are clean. All security findings are pre-existing. The two blocking items (CR-01 KeyError, CR-05 missing tests) are clear and unambiguous.
