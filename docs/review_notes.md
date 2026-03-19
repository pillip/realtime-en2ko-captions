# Review Notes — PR #12 (ISSUE #11: 전체화면 모드)

**Reviewer**: Claude Opus 4.6 (automated)
**Date**: 2026-03-19
**PR Size**: ~919 lines added across 8 files (within review threshold)
**Test Results**: 102/102 passing (unit), E2E tests excluded by default via `-m 'not e2e'`
**Confidence Rating**: Medium -- E2E tests were not executed during this review due to requiring a running Streamlit server. Unit-level tests (test_fullscreen.py) are string-matching only.

---

## Code Review

### Critical

(none)

### High

#### [CR-1] `toggleFullscreen` throws TypeError if Fullscreen API is entirely unavailable (webrtc.html:987-1004)

```js
function toggleFullscreen() {
    if (isFullscreen()) {
      (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    } else {
      // ...
      (viewer.requestFullscreen || viewer.webkitRequestFullscreen).call(viewer);
    }
  }
```

If neither `requestFullscreen` nor `webkitRequestFullscreen` exists on the element (e.g., older browsers, or iframe without `allowfullscreen` attribute), the expression `(undefined || undefined)` evaluates to `undefined`, and `.call(viewer)` throws `TypeError: undefined is not a function`. Since this button is always visible, users on unsupported browsers will get a silent JS error.

**Fix suggestion**: Guard with a capability check:
```js
const fn = viewer.requestFullscreen || viewer.webkitRequestFullscreen;
if (fn) fn.call(viewer);
else console.warn('Fullscreen API not supported');
```

The same pattern applies to `document.exitFullscreen || document.webkitExitFullscreen`.

### Medium

#### [CR-2] Duplicated fullscreen exit handler logic (webrtc.html:1029-1045)

The `fullscreenchange` and `webkitfullscreenchange` event listeners contain identical logic for restoring font sizes from localStorage. This is a maintenance risk -- if the restore logic changes, both handlers must be updated in lockstep.

```js
document.addEventListener('fullscreenchange', () => {
    if (!isFullscreen()) {
      const savedTransSize = localStorage.getItem('translationFontSize') || '28';
      // ...
    }
  });
  document.addEventListener('webkitfullscreenchange', () => {
    if (!isFullscreen()) {
      const savedTransSize = localStorage.getItem('translationFontSize') || '28';
      // ...
    }
  });
```

**Fix suggestion**: Extract into a named function `onFullscreenExit()` and register it for both events.

#### [CR-3] Fragile position-based HTML assertion in test_fullscreen.py (test_fullscreen.py:59-65)

```python
def test_font_controls_inside_viewer(self):
    html = _read_html()
    viewer_start = html.find('id="viewer"')
    viewer_end = html.find("</div>", html.find("</div>", viewer_start) + 1)
    controls_pos = html.find('id="fsFontControls"')
    assert viewer_start < controls_pos < viewer_end + 500
```

This test uses character position arithmetic with a magic offset of `+500` to determine DOM nesting. Any HTML reformatting (e.g., Prettier, added attributes, comments) will break this test without any functional change. The assertion is also structurally flawed -- `viewer_end` finds only the second `</div>` after `id="viewer"`, which may not be the closing tag of the viewer element.

**Fix suggestion**: Use a proper HTML parser (e.g., `html.parser` from stdlib or `BeautifulSoup`) to verify DOM nesting, or remove this test in favor of the E2E test that verifies the actual rendered DOM.

#### [CR-4] E2E tests silently pass when fullscreen is unavailable (test_fullscreen_e2e.py:56-83, 85-102)

`TestFullscreenEntry` tests fall back to checking `typeof requestFullscreen === 'function'` when fullscreen activation fails due to security restrictions. The test also conditionally skips key assertions (`if is_fs:` on line 95). In CI environments, fullscreen will almost always be blocked, meaning the core assertions never execute.

```python
if not is_fs:
    has_fn = frame.evaluate(
        "typeof document.documentElement.requestFullscreen === 'function'"
    )
    assert has_fn, "requestFullscreen 함수가 존재해야 함"
```

This matches **RL-004** (weak test assertions that pass trivially). The test "passes" but verifies almost nothing about the fullscreen behavior.

**Fix suggestion**: At minimum, add a `pytest.warns` or a log message when the fallback path is taken, so CI logs make it obvious that the real assertion was skipped. Consider using Playwright's `--headless=false` option in CI with Xvfb for actual fullscreen testing.

#### [CR-5] Global singleton mutation in database.py creates test isolation risk (database.py:530-543)

```python
_db_manager = None

def get_db_manager() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        db_path = os.getenv("DB_PATH", "data/app.db")
        _db_manager = DatabaseManager(db_path)
```

The `DB_PATH` env var is read only on first call. If any test (unit or otherwise) imports and calls `get_db_manager()` before E2E tests set `DB_PATH`, the singleton will point to the production `data/app.db` path and will not be re-initialized. The E2E conftest mitigates this by setting the env var before launching the Streamlit subprocess, but any in-process test using `get_db_manager()` directly is at risk.

**Fix suggestion**: Add a `reset_db_manager()` function for testing, or accept `db_path` as an optional parameter that forces re-initialization.

### Low

#### [CR-6] `test_fullscreen.py` tests are pure string matching (RL-004 pattern)

All 17 tests in `test_fullscreen.py` use `assert "some_string" in html` to verify the HTML file contains expected IDs, CSS rules, and function names. While these catch deletion/rename regressions, they cannot detect:
- Correct nesting (a string can exist in a comment or wrong element)
- CSS specificity conflicts
- JS runtime behavior

The E2E tests (`test_fullscreen_e2e.py`) cover the runtime behavior, which is the appropriate layer for these checks. The string-matching tests provide a quick-feedback safety net, which is acceptable as a complement, but should not be the primary verification.

#### [CR-7] `database.py` line-length reformatting mixed with functional change

The PR includes SQL line-wrapping changes alongside the functional `DB_PATH` env var change. Mixing formatting and functional changes in the same commit makes it harder to review and bisect. This is minor for a small diff but worth noting as a practice.

---

## Security Findings

### Critical

(none)

### High

(none)

### Medium

#### [SEC-1] Dummy AWS credentials in E2E conftest (conftest.py:42-43)

```python
"AWS_ACCESS_KEY_ID": "test_key",
"AWS_SECRET_ACCESS_KEY": "test_secret",
```

These are clearly dummy values for test startup, but they establish a pattern of hardcoding credential-shaped strings in test files. If a developer copies this pattern with real credentials, they could leak secrets.

**Mitigating factors**: The values are obviously fake (`test_key`, `test_secret`). The `.gitignore` should already exclude `.env` files.

**Fix suggestion**: Add a comment explicitly stating these are dummy values, or use `TESTING_MODE=true` env var to skip AWS credential validation entirely during tests.

#### [SEC-2] DB_PATH env var allows arbitrary file path (database.py:539)

```python
db_path = os.getenv("DB_PATH", "data/app.db")
_db_manager = DatabaseManager(db_path)
```

The `DB_PATH` environment variable is not validated. An attacker with env var control (e.g., via container misconfiguration) could point the database to an arbitrary path, potentially overwriting files or reading from unexpected locations.

**Mitigating factors**: Environment variables are typically controlled by the deployer, not by end users. This is a defense-in-depth concern rather than an exploitable vulnerability.

**Severity justification**: Medium because env var manipulation requires infrastructure-level access, but the lack of any path validation means no defense-in-depth.

### Low

#### [SEC-3] Fullscreen JS executes within sandboxed iframe

The fullscreen feature uses `requestFullscreen` on the `#viewer` element within a Streamlit `st.components.v1.html` iframe. For fullscreen to work, the parent must set the `allow="fullscreen"` or `allowfullscreen` attribute on the iframe. If the parent does not set this, the Fullscreen API will silently fail or throw. This is not a security vulnerability per se, but a configuration dependency worth documenting.

---

## Accessibility Audit (WCAG 2.1 AA)

**Total findings**: 14 (3 Critical, 5 High, 4 Medium, 2 Low)

### Fixes Applied

| Finding | Severity | Fix |
|---------|----------|-----|
| P-1/P-2: `.fs-font-controls` opacity 0.15 (contrast 1.39:1) | **Critical** | Changed to `opacity: 0.6`, hover/focus-within to `1.0` |
| O-3: No focus-visible styles on interactive elements | **Critical** | Added `:focus-visible` with `#3b82f6` outline (5.3:1 contrast) |
| P-3/P-4: FAB background contrast 1.29:1 | **High** | Added `border: 1px solid rgba(255,255,255,0.25)`, bg to 0.15 |
| P-5/P-6: No aria-label on FAB buttons | **High** | Added `aria-label="설정"` and `aria-label="전체화면"` |
| P-7: No aria-label on font control buttons | **Medium** | Added descriptive `aria-label` to all 4 buttons |
| O-1: Font button touch target 32x32px | **High** | Increased to 44x44px |
| O-4: No Escape key for settings panel | **Medium** | Added `keydown` Escape handler |
| R-1: Settings panel missing ARIA state | **Medium** | Added `aria-expanded`, `aria-controls`, `role="region"` |
| U-1: Missing `lang` attribute | **Medium** | Added `lang="ko"` to `<html>` |
| U-2: Font controls not programmatically grouped | **Low** | Added `role="group"` and `aria-label` to control groups, `aria-live="polite"` to size displays |

### Remaining (not fixed in this review)

| Finding | Severity | Reason |
|---------|----------|--------|
| O-2: FABs shrink to 40x40 in compact mode | Medium | Still above 24px AA minimum |
| O-5: No `prefers-reduced-motion` support | Low | Enhancement, not blocking |

---

## Follow-ups

1. **[Follow-up] Extract fullscreen exit handler into named function** -- Deduplicate the `fullscreenchange`/`webkitfullscreenchange` handlers (CR-2). Estimated: 5 minutes.

2. ~~**[Follow-up] Add Fullscreen API guard** (CR-1)~~ -- **DONE** in this review (capability guards added to `toggleFullscreen`).

3. **[Follow-up] Replace HTML string-matching tests with parser-based assertions** -- The position arithmetic in `test_font_controls_inside_viewer` (CR-3) is fragile. Use `html.parser` or accept the E2E tests as the primary verification layer. Estimated: 30 minutes.

4. **[Follow-up] Add `reset_db_manager()` for test isolation** -- Prevent singleton state leakage between test modules (CR-5). Estimated: 15 minutes.

5. **[Follow-up] Document iframe `allowfullscreen` requirement** -- Ensure Streamlit component embedding sets the correct iframe attribute for fullscreen to work (SEC-3). Estimated: 10 minutes.

6. **[Follow-up] Make E2E fullscreen skip explicit** -- When fullscreen cannot be activated in CI, log/warn clearly rather than silently passing (CR-4). Estimated: 15 minutes.

7. **[Follow-up] Add `prefers-reduced-motion` media query** -- Disable animations for motion-sensitive users (O-5). Estimated: 10 minutes.
