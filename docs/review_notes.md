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
