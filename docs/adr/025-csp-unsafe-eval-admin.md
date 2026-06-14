# ADR-025: CSP-`unsafe-eval` nur auf `/admin-mgmt/`

- **Status:** Accepted
- **Date:** 2026-04-28
- **Deciders:** Tobias Nix
- **Refs:** #695, #785, #788
- **Updated:** 2026-06-13 — Relax nur noch für `text/html`-Responses (Refs #1084); JSON-/CSV-Antworten unter `/admin-mgmt/` behalten die strikte globale CSP. Verschriftlicht aus `security-notes.md` als ADR (Refs #1071).

## Context

Die globale CSP setzt `script-src 'self'` — kein `'unsafe-eval'`, kein `'unsafe-inline'`. Alle App-Templates nutzen den `@alpinejs/csp`-Build mit registrierten `Alpine.data()`-Komponenten. Ausnahme ist die Django-Admin-UI auf `/admin-mgmt/`: das gevendorte `django-unfold`-Theme (0.91.0) liefert >20 Templates mit Inline-Function-Calls (`x-data="searchCommand()"`) **und** Inline-Object-Expressions (`x-data="{rowOpen: false}"`). Beide Muster scheitern unter dem strikten `@alpinejs/csp`-Build, weil Alpine sie nur per dynamischer Code-Auswertung (`new AsyncFunction()`) parst — unter `script-src 'self'` ohne `'unsafe-eval'` blockiert. Folge ohne Workaround: Admin-Modals (Cmd+K-Suche `searchCommand`) initialisieren nicht, E2E-Admin-Tests brechen (#695, E2E-Flakiness nach v0.10.1).

## Decision

`'unsafe-eval'` wird **per-Request nur für `/admin-mgmt/` ergänzt**, nicht global. Die [`AdminCSPRelaxMiddleware`](../../src/core/middleware/admin_csp_relax.py) schreibt — nach der `CSPMiddleware` registriert — den `Content-Security-Policy`(-Report-Only)-Header um und hängt `'unsafe-eval'` an `script-src` an, **ausschließlich** wenn (a) der Pfad mit `/admin-mgmt/` beginnt **und** (b) der Response-`Content-Type` mit `text/html` beginnt. JSON-/CSV-Antworten (Autocomplete, Exporte) unter `/admin-mgmt/` führen kein Script aus und behalten die strikte globale CSP (Refs #1084). Für alle anderen Routen (Login, Klient-CRUD, Zeitstrom, Statistik) bleibt `script-src 'self'` unverändert.

Vertretbar, weil `/admin-mgmt/` ohnehin eng abgesichert ist: nur Rollen `super_admin`/`facility_admin` (Custom `AnlaufstelleAdminSite`, #785), Sudo-Mode-Pflicht, Facility-Scoping + RLS, MFA-Pfad (#788), AuditLog auf alle Schreib-Aktionen.

## Consequences

- **+** Strikte CSP (`script-src 'self'`) bleibt für die gesamte fachliche App erhalten — kein Remote-Script-Loading, kein `unsafe-inline`.
- **+** `django-unfold` läuft ohne Fork/Template-Override; kein laufender Wartungs-Tax bei Unfold-Updates.
- **+** Angriffsfläche der Lockerung ist eng: nur HTML unter `/admin-mgmt/`, nur für authentifizierte Admin/Lead-Rollen mit Sudo + MFA.
- **−** `script-src` auf Admin-Routen ist nicht mehr „strict" — externe Audit-Reports werden `'unsafe-eval'` flaggen.
- **−** Eine hypothetische XSS-Lücke in einem Admin-Template hätte dort größeren Sprengradius. Mitigation: Architektur-Tests verbieten repo-weit `|safe`, `mark_safe()`, `csrf_exempt` und Inline-`<script>` ([`src/tests/test_architecture_guards_*.py`](../../src/tests/)).

## Alternatives considered

- **20+ Unfold-Templates überschreiben + Shim-JS mit `Alpine.data()`-Re-Implementierungen.** Verworfen für jetzt: 1–2 Tage Initialaufwand plus Wartungs-Tax bei jedem Unfold-Update; im Issue vertagt.
- **`'unsafe-eval'` global erlauben.** Verworfen: gäbe die strikte CSP der gesamten App auf, um ein reines Admin-UI-Problem zu lösen.
- **Klassischer Alpine-Build überall.** Verworfen: hebt den `@alpinejs/csp`-Schutz für die fachlichen Routen auf.

## Re-Evaluation (Trigger)

Workaround zurücknehmen (Template-Override-Pfad), wenn: `django-unfold` upstream einen `@alpinejs/csp`-kompatiblen Build liefert; ein externer Audit „strict CSP überall" als verbindliches Kriterium fordert; die Admin-UI-Fläche signifikant wächst (z. B. geplante Custom-Admin-UI); oder die XSS-Vorbedingungen (Architektur-Guards) für Admin-Templates aufgeweicht würden.

## References

- [`src/core/middleware/admin_csp_relax.py`](../../src/core/middleware/admin_csp_relax.py) — Pfad- + `text/html`-gegateter Header-Rewrite
- [`docs/security-notes.md` § CSP `'unsafe-eval'`](../security-notes.md) — ausführliche Trade-off- und Threat-Model-Notiz
- [`docs/threat-model.md` § TB1](../threat-model.md) — Session-Hijack-via-XSS-Bewertung
- Issues #695 (Ursprung), #1084 (`text/html`-Gating), #785 (Admin-Zugriff), #788 (MFA)
