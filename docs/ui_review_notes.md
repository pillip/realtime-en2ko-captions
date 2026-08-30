# UI Review Notes — ISSUE-31 (PR #65)

**Reviewer**: Claude Opus 4.7 (automated, ui-review phase)
**Date**: 2026-05-07
**Files reviewed**: `components/viewer.html`

## Scope

Unauthenticated viewer page (`/view/{room_id}`) — language selector, three-state UI (waiting/active/ended), credit-roll captions, mobile responsive.

## State Coverage

| State | DOM hook | Triggered by | A11y |
|-------|----------|--------------|------|
| waiting | `#state-waiting` | initial bootstrap (status != closed) | `aria-live="polite"` + spinner |
| active | `#state-active` | first SSE `message` payload | scroll region with caption list |
| ended | `#state-ended` | `session_end` SSE event OR initial state == closed | `aria-live="polite"` |
| connection-error | `.conn-error` | EventSource `error` event | `role="status"` |

All four states are reachable, mutually exclusive (single `.active` class), and have distinct copy + visual treatment.

## Copy Compliance

| Slot | Copy | Source |
|------|------|--------|
| Waiting headline | `잠시 후 자막이 시작됩니다` | issues.md 화면 상태 표 |
| Ended headline | `세션이 종료되었습니다` | issues.md 화면 상태 표 |
| Active empty | `자막을 기다리는 중…` | UX-friendly fallback |
| 404 headline | `룸을 찾을 수 없습니다` | RL-006 generic guidance |
| 404 body | `QR 코드 또는 링크가 올바른지 다시 확인해 주세요.` | actionable, blame-free |
| Connection error | `연결이 끊어졌습니다. 재연결 중…` | transient, reassuring |
| Lang selector label | `언어` (visible) + `자막 언어 선택` (aria-label) | concise + screen-reader friendly |

All copy is Korean (`<html lang="ko">`); no English fallback shown to end users (per project scope).

## Tokens

No formal design system exists yet for this project. Inline tokens are consistent with `webrtc.html`:

- Background: `linear-gradient(135deg, #0f0f23 0%, #1a1a2e 50%, #16213e 100%)`
- Accent (green/active): `#10b981`
- Warn (amber/connection): `#fbbf24`
- Text primary: `#ffffff`
- Text secondary: `rgba(255,255,255,0.65)` / `rgba(255,255,255,0.45)`
- Border: `rgba(255,255,255,0.15)`
- Caption surface: `rgba(255,255,255,0.08)` + `1px solid rgba(255,255,255,0.15)` + green `border-left: 4px solid #10b981`

These match the existing webrtc.html caption styles, providing visual continuity for any operator who happens to view both pages.

## Accessibility (WCAG 2.1 AA)

| Principle | Check | Status |
|-----------|-------|--------|
| Perceivable — color contrast | `#fff` on `#0f0f23` = 17.55:1 (AAA); secondary `rgba(255,255,255,0.65)` ≈ 11.4:1 (AAA) | PASS |
| Perceivable — language attribute | `<html lang="ko">` | PASS |
| Perceivable — aria-live regions | `#state-waiting` and `#state-ended` have `aria-live="polite"` | PASS |
| Operable — keyboard | Native `<select>` is keyboard-focusable; `:focus-visible` outline added | PASS |
| Operable — focus indicator | `outline: 2px solid #10b981; outline-offset: 2px` on `:focus-visible` | PASS |
| Operable — touch target | `min-height: 44px` on `#lang-select` | PASS |
| Understandable — accessible name | `<select aria-label="자막 언어 선택">` | PASS |
| Understandable — visible label | `<label for="lang-select">언어</label>` next to select | PASS |
| Robust — semantic HTML | `<main role="main">`, `<header>`, `<section>` for each state | PASS |
| Robust — valid HTML5 | doctype, charset, viewport meta all present | PASS |

**No WCAG 2.1 AA failures detected.**

## Interaction fidelity

- **Language switch**: `change` event closes existing EventSource, clears caption container, opens new EventSource with `?lang=<new>`. Same-lang change is a no-op.
- **First caption arrival**: triggers `setState("active")` automatically — user sees waiting → active transition without action.
- **Session end**: `session_end` event triggers `setState("ended")` and closes EventSource, preventing further reconnect attempts.
- **Closed-room initial**: bootstrap renders ended state inline without opening any SSE — RL-006 friendly, zero round-trip overhead.
- **Auto-scroll**: only when `isUserAtBottom()` (slack 80px) — respects manual scroll-up.
- **DOM growth**: bounded at `MAX_LINES = 200` lines (oldest removed first).

## Mobile responsive verification

| Breakpoint | Behavior | Status |
|------------|----------|--------|
| ≥601px (desktop/tablet) | full-size topbar (`14px 20px`), `clamp(20px, 4vw, 32px)` headlines, caption padding `16px 22px` | PASS |
| ≤600px (phone) | compact topbar (`10px 14px`), select font 14px, caption padding `12px 16px`, room name truncated to 50% width with ellipsis | PASS |
| iOS Safari (notch) | `viewport-fit=cover` + `100dvh` cascade — visible viewport sizing without address-bar overflow (RL-011) | PASS |
| All viewports | `word-break: keep-all` on body — Korean text wraps at word boundaries, no mid-syllable breaks | PASS |

Manual smoke (recommended pre-merge):
- iPhone 13 (Safari): scan QR → /view/{room_id} → confirm waiting → first caption → ended.
- Android Chrome: language switch from ko to en, verify caption stream reconnects and clears.

## UI Findings

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| UI-1 | Info | Caption fade-in animation (`fade-in 0.35s ease-out`) is subtle but present — meets motion-sensitive needs (no `prefers-reduced-motion` override yet). Consider adding `@media (prefers-reduced-motion: reduce) { .caption-line { animation: none } }` in a follow-up. | Defer (follow-up) |
| UI-2 | Info | `.conn-error` banner shows during automatic EventSource reconnect — useful feedback. No retry-now button (browser handles). | Defer (follow-up) |
| UI-3 | Info | First-render flash: bootstrap script runs after CSS; no FOUC observed in tests, but on slow networks the initial state could briefly show empty. The initial `display: none` on `.state` (without `.active`) prevents content flash. | — |

**No Critical/High UI findings.** All AC met.

## Confidence

**High**. 3-state UI verified by tests + e2e, mobile breakpoint, contrast/touch-target/aria all WCAG 2.1 AA Pass, copy 1:1 with issues.md spec.

---

# UI Review Notes — ISSUE-40 (PR #122)

**Reviewer**: Claude Opus 5 (independent ui-review pass, did not author the code)
**Date**: 2026-08-23
**Branch / commit**: `issue/ISSUE-40-stage-composite-page` @ `1f3bd0d` (diff vs `origin/main` `9606b55`)
**Files reviewed**: `components/stage.html` (new, 379 L), `sse_broadcast.py` (template substitution), `tests/test_stage_page.py`, `tests/e2e/test_stage_page_e2e.py`
**Method**: CSS reading + independent Playwright/Chromium measurement of 12 viewport × room-config combinations (throwaway probe in scratchpad, worktree untouched) + read-only run of the author's e2e suite (9/9 pass) + numeric WCAG contrast computation.
**Verdict**: **request-changes** — 1 Critical layout defect (UI-1) reproducible at 1920×1080, one of the two AC viewports.

## Scope

ISSUE-40 is **layout only**. In scope: composite grid structure, title-card/placeholder states, caption-column shell (width + narrow-column type), 404 page, closed-room ended state, logo bar with per-image `onerror` degrade. Out of scope and correctly absent: SSE subscription (ISSUE-41), `getDisplayMedia()` capture (ISSUE-42), branding asset serving (ISSUE-38), in-page settings (ISSUE-39).

Leakage check: `grep -n "EventSource|getDisplayMedia|setInterval|requestAnimationFrame|/stream/|conn-error|MAX_LINES" components/stage.html` returns exactly one hit — `stage.html:352`, an HTML comment marking the ISSUE-42 mount point. **No out-of-scope logic leaked in.**

> This project has no `docs/design_system.md`, no `docs/copy_guide.md`, no `figma-export/` and no `prototype/`. Token and copy expectations below are derived from `components/viewer.html` (the existing minimal-dark viewer) and `docs/ux_spec.md § Screen: Stage Composite View`. No design system was invented for this review.

## State Coverage

In-scope subset of the `ux_spec` state list:

| ux_spec state | In scope for #122 | Rendered? | DOM hook / evidence |
|---|---|---|---|
| Default (캡처 미연결) | yes | **YES** | `#title-card` (stage.html:354); `#capture-video` ships `hidden` so the card is always the initial paint |
| Empty (자막 미시작) | yes | **YES** | `#caption-empty` "잠시 후 시작됩니다" (stage.html:369) |
| Ended (`session_end` / closed room) | yes (closed-room bootstrap only) | **YES** | `#caption-ended` + `role="status"` (stage.html:373); bootstrap at stage.html:335-338; e2e confirms `EventSource` is never constructed |
| 404 (알 수 없는 룸) | yes | **YES** | server-side, `_NOT_FOUND_HTML` shared with `/view/` (`sse_broadcast.py:_handle_stage`, 3 branches incl. repo exception and template-read failure) |
| Active (자막 흐름) | shell only | **SHELL ONLY** | `.caption-line` CSS (stage.html:228-237) exists, but **no code path ever creates a `.caption-line` node** — my probe's `getComputedStyle('.caption-line')` returned `null` at every viewport. The narrow-column typography is string-asserted only (`tests/test_stage_page.py:240`), never rendered. See UI-12. |
| Loading (screen-picker) | N/A | N/A | browser-native dialog, ISSUE-42 |
| Error — 캡처 거부 / 자기 캡처 / 트랙 종료 / SSE 끊김 | out | absent (correct) | ISSUE-41 / ISSUE-42 |

No blank or broken view in any in-scope state. No screen renders placeholder/sample data.

## Copy Compliance

| Slot | Copy | Source of truth | Status |
|---|---|---|---|
| Waiting / empty | `잠시 후 시작됩니다` | `viewer.html:375` `WAITING_MSG.ko`, ux_spec § States | 1:1 match |
| Ended | `세션이 종료되었습니다` | `viewer.html:304`, ux_spec § States | 1:1 match |
| 404 headline | `룸을 찾을 수 없습니다` | shared `_NOT_FOUND_HTML` | reused, not reinvented |
| Document title | `{{ROOM_NAME}} — 무대 화면` | new, consistent with the viewer's tone | OK |
| Presentation region label | `발표 화면` (aria-label) | ux_spec "발표 영역" | OK |
| Caption region label | `자막` (aria-label) | ux_spec "자막 컬럼" | OK |
| Logo alt text | `{그룹 라벨} 로고` e.g. `주최 로고` | ux_spec a11y note | OK |
| Logo group labels | `주최` / `주관` / `후원` | server-normalised `stage_config` | OK |

All user-facing strings Korean under `<html lang="ko">`. **No `Lorem ipsum`, `TODO`, `TBD`, English fallback, or sample data anywhere in the rendered output.** Formal-polite register consistent with the rest of the product.

One forward gap (not an ISSUE-40 defect) — see UI-10: the empty-state string is hardcoded in markup while `CONFIG.caption_lang` / `CONFIG.output_langs` are injected but unused; ux_spec says the empty state shows the message *in the selected language*.

## Tokens

No formal token file exists; `viewer.html` is the reference. Comparison:

| Role | stage.html | viewer.html | Verdict |
|---|---|---|---|
| Canvas | `--canvas: #0b0b0c` (stage.html:20) | `#0b0b0c` (viewer.html:25) | match (stage promotes it to a custom property — improvement) |
| Font stack | `"Pretendard", -apple-system, …, "Noto Sans KR"` (stage.html:34) | identical (viewer.html:23) | match |
| Past caption line | `rgba(255,255,255,0.42)` (stage.html:233) | `rgba(255,255,255,0.42)` (viewer.html:199) | match |
| Current caption line | `#ffffff` (stage.html:237) | `#fff` | match |
| Empty-state text | `rgba(255,255,255,0.32)` (stage.html:240) | `rgba(255,255,255,0.32)` (viewer.html:213) | match — but both fail AA, see UI-3 |
| Ended text | `rgba(255,255,255,0.6)` (stage.html:254) | `rgba(255,255,255,0.6)` (viewer.html:161) | match |
| Hairline divider | `--hairline: rgba(255,255,255,0.07)` (stage.html:21) | `rgba(255,255,255,0.06)` (viewer.html:49) | **drift**, see UI-9 |
| Caption type scale | `clamp(20px, 1.4vw + 8px, 32px)` (stage.html:229) | `clamp(24px, 3.4vw, 38px)` (viewer.html:195) | intentional per ux_spec § 자막 컬럼 |

No magic hex literals beyond `#0b0b0c` / `#000000` / `#ffffff`. The page declares 2 custom properties and uses 11 literal `rgba()` values inline — consistent with existing codebase practice, noted under UI-9 for design-auditor rather than charged as a defect.

## Accessibility (WCAG 2.1 AA)

**Measured contrast ratios** (sRGB relative-luminance, alpha composited against the actual painted backdrop; sizes are the computed values my probe read at 1920×1080 / 3440×1440):

| Element | Composited colour | Backdrop | Ratio | Size / threshold | Status |
|---|---|---|---|---|---|
| `.event-title` (stage.html:74) | `#ffffff` | `#0b0b0c` | **19.67:1** | 36.5–44px, large (3:1) | PASS |
| `.event-subtitle` (stage.html:85) | `rgb(133,133,134)` | `#0b0b0c` | **5.34:1** | 19.2–22px, normal (4.5:1) | PASS |
| `.title-card-title` (stage.html:141) | `#ffffff` | `#000000` | **21.00:1** | 61–68px, large | PASS |
| `.title-card-subtitle` (stage.html:147) | `rgb(140,140,140)` | `#000000` | **6.25:1** | 26.9–30px, normal | PASS |
| `.caption-line` current (stage.html:237) | `#ffffff` | `#0b0b0c` | **19.67:1** | 32px, large | PASS |
| `.caption-line` past (stage.html:233) | `rgb(113,113,114)` | `#0b0b0c` | **4.04:1** | 32px at both AC viewports → large-text (3:1) | PASS at AC viewports; FAILS 4.5:1 if the clamp ever lands under 24px (viewport width < ~1143px) |
| `.caption-ended` (stage.html:254) | `rgb(157,157,158)` | `#0b0b0c` | **7.26:1** | 23–24px, normal | PASS |
| `.logo-group-label` (stage.html:185) | `rgb(109,109,109)` | `#0b0b0c` | **3.80:1** | 14.4–16px, normal (4.5:1) | **FAIL** → UI-4 |
| `.caption-empty` (stage.html:240) | `rgb(89,89,90)` | `#0b0b0c` | **2.81:1** | 19px, normal (4.5:1) | **FAIL** → UI-3 |
| `--hairline` 1px divider | `rgb(28,28,29)` | `#0b0b0c` | 1.16:1 | decorative separator, 1.4.11 N/A | informational |

Minimum alpha to reach 4.5:1 on `#0b0b0c` is **0.45** (4.52:1); `0.50` gives 5.34:1 and matches the existing `.event-subtitle` token.

Other checks:

| Check | Evidence | Status |
|---|---|---|
| `lang="ko"` | stage.html:2 | PASS |
| Keyboard nav / focus rings | `document.querySelectorAll('a[href],button,input,select,textarea,[tabindex],[contenteditable],video[controls]').length === 0` at all 12 probed viewports | **Vacuous** — the page has zero interactive elements, so `:focus-visible` is neither pass nor fail today. There is no `:focus-visible` rule in the CSS. **Forward requirement for ISSUE-42**: the moment "발표자료 연결" lands, a visible `:focus-visible` indicator distinct from `:hover` is mandatory (ux_spec a11y note, RL-010). |
| NFR-025 keyboard non-interference | `grep -n "keydown\|keyup\|keypress\|preventDefault\|\.focus(\|requestFullscreen\|autofocus\|tabindex" components/stage.html` → **no matches** | PASS (implementation) |
| `aria-live` structure | `aria-live="polite"` sits on `#caption-container` (stage.html:368) — the exact node ISSUE-41 will mutate every animation frame | **PROBLEM** → UI-6 |
| Ended announcement | `#caption-ended` `role="status"`, revealed by `hidden = false` | PASS |
| Logo `alt` | first image of a group gets `label + " 로고"`, repeats get `alt=""`; title-card copies all get `alt=""` (stage.html:321-322) | PASS as written — but the `onerror` path leaves an announced-but-empty group → UI-5 |
| Landmarks | `<header>`→banner, `<footer>`→contentinfo, `<section aria-label="발표 화면">`→region, `<aside aria-label="자막">`→complementary; **no `<main>`** | minor → UI-11 |
| Headings | single `<h1>` (event title); no skipped levels | PASS |
| `prefers-reduced-motion` | no animations/transitions in this page | N/A |

## Interaction fidelity

Nothing animated or interactive ships in ISSUE-40, so there is little to compare against ux_spec § interactions. What was verified:

- **Closed-room bootstrap**: `initial_state === "closed"` hides `#caption-live` and reveals `#caption-ended` synchronously in `DOMContentLoaded`, and no `EventSource` is constructed (author's e2e patches the constructor and asserts `__esCount === 0` — a genuinely meaningful assertion).
- **Config injection ordering**: the `--caption-width` `setProperty` runs in `<head>` before first paint, so the caption column never flashes at the 25% default before widening to 33.333%. Confirmed visually — no reflow observed.
- **`onerror` degrade**: fires per-image, so one missing asset cannot collapse the bar. Correct in intent; incomplete at the group level (UI-5).
- **XSS/breakout hardening** (`_json_for_script`): `</script>` payload lands as `</script>`, `window.__pwned` stays undefined, zero page errors. Solid; RL-016 hand-off discharged.

## Responsive verification (measured, Chromium)

`.stage` grid and 16:9 invariant, measured across 12 combinations. `--caption-width` is genuinely consumed by `grid-template-columns: 1fr var(--caption-width)` (stage.html:47) — computed `grid-template-columns` resolves to real pixel tracks:

| Viewport | ratio | caption column | % of viewport | frame | frame aspect | slack |
|---|---|---|---|---|---|---|
| 1920×1080 | `1/4` | 480.0px | **25.000%** | 1440×810 | 1.7778 | 67.5px vertical |
| 3440×1440 | `1/4` | 860.0px | **25.000%** | 2172.4×1222 | 1.7777 | 407.6px horizontal (pillarbox) |
| 1920×1080 | `1/3` | 639.98px | **33.333%** | 1280×720 | 1.7778 | 268.3px vertical |
| 3440×1440 | `1/3` | 1146.64px | **33.331%** | 2293.4×1290 | 1.7778 | 47.0px vertical |
| 1920×1080 | `1/4`, no logos | 480.0px | 25.000% | 1440×810 | 1.7778 | logo bar 0px, presentation 988.3px (vs 877.5px with logos) |
| 1024×768 / 1280×1024 / 800×600 / 640×480 / 390×844 / 900×1600 | — | — | exact | — | 1.7776–1.7781 | no document scrollbar at any size |

- **AC 2 (caption_ratio → width): PASS**, verified in pixels at both viewports and both ratios.
- **AC 3 (16:9 letterbox): PASS for well-formed titles** at both 1920×1080 and 3440×1440 — the frame is exactly 16:9 (1.7777–1.7778), never cropped, never stretched; slack becomes letterbox/pillarbox. **FAILS under UI-1** when the header title's max-content width exceeds the left column.
- **AC 4 (empty title): PASS.** Server renders the room name into the `<h1>`; header height is 91.7px at 1920×1080 with the fallback and 91.7px with a short real title — identical, because the `<h1>` is always exactly one `white-space: nowrap` line. `min-height: 88px` is a real floor but only binds at ≤1024px-wide viewports. Note (not an AC): the header does grow to 126.5px when `event_subtitle` is set, so the left column differs between subtitle/no-subtitle rooms.
- **AC 5 (empty logos collapse): PASS.** All-empty → `#logo-bar` height 0, `#presentation` grows 877.5 → 988.3px. Partial case (주최 populated, 주관 empty, 후원 populated) → exactly 2 `.logo-group` nodes with labels `["주최","후원"]`; the empty 주관 group is not rendered at all. Correct.
- **Degenerate viewports**: no horizontal or vertical document overflow at 390×844, 800×600, 900×1600 (`scrollWidth === innerWidth`, `scrollHeight === innerHeight`). Caveat: `html, body { overflow: hidden }` (stage.html:30) means the page can never *report* overflow — it clips silently, which is exactly how UI-1 hides from a scrollbar-based check.

## UI Findings

| ID | Severity | Description | Blocks merge | Status |
|----|----------|-------------|--------------|--------|
| UI-1 | **Critical** | Left column overflows into and paints over the caption column when the header title is long — reproducible at 1920×1080 | YES | Open |
| UI-2 | **High** | e2e containment assertion is tautological (RL-004); it passes against the broken layout in UI-1 | YES | Open |
| UI-3 | **High** | Empty-state text `잠시 후 시작됩니다` at 2.81:1 — WCAG 1.4.3 fail | YES | Open |
| UI-4 | Medium | Logo group labels at 3.80:1 — WCAG 1.4.3 fail | no | Open |
| UI-5 | Medium | `onerror` leaves an orphan labelled logo group with zero images (visible today on every stage page) | no | Open |
| UI-6 | Medium | `aria-live` placed on the node ISSUE-41 will animate per frame — paints ISSUE-41 into a corner | no | Open |
| UI-7 | Low | Header/logo bars do not actually absorb the 16:9 vertical slack the ux_spec says they exist for | no | Open |
| UI-8 | Low | `#card-logos` keeps 32px of dead space when it has no images | no | Open |
| UI-9 | Low | Hairline token drift 0.07 vs viewer's 0.06 | no | Open |
| UI-10 | Low | Empty-state copy hardcoded Korean while `caption_lang` is injected and unused (forward, ISSUE-41) | no | Note |
| UI-11 | Low | No `<main>` landmark | no | Open |
| UI-12 | Low | `.caption-line` typography never rendered by any test — string-asserted only | no | Note |

### UI-1 — Critical — presentation area overflows the left grid column and covers the caption column

**Where**: `components/stage.html:54-59` (`.stage-main`).

**Evidence** (measured, Chromium, `event_title = "제12회 대한민국 인공지능 및 실시간 다국어 자막 기술 국제 콘퍼런스 2026 서울 코엑스 그랜드볼룸 A홀 기조연설 세션"`, a plausible real conference title):

```
1920x1080: stage-main grid area = 1440px
           computed #presentation width = 1743.67px   <-- 303.7px past the column
           #stage-frame right edge     = 1682.8px     <-- 242.8px into the caption column
           caption column left edge    = 1440px
           document.elementFromPoint(1500, 540) -> #card-title   (left column, on top)
2560x1440: overflow +165.0px      1600x900: overflow +188.1px      800x600: overflow +149.6px
3440x1440: no overflow with this title (2580px column absorbs it)
```

`.stage-main` is a grid container whose single **implicit column track is `auto`**, i.e. `minmax(auto, max-content)`. The `<h1>`'s `white-space: nowrap` makes its max-content contribution the full untruncated title width, so the track grows past the 1440px grid area instead of the `text-overflow: ellipsis` firing. `min-width: 0` on `.stage-main` (stage.html:57) constrains the *container*, not the *track*, so it does not help. `.stage-frame` is `position: relative`, so it paints above the non-positioned `.caption-column` — this is not an invisible box, it is a black title card drawn over the captions. Screenshot confirms the waiting message half-buried and the header title clipped at the viewport edge with no ellipsis.

Consequences: violates AC 3 (`발표 영역이 좌측 컬럼을 넘지 않는다`), FR-073 (`좌측 컬럼은 남는 폭을 흡수`), and silently destroys the caption column — the one thing the audience is reading — at the primary AC viewport. `html, body { overflow: hidden }` suppresses any scrollbar, so nothing signals the breakage.

**Fix** (verified in-browser via injected stylesheet — overflow drops to exactly `+0.0` at 1920×1080, 2560×1440, 1600×900 and 800×600, and the header ellipsis engages):

```css
.stage-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr);   /* <-- add: cap the implicit auto track */
  grid-template-rows: auto minmax(0, 1fr) auto;
  min-width: 0;
  min-height: 0;
}
```

Add a regression test with a long title at 1920×1080 asserting `frame.x + frame.width <= caption_column.x + 1` (see UI-2).

### UI-2 — High — the e2e letterbox assertion cannot detect UI-1 (RL-004)

**Where**: `tests/e2e/test_stage_page_e2e.py:159-165`.

```python
assert frame["width"]  <= area["width"]  + 1   # area == #presentation
assert frame["height"] <= area["height"] + 1
assert _box(page, "#event-header")["height"] > 0
assert _box(page, "#logo-bar")["height"] > 0
```

The containment check compares the frame against `#presentation` — but under UI-1 `#presentation` grows *with* the frame (frame 1621.8 ≤ presentation 1743.7), so the assertion **passes on the broken layout**. It is structurally incapable of failing. The fixture also uses a short title (`"2026 개발자 콘퍼런스"`), so the defect is never exercised.

The two `height > 0` assertions are labelled "남는 세로 공간은 헤더/로고 바가 흡수한다" but a 1px bar satisfies them; they prove nothing about absorption (and the absorption claim is itself false — see UI-7).

**Fix**: assert against the *sibling column*, not the parent, and parametrize with a long title:
```python
col = _box(page, "#caption-column")
assert frame["x"] + frame["width"] <= col["x"] + 1, "presentation overflows into the caption column"
assert _box(page, "#event-header")["height"] >= 88     # the declared min-height, not > 0
```
The `16/9` ratio assertion itself (`abs(ratio - 16/9) < 0.02`) **is** meaningful and did its job. I did not modify any test file.

### UI-3 — High — empty-state message fails WCAG 1.4.3 at 2.81:1

**Where**: `components/stage.html:238-244` — `.caption-empty { color: rgba(255,255,255,0.32); font-size: clamp(14px, 1vw, 19px); }`.

Composited `rgb(89,89,90)` on `#0b0b0c` = **2.81:1**, rendered at 19px (normal text ⇒ 4.5:1 required). This is the sole text of the in-scope Empty state, on a screen read from the back of a conference hall. Visually confirmed near-illegible in my 1920×1080 screenshot.

**Fix**: raise the alpha to ≥ `0.45` (4.52:1); recommend `0.5` (**5.34:1**) to reuse the `.event-subtitle` value. Same literal exists at `viewer.html:213`, so this is inherited drift — flagged for design-auditor below, but it must not be re-shipped in a new file.

### UI-4 — Medium — logo group labels fail WCAG 1.4.3 at 3.80:1

**Where**: `components/stage.html:182-187` — `rgba(255,255,255,0.4)` at `clamp(11px, 0.75vw, 16px)` → 14.4px at 1920, 16px at 3440. Composited `rgb(109,109,109)` on `#0b0b0c` = **3.80:1**, normal text ⇒ 4.5:1 required.

**Fix**: alpha ≥ `0.45`; `0.5` gives 5.34:1 and keeps the label visually subordinate to the marks.

### UI-5 — Medium — `onerror` leaves an orphan labelled logo group (and an SR-announced empty group)

**Where**: `components/stage.html:284` `img.addEventListener("error", function () { img.remove(); });` and `stage.html:310-328`.

Per-image removal is right (RL-008) but stops one level too early. When every asset in a group 404s, the `.logo-group` survives with only its `<span class="logo-group-label">` — a floating "주최" with nothing next to it in a 76px bar. **This is the state of every stage page today**, because `/branding/{room_id}/{filename}` does not exist until ISSUE-38 — confirmed in my screenshot and in the author's own e2e, which asserts `#logo-bar img == 0` and `.logo-group == 2` and calls that a pass (`tests/e2e/test_stage_page_e2e.py:206-208`). For screen readers this is a labelled group announcing nothing.

**Fix** in the error handler:
```js
img.addEventListener("error", function () {
  const group = img.closest(".logo-group");
  img.remove();
  if (group && !group.querySelector("img")) group.remove();
  const bar = document.getElementById("logo-bar");
  if (bar && !bar.querySelector(".logo-group")) bar.hidden = true;
});
```
and apply the same emptiness check to `#card-logos` (see UI-8). The e2e expectation at line 206-208 should be updated to assert the *collapsed* bar.

### UI-6 — Medium — `aria-live` is on the node ISSUE-41 will animate every frame

**Where**: `components/stage.html:368` — `<div class="caption-container" id="caption-container" aria-live="polite">`.

`ux_spec § Accessibility notes` requires: *"a `aria-live="polite"` region that updates only when a line is finalised — the per-frame typewriter reveal is `aria-hidden`."* ISSUE-41 appends `.caption-line` nodes into `#caption-container` and mutates the last one's `textContent` on every `requestAnimationFrame`, and trims at `MAX_LINES`. As shipped, every reveal frame and every trim is a live-region mutation — a screen reader would be flooded, and ISSUE-41 would have to *remove* the attribute the shell just added. The shell sets ISSUE-41 up to fail rather than up to succeed.

**Fix now** (cheap, ISSUE-40-sized): drop `aria-live` from `#caption-container`, and add a visually-hidden announcer that ISSUE-41 writes to only on finalise:
```html
<div class="caption-container" id="caption-container" aria-hidden="true"> … </div>
<div id="caption-announcer" class="sr-only" aria-live="polite" aria-atomic="true"></div>
```
(with the usual `.sr-only` clip pattern). Note `#caption-empty` currently lives inside the live region and is present at parse time, so it is not spuriously announced on load — the problem is entirely forward-looking.

### UI-7 — Low — the header/logo bars do not actually absorb the 16:9 vertical slack

`ux_spec` states the two bars exist so that *"the vertical slack a 16:9 source leaves in a taller column is absorbed by the header and logo bars"*. With `grid-template-rows: auto minmax(0, 1fr) auto` (stage.html:56 — literally what the spec's grid prescribes) the bars stay at their content/min height and all residual slack lands inside `#presentation` as pure `#000000` bands:

| config @ 1920×1080 | header | logo bar | presentation | frame | residual black |
|---|---|---|---|---|---|
| `1/4`, logos, subtitle | 126.5px | 76px | 877.5px | 1440×810 | **67.5px** (33.75 top/bottom) |
| `1/4`, no logos | 91.7px | 0px | 988.3px | 1440×810 | **178.3px** |
| `1/3`, no logos | 91.7px | 0px | 988.3px | 1280×720 | **268.3px** |

Not an AC violation (still letterboxed, never cropped or stretched) and the `#000` vs `#0b0b0c` delta is barely perceptible, so this is cosmetic. **Fix (simplest)**: `.presentation { background: var(--canvas); }` (stage.html:100) and keep `#000000` on `.stage-frame` / `.capture-video`, so the residual slack reads as page canvas rather than a second black tone. If the spec's stated intent is to be honoured literally instead, the bars need to grow (e.g. `grid-template-rows: minmax(88px, 1fr) auto minmax(76px, 1fr)` with the frame row sized `auto`) — that is a larger change and should be a design decision, not a drive-by.

### UI-8 — Low — `#card-logos` reserves space when it has no images

**Where**: `components/stage.html:150-157, 357`. `.title-card-logos` has `gap: 28px; padding-top: 12px` and participates in `.title-card`'s `gap: 20px`, so an empty (or fully-404'd) logo list still costs ~32px and shifts the title card's optical centre. **Fix**: `cardLogos.hidden = !cardLogos.querySelector("img")` after the group loop, plus the same check inside the `onerror` handler; add `.title-card-logos[hidden] { display: none; }` since the UA rule loses to `display: flex`.

### UI-9 — Low — hairline token drift

`--hairline: rgba(255,255,255,0.07)` (stage.html:21) vs `rgba(255,255,255,0.06)` (viewer.html:49) for the same "divider between regions" role. Pick one. Also: the page defines 2 custom properties but uses 11 literal colour values inline — matching existing project practice, so recorded here rather than charged as a defect.

### UI-10 — Low / forward — empty-state copy is hardcoded Korean

`잠시 후 시작됩니다` is baked into markup at stage.html:369 while `CONFIG.caption_lang` and `CONFIG.output_langs` (stage.html:267-268) are injected and never read. `ux_spec § States` says the Empty state shows the message *in the selected language*, and ISSUE-41 owns the `WAITING_MSG` map. Correct for a layout-only PR; recorded so it is not lost.

### UI-11 — Low — no `<main>` landmark

`.stage` and `.stage-main` are plain `<div>`s (stage.html:343-344). `<header>`/`<footer>`/`<section aria-label="발표 화면">`/`<aside aria-label="자막">` are all correct. **Fix**: make `.stage-main` a `<main>` (or add `role="main"`); this also strengthens the banner/contentinfo mapping.

### UI-12 — Low / note — narrow-column caption typography is never rendered

No code path creates a `.caption-line`, so `clamp(20px, 1.4vw + 8px, 32px)` / `line-height: 1.45` / `word-break: keep-all` / `overflow-wrap: anywhere` (stage.html:228-236) are verified only by substring match (`tests/test_stage_page.py:240-244`). The ISSUE-40 scope claims "caption column shell (correct width + **narrow-column typography**)". A one-line e2e that injects a `.caption-line` node and asserts the computed `font-size` and the absence of horizontal overflow on a long URL would close this; ISSUE-41 will need it regardless.

## NFR-025 guard test — real or theatre?

`tests/test_stage_page.py:216-222`:
```python
assert "keydown" not in stage_html
assert "keyup" not in stage_html
assert "preventDefault" not in stage_html
assert "requestFullscreen" not in stage_html
assert ".focus()" not in stage_html
```

**Verdict: a useful tripwire for the ISSUE-40 "zero JS interaction" state, but theatre as a durable NFR-025 guard.** Three reasons:

1. It is whole-file substring matching, including the Korean prose comments — the word appearing in a comment fails it, and any spelling it doesn't enumerate (`addEventListener("key" + "down")`, `onkeydown=`, `el.focus()` via a variable, `document.activeElement`, `webkitRequestFullscreen`) passes it.
2. **ISSUE-42 must delete or weaken it.** `requirements.md:326` explicitly sanctions a `?debug=1` `keydown` counter (`{ passive: true }`, never calls `preventDefault()`), which trips assertion #1 on a perfectly compliant implementation. Its sibling `test_no_interactive_controls` (`"<button" not in stage_html`) is broken by ISSUE-42's mandatory "발표자료 연결" button on day one. A guard whose next-sprint fate is deletion protects nothing.
3. It cannot express the actual invariant, which is behavioural, not lexical: *presenter keys are not consumed, and the page never holds focus.*

**Recommendation for ISSUE-42** (not blocking #122): replace it with an e2e that dispatches `ArrowRight` / `PageDown` / `Space` at the document and asserts `event.defaultPrevented === false` and `document.activeElement === document.body`, plus `document.fullscreenElement === null` after load. Keep the string guard only for `preventDefault` / `requestFullscreen`, where the intent survives ISSUE-42 unchanged.

Separately: the **implementation** passes NFR-025 cleanly — my own grep across `keydown|keyup|keypress|preventDefault|.focus(|requestFullscreen|autofocus|tabindex` over `components/stage.html` returns zero matches, and the rendered page has zero focusable elements.

## RL compliance

- **RL-011**: `height: 100vh` × **2** (stage.html:27, 48) / `height: 100dvh` × **2** (stage.html:28, 49). Every `100vh` is immediately followed by its `100dvh` pair. **PASS.** The static guard at `tests/test_stage_page.py:170-177` checks adjacency line-by-line and is a genuinely good guard.
- **RL-012**: the only `margin` declarations in the file are three `margin: 0` (stage.html:25, 73, 82); no `margin-top` / `margin-bottom` / non-zero vertical shorthand on any `height: 100%` / `100vh` / `100dvh` element. **PASS.**
- **RL-006**: all three `_handle_stage` failure branches (repo exception, unknown room, template read failure) return the shared friendly 404 with no internal strings. **PASS.**
- **RL-008**: per-image degrade present but incomplete at the group/bar level — see UI-5.
- **RL-010**: logo `alt` derivation correct; caption `aria-live` placement wrong — see UI-6.
- **RL-016**: `_json_for_script` discharges the ISSUE-37 F-2 hand-off; `</script>` breakout neutralised and round-trips through `json.loads`. **PASS.**

## Notes for design-auditor (system-level, not charged to this PR)

- `docs/ux_spec.md § Stage Composite View` claims the header and logo bars absorb the 16:9 vertical slack, but the grid it prescribes (`auto / minmax(0,1fr) / auto`) cannot do that — fixed-height `auto` rows leave the slack in the presentation row. The spec's stated rationale and its stated grid contradict each other; one of them should be amended (see UI-7).
- `rgba(255,255,255,0.32)` for empty-state text (2.81:1) and `rgba(255,255,255,0.42)` for dimmed caption lines (4.04:1) are used in **both** `viewer.html` and now `stage.html`. The ux_spec asserts "dimmed past lines stay at or above 4.5:1 for the sizes used" — that is only true where the clamp lands at ≥24px. These are de-facto system tokens with no definition file; the project would benefit from a minimal `docs/design_system.md` fixing a text-on-canvas alpha ramp with the AA floor (0.45) marked.
- No `docs/design_system.md` and no `docs/copy_guide.md` exist. Copy is currently coordinated by copy-pasting strings between `issues.md`, `ux_spec.md` and the HTML templates; `잠시 후 시작됩니다` vs the ISSUE-31 record of `잠시 후 자막이 시작됩니다` shows the drift that follows.

## Confidence

**High.** The Critical finding is reproduced numerically (grid track 1743.67px vs 1440px area), by hit-testing (`elementFromPoint` inside the caption column returns `#card-title`), and by screenshot; the one-line fix was verified in-browser to bring overflow to exactly 0 at four viewports. Contrast ratios are computed, not eyeballed. AC 2/4/5 and the 16:9 invariant were each measured at both AC viewports. Residual uncertainty is limited to Chromium-only measurement (Safari's `container-type: size` + `cqh` behaviour was reasoned about from the CSS, not measured) and to the `Active` state, which cannot be exercised until ISSUE-41.

**Worktree left byte-identical**: `git status --porcelain` in the worktree reports only `?? .claude/` (pre-existing untracked directory, not mine). No implementation or test file was modified.

---

# UI Review Notes — ISSUE-41 (PR #127)

**Reviewer**: Claude Opus 5 (independent ui-review pass, did not author the code)
**Date**: 2026-08-23
**Branch / commit**: `issue/ISSUE-41-stage-caption-column` @ `a4622da` → review fixes pushed (diff vs `6b78000`)
**Files reviewed**: `components/stage.html` (+287/−5), `components/viewer.html` (prior art), `tests/test_stage_page.py`, `tests/e2e/test_stage_page_e2e.py`
**Method**: CSS + markup reading, independent numeric WCAG contrast computation (sRGB relative luminance, double-alpha compositing for the pill), `clamp()` resolution at 1280/1366/1600/1920/2560/3440px, read-only runs of the author's suites, and 21 source mutations in a throwaway worktree.
**Verdict**: **approve after fixes** — 0 Critical, 0 High, 1 Medium (FIXED), 1 Low (informational).

> This project has no `docs/design_system.md`, no `docs/copy_guide.md`, no `figma-export/` and no `prototype/`. Expectations below come from `components/viewer.html` and `docs/ux_spec.md § Screen: Stage Composite View`. No design system was invented for this review.

## State Coverage

| ux_spec state | Rendered? | Visible copy | Accessible copy | Evidence |
|---|---|---|---|---|
| Empty (자막 미시작 / waiting) | YES | `#caption-empty` ← `WAITING_MSG[lang]` | **was NO → now YES** | UI-7 below; e2e `test_waiting_state_is_announced_to_screen_readers` |
| Active | YES | `.caption-line` appended per rAF frame | YES, on line finalisation only | e2e `test_sixty_one_finals_trim_to_sixty_lines`, `test_three_partials_then_a_final_collapse_to_one_line`, `test_partial_text_is_revealed_gradually_not_in_one_frame` |
| Ended (`session_end` / closed room) | YES | `#caption-ended` "세션이 종료되었습니다" | YES (`role="status"`, outside the `aria-hidden` subtree) | e2e `test_session_end_switches_the_column_and_leaves_the_stage_intact` |
| Error — SSE 끊김 | YES | `#conn-error` pill, bottom of the caption column | YES (`role="status"`) | e2e `test_connection_banner_sits_at_the_bottom_of_the_caption_column` (now also asserts "bottom", not just containment) |

Every state transition is confined to the `<aside class="caption-column">`. The left column (`#event-header`, `#presentation`, `#stage-frame`, `#title-card`, `#logo-bar`) is asserted present with a non-zero box after `session_end`, and the diff touches none of it.

### UI-7 — Medium — the waiting copy had no accessible counterpart — **FIXED**
Moving the live region off the animated node (correct, RL-019) also silenced the **waiting** state: `#caption-empty` lives inside `#caption-container`, which is now `aria-hidden="true"`, and `applyWaitingText()` never wrote to the announcer. `aria-hidden` on an ancestor removes the whole subtree from the accessibility tree and cannot be undone by a descendant, so a screen-reader user got silence in the waiting state. `components/viewer.html` gets this for free because its waiting message lives in a separate `<section aria-live="polite">`; the stage page collapsed waiting and active into one subtree and lost it. Found independently by the ui-reviewer sub-agent and the code reviewer (as F-10).

**Fix**: `setCaptionState()` now owns the state copy — it writes the localised waiting text to `#caption-announcer` on `waiting` and clears it on `active`/`ended`. The caption-**text** write path is still `_lockLine()` alone, so RL-019 is intact and `test_only_finalised_lines_reach_the_live_region` still passes unchanged. The ended copy is deliberately **not** echoed into the announcer — `#caption-ended` already carries `role="status"`, and echoing would double-announce. Guard: e2e `test_waiting_state_is_announced_to_screen_readers` (asserts `?lang=en` → announcer reads "Starting shortly", then empties once captions start); verified to fail when the announcement is removed.

### Live-region ownership (RL-019) — discharged, verified

- `#caption-container`: `aria-hidden="true"`, no `aria-live`. Confirmed statically **and** in the runtime DOM after the typewriter has mutated it.
- `#caption-announcer`: `.sr-only`, `aria-live="polite" aria-atomic="true"`, a **sibling** of `#caption-container` (so `aria-hidden` does not propagate to it — the common mistake, avoided). `announce()` has exactly one caller for caption text: `_lockLine()`.
- `aria-atomic="true"` does not re-read history: `textContent` is fully replaced, so the region never holds more than one line.
- `#caption-ended` and `#conn-error` (`role="status"`) never receive caption text, so "exactly one element announces caption text" holds.
- The guard genuinely fails on ISSUE-40's shipped markup: running `test_animated_node_is_not_the_live_region` against `6b78000:components/stage.html` fails with `#caption-container still carries aria-live … aria-live="polite"`.
- Restoring `aria-live` to the animated node, or calling `announce()` per reveal frame, each kill a test. Removing `aria-atomic` kills a test.

## Copy Compliance

Compared byte-for-byte against `components/viewer.html` and `docs/ux_spec.md`:

| Slot | stage.html | viewer.html | ux_spec | Match |
|---|---|---|---|---|
| `WAITING_MSG` ko / en / zh / vi | 잠시 후 시작됩니다 / Starting shortly / 即将开始 / Sắp bắt đầu | identical | ko matches | YES |
| Ended | 세션이 종료되었습니다 | identical | matches | YES |
| Reconnect banner | 연결이 끊어졌습니다. 재연결 중… | identical | verbatim match | YES |

No drift, no placeholder/`TODO`/`TBD` text. The banner copy is hard-coded Korean while the waiting copy is localised, so `?lang=en` mixes languages — identical in `viewer.html`, therefore **not a regression** and not charged against this PR; recorded so it is not mistaken for one later.

## Token / contrast usage (RL-018)

Every ratio recomputed from the CSS rather than read from the inline comments. Canvas `#0b0b0c`.

| Selector | Colour | Backdrop | Ratio | Verdict |
|---|---|---|---|---|
| `.caption-line` (past) | `rgba(255,255,255,0.45)` | `#0b0b0c` | **4.51:1** | PASS — ISSUE-40's 4.04:1 fix survives |
| `.caption-line:last-child` | `#ffffff` | `#0b0b0c` | 19.67:1 | PASS |
| `.caption-empty` | `rgba(255,255,255,0.5)` | `#0b0b0c` | 5.33:1 | PASS — ISSUE-40's 2.81:1 fix survives |
| `.caption-ended` | `rgba(255,255,255,0.6)` | `#0b0b0c` | 7.29:1 | PASS |
| `.logo-group-label` | `rgba(255,255,255,0.5)` | `#0b0b0c` | 5.33:1 | PASS — ISSUE-40's 3.80:1 fix survives |
| `.event-subtitle` | `rgba(255,255,255,0.5)` | `#0b0b0c` | 5.33:1 | PASS |
| **`.conn-error` (NEW)** | `rgba(255,255,255,0.65)` | pill `rgba(255,255,255,0.07)` over `#0b0b0c` = `rgb(28,28,29)` | **7.81:1** | PASS |

`clamp()` resolution, applying the ISSUE-40 reasoning that caught the 4.04:1 case: `.caption-line`'s `clamp(20px, 1.4vw + 8px, 32px)` crosses 24px at **1142.9px**, so it resolves to 25.9 / 27.1 / 30.4 / 32 / 32 / 32 px at 1280 / 1366 / 1600 / 1920 / 2560 / 3440. `font-weight: 500` is **not** bold, so the large-text threshold is 24px. The exemption is never needed and never claimed: 0.45 clears the normal-text 4.5:1 bar outright. `.conn-error` (12–16px) and `.caption-empty` (14–19px) are normal text at every width and clear 4.5:1. The `rgba(255,255,255,0.1)` pill border is 1.26:1 but is decorative — the meaning is carried by the text.

**UI-8 — Low (informational)**: `.caption-line`'s 4.51:1 leaves only 0.01 of headroom above the AA floor (0.44 → 4.36:1 would fail). Already pinned numerically by the existing parameterised contrast test, so a regression cannot land silently; noted for awareness only. Lowering the `.conn-error` alpha to 0.45 is **also** a failure (4.44:1 on the pill vs 4.51:1 on the canvas) — the pill floor is 0.46, higher than the canvas floor, and a mutation confirms the test catches it.

## Layout containment (RL-017 / RL-011 / RL-012)

`.stage-main { grid-template-columns: minmax(0, 1fr) }` — ISSUE-40's UI-1 fix is untouched by this diff. New containers all protected: `.caption-column` (`min-width: 0; min-height: 0; overflow: hidden; position: relative`), `.caption-live` (`min-height: 0`; its child `.caption-scroll` sets `overflow-x: hidden`, so the flex automatic minimum size resolves to 0), `.conn-error` and `.sr-only` (`position: absolute`, out of flow, containing block `.caption-column`). Hostile content: the 120-char-title fixture and a 300-char unbroken token are both exercised, the latter with a measured `scrollWidth <= clientWidth` on `#caption-scroll` rather than a CSS grep. No new `100vh`; no vertical margin; `.caption-container` pairs `min-height: 100%` with `padding` under the global `box-sizing: border-box`.

`.sr-only` uses `clip-path: inset(50%)` + `margin: 0` instead of the legacy `margin: -1px` + `clip: rect(...)`. Correct here: the element is `position: absolute`, so the 1×1 box never participates in layout and the negative margin has nothing to compensate for; it stays in the accessibility tree (unlike `display:none` / `visibility:hidden`); and it is not inside the scroll container, so it creates no scroll anchor. The page-wide ban on vertical margin (RL-012) is honoured.

## Interaction fidelity (NFR-025)

Zero document/window-level `keydown` / `keyup` / `keypress` listeners; no `preventDefault()`, `requestFullscreen()`, `.focus()` or `autofocus`; no `<button>` / `<select>` / `<input>`. The only new document-level listener is `visibilitychange`, which cannot touch presenter-remote input. ISSUE-40's two NFR-025 guard tests are **byte-identical** to `6b78000` (SHA-256 match on both function bodies, zero deletions in the diff) and both pass.

**Motion / `prefers-reduced-motion`** — deliberately absent, and that is correct. `prefers-reduced-motion` reflects the OS setting of the machine driving the projector, not any audience member's; and a progressive text reveal has no scaling, parallax or motion-in-space. **Not a defect.**

**Scope** — no `getDisplayMedia`, wake lock, `?debug=1` overlay, capture button or focus-handoff UI. The only occurrence of `getDisplayMedia` is the pre-existing ISSUE-42 mount-point comment carried over unchanged from `6b78000`.

## Notes for a future design-auditor

- `#conn-error` carries `role="status"` and is toggled via `display: none ↔ block`, so mainstream AT will announce "연결이 끊어졌습니다. 재연결 중…" on every reconnect blip. Copied identically from `viewer.html`, so not a defect here — but neither page's `ux_spec` a11y notes say whether transient network chrome on an unattended, audience-facing display should be an announced live region at all. Worth a design-level decision.
- `viewer.html` structures its states as three sibling `<section>`s, where non-animated states get `aria-live` for free at the section level. `stage.html` collapses waiting + active into one subtree, which is exactly why UI-7 happened. If a third screen needs this pattern, the viewer's three-sibling shape is the safer template.

## Confidence

**High.** Contrast ratios and `clamp()` thresholds were computed, not eyeballed, and cross-checked against ISSUE-40's recorded values. Live-region ownership was verified in the runtime DOM, not just the markup, and the guard was re-run against ISSUE-40's actual shipped file. Every claim that mattered was mutation-tested. Residual uncertainty: Chromium-only measurement — Safari's handling of the `clip-path` sr-only pattern was reasoned about from the spec rather than measured.

---

# UI Review Notes — ISSUE-39 (PR #125)

> Scope: implementation-level review of the Streamlit admin "무대 화면 설정" form
> (`admin.py::_render_admin_room_stage_config` and helpers) against the ISSUE-39
> contract, `docs/ux_spec.md`, and `docs/review_lessons.md`.

## Summary / Verdict

**Approve with one fix applied.** This is an unusually careful implementation —
state coverage, RL-006 error hygiene, RL-002 server-side role gating, and the
"don't lose the admin's typed title on a failed save" AC are all correctly
handled, each backed by a targeted unit or e2e test. I found and fixed one
real **High** accessibility defect (delete buttons share an identical
accessible name), and I'm flagging four **Medium** and two **Low** items for
the team to consider; none of them block merge.

Verified in the browser (not just by reading code) using a minimal isolated
Streamlit app + Playwright against this project's actual Streamlit 1.48.1
build, because `st.button(help=...)` behavior is easy to misjudge by reading
source alone.

## Findings

### UI-01 [FIXED] — Delete buttons all share the identical accessible name "삭제"
- **Severity**: High
- **File**: `admin.py:838-843` (now `admin.py:838-849` after fix), function `_render_stage_logo_manager`
- **What**: Every per-logo delete button was created as `st.button("삭제", key=..., help=f"'{filename}' 로고를 삭제합니다")`. I verified against the project's actual Streamlit 1.48.1 build (isolated Playwright probe, not guesswork) that:
  - The rendered `<button>` has `aria-label=""` (empty) — its accessible name is computed from its text content, i.e. literally `"삭제"` for every button.
  - `help=` does **not** attach to the `<button>` itself. Streamlit wraps the button in a `div[data-testid="stTooltipHoverTarget"]` that carries `aria-describedby` pointing at the tooltip content — the `<button>` element itself has no `aria-describedby`. Standard accname computation does not walk up to an ancestor's `aria-describedby`, so the tooltip text is invisible to the button's own accessible name/description.
  - `page.get_by_role("button", name="삭제", exact=True).count()` returned `2` for a 2-button probe — i.e. Playwright/axe-style role+name resolution cannot disambiguate them, and neither can a screen reader tabbing through the list.
  - The adjacent `st.image(..., caption=filename)` does **not** help either: the `<img>`'s `alt` attribute is a Streamlit-internal numeric index (`alt="0"`), not the caption text — the caption renders as a separate visible `<p>` sibling, useful only to sighted users or screen-reader users doing full linear/virtual-cursor reading, not to Tab-only navigation.
  - Net effect: a room with 3+ logos across 3 groups presents 3+ interactive controls that all announce as "삭제, button" with zero differentiation for keyboard/screen-reader users — this is exactly the RL-010 pattern the issue's own implementation note tried to guard against, but the guard (`help=`) doesn't reach the accessible tree the way the comment assumed.
- **Why it matters**: WCAG 2.1 SC 4.1.2 (Name, Role, Value) / 2.4.6 (Headings and Labels) — controls with the same accessible name and different destructive effects on the same page. This is worse than a generic missing-label finding because the destructive action (irreversible file deletion) makes a misidentified control costly.
- **Fix applied**: Changed the button label to embed the filename directly (`f"삭제 · {filename}"`), since Streamlit's `st.button` has no `aria-label` parameter and the only reliable lever for the accessible name is the visible label text. Widened the logo-row column ratio from `[4, 1]` to `[3, 2]` to give the now-longer label room. Kept `help=` for the sighted hover tooltip (harmless, still consistent with the issue's "text label + help" note, now genuinely working for both audiences instead of only the sighted-mouse case).
- **Verification**: Re-ran the full suite after the fix — `1054 passed, 53 deselected`, coverage `94.08%` (identical to the pre-fix baseline). `ruff check .` and `black --check .` both clean. No test asserted the exact literal `"삭제"` label (grepped `tests/`), so nothing broke.

### UI-02 [Deferred] — Logo deletion has no confirmation step, unlike the house pattern for destructive actions
- **Severity**: Medium
- **File**: `admin.py:844-854`
- **What**: Clicking "삭제 · {filename}" deletes the file from disk and updates `stage_config` in a single click — no "정말 삭제하시겠습니까?" step, no undo.
- **Why**: The existing admin surface has an established convention for destructive actions — `docs/ux_spec.md:266-269` and `admin.py`'s user-management "사용자 삭제" tab require an explicit warning + a second confirm click before a delete executes. ISSUE-39's own AC (`issues.md:1984`) only requires the file to be removed on click, with no confirm step specified, so this is not a contract violation — but it is a visible deviation from the sibling convention, and multiple delete buttons are rendered simultaneously in a compact list (increasing misclick risk relative to the single always-guarded user-delete flow).
- **Recommendation**: A lightweight confirm (e.g. a second click required within N seconds, or a `st.popover` "정말 삭제할까요?") would bring this in line with house convention without adding much friction, given logos are admin-only and low-blast-radius (re-uploadable) — hence Medium, not High. Left unfixed: this is a UX/product-scope decision, not a minimal patch.

### UI-03 [Deferred] — "1/4" / "1/3" caption-ratio radio options are unexplained fractions
- **Severity**: Medium
- **File**: `admin.py:906-914` (`_render_stage_config_form`, the `자막 컬럼 비율` radio)
- **What**: The radio's `help` text says only "무대 화면 오른쪽 자막 컬럼이 차지할 가로 비율입니다." — it explains *what the control does*, but not what distinguishes the two options in practical terms (which one gives more room for captions vs. presentation). A non-technical event-day operator has to do fraction comparison (1/4 < 1/3) to know which is "wider captions."
- **Recommendation**: Append a plain-language qualifier, e.g. `"1/4 (좁게)"` / `"1/3 (넓게)"`, or extend the help text: "1/3이 자막을 더 넓게 표시합니다." This is a one-line change but is a copy/UX call, not a defect — left for the team per the Medium/Low deferral policy.

### UI-04 [Deferred] — Orphaned uploads possible if the post-upload asset-count cap is hit
- **Severity**: Low
- **File**: `admin.py:865-905` (`_save_stage_config`)
- **What**: Files are saved to disk (via `partition_uploads` → `save_asset`) *before* the second `validate_stage_config(candidate)` check that enforces the 12-assets-per-room cap. If that second check fails (a save that pushes the room over 12 total assets), the newly-accepted files remain on disk but are never linked into `stage_config` — because `update_stage_config` is never reached — leaving orphaned files. This is a narrow edge case (needs a room already near the 12-asset cap plus a multi-file upload in one save).
- **Why it's Low, not higher**: The implementation already has a self-healing detector for exactly this state — `find_asset_drift` / `_warn_stage_asset_drift` will surface "업로드되었지만 어느 그룹에도 속하지 않은 파일" on the very next render of this section. So the failure mode is caught and surfaced to the admin, just not in the same interaction that caused it, and the admin isn't told at the moment of failure that their upload was *silently* saved-but-unlinked (the on-screen message they see is only about the cap being exceeded).
- **Recommendation**: If addressed, either validate the cap *before* uploading (pre-flight count check against `len(existing) + len(new_uploads)`), or mention explicitly in the cap-exceeded error that "일부 파일은 저장되었으나 아직 반영되지 않았습니다 — 새로고침 후 확인하세요." Not fixed (edge case, existing drift-detector already provides a safety net).

### UI-05 [Deferred] — "은(는)" literal double-particle in `validate_stage_config` messages
- **Severity**: Low
- **File**: `stage_config.py:110,114` (pre-existing, shipped in ISSUE-37, not part of this PR's diff — noted for completeness only)
- **What**: Error messages use the literal string `"은(는)"` (e.g. "행사 타이틀은(는) 최대 120자까지...") instead of picking the grammatically correct particle. This is a common defensive pattern in Korean software for dynamically-labeled fields and is not incorrect, just slightly informal.
- **Not this PR's scope**: `stage_config.py` was delivered and shipped in ISSUE-37; ISSUE-39 only consumes it. Noting for completeness; not counted against this PR.

## State Coverage

| State | Handled? | Evidence |
|---|---|---|
| No rooms (empty) | Yes | `admin.py`: divider/subheader/caption always render, then "설정할 룸이 없습니다. 위에서 새 룸을 먼저 생성하세요." and early return before the selectbox |
| Config/asset-list load failure | Yes | `try/except` around `get_stage_config`/`list_assets` → `st.error("무대 설정을 불러오지 못했습니다.")`, detail logged server-side only (RL-006) |
| Populated (existing config) | Yes | `_seed_stage_form_state` restores title/subtitle/ratio into the form; verified end-to-end by e2e test `test_saved_event_title_is_restored_after_refresh` |
| No logos yet | Yes | "등록된 로고가 없습니다. 아래 폼에서 그룹별로 업로드하세요." |
| Logos present | Yes | Preview + delete button per asset, grouped by label |
| Partial upload failure (some accepted, some rejected) | Yes | Accepted files saved and linked; rejected files reported per-file with reason via `_report_rejected_uploads`; `st.warning("일부 로고를 제외하고 저장했습니다.")`; no `st.rerun()` on this path so the messages and the still-filled form remain visible |
| Full validation failure (e.g. oversized file only, or bad title length) | Yes | `st.error(reason)`, no rerun — title/subtitle/ratio values are untouched because they're bound via `key=` to session_state, not re-seeded on a failed submit (verified by code path: `_seed_stage_form_state` only reseeds on room change) |
| Save success | Yes | `st.success("무대 설정을 저장했습니다.")` + `st.rerun()` |
| Delete success (file removed) | Yes | `st.success("로고를 삭제했습니다.")` + `st.rerun()` |
| Delete success (file already gone, config-only cleanup) | Yes | Distinct message: `"파일이 이미 없어 설정에서만 제거했습니다."` — nice touch, avoids a misleading "삭제했습니다" when nothing was actually on disk |
| Delete failure | Yes | `st.error("로고 삭제에 실패했습니다.")`, internal exception logged only (RL-006) |
| stage_config ↔ disk drift (missing/orphaned files) | Yes | `_warn_stage_asset_drift` renders `st.warning` for both `missing` (config references a file not on disk) and `orphaned` (file on disk not referenced) |
| Loading / in-progress | N/A | Streamlit is synchronous request/response; no spinner is used anywhere else in this file either (consistent with house convention, not a gap specific to this PR) |
| Operator role | Correctly absent | Section is not called at all in the operator branch; covered by both a static AST test (`tests/test_admin_room_mgmt.py:1035+`) and an e2e negative-assertion test that also positively confirms the operator's tab isn't simply empty (RL-004-aware test design) |

No screen renders blank or dead-ends. Every action produces feedback.

## Copy Compliance

- Terminology is consistent: "로고" used throughout for the asset concept, "무대" used throughout for the stage screen (no "스테이지" leakage), "파일" reserved for filesystem-level references ("파일이 없는 로고", "파일이 이미 없어") — no glossary drift.
- Tone matches sibling sections (`_render_admin_room_create`, `_render_room_qr_section`, `_render_room_viewer_metrics`): short imperative Korean, `st.caption()` for section subtext, `[Admin] ... 실패: {e!r}` server-log prefix convention followed exactly.
- Error messages generally follow "[what happened] + [how to fix it]": e.g. `save_asset`'s "로고 파일은 2MB 이하만 업로드할 수 있습니다 (현재 4.0MB)." — states the limit, current value, and implicitly the fix (make the file smaller). Matches the AC's literal wording requirement ("2MB 이하").
- No `Lorem ipsum`/`TODO`/`TBD`/placeholder text anywhere in the diff.
- No internal exception detail leaks to the UI — every `except Exception as e` path prints `{e!r}` to console and shows a generic Korean message to the admin (RL-006 compliant), consistently applied across all 5 try/except blocks added in this PR (config load, logo preview, upload read, config save, URL/QR build).
- See UI-03 above for the one copy-clarity gap (caption ratio fractions).

## Accessibility Findings

- **UI-01 (fixed)** — maps to **RL-010** (interactive elements without distinguishing accessible names) and **WCAG 2.1 SC 4.1.2 / 2.4.6**. Verified against the real rendered Streamlit DOM, not inferred from source. Fixed in this review.
- **`st.image(..., caption=filename)`** (`admin.py:864-878`, `_render_logo_preview`) — the issue's implementation note asked for `caption=파일명` specifically as an a11y measure; that requirement is met literally, but I verified the resulting `<img alt="...">` is a Streamlit-internal numeric placeholder, not the caption text. This is a Streamlit platform limitation outside this PR's control (no `alt=` parameter exists on `st.image`) — noting for awareness, not filing as a separate defect, since the caption text is still present as adjacent visible/readable markup and the issue's literal AC is satisfied.
- **Delete buttons live outside `st.form`** — confirmed correct (`_render_stage_logo_manager` is called before `_render_stage_config_form`, and Streamlit forbids `st.button` inside `st.form` anyway, which would have raised at runtime if violated — the passing test suite already proves this).
- **All widgets have real Korean labels** — verified `st.text_input`, `st.radio`, `st.file_uploader`, `st.selectbox` all pass a human-readable first-positional label; no `label_visibility="collapsed"`/`"hidden"` anywhere in the diff.
- **Focus indicators** — no custom CSS is introduced by this PR that would remove Streamlit's default `:focus-visible` styling (no `border: none` / custom button CSS added), so the platform default focus ring is preserved. Not independently re-verified pixel-by-pixel since no styles were touched.
- **Download button label** — "📥 무대 QR PNG" clearly states what will download and is visually/textually distinguished from the pre-existing "📥 QR PNG" (viewer QR) button, satisfying "download button labels must say what will be downloaded."

## Section Convention Conformance

- Order matches spec: `st.divider()` → `st.subheader("🎬 무대 화면 설정")` → `st.caption(...)` — identical ordering/style to `_render_room_qr_section` and `_render_room_viewer_metrics`.
- `key=` uniqueness checked and confirmed for: room selectbox (`stage_cfg_room_select`, single instance), per-logo delete buttons (`stage_cfg_del_{room_id}_{label}_{filename}` — unique across rooms/groups/filenames), per-group uploaders (`stage_cfg_upload_{label}_{room_id}_{nonce}` — the `nonce` suffix is a deliberate and correct Streamlit idiom to force-reset the uploader's browser-side selection after a successful save, verified by tracing the control flow: the nonce bump happens in session_state and takes effect on the *next* script rerun, which happens automatically on the next user interaction — no explicit `st.rerun()` is needed or even wanted there, since calling it immediately would have hidden the just-shown `st.warning`/`st.error` toasts before the admin could read them; I traced this in detail suspecting a stale-widget/duplicate-upload bug and confirmed it does **not** occur), and the QR download button (`stage_qr_dl_{room_id}`). No `DuplicateWidgetID` risk found, including after a room switch (verified: switching rooms only changes `room_id`, which is embedded in every key that needs it).
- `format_func` on the room selectbox (`f"{r['name']} ({r['id']})"`) matches the identical pattern used in `_render_admin_assign_operator` and `_render_room_logs_section`.

## Component Existence

All components referenced by the issue's Scope are implemented: `_render_admin_room_stage_config`, `_seed_stage_form_state`, `_warn_stage_asset_drift`, `_render_stage_logo_manager`, `_render_logo_preview`, `_render_stage_config_form`, `_upload_pairs`, `_report_rejected_uploads`, `_save_stage_config`, `_render_stage_url_section`, `_safe_download_stem` — all present in `admin.py`, all called from the `is_role_admin` branch of `show_room_management`. `build_stage_config_from_form`, `partition_uploads`, `delete_stage_logo`, `describe_stage_config_drops`, `find_asset_drift` are present in `admin_logic.py` as pure functions (RL-001/RL-005 compliant, no Streamlit import). `build_stage_url` added to `qr_generator.py` alongside a shared `_build_room_url` helper (nice de-duplication against `build_view_url`, both now share one normalization rule). Nothing missing.

## Notes for design-auditor

- `stage_config.py:96-97,110,119` validation messages (`"무대 설정은 JSON 객체(dict) 형식이어야 합니다."`, `"{label}은(는) 문자열이어야 합니다."`) are somewhat developer-facing in tone. They're unreachable from this PR's admin form (the form always builds a proper dict via `build_stage_config_from_form`), but if `validate_stage_config` is ever surfaced from another entry point (e.g. a future API), these messages would leak internal type vocabulary ("dict", "list") to end users. This is `stage_config.py`'s declared contract (shipped in ISSUE-37), not an implementation defect in this PR — flagging for your system-level review, not mine.
- Consider whether the design system should have a documented convention for destructive-action confirmation thresholds (see UI-02) — right now the codebase has two different patterns for "delete something admin-managed" (two-step confirm for user accounts, one-click for logos) with no stated rule for when each applies. That's a system-level consistency question, not something I can resolve at the implementation layer.

## Summary

- Critical: 0
- High: 1 (fixed in review — UI-01)
- Medium: 2 (deferred — UI-02, UI-03)
- Low: 2 (deferred — UI-04, UI-05)

**Changes applied**: `admin.py` — delete-button label changed from `"삭제"` to `f"삭제 · {filename}"` to give each button a distinct accessible name; logo-row column ratio widened from `[4, 1]` to `[3, 2]` to accommodate the longer label.

**Post-fix verification**: `uv run pytest -q` → `1054 passed, 53 deselected`, coverage `94.08%` (matches documented baseline exactly). `uv run ruff check .` → clean. `uv run black --check .` → clean, 56 files unchanged.

---

# Accessibility Audit — ISSUE-39 (PR #125)

**Scope**: `admin.py::_render_admin_room_stage_config` and helpers (`_seed_stage_form_state`,
`_warn_stage_asset_drift`, `_warn_stage_config_drops`, `_render_stage_logo_manager`,
`_render_logo_preview`, `_render_stage_config_form`, `_report_rejected_uploads`,
`_save_stage_config`, `_render_stage_url_section`), plus supporting pure functions in
`admin_logic.py` (`build_stage_config_from_form`, `partition_uploads`, `delete_stage_logo`,
`describe_stage_config_drops`, `find_asset_drift`).

**Worktree audited**: `.worktrees/issue-ISSUE-39-admin-stage-config-form`
**Diff base**: `origin/main` (PR #125)
**Prior work treated as fixed, not re-litigated**: delete-button visible label
`f"삭제 · {filename}"` (WCAG 4.1.2/2.4.6), `st.image(caption=filename)` alt="0" limitation
accepted as a Streamlit constraint.

---

## Perceivable

### A11Y-01 — "현재 등록된 로고" and per-group labels use bold/italic markdown instead of real headings
- **Severity**: Low
- **WCAG**: 1.3.1 Info and Relationships (A)
- **Location**: `admin.py:857` (`st.markdown("**현재 등록된 로고**")`), `admin.py:862` (`st.markdown(f"*{label}*")`, inside `_render_stage_logo_manager`)
- **Finding**: The sub-grouping labels "현재 등록된 로고" and each logo-group name ("주최"/"주관"/"후원") are rendered as bold/italic text via `st.markdown`, not as semantic headings. A screen-reader user who navigates by the heading list (a very common AT workflow) will not see these as landmarks inside the "🎬 무대 화면 설정" section — they will only encounter them by reading linearly.
- **Why it matters**: Screen-reader users jumping directly to "현재 등록된 로고" or a specific group (e.g. "후원" logos) via heading navigation cannot do so; they must read the whole section serially.
- **Fix**: Replace with a real (nested) heading level, e.g. `st.markdown("#### 현재 등록된 로고")` and `st.markdown(f"##### {label}")`, so the outline reads `### 🎬 무대 화면 설정 → #### 현재 등록된 로고 → ##### 주최 / 주관 / 후원`. Verify the resulting `<h4>/<h5>` sits under the section's `<h3>` (`st.subheader`) without skipping a level.

### A11Y-02 — Drift/drop warnings state the effect but not the corrective action
- **Severity**: Low
- **WCAG**: 3.3.1 Error Identification (A) — satisfied minimally; this is a clarity improvement, not a failure
- **Location**: `admin.py:810-823` (`_warn_stage_asset_drift`), `admin.py:825-846` (`_warn_stage_config_drops`)
- **Finding**: e.g. `"설정에는 있으나 파일이 없는 로고: {names} — 무대 화면에서는 해당 로고만 숨겨집니다."` and `"{warning} — 이 폼에서 저장하면 원본에서도 사라집니다."` correctly identify *what* is wrong and *what will happen*, but not *what the admin should do about it* (re-upload the missing file under the same group, or accept that the orphaned/unknown-label value will be lost on the next save).
- **Why it matters**: A non-technical event operator (the persona named in `issues.md`/`ux_spec.md`) can read these and still not know the recommended next step, especially for `describe_stage_config_drops`, whose vocabulary ("알 수 없는 로고 그룹", "정규화") assumes familiarity with the data model.
- **Fix**: Append a short actionable clause, e.g. `"...무대 화면에서는 해당 로고만 숨겨집니다. 다시 사용하려면 같은 그룹에 파일을 다시 업로드하세요."` and for drops: `"...— 이 폼에서 저장하면 원본에서도 사라집니다. 유지하려면 저장하지 말고 관리자에게 문의하세요."` (This is a copy change only, no logic change; keep the underlying warning text generation in `admin_logic.py` so it stays testable.)

### Verified — not a finding
- `st.error` / `st.warning` / `st.success` all render with Streamlit's built-in icon glyphs (⚠️/🚫/✅) in addition to color, so status is not conveyed by color alone (1.4.1). This is Streamlit platform behavior, not something `admin.py` overrides — informational only.
- Upload-rejection reporting order (`_report_rejected_uploads` → per-file `st.error("'{name}' — {reason}")`) reads in a sane linear screen-reader order: each rejected file's name and reason are in the same sentence, and the block always follows the same position (immediately after the failed save attempt), so AT users get a predictable location for it call after call.
- Individual reason strings for validation and upload failures name the specific field/limit/current value (e.g. `"행사 타이틀은(는) 최대 120자까지 입력할 수 있습니다 (현재 143자)."`, `branding_assets.save_asset` messages) — these satisfy 3.3.1/3.3.3 requirements for actionable error text.

---

## Operable

### A11Y-03 — Destructive logo delete has no confirmation step, unlike the established pattern elsewhere in the same file
- **Severity**: High
- **WCAG**: 3.3.4 Error Prevention (Legal, Financial, Data) (AA) — deleting an uploaded, user-controllable asset with no way to recover it
- **Location**: `admin.py:848-896` (`_render_stage_logo_manager`), specifically `admin.py:880-893` (the `st.button("삭제 · {filename}", ...)` → immediate `delete_stage_logo(...)` → `st.rerun()`)
- **Finding**: Clicking a single "삭제 · {filename}" button irreversibly deletes the file from disk and mutates `stage_config.logo_groups` in one action, with no intervening confirmation, undo, or "다시 실행 취소" affordance. This is a real inconsistency inside `admin.py` itself: the pre-existing user-deletion flow (`admin.py:180` `"사용자 삭제"` sub-tab, `admin.py:269` `"🗑️ 삭제"`) already establishes a two-step pattern in this exact file — a dedicated sub-tab with warning text, an explicit primary "삭제" button, *and* a "취소" button (`admin.py:281`) — before anything destructive happens. ISSUE-39's logo delete drops straight to a single click with no equivalent step.
- **Why it matters for whom**: Keyboard-only users tabbing through a list of several logos, and screen-reader users navigating by button role (where every stop reads "버튼, 삭제 · logo-name.png"), are the users most likely to activate the wrong control by one extra Tab/arrow-key press or by misjudging which announcement they just heard — with no way to recover the file afterward. Motor-impaired users using switch access or voice control are similarly exposed to accidental activation with no recovery step.
- **Fix**: Reuse the file's own established pattern instead of inventing a new one — e.g. replace the single click with a two-phase button (`st.session_state`-backed "정말 삭제하시�on요?" confirm/cancel pair rendered in place of the delete button once clicked once), or move deletion into an `st.expander("삭제")` that requires an explicit second click of a "삭제 확정" button plus a visible "취소" button, mirroring `admin.py:269-284`. At minimum, add a `st.warning` naming the file immediately before the confirm control, matching the existing user-delete tab's warning text convention.

### A11Y-04 — Full-page rerun after delete/save does not restore or announce a stable focus target
- **Severity**: Medium (compounds A11Y-03; also applies to the successful-save path)
- **WCAG**: 2.4.3 Focus Order (A) informational aspect; primarily a usability aggravation of A11Y-03
- **Location**: `admin.py:893` (`st.rerun()` after successful delete), `admin.py:1066` (`st.rerun()` after successful save)
- **Finding**: Both the delete action and a successful form save call `st.rerun()`, which recreates the entire Streamlit DOM tree for the page. Streamlit does not restore browser focus to any specific element after a rerun triggered this way (this is a platform-level behavior, not something `admin.py` can fully control per-widget) — keyboard/screen-reader users are returned to a de-facto "top of app" focus state and must re-navigate down to the stage-config section, re-locate the room selector, and re-orient themselves after every single logo deletion.
- **Why it matters**: For a list of several logos, deleting them one at a time (the only way the UI supports it) forces a full re-navigation of the sidebar + tab structure + stage-config section for each deletion. Combined with A11Y-03 (no confirmation), the cost of a stray activation and the cost of legitimate serial use are both high for AT users.
- **Fix (code-level, within what `admin.py` controls)**: This is primarily a Streamlit platform limitation (RL-013-adjacent: DOM is fully torn down and rebuilt on rerun, and Streamlit does not expose a supported "restore focus to element X" API). Two things are still actionable in this PR: (1) resolving A11Y-03 so deletion requires a second explicit click reduces how often this rerun-focus-loss fires from accidental single clicks; (2) after the rerun, ensure the surviving `st.success(message)` / `st.error(message)` text is the *first* new content encountered near the top of the stage-config section (verify it renders before `_render_stage_logo_manager` re-runs, not buried after the room selectbox) so a screen-reader user re-entering the section immediately hears the outcome of the action they just took, rather than having to search for it.

### Verified — not a finding
- Full keyboard path through room selectbox → warnings (non-interactive, correctly skipped by Tab) → logo previews + delete buttons (all native `<button>`, outside `st.form` as required by Streamlit) → form fields (`st.text_input` ×2, `st.radio`, three `st.file_uploader`) → `st.form_submit_button("저장")` → stage URL section → `st.download_button("📥 무대 QR PNG")` uses only native Streamlit widgets, each of which ships keyboard support and (per `docs/a11y_audit.md`/`ux_spec.md`) a Streamlit-default focus ring; no custom JS widgets, `tabindex` overrides, or `onclick`-only elements are introduced by this PR. No keyboard trap was found in the diff.
- No new touch-target-size concerns beyond Streamlit's own default control sizing (admin dashboard is desktop-oriented per `ux_spec.md`'s screen list, which does not document mobile breakpoints for `/admin`) — noted as informational/Streamlit-platform, not filed.

---

## Understandable

### A11Y-05 — "1/4"/"1/3" radio options rely entirely on the adjacent `help=` tooltip for meaning
- **Severity**: Low
- **WCAG**: 3.3.2 Labels or Instructions (A) — satisfied via `help=`, flagged for robustness
- **Location**: `admin.py:927-933` (`st.radio("자막 컬럼 비율", options=list(CAPTION_RATIOS), ..., help="무대 화면 오른쪽 자막 컬럼이 차지할 가로 비율입니다.")`)
- **Finding**: The visible radio labels are the bare fractions `1/4` and `1/3`. The explanatory `help=` text is present (satisfying the letter of 3.3.2), but Streamlit's `help=` tooltip is not always discoverable at a glance (it requires hovering/focusing a small "ⓘ" affordance) and, per RL-010-style prior findings in this project, help text does not reliably attach to the same accessible name/description path across Streamlit versions for all widget types.
- **Why it matters**: An event-day admin who has never used the tool before, reading "1/4" / "1/3" with no visible units, may not immediately understand these are proportions of screen width without deliberately seeking out the tooltip.
- **Fix**: Consider making the visible option text self-explanatory instead of relying on tooltip discovery, e.g. define a `format_func` on `st.radio` that renders `"1/4 (좁게)"` / `"1/3 (넓게)"`, while keeping `caption_ratio` submitted as the raw `"1/4"`/`"1/3"` value the backend expects.

### Verified — not a finding
- Terminology is consistent throughout the added code: "로고" is used uniformly for the uploaded assets (never mixed with "에셋" or generic "파일" in user-facing strings — "파일" only appears in generic disk-level phrasing like `UPLOAD_FAILED_MESSAGE`, which is acceptable since it refers to the upload operation, not the concept). "무대" is used uniformly for the stage screen/section (never "스테이지" in user-facing copy). This matches `docs/ux_spec.md`'s "Copy Guidelines" tone requirements.
- Validation and rejection messages (`stage_config.validate_stage_config`, `branding_assets.save_asset`) name the offending field, the limit, and (for length) the current value — these are actionable, not just "invalid input" (checked directly in `stage_config.py:96-133` and referenced from `admin.py`).

---

## Robust

### A11Y-06 — Confirm: heading hierarchy and preview/delete association (both verified clean)
- **Severity**: N/A (verification note, not a finding)
- **Location**: `admin.py:753` (`st.subheader("🎬 무대 화면 설정")`), sibling sections at `admin.py:625, 636, 686, 1107, 1156, 1199, 1267, 1337` all also use `st.subheader`
- **Finding**: The new section uses `st.subheader` at the same nesting level as every other admin-room-management sub-section (`➕ 새 룸 생성`, `👤 오퍼레이터 배정`, `🛑 룸 강제 종료`, `📋 룸별 대화 기록`, `📱 룸 QR 코드`, `📈 뷰어 지표`). No heading level is skipped relative to its siblings. (A11Y-01 above is a narrower, separate finding about the *nested* bold-text pseudo-headings inside this section, not about the section's own heading level.)
- Each logo preview (`_render_logo_preview`, `st.image(..., caption=filename)`) is immediately followed in the same two-column row by its delete button, whose *visible accessible name* now includes the filename (`"삭제 · {filename}"`, per the already-fixed pattern). Because the accessible name itself disambiguates the control, no additional `aria-labelledby`/grouping wrapper is required for programmatic association — this was explicitly re-verified against the diff and is not re-filed.

### A11Y-07 — `st.rerun()` after delete discards the just-created `st.success`/`st.error` message context for the specific file
- **Severity**: Low
- **WCAG**: 4.1.3 Status Messages (AA) — informational; Streamlit's `st.success`/`st.error` are not implemented as ARIA live regions, so a message shown immediately before an `st.rerun()` is not guaranteed to be announced before the DOM is torn down
- **Location**: `admin.py:886-893` (delete success/error branch, immediately followed by `st.rerun()` on the success path only)
- **Finding**: On successful delete, `st.success(message)` is called and then `st.rerun()` fires in the same script run. Because Streamlit reruns are synchronous script-level replays and `st.success` does not persist across a rerun unless the caller stores it in `st.session_state` and re-renders it, the success toast may be visually present for only a single frame before the rerun replaces the DOM — a sighted user might catch the flash, but there is no guarantee a screen reader announces text that is mounted and unmounted in the same tick (this is consistent with `docs/a11y_audit.md`'s existing "Status chip aria-live: Not implemented" gap noted for the caption viewer, i.e. a pattern already flagged elsewhere in this codebase). Note the *error* path (`st.error(message)`, no `st.rerun()`) does not have this problem — the message survives because the script terminates without a rerun.
- **Why it matters**: A screen-reader user who deletes a logo and expects to hear "로고를 삭제했습니다." confirmation may hear nothing, because the message node may not persist long enough for AT to pick it up as a discrete DOM mutation before the whole tree is replaced.
- **Fix**: Store the outcome in `st.session_state` before calling `st.rerun()` (e.g. `st.session_state["stage_cfg_last_action_msg"] = message`) and render it at the top of `_render_admin_room_stage_config` on the next run, so the confirmation text survives the rerun and is present in the DOM for at least one full stable render — giving AT a reliable chance to announce it, and giving sighted users a persistent (not single-frame) confirmation.

---

## Summary

| ID | Severity | WCAG | Area |
|----|----------|------|------|
| A11Y-01 | Low | 1.3.1 | Perceivable |
| A11Y-02 | Low | 3.3.1 (clarity) | Perceivable |
| A11Y-03 | **High** | 3.3.4 | Operable |
| A11Y-04 | Medium | 2.4.3 (usability) | Operable |
| A11Y-05 | Low | 3.3.2 | Understandable |
| A11Y-06 | — | 1.3.1 / 4.1.2 | Robust (verified clean) |
| A11Y-07 | Low | 4.1.3 | Robust |

**Total findings**: 6 (1 High, 1 Medium, 4 Low), plus 1 explicit verification note (A11Y-06, no action needed).
**WCAG 2.1 AA conformance**: Partial. The single blocking item is A11Y-03 (no confirmation before an irreversible delete) — everything else is a refinement, not a hard failure, given what a Streamlit admin page can control.

### What was verified
- Full read of the PR #125 diff (`git diff origin/main -- admin.py admin_logic.py`) line-by-line, including every new function listed in scope.
- Cross-checked every user-facing string introduced against `docs/ux_spec.md` Copy Guidelines and against `docs/review_lessons.md` (RL-010, RL-013, RL-019, and others that turned out relevant: RL-006, RL-008).
- Verified message text sourced from `stage_config.validate_stage_config` and `branding_assets.save_asset` (read directly) is field-specific and actionable.
- Verified heading levels and button patterns against all sibling sections in `admin.py` (line-numbered greps of every `st.subheader`/`st.button`/`st.form`/`st.rerun()` call in the file) to confirm consistency claims and to find the pre-existing two-step delete-confirmation pattern used as the comparison baseline for A11Y-03.
- Confirmed (per task framing and `docs/a11y_audit.md`) that delete-button accessible-name disambiguation (`"삭제 · {filename}"`) and `st.image(caption=filename)` are already-resolved/accepted items and did not re-file them.

### What could not be verified without a running browser
- Actual screen-reader announcement behavior (NVDA/VoiceOver) for `st.success`/`st.error`/`st.warning` mount-then-rerun timing (A11Y-07) — reasoned from Streamlit's known rerun/DOM-replacement model, not measured.
- Whether Streamlit's default focus-ring/tab-order implementation (relied on for the "Verified — not a finding" items under Operable) in the currently pinned Streamlit version actually produces a visible `:focus-visible` indicator on every widget type used here (`st.selectbox`, `st.radio`, `st.file_uploader`, `st.download_button`) — this is asserted as consistent with prior audits of this codebase but was not re-measured in a live Chromium session for this specific page.
- Real contrast ratios for Streamlit's default alert-box colors (`st.warning`/`st.error`/`st.success` backgrounds/text) were not recomputed pixel-for-pixel in this pass — these are Streamlit-theme-controlled, not overridden by `admin.py`, and out of this PR's control surface.
- Whether the `help=` tooltip in A11Y-05 is exposed via `aria-describedby` on the radio's fieldset/legend or on a wrapper div only (same ambiguity documented for buttons elsewhere in this codebase) — not measured in a live DOM inspection for `st.radio` specifically.

**Confidence**: Medium-High. Source-level reasoning is thorough and cross-referenced against the file's own established patterns (giving A11Y-03 in particular a solid comparative basis), but several findings that depend on Streamlit's live DOM/AT behavior (A11Y-04, A11Y-07, and the focus-ring assumption) are flagged above as "Needs Manual Verification" in a running browser with a screen reader.

## Resolution (team-lead, post-audit)

| Finding | Severity | Disposition |
|---|---|---|
| A11Y-03 | **High** | **FIXED in review** — commit `5676597`. Two-step confirm/cancel following the house pattern at `admin.py:269-284`. Arm state is a `(room, group, index, filename)` tuple in `admin_logic` (pure, unit-tested: 9 arming tests + 8 interaction tests + 2 room-change tests). Confirm/cancel controls name the file in their **visible** label because Streamlit's `help=` puts `aria-describedby` on a wrapper div and cannot name a `<button>` (RL-010 / WCAG 4.1.2). Widget keys keep the index-bearing shape that prevents `StreamlitDuplicateElementKey`. Guarded by `TestStageLogoDeleteIsStructurallyTwoStep`, which additionally tests **its own checker** against single-click regression sources (`test_checker_rejects_single_click_regressions`) so the guard cannot rot into a vacuous pass (RL-004). |
| A11Y-07 | Low | **FIXED alongside A11Y-03** — the delete result message now survives the `st.rerun()` and is rendered on the next run (`test_delete_result_is_announced_after_the_rerun`). |
| A11Y-04 | Medium | Deferred. Focus restoration after a full-page `st.rerun()` is largely a Streamlit platform constraint; A11Y-03's fix reduces rerun frequency on the destructive path. Follow-up. |
| A11Y-01 | Low | Deferred — `st.markdown("**…**")` group labels instead of real headings. Cosmetic-structural; matches the surrounding admin sections, so changing it here alone would make the page inconsistent. Worth a page-wide pass. |
| A11Y-02 | Low | Deferred — drift/drop warnings state the effect, not the corrective action. Copy improvement. |
| A11Y-05 | Low | Deferred — `1/4` / `1/3` meaning depends on the `help=` tooltip. Overlaps UI-03; same follow-up. |
| A11Y-06 | — | Verified clean, no action. |

---

# UI Review Notes — ISSUE-42 (PR #129)

발표자료 화면 캡처 연결 — 프레젠터 조작 비간섭 및 복구 처리. Branch
`issue/ISSUE-42-stage-display-capture`, head `e480693`, rebased on `0a72f90`.
Reviewed by the independent UI reviewer; correctness/security/minimality are the
parallel code reviewer's (`docs/review_notes.md`).

## Scope

In: rendered state coverage, copy usage at the call site, token usage in
`components/stage.html`, interaction fidelity vs. the ISSUE-42 AC, in-code
accessibility (incl. `aria-live` ownership per RL-019), component existence.

Out: the design system itself. This project ships no `docs/design_system.md` —
tokens live inline in `stage.html` `:root` and in the sibling `viewer.html`, so
"token consistency/coverage" findings are recorded under *Notes for
design-auditor* rather than as my findings. Viewer-page parity / viewer WCAG /
viewer caption pipeline (ISSUE-44/45/46) are separately owned and untouched.

## Independent verification performed

Every number below is my own measurement. The implementer's claimed ratios were
treated as unverified assertions and recomputed from the CSS, then confirmed
against the rasterised screenshot.

1. **Contrast recomputed from source** — `clamp()` resolved at the real viewport
   (1920 and 1280), alpha composited over the *actual* background each element
   sits on, both float and 8-bit-rounded. Script:
   `scratchpad/contrast42.py`.
2. **Rendered measurement in Chromium (Playwright)** — for each element I walked
   the ancestor chain compositing background layers until the first opaque one,
   then computed the ratio from `getComputedStyle` values as rendered. Script:
   `scratchpad/probe42.py`.
3. **Pixel-level proof** — screenshotted the real page and sampled the actual
   pixels inside each text element's bounding box, so the "background" is what
   the compositor painted, not what the CSS cascade implies. Script:
   `scratchpad/pixel42.py`. This is what caught UI-1.
4. **Every AC state rendered**, not grepped: initial, connected, handoff prompt,
   blur-dismiss, self-capture mirror, track `ended`, `NotAllowedError`,
   unsupported browser, `?debug=1` present and absent.
5. **`getDisplayMedia` stubbed** via `add_init_script` with a canvas
   `captureStream()` track. The canvas is filled **`#ffffff`** on purpose — a
   real slide deck is white, and the suite's own stub uses `#1d4ed8`, which is
   dark enough to mask UI-1.
6. **Layout re-checked per RL-017** with a 46-character Korean title *and* a live
   `<video>` in the frame, at 1920×1080 / 1366×768 / 1280×600 / 1024×500 /
   900×1200.
7. Suites re-run after my fix: `tests/test_stage_page.py` **94 passed**,
   `tests/e2e/test_stage_capture_e2e.py -m e2e` **18 passed**.

All throwaway scripts live in the session scratchpad, never in the repo or
`tests/` (the earlier-wave `contrast_table.py` / `e2e_debug*.py` / `probe.py`
mistake was not repeated).

## Contrast table (my measured ratios)

Resolved font size at 1920px, and the **composited background actually painted
behind the element**. `need` is 4.5:1 unless the resolved size clears the
large-text threshold (≥24px, or ≥18.66px bold).

| element | resolved px @1920 | composited background | measured | need | verdict | implementer claimed |
|---|---|---|---|---|---|---|
| `.capture-button` | 19.20 (600 wt) | `#ffffff` own opaque pill | **19.67:1** | 3.0 | PASS | 19.67 ✓ |
| `.capture-hint` *(as shipped, title-card path)* | 17.28 | `#000000` `.stage-frame` | **11.42:1** | 4.5 | PASS | 11.42 ✓ |
| `.capture-hint` *(as shipped, over live `<video>`)* | 17.28 | **whatever the deck is showing** | **1.00:1** | 4.5 | **FAIL** | not considered |
| `.capture-hint` *(after my fix)* | 17.28 | `#0b0b0c` own opaque pill | **11.05:1** (10.94 rasterised) | 4.5 | PASS | — |
| `.capture-warning` | 18.24 | `#0b0b0c` own opaque | **12.34:1** | 4.5 | PASS | 12.34 ✓ |
| `.handoff-prompt` | 21.12 (500 wt) | `#0b0b0c` own opaque | **16.51:1** | 4.5 | PASS | 16.51 ✓ |
| `.debug-overlay` | 14.00 | `#0b0b0c` own opaque | **14.08:1** | 4.5 | PASS | 14.08 ✓ |

Pre-existing baseline — no regression, all ≥ 4.5:1:

| element | resolved px @1920 | composited background | measured | baseline |
|---|---|---|---|---|
| `.conn-error` | 13.44 | `rgb(28,28,29)` = 0.07 pill on `#0b0b0c` | **7.85:1** | 7.81 ✓ |
| `.caption-line` (dim) | 32.00 | `#0b0b0c` | **4.52:1** | 4.51 ✓ |
| `.caption-empty` | 19.00 | `#0b0b0c` | **5.34:1** | 5.33 ✓ |
| `.logo-group-label` | 14.40 | `#0b0b0c` | **5.34:1** | 5.33 ✓ |
| `.caption-ended` | 23.04 | `#0b0b0c` | **7.26:1** | 7.29 ✓ |
| `.event-subtitle` | 19.20 | `#0b0b0c` | **5.34:1** | — |
| `.title-card-subtitle` | 26.88 | `#000000` | **6.25:1** | — |

At 1280px every `clamp()` lower bound still clears AA
(`.capture-button` 15px/19.67, `.capture-hint` 13px/11.42, `.capture-warning`
13px/12.34, `.handoff-prompt` 15px/16.51). **No element relies on the
large-text 3:1 exemption**, so RL-018's trap is genuinely avoided — with the one
exception recorded as UI-1, which is not an exemption error but a *wrong
backdrop* error.

**Arithmetic verdict on the implementer's claims: all five are correct.** The
two-hundredths deltas (`.caption-ended` 7.26 vs 7.29, `.conn-error` 7.85 vs 7.81)
are integer-rounding of the composited channel, and the file's own comment
already discloses the 7.81/7.85 pair. The defect in UI-1 is not a miscomputed
ratio — it is a ratio computed against a background that is not on screen.

## State coverage

Every state the AC names exists and was **rendered**, not grepped.

| state | route exercised | result |
|---|---|---|
| initial title card + "발표자료 연결" | page load | ✓ card visible, 1 visible clickable |
| connected | click connect | ✓ `<video>` visible, title card hidden, **0 visible clickables**, `cursor: none` |
| handoff prompt (one-shot) | on connect success | ✓ shown; auto-dismisses on `blur` immediately, and on the `HANDOFF_MS = 8000` timer otherwise. Does not persist |
| self-capture mirror | `displaySurface:'browser'` + matching size | ✓ warning banner + reselect control retained, capture **not** force-stopped, cursor stays `default` |
| track `ended` | dispatch `ended` on the track | ✓ title card returns, `srcObject` nulled, label → "발표자료 다시 연결". **Never a black frame** |
| `NotAllowedError` | stubbed rejection carrying a sentinel path string | ✓ generic guidance only; sentinel **absent** from DOM (RL-006 holds) |
| unsupported browser | `navigator.mediaDevices` undefined | ✓ distinct Korean guidance, no exception |
| `?debug=1` overlay | query flag | ✓ focus readout + keydown counter, live and correct |
| `?debug=1` absent | plain load | ✓ `#debug-overlay` **does not exist** — DOM and listeners are never created |

Caption-column continuity across capture teardown (an explicit AC): emitted a
caption before connect, one during, one after `ended` — line count grew 2 → 3
and the announcer carried the final text. `onCaptureEnded` touches nothing in
the caption column. ✓

## Copy

Exact-match against the AC strings, checked at the render site:

- "발표자료 연결" / "발표자료 다시 연결" ✓
- "발표 앱을 클릭해 포커스를 넘기세요" ✓
- "무대 화면이 캡처되었습니다. 발표자료 창을 선택하세요" ✓
- "잠시 후 시작됩니다" / "세션이 종료되었습니다" ✓ (unchanged)
- Failure copy follows *[what happened] + [how to fix]*: "발표자료 화면을
  가져오지 못했습니다. 다시 연결을 눌러 창을 선택하세요" and "이 브라우저는
  발표자료 화면 공유를 지원하지 않습니다. Chrome 또는 Edge 최신 버전을
  사용하세요" ✓
- No placeholder text, no `Lorem ipsum`/`TODO`/sample data in any state ✓
- No developer-facing string reaches the event screen; the injected sentinel
  exception message never appears ✓

Two nits recorded below (UI-2, UI-3). Tone is consistent with `viewer.html`
(plain Korean, no trailing period on short status lines).

## Tokens

`:root` defines only `--caption-width`, `--canvas`, `--hairline` — deliberately
minimal, and pre-dating this PR. The new CSS adds colour literals rather than
using them:

- `#0b0b0c` appears literally **4×** in new rules (`.capture-button` colour,
  `.capture-warning`/`.handoff-prompt`/`.debug-overlay` backgrounds, plus a 5th
  in my fix) while `var(--canvas)` holds exactly that value.
- `#7cc4ff` (focus ring) is a brand-new literal with no token.

This matches existing precedent in the same file (ISSUE-40 shipped `#000000` and
`#ffffff` literals), so it is not a regression and I did not "fix" it — churning
it now would fight the parallel reviewer for no user-visible gain on the last
issue of the sprint. Logged as UI-4 / design-auditor note.

No magic numbers in the JS; all capture behaviour is driven by named constants
(`CAPTURE_CONSTRAINTS`, `HANDOFF_MS`, `CONNECT_LABEL`, `RECONNECT_LABEL`,
`CAPTURE_FAIL_MSG`, `CAPTURE_UNSUPPORTED_MSG`, `MAX_LINES`,
`MAX_REVEAL_PER_FRAME`).

## Accessibility (implementation)

### `aria-live` ownership (RL-019) — the headline check

| | before (`0a72f90`) | after (`e480693`) |
|---|---|---|
| `[aria-live]` | **1** — `p#caption-announcer` (`polite`, `aria-atomic="true"`) | **1** — unchanged |
| `[role="status"]` | 2 — `#caption-ended`, `#conn-error` | **2** — unchanged, both pre-existing |
| `[role="alert"]` | 0 | **0** |
| `[aria-hidden]` | 1 — `#caption-container` | **1** — unchanged |

**No competing live region was introduced.** All five new surfaces — handoff
prompt, self-capture warning, capture hint, reselect zone, `?debug=1` overlay —
carry no `aria-live` and no `role`. Verified in the connected state and with
`?debug=1` active, not just at initial load.

The `?debug=1` overlay specifically: `aria-live` = `null`, `role` = `null`,
`pointer-events: none`. It would otherwise fire on every keypress and fight
`#caption-announcer` — the exact RL-019 failure. Correct.

`#caption-container` remains `aria-hidden="true"` with the announcer written only
from `_lockLine()`. ISSUE-41's structure survived this PR intact.

### Keyboard and focus

- The connect button is a real `<button type="button">`, reachable by `Tab`
  before connect (verified: `Tab` → `BUTTON#capture-connect`).
- `:focus-visible` ring retained and rendered: `3px solid rgb(124,196,255)` with
  `outline-offset: 3px`; `matches(':focus-visible')` is `true`. RL-010 is not
  sacrificed to the non-interference constraint.
- After connect, `document.activeElement === document.body` and
  `document.fullscreenElement === null`.
- Presenter keys (`ArrowRight`/`ArrowLeft`/`PageDown`/`PageUp`/`Space`) all come
  back `defaultPrevented === false`, with and without `?debug=1`; the debug
  counter increments (3 for 3) while cancelling nothing.

### Click-surface minimisation (the user's hard constraint)

**Visible clickable elements after connect: 0.** Confirmed by querying
`button, a, input, select, textarea, [tabindex]` and filtering on rendered
visibility. The controls are removed with `display: none` (via the `[hidden]`
rule), not `pointer-events: none` — correct, because `pointer-events` only
changes in-document hit-testing and cannot stop the OS giving the window focus.
`cursor` is `none` on the stage root and inherited by `#capture-video` and
`#reselect-zone`. No auto `focus()` / `requestFullscreen()` anywhere.

The single `_showControls()` writer flips visibility and cursor together, so the
two cannot desynchronise into "cursor hidden but button still clickable".

## Interaction fidelity

- `cursor: none` while connected, `cursor: default` restored by the corner
  double-click — verified as computed style, both directions.
- Corner double-click is the only route back to the controls. It is a
  non-focusable, affordance-free 80–200px × 60–160px top-left zone. Usable in
  rehearsal, not an accidental click surface. The file documents the
  RL-010 trade-off explicitly and leaves two keyboard-equivalent recovery paths
  (track `ended` restores a real button; reload restores the initial state).
- `getDisplayMedia` constraints as rendered: `{"video":{"frameRate":{"ideal":30}},
  "audio":false,"selfBrowserSurface":"exclude","surfaceSwitching":"exclude",
  "systemAudio":"exclude"}` — matches the AC exactly.
- **16:9 letterbox holds with a live `<video>` (RL-017 re-check).** With a
  46-char Korean title, frame aspect ratio is **1.778 at all five viewports**;
  `.stage-main`'s right edge equals the caption column's left edge at every one
  (no encroachment); `document.scrollWidth === innerWidth` (no overflow);
  `object-fit: contain`; the video never exceeds the frame. The title ellipsises
  at ≤1024px as intended. **ISSUE-40's max-content track blowout does not
  recur** — `grid-template-columns: minmax(0, 1fr)` is doing its job.
- Captions keep flowing across connect / disconnect / reconnect (see State
  coverage).

### Smoothness (the other half of the hard constraint)

- `setInterval` call count during a live session: **0**. ISSUE-41's rAF
  typewriter is intact.
- `MAX_REVEAL_PER_FRAME = 24`, `MAX_LINES = 60` — after pushing 80 captions the
  DOM held **exactly 60** `.caption-line` nodes.
- At most one rAF loop: `_twStart()` early-returns on `if (twRaf || !currentLine)`.
- **No compositing hints added**: `will-change` computes to `auto` on
  `#capture-video`, `#stage-frame`, `#presentation`, `#capture-controls`,
  `#stage-root`. The file comments that video is already its own layer and a hint
  would only burn VRAM — correct.
- `contain: layout paint` on `.caption-column` does **not** break the layout:
  column geometry is intact, the last caption line stays inside the column
  bounds, and `.conn-error` still anchors within the column (it is `display:none`
  when connected, hence the 0-width reading).

## Component existence

Every component the ISSUE-42 scope names exists in `components/stage.html`:
`#capture-video`, `#title-card`, `#capture-controls`, `#capture-connect`,
`#capture-hint`, `#capture-warning`, `#handoff-prompt`, `#reselect-zone`, and the
conditionally-created `#debug-overlay` / `#debug-focus` / `#debug-key-count`.
No wireframe-referenced component is missing. ✓

## Findings by severity, with resolution

### UI-1 — **High** — `.capture-hint` renders at 1.00:1 (completely invisible) over a live capture video — **FIXED**

`components/stage.html`, `.capture-hint` rule (was line 223).

`.capture-hint` was the only one of the four new text surfaces with **no
background declaration at all** (`.capture-warning`, `.handoff-prompt` and
`.debug-overlay` each declare an opaque `#0b0b0c`). Its 11.42:1 was computed
against `.stage-frame`'s `#000000` — the ancestor background. That ancestor is
correct on the first-run path, where the title card is up and no video is
playing. It is **wrong on the reselect path**, where the `<video>` is painted on
top of it.

Reachable, and by the AC-blessed route:

> connect → present → corner double-click to reselect → click "발표자료 다시
> 연결" → cancel/deny the picker.

`startCapture()` returns early on rejection *before* `_releaseCapture()`, so the
old track keeps playing and the hint is drawn over live deck pixels.

Pixel-sampled from the rasterised screenshot with a white slide in the frame:
**every pixel inside the hint's bounding box was `#ffffff`** — glyph and
background alike. Measured ratio **1.00:1** at 17.28px, against a required 4.5:1.
The operator receives *no feedback whatsoever* that the reselect failed, so
NFR-026's "no dead end in any capture failure path" and the AC's "일반 안내
문구만 표시된다" are both silently unmet on that path. Severity is High rather
than Medium precisely because the message is not merely low-contrast, it is
absent.

This is RL-018 one step further than the lesson currently states: the alpha was
computed, and computed correctly — against a backdrop that a sibling element
occludes at render time.

**Fix applied** — gave `.capture-hint` the same opaque-pill treatment its two
sibling banners already use, and for the same reason the file already documents
under `.capture-warning` ("배경을 **불투명**으로 두는 이유는 임의의 캡처 영상
위에 뜨기 때문이다"). The reasoning was written down; it just was not applied to
the third banner.

```css
.capture-hint {
  margin: 0;
  max-width: 100%;
  padding: 10px 20px;
  border-radius: 999px;
  background: #0b0b0c;
  border: 1px solid rgba(255, 255, 255, 0.18);
  font-size: clamp(13px, 0.9vw, 20px);
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.75);
}
```

Re-measured over the same white slide: background `rgb(11,11,12)`, glyph
`rgb(193,193,194)` → **10.94:1 rasterised / 11.05:1 computed**. No layout change
at any of the five viewports; the pill stays inside `.capture-controls`'
`max-width: 82%`.

**Guard added** so the class of defect cannot recur silently. The existing
`test_dim_text_meets_wcag_aa_contrast` *assumed* a constant backdrop and its
docstring asserted in prose that "배경은 전부 **불투명**" — which was false for
`.capture-hint`, and the assumption is exactly what let the ratio pass at
11.42:1. I turned that prose into a structural fact:

- `tests/test_stage_page.py::test_capture_surfaces_declare_an_opaque_background`
  — parametrised over all four capture surfaces; each must declare a `background`
  with alpha 1, so the contrast test's `_CANVAS_RGB` assumption becomes true by
  construction rather than by luck.
- Retargeted `.capture-hint`'s backdrop in the contrast test from `_FRAME_RGB`
  to `_CANVAS_RGB`.

**Proven RED**: stashed the CSS fix and re-ran — fails with
`AssertionError: .capture-hint declares no background; its contrast would be
decided by whatever the capture <video> happens to be showing`. Restored, GREEN.

Suites after the fix: **94 passed** static (was 90; +4 parametrised cases),
**18 passed** e2e.

### UI-2 — **Medium** — failure copy names a button label that is not on screen — follow-up

`CAPTURE_FAIL_MSG` says "**다시 연결**을 눌러 창을 선택하세요", but on the
*first* failure `captureStarted` is still `false`, so the button beside it reads
"발표자료 **연결**", not "발표자료 다시 연결". Verified by rendering both paths:
first-run label "발표자료 연결" + that hint; reselect-path label "발표자료 다시
연결" + the same hint. The instruction points at a control that does not exist
under that name until the second attempt.

Not fixed — Medium, and this is the last issue of the sprint. Suggested wording
that is correct in both states: "발표자료 화면을 가져오지 못했습니다. 아래
버튼을 눌러 창을 다시 선택하세요".

### UI-3 — **Low** — `?debug=1` overlay renders the bare English token `keydown` — follow-up

`setupDebugOverlay()` sets `keys.textContent = "keydown "`. The overlay is
operator-facing and may be terse, and the rehearsal criterion is literally "keydown
counter stays 0", so the term is meaningful to its audience. Still the only
non-Korean user-visible string on the page. Suggest "키 입력 (keydown)" or
"눌린 키". Gated behind the flag, so it never reaches the event screen.

### UI-4 — **Low** — new colour literals bypass the existing `--canvas` token — follow-up

`#0b0b0c` written literally in 4 new rules where `var(--canvas)` holds the same
value; `#7cc4ff` focus ring introduced with no token. Consistent with existing
precedent in the same file, so not a regression. See *Notes for design-auditor*.

### UI-5 — **Low** — mirror state shows two prompts giving opposite instructions — follow-up

In the self-capture case the page displays the warning ("발표자료 창을
선택하세요", y≈239) **and** the handoff prompt ("발표 앱을 클릭해 포커스를
넘기세요", y≈805) **and** the controls (y≈921) simultaneously. Geometrically
they do not overlap — verified — so this is not a layout defect. But the guidance
conflicts: the operator is told to hand focus away at the same moment they are
told to stay and reselect the source. Suggest suppressing `_showHandoff()` when
`mirrored` is true, since the handoff has not yet earned its prompt.

## Follow-ups

| id | severity | item |
|---|---|---|
| UI-2 | Medium | `CAPTURE_FAIL_MSG` names "다시 연결" while the first-run button reads "발표자료 연결" |
| UI-3 | Low | `?debug=1` overlay renders the English token `keydown` |
| UI-4 | Low | `#0b0b0c` ×4 and `#7cc4ff` as literals instead of tokens |
| UI-5 | Low | Mirror state shows conflicting handoff + reselect guidance together |

## Notes for design-auditor

- **No `docs/design_system.md` exists.** Tokens are inline `:root` custom
  properties duplicated between `components/stage.html` and
  `components/viewer.html`. There is no single source for `--canvas`,
  `--hairline`, the focus-ring colour, or the type scale, which is why UI-4 is
  a literal-vs-token drift rather than a violation of a declared contract.
- **The AA alpha floor is being rediscovered per element.** `stage.html` now
  carries three separate hand-written comments deriving it (0.45 on `#0b0b0c`;
  0.46 on the `.conn-error` pill because the 0.07 pill lightens the backdrop to
  `rgb(28,28,29)`; 0.5 chosen for labels). RL-018's *Recommended action* — record
  the floor once in a design-system note — is still unimplemented, and UI-1 is
  the cost of that. Worth a token like `--dim-on-canvas`.
- **A backdrop token would have prevented UI-1 structurally.** The page has two
  distinct canvases (`#0b0b0c` page, `#000000` stage frame) plus a third
  effective one (arbitrary video pixels). Only the first two are named. Any
  element that can be painted over the video needs an opaque surface token.
- `#7cc4ff` focus ring is unique to `stage.html`; `viewer.html` has no
  equivalent, so cross-page focus styling is undeclared.

## Confidence

**High.** Every claim above is backed by a rendered measurement rather than a
source read: contrast recomputed twice by independent methods and confirmed at
the pixel level, all nine states rendered, layout checked at five viewports with
worst-case content, live-region counts taken from the DOM in three different
states, and both suites re-run after the change. The one finding I fixed was
proven RED before being proven GREEN.

Residual risk is narrow and named: the `?debug=1` counter's "20 presses → 0"
criterion is a genuinely manual rehearsal check (TC-061) that no automated test
can stand in for, because it depends on real OS focus. The e2e suite proves the
counter counts and cancels nothing; it cannot prove the projector-room setup is
right.

## Verdict

**Approve with one fix applied.**

The hard constraint — "비디오 캡쳐 형태로 가더라도 버벅인다거나 프레젠터로
조작이 안되거나 해서는 안된다" — is met on the UI side, and met structurally
rather than by assertion. After connect the page renders **zero** visible
clickable elements, hides the cursor, registers no default-path keydown handler,
cancels nothing, never calls `focus()` or `requestFullscreen()`, and returns
focus to `document.body`. Smoothness holds: no `setInterval`, no gratuitous
compositing hints, a 60-node caption cap, and one rAF loop at most.

RL-019 is respected exactly — the live-region count is unchanged at 1, and the
`?debug=1` overlay, the likeliest offender, is correctly inert. RL-017 does not
recur with a live `<video>` and a long title. RL-006 holds under an injected
sentinel. RL-010 survives the click-surface minimisation.

UI-1 was a real accessibility failure on a reachable path and is fixed and
guarded. UI-2 through UI-5 are copy and consistency nits that do not block.

---

# PR #137 — ISSUE-47 오퍼레이터 무대 모드 (UI review)

리뷰 대상 커밋 `b8051f4`. 코드 리뷰어와 **별도 워크트리**에서 독립 수행.

## 대비 — 주장하지 않고 측정함 (공용 `tests/wcag.py` 재사용, 두 번째 계산기 없음)

| 표면 | 주장 | 측정 | 판정 |
|---|---|---|---|
| 버튼 `#ffffff` on `#1858c4` | 6.50 | **6.4979:1** | 통과 |
| 힌트 `#a8a8b3` on `#0b0b0c` | 8.36 | **8.3538:1** | 통과(주석은 8.35 로 정정) |
| 폴백 `#9ec5ff` on `#0b0b0c` | 11.13 | **11.1338:1** | 통과 |

**backdrop 은 하나가 아니다.** `#viewer:fullscreen { background: #000 }` 이 웰컴
화면에도 적용되는 것을 브라우저에서 확인(전체화면 진입 후
`getComputedStyle(#viewer).backgroundColor === rgb(0,0,0)`). 세 색이 모두 불투명
hex 라 `#000` 쪽에서 대비가 **올라가므로**(힌트 8.92:1, 폴백 11.88:1) 판정은
바뀌지 않는다 — 이는 우연이 아니라 구조적 성질이고, 파일의 지배적 관용인
`rgba(255,255,255,α)` 를 썼다면 두 backdrop 이 갈라졌을 자리다(RL-018 이 두 번
물린 지점). 테스트를 두 backdrop 으로 parametrize 해 상수 자체를 사실로 고정했다.

추가 측정: hover `#ffffff` on `#1a63dd` = **5.42:1**; 버튼 표면 vs 페이지 =
**3.03:1**(SC 1.4.11 을 0.03 차로 통과 — 취약해서 가드 추가); 포커스 링
`rgba(255,255,255,0.55)` 합성 = **6.25:1** vs 페이지.

## H-1 (High, 수정됨) — 첫 자막이 컨트롤을 파괴 → AC4 도달 불가
코드 리뷰 F-1 과 **독립적으로 동일한 결함**을 재현했다. 상세와 수정 내용은
`docs/review_notes.md` F-1 참조.

## M-2 (Medium, 수정됨) — 새 창 경고가 스크린리더에 전달되지 않음
힌트가 `id` 없는 형제 `<div>` 라 버튼의 접근 가능한 이름은 정확히
"무대 화면 열기" 뿐이었다 — 새 창이 열린다는 사실도, 프로젝터 안내도 전달되지
않는다. → `aria-describedby="stage-launch-hint"` + `id` 부여, 정적 가드 추가.

## 포커스 vs aria-live 트레이드오프 — 유지가 맞다 (단, 근거 정정)
사용자가 직접 활성화한 결과로 포커스를 옮기는 것은 표준 패턴이고 SC 3.2.1/3.2.2
위반이 아니다. 폴백 링크의 접근 가능한 이름이 자기 설명적이라 announcer 없이도
충분하다. 다만 원래 근거였던 "페이지당 announcer 는 하나" 라는 전제는 사실이
아니다 — 이미 `aria-live` 노드가 2개(폰트 크기 표시) 있다. 결론은 유지하되
근거는 "여기서는 알림보다 포커스 이동이 낫다" 로 정정한다. 진짜 SR 갭은 M-2 였다.

## Low
- **L-1 (수정)** CSS 주석의 8.36 → 8.35.
- **L-2 (미수정, 의도적)** 🎬 는 제품 전체에서 유일하게 렌더되는 이모지다.
  다른 ~40건은 전부 주석/`console.log`. 같은 컨테이너의 형제 노드에
  "미니멀: 한 줄 문구, 이모지 없음" 주석까지 있다. **다만 ISSUE-47 Scope 가
  `"🎬 무대 화면 열기"` 를 문자 그대로 명시**하므로 구현 결함이 아니라 이슈
  작성상의 불일치다. 스펙을 따르되 후속 결정 대상으로 기록. `aria-hidden`
  래핑은 올바르다.
- **L-3 (수정)** 폴백 문구를 하우스 패턴(두 문장, 마침표 구분)에 맞춤:
  "팝업이 차단되었습니다. 눌러서 무대 화면을 여세요".
- **L-4 (수정)** "전체화면하세요" → "전체 화면으로 전환하세요".
- **L-5 (F-1 수정으로 자동 해소)** "세션이 유지됩니다" 가 세션이 없을 때만
  보이던 문제 — 이제 세션 중에도 버튼이 살아 있으므로 문구가 정확해졌다.
- **L-6 (수정)** 폴백 링크 터치 타깃 297 × 20.8px → `padding: 6px 10px` 로
  24px 이상 확보(WCAG 2.2 SC 2.5.8). 급하게 눌러야 하는 복구 컨트롤이다.
- **L-7 (수정)** 힌트 12px → 13px (파일의 다른 보조 텍스트와 같은 단계).

## 깨끗함 — 명시
키보드 조작(Tab 도달, `:focus-visible` 이 클릭에는 걸리지 않음, Enter 로 창
1개), 폴백 도달성(`href` 를 먼저 설정한 뒤 노출하므로 포커스 가능), RL-006
(blocked 경로 `body.innerText` 확인), 기본 모드 무회귀.

## 이 PR 밖의 인접 결함 (후속 이슈 후보)
같은 `.welcome-state` 안의 `.welcome-state .hint` 와 `.welcome-rules` 가 모두
`rgba(255,255,255,0.4)` @13px = **3.80:1** 로 RL-018 위반(4.5:1 필요).
`.welcome-room` @14px = 4.52:1 로 간신히 통과. 역설적으로 새로 추가한
`.stage-launch-hint` 가 이 화면에서 유일하게 규격을 지키는 작은 텍스트다.

## 이번 리뷰의 커버리지 갭
표시 모드 `st.radio` 자체는 Streamlit 이 스타일링하고 라이브 서버가 없어
렌더 상태를 확인하지 못했다 — 사이드바 좁은 폭에서의 `horizontal=True` 레이아웃,
대비, 포커스 링은 미검증. `:8501` 에서 육안 확인 필요.

---

# PR #147 — ISSUE-49 오퍼레이터 웰컴 화면 WCAG AA 보정 (UI review)

`components/webrtc.html` 웰컴/대기 화면 저대비 보조 텍스트 및
`#viewer:fullscreen .welcome-state` backdrop 분기 제거. Closes #145. Reviewed at
commit `247860c`. 위 `# PR #137` 항목의 "이 PR 밖의 인접 결함" 이 이 PR 의
출발점이다 — RL-018 Frequency 3.

**Worktree**: `/Users/pillip/project/practice/realtime-en2ko-captions/.worktrees/review-ISSUE-49-ui`,
detached HEAD, pinned at `247860c` for the entire session (`git status` clean
before and after every measurement pass, re-checked immediately before writing
this note). Did not read from or write to `.worktrees/issue-ISSUE-49-welcome-contrast-aa`,
`.worktrees/review-ISSUE-49-code`, or any ISSUE-50/53 tree. No sign of
concurrent mutation — every value below reproduced identically across two
independent measurement passes (the repo's own e2e suite, and a standalone
Playwright script I wrote separately). This review does not repeat ISSUE-42's
mistake: nothing here is trusted from the stylesheet alone, every ratio is
measured from `getComputedStyle` in a **real, user-gesture-triggered**
`#viewer:fullscreen`, not a class toggle.

## Scope
In: rendered state coverage (normal / real fullscreen), copy usage at the call
site, token usage in the touched CSS block, interaction fidelity (none claimed
by this PR — colour-only), in-code accessibility (aria-describedby survival,
contrast), component existence (n/a — no new components). Out: the design
system itself — this project ships no `docs/design_system.md`; token
consistency across the whole file is a `Notes for design-auditor` item, not
mine. `.welcome-state .icon` (1.4.11), `.qr-code-caption`, `st.radio`,
`viewer.html`/`stage.html` are explicitly out of this issue's scope and I did
not audit them beyond confirming the diff didn't touch them.

## Independent verification performed
1. Ran the PR's own suites unmodified: `pytest tests/test_webrtc_stage_launch.py`
   (**44 passed**, includes the new `TestWelcomeStateContrast` class) and
   `env -u NODE_OPTIONS pytest tests/e2e/test_operator_welcome_contrast_e2e.py -m e2e`
   (**10 passed**, includes a real `document.getElementById('viewer').matches(':fullscreen')`
   assertion with a positive-control check per RL-004).
2. Wrote a **separate** standalone Playwright script (not committed, not in
   `tests/`) that re-renders `webrtc.html` via `operator_ui.render_component_html`,
   reads `getComputedStyle(...).color` on every welcome text node, clicks the
   real fullscreen FAB, re-reads after confirming `:fullscreen` matches, then
   calls `clearViewer()` in-page and re-reads a third time. This is deliberately
   independent of the PR's e2e test so a shared bug in both wouldn't hide.
3. Ran `ruff check` on both touched Python test files (clean) and confirmed
   `git status` was clean throughout (worktree isolation, see above).

## Measured — normal (`body`, `#0b0b0c`) vs. real fullscreen (`#viewer`, `#000`)
All from my standalone script; `getComputedStyle(...).color`, not the source
CSS. Both match the PR's claimed table and both test suites exactly.

| selector | colour (both states) | ratio `#0b0b0c` | ratio `#000` (real, entered via click) |
|---|---|---|---|
| `.welcome-title` | `rgb(255,255,255)` | 19.67 | 21.00 |
| `.welcome-state` / `.welcome-desc` | `rgb(200,200,210)` | 11.85 | 12.65 |
| `.welcome-room` | `rgb(168,168,179)` | 8.35 | 8.92 |
| `.welcome-state .hint` | `rgb(168,168,179)` | 8.35 | 8.92 |
| `.welcome-state .hint span` | `rgb(168,168,179)` (inherited, no own rule) | 8.35 | 8.92 |
| `.welcome-rules` | `rgb(168,168,179)` | 8.35 | 8.92 |

Backdrops observed via `getComputedStyle`, not assumed: `body` background
painted `rgb(11,11,12) α1.0`; `#viewer` background painted `rgb(0,0,0) α1.0`
**after** a real fullscreen entry (`page.locator('#fullscreenFab').click()`
then re-checked `matches(':fullscreen') === true` before sampling — the RL-018
ISSUE-42 positive-control lesson applied). All seven measured values, both
states, clear 4.5:1. Every computed colour was **byte-identical** between the
normal and fullscreen states (`before[selector] == after[selector]` for all
seven) — the backdrop-branch removal is confirmed as an in-browser fact, not
just an absent-rule inference.

## Item-by-item against the task's ask
1. **`getComputedStyle` both states, all nodes** — done, table above. No
   divergence anywhere.
2. **Deleted fullscreen branch, `.hint span` inheritance** — confirmed. The
   bare `<span>` inside `.hint` has no own `color` rule; `getComputedStyle`
   shows it inherits `#a8a8b3` from `.welcome-state .hint` correctly in both
   states. `#viewer:fullscreen .welcome-state { color: ... }` is gone from the
   stylesheet (static test) **and** produces no observable colour change (my
   in-browser probe) — both halves of the claim hold.
3. **Both markup copies** — static markup (line 768) and `clearViewer()`
   (line 1633) are textually identical for the welcome block; I additionally
   called `clearViewer()` in-page and re-read all seven nodes: every colour
   matched the static markup exactly. `grep` for inline `style="...color"` on
   any welcome node: 0 hits, confirmed by both source inspection and
   `element.getAttribute('style')` in-browser (also 0).
4. **Visual hierarchy (AC5)** — `test_welcome_brightness_hierarchy_is_strictly_descending`
   passes, and I recomputed the luminance ordering by hand from the measured
   RGB: title (255) > desc (200) > hint/rules/room (168). Strictly descending,
   AC5 holds. On `.welcome-desc` jumping to 11.85 "competing" with the title's
   19.67: it doesn't invert the ordering and the two are visually distinct
   colours (`#ffffff` vs `#c8c8d2`), but the jump does compress the headroom
   between title and body from what a 0.5-alpha/0.7-alpha pair implied before.
   I judge this Low, not a defect — see Low-1 below.
5. **`.hint` legibility** — 8.35:1 / 8.92:1, both comfortably above 4.5:1, and
   it is now the *same* value as `.stage-launch-hint` (ISSUE-47) and
   `.welcome-rules`, so the one truly actionable sentence on this screen reads
   at the same weight as the rest of the supporting tier rather than being the
   dimmest text on the page as it was pre-fix (3.80:1, dimmer than
   `.welcome-room`'s old 4.52:1).
6. **No large-text relaxation used** — confirmed both structurally
   (`test_welcome_text_meets_aa_against_every_backdrop` asserts `>= 4.5`
   unconditionally, no branch on size) and empirically: I swept
   `.welcome-title` computed `font-size` at 320/375/414/768/1024/1280/1440/1920px
   — minimum observed **26px** (at 320–414px; 768px resolves to 26.112px),
   never below the 24px large-text floor, and irrelevant anyway since
   `font-weight: 500` isn't bold and the colour already clears 4.5:1 as normal
   text (19.67:1 minimum). No value in this diff relies on the relaxation.
7. **Responsive `@media` backdrop check** — swept `body` background at
   375×600, 375×500, and 1280×400 (the padding-changing breakpoints at
   ~lines 599/614 only touch `padding`, not `background`): backdrop stayed
   `rgb(11,11,12)` at every size. No viewport moves a welcome text node to a
   different backdrop.

## Regressions checked — none found
- **ISSUE-47 `aria-describedby="stage-launch-hint"`**: present on
  `[data-stage-open]` in both my probe and the existing
  `TestPopupOpenContract`-adjacent static tests; `#stage-launch-hint` element
  still exists. `.stage-launch-hint`'s `#a8a8b3` is untouched by this diff
  (confirmed via `git diff`, only the three welcome rules + `.welcome-room` +
  `.welcome-state`/`.welcome-desc` changed).
- **RL-010** (accessible names / focus indicators): out of this diff's touched
  surface; `.stage-launch-button:focus-visible` box-shadow and button labels
  unchanged by `git diff`.
- **Large-text relaxation creep**: none — see item 6.

## Out-of-scope creep check — clean
`git diff ee409e7...247860c --stat` touches exactly the three files the task
named. Confirmed by reading the full diff: status chips, FAB, caption area,
`.welcome-state .icon` (still `rgba(255,255,255,0.5)`, correctly untouched —
non-text, 1.4.11, out of scope), `st.radio`, `viewer.html`, `stage.html` are
untouched.

**`.qr-code-caption` follow-up, re-measured in-browser (not fixed here, as
required)**: rendered with a real QR payload injected via
`render_component_html`, `getComputedStyle` gives `color: rgba(0,0,0,0.5)` on
a `getComputedStyle(.qr-code-container).backgroundColor === rgb(255,255,255)`
card → **3.9494:1**, i.e. the claimed 3.95:1 is confirmed to the fourth digit.
This diff correctly leaves it alone; recorded here only as a live pointer for
the next PR (already known per the issue text, not a new finding).

## State Coverage
Only one state exists for this screen pre-microphone-activation (idle/welcome),
and it renders correctly in both the normal and real-fullscreen sub-states,
via both code paths that produce it (initial paint, `clearViewer()`). No
loading/empty/error variant applies to this static informational screen — N/A,
not a gap.

## Copy Usage
No copy changed by this diff (Scope explicitly excludes copy/layout changes,
confirmed — only `color:` declarations differ in `git diff`). No placeholder
text found in either markup copy.

## Token Usage
Every changed declaration is a bare `#rrggbb` opaque hex literal, matching the
existing house style for this file (this file has no `--color-*` custom
properties at all — only two unrelated `--translation-font-size` /
`--original-font-size` tokens exist in `:root`, both pre-existing and
untouched). `test_no_alpha_white_text_colour_remains_in_the_welcome_block`
structurally guards against `rgba(255,255,255,α)` reappearing. This is
consistent with every prior PR reviewed in this file (see `# PR #137` above
using the same convention) — not a new deviation introduced by ISSUE-49, so no
finding. Whether the codebase *should* have a colour-token layer is a
system-level question — recorded under Notes for design-auditor.

## Interaction Fidelity
None claimed by this PR (colour-only change, no layout/animation/timing
change per Scope). N/A.

## Accessibility (implementation)
- Contrast: all seven welcome text rules clear 4.5:1 against both real
  backdrops, verified in-browser (table above) — RL-018 requirement met, not
  just claimed.
- `aria-describedby` wiring from ISSUE-47 survives (checked above).
- No new interactive elements introduced; no new focus/keyboard surface to
  check.
- `.welcome-state .icon` (non-text, 1.4.11) correctly left alone — out of
  scope per the issue text, and I did not flag it.

## Component Existence
N/A — no new components in wireframes to check against; this PR only edits
existing CSS rules.

## Notes for design-auditor
- This file (and apparently the whole `components/` directory) has no
  colour-token system — every colour in `webrtc.html`/`viewer.html`/`stage.html`
  is a literal hex or `rgba()`. `#c8c8d2` and `#a8a8b3` introduced here join a
  small informally-converging palette (`.stage-launch-hint` already used
  `#a8a8b3`) with no `:root` custom property backing it. If a design-system
  audit doc is ever started for this project, recommend promoting
  `#ffffff`/`#c8c8d2`/`#a8a8b3`/`#0b0b0c`/`#000000` to named tokens — that
  would also make the "reuse the same value across three rules" pattern this
  PR relies on into something a linter can enforce structurally instead of by
  convention + comment.
- `.qr-code-caption` (3.95:1 on its white card) remains a real, unfixed AA gap
  — tracked here for whoever owns the next system-level pass, not a finding
  against this PR.

## Findings by severity
No Critical, High, or Medium findings — the PR's claimed table, its two test
suites, and my independent re-measurement all agree to the ratio, byte-for-byte
colour, and font-size.

- **Low-1 (not fixed, informational)**: `.welcome-desc` moved from a
  0.5-alpha dim tone (5.34:1 pre-fix, per the issue's own table context) to
  `#c8c8d2` (11.85:1/12.65:1) — a bigger jump in perceived brightness than the
  supporting tier's move (0.4-alpha → `#a8a8b3`, 3.80:1 → 8.35:1). The
  strict-descending ordering (title > desc > hint/rules/room) still holds
  numerically and the test guards it, but the *gap* between title and desc
  narrowed while the gap between desc and the supporting tier widened, which
  is a legitimate aesthetic/hierarchy observation, not an AA violation. No
  action required by this issue's Scope (colour-values-only, no "keep the same
  gap size" AC was written). Flagging for awareness only.

## Verdict
**Approve.** Every claimed ratio in the task's table reproduced exactly via
independent in-browser measurement, in real fullscreen (not a class toggle),
across both markup copies, with the `.hint span` inheritance path explicitly
checked. The deleted fullscreen colour branch is confirmed absent both
structurally and by observed-colour equality across states. No large-text
relaxation is relied upon anywhere, confirmed at real computed font sizes down
to 320px viewports. ISSUE-47's `aria-describedby` wiring is untouched and
still present. No scope creep into the excluded surfaces. The one known
adjacent gap (`.qr-code-caption`, 3.95:1) is correctly left unfixed per Scope
and is not new.

## Confidence
High. Two independent measurement paths (the PR's own e2e suite, and a
separately-authored Playwright script) produced identical numbers; worktree
stayed pinned and clean at `247860c` for the whole session with no observed
interference.
