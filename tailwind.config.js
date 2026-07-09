/** @type {import('tailwindcss').Config} */
const defaultTheme = require('tailwindcss/defaultTheme');

module.exports = {
  content: [
    "./src/templates/**/*.html",
    "./src/core/**/*.py",
  ],
  // Hinweis (Refs #1480): Der frühere `safelist`-Key (dynamische Badge-/
  // Fälligkeits-Farben) ist entfernt — Tailwind v4 unterstützt `safelist` in der
  // JS-Config NICHT mehr, auch nicht via `@config`. Das Safelisting steht jetzt
  // als `@source inline(...)` in src/static/css/input.css (1:1 aus den Python-
  // Quellen _BADGE_COLOR_MAP (core/templatetags/core_tags.py) + core/utils/dates.py).
  // Der Drift-Guard src/tests/test_tailwind_safelist_drift.py hält beide synchron.
  theme: {
    extend: {
      fontFamily: {
        sans: ["'DM Sans'", ...defaultTheme.fontFamily.sans],
        mono: ["'DM Mono'", ...defaultTheme.fontFamily.mono],
      },
      colors: {
        // Theme-Tokens (Quelle: Anlaufstelle Prototype.html)
        accent: {
          DEFAULT: 'var(--accent)',
          light: 'var(--accent-light)',
        },
        canvas: 'var(--bg)',
        surface: 'var(--surface)',
        ink: {
          DEFAULT: 'var(--text-primary)',
          soft: 'var(--text-secondary)',
          muted: 'var(--text-muted)',
        },
        subtle: 'var(--border)',
        // Eintragstypen (Feed-Cards)
        event: {
          DEFAULT: 'var(--kind-event)',
          soft: 'var(--kind-event-bg)',
          deep: 'var(--kind-event-text)',
        },
        activity: {
          DEFAULT: 'var(--kind-activity)',
          soft: 'var(--kind-activity-bg)',
          deep: 'var(--kind-activity-text)',
        },
        workitem: {
          DEFAULT: 'var(--kind-workitem)',
          soft: 'var(--kind-workitem-bg)',
          deep: 'var(--kind-workitem-text)',
        },
        ban: {
          DEFAULT: 'var(--kind-ban)',
          soft: 'var(--kind-ban-bg)',
          deep: 'var(--kind-ban-text)',
        },
      },
      borderColor: {
        DEFAULT: 'var(--border)',
      },
    },
  },
  plugins: [],
};
