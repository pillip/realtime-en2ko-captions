# Data Model

## Storage Strategy

- Primary storage: SQLite at `data/app.db` `[CONFIRMED]`
- ORM: None — raw `sqlite3` stdlib with `sqlite3.Row` factory (Python 3.11) `[CONFIRMED]`
- Secondary storage: None. No cache, search, or object storage layer.
- Source: `database.py:19`, `database.py:539`; database path is overridable via `DB_PATH` environment variable `[CONFIRMED]` — `database.py:539`, `tests/test_database.py:TestDatabaseManager.test_init_creates_directory`

---

## Access Patterns

| Pattern | Source | Operation | Frequency | Confidence |
|---------|--------|-----------|-----------|------------|
| Authenticate user by username + bcrypt verify | `database.py:136-167` | read (users) | Per login event | `[CONFIRMED]` |
| Update `last_login` timestamp on successful auth | `database.py:285-294` | write (users) | Per login event | `[CONFIRMED]` |
| Session restore: look up user by `user_id` | `auth.py:88`, `database.py:169-182` | read (users) | Per Streamlit page rerun | `[INFERRED]` |
| Check remaining quota before transcription | `database.py:271-283`, `auth.py:160-187` | read (users) | Per transcript event | `[CONFIRMED]` |
| Accumulate usage after transcription | `database.py:257-269` | write (users) | Per transcript event | `[CONFIRMED]` |
| Record usage log after transcription | `database.py:328-359`, `websocket_handler.py:177-191` | write (usage_logs) | Per transcript event | `[CONFIRMED]` |
| List all users for admin dashboard | `database.py:199-210`, `admin.py:52` | read (users), full scan | Admin session, low | `[INFERRED]` |
| Paginated log query per user | `database.py:361-385`, `admin.py:445-446` | read (usage_logs) with LIMIT/OFFSET | Admin session, low | `[CONFIRMED]` |
| Paginated log query across all users (JOIN) | `database.py:410-434`, `admin.py:449` | read (usage_logs JOIN users) with LIMIT/OFFSET | Admin session, low | `[CONFIRMED]` |
| Aggregated usage stats (SUM/COUNT/AVG) with optional date filter | `database.py:436-496`, `admin.py:316-318` | read (usage_logs), aggregate | Admin session, low | `[CONFIRMED]` |
| Language-pair breakdown (GROUP BY) | `database.py:480-494` | read (usage_logs), aggregate | Admin session, low | `[CONFIRMED]` |
| Full user log export for CSV download | `database.py:387-408`, `admin.py:394` | read (usage_logs), unbounded full scan per user | Admin session, very low | `[INFERRED]` |
| Update user fields (email, role, is_active, limit) | `database.py:212-255`, `admin.py:146-153` | write (users) | Admin action, very low | `[CONFIRMED]` |
| Delete user + cascade delete logs | `database.py:311-318`, `admin.py:173` | write (users + usage_logs) | Admin action, very low | `[CONFIRMED]` |
| Create user | `database.py:100-134`, `admin.py:249-256` | write (users) | Admin action, very low | `[CONFIRMED]` |
| Bootstrap admin from env vars at startup | `database.py:499-526` | read + conditional write (users) | Once per process start | `[CONFIRMED]` |

---

## Schema

### Table: `users`

- Source: `database.py:39-57`

| Column | Type | Constraints | Default | Description |
|--------|------|-------------|---------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | — | Surrogate key |
| `username` | TEXT | UNIQUE NOT NULL | — | Login identifier |
| `password_hash` | TEXT | NOT NULL | — | bcrypt hash (`$2b$` format) |
| `salt` | TEXT | — | `''` | Legacy column; bcrypt embeds its own salt. Not written or read by any current code path. |
| `email` | TEXT | nullable | NULL | Optional contact address |
| `full_name` | TEXT | nullable | NULL | Display name / affiliation |
| `role` | TEXT | — | `'user'` | Enum-style: `'user'` or `'admin'` |
| `is_active` | BOOLEAN | — | `1` | Soft-disable flag; `0` blocks authentication |
| `total_usage_seconds` | INTEGER | — | `0` | Cumulative seconds consumed; incremented atomically |
| `usage_limit_seconds` | INTEGER | — | `3600` | Quota ceiling; `0` is the admin convention for unlimited |
| `created_at` | TIMESTAMP | — | `CURRENT_TIMESTAMP` | Row creation time |
| `last_login` | TIMESTAMP | nullable | NULL | Updated on each successful `authenticate()` call |
| `hash_type` | TEXT | — | `'bcrypt'` | Legacy column; not used by any current query |

- Relationships: One `users` row has zero or many `usage_logs` rows. Foreign key declared on `usage_logs.user_id`. Cascade is implemented in application code (`delete_user` manually deletes logs before deleting the user row) — SQLite foreign key enforcement (`PRAGMA foreign_keys = ON`) is never enabled in `database.py`. `[CONFIRMED]` — `database.py:311-317`

---

### Table: `usage_logs`

- Source: `database.py:59-74`

| Column | Type | Constraints | Default | Description |
|--------|------|-------------|---------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | — | Surrogate key |
| `user_id` | INTEGER | NOT NULL, FOREIGN KEY → `users(id)` | — | Owner of this log entry |
| `action` | TEXT | NOT NULL | — | Always `"transcribe"` in current runtime; field reserved for future action types |
| `duration_seconds` | INTEGER | NOT NULL | — | Audio duration in seconds; estimated as `max(1, len(transcript)/5.0)` when audio metadata is absent |
| `source_language` | TEXT | nullable | NULL | ISO 639-1 code (`"en"` or `"ko"`) |
| `target_language` | TEXT | nullable | NULL | ISO 639-1 code (`"ko"` or `"en"`) |
| `created_at` | TIMESTAMP | — | `CURRENT_TIMESTAMP` | Log creation time |
| `metadata` | TEXT | nullable | NULL | JSON blob serialised by `json.dumps()`; deserialized on read. Shape documented below. |

- Relationships: Many-to-one with `users`. No ORM cascade — deletion is handled explicitly in `User.delete_user()`.

#### `metadata` JSON blob shape

Written by `websocket_handler.py:183-190`. No column-level schema enforcement; structure is a Python dict serialised as JSON.

| Key | Type | Description |
|-----|------|-------------|
| `transcript_length` | int | `len(transcript)` in characters |
| `translated_length` | int | `len(translated_text)` or `0` if translation absent |
| `used_llm` | bool | `True` if Bedrock was used; `False` if AWS Translate fallback |
| `estimated` | bool | `True` if `audio_duration_seconds` was estimated rather than measured |
| `source_text` | str | Original transcribed text |
| `target_text` | str | Translated output text |

---

### Table: `rooms` — column `stage_config` `[CONFIRMED]`

> Appended 2026-08-23, delivered by ISSUE-37 (PR #119). `[CONFIRMED]` = code and tests
> back this entry — `stage_config.py`, `database.py:_migrate_add_room_stage_config` /
> `Room.get_stage_config` / `Room.update_stage_config`, covered by
> `tests/test_stage_config.py`, `tests/test_rooms_db.py` and
> `tests/test_migration_idempotency.py`. The `rooms` table itself was introduced
> by ISSUE-26 and extended by ISSUE-30 (`primary_output_lang`, `output_langs`) and
> ISSUE-33 (`total_viewers`, `peak_viewers`).

| Column | Type | Constraints | Default | Description |
|--------|------|-------------|---------|-------------|
| `stage_config` | TEXT | NOT NULL | `'{}'` | JSON blob holding the per-room stage-screen branding. Added by the idempotent migration `DatabaseManager._migrate_add_room_stage_config()` (`PRAGMA table_info(rooms)` gate + `ALTER TABLE rooms ADD COLUMN`), matching the ISSUE-29/30/33 pattern. `'{}'` is a valid value and normalises to the documented default. |

#### `stage_config` JSON blob shape

Written by `Room.update_stage_config()`, read through `Room.get_stage_config()`. Both go
through the pure `stage_config.normalize_stage_config()` / `validate_stage_config()` pair,
so an absent, empty, or malformed value degrades to the default rather than raising. As with
`usage_logs.metadata`, there is **no column-level schema enforcement** — the invariant lives
in application code.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `event_title` | str | `""` | Header bar primary line. Max 120 chars. Empty → the stage page falls back to the room name while keeping the bar's height. |
| `event_subtitle` | str | `""` | Header bar secondary line. Max 80 chars. |
| `caption_ratio` | str | `"1/4"` | Caption column width. Enum: `"1/4"` (25%) or `"1/3"` (33.333%). Any other value is rejected by `validate_stage_config` and normalised to `"1/4"` on read. |
| `show_logo_labels` | bool | `true` | Whether the stage logo bar renders the group names (`주최` / `주관` / `후원`) as visible text. Admin toggle. **A missing key reads as `true`**, not `false` — rooms saved before this key existed were showing labels, and treating absence as "off" would silently change their stage screen. Non-bool values are rejected by `validate_stage_config`; on read they degrade to `true`. Turning it off hides the text only — logo `alt` text keeps the group name for screen readers. |
| `logo_groups` | list[object] | 3 empty groups | Fixed order, fixed label set: `주최`, `주관`, `후원`. Unknown labels and non-object elements are discarded on normalise. |
| `logo_groups[].label` | str | — | One of `주최` / `주관` / `후원`. |
| `logo_groups[].assets` | list[str] | `[]` | **Filenames only**, never paths. Each name resolves against `data/branding/{room_id}/`. Max 12 assets per room across all groups. |

Example:

```json
{
  "event_title": "2026 개발자 콘퍼런스",
  "event_subtitle": "A홀 기조연설",
  "caption_ratio": "1/4",
  "show_logo_labels": true,
  "logo_groups": [
    {"label": "주최", "assets": ["host-1.png"]},
    {"label": "주관", "assets": []},
    {"label": "후원", "assets": ["sponsor-a.svg", "sponsor-b.png"]}
  ]
}
```

---

### Filesystem store: `data/branding/{room_id}/` `[CONFIRMED]`

> Appended 2026-08-23. Delivered by ISSUE-38.

Logo binaries are **not** stored in SQLite. They live on the filesystem under
`data/branding/{room_id}/`, with only their filenames referenced from
`rooms.stage_config.logo_groups[].assets`. Rationale: the assets are static, are requested
by the browser as ordinary image URLs (`GET /branding/{room_id}/{filename}` on the aiohttp
server), and would otherwise force multi-megabyte BLOBs through a single-file SQLite
database that already has no WAL-era concurrency headroom to spare for large writes.

| Aspect | Decision |
|--------|----------|
| Root | `data/branding` (overridable via `BRANDING_DIR`). Sits inside the existing `./data` Docker volume, so assets survive container restarts alongside `app.db`. |
| Layout | One directory per room, created lazily on first upload. |
| Naming | Sanitised to `[A-Za-z0-9._-]`; collisions get a numeric suffix (`logo-2.png`) rather than overwriting an existing file. |
| Accepted formats | `.png`, `.jpg`, `.jpeg`, `.svg`, each verified against its content (PNG/JPEG magic bytes; SVG must begin with `<?xml` or `<svg` after leading whitespace). |
| Limits | 2 MB per file, 12 assets per room. |
| Containment | `Path.resolve()` must stay under the resolved room directory; symlinks are rejected; traversal attempts return a generic 404 (NFR-028, RL-006). |
| Referential integrity | None enforced. A filename listed in `stage_config` whose file has been deleted simply fails to load; the stage page hides that single `<img>` via `onerror` (ISSUE-40). Deleting a room does **not** currently remove its branding directory — see Observations. |

---

## Indexes

| Table | Index | Columns | Type | Source |
|-------|-------|---------|------|--------|
| `users` | (implicit) PRIMARY KEY | `id` | Unique B-tree | `database.py:42` (SQLite auto-creates) |
| `users` | (implicit) UNIQUE constraint | `username` | Unique B-tree | `database.py:43` (SQLite auto-creates) |
| `usage_logs` | (implicit) PRIMARY KEY | `id` | Unique B-tree | `database.py:61` (SQLite auto-creates) |

No explicit `CREATE INDEX` statements exist anywhere in the codebase. `[CONFIRMED]`

Notable absence: `usage_logs.user_id` has no explicit index. Every per-user log query (`WHERE user_id = ?`) and the cascade delete (`DELETE FROM usage_logs WHERE user_id = ?`) performs a full table scan. At low row counts this is inconsequential, but it is a latent performance issue. `[INFERRED]`

Notable absence: `usage_logs.created_at` has no index. The date-range filter in `get_usage_stats()` (`WHERE created_at >= ? AND created_at <= ?`) also performs a full table scan. `[INFERRED]`

---

## Migrations

- Framework: None `[CONFIRMED]`
- Total migrations: 0 `[CONFIRMED]`
- Latest: N/A
- Pending: N/A
- Rollback support: N/A

Schema is initialised entirely by `DatabaseManager.init_database()` using `CREATE TABLE IF NOT EXISTS`. This means the schema can only be created, never altered through any provided mechanism. Adding or removing columns requires manual SQLite DDL (`ALTER TABLE ... ADD COLUMN`) applied directly to the database file, with no tooling or history. `[CONFIRMED]` — `database.py:35-76`

Appended 2026-08-23: since that scan, a convention has emerged in place of a framework — each schema addition gets a private, idempotent `_migrate_add_*` method that gates `ALTER TABLE` behind `PRAGMA table_info(<table>)` and is called at the end of `init_database()`. Existing instances: `_migrate_drop_legacy_columns` (ISSUE-20), `_migrate_add_usage_logs_room_id` (ISSUE-29), `_migrate_add_room_output_lang_columns` (ISSUE-30), `_migrate_add_room_viewer_metric_columns` (ISSUE-33), `_migrate_add_room_stage_config` (ISSUE-37, `[CONFIRMED]` — adds `rooms.stage_config TEXT NOT NULL DEFAULT '{}'`; idempotency covered by `tests/test_migration_idempotency.py`). There is still no version table and no down-migration; rollback means dropping the column manually (SQLite ≥ 3.35 supports `ALTER TABLE ... DROP COLUMN`, and Python 3.11 bundles it).

---

## Seed Data

| Table | Data | Source |
|-------|------|--------|
| `users` | One admin account is conditionally inserted on first process start if `ADMIN_USERNAME` and `ADMIN_PASSWORD` environment variables are set | `database.py:499-526`, `database.py:541-542`; `[CONFIRMED]` — `tests/test_database.py:TestInitAdminFromEnv` |

No fixture files, SQL seed scripts, or migration-embedded seed rows exist. `[CONFIRMED]`

---

## Observations

| Observation | Evidence | Impact |
|-------------|----------|--------|
| `salt` and `hash_type` columns are legacy artefacts — written only with their defaults and never read by any application query | `database.py:46,54`; no SELECT or UPDATE references these columns in any model method | Neutral: dead storage; if the schema were ever migrated, these columns could be dropped cleanly |
| SQLite foreign key enforcement is disabled — `PRAGMA foreign_keys = ON` is never set, so the `FOREIGN KEY (user_id) REFERENCES users(id)` declaration on `usage_logs` has no runtime effect | `database.py:25-33` (no PRAGMA in `get_connection`); `database.py:311-317` (manual cascade) | Negative: inserting a `usage_log` with a non-existent `user_id` would silently succeed |
| Cascade deletion is implemented in application code rather than the database engine; if a future code path deletes a user without calling `User.delete_user()`, orphaned `usage_logs` rows will accumulate | `database.py:311-318` | Negative: data integrity depends on all callers using the correct model method |
| No index on `usage_logs.user_id` — the most common filter column in log queries and cascade deletes | All `get_user_logs`, `get_all_user_logs`, `delete_user` queries; `database.py:315,365,391` | Negative: full-table scans at scale; currently benign at low row counts |
| No index on `usage_logs.created_at` — used as a range filter in `get_usage_stats()` | `database.py:451-461` | Negative: date-range statistics queries will scan the full `usage_logs` table |
| `metadata` is stored as a raw JSON TEXT blob with no schema validation; any caller can write any shape | `database.py:338,379,383,404` | Negative: type drift across deployments is invisible at the database layer |
| `usage_limit_seconds = 0` for admin accounts is not enforced by the database — the `check_usage_limit` admin bypass depends on the role field being read correctly before the arithmetic check; if call order were reversed, an admin would always be denied (remaining = 0 - 0 = 0, which is not >= any positive duration) | `auth.py:168-171`, `database.py:271-283` | Negative: correctness depends on call order in application logic, not a database constraint |
| No WAL mode or connection pooling — SQLite default journal mode (`DELETE`) means concurrent write transactions block each other; in a multi-tab Streamlit deployment this could cause write contention | `database.py:25-33`; no `PRAGMA journal_mode=WAL` | Negative: risk of `database is locked` errors under concurrent usage |
| `pandas` is imported in `admin.py` for DataFrame display and CSV export but is not listed in `pyproject.toml` as a dependency | `admin.py:8`; `pyproject.toml:7-18` | Negative: clean environment installs will fail at admin dashboard load with `ModuleNotFoundError` |
| `rooms.stage_config` is the third JSON-in-TEXT blob in the schema (after `usage_logs.metadata` and `rooms.output_langs`) with no column-level validation; shape drift is invisible to the database | `stage_config.py`, `database.py:Room.get_stage_config/update_stage_config` (ISSUE-37, shipped) | Neutral-to-negative: mitigated by routing every read/write through the pure `stage_config.normalize_stage_config()` so malformed values degrade to defaults instead of raising |
| `[PLANNED]` Branding assets are stored on the filesystem with no referential integrity against `rooms`; deleting or closing a room leaves `data/branding/{room_id}/` behind | ISSUE-38 | Negative: orphaned asset directories accumulate over time. Acceptable at event-scale volumes (tens of rooms, ≤12 small files each); a cleanup hook in `Room.force_close`/delete is deferred, not designed away |

---

## Access Patterns — appended 2026-08-23 `[PLANNED]`

| Pattern | Source | Operation | Frequency | Confidence |
|---------|--------|-----------|-----------|------------|
| Read stage branding when rendering `/stage/{room_id}` | `sse_broadcast._handle_stage` → `Room.get_by_id` | read (rooms) | Once per stage page load (once per event, effectively) | `[PLANNED]` |
| Write stage branding from the admin form | `admin._render_admin_room_stage_config` → `Room.update_stage_config` | write (rooms) | Admin action, very low | `[PLANNED]` |
| Serve a logo asset | `branding_routes.handle_branding_asset` → `branding_assets.resolve_asset_path` → filesystem read | read (filesystem) | Once per logo per stage page load; then browser-cached for 300 s | `[CONFIRMED]` |
| Save / delete a logo asset | `admin` form → `branding_assets.save_asset` / `delete_asset` | write (filesystem) + write (rooms) | Admin action, very low | `[PLANNED]` |

## Access Patterns — appended 2026-08-30 `[PLANNED]`

> ISSUE-52 / ISSUE-53. **No schema change**: the language write-back targets three columns that
> already exist, and the caption font scale is not persisted at all. Recorded here because the
> `rooms` table gains its first write path driven by the live caption session rather than by an
> administrator.

| Pattern | Source | Operation | Frequency | Confidence |
|---------|--------|-----------|-----------|------------|
| Record the operator's session languages on the room row | `websocket_handler._authenticate_client` and the `language_update` branch → `Room.update_session_languages` | write (rooms: `input_lang`, `output_lang`, `primary_output_lang` in one statement) | Once per WebSocket authentication, plus once per operator language change; skipped entirely when the values are unchanged | `[PLANNED]` |
| Resolve the caption channel when rendering `/stage/` or `/view/` | `sse_broadcast._handle_stage` / `_handle_view` → `Room.get_by_id` → `primary_output_lang` | read (rooms) | Once per page load | `[CONFIRMED]` |
| Read / write live presentation control state (primary language, caption scale) | `BroadcastManager.set_control` / `get_control` | **in-process memory only — no database, no filesystem** | Per operator control action; read once per SSE connection for the snapshot frame | `[PLANNED]` |

**Why the caption font scale is not a column and not a `stage_config` key.** `rooms.stage_config` is the administrator's pre-event branding record, and the admin form (ISSUE-39) saves it through a `normalize → save` round trip; `stage_config.normalize_stage_config` silently discards keys it does not know (`stage_config.py:76-81`). A venue-time value written there by the operator would be reset to its default the next time an administrator edited branding — with no error and no log line. That is two writers over one blob with no named authority (RL-024 / RL-025). Persisting it properly would mean touching `DEFAULT_STAGE_CONFIG`, `normalize_stage_config`, `validate_stage_config`, the admin form and a migration, for a value whose natural lifetime is one event.

The reload case that persistence would have solved is handled instead by the SSE connect snapshot (FR-084): a stage page that reloads mid-event receives the current language and scale as its first frame. The one residual gap is stated rather than designed away — **a server process restart resets `caption_scale` to 1.0**, and the operator's recovery is a single slider move. The language survives a restart, because ISSUE-52 writes it to the room row.

**Why `rooms.output_langs` stays untouched.** It is written by nothing on the serving path and read by nothing: `_supported_output_langs` (`sse_broadcast.py:328`) deliberately uses the global `translation.SUPPORTED_OUTPUT_LANGS` so viewers can choose any supported language regardless of room configuration (#91/#92), and `_coerce_output_langs_list` (`:315`) has no caller. The obvious instinct when adding language write-back is to keep this column in sync too; doing so would re-couple viewer language choice to room configuration and quietly undo that decision. ISSUE-52 carries an acceptance criterion asserting the column's value is byte-identical after the write.
