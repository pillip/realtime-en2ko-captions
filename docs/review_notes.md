# Review Notes -- PR #57 (ISSUE-36: webrtc.html viewport responsive height)

**Reviewer**: Claude Opus 4.6 (automated)
**Date**: 2026-05-06
**PR Size**: +105 -6 lines across 3 files (2 HTML, 1 test)
**Test Results**: 9/9 passing
**Confidence Rating**: High -- all changed files, existing layout files, and the full issue AC were reviewed.

---

## Summary

PR #57 fixes the viewport-height mismatch in the caption iframe. Previously, the iframe was fixed at 900px (via `st.components.v1.html(height=900)`) and the body used `90vh` (= 810px of 900px), ignoring the actual browser viewport. This PR:

1. Adds `height: 100vh !important` to `.main iframe` in `scroll_lock.html` (overrides inline 900px)
2. Changes `body` height from `90vh` to `100%` in `webrtc.html` (3 occurrences: main rule + 2 media queries)
3. Adds 9 regression tests validating the CSS changes

The approach is sound: the parent page's `scroll_lock.html` (where `vh` refers to the browser viewport) sets the iframe to `100vh`, and the iframe content uses `100%` (which correctly resolves to the iframe's dimensions rather than defining its own viewport unit).

---

## Findings

### Code Review

### [Info] CR-1: Body `margin-top: 5%` causes slight overflow with `height: 100%`

- **File**: `components/webrtc.html:13` (`margin: 5% 0 0 0`)
- **Issue**: The body has `height: 100%` and `margin-top: 5%`, so the total space occupied is 105% of the containing block. The `overflow: hidden !important` on body clips the overflow, so content is not visibly broken. However, the effective visible area for body content is 95% of the viewport, not 100%. This was the same behavior before the change (90vh + 5% margin = also overflow), so this is not a regression.
- **Blocking**: No. Pre-existing behavior, not introduced by this PR.
- **Suggestion**: Consider using `padding-top` instead of `margin-top` (which stays inside the content box) or adjusting height to `calc(100% - 5%)` in a follow-up.

### [Medium] CR-2: Mobile Safari `100vh` includes address bar, causing content clipping

- **File**: `components/scroll_lock.html:46` (`height: 100vh !important`)
- **Issue**: On iOS Safari, `100vh` includes the space behind the browser's address/navigation bar. When the address bar is visible (e.g., after page load, before scrolling), `100vh` is taller than the visible viewport, causing the bottom ~70-90px of the iframe to be hidden behind the browser chrome. This is the well-known "iOS 100vh bug." The fix is to use `100dvh` (dynamic viewport height, supported since iOS 15.4 / Safari 15.4, 2022) with a `100vh` fallback.
- **Blocking**: No -- the target browsers listed in CLAUDE.md are "Chrome/Edge/Safari latest," and this is an improvement over the previous fixed-900px approach even on mobile Safari. However, this is a meaningful UX issue for mobile users.
- **Suggested fix** (follow-up):
  ```css
  .main iframe {
      height: 100vh !important;  /* fallback */
      height: 100dvh !important; /* modern browsers */
  }
  ```
  Apply the same pattern to all `100vh` usages in `scroll_lock.html`.

### [Low] CR-3: Test regex patterns are fragile against whitespace/formatting changes

- **File**: `tests/test_viewport_responsive.py:55-56, 63-64`
- **Issue**: The media query tests use `\n\s*\n` as the end delimiter for the media query block. If someone reformats the CSS (e.g., removes the blank line between rules, or a minifier strips whitespace), the regex will fail to match and the test will error with "media query not found" rather than testing the actual content. The `_get_body_css()` helper uses `[^}]*}` which is more robust.
- **Mitigating factors**: These are static HTML files unlikely to be minified, and the error messages are descriptive enough to debug.
- **Blocking**: No.
- **Suggestion**: Use a more robust end delimiter, such as matching the closing brace at the correct nesting level, or extract the full media query block using a brace-counting approach.

### [Low] CR-4: Tests read HTML files from disk on every test method invocation

- **File**: `tests/test_viewport_responsive.py:15-21`
- **Issue**: Each of the 9 tests calls `_read_webrtc()` or `_read_scroll_lock()`, performing file I/O on every invocation. For a file this size (~25K tokens), this is negligible in absolute terms but could be avoided by reading once at class or module level.
- **Blocking**: No. Performance impact is trivial.
- **Suggestion**: Use a `@pytest.fixture(scope="module")` or class-level `setup_class` to read the file once.

### [Info] CR-5: `app.py:209` still has `height=900` -- intentional per issue scope

- **File**: `app.py:209` (`st.components.v1.html(html_content, height=900, scrolling=False)`)
- **Issue**: The `height=900` parameter remains. Per the issue scope: "app.py의 height=900 파라미터 제거 (CSS가 덮어쓰므로 폴백으로 유지)." This is intentional -- `scroll_lock.html`'s `!important` overrides Streamlit's inline `style="height: 900px"`, but keeping the value ensures Streamlit's component API receives a valid height (some Streamlit versions require a numeric height).
- **Blocking**: No. This is correctly scoped out.

---

### Security Findings

No security issues identified.

- The CSS changes are in static HTML files served from the server filesystem. No user input reaches these files.
- `scroll_lock.html` is injected via `st.markdown(unsafe_allow_html=True)`, but its content is a static file read from disk, not constructed from user input.
- No new JavaScript is introduced.
- No authentication, authorization, or data handling changes.

---

## AC Verification

| AC | Status | Notes |
|----|--------|-------|
| 1080p display fills viewport | Pass | `100vh` on iframe + `100%` on body = full viewport coverage |
| 768px laptop no clipping | Pass | Media query updated to `100%`, status chip/button use `position: fixed` with bottom offsets |
| 4K monitor no empty space | Pass | `100vh` scales to any viewport height |
| Fullscreen round-trip unchanged | Pass | `#viewer:fullscreen` CSS untouched, 2 regression tests confirm `100vh` remains |
| FAB/settings positions correct | Pass | All positioned elements use `fixed`/`absolute` with `top`/`right`/`bottom` offsets, unaffected by body height |

---

## Verdict

**APPROVE**

The PR is clean, minimal, and correctly solves the stated problem. All 9 tests pass. The CSS approach (parent sets iframe to `100vh`, child uses `100%`) is the correct way to handle iframe-within-viewport sizing. No blocking issues found.

**Non-blocking suggestions for follow-up:**
- CR-2: Add `100dvh` for mobile Safari compatibility
- CR-3: Harden media query test regex patterns

---

## Follow-ups

1. **[Follow-up] Add `100dvh` fallback for iOS Safari** -- Apply `height: 100dvh !important` as a progressive enhancement after `height: 100vh !important` in `scroll_lock.html`. This eliminates the iOS Safari address-bar-overlap issue for all `100vh` usages. Estimated: 15 minutes. (CR-2)

2. **[Follow-up] Address `body margin-top: 5%` + `height: 100%` interaction** -- Consider whether the 5% top margin should be converted to `padding-top` or the height adjusted to `calc(100% - 5%)` to avoid the silent overflow clip. Estimated: 10 minutes. (CR-1)
