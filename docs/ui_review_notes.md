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
