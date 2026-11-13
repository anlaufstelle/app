# Accessibility audit — contrast addendum

This partial translation mirrors the two-level border addendum in § 2.3 of the German audit; the historical findings remain in the German source.

## Two-level borders (#2657)

`--border-soft: oklch(0.87 0.01 75)` is decorative only and must not define control boundaries relevant to WCAG 1.4.11. Cards, the tally content and filter panels/chips opt into it explicitly. The global default remains `var(--border)`. Ban notices use the full red warning border `border-ban` (`--kind-ban`): 4.963:1 against `--kind-ban-bg` and 5.468:1 against canvas. The tally title is a separate 12 px uppercase label above the content; the focus box already has a separate 12 px label.

Calculation: OKLCH → OKLab (`a=C·cos(h)`, `b=C·sin(h)`) → linear sRGB (channels clamped to 0–1); `Y=0.2126·R+0.7152·G+0.0722·B`, contrast `(Ylight+0.05)/(Ydark+0.05)`. Luminance: white `1`, canvas `0.898026`, hard border `0.261810`, soft border `0.658007`, accent `0.097304`, muted text `0.157266`. Example, border/canvas: `(0.898026+0.05)/(0.261810+0.05)=3.040:1`.

| Element | Boundary/indicator | On white | On canvas | Result |
|---|---|---|---|---|
| Text input, textarea | `--border` (default or `border-subtle`) | 3.367:1 | 3.040:1 | ≥ 3:1 |
| Select (shift, document type, contact filters) | `border-subtle` | 3.367:1 | 3.040:1 | ≥ 3:1 |
| Unchecked checkbox, radio button | `--border` / existing native appearance | 3.367:1* | 3.040:1* | CSS boundary unchanged |
| Secondary button, filter toggle | `.btn-secondary`, `--border` | 3.367:1 | 3.040:1 | ≥ 3:1 |
| Tally tile, date trigger, day arrows, inactive segment | `border-subtle` | 3.367:1 | 3.040:1 | ≥ 3:1 |
| Primary button, active segment | Accent fill/border | 7.128:1 | 6.436:1 | ≥ 3:1 |
| Keyboard focus (links, buttons, inputs, selects, textareas) | `ring-accent`, 2 px offset; feed rows: 2 px inner outline using `--accent` | 7.128:1 | 6.436:1 | ≥ 3:1 |
| Card/list borders, row separators | `--border-soft` | 1.483:1 | 1.339:1 | Decorative only |
| Column text, time, uppercase labels | `text-ink-muted` | 5.066:1 | 4.574:1 | ≥ 4.5:1 |

*These numbers describe the CSS token rather than measuring operating-system-specific native controls. This change does not alter native checkbox/radio rendering. Buttons, fields and focus indicators do not receive the soft token; automated tests pin the control colours and calculated thresholds. The following evidence supplements the token calculation:

- **A11Y-017 — shift selection:** Shift selection uses a labelled native `<select>` (`zeitstrom-shift-select`). The native control exposes its value and selection; a custom `aria-selected` tab pattern is not required there. On mobile, the same selection sits in a dialog sheet that returns focus to its trigger.
- **Tiles and the persistent button:** Tally tiles are at least 72 px tall on mobile and 64 px on desktop; the action stretched over each tile has a 44 × 44 CSS-px minimum at every width. The top-right **Quick exit**/**Leave** button remains reachable while scrolling and also has a 44 × 44 CSS-px minimum independent of breakpoints (`base.html`, `partials/_quick_exit_standalone.html`). At 200% (640 CSS px at DPR 2) `test_list_zoom_acceptance.py` measures the exit button (at least 44 × 44 CSS px, inside the viewport, not covered, with a focus indicator) as well as the tally actions, the undo bar and the number form (each at least 44 × 44 CSS px). The tally was measured with the seed staff account, not with the assistant role.
- **200% equivalent and list targets:** `test_list_zoom_acceptance.py` compares 1280 CSS px at DPR 1 with 640 CSS px at DPR 2 for Timeline, contacts, people and tasks. It checks reflow without page overflow, visible labels, field boundaries, keyboard focus and, in the narrow view, a 44 × 44 CSS-px minimum for visible fields and the sampled main-content links/buttons. That test does not explicitly require tally tiles; a separate test in the same file measures the tally actions (see above); the same applies to the persistent exit button. `test_event_list_day_groups.py` adds desktop (1280 px) and mobile (390 px) checks: contact rows have at least 44 × 44 CSS px, as do **Go to entry** and both pagination arrows on mobile; Enter/Space operation and axe scans before and after expansion are covered. Expanded targets are measured at 390 px there; at 200% `test_list_zoom_acceptance.py` checks all expanded contact cards of the first page (targets of at least 44 × 44 CSS px, no clipped text in the expanded details, no page overflow).

These checks do not replace a full WCAG assessment or assistive-technology testing. The demo pre-acceptance runs for staff and assistant as `test_demo_preacceptance.py` in the demo profile. It visits the timeline, contacts (all cards of the first page expanded), the people list, “Log out” (in the “More” menu at 390 px, in the navigation at 1280 px) and the exit button (clicked at 390 px only); the tally’s undo bar and number form follow only if the timeline shows tally tiles. At 1280 and 390 px it checks page overflow, axe violations of the `critical` and `serious` levels and the demo notice (not checked while the number form is open). The target size of at least 44 px is checked at 390 px only, for the visible links, buttons, expandable sections (`summary`), input fields and selects in the main content and for the exit button; for “Log out” only the height is checked. At 1280 px the sizes are recorded, not checked. At 390 px the check exempts only the toggle of the demo notice, 24 px high (WCAG 2.5.8 AA, demo instances only), from the 44 px goal.

<!-- translation-source: docs/accessibility-audit.md (section 2.3 addendum) -->
<!-- translation-version: v0.99.10 -->
<!-- translation-date: 2026-11-13 -->
<!-- source-hash: cdf8d4d6ebd39347df5592ca33df2eb7b1bd63a6 -->
