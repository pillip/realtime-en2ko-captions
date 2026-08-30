# Review Lessons

Preventable patterns identified during code reviews. Each entry includes when the pattern could have been caught earlier.

---

## [RL-001] Copy-paste testing instead of extracting importable modules

- **Category**: Architecture
- **Frequency**: 5
- **Observed-In**: PR #8, PR #10 (partially addressed -- modules extracted but tests not yet added), PR #42 (`SessionState` class duplicated in `test_coverage_gaps.py`), PR #127 (ISSUE-41 — `viewer.html`'s SSE + typewriter logic ported into `stage.html` with a defensible mechanical justification: two standalone `read_text()`+substitution templates, no bundler, no static-JS route. But the copy silently **fixed** a caption-dropping bug the original still has — two back-to-back finals render only the second on `/view/{room_id}`, reproduced in-browser — so the drift RL-001 predicts materialised inside the very PR that created the copy. Duplication was accepted for this PR; the un-fixed copy now needs its own issue), PR #141 (ISSUE-52 — the output-language whitelist is correctly `import`ed from `translation.SUPPORTED_OUTPUT_LANGS`, but the **input**-language whitelist `database._SESSION_INPUT_LANGS` was hand-copied out of `components/webrtc.html`'s `#selInputLang` options with a source comment and no equality guard. Adding one `<option>` to that `<select>` would make every session in the new language fail validation, silently leaving the stage subscribed to the previous language — the exact defect shape ISSUE-52 exists to remove. Caught in review; closed in-PR with a static set-equality test over both dropdowns plus a value check that each option is actually persisted)
- **Description**: When a source file has import side effects (e.g., Streamlit's `st.set_page_config` at module level), test authors copy pure functions into the test file instead of importing them. This creates drift risk -- the copy diverges from the original as the source evolves, producing false coverage.
- **Prevention**: At kickoff/design time, separate pure logic (port finding, text splitting, API response parsing) into side-effect-free modules. This is a 10-minute refactor that eliminates an entire class of test maintenance bugs.
- **Recommended action**: Extract pure functions into `utils.py` or similar; import in both `app.py` and tests.

## [RL-002] Trusting client-supplied identity in server-side handlers

- **Category**: Security
- **Frequency**: 2
- **Observed-In**: PR #8 review (pre-existing in source), PR #10 (preserved during extraction)
- **Description**: WebSocket handler accepts user identity (including role) from the first client message without server-side validation. Tests for `check_usage_limit` pass admin-role dicts directly, normalizing the pattern.
- **Prevention**: During architecture review, require that all identity claims go through server-side session validation. Never trust role/permission claims from client messages.
- **Recommended action**: Implement token-based WebSocket auth where the server validates session tokens against its own store.

## [RL-003] Deterministic token generation using predictable inputs

- **Category**: Security
- **Frequency**: 1
- **Observed-In**: PR #8 review (pre-existing in source)
- **Description**: Session tokens generated via `SHA-256(user_id:username:timestamp)` are predictable. All inputs are guessable, making the token brute-forceable.
- **Prevention**: Use `secrets` module for all security tokens. This should be a standard item in security kickoff checklists.
- **Recommended action**: Replace with `secrets.token_hex()` and store token-to-session mapping server-side.

## [RL-004] Weak test assertions that pass trivially

- **Category**: Testing
- **Frequency**: 11
- **Observed-In**: PR #8, PR #12 (E2E fullscreen tests silently pass when fullscreen is unavailable; string-matching unit tests cannot detect structural correctness), PR #119 (`test_default_is_importable_without_side_effects` asserts only `not hasattr(module, "sqlite3")` / `"st"` — passes even if the module imported `streamlit` unaliased, opened a file, or hit the network), PR #122 (`test_presentation_area_keeps_16_9` asserts `frame.width <= presentation.width` — the parent grows with the child, so it passes on the overflowing layout; `header.height > 0` is offered as proof that the bars absorb the vertical slack, which a 1px bar satisfies — **independently confirmed by measurement in the code review: `pres.w = 1727.1`, `frame.w = 1695.1`, assertion PASSES while the deck overlays the caption column**), PR #122 code review (`assert "object-fit: contain" in stage_html` — 3 occurrences in the file, so it passes if the AC-relevant `.capture-video` rule loses it; `assert "min-height: 0;" in stage_html` — 4 occurrences; `test_empty_event_title_falls_back_to_room_name` asserts the server-rendered `{{ROOM_NAME}}`, which is present for every room regardless of `event_title`, so it cannot fail for the fallback it is named after), **PR #127 (ISSUE-41) — three guards survived their own mutation, all found by systematic mutation rather than by reading, in a suite that reads as unusually thorough**: (a) mutating `_twStep` to reveal the whole string in one frame passed the *entire* suite, so the PR's headline AC ("타자기 스무딩") had no behavioural guard at all — every caption test inspected only the end state; (b) deleting `_twStart`'s `twRaf ||` double-schedule guard produced **13 concurrent rAF loops** with the suite still green; (c) `test_hidden_tab_stops_the_typewriter_loop` asserted only that `addEventListener("visibilitychange"` and `document.hidden` appear in the file, so an empty `if (document.hidden) { }` body passed, **PR #125 (ISSUE-39) — the inverse case**: the guards that exist are strong (three mutations verified RED, including an AST test where 3 of 4 cases turn red when the call is moved out of the `is_role_admin` branch), but two ACs have **no automated test at all** — AC-4's "the typed title survives a failed save" (which depends on stable widget keys + conditional session_state seeding + the absence of `st.rerun()` on failure paths) and AC-5's end-to-end delete were verified only by hand in a browser. The suite is green and the ACs rest on manual verification; a refactor adding one `st.rerun()` to a failure path would break AC-4 silently. Absence of a weak assertion is not presence of a guard, **PR #141 (ISSUE-52) — a guard that could not fail on the branch it named**: `test_recorded_room_id_is_the_server_resolved_one` claimed to enforce RL-002 (use the server-resolved room id, never the client payload) but supplied `room_id="r1"` in the auth message and asserted the recorded id was `"r1"` — in that scenario `resolved_room_id == data["room_id"]`, so swapping the production code to `data.get("room_id")` still passed. The rest of the same suite was unusually strong (SQL-statement-level spy for the no-op guard, byte-identity assertion on the untouched column, both directions of RL-006), which is the point: one vacuous test hid inside 30 good ones and was found only by asking *which branch makes the two values differ*. Fixed by driving the no-`room_id` path where the server resolves `DEFAULT_ROOM_ID` and the client payload is `None`, then verified with a mutant, **PR #142 (ISSUE-43) — the pattern reproduced inside the tool built to eliminate it**: `guard_mutations.py` treats pytest exit code 1 as "the guard's kill test failed", but every kill test in this repo does a function-local `import branding_assets`, so a mutant that does not even compile fails *inside* the test (exit 1, a real `FAILED` line) rather than at collection (exit 2). Measured: a catalogue `replace` with an unbalanced paren reported `… killed by tests/…::test_dotdot_in_filename_is_rejected_not_stripped` and exit **0**. The strongest counter-evidence is also measured — weakening the same test to a trivially-true assertion, and separately reverting it to the exact ISSUE-38 shape (symlink target *outside* the room), both flipped the guard to `SURVIVED` with exit 1. The device works; its exit-code contract did not, **PR #147 (ISSUE-49) — the parser reads the declaration CSS does not apply**: every static welcome-contrast guard pulls the colour with `re.search(r"color:\s*…", block)`, i.e. the **first** `color` declaration in the rule, while CSS applies the **last**. Measured: adding a second, later `color: #55555c` (**2.66:1** on `#0b0b0c`) to `.welcome-rules` survives the entire static suite — the AA test, the opaque-hex test and the brightness-hierarchy test all keep reading the healthy first declaration, so a severe AA failure passes three assertions written to prevent exactly it. The alpha-white guard catches the `rgba()` variant of the same mutant (that guard uses `findall`), which is why it is *not* redundant with the hex guard, but the hex variant is caught only by the browser-driven e2e (which reported `2.66:1` correctly). The general shape: an assertion over parsed source is a claim about the *source*, and the cascade is a property of the *engine* — `re.search` silently encodes "first wins" where the platform says "last wins"
- **Description**: Tests use `assert len(result) >= 1` for functions that split text into multiple parts. This assertion passes even when the function fails to split at all, giving false confidence.
- **Prevention**: During test review, check that assertions would fail if the function under test did nothing (returned input unchanged). If an assertion passes for both correct and broken implementations, it is too weak.
- **Recommended action**: Use exact expected values or at minimum assert the expected count of results.

## [RL-005] Refactoring for testability without adding tests

- **Category**: Testing
- **Frequency**: 1
- **Observed-In**: PR #10
- **Description**: A refactoring PR extracts pure functions into importable modules specifically to enable testing, but ships without any tests. The testability improvement is real but unrealized -- the modules can drift or break without detection until someone eventually writes tests.
- **Prevention**: At PR planning time, pair every "extract for testability" task with a mandatory "add baseline tests" subtask. The tests do not need to be exhaustive -- even 5-10 assertions on the pure functions provide a regression safety net that justifies the refactoring effort.
- **Recommended action**: Block merge of extraction PRs until at least the pure-function modules (no mocking required) have basic test coverage.

## [RL-006] Internal error details leaked to clients via WebSocket/API responses

- **Category**: Security
- **Frequency**: 1
- **Observed-In**: PR #10 review (pre-existing in source, preserved during extraction)
- **Description**: Exception messages are sent directly to WebSocket clients via `str(e)`. These messages can contain internal file paths, class names, database details, or stack information that aids attackers in reconnaissance.
- **Prevention**: Establish a project-wide pattern for error responses: log the full error server-side, return a generic message to the client. Add this as a checklist item in the security kickoff.
- **Recommended action**: Create an error response helper function that maps exceptions to user-safe messages and logs the original error. Apply consistently across all client-facing endpoints.

## [RL-007] Dead code carried forward through refactoring

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #10
- **Description**: Functions that were never called in the original monolith (`create_aws_session`, `start_health_server`) were faithfully extracted into the new module structure. Refactoring is the ideal time to identify and remove dead code, but the mechanical nature of extraction ("move, don't change") can preserve it indefinitely.
- **Prevention**: During refactoring kickoff, run a dead code analysis (e.g., `vulture` or manual grep for callers) on the original file. Flag uncalled functions for removal or explicit documentation of their intended future use.
- **Recommended action**: Add a dead code scan step to the refactoring checklist. Functions with no callers should either be removed or annotated with a comment explaining their purpose.

## [RL-008] Browser API calls without capability guards

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #12
- **Description**: Calling browser APIs (e.g., `requestFullscreen`, `exitFullscreen`) via `(obj.method || obj.prefixedMethod).call(obj)` without checking that the resolved value is a function. When neither variant exists, the expression evaluates to `undefined` and `.call()` throws a `TypeError`, crashing the feature entirely instead of degrading gracefully.
- **Prevention**: At implementation time, always wrap optional browser APIs in a capability check (`if (fn) fn.call(obj); else fallback()`). This is especially important for APIs that require specific iframe attributes or user gestures to be available.
- **Recommended action**: Establish a project convention for calling optional/prefixed browser APIs: resolve the function reference first, check for truthiness, then call. Add this to the JS code style guide.

## [RL-009] Vendor-prefixed event handlers with duplicated logic

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #12
- **Description**: Registering separate event listeners for `fullscreenchange` and `webkitfullscreenchange` with identical inline handler bodies. When the handler logic needs to change, both copies must be updated, creating a maintenance risk.
- **Prevention**: Extract the shared handler into a named function and register it for both events. This is a standard DRY pattern that should be applied whenever vendor-prefixed events require parallel listeners.
- **Recommended action**: Refactor to `function onFsChange() { ... }; ['fullscreenchange', 'webkitfullscreenchange'].forEach(e => document.addEventListener(e, onFsChange));`.

## [RL-010] Interactive elements without accessible names or focus indicators

- **Category**: Accessibility
- **Frequency**: 1
- **Observed-In**: PR #12
- **Description**: Icon-only buttons (emoji/unicode symbols) shipped without `aria-label` attributes, and `border: none` removed default focus rings without providing `:focus-visible` replacements. Font controls had `opacity: 0.15` making them effectively invisible (contrast ratio 1.39:1 vs required 4.5:1). These are WCAG 2.1 AA failures that affect keyboard and screen reader users.
- **Prevention**: At implementation time, every interactive element should have: (1) an accessible name (`aria-label` for icon-only buttons), (2) a visible focus indicator, (3) sufficient contrast in its default state. Add these as a checklist item for UI PRs.
- **Recommended action**: Create a project-level a11y checklist for UI components: aria-labels, focus-visible styles, contrast ratios, touch target sizes (44x44px minimum).

## [RL-011] Using `100vh` without `dvh` fallback for mobile Safari

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #57 (ISSUE-36)
- **Description**: CSS `100vh` on iOS Safari includes the space behind the browser's address bar, so elements sized to `100vh` extend beyond the visible viewport when the address bar is shown. This is a well-documented browser inconsistency affecting all iOS Safari versions. The fix is to use `100dvh` (dynamic viewport height, supported since Safari 15.4 / iOS 15.4, March 2022) with `100vh` as a fallback for older browsers.
- **Prevention**: At implementation time, whenever `100vh` is used for full-viewport sizing, also declare `100dvh` as a progressive enhancement on the next line. The property cascade means older browsers ignore the unknown `dvh` unit and use the `vh` fallback.
- **Recommended action**: Establish a project CSS convention: always pair `height: 100vh` with `height: 100dvh` when the intent is to fill the visible viewport. Apply retroactively to `scroll_lock.html` which uses `100vh` in multiple rules.

## [RL-012] CSS `margin` vs `padding` confusion with `height: 100%` and `box-sizing: border-box`

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #58 (ISSUE-36 hotfix)
- **Description**: `box-sizing: border-box` makes `padding` and `border` included in the element's declared `height`/`width`, but `margin` is always outside the content box regardless of `box-sizing`. Setting `body { height: 100%; margin-top: 5%; box-sizing: border-box }` results in the element occupying 105% of its container. This was caught in PR #57 review (CR-1) but shipped and required a hotfix.
- **Prevention**: At implementation time, when using `height: 100%` or `height: 100vh`, verify that `margin` is zero on the same element. If top/bottom spacing is needed, use `padding` (which respects `border-box`) or adjust height to `calc(100% - margin)`. Include this check in CSS review checklists.
- **Recommended action**: Add to project CSS conventions: "Never combine `height: 100%`/`100vh` with non-zero vertical `margin`. Use `padding` instead."

## [RL-013] Streamlit inline styles require both CSS `!important` and JS DOM manipulation to override

- **Category**: Architecture
- **Frequency**: 1
- **Observed-In**: PR #58 (ISSUE-36 hotfix)
- **Description**: Streamlit sets inline `style="height: 900px"` on wrapper `<div>` elements around `st.components.v1.html()` iframes. Inline styles have higher specificity than external/embedded CSS rules, so even `!important` in a `<style>` block may not reliably override them across all Streamlit versions. PR #58 correctly uses a dual approach: CSS `!important` for known class selectors, plus a JS `MutationObserver` that walks the DOM and sets inline styles directly. The need for this dual approach was not anticipated during ISSUE-36 planning.
- **Prevention**: When embedding content in Streamlit via `st.components.v1.html()` or `st.markdown(unsafe_allow_html=True)`, inspect the generated DOM (DevTools) to identify all inline styles that Streamlit applies. Plan CSS override strategies accordingly at design time, not as hotfixes.
- **Recommended action**: Document the known Streamlit wrapper DOM structure and inline style patterns in the project architecture docs. Include the JS DOM walker pattern as a standard approach for full-viewport Streamlit components.

## [RL-014] Hotfix PRs without regression tests for the specific fix

- **Category**: Testing
- **Frequency**: 1
- **Observed-In**: PR #58 (ISSUE-36 hotfix)
- **Description**: A hotfix changed `margin` to `padding` and added wrapper div selectors, but no tests were added to verify these specific changes. The existing tests (from PR #57) check for `height: 100%` and absence of `90vh`, which still pass, but would not catch a regression that reintroduces `margin-top`. Hotfixes are especially prone to this because time pressure encourages skipping tests.
- **Prevention**: Even for hotfixes, add at least one test per fix that would fail if the fix were reverted. For CSS-based fixes, a simple string-presence test (`assert "margin: 0" in body_css`) takes under 5 minutes to write and prevents the exact regression the hotfix addresses.
- **Recommended action**: Establish a team rule: every hotfix commit must include at least one regression test. Block merge if the hotfix-specific test is missing.

## [RL-015] Narrow `except` that misses sibling failure modes of the same stdlib call

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #119 (ISSUE-37) — `stage_config._as_mapping()` and `Room.get_stage_config()` both caught only `json.JSONDecodeError`
- **Description**: `json.loads()` on untrusted input does **not** fail exclusively with `JSONDecodeError`. Deeply nested input (`'[' * 100000`) raises `RecursionError`, which is not a `ValueError` subclass and therefore slips straight through `except json.JSONDecodeError`. Both layers here carried docstrings promising "절대 예외를 던지지 않는다", and both broke that promise on the same input. The same class of bug applies to `int()` (`ValueError` vs `TypeError`), `decode()` (`UnicodeDecodeError` vs `LookupError`), and `re` (`re.error` vs `RecursionError`).
- **Prevention**: When a function's contract is "never raises", the exception clause is part of the contract — do not derive it from the happy-path error only. At implementation time, fuzz the boundary function with deep nesting, oversized values, and wrong types *before* writing the `except`. A degradation guarantee that has never been fuzzed is an assumption, not a guarantee.
- **Recommended action**: For any "never raises" normaliser fed by untrusted or persisted data, pair the docstring promise with an adversarial parametrised test (deep nesting, huge string, wrong top-level type). If the promise is truly unconditional, `except Exception` with a server-side log is more honest than an enumerated tuple that is silently incomplete.

## [RL-016] Persisted user text destined for HTML injection with no documented escaping owner

- **Category**: Security
- **Frequency**: 2
- **Observed-In**: PR #119 (ISSUE-37) — `stage_config.event_title` / `event_subtitle` stored unescaped; the issue's Implementation Notes prescribe injecting the blob "JSON 리터럴로" into the stage page (ISSUE-40). PR #122 (ISSUE-40) — the `<script>` half was correctly discharged by `_json_for_script` and proven RED, but the **same shape recurred one link downstream**: `stage_config.logo_groups[].assets[]` filenames are explicitly left unsanitised by `_as_logo_groups` ("경로 검증/봉인은 …ISSUE-38의 책임이다") and this PR puts them into an `<img src>`. The runtime path is safe (`encodeURIComponent` + `src` property assignment, verified in Chromium) but **no test constrains it**, so a future refactor to `innerHTML` breaks nothing in CI
- **Description**: A persistence/normalisation layer correctly declines to HTML-escape (escaping belongs at render), but no artifact records *who* must escape, so the obligation evaporates between issues. `json.dumps` escapes neither `<` nor `/`, so a `</script><script>…` payload in a stored title breaks out of an inline `<script>` block. The storage PR is not defective; the hand-off is. The gap only becomes exploitable one or two issues later, by which point the storage review has already been approved.
- **Prevention**: At kickoff, whenever a schema field is known to be rendered into HTML/JS by a *later* issue, write the escaping requirement into that later issue's AC list at the moment the field is designed — not when the renderer is built. Storage-layer reviews should flag "this field is an XSS carrier" as a forward-looking finding even though the storing PR is clean.
- **Recommended action**: For server-rendered JSON literals inside `<script>` blocks, standardise on escaping `<`, `>`, `&` (or replacing `</` with `<\/`) in a shared helper, and reference that helper from the data-model doc row for every user-controlled text column. When a renderer discharges such a hand-off, check whether the same blob carries a *second* field into a *different* sink (attribute, URL, CSS) and require a regression test for that sink too — discharging the named carrier does not discharge the column.

## [RL-017] Layout verified only against benign fixture content

- **Category**: UI State
- **Frequency**: 1
- **Observed-In**: PR #122 (ISSUE-40) — `.stage-main`'s implicit `auto` grid track grew to the `<h1>`'s max-content width (1743.67px against a 1440px grid area) and painted the presentation area over the caption column at 1920×1080, one of the two AC viewports
- **Description**: A responsive layout is exercised at many *viewport* sizes but only one *content* size, and the fixture content is always short and well-behaved ("2026 개발자 콘퍼런스"). Intrinsic-sizing defects — `auto` grid tracks resolving to `max-content`, flex items refusing to shrink below `min-content`, `white-space: nowrap` defeating `text-overflow: ellipsis` — are invisible until real content exceeds the container. The defensive CSS present (`min-width: 0` on the grid *container*, `overflow: hidden`, `text-overflow: ellipsis`) reads as thorough while constraining the wrong box, and `html { overflow: hidden }` suppresses the scrollbar that would otherwise expose the overflow.
- **Prevention**: Parametrise layout tests over content length as well as viewport, with at least one fixture at a realistic worst case (a 50+ character Korean conference title, a 40-character room name). For any CSS Grid whose children can be wider than their track, declare the track explicitly as `minmax(0, 1fr)` rather than relying on the implicit `auto` track — `min-width: 0` on the container does not constrain the track.
- **Recommended action**: Add a long-content fixture to every layout e2e suite at design time, and assert containment against the *sibling* boundary (the neighbouring column's left edge), never against the element's own parent, which grows with it.

## [RL-018] Dim-on-dark alpha values contrast-checked against a backdrop that is not what renders

- **Category**: Accessibility
- **Frequency**: 4
- **Observed-In**: PR #147 (ISSUE-49) — the cleanest instance of the "one backdrop is not the backdrop" half, and the first one caught *before* shipping: `.welcome-room`'s `rgba(255,255,255,0.45)` measured **4.52:1 on `body` (#0b0b0c) and 4.43:1 on `#viewer:fullscreen` (#000)**, i.e. it passed the audit everyone runs and failed the one nobody runs, because alpha text *loses* contrast as the backdrop darkens. Two sibling rules (`.welcome-state .hint`, `.welcome-rules`) were at **3.80:1 / 3.66:1** on both. The fix is the structural one this lesson asks for — opaque `#rrggbb`, so there is no compositing and the darker backdrop can only raise the ratio — and it deleted the `#viewer:fullscreen .welcome-state` colour branch that existed solely to top the alpha back up. Note the shape of the bug that *created* it: the branch made the fullscreen case look deliberately handled, which is why nobody re-derived the children's ratios on `#000`. PR #122 (ISSUE-40) — `rgba(255,255,255,0.32)` empty-state text carried over from `viewer.html:213` renders at **2.81:1** on `#0b0b0c` (needs 4.5:1); `rgba(255,255,255,0.4)` logo labels at **3.80:1**. PR #129 (ISSUE-42) — `.capture-hint` was computed **correctly** at 11.42:1 against its ancestor `.stage-frame` (`#000000`), but on the reselect-then-cancel path the capture `<video>` is still playing and occludes that ancestor; over a white slide the same `rgba(255,255,255,0.75)` renders at **1.00:1 — every pixel in the hint box sampled `#ffffff`**, i.e. the guidance disappears entirely
- **Description**: The second occurrence is the first one's harder half: the alpha *was* computed, and computed correctly — against a backdrop a sibling element occludes at render time. A contrast figure is a claim about the **composited** colour, but the arithmetic is done against whichever ancestor `background` the reviewer can see in the stylesheet, and that ancestor stops being the backdrop the moment a `<video>`, `<canvas>`, image or overlay paints between them. Worse, the wrong figure is not merely optimistic — semi-transparent text over arbitrary video content can reach 1.00:1 and vanish outright, while the contrast test that assumed the ancestor stays green. On a near-black canvas, "visually subordinate" is expressed by lowering alpha, and the values are copied from an earlier page as if they were tokens. But contrast depends on the *composited* colour and on the *rendered font size*, both of which change between pages — the source page's 0.42 dim line is compliant at 32px (large-text 3:1) and non-compliant at 20px, and a 0.32 caption that was acceptable at one size is not at another. Because the source page already shipped, the value carries an unearned presumption of compliance, and the new page's review inherits the failure instead of catching it.
- **Prevention**: Any text that can render over media the page does not control must declare its **own opaque background** rather than inherit one; then the composited backdrop is a structural fact and the contrast arithmetic is decidable. Encode that as a structural test ("every capture-state text rule declares a background with alpha 1") instead of trusting the contrast test's backdrop constant — the constant is the assumption that fails. Compute the ratio numerically for every text colour at the size it actually renders, at design time, not by eye. On `#0b0b0c`, white text needs alpha ≥ **0.45** to clear 4.5:1 — anything dimmer is only legal if the computed font size is ≥ 24px (or ≥ 18.66px bold).
- **Recommended action**: Record the AA alpha floor for the product's canvas colour in a design-system note, and treat any alpha below it as requiring an explicit large-text justification in the PR description.

## [RL-019] Shell issue installs a11y scaffolding the follow-up issue must undo

- **Category**: Accessibility
- **Frequency**: 1
- **Observed-In**: PR #122 (ISSUE-40) — `aria-live="polite"` placed on `#caption-container`, the exact node ISSUE-41's per-frame typewriter loop will mutate, despite `ux_spec` requiring the animated node be `aria-hidden` with a separate finalise-only live region
- **Description**: When a screen is split into a "layout shell" issue and a "behaviour" issue, the shell author adds ARIA attributes to demonstrate a11y diligence. The attributes are correct for a static page and wrong for the animated page that lands next sprint, so the follow-up author must *remove* them — which reads like an a11y regression in review and is therefore often skipped, leaving a live region that fires on every animation frame. The shell passes its own a11y review precisely because the failure mode does not exist yet.
- **Prevention**: When splitting a screen across issues, the shell issue owns the *final* ARIA structure, not a provisional one: if the follow-up will animate a node, the shell should already mark that node `aria-hidden` and ship the empty visually-hidden announcer the follow-up will write into. Review the shell against the *finished* screen's a11y spec, not against what the shell alone renders.
- **Recommended action**: In any issue whose Scope says "shell / layout only" for a region that a later issue will animate, add an AC naming the live-region owner explicitly, and have the shell ship the announcer element even though nothing writes to it yet.

## [RL-020] HTML escaper used for a value that lands in a JS string literal

- **Category**: Security
- **Frequency**: 1
- **Observed-In**: PR #122 (ISSUE-40) — `_render_stage_html` sends `{{ROOM_ID}}` / `{{PRIMARY_LANG}}` / `{{INITIAL_STATE}}` through `html.escape(..., quote=True)` even though all three appear **only** inside an inline `<script>`; the correct helper (`_json_for_script`) was written in the same PR but applied only to the two structured fields. Pre-existing identically in `_render_viewer_html`
- **Description**: `html.escape` looks like "the escaping is handled" and is even partially protective — escaping `<` does prevent a `</script>` breakout — so the defect survives review. But a `<script>` element is *raw text*: HTML entities are never decoded inside it. Two things follow. (1) The value is silently **corrupted**: `a"b` becomes the JS string `a&quot;b`, and that string is then used to build URLs (`/branding/{room_id}/…`, later `/stream/{room_id}`). (2) `html.escape` does not touch `\`, so a value ending in a backslash escapes the closing quote and the entire bootstrap dies with `SyntaxError: Invalid or unexpected token` — verified: `GET /stage/bs%5C` returns 200 with `typeof CONFIG === "undefined"`, no caption width, no title, and the `closed` branch never running. Reviewers rate it safe because no script executes; the real damage is data corruption plus a silent total loss of client-side behaviour.
- **Prevention**: Pick the escaper by *sink*, not by "is this user input". A value going into a `<script>` needs a JS-literal serialiser (`json.dumps` + `<`/`>`/`&` escaping); a value going into markup needs `html.escape`; a value going into an attribute needs `html.escape(quote=True)`; a value going into a URL needs percent-encoding. When a PR introduces the correct helper for one field, grep the template for *every* placeholder in the same context and convert them all in that PR — a half-applied helper is more dangerous than none, because it signals "handled".
- **Recommended action**: Emit script-context scalars as complete JS literals (`room_id: {{ROOM_ID}},` with the server producing `"…"`) rather than quoting in the template. Add a regression test that a room whose id/status contains `"` and `\` still yields a page where `typeof CONFIG !== "undefined"`.

## [RL-021] Chained `str.replace` template substitution lets an earlier value expand a later placeholder

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #122 (ISSUE-40) — `_render_stage_html` chains six `.replace()` calls; a room named `{{STAGE_CONFIG_JSON}}` renders the entire config blob into `<title>` and `<h1>`, and `{{OUTPUT_LANGS_JSON}}` renders `["ko", "en"]`. Pre-existing identically in `_render_viewer_html`
- **Description**: Sequential `str.replace` is not substitution, it is repeated rewriting: everything a previous step wrote is visible to every later step. Because room names are operator-controlled free text, an operator can make one placeholder's value appear where another was meant to go. Today this is only cosmetic — every substituted value happens to be escaped for *both* the markup and the script context, verified in a browser — but that is a coincidence of the current value set, not an invariant anyone stated. It breaks the first time a placeholder carries deliberately pre-escaped markup (a CSP nonce, an SVG blob, a `<style>` body), and the failure will look like an XSS in a component nobody changed.
- **Prevention**: Use single-pass substitution for any template whose values are not from a closed, developer-controlled set: `re.sub(r"\{\{(\w+)\}\}", lambda m: mapping[m.group(1)], template)`. The lambda form is important — a plain replacement string in `re.sub` reintroduces the problem via backslash/backreference interpretation. Cost is one line and it makes the "values cannot interfere" property structural rather than incidental.
- **Recommended action**: Convert `_render_viewer_html` and `_render_stage_html` to a single-pass `re.sub` with a lambda, and add a test rendering a room named `{{STAGE_CONFIG_JSON}}` that asserts the literal placeholder text survives in the `<h1>`.

## [RL-022] Terminal-state handlers that clear one variable and leave every entry point armed

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #127 (ISSUE-41) — the `session_end` handler stops the rAF loop and sets `currentLine = null`, but leaves `twTarget` / `twShown` / `twFinalize` stale and sets no terminal flag. One late `message` frame re-enters `setCaptionState("active")`, appends a caption line, restarts the animation loop and writes the live region — on a session that has ended and a socket that is closed. Reproduced in Chromium: the ended column resurrects with a ghost caption
- **Description**: A "stop" handler gets written against the variable the author was thinking about (the animation handle) rather than against the state machine's *reachability*. Every other entry point stays armed, so the terminal state is only terminal until the next event. Hand-rolled test stubs hide it: a `FakeEventSource` keeps dispatching after `close()` because implementing `readyState` semantics faithfully felt like scope creep, so the harness that could expose the resurrection is the reason nobody looks for it.
- **Prevention**: At implementation time, for any handler that means "this is over", add a single boolean and early-return from every event entry point, rather than clearing individual fields. Reachability is a property of the entry points, not of the fields. Review-time heuristic: for each terminal handler, list every other handler on the same object and ask what each one does if it fires next.
- **Recommended action**: When a test stub replaces a browser API that has terminal semantics (`EventSource.close`, `AbortController.abort`, `MediaStreamTrack.stop`), keep the stub permissive **and** add one explicit test that fires an event *after* the terminal call and asserts the page ignores it. A stub that faithfully refuses to dispatch would make the page's own guard untestable.


## [RL-023] Widget keys derived from user-controlled data collide and abort the whole page

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #125 (ISSUE-39) — the logo delete button used `key=f"stage_cfg_del_{room_id}_{label}_{filename}"`. Reproduced end to end in Chromium: a file that disappears out of band makes the form's own drift warning invite the admin to re-upload it; `save_asset` sees no collision so the config becomes `['logo.png', 'logo.png']`, and the next render raises `StreamlitDuplicateElementKey`. A full Python traceback with absolute server paths is painted into the admin UI and **오퍼레이터 배정 / 룸 강제 종료 / 룸별 대화 기록 all stop rendering** (measured: `"오퍼레이터 배정" in body -> False`). The state is sticky — the only control that could remove the duplicate is the button that crashes
- **Description**: A `key=` built from an identifier (filename, label, name) looks unique until the data model permits a duplicate. Streamlit then raises and kills every element *after* the offending one, so the visible symptom is unrelated features silently disappearing, not a message about keys. The blast radius is the whole page, and the failure is self-locking when the only remedy is rendered below the crash point.
- **Prevention**: Derive widget keys from a positional index **plus** the identifier (`enumerate`), never from the identifier alone, and separately make the layer that assembles the list guarantee uniqueness. Two independent defences, because either one alone leaves a reachable path.
- **Recommended action**: Grep for `key=f"` in Streamlit code and check every interpolated value for a uniqueness guarantee. Where a list is user-controlled, dedupe at assembly time and index at render time.

## [RL-024] A deferred finding "closed" by a helper its own production caller can never trigger

- **Category**: Architecture
- **Frequency**: 1
- **Observed-In**: PR #125 (ISSUE-39) — `describe_stage_config_drops` was written to discharge ISSUE-37 review finding F-5 ("warn when `normalize_stage_config` silently drops groups or assets"). It was fully unit-tested and returned `[]` for **every input its real call site could produce**, because the candidate was assembled from `Room.get_stage_config()`, which is already normalised, keyed by the three known labels. The tests passed, the review checkbox ticked, and the warning could never appear. Caught only because the reviewer ran the production shape through it rather than reading the tests
- **Description**: Hand-built fixtures prove a function's behaviour, not its reachability. When a follow-up finding is closed by a new helper, the tests naturally exercise the helper directly — which is exactly the evidence that cannot distinguish "this fires" from "this is decorative". The finding then reads as closed for the rest of the project's life.
- **Prevention**: When a PR claims to discharge a deferred finding, require one test that drives the **production call sequence** end to end and asserts the observable effect — not a unit test of the new helper. If no production input can produce the effect, the finding is discharged *structurally* and the PR must say so instead of shipping a dead mechanism.
- **Recommended action**: For each "fixes deferred finding X" claim, ask: which real caller, with which real data, produces the non-trivial result? If the answer requires constructing the input by hand, the mechanism is not wired up.

## [RL-025] Two layers enforcing "the same" cap over different sources of truth

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #125 (ISSUE-39) — `save_asset` enforces the 12-asset cap by counting **files on disk**; `validate_stage_config` enforces it by counting **references in config**. A pre-flight validation was added to prevent uploads when the text fields are invalid, which genuinely closes the largest orphan window, and was then described as total. Reproduced: config references 12, disk holds 5 -> pre-flight returns `(True, '')` -> 3 uploads accepted and written -> the final `validate_stage_config` rejects with "현재 15개" -> 3 orphan files on disk, config untouched
- **Description**: When two layers enforce a limit against different sources of truth, they agree only while the two sources agree. Drift is exactly the condition under which the limit matters, so the disagreement surfaces precisely when the guard is load-bearing. A partial mitigation described as complete is worse than no mitigation, because the next reader stops looking.
- **Prevention**: When two layers enforce the same cap, name which one is authoritative, and write a test for the disagreement case — not just for each layer in isolation. If a pre-flight closes some but not all windows, say which ones in the docstring.
- **Recommended action**: For any write path with validate-then-commit ordering, enumerate what has already been persisted at each early-return and either roll it back or document the residual window.

## [RL-026] Declarative markup attributes that gate runtime behaviour are invisible to every assertion

- **Category**: Testing
- **Frequency**: 1
- **Observed-In**: PR #129 (ISSUE-42) — review mutations M33/M34: deleting `<video autoplay muted playsinline>` wholesale, or just the `muted` attribute, passed all 17 e2e tests and every static guard. A related mutation (M35, never attaching the stream at all) also survived and silently defanged `test_track_ended_falls_back_to_the_title_card`, whose `srcObject === null` assertion passed because the stream had never been set
- **Description**: Boolean/declarative HTML attributes (`autoplay`, `muted`, `playsinline`, `defer`, `loading`, `rel="noopener"`) change what the browser does but appear in no JS code path. Static substring guards do not look for them because they are markup, not calls; behavioural tests do not catch them because the harness usually stubs away the very condition the attribute governs — a headless canvas `captureStream()` plays whether or not `muted` is set, so the attribute's absence is unobservable in test and catastrophic in the venue (a `<video>` without `muted` will not autoplay in Chrome). The mutation survives silently, and worse, it can make a *neighbouring* assertion vacuous: an assertion that a value is cleared passes trivially when the value was never set (RL-004's shape, reached through a markup mutation).
- **Prevention**: Assert the DOM **property**, not the attribute string and not the observable side effect: `v.muted === true`, `v.autoplay === true`, `v.playsInline === true`. Pair every "state was torn down" assertion with a gate proving the state was established first, so the teardown assertion cannot pass vacuously.
- **Recommended action**: When an issue's mutation catalogue is written (see ISSUE-43), include markup-attribute deletion as a mutation class alongside expression and branch mutations — it is the class that both existing test styles structurally miss.

## [RL-027] A control mounted inside a container that another code path destroys

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #137 (ISSUE-47) — `.stage-launch` (the issue's headline control) was mounted inside `.welcome-state`, which `appendLine()` removes wholesale on the first caption. Measured in Chromium: 1 button node before the first caption, **0 after**. The only route back was 정지 → 시작, i.e. exactly the room restart AC4 forbids. **Found independently by both the code reviewer and the UI reviewer**, and by neither the 19-mutant campaign nor the original 12 e2e tests
- **Description**: A persistent control was placed in the most convenient markup location — next to the QR block it visually resembles — without asking which code paths delete that location's ancestors. The welcome screen is by definition the *pre-session* surface, so anything mounted there is implicitly scoped to "before the session starts"; a control whose whole purpose is to be available *during* the session inherits the wrong lifetime. The failure is invisible in tests that only ever exercise the idle state, which is the state every fixture starts in.
- **Prevention**: Before mounting a control that must stay available, grep for every `.remove()` / `innerHTML =` / `replaceChildren()` whose target is an ancestor of the mount point, and mount outside all of them. For each such control, write one test that drives the app **out of** the state the fixture starts in (here: append a caption) and then asserts the control is still present *and still functional* — presence alone would have passed with a detached node.
- **Recommended action**: A mutation campaign scoped to the diff cannot find this class, because the destroying code (`appendLine`) is untouched by the change. Treat "which existing code paths can delete what I just added?" as a separate review question from "is what I added correct?".

## [RL-028] A Streamlit component payload field driven by a live UI toggle silently remounts the iframe

- **Category**: Architecture
- **Frequency**: 1
- **Observed-In**: PR #137 (ISSUE-47) — adding `display_mode` to the `{{BOOTSTRAP_JSON}}` payload means toggling the sidebar radio changes the string Streamlit passes as the component iframe's `srcdoc`, which reloads the document and destroys in-iframe session state (`RTCPeerConnection`, mic `MediaStream`, the WS client, the caption scrollback). Measured on Streamlit 1.48.1: srcdoc change → reload; identical rerun → no reload. Mitigated by disabling the radio while `action in ("start","starting")`
- **Description**: `st.components.v1.html` renders its argument via `srcdoc`, so the component's document lifetime is coupled to the *byte value* of the payload. Any BOOT field that can change while a session is running is therefore a hidden teardown trigger. The room selectbox had this property already, but changing rooms *means* changing sessions, so the disruption matches intent; a control advertised as presentation-only does not, and its own help text ("두 모드가 같습니다") actively denies it.
- **Prevention**: Classify every new BOOT field as *session-scoped* (only changes at start/stop — safe) or *interaction-scoped* (can change mid-session — unsafe). Interaction-scoped state belongs in Streamlit-native chrome outside the component, or its widget must be `disabled` while the session is live. State the classification in the PR description for each new field.
- **Recommended action**: Document the "the component iframe remounts whenever the payload string changes" invariant in `docs/architecture.md` beside the component-embedding section, so it is discovered at design time rather than by a torn-down session in a venue.

## [RL-029] A deliberately fail-loud mechanism whose only caller destroys the signal

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #143 (ISSUE-48) — `operator_ui.render_component_html` chose **strict** indexing (`values[m.group(1)]`) over the lenient `.get(…, m.group(0))` used by the sibling stage renderer, and documented the choice in both the docstring and `test_unknown_placeholder_fails_loudly`: a mistyped placeholder "dies with `KeyError` instead of leaking to the page, and `app.py`'s `try/except` turns it into a generic message (RL-006)". The raise half was real and unit-tested. The hearing half was not: `app.py:491` was `except Exception:` → `st.error(...)` with **no server-side log**, while every other swallow in the same file (`[Sidebar]`, `[QR]`, `[Stage]`) and in `sse_broadcast.py` prints `{e!r}`. The same handler also folds a missing template file and a non-serialisable payload into that one sentence, so during an event the three are indistinguishable. Caught by asking what the caller does with the exception the test proves is raised
- **Description**: "Fail loudly" is a claim about a *system*, but it is tested at the *function*. `pytest.raises(KeyError)` proves the throw and says nothing about whether anyone is listening — and the catch is usually in a different file, written by a different issue, often pre-dating the PR that starts depending on it. The PR then ships a genuine safety improvement (strict over lenient) whose observable behaviour in production is identical to the lenient version it replaced: a blank screen and a generic message. The reasoning is doubly seductive because the generic client message is *correct* — RL-006 demands it — so the handler looks compliant, and the missing half (the server-side log) is invisible in both the diff and the test suite.
- **Prevention**: When a PR justifies a design choice by "it fails loudly / the caller handles it", open the caller in the same review and confirm the signal survives to somewhere a human reads. Treat "raises" and "is observable" as two separate claims needing two separate pieces of evidence. Where a repo already has a logging convention for swallowed exceptions, a bare `except Exception:` with no log is a defect regardless of which issue introduced it — grep the file for its siblings before accepting it.
- **Recommended action**: For every `except Exception` that degrades to a user-facing message, require a server-side `print`/`log` of `{e!r}` in the same commit. When a test asserts a raise, ask which production catch receives it and what it does — if the answer is "discards it", the raise is decorative and the strict/lenient choice it justified has no observable effect.

## [RL-030] A verification tool's exit-code contract asserted without checking the codebase's own import style

- **Category**: Testing
- **Frequency**: 1
- **Observed-In**: PR #142 (ISSUE-43) — `guard_mutations.py` documents "only pytest exit 1 is a kill; 2 (collection error) / 4 (usage) / 5 (no tests) are ERROR" as its false-kill defence. Measured: a mutant that introduces a `SyntaxError` in `branding_assets.py` produces exit **1**, not 2, because every kill test in this repo imports the module under test *inside the test function* (`def test_…(): import branding_assets`) rather than at module scope. The runner reported `killed by <node>` and exited 0 for a mutant that proved nothing. A `ModuleNotFoundError` variant behaved identically
- **Description**: Verification harnesses (mutation runners, fuzz oracles, contract tests) classify outcomes by an external signal — an exit code, a log line, an HTTP status. That classification is a *claim about the harness's environment*, not a property of the harness, and it silently stops holding when the environment's conventions differ from the author's mental model. Here the whole runner rests on "a broken module surfaces as a collection error", which is true for module-scope imports and false for the deferred imports this suite uses everywhere. The failure is invisible: the tool reports success, so nobody looks
- **Prevention**: For every outcome class a harness claims to distinguish, construct an input that should land in that class and *measure* which class it actually lands in — do not reason about it. Specifically for mutation runners, verify the mutant is well-formed before crediting a kill (`compile(mutated, path, "exec")` costs nothing), and reject a "kill" whose evidence is an import/syntax traceback rather than the assertion the guard is named after
- **Recommended action**: Treat "which of my classifier's branches has never been exercised by a real input?" as a mandatory review question for any tool whose output gates CI. An unexercised branch of a verdict function is where false confidence accumulates.

## [RL-031] Cleanup registered only via `atexit` does not survive SIGTERM

- **Category**: Code Quality
- **Frequency**: 1
- **Observed-In**: PR #142 (ISSUE-43) — `guard_mutations.py:316` registers `_cleanup_sandboxes` with `atexit` and the module docstring claims removal on "정상/예외/`KeyboardInterrupt`/`SystemExit` 모든 종료 경로". Measured: `kill -TERM` 3 s into a run left `$TMPDIR/guard-mutations-2gnmv01s` on disk (2.6 MB) containing a **live mutant** (`branding_assets.py:218` → `if False and candidate.is_symlink():`). SIGINT was measured clean (returncode −2, zero leftovers) because the `try/finally` in `run_catalog` runs
- **Description**: `atexit` handlers run on normal interpreter shutdown and on `SystemExit`, but the default SIGTERM disposition terminates the process without unwinding — so `atexit` is exactly the wrong last line of defence for the case that most needs one: a supervisor, a CI cancel, or a watchdog killing a long-running job. The `try/finally` that does most of the work covers the exception and `KeyboardInterrupt` paths; the `atexit` registration exists to cover the rest and does not
- **Prevention**: When a process creates external state (temp trees, lock files, spawned children), enumerate the termination signals it can actually receive in its deployment (SIGTERM from CI cancel / `docker stop` / systemd, SIGINT from a terminal) and install an explicit handler for each — `signal.signal(signal.SIGTERM, lambda *_: (cleanup(), sys.exit(143)))`. Then measure it by sending the signal, not by reading the code
- **Recommended action**: Any docstring or PR claim of the form "cleaned up on all exit paths" must name the signals it covers and the one it cannot (SIGKILL), and be backed by a probe per signal. Unqualified "all exit paths" is a claim nobody has measured.

## [RL-033] An exhaustiveness guard written as a hand-maintained list of today's names

- **Category**: Testing
- **Frequency**: 1
- **Observed-In**: PR #147 (ISSUE-49) — the welcome-screen contrast suite is built on two hardcoded tuples, `_WELCOME_TEXT_RULES` (static) and `_WELCOME_TEXT_NODES` (e2e), and nothing asks whether the stylesheet grew a rule that is in neither. Measured with mutants: adding `.welcome-note { color: rgba(255,255,255,0.4) }` to the welcome block **survives the static suite and the e2e suite** (0 failures each), and the fullscreen structural guard skips any rule whose selector does not literally contain the substring `"welcome"`, so `#viewer:fullscreen .hint { color: rgba(255,255,255,0.4) }` — a backdrop colour branch on a welcome-screen node — **survives the static guard** (the e2e catches that one). The guard's own docstring cites RL-018's Prevention ("주석이 아니라 검사된 사실로 둔다"), and it is a checked fact only for the selectors that already existed. The counter-example is in the same repo and the same lesson family: `tests/test_viewer_page.py::test_opaque_background_surfaces_are_the_enumerated_set` (ISSUE-45) walks every rule in the stylesheet, diffs the discovered set against `_KNOWN_BACKGROUNDS`, and **fails when the file grows a new surface**, forcing the audit to reopen
- **Description**: A guard exists to stop a class of regression, but is implemented by iterating a literal list of the instances that exist on the day it is written. It therefore catches *changes to today's things* and is blind to *tomorrow's things* — which is the direction the regression actually arrives from, since nobody edits a compliant colour but everyone adds a new element. The list also reads as diligence in review: it is explicit, it is complete at the moment of writing, and each entry is individually well asserted. The failure only becomes visible under a mutation that **adds** rather than **modifies**, and mutation batteries are usually written by enumerating the same list, so the batch reproduces the blind spot it should expose. The substring form (`if "welcome" not in selector`) is the same defect wearing a regex: it encodes the naming convention of the current selectors as if it were a structural property
- **Prevention**: Derive the set, then assert the set. Scan the artefact (stylesheet, module, route table) for everything matching the *category*, compare the discovered set to the enumerated one, and fail on additions with a message that says why the audit must reopen — then feed the discovered set into the per-item parametrization so a new member is checked, not merely announced. When writing the mutation battery, require at least one mutant that **adds a new member** and one that **adds a second declaration to an existing member**; a battery made only of "change value X" mutants cannot find this
- **Recommended action**: In review, for every guard phrased as "all X are Y", ask where the list of X comes from. If it is a literal in the test file, the guard is a snapshot, not an invariant — either derive it or state the limitation in the docstring so the next author does not over-trust it.

## [RL-034] A mutation run silently re-uses a stale `.pyc` when the mutant is the same size

- **Category**: Testing
- **Frequency**: 1
- **Observed-In**: PR #147 (ISSUE-49) code review — the reviewer's own mutation harness. Mutating the test's oracle `assert ratio >= 4.5` to `>= 3.0` changes **zero bytes of file length**; restoring the original within the same wall-clock second left the source's `(mtime_seconds, size)` identical to the header of the pytest-rewritten `__pycache__/test_webrtc_stage_launch.cpython-311-pytest-9.0.2.pyc`, so CPython judged the cache valid and every subsequent run executed the **weakened** bytecode. Symptom: `_welcome_text_colour` returned `(255,255,255,0.4)` and `_contrast_ratio` returned `3.80` when called directly, while the test asserting `>= 4.5` on those exact values **passed** — an impossible result that was only resolved by `struct.unpack`-ing the pyc header (`pyc_mtime 1788110989 == src_mtime`, `size 25964 == 25964`). Three mutants were mis-scored as SURVIVED/KILLED before the whole battery was re-run with `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider` and a `__pycache__` purge between mutants
- **Description**: Python's default bytecode invalidation is `(source mtime in whole seconds, source size)`. Mutation testing violates both assumptions at once: a good mutant is a *minimal* edit, so the size very often does not change (`4.5`→`3.0`, `>=`→`>`, `and`→`or`, `True`→`Fals`… ), and an automated harness writes-runs-restores in far under a second. The result is not a crash but a **silent authority inversion**: the harness reports on bytecode that no longer corresponds to the file, and because the mutation was applied to the *oracle*, the mis-scoring makes the suite look weaker or stronger than it is. This is especially dangerous in a repo whose review culture is mutation-driven, because the corrupted numbers are exactly the numbers that get pasted into the PR description
- **Prevention**: Any harness that edits a `.py` file and re-invokes pytest in-place must disable bytecode caching (`PYTHONDONTWRITEBYTECODE=1`) or purge `__pycache__` between mutants — or run the mutant in a fresh sandbox copy, which is what `guard_mutations.py` (ISSUE-43) already does and why it is immune. Cross-check the result with `hash`-based invalidation (`py_compile.compile(..., invalidation_mode=CHECKED_HASH)`) if caching cannot be disabled. Treat "the arithmetic says FAIL but the test says PASS" as a tooling fault until the pyc header is read, not as a mystery in the test
- **Recommended action**: When a review reports mutation results for `.py` files, the report should state how bytecode caching was neutralised. A mutation table with no such statement and with same-length mutants should be re-run before it is trusted.
