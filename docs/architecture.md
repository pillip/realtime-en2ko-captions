# Architecture

## Overview

- Architecture style: Single-process monolith with embedded browser component `[CONFIRMED]`
- Justification: All Python modules run in one Streamlit process; the browser component is a self-contained HTML/CSS/JS file served via `st.components.v1.html`. No separate services, no message broker. `[CONFIRMED]`
- Key constraints:
  - Streamlit's execution model reruns the entire script on each user interaction; mutable shared state is managed through `st.session_state` and a background daemon thread. `[CONFIRMED]` — `app.py:141-158`
  - Long-term AWS credentials never reach the browser; the browser authenticates with OpenAI using a short-lived ephemeral token obtained server-side. `[CONFIRMED]` — `services.py:31-91`, `components/webrtc.html:1801`
  - The WebSocket server runs on a dynamically allocated port (8765–8800) in a daemon thread spawned by Streamlit. `[CONFIRMED]` — `websocket_handler.py:25-43`, `app.py:152-158`

---

## Tech Stack

| Layer | Choice | Version | Source |
|-------|--------|---------|--------|
| Language | Python | 3.11 | `.python-version` `[CONFIRMED]` |
| Web framework | Streamlit | >=1.48.1 | `pyproject.toml:16` `[CONFIRMED]` |
| Async HTTP client | httpx | >=0.28.1 | `pyproject.toml:12` `[CONFIRMED]` |
| WebSocket server | websockets | >=15.0.1 | `pyproject.toml:17` `[CONFIRMED]` |
| AWS SDK | boto3 / botocore | >=1.40.25 | `pyproject.toml:8-9` `[CONFIRMED]` |
| Password hashing | bcrypt | >=5.0.0 | `pyproject.toml:7` `[CONFIRMED]` |
| Database | SQLite (raw sqlite3) | stdlib | `database.py:8` `[CONFIRMED]` |
| Cookie management | extra-streamlit-components | >=0.1.81 | `pyproject.toml:11` `[CONFIRMED]` |
| Browser component | Vanilla JS / WebRTC / WebSocket | — | `components/webrtc.html` `[CONFIRMED]` |
| Container runtime | Docker (python:3.11-slim base) | — | `Dockerfile:1` `[CONFIRMED]` |
| Test runner | pytest + pytest-cov | >=9.0.2 | `pyproject.toml:24-25` `[CONFIRMED]` |
| E2E tests | pytest-playwright | >=0.7.2 | `pyproject.toml:26` `[CONFIRMED]` |
| Linter / formatter | ruff + black | >=0.12.9 / >=25.1.0 | `pyproject.toml:27-28` `[CONFIRMED]` |
| Speech recognition (unused at runtime) | speechrecognition | >=3.14.3 | `pyproject.toml:15` `[CONFIRMED]` |

---

## Modules

### Module: `app.py`
- Responsibility: Streamlit entry point. Initialises session state, enforces authentication gate, spawns the WebSocket daemon thread (once per process lifetime), calls `create_openai_session()` on start, renders the `{{BOOTSTRAP_JSON}}` payload into `webrtc.html` via `operator_ui.render_component_html()` (single-pass substitution over the shared `script_escape.json_for_script` escaper), and renders the iframe component. `[CONFIRMED]`
- Dependencies: `auth`, `services`, `websocket_handler`, `streamlit`, `asyncio`, `dotenv`
- Key interfaces:
  - `st.session_state["action"]` — string state machine (`idle` | `start` | `starting` | `stop` | `error`)
  - `st.session_state["websocket_port_ref"]` — dict shared between Streamlit thread and WebSocket daemon thread for port discovery
  - `st.components.v1.html(html_content, height=900)` — renders the browser component

### Module: `services.py`
- Responsibility: Creates the OpenAI ephemeral session token (async HTTP POST to `api.openai.com/v1/realtime/sessions`) and validates AWS credentials via STS `get_caller_identity`. Also exposes a minimal health-check HTTP server for ALB. `[CONFIRMED]`
- Dependencies: `httpx`, `boto3`, `botocore`, `os`
- Key interfaces:
  - `create_openai_session() -> dict` — async; returns `{id, client_secret, expires_at, model}`
  - `create_aws_session(websocket_port) -> dict` — sync; validates AWS credentials and returns region/key info
  - `start_health_server(port)` — blocking; responds `200 OK` to `GET /` and `GET /health`

### Module: `websocket_handler.py`
- Responsibility: Runs an async WebSocket server in a daemon thread. Each connection authenticates the client via first-message (`{type: "auth"}`), initialises boto3 clients for AWS Translate and Bedrock, then handles two message types: `request_openai_session` (re-issues an ephemeral token) and `transcript` (translate + record usage + respond). `[CONFIRMED]`
- Dependencies: `translation`, `auth`, `database`, `services`, `boto3`, `websockets`
- Key interfaces:
  - `start_websocket_server(port_ref: dict)` — called in a `threading.Thread`; mutates `port_ref["port"]` with the allocated port
  - `handle_openai_websocket(websocket)` — per-connection async coroutine
  - `find_free_port(start=8765, max=8800) -> int`

### Module: `translation.py`
- Responsibility: Language detection (Korean Unicode range heuristic), sentence splitting, LLM prompt construction, and Bedrock invocation with 3-model fallback (claude-3-5-sonnet → claude-3-haiku → claude-3-sonnet). No I/O beyond the boto3 client passed in. `[CONFIRMED]`
- Dependencies: `boto3` (client passed in), `json`, `re`
- Key interfaces:
  - `detect_language(text) -> (source_lang, target_lang)` — returns `("ko","en")` if Korean chars present, else `("en","ko")`
  - `translate_with_llm(bedrock_client, text, source_lang, target_lang) -> str | None`
  - `split_into_sentences(text, language) -> list[str]`
  - `BEDROCK_MODEL_IDS` — ordered fallback list

### Module: `auth.py`
- Responsibility: Session state management using Streamlit `session_state` plus 24-hour browser cookie persistence via `extra-streamlit-components` `CookieManager`. Exposes decorators (`@require_auth`, `@require_admin`), login/logout helpers, and usage-limit enforcement. `[CONFIRMED]`
- Dependencies: `database`, `streamlit`, `extra_streamlit_components`, `hashlib`, `time`
- Key interfaces:
  - `init_session_state()` — sets up state and attempts cookie restore (once per session)
  - `login_user(username, password) -> bool`
  - `check_usage_limit(duration_seconds, user_info) -> bool` — admin role bypasses limit
  - `@require_auth`, `@require_admin` — decorator guards for Streamlit page functions
  - Session cookie format: `"{user_id}:{username}:{sha256_token_32chars}"` (no signing, no expiry validation beyond `max_age`) `[CONFIRMED]` — `auth.py:31-43`

### Module: `database.py`
- Responsibility: Wraps raw `sqlite3` connections with a context manager. Provides `DatabaseManager` (schema init), `PasswordManager` (bcrypt), `User` (CRUD, usage accounting), and `UsageLog` (recording, stats, pagination). Three module-level singletons initialised lazily. `[CONFIRMED]`
- Dependencies: `sqlite3`, `bcrypt`, `json`, `os`
- Key interfaces:
  - `get_db_manager() -> DatabaseManager` — singleton; also calls `init_admin_from_env()` on first access
  - `get_user_model() -> User`
  - `get_usage_log_model() -> UsageLog`
  - `User.authenticate(username, password) -> dict | None`
  - `User.add_usage(user_id, duration_seconds)`
  - `UsageLog.record_usage(user_id, action, duration_seconds, ...)`

### Module: `admin.py`
- Responsibility: Admin dashboard UI implemented as Streamlit tabs. Provides user list/edit/delete, new-user creation form, usage statistics, paginated log viewer, and CSV export. `[CONFIRMED]`
- Dependencies: `auth` (`@require_admin`), `database`, `streamlit`, `pandas`
- Key interfaces:
  - `show_admin_dashboard()` — guarded by `@require_admin`

### Module: `login.py`
- Responsibility: Standalone login page (runs at the `/login` route if navigated to directly). Duplicates the login form logic already embedded in `auth.display_login_form()`. `[INFERRED]` — `login.py:154` shows it is callable as `__main__` but Streamlit multi-page routing determines if it is ever served.

### Module: `pages/admin_dashboard.py`
- Responsibility: Streamlit multi-page routing shim. Adds the parent directory to `sys.path` and delegates to `admin.show_admin_dashboard()`. `[CONFIRMED]`

### Component: `components/webrtc.html`
- Responsibility: ~1750-line self-contained browser component. Handles microphone permission, device enumeration, WebRTC connection setup (SDP offer/answer against `api.openai.com/v1/realtime`), DataChannel message parsing (OpenAI Realtime events), WebSocket connection to the Python server for translation, credit-roll UI with auto-scroll, fullscreen mode, settings panel, font-size controls backed by `localStorage`. Also contains a dead-code `initializeAWSConnection()` function labeled "레거시 - 사용 안함". `[CONFIRMED]`

### Component: `components/scroll_lock.html`
- Responsibility: Injects CSS/JS into Streamlit to prevent the host page from scrolling while the embedded component is active. `[CONFIRMED]` — `app.py:67-71`

<!-- Stage Composite View modules — appended 2026-08-23. `[PLANNED]` = specified,
     not yet implemented. Delivered by ISSUE-37 to ISSUE-42. -->

### Component: `components/stage.html` `[PLANNED]`
- Responsibility: Self-contained stage-front composite page. Renders four regions — event title header bar, presentation area, sponsor logo bar (left column) and a caption column (right column, 25% or 33.333% wide). Owns three concerns: (1) `getDisplayMedia()` capture lifecycle including source reselect, track `ended` → title-card fallback, self-capture warning and Screen Wake Lock; (2) an SSE caption column that reuses `viewer.html`'s partial-chunk + typewriter smoothing logic, retimed onto `requestAnimationFrame` and capped at 60 DOM lines; (3) a 16:9 letterbox layout that keeps the deck's aspect ratio at any viewport size by letting the header and logo bars absorb the leftover vertical space.
- Served by: `sse_broadcast._handle_stage` via the same template-substitution pattern as `_render_viewer_html` (placeholders `{{ROOM_ID}}`, `{{ROOM_NAME}}`, `{{OUTPUT_LANGS_JSON}}`, `{{PRIMARY_LANG}}`, `{{INITIAL_STATE}}`, `{{STAGE_CONFIG_JSON}}`). **The escaper is picked by sink, not by "is this user input" (RL-020):** only `{{ROOM_NAME}}` lands in markup and is `html.escape`d; every placeholder that lands inside the inline `<script>` — scalars included — is emitted by `script_escape.json_for_script` (the single definition; `sse_broadcast._json_for_script` is now only an alias to it, kept for test compatibility), which is `json.dumps` plus `<` / `>` / `&` / U+2028 / U+2029 → `\uXXXX`. Its output is a **complete** JS literal, so the template supplies no quotes of its own. Plain `html.escape` in a script context silently corrupts the value (a `<script>` is raw text, so entities never decode) and leaves `\` untouched, letting a trailing backslash escape the closing quote and kill the whole bootstrap with a `SyntaxError`. Substitution is a **single-pass** `re.sub(r"\{\{(\w+)\}\}", lambda …)` (shared as `script_escape.PLACEHOLDER_RE`) so no substituted value can expand a later placeholder (RL-021); the viewer indexes the mapping directly, so a mistyped placeholder raises `KeyError` and degrades to the friendly 404 rather than leaking to the page. `{{ROOM_NAME}}` appears in *both* sinks on the viewer, so it is split there into `{{ROOM_NAME}}` (markup) and `{{ROOM_NAME_JSON}}` (script). Delivered by ISSUE-40 (stage) and ISSUE-44 (viewer parity); the operator bootstrap (`app.py` / `operator_ui.render_component_html`) joined the same rule via ISSUE-48, which is also where the escaper and placeholder regex were extracted into the dependency-free `script_escape.py` so pure modules like `operator_ui` can import them without pulling in aiohttp/streamlit.
- Hard constraint it must uphold (NFR-025): registers **no** document/window-level `keydown` handler and never `preventDefault()`s arrow / PageUp / PageDown / Space, never auto-calls `focus()` or `requestFullscreen()` — otherwise a presenter remote stops driving the deck.
- Delivered by: ISSUE-40 (layout + route), ISSUE-41 (caption column), ISSUE-42 (capture).

### Module: `stage_config.py` `[CONFIRMED]` — `stage_config.py`, `tests/test_stage_config.py`
- Responsibility: Pure, side-effect-free normalisation and validation of the `rooms.stage_config` JSON blob (`DEFAULT_STAGE_CONFIG`, `normalize_stage_config`, `validate_stage_config`). No DB, filesystem, network or Streamlit access at import or call time (RL-001), so it is shared unchanged by the admin form (ISSUE-39) and the server-side stage render (ISSUE-40).
- Delivered by: ISSUE-37 (PR #119, shipped).

### Module: `branding_assets.py` `[CONFIRMED]`
- Responsibility: Owns the logo asset store on disk — `save_asset`, `list_assets`, `delete_asset`, `resolve_asset_path` over `data/branding/{room_id}/` (root overridable via `BRANDING_DIR`). Enforces the extension whitelist (png/jpg/jpeg/svg) with magic-byte verification, the 2 MB per-file and 12-asset per-room caps, filename sanitisation, `Path.resolve()` containment checks and symlink rejection (NFR-028). Kept free of aiohttp so it is unit-testable without a server; the route handler is a thin wrapper.
- Delivered by: ISSUE-38 (shipped, PR #123). The HTTP wrapper lives in a separate `branding_routes.py` so `sse_broadcast.py` gained only an import and one `app.router.add_get` line.

### Module: `sse_broadcast.py` — stage additions `[PLANNED]`
- Additional responsibility: serves the stage page and the branding assets from the **same origin as the viewer** (default port 8766). This is deliberate — Streamlit is on 8501, so hosting logos or the stage page there would make the stage page mixed-origin behind a proxy/HTTPS. New routes are registered in `build_sse_app()` alongside `/stream`, `/view` and `/health`; both reuse `_handle_view`'s three-branch shape (repo failure / unknown room → generic 404, closed room → terminal state, otherwise → render) and its RL-006 policy of logging detail server-side while returning generic bodies.
- Delivered by: ISSUE-38 (assets), ISSUE-40 (stage page).

---

## Data Model

Storage: SQLite at `data/app.db` (path overridable via `DB_PATH` env var). No ORM; all queries are raw SQL with parameterised placeholders. `[CONFIRMED]` — `database.py:19`, `database.py:539`

### Table: `users`

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK AUTOINCREMENT | |
| username | TEXT UNIQUE NOT NULL | |
| password_hash | TEXT NOT NULL | bcrypt |
| salt | TEXT DEFAULT '' | legacy column, unused by bcrypt path |
| email | TEXT | nullable |
| full_name | TEXT | nullable |
| role | TEXT DEFAULT 'user' | 'user' or 'admin' |
| is_active | BOOLEAN DEFAULT 1 | |
| total_usage_seconds | INTEGER DEFAULT 0 | cumulative |
| usage_limit_seconds | INTEGER DEFAULT 3600 | 0 = unlimited (admin) |
| created_at | TIMESTAMP | |
| last_login | TIMESTAMP | nullable |
| hash_type | TEXT DEFAULT 'bcrypt' | legacy field |

### Table: `usage_logs`

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK AUTOINCREMENT | |
| user_id | INTEGER FK → users.id | |
| action | TEXT NOT NULL | always `"transcribe"` in current code |
| duration_seconds | INTEGER NOT NULL | |
| source_language | TEXT | `"en"` or `"ko"` |
| target_language | TEXT | `"ko"` or `"en"` |
| created_at | TIMESTAMP | |
| metadata | TEXT | JSON blob; includes `source_text`, `target_text`, `used_llm`, `estimated` flag |

No migration framework is used. Schema is created once via `CREATE TABLE IF NOT EXISTS` in `DatabaseManager.init_database()`. `[CONFIRMED]` — `database.py:35-76`

Appended 2026-08-23 `[CONFIRMED]` — `database.py:_migrate_add_room_stage_config`, `tests/test_migration_idempotency.py`: `rooms.stage_config TEXT NOT NULL DEFAULT '{}'` — per-room stage branding as a JSON blob (`event_title`, `event_subtitle`, `caption_ratio`, `logo_groups[{label, assets[]}]`), added by the idempotent `PRAGMA table_info` + `ALTER TABLE` migration `_migrate_add_room_stage_config()` (ISSUE-37, shipped). Logo binaries themselves live on the filesystem under `data/branding/{room_id}/`, not in SQLite; only filenames are stored in the blob. See `docs/data_model.md` for the full shape.

---

## API Design

### External HTTP (server-side)

#### POST `https://api.openai.com/v1/realtime/sessions`
- Called by: `services.create_openai_session()` — `services.py:39-83`
- Auth: `Authorization: Bearer <OPENAI_KEY>`
- Request body: model, voice, instructions, input_audio_transcription, turn_detection, modalities, temperature
- Response: `{id, client_secret: {value}, model, ...}` — `client_secret.value` is the ephemeral token
- Returned to Streamlit session state and injected into browser bootstrap

#### POST `https://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01`
- Called by: browser JS — `components/webrtc.html:1801`
- Auth: `Authorization: Bearer <client_secret>` (ephemeral token)
- Request body: raw SDP offer
- Response: raw SDP answer — WebRTC connection completes

#### POST `https://<bedrock-runtime>.amazonaws.com/model/<model_id>/invoke`
- Called via boto3 `bedrock_client.invoke_model()` — `translation.py:163`
- Models tried in order: `claude-3-5-sonnet-20240620-v1:0`, `claude-3-haiku-20240307-v1:0`, `claude-3-sonnet-20240229-v1:0`
- Request: Anthropic Messages API JSON, `max_tokens: 200`, `temperature: 0.5`

#### `translate_text` via `boto3` AWS Translate
- Fallback when Bedrock fails or is unavailable — `websocket_handler.py:141-151`

#### `get_caller_identity` via AWS STS
- Called at session creation to validate credentials — `services.py:104-111`

### Internal WebSocket Protocol (`ws://localhost:<dynamic_port>`)

Messages are JSON. Port range 8765–8800, allocated at startup and communicated to the browser via bootstrap JSON. `[CONFIRMED]`

#### Browser → Server

| Type | Payload fields | Description |
|------|---------------|-------------|
| `auth` | `user: {id, username, role, ...}` | First message; must arrive within 5 s or connection is unauthenticated |
| `transcript` | `text, audio_duration_seconds, timestamp` | Triggers translation + usage recording |
| `request_openai_session` | — | Re-issues an ephemeral token |

#### Server → Browser

| Type | Payload fields | Description |
|------|---------------|-------------|
| `auth_success` | `message` | Auth acknowledged |
| `connection` | `status, message` | Sent after auth, confirms service ready |
| `transcription_result` | `original_text, translated_text, source_language, target_language, used_llm, audio_duration, timestamp, remaining_seconds, user_id` | Main translation response |
| `openai_session` | `session: {id, client_secret, expires_at, model}` | Response to `request_openai_session` |
| `usage_exceeded` | `message, remaining_seconds` | Quota exceeded; browser closes connection |
| `error` | `message` | Generic error |

### WebRTC DataChannel (`oai-events`)

Between browser and OpenAI Realtime API (bypasses Python server entirely). `[CONFIRMED]`

Browser sends `session.update` on DataChannel open to configure transcription model (`gpt-4o-transcribe`) and VAD. Incoming event types handled:

| Event type | Action |
|-----------|--------|
| `session.created` | Update status UI |
| `conversation.item.input_audio_transcription.completed` | Display transcript, send `transcript` msg to Python WS |
| `input_audio_buffer.speech_started` | Record `speechStartTime` |
| `input_audio_buffer.speech_stopped` | Record `speechEndTime` |
| `error` | Display toast |

### HTTP Health Check

`GET /` and `GET /health` → `200 OK` body `"OK"` — `services.py:143-147`

Port not specified in code; intended to be bound on a separate port from Streamlit. `[INFERRED]`

### Streamlit Multi-Page Routes

| Path | File | Auth |
|------|------|------|
| `/` | `app.py` | `is_authenticated()` gate |
| `/admin_dashboard` | `pages/admin_dashboard.py` | `@require_admin` |

### Viewer/Stage HTTP Server (aiohttp, `SSE_PORT`, default 8766)

Unauthenticated, audience-facing. Registered in `sse_broadcast.build_sse_app()`.

| Path | Handler | Status | Description |
|------|---------|--------|-------------|
| `GET /stream/{room_id}?lang=<code>` | `_handle_stream` | `[CONFIRMED]` | SSE caption stream; `event: message` payloads plus `event: session_end` |
| `GET /view/{room_id}` | `_handle_view` | `[CONFIRMED]` | Audience viewer page rendered from `components/viewer.html` |
| `GET /health` | `_handle_health` | `[CONFIRMED]` | `200 OK` |
| `GET /stage/{room_id}[?lang=<code>]` | `_handle_stage` | `[PLANNED]` | Stage composite page rendered from `components/stage.html`. Same three-branch shape as `_handle_view`: repo failure or unknown room → 404 with the shared friendly not-found body; `status == "closed"` → rendered with `initial_state="closed"` so the caption column shows the ended state without opening SSE; otherwise → 200. `stage_config` is normalised server-side (`stage_config.normalize_stage_config`) before being injected as `{{STAGE_CONFIG_JSON}}`, so a malformed blob degrades to defaults rather than a 500. Optional `?lang=` fixes the caption language; absent → room `primary_output_lang`. Delivered by ISSUE-40/41/42. |
| `GET /branding/{room_id}/{filename}` | `branding_routes.handle_branding_asset` | `[CONFIRMED]` | Serves a stored logo asset from `data/branding/{room_id}/`. Content-Type from the extension whitelist (png/jpg/jpeg/svg), `Cache-Control: public, max-age=300`, and `Content-Security-Policy: default-src 'none'` on SVG responses so uploaded markup cannot execute script on the stage origin. Filename is sanitised and the resolved path is containment-checked against the room directory; traversal attempts and symlinks return a generic 404 (NFR-028, RL-006). Delivered by ISSUE-38 (shipped, PR #123). |

Why these live here rather than on Streamlit (8501): the stage page must load its logos from its own origin, otherwise every proxy/HTTPS deployment turns them into mixed-origin requests. `[PLANNED]` — appended 2026-08-23.

#### SSE event types on `/stream/{room_id}` `[PLANNED]` — appended 2026-08-30

> ISSUE-53. `_write_sse_event` (`sse_broadcast.py:715`) already takes the event name as an
> argument; the addition is a third name, a room-wide fan-out, and a connect-time snapshot.

| Event | Emitted by | Payload | Consumer behaviour |
|-------|-----------|---------|--------------------|
| `message` | `BroadcastManager.publish` via `broadcast_translation_for_room` / `_publish_partial_to_viewers` | `{text, lang, partial?, timestamp}` | `partial: true` feeds the typewriter reveal; the flag's absence locks the line |
| `session_end` | `_handle_stream` for a room already `closed`; room close during a session | `{event:"session_end", room_id, timestamp}` | Terminal — stop the typewriter, show the ended state, close the EventSource |
| `control` | `BroadcastManager.publish_control`, called from the WebSocket handler when the operator's language is recorded (ISSUE-52) or a `stage_control` message arrives | `{event:"control", room_id, primary_lang?, caption_scale?, timestamp}` | Re-subscribe to `primary_lang` **unless** the client's language was chosen explicitly; apply `caption_scale` to the `--caption-scale` CSS variable |

Three properties of the `control` channel that are load-bearing and easy to lose in a refactor:

1. **Fan-out is room-wide, not per language.** The `_channels` registry is keyed `(room_id, lang)`, and the whole point of a language change is to reach clients still sitting on the *old* channel. A per-language publish would deliver the announcement to everyone except the people who need it.
2. **The event name comes from an allow-list, not from the payload.** `_handle_stream` resolves `payload.get("event")` to a name only when it is in `{"control"}`; everything else is written as `message`. Caption payloads are server-built today, so nothing can currently forge a name — the allow-list turns that from a convention into a structural invariant, and gives the test suite something that fails when it is removed.
3. **A connecting client gets one snapshot frame.** Written after `register_viewer` and after the `: connected` comment, before the queue loop. Earlier and it races updates arriving during registration; inside the loop and it interleaves with caption frames. This is what lets a reloaded stage screen — a real occurrence mid-event — recover the current language and font size with no database round trip and no persisted state.

**Control state lives in process memory on the shared `BroadcastManager`** (`set_control` / `get_control`, synchronous and lock-free like `has_viewers` and `get_metrics`). It is deliberately **not** written to `rooms.stage_config`: that blob is the administrator's pre-event branding record, and the admin form saves it through a `normalize → save` round trip that silently drops keys it does not render (`stage_config.normalize_stage_config`), so a venue-time value stored there would be reset without a log line the next time an administrator edited branding — two writers over one blob with no named authority (RL-024 / RL-025). The residual gap is stated rather than engineered around: **a server restart resets `caption_scale` to 1.0**. The language survives, because ISSUE-52 writes it to the room row.

Control frames travel the **same** `asyncio.Queue` path as captions. The WebSocket server and the SSE server run in separate daemon threads with separate event loops, and payloads already cross that boundary today (`_publish_to_viewers` in the WS loop → `BroadcastManager.publish` → `queue.get()` in the SSE loop). Reusing that path adds no new class of risk; a second socket, queue or thread for control would introduce one.

#### Component iframe remount invariant `[CONFIRMED]` — appended 2026-08-30

`st.components.v1.html` renders its argument as the iframe's `srcdoc`, so **the component document's lifetime is coupled to the byte value of the bootstrap payload**. Any change to `{{BOOTSTRAP_JSON}}` reloads the document and destroys everything the component holds: `RTCPeerConnection`, the microphone `MediaStream`, the WebSocket client, and the caption scrollback. Measured on Streamlit 1.48.1 during ISSUE-47 — a `srcdoc` change reloads; an identical rerun does not (RL-028).

Classify every new BOOT field before adding it:

- **Session-scoped** (changes only at start/stop): safe. `openai_session`, `websocket_port`, `action`.
- **Interaction-scoped** (can change mid-session): unsafe. ISSUE-47's `display_mode` is one, and it is mitigated by disabling its radio while `action in ("start", "starting")`.

There is a third option the ISSUE-52/53/54 track takes: **do not put the state in the payload at all.** The output-language dropdown has always lived inside the component and pushed changes over the already-open WebSocket (`sendLanguageUpdate`, `components/webrtc.html:1077-1088`), which is why ISSUE-2 could promise "실시간 언어 변경이 재연결 없이 반영된다". The new caption-scale slider follows the same rule via a `stage_control` message. Neither adds a BOOT field, so neither can remount the iframe.

---

## Background Jobs

| Job | Trigger | Source |
|-----|---------|--------|
| WebSocket server | `threading.Thread(daemon=True)` spawned by `app.py` on every Streamlit rerun if thread is dead | `app.py:141-158` |
| (None) Health server | `start_health_server()` is defined but there is no call site in the codebase | `services.py:137` `[CONFIRMED]` |

No periodic jobs, task queues, or schedulers exist. `[CONFIRMED]`

---

## Observability

- Logging: `print()` statements throughout all modules. No structured logging, no log levels, no log handler configuration. `[CONFIRMED]`
- Metrics: None. No instrumentation library is present. `[CONFIRMED]`
- Alerting: None. `[CONFIRMED]`
- Latency display: The browser component measures end-to-end caption latency (`Date.now()` difference) and renders it in a `#latency` DOM element — `components/webrtc.html:1212-1221`. This is client-side only and not persisted or exported.

---

## Security

### Auth scheme
- Server-side: Streamlit `session_state` (in-memory, per-tab) + 24-hour browser cookie set by `extra-streamlit-components` `CookieManager`. `[CONFIRMED]` — `auth.py:37-43`
- Cookie format: `"{user_id}:{username}:{sha256[:32]}"` where the token is derived from `user_id + username + epoch_seconds`. There is no HMAC or server-side signature validation; session restoration only checks that the username exists in the DB and `is_active=True`. The token field is effectively decorative. `[CONFIRMED]` — `auth.py:31-34`, `auth.py:80-97`
- WebSocket: First-message auth passes `user_info` from the browser bootstrap JSON. The server accepts whatever `user_info` the browser sends — it does not re-validate against the database or compare to a server-issued token. `[CONFIRMED]` — `websocket_handler.py:46-63`
- Admin: `role == "admin"` check in `is_admin()` reads from `session_state.user` which was populated at login time. `[CONFIRMED]` — `auth.py:138-141`

### Input validation
- Passwords: minimum 6-character length enforced in `admin.py:239`; bcrypt-hashed via `PasswordManager`. `[CONFIRMED]`
- SQL: Parameterised queries throughout `database.py`. Exception: one `f-string` format is used to build a WHERE clause in `get_usage_stats()` from a controlled list of conditions (not user input). `[CONFIRMED]` — `database.py:462-493`
- Browser input: No server-side sanitisation of transcript text before it is passed to the LLM prompt or stored in the metadata JSON blob. `[INFERRED]`

### Secrets management
- AWS long-term keys, OpenAI API key, and admin credentials are loaded from environment variables via `python-dotenv`. `[CONFIRMED]` — `.env.example`
- Long-term AWS keys are passed directly to boto3 clients inside the WebSocket handler per connection (`_init_translation_clients`), meaning they are held in memory for the duration of each session. `[CONFIRMED]` — `websocket_handler.py:66-93`
- The `create_aws_session()` function returns the raw long-term access key and secret in its return dict (used for passing to browser in older AWS flow); the dict is currently only consumed server-side. `[CONFIRMED]` — `services.py:116-123`
- XSRF protection is enabled in Dockerfile via `STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true`. `[CONFIRMED]` — `Dockerfile:6`

---

## Deployment & Rollback

### Deployment target
- Single Docker container, port 8501 (Streamlit). `[CONFIRMED]` — `Dockerfile:30,33`
- Base image: `python:3.11-slim`. System packages: `libportaudio2`, `portaudio19-dev`, `build-essential`, `curl`. `[CONFIRMED]` — `Dockerfile:11-17`
- Dependencies installed from `requirements.txt` (generated via `uv export`); `pyproject.toml` is present but not used inside the container. `[CONFIRMED]` — `Dockerfile:20-21`
- Container `HEALTHCHECK`: `curl -f http://localhost:8501` every 30 s. `[CONFIRMED]` — `Dockerfile:27-29`
- ALB/proxy support: `STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true`, `--server.enableCORS=false`. `[CONFIRMED]` — `Dockerfile:6,33`
- ALB WebSocket routing: production browser uses `wss://<hostname>/ws` path-based routing; local dev uses `ws://localhost:<dynamic_port>`. The `/ws` path-based routing target is not implemented in Python — there is no reverse-proxy config in the repo. `[CONFIRMED]` — `components/webrtc.html:1598-1601`

### CI/CD
- No `.github/workflows/` or other CI configuration exists in the repository. `[CONFIRMED]`

### Rollback
- No rollback scripts or documented procedure. `[CONFIRMED]`

---

## Tradeoffs & Observations

| Observation | Evidence | Impact |
|-------------|----------|--------|
| WebSocket server spawned on every Streamlit rerun if thread is dead; no explicit lifecycle management | `app.py:141-158` — thread.is_alive() check but no explicit stop/join | Negative: port leaks possible on rapid reloads; multiple server instances could theoretically start |
| Session cookie token is not cryptographically signed; restore only checks DB existence | `auth.py:31-34`, `auth.py:80-97` | Negative: an attacker who knows a valid `user_id:username` can forge a restore token |
| WebSocket client auth accepts user_info from browser with no server re-validation | `websocket_handler.py:46-63` | Negative: any client that knows the WebSocket port can impersonate any user |
| Long-term AWS credentials passed directly to boto3 per WebSocket connection | `websocket_handler.py:66-93` | Negative: keys held in memory for session lifetime; no credential rotation or scoping |
| Dead code: `initializeAWSConnection()` and `BOOT.aws_session` branch in browser | `components/webrtc.html:1836-1900` | Neutral: increases file size, creates confusion; `create_aws_session()` in `services.py` is also effectively unused |
| No structured logging; all observability is `print()` | All modules | Negative: no filtering, no log aggregation, no correlation IDs |
| No CI/CD pipeline | No `.github/workflows/` | Negative: no automated test gate before deployment |
| `speechrecognition` listed as a production dependency but no import found in application code | `pyproject.toml:15` | Neutral: unused dependency increases container image size |
| `start_health_server()` is defined but never called | `services.py:137-161` | Negative: Docker HEALTHCHECK hits port 8501 (Streamlit), not a dedicated health endpoint |
| Translation model selection in browser (session instructions) differs from server-side (Bedrock) | `components/webrtc.html:1091-1188` vs `translation.py:20-24` | Neutral: two independent translation paths with different prompt strategies; browser path uses OpenAI for transcription + Bedrock/Translate for translation |
| `login.py` module exists and is importable but Streamlit multi-page routing does not expose it as a page | `login.py`, `pages/` directory | Neutral: dead entry point |
| Unit test coverage exists for `database.py`, `auth.py`, and pure functions in `translation.py` / `websocket_handler.py`; no tests for `services.py`, `admin.py`, or `app.py` | `tests/` directory | Negative: critical path (OpenAI session creation, usage recording) has no test coverage |
| E2E tests require a running Streamlit server and are excluded from default pytest run (`-m 'not e2e'`) | `pyproject.toml:81`, `tests/e2e/` | Neutral: correctly isolated but depend on an undocumented server setup |
| SQLite with no migration framework; schema initialised with `CREATE TABLE IF NOT EXISTS` | `database.py:35-76` | Negative: column additions require manual migration; `salt` and `hash_type` columns are legacy artefacts with no code path |
| `pandas` is an implicit runtime dependency (imported in `admin.py`) but is not listed in `pyproject.toml` | `admin.py:8`, `pyproject.toml` | Negative: dependency tracking is incomplete; would fail on a clean install without `pandas` |
| **The room's caption language had two sources of truth that never reconciled** — publish read the operator's live session setting (`websocket_handler.py:686-689`, comment: "오퍼레이터 설정이 source-of-truth"), page render read the `rooms` row (`sse_broadcast._handle_stage:549`), and nothing wrote the former into the latter | Measured in production: `usage_logs` recorded `ko → vi` with genuine Vietnamese `target_text` while the stage screen displayed Korean | Negative, and **silent** — the lazy translation gate turned an empty column into a wrong-language column, because the stage page had registered itself as a `ko` viewer so the server dutifully translated Korean to Korean and published it. No error, no log line. Addressed by ISSUE-52 (write-back) + ISSUE-53 (live follow). `[PLANNED]` — appended 2026-08-30 |
| `rooms.output_lang` and `rooms.primary_output_lang` are two columns meaning approximately the same thing | `database.py:_migrate_add_room_output_lang_columns` (ISSUE-30 added the second beside the ISSUE-26 original); only `primary_output_lang` is read by the SSE/render path | Negative but bounded: ISSUE-52 writes both in a single statement so they cannot diverge, and names `primary_output_lang` as authoritative in the docstring (RL-025's prevention). Collapsing them into one column is a separate migration nobody has scheduled. `[PLANNED]` — appended 2026-08-30 |
| `rooms.output_langs` is written by nothing and read by nothing on the serving path | `_supported_output_langs` (`sse_broadcast.py:328`) deliberately uses the global `translation.SUPPORTED_OUTPUT_LANGS` instead (#91/#92); `_coerce_output_langs_list` (`:315`) has no caller | Neutral today, a trap tomorrow: the obvious "fix" when adding language write-back is to update `output_langs` too, which would silently re-couple viewer language choice to room configuration and undo #91/#92. ISSUE-52 carries an explicit AC asserting the column is unchanged. `[PLANNED]` — appended 2026-08-30 |
| **보안 가드 뮤테이션 점검은 오프더셸프 대신 선언형 자체 러너를 채택** (`guard_mutations.py` + `guard_mutations.toml`, 20건 허용목록 카탈로그) | 실측 비교 (ISSUE-43 / PR #142). **선언형 러너: 20 가드 전량 kill, 9.6초, 사람이 판정해야 하는 미분류 생존 뮤턴트 0건** — 출력 자체가 `가드 ID killed by <테스트 노드 ID>` 대응표다. **`mutmut` 3.4.0: 4초만에 실패 종료, 처리한 뮤턴트 0건** — `--paths-to-mutate` 가 3.x 에서 제거되어 `setup.cfg` 의 `[mutmut] source_paths` 를 저장소에 커밋해야만 기동한다(`FileNotFoundError: Could not figure out where the code to mutate is`). 라이브러리 API 우회도 `mutmut.__main__` 이 import 시점에 설정을 읽어 동일 실패. 1회 설치·1회 실행(300초 상한)으로 시간을 제한했고 디버깅하지 않았다 | 채택. 탈락 사유는 실행 실패 자체가 아니라 **성질**이다 — (a) 이 이슈가 요구하는 허용목록 한정을 mutmut 3.x 는 호출 단위로 표현할 수 없어 저장소 전역 설정이 되고, (b) 자동 생성 뮤턴트는 통제 이름을 모르므로 생존분이 NFR-028 통제인지 무해한 로그 문자열인지 매번 사람이 판정해야 한다. 우리가 지키려는 것은 커버리지 공백이 아니라 **이름 있는 가드**이므로 산출물이 가드 ID ↔ 킬 테스트 대응표인 쪽이 맞다. 대가는 폭이 좁다는 것 — 카탈로그에 적지 않은 가드는 점검되지 않으며, 확대는 `guard_mutations.toml` 의 명시적 편집으로만 가능하다. 재평가하려면 `mutmut` 에 `setup.cfg` 를 커밋한 뒤 위 두 수치를 다시 재면 된다 `[SHIPPED]` — appended 2026-08-31 |
