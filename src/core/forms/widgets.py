"""Geteilte Widget-CSS-Konstanten und Helper für Tailwind-Forms.

Bisher war ``INPUT_CSS`` 5x dupliziert (cases/clients/episodes/events/
workitems). Eine zentrale Definition vermeidet Drift, wenn z.B. die
Tailwind-Klassen geaendert werden. Refs #733, Audit-Massnahme #41.
"""

INPUT_CSS = "w-full bg-canvas border border-subtle rounded-md px-3 py-2 text-[13px] text-ink"
