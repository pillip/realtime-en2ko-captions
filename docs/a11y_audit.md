# Accessibility Audit Report (WCAG 2.1 AA)

**Audit Date**: 2026-03-19
**Auditor**: Claude Opus 4.6 (automated)
**Scope**: Fullscreen feature in PR #12 -- specifically `#fullscreenFab`, `#settingsFab`, `.fs-font-controls`, and `:fullscreen` CSS state
**File**: `/Users/pillip/project/practice/realtime-en2ko-captions/components/webrtc.html`

---

## Summary

- **WCAG conformance**: Partial (multiple AA failures)
- **Total findings**: 14
- **Critical**: 3 | **High**: 5 | **Medium**: 4 | **Low**: 2
- **Audit scope**: `components/webrtc.html` (lines 1--1055+), `docs/review_lessons.md`
- **Confidence**: High (contrast ratios computed via WCAG relative luminance formula; all findings verified against source code)

---

## Findings

### Perceivable

| # | Criterion | Severity | Finding | Location | Fix |
|---|-----------|----------|---------|----------|-----|
| P-1 | 1.4.3 Contrast (Minimum) | **Critical** | `.fs-font-controls` default opacity is `0.15`. At this opacity, all child text (labels, buttons, size displays) becomes effectively invisible against the `#000` fullscreen background. Computed contrast ratios: button text `#fff` at 0.15 opacity = **1.39:1** (required: 4.5:1); `.fs-font-size` text `rgba(255,255,255,0.7)` at 0.15 opacity = **1.21:1** (required: 4.5:1). These are 11px text -- small text requiring 4.5:1. | Line 449: `opacity: 0.15;` | Change default opacity to at least `0.6` and hover to `1.0`. See Fix F-1 below. |
| P-2 | 1.4.11 Non-text Contrast | **Critical** | `.fs-font-btn` border `rgba(255,255,255,0.3)` at container opacity 0.15 yields an effective contrast of **1.07:1** against `#000` background. Interactive UI components require >= 3:1 against adjacent colors. The buttons are essentially invisible in their default state. | Line 479: `border: 1px solid rgba(255,255,255,0.3);` combined with line 449 | Same fix as P-1: increase container base opacity to 0.6. |
| P-3 | 1.4.11 Non-text Contrast | **High** | `#fullscreenFab` background `rgba(255,255,255,0.1)` blended over `#0f0f23` produces `rgb(39,39,57)`. Contrast of FAB background against page background is **1.29:1** (required: 3:1 for UI components). The button boundary is hard to perceive. | Line 423: `background: rgba(255,255,255,0.1);` | Increase to `rgba(255, 255, 255, 0.2)` or add a visible border. See Fix F-2. |
| P-4 | 1.4.11 Non-text Contrast | **High** | Same issue for `#settingsFab` -- identical background `rgba(255,255,255,0.1)` with **1.29:1** contrast against page. | Line 38: `background: rgba(255, 255, 255, 0.1);` | Same fix as P-3. |
| P-5 | 1.1.1 Non-text Content | **High** | `#settingsFab` uses emoji `⚙️` as its only content with no `aria-label`, `aria-labelledby`, or visible text. Screen readers will announce this inconsistently across platforms (e.g., "gear" or the raw Unicode codepoint). | Line 713: `<button class="settings-fab" id="settingsFab">⚙️</button>` | Add `aria-label="설정"`. See Fix F-3. |
| P-6 | 1.1.1 Non-text Content | **High** | `#fullscreenFab` uses Unicode character `⛶` (U+26F6, SQUARE FOUR CORNERS) which many screen readers will not announce meaningfully. The `title` attribute is not a reliable accessible name. | Line 715: `<button class="fullscreen-fab" id="fullscreenFab" title="전체화면">⛶</button>` | Add `aria-label="전체화면"`. See Fix F-4. |
| P-7 | 1.1.1 Non-text Content | **Medium** | The four `.fs-font-btn` buttons use plain text characters (`−` and `+`) with no `aria-label`. Screen readers cannot convey which font size each button controls. | Lines 783--791 | Add `aria-label` to each button. See Fix F-5. |

### Operable

| # | Criterion | Severity | Finding | Location | Fix |
|---|-----------|----------|---------|----------|-----|
| O-1 | 2.5.8 Target Size (Minimum) | **High** | `.fs-font-btn` has a fixed size of **32x32px**, which is below the WCAG 2.1 AA recommended minimum of 44x44 CSS pixels (and below the 2.5.8 Level AAA target of 44x44, though AA specifies 24x24 minimum with sufficient spacing). At 32px with only 6px gap between adjacent buttons, effective target area is inadequate for motor-impaired users. | Line 477-478: `width: 32px; height: 32px;` | Increase to `44px` or add at least `8px` padding around each button. See Fix F-6. |
| O-2 | 2.5.8 Target Size (Minimum) | **Medium** | At `max-height: 600px` media query, both FABs shrink to **40x40px**. While this exceeds 24x24 AA minimum, it is below the 44x44 best practice and may cause difficulties on touch devices. | Lines 700-704 | Maintain minimum 44x44px even in compact mode. |
| O-3 | 2.4.7 Focus Visible | **Critical** | No `:focus` or `:focus-visible` styles are defined for `.fullscreen-fab`, `.settings-fab`, or `.fs-font-btn`. The `border: none` on FABs removes any default focus ring. Keyboard users cannot see which element is focused. | Lines 417-440 (no focus styles), line 424: `border: none;` | Add visible focus indicators. See Fix F-7. |
| O-4 | 2.1.1 Keyboard | **Medium** | The settings panel (`#settingsPanel`) opens/closes via click handler on `#settingsFab` but there is no keyboard-specific handling. While `<button>` elements are natively keyboard-accessible (Enter/Space), the panel close behavior (click outside) has no keyboard equivalent (Escape key). | Lines 1047-1054 | Add Escape key handler to close the settings panel. See Fix F-8. |
| O-5 | 2.3.1 Three Flashes or Below | **Low** | Multiple animations (`pulse`, `breathe`, `typing-breathe`, `cursor-blink`) run continuously with no `prefers-reduced-motion` media query anywhere in the file. While none appear to flash more than 3 times per second, users who are sensitive to motion will experience discomfort. | Lines 357-370, 385-388, 411-413 | Add `prefers-reduced-motion: reduce` media query. See Fix F-9. |

### Understandable

| # | Criterion | Severity | Finding | Location | Fix |
|---|-----------|----------|---------|----------|-----|
| U-1 | 3.1.1 Language of Page | **Medium** | `<html>` tag has no `lang` attribute. The page content is primarily Korean. Screen readers will use their default language model, which may mispronounce Korean text. | Line 2: `<html>` | Change to `<html lang="ko">`. See Fix F-10. |
| U-2 | 3.3.2 Labels or Instructions | **Low** | `.fs-font-controls` labels "번역" and "원문" are bare text spans not programmatically associated with the buttons they describe. The relationship between label and button group is visual only. | Lines 782, 788 | Wrap in a `<fieldset>` with `<legend>` or use `aria-labelledby`. See Fix F-11. |

### Robust

| # | Criterion | Severity | Finding | Location | Fix |
|---|-----------|----------|---------|----------|-----|
| R-1 | 4.1.2 Name, Role, Value | **Medium** (partially covered in P-5/P-6) | The `#settingsPanel` slide-in panel has no ARIA attributes to communicate its state. It should have `role="dialog"` or `role="region"` with `aria-label`, and `aria-expanded` on the trigger button. | Lines 718, 713 | Add ARIA attributes to panel and trigger. See Fix F-12. |

---

## Fix Summary

### F-1: Increase `.fs-font-controls` base opacity (P-1, P-2)

```css
/* BEFORE */
.fs-font-controls {
  opacity: 0.15;
  transition: opacity 0.4s ease;
}

.fs-font-controls:hover {
  opacity: 0.85;
}

/* AFTER */
.fs-font-controls {
  opacity: 0.6;
  transition: opacity 0.4s ease;
}

.fs-font-controls:hover,
.fs-font-controls:focus-within {
  opacity: 1.0;
}
```

At opacity 0.6, the white button text achieves approximately **8.4:1** contrast against `#000`, well above the 4.5:1 requirement. The `:focus-within` selector ensures keyboard users also trigger the full-opacity state.

### F-2: Improve FAB boundary contrast (P-3, P-4)

```css
/* BEFORE */
.settings-fab {
  background: rgba(255, 255, 255, 0.1);
  border: none;
}

.fullscreen-fab {
  background: rgba(255, 255, 255, 0.1);
  border: none;
}

/* AFTER */
.settings-fab {
  background: rgba(255, 255, 255, 0.15);
  border: 1px solid rgba(255, 255, 255, 0.25);
}

.fullscreen-fab {
  background: rgba(255, 255, 255, 0.15);
  border: 1px solid rgba(255, 255, 255, 0.25);
}
```

### F-3: Add accessible name to settings FAB (P-5)

```html
<!-- BEFORE -->
<button class="settings-fab" id="settingsFab">⚙️</button>

<!-- AFTER -->
<button class="settings-fab" id="settingsFab" aria-label="설정">⚙️</button>
```

### F-4: Add accessible name to fullscreen FAB (P-6)

```html
<!-- BEFORE -->
<button class="fullscreen-fab" id="fullscreenFab" title="전체화면">⛶</button>

<!-- AFTER -->
<button class="fullscreen-fab" id="fullscreenFab" aria-label="전체화면" title="전체화면">⛶</button>
```

### F-5: Add accessible names to font control buttons (P-7)

```html
<!-- BEFORE -->
<button class="fs-font-btn" id="fsTransMinus">−</button>
<button class="fs-font-btn" id="fsTransPlus">+</button>
<button class="fs-font-btn" id="fsOrigMinus">−</button>
<button class="fs-font-btn" id="fsOrigPlus">+</button>

<!-- AFTER -->
<button class="fs-font-btn" id="fsTransMinus" aria-label="번역 글자 크기 줄이기">−</button>
<button class="fs-font-btn" id="fsTransPlus" aria-label="번역 글자 크기 키우기">+</button>
<button class="fs-font-btn" id="fsOrigMinus" aria-label="원문 글자 크기 줄이기">−</button>
<button class="fs-font-btn" id="fsOrigPlus" aria-label="원문 글자 크기 키우기">+</button>
```

### F-6: Increase font control button touch target (O-1)

```css
/* BEFORE */
.fs-font-btn {
  width: 32px;
  height: 32px;
}

/* AFTER */
.fs-font-btn {
  width: 44px;
  height: 44px;
  font-size: 20px;
}
```

### F-7: Add focus indicators to all interactive elements (O-3)

```css
/* ADD to stylesheet */
.settings-fab:focus-visible,
.fullscreen-fab:focus-visible,
.fs-font-btn:focus-visible {
  outline: 2px solid #3b82f6;
  outline-offset: 2px;
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.3);
}

/* Ensure focus ring is not hidden by border:none */
.settings-fab:focus:not(:focus-visible),
.fullscreen-fab:focus:not(:focus-visible) {
  outline: none;
}
```

The `#3b82f6` outline on `#0f0f23` background achieves approximately **5.3:1** contrast, exceeding the 3:1 requirement for focus indicators.

### F-8: Add Escape key handler for settings panel (O-4)

```javascript
// ADD after the existing click-outside handler (line 1054)
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && settingsPanel && settingsPanel.classList.contains('open')) {
    settingsPanel.classList.remove('open');
    settingsFab.classList.remove('active');
    settingsFab.focus();
  }
});
```

### F-9: Add prefers-reduced-motion support (O-5)

```css
/* ADD to stylesheet */
@media (prefers-reduced-motion: reduce) {
  .caption-line.typing::after,
  .caption-line.unstable,
  .caption-line.typing,
  .status-chip.connecting,
  .back-to-live {
    animation: none;
  }

  .caption-line.fade-in {
    animation: none;
    opacity: 1;
    transform: none;
  }

  .settings-fab,
  .fullscreen-fab,
  .control-group button,
  .fs-font-btn,
  .settings-panel {
    transition: none;
  }
}
```

### F-10: Add lang attribute to HTML element (U-1)

```html
<!-- BEFORE -->
<html>

<!-- AFTER -->
<html lang="ko">
```

### F-11: Add programmatic grouping to font controls (U-2)

```html
<!-- BEFORE -->
<div class="fs-font-controls" id="fsFontControls">
  <div class="fs-font-group">
    <span class="fs-font-label">번역</span>
    ...
  </div>
  <div class="fs-font-group">
    <span class="fs-font-label">원문</span>
    ...
  </div>
</div>

<!-- AFTER -->
<div class="fs-font-controls" id="fsFontControls" role="group" aria-label="전체화면 글자 크기 조절">
  <div class="fs-font-group" role="group" aria-label="번역 글자 크기">
    <span class="fs-font-label" id="fsTransLabel">번역</span>
    <button class="fs-font-btn" id="fsTransMinus" aria-label="번역 글자 크기 줄이기">−</button>
    <span class="fs-font-size" id="fsTransSize" aria-live="polite">28px</span>
    <button class="fs-font-btn" id="fsTransPlus" aria-label="번역 글자 크기 키우기">+</button>
  </div>
  <div class="fs-font-group" role="group" aria-label="원문 글자 크기">
    <span class="fs-font-label" id="fsOrigLabel">원문</span>
    <button class="fs-font-btn" id="fsOrigMinus" aria-label="원문 글자 크기 줄이기">−</button>
    <span class="fs-font-size" id="fsOrigSize" aria-live="polite">16px</span>
    <button class="fs-font-btn" id="fsOrigPlus" aria-label="원문 글자 크기 키우기">+</button>
  </div>
</div>
```

The `aria-live="polite"` on the size display spans ensures screen readers announce the new size when buttons are pressed.

### F-12: Add ARIA attributes to settings panel (R-1)

```html
<!-- BEFORE -->
<button class="settings-fab" id="settingsFab">⚙️</button>
<div class="settings-panel" id="settingsPanel">

<!-- AFTER -->
<button class="settings-fab" id="settingsFab" aria-label="설정" aria-expanded="false" aria-controls="settingsPanel">⚙️</button>
<div class="settings-panel" id="settingsPanel" role="region" aria-label="실시간 자막 설정">
```

Then update the JavaScript toggle:

```javascript
// BEFORE
settingsFab.onclick = () => {
  settingsPanel.classList.toggle('open');
  settingsFab.classList.toggle('active');
};

// AFTER
settingsFab.onclick = () => {
  const isOpen = settingsPanel.classList.toggle('open');
  settingsFab.classList.toggle('active');
  settingsFab.setAttribute('aria-expanded', isOpen);
};
```

---

## Recommendations

Priority-ordered improvements beyond minimum compliance:

1. **[Highest priority] Fix the opacity 0.15 issue (P-1, P-2)** -- The font size controls are functionally invisible to all users by default, not just those with visual impairments. This is both a usability and accessibility failure.

2. **[High priority] Add focus indicators (O-3)** -- Without these, the entire fullscreen feature is inaccessible to keyboard-only users. This is a WCAG AA failure.

3. **[High priority] Add `aria-label` to all icon/symbol-only buttons (P-5, P-6, P-7)** -- These are quick, low-risk changes that immediately improve screen reader support.

4. **[Medium priority] Add `lang="ko"` to `<html>` (U-1)** -- One-line fix with significant impact on screen reader pronunciation accuracy.

5. **[Medium priority] Add `prefers-reduced-motion` support (O-5)** -- The page has many animations. Users sensitive to motion currently have no way to disable them.

6. **[Lower priority] Consider adding `role="status"` and `aria-live="polite"` to `#status`** -- The status chip changes dynamically (e.g., "대기중" to "연결됨") but these changes are not announced to screen readers.

7. **[Lower priority] Add a keyboard shortcut for fullscreen toggle** -- Common convention is `F` key or `F11`. Document the shortcut with `aria-keyshortcuts` if implemented.

8. **[Enhancement] Update fullscreen FAB text dynamically** -- When in fullscreen, the button should indicate "exit fullscreen" rather than showing the same `⛶` symbol. Update `aria-label` to "전체화면 종료" when fullscreen is active.
