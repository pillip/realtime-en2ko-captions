# Review Notes — ISSUE-38 (PR #123)

**Reviewer**: Claude Opus 5 (automated, independent REVIEW phase)
**Date**: 2026-08-23
**Branch**: `issue/ISSUE-38-branding-asset-store`
**PR Size**: +1472 -0 across 6 files
- `branding_assets.py` (NEW, 435 LOC): `save_asset` / `list_assets` / `delete_asset` / `resolve_asset_path` / `build_asset_headers`
- `branding_routes.py` (NEW, 51 LOC): `handle_branding_asset` aiohttp handler
- `sse_broadcast.py` (+2): one import + one `app.router.add_get`
- `tests/test_branding_assets.py` (NEW, 167 tests after review)
- `tests/test_sse_broadcast.py` (+234, 16 tests)
- `CLAUDE.md` (+1): `BRANDING_DIR` env var

**Verdict**: **Approve.** No Critical or High findings. One Medium test-quality gap (an
untested NFR-028 control) was found by mutation testing and fixed in this review. All 9
Acceptance Criteria and all 6 `#### Tests` bullets are satisfied by real, discriminating
assertions — verified by deleting each control from the source and confirming a test fails.

---

## Scope

Verified against `issues.md` ISSUE-38, `docs/requirements.md` FR-077 / FR-078 / **NFR-028**,
`docs/data_model.md` *Filesystem store: `data/branding/{room_id}/`*, and
`docs/test_plan.md` Gap 8 (TC-053 … TC-056). Review lens: `docs/review_lessons.md`
RL-001 … RL-016.

**Scope discipline: clean.** `git diff origin/main...HEAD --name-only` returns exactly the
6 files above. No ISSUE-39/40/41/42 scope. `components/stage.html` untouched.
`issues.md` / `STATUS.md` / `CHANGELOG.md` **not** committed to the feature branch.
The `sse_broadcast.py` diff is the minimal 2 lines, so it will not collide with ISSUE-40 (PR #122).

---

## Findings

| ID | Severity | Location | Finding | Status |
|----|----------|----------|---------|--------|
| R-01 | Medium | `branding_assets.py:213` (guard) / `tests/test_branding_assets.py:224`, `tests/test_sse_broadcast.py:1293` (tests) | **Symlink rejection had zero discriminating coverage (RL-004 vacuous pass).** All three symlink tests pointed the link *outside* the room directory, so the 404 came from the `is_relative_to()` containment check, not from the symlink guard. Deleting `if candidate.is_symlink(): return None` made **no test fail**. NFR-028 names symlink rejection as a distinct control; it could have been removed silently. | **fixed** |
| R-02 | Low | `branding_assets.py:309` | The `".." in value` rejection rule in `_reject_path_signatures` had no discriminating test — deleting it left the suite fully green. The rule is real defence (it is what turns `"a..b.png"` into a rejection instead of a stored file), but it was unpinned. | **fixed** |
| R-03 | Low | `branding_assets.py:221`, `branding_assets.py:251` | The `except ValueError` clauses that deliberately return without logging had no test. Collapsing them into the logging `except Exception` left the suite green — yet that regression turns every malformed request on an **unauthenticated public route** into a server-log write (log-flood / disk-fill vector). | **fixed** |
| R-04 | Low | `branding_assets.py:284` | No `X-Content-Type-Options: nosniff` on asset responses. Not required by NFR-028 or any AC; modern browsers do not sniff `image/*` into HTML and the SVG path is already neutralised by CSP. Cheap hardening worth adding when the upload UI lands (ISSUE-39). | not-fixed (follow-up) |
| R-05 | Low | `branding_routes.py:39` | **TOCTOU**: `resolve_asset_path()` runs `is_symlink()` → `resolve()` → `is_file()`, then the route calls `path.read_bytes()`. An attacker who can already write inside `data/branding/{room_id}/` could swap the file for a symlink in that window. Requires local filesystem write access, which is outside the HTTP threat model for this route. | not-fixed (accepted) |
| R-06 | Low | `branding_assets.py:380` | `_contained_room_dir()` checks containment against the branding **root**, so a room directory that is itself a symlink to a *sibling room* resolves inside the root and is accepted — a cross-room read. This is exactly the rule the spec prescribes (`is_relative_to(root)`), and it also requires local FS write access. | not-fixed (accepted) |
| R-07 | Low | `branding_assets.py:108` | `_MAX_COLLISION_ATTEMPTS = 100` is an unreachable ceiling: the 12-asset-per-room cap bounds same-stem collisions at ~13, so attempts 14–100 and the trailing `raise` are dead (already marked `# pragma: no cover`). | not-fixed (harmless) |
| R-08 | Low | `branding_assets.py:368`, `branding_assets.py:371` | `_MAX_ROOM_ID_LEN` and `_reject_path_signatures(value, "룸 식별자")` are fully subsumed by the `_DISALLOWED_CHARS.search()` check three lines below — `/`, `\`, and control characters are all outside `[A-Za-z0-9._-]`. Deleting either leaves the suite green *and* leaves the security property intact. Redundant, but deliberate defence-in-depth on a trust boundary. | not-fixed (accepted) |
| R-09 | Low | `branding_assets.py:285` | The `application/octet-stream` fallback in `build_asset_headers()` is unreachable from the route — the extension whitelist runs inside `resolve_asset_path()` before a path is ever returned. Directly unit-tested, so not dead for coverage purposes. | not-fixed (harmless) |

### Fix applied

`tests/test_branding_assets.py` +68 lines, 4 new tests (source code unchanged — every
finding fixed here was a *missing test*, not a defect):

- `test_resolve_rejects_symlink_that_points_inside_the_room` (:237) — the only input where
  the two defences diverge. Asserts the link target *does* pass containment
  (`.resolve().is_relative_to(room)`) so the test cannot silently degrade into another
  containment test, then asserts `resolve_asset_path` / `delete_asset` / `list_assets` all
  reject it while the real file stays servable (no over-blocking).
- `test_resolve_rejects_symlink_to_sibling_room` (:260) — cross-room isolation.
- `test_dotdot_in_filename_is_rejected_not_stripped` (:272) — pins `..` as a *rejection*,
  not a strip, for separator-free names (`a..b.png`, `..logo.png`, `logo...png`).
- `test_rejected_public_requests_do_not_write_server_logs` (:287) — pins the no-log
  rejection path across the whole hostile-name corpus.

---

## Code Review

### Strengths

- **RL-001 / RL-005 honoured properly.** `branding_assets.py` imports only `os`, `re`,
  `pathlib` and `stage_config`. Verified empirically: importing it pulls in **no**
  `streamlit` / `aiohttp` / `sqlite3` / `boto3` / `pandas`. `BRANDING_DIR` is read inside
  `_branding_root()` per call, not at module scope — `test_branding_dir_is_read_per_call_not_at_import`
  proves it by flipping the env var between two `save_asset` calls. Extraction came *with*
  its tests, not after.
- **RL-015 done for real, not guessed.** The `except` clauses match what fuzzing actually
  produces. Independently re-fuzzed with ~1000 (room_id × filename) combinations including
  `None` / `int` / `bytes` / `list`, NUL bytes, 100 000-char names, NFD-decomposed Korean,
  surrogate-escape sequences from overlong UTF-8, fullwidth `．．`, and `"../" * 10000`:
  **0 non-`ValueError` escapes** from `save_asset`, **0 exceptions** from
  `resolve_asset_path` / `list_assets` / `delete_asset`.
- **No constant duplication.** `MAX_ASSETS_PER_ROOM` is imported from `stage_config`
  (:49) rather than redeclared, so the validator and the writer cannot drift.
- **Sanitiser rejects rather than strips.** `_reject_path_signatures` refuses `/`, `\`,
  `..` and control characters instead of silently unwrapping to a basename. This is the
  correct call — `"a/b.png"` silently becoming `"b.png"` would make the defence
  accidental. The module docstring says exactly this, and the behaviour matches.
- **Ordering of `save_asset` validation is deliberate and correct.** Size → name → magic
  bytes → count cap → containment, and only then `mkdir`. The AC "부분 파일조차 생성되지
  않는다" is met in the strong sense: a rejected 3 MB upload does not even create the
  branding root, which `test_three_mb_png_rejected_before_directory_is_created` asserts
  via `assert not branding_root.exists()`.
- **Exclusive create.** `open(candidate, "xb")` (:427) is genuine — mutating it to `"wb"`
  fails 3 collision tests. Collision names are built from an already-sanitised
  `[A-Za-z0-9._-]` stem plus a whitelisted suffix, so the generated name is not injectable.

### Correctness notes

- `_suffix_of()` recomputes the suffix from the *sanitised* name via `rfind(".")`. Safe
  because the suffix is appended last from a 4-value whitelist; dotted stems (`a.b.png`)
  round-trip correctly and collide to `a.b-2.png`.
- 100 % line coverage on both new modules is **real**, not line-coverage-without-assertion:
  every mutation listed below is caught by a named test. The four `# pragma: no cover`
  markers are honest — with the stem (80) and room-id (64) length caps in place, the
  defensive `except Exception` arms and the `_MAX_COLLISION_ATTEMPTS` exhaustion path are
  genuinely unreachable.
- Test isolation is clean: every test monkeypatches `BRANDING_DIR` into `tmp_path`. After a
  full suite run, `git status` is clean and no `data/branding/` directory exists in the
  worktree.

---

## Security Findings

NFR-028 is the contract for this issue. Each control was verified by **execution**, not by
reading, and then by **deletion** (mutation) to confirm a test defends it.

### Path traversal — the sanitiser is confirmed to be the sole defence

The implementer's claim that aiohttp's router does not stop `%2F` is **true**. Measured on
aiohttp 3.13.5 / yarl 1.23.0, `GET /branding/r1/..%2F..%2Fapp.db` arrives at the handler
with `match_info["filename"] == "../../app.db"` — fully decoded, handler invoked. Same for
`%2e%2e%2f` → `"../app.db"`, `%5C` → `"..\\..\\app.db"`, `%00` → `"\x00.png"`, and
`/branding/%2e%2e%2f%2e%2e%2fetc/logo.png` → `match_info["room_id"] == "../../etc"`.
Double-encoded `%252F` arrives literally as `"%2F..%2Fapp.db"`.

`tests/test_sse_broadcast.py::test_traversal_payload_actually_reaches_the_handler` is a
**genuine** RL-004 guard: it spies on `resolve_asset_path`, asserts the handler was called
exactly once, asserts the traversal signature survived into the payload, and asserts the
sanitiser is what rejects it. It is not a "the router already 404'd" vacuous pass.

`room_id` is sanitised and contained **identically** to `filename` — `_sanitize_room_id()`
applies the same rejection rules plus a strict `[A-Za-z0-9._-]` whitelist and a leading-dot
ban. A traversal via `room_id` is not possible.

**Containment fuzz result**: across ~1000 hostile (room_id × filename) pairs, `save_asset`
created **0 files outside the branding root**, `resolve_asset_path` returned **0 paths
outside the root**, and the out-of-root decoy `app.db` was never read, modified, or deleted.

### Magic bytes and hostile SVG

- PNG (`\x89PNG\r\n\x1a\n`) and JPEG (`\xff\xd8\xff`) prefixes are exact.
- SVG: `data.removeprefix(BOM).lstrip()` must then start with `<?xml` or `<svg`. A leading
  `<!-- … -->` comment, a bare `<!DOCTYPE`, or a leading `<script>` does **not** pass —
  `test_svg_that_is_actually_html_rejected` covers the last case. A BOM alone does not
  bypass it (the prefix is stripped, then the same check applies).
- A polyglot **can** still be built — `<?xml …?>` followed by arbitrary markup is accepted
  by design, because that is what a real SVG looks like. This is precisely why the CSP is
  the load-bearing control, and it holds: an SVG containing `onload="alert(1)"`,
  `<script>alert(2)</script>` **and** an external `<image href="http://evil/x"/>` was stored
  and then served with `Content-Security-Policy: default-src 'none'; style-src
  'unsafe-inline'`. `default-src 'none'` fills in for `script-src`, which blocks inline
  scripts, inline event handlers, and `javascript:` URLs; it also blocks the external
  fetch. `style-src 'unsafe-inline'` cannot exfiltrate because no other directive permits
  an outbound request.
- CSP is on **every** SVG response path: the route is the only serving path, and
  `build_asset_headers(path.name)` is called unconditionally on the 200 branch with a name
  whose extension already passed the whitelist.

### Caps

- **2 MB cap is enforced before any filesystem access** (`branding_assets.py:145`, ahead of
  `mkdir` at :177). Confirmed: a rejected oversize upload leaves no partial file *and* no
  stray room directory *and* no branding root.
- **12-asset cap** counts across the whole room via `list_assets()` and is not per-group.
  A check-then-write race could admit a 13th asset under true concurrency; the only writer
  is the single-threaded Streamlit admin form (ISSUE-39), so this is race-safe enough for
  its context.

### RL-006 — no information leakage

Measured raw responses on every failure path (traversal, unknown room, unknown file,
symlink, forced `OSError`): body is always exactly `not found` (9 bytes), and neither the
body nor any response header contains the branding root, the temp path, the decoy DB
contents, an errno, an exception repr, or a traceback. `save_asset`'s `OSError` handler
(:179-184) logs the errno server-side and re-raises a generic Korean `ValueError` — a test
asserts `"Errno" not in message` and `str(root) not in message`.

---

## Over-Engineering

Minimality audit of the diff. The module is belt-and-braces, but every redundant layer is a
control that NFR-028 names explicitly, so removal would trade a documented security property
for ~10 lines. **Recommendation: keep all of it.**

- `branding_assets.py:108: [yagni] _MAX_COLLISION_ATTEMPTS = 100 → 16` — the 12-asset room
  cap bounds same-stem collisions at ~13; attempts 14–100 and the trailing `raise` are
  unreachable (R-07).
- `branding_assets.py:368,371: [shrink] _MAX_ROOM_ID_LEN + _reject_path_signatures(room_id) → drop`
  — both are fully subsumed by `_DISALLOWED_CHARS.search()` three lines below; deleting
  either keeps the suite green *and* the security property intact (R-08). Kept as
  defence-in-depth on a trust boundary.
- `branding_assets.py:285: [yagni] "application/octet-stream" fallback` — unreachable from
  the route, since the extension whitelist gates `resolve_asset_path` (R-09).

Net removable: **~10 lines**, all safety-adjacent. The minimality axis never overrides
safety, so none of these is recommended for removal.

`branding_routes.py` as a separate 51-line module is **justified**, not speculative
generality: it keeps `sse_broadcast.py`'s diff to 2 lines while ISSUE-40 (PR #122) is
concurrently editing that same file. No unused parameters, no dead branches, no duplicated
constants.

---

## Test quality

179 tests for two small modules is a ratio that warrants suspicion, so it was checked by
**mutation testing** rather than by reading: 22 mutations were applied to the source, the
branding tests run, and the source reverted.

**Result: 18 / 22 mutations killed before this review; 4 survived; all 4 now killed.**

| Mutation | Before | After |
|----------|--------|-------|
| neuter `_reject_path_signatures` separator branch | 3 fail | 3 fail |
| drop `is_relative_to` in `_contained_room_dir` | 1 fail | 1 fail |
| drop `is_relative_to` in `resolve_asset_path` | 1 fail | 1 fail |
| drop SVG CSP header | 2 fail | 2 fail |
| raise `MAX_ASSET_BYTES` ×100 | 1 fail | 1 fail |
| magic bytes always match | 3 fail | 3 fail |
| `open(…, "xb")` → `"wb"` | 3 fail | 3 fail |
| drop `isinstance(filename, str)` | 4 fail | 4 fail |
| drop 12-asset cap | 1 fail | 1 fail |
| leak exception text into 404 body | 1 fail | 1 fail |
| drop stem truncation | 3 fail | 3 fail |
| drop `room_id` charset check | 2 fail | 2 fail |
| drop non-bytes payload check | 1 fail | 1 fail |
| drop extension whitelist | 1 fail | 1 fail |
| `list_assets` includes symlinks / any ext | 3 fail | 3 fail |
| drop `Cache-Control` | 2 fail | 2 fail |
| drop route registration | 5 fail | 5 fail |
| drop control-char rejection | 2 fail | 2 fail |
| drop `_DISALLOWED_CHARS.sub` on stem | 1 fail | 1 fail |
| drop `room_id` leading-dot ban | 1 fail | 1 fail |
| drop BOM `removeprefix` | 1 fail | 1 fail |
| drop `is_file()` check | 5 fail | 5 fail |
| drop `Content-Type` mapping | 7 fail | 7 fail |
| **drop `is_symlink()` guard** | **0 — SURVIVED** | **1 fail** |
| **drop `".."` rejection** | **0 — SURVIVED** | **1 fail** |
| **drop `except ValueError` (resolve)** | **0 — SURVIVED** | **1 fail** |
| **drop `except ValueError` (list_assets)** | **0 — SURVIVED** | **1 fail** |

No test asserts only `is not None` or `assert True`. The parametrised blocks are not
inflation — the `HOSTILE_NAMES` / `HOSTILE_ROOM_IDS` corpora each cover a distinct failure
mode (`TypeError` from `re.sub`, `ValueError` from NUL bytes, `OSError(ENAMETOOLONG)`), and
`test_save_asset_raises_valueerror_only` fails loudly on a non-`ValueError` escape rather
than swallowing it.

---

## Acceptance Criteria verification

All 9 ACs verified by live execution against a real `build_sse_app()` test server, not by
reading test names.

| AC | Result |
|----|--------|
| `filename="../../app.db"` rejected, nothing created outside `data/branding/` | PASS (fuzz: 0 leaks over ~1000 pairs) |
| `GET …/..%2F..%2Fapp.db` → 404, no DB content / no internal path | PASS (measured body = `not found`, headers clean) |
| 3 MB PNG → `ValueError`, no partial file | PASS (branding root not even created) |
| `.png` extension over non-PNG bytes → rejected | PASS |
| 13th asset rejected, existing 12 intact | PASS (reason string contains `12` and `개`) |
| stored `logo.png` → 200 + `image/png` + byte-identical body | PASS (measured) |
| unknown room / file → 404 + generic message | PASS (measured, both paths) |
| `sponsor.svg` → `image/svg+xml` + exact CSP header | PASS (measured on a hostile SVG) |
| same name twice → `logo-2.png`, no overwrite | PASS |

All 6 `#### Tests` bullets map to named tests with real assertions (TC-053 … TC-056 covered).

---

## Gate

- `uv run pytest -q` → **936 passed, 22 deselected**, coverage **93.70 %**
  (`branding_assets.py` 100 %, `branding_routes.py` 100 %)
- `uv run ruff check .` → All checks passed
- `uv run black .` → 52 files unchanged

---

## Follow-ups

- **R-04** `X-Content-Type-Options: nosniff` on branding asset responses — fold into
  ISSUE-39 when the upload UI lands. Low.
- **R-05 / R-06** local-filesystem TOCTOU and symlinked-room-directory cross-room read —
  documented as accepted; revisit only if `data/branding/` ever becomes writable by a
  non-admin process.

## Lessons applied

- **RL-004** (weak assertions that pass trivially) — this is the finding. A security test
  whose subject is guarded by a *second, stronger* control passes for the wrong reason. The
  detection method that worked was mutation, not reading. **Prevention**: when a module
  advertises "N-stage defence", every stage needs a test using an input that the *other*
  stages let through; if no such input exists, the stage is redundant and should be labelled
  as defence-in-depth rather than counted as tested.
- **RL-015** — applied correctly here for the first time (fuzz-then-write-the-except, with
  the observed exception types recorded in the docstring). Worth keeping as the template.
- **RL-001 / RL-005 / RL-006** — all satisfied; no new lesson needed.

---

# Review Notes — ISSUE-37 (PR #119)

**Reviewer**: Claude Opus 5 (automated, DEGRADED review path — correctness + security + minimality in one pass)
**Date**: 2026-08-23
**Head reviewed**: `25dfae1` → fixes at `a522450`
**PR Size**: +987 -5 across 5 files (well under the 500-line *source* review threshold; ~700 of those lines are tests)
- `stage_config.py` (NEW, 187 LOC): `DEFAULT_STAGE_CONFIG`, `normalize_stage_config`, `validate_stage_config` + 4 private helpers
- `database.py` (+94 LOC): `_migrate_add_room_stage_config`, `Room.get_stage_config`, `Room.update_stage_config`
- `tests/test_stage_config.py` (NEW, 373 LOC, 49 tests)
- `tests/test_rooms_db.py` (+183 LOC, TC-052 surface)
- `tests/test_migration_idempotency.py` (+155 LOC, TC-049 surface)

**Diff hygiene**: `gh pr diff 119 --name-only` returns exactly the 5 expected files. No `issues.md`, `STATUS.md`, `docs/`, `data/`, or `.claude/` leakage onto the feature branch. Clean.

## Verdict

**approve-with-followups.** One High finding (unhandled `RecursionError` breaking the module's "never raises" contract on both layers) was found, fixed, and covered with regression tests that provably fail against the pre-fix code. Everything else is Medium/Low and forward-looking. All 7 ACs are genuinely satisfied by code AND by tests that fail if the code is broken.

## Findings

| # | Severity | Area | Finding | Status |
|---|----------|------|---------|--------|
| F-1 | **High** | Correctness / Availability | `json.loads` raises `RecursionError` (not `JSONDecodeError`) on deeply-nested JSON such as `'[' * 100000`. Both `stage_config._as_mapping()` and `Room.get_stage_config()` caught only `json.JSONDecodeError`, so the exception propagated to the caller — breaking the documented "절대 예외를 던지지 않는다" contract that ISSUE-39/40 are being told to rely on, and giving the stage page a mid-event crash path. | **FIXED** `a522450` |
| F-2 | Medium | Security (XSS, forward-looking) | `normalize_stage_config` deliberately does not HTML-escape `event_title`/`event_subtitle`, and `json.dumps` does not escape `<` or `/`. ISSUE-37's own Implementation Notes prescribe injecting the result "JSON 리터럴로" into the stage page — a `</script><script>…` payload in `event_title` breaks out of an inline `<script>` block. No exploit path exists today (no renderer). | DEFERRED — out of scope, follow-up for ISSUE-40 |
| F-3 | Low | Testing (RL-004) | `test_default_is_importable_without_side_effects` asserts only `not hasattr(module, "sqlite3")` / `not hasattr(module, "st")`. The name promises "no side effects" but the assertions would still pass if the module imported `streamlit` (unaliased), opened a file, or hit the network. Weak proxy for the invariant it guards. | DEFERRED (Low) |
| F-4 | Low | UX / API shape | `update_stage_config` validates the raw config, then normalises before persisting — so unknown labels and non-string assets are silently discarded while the method still returns `True`. An admin who typed a "협찬" group sees it vanish with no feedback. | DEFERRED — ISSUE-39 concern |
| F-5 | Low | API shape | `update_stage_config` returns a bare `bool`; the human-readable rejection reason is logged server-side and dropped. ISSUE-39's Streamlit form must call `validate_stage_config()` itself to show the user why. Worth documenting rather than changing. | DEFERRED (by design) |
| F-6 | Low | Concurrency | Two admins editing the same room concurrently is last-writer-wins with no detection. Inherent to the existing `Room.update_*` pattern; noted, not a regression. | DEFERRED |
| F-7 | Low | Validation semantics | Length limits use Python `len()` (code points). A ZWJ emoji sequence such as 👨‍👩‍👧‍👦 counts as 7 toward the 120 cap. Consistent with `docs/data_model.md` ("Max 120 chars") and acceptable; grapheme clustering is **not** demanded. Flagged only because the behaviour surprises. | DEFERRED (informational) |

### F-1 detail (the fix)

Reproduced end-to-end through the real DB read path before fixing:

```
get_stage_config -> *** RAISED RecursionError: maximum recursion depth exceeded
                    while decoding a JSON array from a unicode string
```

Fix is two words plus comments — widen both handlers to `(json.JSONDecodeError, RecursionError)`. The write path (`update_stage_config`) normalises before persisting so it cannot itself create such a value, but backup/restore, direct SQL, and any future import path can. Regression tests added at both layers; verified they fail (3 failures) with the fix stashed.

## Security Findings

| Severity | Finding |
|----------|---------|
| Medium | **F-2** stored-XSS carrier — see table above. `stage_config` is a persistence layer for operator-controlled text that ISSUE-40 will inject into HTML. Escaping correctly belongs at the render layer, so this is not a defect *in this PR*, but the invariant must be written down before ISSUE-40 ships. |
| — | **SQL injection: clean.** Both new queries (`SELECT stage_config FROM rooms WHERE id = ?`, `UPDATE rooms SET stage_config = ? WHERE id = ?`) bind every value. No f-string interpolation anywhere in the new code. The only f-string SQL in the diff is `PRAGMA table_info({table})` in a pre-existing test helper with literal table names — not attacker-reachable. |
| — | **RL-006: compliant.** Parse failures print `[Room] stage_config parse failed (room=…): {e!r}` to server stdout only. No exception text, file path, or `repr` reaches a return value or any caller-visible string. `get_stage_config` returns the default; `update_stage_config` returns a bare `bool`. Verified by `test_broken_json_logs_to_stdout_only`. |
| — | **Insecure deserialization: none.** `json` only; no `pickle`/`eval`/`yaml.load`. |
| — | **Dependencies: none added.** No new CVE surface. |
| — | **Secrets: none.** No credentials, keys, or tokens in the diff. |
| — | **Authorization**: `update_stage_config` has no permission check, consistent with every sibling `Room.update_*`. Authz belongs at the ISSUE-39 admin form (FR-079: "operators do not see this section"). Not a finding at this layer, but it is a hard requirement on ISSUE-39. |

## AC-by-AC Coverage

| AC | Requirement | Code | Test | Verdict |
|----|-------------|------|------|---------|
| 1 | Legacy DB gains `stage_config`; existing rows = `'{}'` | `_migrate_add_room_stage_config`, wired at `init_database()` end | `test_existing_room_rows_get_empty_object_default` (also asserts `name`/`status`/`timeout_minutes` survive), `TestMigrationFromLegacyDb` | **PASS** |
| 2 | Double `init_database()` — no `duplicate column name`, same column count | `PRAGMA table_info(rooms)` gate; commits only when `added` is non-empty | `test_double_init_adds_stage_config_exactly_once` (pre-asserts the column is absent first, so it cannot pass vacuously), `TestMigrationIdempotency` `count == 1`, parametrised `extra_init_calls` | **PASS** |
| 3 | NULL / empty / broken JSON → `DEFAULT_STAGE_CONFIG`, no exception | `get_stage_config` + `normalize_stage_config` | `test_unusable_values_degrade_to_default` (5 params), `test_broken_json_logs_to_stdout_only`, plus F-1's new deep-nesting case | **PASS** — the NULL branch is unreachable by construction (`NOT NULL`); `test_column_rejects_null` documents and proves this rather than faking it. Honest handling. |
| 4 | `caption_ratio="1/2"` → `(False, reason)`, `update` → `False`, DB unchanged | validate-before-write ordering | `test_invalid_ratio_rejected_and_row_untouched` **reads the raw row back and compares before/after** — the byte-identical check, not just `assert is False` | **PASS** |
| 5 | 121-char `event_title` → `(False, ...)` | `len(value) > limit` | `test_title_over_120_chars_is_rejected` **and** `test_title_at_the_120_char_boundary_passes`; verified empirically inclusive at 120/80, exclusive at 121/81 | **PASS** |
| 6 | Round trip preserves Korean/emoji exactly; `ensure_ascii=False` | `json.dumps(..., ensure_ascii=False)` | `test_round_trip_preserves_korean_and_emoji` (full dict equality, payload contains 🎉), `test_stored_json_is_not_ascii_escaped` (asserts the raw stored string contains the Hangul and no `\u`) | **PASS** |
| 7 | Unknown labels / non-dict elements discarded, 3 known labels in fixed order | `_as_logo_groups` | `test_unknown_labels_and_non_dict_elements_are_discarded`, `test_labels_are_reordered_to_the_fixed_order`, `test_unknown_labels_are_normalised_before_persisting` (through the DB) | **PASS** |

TC-049 / TC-050 / TC-051 / TC-052 all mapped and covered.

## Targeted verification performed

- **Import purity (RL-001/RL-005)**: `import stage_config` leaks **zero** of `sqlite3, streamlit, boto3, database, requests, aiohttp`. Touches no file, opens no DB. Tests import the module directly — no logic is re-implemented inline anywhere.
- **Shared mutable default**: mutating a returned `logo_groups[0]["assets"]`, replacing a nested `label`, and appending a 4th group all leave `DEFAULT_STAGE_CONFIG` byte-identical; `result["logo_groups"] is not DEFAULT_STAGE_CONFIG["logo_groups"]` and the same holds for each nested dict. No aliasing bug.
- **Hostile-input fuzz of `normalize_stage_config`**: 10 adversarial inputs (deep nesting, 1 MB string, `logo_groups` as dict, nested lists in `assets`, duplicate labels, unhashable `label`, `bytes`, `set`, non-dict elements). All returned the documented 4-key shape with labels exactly `["주최","주관","후원"]`. Only the two deep-nesting cases raised → F-1, now fixed. The unhashable-`label` case is safe by short-circuit ordering, not by luck.
- **Migration parity**: structurally identical to `_migrate_add_room_viewer_metric_columns` — same PRAGMA gate, same `added` list, same commit-only-on-change, same log line. Column type verified `TEXT NOT NULL DEFAULT '{}'` by reading `PRAGMA table_info` (`notnull == 1`).
- **Test harness extension**: `tests/test_migration_idempotency.py` was genuinely **extended** — `stage_config` assertions added to all 4 pre-existing test surfaces, and the new `_seed_legacy_db_with_rooms` *calls* `_seed_legacy_db` rather than duplicating it. The new seeder is necessary: the old harness has no `rooms` table at all, so it structurally cannot test AC1's "pre-existing room rows". Not a bolted-on duplicate.

## Over-Engineering (minimality axis)

`Lean already. Ship.`

**Net removable lines: 0.**

Two candidates were examined and deliberately rejected:

- **The 12-asset cap in `validate_stage_config` — VERDICT: KEEP, not scope creep.** It is absent from ISSUE-37's AC list, and FR-077 assigns *a* 12-asset cap to ISSUE-38. But those are two different enforcement points for one invariant: ISSUE-38 caps **files on disk** (`save_asset` rejects the 13th upload), while this caps **filenames in the blob**. Nothing stops a caller from writing 13 filenames into `stage_config` without uploading 13 files, so ISSUE-38's cap does not protect the blob. `docs/data_model.md` documents the cap directly under the `stage_config` blob shape and states the invariant "lives in application code" — this is that code. It is validation at a trust boundary, which the minimality axis never overrides, and it is tested at the exact 12-pass/13-reject boundary. Keeping it.
- **The double parse in `get_stage_config`** (`json.loads` there, then `normalize_stage_config` which can also parse strings) is *not* redundant: the issue's Implementation Notes explicitly require the room-scoped `[Room] stage_config parse failed (room=…)` log line, which the pure module cannot emit without taking a `room_id` it has no business knowing. The split is the correct seam.

No dead code, no single-implementation abstractions, no reinvented stdlib, no dependency doing a native job.

## Self-Review

- **Severity re-assessment**: F-1 held at High, not Critical — the only writer today (`update_stage_config`) normalises before persisting, so there is no live unauthenticated attack path; but it breaks an explicitly documented invariant that two downstream issues are being instructed to depend on, and the fix is trivial. F-2 held at Medium precisely because no render path exists yet (a finding with no exploit path is Medium at most); it becomes High the moment ISSUE-40 renders.
- **False-positive check**: F-1 was confirmed through the real `Room.get_stage_config` DB path, not just the pure function. The 12-asset "scope creep" hypothesis was actively investigated and *disproved*. Migration wiring, parameterisation, and aliasing were each probed for evidence of non-issue status and cleared.
- **Blind-spot scan**: initial passes produced nothing in authz, dependencies, concurrency, or error-message quality, so each was re-read specifically — yielding F-5 (reason string dropped), F-6 (last-writer-wins), and the authz note. Error messages are Korean, user-facing, and actionable; that is a genuine strength here.
- **Confidence: High.** Every claim above was verified by execution, not by eyeballing: import-purity probe, aliasing probe, 10-case fuzz, exact boundary sweep at 119/120/121 and 79/80/81, and a stash-revert proving the new tests fail against pre-fix code.

## Follow-up issues proposed

| Proposed severity | Description |
|---|---|
| **Medium** (becomes High at render time) | **ISSUE-40 must HTML-escape `event_title`/`event_subtitle` before injection.** `json.dumps` escapes neither `<` nor `/`, so the Notes' prescribed "JSON 리터럴로 주입" is `</script>`-breakout-prone. Escape `<`, `>`, `&` (or use `</` → `<\/`) when embedding in an inline `<script>`, and HTML-escape when writing into markup. |
| Low | Strengthen `test_default_is_importable_without_side_effects` to assert against `sys.modules` after a fresh import rather than `hasattr` (F-3). |
| Low | ISSUE-39's form should surface `validate_stage_config`'s reason string and warn when normalisation drops groups/assets (F-4, F-5). |

## Lessons applied

| Lesson | Application |
|--------|-------------|
| RL-001 | Verified empirically: `stage_config.py` imports zero heavy modules; tests import it directly instead of re-implementing its logic |
| RL-004 | Every new test checked against "would this pass if the function returned its input unchanged / the default?" — round-trip asserts full dict equality, rejection tests re-read the raw row, migration test pre-asserts column absence. One weak case found (F-3) |
| RL-005 | New pure module ships with 49 tests and 100% line coverage — not merely importable |
| RL-006 | Parse failures log server-side only; no exception text reaches any caller-visible value. Verified by test and by reading every return path |
| RL-014 | The review fix (F-1) ships with 3 regression tests, proven to fail when the fix is reverted |

---

# Review Notes — ISSUE-40 (PR #122)

**Reviewer**: Claude Opus 5 (independent code reviewer — correctness + security + minimality in one pass)
**Date**: 2026-08-23
**Head reviewed**: `1f3bd0d` (branch `issue/ISSUE-40-stage-composite-page`, diff vs `origin/main` = `9606b55`)
**PR Size**: +1243 −6 across exactly 4 files — 515 lines of implementation, 734 of tests. Under the 500-line *source* review threshold.
- `components/stage.html` (NEW, 379 lines)
- `sse_broadcast.py` (+136 −6): `_json_for_script`, `_render_stage_html`, `_handle_stage`, one router line, one docstring/call-site change inside `_render_viewer_html`
- `tests/test_stage_page.py` (NEW, 479 lines, 42 collected cases)
- `tests/e2e/test_stage_page_e2e.py` (NEW, 255 lines, 9 cases, `e2e` marker)

**Diff hygiene**: `git diff origin/main...HEAD --stat` returns exactly the 4 expected files. No `issues.md`, `STATUS.md`, `docs/`, `data/` or `.claude/` leakage onto the branch. Clean.

**Suite status**: `uv run pytest -q` → **795 passed, 31 deselected**, coverage 93.18% (gate 50%). `uv run pytest tests/e2e/test_stage_page_e2e.py -m e2e` → **9 passed** (Chromium available locally).

## Verdict

**request-changes.** One **High** layout defect (F-11) breaks the headline AC at the headline resolution with ordinary content, and the PR's own e2e suite structurally cannot catch it. The fix is one CSS line, verified by measurement.

Everything *security*-related is in good shape. The single highest-risk item — the RL-016 / ISSUE-37-F-2 XSS hand-off — is **genuinely discharged**. `_json_for_script` was attacked with 14 hand-built payloads at the render layer and 8 in a real Chromium instance; not one increased the `<script>`/`</script>` count or set a global. The RED claim was verified by executing the suite against a plain `json.dumps` (5 failures). 8 of 9 ACs are satisfied by code and by tests that provably fail against a broken implementation.

Remaining: one Medium context-confusion defect that the PR *introduced the correct tool for but did not finish applying* (F-1), one Medium test-coverage gap on the next link in the same RL-016 chain (F-3), and seven Lows — all follow-ups.

> **Correction, recorded in full**: my first pass concluded "approve-with-fixes, nothing blocks merge" and explicitly *disputed* the parallel ui-review's RL-017 entry. That conclusion was wrong on both counts. I had swept 8 viewports but only one content length. Re-testing with a 65-character Korean title reproduced the overflow immediately. The retraction is kept in place rather than deleted — see "Targeted verification / Retraction".

## Findings

| # | Severity | Area | Finding | Blocks merge |
|---|----------|------|---------|--------------|
| F-11 | **High** | Correctness (layout) — AC 3, AC 4 | `.stage-main` (`components/stage.html:54-59`) declares `grid-template-rows` but **no** `grid-template-columns`, so its single implicit column is `auto` and sizes to max-content. `min-width: 0` on `.stage-main` constrains its own box, not its implicit track. A long room name or `event_title` therefore grows the header **and the presentation area** past the left column and paints them over the caption column. Measured at 1920×1080: `.event-header` 1727.1px and `#stage-frame` 1695.1px against a 1440px column. `text-overflow: ellipsis` never fires; `html { overflow: hidden }` hides the evidence. | **YES** |
| F-1 | **Medium** | Security (escaper/context confusion) + Availability | `_render_stage_html` escapes the three *script-context* scalars with `html.escape` (`sse_broadcast.py:480,483,484`) while the new, correct `_json_for_script` is applied only to the two structured fields. `html.escape` is an HTML-markup escaper; entities are **not** decoded inside `<script>` raw text, and `\` is not escaped at all. | No |
| F-2 | Low | Correctness (template substitution) | Chained `str.replace` (`sse_broadcast.py:480-485`) lets an earlier-substituted value expand a later placeholder. A room named `{{STAGE_CONFIG_JSON}}` renders the entire config blob into `<title>` and `<h1>`. | No |
| F-3 | **Medium** | Testing (RL-004 / RL-016 chain) | The logo-filename → `<img src>` path — the *next* carrier in the RL-016 chain — has **zero** regression guard. Runtime behaviour is safe (verified), but nothing fails if `makeLogoImage` is later rewritten with `innerHTML`. | No |
| F-4 | Low | Testing (RL-004) | `test_empty_event_title_falls_back_to_room_name` cannot fail for the reason its name claims — it asserts the server-rendered `{{ROOM_NAME}}`, which is present for every room regardless of `event_title`. | No |
| F-5 | Low | Testing (RL-004) | `assert "object-fit: contain" in stage_html` (3 occurrences in the file) and `assert "min-height: 0;" in stage_html` (4 occurrences) pass even if the AC-relevant element loses the rule. | No |
| F-6 | Low | Testing (RL-012 guard) | The RL-012 guard bans only the `margin-top` / `margin-bottom` longhands. `margin: 12px 0;` on `.stage` would sail through and reintroduce exactly the PR #58 hotfix bug. | No |
| F-7 | Low | Robustness / AC-2 | The caption-column width is applied **only** by the inline `<script>`. If the bootstrap throws (see F-1) a `1/3` room silently renders at the `calc(100% / 4)` CSS fallback with no error. | No |
| F-8 | Low | UX (near-term, spec-compliant) | Until ISSUE-38 lands every logo 404s; per-image `remove()` degrades correctly but leaves an empty 76px bar with bare "주최 / 후원" labels. The PR's own e2e asserts this as intended behaviour. | No |
| F-9 | Low | Docs drift | `_json_for_script`'s docstring claims "Every server-rendered JSON literal in this module goes through here" — `sse_broadcast.py:692` does not (correctly so; it is not a script context). `docs/architecture.md:113` still says structured fields are "`json.dumps`ed". | No |
| F-10 | Low | DRY | `_DEFAULT_CAPTION_WIDTH = _CAPTION_WIDTHS["1/4"]` (`sse_broadcast.py:451`) re-states `stage_config.DEFAULT_CAPTION_RATIO` instead of importing it. | No |

### F-11 detail — the left column overflows onto the caption column with a realistic title

Reproduced against the live app in Chromium with `event_title` (and separately, `name`) set to a 65-character Korean conference title — well inside `stage_config.EVENT_TITLE_MAX_LEN = 120`, and room names have no length limit at all:

```
                     main.w   hdr.w    h1.w    col.x   pres.w   frame.w   ellipsized  OVERLAP
short     1920x1080: 1440.0   1440.0   1352.0  1440.0  1440.0   1440.0    False       no
longtitle 1920x1080: 1440.0   1727.1   1639.1  1440.0  1727.1   1695.1    False       YES
longname  1920x1080: 1440.0   1727.1   1639.1  1440.0  1727.1   1727.1    False       YES
longtitle 3440x1440: 2580.0   2580.0   2492.0  2580.0  2580.0   2307.5    False       no
```

`col.x = 1440` is the caption column's left edge. At 1920×1080 — the resolution AC 3 names first, and the standard venue projector — the header **and the 16:9 presentation frame** extend to 1727px, i.e. 287px of the deck is painted over the captions. It does not reproduce at 3440×1440 because the wider column happens to fit this title, which is precisely why the parametrised e2e passes.

Three compounding factors make it silent:
1. `html, body { overflow: hidden }` suppresses the scrollbar (`scrollWidth == clientWidth == 1920`).
2. `.event-title`'s `text-overflow: ellipsis` never fires (`ellipsized = False`) — ellipsis needs a constrained containing block, and the block grew instead.
3. `container-type: size` on `.presentation` means the frame is sized from the *overflowed* container, so it stays a perfect 16:9 while being in the wrong place. The ratio assertion still reads 1.7778.

**Root cause**: `.stage-main { display: grid; grid-template-rows: auto minmax(0,1fr) auto; min-width: 0; }`. With no `grid-template-columns`, the implicit column is `auto` = `minmax(auto, max-content)`. The issue's own Implementation Notes anticipated the *row* axis (`minmax(0, 1fr)`, `min-height: 0`) and the author applied that correctly; the column axis was left implicit. `min-width: 0` on the grid *container* does not constrain a track inside it.

**Fix — one line**, verified by injecting it at runtime into the real page:

```css
.stage-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr);   /* ← add */
  grid-template-rows: auto minmax(0, 1fr) auto;
  ...
}
```

With it, all six long-content cases clamp to 1440 / 2580, `OVERLAP=no`, and `ellipsized=True` — the ellipsis the CSS already asked for finally works.

**Required test alongside the fix** (this is the RL-017 lesson): the e2e suite must gain a long-content room fixture, and the containment assertion must compare against the **sibling boundary**, not the parent:

```python
assert frame["x"] + frame["width"] <= caption_column["x"] + 1
```

The current `frame["width"] <= area["width"] + 1` cannot fail for this bug — see the retraction note below.

### F-1 detail — the escaper is right for the markup context, wrong for the script context

`{{ROOM_ID}}`, `{{PRIMARY_LANG}}` and `{{INITIAL_STATE}}` appear **only** inside the inline `<script>` (`components/stage.html:266,268,269`); `{{ROOM_NAME}}` appears **only** in markup (`:6,:346`). The separation is clean, which makes the fix safe — but the PR applies the markup escaper to all four.

Two consequences, both reproduced:

1. **Silent data corruption.** `room_id = 'a"b'` renders as the JS string literal `"a&quot;b"`. `CONFIG.room_id` is used to build `/branding/{room_id}/…` today and will build `/stream/{room_id}?lang=` in ISSUE-41. The value is wrong, not merely ugly.
2. **Bootstrap kill.** `\` is untouched by `html.escape`, so a trailing backslash escapes the closing quote. Verified in Chromium against the live app:

```
GET /stage/bs%5C
  http status   : 200
  CONFIG defined: False
  pageerrors    : ['Invalid or unexpected token']
  computed col w: 320px        # stuck on the CSS fallback, i.e. F-7 realised
```

The whole `<script>` block dies: no caption width, no title, no logos, and the `initial_state === "closed"` branch never runs — a closed room would render as if it were live.

**Why Medium and not High**: every precondition fails today. `room_id` is `secrets.token_urlsafe(8)` (`room_manager.py:33`, charset `[A-Za-z0-9_-]`) and must match a DB row or the handler 404s first; `primary_output_lang` has a `DEFAULT 'ko'` column and **no writer anywhere in the codebase** (grep: only the migration); `status` is enum-validated in `Room.update_*`. There is no exploit path, so per house policy this is Medium — the same rating ISSUE-37's F-2 carried for the same reason. It becomes High the moment ISSUE-39's admin form makes any of those three fields writable.

**Recommended fix (2 lines + 3 template edits)**: pass the script-context scalars through `_json_for_script` (it emits a complete, quoted JS string literal) and drop the surrounding quotes in the template:

```python
.replace("{{ROOM_ID}}", _json_for_script(room_id))
.replace("{{PRIMARY_LANG}}", _json_for_script(caption_lang or "ko"))
.replace("{{INITIAL_STATE}}", _json_for_script(initial_state))
```
```js
room_id: {{ROOM_ID}},
caption_lang: {{PRIMARY_LANG}},
initial_state: {{INITIAL_STATE}},
```

`{{ROOM_NAME}}` stays on `html.escape` — it is markup. The identical latent defect exists in `_render_viewer_html` (pre-existing, not introduced here); fixing both together is one more line.

### F-2 detail — placeholder expansion through chained `replace`

Browser-verified, `/stage/{id}` for a room named `{{STAGE_CONFIG_JSON}}`:

```
h1 -> '{"event_title": "", "event_subtitle": "", "caption_ratio": "1/4", "logo_groups": [{"label"…'
```

and for `{{OUTPUT_LANGS_JSON}}` → `h1 -> '["ko", "en"]'`. Room names are operator-controlled free text.

**This is not XSS today** — I confirmed in Chromium that `window.__pwned` is never set, because *every* substituted value is already escaped for both contexts (`html.escape` for scalars, `\uXXXX` for the JSON). But the safety is accidental, not structural: it holds only as long as no future placeholder carries pre-escaped markup. ISSUE-38 (SVG/CSP) and ISSUE-39 both add surface here. Fix: single-pass substitution, e.g. `re.sub(r"\{\{(\w+)\}\}", lambda m: mapping[m.group(1)], template)` — the lambda form also avoids `re.sub`'s backslash-in-replacement trap.

### F-3 detail — the RL-016 chain has a second link and it is untested

`stage_config._as_logo_groups` explicitly declines to sanitise filenames ("경로 검증/봉인은 에셋을 실제로 서빙하는 쪽(ISSUE-38)의 책임이다"). This page is the renderer that puts those filenames into an `src` attribute. **The runtime path is safe** — I attacked it directly (see Targeted verification). But `test_logo_group_assets_are_injected` uses only `host-1.png` / `sponsor-a.svg`, so no test constrains *how* the filename reaches the DOM. A future refactor to a template literal (`wrap.innerHTML += \`<img src="/branding/…/${name}">\``) breaks nothing in CI. That is exactly the shape RL-016 warns about.

Fix — two cheap tests:
- route test with `assets: ['" onerror=alert(1) x="', "<img src=x>"]` asserting the raw `"` / `<` never reach the body;
- static assert that `stage.html` contains `encodeURIComponent(filename)` and contains no `innerHTML` / `outerHTML` / `insertAdjacentHTML`.

### F-5 detail — assertions satisfied by the wrong element

`object-fit: contain` occurs three times in `stage.html` (`.capture-video:123`, `.title-card-logos img:161`, `.logo-group img:192`). `test_letterbox_css_present` names AC 3 (the capture source must letterbox) but passes if `.capture-video` loses the declaration entirely, as long as a logo rule keeps it. Same for `min-height: 0;` (`:58,:97,:200,:208`) in `test_left_column_grid_rows`, which names the Grid-overflow guard on `.presentation`. Fix: scope to the rule block, e.g. `re.search(r"\.capture-video\s*\{[^}]*object-fit:\s*contain", stage_html)`.

## Security Findings

| Severity | Finding |
|----------|---------|
| **Medium** | **F-1** — HTML escaper used for a JS string-literal context. No live exploit path (three independent preconditions all fail today); breaks the page rather than executing script. See F-1 detail. |
| **Medium** | **F-3** — missing regression guard on the logo-filename → `src` carrier. The code is correct; the *guarantee* is untested. |
| Low | **F-2** — placeholder expansion via chained `replace`. Not exploitable today because every substituted value is escaped for both contexts; the invariant is undocumented and unowned. |
| — | **XSS — the RL-016 hand-off: CLOSED.** `_json_for_script` escapes `<`, `>`, `&`, U+2028, U+2029 to `\uXXXX` *after* `json.dumps`, so the output is still valid JSON (`json.loads` round-trips it; the PR tests this). Replacement order is non-reentrant — no replacement introduces a character that a later replacement rewrites. Applied to **both** JSON literals in the stage template (`{{OUTPUT_LANGS_JSON}}`, `{{STAGE_CONFIG_JSON}}`) **and** retro-applied to the viewer page's `{{OUTPUT_LANGS_JSON}}`. 14 render-layer payloads + 8 browser payloads, all neutralised. **No payload survived.** |
| — | **Logo filename in `src`: SAFE, verified by execution.** `makeLogoImage` (`stage.html:280-288`) percent-encodes with `encodeURIComponent` and assigns to the `src` **property**, never string-interpolating into markup. Chromium output for four hostile filenames: `<img alt="주최 로고" decoding="async" src="/branding/logo-attr/%22%20onerror%3D%22window.__pwned%3D1%22%20x%3D%22">`; `javascript:…` → `/branding/…/javascript%3A…` (a relative path, no scheme); `../../../etc/passwd` → `..%2F..%2F..%2Fetc%2Fpasswd`; `<img src=x onerror=…>` → fully percent-encoded. `window.__pwned` never set, zero page errors. Note this is a *positive* interface constraint on ISSUE-38: it will receive `%2F`-encoded traversal segments, so its containment check must run on the decoded filename. |
| — | **RL-006: compliant, verified line by line.** Both `except Exception` blocks in `_handle_stage` (`:512`, `:539`) `print(f"… {e!r}")` to server stdout and return the module-level `_NOT_FOUND_HTML` constant. `e`, `repr(e)`, the template path and any traceback are structurally unable to reach the body — the response text is a constant, not a formatted string. Covered by `test_repo_exception_returns_generic_404` (asserts `"secrets.db"` and `"RuntimeError"` absent) and `test_missing_template_returns_friendly_404` (asserts `"nonexistent"` and `"FileNotFoundError"` absent). |
| — | **RL-015: the "never raises" boundary is complete.** `normalize_stage_config(room.get("stage_config"))` is evaluated **inside** the `try` that wraps `_render_stage_html`, so even if ISSUE-37's contract were broken again the page degrades to the friendly 404 instead of a 500. The `except` is `Exception`, not an enumerated tuple. `_json_for_script` can only raise `TypeError` on non-JSON-serialisable input, and it too sits inside that `try`. |
| — | **Injection: none.** No SQL, no `subprocess`, no `eval`. Template substitution is `str.replace` with literal replacement strings (no `re.sub` backreference exposure). |
| — | **Insecure deserialization: none.** `json` only; no `pickle` / `yaml` / `eval`. |
| — | **Dependencies: none added.** No new CVE surface. |
| — | **Secrets: none** in the diff. |
| — | **Authorization**: `/stage/{room_id}` is unauthenticated **by design** — FR-072 and `ux_spec` "Auth: None", identical to `/view/{room_id}`. It exposes only data intended for projection (room name, event title/subtitle, logo filenames). Room IDs are 64-bit `secrets.token_urlsafe(8)`, not enumerable. Not a finding. |
| — | **Misconfiguration**: no `Cache-Control`, `X-Content-Type-Options` or CSP on the response, and the page relies on an inline `<script>`. Identical to the existing `/view` handler — pre-existing, not a regression, and a CSP would need `'unsafe-inline'` or a nonce before it could be added. Noted, not charged to this PR. |

## AC-by-AC Coverage

| AC | Requirement | Code | Test | Verdict |
|----|-------------|------|------|---------|
| 1 | Unknown room → 404 + `_NOT_FOUND_HTML`, no internal exception text (RL-006) | `_handle_stage:507-518` — three branches mirroring `_handle_view`, both `except` clauses return the shared constant | `test_unknown_room_returns_friendly_404`, `test_repo_exception_returns_generic_404`, `test_missing_template_returns_friendly_404` — the last two assert on the *specific* internal strings (`secrets.db`, `RuntimeError`, `nonexistent`, `FileNotFoundError`), not just on the happy body | **PASS** |
| 2 | `"1/4"` → `25%`, `"1/3"` → `33.333%` | `_CAPTION_WIDTHS:450` + `_render_stage_html:474`; mapping lives **only** server-side | `test_quarter_ratio_renders_25_percent` / `test_third_ratio_renders_33_percent` assert presence **and absence of the other value** — discriminating only because `test_template_does_not_hardcode_ratio_widths` forbids both literals in the template. That pairing is a genuinely strong design. E2E `test_caption_column_width_follows_ratio` measures the real pixel box. I independently measured `caption-column.width / viewport.width` = 0.2500 / 0.3333 across 8 viewports | **PASS** |
| 3 | 16:9 letterbox at 1920×1080 and 3440×1440, no crop/stretch | `.stage-frame:103-115` (`aspect-ratio: 16 / 9` + `container-type: size` on the parent + `width: min(100%, calc(100cqh * 16 / 9))`), `.capture-video:123` `object-fit: contain` | E2E `test_presentation_area_keeps_16_9` parametrised on both viewports. I swept **8 viewports × 3 room configs** with short content — ratio 1.7778 everywhere, no horizontal scrollbar | **FAIL (F-11)** — the *ratio* is preserved at every viewport and content length, but with a 65-char title the frame is 1695px inside a 1440px column at 1920×1080 and overlays the caption column. The e2e assertion cannot detect it (parent grows with child). Ratio: pass. Containment: fail |
| 4 | Empty `event_title` → header shows room name, header height unchanged | Server renders `{{ROOM_NAME}}` into `<h1>`; JS falls back via `cfg.event_title \|\| headerTitle.textContent.trim()` (`:296`); `.event-header { min-height: 88px }` | E2E `test_empty_title_falls_back_to_room_name_and_keeps_header` (asserts `inner_text() == "맨몸 룸"` **and** `header.height >= 60`) | **PARTIAL** — the fallback works and the *height* is stable; but "좌측 컬럼 레이아웃이 변하지 않는다" fails on the width axis for a long room name (F-11), which is exactly the fallback path this AC describes. The unit test named for this AC also cannot fail for it (F-4) |
| 5 | All three logo groups empty → bar collapses to 0, presentation takes the space | `logoBar.hidden = shown === 0` (`:328`) + `.logo-bar[hidden] { display: none }` (`:176`, needed because the UA rule loses to `display: flex`) + grid row `auto`; the footer also ships `hidden` in the server HTML, so the no-JS default is correct too | E2E `test_empty_logo_groups_collapse_the_bar` compares presentation height with/without logos. I measured the delta directly: 877px → 953px at 1920×1080, exactly the 76px bar | **PASS** |
| 6 | `<script>alert(1)</script>` in room name or title → escaped, not executed | `html.escape(..., quote=True)` for markup; `_json_for_script` for the config blob | `test_room_name_script_tag_is_escaped`, `test_event_title_script_tag_is_escaped`, e2e `test_script_breakout_payload_does_not_execute` (asserts `window.__pwned === undefined` **and** zero page errors **and** the literal text is displayed) | **PASS** |
| 7 | `event_title = "</script><script>alert(1)</script>"` → no early script termination, no execution (RL-016 hand-off) | `_json_for_script:339-360` | `TestJsonForScript` ×5 + `test_event_title_cannot_close_script_block` (counts `</script>` in the body against the template's own count — a structural check, not a substring check) | **PASS** — and proven RED, see Targeted verification |
| 8 | `status="closed"` → `initial_state="closed"`, ended state shown, no EventSource | `_handle_stage:524` + `stage.html:335-338` | `test_closed_room_bootstraps_closed_state`, e2e `test_closed_room_shows_ended_copy_without_opening_sse` — wraps `window.EventSource` in an `add_init_script` counter and asserts it stays 0. That is the right way to test a negative | **PASS** |
| 9 | Broken `stage_config` JSON → normalised defaults, no 500 | `normalize_stage_config(room.get("stage_config"))` on the **raw column value**, inside the `try` | `test_malformed_stage_config_renders_defaults` parametrised over `["{", "", None, "[]", '{"caption_ratio": "1/2"}']` + `test_missing_stage_config_column_renders_defaults`. I additionally probed `'{"caption_ratio": null}'` and `'{"caption_ratio": ["1/3"]}'` — both → `25%`. The `_CAPTION_WIDTHS.get(..., default)` mapping is total | **PASS** |

**§ Tests block (5 items)**: all 5 present and passing — 404 body (`test_unknown_room_returns_friendly_404`), ratio → `25%`/`33.333%` (2 tests), `&lt;script&gt;` escaping (`test_room_name_script_tag_is_escaped`), static `aspect-ratio` / `object-fit: contain` / `100dvh` presence (`test_letterbox_css_present`, `test_every_vh_height_has_dvh_fallback`), and `stage_config == "{"` → 200 + `25%` (parametrised).

**AC 3 fails and AC 4 is partial, both due to F-11.** The remaining seven pass. AC 3 and AC 4 rest on the e2e layer rather than the unit layer; that is the correct split (neither is decidable by string matching), but it means `-m 'not e2e'` CI does not gate them — and in AC 3's case the e2e assertion is itself vacuous for the failure that actually occurs.

## Targeted verification performed

- **RED swap on `_json_for_script` — CONFIRMED RED.** Rather than editing the file, I ran the suite with a pytest plugin that rebinds `sse_broadcast._json_for_script` to `lambda obj: json.dumps(obj, ensure_ascii=False)` (`_render_stage_html` resolves the module global at call time, so the swap is faithful). Result: **5 failed, 37 passed** — `test_escapes_script_terminator`, `test_escapes_ampersand`, `test_escapes_js_line_separators`, `test_event_title_script_tag_is_escaped`, `test_event_title_cannot_close_script_block`. The failure output shows the raw payload landing in the body: `_title": "</script><script>alert(1)</script>", "event_subtitle": …`. **The worktree was never modified.**
  - Honest note: the third test the author cites, `test_room_name_cannot_close_script_block`, does **not** fail under the swap — it guards the *markup* path (`html.escape`), not the helper. It is still a valid, non-vacuous test (it would fail if `html.escape` were dropped), but it is not evidence for `_json_for_script`. Two of the three cited tests are the real guards.
- **14 hand-built render-layer payloads**, each checked for `</script>` count, `<script>` count and JSON round-trip: `</script><script>`, `</SCRIPT >` (case + space), `<!--<script>`, `-->`, trailing `\`, `"`, U+2028, `\x00\r\n`, `{{STAGE_CONFIG_JSON}}`, `</script><img onerror>` in the subtitle, and four hostile logo filenames. **All neutralised**; the script block kept exactly 1 opener and 1 closer in every case.
- **8 browser payloads in real Chromium** against the live aiohttp app, watching `window.__pwned`, `pageerror` and `dialog`: title breakout, comment-wrapped breakout, subtitle breakout, four hostile logo filenames, attribute-break room name, placeholder-injection room name. **`window.__pwned` never set; no dialogs; no page errors** (except the deliberate F-1 backslash case).
- **Logo `src` attack, executed:** called `makeLogoImage()` inside the page and read `outerHTML`. Confirmed percent-encoding of `"`, `<`, `>`, `:`, `/` and property-based assignment. Full output quoted in Security Findings.
- **Viewport sweep, 24 cases** (1920×1080, 3440×1440, 1280×1024, 800×1400, 2560×1080, 1024×768, 3840×2160, 640×480 × {1/4 with logos, 1/3, no logos}): frame ratio `1.7778` in all 24, frame always ⊆ presentation box, `scrollWidth == clientWidth` everywhere. AC 3 holds well beyond the two viewports the AC names.
- **RL-011: satisfied, counted.** Exactly 2 × `height: 100vh;` (`:27`, `:48`) and exactly 2 × `height: 100dvh;` (`:28`, `:49`), each immediately following its `vh` sibling. The PR's own `test_every_vh_height_has_dvh_fallback` checks adjacency line-by-line, not mere presence — a good test.
- **RL-012: satisfied.** The only `margin` declarations in the file are four `margin: 0;` (`:25`, `:73`, `:82` and the `html, body` rule). No shorthand with a non-zero vertical component anywhere. The *guard* is weaker than the property (F-6).
- **RL-001: satisfied.** No pure logic is re-implemented in the tests — the caption-width mapping is asserted through the rendered output, never duplicated; the tests import `_json_for_script`, `build_sse_app`, `BroadcastManager` directly and read the real `components/stage.html`. Only *fixture data* is duplicated (the default blob shape and `_StubRoomRepo`, which already exists at `tests/test_sse_broadcast.py:593`) — see Over-Engineering.
- **Scope discipline: clean.** `grep -E "EventSource|getDisplayMedia|innerHTML|keydown|keyup|preventDefault|requestFullscreen|autofocus|\.focus\(" components/stage.html` returns **one** hit: the comment `<!-- ISSUE-42 가 getDisplayMedia 스트림을 여기에 연결한다. -->`. No capture button, no `/branding` route, no settings UI, no SSE subscription. NFR-025 is upheld and guarded by `test_no_presenter_keyboard_interference` / `test_no_interactive_controls`.
- **Merge-conflict surface: minimal.** The router edit is exactly one added line (`sse_broadcast.py:263`) between the existing `/view` and `/health` registrations, with zero reformatting of the surrounding block. ISSUE-38's `/branding/{room_id}/{filename}` will land as another adjacent insertion — a trivial add/add at worst.
- **A minimality candidate I tested and *rejected*.** I hypothesised that `container-type: size` + `100cqh` could be replaced by the shorter, better-supported `aspect-ratio + height: 100% + width: auto + max-width: 100%` idiom. I built the reduced case and measured it in Chromium: it **fails** — at 1920×1080 the frame came out 1628px wide inside a 1440px area (`max-width: 100%` does not clamp once `aspect-ratio` has derived the width from a definite height). The PR's container-query approach is correct and necessary. Reporting the negative result so nobody re-proposes it.
- **Long-content sweep (added after the first pass — this is what found F-11).** 3 content configurations × 2 viewports, measuring `.stage-main`, `#event-header`, `#event-title`, `#presentation`, `#stage-frame` and `#caption-column` boxes plus `scrollWidth` and `el.scrollWidth > el.clientWidth` (ellipsis actually firing). A 37-char title fits exactly (h1 = 1352 of 1352 available) and hides the bug; a 65-char title overflows to 1727.1px. Both `event_title` (JS path) and `name` (pure server-render path, no JS involved) reproduce it.
- **F-11 fix verified, not merely proposed.** Injected `.stage-main{grid-template-columns:minmax(0,1fr)}` at runtime via `page.add_style_tag` on the real page (no file modified). All six long-content cases went `OVERLAP=YES → no`, boxes clamped to 1440 / 2580, and `ellipsized` flipped `False → True`.
- **RETRACTION — the parallel ui-review's RL-017/RL-004 entries are correct; my first-pass disagreement was wrong.** I initially argued that `test_presentation_area_keeps_16_9`'s `frame["width"] <= area["width"] + 1` could not be vacuous, because `container-type: size` gives the presentation box size containment and `.stage-main` carries `min-width: 0`. Both premises are true and the conclusion is still false: size containment stops the *frame* from sizing the *presentation box*, but the presentation box is a grid item in `.stage-main`'s **implicit `auto` column**, which grows from the header's max-content width. So the parent does grow — just for a reason one level up from where I was looking. Measured in the failing case: `pres.w = 1727.1`, `frame.w = 1695.1` ⇒ the assertion evaluates **PASS while the layout is broken**. My earlier counter-example (the rejected `aspect-ratio + max-width` alternative, where the assertion did evaluate False) was a *different* failure mode — child overflowing a fixed parent — and proves nothing about this one. Retracted in full; the RL-017 mechanism as written is accurate.

## Over-Engineering (minimality axis)

379 lines of HTML and 479 of unit tests for a layout-only page justified a hard look. It is close to lean; the removable set is small and one item is verified dead by browser measurement.

- `components/stage.html:90: delete .event-subtitle[hidden] { display: none; } → redundant; .event-subtitle declares no display, so the UA sheet's [hidden]{display:none} already applies (measured in Chromium: computed display === "none" without the rule)`
- `components/stage.html:149: delete .title-card-subtitle[hidden] { display: none; } → same, same measurement`
- `components/stage.html:139: yagni .title-card[hidden] { display: none; } → nothing sets titleCard.hidden in this PR; it is ISSUE-42's capture switch. KEEP if ISSUE-42 lands next sprint — flagging, not recommending`
- `tests/test_stage_page.py:246-248: delete test_lang_attribute_and_viewport_meta → asserts <html lang> and the viewport meta, neither of which appears in any AC or Tests item; both are boilerplate copied unchanged from viewer.html and neither can regress from this PR's logic`
- `tests/test_stage_page.py:41-81 + tests/e2e/test_stage_page_e2e.py:46-64: shrink _StubRoomRepo and the stage-config fixture builder are duplicated across the two new files (and _StubRoomRepo already exists at tests/test_sse_broadcast.py:593) → one shared helper module. ~28 lines. Low value: tests/ and tests/e2e/ have no shared-fixture precedent, so this trades duplication for a new convention`

**Net removable lines: ~5 immediately (2 dead CSS rules + a 3-line boilerplate test), ~28 more only if a shared test-fixture module is introduced.** Net across the whole review the PR should get *longer*, not shorter: F-11 adds one CSS line and one e2e fixture, and F-3 adds two tests. Correctness and validation always outrank this axis.

Explicitly **kept**, having been examined:
- The always-hidden `<video class="capture-video">` and its 8 lines of CSS. AC 3 and the issue's Scope-In require the presentation source to letterbox with `object-fit: contain`; the element is what that rule applies to. Not speculative.
- The `container-type: size` / `100cqh` construction — tested against a shorter alternative, which failed (above).
- `_DEFAULT_CAPTION_WIDTH`'s unreachable `.get()` fallback. `normalize_stage_config` guarantees a valid ratio, so the default never fires — but it is a total-mapping guard at a render boundary and the minimality axis never overrides that. Fix its *source* (F-10), do not delete it.
- Every `[hidden]` rule other than the two named: `.capture-video`, `.title-card`, `.logo-bar`, `.caption-live`, `.caption-ended` all declare `display`, so the UA rule loses and the override is load-bearing. Verified by measurement, not by reading.

## Self-Review

- **Severity re-assessment.** F-11 is High and blocking: it breaks AC 3's containment guarantee at the first viewport the AC names, with content the write-path validator explicitly permits (120-char titles) and content that has no validator at all (room names), on the product's primary display. It is not Critical — no data loss, no security impact, and the deck and captions both still render, just overlapping. F-1 was drafted High and demoted to Medium after I traced all three input sources to ground (`secrets.token_urlsafe` charset; `primary_output_lang` has no writer — verified by grepping every `UPDATE rooms SET` in `database.py`; `status` is enum-validated). No exploit path ⇒ Medium at most, and that matches the precedent this file set for ISSUE-37's F-2. F-3 was drafted Low and promoted to Medium: it is a *missing guarantee* on the exact carrier class RL-016 exists to protect, in the PR whose job was to discharge RL-016. F-2 was drafted Medium and demoted to Low once I proved in a browser that no substituted value is unescaped in either context — the risk is future-tense, not present.
- **False-positive check.** F-1: could `html.escape` be intentional and sufficient? No — `a"b` → `a&quot;b` is a wrong value, not a safe one, and the backslash case was reproduced end-to-end. F-3: is the runtime path perhaps already unsafe? I attacked it and it held; the finding is scoped to the *test* gap, not the code. F-5/F-6: I re-read the file to confirm the assertions really are ambiguous (3 and 4 occurrences respectively) rather than accidentally unique. My one minimality hypothesis was tested and **disproved**, and I removed it. I also actively looked for evidence against the parallel reviewer's RL-004 claim and found a decisive counter-example rather than deferring to it.
- **Blind spot I actually hit (worth recording).** My first pass swept **8 viewports × 1 content length** and concluded AC 3 passed. Viewport variation was the axis I thought to parametrise; content variation was not. The bug is invisible along the axis I varied and unmissable along the one I did not — and the PR's own e2e made exactly the same choice, which is why it is green. I only re-tested because a parallel review asserted the opposite of my finding; had it not, I would have shipped an incorrect "AC 3: PASS". The generalisable rule is RL-017's: intrinsic sizing is a *content*-driven failure mode, so a viewport sweep is not evidence about it.
- **Blind-spot scan.** My first pass found nothing in: authorization, dependencies, caching/headers, i18n, and error-message quality. Re-reading for each: authz is by-design unauthenticated per FR-072 and leaks only projection data (cleared); no deps added (cleared); no cache/CSP headers but identical to the incumbent `/view` handler, so not a regression (recorded, not charged); **i18n produced a real observation** — `<html lang="ko">` is hardcoded while `?lang=en` will fill the caption column with English in ISSUE-41, which mis-cues screen readers; it matches `viewer.html` exactly, so it is a project-wide follow-up rather than a PR defect. Error messages: the only user-facing copy is the shared Korean not-found page and "세션이 종료되었습니다" — both actionable and audience-appropriate.
- **AC verification**: all 9 ACs and all 5 § Tests items re-read against the code, not against test names. Two ACs (3, 4) are carried by the e2e layer only; called out explicitly above.
- **Confidence: High on security, Medium on layout.** Every claim here was produced by execution, not inspection: a 795-test suite run, a 9-test e2e run, a plugin-driven RED swap (5 failures), a 14-payload render probe, an 8-payload Chromium probe, a direct `makeLogoImage()` DOM probe, a 24-case viewport sweep, a 6-case long-content sweep, a runtime fix-injection proving F-11's remedy, a computed-style probe isolating the redundant `[hidden]` rules, and a control experiment that killed my own minimality proposal. Security is High confidence — the escaping was attacked from two layers and proven RED. **Layout is only Medium**: I demonstrably missed F-11 on the first pass, and the same reasoning gap (varying one axis and generalising) could hide another intrinsic-sizing defect I have not thought to probe — long logo group labels, a 12-asset logo bar, or a caption line with a single unbreakable token in the 25% column are the untested neighbours. Two other areas I could not verify by execution: how ISSUE-38's future `/branding` route will treat `%2F`-encoded segments (stated as an interface expectation, not a finding), and Safari's behaviour for `container-type: size` / `100cqh`, which I could only test in Chromium.

## Follow-up issues proposed

| Proposed severity | Description |
|---|---|
| **High — fix in this PR, not a follow-up** | **Add `grid-template-columns: minmax(0, 1fr)` to `.stage-main` (F-11)**, plus a long-content e2e fixture (65+ char Korean title *and* a long room name) whose containment assertion compares against `#caption-column`'s left edge rather than the parent's width. Also worth asserting `event-title.scrollWidth > clientWidth` so the ellipsis path is proven to engage. |
| **Medium** | **Use `_json_for_script` for every value that lands in a `<script>`, in both renderers (F-1).** Three call sites in `_render_stage_html` + the four in `_render_viewer_html`, plus dropping the now-redundant quotes in both templates. Add a regression test: a room id / status containing `"` and `\` must produce a page where `typeof CONFIG !== "undefined"`. |
| **Medium** | **Close the second RL-016 link (F-3).** Route test with hostile logo filenames + static assertion that `stage.html` uses `encodeURIComponent` and contains no `innerHTML`/`insertAdjacentHTML`. Should land before ISSUE-38 makes `/branding` real. |
| Low | **Single-pass template substitution (F-2)** in `_render_stage_html` and `_render_viewer_html`, so an operator-chosen room name can never expand a placeholder. |
| Low | **Tighten three test assertions (F-4, F-5, F-6)**: scope `object-fit: contain` / `min-height: 0` to their rule blocks; make the AC-4 unit test either assert the JSON-side precondition only (and rename) or defer the claim to the e2e mapping; extend the RL-012 guard to `margin:` shorthands with non-zero vertical components. |
| Low | **Inject `--caption-width` through a server-rendered `<style>` instead of `setProperty` (F-7)**, so a layout-critical value does not depend on the inline script surviving. The "template must not contain both widths" invariant — which `test_template_does_not_hardcode_ratio_widths` correctly enforces — is preserved, since the server still emits exactly one value. |
| Low | **After `img.remove()`, drop an emptied `.logo-group` and re-collapse the bar when none remain (F-8).** Currently spec-compliant ("해당 이미지만"), but between now and ISSUE-38 every stage page burns 76px on bare group labels. |
| Low | **Doc/DRY sweep (F-9, F-10)**: soften `_json_for_script`'s "every JSON literal" claim to "every JSON literal injected into a template"; update `docs/architecture.md:113` from "`json.dumps`ed" to `_json_for_script`; derive `_DEFAULT_CAPTION_WIDTH` from `stage_config.DEFAULT_CAPTION_RATIO`. |
| Low | **Project-wide**: make `<html lang>` (or the caption container's `lang`) follow the selected caption language in both `viewer.html` and `stage.html`. Currently hardcoded `ko` in both. |

## Lessons applied

| Lesson | Application |
|--------|-------------|
| RL-001 | Verified: no pure logic re-implemented in either test file; the width mapping is asserted through rendered output. Only fixture data is duplicated (recorded in the minimality axis) |
| RL-004 | Every one of the 42 unit tests read against "would this pass against a do-nothing implementation?". Four weak cases found (F-4, F-5, F-6 and the non-RED `test_room_name_cannot_close_script_block` note). Two tests are notably **strong** and worth copying: `test_template_does_not_hardcode_ratio_widths` (makes the route tests discriminating) and `test_event_title_cannot_close_script_block`'s `</script>`-count comparison against the template |
| RL-006 | Both `except` branches traced to a constant response body; the internal detail is `print`-only. Confirmed by test and by reading every return path |
| RL-008 | `<img onerror>` per-image degrade present and e2e-verified against a live 404 |
| RL-011 | Counted: 2 × `100vh`, 2 × `100dvh`, adjacency enforced by the PR's own line-pair test |
| RL-012 | Only `margin: 0` in the file; guard is weaker than the property (F-6) |
| RL-015 | `normalize_stage_config` is called inside the `try`, and the `except` is `Exception`, not an enumerated tuple — the "never raises" promise is not re-derived from the happy path |
| RL-016 | **The hand-off is discharged.** Attacked with 22 payloads across two layers and proven RED against `json.dumps`. The lesson recurs one link downstream (F-3): the logo filename is the next unescaped carrier, correct at runtime but unguarded by tests |
| RL-017 | Applied *after* the parallel ui-review raised it and I initially disputed it. Re-testing with long content reproduced the overflow immediately (F-11). Frequency incremented; the entry's mechanism is confirmed accurate, including the specific claim that `frame.width <= presentation.width` is vacuous here because the implicit `auto` track grows the parent |


---

<!-- 복원됨 2026-08-23: ISSUE-38 머지(c44afd2)가 이 파일을 통째로 덮어써 아래 ISSUE-33 섹션이 main 에서 유실됨. a50e51a 에서 복원. -->

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

---

# Review Notes — ISSUE-41 (PR #127)

**Reviewer**: Claude Opus 5 (independent review pass, did not author the code)
**Date**: 2026-08-23
**Branch / commit**: `issue/ISSUE-41-stage-caption-column` @ `a4622da` → review fixes pushed (diff vs `6b78000`)
**Files reviewed**: `components/stage.html` (+287/−5, 691 L), `tests/test_stage_page.py` (+202), `tests/e2e/test_stage_page_e2e.py` (+418)
**Method**: full diff read + `reviewer` and `ui-reviewer` sub-agents in parallel (separate contexts) + independent WCAG contrast recomputation + **21 source mutations run against the suite** in a throwaway worktree (`scratchpad/wt41-guard`, the PR worktree untouched during the audit) + the RL-019 guard re-run against ISSUE-40's shipped markup.
**Verdict**: **approve after fixes** — 0 Critical, 0 High, 4 Medium (all FIXED in this PR), 5 Low (3 FIXED, 2 accepted), 3 deferred to follow-up issues.

## Scope

In scope: the stage page's right-hand caption column — SSE subscription, rAF typewriter, live-region ownership hand-off from ISSUE-40 (RL-019), narrow-column typography, `conn-error` banner. Out of scope and correctly absent: screen capture (ISSUE-42), viewer-page parity fixes (ISSUE-44/45).

Leakage check: `getDisplayMedia` / wake lock / `?debug=1` / capture button / focus-handoff — **zero code hits**; the only occurrence is the pre-existing ISSUE-42 mount-point comment (`stage.html:652`, unchanged from `6b78000`). The `git diff 6b78000 HEAD -- components/stage.html` deletion set is exactly 5 lines, all inside the caption column / bootstrap comment. The left presentation area is byte-identical.

## Independent verification (not the implementer's claims)

| Claim | How verified | Result |
|---|---|---|
| rAF, no `setInterval` typewriter | `grep` over the whole file | 0 `setInterval`, 0 `setTimeout` |
| No double-scheduled rAF loop | instrumented `requestAnimationFrame`/`cancelAnimationFrame` in Chromium; 12 synchronous partials then 12 partial+final pairs | max concurrent callbacks **1**, live after settle **0**. Removing the `twRaf` guard yields **13** — previously unguarded, now guarded (see F-15) |
| `cancelAnimationFrame` always fires | read every `_twStop()` call site (`_lockLine`, `visibilitychange`→hidden, `session_end`); `_twStep` clears `twRaf` at entry so `_twStart`'s guard is never bypassed | no leak, no idle spin (`gap == 0` returns without rescheduling) |
| RL-019 guard fails on ISSUE-40's markup | checked out `6b78000:components/stage.html` into a throwaway worktree and ran `TestStageCaptionColumn` | `test_animated_node_is_not_the_live_region` **FAILS** with `#caption-container still carries aria-live … <div class="caption-container" id="caption-container" aria-live="polite">` (9 of 11 tests fail there) |
| Exactly one element announces caption text | `announce()` has exactly one caller (`_lockLine`); `#caption-announcer` is a **sibling** of `#caption-container`, so `aria-hidden` does not propagate to it; `#caption-ended` / `#conn-error` never receive caption text | holds |
| NFR-025 guards byte-identical + green | `git diff 6b78000 HEAD -- tests/test_stage_page.py` → **0 deletions**; SHA-256 of both function bodies identical (`2d9150c0fba8`, `ce7a66aa7135`) | identical, both pass |
| Only one new document-level listener | `grep 'document\.addEventListener'` | `visibilitychange` (new) + `DOMContentLoaded` (pre-existing). No `keydown`/`keyup`/`keypress`/`preventDefault`/`requestFullscreen`/`.focus()` anywhere |
| No new template placeholder | added lines contain zero `{{…}}`; `sse_broadcast.py` untouched | RL-016/020/021 surface unchanged |

### WCAG 1.4.3 — every ratio recomputed from the CSS

Canvas `#0b0b0c` = `rgb(11,11,12)`. `.conn-error` text is composited over the pill, not the canvas (RL-018).

| Selector | Colour | Backdrop | Ratio | Verdict |
|---|---|---|---|---|
| `.caption-line` (past) | `rgba(255,255,255,0.45)` | `#0b0b0c` | **4.51:1** | PASS (0.42 → 4.06 would fail; ISSUE-40's fix survives) |
| `.caption-line:last-child` | `#ffffff` | `#0b0b0c` | 19.67:1 | PASS |
| `.caption-empty` | `rgba(255,255,255,0.5)` | `#0b0b0c` | 5.33:1 | PASS (ISSUE-40's 2.81:1 fix survives) |
| `.caption-ended` | `rgba(255,255,255,0.6)` | `#0b0b0c` | 7.29:1 | PASS |
| `.logo-group-label` | `rgba(255,255,255,0.5)` | `#0b0b0c` | 5.33:1 | PASS (ISSUE-40's 3.80:1 fix survives) |
| `.event-subtitle` | `rgba(255,255,255,0.5)` | `#0b0b0c` | 5.33:1 | PASS |
| **`.conn-error` (NEW)** | `rgba(255,255,255,0.65)` | pill `rgba(255,255,255,0.07)` on `#0b0b0c` = `rgb(28,28,29)` | **7.81:1** | PASS — implementer's figure confirmed exactly |
| `.conn-error` border | `rgba(255,255,255,0.1)` | `#0b0b0c` | 1.26:1 | n/a (decorative pill border, meaning carried by the text) |

`clamp()` resolution, applying the ISSUE-40 reasoning: `.caption-line`'s `clamp(20px, 1.4vw + 8px, 32px)` crosses 24px at **1142.9px** viewport width — 25.9px @1280, 30.4px @1600, 32px @1920/2560/3440. `font-weight: 500` is **not** bold for WCAG, so the threshold is 24px, not 18.66px. The large-text exemption is therefore not needed at any realistic stage width **and is not relied on**: 0.45 clears the normal-text 4.5:1 bar outright. `.conn-error` (12–16px) and `.caption-empty` (14–19px) are normal text at every width and both clear 4.5:1. **No contrast regression; no exemption claimed where it does not hold.**

### RL-017 layout containment

`.stage-main { grid-template-columns: minmax(0, 1fr) }` is untouched by this diff (verified line-by-line). New containers: `.caption-column` (`min-width: 0; min-height: 0; overflow: hidden`), `.caption-live` (`min-height: 0`; its child `.caption-scroll` has `overflow-x: hidden`, so the flex automatic minimum size resolves to 0 rather than min-content), `.conn-error` and `.sr-only` (both `position: absolute`, out of flow, containing block `.caption-column`). Hostile content is exercised by the existing 120-char-title fixture and the 300-char unbroken token (`scrollWidth <= clientWidth` measured, not grepped). No containment gap.

### RL-011 / RL-012

No new `100vh` (the existing pairings are ISSUE-40's and untouched). No vertical `margin` anywhere in the new CSS; `.caption-container` uses `min-height: 100%` + `padding` under the global `box-sizing: border-box`. `.sr-only` uses `clip-path: inset(50%)` + `margin: 0` rather than the legacy `margin: -1px` + `clip: rect(...)` — correct: it is `position: absolute`, so the 1×1 box never participates in layout, and it stays in the accessibility tree (unlike `display:none`/`visibility:hidden`). It is not inside the scroll container (`.caption-scroll`), so it creates no scroll anchor.

## Mutation audit (RL-004, frequency 5 — escalated as ISSUE-43)

21 mutations applied to `components/stage.html` in an isolated worktree; every mutation reverted afterwards. Killed:

| Mutation | Killed by |
|---|---|
| `MAX_LINES` 60 → 200 | unit `test_caption_dom_cap_is_sixty` + e2e `test_sixty_one_finals_trim_to_sixty_lines` |
| restore ISSUE-40's `aria-live` on `#caption-container` | unit `test_animated_node_is_not_the_live_region` + e2e `test_only_finalised_lines_reach_the_live_region` |
| rAF → `setInterval(…, 28)` | unit `test_typewriter_runs_on_request_animation_frame` |
| remove `Math.min(MAX_REVEAL_PER_FRAME, …)` | unit `test_per_frame_reveal_is_capped` |
| empty the `visibilitychange` snap body | e2e `test_hidden_tab_snaps_the_current_line_instead_of_queueing` |
| drop `overflow-wrap: anywhere` | unit ×2 + e2e `test_long_token_wraps_without_horizontal_scroll` |
| drop `word-break: keep-all` | unit `test_caption_line_wrapping_rules_live_in_the_caption_line_rule` |
| `.conn-error` alpha 0.65 → 0.45 (4.44:1) | unit `test_conn_error_banner_meets_wcag_aa_contrast` |
| drop the pending-line lock in `finalizeCaption` | e2e `test_sixty_one_finals_trim_to_sixty_lines` |
| `announce()` per reveal frame | e2e `test_only_finalised_lines_reach_the_live_region` |
| `session_end` loop truly kept running (`_twStop()` **and** `currentLine = null` removed) | e2e `test_session_end_switches_the_column_and_leaves_the_stage_intact` |
| drop `aria-atomic` | unit `test_animated_node_is_not_the_live_region` |
| `_lockLine` writes the slice instead of `twTarget` | e2e `test_sixty_one_finals_trim_to_sixty_lines` |
| move `#conn-error` into `.stage-main` | unit `test_conn_error_banner_lives_inside_the_caption_column` + e2e |
| `.conn-error` `position: absolute` → `fixed` | e2e `test_connection_banner_sits_at_the_bottom_of_the_caption_column` |

**Survivors → findings** (all now fixed and re-mutated to confirm the new guards kill them):

- Instant reveal (`twShown = twTarget.length`) — nothing failed. The headline AC ("타자기 스무딩") had **no** behavioural guard → **F-14**.
- Removing `_twStart`'s `twRaf ||` double-schedule guard — nothing failed (13 concurrent loops) → **F-15**.
- Emptying the `visibilitychange` hidden branch — the **unit** guard survived (pure existence grep) → **F-5**.
- Dropping only `_twStop()` from `session_end` — survives, but behaviour is unchanged because `currentLine = null` also stops the loop, and the real regression (both removed) *is* caught. **Not a defect.**
- Always writing `scrollTop` — unobservable perf-only change. **Not a defect** (see F-8).
- `firstElementChild` → `firstChild` — behaviour identical today; hardening only (F-7).

## Code Review

### F-2 — Medium — an empty final permanently blanks the caption column — **FIXED**
`components/stage.html` `finalizeCaption()`. `_ensureCurrentLine()` removes `#caption-empty` and opens a line even when there is nothing to show, so a `{"text": ""}` final destroys the "잠시 후 시작됩니다" placeholder and leaves a blank full-height column that never recovers; each empty node also burns one of the 60 slots. Reproduced in Chromium: three empty finals → 3 empty `.caption-line` nodes, placeholder gone. Reachable — `broadcast_translation_for_room` has no non-empty guard and AWS Translate returns `""` for punctuation-only input. **Fix**: bail out before `_ensureCurrentLine()` when there is nothing to show, while preserving the deliberate "empty final locks the in-flight partial" fallback. Guards added: unit `test_empty_final_never_opens_a_blank_line`, e2e `test_empty_final_does_not_blank_the_column`.

### F-14 — Medium — the headline AC (gradual reveal) had no behavioural guard — **FIXED (test only)**
Every caption test inspected only the **end** state, so mutating `_twStep` to reveal the whole string in one frame passed the entire suite; the static tests only prove the string `requestAnimationFrame(` appears somewhere. RL-004 exactly. **Fix**: e2e `test_partial_text_is_revealed_gradually_not_in_one_frame` samples mid-flight (`0 < len < 600` at ~60 ms) and then asserts the reveal completes.

### F-15 — Medium — no guard on rAF double-scheduling — **FIXED (test only)**
`_twStart`'s `if (twRaf || …) return` is the only thing preventing one extra rAF loop per partial. Removing it produced **13** concurrent loops with the whole suite still green — and ISSUE-42 is held behind this PR specifically because it asserts against the rAF loop. **Fix**: e2e `test_only_one_raf_loop_ever_runs` instruments `requestAnimationFrame`/`cancelAnimationFrame` and asserts max concurrent ≤ 1 and 0 live after settling. The implementation was already correct — this closes the guard gap, not a bug.

### F-5 — Medium — `test_hidden_tab_stops_the_typewriter_loop` was an existence grep — **FIXED (test only)**
It asserted only that `addEventListener("visibilitychange"` and `document.hidden` appear in the file; an empty `if (document.hidden) { }` body passed. **Fix**: the test now extracts the branch bodies and asserts `_twStop()`, the snap write, and the re-arm on return. Verified to fail against an emptied branch.

### F-1 — Low→fixed — `session_end` left the state machine armed — **FIXED**
`session_end` cleared `currentLine` and the rAF handle but set no terminal flag, so one late `message` re-entered `setCaptionState("active")`, appended a line, restarted the loop and wrote the live region on a closed socket (reproduced in Chromium: ended column resurrected with a ghost caption). Not reachable through today's server, which is why it is Low. **Fix**: a `sessionEnded` flag set in the `session_end` handler and checked at the top of the `message` handler — reachability is a property of the entry points, not of the fields. Guards: unit `test_terminal_state_blocks_late_messages`, e2e `test_message_after_session_end_is_ignored`.

### F-3 — Low — a shrinking partial left stale longer text on screen — **FIXED**
`_twStep` clamped `twShown` but `gap` then became 0, so the write was skipped and the previously-revealed longer string stayed. Benign today (the publisher sends cumulative text) and inherited verbatim from `viewer.html`. **Fix**: repaint inside the clamp branch. Guard: e2e `test_a_shrinking_partial_repaints_instead_of_leaving_stale_text`.

### F-7 — Low — element-count guard driving a node-based removal — **FIXED (hardening)**
`while (container.children.length > MAX_LINES) container.removeChild(container.firstChild)` — `children` counts elements but `firstChild` is the template's leading whitespace **text node**. Correct today only because the extra iterations self-correct; one stray comment node would break it. **Fix**: `firstElementChild`. Behaviour is unchanged, so no new test — the existing `lines[0] == "자막 2"` assertion already pins the outcome.

### F-8 — Low — one forced synchronous layout per reveal frame — **ACCEPTED, comment corrected**
The inline comment attributed the forced layout to the `scrollTop` **write**; it is the `scrollHeight` **read** (after the `textContent` write dirties layout) that flushes synchronously. The reviewer's proposed fix — scroll only on line open/lock — would break bottom-anchoring while a long line wraps mid-reveal, so it is rejected. The cost is one layout per animated frame, which rAF would perform before paint regardless. **Comment rewritten to state the real mechanism** rather than leaving an inaccurate rationale in the file.

### F-11 / F-12 — Low — two assertions weaker than their names — **FIXED**
`test_connection_banner_sits_at_the_bottom_of_the_caption_column` asserted containment only, so a banner pinned to the **top** of the column (covering the first caption line) passed → now also asserts `banner.y > column midpoint` (verified to fail against `top: 16px`). `test_stream_url_falls_back_to_primary_lang` used `endswith("?lang=ko")` where its sibling uses exact equality → now exact.

### F-9 — Low — deliberate divergence from `viewer.html` — **NOT A DEFECT**
Scope says "동작 동등성 유지" but `finalizeCaption` intentionally locks a pending line before opening a new one. The change is correct, documented in the code and the PR body, and **fixes a real caption-dropping bug** — reproduced on `/view/{room_id}`: two back-to-back finals render only the second. The problem is that the fix now lives in only one of the two copies → follow-up FU-1.

### F-13 — Low — mixed-language chrome — **NOT A DEFECT (pre-existing parity)**
`?lang=en` yields "Starting shortly" followed by the hard-coded Korean banner "연결이 끊어졌습니다. 재연결 중…". Identical in `viewer.html`; not a regression. Recorded so it is not mistaken for one later.

### F-16 — Low — test-file path deviates from the spec — **ACCEPTED**
`issues.md` / `test_plan.md` TC-062 name `tests/e2e/test_stage_e2e.py`; the tests went into ISSUE-40's existing `tests/e2e/test_stage_page_e2e.py`. Creating a near-duplicate module would spin up a second aiohttp server for no benefit. Co-location is the better call; the docs should be reconciled at doc-sync time.

## Security Findings

**None.** No new attack surface.

- **XSS sinks**: `innerHTML` / `outerHTML` / `insertAdjacentHTML` / `document.write` / `eval` / `new Function` / `srcdoc` / `setAttribute` → **zero occurrences** in the whole of `components/stage.html`. Every text write in the new code is `textContent`. No markup assembly.
- **URL context**: `"/stream/" + encodeURIComponent(CONFIG.room_id) + "?lang=" + encodeURIComponent(CONFIG.caption_lang)` — a room id of `//evil.com` encodes to `%2F%2Fevil.com`, so the relative URL cannot be forced cross-origin. `caption_lang` is validated server-side against the supported set (`_handle_stage`) **and** encoded here.
- **RL-016 / RL-020 / RL-021**: the PR adds **no** new `{{PLACEHOLDER}}` and does not touch `sse_broadcast._render_stage_html`. The single-pass `re.sub` substitution and `_json_for_script`-by-sink escaping are unchanged and still cover 100% of the placeholders.
- **Deserialisation**: `JSON.parse` inside `try/catch`, result gated by `typeof payload.text === "string"`, no spread/merge → no prototype-pollution path.
- **RL-006**: `showError()` renders a fixed Korean string; no exception text ever reaches the DOM. The two `catch` blocks swallow deliberately.
- **Dependencies**: none added. **Auth**: unchanged (`/stage` and `/stream` were already unauthenticated by design, ISSUE-30/40).
- Informational, pre-existing, not a finding against this PR: the stage response carries no `Content-Security-Policy`, and the bootstrap is an inline `<script>`, so a future CSP needs a nonce.

## Over-Engineering

Close to lean. Nothing was cut, and each decision is recorded rather than silently accepted:

```
tests/e2e/test_stage_page_e2e.py: keep    FakeEventSource.removeEventListener / readyState — KEPT deliberately.
                                          A permissive stub is what makes the F-1 "message after session_end"
                                          guard possible; a stub that honours close() would hide the defect.
tests/test_stage_page.py:         keep    TestStageCaptionColumn.stage_html duplicates the sibling class fixture
                                          (3 lines). Not worth coupling two test classes.
components/stage.html:            keep    try/catch around `new EventSource` and around `es.close()` — defensive,
                                          matches viewer.html, RL-006 discipline. ~6 lines.
components/stage.html:            keep    applyWaitingText() — one caller, but it is now paired with waitingText()
                                          which has two, and it names the concept.
```

**Net removable: ~12 lines of test scaffolding, all rejected for the reasons above. Lean enough. Ship.**

## Test results

| | Before review | After review fixes |
|---|---|---|
| `uv run pytest -q` | 1001 passed / 57 deselected | **1003 passed / 63 deselected** |
| Coverage | 93.82% | **93.82%** (unchanged) |
| `pytest -m e2e tests/e2e/test_stage_page_e2e.py` | 35 passed | **41 passed** |
| `ruff check .` / `black --check .` / `ruff format --check .` | clean | **clean** |

Net test change from this review: +3 unit guards, +6 e2e guards (2 of which close mutation gaps in behaviour that was already correct).

## Follow-ups (not fixed here — out of ISSUE-41 scope)

- **FU-1 (P1, bug)** `viewer.html` drops a caption when two finals arrive back-to-back. Reproduced on `/view/{room_id}`: only the second renders. Port `finalizeCaption`'s pending-line lock and add the regression test. Fold into ISSUE-44/45 or open its own issue.
- **FU-2 (P2, bug)** `viewer.html` carries F-2 (empty final blanks the area), F-3 (stale shrinking partial) and F-7 (`firstChild` trimming) verbatim; it also still resets via `innerHTML = ""`, a sink `stage.html` now bans file-wide by test. Same fixes apply.
- **FU-3 (P2, a11y)** `viewer.html`'s caption stack has **no** live region at all — screen-reader attendees on the public viewer page get zero caption text. Fold the `#caption-announcer` + `aria-hidden` pattern into ISSUE-45.
- **FU-4 (P3, tech-debt, RL-001)** Extract the shared SSE + typewriter core once a third consumer appears. Shape: `components/_caption_stream.js` inlined via a placeholder in both renderers, five knobs passed through the existing `CONFIG` object — no new route, no bundler. **Do not** do it in this PR.

## Confidence

**High.** Both dimensions were reviewed by separate-context sub-agents that independently converged on the waiting-state a11y gap; every numeric claim (contrast, clamp thresholds, rAF concurrency, byte-identity of the NFR-025 guards) was recomputed or measured rather than read from the implementer's comments; and every guard that matters was mutation-tested, with each new guard re-mutated to prove it kills its own defect. Residual uncertainty: Chromium-only measurement (Safari's `clip-path` sr-only and `container-type: size` behaviour was reasoned about, not measured), and the "버벅이지 않아야 한다" smoothness half of NFR-025 still needs the venue rehearsal that `test_plan.md` already calls out as a manual gate.

---

# Review Notes — ISSUE-39 (PR #125)

Reviewed commit range: `origin/main..42e9d6d` (2 commits, 9 files, +1736/-21).
Worktree: `/Users/pillip/project/practice/realtime-en2ko-captions/.worktrees/issue-ISSUE-39-admin-stage-config-form`

> **Concurrency notice for the team-lead.** When I started, `git status` was clean. Partway
> through the review the working tree gained an **uncommitted change to `admin.py` that is not
> mine** — the delete-button label `"삭제"` → `f"삭제 · {filename}"` plus a 6-line comment about
> `help=` landing on the wrapper div via `aria-describedby`, and `st.columns([4, 1])` →
> `[3, 2]`. That looks like a concurrent a11y/UI reviewer. I preserved it (verified byte-exact
> after my mutation experiments) and layered my fix on top. **My fix touches the same function
> (`_render_stage_logo_manager`), so whoever writes `admin.py` next must not overwrite from a
> stale buffer.** Everything below is judged against the working tree as it now stands.

## Summary / Verdict

**Approve with nits**, after one High finding I fixed in review.

This is a genuinely well-built PR. The reuse contract is honoured: `admin_logic`/`admin.py`
never restate a size/extension/count rule — `partition_uploads` forwards `save_asset`'s Korean
`ValueError` text verbatim, `_LOGO_UPLOAD_TYPES` is derived from `ALLOWED_EXTENSIONS`, the ratio
radio is driven by `CAPTION_RATIOS`, and the 12-asset cap is enforced only by
`validate_stage_config`/`save_asset`. RL-006 discipline is clean — I audited every
`st.error`/`st.warning`/`st.caption` in the new code and found **no** path where a
non-`ValueError` message, an internal path, an errno, or a traceback reaches the UI. RL-002 is
enforced server-side and the AST test that proves it is mutation-sensitive. Scope discipline is
clean: `sse_broadcast.py` and `components/stage.html` are untouched.

I verified AC-1/2/4/5/6/7 in a real browser (Chromium via Playwright) beyond what the suite
covers, and found one reproducible crash (R-01) that the automated tests could not see.

- Blocking (fixed in review): **R-01**.
- Non-blocking, deferred to follow-up triage: R-02 … R-09.

Post-fix gates: **1056 passed, 53 deselected, 94.10% coverage** (baseline 1054 / 94.08%).
`ruff check .` clean, `black --check .` clean. e2e suite (`-m e2e`, 5 tests) green — 6 clean runs.

Confidence: **High** for `admin_logic.py` / `branding_assets.py` / `qr_generator.py` / the test
suite (read line by line, mutation-tested, plus runtime experiments). **Medium** for the
`admin.py` Streamlit control flow — it is excluded from coverage by project convention
(`pyproject.toml [tool.coverage.run] omit`), so my confidence there rests on the browser runs I
did rather than on CI.

---

## Code Review

### R-01 — Duplicate `st.button` key destroys the rest of the room-management tab
- **Severity: High** · **Fixed in review**
- `admin.py:833-849` (`_render_stage_logo_manager`), `admin_logic.py:495-503`
  (`build_stage_config_from_form`)
- **What.** The delete button key was `f"stage_cfg_del_{room_id}_{label}_{filename}"`. If one
  logo group's `assets` list contains the same filename twice, Streamlit raises
  `StreamlitDuplicateElementKey` and aborts the script.
- **Why it matters.** This is reachable through the flow the PR's *own* drift warning invites.
  Reproduced end to end in Chromium:
  1. upload `logo.png` → config `['logo.png']`, disk `['logo.png']`;
  2. the file disappears out of band (volume remount / backup restore / manual cleanup);
  3. `_warn_stage_asset_drift` correctly shows *"설정에는 있으나 파일이 없는 로고: logo.png"*, so
     the admin re-uploads the same file. `save_asset` sees no collision (the file is gone) and
     returns `logo.png`, so `existing + accepted` becomes `['logo.png', 'logo.png']`;
  4. next render:
     `streamlit.errors.StreamlitDuplicateElementKey: There are multiple elements with the same key='stage_cfg_del_roomddd_주최_logo.png'`
     — a **full Python traceback with absolute server paths is rendered into the admin UI**, and
     **오퍼레이터 배정 / 룸 강제 종료 / 룸별 대화 기록 all stop rendering** (measured:
     `"오퍼레이터 배정" in body → False`). The state is sticky: the only UI that could remove the
     duplicate is the delete button that crashes.
  This is a functional break of pre-existing features introduced by this PR, plus a (admin-only)
  traceback leak, so High rather than Medium.
- **Fix applied** (minimal, two parts, both mutation-verified):
  - root cause — `build_stage_config_from_form` now collapses duplicate filenames within a group
    via a new order-preserving `_dedupe_preserving_order` (list membership, not `set`, because
    callers may pass unhashable junk and the per-room cap is 12);
  - defence in depth — the widget key now carries the loop index
    (`for index, filename in enumerate(assets)` → `..._{label}_{index}_{filename}`), so a config
    that never went through the save path still renders.
  - regression tests: `tests/test_admin_stage_config.py::TestBuildStageConfigFromForm::`
    `test_duplicate_filenames_in_a_group_are_collapsed` and
    `test_duplicates_do_not_consume_the_room_asset_budget`. Both verified RED when the dedupe is
    reverted.
  - Re-ran the browser repro after the fix: `step3 cfg: ['logo.png']`, `stException count: 0`,
    all later sections present.

### R-02 — `describe_stage_config_drops` can never fire in the admin save path (F-5 is decorative)
- **Severity: Medium** · **Deferred**
- `admin_logic.py:~330-400`, called at `admin.py:1013-1017`
- **What.** In `_save_stage_config` the candidate is built as
  `build_stage_config_from_form(..., logo_groups={label: existing.get(label,[]) + accepted for label in LOGO_GROUP_LABELS})`.
  `existing` comes from `Room.get_stage_config` → already `normalize_stage_config`'d, so it only
  ever has the three known labels with `str` assets; `accepted` comes from `save_asset`, always
  `str`. The dict comprehension iterates `LOGO_GROUP_LABELS` only. Therefore the candidate can
  never carry an unknown label, a duplicate label, or a non-string asset — the three things
  `describe_stage_config_drops` detects. I confirmed experimentally: for the exact candidate
  shape the admin path builds, it returns `[]`.
- **Why it matters.** F-5 asked for a warning when normalisation silently drops something. The
  named mechanism is ~60 lines of source plus ~55 lines of tests that only ever run against
  hand-built candidates in the test file. The *risk* F-5 targeted is genuinely closed — but
  structurally, by the shape of the candidate, not by this function. Carrying a permanently-dead
  warning path is worse than saying so, because a future reader will assume drops are covered.
- **Suggested action.** Either (a) delete `describe_stage_config_drops`, its call site and its
  test class and record in the notes that F-5 is discharged structurally (the useful live guard,
  `find_asset_drift`, stays); or (b) keep it and add one production path that can actually feed
  it an unknown label (e.g. round-trip the raw DB blob rather than the normalised config). I did
  not apply either — removing a claimed deliverable is a team decision, not a review edit.

### R-03 — Orphan-file window on the asset-count path is not closed by the pre-flight
- **Severity: Low** · **Deferred**
- `admin.py:958-997` (`_save_stage_config`)
- **What.** The author's pre-flight (validate text before uploading) is sound and does close the
  text-validation orphan window. It does **not** close the count window, because `save_asset`'s
  12-asset cap counts files **on disk** while `validate_stage_config` counts references **in
  config**. When the two have drifted, uploads succeed and the merged candidate is then rejected,
  leaving the newly written files unreferenced. Reproduced:
  config references 12, disk holds 5 → pre-flight `(True, '')` → 3 uploads accepted → final
  `validate_stage_config` returns `(False, '로고 파일은 룸당 최대 12개까지 … (현재 15개).')` →
  3 orphan files on disk, config untouched.
- **Why it matters (and why only Low).** Requires a pre-existing drift state; it is bounded (disk
  can never exceed 12 per room, so it cannot be used to fill a volume); and the very next render
  surfaces the files via `find_asset_drift`'s "업로드되었지만 어느 그룹에도 속하지 않은 파일"
  warning. The same window exists, even more narrowly, if `update_stage_config` raises or returns
  `False` after a successful upload.
- **Suggested action.** Either delete the accepted files when the post-upload validation fails,
  or note the residual window in the module docstring so the next reader does not assume the
  pre-flight is total.

### R-04 — Admin layer restates the title/subtitle length rules in help text
- **Severity: Low** · **Deferred**
- `admin.py:892` `help="… (최대 120자)."`, `admin.py:897` `help="… (최대 80자)."`
- **What.** The issue explicitly says *"검증 자체는 … validate_stage_config 와 … save_asset 이
  담당 — admin 계층에서 규칙을 재구현하지 말 것."* Everything else obeys this; these two literals
  do not. `stage_config` already exports `EVENT_TITLE_MAX_LEN` / `EVENT_SUBTITLE_MAX_LEN`.
- **Why it matters.** Pure drift risk: change the constant and the form silently lies. Two-line
  fix (`help=f"… (최대 {EVENT_TITLE_MAX_LEN}자)."`). Left unfixed per the Medium/Low policy.
- Bonus nit: neither `st.text_input` sets `max_chars`, so over-long input is only caught at save.
  That is arguably correct (the validator owns the rule and gives the reason), just noting it.

### R-05 — Partial-success save leaves the section visibly stale
- **Severity: Low** · **Deferred**
- `admin.py:1019-1028`
- **What.** On the "some accepted, some rejected" path the upload nonce is incremented but the
  function returns without `st.rerun()` (deliberately, to preserve the error text and the typed
  values). Consequence: `_render_stage_logo_manager`, which rendered *above* earlier in the same
  run, does not show the logos that were just saved, and the uploader below still displays the
  files it just consumed. Both correct themselves on the next interaction.
- **Why it matters.** Only a momentary UX inconsistency, and the warning text explains the
  outcome — but an admin can reasonably read "일부 로고를 제외하고 저장했습니다" plus an unchanged
  logo list as "nothing was saved" and re-upload. Consider rendering a short recap of the accepted
  filenames alongside the warning.

### R-06 — Stale docstring about the delete button label
- **Severity: Low** · **Deferred**
- `admin.py:744` — *"삭제 버튼은 "삭제" 텍스트 라벨 + 파일명을 담은 ``help``"*. The button now
  renders `f"삭제 · {filename}"` (concurrent a11y change, see the notice at the top). The docstring
  should describe the filename-in-label decision, which is the a11y-relevant one.

### R-07 — New e2e helpers are timing-fragile under load
- **Severity: Low** · **Deferred**
- `tests/e2e/test_admin_stage_config_e2e.py:80-86` (`_login_form_present`),
  `:212-217` (`_open_room_tab`)
- **What.** `_login_form_present` gives the first render at most ~3 s + one 3 s retry, and
  `_open_room_tab` clicks the tab, sleeps a fixed 2000 ms and then asserts the panel heading is
  visible — with no check that the tab actually became `aria-selected="true"`. On my first runs,
  with two leftover Streamlit servers competing for CPU, **all 5 tests failed**: once at
  `assert landed == username` (`landed=None`, the login form had not painted yet), once
  with `Locator.wait_for … 🎬 무대 화면 설정 … Timeout 20000ms`, and once with the operator test
  timing out on `📱 룸 QR 코드`. On an unloaded machine I then got **6/6 clean runs** (39 s each),
  so this is contention sensitivity, not a real defect — but CI runners are contended.
- **Why it matters.** These four tests are the *only* automated evidence for AC-1, AC-2 and AC-7,
  so a flaky red here will be triaged as "flaky, retry" and stop guarding anything.
- **Suggested action.** Poll for `input[aria-label="사용자명"]` with a deadline instead of two
  fixed sleeps, and in `_open_room_tab` retry the click until
  `tab.get_attribute("aria-selected") == "true"`.

### R-08 — No HTTP-level assertion for `X-Content-Type-Options`
- **Severity: Low** · **Deferred**
- `tests/test_branding_assets.py:820-831`
- The unit test on `build_asset_headers` is strong, and `branding_routes.handle_branding_asset`
  passes that dict straight into `web.Response(headers=...)`, so the header does reach the wire.
  But the sibling SVG-CSP guarantee has an HTTP-level test
  (`tests/test_sse_broadcast.py:1174-1186`) and this one does not — worth one parallel assertion
  so the two security headers are guarded at the same layer.

### R-09 — Informational: preview `<img>` has no meaningful `alt`
- **Severity: Low (a11y, informational)** · **Deferred**
- `admin.py:869-878`. Measured in Chromium: `st.image(..., caption=filename)` emits
  `<img alt="0">` and renders the caption as a sibling `stCaptionContainer`. The issue's a11y
  instruction (`caption=파일명`) is satisfied and the filename is announced adjacently, and the
  delete button now carries the filename in its own accessible name — so this is acceptable. Noted
  only so the a11y record is honest about `alt` being an index, which is a Streamlit limitation.

## Security Findings
- **Injection**: none. `update_stage_config` uses parameterised SQL; no shell, no templating.
- **AuthZ (RL-002)**: `_render_admin_room_stage_config` is called only inside
  `if is_role_admin:`; `room_id` is chosen from server-derived `visible_rooms`, never from client
  input; `room_id`/`filename` are re-sealed by `branding_assets` sanitisation on every call.
  Verified in the browser that the operator session renders neither the section, nor
  `설정할 룸`, nor the `/stage/{room_id}` URL.
- **Secrets**: none introduced.
- **RL-006**: audited every UI-facing string in the new code. `partition_uploads` forwards
  `str(e)` only for `ValueError` (all curated Korean, including `save_asset`'s own OSError→
  ValueError conversion); everything else — `except Exception` in `partition_uploads`,
  `delete_stage_logo`, `_render_logo_preview`, `_upload_pairs`, the `get_stage_config`/
  `list_assets` load, `update_stage_config`, and the QR build — prints `{e!r}` to stdout and shows
  a fixed generic message. No path leaks a path/errno/traceback. (R-01 was the one exception, and
  it is now fixed.)
- **Uploaded SVG**: `_render_logo_preview` hands the SVG *text* to `st.image`, which base64-encodes
  it into `data:image/svg+xml;base64,…` on an `<img src>`. Confirmed in the DOM. `<img>` is a
  non-scripting context, so `<svg onload=…>` cannot execute — the admin preview does not
  re-open the hole `SVG_CSP` closes for the serving route.
- **RL-016 hand-off**: `event_title`/`event_subtitle` are the XSS carriers this form now lets an
  admin populate. The `<script>` sink was already discharged in ISSUE-40 by `_json_for_script`;
  nothing in this PR re-widens it. No action, recorded for the chain.
- **Informational**: Streamlit's uploader advertises "Limit 200MB per file" and buffers the whole
  body server-side before `save_asset`'s 2 MB check rejects it. Admin-only (already fully
  privileged) and nothing reaches disk, so no real attack surface — but `server.maxUploadSize`
  could be lowered to align the two limits.

---

## F-4 / F-5 / R-04 verification verdicts

- **F-4 (surface `validate_stage_config`'s reason string) — GENUINELY IMPLEMENTED.**
  `admin.py:993-997` calls `validate_stage_config(candidate)` directly and does `st.error(reason)`,
  so the reason never has to be recovered from `update_stage_config`'s bool. The double
  validation is **sound in intent** — the pre-flight really does prevent uploads when the text is
  invalid, which removes the largest orphan window — but it is **not total**: the asset-count
  branch can still fail after files are written (see R-03, reproduced). Verified in the browser
  that reasons render (`"자막 컬럼 비율은 1/4 또는 1/3 중 하나여야 합니다."` style) and that no
  internal detail rides along.
- **F-5 (warn on silent `normalize_stage_config` drops) — PARTIALLY / DECORATIVELY IMPLEMENTED.**
  `find_asset_drift` + `_warn_stage_asset_drift` are real and fire (I saw
  "설정에는 있으나 파일이 없는 로고: logo.png" in the browser). `describe_stage_config_drops` is
  **structurally unreachable-as-non-empty** from `_save_stage_config` — see R-02, confirmed by
  experiment. The underlying risk is closed; the named mechanism is not what closes it.
- **R-04 (`X-Content-Type-Options: nosniff`) — GENUINELY IMPLEMENTED.**
  `branding_assets.build_asset_headers` adds it unconditionally (correctly *not* gated on
  extension), and `branding_routes.handle_branding_asset` passes the dict straight to
  `web.Response(headers=...)`, so it reaches every asset response including the
  `application/octet-stream` fallback. Test is mutation-verified: deleting the header line turns
  all 5 parametrised cases red.

---

## Test Quality (RL-004 mutation assessment)

I ran real mutations rather than reasoning about them. All three of the tests the brief singled
out are genuinely discriminating.

| Test | Mutation applied | Result |
|---|---|---|
| `TestStageConfigSectionIsAdminOnly` (AST) | moved `_render_admin_room_stage_config(...)` out of `if is_role_admin:` to the top level of `show_room_management` | **3 of 4 FAIL** (`test_called_inside_the_is_role_admin_branch`, `test_never_called_outside_the_admin_branch`, `test_receives_the_role_filtered_room_list`). Only `test_function_is_defined_at_module_level` survives, correctly — it targets the definition. Also survives an `else:`-clause move, because `_admin_only_statements` deliberately collects `node.body` only. Strong. |
| `test_nosniff_is_always_present` | removed `"X-Content-Type-Options": _NOSNIFF` | **5 of 5 parametrised cases FAIL**. Asserts the exact value `== "nosniff"`, not membership. Strong. |
| `test_config_is_updated_even_when_file_already_gone` | made `delete_stage_logo` early-return when `delete_fn` is falsy | **FAILS**. It asserts `ok is True`, `update_stage_config.assert_called_once()` **and** the resulting group is empty — it covers both the `delete_fn is False` path and the config write. Strong. |
| `test_oversized_and_valid_are_partitioned` | (read, not mutated) | Asserts the literal `"2MB 이하" in reason`, the exact accepted list, and `list_assets(...) == ["logo.png"]` so the rejected file left no trace. Not a truthiness check. Strong. |
| `test_unexpected_exception_is_not_leaked_to_the_admin` | (read) | Asserts both directions — `"secret" not in reason` **and** `"/srv/secret/path denied" in capsys stdout`. This is the RL-006 pattern done right. Strong. |
| e2e `test_operator_room_tab_never_renders_stage_section` | (read + browser-verified) | **Not vacuous.** It pins the role (`"오퍼레이터 대시보드" in h1`), proves the tab is non-empty (waits for `📱 룸 QR 코드`, then asserts the seeded room's `/view/{id}` code is visible — the fixture assigns the room to the operator precisely so the tab cannot be empty), and only then asserts absence of the heading, of `설정할 룸`, and of the `/stage/{id}` URL. `_open_dashboard_as` additionally asserts which account actually landed, so it cannot pass while silently logged in as admin. |

Weaknesses worth recording:

- **AC-4 and AC-5 have no automated end-to-end coverage.** The unit tests cover
  `partition_uploads` and `delete_stage_logo` in isolation; the e2e file covers AC-1/2/6/7. The
  most subtle mechanism in the PR — "the typed title survives a failed save", which depends on
  stable widget keys + `_seed_stage_form_state` + the absence of `st.rerun()` — is asserted by
  nobody. I verified it by hand in Chromium (results below); it works, but a refactor that adds a
  `st.rerun()` to a failure path would break it silently.
- No test covers `_seed_stage_form_state`'s room-change branch (the cross-room-leak concern). Also
  verified by hand.
- `test_stage_and_view_paths_differ_only_in_the_segment` is a nice touch — it would catch a
  copy-paste that pointed `build_stage_url` at `/view/`.

### Manual browser verification (Chromium, Playwright, real Streamlit server)

| Check | Result |
|---|---|
| AC-4: 4 MB PNG + typed title → save | alert `'big.png' — 로고 파일은 2MB 이하만 업로드할 수 있습니다 (현재 4.0MB).` + `일부 로고를 제외하고 저장했습니다.` — **PASS** |
| AC-4: title preserved after the failure | `input_value() == "2026 개발자 콘퍼런스"` — **PASS** |
| AC-4: nothing written to disk | `list_assets(room) == []` — **PASS** |
| AC-5: upload PNG + SVG → save | disk `['logo.png','mark.svg']`, config `주최=['logo.png']`, `후원=['mark.svg']` — **PASS** |
| AC-5: click 삭제 | disk `['mark.svg']`, config `주최=[]` — **PASS** (both file and config entry) |
| Cross-room leak: type in room A without saving, switch to room B | room B's title field is `''` — **PASS**, no leak. `_seed_stage_form_state`'s room-change branch reseeds correctly and unsubmitted form edits are discarded. |
| Previews | PNG → `/media/…`, SVG → `data:image/svg+xml;base64,…`; failure fallback shows `logo.png (미리보기를 표시할 수 없습니다)` with the exception only on stdout — **PASS** |
| Stage URL / QR | `http://localhost:8766/stage/roombbb` + `📥 무대 QR PNG` — **PASS** |

---

## Over-Engineering (minimality)

The `admin.py` decomposition is **justified, not scaffolding**. Ten of the eleven new helpers map
1:1 onto a distinct AC or onto the file's own existing convention
(`_render_admin_room_create` / `_render_room_qr_section` / `_render_room_viewer_metrics` are all
one-`st.divider()`-per-function). `_report_rejected_uploads` has three call sites,
`_safe_download_stem` has two and **removes** a pre-existing duplicate at `_render_room_qr_section`,
and `_upload_pairs` is the Streamlit→pure boundary the issue asked for. The `save_fn=` /
`delete_fn=` injection points are not YAGNI: both are exercised by tests that could not exist
otherwise (`test_unexpected_exception_is_not_leaked_to_the_admin`, `test_db_exception_is_not_leaked`).
`qr_generator._build_room_url` has two real callers and prevents the two normalisers from drifting.

One finding:

- `admin_logic.py:330-400: delete describe_stage_config_drops (+ admin.py:1013-1017 call site, + tests/test_admin_stage_config.py:372-444 TestDescribeStageConfigDrops) → structurally unreachable from _save_stage_config; keep find_asset_drift, which does fire, and record F-5 as discharged structurally` (see R-02 — reports only, needs a team decision)
- `admin.py:892,897: shrink 하드코딩된 "최대 120자"/"최대 80자" → f-string on EVENT_TITLE_MAX_LEN / EVENT_SUBTITLE_MAX_LEN` (see R-04; 0 net lines, but removes a duplicated rule)

**Net removable: ≈ 120 lines** (≈65 source + ≈55 test), all in the `describe_stage_config_drops`
branch. Everything else in the diff is lean.

---

## AC coverage table

| AC | Requirement | Verdict | Evidence |
|---|---|---|---|
| AC-1 | admin sees "🎬 무대 화면 설정" + room dropdown | **PASS** | e2e `test_admin_room_tab_shows_stage_section_with_room_select` (asserts the seeded room label is actually selected, not just that a dropdown exists); browser-confirmed |
| AC-2 | title restored after refresh (DB round-trip) | **PASS** | e2e `test_saved_event_title_is_restored_after_refresh` (polls the DB, then re-enters with cookies kept = new Streamlit session) + unit `test_form_values_survive_the_round_trip` |
| AC-3 | `1/3` selected → `get_stage_config().caption_ratio == "1/3"` | **PASS** | unit `test_form_values_survive_the_round_trip` against a real SQLite DB; e2e asserts the radio exposes both ratios |
| AC-4 | 4 MB upload → not saved, "2MB 이하" shown, title not lost | **PASS (behaviour) / partial test coverage** | unit `test_oversized_and_valid_are_partitioned` covers the rejection + reason; the *title-preservation* half is untested — I verified it manually (see table above). See R-05 for the stale-list nit on this path |
| AC-5 | delete removes the file and the `logo_groups` entry | **PASS** | unit `test_deletes_file_and_updates_config`, `test_config_is_updated_even_when_file_already_gone` (mutation-verified), `test_uses_branding_delete_asset_by_default`; manually verified end to end |
| AC-6 | operator does not see the section (server-side, RL-002) | **PASS** | AST test (mutation-verified, 3/4 turn red) + e2e with explicit non-vacuity guards; browser-confirmed |
| AC-7 | `{base}/stage/{room_id}` text + QR PNG download button | **PASS** | e2e `test_admin_sees_stage_url_and_qr_download_button` (checks the `/stage/` code element specifically so it cannot be satisfied by the `/view/` QR section) + `TestBuildStageUrl` |
| AC-8 | disk error → generic message only, detail to console (RL-006) | **PASS** | unit `test_unexpected_exception_is_not_leaked_to_the_admin` asserts both directions; full audit of `admin.py`'s try/except blocks found no leak path |

| Issue `#### Tests` item | Verdict |
|---|---|
| `build_stage_config_from_form` → `validate_stage_config` True | **PASS** — `test_valid_form_produces_validatable_config` asserts `== (True, "")` plus every field |
| `partition_uploads` mixed list → 1 accepted / 1 rejected with a readable reason | **PASS** — asserts the literal `"2MB 이하"` |
| AST: `_render_admin_room_stage_config` inside `is_role_admin` | **PASS** — mutation-verified |
| `build_stage_url` trailing-slash invariance | **PASS** — 6 parametrised bases incl. `///`, plus whitespace and empty-input cases |
| delete → both `delete_asset` and `update_stage_config` called | **PASS** — mutation-verified, and the `delete_fn is False` path is covered separately |

---

## Files changed by this review

- `admin.py` — `_render_stage_logo_manager`: `enumerate` + index in the delete-button key (R-01).
- `admin_logic.py` — new `_dedupe_preserving_order`; `build_stage_config_from_form` uses it (R-01).
- `tests/test_admin_stage_config.py` — two regression tests, both verified RED without the fix.

No other file touched. Registry/notes files untouched, per instructions.

---

## Suggested follow-up issues

1. **Decide F-5's fate** (R-02) — delete `describe_stage_config_drops` and document that F-5 is
   discharged structurally, or give it a live feed. ~120 lines either way.
2. **Close or document the count-path orphan window** (R-03) — unlink accepted files when the
   post-upload validation fails.
3. **Harden the new e2e helpers** (R-07) — deadline-poll the login form; assert
   `aria-selected="true"` after the tab click.
4. **Add an e2e for AC-4's "input is not lost"** — the mechanism (stable keys + conditional
   seeding + no `st.rerun()` on failure) is load-bearing and currently unguarded by CI.
5. **Source the help-text limits from `stage_config` constants** (R-04).

---

## Learning Extraction (proposed entries — for the team-lead to merge into `docs/review_lessons.md`)

I did **not** edit `docs/review_lessons.md`. Proposed deltas:

- **[RL-004] — increment Frequency to 5**, Observed-In += "PR #125 (ISSUE-39): *inverse* case —
  the guard tests here are strong (three mutations verified RED), but two ACs (AC-4's
  input-preservation half, AC-5's end-to-end delete) have no automated test at all, so the suite
  is green while the ACs rest on manual verification. Absence of a weak assertion is not presence
  of a guard."
- **[RL-022] (new) — Widget keys derived from user-controlled data collide and abort the whole
  Streamlit page.** *Category: Code Quality.* A `key=` built from a filename/label looks unique
  until the data model permits a duplicate; Streamlit then raises `StreamlitDuplicateElementKey`
  and kills every element *after* the offending one, so an unrelated feature (operator assignment,
  force close, room logs) disappears and a traceback with server paths is painted into the UI.
  *Prevention:* derive widget keys from a positional index plus the identifier, never from the
  identifier alone, and make the layer that assembles the list guarantee uniqueness. Observed-In:
  PR #125 (ISSUE-39).
- **[RL-023] (new) — A deferred finding is "discharged" by a helper that its own production caller
  can never trigger.** *Category: Architecture.* `describe_stage_config_drops` was written to
  answer F-5, is fully unit-tested, and returns `[]` for every input the real call site can
  produce, because the candidate is assembled from an already-normalised source. The tests pass,
  the review checkbox ticks, and the warning never appears. *Prevention:* when a follow-up finding
  is closed by a new helper, the closing PR must show the helper firing **from its production call
  site**, not only from a hand-built fixture. Observed-In: PR #125 (ISSUE-39).
- **[RL-024] (new) — A pre-flight validation closes the window it was written for and is then
  described as total.** *Category: Code Quality.* The pre-flight here genuinely prevents
  text-invalid saves from writing files, but the second validation can still fail on a limit whose
  two enforcers count different things (disk files vs config references), so orphans survive.
  *Prevention:* when two layers enforce "the same" cap over different sources of truth, state which
  one is authoritative and test the disagreement case explicitly. Observed-In: PR #125 (ISSUE-39).

---

## Team-lead resolution addendum — ISSUE-39 (PR #125)

Post-review dispositions. Two findings were fixed after the reviewer's pass; the rest are deferred with rationale.

| Finding | Severity | Disposition |
|---|---|---|
| R-01 duplicate widget key aborts the room-management tab | High | **FIXED in review** (reviewer). Root cause deduped in `build_stage_config_from_form` via `_dedupe_preserving_order`; defence-in-depth index added to the widget key. Two regression tests, both verified RED without the fix. Browser repro re-run clean. |
| R-02 `describe_stage_config_drops` unreachable → **F-5 decorative** | Medium | **FIXED, not deleted** (commit `8ade13a`). The reviewer was right that the function could never fire from `_save_stage_config` — the candidate is assembled from an already-normalised source. Rather than delete a required deliverable, F-5 was wired to the signal that actually exists: new `Room.get_raw_stage_config()` returns the **unnormalised** DB blob, and the form now warns on the **load** path, before the normalise-and-save round trip destroys the data. Proven from the production sequence by `TestStageConfigDropWarningsFromDatabase::test_unknown_group_label_in_db_blob_produces_a_warning`. The dead `_save_stage_config` call site was removed; the function was kept. |
| R-04 admin restates title/subtitle length rules in help text | Low | **FIXED** (`8ade13a`) — help strings are now f-strings over `EVENT_TITLE_MAX_LEN` / `EVENT_SUBTITLE_MAX_LEN`. |
| R-06 stale docstring about the delete button label | Low | **FIXED** (`8ade13a`). |
| R-03 orphan-file window on the asset-count path | Low | Deferred. Requires pre-existing config↔disk drift; bounded (disk cannot exceed 12/room); the next render surfaces the files via `find_asset_drift`. Follow-up. |
| R-05 partial-success save leaves the logo list visibly stale | Low | Deferred — self-corrects on the next interaction. |
| R-07 new e2e helpers are timing-fragile under CPU contention | Low | Deferred. 6/6 clean runs unloaded; failures only reproduced with competing Streamlit servers. Should be deadline-polled before CI adoption. |
| R-08 no HTTP-level assertion for `X-Content-Type-Options` | Low | Deferred deliberately — the HTTP-level test belongs in `tests/test_sse_broadcast.py`, which ISSUE-40/41 were actively editing in parallel worktrees. `build_asset_headers` is the single owner and its unit test is mutation-verified. |
| R-09 preview `<img alt>` is an index | Low (informational) | No action — measured Streamlit limitation; filename is announced adjacently and now also in the delete button's accessible name. |

### Deferred-finding hand-off verdicts (final)

- **F-4 — GENUINELY IMPLEMENTED.** `admin._save_stage_config` calls `validate_stage_config(candidate)` directly and renders `st.error(reason)`. `Room.update_stage_config` only logs its rejection reason, so its bool alone would have swallowed it. Reviewer confirmed the reason renders in a real browser with no internal detail attached. Residual gap: the asset-count branch can still fail after files are written (R-03, Low, deferred).
- **F-5 — GENUINELY IMPLEMENTED after rework.** Initially decorative (R-02); now fires from the load path against the raw DB blob. Two live warning surfaces: `describe_stage_config_drops` (normalisation drops) and `find_asset_drift` (config↔disk drift). Residual gap recorded below.
- **R-04 (nosniff) — GENUINELY IMPLEMENTED.** `branding_assets.build_asset_headers` emits `X-Content-Type-Options: nosniff` unconditionally — correctly not gated on extension, so it also covers the `application/octet-stream` fallback. `branding_routes.handle_branding_asset` passes the dict straight to `web.Response(headers=...)`. Mutation-verified: deleting the line turns all 5 parametrised cases red.

### Residual gap knowingly accepted
`describe_stage_config_drops` still stays silent for two normalise-drop shapes: a group whose `assets` is a non-list, and a non-dict entry inside `logo_groups`. The latter is currently asserted as intentional silence by an existing test, so widening it needs that test revisited. Logged as a follow-up rather than changed late in review.

# Review Notes — ISSUE-42 (PR #129)

Reviewer: independent code reviewer (correctness / security / test quality / minimality).
Branch `issue/ISSUE-42-stage-display-capture`, head `e480693`, rebased on `0a72f90`.
Diff under review: `components/stage.html` +475/−18, `tests/test_stage_page.py` +437/−11,
`tests/e2e/test_stage_capture_e2e.py` +714 (new). No server-side change —
`sse_broadcast.py` is untouched and `?debug=1` is parsed client-side.

**Verdict: approve-with-fixes** (fixes applied in-worktree and re-verified — see below).

## Scope

Verified against the `### ISSUE-42:` contract in `issues.md` (line 2160), FR-074/075/081,
NFR-025/026/027, and test_plan Gap 8 (TC-060/061/063/064). The UI dimension is owned by the
parallel ui-reviewer (`docs/ui_review_notes.md`); this section stays on correctness,
security, test discrimination, and minimality.

The user's hard constraint — *"비디오 캡쳐 형태로 가더라도 버벅인다거나 프레젠터로 조작이
안되거나 해서는 안되"* — was treated as an acceptance criterion. I checked the priority the
issue sets out: the PRIMARY invariant is that the stage window never holds OS focus while the
deck is driven; absence of keydown handlers is the SECOND line of defence. The implementation
and its tests get this ordering right (focus-surface removal is enforced behaviourally in e2e;
the keydown guard is a structural static assertion), so there is no finding on that axis.

## Independent verification performed

Everything below was run by me in the worktree; none of it restates the implementer's report.

- `uv run pytest -q` → **1135 passed / 86 deselected / 94.16 % coverage** at head `e480693`
  (the implementer reported 94.20 %; the real number is 94.16 %).
- `uv run pytest -q -m e2e tests/e2e/test_stage_capture_e2e.py --no-cov` → **17 passed**.
- `uv run ruff check .` → clean. `uv run black --check .` → clean at head.
- Read every changed line of all three files, plus the surrounding untouched JS
  (typewriter loop, visibilitychange handler, EventSource wiring) for regressions.
- Confirmed the placeholder set in `stage.html` is **byte-identical** to the base commit
  (`{{INITIAL_STATE}} {{OUTPUT_LANGS_JSON}} {{PRIMARY_LANG}} {{ROOM_ID}} {{ROOM_NAME}}
  {{STAGE_CONFIG_JSON}}`) — no new template injection point, so no new `_json_for_script`
  obligation arises.
- Counted the guard assertions with an AST walk rather than by eye (below).
- Applied **35 source mutations** to `components/stage.html` by hand via a scratchpad harness
  that restores the file byte-for-byte in a `finally:` block, and confirmed
  `git diff --quiet components/stage.html` afterwards.
- Probed the capture `<video>` element's real runtime state in headless Chromium with a
  standalone Playwright script to settle a test-design question empirically (below).

### Note on concurrency (affects reproducing my numbers)

The parallel ui-reviewer is editing **the same worktree** concurrently. Partway through my
review `tests/test_stage_page.py` acquired uncommitted edits that are not mine (a
`.capture-hint` backdrop change from `_FRAME_RGB` to `_CANVAS_RGB` plus a new
`test_capture_surfaces_declare_an_opaque_background`). I left them alone. Two consequences:

1. My final suite numbers (1139/88) include their four in-flight tests, not just my two.
2. `uv run black --check .` currently **fails on `tests/test_stage_page.py`** — an
   `assert ... not in {...}` that black wants re-wrapped. I verified the committed `HEAD`
   version of that file *is* black-clean, so this is purely their uncommitted work in
   progress, not a defect in PR #129. **It must be formatted before merge.** My own changed
   file is ruff-clean and black-clean.

## Mutation audit (my own kill matrix)

RL-004 has now recurred five times in this codebase and was caught only by manual mutation
last time, so I did not trust the implementer's matrix. Each mutation below was applied to
`components/stage.html`, run against the named suite(s), then reverted.

| # | Mutation | Result |
|---|---|---|
| M01 | stray `preventDefault()` | killed — `test_no_presenter_keyboard_interference` |
| M02 | 2nd `keydown` listener outside the debug guard | killed — same |
| M03 | defer the registration behind an IIFE *inside* the guard | **equivalent mutant** (see below) |
| M03b | **move** the single registration to module scope (count stays 1) | killed — same + 3 e2e |
| M04 | drop `{ passive: true }` | killed — same |
| M05 | add `stopPropagation()` | killed — same |
| M06 | add `<a href>` | killed — `test_no_interactive_controls` |
| M07 | add `<input>` | killed — same |
| M08 | add `<button>` without `type="button"` | killed — same |
| M09 | `surfaceSwitching: "include"` | killed — `test_capture_constraints_are_pinned` |
| M10 | `frameRate.ideal` 30 → 60 | killed — same |
| M11 | remove `object-fit: contain` | killed — `test_letterbox_css_present` |
| M12 | remove the `"wakeLock" in navigator` guard | killed — `test_wake_lock_sits_behind_a_capability_guard` |
| M13 | add an auto `requestFullscreen()` | killed — `test_no_presenter_keyboard_interference` |
| M14 | remove `contain: layout paint` | killed — `test_caption_column_is_paint_contained` |
| M15 | add `will-change` to the capture container | killed — `test_no_gratuitous_compositing_hints` |
| M16 | drop `removeEventListener("ended")` | killed — `test_reconnect_does_not_tear_itself_down` |
| M17 | drop `document.activeElement?.blur()` | killed by the **static** test only (see F-4) |
| M18 | never hide controls after connecting | killed — 3 e2e tests |
| M19 | leak `e.message` into the DOM | killed — `test_not_allowed_error_leaks_no_exception_text` |
| M20 | self-capture heuristic drops the resolution half | **SURVIVED → fixed (F-1)** |
| M21 | handoff prompt never auto-dismisses | killed — `test_handoff_prompt_appears_once_and_auto_dismisses` |
| M22 | `onCaptureEnded` leaves the stream attached | killed — `test_track_ended_falls_back_to_the_title_card` |
| M23 | debug overlay renders without `?debug=1` | killed — `test_overlay_is_absent_without_the_query_flag` |
| M24 | corner double-click does not restore the cursor | killed — `test_corner_double_click_restores_the_controls` |
| M25 | wake lock never re-requested on visibility return | killed — `test_wake_lock_reuses_the_existing_visibilitychange_listener` |
| M26 | `MAX_LINES` 60 → 600 | killed — 2 tests |
| M27 | typewriter back to `setInterval` | killed — 2 tests |
| M28 | reselect corner ignores the `captureStarted` guard | **SURVIVED** → follow-up (Low) |
| M29 | `onWindowBlur` stops dismissing the prompt | killed — 2 tests |
| M30 | `_showControls` stops toggling the cursor class | killed — 2 tests |
| M31 | wake lock requested with no live track / no re-entrancy guard | **SURVIVED** → follow-up (Low) |
| M32 | `_releaseCapture` stops the track *before* detaching the listener | killed — 2 tests |
| M33 | drop `autoplay muted playsinline` from `<video>` | **SURVIVED → fixed (F-2)** |
| M34 | drop only `muted` | **SURVIVED → fixed (F-2)** |
| M35 | never attach the stream to `<video>` | **SURVIVED → fixed (F-3)** |

**M03 is an equivalent mutant, not a survivor.** My first attempt wrapped the registration in
an immediately-invoked function that was still lexically inside `setupDebugOverlay()`, so it
remained genuinely guarded and *should* survive. The faithful version (M03b) — hoisting the
one registration to module scope so it runs on every page load while keeping the count at
exactly 1 — **is killed**. That is the important result: the AC's demand that containment be
proven *structurally* rather than by substring coincidence is satisfied. The test takes the
byte offsets of the `setupDebugOverlay()` body and asserts
`start < registration.start() < end`, plus that the guard line is the function's very first
statement and that the `{ passive: true }` match does not escape the block.

## Code Review findings

### F-1 (High, fixed) — the self-capture heuristic's resolution half had no discriminating test

`_detectSelfCapture()` is two conditions ANDed: `displaySurface === "browser"` **and** the
capture resolution matching this window. The only control test,
`test_ordinary_window_capture_raises_no_warning`, feeds `displaySurface: "window"`, which is
rejected by the *first* condition alone. Replacing the entire resolution comparison with
`return true` therefore passed both suites (M20). The static test does not help either — it
asserts `"innerWidth" in detect`, and the mutant leaves that code present as dead code below
the early `return`.

Why it matters: the resolution comparison is the only thing preventing a false positive on a
legitimate capture of another browser tab — which is the *normal* case when the deck is
Google Slides or reveal.js. A false positive does not merely show a spurious banner: the code
does `_showControls(mirrored)`, so it also leaves the connect button and the mouse cursor on
screen for the whole talk, which is a direct breach of the NFR-025 primary invariant.

**Fix applied:** added
`TestStageCaptureLifecycle::test_browser_surface_of_a_different_size_raises_no_warning`
— `displaySurface: "browser"` with a deliberately mismatched resolution, asserting no warning,
controls hidden, and `cursor: none`. Re-ran M20: now killed by exactly that test.

### F-2 (High, fixed) — `<video autoplay muted playsinline>` was asserted nowhere

These three attributes are named verbatim in the issue's Scope and are the difference between
a live deck and a black rectangle: without `muted` the autoplay policy blocks playback, and
without `autoplay` nothing ever calls `play()`. Because they are declarative markup they were
caught by no behavioural assertion and no string assertion — deleting all three (M33), or just
`muted` (M34), passed the entire suite.

**Fix applied:** added
`TestStageCaptureLifecycle::test_capture_video_declares_the_attributes_that_let_it_autoplay`,
reading the DOM *properties* (`v.autoplay`, `v.muted`, `v.playsInline`) rather than matching
strings, so a typo'd attribute shows up as `False`. M33 and M34 are now killed.

### F-3 (High, fixed) — `_connect()` accepted a black frame, and made a sibling assertion pass for the wrong reason

The e2e helper defined "connected" as `capture-video.hidden === false`. Deleting
`captureVideo.srcObject = stream;` outright (M35) — i.e. showing an empty `<video>`, exactly
the black-frame state FR-075/NFR-026 exist to prevent — passed all 17 tests.

Worse, it *also* silently defanged `test_track_ended_falls_back_to_the_title_card`, whose
`srcObject === null` assertion then passed because the stream was never attached in the first
place, not because `onCaptureEnded` cleared it. This is precisely the RL-004 shape that cost
ISSUE-38 a High finding: an assertion that holds because the subject was never set up.

**Fix applied:** `_connect()` now waits for `srcObject !== null && videoWidth > 0 &&
readyState >= 2 && paused === false`. Because nearly every test funnels through this helper,
one change re-arms the whole suite. M35 is now killed by three tests.

*Empirical note:* I first asserted `currentTime > 0` and 12 tests failed. Rather than guess, I
probed the live element and found `{paused: false, readyState: 4, videoWidth: 640,
currentTime: 0}` stable at 3 s — the stub's canvas is painted once, so `captureStream()` never
advances the MediaStream timeline. `currentTime` is therefore unattainable with a static-canvas
stub; `paused === false` is the correct proxy. Recorded in the helper's docstring and as a
follow-up.

### F-4 (Medium, not fixed — follow-up) — `blur()` is held only by a string match on the path where it is load-bearing

Removing `document.activeElement?.blur()` (M17) survives the *behavioural* e2e suite entirely;
it is killed only by
`test_focus_is_returned_to_the_document_body_after_capture_starts`, which is
`assert "document.activeElement?.blur();" in stage_html`. The behavioural tests cannot
discriminate because on the normal path `_showControls(false)` sets `display: none` on the
button's container, and the browser then moves focus to `<body>` on its own — `blur()` is
redundant there.

But on the self-capture path `_showControls(mirrored=true)` deliberately *keeps* the controls
visible, so the just-clicked connect button retains focus, and `blur()` is the only thing
returning focus to the body. If it regressed, a presenter's Space keypress would re-activate
the focused button and re-open the screen-picker dialog over the live stage output. No test
asserts `activeElement` on that path. Suggested fix: assert `document.activeElement === body`
inside `test_self_capture_warns_without_terminating_the_stream`. Left as a follow-up under
end-of-sprint scope discipline; the string guard does currently hold the line.

### F-5 (Medium, not fixed — follow-up) — self-capture heuristic false-positives on a same-sized browser tab

Independent of the test gap in F-1, the heuristic itself fires whenever `displaySurface ===
"browser"` and the captured resolution is within `max(8px, 5%)` of this window's
`innerWidth/innerHeight × dpr`. Two maximised Chrome windows on the same display at the same
zoom produce identical viewport dimensions, so capturing a browser-based deck in the adjacent
window is a guaranteed false positive — banner shown, controls and cursor left on screen for
the session. The issue explicitly sanctions this heuristic as a Safari fallback and specifies
warn-don't-terminate, so this is a design trade-off rather than a defect, but the coupling of
the warning to `_showControls(mirrored)` is what turns a cosmetic false positive into an
NFR-025 breach. Worth revisiting: show the banner but keep the controls hidden until the
operator uses the corner gesture.

### F-6 (Low, not fixed — follow-up) — two unguarded lifecycle predicates

- `if (!captureStarted) return;` in the reselect handler survives deletion (M28). Impact is
  cosmetic: double-clicking the corner before ever connecting relabels the button to
  "발표자료 다시 연결" prematurely.
- `if (!captureTrack || wakeLockSentinel) return;` in `requestWakeLock()` survives deletion
  (M31). The `wakeLockSentinel` half is the re-entrancy guard; without it a
  visibility flap requests a second sentinel and leaks the first.

### F-7 (Low) — a tautological assertion

`tests/test_stage_page.py:1063`:
`assert 'id="debug-key-count"' in stage_html or "debug-key-count" in stage_html`.
The left operand can never be true — the id is assigned in JS (`counter.id = "debug-key-count"`,
`stage.html:928`), never as an HTML attribute — and the right operand subsumes it anyway, so
the whole line degrades to a bare substring check. I verified both operands directly.

### Non-findings I checked and cleared

- **Listener leaks across reconnect (RL-009).** `_releaseCapture()` detaches before stopping,
  and the ordering is load-bearing: M32 (swap the order) is killed. Reconnecting N times leaves
  exactly one `ended` listener; the e2e drives a late `ended` from the *old* track and asserts
  the new capture survives.
- **Wake-lock sentinel accumulation.** Each `request()` returns a fresh sentinel with its own
  `release` listener, so no listener piles up on one object.
- **Handoff timer leak.** `_showHandoff()` clears any existing timer before arming, and both
  `_hideHandoff()` and the blur path clear it. No leak across reconnects; the prompt cannot
  reappear because `_showHandoff()` has a single call site.
- **Second document-level listener.** Only `visibilitychange` (pre-existing, extended in place
  per RL-009), the pre-existing `DOMContentLoaded`, and the debug-only `keydown` exist. The
  wake-lock re-request extends the existing branch rather than adding a listener; M25 confirms
  the branch is asserted.
- **ISSUE-41 smoothness invariants.** `setInterval` count 0, `requestAnimationFrame` in use,
  `MAX_REVEAL_PER_FRAME = 24`, `MAX_LINES = 60`, and the double-scheduling guard
  `if (twRaf || !currentLine) return;` intact. No `will-change` anywhere (M15 killed).
  `contain: layout paint` is on `.caption-column` only and does not affect the capture column.
- **Corner double-click reachability.** `.reselect-zone` is a non-focusable `<div>` with no
  affordance; it restores `cursor: default` through the single `_showControls()` chokepoint
  (M30 killed).

## Security Findings

No Critical or High security findings. The change adds no server-side surface.

- **Injection / template (cleared).** The placeholder set is byte-identical to the base commit,
  so no value newly reaches a JS string literal and no new `_json_for_script` obligation exists.
  This was the exact defect class found in ISSUE-40's review (`html.escape` is
  backslash-vulnerable inside JS literals); it does not recur here.
- **DOM-XSS via `?debug=1` (cleared).** Parsed as
  `new URLSearchParams(location.search).get("debug") !== "1"` — a strict equality test whose
  result is only ever used as a boolean bail-out. The attacker-controlled value is never
  interpolated anywhere. The overlay is built exclusively with `createElement` +
  `textContent`, and the counter writes `String(seen)` (a number). `stage.html` contains no
  `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, or `eval` at all.
- **RL-006, internal error text reaching the DOM (cleared — checked all seven `catch` blocks,
  not just the `NotAllowedError` one the test covers).** Lines 791, 839, 848, 864, 971, 986,
  1004: every one either swallows silently or routes to a file-local constant. Enforced
  statically by an allowlist over `_setCaptureError()` call sites
  (`{"", CAPTURE_FAIL_MSG, CAPTURE_UNSUPPORTED_MSG, message}`) plus a ban on
  `e.message`/`e.name`/`String(e)`/`e.stack`, and behaviourally by an e2e test that throws a
  unique sentinel containing a fake credential path and asserts it appears nowhere in
  `page.content()` and raises no `pageerror`. M19 confirms the net is live.
- **Information disclosure via the debug overlay (Low, accepted).** `/stage/{room_id}` is
  unauthenticated, so anyone with the URL can append `?debug=1`. The overlay reveals only the
  viewer's own `document.hasFocus()` and a local keydown tally — no room, user, or credential
  data. No action needed.
- **Dependencies.** None added or changed.

## Over-Engineering (minimality axis)

The production diff is disciplined: every block in `stage.html` traces to a named AC, and the
two things that *look* redundant are not. `systemAudio: "exclude"` alongside `audio: false` is
formally redundant but is written into the issue's Scope and asserted by a test — explicitly
requested, so not a cut. The `typeof md.getDisplayMedia !== "function"` guard is not
speculative either: this project documents plain-HTTP deployment
(`VIEWER_BASE_URL=http://<host>:8766`), and `navigator.mediaDevices` is genuinely `undefined`
in a non-secure context, so without it the page throws.

- `tests/test_stage_page.py:1063: shrink` tautological `or` whose left operand is provably always false → `assert 'counter.id = "debug-key-count"' in stage_html` (also upgrades a substring check into a real one; see F-7)
- `components/stage.html:796: shrink` `function onWindowFocus()`, a three-line wrapper that only calls `_updateDebugFocus()` → register `_updateDebugFocus` directly as the `focus` listener

**Net removable: ~4 lines out of 1,606 added.** That is noise; treat the diff as lean.

## AC-by-AC coverage

**프레젠터 조작 비간섭 (하드 제약)**

| AC | Status | Evidence |
|---|---|---|
| no `preventDefault()` in any keydown handler; no document/window keydown outside `?debug=1`; that one is `{ passive: true }` counting-only | **MET** | count-based static assertions + behavioural e2e; M01/M02/M03b/M04/M05 all killed |
| page does not regain focus after handoff (no auto `focus()` / `requestFullscreen()`); capture keeps updating | **MET** | both verbatim string guards retained; e2e asserts `activeElement === body` and `fullscreenElement === null` before *and* during capture; M13 killed |
| `currentTime` keeps increasing over 30 s while unfocused | **NOT AUTOMATED** | unattainable with a static-canvas stub (measured `currentTime === 0` at 3 s with `paused === false`). My F-3 fix asserts the achievable proxy (`paused === false`, `readyState >= 2`, `videoWidth > 0`). Rehearsal item — follow-up below |
| the two ISSUE-40 guards are tightened, never deleted or weakened | **MET** | see the assertion count below |

**포커스 핸드오프 및 클릭 표면 최소화** — all three MET (one-shot prompt auto-dismisses and
also clears on `blur`, M21/M29 killed; `cursor: none` with zero visible clickables, M18/M30
killed; corner double-click restores controls *and* cursor, M24 killed).

**리허설 진단 (`?debug=1`)** — overlay absent without the flag (M23 killed) and `hasFocus()`
updates live: MET. "20 presses → counter 0" is inherently a rehearsal criterion (it requires
another OS application to hold focus); the e2e proves the counter counts every key the window
*does* receive and cancels none, which is the strongest automatable form.

**캡처 부드러움 (하드 제약)** — MET. The e2e asserts the exact constraint object by value
(`frameRate.ideal 30`, `selfBrowserSurface`/`surfaceSwitching`/`systemAudio: "exclude"`,
`audio: false`); `object-fit: contain` is scoped to the `.capture-video` rule (M11 killed);
the 60-node cap and single-rAF-loop invariants are intact (M26/M27 killed).

**복구 및 안전장치** — MET. Self-capture warns without terminating (and, after F-1, the
resolution half is finally discriminated); `ended` → title card with captions still flowing
(M22 killed); wake lock capability-guarded and re-requested through the existing listener
(M12/M25 killed); `NotAllowedError` yields generic copy only (M19 killed).

### The guard-tightening AC, verified by AST

Counted with `ast.walk` over both revisions rather than by eye:

| Test | ISSUE-40 (`6b78000`) | Now |
|---|---|---|
| `test_no_presenter_keyboard_interference` | 5 | 11 |
| `test_no_interactive_controls` | 3 | 9 |
| **Total** | **8** | **20** |

Both test **names** survive. `assert "requestFullscreen" not in stage_html` and
`assert ".focus()" not in stage_html` survive **verbatim and unconditional** (plain statements
at function-body level, not nested in any branch). The replacements assert **counts**
(`== 1`, `== 0`) rather than `in` / `not in` existence, per RL-004. Containment of the single
keydown registration inside the `?debug=1` block is proven by **byte-offset containment**, and
M03b demonstrates it is not a substring coincidence. Net assertion count went **up**, 8 → 20 —
the implementer's claim of 20 is accurate.

## Test results

| Command | Result |
|---|---|
| `uv run pytest -q` (at head `e480693`, before my fixes) | 1135 passed / 86 deselected / **94.16 %** |
| `uv run pytest -q -m e2e tests/e2e/test_stage_capture_e2e.py` (at head) | 17 passed |
| `uv run pytest -q` (after my fixes; includes ui-reviewer's in-flight tests) | **1139 passed / 88 deselected / 94.16 %** |
| `uv run pytest -q -m e2e` (whole e2e suite, after fixes) | **88 passed / 1139 deselected** |
| `uv run pytest -q -m e2e tests/e2e/test_stage_capture_e2e.py` (after fixes) | **19 passed** |
| `uv run ruff check .` | clean |
| `uv run black --check .` | **1 file** — `tests/test_stage_page.py`, from the ui-reviewer's uncommitted concurrent edit, not PR #129 (HEAD's version is clean). Must be formatted before merge |

No `skip` / `xfail` / `xpass` markers were added anywhere in the diff. The `getDisplayMedia`
stub returns a genuine canvas `captureStream()` track, not a hand-rolled mock, so the `<video>`
decodes real frames — confirmed by direct probe (`videoWidth: 640`, `readyState: 4`).

Note for whoever re-runs this: the `implement --phase test` checkpoint has a hard-coded 60 s
timeout while the suite takes ~78 s, so it exits 124 spuriously. Run pytest directly.

## Follow-ups (proposed issues, not fixed here)

1. **Assert focus on the self-capture path** — add `document.activeElement === body` to
   `test_self_capture_warns_without_terminating_the_stream` so `blur()` has a behavioural net on
   the one path where it is load-bearing (F-4).
2. **Decouple the mirror banner from the reselect controls** — showing the banner should not by
   itself leave a visible click surface for the whole session (F-5).
3. **Animate the stub canvas** so `captureStream()` advances the MediaStream timeline, enabling
   a real `currentTime`-increases assertion and closing the one un-automated AC (F-3 note).
4. **Cover the two unguarded predicates** `captureStarted` and the `wakeLockSentinel`
   re-entrancy guard (F-6), plus the request/release race: `_releaseWakeLock()` during an
   in-flight `requestWakeLock()` leaves a live sentinel with no track, because the
   `!captureTrack || wakeLockSentinel` check happens only before the `await`.
5. **Fix the tautological assertion** at `tests/test_stage_page.py:1063` (F-7).
6. **Coordination:** two reviewers sharing one worktree produced a transient black failure and
   makes test counts non-reproducible. Give parallel reviewers separate worktrees, or make the
   UI reviewer report-only.

## Confidence

**High** for correctness, security, and test discrimination: I read every changed line, ran 35
of my own mutations with an auto-reverting harness, resolved all survivors (three fixed, one
shown to be an equivalent mutant, two logged as Low), verified the guard-tightening AC by AST
rather than by eye, and settled the one test-design question by probing the browser instead of
guessing.

**Medium** on exactly two points, flagged rather than buried: (a) the "`currentTime` increases
over 30 s while unfocused" AC cannot be automated with the current static-canvas stub and
remains a rehearsal item; (b) my post-fix suite numbers are entangled with the ui-reviewer's
concurrent uncommitted edits to `tests/test_stage_page.py`, including a black violation that is
theirs, not this PR's.

## Verdict

**approve-with-fixes.** The implementation satisfies the hard constraint and gets the
focus-first / keydown-second priority ordering right. The two ISSUE-40 guards were genuinely
tightened, not weakened. The three High findings were all *test* gaps rather than production
defects — but they were the kind that let a future regression ship silently, and all three are
now fixed and re-verified by re-running the exact mutations that had survived. Merge once
`tests/test_stage_page.py` is black-formatted.

### Candidate lesson for `docs/review_lessons.md` (registry owner to apply; do not add here)

Next free ID is **RL-026** (the registry already runs to RL-025, not RL-022).
*Declarative markup attributes that gate runtime behaviour are invisible to both string and
behavioural assertions.* `<video autoplay muted playsinline>` and similar
(`<script defer>`, `<img loading>`, `<a rel="noopener">`) change what the browser does but
appear in no code path, so neither a substring guard nor a DOM-behaviour test discriminates
them. Prevention: assert the corresponding DOM **property** (`v.muted === true`), not the
attribute string. Observed-In: ISSUE-42 (PR #129), review mutations M33/M34.

---

# PR #137 — ISSUE-47 오퍼레이터 무대 모드 (code review)

리뷰 대상 커밋 `b8051f4`. 리뷰어 전용 워크트리에서 수행(UI 리뷰어와 별도 트리).
검증된 상태: 1223 passed / 112 deselected / 94.17%, ruff·black clean, ISSUE-47 e2e 12/12.

## Code Review

### F-1 (High, 수정됨) — 첫 자막이 무대 버튼을 파괴해 AC4 가 성립하지 않음
`.stage-launch` 가 `.welcome-state` 안에 있었고 `appendLine()` 이 그 서브트리를
통째로 `remove()` 한다. Chromium 측정: 자막 전 버튼 1개 → 자막 1건 후 **0개**.
복구 경로는 정지 → 시작뿐인데 AC4 가 명시적으로 금지한 룸 재시작이다. 발표자가
한 문장만 말하면 이슈의 핵심 컨트롤이 사라진다.
**UI 리뷰어가 독립적으로 같은 결함(H-1)을 찾았다.**
→ 수정: 컨트롤을 `#captionContainer` 밖 지속 오버레이 층(`.status-overlay` /
`.back-to-live` 와 같은 층)으로 이동. 복제본 2벌이 1벌로 줄고 `clearViewer()` 의
재주입 호출도 불필요해졌다. 가드 2종 추가(정적 배치 규칙 + e2e 행동 테스트).

### F-2 (High, 완화됨) — 세션 중 표시 모드 토글이 파이프라인을 무너뜨림
`display_mode` 가 BOOT payload 에 실리고 Streamlit 은 그 문자열을 iframe `srcdoc`
으로 넣으므로, 값이 바뀌면 문서가 새로 로드되어 RTCPeerConnection·마이크
스트림·WS·자막 스크롤백이 전부 사라진다. Streamlit 1.48.1 에서 측정: 값 변경 →
reload, 동일 rerun → reload 없음. 도움말 문구("두 모드가 같습니다")가 이 위험을
적극적으로 부인하고 있었다.
→ 수정: `action in ("start","starting")` 인 동안 라디오를 `disabled` 로 잠그고
도움말을 정정("세션 중에는 바꿀 수 없습니다 — 정지 후 변경하세요"). 근본 해법
(컨트롤을 컴포넌트 밖으로)은 별도 이슈로 제안.

### F-3 (Low, 수정됨) — 리스너 누적 방어가 우연에 의존
`clearViewer()` 가 노드를 갈아치우기 때문에 우연히 안전했을 뿐. F-1 수정으로
노드가 지속되면서 이 우연이 사라졌다 → `dataset.stageBound` 가드 + 두 번 호출 후
클릭이 창을 하나만 여는 e2e 테스트 추가(재검증 뮤턴트 N5 로 비공허성 확인).

### F-4 (Low, 수정됨) — "새 창"이 실제로는 탭
windowFeatures 없는 `window.open` 은 크롬/엣지/파이어폭스에서 **탭**을 연다.
NFR-029 의 근거(보조 디스플레이로 분리)와 힌트 문구가 모두 창을 전제한다.
→ `'popup=yes,width=1280,height=720'` 추가. `popup` 은 noopener 와 달리 null
반환을 유발하지 않는다.

### F-5 (Low, 미수정) — `format_display_mode_label` 의 fallback 분기는 도달 불가
`st.radio(options=list(DISPLAY_MODES))` 가 유일한 호출부라 알 수 없는 코드가
들어올 수 없다. `format_room_status_label` 선례와 동일한 관용이라 유지하되,
실질 커버리지로 계산하지 않는다.

## Security Findings

### S-1 (Medium, 기존 결함 — 이 PR 이 유발하지 않음, 후속 이슈 필요)
`html_template.replace("{{BOOTSTRAP_JSON}}", json.dumps(payload))` 는 raw
`<script>` 싱크인데 `json.dumps` 는 `<` 와 `/` 를 이스케이프하지 않는다.
`room_name` 은 admin 자유 입력(길이 검증만). 측정: `room_name` 에
`</script><script>…</script>` 를 넣으면 `window.__pwned = 1` 이 실행되고
`BOOT` 가 정의되지 않아 컴포넌트 부트스트랩이 통째로 죽는다. Streamlit 컴포넌트
샌드박스에 `allow-same-origin` 이 있어 주입 스크립트가 `localhost:8501` 오리진에서
실행된다(쿠키·localStorage 접근). admin 권한이 필요해 Medium.
RL-016 / RL-020 이 예측한 지점. ISSUE-47 은 악화시키지 않는다 — `display_mode` 는
닫힌 집합이고 `stage_url` 은 서버 생성 room id + 기존에 노출되던 `VIEWER_BASE_URL`.
→ 후속 이슈: `sse_broadcast.py` 에 이미 있는 script-context 이스케이프 헬퍼를
공유해 `app.py` 에서도 쓴다.

### S-2 (Low) — base URL 스킴 미검증
`_resolve_viewer_base_url()` 은 의도적으로 형식 검증을 하지 않는다.
`VIEWER_BASE_URL=javascript:…` 면 `window.open` / `fallback.href` 가 오퍼레이터
오리진에서 실행한다. 배포자 제어 환경변수이고 기존 `view_url` 로도 동일 노출이
있어 Low. 환경변수 외의 경로에서 base URL 을 받게 되는 순간 High 로 승격된다.

### S-3 (Low, 미해결 — 후속) — opener 차단은 실제로 동작하나 행동 커버리지가 없다
두 오리진(:18501 / :18766) 실제 Chromium 팝업으로 측정: `opened.opener = null` 은
**throw 하지 않고**, 팝업이 크로스 오리진으로 내비게이트한 뒤에도
`window.opener === null` 로 유지된다(SEVERED). 이유: `window.open` 이 반환하는
순간의 활성 문서는 아직 오리진을 상속한 `about:blank` 이라 그 대입은 동일 오리진
쓰기다. 다만 이 대입이 동기 실행 구간 밖으로 옮겨지면 크로스 오리진 `[[Set]]` 이
되어 `SecurityError` 를 던지고, 현재 `try/catch` 가 이를 `console.debug` 로
삼켜 **조용히 무력화**된다. e2e 스텁은 같은 realm 의 평범한 객체를 돌려주므로
어느 쪽이든 성공한다 → 실질 커버리지 0 (RL-026 형태). 두 오리진 HTTP fixture 가
필요해 이번 PR 범위 밖.

## Over-Engineering
- `operator_ui.py` `format_display_mode_label` 미도달 fallback (F-5) — 선례 유지로 존치
- `app.py` `_build_stage_url` 의 try/except — `build_stage_url` 은 빈 입력에서만
  raise 하고 두 경우 모두 상위 가드가 이미 배제한다. 형제 함수와의 대칭성 및
  향후 `build_stage_url` 변경에 대한 방어로 존치(리뷰어 의견과 다른 결론, 명시).
- 구조적 대안(컨트롤을 `st.link_button` 으로 사이드바에 렌더)은 F-1·F-2·S-3 을
  한꺼번에 없애고 ~400줄을 삭제하지만, 이슈의 Implementation Notes / TC-068~070 이
  컴포넌트 + `window.open` + 폴백을 명시하므로 **일방적으로 변경하지 않고 후속
  이슈로 제안**한다.

---

# ISSUE-52 / PR #141 — 오퍼레이터 세션 출력 언어를 rooms 행에 기록

리뷰어는 PR head 커밋(`4a854a1`)의 **분리된 detached worktree**(`.worktrees/review-ISSUE-52`)
에서 구현 결정에 대한 사전 지식 없이 검토했다. RED 검증을 위해 base(`72368f1`)를
별도 경로에 풀고 **테스트 파일 5개만** 덮어 재실행했다.

**판정: APPROVE-WITH-NITS.** 머지 차단 항목 없음.

## RED 증거 (이 스위트가 원래 결함을 통과시켰을 리 없다는 근거)

신규 테스트 30건 중 **22건이 base 코드에서 실패**한다
(`AttributeError: 'Room' object has no attribute 'update_session_languages'`,
`assert [] == [{'input_lang': 'ko', ...}]`,
`assert 'db is on fire' in '[Auth] 사용자 인증 성공…'`).
base 에서 통과하는 8건은 전부 **의도된 대조군/전방 가드**이며 그렇게 이름 붙어 있다
(TC-074 가 명시적으로 요구하는 렌더 4건, `..._reproduces_the_production_mismatch`(대조군),
`test_transcript_messages_never_hit_the_language_write`, 메모리 전용 모드 2건).

## Code Review — 6개 정밀 검토 항목

| 항목 | 판정 | 증거 |
|---|---|---|
| `output_langs` 미변경 (#91/#92 재결합 방지) | PASS | `database.py` UPDATE 는 3컬럼/4파라미터, `output_langs` 미등장. 가드 테스트가 공백 포함 `'["ko", "en"]'` 을 심어 `json.dumps` 재직렬화도 실패시킨다 |
| RL-025 권위 컬럼 명명 | PASS | docstring 이 `primary_output_lang`=권위, `output_lang`=미러로 선언. 한 UPDATE 가 같은 인자를 두 번 쓰고, no-op 가드가 **세 컬럼 전부**를 비교해 이미 어긋난 행을 복구한다 (`test_no_op_guard_still_repairs_a_drifted_mirror_column`) |
| RL-004 단언 강도 | PASS (예외 1건, 아래 F-3 에서 수정) | 전부 값 단언. "행이 갱신됐다" 형태 없음 |
| transcript hot path 미접촉 | PASS | `_persist_session_languages` 참조는 정의 1 + 호출 2 뿐. transcript 분기는 `_handle_transcript` 로만 간다. 참고: `_record_usage` 가 이미 transcript 마다 동기 SQLite 쓰기를 2회 하므로 이 PR 의 연결당 1회는 기존보다 가볍다 — `asyncio.to_thread` 미사용 결정이 정당화된다 |
| 실패 degradation | PASS | 헬퍼가 `try/except` → `print` → `False`. DB 계층은 아무것도 raise 하지 않는다. 테스트가 예외 문구 **와 파일 경로**가 응답에 없고 `type=="error"` 프레임도 없으며 stdout 에는 **있음**을 양방향으로 단언 |
| 멱등성 | PASS | `set_trace_callback` 기반 SQL 레벨 spy 로 1 → 0 → 1 을 확인 (세 번째가 "항상 no-op" 구현을 배제) |

## 지적 사항과 처리

| ID | Sev | 지적 | 처리 |
|---|---|---|---|
| F-1 | Medium | `_SESSION_INPUT_LANGS` 가 `webrtc.html` `#selInputLang` 의 **손복사본**인데 둘을 묶는 테스트가 없다. 옵션이 하나 늘면 그 언어 세션은 전부 거절되어 무대가 이전 언어를 계속 구독한다 — 이 이슈가 고친 결함과 같은 모양 (RL-001) | **수정** — `TestSessionLanguageWhitelistsTrackTheirSources` 추가. 입력/출력 두 드롭다운 모두 집합 동등 단언 + 각 옵션이 실제로 기록되는지 값 확인. `<option value="ja">` 를 넣는 뮤턴트로 kill 확인 |
| F-2 | Low | AC4(지원 목록 밖 코드)가 `database.Room` 계층에만 있고 WS 계층 테스트가 없다 | **수정** — `test_unsupported_output_lang_does_not_break_auth` 추가 |
| F-3 | Low | `test_recorded_room_id_is_the_server_resolved_one` 이 **RL-002 가드로서 무의미**했다. `room_id="r1"` 을 보내고 `"r1"` 을 단언하므로 `resolved_room_id` 와 `data.get("room_id")` 가 같은 값 — 구현을 바꿔도 통과한다 (RL-004 그 자체) | **수정** — room_id 를 **보내지 않는** 분기로 변경해 두 값이 갈리게 했다(`DEFAULT_ROOM_ID` vs `None`). `data.get("room_id")` 뮤턴트로 kill 확인 |
| F-4 | Low | 두 호출 지점이 ack 대비 **반대 순서**인데 한쪽만 이유를 설명한다. 미래의 독자가 한쪽을 "고칠" 수 있다 | **수정** — auth 경로에 의도적 선(先)기록 이유를 주석으로 남겼다(인증 직후 무대 화면을 열 수 있으므로 그 시점에 행이 이미 정확해야 한다) |
| F-5 | Low | 검증이 두 인자에 대해 all-or-nothing 인 점이 문서화되지 않았다 | **수정** — docstring 에 명시 |
| S-1 | High (**기존 결함, 이 PR 소관 아님**) | WS 인증이 DB 에 존재하는 `{id, username}` 쌍만 확인한다 — 토큰/비밀번호/origin 검사 없이 `0.0.0.0:8765`. 이 PR 은 그 채널에 **영속적** 능력을 하나 더 준다: 조작된 `auth` 프레임 하나로 무대 자막 채널을 조용히 돌릴 수 있다. 채널이 이미 임의 자막 발행을 허용하므로 **증분** 위험은 Medium | **후속 이슈** (아래) |
| S-2 | Medium (기존) | 인증된 사용자면 **아무 룸에나** 붙어 언어 컬럼을 바꿀 수 있다. `admin_logic.py` 는 같은 룸에 `operator_id == user_id` 를 강제하는데 WS 경로만 이 규칙이 없다 | **후속 이슈** (아래) |

이 PR 이 새로 만든 Critical/High 결함은 없다. SQL 인젝션(전량 파라미터 바인딩),
XSS(`_json_for_script` sink 기반 이스케이프 — 화이트리스트가 뚫려도 안전),
하드코딩 비밀, 신규 의존성: 해당 없음. 신규 import 엣지 `database → translation` 은
순환/임포트 시점 부작용 없음을 확인.

## Over-Engineering

- `tests/test_ws_sse_integration.py: delete _LANG_DB_COUNTER 전역 + 증가 → pytest tmp_path 가 이미 테스트마다 고유` — **적용**
- `websocket_handler.py: shrink 이미 -> bool 계약인 repo 메서드를 bool() 로 감쌈 → 직접 return` — **적용**

순 제거 ~6줄. 나머지는 lean 판정. 다음은 **의도적으로 유지**한다:
`get_by_id` 선(先)조회(no-op 과 미존재 룸을 구분한다 — SQL 가드로는 불가),
`-> bool` 반환(ISSUE-53 이 `Depends-On: ISSUE-52` 로 소비한다),
서로 다른 두 화이트리스트(`auto` 때문), docstring 분량(RL-025 권위 선언 +
#91/#92 비재결합 기록이 load-bearing).

## 뮤테이션 (16종 선언 / 16종 kill)

선언 범위: diff 내 12 + 호출 지점 2 + 리뷰 수정 검증 2. 주요 kill:
`output_langs` 동기화 추가, `primary_output_lang` 을 UPDATE 에서 제거(=원래 버그 복원),
지원 목록 리터럴 복사(은퇴한 `ja` 부활), 입력에 출력 목록 재사용(`auto` 거부),
no-op 가드 삭제/약화, `last_activity` 부수 효과, 두 호출 지점 각각 삭제,
**diff 밖** `_supported_output_langs` 룸 스코프 회귀, 입력 드롭다운 드리프트,
RL-002 위반(클라이언트 room_id 사용).

커버하지 않은 것: `_publish_to_viewers` 의 `repo is None` 조기 반환(기존 ISSUE-30 코드,
diff 밖이며 main 에서도 미커버), 그리고 `language_update` 를 **실 SQLite** `Room` 에
통과시키는 단일 테스트(fake repo + 별도 실 DB 통합 테스트로 나뉘어 있다).

## 후속 이슈

- WS 인증에 비밀이 없다 (S-1) — Streamlit 세션 HMAC 쿠키 재사용 또는 서버 발급 1회용 토큰
- `_authenticate_client` 에 operator↔room 소유권 규칙 미적용 (S-2) — `admin_logic.py` 규칙 이식
- `source_lang == target_lang` 자기 번역 낭비 — 이 PR 이후 **버그가 아니라 정상 기능**이
  되지만 LLM 호출 낭비는 남는다. 현재 어느 이슈도 담고 있지 않다

---

# PR #143 — ISSUE-48 오퍼레이터 부트스트랩 script-context 이스케이프 (code review)

리뷰 커밋 `a016061` + 리뷰 수정 1건. 전용 워크트리 `.worktrees/review-ISSUE-48`
(detached, 구현자 워크트리와 분리). 구현자 요약을 신뢰하지 않고 전 항목을 재측정했다.

## Code Review

### R-1 (Medium, **이번 PR 에서 수정**) — "시끄럽게 실패한다" 는 설계가 실제로는 조용히 실패한다
`render_component_html` 은 관대한 `.get` 대신 **엄격 인덱싱**(`values[m.group(1)]`)
을 택했고, 그 근거를 docstring 과 `test_unknown_placeholder_fails_loudly` 에
명시한다 — "오타 난 플레이스홀더는 페이지로 새는 대신 `KeyError` 로 죽고,
`app.py` 의 `try/except` 가 일반 문구로 바꿔 준다 (RL-006)".

전제의 앞쪽 절반은 참이다. 뒤쪽 절반이 거짓이었다. `app.py:491` 의 핸들러는

```python
    except Exception:
        st.error("시스템을 로드할 수 없습니다.")
```

로 예외를 **흔적 없이** 버린다. 같은 파일의 다른 모든 스왈로우는 서버 로그를 남긴다
— `app.py:130` `print(f"[Sidebar] …: {e!r}")`, `app.py:415` `[QR]`,
`app.py:438` `[Stage]`. `sse_broadcast.py:516` 도 `[Stage] room lookup failed` 로
같은 규약을 따른다. BOOT 렌더 경로만 예외였다.

결과적으로 `KeyError` 는 발생하되 **아무도 듣지 못한다.** 게다가 이 핸들러는
템플릿 파일 누락, 직렬화 불가 payload, 플레이스홀더 오타를 전부 같은 한 문장으로
접어 버려, 행사 중 오퍼레이터 화면이 죽었을 때 셋을 구분할 단서가 남지 않는다.
`try/except` 자체는 `72368f1` 부터 있던 선행 결함이지만, **이 PR 이 그것을
안전장치로 지목하면서 비로소 하중을 받게 됐다.**

수정: 파일의 기존 규약과 동일한 한 줄을 추가했다 (클라이언트 노출 문구는 불변,
RL-006 유지).

```python
    except Exception as e:
        print(f"[Bootstrap] 컴포넌트 렌더 실패: {e!r}")
        st.error("시스템을 로드할 수 없습니다.")
```

### R-2 (Low, 미해결 — 후속 판단) — 파일 전체에 대한 문자열 부재 단언
`test_app_no_longer_dumps_json_into_the_template` 은 `app.py` **전체**에 대해
`"json.dumps" not in source` 를, `test_app_drops_the_now_unused_json_import` 는
`"\nimport json\n" not in source` 를 단언한다. 실제 성질("템플릿 문자열에 맨
`json.dumps` 를 꽂지 않는다")보다 넓다. 훗날 `app.py` 가 템플릿과 무관한 이유로
`json.dumps` 를 쓰면 이 테스트가 오해를 부르는 메시지로 깨진다. 지금은 참이고
해가 없어 존치했다 — 좁히는 편집이 CI 사이클을 한 번 더 돌려 ISSUE-49 를 늦춘다.

### R-3 (Low, 미해결 — 후속 이슈 후보) — 치환 실패 정책이 렌더러마다 다르다
세 렌더러가 같은 `PLACEHOLDER_RE` 를 공유하지만 미매핑 플레이스홀더 정책이 갈린다.

| 렌더러 | 형태 | 미매핑 시 |
|---|---|---|
| `sse_broadcast._render_viewer_html:389` | `values[…]` | `KeyError` (엄격) |
| `operator_ui.render_component_html` (신규) | `values[…]` | `KeyError` (엄격) |
| `sse_broadcast._render_stage_html:494` | `values.get(…, m.group(0))` | 리터럴 `{{FOO}}` 방출 (관대) |

무대 경로에서 플레이스홀더가 하나라도 누락되면 `<script>` 안에 리터럴
`{{FOO}}` 가 남아 **SyntaxError 로 부트스트랩이 통째로 죽는다** — RL-020 이
기록한 자폭 형태와 같고, 예외조차 없어 더 조용하다. 이 PR 이 만든 결함이 아니고
(`72368f1` 선행), 고치려면 diff 가 `sse_broadcast` 무대 렌더러로 번져
ISSUE-49 대기를 늘린다. 후속 이슈로 분리 권고.

### R-4 (정보) — AC 5 유니크니스 테스트의 회피면은 구현자 우려보다 좁다
`_SUBSTITUTION_RE = re.compile(r'\.replace\(\s*"<"')` 는 큰따옴표 형태만 잡는다.
구현자가 스스로 약점으로 신고했다. 실측 판단:

- **작은따옴표 회피는 닫혀 있다.** `ruff.toml:77` 이 `quote-style = "double"`,
  CLAUDE.md §6 이 `black` 을 지정한다. `.replace('<'` 는 커밋 전 포매터가
  큰따옴표로 되돌린다.
- **`str.translate` / `maketrans` 회피는 열려 있다.** 다만 저장소 전체를 스캔한
  결과 그 형태는 프로덕션에 0건이고, RL-001 의 실제 실패 양상은 "기존 코드를
  복사" 이므로 그물이 겨냥한 표적과 일치한다.
- **더 실질적인 구멍은 `_SKIP_PARTS` 의 `"tests"` 다** (`:59`). 스캔이 `tests/`
  트리를 통째로 건너뛰므로 **테스트 안의 재구현은 보이지 않는다** — 그런데
  이 PR 이 제거해야 했던 복사본이 정확히 그것이었다
  (`test_operator_stage_mode_e2e.py` 의 자체 `json.dumps` fixture, RL-024).
  현재 그 자리는 `TestE2EFixtureUsesProduction` 이 **파일명 하드코딩**으로만
  지키므로, 새 e2e 파일이 같은 재구현을 하면 아무것도 잡지 못한다.
- 무엇보다 이 grep 은 단독 근거가 아니다. `is` 동일성 테스트 3건이 **실제 소비자
  두 곳이 같은 객체를 쥔다** 는, 정말 중요한 성질을 직접 고정한다. 소비자에
  연결되지 않은 두 번째 이스케이퍼는 무해하다.

값싼 트립와이어로서 존치가 맞다. 유일한 증명으로 읽지 말 것.

### R-5 (정보) — PR 설명의 Chromium 매트릭스와 커밋된 e2e 스위트가 다르다
PR 본문은 8행(U+2029 포함) 매트릭스를 싣지만, 커밋된
`tests/e2e/test_operator_bootstrap_escaping_e2e.py` 는 적대 케이스 **6** + 대조군
**1** 이다. U+2029 는 e2e 에 없고 유닛 `_HOSTILE_TEXT` 에만 있다. 따라서 수정 전에도
통과하는 진짜 음성 대조군은 **3 케이스**(U+2028 line-separator, attribute-payload,
benign `A홀 & B홀`) = 7 테스트이지 4 케이스가 아니다. RL-004 성질 자체는 충족되며
(아래 뮤테이션 실측), 문서 정확도 문제일 뿐 결함이 아니다.

### R-6 (Low, 미해결) — docstring 이 측정하지 않는 것을 주장한다
`test_render_helper_is_importable_without_streamlit` 은 "streamlit 없이 임포트
가능" 을 증명한다고 적었지만, 같은 파일 `:37-38` 이
`sys.modules["streamlit"] = MagicMock()` 을 주입하므로 임포트 가능성은 **측정되지
않는다.** 실제로 단언하는 것은 "`operator_ui` 가 `st` / `streamlit` 이름을 바인딩
하지 않는다" 뿐이다. 그 성질도 가치가 있어(`import streamlit as st` 추가를 잡는다)
존치하되, docstring 이 과장이다. 진짜로 만들려면 별도 프로세스
(`subprocess.run([sys.executable, "-c", "import operator_ui"])`)가 필요하다.

### R-7 (Low, 미해결) — AC 3 라운드트립 테스트는 보안 게이트가 아니다
`test_hostile_payload_round_trips_through_the_boot_literal` 은 **취약한**
맨 `json.dumps` 구현에서도 통과한다(뮤테이션에서 죽은 6건에 포함되지 않았다) —
`json.dumps` 출력도 유효한 JSON 이기 때문이다. 데이터 무결성 가드로서는 옳지만
XSS 게이트로 오독될 수 있다. 실제 보안 단언은 `</script>` **개수 비교**와
raw 문자 부재 쪽이다. 후속 편집 시 이름/주석에 그 사실을 남길 것.

## Security Findings

이번 PR 의 보안 목적(`room_name` 발 stored XSS + 자폭 부트스트랩 차단)은
**달성됐다.** 재측정 근거는 아래 "검증" 절에 있다. 신규 보안 결함 없음.

- **BOOT 데이터는 `innerHTML` 싱크에 닿지 않는다(전수 확인).** `webrtc.html` 의
  `innerHTML` 6곳 중 웰컴 화면 2곳(`:1067` `descEl`, `:1069` `rulesDiv`)은
  `inputName`/`outputName` 언어명 룩업(닫힌 집합)으로만 조립되고 BOOT 을 참조하지
  않는다. `:1474`/`:1613` 은 자막 파이프라인, `:1692`/`:1695` 는 정적 문자열이다.
  `BOOT.room_name` 은 `:1063` `textContent` 와 `:1064` `style.display` 뿐이다.

- **범위 밖(재확인, 재론하지 않음)**: `VIEWER_BASE_URL` 스킴 검증 (PR #137 리뷰
  S-2). `BOOT.stage_url` / `BOOT.view_url` 은 `fallback.href` 와 `window.open` 으로
  흐르고 `BOOT.qr_data_url` 은 `img.src` 로 흐른다 — URL 스킴 문제이지
  script-context 이스케이프 문제가 아니며, 배포자 제어 환경변수다. 이 PR 의
  이스케이프는 이 싱크들을 **손상시키지 않는다**(라운드트립 실측 확인).
- **이중 이스케이프 없음(AC 2)**: `room_name` 의 DOM 싱크는 `textContent` 와
  `style.display` 뿐임을 템플릿 전수로 확인했다. 마크업 이스케이프를 추가하지
  않은 판단이 옳다. `sse_broadcast` 가 뷰어/무대에서 쓰는
  `html.escape(name, quote=True)` 는 **마크업 플레이스홀더** 전용이고
  오퍼레이터 템플릿에는 그런 자리가 없다.

## Over-Engineering (minimality axis)

- `sse_broadcast` 의 두 별칭은 **YAGNI 가 아니다.** `_json_for_script` 는 40곳,
  `_PLACEHOLDER_RE` 는 `tests/test_viewer_page.py:759` 가
  `assert "_PLACEHOLDER_RE.sub(" in body` 로 **이름 자체를 소스에서** 고정한다.
  이름을 갈아엎는 대안이 오히려 diff 를 키운다. 별칭 유지가 최소 선택이다.
- `script_escape.py` 53줄 중 실행문은 7줄, 나머지는 sink 선택 근거다. 보안
  헬퍼로서 정당한 비율.
- `render_component_html` 의 lambda 치환은 취향이 아니라 **필수**다. 문자열
  치환형은 `re.sub` 가 값 안의 `<` 를 역참조로 해석해
  `error: bad escape \u` 로 죽는다 (실측 확인).

삭제 가능한 것은 테스트 쪽 **~6줄**뿐이다(프로덕션 코드는 0줄).

```
tests/test_operator_bootstrap_escaping.py:337-341: native  test_app_drops_the_now_unused_json_import → ruff F401 이 이미 미사용 import 를 CI/pre-commit 에서 잡고, app.py 를 json.dumps 로 되돌리는 변이는 :329 가 이미 죽인다. 고유하게 죽이는 변이가 없다
tests/test_operator_bootstrap_escaping.py:299: shrink  assert ".replace(" not in body → 삭제. :297-298 의 두 단언이 이미 형태를 고정하고, :271/:312/:147 의 행동 테스트가 관측 가능한 성질을 전부 고정한다. 구현이 정당하게 .replace 를 쓰게 되면 거짓 실패만 만든다
```

**Net removable: ~6 lines.** 이 PR 의 diff 를 넓히지 않기 위해 적용하지 않았다.
프로덕션 변경(28 + 53줄, `app.py` 는 호출 한 줄)은 그 자체로 lean 하다.

## 검증 — 구현자 주장 8건 독립 재측정

| # | 주장 | 결과 |
|---|---|---|
| 1 | 이스케이퍼 정의가 저장소에 1곳 | **확인.** `script_escape.py:41` 단 1건. `translate(`/`maketrans` 대체 구현 0건. 회피면 평가는 R-4 |
| 2 | 두 소비자가 **같은 객체** (`is`) | **확인.** `sse_broadcast._json_for_script`, `operator_ui.json_for_script`, 두 `PLACEHOLDER_RE` 모두 `is script_escape.*` → True |
| 3 | 추출이 `72368f1` 원본과 바이트 동일 | **확인.** 코드 본문 문자 단위 동일(docstring 만 개정). 추가로 원본을 복원해 **12,000 케이스 차등 퍼즈 → mismatch 0**, 라운드트립 실패 0 |
| 4 | `ensure_ascii` True→False 가 하류를 깨지 않음 | **확인.** 한글 리터럴 방출, `\uXXXX` 형태 부재. 스냅샷/바이트길이 단언 0건. `st.components.v1.html(…, height=900)` 은 **상수**라 내용 길이와 무관 |
| 5 | 이중 이스케이프 없음 (`.welcome-room` 원문 일치) | **확인.** 룸 이름 `A&lt;홀&amp;` 이 `"A&lt;홀&amp;"` 로 정확히 왕복. e2e 는 `text == room_name` 로 **원문과 등가 비교**(부재 단언 아님) |
| 6 | 오타 플레이스홀더 → `KeyError` → 일반 문구 | **부분 확인 → R-1.** `KeyError` 는 실제로 발생하고 클라이언트로 내부 텍스트가 새지 않는다. 그러나 서버 로그도 남지 않았다. 수정함 |
| 7 | 음성 대조군이 진짜이고 값 비교다 | **확인(수 정정).** 뮤테이션 하에서 정확히 **7 테스트가 통과** = 3 대조군 케이스. 4가 아니라 3 (R-5). 단언은 `boot["room_name"] == room_name`, `text == room_name`, `typeof appendLine == "function"`, `is_visible() is True` — 전부 측정값 비교 |
| 8 | `red` 체크포인트는 false-PASS, 진짜 RED 는 되돌림 실행 | **확인 — 독립 재현.** `render_component_html` 을 `72368f1` 프로덕션 라인으로 되돌린 결과 유닛 **6 failed / 14 passed**, Chromium **12 failed / 7 passed**. 구현자 수치와 정확히 일치 |

## 게이트 실측 (리뷰 수정 반영 후)

- `uv run pytest -q` → **1254 passed, 133 deselected, coverage 94.20%**
  (구현자 주장과 일치). `operator_ui.py` 100%, `script_escape.py` 100%
- e2e (ISSUE-48 19건 + ISSUE-47 무대 모드 14건) → **33 passed**
- `uv run ruff check .` → clean · `black --check` → clean · `ruff format --check` → clean
- **`test` 체크포인트는 exit 124** — `verify_checkpoint.py` 가 pytest 에
  `timeout=60` 을 하드코딩하는데 스위트는 실측 **75~98초**다. 테스트 실패가 아니라
  하네스 한계 (sprint_state 에 이미 등재된 기존 항목)

## 회귀 확인

- `components/webrtc.html` / `stage.html` / `viewer.html` **diff 0바이트** —
  ISSUE-49 와 파일 충돌 없음, 범위 확장 없음
- `stage.html` / `viewer.html` 의 `getUserMedia` / `RTCPeerConnection` / OpenAI
  참조 **각 0건** 유지
- ISSUE-47 무대 모드 e2e 14건 fixture 교체 후 전건 통과 (AC 7)

## 스프린트 브리프 정정 확인

구현자가 브리프를 정정한 내용은 **정확하다.** `build_bootstrap_payload` 키 집합에
대한 set-equality 단언은 저장소에 **존재하지 않는다** — `docs/test_plan.md:320`
의 **TC-089 (계획)** 이며 ISSUE-53 몫이다. 현존 최근접 가드는
`tests/test_operator_ui.py:474-486` 의 튜플 루프(캡션/무대 모드 간 7개 키 값 일치)
이고, 이 PR 은 payload 형태를 바꾸지 않는다.

`STATUS.md` 정정도 동시 작업 행을 덮지 않았다 — ISSUE-43 / ISSUE-52 가 in-flight
로 보존돼 있고, ISSUE-3/4/24 정정 사실이 본문에 명시돼 있다.

## 리뷰 프로세스 사고 (기록)

리뷰어 서브에이전트를 **내가 뮤테이션을 돌리고 있던 것과 같은 워크트리**로
보냈다. 그 결과 리뷰어의 첫 전체 실행이 `inspect.getsource` 에서
`import json as _json` 를 읽어 6건 실패로 관측됐고, 리뷰어는 `git status` 가 깨끗한데
`operator_ui.py` mtime 만 자기 세션 구간 안에 있다는 점으로 외부 간섭을 정확히
진단한 뒤 두 번 재실행해 그린을 확인했다(1254 passed — 내 수치와 일치). 리뷰어가
그 때문에 in-place 수정을 하나도 적용하지 않은 판단도 옳다.

이건 `sprint_state.md` 에 이미 등재된 항목의 **재발**이다 — "각 리뷰어에게 자기
워크트리를 줄 것". 뮤테이션 게이트를 돌리는 리뷰는 워크트리를 **하나 더** 파야
한다. 이번 결론에는 영향이 없지만(양쪽 최종 수치 일치), 기록해 둔다.

## Verdict

**Approve** (R-1 in-PR 수정 반영). AC 8건 전부 증거로 충족.

R-1 은 독립적으로 두 경로에서 같은 결론에 도달했다 — 내 정적 규약 대조와
리뷰어 서브에이전트의 독립 분석이 `app.py:491` 을 같은 Medium 으로 지목했다.

R-2 ~ R-7 은 전부 Low 이고 테스트 표현/정책 일관성 영역이다. 이 PR 의 diff 를
넓히지 않는 편이 낫다 — 특히 **ISSUE-49 가 이 머지를 기다리고 있고** 같은
`components/webrtc.html` 을 건드린다. R-3(무대 렌더러 엄격/관대 불일치)은
후속 이슈로 분리 권고.

# PR #142 — ISSUE-43 보안 가드 뮤테이션 점검 하네스 (code review)

리뷰 대상 커밋 `a51bb2c` (detached). 리뷰어 전용 워크트리에서 수행.
**프로덕션 코드 변경 0건** — `git diff --name-status main...HEAD` 결과가
`.github/workflows/ci.yml` (M, +39/-0), `guard_mutations.py` (A),
`guard_mutations.toml` (A), `tests/` 4건뿐임을 확인했다.

실측 기준값 (전부 리뷰어가 직접 측정. PR 본문·코드 주석의 주장은 재사용하지 않았다):

| 항목 | 측정값 |
|---|---|
| `python guard_mutations.py` | **20 guards, 20 killed, 0 survived, 0 errors — 8.9 / 9.2 / 9.7 s**, exit **0** |
| 전체 스위트 | **1261 passed / 1 skipped / 114 deselected / 94.24%**, 80.57 s |
| `ruff check . --config pyproject.toml` | All checks passed |
| `black --check .` | 63 files unchanged |
| `git diff --quiet` (러너 실행 후) | **0** (정상·SIGINT·SIGTERM·카탈로그 오류 전 경로) |

## 하네스가 실제로 무는가 — 실측 프로브 7종

1. **`Guard:` docstring 1줄 삭제** (`tests/test_branding_assets.py:297`) →
   `branding_assets.resolve_asset_path#symlink unnamed — no test declares
   "Guard: …"`, exit **1**, 뮤테이션 미적용(fail fast).
2. **허용 목록 밖 파일 지목** (`file = 'translation.py'`) →
   `ERROR — 허용 목록 밖 파일입니다: translation.py`, exit **1**.
   `shasum translation.py` 가 실행 전후 동일(`22b659dd…`).
3. **`find` 스니펫 다중 등장** (`'        return None'` = 7회) →
   `ERROR — find 스니펫이 branding_assets.py 에 7회 등장합니다 — 정확히 1회여야
   합니다`, exit **1**, 아무것도 뮤테이션되지 않음.
4. **킬 테스트 약화 — 이 이슈가 사려는 성질 그 자체.**
   `test_resolve_rejects_symlink_that_points_inside_the_room` 의 본문을 두 가지로
   약화했다.
   - (a) 단언을 자명하게 참인 것으로 (`… is not False`) →
     `SURVIVED — branding_assets.py:218`, exit **1**.
   - (b) **ISSUE-38 의 원래 결함 재현** (링크 대상을 룸 *바깥*으로 = 404 를 만드는
     것이 containment 인 그 입력) → `SURVIVED — branding_assets.py:218`,
     exit **1**. 하네스는 자신이 존재하는 이유가 된 그 실패를 정확히 검출한다.
   - 참고: 단언 3개 중 1개만 약화했을 때는 여전히 `killed` 이었다 —
     `delete_asset` 이 `resolve_asset_path` 를 재사용해 *다른* 파일을 지우면서
     `list_assets` 단언이 대신 깨졌기 때문. 이 테스트는 다중 방어선이다.
5. **샌드박스가 진짜 import 되는 모듈인가** — `_run_pytest` 를 감싸 실패 출력을
   캡처했다. pytest traceback 이
   `<module 'branding_assets' from '/var/folders/…/T/guard-mutations-dyknpoes/repo/branding_assets.py'>`
   를 보여준다. 원본 트리가 아니라 **샌드박스 사본**이 import 된다. 같은 실행에서
   원본 `branding_assets.py` 는 무변경, 샌드박스는 종료 시 삭제됐다.
6. **`--no-cov` 가 하중을 받는가** — 같은 노드를 `--no-cov` 없이 돌리면
   `1 passed` 인데 exit **1** (`FAIL Required test coverage of 50% not reached.
   Total coverage: 3.29%`). 이슈가 경고한 오탐이 실재하고 러너는 그걸 막고 있다.
7. **동시 실행 2개** → 양쪽 모두 `20 killed`, exit 0, 샌드박스 충돌·잔존 0건.
   (`tempfile.mkdtemp` + `-p no:cacheprovider` + `--no-cov` + `cwd=sandbox`.)

## 변별력 매트릭스 (meta-RL-004)

카탈로그 20건 × 바인딩된 노드 집합 20종 = **400회 pytest** 를 돌려 교차 매트릭스를
측정했다 (baseline 전건 exit 0).

- **20/20 모두 자기 바인딩 테스트에 의해 죽는다** (own-kill 실패 0건).
- **19/20 은 오직 자기 바인딩에만 죽는다** — 바인딩이 유일 변별자다.
- 유일한 중첩: `branding_assets.list_assets#symlink` 가
  `resolve_asset_path#symlink` 의 테스트에도 죽는다(그 테스트가
  `list_assets(...) == ["real.png"]` 를 단언하기 때문). 결합을 **강화**하는
  중첩이므로 결함이 아니다.
- **`resolve_asset_path#containment` 의 TOCTOU 테스트는 정직하다.**
  `monkeypatch.setattr(Path, "is_symlink", lambda self: False)` 로 만든 입력은
  `#containment` 를 죽이고 `#symlink` 는 **죽이지 않는다** — 매트릭스에서
  `#symlink` 행의 "also killed by" 가 빈 목록이다. 러너를 만족시키려고 만든
  테스트가 아니라 2단 방어를 단독 검증한다.
- 경계 고정 확인: `#size_cap` 은 `assert len(data) == MAX_ASSET_BYTES` 로 **정확히
  상한**을 받는다(`>`→`>=` 를 죽임). `#count_cap` 은 12개를 채운 뒤 13번째를
  거부한다(`>=`→`>` 를 죽임). `#exclusive_create` 는 반환 이름과 **첫 파일의
  바이트**를 둘 다 단언한다. `#quiet_reject` 는 `capsys` 로 `out`·`err` 모두
  빈 문자열을 단언한다 — 모듈이 `print()` 를 쓰므로 `capsys` 가 맞는 캡처다
  (`capfd` 불필요).

## Code Review

### F-1 (High, 미수정) — 컴파일되지 않는 뮤턴트가 `killed` 로 보고된다 (러너 안의 RL-004)

`guard_mutations.py:513-522` (뮤테이션 적용) / `:544-563` (`_verdict`) /
모듈 docstring `:59-61` (계약 선언).

**측정한 증거.** 카탈로그의 `_reject_path_signatures#dotdot` 항목 `replace` 를
괄호가 닫히지 않은 `'    if False and (".." in value'` 로 바꿔 러너를 돌렸다.

```
branding_assets._reject_path_signatures#dotdot killed by tests/test_branding_assets.py::…::test_dotdot_in_filename_is_rejected_not_stripped
1 guards, 1 killed, 0 survived, 0 errors — 0.5s
EXIT=0
```

같은 뮤테이션을 손으로 재현해 pytest 종료 코드를 직접 쟀다 — **exit 1**, 출력에
정상적인 `FAILED …` 요약 줄과 `E SyntaxError: expected ':'`.
`import no_such_module_xyz` 를 앞에 붙인 변형도 **exit 1 / FAILED** 로 동일했다.

**왜 문제인가.** 러너의 선언된 계약은 "`1` 만 kill, `2` 수집 오류 / `4` 사용법
오류 / `5` 미수집은 ERROR" 다. 그 계약은 **이 저장소에서 성립하지 않는다** —
킬 테스트가 전부 함수 몸통 안에서 `import branding_assets` 를 하기 때문에, 모듈이
깨져도 *수집* 오류(exit 2)가 아니라 *테스트 런타임* 실패(exit 1)가 된다.
따라서 `replace` 오타 하나로 게이트가 초록색이 되면서 "그 테스트가 변별력이
있다" 는 증명은 하나도 하지 않는다. **RL-004 가, RL-004 를 막으려고 만든 도구
안에서 재발하는 형태**이며 이슈 본문이 "그 오탐이야말로 RL-004 의 재발이다" 로
명시적으로 금지한 것이다. 외부 익스플로잇 경로는 없고(잘못된 카탈로그 항목이
전제이며 그것은 코드 리뷰를 거친다) 그래서 Critical 이 아니지만, 영향은 해당
항목의 보증이 **조용히 전부 소실**되는 것이다.

**최소 수정 (리뷰어가 적용해 측정 완료, 이후 되돌림).**
`run_catalog` 에서 뮤테이션을 쓰기 전에 문법 검증 한 겹:

```python
            target = sandbox / mutation.file
            original = target.read_bytes()
            mutated = original.decode("utf-8").replace(
                mutation.find, mutation.replace, 1
            )
            # 뮤턴트가 import 조차 되지 않으면 바인딩된 테스트는 가드와 무관하게
            # 실패하고 pytest 는 exit=1 을 낸다 — 그 오탐을 kill 로 세는 것이야말로
            # RL-004 의 재발이다. 문법 검증에 실패하면 kill 이 아니라 ERROR 다.
            try:
                compile(mutated, str(target), "exec")
            except SyntaxError as e:
                report.results.append(
                    GuardResult(
                        mutation.guard,
                        ERROR,
                        f"replace 결과가 문법적으로 유효하지 않습니다 "
                        f"({mutation.file}:{e.lineno}): {e.msg}",
                    )
                )
                emit(report.results[-1].render())
                continue
            try:
                target.write_text(mutated, encoding="utf-8")
                code, output = _run_pytest(sandbox, nodes, timeout)
            finally:
                target.write_bytes(original)
```

적용 후 실측: 위 오타 카탈로그가
`ERROR — replace 결과가 문법적으로 유효하지 않습니다 (branding_assets.py:318):
'(' was never closed`, exit **1** 로 바뀌고, 전체 카탈로그는 여전히
**20 killed / 9.2 s**, ruff·black clean.

문법은 유효한데 import 가 깨지는 변형(모듈 레벨 이름 오타 등)까지 막으려면
`_verdict` 에서 `code == 1` 이면서 출력에
`^E\s+(SyntaxError|IndentationError|ImportError|ModuleNotFoundError):` 가 보이면
ERROR 로 내리는 2겹을 추가한다. 모듈 docstring `:59-61` 의 계약 문구도 함께
정정해야 한다 — 현재 문구는 사실이 아니다.

### F-2 (Medium, 미수정) — SIGTERM 은 살아 있는 뮤턴트가 든 샌드박스를 남긴다

`guard_mutations.py:316` (`atexit.register`) / docstring `:39-44` (주장).

**측정한 증거.** 실행 3초 뒤 `kill -TERM` → 프로세스 exit 143,
`$TMPDIR/guard-mutations-2gnmv01s` **잔존**, 2.6 MB, 그 안의
`repo/branding_assets.py:218` 이 `if False and candidate.is_symlink():` —
심볼릭 링크 가드가 꺼진 **살아 있는 뮤턴트**다.
대조군으로 SIGINT 도 측정했다 — returncode −2, 잔존 **0건**
(`run_catalog` 의 `finally` 가 돈다).

작업 트리는 어느 경로로도 오염되지 않으므로(측정 확인) 형제 워크트리를 오염시킬
수는 없고 AC 위반도 아니다. 하지만 docstring 은 "`try/finally` + `atexit` 로 …
**모든 종료 경로**에서 제거한다" 고 주장하는데 SIGTERM 에서 `atexit` 는 돌지
않는다. CI job 취소·타임아웃마다 러너 디스크에 스테일 사본이 쌓인다.

**최소 수정** — `main()` 진입 시 3줄 (`import signal` 추가):

```python
    signal.signal(
        signal.SIGTERM,
        lambda *_: (_cleanup_sandboxes(), sys.exit(143)),
    )
```

또는 docstring 의 주장을 "SIGTERM 제외" 로 좁힌다. 핸들러 쪽을 권한다.

### F-3 (Low, 미수정) — 검증 실패 시 요약이 "1 guards" 로 나온다

`guard_mutations.py:208-213` / `:482-490`.
프로브 1(불명 가드 1건)에서 카탈로그에 20건이 있는데 출력은
`1 guards, 0 killed, 0 survived, 1 errors — 0.1s`. fail-fast 동작 자체는 옳지만
(뮤테이션 전에 중단) 숫자가 "카탈로그가 1건뿐" 처럼 읽힌다.
수정: `f"{len(self.results)}/{total} guards…"` 처럼 분모를 함께 낸다.

### F-4 (Low, 미수정) — `Mutation.note` 는 죽은 필드

`guard_mutations.py:160`, `:260`. 어디서도 읽지 않고
`grep -c "^note" guard_mutations.toml` = **0**. 카탈로그는 `#` 주석을 쓴다.

### F-5 (Low, 미수정) — 게이트된 종단 테스트의 skip 사유가 실측과 7~13배 어긋난다

`tests/test_guard_mutations.py:472-475` — 사유 문자열이
"전체 카탈로그 실행은 1~2분 걸린다".
**측정값: `GUARD_MUTATION_FULL=1 pytest …::test_full_catalog_run_kills_every_guard
-q --no-cov` → 1 passed in 8.95 s** (wall 9.17 s). 전체 스위트가 80.57 s 이므로
기본 실행에 넣어도 +11% 다. 헤드라인 AC 를 증명하는 유일한 테스트를 기본
게이트에서 빼는 근거가 **재보지 않은 숫자**인 것은 하필 이 PR 에서 아이러니다.
AC 자체는 CI `mutation` job 이 같은 커맨드로 게이트하므로 실제 영향은 낮다.
수정: 사유 문자열을 실측값으로 정정하거나 `skipif` 를 제거한다.

### 정보성 (수정 불필요)

- `tests/test_branding_assets.py:874` 의
  `monkeypatch.setattr(Path, "is_symlink", …)` 는 stdlib 메서드를 프로세스
  전역으로 패치한다. `monkeypatch` 가 teardown 에서 되돌리고, 전체 스위트가
  1261 passed 이며, 매트릭스상 이 테스트는 `#containment` 만 죽인다 — 누출
  없음. 같은 파일의 기존 `test_save_rejects_when_containment_check_fails` 가
  이미 쓰는 패턴이라 프로젝트 관례와도 일치한다.
- `collect_guard_annotations` `:297-298` — `--tests-root` 를 `repo_root` 밖으로
  주면 노드 ID 가 절대경로가 되어 원본 트리의 테스트를 가리킨다. 그 경우 뮤턴트가
  적용되지 않아 전건 `SURVIVED` → exit 1 로 **안전하게** 깨진다. 기본 경로·CI
  에서는 도달 불가.
- `SANDBOX_IGNORE` 의 `scripts` / `.claude-kit` 제외는 옳다. `scripts` 가 추적되지
  않는 심볼릭 링크임을 직접 확인했다(`git ls-files scripts` → 0건,
  `ls -la scripts` → `-> .claude-kit/scripts`, 워크트리에는 부재).
- `#svg_csp` 처럼 여러 줄 `find` 를 쓰는 항목의 `file:line` 보고를 검증했다 —
  강제 SURVIVED 로 `branding_assets.py:297` 이 나왔고 실제 소스 행도 297 이다
  (off-by-one 없음).

## Security Findings

### S-1 (Medium, 미수정) — 샌드박스 사본이 `.env` 와 `data/`(로컬 DB)를 `$TMPDIR` 로 복사한다

`guard_mutations.py:111-126` (`SANDBOX_IGNORE`).

**측정한 증거.** `SANDBOX_IGNORE` 에 `.env` / `data` / `*.db` 가 없다. 실제 개발
체크아웃에는 `/…/realtime-en2ko-captions/.env` (458 B — CLAUDE.md 기준
`OPENAI_KEY` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` 를 담는다) 와
`data/` 480 KB (`app.db`, `app.db.bak.20260608`, `data/branding/{jMWPEC_YjOU,
e2estage1}`) 가 존재한다. 둘 다 `.gitignore:2,5-6` 에 등재된 비공개 자산이다.
`shutil.copytree` 는 이들을 그대로 복사한다. **F-2 와 결합하면** SIGTERM 으로
죽은 실행마다 그 사본이 `$TMPDIR` 에 남는다.

완화 요인: `tempfile.mkdtemp` 는 0700 이고 macOS `$TMPDIR` 는 사용자 전용이며
CI 체크아웃에는 두 경로 모두 없다. 실행 가능한 공격 경로가 없으므로 **Medium**
(High 아님).

**최소 수정** — 목록에 4개 추가:

```python
    ".env",
    ".env.*",
    "data",
    "*.db",
```

리뷰 워크트리에는 `data/` 가 아예 없는 상태에서 **20/20 killed** 가 나오므로,
제외해도 카탈로그 실행에 영향이 없음을 실측으로 확인했다.

### S-2 (Medium, 미수정) — `mutation` job 에 `timeout-minutes` 가 없다 (AC 의 5분 상한이 강제되지 않음)

`.github/workflows/ci.yml:81-109`.

**측정한 증거.** 러너는 `len(selected)` 회만 돈다 — 뮤턴트 자동 생성도, 재시도도,
`while` 루프도 없다(코드 확인). 그러나 바인딩된 노드 집합이 20종이므로 최악의
경우 baseline 20회 + 뮤턴트 20회 = **40 × `PYTEST_TIMEOUT_SECONDS = 180`
(`guard_mutations.py:129`) = 7200 s ≈ 2시간**이다. 실측은 8.9~9.7 s.
AC 는 "job 총 소요가 5분 이내" 를 요구하는데 이를 강제하는 장치가 없고
GitHub 기본 job 타임아웃은 360분이라 사실상 무방비다.

**최소 수정** — `mutation:` 블록에 1줄:

```yaml
  mutation:
    name: Guard mutation check
    runs-on: ubuntu-latest
    needs: test
    timeout-minutes: 10
```

(기존 `lint`/`test`/`e2e` 도 `timeout-minutes` 가 없다 — 이 PR 범위 밖, 후속으로.)

### 취약점 없음으로 확인한 항목 (blind-spot scan)

- **명령 주입**: `subprocess.run` 은 리스트 인자 + `shell=False`. 노드 ID 는
  `ast` 가 판독한 파일 경로·식별자에서만 만들어지고 카탈로그 문자열이 관여하지
  않는다.
- **역직렬화**: `tomllib`(코드 실행 없음). 카탈로그의 `find`/`replace` 는 샌드박스
  파일에 문자열로 쓰일 뿐 `eval`/`exec` 되지 않는다.
- **의존성**: 신규 의존성 0건 — `pyproject.toml`·`uv.lock` 무변경, `tomllib` 은
  3.11 stdlib.
- **허용 목록 상향 불가**: `ALLOWED_FILES` 는 러너에 하드코딩(`:101-108`)이고
  카탈로그는 이를 넓힐 수 없다 — 프로브 2 로 실증.
- **경로 이식성**: `Path(__file__).resolve().parent` 기준이라 체크아웃 위치와
  무관하다. `pyproject.toml` 에 `[build-system]` 이 없어 `uv sync` 가 프로젝트를
  editable 로 설치하지 않고(`.venv/…/site-packages` 에 `_virtualenv.pth` /
  `a1_coverage.pth` 뿐), `tests/__init__.py` 가 있어 pytest 의 `prepend` 모드가
  샌드박스 루트를 `sys.path[0]` 에 넣는다 — CI 에서도 샌드박스가 이긴다.
- **XSS / 인가 / 하드코딩된 비밀**: 해당 없음(개발 도구, 사용자 대면 출력 없음,
  하드코딩된 비밀 0건).

## 사양 이탈 3건에 대한 판정

1. **배치 (`scripts/` → 저장소 루트) — 정당.** 직접 확인: `scripts` 는
   `.claude-kit/scripts`(submodule) 를 가리키는 심볼릭 링크이고
   `git ls-files scripts` 가 0건이며 리뷰 워크트리에 부재하다.
   `actions/checkout@v4` 는 기본이 `submodules: false` 이므로 CI 체크아웃에도
   없다. 루트 배치가 유일하게 동작하는 선택이고 docstring `:14-20` 에 근거가
   적혀 있다. CI 의 `uv run python guard_mutations.py` 는 추적된 루트 파일을
   가리키므로 클린 체크아웃에서 동작한다.
2. **카탈로그에 `tests` 키 없음 (docstring 파생) — 정당, 단서 1개.**
   AC 문구("카탈로그에 적힌 테스트 노드만 실행")의 *글자*는 만족하지 않지만
   *의도*(전체 스위트가 아니라 지정 노드만)는 만족한다. 파생이 나은 이유는
   RL-001 이고, `test_every_guard_is_owned_and_no_annotation_is_orphaned` 가
   양방향 대응을 빠른 스위트에서 정적으로 고정한다. 잃는 것은 "가드↔테스트
   대응표를 파일 하나로 diff 할 수 있는 성질" 인데, 러너 출력 자체가 그 표이고
   `Guard:` 를 약한 테스트로 옮기면 `SURVIVED` 로 드러난다(프로브 4 로 실증).
   순이익이다.
3. **샌드박스 사본 (제자리 수정 + `try/finally` 복구 대신) — 정당, 더 강함.**
   AC "실행 전과 바이트 단위로 동일" 을 *구조적으로* 만족한다 — 원본에 애초에
   쓰지 않는다. 정상·SIGINT·SIGTERM·검증 오류 전 경로에서 `git diff --quiet` = 0
   을 실측했다. 비용은 1회 copytree(측정 2.6 MB). 단, 이 선택이 F-2·S-1 이라는
   **새 실패 모드**(임시 디렉터리 잔존, 비밀 사본)를 만들었으므로 두 건을 함께
   고쳐야 이 이탈이 온전히 우월해진다.

## Over-Engineering

- `guard_mutations.py:160,260`: **delete** `Mutation.note` (읽는 곳 0건, 카탈로그
  사용 0건) → 필드와 `note=entry.get(...)` 제거. −3줄
- `guard_mutations.py:589-594`: **yagni** `--timeout` CLI 플래그 (CI·테스트 어디서도
  쓰지 않음) → `PYTEST_TIMEOUT_SECONDS` 상수만 유지. `run_catalog`/`_run_pytest`
  의 `timeout` **인자는 유한성 안전 장치이므로 남긴다**. −7줄
- `--repo-root` / `--tests-root` 는 **정당**하다 — `main()` 의 종료 코드 계약을
  가짜 미니 저장소로 종단 검증하는 유일한 수단이고 합계 4줄이다. 컷하지 않는다.
- 618줄이라는 총량은 과하지 않다: 실행 문(coverage `Stmts`) 기준 **249**이고
  나머지는 이슈가 명시적으로 요구한 설계 기록(이탈 3건의 근거)과 구획 주석이다.

**net removable ≈ 10줄** (618줄 중). 나머지는 lean.

## AC 대조

- 접근 방식 결정 — **충족**. `docs/architecture.md:380` 에 실측 1행
  (선언형 20 kill / 9.6 s vs `mutmut` 3.4.0 4 s 만에 실패·처리 0건, 탈락 사유
  포함). 이 파일은 프로젝트 관례상 git 미추적(추적 문서는 5건뿐)이라 PR diff 에
  없는 것이 정상이다.
- 러너 계약 4건 — **충족** (프로브 1·3, 출력 형식, `git diff --quiet` = 0).
- retro-apply 3건 — **충족**. `branding_assets` 항목 15건 전건 kill, 생존 0.
- 명명 규약 2건 — **충족** (프로브 1).
- 비용 상한 2건 — **부분 충족**. 기존 job 커맨드 무변경(+39/−0)·카탈로그 노드만
  실행·실측 9 s 는 충족이나 **5분 상한을 강제하는 장치가 없다**(S-2).

## Follow-ups (이 PR 이 아니라 별도 이슈)

1. **RL-026 이 주인 없이 남는다.** RL-026 의 Recommended action 은 "ISSUE-43 의
   카탈로그가 작성될 때 **마크업 속성 삭제**를 뮤테이션 클래스로 포함하라" 며
   ISSUE-43 을 명시적으로 지목하는데, ISSUE-43 의 Scope 는 JS/HTML 을 Out 으로
   못 박았다(파이썬 소스만). 두 문서가 서로를 가리키며 아무도 소유하지 않는다.
   → `components/*.html` 대상 마크업 뮤테이션 카탈로그를 별도 이슈로 연다.
2. `lint` / `test` / `e2e` job 에도 `timeout-minutes` 부재 (S-2 참고).
3. `guard_mutations.py` 가 `--cov=.` 분모에 들어가 있다
   (`[tool.coverage.run] omit` 미등재, 249 stmts / 95%). 앱 커버리지에 도구
   스크립트가 섞인다 — omit 추가 여부를 결정할 것.
4. 허용 목록 확대 후보 (`sse_broadcast.py` 의 라우트 가드, `stage_config` 검증).

## Confidence

**High.** 코드·PR 본문·리뷰 지시문의 주장을 하나도 그대로 받지 않고 전부 실측했다
(프로브 7종 + 400회 교차 매트릭스 + 시그널 2종 + 동시 실행 + 전체 스위트/린트).
유일하게 직접 실행하지 못한 것은 **실제 GitHub 러너에서의 CI job** 이다 —
editable 설치 부재와 `tests/__init__.py` 로 인한 `sys.path` 우선순위를 근거로
안전하다고 판단했고 그 근거는 Security Findings 마지막 항목에 남겼다. 이 한
가지만 Medium 확신이다.

## Verdict

**조건부 승인.** 하네스는 실제로 문다 — ISSUE-38 의 원래 결함을 재현하면
`SURVIVED` 로 잡아내고(프로브 4b), 20개 가드 중 19개가 자기 바인딩에만 죽는다.
프로덕션 코드 변경 0건, 기존 `lint`/`test`/`e2e` 커맨드 무변경(+39/−0),
스위트·린트 전부 초록.
**머지 전 F-1 은 반드시 고쳐야 한다** — 도구의 선언된 계약이 이 저장소에서
거짓이고, 그 거짓이 곧 이 이슈가 없애려는 실패 모드 그 자체다.
S-2 는 1줄, F-2·S-1 은 각각 3~4줄이므로 함께 처리하기를 권한다.

## Resolution — 팀리드 반영 (커밋 `7f71c95`, 반영 후 실측)

리뷰 지적 전량을 이 PR 안에서 처리했다. 프로덕션 코드는 여전히 0건 변경이다.

| ID | 심각도 | 처리 | 반영 후 실측 |
|---|---|---|---|
| F-1 | High | `compile()` 사전 검증(레이어 a) + `_verdict` 의 import/문법 트레이스백 차단(레이어 b) + 거짓 계약을 서술하던 docstring 교정(레이어 c). 회귀 테스트 2건 추가 | **팀리드가 독립 재측정**: 괄호 불균형 `replace` → `ERROR — replace 결과가 문법적으로 유효하지 않습니다 (branding_assets.py:318): invalid syntax`, exit **1** (수정 전: `killed`, exit 0). 문법은 유효하나 import 불가한 뮤턴트 → `ERROR — 뮤턴트를 import 하지 못했습니다 … ModuleNotFoundError`, exit **1**. 대조군(정상 뮤테이션) → `killed by …`, exit **0** |
| F-2 | Medium | `main()` 첫 문장에 SIGTERM 핸들러 설치(샌드박스가 생기기 전에 무장), 격리 docstring 교정 — 커버하는 시그널과 커버 못 하는 SIGKILL 을 명시 | `kill -TERM` 후 `$TMPDIR/guard-mutations-*` 잔존 0건, 고아 pytest 자식 0건 |
| S-1 | Medium | `SANDBOX_IGNORE` 에 `.env`, `.env.*`, `data`, `*.db` 추가 | 20/20 유지 — 아무것도 이들에 의존하지 않는다 |
| S-2 | Medium | `mutation` job 에 `timeout-minutes: 10`. `lint`/`test`/`e2e` 는 의도적으로 미변경 | `git diff origin/main -- .github/workflows/ci.yml` 삭제 라인 0 |
| F-3 | Low | 요약에 분모 출력 (`1/20 guards`) | — |
| F-4 | Low | 죽은 `Mutation.note` 삭제 | — |
| F-5 | Low | skip 사유의 미측정 수치("1~2분")를 실측(~9초)과 실제 사유(중첩 pytest 서브프로세스 약 40회, CI `mutation` job 이 권위 게이트)로 교체 | 실측 8.7~9.4초 |
| 미니멀리티 | — | 미사용 `--timeout` CLI 플래그 삭제. `run_catalog`/`_run_pytest` 의 `timeout` 인자는 **유지**(경계성 안전 속성) | 순 −10줄 |

반영 후 전체: **1265 passed / 1 skipped / 114 deselected / 94.44%**, `ruff --config pyproject.toml` · `black --check` 초록,
러너 **20/20 killed, 0 survived, 0 errors — 9.4s**, `git diff --quiet` = 0.

리뷰어가 별도 이슈로 넘기라고 한 4건(RL-026 마크업 속성 뮤테이션 클래스 미소유,
`lint`/`test`/`e2e` 의 `timeout-minutes` 부재, `guard_mutations.py` 의 커버리지 분모 포함 여부,
허용 목록 확대 후보)은 이 PR 에서 건드리지 않고 `docs/sprint_state.md` 의 Discovered Issues 에 기록했다.

---

# PR #147 — ISSUE-49 오퍼레이터 웰컴 화면 WCAG AA 보정 (code review)

**Reviewer**: Claude Opus 5 (automated, independent REVIEW phase — code half)
**Date**: 2026-08-31
**Worktree**: `/Users/pillip/project/practice/realtime-en2ko-captions/.worktrees/review-ISSUE-49-code` (detached HEAD `247860c`)
**Base**: `origin/main` = `ee409e7` · **Diff**: 3 files, +422 −8
**동시 실행 간섭**: 없음. 리뷰 시작·종료 시점 `git status --porcelain` 빈 출력, 3개 대상 파일 mtime 모두 워크트리 생성 시각(`02:22:30`)에서 이동 없음. 다른 워크트리는 읽지도 쓰지도 않았다.

**Verdict**: **APPROVE-WITH-NITS.** Critical/High **0건**. AC 7개 전부 충족을 독립 실측으로 확인했고, 프로덕션 변경은 색 리터럴 4개 + 규칙 1개 삭제뿐이다. 이 PR 안에서 고친 것은 **없다**(고칠 Critical/High 가 없었다). Medium 2 / Low 3 은 전부 **테스트 강도**와 문서에 대한 것이며 후속 이슈로 넘긴다.

## 게이트 실측 (이 워크트리에서 직접 실행)

| 게이트 | 결과 |
|---|---|
| `uv run pytest -q` | **1340 passed, 1 skipped, 143 deselected, 94.53%** — 107.08s. 구현자 보고와 **정확히 일치** |
| `uv run ruff check .` | `All checks passed!` |
| `uv run black --check .` | `67 files would be left unchanged` |
| `env -u NODE_OPTIONS uv run pytest -q -m e2e tests/e2e/test_operator_welcome_contrast_e2e.py` | **10 passed** (8.91s). 전체화면 진입 양성 대조군 포함 통과 |
| `env -u NODE_OPTIONS uv run pytest tests/e2e -m e2e --no-cov -q` (전체) | 2 failed, 141 passed |

- `1 skipped` 는 ISSUE-43 이 남긴 `test_guard_mutations.py:609` 기존 skip 이다. 이 PR 과 무관.
- 스위트 실행 시간이 **107s** 이므로 `verify_checkpoint.py` 의 60s 타임아웃은 exit 124(타임아웃)를 낼 수밖에 없다 — 실패가 아니다. 위 수치가 실제 조건이다.
- **전체 e2e 의 2건 실패는 이 PR 의 회귀가 아니다.** 같은 워크트리에서 base `ee409e7` 를 체크아웃해 전체 e2e 를 다시 돌린 결과 **5 failed / 111 passed / 17 errors** 로 오히려 더 나빴고, 실패 파일은 양쪽 모두 `tests/e2e/test_admin_stage_config_e2e.py`(ISSUE-39 계열, Streamlit 관리 UI 타임아웃) 하나였다. 이 파일만 단독 실행하면 **6 failed** 가 되어 실행 순서·서버 준비 상태에 따라 결과가 흔들리는 플레이키다. 측정 후 워크트리는 `247860c` 로 복귀시켰고 `git status` 는 비어 있다.

## 대비 수치 독립 재계산

PR 본문/지시문의 표를 그대로 받지 않고 `tests/wcag.py::_contrast_ratio` 로 전부 다시 계산했다. **7/7 전부 주장과 일치**한다.

| 규칙 | 값 | `#0b0b0c` | `#000` |
|---|---|---|---|
| `.welcome-state h1, .welcome-title` | `#ffffff` | **19.67** | **21.00** |
| `.welcome-state` (컨테이너) | `#c8c8d2` | **11.85** | **12.65** |
| `.welcome-state p, .welcome-desc` | `#c8c8d2` | **11.85** | **12.65** |
| `.welcome-room` | `#a8a8b3` | **8.35** | **8.92** |
| `.welcome-state .hint` | `#a8a8b3` | **8.35** | **8.92** |
| `.welcome-rules` | `#a8a8b3` | **8.35** | **8.92** |
| `.stage-launch-hint` (ISSUE-47, 재사용·미편집) | `#a8a8b3` | 8.35 | 8.92 |

Before 값도 재현했다: 알파 `0.4` → 3.80 / 3.66, 알파 `0.45` → **4.52 / 4.43**(전체화면에서만 미달), 알파 `0.5` → 5.34 / 5.32, 알파 `0.7` → 9.70 / 9.90. 즉 이슈가 기술한 "알파는 backdrop 이 어두워질수록 대비가 내려간다" 는 성질과 `.welcome-room` 이 전체화면에서만 떨어졌다는 진단이 모두 사실이다.

## AC 대조 (7/7)

| AC | 판정 | 근거 |
|---|---|---|
| 1. 모든 규칙 × 두 backdrop ≥ 4.5, large-text 완화 금지 | ✅ | 위 표(최솟값 8.35) + 정적 12 케이스 + **M7 실측**: `.welcome-title` 을 `#6a6a70`(3.66 / 3.91 — 둘 다 large-text 3:1 은 통과)로 바꾸면 **KILLED**. 완화 경로가 실제로 닫혀 있다 |
| 2. `.hint` / `.welcome-rules` 가 불투명 hex 이고 양쪽 통과 | ✅ | `#a8a8b3` 8.35 / 8.92 |
| 3. `.welcome-room` 이 `#000` 에서 ≥ 4.5 | ✅ | 8.92 (이전 4.43) |
| 4. 웰컴 블록 텍스트 `color` 에 `rgba(255,255,255,α)` 0건, 전부 `#rrggbb` | ✅ | 6개 텍스트 규칙 전부 `#rrggbb`. 남은 `rgba(255,255,255,0.5)` 는 `.welcome-state .icon`(비텍스트, SC 1.4.11, 명시적 Scope Out)뿐이며 그 값도 5.34 / 5.32 로 3:1 을 넘긴다 |
| 5. 밝기 서열 제목 > 본문 > 보조 유지 | ✅ | 상대휘도 1.0000 > 0.5824 > 0.3958, 강한 내림차순. 테스트가 수치로 고정 |
| 6. `.hint` 되돌린 변이가 **`3.80:1` 을 보고하며** 실패 | ✅ | **직접 실측**: `AssertionError: .welcome-state .hint contrast is 3.80:1 against #0b0b0c (declared colour (255, 255, 255, 0.4)) — WCAG AA 1.4.3 needs 4.5:1` (그리고 `#000000` 에서 `3.66:1`) |
| 7. backdrop 상수 ↔ 파일의 실제 `background` 선언 일치 | ✅ | 상수를 새로 만들지 않고 ISSUE-47 의 `test_body_backdrop_is_the_expected_opaque_colour` / `test_fullscreen_backdrop_is_the_expected_opaque_colour` 를 재사용. e2e 는 한 걸음 더 나아가 브라우저가 **칠한** 배경을 읽어 상수와 대조하고 불투명 여부까지 단언 |

`.welcome-room` 되돌린 변이도 AC 대로 동작한다 — **`4.43:1 against #000000`** 만 실패하고 `#0b0b0c` 케이스는 통과한다. 두 backdrop parametrize 가 장식이 아니라 이 한 줄을 위해 존재한다는 것이 실측으로 증명된다.

## 지시받은 6개 판단 항목

### 1. 구조 가드 (c) 는 정말 "구조적" 인가 — **부분적으로만 그렇다**

`test_fullscreen_rules_do_not_re_declare_welcome_text_colour` 는 `:fullscreen` / `:-webkit-full-screen` 뒤의 선택자 문자열에 **리터럴 `"welcome"` 이 들어있는 규칙만** 검사한다(`tests/test_webrtc_stage_launch.py:278`). 세 가드를 각각 깨뜨려 본 결과:

- **(a) backdrop 상수** — 기존 두 테스트가 파일의 실제 `background` 선언과 대조하므로 유효. 게다가 e2e 가 브라우저 실측으로 이중 확인한다.
- **(b) 알파-화이트 0건** — 유효하며, 예상과 달리 **중복되지 않는다**. `.welcome-rules { color: #a8a8b3; color: rgba(255,255,255,0.4); }`(CSS 는 뒤가 이김)를 넣으면 **이 가드만** 죽는다 — 대비 테스트도 hex 테스트도 `re.search` 로 **첫 번째** 선언만 읽기 때문이다. 처음엔 hex 가드에 흡수된 중복 테스트로 판단했으나, 뮤턴트로 반증했다.
- **(c) fullscreen 재선언 금지** — **오늘의 선택자만 열거한다.** 아래 M4 참조.

정면으로 깨보면:

| 뮤턴트 | 정적 | e2e |
|---|---|---|
| M3 `#viewer:fullscreen .welcome-state { color: rgba(...,0.8) }` 복원 | **KILLED** (규칙을 인용해 보고) | KILLED |
| **M4 `#viewer:fullscreen .hint { color: rgba(...,0.4) }` (새 선택자)** | **SURVIVED** | KILLED (2건) |
| **M5 `.welcome-note { color: rgba(...,0.4) }` (새 웰컴 규칙)** | **SURVIVED** | **SURVIVED** |
| **M6 `#viewer:fullscreen .welcome-state { opacity: 0.35 }`** | **SURVIVED** | **SURVIVED** |
| **M14 중복 후행 `color: #55555c` (2.66:1)** | **SURVIVED** | KILLED (`2.66:1` 보고) |

`.hint` 는 웰컴 화면의 노드인데 선택자에 `welcome` 이 없다. 그래서 **웰컴 텍스트에 대한 fullscreen 색 분기가 재도입되어도 정적 가드는 조용하다.** 다만 e2e 가 잡고, CI 의 `e2e` job 이 `pytest tests/e2e -m e2e` 를 정식 게이트로 돌리므로(`.github/workflows/ci.yml:158`) 그물은 남아 있다. → **F-1 (Medium)**

### 2. `tests/wcag.py` 재사용 여부 — **깨끗하다 (RL-001 준수)**

- 정적: `from tests.wcag import _contrast_ratio, _hex_rgb, _relative_luminance, _rule_block, _rule_hex, _rule_rgba` — 계산기 신규 작성 0건.
- e2e: `from tests.wcag import _contrast_ratio, _hex_rgb` + `from tests.test_webrtc_stage_launch import _FULLSCREEN_BACKDROP, _PAGE_BACKDROP`. 상수는 **재정의가 아니라 import** 다. `_PAGE_BACKDROP` / `_FULLSCREEN_BACKDROP` / `_ALL_BACKDROPS` 는 `git show ee409e7:tests/test_webrtc_stage_launch.py` 기준 이미 존재하던 상수(36/42/43행)이고 이 diff 는 손대지 않았다.
- **`tests/wcag.py` 는 이 diff 에서 0바이트 변경**(`git diff ee409e7...247860c -- tests/wcag.py` 빈 출력). ISSUE-53 과 충돌 없음.
- e2e 의 `_parse_css_colour` 는 **세 번째 계산기가 아니다** — 브라우저가 돌려주는 *computed* `rgb()/rgba()` 문자열 파서이고, `tests/wcag.py` 에는 대응 함수가 없다(`_rule_rgba` 는 CSS 소스 + 선택자를 받는다). 산술은 전부 `tests/wcag.py` 에서 온다.

### 3. M8(오라클 완화) 생존 판정 — **수용 가능. 단, 구현자가 댄 이유는 틀렸다**

내 실측으로도 재현된다: 정적 AA 단언의 `>= 4.5` 를 `>= 3.0` 으로 바꾸면 **SURVIVED**.

- 구현자 논거 ① "현재 값이 전부 8.35 이상이라 무해하다" — **값 의존적이라 유통기한이 있다.** 누군가 보조 텍스트를 어둡게 하는 순간 이 논거는 소멸한다.
- 구현자 논거 ② "M8+M1 복합은 두 번 죽는다" — 사실이다(내 실측에서도 KILLED). 그러나 죽인 것은 **불투명 hex 가드·알파-화이트 가드·서열 가드**이지 **AA 오라클 자신이 아니다.** 즉 대비 오라클은 여전히 침묵한다.
- **올바른 이유는 구조적이다**: *테스트 자신의 임계값을 바꾸는 뮤턴트는 어떤 테스트로도 죽일 수 없다.* 테스트는 다른 테스트의 오라클을 관측하지 않는다. M8 은 운이 좋아 무해한 것이 아니라 **정의상 equivalent mutant** 다. 남는 위험은 "누가 4.5 를 낮춘다" 이고 그건 테스트 커버리지 문제가 아니라 **코드 리뷰가 잡을 문제**다.
- 실질적 완충도 이미 있다: e2e 파일이 **독립적으로** `>= 4.5` 를 갖고 있어 한쪽만 낮춰서는 그물이 다 뚫리지 않는다.
- 그래도 잡고 싶다면 값싼 장치가 있다 — 같은 파일에 메타 가드(AA 단언 라인에 리터럴 `4.5` 가 존재하는지)를 두면 된다. 이 저장소는 이미 `guard_mutations.py`(ISSUE-43)로 그 관용구를 소유하고 있다. **차단 사유는 아니다.**

### 4. large-text 3:1 완화 — **어디에도 적용되지 않는다 (실측 확인)**

`.welcome-title` 을 `#6a6a70` 으로 바꾸면 `#0b0b0c` 3.66:1 / `#000` 3.91:1 로 **large-text 기준(3:1)은 통과**하지만 정적 3건 + e2e 2건이 죽는다. 전부 4.5 로 재고 있음이 값으로 증명된다.

다만 **근거 문구는 부정확하다** → **F-5 (Low)**. `clamp(26px, 3.4vw, 34px)` 의 하한은 26px 이고 WCAG large text 는 24px(bold 18.66px)이므로 이 규칙은 **어떤 뷰포트에서도 large text 로 분류된다**. "하한이 임계 아래로 내려갈 수 있다" 는 이 값에 대해서는 성립하지 않는다. 결론(4.5 로 잰다)은 더 엄격해서 옳고, 틀린 것은 이유뿐이다. 다음 사람이 복사하는 것은 이유다.

### 5. `#viewer:fullscreen .welcome-state` 삭제 — **안전하다**

- 웰컴 마크업 두 벌(정적 768–787행, `clearViewer()` 1632–1653행)을 모두 훑었다. `.welcome-state` 의 직계 텍스트 노드는 없고 자식은 전부 자기 `color` 를 갖는다: `.icon`, `h1.welcome-title`, `.welcome-room`, `p.welcome-desc`, `.hint`, `.welcome-rules`, `.qr-code-caption`.
- `.hint` 안의 **맨 `<span>`** 은 `.welcome-state` 가 아니라 `.hint`(`#a8a8b3`)에서 상속받는다. e2e 가 `.welcome-state .hint span` 을 목록에 넣어 **브라우저 실측**으로 8.35 / 8.92 를 확인한다. 이 노드가 오퍼레이터의 유일한 행동 지시문이므로 목록에 있는 것이 정확히 옳다.
- 동적 재작성 경로(`updateWelcomeTranslationRules()`, 1043–1088행)도 `.welcome-title` / `.welcome-room` / `.welcome-desc` / `.welcome-rules` 만 건드린다 — 전부 자기 색을 가진 노드다. 컨테이너 색에 기대는 텍스트는 없다.
- 참고로 `#viewer:-webkit-full-screen .welcome-state` 색 분기는 **원래부터 없었다**(비대칭이 이 PR 이전부터 존재). 삭제로 인해 Safari 쪽 동작이 달라지는 것은 없다.

### 6. Scope 규율 — **지켜졌다**

- 변경 파일 3개, `components/viewer.html` / `components/stage.html` **0바이트 변경**.
- `components/webrtc.html` 의 변경은 색 리터럴 4개(`.welcome-state`, `.welcome-room`, `.welcome-state p/.welcome-desc`, `.welcome-state .hint`, `.welcome-rules`) + fullscreen 규칙 1개 삭제 + 주석. 레이아웃·문구·타이포 변경 0건.
- **`.qr-code-caption` 은 손대지 않았다.** 내 실측으로 `rgba(0,0,0,0.5)` on `#ffffff` = **3.95:1** — 실제 AA 미달이 맞고, 이슈가 Scope Out 으로 못 박았으며 PR 본문이 "확인된 별건 결함" 으로 명시했다. 여기서 고치지 않은 것이 **옳다**. → **F-6** 으로 후속 이슈 제안.

## 회귀 확인 (지시된 4건)

| 항목 | 결과 |
|---|---|
| **ISSUE-47** `aria-describedby="stage-launch-hint"` | **온전**. `components/webrtc.html:738` 에 그대로 있고 `:741` 의 `id="stage-launch-hint"` 와 짝이 맞는다. `.stage-launch-hint` 의 `#a8a8b3` 은 diff 에 나타나지 않는다 — **재사용이지 편집이 아니다** |
| **ISSUE-48** `{{BOOTSTRAP_JSON}}` 이스케이프 | **무접촉**. `git diff ee409e7...247860c -- components/webrtc.html` 안에 `BOOTSTRAP_JSON` / `script_escape` / `PLACEHOLDER_RE` / `.replace(` 매치 **0건**. `operator_ui.py:212-213` 의 `json_for_script` + 단일 패스 strict `PLACEHOLDER_RE.sub` 경로 그대로. 오히려 새 e2e fixture 가 `operator_ui.render_component_html` 을 **프로덕션 함수 그대로 호출**해 그 경로를 한 번 더 지난다(RL-024 준수) |
| **RL-010** 접근 가능한 이름 / 포커스 표시 | **무영향**. 이 diff 는 마크업·인터랙티브 요소를 전혀 바꾸지 않는다 |
| `viewer.html` / `stage.html` | **무접촉** (0바이트) |

## Code Review

### F-1 (Medium) — fullscreen 구조 가드가 오늘의 선택자만 열거한다
`tests/test_webrtc_stage_launch.py:277-283`

**무엇이 문제인가.** `if "welcome" not in selector: continue` 이므로 선택자 문자열에 `welcome` 이 없는 웰컴 화면 노드(`.hint`, `.icon`, `h1`, `p`)에 대한 fullscreen `color` 재선언은 검사 대상에서 **빠진다**.

**왜 중요한가.** 이 가드의 docstring 은 RL-018 Prevention 을 인용하며 "주석이 아니라 검사된 사실로 둔다" 고 선언한다. 그런데 backdrop 분기 재도입의 절반은 검사되지 않는다. 실측: `#viewer:fullscreen .hint { color: rgba(255,255,255,0.4) }` → **정적 SURVIVED**.

**완화 요인.** e2e 가 죽인다(2건). e2e 는 CI 정식 job 이다. 그래서 Medium 이지 High 가 아니다.

**구체적 수정안(후속).** 리터럴 `"welcome"` 대신 `_WELCOME_TEXT_RULES` 에서 클래스 토큰 집합을 파생시켜 매칭한다:
```python
_WELCOME_TOKENS = frozenset(
    t for rule in _WELCOME_TEXT_RULES for t in re.findall(r"[.#][\w-]+|\bh1\b|\bp\b", rule)
)
...
if not any(tok in selector for tok in _WELCOME_TOKENS):
    continue
```
목록이 하나 늘면 가드도 같이 자란다.

### F-2 (Medium) — 새 웰컴 규칙은 정적·e2e **어느 쪽도** 감사하지 않는다
`tests/test_webrtc_stage_launch.py:154-161`, `tests/e2e/test_operator_welcome_contrast_e2e.py:56-64`

**무엇이 문제인가.** `_WELCOME_TEXT_RULES` 와 `_WELCOME_TEXT_NODES` 는 손으로 유지하는 목록이고, "파일이 새 웰컴 규칙을 갖게 되었는가" 를 묻는 검사가 없다. 실측: `.welcome-note { color: rgba(255,255,255,0.4) }` 를 추가하면 **정적 SURVIVED, e2e 도 SURVIVED**.

**왜 중요한가.** AC4 의 문구는 "웰컴 블록 CSS 의 텍스트 `color` 선언을 훑으면" 이라는 **전수 조사**로 읽힌다. 구현은 고정 목록이다. 그리고 RL-018 의 재발 형태가 정확히 "새로 추가된 저대비 텍스트" 다 — 가드가 막으려던 바로 그 사건이 가드의 사각이다.

**이건 저장소 기존 패턴에서의 이탈이다.** 같은 RL-018 계보의 직전 이슈(ISSUE-45)가 `tests/test_viewer_page.py:1145` `test_opaque_background_surfaces_are_the_enumerated_set` 에서 이미 **파생형**을 출하했다 — 스타일시트를 순회해 발견 집합을 만들고 `assert painted == _KNOWN_BACKGROUNDS` 로 목록과 대조해, **새 표면이 생기면 가드가 실패하며 재감사를 강제한다.** ISSUE-49 는 그 형태를 따르지 않았다.

**구체적 수정안(후속).** 선택자가 `\.welcome[\w-]*` 또는 `.welcome-state <자손>` 에 매치되는 모든 규칙을 스타일시트에서 수집해 (a) 발견 집합 == `_WELCOME_TEXT_RULES` 를 단언하고 (b) 그 집합으로 AA parametrize 를 구동한다. 순회 헬퍼(`_rules` / `_declarations` / `_style_sheet`)는 `tests/test_viewer_page.py` 에 이미 있다 — 이상적으로는 `tests/wcag.py` 로 **추가만 하는(additive)** 승격이 맞지만, ISSUE-53 이 같은 파일에서 동시 진행 중이므로 **이 PR 이 아니라 후속에서** 하는 편이 안전하다.

### F-3 (Low) — 정적·e2e 둘 다 `color` 만 본다: `opacity` 로 되살린 분기는 보이지 않는다
`tests/test_webrtc_stage_launch.py:280`, `tests/e2e/test_operator_welcome_contrast_e2e.py:96`

실측: `#viewer:fullscreen .welcome-state { opacity: 0.35 }` → **양쪽 SURVIVED**. 정적 가드의 정규식은 `color:` 만 찾고, e2e 의 `getComputedStyle(...).color` 는 조상 `opacity` 를 합성하지 않는다. `opacity` / `filter` 는 `color` 보다 가능성 낮은 벡터이고 이 diff 가 도입하지도 않았으므로 Low. 후속에서 fullscreen 스캐너에 `opacity|filter` 를 함께 금지하면 1줄이다.

### F-4 (Low) — 정적 파서는 규칙의 **첫** `color` 를 읽고 CSS 는 **마지막** 을 적용한다
`tests/test_webrtc_stage_launch.py:187-190`, `:231`

실측: `.welcome-rules { color: #a8a8b3; color: #55555c; }`(**2.66:1**) → **정적 SURVIVED**, e2e 는 KILLED 하며 `2.66:1` 을 정확히 보고한다. 알파 버전(`color: rgba(255,255,255,0.4)` 후행)은 알파-화이트 가드가 잡지만 **hex 후행은 아무도 못 잡는다**. 이 한 뮤턴트가 e2e 파일의 존재 가치를 단독으로 증명한다 — 소스 파싱은 캐스케이드를 흉내 낼 뿐이고 브라우저만 사실을 안다. 수정은 `re.search` → `re.findall(...)[-1]`.

### F-5 (Low) — `clamp()` 근거 문구가 이 값에 대해 사실이 아니다
`tests/test_webrtc_stage_launch.py:208-214`, PR 본문 동일 문장

`clamp(26px, 3.4vw, 34px)` 의 하한은 26px 로 large-text 임계(24px)보다 항상 크다. "하한이 임계 아래로 내려갈 수 있다" 는 ISSUE-45 의 관찰을 이 값에 잘못 전용한 것이다. 결정(전부 4.5 로 측정)은 더 엄격해 옳으므로 **행동 변경 없음**, 문구만 정정 권고: "완화가 성립하더라도 쓰지 않는다 — 19.67:1 이라 완화가 필요 없다".

### F-6 (Medium, 이 PR 밖) — `.qr-code-caption` 3.95:1 실제 AA 미달
`components/webrtc.html:.qr-code-caption`

`rgba(0,0,0,0.5)` on `.qr-code-container { background: #ffffff }` = **3.95:1** (직접 실측). `.welcome-state` 안에서 `BOOT.qr_data_url` 이 있을 때 실제로 렌더된다. **이 PR 에서 고치지 않은 것이 옳다** — 이슈가 Scope Out 으로 명시했고 backdrop 이 완전히 달라 같은 표에 넣으면 계산 자체가 틀린다. PR 본문도 명시했다. **후속 이슈 필수** (아래 참조). 심각도는 이 PR 에 대한 것이 아니라 제품에 대한 것이다.

### 잘한 점 (실측으로 확인한 것)

- **양성 대조군이 두 곳에 있다.** 정적 fullscreen 스캐너는 `.caption-container` 규칙을 먼저 찾아 "정규식이 깨져 0건이 된" 공허한 통과를 막고, e2e 는 `#viewer` 가 실제로 `:fullscreen` 에 매치되는지를 먼저 단언해 "일반 화면을 두 번 잰" 공허한 통과를 막는다. 후자는 헤드리스 Chromium 에서 실제로 통과한다(10 passed).
- **주석을 걷어낸 뒤 선언만 스캔한다.** 삭제한 규칙을 설명하려면 인용할 수밖에 없는데, 인용문에 가드가 걸리면 다음 사람은 가드가 아니라 설명을 지운다. 그 함정을 알고 피했다.
- **알파-화이트 가드는 중복이 아니다** (M13 로 반증). 중복 선언 케이스를 단독으로 잡는 유일한 정적 가드다.
- **실패 메시지가 전부 계산값을 말한다** (RL-004). `3.80:1` / `4.43:1` / `3.66:1` / `2.66:1` 을 전부 실제 실패 출력에서 확인했다.
- **e2e 가 프로덕션 렌더 함수(`operator_ui.render_component_html`)를 호출한다** — fixture 가 치환을 재구현하지 않는다 (RL-024).

## Security Findings

**Critical 0 / High 0 / Medium 0 / Low 0.**

diff 는 CSS 색 리터럴 4개, CSS 규칙 1개 삭제, 테스트 2개 파일이다. 각 카테고리를 명시적으로 재확인했다:

- **Injection / 템플릿 주입**: 프로덕션 치환 경로 무접촉(위 회귀 표). 새 e2e 는 오히려 프로덕션 `render_component_html` 을 그대로 지난다.
- **XSS**: `innerHTML` 사용처 변경 0건. `clearViewer()` 템플릿 리터럴은 이 diff 에서 바뀌지 않았다(색은 CSS 로만 바뀌어 두 벌 마크업을 한 번에 덮는다 — 인라인 `style` 색 주입 0건).
- **AuthN/AuthZ**: 접근 제어 코드 무접촉.
- **비밀정보**: 새 파일의 `_boot_payload()` 는 더미(`op1`, `room-42`)이고 자격 증명·토큰·URL 시크릿 없음. `openai_session: None`.
- **입력 검증 / 역직렬화**: 새 입력 경로 없음. `_parse_css_colour` 는 **브라우저가 생성한** computed style 문자열만 파싱하며 매치 실패 시 값을 담아 assert 한다(신뢰 경계 아님).
- **의존성**: 추가 0건. `uv.lock` / `pyproject.toml` 무변경.
- **설정 오류**: CI 워크플로 무변경. e2e 는 `tmp_path` 에 `file://` 로 쓰고 네트워크를 열지 않는다.

접근성은 보안이 아니라 품질로 분류했지만, 이 PR 의 본질이 **텍스트 가독성 확보**이므로 값은 위 표에 전부 남겼다.

## Over-Engineering (minimality axis)

- `tests/test_webrtc_stage_launch.py:238-252`: **처음에 `shrink`(알파-화이트 가드가 hex 가드에 흡수된 중복) 로 판정했다가 뮤턴트 M13 으로 반증했다** — 중복 후행 `color: rgba(...)` 를 잡는 유일한 정적 가드다. **철회한다.**
- `components/webrtc.html:435-451`: 17줄 주석 블록. 길지만 RL-018 이 "눈대중 금지" 를 요구하고 이슈가 실측값 기록을 명시적으로 요구했다. **유지.**
- `tests/e2e/.../test_operator_welcome_contrast_e2e.py:_parse_css_colour`: `tests/wcag.py` 로 승격하면 다음 브라우저 대비 테스트가 재사용할 수 있으나, ISSUE-53 이 그 파일에서 동시 진행 중이므로 **지금 옮기지 않는 것이 옳다**. 후속.

**Net removable lines: 0.** 이 diff 는 이 축에서 군살이 없다. 오히려 F-1/F-2 는 **줄을 더 써야** 하는 방향이다.

## 뮤테이션 실측 (10종, 전량 내가 직접 실행)

| # | 뮤턴트 | 정적 | e2e | 비고 |
|---|---|---|---|---|
| M1 | `.hint` → `rgba(255,255,255,0.4)` | KILLED (5) | KILLED (3) | `3.80:1` / `3.66:1` 보고 |
| M2 | `.welcome-room` → `rgba(255,255,255,0.45)` | KILLED (3) | KILLED (1) | `#000` 에서만 `4.43:1` 실패 |
| M3 | fullscreen 색 분기 복원(알파) | KILLED (1) | KILLED (1) | 구조 가드가 규칙을 인용 |
| M4 | `#viewer:fullscreen .hint{color:rgba .4}` | **SURVIVED** | KILLED (2) | → F-1 |
| M5 | `.welcome-note{color:rgba .4}` 신규 | **SURVIVED** | **SURVIVED** | → F-2 |
| M6 | `#viewer:fullscreen .welcome-state{opacity:.35}` | **SURVIVED** | **SURVIVED** | → F-3 |
| M7 | `.welcome-title` → `#6a6a70` (3.66/3.91) | KILLED (3) | KILLED (2) | large-text 완화 미적용 증명 |
| M8 | 오라클 `>= 4.5` → `>= 3.0` | **SURVIVED** | n/a | equivalent by construction |
| M13 | 중복 후행 `color: rgba(...,.4)` | KILLED (알파 가드 단독) | — | 알파 가드가 중복 아님을 증명 |
| M14 | 중복 후행 `color: #55555c` (2.66:1) | **SURVIVED** | KILLED | → F-4 |

구현자의 12종 배치는 전부 **오늘의 선택자**를 겨냥한다. 살아남은 4종(M4·M5·M6·M14)은 전부 **"새 것" 또는 "두 번째 선언"** 형태다 — 배치 설계가 자기 사각을 재생산했다.

## 리뷰 프로세스 사고 (기록)

첫 뮤테이션 배치의 뒤쪽 실행이 **오염되었고, 그것을 알아채고 전량 재측정했다.**

M8(오라클 `4.5` → `3.0`)을 위해 `tests/test_webrtc_stage_launch.py` 를 수정했다가 되돌렸는데, `4.5` 와 `3.0` 은 **바이트 수가 같아** 파일 크기가 변하지 않았고, 복원이 같은 초 안에 일어나 mtime 도 pyc 헤더의 초 단위 값과 일치했다. Python 의 pyc 무효화는 **(mtime 초, size)** 이므로 pytest 가 재작성한 `__pycache__/*.pyc` 가 **유효하다고 판정되어 계속 로드**되었다 — 이후 모든 실행이 `>= 3.0` 바이트코드로 돌았다.

증상은 "산술은 3.80 인데 테스트는 통과" 라는 불가능한 결과였다. `struct.unpack` 으로 pyc 헤더를 직접 읽어(`pyc_mtime 1788110989 == src_mtime`, `size 25964 == 25964`) 원인을 확정하고, `__pycache__` / `.pytest_cache` 전량 삭제 + `PYTHONDONTWRITEBYTECODE=1` + `-p no:cacheprovider` 로 **10종 전부 재실행**했다. 위 표는 재실행 결과다. 이 노트의 중간 수치가 아니라 최종 표를 신뢰하면 된다.

이 저장소는 뮤테이션 기반 검증을 문화로 삼고 있으므로(ISSUE-43 의 `guard_mutations.py`) 기록해 둔다. `guard_mutations.py` 는 뮤턴트를 샌드박스 사본에서 실행하므로 이 함정에 걸리지 않지만, **손으로 돌리는 뮤테이션은 걸린다.**

## Follow-ups (별도 이슈 제안)

1. **`.qr-code-caption` AA 보정** (P1, fix) — `rgba(0,0,0,0.5)` on `#ffffff` = 3.95:1. 흰 카드 backdrop 이므로 이 이슈의 두 backdrop 표와 계산이 다르다. `#595959`(4.54:1) 부근이 최소 불투명 후보. 같은 카드의 `strong` 은 12.63:1 로 통과.
2. **웰컴 대비 가드를 파생형으로 전환** (P2, test) — F-1 + F-2 + F-3 + F-4 를 한 이슈로. `tests/test_viewer_page.py:1145` 의 `test_opaque_background_surfaces_are_the_enumerated_set` 형태를 따라 (a) 스타일시트에서 웰컴 규칙을 **발견**해 목록과 대조, (b) fullscreen 스캐너를 클래스 토큰 기반으로 + `opacity|filter` 포함, (c) `color` 파싱을 last-wins 로. 순회 헬퍼의 `tests/wcag.py` 승격(additive)을 함께 검토 — **ISSUE-53 머지 이후**.
3. **`components/webrtc.html` 잔여 표면 대비 감사** (P2, fix) — 상태 칩 / FAB / `.caption-line` 변형 / `st.radio` 표시 모드. PR 본문이 미감사로 명시한 목록 그대로.
4. **`tests/e2e/test_admin_stage_config_e2e.py` 플레이키 안정화** (P2, test) — base `ee409e7` 에서도 5 failed / 17 errors, 단독 실행 시 6 failed. 이 PR 과 무관하나 e2e 게이트의 신호를 갉아먹는다.

## Confidence

**High.**

- 대비 수치 13개를 지시문·PR 본문과 독립적으로 `tests/wcag.py` 로 재계산했고 전부 일치했다.
- 가드 3종을 각각 정면으로 깨뜨려 보았고(10 뮤턴트 × 정적·e2e), 살아남은 4종을 값과 함께 보고했다.
- 오염된 첫 측정을 pyc 헤더로 진단하고 전량 재측정했다 — 이 노트의 수치는 캐시를 비우고 `PYTHONDONTWRITEBYTECODE=1` 로 다시 얻은 것이다.
- e2e 실패 2건은 base 를 실제로 체크아웃해 대조했다(base 가 더 나쁨) — 추론이 아니라 실측이다.
- 유일한 미확인 영역: **실제 브라우저 픽셀 샘플링(occlusion)**. 하지 않았고, 필요도 없다고 판단했다 — 웰컴 화면 위에 재생 중인 `<video>`/`<canvas>` 가 없어 RL-018 두 번째 사례의 조건이 성립하지 않고, e2e 가 이미 `getComputedStyle` 로 실제 backdrop 을 관측한다. 이 판단만 Medium 확신이다.

## Verdict

**APPROVE-WITH-NITS.** 머지 가능하다.

AC 7/7, 프로덕션 변경은 색 리터럴뿐, 회귀 4건(ISSUE-47 / ISSUE-48 / RL-010 / viewer·stage) 전부 무접촉, 게이트 전부 초록, 그리고 이 이슈가 존재하는 이유였던 두 변이(`3.80:1`, `4.43:1`)가 실제로 그 숫자를 말하며 죽는다. Scope 규율도 지켜졌다 — 눈앞의 `.qr-code-caption` 3.95:1 을 참고 남겨 두었다.

**차단 없음.** F-1·F-2 는 Medium 이지만 **테스트 강도**에 대한 것이고, 출하된 코드는 규격을 만족하며, F-1 은 CI e2e 가 그물을 유지한다. 다만 F-2 는 RL-018 의 재발 벡터 그 자체이고 직전 이슈(ISSUE-45)가 이미 정답 형태를 출하해 두었으므로, **후속 이슈 2번을 다음 스프린트에 반드시 잡을 것**을 권고한다.

---

# Review Notes — ISSUE-50 (PR #149)

**Reviewer**: Claude Opus 5 (automated, independent REVIEW phase)
**Date**: 2026-08-31
**Branch**: `issue/ISSUE-50-two-origin-opener-e2e` (reviewed at `b8111d4`)
**Reviewer worktree**: `.worktrees/review-ISSUE-50-code` (detached HEAD, **separate from the
implementer worktree** — `git rev-parse --show-toplevel` confirmed before any command ran)
**PR Size**: +422 −0, one new file: `tests/e2e/test_stage_window_opener_e2e.py` (7 tests)

**Verdict**: **APPROVE-WITH-NITS. Merge-blocking findings: 0.**
The test is real. Its discriminating power was re-measured independently rather than taken
from the PR body, and the anti-vacuity gate was **falsified on purpose** — it fires when it
should, and removing it turns the suite green on a popup that never left `about:blank`.

---

## Scope discipline — production diff is genuinely zero

`git diff origin/main b8111d4 -- components/ operator_ui.py guard_mutations.py guard_mutations.toml`
→ **empty**. `git diff --stat origin/main b8111d4` → exactly one file, `+422`.
`issues.md` / `STATUS.md` not committed to the branch. Confirmed independently.

## Code Review

### The two-origin claim is real — verified three ways, one of them by the browser

1. **Ports do make them cross-origin.** Origin is the (scheme, host, port) tuple, so
   `127.0.0.1:A` vs `127.0.0.1:B` is cross-origin by definition. But this review did not
   stop at the spec: under mutation M1/M2 the popup reports
   `{'isNull': False, 'type': 'object', 'str': 'threw:SecurityError'}`. A `SecurityError`
   raised by `String(window.opener)` is the browser itself certifying that the retained
   handle is a **cross-origin `WindowProxy`**. Same-origin would have returned
   `[object Window]`. That is direct empirical proof, not an argument from the spec.
2. **The operator page is built by the production render path.** The fixture calls
   `operator_ui.build_bootstrap_payload(...)` then `operator_ui.render_component_html(...)`
   — the exact two functions `app.py:469` and `app.py:488` call. No fixture-local
   reassembly, so the ISSUE-48 / RL-024 trap ("the fixture keeps testing the old path after
   production is fixed") is structurally avoided.
3. **Nothing fakes the stage document.** `add_init_script` count = 0, `page.route` count = 0
   (AST-verified by the file's own guards and re-grepped by hand). The only non-read
   `evaluate` in the file sets `style.display='block'` on the fallback anchor — which is
   character-for-character what the production popup-blocked branch does at
   `webrtc.html:1017`. The reveal is legitimate; the browser still does the window
   creation, `rel` interpretation and navigation.

### The vacuity gate is load-bearing — falsified, not assumed

`_assert_navigated_to_stage` was attacked directly in the reviewer worktree by pointing
`stage_url` at `about:blank`, so no cross-origin navigation ever happens:

| Experiment | Gate | Result |
|---|---|---|
| **V1** — no cross-origin navigation, **gate present** | present | **exit 1 — gate FIRED.** `popup = <Page url='about:blank'>`, assertion `observed_origin == stage_origin` fails with the crafted message |
| **V2** — same, **gate removed** | removed | **exit 0 — VACUOUS GREEN.** Observed `{'href': 'about:blank', 'isNull': True, 'str': 'null'}` — 1 passed |

V2 is the finding that matters: with the gate deleted the suite is **perfectly green while
proving nothing**, because `opened.opener = null` on an origin-inheriting `about:blank` is a
same-origin write that always succeeds. The gate is the only thing standing between this
file and RL-004. It is not decoration. Both experiments reverted; `git diff --quiet` verified.

### Independent mutation proof — three pre-declared mutations, one batch

Re-derived from scratch in the reviewer worktree; the PR body's table was **not** taken on
faith. Each mutation reverted with `git checkout --` + `git diff --quiet` verified after.

| # | Mutation of `components/webrtc.html:1002` | New e2e (`test_stage_window_opener_e2e.py`) | Old string guard (`test_webrtc_stage_launch.py`) |
|---|---|---|---|
| **M1** | `setTimeout(() => { opened.opener = null; }, 0);` | **5/5 KILL** (1 failed, 6 passed each run) | **23 passed — SURVIVED** |
| **M2** | `setTimeout(() => { opened.opener = null; }, 300);` | **5/5 KILL** | **23 passed — SURVIVED** |
| **M3** | assignment deleted | **1/1 KILL** | 1 failed, 22 passed — KILL |

Every kill landed on exactly one node —
`TestStagePopupOpenerIsSevered::test_popup_from_the_button_has_no_opener` — 11 times out of
11. No collateral damage, which is what distinguishes a discriminating guard from a brittle
one. The fallback-anchor test stayed green under all three (its protection is the markup's
`rel="noopener"`, which these mutations do not touch) — correct, precise attribution.

**This contrast is the whole issue.** M1/M2 completely defeat the mitigation and the
pre-existing string guard is 23/23 green, because `"opened.opener = null"` is still in the
file. The substring guard can only see **deletion** (M3). Relocation is structurally
invisible to it.

### On M1's non-determinism — the PR reports it honestly, and the test is right

The PR body states plainly that M1 survived 1 run in 11 and explains why
(`setTimeout(…, 0)` can fire before the popup's cross-origin document commits, making the
assignment a same-origin write that succeeds). It also says no tuning was done to push the
result toward kill. That disclosure is accurate and more forthcoming than the norm.
This review got **5/5 kills** on M1 — entirely consistent with a ~9% survival rate
(P(no survivor in 5) ≈ 0.63), so the two measurements do not conflict.

**The deeper point, and the reason no change is requested:** when M1 survives, the mitigation
*genuinely worked in that run* — `window.opener` really was `null` and the operator tab
really was protected. The test asserts the **security outcome**, not the code shape, so a
green there is correct rather than vacuous. Making the test kill M1 deterministically would
require asserting the shape of the source — which is precisely the defect
(`"opened.opener = null" in html`) this issue exists to eliminate. **Do not "fix" this.**
M2 discharges AC6 deterministically (5/5 here, 3/3 in the PR), and AC6 does not pin a delay.

A corollary worth recording for ISSUE-58: deferrals *shorter* than a macrotask (an `await`
on an already-resolved promise, i.e. a microtask hop) would survive more often than M1, for
the same reason — but in those runs opener is genuinely severed, so the suite is not lying.
Inference from the M1 data, deliberately **not** measured here (the batch was bounded to
three pre-declared mutations, no adaptive generation).

## Security Findings

**None.** Test-only diff; zero production change. No secrets, no user input, no injection
surface — `_STAGE_STUB_HTML` is a module constant and the operator payload is
developer-controlled. Both `web.TCPSite` instances bind `127.0.0.1` (not `0.0.0.0`), so the
ephemeral servers are not reachable off-host during a run.

The PR **strengthens** a security control: `opened.opener = null` (tabnabbing / opener
severance, NFR-029) had *zero* discriminating coverage before this file — the sibling e2e
replaces `window.open` with a same-realm plain object whose `opener` property accepts any
write regardless of browser rules, and the only other guard was a substring match. A
cross-realm security property was unobservable by construction. It is now observed in the
popup's own realm.

Bonus, unclaimed by the PR: `test_popup_from_the_fallback_anchor_has_no_opener` is the first
**behavioural** kill test for `rel="noopener"` on the fallback anchor. ISSUE-58's seed
catalogue lists `webrtc.stage_launch_fallback#rel_noopener` with verification lane
"정적(태그 범위)"; that row can now be upgraded to an e2e DOM-behaviour lane.

## Over-Engineering

422 lines for 7 tests. Net removable: **~19 lines confidently, ~36 if nits are taken** —
under 9% of the file. The core design (two real origins, no `window.open` stub, assertion
executed in the popup's own realm, vacuity gate before the opener read) is the right shape
and was not touched.

| ID | Tag | Finding | Sev | Lines | Resolution |
|---|---|---|---|---|---|
| O-1 | `delete` | `test_no_network_interception_fakes_the_stage_document` — its **stated premise is factually wrong** (see below). Only test in the file mapping to no AC | Low | 17 | **Docstring corrected in-PR**, test kept (author intent preserved) |
| O-2 | `shrink` | unused `request` fixture param on `test_popup_from_the_button_has_no_opener` | Low | 2 | **Fixed in-PR** |
| O-3 | `delete` | `sys.modules["streamlit"] = MagicMock()` shim is dead — this file's only production import is `operator_ui` → `script_escape` → stdlib. Cargo-culted from siblings that *do* pull `sse_broadcast` | Nit | 6 | Accepted (cheap house pattern, guards a future import) |
| O-4 | `native` | `assert marker.count() == 1` after `wait_for(state="attached")`, and `assert popup.is_closed()` after a sync `close()` — neither can fail | Nit | 6 | Accepted |
| O-5 | `shrink` | `rsplit(":",1)[1]` port comparison subsumed by `!=` plus both `startswith("http://127.0.0.1:")` | Nit | 3 | Accepted (reads as intent) |
| O-6 | `shrink` | `_DESCRIBE_OPENER` wraps `isNull` and `typeof` in try/catch; neither can throw (own-global read; `typeof` on a cross-origin `WindowProxy` returns `"object"` without a trap). Only `String(...)` can raise | Nit | 2 | Accepted — symmetry keeps the diagnostic readable |

**O-1 in detail (the one real error in the file).** The docstring claimed that a faked
origin-B response would defeat the "it navigated to another origin" gate at the network
level. That is **wrong**: `route(...).fulfill(...)` still commits the document at the
**requested URL's** origin — origin is decided by the URL, not by whether a real socket
answered. Interception therefore *cannot* make the opener assertion vacuous; `abort()` would
only trip the gate, never silence it. Since a wrong rationale in a file whose entire subject
is origin semantics will mislead the next reader (this repo does a lot of origin-sensitive
work — opaque `srcdoc` origins, cross-origin iframes, SSE), the **rationale was corrected
rather than the test deleted**: it is a fixture-contract guard (ISSUE-50 Scope requires two
real servers), not a vacuity gate, and it is now labelled as such.

**`TestThisHarnessDoesNotStubTheBrowser` — keep all three.** AC4 asks for one of them, but
the other two earn their keep. The single most likely future edit to this file is "the popup
test is flaky, stub `window.open` like the file next to it does" — and
`tests/e2e/test_operator_stage_mode_e2e.py:85` does exactly that, in the same directory, via
`page.add_init_script`. `test_no_init_script_is_injected_at_all` blocks that vector;
`test_no_string_in_this_file_reassigns_the_popup_api` blocks the *other* vector
(`page.evaluate("window.open = …")`), which this file is already one line away from since it
calls `operator_page.evaluate(...)` at line 314. The AST-based docstring exclusion is not
ceremony either: a naive grep would false-positive the moment someone documents the mutation
table inline, and a guard that cries wolf gets deleted rather than fixed — RL-004's exact
failure mode.

## Correctness notes (non-blocking, no change requested)

- **C-1 (Low, accepted).** `_assert_navigated_to_stage` reads `location.origin` one-shot
  after `wait_for_load_state("load")`. In principle the popup could still be on the
  inherited `about:blank` when that read happens, making the gate over-strict. Rated Low and
  **deliberately not changed**: the failure direction is a false RED, never a false GREEN
  (a race makes the gate stricter, never vacuous), it did not occur once in ~18 executions
  of this path here (3 flakiness runs + 11 mutation runs + full-suite + V1/V2), and the
  obvious "fix" (marker-wait first) trades away the crafted origin-mismatch message that
  made the V1 falsification legible — replacing it with a bare 5s Playwright timeout.
- **C-2 (not a finding).** Fixture scoping is sound. Module-scoped servers under a
  function-scoped `page` is the legal dependency direction; both handlers are stateless
  closures returning constants. `len(page.context.pages) == 1` is robust (fresh
  function-scoped context per test) and has real power beyond AC7 — it fails if a click ever
  opens two windows, which is the `dataset.stageBound` double-bind hazard at
  `webrtc.html:976`.
- **C-3 (Nit, pre-existing, not this PR).** `_free_port()` is TOCTOU and is now duplicated in
  six e2e files; `_serve`'s shutdown swallows exceptions with a bare `except Exception: pass`.
  This PR is the first to factor `_serve` into a reusable `(base_url, shutdown)` pair — that
  shape should be promoted into `tests/e2e/conftest.py`. Follow-up, not a blocker.
- **AC7 partial.** Popup closure is asserted; *server* teardown is performed
  (`shutdown()`) but never asserted. A `ConnectionRefusedError` probe in the module
  finalizer would close it (~4 lines). Low — a daemon thread on an ephemeral port dies with
  the pytest process. Follow-up.

## Measurements (reviewer worktree, `.worktrees/review-ISSUE-50-code`)

| Check | Result |
|---|---|
| `uv run ruff check .` | clean |
| `uv run black --check .` | clean, 67 files |
| `uv run pytest -q` (unit) | **1319 passed, 1 skipped, 140 deselected** (105s) |
| new file × 3 (flakiness) | **7 passed / 7 passed / 7 passed** — 0 flakes (2.59s / 0.80s / 0.80s) |
| new file × 3 again, after review edits | **7 passed** × 3 (0.88 / 0.78 / 0.75s) |
| `pytest tests/e2e -m e2e --no-cov -q` | **140 passed, 0 errors** (209s), exit 0 |
| `pytest tests/e2e/test_fullscreen_e2e.py` isolated | **14 passed, 0 errors** (92s), exit 0 |

### e2e baseline reconciliation — the branch introduces no regression, and the "pre-existing breakage" does not reproduce

The PR reported the full e2e as **138 passed / 2 errors** and separately claimed
`test_fullscreen_e2e.py` yields **6 passed / 8 errors on untouched main in isolation**. Both
were re-measured here:

- Full e2e on the branch: **140 passed, 0 errors.** The *collection* count reconciles exactly
  (133 baseline + 7 new = 140; 138 + 2 = 140), so only the pass/error split differs.
- `test_fullscreen_e2e.py` in isolation: **14 passed, 0 errors** — not 6/8. Note this file is
  byte-identical to `origin/main` on this branch (the PR adds one unrelated file and touches
  no conftest), so running it here *is* running it at main's state.

**Conclusion: this branch introduces zero e2e regression, and there is no pre-existing
`test_fullscreen_e2e.py` breakage to file.** The most probable explanation for the
implementer's numbers is machine load — this sprint runs up to 3 issues in parallel, and
every one of those errors was a 20s `Locator.wait_for` timeout in the Streamlit-subprocess
`streamlit_server` / `logged_in_page` fixture. That makes the file **load-sensitive and
flaky**, not broken. The existing `docs/sprint_state.md` note asserting it is "선재적으로
깨져 있다" on main overstates the evidence and is corrected there.

## Guard-catalogue decision — verified against the code, decision holds

The PR declines to add `webrtc.stage_launch#opener_sever` to `guard_mutations.toml`,
assigning it to ISSUE-58. All four grounds check out against the actual source:

| Claim | Verified |
|---|---|
| `ALLOWED_FILES` is four `.py` modules, hardcoded by design | `guard_mutations.py:118-125` — exactly `branding_assets.py`, `branding_routes.py`, `auth.py`, `admin_logic.py`, with the comment "카탈로그(데이터)가 자신의 허용 범위를 스스로 넓힐 수 있으면 허용 목록이 아니다". `:440` rejects anything else as `ERROR — 허용 목록 밖 파일입니다` |
| `run_catalog`'s `compile()` pre-check is Python-only | `guard_mutations.py:555` `compile(mutated, str(target), "exec")`, added specifically to stop RL-004-style false kills. No HTML analogue exists |
| `_run_pytest` inherits `-m 'not e2e'` → deselect → exit 5 → mis-reported ERROR | `:380-390` passes only `--no-cov -p no:cacheprovider -q -rf`, so `pyproject.toml:90` `addopts = "-ra -m 'not e2e' …"` applies. `:594-620`: only exit 1 is a kill candidate; exit 5 falls through to `ERROR` |
| `mutation` CI job has no Playwright install, dimensioned ~9s / `timeout-minutes: 10` | `.github/workflows/ci.yml:81-113` — checkout, setup-uv, python, `uv sync`, cache, run. No Playwright step (the separate `e2e` job at `:141-149` has it). `timeout-minutes: 10`, comment "실측 ~9s" |

Stronger than "plausible": **ISSUE-58's own spec already enumerates these same three
structural blockers verbatim** and lists `ALLOWED_FILES` widening as an In-scope item, and
its seed catalogue already contains a row for this very file
(`webrtc.stage_launch_fallback#rel_noopener`). The natural home is ISSUE-58, exactly as the
PR argues. **Decision upheld.** Recommend ISSUE-58 add `webrtc.stage_launch#opener_sever`
with the M1/M2/M3 rows above as its evidence.

## Checkpoints

| phase | exit | note |
|---|---|---|
| checkout | 0 | PASS |
| review | 0 | PASS — but see caveat below |
| figma-compliance | 0 | PASS (no Figma data → skip) |
| computed-styles | 0 | PASS (skip) |
| visual-diff | 0 | SKIP (no implementation HTML/URL) |
| structural-match | 0 | PASS (skip) |
| layout | 0 | PASS (skip) |
| ui-review | 0 | PASS — `UI: false`, correctly skipped |
| test-quality | 0 | PASS — 45 test files have real assertions |
| test | 0 | PASS — 88.1s unit gate. **The documented 60s/exit-124 trap did not fire** |
| push | 0 | PASS |

Two checkpoint defects observed and worth an issue against the kit:

1. **`_find_worktree_path` returns the first matching worktree**, which for ISSUE-50 is the
   *implementer's* worktree, not the reviewer's. Every review checkpoint therefore inspected
   `.worktrees/issue-ISSUE-50-two-origin-opener-e2e` — a reviewer's artifacts in a dedicated
   worktree are invisible to the gate that is supposed to verify them. (The implementer
   worktree's tracked state was snapshotted before and after and is unchanged at `b8111d4`.)
2. **`review/review` is not issue-scoped.** It regex-searches the whole 228 KB append-only
   `review_notes.md` for the three headers, so it passes on headers written for *any* past
   issue. It cannot detect a missing review for the issue under test.

## Verdict

**APPROVE-WITH-NITS — 0 merge-blocking findings.** Ship it.

This is a test-only PR whose entire value is discriminating power, and it has it: 10/10 kills
across two relocation mutations that the pre-existing guard survives 2/2, precise
single-node attribution, an anti-vacuity gate proven load-bearing by direct falsification,
and zero flakes in six consecutive green runs. The production diff is genuinely zero.
