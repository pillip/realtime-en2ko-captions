# Review Lessons

Preventable patterns identified during code reviews. Each entry includes when the pattern could have been caught earlier.

---

## [RL-001] Copy-paste testing instead of extracting importable modules

- **Category**: Architecture
- **Frequency**: 2
- **Observed-In**: PR #8, PR #10 (partially addressed -- modules extracted but tests not yet added)
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
- **Frequency**: 2
- **Observed-In**: PR #8, PR #12 (E2E fullscreen tests silently pass when fullscreen is unavailable; string-matching unit tests cannot detect structural correctness)
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
