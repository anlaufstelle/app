# ADR-025: CSP-Lockerung (`unsafe-eval`, `style-src 'unsafe-inline'`) nur auf `/admin-mgmt/`

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

## Nachtrag (2026-10-06): Umfang der Lockerung und tatsächliche Schutzschichten (Refs #2468)

Die Decision und die Consequences begründen die Lockerung mit Schutzannahmen, die der Code nur zum Teil trägt. Der Titel nennt jetzt beide gelockerten Direktiven. Context und Decision bleiben stehen; es gilt der Ist-Stand:

- **Auch `style-src` ist gelockert** (#1660). Für HTML-Antworten unter `/admin-mgmt/` entfernt `_relax` in [`admin_csp_relax.py`](../../src/core/middleware/admin_csp_relax.py) alle Hash- und Nonce-Tokens aus `style-src` und hängt `'unsafe-inline'` an. Neben dynamischem Code (`'unsafe-eval'`) sind dort also auch beliebige Inline-Styles erlaubt. Grund: Unfold rendert den Farbblock `<style id="unfold-theme-colors">` dynamisch und nutzt `style=`-Attribute. Hashes müssen raus, weil Browser `'unsafe-inline'` neben einem Hash ignorieren.
- **Die Lockerung gilt auch anonym.** Sie greift für jede HTML-Antwort unter `/admin-mgmt/`, unabhängig vom Login, also auch für die anonym erreichbare Admin-Login-Seite `/admin-mgmt/login/`. Unfolds Skeleton lädt seinen Alpine-Build und `x-data="theme(...)"` auch dort; ohne `'unsafe-eval'` bliebe die Seite leer. Die Einschränkung auf angemeldete Requests (#1376, I3) wurde deshalb zurückgenommen (Modul-Docstring). Die Consequence „nur für authentifizierte Admin/Lead-Rollen mit Sudo + MFA“ trifft damit für die Login-Seite nicht zu.
- **Rollen:** Hinter dem Login sind nur `super_admin` und `facility_admin` zugelassen (`AnlaufstelleAdminSite._has_admin_role` in [`admin_site.py`](../../src/core/admin_site.py)), danach ist Sudo-Mode Pflicht (`has_permission`). Die Leitung (`lead`) hat keinen Admin-Zugang.
- **MFA** ist kein offener Pfad mehr: #788 ist umgesetzt. In Produktion erzwingen `super_admin` und `facility_admin` MFA sofort (`MFA_ENFORCED_ROLES_IMMEDIATE`, Standard `super_admin,facility_admin`, [`settings/base.py`](../../src/anlaufstelle/settings/base.py)).
- **Audit (#2527):** Der generische Admin-Mixin protokolliert Konfigurationsanlage, -änderung, -löschung und Inline-Änderungen mit Actor; vorhandene spezialisierte Auditpfade werden nicht doppelt geschrieben. Alle registrierten Verwaltungsmodelle tragen den Leseaudit-Mixin: Ausgabewege der Systemadministration schreiben `SYSTEM_VIEW`, bei Schreibfehlern sperren sie Inhalte mit HTTP 403. Einrichtungsgebundene Verwaltungsrollen erhalten dadurch keinen RLS-Bypass. Fachobjekte bleiben read-only; Ereignisse und Historien zeigen ausschließlich Metadaten ohne Feldinhalte.
- **Ratelimit** gilt nur für den Admin-Login-POST (`5/m` je IP, `10/h` je Benutzername, `AnlaufstelleAdminSite.login`, #2020). Die Admin-Change-Views selbst sind nicht limitiert.
- **XSS-Vorbedingungen, tatsächlich durchgesetzt:**
  - Template-Guards in [`test_architecture_guards_templates.py`](../../src/tests/test_architecture_guards_templates.py): Inline-`<script>`, Inline-Event-Attribute (`on…=`), `javascript:`-URIs, Inline-`x-data`-Objekte und unsichere Alpine-/htmx-Ausdrücke, Inline-Styles.
  - [`test_architecture_guards_safe_markup.py`](../../src/tests/test_architecture_guards_safe_markup.py): `|safe`, `|safeseq` und `{% autoescape off %}` in Templates sowie `mark_safe()`/`SafeString` im Python-Code nur mit Allowlist-Eintrag und nicht-leerer Begründung (ein Eintrag mit leerer Begründung gibt nichts frei). Kommentarblöcke endet der Guard wie Django am ersten `{% endcomment %}`. Er sieht nur statisch erkennbare Verwendungen, auch über Import-Aliase; dynamische Zugriffe wie `getattr(modul, "mark_safe")` erkennt er nicht. Erlaubt sind heute nur die zwei Klartext-Mailvorlagen (Einladung, Passwort-Reset); im Python-Code gibt es keine Ausnahme. Zusätzlich meldet Ruff `S308` jeden `mark_safe`-Aufruf (Regelgruppe `S` in `pyproject.toml`).
  - [`test_architecture_guards_csrf_exempt.py`](../../src/tests/test_architecture_guards_csrf_exempt.py): `csrf_exempt` und `_dont_enforce_csrf_checks` nur mit Allowlist-Eintrag und nicht-leerer Begründung. Einzige Ausnahme ist der CSP-Report-Endpunkt (`CSPReportView`): Browser-Reports tragen kein Token, der Endpunkt nimmt nur POST an, loggt bereinigt und ändert keinen Anwendungszustand.
  - Alle diese Guards prüfen die **eigenen** Templates unter `src/templates/` und den eigenen Python-Code. Die gevendorten Unfold-Templates prüft keiner von ihnen. Die Consequence „Architektur-Tests verbieten repo-weit `|safe`, `mark_safe()`, `csrf_exempt`“ galt bis zu diesem Nachtrag nicht; einen `|safe`-Guard gab es nicht.
- **Andere Routen sind nicht in jedem Deployment unverändert.** Auf der Demo erweitert [`DemoUmamiCSPRelaxMiddleware`](../../src/core/middleware/demo_umami_csp_relax.py) `script-src` und `connect-src` um den Umami-Host, nur mit `DEMO_MODE`, nach dem Login und für HTML. `settings/devlive.py` erweitert beide Direktiven global, sobald `UMAMI_SCRIPT_SRC` gesetzt ist. In Produktion bleiben alle Routen außerhalb von `/admin-mgmt/` bei `script-src 'self'`.
- **Reihenfolge:** `AdminCSPRelaxMiddleware` steht in `MIDDLEWARE` **vor** `csp.middleware.CSPMiddleware`, nicht danach ([`settings/base.py`](../../src/anlaufstelle/settings/base.py)). Antworten durchlaufen die Liste rückwärts, die Middleware schreibt den Header also um, nachdem `CSPMiddleware` ihn gesetzt hat.
- **Unfold-Version und Muster:** Installiert ist `django-unfold` 0.108.0 (`requirements.txt`), nicht 0.91.0. Funktionsaufrufe in `x-data` gibt es dort nur noch für `theme(...)` und `searchCommand(...)`; Objekt-Ausdrücke (`x-data="{…}"`) stehen in rund 20 Templates.
- **Re-Evaluation:** Den Abbau der Lockerung plant #2104. Die Trigger oben gelten weiter; „Architektur-Guards aufgeweicht“ meint jetzt konkret die oben genannten Guards und ihre Allowlists.

**English summary of the audit update.** The generic admin mixin audits configuration creation, update, deletion and inline changes with an actor, preserving existing specialised records without duplication. All registered admin models audit system-administrator output paths with `SYSTEM_VIEW` and deny content with HTTP 403 if the write fails. Facility-bound administration gains no RLS bypass. Substantive objects remain read-only; event/history admin pages show metadata without field contents.
