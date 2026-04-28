/** @type {import('tailwindcss').Config} */
const defaultTheme = require('tailwindcss/defaultTheme');

// Dynamische Badge-Farben (core_tags.py: _BADGE_COLOR_MAP) — Tailwind kann sie
// im Content-Scan nicht entdecken, daher als Safelist eintragen.
const BADGE_COLORS = ['indigo', 'amber', 'red', 'green', 'blue', 'purple', 'teal', 'rose', 'gray', 'yellow'];
const BADGE_SAFELIST = BADGE_COLORS.flatMap((c) => [`bg-${c}-100`, `text-${c}-800`, `text-${c}-700`, `bg-${c}-50`]);

// Dynamische due_date_info-css_class-Farben (core/utils/dates.py) — emittiert
// text-{red,amber,yellow,gray}-{400..600} fuer Faelligkeits-Stati.
const DATE_STATUS_SAFELIST = [
  'text-red-600', 'text-amber-600', 'text-amber-500',
  'text-yellow-600', 'text-gray-400', 'text-gray-500',
];

module.exports = {
  content: [
    "./src/templates/**/*.html",
    "./src/core/**/*.py",
  ],
  safelist: [...BADGE_SAFELIST, ...DATE_STATUS_SAFELIST],
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
