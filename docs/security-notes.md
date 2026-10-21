# Security Notes

Dokumentiert bewusste Security-Entscheidungen, die beim Audit der Tiefenanalyse (internes Code-Audit 2026-04-21, dev-only) verifiziert und für die Roadmap festgehalten wurden. Dieses Dokument ist das „Warum wir das so lassen"-Gegenstück zur RLS- und Facility-Scoping-Doku in [CONTRIBUTING.md](../CONTRIBUTING.md#facility-scoping--row-level-security) und zum Ops-Runbook [docs/ops-runbook.md](ops-runbook.md).

---

## `core_user` bleibt RLS-frei (Finding)

**Status:** Design-Entscheidung, kein Fix notwendig.

### Beobachtung

Das `User`-Model hat einen `facility`-FK (nullable, `on_delete=SET_NULL`) in [`src/core/models/user.py`](../src/core/models/user.py), ist aber **nicht** in `DIRECT_TABLES` oder `JOIN_TABLES` der RLS-Setup-Migration [`0047_postgres_rls_setup.py`](../src/core/migrations/0047_postgres_rls_setup.py). Jede andere facility-gescopte Tabelle hat eine `facility_isolation`-Policy; `core_user` fehlt absichtlich.

### Begründung

Zwei Use-Cases sind auf Cross-Facility-Zugriff angewiesen:

1. **Login-Bootstrap.** Beim Anmelden ist die Session noch nicht an eine Facility gebunden — Django-`auth_login` sucht den User über `username` (oder E-Mail beim Password-Reset) und bindet erst danach die Einrichtung: die [`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py) setzt sie je Request als `request.current_facility` und als PostgreSQL-Session-Variable `app.current_facility_id`. Eine facility-isolierte `core_user`-Policy würde den Login von **jedem** User scheitern lassen, solange der Session-Variablen-Setzer (die [`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py)) nicht schon läuft — klassischer Chicken-and-Egg.
2. **Cross-Facility-Administration.** Die Systemadministration (`super_admin`) verwaltet Konten aller Einrichtungen (Passwort-Reset senden, Rollen anpassen, Token-Invite neu generieren). [`core.admin.users.UserAdmin`](../src/core/admin/users.py) erbt `FacilityScopedAdminMixin`; dessen `scope_to_facility` ([`admin_site.py`](../src/core/admin_site.py)) lässt `super_admin` alle Zeilen sehen und begrenzt `facility_admin` auf `request.current_facility`. Eine facility-isolierte Policy würde den installationsweiten Teil unterbinden.

### Andere Verteidigungslinien

Auch ohne RLS-Isolation auf `core_user` gilt:

- **Facility-Scoping im ORM-Layer:** Alle user-facing Views gehen über [`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py), die `request.current_facility` setzt. Queries, die User ausschließlich einer Facility liefern sollen, filtern explizit (z.B. in [`forms/workitems.py`](../src/core/forms/workitems.py): `User.objects.filter(facility=facility, ...)`).
- **Rollen-Gates:** In [`views/mixins.py`](../src/core/views/mixins.py) begrenzt `FacilityAdminRequiredMixin` die Einrichtungsverwaltung auf `facility_admin` (an die eigene Einrichtung gebunden), `SuperAdminRequiredMixin` den installationsweiten `/system/`-Bereich auf `super_admin`. Im Django-Admin scopt `FacilityScopedAdminMixin` ([`admin/mixins.py`](../src/core/admin/mixins.py)) die Querysets.
- **AuditLog:** User-Role-Changes und Deaktivierungen werden via `post_save`-Signale geloggt (siehe [`signals/audit.py`](../src/core/signals/audit.py)); der handelnde Admin wird dabei über das `actor`-Feld erfasst und in die HMAC-Audit-Kette eingebunden — der Eintrag trägt das Ziel im `user`-Feld und den Verursacher in `actor`, statt beide zu vermengen (#1369). Was die Kette nachweist, ist begrenzt: `verify_audit_chain` erkennt genau drei Fälle (Inhalt passt nicht zum `entry_hash`, `prev_hash` passt weder zur Vorgängerzeile noch zu einer authentifizierten Prune-Grenze, fehlender High-Water-Mark-Anker); Zeilen ohne `entry_hash` überspringt sie. Gegen die Owner-Rolle der Tabelle ist die Kette deshalb kein vollständiger Manipulationsnachweis — Details im [ADR-007-Nachtrag (2026-10-06)](adr/007-auditlog-append-only.md#nachtrag-2026-10-06-privilegierter-pfad-hmac-kette-tombstones-und-lockout-recovery-refs-2449).

### Verifikation

Der Retro-Audit #600 hat die Liste der RLS-geschützten Tabellen vollständig durchgegangen und `core_user` bewusst **nicht** ergänzt — zusammen mit `core_organization`, `core_facility`, `core_user_groups`, `core_user_user_permissions` bleibt die Bootstrap/Auth-Schicht außerhalb der per-Facility-Policy.

---

## `SESSION_COOKIE_SAMESITE = "Lax"` bleibt (Finding)

**Status:** Design-Entscheidung mit Rationale.

`CSRF_COOKIE_SAMESITE` ist in [`prod.py`](../src/anlaufstelle/settings/prod.py) auf `"Strict"` gesetzt, `SESSION_COOKIE_SAMESITE` bleibt bei `"Lax"`. Grund: **Password-Reset-E-Mail-Links** (Cross-Site → same-site nach Klick) müssen den Session-Cookie mitbringen, sonst landet der User auf der Login-Seite, obwohl der Token noch gültig ist. Django-Default `"Lax"` lässt genau diesen GET-Cross-Site-Flow durch, während POST-Formulare und Fetch-Requests weiter geblockt werden. `"Strict"` würde den E-Mail-Link-Flow aufbrechen.

Wenn wir den Token-Link-Flow (Invite, Passwort-Reset, 2FA-Backup-Download) jemals auf strikt trennen (eigener Host / eigene Subdomain), kann `"Strict"` greifen.

---

## AuditLog `facility_id` ist nullable (Design)

`AuditLog.facility` ist `null=True` (siehe [`models/audit.py`](../src/core/models/audit.py)), weil System-weite Events (z.B. fehlgeschlagene Logins vor dem Facility-Context) keine Facility haben. Diese Zeilen matchen die `facility_isolation`-Policy **nicht** — sie sind für reguläre App-User (`assistant`/`staff`/`lead`/`facility_admin`) unsichtbar.

**Update 2026-05-10 (Refs #867, schließt #866):** Mit Einführung der Rolle `super_admin` ist die NULL-Facility-Sichtbarkeit jetzt zusätzlich über das UI verfügbar — der `/system/`-Bereich ([`src/core/views/system/`](../src/core/views/system/)) zeigt Pre-Auth- und systemweite Einträge für die Systemadministration, gekapselt durch `SuperAdminRequiredMixin` und durch eine zusätzliche Session-Variable `app.is_super_admin='true'` (OR-Branch in den RLS-Policies). Jeder dieser Aufrufe wird im AuditLog mit der Action `SYSTEM_VIEW` protokolliert (DSGVO-Rechenschaftspflicht). Direkter psql-Pfad über `anlaufstelle_admin` (BYPASSRLS) bleibt für Forensik bestehen. Details: [ADR-007 Update 2026-05-10](adr/007-auditlog-append-only.md), [ADR-018](adr/018-rollenmodell-superadmin.md).

---

## Fachobjekte im Admin read-only + Cross-Facility-Read-Audit (Issue #1341)

Das Django-Admin (`/admin-mgmt/`, Custom `AnlaufstelleAdminSite`) hatte zwei Rechenschafts-Lücken:

- **AUTHZ-1 — Schreibpfad umgeht die Service-Schicht.** Add/Change/Delete auf `Client`/`Case`/`Event`/`WorkItem` liefen am Service-Layer vorbei und umgingen damit das Domänen-AuditLog (`CLIENT_UPDATE`/`EVENT_CREATE`/…), die `EventHistory`-Versionierung sowie die Vier-Augen-Löschung/Legal-Hold-/Retention-Workflows. Da ein Admin-Save diese Invarianten (u. a. Feld-Verschlüsselung, History-Diff) gar nicht replizieren kann, sind diese Fachobjekte im Admin jetzt **strikt read-only** ([`ReadOnlyDomainAdminMixin`](../src/core/admin/mixins.py) → `has_add/change/delete_permission = False`) — konsistent mit den bereits append-only gestellten Admins (`EventHistory`/`Attachment`/`AuditLog`/`DeletionRequest`). Konfig-Objekte ohne tamper-evidente Klientel-Historie (`DocumentType`/`FieldTemplate`/`QuickTemplate`/`TimeFilter`) bleiben schreibbar; `Settings`-Änderungen sind über `SettingsAdmin.save_model` → `log_settings_change` weiterhin auditiert.
- **AUTHZ-2 — Cross-Facility-PII-Read unauditiert.** Ein `super_admin` konnte über die Admin-Listen/Change-Views facility-übergreifend Klientel-PII lesen, ohne Spur — im Gegensatz zu `/system/`, wo jeder Zugriff als `SYSTEM_VIEW` protokolliert wird. [`AdminReadAuditMixin`](../src/core/admin/mixins.py) schreibt für super_admin-Reads jetzt einen `SYSTEM_VIEW`-Eintrag (über den Helper [`audit_admin_view`](../src/core/services/audit/helpers.py), `facility=None`, RLS-GUCs via `_set_session_vars` gesetzt — exakt wie `SystemAuditMixin`). Die Changelist erzeugt genau **einen** Sammel-Audit pro Request (nicht pro Zeile). Ein same-facility-Read durch einen `facility_admin` erzeugt **keinen** Audit — sein Queryset ist bereits facility-gescopt (kein Cross-Facility-Read möglich), sodass `app.is_super_admin` nicht fälschlich in seiner Session gesetzt wird. **Reichweite (Refs #1862):** Der Mixin hängt an sechs ModelAdmins — `Client`, `Case`, `Event`, `EventHistory`, `WorkItem`, `Attachment`. „Jeder `super_admin`-Read im Admin wird protokolliert" ist also **nicht** zutreffend: [`AuditLogAdmin`](../src/core/admin/system.py), `SettingsAdmin`, `StatisticsSnapshotAdmin`, `UserAdmin`, `OrganizationAdmin`, `FacilityAdmin`, `DeletionRequestAdmin` und die Katalog-Admins tragen ihn nicht. Am schwersten wiegt `AuditLogAdmin` mit `search_fields = (…, "detail")`: dort kann ein `super_admin` einrichtungsübergreifend über die Detail-Payloads suchen, ohne dass ein `SYSTEM_VIEW` entsteht. Der Zugang bleibt sudo-gated ([ADR-019](adr/019-custom-admin-site-sudo.md)) und rollen-gegated; die Ausweitung des Mixins ist eine eigene Entscheidung (Wechselwirkung mit der `detail`-Allowlist aus #1944) und hier nur festgehalten, nicht getroffen.

Ein Architektur-Guard ([`TestDomainAdminReadOnlyGuard`](../src/tests/test_architecture_guards_audit.py)) lässt künftige Fach-Model-Admins ohne `ReadOnlyDomainAdminMixin` fehlschlagen; Verhaltensnachweis in [`test_admin_audit.py`](../src/tests/test_admin_audit.py).

**Restrisiko / Trade-off:** Die Read-only-Sperre entfernt den legitimen Notfall-Korrektur-Pfad über das Admin — bewusste Konsequenz der Entscheidung „Sperren statt Audit-Wiring", weil ein Admin-Save die Service-Invarianten nicht sauber replizieren kann. Notfall-Korrekturen laufen über die reguläre App (Service-Schicht, auditiert) bzw. den forensischen psql-Pfad (`anlaufstelle_admin`, BYPASSRLS).

---

## CSP `'unsafe-eval'` auf `/admin-mgmt/` (Issue #695)

**Status:** Design-Entscheidung, Workaround akzeptiert. Trigger-Liste für Re-Evaluation am Ende.

> **Als ADR dokumentiert:** Die Architektur-Entscheidung ist als [ADR-025](adr/025-csp-unsafe-eval-admin.md) festgehalten (inkl. der `text/html`-Verschärfung aus #1084). Dieser Abschnitt bleibt als ausführliche Trade-off- und Threat-Model-Notiz.

### Beobachtung

Die globale CSP setzt `script-src 'self'` (kein `'unsafe-eval'`, kein `'unsafe-inline'`) — alle App-Templates nutzen den `@alpinejs/csp`-Build mit registrierten `Alpine.data()`-Komponenten. Eine Ausnahme bildet die Django-Admin-UI auf `/admin-mgmt/`: das gevendor'te [`django-unfold`-Theme](https://github.com/unfoldadmin/django-unfold) (Version 0.108.0, `requirements.txt`) liefert Templates mit Inline-Function-Calls (`x-data="theme(...)"`, `x-data="searchCommand(...)"`) **und** rund 20 Templates mit Inline-Object-Expressions (`x-data="{rowOpen: false}"` u.ä.). Beide Patterns scheitern unter dem strikten `@alpinejs/csp`-Build, weil Alpine sie nur mit dynamischer Code-Auswertung parsen kann (klassischer Alpine-Build), die unter `script-src 'self'` ohne `'unsafe-eval'` blockiert ist.

[`AdminCSPRelaxMiddleware`](../src/core/middleware/admin_csp_relax.py) hängt `'unsafe-eval'` an `script-src` an und baut `style-src` auf `'unsafe-inline'` ohne Hashes um (Abschnitt [Ausnahme `/admin-mgmt/`](#ausnahme-admin-mgmt) unten). Das geschieht per Response-Header-Rewrite für **jede** HTML-Antwort unter `/admin-mgmt/`, unabhängig vom Login, also auch für die anonym erreichbare Admin-Login-Seite `/admin-mgmt/login/` (Unfolds Skeleton braucht `theme()` auch dort, I3-Revert aus #1376). Die Middleware steht in `MIDDLEWARE` **vor** `CSPMiddleware` und schreibt den Header auf dem Rückweg um. In Produktion laufen die App-Login-Seite, Klient-CRUD, Zeitstrom, Statistik und alle anderen Routen weiter mit `script-src 'self'`. Zwei Deployments erweitern die CSP zusätzlich: Auf der Demo gibt [`DemoUmamiCSPRelaxMiddleware`](../src/core/middleware/demo_umami_csp_relax.py) den Umami-Host in `script-src`/`connect-src` frei (nur `DEMO_MODE`, nach dem Login, HTML), und `settings/devlive.py` erweitert beide Direktiven global, sobald `UMAMI_SCRIPT_SRC` gesetzt ist.

### Trade-off

Die saubere Alternative wäre Option 2 aus dem Issue: 20+ Unfold-Templates in [`src/templates/admin/`](../src/templates/) überschreiben + Shim-JS mit `Alpine.data('searchCommand', ...)`-Re-Implementierungen. Initialaufwand 1–2 Tage, dazu laufender Wartungs-Tax bei jedem `django-unfold`-Update. Im Issue-Body selbst auf v0.11 verschoben.

**Was wir aufgeben:**
- `script-src` auf Admin-Routes ist nicht mehr „strict" — Audit-Reports werden `'unsafe-eval'` flaggen
- Falls je eine XSS-Lücke in einem Admin-Template entsteht, hat ein Angreifer dort grösseren Sprengbereich als auf den restlichen Routes

**Was wir behalten:**
- `script-src` bleibt `'self'` — kein Remote-Script-Loading
- Architektur-Tests für die eigenen Templates und den eigenen Python-Code; die Markup-/CSRF-Guards beziehen zusätzlich die installierten Django-Admin-/Unfold-Pakete ein:
  - [`test_architecture_guards_templates.py`](../src/tests/test_architecture_guards_templates.py): Inline-`<script>`, Inline-Event-Attribute, `javascript:`-URIs, Inline-`x-data`-Objekte, unsichere Alpine-/htmx-Ausdrücke, Inline-Styles.
  - [`test_architecture_guards_safe_markup.py`](../src/tests/test_architecture_guards_safe_markup.py) (#2468): `|safe`, `|safeseq`, `{% autoescape off %}` und `mark_safe()`/`SafeString` nur mit begründetem Allowlist-Eintrag (leere Begründung gibt nichts frei; Kommentarblöcke enden wie in Django am ersten `{% endcomment %}`; nur statisch erkennbare Verwendungen, keine dynamischen `getattr`-Zugriffe). Erlaubt sind die zwei Klartext-Mailvorlagen `registration/invite_email.html` und `registration/password_reset_email.html` (Versand ohne HTML-Teil); im eigenen Python-Code gibt es keine Markup-Ausnahme. Django-Admin-/Unfold-Templates werden vom installierten Paketpfad inventarisiert; 14 konkrete Pfad-/Konstrukt-Ausnahmen begründen 20 Escaping-Treffer, ohne pauschalen Vendor-Ausschluss. `mark_safe` meldet zusätzlich Ruff `S308`.
  - [`test_architecture_guards_csrf_exempt.py`](../src/tests/test_architecture_guards_csrf_exempt.py) (#2468): `csrf_exempt` und `_dont_enforce_csrf_checks` nur mit begründetem Allowlist-Eintrag. Bewusste Ausnahme ist der CSP-Report-Endpunkt [`CSPReportView`](../src/core/views/csp_report.py): Browser-Reports tragen kein Token, der Endpunkt nimmt nur POST an, loggt bereinigt, ist auf `10/m` je IP limitiert und ändert keinen Anwendungszustand.
- Hinter dem Login ist `/admin-mgmt/` nur fuer Rollen `super_admin` und `facility_admin` erreichbar (Custom `AnlaufstelleAdminSite`, `_has_admin_role`, Refs #785). `lead`/`staff`/`assistant` werden geblockt — auch wenn `is_staff=True` gesetzt ist. Sudo-Mode-Pflicht: erster Zugriff redirected zu `/sudo/?next=/admin-mgmt/`. Facility-Scoping in ModelAdmin: `facility_admin` sieht nur Daten der eigenen Facility, `super_admin` sieht alle; RLS gegen Cross-Facility-Leaks. MFA: In Produktion müssen beide Admin-Rollen MFA sofort einrichten (`MFA_ENFORCED_ROLES_IMMEDIATE`, Standard `super_admin,facility_admin`; #788 ist umgesetzt).
- Ratelimit nur am Admin-Login-POST (`5/m` je IP, `10/h` je Benutzername, `AnlaufstelleAdminSite.login`, #2020); die Admin-Change-Views selbst sind nicht limitiert.
- Audit-Abdeckung im Admin, **nicht** auf alle Schreib-Aktionen: Ins HMAC-verkettete AuditLog schreiben `SettingsAdmin` (`SETTINGS_CHANGE`), Benutzer-Änderungen über `UserAdmin` (Signale in `signals/audit.py`, Admin-Aktionen, Passwort-Reset), das Speichern eines Dokumentationstyps (`CONFIG_CREATE`/`CONFIG_UPDATE`) und das Sammel-Aktivieren/-Deaktivieren von Feldvorlagen. Einzel-Anlegen/-Ändern/-Löschen von Feldvorlagen, das Löschen von Dokumentationstypen und alle Schreibvorgänge an Schnellvorlagen, Zeitfiltern, Organisation und Einrichtung hinterlassen nur Djangos veränderbares `admin.LogEntry`. Fachobjekte sind read-only, einrichtungsübergreifende Lesezugriffe schreiben `SYSTEM_VIEW` (Abschnitt [#1341](#fachobjekte-im-admin-read-only--cross-facility-read-audit-issue-1341) oben).

### Threat-Model-Bewertung

Wer kann von der CSP-Lockerung profitieren?

1. **Externer Angreifer ohne Session** — erreicht von `/admin-mgmt/` nur die Login-Seite. Sie läuft bereits mit der gelockerten CSP, rendert aber keine Nutzdaten; dahinter halten Rollen-Gate, Sudo, Login-Ratelimit, Account-Lockout und MFA ([Threat Model TB1](threat-model.md#tb1--browser--caddydjango)). Eine Injektionslücke **auf der Login-Seite** fände die gelockerte CSP vor. Die Seite ist ein Unfold-Template; es gilt Djangos Autoescaping, der Markup-Guard prüft auch die installierten Admin-Pakettemplates mit konkreten, begründeten Ausnahmen. Der Inline-/Alpine-Guard bleibt auf eigene Templates beschränkt.
2. **Externer Angreifer mit valider Admin-Session** — hat ohnehin Vollzugriff; `'unsafe-eval'` ändert sein Schadenspotenzial nicht
3. **XSS in Admin-Template** — die gelockerte CSP bleibt ein Restrisiko. Der Markup-Guard prüft eigene und installierte Django-Admin-/Unfold-Templates auf statisch erkennbares `|safe`, `safeseq` und `autoescape off`; der CSRF-Guard inventarisiert Python-Code derselben Pakete. Konkrete Ausnahmen tragen Einzelbegründungen. Der Inline-/Alpine-Guard bleibt auf eigene Templates beschränkt; dynamische Zugriffe und die semantische Sicherheit jeder zugelassenen Ausgabe sind dadurch nicht bewiesen. Eine beliebige XSS-Lücke ist damit nicht ausgeschlossen.
4. **Malicious Insider mit Admin-Rolle** — braucht keinen CSP-Bypass

### Trigger für Re-Evaluation (dann Option 2 oder Option 4 angehen)

- `django-unfold` liefert upstream einen `@alpinejs/csp`-kompatiblen Build (Wartungs-Tax fällt weg)
- Externer Audit fordert „strict CSP überall" als verbindliches Akzeptanzkriterium
- Admin-UI-Surface wächst signifikant (z.B. geplante Custom-Admin-UI) — dann lohnt sich der Override-Pfad ohnehin
- Architektur-Tests, die die XSS-Vorbedingungen verhindern (`test_architecture_guards_templates.py`, `test_architecture_guards_safe_markup.py`, `test_architecture_guards_csrf_exempt.py`), würden aufgeweicht oder ihre Allowlists um Admin-Ausgaben erweitert
- Den Abbau der Lockerung plant #2104

### Verifikation

- Middleware-Logik: [`src/core/middleware/admin_csp_relax.py`](../src/core/middleware/admin_csp_relax.py) (Rewrite nur für `text/html` unter dem Pfad-Präfix `/admin-mgmt/`, auch anonym)
- Reihenfolge und Policy: `MIDDLEWARE` und `CONTENT_SECURITY_POLICY` in [`settings/base.py`](../src/anlaufstelle/settings/base.py)
- Guards: [`test_architecture_guards_safe_markup.py`](../src/tests/test_architecture_guards_safe_markup.py), [`test_architecture_guards_csrf_exempt.py`](../src/tests/test_architecture_guards_csrf_exempt.py)
- Threat-Model-Eintrag: [`threat-model.md` § TB1, Zeile *S Session-Hijack via XSS*](threat-model.md#tb1--browser--caddydjango)

---

## CSP `style-src` ohne `'unsafe-inline'` (Issue #1660)

**Status:** umgesetzt. `style-src` ist strikt (#1660) — `'unsafe-inline'` ist entfernt, drei Sonderseiten laufen über sha256-Hashes, `/admin-mgmt/` behält eine eng gescopte Ausnahme.

### Ausgangslage

`'unsafe-inline'` in `style-src` war historisch gewachsen (Django-typisch, im internen Code-Audit als L10 „low-risk" eingestuft). Ein realer Style-Injection-Sink existierte nicht — die Erlaubnis schwächte aber die Defense-in-Depth: gelänge irgendwo eine HTML-/Attribut-Injektion, wären Inline-Styles ein zusätzlicher Hebel (UI-Redressing, Exfiltration über CSS-Attribut-Selektoren).

### Umsetzung

| Fundstelle | Strategie |
|---|---|
| `components/_password_input.html` (`style="display:none"` am Auge-durchgestrichen-SVG) | `x-cloak` — `[x-cloak]{display:none!important}` steht in `input.css`, Alpine entfernt das Attribut erst nach dem ersten `x-show`-Durchlauf |
| `core/statistics/partials/dashboard_content.html` (3× `style="height: 300px"` an den Chart-Containern) | Tailwind-Utility `h-[300px]` |
| `core/system/vvt.html` (`<style>` mit Print-Regeln) | ausgelagert nach `src/static/css/input.css` (`@media print`, Karten über `.print-card`) |
| htmx-Lade-Indicator (htmx injiziert `.htmx-indicator`-Regeln selbst als Inline-`<style>`) | `<meta name="htmx-config" content='{"includeIndicatorStyles": false}'>` in `base.html`, Regeln statisch in `input.css` |
| `500.html`, `503.html`, `offline.html` | **bleiben inline**, per `'sha256-…'` in `style-src` gepinnt |
| `core/export/*_pdf.html` | unverändert — serverseitig via WeasyPrint zu PDF gerendert, erreichen nie einen Browser |

**Warum Hash statt Auslagern bei den drei Sonderseiten:** Sie müssen genau dann korrekt aussehen, wenn der Normalpfad kaputt ist — `500.html` bei Serverfehler und ggf. defektem Static-Serving, `503.html` im Wartungsmodus, `offline.html` ohne Netz. Ein `<link rel="stylesheet">` auf `/static/css/styles.css` wäre dort die unzuverlässigste Abhängigkeit.

**Warum Hash statt Nonce:** `offline.html` liegt im Service-Worker-Precache. Der SW liefert gecachtes HTML **samt gecachtem Header** aus — eine frisch generierte Nonce im Header passte nach dem ersten Cache-Treffer nicht mehr zum Markup. Hashes sind über die Cache-Generation stabil.

`'unsafe-hashes'` ist bewusst **nicht** gesetzt: die Hashes gelten dadurch ausschließlich für `<style>`-Elemente, nie für `style=`-Attribute.

Nicht betroffen ist die CSSOM-API: `el.style.setProperty(…)` / `el.style.display = …` unterliegt keiner CSP — genau darüber setzen Alpine (`x-show`/`x-cloak`/`x-transition`) und Chart.js ihre Styles. Blockiert wären nur Markup-`style=`-Attribute (auch `style="--var: x"`) und `el.setAttribute("style", …)`.

### Ausnahme `/admin-mgmt/`

[`AdminCSPRelaxMiddleware`](../src/core/middleware/admin_csp_relax.py) hängt jetzt — analog zu `'unsafe-eval'` in `script-src` (siehe Abschnitt oben / [ADR-025](adr/025-csp-unsafe-eval-admin.md)) — `'unsafe-inline'` in `style-src` an, ausschließlich für HTML-Antworten unter `/admin-mgmt/`. Grund: `unfold/layouts/skeleton.html` rendert einen `<style id="unfold-theme-colors">`-Block, dessen Inhalt aus `UNFOLD["COLORS"]` **dynamisch** zusammengesetzt wird — ein statischer Hash ist prinzipiell unmöglich. Dazu kommen rund ein Dutzend `style=`-Attribute in Unfold-Templates (Progress-Bars, Avatare, Sidebar, Change-List). Ohne die Lockerung verlöre die Admin-UI ihr komplettes Farbschema. Die Trade-off-Bewertung ist dieselbe wie bei `'unsafe-eval'`, mit denselben Grenzen (Abschnitt oben): Die Lockerung gilt auch für die anonyme Admin-Login-Seite; hinter dem Login schützen Rollen-Beschränkung, Sudo-Mode, MFA-Pflicht und RLS; das Ratelimit sitzt nur am Login-POST; das AuditLog deckt nur einen Teil der Admin-Schreibvorgänge ab. Der Trigger für Re-Evaluation ist ebenfalls derselbe — ein CSP-taugliches Upstream-Unfold.

### Verifikation

- Guards (Quelltext + Settings): [`test_architecture_guards_templates.py::TestNoInlineStylesGuard`](../src/tests/test_architecture_guards_templates.py) — verbietet neue `style=`-Attribute und neue `<style>`-Blöcke in Browser-Templates (PDF-Templates ausgenommen, drei Sonderseiten allowlistet), rechnet die gepinnten Hashes aus dem Template neu nach und verbietet `'unsafe-inline'`/`'unsafe-hashes'` in `style-src`.
- Ende-zu-Ende (ausgelieferter Header vs. gerendertes Markup): [`test_csp_style_src.py`](../src/tests/test_csp_style_src.py).
- Admin-Ausnahme: [`test_admin_csp_relax.py::TestAdminCSPRelaxStyleSrc`](../src/tests/test_admin_csp_relax.py).

---

## Klartext-Freitexte ausserhalb des Sensitivity-Modells (Issue #716)

**Status:** Inventarisierung + UI-Warnung umgesetzt; feldweise Encryption deferred bis post-v1.0 (Audit-Item 17).

### Inventar

Diese Freitext-Felder folgen **nicht** dem Sensitivity/Encryption/Retention-Modell der Event-`data_json`-Felder. Klartext liegt facility-gescoped in der DB und im Backup (Backup ist AES-256-CBC verschlüsselt; DB selbst nicht).

| Feld | Modell | Typische Inhalte | Risiko |
|---|---|---|---|
| [`Client.notes`](../src/core/models/client.py) | `Client` | Freie Notizen über die Person | hoch (Klarname-Risiko) |
| [`Case.description`](../src/core/models/case.py) | `Case` | Fall-Beschreibung | mittel |
| [`Episode.description`](../src/core/models/episode.py) | `Episode` | Episoden-Verlauf | mittel |
| [`WorkItem.description`](../src/core/models/workitem.py) | `WorkItem` | Aufgaben-Beschreibung | mittel |
| [`AuditLog.detail`](../src/core/models/audit.py) | `AuditLog` | JSON mit `changed_fields` (nur Feldnamen), technischen Metadaten und den in [`AUDIT_DETAIL_KEYS`](../src/core/services/audit/detail_policy.py) zugelassenen Freitext-Schlüsseln `name`, `username`, `actor_username`, `note`, `recipient`, `purpose` (`message` ist eine feste Codekonstante) | mittel: Dateiname, Meilenstein-Titel und Pseudonym schreibt die Anwendung nicht (#1751); nur `recipient`/`purpose` unterliegen einer zusätzlichen Werteheuristik; die übrigen Freitextwerte prüft sie nicht — ein Klientenname etwa im `purpose` einer Berichts-Freigabe bleibt im Klartext stehen, die Minimierung dort ist Betreiber-Obliegenheit |

### Was bereits umgesetzt ist (Audit-Item 16)

Der `help_text` der vier Freitextfelder (`Client.notes`, `Case.description`, `Episode.description`, `WorkItem.description`) warnt, dass das Feld nicht verschlüsselt gespeichert wird, dass dort keine Klarnamen und keine Angaben nach Art. 9 DSGVO stehen sollen und dass solche Angaben in ein verschlüsseltes Zusatzfeld gehören (Wortlaut in den Modellen). `AuditLog.detail` hat keinen `help_text`; es ist kein Formularfeld. Django rendert `help_text` automatisch unter den Form-Widgets.

### Was bewusst nicht jetzt umgesetzt wird (Audit-Item 17)

Feldweise Encryption (`Client.notes`, `Case.description` als verschlüsselte Felder) ist im internen Code-Audit (dev-only) als Maßnahme mit mittlerem Aufwand nach v1.0 eingestuft. Voll-Implementation würde:
- Schema-Migration (TextField → JSONField mit `{"__encrypted__": True, "value": ...}`)
- Existing-Data-Migration (Bestand verschlüsseln)
- Search/Sort/Export anpassen — `Client.notes` wird in Detail-Views, Exports und ggf. Volltext-Suche gerendert
- Decrypt-Pfad in allen Views, die das Feld lesen

### Trade-off

**Aktueller Schutz:**
- Backup-Encryption AES-256-CBC + PBKDF2 mit `BACKUP_ENCRYPTION_KEY`
- RLS + Role-Mixin verhindern Cross-Facility-Reads
- DSGVO-Anonymize-Cascade (#715) säubert die **vier Fach-Freitexte** (`Client.notes`, `Case.description`, `Episode.description`, `WorkItem.description`) — **nicht** `AuditLog.detail`: das Audit-Log ist append-only (DB-Trigger aus Migration `0024` + HMAC-Kette), eine nachträgliche Redaktion ist dort strukturell unmöglich. Der einzige Hebel ist Minimierung **beim Schreiben**: Die Schreibstellen für Upload-Dateinamen (HMAC + Endung), Meilensteine (kein Titel) und den Art.-15-/20-Export (kein Pseudonym) schreiben keine Klienten-PII (#1751); die zugelassenen Freitext-Schlüssel (`name`, `username`, `actor_username`, `note`, `recipient`, `purpose`) übernehmen dagegen, was eingegeben wird — siehe [§ Technischer Guard statt Call-Site-Konvention](#technischer-guard-statt-call-site-konvention-1944). Bereits geschriebene Zeilen bleiben unverändert stehen — siehe [§ Audit-Log-Restdaten überleben die Art.-17-Löschung](#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753)

**Wenn jetzt durchgezogen:** ~1–2 Tage Arbeit + Risiko in Search/Export, ohne Live-Feedback aus Pilot zu haben.

### Trigger für Re-Evaluation (Audit-Item 17 angehen)

- Pilot-Einrichtung beginnt v1.0-Roll-Out — dann wird das Risiko real
- Externer Audit fordert Encryption-at-Rest auch für freie Notizen
- DPA mit Pilot-Träger fordert Field-Level-Encryption explizit
- Backup-Storage wechselt zu System ohne Object-Lock/Encryption-at-Rest

### Verifikation

- Die `help_text`-Werte stehen an den vier Modellfeldern (`client.py`, `case.py`, `episode.py`, `workitem.py`) und erscheinen in den zugehörigen ModelForms.
- Threat-Model-Eintrag: [`threat-model.md` § TB2/TB3, Zeile *I Sensitive Felder ohne Encryption*](threat-model.md#tb2tb3--django--postgresql)

---

## Audit-Log-Restdaten überleben die Art.-17-Löschung (Issue #1753)

**Status:** Akzeptiertes Restrisiko, durch Minimierung beim Schreiben abgemildert (#1751). Betriebliche Gegenmaßnahme: Aufbewahrungsfrist nicht abschalten.

### Beobachtung

Die Anonymisierung einer Person (Art. 17) räumt die Fach-Tabellen ab, **nicht** das Audit-Log: `AuditLog` ist append-only (DB-Trigger `auditlog_immutable` aus Migration `0024`, `ENABLE ALWAYS` seit `0107`, plus HMAC-Verkettung). `anonymize_client()` ([`src/core/services/client/main.py`](../src/core/services/client/main.py)) fasst es nicht an — und **kann** es nicht, ohne die Kette zu brechen. Was in einer Audit-Zeile steht, bleibt dort bis zum Pruning stehen.

Daraus folgen drei Fälle, in denen personenbeziehbare Restdaten die Löschung überdauern:

1. **Bis zum Pruning** — Regelfall: bis zu `Settings.auditlog_retention_months` (Default 24 Monate) nach dem Eintrag.
2. **Dauerhaft bei `auditlog_retention_months = 0`** — der Wert schaltet das Pruning ab ([`audit_pruning.py`](../src/core/retention/audit_pruning.py): `months <= 0` → No-op). Dann überlebt **jede** Audit-Zeile jede Löschung, unbegrenzt.
3. **Dauerhaft bei `SECURITY_VIOLATION`** — die Aktion steht in `PRUNE_EXEMPT_ACTIONS` und wird nie gepruned, unabhängig von der Frist. Dasselbe gilt für `RETENTION_RUN_COMPLETED`, `AUDIT_PRUNE_CHECKPOINT` und `EVENT_HISTORY_REDACTED` (alle drei in `PRUNE_EXEMPT_ACTIONS`, sie tragen nur System-Metadaten) sowie (#1770) für `EXPORT`-Einträge, die eine **Berichts-Freigabe** sind (`RELEASE_EXPORT_Q`: Berichtszeitraum **und** herausgegebenes Artefakt; Datenbasis der Freigabe-Übersicht, siehe § Disclosure-Analyse K4a). Die Grenze hängt bewusst **nicht** an der Aktion: `EXPORT` ist eine Sammelklasse, unter der auch die Art.-15-Datenauskunft einer Person, die DSGVO-Dokument-Downloads (beide ohne Zeitraum) und jeder **Bildschirm-Aufruf** des externen Berichts (Zeitraum, aber kein Artefakt) laufen — die alle bleiben der Frist unterworfen. Was in der Berichts-Teilmenge dauerhaft stehen bleibt, sind Zeitraum, Format, die Empfänger-/Zweck-**Freitexte** und die handelnde Person; Klientenbezug ist dort nicht vorgesehen, aber die Freitextfelder sind Freitext — ihre Datenminimierung ist eine Betreiber-Obliegenheit (Empfehlung im Admin-Handbuch), keine technische Zusicherung.

### Was bereits umgesetzt ist (#1751, Punkt 5)

Weil nachträgliche Redaktion strukturell ausgeschlossen ist, ist **Minimierung beim Schreiben** der einzige Hebel. An drei `detail`-Schreibstellen läge Klienten-PII im Klartext nahe; sie schreiben stattdessen nur Hash bzw. Zuordnungs-IDs:

| Schreibstelle | Naheliegender Klartext | Geschrieben wird |
|---|---|---|
| File-Vault-Reject (`SECURITY_VIOLATION`) | Original-Dateiname im Klartext (Uploads heißen im Feld regelmäßig nach der betroffenen Person) | `filename_hash` (stabiler HMAC, Wiederholungen bleiben korrelierbar) + `filename_ext` als Typmarker — zentral in [`log_attachment_violation`](../src/core/services/file_vault/audit.py); die Ablehnungspfade in [`policy.py`](../src/core/services/file_vault/policy.py) rufen sie über `_reject` auf; sie nimmt den Dateinamen als Parameter `filename` entgegen und schreibt davon nur Hash und Endung |
| `MILESTONE_DELETE` | Meilenstein-`title` (derselbe Freitext, den die Anonymisierung tilgt) | nur `case_id`; Zuordnung über `target_id` |
| Art.-15-/20-Export | `pseudonym` | nur `format`; Zuordnung über `target_id` |

Der File-Vault-Fall ist der schärfste: `SECURITY_VIOLATION` ist prune-exempt, ein Klartextname würde also Frist **und** Anonymisierung überleben.

Bewusst **im Klartext** geschrieben wird der `username` bei fehlgeschlagenem Login (einziger Nachweis, *was* versucht wurde — bei Angriffen existiert der User nicht, `AuditLog.user` ist `NULL`) und bei Rollenwechsel/Deaktivierung/Löschbestätigung (wegen `on_delete=SET_NULL` die einzige dauerhafte Zuschreibung einer Rechte-Eskalation). Beides sind **Beschäftigten**-, keine Klientendaten.

### Technischer Guard statt Call-Site-Konvention (#1944)

Die Minimierung an den drei Stellen ist für sich kein Gate: der Architekturtest `TestAuditLogCreationAllowlist` prüft nur, **wo** Audit-Zeilen entstehen dürfen, nie **was** in ihnen steht. Einen neuen Service-Aufruf mit `detail={"pseudonym": …}` — oder mit einem Freitextwert unter einem unverdächtigen Schlüssel — erkennt er nicht.

Deshalb gilt eine **Schlüssel-Allowlist**: [`src/core/services/audit/detail_policy.py`](../src/core/services/audit/detail_policy.py) führt jeden zulässigen `detail`-Schlüssel mit Begründung, warum sein Wert keine Klienten-PII tragen soll bzw. warum er als Freitext bleiben muss. Geprüft werden nur die Schlüssel, nicht die Werte. Bewusst Schlüssel statt Werte-Blacklist — eine Liste verdächtiger Namen (`pseudonym`, `notes`, `email`) fängt genau den Fall nicht, der wehtut: Freitext unter unauffälligem Schlüssel.

- **Laufzeit-Prüfung** in `audit_event` / `log_audit_event` (und damit in allen typed Helpern, die darauf aufsetzen) sowie an den beiden allowlisteten Direktschreibern in [`signals/audit.py`](../src/core/signals/audit.py) und [`services/system/settings.py`](../src/core/services/system/settings.py). Sie greift auch dort, wo das `detail` dynamisch entsteht (`**kwargs`, Zähler-Dicts) — rund ein Dutzend Stellen, an denen ein rein statischer Guard blind wäre.
- **Statisches Gegenstück** `TestAuditDetailKeyInventory` in [`test_architecture_guards_audit.py`](../src/tests/test_architecture_guards_audit.py): liest die literalen `detail={…}`-Dicts unter `src/core` und meldet einen undeklarierten Schlüssel schon ohne Testausführung. Die dynamischen Schreibpunkte sind dort namentlich mit Anzahl geführt.
- **Fail-Verhalten:** `AUDIT_DETAIL_STRICT` weist unbekannte Schlüssel zurück — Default `True` in dev/test und Produktion (einschließlich `docker-compose.prod.yml`). Ein explizites `DJANGO_AUDIT_DETAIL_STRICT=false` schreibt unbekannte Schlüssel mit ERROR-Meldung; die Meldung nennt nur Schlüssel, nie Werte. Die Wertprüfung für Empfänger/Zweck bleibt unabhängig davon aktiv und weist Verstöße zurück.
- **Bewusst zugelassener Freitext** bleibt deklariert: `name`, `username`/`actor_username`, `note`, `recipient`/`purpose`; `message` ist eine feste Codekonstante. Für `recipient`/`purpose` weist [`validate_release_metadata`](../src/core/services/audit/detail_policy.py) nicht-textuelle oder über 200 Zeichen lange Werte, erkannte E-Mail-/Telefonmuster und typische Namenspaare zurück. Die Export-Views antworten vor der Herausgabe mit HTTP 400 und spiegeln abgewiesene Werte nicht zurück. Institutionelle Präfixe wie „Stadt Musterstadt“ bleiben erlaubt. Die Heuristik garantiert keine PII-Freiheit; akzeptierte Freitexte bleiben im append-only Log und erfordern weiterhin Datenminimierung durch die Einrichtung.

### Restfläche (akzeptiert)

- **Geschriebene Zeilen bleiben unverändert.** Was einmal in einem `detail` steht, bleibt dort; es zu redigieren hieße, die HMAC-Kette zu brechen (und damit genau die Integritätszusicherung aufzugeben, wegen der das Log existiert). Sie verschwinden mit dem regulären Pruning.
- **`changed_fields`, `target_id` und Zeitstempel bleiben personenbeziehbar** — solange die Ziel-Zeile noch existiert, ist die Zuordnung auflösbar. Das ist gewollt: ohne sie wäre das Log als Rechenschaftsnachweis (Art. 5 Abs. 2) wertlos.
- **Abwägung:** Art. 17 Abs. 3 lit. b/e deckt die Aufbewahrung zur Erfüllung rechtlicher Nachweispflichten und zur Rechtsverteidigung; der Nachweis *dass* verarbeitet wurde ist der Zweck des Logs. Die Grenze ist die Verhältnismäßigkeit der Frist — deshalb ist sie konfigurierbar und im Regelfall endlich.

### Betriebliche Empfehlung

- `auditlog_retention_months = 0` **nicht** setzen. Wer unbegrenzte Nachweisführung braucht, exportiert das Log turnusmäßig nach außen, statt das Pruning abzuschalten — sonst gibt es innerhalb der Anwendung keinen Löschhorizont mehr.
- Die Frist gehört in Verarbeitungsverzeichnis und Löschkonzept (die DSFA-Vorlage [`dsfa.md` § 2.3](../src/core/dsgvo_templates/dsfa.md) nennt sie).
- Bei einem Auskunfts-/Löschersuchen: der Art.-15-Export enthält bereits einen Audit-Auszug zur Person (ohne Beschäftigten-Identität, [`docs/admin-guide.md` § 7.6](admin-guide.md#umfang-der-auskunft-art-15)); die vollständigen Rohzeilen stehen der Anwendungsbetreuung unter `/audit/` zur Verfügung.

### Verifikation

- [`test_audit_detail_pii_minimization.py`](../src/tests/test_audit_detail_pii_minimization.py) — pinnt für alle drei Schreibstellen, dass der Klartext **nicht** im `detail` landet.
- [`test_audit_trigger.py`](../src/tests/test_audit_trigger.py) — Append-only-Trigger (Grund, warum nachträgliche Redaktion ausscheidet).
- [`test_pii_residue_sweep.py`](../src/tests/test_pii_residue_sweep.py) — Residuen-Sweep über die Redaktionspfade.
- Threat-Model-Eintrag: [`threat-model.md` § 6 Bekannte offene Lücken](threat-model.md#6-bekannte-offene-lücken)

---

## Medien-Backups überleben das ersatzlose Löschen einer Datei (Issue #1874)

**Status:** Akzeptiertes Restrisiko. Die Anwendung kann es technisch nicht schließen; die Gegenmaßnahme ist betrieblich und die Zusage an die betroffene Person ist entsprechend eingeschränkt formuliert.

### Beobachtung

Das ersatzlose Löschen eines Dateianhangs (`purge_attachment_chain`, Route `core:attachment_delete`) entfernt die Zeilen, die `__files__`-Marker **und** die verschlüsselten `.enc`-Dateien der ganzen Versionskette vom Datenträger. Innerhalb der laufenden Instanz ist der Vorgang damit vollständig und unwiderruflich — es gibt keinen Papierkorb und keinen Wiederherstellungspfad.

**Nicht erfasst sind die Sicherungskopien daneben.** Der Prod-Backup-Pfad ([`scripts/ops/backup.sh`](../scripts/ops/backup.sh)) schreibt neben dem DB-Dump einen Medien-Tarball `*_media.tar.gz.enc` (+ `.hmac`), der über [`scripts/ops/restore.sh`](../scripts/ops/restore.sh) zurückspielbar ist (siehe [`disaster-recovery.md` §2/§3](disaster-recovery.md#2-backup-format)). Eine Datei, die heute gelöscht wird, liegt in jedem Backup weiter, das vor der Löschung entstanden ist. Lokal entfernt `backup.sh` die Stände nach 7 (täglich), 28 (wöchentlich) und 90 Tagen (monatlich) und setzt diese Rotation aus, solange der Off-Site-Abgleich fehlschlägt. Ist das optionale Off-Site-Ziel (`BACKUP_OFFSITE_TARGET`) gesetzt, überträgt das Skript per `rclone copy`, `aws s3 sync` ohne `--delete` bzw. `scp` und löscht dort nichts: Die Kopien liegen am Ziel, bis eine Aufbewahrungsregel des Betreibers sie entfernt. Ein Restore holt sie zurück; die DB-Zeile käme dabei mit, weil der DB-Dump aus demselben Zeitpunkt stammt.

Für ein Feature, das ausdrücklich als **Datenminimierung** angeboten wird (Anlassfall: falsche Datei, falsche Person, Klarname im Dokument), ist eine unbedingte Zusage „keine Wiederherstellung — auch nicht durch die Administration" gegenüber der betroffenen Person deshalb falsch.

### Warum die Anwendung das nicht schließen kann

Ein Backup ist per Konstruktion ein Stand **von vorher**. Nachträglich einzelne Objekte aus einem verschlüsselten, HMAC-signierten Tarball zu entfernen hieße, das Artefakt neu zu schreiben und seine Signatur neu zu setzen — damit wäre die Integritätszusicherung des Backups aufgegeben, also genau die Eigenschaft, wegen der es existiert. Dieselbe Abwägung wie beim append-only Audit-Log (§ Audit-Log-Restdaten): Integrität der Sicherung schlägt selektive Nachredaktion.

### Was umgesetzt ist

- **Die Bestätigungsseite sagt es.** Der Warnkasten spricht die Unwiderruflichkeit **„in der Anwendung"** aus; ein zweiter, eigener Kasten (`data-testid="attachment-purge-backup-hint"`) nennt das Medien-Backup und verweist auf die Administration. Beides ist mit Test gepinnt.
- **Beide Handbücher** (`user-guide.md` / `en/user-guide.md`, Abschnitt 3a „Endgültig löschen") tragen dieselbe Einschränkung, statt der vorherigen unbedingten Zusage.

### Betriebliche Empfehlung

- Die Aufbewahrungsdauer der Backups gehört in Verarbeitungsverzeichnis und Löschkonzept — sie ist die tatsächliche Obergrenze für die Beseitigung einer Datei.
- Bei einem Löschersuchen nach Art. 17: die Anwendungslöschung sofort ausführen und den Backup-Rest über die reguläre Rotation auslaufen lassen; Art. 17 Abs. 1 verlangt Löschung „unverzüglich", nicht die Zerstörung integritätsgesicherter Sicherungsstände.
- Wer eine kürzere Frist zusichern will, verkürzt die Backup-Aufbewahrung — nicht die Löschroutine.

### Verifikation

- [`test_attachment_purge.py`](../src/tests/test_attachment_purge.py) — `TestConfirmationNamesTheRecord::test_warning_qualifies_the_promise_against_media_backups` pinnt den Hinweiskasten.
- [`disaster-recovery.md` §2/§3](disaster-recovery.md#2-backup-format) — Nachweis, dass der Medien-Tarball existiert und zurückspielbar ist.

---

## `Event.idempotency_payload_hash` überlebt die Klienten-Anonymisierung (Issue #1935)

**Status:** Akzeptiertes Restrisiko — bewusste Beibehaltung der Spalte nach Bewertung, keine Löschung. Einen Enumerations-/Verschlüsselungs-Bypass bei `DocumentType`s ohne Freitextfeld schließt #1942: der Fingerprint ist ein HMAC-SHA-256 mit serverseitigem Pepper, kein nackter Digest des Klartext-Payloads (siehe „Härtung per HMAC-Pepper" weiter unten). Die Aufbewahrungs- und Export-Bewertung dieses Abschnitts gilt davon unabhängig.

### Beobachtung

`Event.idempotency_payload_hash` (`CharField(max_length=64)`; ein gepfeffertes HMAC-SHA-256 im Format `h1:<pepper-id>:<base64url>`) ist der persistente DB-Backstop (#1909) des Offline-Replay-Idempotenz-Schutzes (Review R5/R6, #1424): [`payload_fingerprint()`](../src/core/services/events/idempotency.py) hasht die kanonische Form des Request-Payloads (alle `POST`-Feldpaare außer CSRF-Token und Idempotenz-Schlüsselfeld, sortiert, plus Datei-Tripel), damit ein Replay mit demselben `X-Idempotency-Key`, aber abweichendem Body als Kollision erkannt wird (`has_idempotency_payload_mismatch()`) statt still den falschen gecachten Erfolg zurückzugeben.

Bei der Klienten-Anonymisierung (`anonymize_client()`, [`client/main.py`](../src/core/services/client/main.py)) leert `_redact_live_events()` `Event.data_json`/`search_text` — `idempotency_key`/`idempotency_payload_hash` stehen bewusst **nicht** in deren `update_fields` und bleiben unverändert. Derselbe Befund gilt für den K-Anon-Bridge-Pfad ([`retention/anonymization.py`](../src/core/retention/anonymization.py)): der ruft `_redact_live_events` gar nicht auf (Eintrittsbedingung `active_events == 0`), und der vorgelagerte Soft-Delete (`soft_delete_event`) fasst die Spalte ebenfalls nicht an.

### Zweck (warum der Hash überhaupt existiert)

Der Cache-Eintrag (72 h TTL, `IDEMPOTENCY_TTL_SECONDS`; in Produktion Redis oder, ohne `REDIS_URL`, `DatabaseCache` laut `CACHES` in `prod.py`) ist der Fast-Path; die Spalte ist die Ausfallsicherung dafür — sie greift genau dann, wenn der Cache nicht mehr da ist (Worker-Neustart, Eviction, R5) oder zwei parallele Replays das Get-then-Set-Fenster gleichzeitig passieren (R6). Ohne den persistierten Hash fiele ein Cache-Verlust in genau die Lücke zurück, die #1424 schließt: ein Replay mit demselben (client-frei wählbaren) Schlüssel, aber anderem Payload, würde still den falschen gecachten Treffer zurückliefern, statt als Kollision erkannt zu werden.

### Unlinkability-Bewertung

> **Stand dieser Bewertung: der ungesalzene Digest vor #1942.** Sie bleibt als Herleitung stehen, weil sie begründet, *warum* der Pepper nötig war. Die beiden mit „real, nicht nur theoretisch" bzw. „Unterläuft die At-Rest-Verschlüsselung" bezeichneten Befunde treffen auf den heutigen Code **nicht mehr zu**: beide setzen voraus, dass sich der Digest ohne serverseitiges Geheimnis nachrechnen lässt (§ Härtung per HMAC-Pepper). Für Fingerprints, die noch aus der Zeit davor in der Datenbank liegen, gilt sie unverändert weiter, bis ihre `Event`-Zeile ausläuft.

- **Einweg-Digest, kein Klartext.** Der Hash ist SHA-256 über den vollständigen Formular-Payload — er trägt denselben Informationsgehalt wie `data_json` zum Anlagezeitpunkt, aber nur als Digest. Ohne Kenntnis des exakten Original-Payloads ist er wegen Preimage-Resistenz nicht umkehrbar.
- **Rekonstruktions-Orakel bei kleinem Wertebereich — real, nicht nur theoretisch.** Die Zeile behält nach der Anonymisierung `client_id`, `case_id`, `episode_id`, `occurred_at` und `document_type` im Klartext (siehe nächster Punkt) — einem Angreifer mit DB-Zugriff fehlen nach der Redaktion also ausschließlich die **Feldwerte** aus dem geleerten `data_json`. Trägt der `DocumentType` des Ereignisses ausschließlich strukturierte, nicht-freitextliche Felder (mit den heutigen `FieldTemplate.FieldType`-Optionen zulässig; der Tally-Zählpfad ist bereits durchgehend so aufgebaut), ist der Payload-Rateraum klein genug, um ihn vollständig gegen den gespeicherten Hash zu enumerieren. Ein Treffer **bestätigt dann nicht bloß eine Vermutung, sondern rekonstruiert exakt die redigierten Werte** — der Hash fungiert in diesem Fall als Rekonstruktions-Orakel für Inhalte, die `_redact_live_events()`/die K-Anon-Bridge eigentlich löschen sollten. Für Freitext-Felder (hohe Entropie) bleibt ein Wörterbuch-/Brute-Force-Angriff dagegen praktisch aussichtslos — das Risiko ist an die **Feld-Konfiguration** des jeweiligen `DocumentType` gekoppelt, nicht an eine hypothetische künftige Änderung von `payload_fingerprint()`.
- **Unterläuft die At-Rest-Verschlüsselung — unabhängig von der Anonymisierung.** `payload_fingerprint()` hasht `request.POST` **vor** jeder Verschlüsselung, auch für Felder mit `FieldTemplate.is_encrypted=True`. Der Hash liegt danach unverschlüsselt in derselben Tabelle wie die Fernet-verschlüsselte `data_json`-Spalte. Bei kleinem Wertebereich (siehe oben) kann ein Angreifer mit reinem DB-Lesezugriff (Dump, Backup, kompromittierte Read-Replica) den Klartext eines verschlüsselten Feldes **ohne den Fernet-Schlüssel** per Wörterbuchangriff auf den Hash ermitteln — die Verschlüsselung schützt in diesem Grenzfall nicht mehr, weil der Hash denselben Klartext ungesalzen daneben trägt. Dieser Befund ist **unabhängig von der Anonymisierung**: er gilt bereits für aktive, nicht-anonymisierte Klient:innen, sobald ein `DocumentType` verschlüsselte Felder mit kleinem Wertebereich hat.
- **Kein zusätzlicher Gewinn für die reine Existenz-Frage.** Dass am `occurred_at` ein Ereignis dieses `document_type` für `client_id`/`case_id`/`episode_id` existierte, steht ohnehin unredigiert in derselben Zeile — dafür liefert der Hash keinen zusätzlichen Erkenntnisgewinn. Für den **Inhalt** gilt das nur, solange der oben beschriebene kleine Wertebereich nicht vorliegt.
- **Kein Export-Pfad.** Siehe § Exportausschluss unten — der Hash verlässt die Datenbank nie.

### Kollisionsbewertung

SHA-256s Kollisionswahrscheinlichkeit wird erst im Geburtstagsregime relevant: selbst bei einer für dieses System utopischen Größenordnung von ~2⁶⁴ gehashten Payloads läge die Wahrscheinlichkeit **irgendeiner** Kollision unter allen davon bei nur ~2⁻¹²⁸ (Geburtstagsschranke, ≈ n²/2^(k+1) für n Payloads und einen k-Bit-Hash). Der hier tatsächlich relevante Fall ist aber der **paarweise** Vergleich — `has_idempotency_payload_mismatch()` vergleicht bei jedem Replay genau **einen** neu berechneten Hash gegen **einen** gespeicherten —, dessen Kollisionswahrscheinlichkeit bei ~2⁻²⁵⁶ liegt. Beides ist bei den hier realistischen Ereigniszahlen (soziale Einrichtung, nicht Internet-Skala) vernachlässigbar. Selbst eine (praktisch nie eintretende) Kollision hätte ausschließlich eine funktionale Konsequenz innerhalb desselben `(scope, user, idempotency_key)`-Tripels: `has_idempotency_payload_mismatch()` würde einen abweichenden Payload fälschlich als Treffer werten (False Negative der Kollisions-Erkennung selbst) — kein Datenschutz-Vorfall, weil der Vergleich nie gegen fremde, beliebige Angreifer-Payloads läuft, sondern nur innerhalb der eigenen Replay-Kette desselben Nutzers.

### Retention-Begründung

Die Spalte hat **keine eigene Ablauffrist** — ihre Aufbewahrung ist 1:1 an die Lebensdauer der `Event`-Zeile selbst gebunden, genau wie die übrigen dort bewusst erhaltenen technischen Metadaten (`client_id`, `occurred_at`, `document_type`, `created_via`). `Event`-Zeilen werden von der aktuellen Retention-Pipeline nur soft-gelöscht (`is_deleted=True`), nie hart gelöscht — es gibt aktuell keinen Pfad, der die Zeile physisch entfernt.

Ein zeitbasiertes Leeren (z. B. nach der 72-h-Cache-TTL oder einem sonst gewählten Fenster) würde den Zweck der Spalte gerade zunichtemachen: Sie existiert *ausdrücklich*, weil der Cache-Fast-Path zeitlich begrenzt und nicht garantiert überlebensfähig ist (Neustart/Eviction), während der partielle DB-Unique-Constraint `event_idem_key_per_user_uniq` selbst **keine** Zeitgrenze kennt — ein doppelt verwendeter Schlüssel mit abweichendem Payload muss auch nach Monaten noch als Kollision erkennbar sein. Ein TTL auf der Spalte würde also exakt die Lücke wieder öffnen, die #1909/N14 geschlossen hat, ohne einen Datenschutz-Gewinn zu erzielen (siehe Unlinkability-Bewertung).

### Härtung per HMAC-Pepper (umgesetzt, #1942)

Die zu #1935 nur vorgemerkte dritte Option ist umgesetzt: [`payload_fingerprint()`](../src/core/services/events/idempotency.py) bildet über die kanonische Payload-Form seither ein **HMAC-SHA-256 mit serverseitigem Pepper** statt eines nackten Digests. Der Idempotenz-Zweck bleibt vollständig erhalten — der Vergleich in `has_idempotency_payload_mismatch()` läuft ohnehin nur innerhalb derselben `(scope, user, idempotency_key)`-Kette, nie gegen fremde Payloads —, aber die Enumerierbarkeit gegen einen reinen DB-Dump entfällt: ohne Kenntnis des Peppers ist ein Offline-Wörterbuchangriff auf den Hash nicht mehr möglich, auch nicht bei kleinem Feld-Wertebereich. Damit ist auch der Verschlüsselungs-Bypass geschlossen — der Hash trägt den Klartext eines `is_encrypted`-Feldes nicht mehr ungesalzen neben der Fernet-Spalte.

Die getroffenen Entscheidungen:

- **Pepper-Quelle: abgeleitet aus `ENCRYPTION_KEY`/`ENCRYPTION_KEYS`, kein neues Pflicht-ENV.** `sha256("anlaufstelle:idempotency-fingerprint:v1:" ‖ erster Schlüssel)` — dieselbe Schlüsselquelle wie `MultiFernet`, per Domain-Separation von jedem anderen Gebrauch getrennt, und aus dem Pepper ist der Fernet-Schlüssel nicht rückrechenbar. Ein eigenes Pflicht-Setting wäre für Selbsthoster ein zusätzlicher Weg, eine Installation fail-closed lahmzulegen, ohne Sicherheitsgewinn: wer den Pepper aus der Umgebung liest, liest dort auch `ENCRYPTION_KEY`. Fehlt der Schlüssel, bricht die Ableitung ab, statt auf einen ungepfefferten Digest zurückzufallen — ein solcher Rückfall wäre genau die Lücke, die dieses Ticket schließt (und eine Installation ohne `ENCRYPTION_KEY` kann ohnehin kein verschlüsseltes Feld schreiben).
- **Format `h1:<pepper-id>:<base64url>`.** Das Schema-Präfix ist das einzige Unterscheidungsmerkmal gegenüber einem Legacy-Wert (beide wären sonst 64 Zeichen). Die Pepper-Kennung (8 Hex-Zeichen aus `sha256(pepper)`, kein Schlüsselmaterial) macht eine Schlüssel-Rotation als solche erkennbar. `base64url` ohne Padding hält die vollen 256 Bit in `CharField(max_length=64)` (55 statt 76 Zeichen in Hex) — die Spalte bleibt unverändert, es braucht keine Migration.
- **Schlüsselrotation erhält den Kollisionsschutz vollständig.** `has_idempotency_payload_mismatch()` bekommt den **Request**, nicht einen vorberechneten Hash, und rechnet einen gespeicherten Fingerprint unter genau der Generation nach, unter der er entstand — dafür durchsucht sie alle in `ENCRYPTION_KEYS` konfigurierten Schlüssel (`_get_keys()`, dieselbe Quelle wie `MultiFernet`). Während einer Rotation stehen Alt- und Neuschlüssel dort ohnehin nebeneinander; der N14-Schutz bleibt über die gesamte Rotation hinweg wirksam. Neue Fingerprints entstehen dabei immer unter dem **ersten** Schlüssel.
- **Zwei Fälle ohne Vergleichsgrundlage — graceful, kein 422.** Ein Legacy-Wert (nackter SHA-256 ohne Präfix) und ein Wert, dessen Schlüssel **nicht mehr** in `ENCRYPTION_KEYS` steht, sind keine Kollisions-*Beweise*, sondern fehlende Vergleichsgrundlage. Sie gelten deshalb wie ein fehlender Fingerprint (`NULL`) als **Treffer ohne Payload-Prüfung** — andernfalls liefe jeder solche Replay in einen falschen Kollisions-422 und landete als Dead Letter in der Offline-UI. Eine nachträgliche Datenmigration ist in beiden Fällen unmöglich: der Fingerprint deckt den ursprünglichen **Request-Payload** ab (inklusive Datei-Tripel), und der existiert nach der Anlage nirgends mehr — anders als bei `reencrypt_fields`, das gespeicherte Chiffrate umschlüsselt, gibt es hier kein Original zum Neuberechnen.
- **Restrisiko — ehrlich: dauerhaft, nicht zeitlich begrenzt.** Für Zeilen einer Generation, deren Schlüssel entfernt wurde, entfällt die Payload-Kollisionsprüfung **für immer**. `Event`-Zeilen werden von der Retention nie hart gelöscht (siehe § Retention-Begründung oben), sie laufen also **nicht** aus; nur der 72-h-Cache-Eintrag tut das. Der Ausgang bleibt der Zustand *vor* N14 (Treffer statt 422), also kein Rückschritt hinter den historischen Stand, und er betrifft ausschließlich denselben `(scope, user, idempotency_key)`-Strang derselben Nutzerin.
  **Handlungsempfehlung für Betreiber:** Altschlüssel erst nach Feldrotation (`reencrypt_fields`), Dateirotation (`reencrypt_files`), ungefilterter Primär-only-Prüfung (`--verify`), Klärung der Import-/Staging-Grenzen und tatsächlichem Ablauf betroffener lokaler/Off-Site-Backups entfernen; danach Prozesse mit dem gekürzten Ring neu erstellen und ungefiltert erneut prüfen ([Ops-Runbook § 9.5](ops-runbook.md#dateirotation-und-altschlüssel-entfernen)). Alte Idempotenz-Fingerprints lassen sich nicht umschlüsseln: Nach der Entfernung ihres Schlüssels entfällt für diese Ereignisse die Payload-Kollisionsprüfung, der Duplikat-Guard bleibt. Das ist ein dokumentierter Funktionsverlust, kein zusätzlicher Aufbewahrungsgrund für den Altschlüssel und kein Schutzziel der Betroffenen.

### Exportausschluss (verifiziert)

Alle Event-Serialisierer arbeiten als **Allowlist** (explizites Feld-Dict, keine Blockliste) — `idempotency_key`/`idempotency_payload_hash` sind darin schlicht nicht enthalten:

- Art.-15-/Auskunfts-Export: `_serialize_event()` in [`client/export.py`](../src/core/services/client/export.py), gepinnt in [`test_client_export.py`](../src/tests/test_client_export.py).
- Facility-/Backup-Export: `_serialize_event()` in [`system/facility_export.py`](../src/core/services/system/facility_export.py), gepinnt in [`test_facility_export.py`](../src/tests/test_facility_export.py).
- Offline-Bundle: `_serialize_event()` in [`system/offline.py:165`](../src/core/services/system/offline.py) (dieselbe Allowlist-Bauart), gepinnt in [`test_offline_bundle_api.py::test_bundle_contains_only_visible_events`](../src/tests/offline/test_offline_bundle_api.py) (Zeilen 210f. setzen den Hash, 228f. prüfen sein Fehlen im Bundle).
- CSV-Sachbericht-Export (`export_events_csv()` in [`system/export.py`](../src/core/services/system/export.py)) baut Zeilen ausschließlich aus fest benannten Spalten plus `document_type`-Feld-Slugs — der Hash kann strukturell gar nicht in eine Zeile geraten.
- Detail-Ansicht (kein Export im engeren Sinn, aber derselbe Vertrag — der Hash läuft dem UI nie zu): gepinnt in [`test_events_crud.py::TestEventDetailView::test_event_detail_renders`](../src/tests/test_events_crud.py) (Zeilen 582-590) — setzt den Hash explizit und prüft, dass er im gerenderten HTML nicht vorkommt.

### Trigger für Re-Evaluation

- Ein Dokumentationstyp ohne Freitextfeld wird für klientenbezogene Ereignisse verwendet — das erlauben die heutigen `FieldTemplate.FieldType`-Optionen bereits, der Tally-Zählpfad ist schon rein numerisch. Das ist kein hypothetischer künftiger Code-Umbau von `payload_fingerprint()`, sondern eine heute per `DocumentType`-Konfiguration erreichbare Einstellung: sobald ein Träger einen solchen Typ tatsächlich klientenbezogen einsetzt, ist der Rekonstruktions-Orakel-Fall aus der Unlinkability-Bewertung kein abstraktes Risiko mehr, sondern eine konkrete, im Betrieb beobachtbare Konfiguration. **Erledigt** durch den HMAC-Pepper (#1942): eine solche Konfiguration ist kein Auslöser mehr. Der Trigger bleibt hier stehen für **Alt-Fingerprints** aus der Zeit vor der Härtung — wird ein solcher Typ eingesetzt und liegen noch Bestandszeilen ohne Schema-Präfix in der Tabelle, gilt die Bewertung für genau diese Zeilen weiter.
- Ein Hard-Delete-/Purge-Pfad für `Event`-Zeilen wird eingeführt (dann braucht auch der Hash eine explizite Löschregel, statt implizit an der Zeilen-Lebensdauer zu hängen).
- Ein externer Audit fordert eine kürzere technische Aufbewahrung für Idempotenz-Metadaten.

### Verifikation

- [`test_anonymize_residue.py::TestAnonymizeResidueMatrix::test_idempotency_payload_hash_and_key_survive_anonymization`](../src/tests/test_anonymize_residue.py) — pinnt, dass die Spalte `anonymize_client()` (Hard-Pfad) unverändert übersteht.
- [`test_retention_k_anonymization.py::TestKAnonEnforcement::test_full_bucket_is_k_anonymized`](../src/tests/test_retention_k_anonymization.py) — pinnt, dass der K-Anon-Bridge-Pfad die Spalte ebenfalls nicht anfasst.
- [`test_retention_redaction.py::TestSoftDeleteEventRedacts::test_manual_soft_delete_writes_redacted_history`](../src/tests/test_retention_redaction.py) — pinnt, dass der manuelle Soft-Delete-Pfad (`soft_delete_event`) die Spalte nicht anfasst.
- [`test_client_export.py`](../src/tests/test_client_export.py), [`test_facility_export.py`](../src/tests/test_facility_export.py), [`test_offline_bundle_api.py`](../src/tests/offline/test_offline_bundle_api.py), [`test_events_crud.py`](../src/tests/test_events_crud.py) — pinnen den Exportausschluss/UI-Ausschluss.
- [`tests/_residue_expectations.py`](../src/tests/_residue_expectations.py) — Klassifikation `non_pii` für `core_event.idempotency_payload_hash`/`idempotency_key`.
- Threat-Model-Eintrag: [`threat-model.md` § 6 Bekannte offene Lücken](threat-model.md#6-bekannte-offene-lücken)

---

## Restidentifizierbarkeit der Tombstone-Timeline: Monatsbucket statt exakter Zeitpunkte (Issue #2047)

**Status:** Befund **mitigiert** — die Zeitachse anonymisierter Personen wird (#2047) auf den Monatsersten 00:00 Uhr generalisiert. Ein Restrisiko bleibt und ist unten benannt.

### Beobachtung

Nach der Anonymisierung (`anonymize_client()` wie K-Anon-Bridge) bleibt die `Event`-Zeile bewusst stehen — sie ist das Statistik-Aggregat, das die Löschung überlebt. Mit ihrem **exakten** `occurred_at` stünde je Tombstone die vollständige, personenverknüpfte Timeline aus Zeitpunkt × `document_type` × Fallstruktur weiter in der Datenbank und im Art.-15-Export (#2047).

Die k-Anonymitäts-Zusicherung deckte das nicht ab: `is_k_anonymous` attestiert die Äquivalenzklasse `(age_cluster, contact_stage)` — zwei niedrigdimensionale Merkmale. Die Timeline ist hochdimensional und wurde von *k* nie erfasst. Mit Außenwissen („Krisenintake am 3. Mai gegen halb drei") war ein Single-out bei realistischen Einrichtungsgrößen plausibel; der Datensatz war damit **pseudonymisiert, nicht anonym** — im Widerspruch zu der Zusage, die die Anwendung mit „anonymisiert" selbst gibt.

Zusätzlich fiel eine Asymmetrie auf: Der Hard-Pfad setzte `age_cluster` auf `UNKNOWN`, ließ `contact_stage` aber stehen. Die im Code hinterlegte Begründung verwies analog auf `k_anonymize_client` — die Analogie trägt nicht, denn die K-Anon-Primitive lässt **beide** Felder stehen (sie braucht sie für die Äquivalenzklasse), während der Hard-Pfad gar keine k-Zusicherung gibt und deshalb auch keines von beiden braucht.

### Entscheidung: Monat, nicht Tag

Abgewogen wurden Tages- und Monatsgenauigkeit.

- **Utility:** Die Auswertung aggregiert auf Monatsebene (`TruncMonth`). Der Informationsverlust des Monatsbuckets ist dort **null** — die Monatszahlen sind vor und nach der Anonymisierung identisch (gepinnt in `test_tombstone_timestamp_generalization.py::TestMonthlyAggregateUnchanged`).
- **Schutz:** Ein Tagesbucket ließe pro Person weiterhin eine Sequenz von Kalendertagen stehen — bei mehreren Ereignissen ein nach wie vor stark unterscheidendes Muster. Der Monatsbucket kollabiert diese Sequenz auf die Monatszählung.

Der Monatsbucket ist damit die Wahl, die den Schutz erhöht, ohne Utility zu kosten.

**Bucket-Grenze im lokalen Kalender.** Generalisiert wird gegen `TIME_ZONE` (`Europe/Berlin`), nicht gegen UTC. Ein Ereignis am 1. März um 00:30 Ortszeit liegt in UTC noch im Februar; ein UTC-Bucket schöbe es in den Vormonat und verfälschte genau die Monatsstatistik, deren Erhalt die Abwägung trägt.

**Beide Einstiege.** Der Helfer `_generalize_event_timestamps()` läuft im Hard-Pfad (`anonymize_client`, und damit auch im nächtlichen Trash-Expiry-Lauf und in „Jetzt endgültig anonymisieren") **und** in der K-Anon-Bridge. Für die Bridge gibt es hier — anders als bei `age_cluster`/`contact_stage` — keinen Ausnahmegrund: die k-Anonymität ruht auf den beiden niedrigdimensionalen Quasi-Identifikatoren, nicht auf der Timeline. Der ableitungsbasierte Paritäts-Guard (`TestKAnonBridgeRedactionParity`) erzwingt das strukturell.

**Transparenz in der Auskunft.** Der Art.-15-Export weist die Generalisierung aus (`event_timestamps_generalized` im JSON, eigene Zeile im PDF). Ohne diesen Hinweis läse sich die Auskunft, als hätten sämtliche Kontakte am Monatsersten um Mitternacht stattgefunden — eine unzutreffende Aussage über den eigenen Hilfeverlauf.

### Verbleibendes Restrisiko

- **Die Monatszählung selbst bleibt ein Merkmal.** Wer weiß, dass eine bestimmte Person im Mai 2026 genau sieben Kontakte eines bestimmten Dokumentationstyps hatte, kann einen Tombstone mit exakt diesem Profil weiterhin herausgreifen. Der Monatsbucket senkt die Dimensionalität deutlich, er beseitigt sie nicht. Ein weitergehender Schritt (Zeitachse ganz entfernen, Ereigniszahlen gruppieren) wurde verworfen, weil er die Monatsstatistik zerstört — genau den Zweck, für den die Zeile überhaupt stehen bleibt.
- **Nachbarspalten bleiben unangetastet.** `EventHistory.changed_at` und die Zeitstempel des Aktivitäts-Feeds tragen weiterhin exakte Zeitpunkte; ihr Inhalt ist redigiert, ihre Zeitachse nicht. Sie sind gegenüber `occurred_at` das schwächere Signal (Bearbeitungs-, nicht Kontaktzeitpunkt), aber kein Nullsignal.
- **Backups.** Sicherungsstände, die vor der Anonymisierung entstanden sind, tragen die exakten Zeitpunkte bis zum Ablauf der Backup-Rotation weiter — dieselbe Grenze wie bei jeder anderen Redaktion (§ Medien-Backups).

### Re-Evaluierungs-Trigger

Diese Bewertung ist an die heutige Auswertungs-Granularität gebunden und **neu zu bewerten**, sobald einer dieser Punkte eintritt (Muster #1935):

1. **Die Statistik wird feiner als monatlich.** Ein Wochen-, Tages- oder Uhrzeit-Bericht ließe sich aus generalisierten Tombstones nicht mehr bilden — dann steht die Abwägung „Utility gegen Single-out" neu an, und der Monatsbucket darf **nicht** stillschweigend gelockert werden, um den Bericht zu ermöglichen.
2. **Ein Hard-Delete-Pfad für `Event`-Zeilen entsteht.** Werden Tombstone-Zeilen künftig physisch entfernt, ist die Generalisierung für diese Zeilen gegenstandslos — sie bliebe aber für alle noch nicht entfernten nötig.
3. **Ein neuer Zeitstempel wird an der `Event`-Zeile aufbewahrt** (etwa ein fachlicher „gültig ab"-Wert). Er wäre ein neuer Kanal derselben Timeline und müsste in `_generalize_event_timestamps()` mitlaufen.
4. **Die k-Anonymitäts-Äquivalenzklasse wird erweitert.** Kämen weitere Quasi-Identifikatoren hinzu, ist erneut zu prüfen, welche davon der Hard-Pfad zerstören muss und welche die Bridge für ihre Zusicherung braucht — die #2047 aufgelöste Asymmetrie darf nicht in neuer Form zurückkehren.

---

## Offline-Bundles nach Anonymisierung: Revoke-Liste + 48-h-TTL als letzte Linie (Issue #1760)

**Status:** Aktiver Widerrufskanal (#1760), Reichweite erweitert in #1774 (gequeute Event-Edit-POSTs + Autosave-Entwurf); das Restrisiko „dauerhaft offline gebliebenes Gerät" bleibt akzeptiert und ist durch die Bundle-TTL begrenzt.

### Beobachtung

Ein offline mitgenommener Personendatensatz liegt als AES-GCM-verschlüsseltes Bundle in der IndexedDB des Geräts ([ADR-022](adr/022-offline-snapshot-keys.md), [`offline.py`](../src/core/services/system/offline.py)) — mit Pseudonym, Notizen, Fall-Titeln und Ereignisdaten im entschlüsselbaren Klartext, solange die Session lebt. Wird die Person serverseitig anonymisiert, räumt das zunächst **nur den Server** ab; ohne eigenen Kanal erreicht kein Signal das Gerät:

- Die Revalidierung beim nächsten Online-Kontakt (`revalidateCachedClient` in [`offline-bundles.js`](../src/static/js/offline-bundles.js)) purgt lokal nur bei **401/404/410**. Eine anonymisierte Person existiert aber weiter (die Zeile bleibt für Statistik-Aggregate stehen) und liefert **200**.
- Und selbst dieses 200 wird bewusst **nicht** über den lokalen Stand geschrieben, solange zu der Person ungesyncte Änderungen offen sind (`"skipped"`-Zweig — sonst überschriebe ein Refresh nicht replizierte Arbeit). Genau dort überlebte der Klartext.

Damit blieb der gelöschte Datenbestand bis zu 48 h (`BUNDLE_TTL_SECONDS`) auf dem Gerät lesbar. Bezug: #1476 (R20), #1493.

### Maßnahme: personen-genaue Revoke-Liste

Der Server führt eine Widerrufsliste ([`OfflineClientRevocation`](../src/core/models/offline_revocation.py), Migrationen `0126`/`0127`). Beide Anonymisierungs-Einstiege — der harte Pfad in `anonymize_client()` und die K-Anon-Bridge — schreiben in derselben Transaktion wie die Redaktions-Kaskade einen Eintrag. Der Eintrag ist **klartextfrei**: nur Einrichtung, die (ehemalige) Personen-UUID und der Zeitstempel; `client_pk` ist bewusst kein Fremdschlüssel, damit der Widerruf die Zeile überdauern kann.

Der Client holt die Liste unter `GET /api/v1/offline/revocations/` (Rolle Assistenz+, RLS-gescoped, `no-store`, eigenes großzügiges Rate-Limit `RATELIMIT_OFFLINE_REVOCATIONS`) und wendet sie an — **bei jedem Sync** als *erstem* Schritt der Sync-Orchestrator-Sequenz und **beim Start** — dort als eigener, minimaler Lauf unter demselben Web Lock und **höchstens einmal pro Minute**. Der Boot-Check ist notwendig, weil die Sync-Sequenz sonst nur ein `online`-Event oder replaybare Arbeit als Auslöser hätte — ein Gerät, das schlicht wieder benutzt wird, träfe nie auf die Liste. Sein Gate zählt alle vier Bestände, die der Purge tatsächlich anfasst (`clients`, `queue`, `drafts`, `events`); Entwürfe und ungesyncte Ereignisse können ohne gecachte Person existieren, und `conflict`/`dead`-Ereignisse lösen auch den Startup-Drain nicht aus. Der Boot-Poll ist client-seitig auf einen Lauf pro Minute gedrosselt (Zeitstempel in der `meta`-Tabelle), der Sync-Pfad bleibt ungedrosselt.

Drei Design-Entscheidungen mit Sicherheitsbezug:

1. **Widerruf läuft vor dem Replay — als *best effort*, nicht als Sperre.** Er steht als erster Schritt der Sync-Sequenz *vor* `replayQueue`/`replayAllModifiedEvents`; läge er danach, spielte der Replay die gequeuten Schreibzugriffe zu der gerade anonymisierten Person garantiert noch aus. **Er ist aber keine Vorbedingung:** schlägt der Abruf fehl (kein Netz, `429`, Login-Redirect), wird der Fehler geschluckt und der Replay läuft trotzdem — sonst blockierte ein Netzproblem die gesamte Offline-Synchronisation. Die Reihenfolge verkleinert das Fenster, sie schließt es nicht. **Harte Grenze bleiben die serverseitige Anonymisierung und die Bundle-TTL**, nicht dieser Kanal (siehe Restrisiko unten).
2. **Ungesyncte Arbeit wird verworfen, nicht als Dead-Letter erhalten** (fail-safe). Ein Dead-Letter behielte den entschlüsselbaren Klartext genau dort liegen, wo der Widerruf ihn entfernen soll; ein Replay träfe serverseitig ohnehin auf einen anonymisierten Datensatz. Es ist derselbe Löschgrund wie der Sicherheits-Purge bei Rechteentzug (F-10, #1110) und nutzt dieselbe Primitive (`removeOfflineClient(pk, {force: true})`) — kein zweiter Purge-Pfad. Betroffen sind Bundle-Spiegel, ungesyncte Ereignisse, Queue-Einträge und Entwürfe, **die die Personen-UUID in URL oder Payload tragen — oder die lokal einer widerrufenen Person zugeordnet werden können** (#1774). Das ist die Reichweite von `_mentionsRevoked`; sie ist keine Zusicherung über jeden Bestand mit Personenbezug, deckt aber zwei reale Lücken ab (#1774):

   - **Gequeuter `POST /events/<event-uuid>/edit/`** (analog `/workitems/<uuid>/edit/`): Die URL trägt die Ereignis-/Aufgaben-, nicht die Personen-UUID, und der Body enthält nur CSRF, `expected_updated_at` und Feld-Slugs. `applyClientRevocations` löst die Zuordnung deshalb über die lokale `events`-Tabelle auf (`pk`/`clientPk` liegen dort als Plaintext-Indexfelder — kein Session-Key nötig) und nimmt die so gefundenen Record-UUIDs als zweiten Suchbegriff-Satz in den Queue-/Entwurfs-Purge auf. **Reihenfolge ist hier sicherheitskritisch:** die Auflösung läuft *vor* dem Bundle-Purge, denn `removeOfflineClient(pk, {force: true})` löscht genau die `events`-Zeilen, die die Zuordnung tragen. Ein gequeuter `POST /partials/workitems/<uuid>/status/` trägt in URL und Body ebenfalls keine Personen-UUID und wird über denselben Kanal **nur** erfasst, wenn die Aufgabe bereits einen `events`-Eintrag hat (`data.kind === "workitem"`) — also bereits einmal offline voll editiert wurde. Eine reine Bundle-Aufgabe, die nie offline editiert, sondern nur im Status umgeschaltet wurde, hat keine solche Zeile; für sie greift die Rest-Grenze unten.
   - **Autosave-Entwurf von `/events/new/`:** Der Storage-Key bildet weiterhin nur `user` + `pathname` — der Personenbezug steckt jetzt im *verschlüsselten Umschlag*: ein Formularfeld weist sich per `data-autosave-person` aus (das versteckte, Alpine-gebundene `client`-Feld), und `autosave.js` schreibt dessen Wert unter dem reservierten Schlüssel `__personPk` mit. Bewusst nicht als Plaintext-Row-Feld — das wären unverschlüsselte PII-Verknüpfungsmetadaten (dieselbe Begründung wie der `occurredAt`-Ausbau in #1476/R9). Der Bezug ist monoton: eine neue Auswahl überschreibt ihn, ein Wiederherstellen (bei dem die Alpine-Auswahl zurückgesetzt ist) verliert ihn nicht. Ein Entwurf **ohne** gewählte Person bleibt stehen — er hat keinen Personenbezug, den der Widerruf treffen könnte.

   **Verbleibende Rest-Grenze** (präzisiert, siehe auch Restrisiko unten): ein gequeuter Edit-POST, dessen Ereignis lokal **nicht (mehr)** in der `events`-Tabelle steht *und* dessen Payload die Personen-UUID nicht trägt, bleibt bewusst liegen — ohne jeden Personenbezug wäre sein Verwurf ein Breitband-Löschen fremder Arbeit. Dieselbe Grenze gilt für einen `WORKITEM_STATUS`-POST einer reinen Bundle-Aufgabe (nie offline editiert, also ohne `events`-Zeile). Ebenso ein vor #1774 geschriebener Autosave-Entwurf, solange ihn kein Autosave-Tick neu geschrieben hat (Alt-Format wird tolerant gelesen, es gibt keine Migration; spätestens die 48-h-Draft-TTL räumt ihn ab). Für alle drei Fälle ist die serverseitige Ablehnung von Schreibzugriffen auf anonymisierte Personen (#1771) die zweite Linie.
3. **Der Abruf wird nicht auditiert.** Die Antwort ist PII-frei (opake UUIDs bereits anonymisierter Personen) und wird bei jedem Sync gepollt — ein Eintrag je Abruf würde das append-only Log fluten, ohne einen Datenabfluss zu dokumentieren. Dieselbe Abwägung wie beim audit-freien 304-Zweig der Bundle-Endpunkte. Die Widerrufs-*Erzeugung* hängt am bestehenden `CLIENT_ANONYMIZED`- bzw. Retention-Entscheidungs-Audit.

**Aufbewahrung der Widerrufe:** `OFFLINE_REVOCATION_RETENTION_SECONDS` = 2 × Bundle-TTL (96 h), danach Prune im nächtlichen `enforce_retention`-Lauf. Begründung: ein Bundle ist höchstens 48 h gültig, ein Gerät kann aber exakt am Fristende wieder online gehen und muss den Eintrag dann noch sehen — der doppelte Abstand deckt diese Überlappung plus Uhren-Drift ab, ohne die Liste unbegrenzt wachsen zu lassen. Der Eintrag ist zwar klartextfrei, bleibt aber ein pseudonymer Bezug auf eine bereits gelöschte Person und darf nicht länger liegen, als er einen Zweck hat (Art. 5 Abs. 1 lit. e).

### Abgrenzung: gelöschter **Fall**, nicht gelöschte Person (Refs #1818)

Mit dem Fall-Löschweg ([ADR-044](adr/044-fall-loeschpfad.md)) gibt es einen zweiten Weg, auf dem Inhalte aus dem Bundle verschwinden müssen. Der Bundle-Bau filtert seither über `cases_with_live_client()`, ein Fall im Papierkorb geht also **nicht mehr** mit hinaus. Ein **Widerruf** bereits ausgelieferter Bundles wird dabei bewusst **nicht** ausgelöst:

- Die Revoke-Liste ist **personen**-genau (`OfflineClientRevocation.client_pk`) und wirkt mit `force: true` — sie verwürfe die ungesyncte Arbeit zur **ganzen** Person, weil ein einzelner Fall gelöscht wurde. Für den häufigsten Anlass (versehentlich angelegter Fall) steht dieser Preis in keinem Verhältnis.
- Der reguläre Abgleich überschreibt den lokalen Bestand ohnehin mit einem Bundle **ohne** den gelöschten Fall; es bleibt dasselbe Zeitfenster wie beim Löschen eines **Ereignisses**, das ebenfalls keinen Widerruf auslöst.
- Letzte Linie bleibt auch hier die 48-h-Bundle-TTL.

Wer diese Grenze verschieben will, braucht einen fall-genauen Widerrufskanal — nicht einen zweiten Aufrufer des personen-genauen.

### Restrisiko (akzeptiert)

**Ein Gerät, das nach der Anonymisierung nie wieder online geht, erhält das Signal nicht.** Für diesen Fall bleiben die **48-h-Bundle-TTL** und die Leerlaufprüfung, beide aber erst beim nächsten Öffnen der App nach Ablauf: `purgeExpired`/`purgeExpiredBundles` verwerfen den Server-Spiegel rein lokal, wenn eine Seite der App mit geladenem Schlüssel startet und `expiresAt` überschritten ist; die Leerlaufprüfung (Session-Dauer, Default 30 Min) entzieht beim nächsten Ausführen den Schlüssel und löscht den Bestand nur, wenn keine ungesendete Arbeit vorliegt. Wird das Gerät nie wieder geöffnet, greift keine der beiden Linien. Ein Widerruf kann diese Grenze prinzipiell nicht unterschreiten — jede Server→Gerät-Zustellung setzt Konnektivität voraus.

**Der Widerruf ist ein Best-Effort-Kanal, keine Garantie.** Er ist eine Verbesserung gegenüber „gar kein Signal", ersetzt aber weder die serverseitige Löschung noch die TTL:

- **Fail-open bei Transportfehlern.** Netzfehler, `429` oder ein Login-Redirect liefern `error`/`ratelimited`; es wird nichts gelöscht (ohne verlässliche Liste *darf* nichts gelöscht werden) — und der nachfolgende Replay läuft trotzdem. Ein gequeuter Schreibzugriff zu einer inzwischen anonymisierten Person kann in diesem Fenster also serverseitig ankommen. **Der Server weist ihn ab** (#1771) — die serverseitige zweite Verteidigungslinie ist umgesetzt: `resolve_writable_client` (`services/client/main.py`) prüft an allen drei Formularen mit Personen-Auflösung (`EventMetaForm`, `CaseForm`, `WorkItemForm`) sowohl `is_deleted` (Papierkorb) als auch `is_active` (anonymisiert); `ClientUpdateView` und das Personen-Feld-Partial ziehen `is_active=True` nach. `EventUpdateView` löst den Personenbezug nicht über ein Formular auf (der Client ist am Ereignis fixiert) und zieht die Grenze deshalb direkt in `dispatch` — der PII-schwerste offline-queuebare Schreibpfad (`data_json`, `search_text`, neue `.enc`-Anhänge) ist damit ebenfalls dicht (#1771/#1774). Über den bestehenden 422-Contract landet der Replay damit als `localStatus: "dead"` in der Dead-Letter-UI und wird **nicht** erneut ausgespielt. Das schließt zugleich die Lücke, die bis dahin **unbefristet** stehenblieb — insbesondere Klartext-Titel und -Beschreibung eines WorkItems: `anonymize_clients` schließt Klienten mit `k_anonymized=True` bzw. dem `Gelöscht-`-Pseudonym dauerhaft aus dem Kandidatenfilter aus und verlangt zusätzlich `active_events = 0` — nach einem Replay nie wieder erfüllt. `_redact_workitems` läuft ausschließlich aus den beiden Anonymisierungs-Kaskaden, und die Retention-Pipeline kennt keinen WorkItem-Prune (`dsfa.md` § 2.3: „kein eigener Löschhorizont“). Es gibt also **keinen** nachgelagerten Lauf, der so etwas aufräumen würde — genau deshalb muss der Schreibzugriff schon an der Tür scheitern.
- **Der Boot-Poll ist gedrosselt** (1×/Minute). Innerhalb dieses Fensters wirkt nur der Sync-Pfad.

Zwei weitere, bewusst nicht geschlossene Kanten:

- **Ungesyncte Ereignisse überleben den reinen TTL-Ablauf** (by design, #1353 — kein stiller Datenverlust). Für ein dauerhaft offline gebliebenes Gerät bedeutet das: der Bundle-Spiegel fällt nach 48 h weg, eine offline erfasste Notiz zu derselben Person bleibt bis zum nächsten Online-Kontakt liegen. Sie ist dann der Anlass, nicht das Ergebnis der Löschung — und der Widerruf greift, sobald das Gerät zurückkehrt.
- **Ohne Session-Key räumt der Widerruf nur die Bundle-Zeilen ab** (planer Delete), nicht Queue-/Entwurfs-Zeilen: deren Personenbezug steckt im verschlüsselten Umschlag. Ohne Schlüssel sind sie allerdings auch für einen Angreifer unlesbar; der nächste Lauf mit Schlüssel holt es nach (die Liste ist cursor-frei und damit idempotent). Die Ereignis→Person-Auflösung aus #1774 arbeitet dagegen auf Klartext-Indexspalten und funktioniert auch ohne Schlüssel — sie *nützt* aber nur zusammen mit dem Queue-Purge, der den Schlüssel braucht.
- **Ein Edit-POST ohne jeden auflösbaren Personenbezug bleibt liegen** (#1774, präzisierte Rest-Grenze): Steht das Ereignis lokal nicht mehr in der `events`-Tabelle (Bundle-TTL abgelaufen, „Person aus Offline entfernen", oder der Edit-Request stammt aus einer rein online geladenen Seite) und nennt der Body die Person nicht, kennt der Client keine Zuordnung. Ein Purge „aller Edit-POSTs" wäre stattdessen ein Breitband-Löschen fremder Arbeit. Hier greift ausschließlich die serverseitige zweite Linie (#1771).

### Dritte Grenze: Ursprungs-Pinning der Personenzuordnung (#1786)

`resolve_writable_client` zieht zwei Grenzen um einen Schreibzugriff mit Personenbezug: **Facility-Scope** (die Person muss zur Einrichtung des Requests gehören) und **Widerrufsstatus** (`is_deleted`/`is_active`, #1771). Beide fragen *ob* die Zielperson beschreibbar ist — keine fragt, ob sie überhaupt die **richtige** ist. Ein manipulierter POST, der die versteckte `client`-UUID eines Bearbeiten-Formulars gegen die einer anderen, völlig regulären lebenden Person derselben Einrichtung tauscht, passiert beide Prüfungen unbemerkt und hängt den gesamten bestehenden Verlauf (Titel, Beschreibung, `data_json`, Anhänge, zugeordnete Ereignisse) an die neue Person um. Es gibt keinen nachgelagerten Lauf, der das aufräumt — dieselbe Begründung wie beim WorkItem-Klartext oben.

Die dritte Grenze ist deshalb das **Ursprungs-Pinning**: die Personenzuordnung entsteht beim Anlegen und ist danach nicht mehr frei änderbar. Sie sitzt im Service-Layer (SSOT, deckt auch Nicht-Formular-Aufrufer wie den Offline-Replay ab) und wird von Formular, View und Template lediglich gespiegelt.

| Datensatz | Semantik | Ort der Grenze |
|-----------|----------|----------------|
| `Case` | **strikt** — `client` ist kein Update-Feld mehr; jeder Kwarg wird abgewiesen, auch der unveränderte Wert (die Mass-Assignment-Prüfung darf nicht vom Diff abhängen) | `update_case` (Allowlist), `CaseForm.clean_client` (`pinned_client`), `CaseUpdateView` verwirft `client` vor dem Service-Aufruf |
| `WorkItem` | **Attach-only** — nachträgliches Zuordnen an eine *personenlose* Aufgabe bleibt erlaubt (Maintainer-Entscheid 2026-08-05); Wechsel und Entfernen werfen `ValueError`, identischer Wert ist ein No-op und fällt aus dem Audit-Diff | `update_workitem` (neue Allowlist + Pin-Prüfung), `WorkItemForm.clean_client` (`pinned_client`), `WorkItemUpdateView` reicht `client` nur im Attach-Fall durch |
| `Event` | nicht betroffen — der Personenbezug ist am Ereignis fixiert, `EventUpdateView` löst ihn gar nicht über ein Formular auf (siehe oben) | `EventUpdateView.dispatch` (#1771) |

**Das Pinning ersetzt die beiden anderen Grenzen nicht, es kommt dazu.** Der gepinnte Wert stammt aus dem Bestand statt aus dem POST — er wird trotzdem durch `resolve_writable_client` geschickt, sonst wäre das Bearbeiten-Formular einer inzwischen anonymisierten Person genau der Pfad, den #1771 schließt (bei `Case` fängt `cases_with_live_client()` das ohnehin schon mit 404 ab; `WorkItemUpdateView` hat kein solches Queryset und hängt allein am Formular-Gate).

Die Bearbeiten-Formulare rendern die gepinnte Person **read-only** (Pseudonym als Text, ohne `<input name="client">`). Das ist Ergonomie, keine Grenze: was nicht im DOM steht, kann nicht versehentlich umgebogen werden — der bewusst manipulierte POST scheitert an `clean_client` (sichtbarer Formularfehler) und, falls er das Formular ganz umgeht, am Service. `clean_client` prüft dabei den **rohen** POST-Wert: Wechsel *und* Entfernen (leeres Feld) sind gleichermaßen Fehler (#1791) — nur der Key, der gar nicht erst gesendet wird (der gepinnte Zustand rendert kein `<input name="client">`), ist der legitime Normalfall.

**Restpunkt geschlossen (#1789):** [`update_event`](../src/core/services/events/crud.py) hat jetzt ebenfalls eine Allowlist — eine **leere**. `**kwargs` bleibt in der Signatur, aber jeder durchgereichte Kwarg endet in einem `ValueError`, statt blind per `setattr` am Ereignis zu landen; der einzige Aufrufer (`EventUpdateView`) nutzt keine. Die Prüfung läuft direkt nach `check_version_conflict`, also vor jedem Write — ein abgewiesener Kwarg hinterlässt weder geänderte Felder noch eine `EventHistory`-Zeile. Ein künftiges Update-Feld muss bewusst eingetragen werden. Damit tragen alle vier Update-Services dieselbe Prüfung.

**Lost-Update-Fenster in `update_workitem` geschlossen (ebenfalls #1789):** Das abschließende `save()` schrieb bislang *alle* Felder der übergebenen — und potenziell stalen — Instanz zurück. Spalten außerhalb der Feld-Allowlist fielen damit auf den Stand des letzten Reads zurück: `status`/`completed_at` (eine parallel abgeschlossene Aufgabe wurde still wieder offen), der Recurrence-Marker (Doppel-Duplikat beim nächsten DONE), die Soft-Delete-Felder — und vor allem die von `_redact_workitems` redigierten Freitexte `title`/`description`. Genau für die gibt es keinen nachgelagerten Prune (siehe oben): ein zeitgleich offener Bearbeiten-Request hätte den Klartext unbefristet wieder festgeschrieben. Der Service speichert jetzt mit `update_fields` — ausschließlich die im Aufruf genannten Spalten plus `updated_at`.

**Dieselbe Härtung für `update_client` und `update_case` (Review-Nachzug zu #1789):** Beide Geschwister-Services schrieben ebenfalls per vollem `save()` die potenziell stale Instanz zurück. Schwerster belegbarer Pfad beim Client: die Soft-Delete-Spalten (`is_deleted`/`deleted_at`/`deleted_by`/`is_active`) bumpen `updated_at` nicht — ein zeitgleich offener Bearbeiten-Request konnte eine parallel committete Vier-Augen-Löschung still reaktivieren, ohne Selbstheiler und ohne Audit-Spur. Beim Fall re-publizierte derselbe Mechanismus die von der Redaktions-Kaskade anonymisierten `title`/`description` unbefristet (kein Lauf redigiert den Fall erneut, sobald die Person aus dem Kandidatenfilter gefallen ist). Beide Services speichern jetzt mit `update_fields` nach dem `update_workitem`-Muster; Tests: [`test_clients.py`](../src/tests/test_clients.py) (`erhaelt_parallelen_soft_delete`/`erhaelt_parallele_redaktion`), [`test_cases.py`](../src/tests/test_cases.py) (`erhaelt_parallele_redaktion`).

### Verifikation

- [`test_cases.py`](../src/tests/test_cases.py) / [`test_workitems_service_helpers.py`](../src/tests/test_workitems_service_helpers.py) — Service-Grenze des Pinnings (#1786): Case strikt (auch der unveränderte Wert fliegt), WorkItem Attach-only inkl. No-op-Fall und der neuen Feld-Allowlist.
- [`test_events_crud.py`](../src/tests/test_events_crud.py) / [`test_workitems_service_helpers.py`](../src/tests/test_workitems_service_helpers.py) — die Nachzügler aus #1789: leere `update_event`-Allowlist (auch der `client`-Kwarg fliegt, ohne Teil-Write) und der Lost-Update-Schutz von `update_workitem` (paralleler Statuswechsel, Recurrence-Marker und Redaktion überleben einen stalen Save).
- [`test_client_pinning_views.py`](../src/tests/test_client_pinning_views.py) — die Fläche darüber (#1786): manipulierter POST auf eine andere lebende Person derselben Einrichtung ändert nichts (Formularfehler statt Redirect), Bearbeiten-GET rendert die Person ohne `<input name="client">`, Neuanlage und personenlose Aufgabe behalten das Autocomplete.
- [`test_offline_client_revocation.py`](../src/tests/offline/test_offline_client_revocation.py) — Widerruf entsteht auf beiden Anonymisierungspfaden, Endpoint ist facility-gescoped/audit-frei, Prune-Fenster.
- [`test_revoked_client_write_gate.py`](../src/tests/test_revoked_client_write_gate.py) — die serverseitige zweite Linie (#1771): Formular-Gates für beide Widerrufs-Zustände, Replay endet reproduzierbar als 422-Dead-Letter (kein Retry-Loop), und ein Multipart-Schreibzugriff erzeugt weder `Attachment`-Zeile noch `.enc`-Datei.
- [`tests/js/offline-revocations.test.mjs`](../tests/js/offline-revocations.test.mjs) — Client-Purge gegen echtes Dexie + echte Krypto (inkl. ungesyncter Arbeit und Entwürfe) sowie die Ereignis→Person-Auflösung gequeuter Edit-POSTs samt Reihenfolge-Pin und der konservativ stehen bleibenden Rest-Grenze (#1774).
- [`tests/js/autosave-draft-person.test.mjs`](../tests/js/autosave-draft-person.test.mjs) — Personenbezug des Autosave-Entwurfs Ende-zu-Ende: `data-autosave-person` → `__personPk` im verschlüsselten Umschlag → Purge (#1774), inkl. Entwurf ohne Person und tolerant gelesenem Alt-Format.
- [`tests/js/offline-revocation-boot.test.mjs`](../tests/js/offline-revocation-boot.test.mjs) — Boot-Gate über alle vier vom Purge betroffenen Tabellen (insb. `drafts`-only und `conflict`-Ereignisse ohne gecachte Person) plus die 1×/Minute-Drossel.
- [`e2e/test_offline_revocation_journey.py`](../src/tests/e2e/test_offline_revocation_journey.py) — Person mitgenommen → anonymisiert → Sync bzw. Boot purgt sie restlos; der gequeute Schreibzugriff wird nicht repliziert.
- Threat-Model: [`threat-model.md` § TB6](threat-model.md), Zeile „**I** Bundle überlebt die Löschung der Person".

---

## `Client.pseudonym` bleibt im Klartext bis post-v1.0 (Issue #717)

**Status:** Bewusste Defer-Entscheidung. Re-Evaluation per Trigger-Liste.

### Beobachtung

[`Client.pseudonym`](../src/core/models/client.py) ist ein
`CharField(max_length=100, db_index=True)` mit zusätzlichem
[`GinIndex` für `gin_trgm_ops`](../src/core/models/client.py) (`Client.Meta.indexes`).
Trigram-Fuzzy-Search läuft über [`src/core/services/dashboard/search.py`](../src/core/services/dashboard/search.py)
und ist eine zentrale UX-Funktion für Fachkräfte ("Marie" findet auch
"Maria-23"). Bei einem **Backup-Diebstahl** mit Klartext-Pseudonymen ist
direkte Wiedererkennung in Kontaktläden möglich (Quelle: internes Code-Audit,
dev-only).

### Was bereits umgesetzt ist

[`FieldTemplate`-Validator `Sensitivity=HIGH ⇒ is_encrypted=True`](../src/core/models/field_template.py) (`FieldTemplate.clean`)
erzwingt, dass HIGH-Felder **immer** encrypted sind. Dieser Teil des Befunds ist
funktional + per Test verifiziert (siehe [`tests/test_field_template_validator.py`](../src/tests/test_field_template_validator.py)).

### Was bewusst nicht jetzt umgesetzt wird

`Client.pseudonym` per `EncryptedTextField` + HMAC-Lookup-Index. Das
interne Code-Audit stuft das als strukturelle Maßnahme mit großem Aufwand
nach v1.0 ein (Item 51):

> 51 | `Client.pseudonym`-Verschlüsselung mit HMAC-Lookup-Index | L | hoch

### Trade-off

**Wenn jetzt durchgezogen:**
- Trigram-Fuzzy-Search bricht — entweder UX-Verlust oder HMAC-Bucket-
  Fuzzy mit n-gram HMACs (deutlich teurere Implementierung)
- Sortierung `ordering=["pseudonym"]` (`Client.Meta` in [client.py](../src/core/models/client.py))
  liefert auf encrypted Bytes keine sinnvolle Reihenfolge
- `unique_facility_pseudonym`-Constraint braucht HMAC-basierten Unique-Index
- Sämtliche `pseudonym__ilike`-Filter (Suche in [`search.py`](../src/core/services/dashboard/search.py)) und ihre Tests
  müssen umgestellt werden

**Aktueller Schutz:**
- Backup ist AES-256-CBC + PBKDF2-verschlüsselt mit `BACKUP_ENCRYPTION_KEY`
  ([`backup.sh`](../scripts/ops/backup.sh)) — Klartext-Pseudonyme nur bei
  Schlüssel-Kompromittierung exponiert
- Optionaler Off-Site-Abgleich (`BACKUP_OFFSITE_TARGET`, Refs #738);
  Object-Lock am Ziel gegen Ransomware richtet der Betreiber ein
- RLS + Role-Mixin verhindern Pseudonym-Leak via App-Schicht
- Pseudonyme sind bereits Decoy-Namen, nicht Klarnamen — die Re-
  Identifikations-Hürde ist UX-abhängig (Mitarbeiter müsste den Namen
  in der Einrichtung wiedererkennen)

### Trigger für Re-Evaluation (dann Item 51 angehen)

- v1.1-Release-Planung erreicht — Item 51 ist explizit „post-v1.0"
- Externe Compliance-Auditierung fordert Encryption-at-Rest auch für
  Pseudonyme (Art.-9-Bias-Argument verschärft sich)
- Backup-Storage wechselt zu einem System ohne starkes
  Object-Lock/Encryption-at-Rest
- Realistisches Backup-Diebstahl-Szenario in einer Pilot-Einrichtung
  (Sicherheits-Vorfall reicht)

### Verifikation

- Validator-Test: [`tests/test_field_template_validator.py`](../src/tests/test_field_template_validator.py)
- Threat-Model-Eintrag: [`threat-model.md` § TB2/TB3, Zeile *I Sensitive Felder ohne Encryption*](threat-model.md#tb2tb3--django--postgresql)

---

## CSP-Reporting (Issue #684)

**Status:** Aktiv. Detection-Lücke L2 aus einem internen Sicherheitsbericht 2026-04-26 (dev-only) geschlossen.

Die globale CSP enthält jetzt `report-uri /csp-report/` ([`base.py`](../src/anlaufstelle/settings/base.py), `CONTENT_SECURITY_POLICY`). Browser POSTen Verstöße als `application/csp-report` (CSP Level 2) oder `application/reports+json` (CSP Level 3) — die View [`CSPReportView`](../src/core/views/csp_report.py) parsed beide Formate, loggt strukturiert als WARNING auf dem `security.csp`-Logger und antwortet `204 No Content`.

**Härtung:**
- Rate-Limit `10/m` pro IP gegen Log-Flooding
- 32-KiB-Body-Cap gegen Payload-Spam
- `csrf_exempt` (Browser-Reports tragen keinen Token; Endpoint ist write-only ⇒ kein CSRF-Risiko)
- Eigener Logger-Namespace `security.csp` (außerhalb des `core`-Loggers, damit Sentry-Integration und pytest-caplog beide funktionieren)

**Detection-Pfad:** Primär landen CSP-Violations in der lokalen Log-Datei (Logger `security.csp`, Level `WARNING`, mit allen Feldern `blocked-uri`, `violated-directive`, `source-file`, ...) und werden von dort per Caddy/Filebeat/Loki ausgewertet. Ist `SENTRY_DSN` gesetzt ([`prod.py`](../src/anlaufstelle/settings/prod.py)), erzeugen diese WARNINGs **keine eigenen Sentry-Events:** Die Sentry-`LoggingIntegration` ist bewusst nicht mit einem eigenen `event_level` konfiguriert, sodass ihr Default `event_level=ERROR` gilt — WARNINGs erscheinen daher nur als **Breadcrumb** an einem später erfassten Error-Event, nicht als eigenständige Violation-Meldung im Dashboard. Diese Breadcrumbs durchlaufen den `before_send`/`before_send_transaction`-Scrubber ([`_sentry.py`](../src/anlaufstelle/settings/_sentry.py)), bevor sie das Programm verlassen.

**Bewusste Entscheidung (Noise-/PII-Abwägung):** Kein explizites `event_level`-Capture für `security.csp`. CSP-Reports sind hochvolumig und teils browser-/angreifer-kontrolliert (`blocked-uri`/`source-file` können URLs mit Query-Strings tragen); sie als eigene Sentry-Events zu erfassen, würde Rauschen erzeugen und die Scrub-Grenze für frei befüllbare Report-Felder unnötig ausreizen. Wer CSP-Violations aktiv im Tracker auswerten will, richtet die Alarmierung stattdessen auf den `security.csp`-Log-Stream ein. In #1949 erneut geprüft und bestätigt — mit einem zusätzlichen technischen Argument: Die `LoggingIntegration` patcht `logging.Logger.callHandlers` **global**, ein `event_level` lässt sich also gar nicht auf `security.*` beschränken, und der SDK-Handler hebt sämtliche `extra`-Felder eines Records ins Event — für `security.csp` also `remote_addr` (Client-IP) und `user_agent` in jedes Event. Der Sink ist auch im [Threat-Model](threat-model.md#tb1--browser--caddydjango) (TB1, CSP-Zeile) benannt.

**Verifikation:** [`tests/test_csp_report.py`](../src/tests/test_csp_report.py) — Report-Parsing beider Formate, Feld-Whitelist, Steuerzeichen-Filter, Violation-Cap sowie das Rate-Limit selbst (#1949) (10 Reports/min/IP → 204, der elfte → 429, neues Zählfenster lässt wieder durch, und der Zähler läuft pro Client-IP statt global).

**Bewusste Trade-off-Entscheidung:** `report-to` (Reporting API + `Reporting-Endpoints`-Header) ist nicht konfiguriert — `report-uri` deckt alle relevanten Browser ab und wurde in #684 bewusst als ausreichende Implementierung gewählt. Falls die Browser-Spezifikation `report-uri` deprecaten sollte, muss `report-to` ergänzt werden.

---

## GitHub-Repo-Härtung: Workflow-Permissions, SHA-Pinning, Branch Protection (Issue #888)

**Status:** Minimal-Permissions und SHA-Pinning sind in den Workflow-Dateien umgesetzt. Ein Branch-Protection-Ruleset ist aus dem Repository heraus nicht belegbar und wird hier nicht zugesichert.

### Hintergrund

Am 11. Mai 2026 veröffentlichte ein Angreifer **84 bösartige Versionen** über 42 `@tanstack/*`-npm-Pakete, später ausgeweitet auf insgesamt ≈169 npm-Pakete und 4 PyPI-Pakete ([„Mini Shai-Hulud"](https://www.aikido.dev/blog/mini-shai-hulud-is-back-tanstack-compromised), [TanStack-Postmortem](https://tanstack.com/blog/npm-supply-chain-compromise-postmortem)). Anlaufstelle ist vom Vorfall selbst **nicht betroffen**:

| Quelle | Geprüft | IoC-Treffer |
|---|---|---|
| [`package-lock.json`](../package-lock.json) | 73 Pakete, alle mit `integrity` | keine |
| [`requirements.txt`](../requirements.txt) / [`requirements-dev.txt`](../requirements-dev.txt) | Django-/Playwright-Stack | keine |
| `.github/workflows/*` | kein `pull_request_target`, kein `workflow_run` | nicht ausnutzbar |

Die folgenden Härtungen sind als Defense-in-Depth umgesetzt.

### Umgesetzt: Minimal-`permissions:`-Blocks

Alle Workflows unter `.github/workflows/` haben einen expliziten `permissions:`-Block. Statt des Default-`GITHUB_TOKEN` mit Schreibrechten läuft jeder Workflow mit dem Minimum:

| Workflow | Top-Level / Job | Permissions |
|---|---|---|
| [`codeql.yml`](../.github/workflows/codeql.yml) | Job | `actions: read`, `contents: read`, `security-events: write` |
| `dev-image.yml` (dev-only) | Job | `contents: read`, `packages: write` (GHCR-Push) |
| [`e2e.yml`](../.github/workflows/e2e.yml) | Top-Level | `contents: read` |
| [`lint.yml`](../.github/workflows/lint.yml) | Top-Level | `contents: read` |
| `mutation-nightly.yml` (dev-only) | Top-Level | `contents: read` |
| `perf-nightly.yml` (dev-only) | Top-Level | `contents: read`, `issues: write` |
| [`release.yml`](../.github/workflows/release.yml) | Job | `contents: read`, `id-token: write` (cosign keyless), `packages: write` (GHCR-Push) |
| [`test.yml`](../.github/workflows/test.yml) | Top-Level | `contents: read` |

`issues: write` in `perf-nightly` ist nötig, weil [`peter-evans/create-issue-from-file`](https://github.com/peter-evans/create-issue-from-file) bei einer Budget-Regression automatisch ein Issue anlegt. `id-token: write` in `release.yml` braucht der Schritt „Sign the pushed image (cosign keyless)“: Cosign holt sich darüber ein OIDC-Token für die schlüssellose Signatur des Image-Digests. Die als dev-only markierten Workflows entfernt der Release-Build aus dem öffentlichen Baum.

### Umgesetzt: SHA-Pinning aller Actions

Alle `uses:`-Referenzen in allen Workflows zeigen auf **40-stellige Commit-SHAs** statt auf Tags. Format: `uses: <owner>/<action>@<sha>  # vX.Y.Z`. Für `release.yml` pinnt das `test_all_actions_are_commit_sha_pinned` (`TestReleaseWorkflowSupplyChain` in [`test_architecture_guards_ops.py`](../src/tests/test_architecture_guards_ops.py)); für die übrigen Workflows gilt der Prüfbefehl unter „Verifikation“. Dependabot ist über das `github-actions`-Ökosystem in `.github/dependabot.yml` (dev-only) konfiguriert (wöchentlich, Mo 06:00 Europe/Berlin) und aktualisiert SHA und Tag-Kommentar bei neuen Versionen.

**Warum:** Tags sind mutierbar. Bei einer Übernahme eines Action-Repositorys könnte ein Angreifer einen Tag auf einen bösartigen Commit verschieben — der nächste CI-Lauf würde dann den bösartigen Code ausführen. SHA-Pinning blockt diesen Vektor; Dependabot übernimmt das laufende Update.

### Branch-Schutz und Maintainer-Nachweis

Branch-Schutz/Rulesets stehen für das private Dev-Repo ohne GitHub Pro nicht zur Verfügung; Ersatz sind lokale Gates, signierte Tags und der Release-Dry-Run. Für das öffentliche app-Repo ist ein Ruleset vorgesehen: kein Force-Push, keine Löschung.

Der Nachweis der aktiven Einstellung erfolgt über die Maintainer-Checkliste #1597. Der lokale Quellbaum belegt keine aktive GitHub-Einstellung; diese Dokumentation behauptet keine eigene Prüfung des Rulesets. Vorgesehen sind zwei getrennte Rulesets auf `refs/heads/main`; die Tabelle ist eine Sollvorgabe, kein Aktivierungsnachweis:

| Ruleset / Regel | Vorgesehener Wert |
|---|---|
| `main-integrity` | Block force pushes (`non_fast_forward`) und Restrict deletions (`deletion`); keine Bypass-Liste |
| `main-pr` | Pull Request (`pull_request`) und erforderliche Status-Checks (`required_status_checks`) |
| `main-pr` Bypass | Nur die konkret benannte Release-Identität, Modus „Always“; keine Restrict-updates-Regel (`update`) |
| Erforderliche Status-Checks | `lint`, `licenses`, `translation-versions`, `translations-coverage`, `typecheck`; `test (1)`, `test (2)`, `test (3)`, `coverage`, `check`, `audit`, `lock-check`; `E2E-Scheibe 1` bis `E2E-Scheibe 4` |

Die Check-Liste ist aus Job-IDs, Job-`name` und Matrix-Schema der ausgelieferten [Lint](../.github/workflows/lint.yml)-, [Test](../.github/workflows/test.yml)- und [E2E](../.github/workflows/e2e.yml)-Workflows abgeleitet; gegen die Check-Namen eines App-Laufs abgleichen, bevor sie als erforderlich gesetzt wird. CodeQL liefert zusätzlich `Analyze (python)` und `Analyze (javascript)`; der Tag-Workflow `release.yml` liefert `build-and-push` und ist kein PR-Check.

Der Maintainer prüft `enforcement = active`, das Ziel sowie Regeln und Bypass-Felder mit `gh api repos/anlaufstelle/app/rulesets` und je ID `gh api repos/anlaufstelle/app/rulesets/$ID`. Bietet GitHub für die benannte Identitätsart keinen Bypass an, ist eine Rückfrage erforderlich; eine Admin-Rolle ist kein Ersatz.

### Trigger für Re-Evaluation

- **Erster externer Contributor** öffnet einen Fork-PR — dann `pull_request_target` und `workflow_run`-Trigger weiter meiden und neue Workflows darauf prüfen.
- **CodeQL-Findings** zu Workflow-Hygiene — über den [GitHub Security-Tab](https://github.com/anlaufstelle/app/security) verfolgen (CodeQL läuft nur in öffentlichen Repositorys, siehe [`codeql.yml`](../.github/workflows/codeql.yml), Bedingung `github.event.repository.private == false`).

### Verifikation

- `grep -rhE '^\s*-?\s*uses: ' .github/workflows/ | grep -vE '@[0-9a-f]{40}(\s|$)'` muss leer sein → keine Action ohne Commit-SHA.
- `grep -L 'permissions:' .github/workflows/*.yml` muss leer sein → jeder Workflow hat Permissions (Top-Level oder Job-Level).
- `test_all_actions_are_commit_sha_pinned` und die Cosign-Prüfung in `TestReleaseWorkflowSupplyChain` ([`test_architecture_guards_ops.py`](../src/tests/test_architecture_guards_ops.py)).

---

## Dependabot-Ignore für Django-Updates (Issue #2264)

**Status:** Akzeptiertes, zeitlich begrenztes Restrisiko mit manueller Kompensation.

Die Anwendung pinnt Django auf `Django>=6.1.1,<6.2`. In `.github/dependabot.yml` ignoriert Dependabot für `dependency-name: "django"` exakt die Update-Typen `version-update:semver-minor` und `version-update:semver-major`. Minor- und Major-Sprünge kommen deshalb nicht automatisch als Dependabot-PR; Patch-Bumps der gepinnten 6.1-Linie laufen über die Gruppe `django-stack` weiter automatisch.

Das Restrisiko besteht vor allem darin, dass dieser Ignore das Ende des Security-Supports der gepinnten Linie still überdauert. Zusätzlich können Härtungen fehlen, die nur in einer späteren Minor-Linie erscheinen und nicht zurückportiert werden.

Drei Netze kompensieren dieses Risiko:

1. Dependabot-Alerts und Security-Updates sind auf dem Entwicklungs-Repository aktiv.
2. Ein manueller Check sichtet monatlich die Django-Security-Releases und führt lokal `pip-audit -r requirements.txt` aus.
3. Bei jedem Release-Cut läuft `pip-audit` im Release-Gate.

`pip-audit` speist sich aus OSV und der PyPI Advisory Database; GHSA-Einträge ohne
PyPI-Spiegel können deshalb fehlen. So meldete es für `django-otp-webauthn==0.10.0`
keinen Befund, obwohl
[GHSA-8r76-c459-ww98](https://github.com/Stormbase/django-otp-webauthn/security/advisories/GHSA-8r76-c459-ww98)
Versionen unter 0.10.3 betraf. Eine zweite Werkzeugkette im Release-Gate wird dafür nicht
eingeführt: Die Dependabot-Alerts des Entwicklungs-Repositories dienen als Zweitquelle,
ergänzt durch den monatlichen manuellen Check aus Punkt 2.

Der Ignore muss **vor Dezember 2027**, also vor dem Ende des Security-Supports der Django-6.1-Linie, durch die zweite Etappe des LTS-Umstiegs #1662 abgelöst werden.

---

## K-Anonymität gegen Re-Identifikation in Aggregaten (Issue #999)

**Status:** Design-Entscheidung, produktiv verdrahtet.

### Beobachtung

Aggregat-Auswertungen (Statistiken, externe Berichte) können trotz Pseudonymisierung
einzelne Personen offenlegen, wenn eine Merkmalskombination (z.B. *Alterscluster* ×
*Geschlecht*) nur bei einer einzigen Person vorkommt. Pseudonymisierung schützt den
direkten Identifikator, **nicht** vor Re-Identifikation über Kombinationen indirekter
Merkmale.

### Maßnahme

Anlaufstelle wendet **K-Anonymität** an: Aggregat-Zellen mit weniger als *k*
gezählten Einheiten werden unterdrückt (`count=None`, `suppressed=True`). Schwelle
pro Einrichtung über [`Settings.k_anonymity_threshold`](../src/core/models/settings.py)
(Default **5**).

> **Präzisierung (Refs #1742,
> #1770):** Welche Einheit gezählt wird,
> hängt am **Bucket-Typ**:
>
> * **Personenabgeleitete** Aufschlüsselungen — `by_age_cluster`, `by_contact_stage.identifiziert`,
>   `by_contact_stage.qualifiziert` und `unique_clients` — werden gegen die **DISTINCT-Personenzahl**
>   des Buckets im Berichtszeitraum geprüft, zusätzlich zur Ereigniszahl, und es gilt die strengere der
>   beiden. Eine sichtbare Zelle „Unter 18: 7" bedeutet seither 7 *Kontakte* von **mindestens *k*
>   Personen**; ist die Personenzahl unbekannt (Snapshot ohne Personenzahl, retention-bereinigter Monat), wird
>   unterdrückt.
> * **Ausnahme `by_contact_stage.anonym`** — hier ist die Prüfgröße **keine** Personenzahl, sondern
>   „DISTINCT *verknüpfte* Personen **+** Anzahl der *unverknüpften* Ereignisse". Die Stufe ist als
>   „`is_anonymous` ODER `client IS NULL`" definiert; Ereignisse ohne Klienten-FK tragen überhaupt
>   keinen Personenbezug und sind untereinander nicht verknüpfbar, weshalb jedes als eigenes
>   Individuum zählt (`statistics.py`, `persons_by_contact_stage`; gepinnt in
>   `test_statistics.py::TestPersonenzaehlungImZeitraum::test_anonyme_stufe_zaehlt_ereignisse_ohne_personenbezug_je_einzeln`).
>   **Konsequenz, die die Zusicherung einschränkt:** die dahinterliegende Personen-Äquivalenzklasse
>   kann *kleiner* als *k* sein. Konstruktion bei *k* = 5: eine Person mit 12 `is_anonymous`-Ereignissen
>   plus 4 unverknüpfte Ereignisse ergibt Prüfgröße 1 + 4 = 5 ≥ *k* — „anonym: 16" bleibt sichtbar,
>   obwohl nur **eine** identifizierbare Person dahintersteht. Bewusst akzeptiert: die unverknüpften
>   Ereignisse tragen keine identifizierenden Attribute, an denen eine Re-Identifikation ansetzen
>   könnte; die Alternative (unverknüpfte Ereignisse mit 0 Personen bewerten) hätte die Stufe in jeder
>   niedrigschwelligen Einrichtung dauerhaft unterdrückt, ohne jemanden zu schützen. Wer die
>   Zusicherung zitiert, muss diese Stufe ausnehmen.
> * **Ereignisabgeleitete** Aufschlüsselungen (`by_document_type`, `by_category`, `total_contacts`)
>   zählen weiterhin **Ereignisse** (`Count("id")`). Ihre Buckets sind Dokumenttypen, keine
>   Personenmengen — eine Personenschwelle hätte dort keine Äquivalenzklasse, auf die sie sich bezöge.
>
> „Weniger als *k* **Personen**" gilt (#1770)
> für `by_age_cluster` und `unique_clients` (sowie die beiden nicht-anonymen Kontaktstufen) — **nicht**
> für `by_contact_stage.anonym`, siehe die Ausnahme oben. Die verbleibende Ungleichung zwischen
> Personen- und Ereigniszählung ist als akzeptierte Definitionsgrenze (2) unten dokumentiert.

Verdrahtet in den datenschutzfreundlichen externen Berichten
([`external_report.py`](../src/core/services/dashboard/external_report.py), Refs #921)
und optional im Retention-Löschpfad (`retention_use_k_anonymization`, Refs #780).
Konzeptionelle Definition mit Beispiel: [Glossar § K-Anonymität im Detail](glossar.md#k-anonymität-im-detail).
Hintergrund/Trade-offs: [ADR-023](adr/023-k-anonymization-statistik.md).

### Geltungsbereich der Suppression (Issue #1311)

Die Small-Cell-Suppression ist **artefakt-**, nicht rollenbasiert: unterdrückt wird
in genau den Ausgaben, die die Einrichtung **verlassen** und deren Empfänger **keinen**
Row-Level-Zugriff auf die Rohdaten haben.

**Suppression aktiv (verdrahtet + getestet):**

- On-Screen-**External-Report** `/statistics/external/` (HTML **und** `?format=json`) — [`build_external_report`](../src/core/services/dashboard/external_report.py), Refs #921.
- **Beispiel-Sachbericht-PDF** (`generate_sample_report_1_pdf` → `suppress_sample_report_1_stats`) — das am ehesten extern zirkulierende Artefakt, Refs #1278.
- **Halbjahres-Sachbericht-PDF** im Standard-(externen-)Modus (`generate_report_pdf` → `suppress_report_stats`); `?internal=1` mit INTERN-Banner (Lead/Admin) bleibt roh — Security-Review R4.
- **Randsummen** (`total_contacts`/`total`) unterhalb der Schwelle sind selbst Kleinstfallzahlen und werden ebenfalls unterdrückt — Security-Review R14.

Gemeinsame Logik (Single Source of Truth): `_suppress_small` / `_suppress_stage_dict` /
`_audit_feasible_span` / `apply_residual_guard` in
[`external_report.py`](../src/core/services/dashboard/external_report.py), gestützt auf den reinen
Inferenz-Rechenkern [`disclosure.py`](../src/core/services/dashboard/disclosure.py); die drei
Public-Wrapper (`build_external_report`, `suppress_sample_report_1_stats`, `suppress_report_stats`)
normalisieren nur die jeweilige Datenform — kein Copy-Paste der Suppression selbst. Seit
#1770 erzwingt das ein Architektur-Guard
(`TestZentraleSuppressionsfunktion`), und die beiden sicherheitsrelevanten Entscheidungen jeder
Aufrufstelle — gibt es eine publizierte **Randsumme** (`margin`) und welche kanonische
**Ausgabeordnung** gilt (`order`) — sind Pflicht-Keywords statt geratener Defaults.

**Suppression BEWUSST NICHT aktiv:**

- **Internes Statistik-Dashboard** (`StatisticsView` → `get_statistics_hybrid`) und
- **Trend-JSON-API** (`ChartDataView` → `get_statistics_trend`).

Begründung: Beide Sichten sind über `LeadOrAdminRequiredMixin` auf **einrichtungs-interne**
Lead-/Admin-Rollen beschränkt, die unter Row Level Security ohnehin **Zeilen-Zugriff auf
dieselben Roh-Events/-Klienten** haben — sie können jede Kleinstzelle per Drill-down in die
Einzelfälle direkt einsehen. Eine Aggregat-Suppression brächte hier **keinen Privacy-Gewinn**,
aber **Usability-Kosten** (das eigene operative Dashboard zeigte „unterdrückt" auf Zahlen,
die die Rolle regulär sehen darf). Die Schutzgrenze ist der **Zweck des Artefakts** (externe
Weitergabe vs. interne Steuerung), **nicht** die betrachtende Person — deshalb unterdrückt der
On-Screen-External-Report (WYSIWYG-Vorschau des Herausgabe-Artefakts) trotz identischer
Betrachterrolle. Ändert sich diese Entscheidung (Suppression im internen Pfad), muss diese
Notiz mit — festgeschrieben in `test_statistics_hybrid.py::TestDashboardKAnonScopeRaw` und
`test_statistics_trend.py::TestTrendKAnonScopeRaw`.

**Client-Level-Retention-k-Anon bleibt per Default AUS** (`Settings.retention_use_k_anonymization = False`).
Der Default ist **Hard-Delete** — die stärkere, fail-safe Datenschutz-Voreinstellung (der
Datensatz wird zerstört, nicht nur generalisiert). K-Anonymisierung im Retention-Pfad
**erhält** statistisch verwertbare, generalisierte Datensätze und ist damit eine bewusste
Aufbewahrungs-Abwägung, die eine Einrichtung aktiv (Opt-in) setzen und im
Verarbeitungsverzeichnis dokumentieren muss; ein Default-„AN" würde Daten still aufbewahren,
die eine naive Betreiberin gelöscht glaubt (Compliance-Regress). Zusätzlich fällt der
aktivierte K-Anon-Pfad bei unterbesetzten Buckets (< *k*) fail-safe auf Hard-Delete zurück
(Security-Review N5). Default festgeschrieben in
`test_retention_k_anonymization.py::TestKAnonymity::test_model_default_is_hard_delete`.

**Redaktions-Parität beider Retention-Pfade** (#1750):
Die K-Anon-Bridge leistet **dieselbe** Tilgung wie der Hard-Pfad `anonymize_client` — und
das ist seit dem Befund nicht mehr behauptet, sondern **test-gepinnt**. Ein ableitungsbasierter
Architektur-Guard (`TestKAnonBridgeRedactionParity` in
[`test_architecture_guards_services.py`](../src/tests/test_architecture_guards_services.py))
liest die Redaktions-Helfer per AST aus `anonymize_client` und verlangt, dass die Bridge jeden
davon aufruft; ein neuer Helfer im Hard-Pfad reißt den Test, bis er in der Bridge landet oder
begründet ausgenommen wird. Genau diese Drift hatte den Befund erzeugt: die Bridge rief 2 von
9 Helfern; damals behielt ein k-anonymisierter Klient Klartext in `EventHistory`,
`DeletionRequest.reason` und `Activity.summary` (die Spalte wurde mit #1972 entfernt). Zwei Ausnahmen sind bewusst hinterlegt (`_redact_live_events` — die
Eintrittsbedingung garantiert `active_events == 0`; `_redact_client_identity` — würde
`age_cluster` löschen und damit den Quasi-Identifikator zerstören, auf dem die k-Anonymität
beruht). Aufbewahrungssperren (Legal Hold — nicht zu verwechseln mit dem Art.-18-Sperrvermerk,
siehe § Restidentifizierbarkeit der Tombstone-Timeline) blockieren (#1756)
**beide** Bulk-Einstiege; Begründung und Bucket-Entscheid in
[ADR-023 Update 2026-08-03](adr/023-k-anonymization-statistik.md).

### Reichweite des Legal Hold: bewusst kein Zieltyp „Übergabenotiz" (#2042)

Der Legal Hold kennt die Zieltypen `Client`, `Case`, `Event` und `Activity` (Anhänge folgen ihrem Owner, `owner_has_active_hold`) — die manuelle **Schichtnotiz** (`ShiftHandoverNote`, Refs #2042) ist endgültig **kein** Hold-Zieltyp, und `enforce_handover_notes` fragt deshalb keinen Hold ab. Begründung: Der Hold sichert die Akte, auf die sich ein laufendes Verfahren bezieht; die Schichtnotiz trägt keinen Personen- oder Fallbezug im Datenmodell (kein Client-/Case-Fremdschlüssel) und ließe sich einem Verfahren nur über ihren Freitext zuordnen — genau die Zuordnung, die aus Datenminimierungsgründen nicht hergestellt wird. Das **benannte Restrisiko**: eine Notiz, die für ein Verfahren relevant gewesen wäre, wird nach der einstellbaren Frist `retention_handover_notes_days` (Vorgabe 90 Tage) endgültig gelöscht, ohne dass ein Hold sie hält. Der verbindliche organisatorische Handgriff lautet deshalb: den verfahrensrelevanten Inhalt **innerhalb dieser Frist als Aktenvermerk am zugehörigen Fall dokumentieren**; die Übergabenotiz selbst bleibt eine bewusst kurzlebige Dienstmitteilung und wird nicht nachträglich zum Hold-Zieltyp erweitert.

### Hold-Matrix: welcher Pfad welchen Ziel-Typ wie behandelt (#2234)

Der Legal Hold koppelt an neun Stellen im Code an — jede davon behandelt die drei Zieltypen `Event`, `Client` (Personen-Hold) und `Case` (Fall-Hold) unterschiedlich (abweisen, zurückstellen, vorfiltern oder gegenstandslos). Die folgende Tabelle listet für jede Zelle den tatsächlichen Nachweis im Testbaum — jede Angabe ist gegen den heutigen Code und Testbaum belegt (Datei::Klasse::Test).

| Pfad ↓ / Ziel-Typ → | Ereignis-Hold | Personen-Hold (Client) | Fall-Hold (Case) |
|---|---|---|---|
| **Manuell löschen** (`soft_delete_event`) | abgewiesen · [`test_eventhistory_retention.py`](../src/tests/test_eventhistory_retention.py)`::TestLegalHoldCoupling::test_manueller_loeschpfad_blockt_unter_aktivem_hold` | abgewiesen · [`test_hold_matrix_gaps.py`](../src/tests/test_hold_matrix_gaps.py)`::TestL1DirectDeleteUnderClientHold::test_direct_soft_delete_refuses_under_client_hold` | abgewiesen · [`test_event_soft_delete_fields.py`](../src/tests/test_event_soft_delete_fields.py)`::TestCaseHoldBlocksTheManualDeletePath::test_event_of_a_held_case_keeps_payload_audit_and_file`; nachträglich einem gehaltenen Fall zugeordnete Instanz: `…::test_a_case_assigned_after_the_instance_was_read_still_blocks`; Gegenproben `test_hold_on_another_case_leaves_the_delete_path_open`, `test_event_without_a_case_is_unaffected`, `test_a_case_assigned_after_the_instance_was_read_does_not_block_without_a_hold` |
| **Vier-Augen** (`approve_deletion`) | abgewiesen · [`test_event_deletion_hold.py`](../src/tests/test_event_deletion_hold.py)`::test_event_hold_stops_the_approved_deletion` | abgewiesen · `test_eventhistory_retention.py::TestApprovedDeletionTakesClientLock::test_genehmigter_loeschweg_respektiert_den_personen_hold` | abgewiesen · `test_event_deletion_hold.py::test_case_hold_stops_the_approved_deletion` (Antrag bleibt PENDING); Zuordnung zwischen Laden und Sperre: `…::test_a_case_assigned_between_load_and_delete_still_stops_the_approval`; Gegenprobe `test_approval_without_a_hold_still_deletes` |
| **Strichlisten-Undo** (`undo_tally_event`) | abgewiesen · [`test_tally_undo.py`](../src/tests/test_tally_undo.py)`::TestTallyUndoAndLegalHolds::test_undo_of_an_event_under_legal_hold_is_refused` | gegenstandslos — Strichlisten-Ereignisse sind anonym (`client_id is None`); die Fähigkeitsprüfung verweigert schon vorher | gegenstandslos — dieselbe Prüfung schlägt bei `case_id is not None` fehl · `test_tally_undo.py::test_a_tally_event_with_a_case_loses_its_undo_capability_before_the_hold_gate` |
| **Sweep** (`lock_and_partition_events` / `enforce_eventhistory_retention`) | zurückgestellt · `test_eventhistory_retention.py::TestLegalHoldCoupling::test_hold_zuerst_stellt_das_ereignis_im_lauf_zurueck` | zurückgestellt · `TestClientHoldsAreHard::test_client_hold_stellt_die_historie_zurueck` | zurückgestellt · `TestCaseHoldsAreHardToo::test_ereignis_eines_gehaltenen_falls_wird_zurueckgestellt`; Revalidierung nach der Sperre: `…::test_zwischenzeitlich_in_einen_gehaltenen_fall_verschobenes_ereignis_wird_gehalten`; Gegenprobe `test_ereignis_eines_ungehaltenen_falls_wird_weiterhin_redigiert` |
| **Migration 0174** (`redact_deleted_event_history_backlog`) | zurückgestellt · `TestBacklogMigrationHonoursHolds::test_ereignis_unter_event_hold_wird_zurueckgestellt` | zurückgestellt · `…::test_ereignis_unter_client_hold_wird_zurueckgestellt` | zurückgestellt · `…::test_ereignis_unter_case_hold_wird_zurueckgestellt` — ohne eine Zeile Migrationscode (gemeinsamer Baustein, [ADR-044 §3](adr/044-fall-loeschpfad.md)) |
| **Merge** (`_validate_merge`) | abgewiesen (über `client_has_active_hold` → `events__pk__in`) · `test_hold_matrix_gaps.py::TestL2MergeUnderEventOrCaseHold::test_merge_refuses_when_an_event_of_the_source_is_under_hold` | abgewiesen · [`test_client_merge_workflow.py`](../src/tests/test_client_merge_workflow.py)`::TestMergeValidation::test_refuses_participant_under_legal_hold` | abgewiesen (über `cases__pk__in`) · `test_hold_matrix_gaps.py::TestL2MergeUnderEventOrCaseHold::test_merge_refuses_when_a_case_of_the_source_is_under_hold` |
| **Unmerge** (`unmerge_client`) | abgewiesen · `test_client_merge_workflow.py::TestUnmergeUnderLegalHold::test_hold_on_a_returning_event_is_refused` | abgewiesen (Ziel) · `…::test_hold_on_the_target_keeps_the_documentation_covered`; (Quelle, Bypass-Fall) · `…::test_hold_on_the_source_tombstone_is_refused_as_well`; HTTP · [`test_client_merge_views.py`](../src/tests/test_client_merge_views.py)`::TestTrashAndUnmerge::test_unmerge_post_under_legal_hold_shows_the_error_instead_of_undoing` | abgewiesen · `test_client_merge_workflow.py::TestUnmergeUnderLegalHold::test_hold_on_a_returning_case_is_refused`; Deckungs-Nachweis ohne Hold: `…::test_case_binding_survives_an_unmerge_without_holds` |
| **Cron — Vorfilter** (`collect_doomed_events`) | ausgefiltert (über `client_has_active_hold`) · kein dedizierter Test | ausgefiltert · kein dedizierter Test | ausgefiltert · [`test_retention_strategies_unit.py`](../src/tests/test_retention_strategies_unit.py)`::TestFallHoldsImVorfilter::test_collect_doomed_events_laesst_das_ereignis_des_gehaltenen_falls_aus`; `…::test_enforce_identified_zaehlt_das_ereignis_des_gehaltenen_falls_nicht` (`dry_run`/`count == 0`) |
| **Cron — Recheck unter der Sperre** (`_lock_event_and_recheck_hold`; ein Hold, der NACH dem Sammeln entsteht, stellt das Ereignis zurück) | zurückgestellt · [`test_retention_redaction.py`](../src/tests/test_retention_redaction.py)`::test_event_hold_after_collection_defers_the_event` | zurückgestellt · `…::test_client_hold_after_collection_defers_the_event` | zurückgestellt · `test_hold_matrix_gaps.py::TestL3CaseHoldInterleavesWithTheRecheck::test_case_hold_created_after_collection_defers_the_event` |

**Offene Zellen — begründet:** Der Vorfilter (`collect_doomed_events`) schließt gehaltene Ereignisse und Personen zwar mit aus (derselbe Codepfad wie der Fall-Zweig), hat für diese beiden Zweige aber keinen dedizierten Test — der **verbindliche Halt** ist ohnehin der Recheck unter der Zeilensperre, der Vorfilter reduziert nur unnötiges „zurückgestellt"-Rauschen im Lauf-Protokoll. **Aktivitäts-Holds** (`target_type="Activity"`) stehen bewusst nicht in dieser Matrix: sie betreffen Aktivitäten, nicht Ereignisse/Personen/Fälle, und liegen mit #2152 in einer eigenen Testfamilie (`test_retention_hold_toctou_rest.py`).

### Disclosure-Analyse der Berichts-Artefakte (2026-08-03, Issue #1742)

**Status:** Analyse **abgeschlossen und umgesetzt** — **K5**
(#1767), **K0 (a, Fail-safe)**
(#1768), **K6**
(#1769) sowie **K0 (b, Zellkriterium)**,
**K4b**, **K2**, **K1**, **K3 (A1/A3)** und **K4c**
(#1770) sind **mitigiert**; **K4a** bleibt die
akzeptierte Definitionsgrenze, flankiert durch die Freigabe-Übersicht, und **K3-A2** ist seit dem
Maintainer-Entscheid vom 2026-08-04 **entschieden: Option A, Status quo ohne Restzeile**
(#1773; Spalte „Status" in der Übersicht). Sieben Kanäle untersucht, alle
**bestätigt**; drei sind Implementierungsfehler mit kleinem Aufwand, drei Teilaspekte sind als
k-Anonymitäts-**Definitionsgrenze** akzeptiert. Anlass war die M5-Erweiterung der Suppression auf die
Berichts-Exporte ([ADR-038 § 5](adr/038-reporting-plugin-backends.md)); die dort geschlossenen Kanäle
(Bezugsgrößen in Vollständigkeitsmeldungen, Vier-Term-Identität) sind verifiziert geschlossen, die
hier gelisteten lagen zum Analysezeitpunkt außerhalb dieses Scopes und bestanden größtenteils seit
#1278.

#### Scope, Empfängermodell, Methodik

Betrachtet wird die **Berichts-Artefakt-Familie**: Halbjahres-Sachbericht-PDF (Standardmodus),
Beispiel-Sachbericht-PDF, externer Bericht (HTML **und** `?format=json`), Berichts-Export
(CSV/JSON/XML), On-Screen-Berichts-Panel. **Nicht** im Scope: internes Statistik-Dashboard und
Trend-JSON (bewusst roh, #1311) sowie der
`?internal=1`-Modus des Sachbericht-PDF (Security-Review R4).

Kein Befund setzt privilegierten Zugriff voraus (Kerckhoffs): der Empfänger kennt **k** (steht im
Artefakt bzw. in der öffentlichen Doku), den Algorithmus (Open Source), **welche** Zellen unterdrückt
sind (Label + Marker bleiben sichtbar) und die **Zeilenreihenfolge**.

Methodik: Preimage-Enumeration über Kleinst-Welten — die Suppressionslogik wurde gegen den
Produktionscode auf Verhaltensgleichheit geprüft, dann für eine beobachtete Publikation *alle*
konsistenten Rohwelten aufgezählt; einwertiges Preimage = Kleinstfallzahl exakt rekonstruiert. Nur
Beobachtungen mit publizierter Randsumme **innerhalb** der Enumerationsgrenze sind auswertbar, sonst
erzeugt die Grenze selbst Schein-Pins. Alle Prozentangaben sind modellabhängig, Größenordnung statt
Betriebskennzahl.

#### Übersicht

| # | Kanal | Empfehlung | Aufwand | Priorität | Status |
|---|---|---|---|---|---|
| **K0** | Schwelle auf Ereignis- statt Personenzahlen | **schließen** | S (Fail-safe) / M (Zellkriterium) | 1 | **mitigiert** — (a) (#1768), (b) (#1770) |
| **K5** | Zeilensortierung nach Anzahl als Rangkanal | **schließen** | S | 1 | **mitigiert** (#1767) |
| **K6** | Unterdrückungs-*Entscheidung* der Vollständigkeitsmeldungen | **schließen** | S | 2 | **mitigiert** (#1769) |
| **K2** | Auslösekriterium der Sekundär-Suppression (`len(suppressed) != 1`) | **schließen** | M | 2 | **mitigiert** (#1770, Variante „Spanne ≥ 2") |
| **K4b** | `unique_clients`-Inflation über Monatssegmente | **schließen** | S–M | 2 | **mitigiert** (#1770) |
| **K1** | Struktur-Kollaps / Residuum bei publizierter Randsumme | teilweise; Rest akzeptieren | M+ | 3 | **mitigiert** (#1770) — Fixpunkt-Guard inkl. der Personen-Randsumme Σ `persons` = `unique_clients` |
| **K3** | zwei Grundgesamtheiten nebeneinander | teilweise; Ungleichungen akzeptieren | M | 3 | **mitigiert** — A1 + A3 (#1770); A2 = entschieden, Option A / Status quo (#1773) |
| **K4c** | Lesequellen-Divergenz (Snapshot vs. live) | teilweise (Herkunft stempeln) | S | 4 | **mitigiert** (#1770) |
| **K4a** | Zeitraum-Differenz / Partitions-Rückrechnung | **akzeptieren + dokumentieren** | — | 4 | akzeptiert; Freigabe-Übersicht (#1770) |

Alle Verdikte: **bestätigt** (per Enumeration belegt, nicht vermutet). **Reihenfolge:** K5 → K0
(Fail-safe) → K6 → K2 → K1-Guard → K3 → K4b. K5 zuerst, weil es die Messbasis aller übrigen Kanäle
senkt und keine Utility kostet.

> **Kopplungs-Warnung — die wichtigste Aussage dieser Analyse:** **Machbarkeits-Audit (K2)** und
> **Residual-Guard (K1)** wirken **nur paarweise mit der Sortier-Korrektur (K5)**. Der Guard allein
> ist zirkulär validiert: er entscheidet über ein Preimage-Modell der *ungeguardeten* Sicht, sein
> Feuern ist selbst informativ. Ground-Truth über beweisbar vollständige Preimages: 24 von 853
> Sichten behalten mit Guard **allein** einen exakten Pin (Gegenbeispiel: Randsumme 11, drei Zeilen
> alle unterdrückt ⇒ Preimage {(1,1,9),(2,1,8)}, dritte Zeile in beiden = 1). Erst **K5 + Guard**
> ergaben 0 Pins über 516 beweisbar vollständige Sichten. Wer den Guard ohne K5 ausliefert, hat die
> Kosten bezahlt und den Kanal nicht geschlossen.

#### K0 — Die Schwelle wird auf Ereigniszahlen angewandt, nicht auf Personenzahlen

Sämtliche suppressionsbewehrten Zellen sind `Count("id")` über **Ereignisse**; `_suppress_small`
vergleicht die Schwelle also gegen eine Ereigniszahl. Die Äquivalenzklasse, die k-Anonymität schützen
soll (Einrichtung × Zeitraum × Altersgruppe), darf damit Größe 1 haben und wird trotzdem publiziert —
**ohne** jeden Rückrechnungsschritt. Selbstbeweisend wird der Fall, sobald `unique_clients`
unterdrückt ist: dann steht im Artefakt, dass die Gesamtpersonenmenge < k ist, während die
Altersverteilung genau dieser Menge im Klartext danebensteht.

*Betroffen:* alle Artefakte. *Beleg:* Brute-Force über 2 000 718 Welten — 2,6 Mio. publizierte
Alterszellen mit Personen-Bucket < k, die Mehrzahl davon sogar bei **sichtbarem** `unique_clients` ≥ k.
Exakte Pinnung ist konstruierbar: 4 Personen à 5 Kontakte in 4 Clustern ⇒ vier sichtbare Alterszellen
bei unterdrücktem `unique_clients` ⇒ jede Zelle genau 1 Person.

*Empfehlung — schließen* (keine Restunschärfe des Modells, sondern das Verfahren auf der falschen
Größe): **(a, S, sofort)** ist `unique_clients` < k, die **gesamte** personenabgeleitete
Aufschlüsselung unterdrücken, nicht nur die Kennzahl — wenige Zeilen in den drei Wrappern.
**(b, M)** Zellkriterium für personenbezogene Zellen auf die DISTINCT-Personenzahl des Buckets
umstellen. **Falle:** `count_clients_in_bucket` ist dafür **nicht** verwendbar (kein Zeitraumfilter,
zählt alle Clients der Einrichtung — ein direkter Aufruf wäre *fail-open*); es braucht eine
zeitraum-skopierte Variante, und die Snapshot-Payload muss `persons` je Cluster mitführen
(Formatänderung, kollidiert mit #1741).
**Nebenwirkung:** nach (b) gilt „unterdrückt ⇒ Wert ≤ k−1" nicht mehr — das entschärft K1, bricht
aber die Annahme, auf der der Guard aus K1/K2 rechnet. Beide Maßnahmen gemeinsam modellieren.

*Status (a): **mitigiert***
(#1768). `_drop_person_derived_breakdowns`
in [`external_report.py`](../src/core/services/dashboard/external_report.py) ist ein einzelner,
benannter Guard mit **einer** Bedingung (`unique_clients < k`), den alle drei Wrapper aufrufen; er
entfernt dann `by_age_cluster` **und** `by_contact_stage` vollständig aus dem Artefakt — Key weg, nicht
nur Werte auf `None`. Eine bloß genullte Alterszeile wäre selbst noch die Aussage „in dieser
Altersgruppe gibt es mindestens ein Ereignis". Ereignisabgeleitete Aggregate (`total_contacts`/`total`,
`by_document_type`, `by_category`) bleiben unberührt. Die Templates weisen die Lücke aus, statt sie als
„keine Daten" auszugeben (Nichtirreführung). **Bewusst grob:** der Guard greift auch, wenn nur
`by_contact_stage.anonym` betroffen wäre — die feinere Lösung ist (b), die Umstellung des
Zellkriteriums auf die DISTINCT-Personenzahl je Bucket; diese Stelle ist dafür der vorgesehene
Ersatz-/Ergänzungspunkt. Test-gepinnt in
`test_external_report_service.py::TestPersonenabgeleiteteTabellenFailSafe` und
`test_external_report_view.py::TestPersonenTabellenImArtefakt` (JSON + HTML).

*Status (b): **mitigiert*** (#1770). Das
Zellkriterium prüft personenabgeleitete Zellen gegen die **DISTINCT-Personenzahl ihres Buckets im
Berichtszeitraum**: `get_statistics` liefert `by_age_cluster[].persons` und
`persons_by_contact_stage`, `_suppress_small`/`_suppress_stage_dict` unterdrücken, sobald die
**strengere** von Ereignis- und Personenzahl unter *k* liegt (min-Logik). Die Ereignisprüfung bleibt
bestehen — sie ist in Sonderfällen die schärfere Bedingung. Vier Eigenschaften tragen das Ergebnis:

* **Zeitraum-skopiert und fail-safe.** `count_clients_in_bucket` bleibt wie angekündigt unbenutzt (kein
  Zeitraumfilter ⇒ fail-open); es behält seinen Verwender im Retention-Pfad und trägt jetzt eine
  Warnung. Der Berichtspfad zählt ausschließlich über die Ereignis-Grundgesamtheit des Zeitraums.
* **Die Personenzahl verlässt die Suppression nie.** `_suppress_small` entfernt `persons` aus **jeder**
  Ausgabezeile — sichtbar wie unterdrückt. Bliebe sie stehen, wäre eine Zelle „27+: 30 Ereignisse,
  6 Personen" selbst eine publizierte Kleinstfallzahl, die nie durch eine Regel gelaufen ist.
* **Unbekannt ⇒ unterdrückt.** Snapshots ohne `persons` und Monate, deren historische Personenzahl
  nach Ablauf der Löschfristen nicht mehr rekonstruierbar ist, tragen `None`. `require_person_counts`
  normalisiert jede `by_age_cluster`-Liste auf diesen Zustand, damit keine Zeile still auf das alte,
  ereignisbasierte Kriterium zurückfällt; die Backfill-Migration `0128` rät nicht, sondern markiert.
  Im Merge über Monatssegmente wird die Personenzahl **maximiert, nie summiert** (das Maximum ist eine
  gültige Untergrenze der wahren DISTINCT-Zahl; eine Summe wäre K4b in klein).
* **Der Tabellen-Drop aus (a) bleibt.** Er ist keine Redundanz: das Zellkriterium nullt nur Werte, und
  eine genullte Alterszeile ist selbst noch die Aussage „in dieser Altersgruppe gibt es mindestens ein
  Ereignis". Nur `_drop_person_derived_breakdowns` entfernt den Key.

Test-gepinnt in `test_external_report_service.py::TestPersonenbasiertesZellkriterium` und
`::TestPersonenkriteriumInDenWrappern`, `test_statistics.py::TestPersonenzaehlungImZeitraum`,
`test_statistics_hybrid.py::TestMergeSummiertPersonenzahlenNie` sowie
`src/tests/migrations/test_0128_backfill_snapshot_person_counts.py`.

**Damit gilt:** die Zusicherung, Merkmalskombinationen mit weniger als *k* **Personen**
würden unterdrückt, gilt für die personenabgeleiteten Aufschlüsselungen — auf Tabellen-
**und** Zellebene.

**Restrisiko, ehrlich ausgewiesen — und eine neue Identität.** Fallen durch (b) *alle* Zellen einer
Alterstabelle, während `unique_clients` ≥ *k* bleibt, publiziert das Artefakt weiterhin die
**Existenz** der Zeilen (jedes gelistete Label trägt ≥ 1 Ereignis). Das allein wäre K1/K2-Terrain
(Residual-Guard bzw. Machbarkeits-Audit). Gravierender ist jedoch eine Identität, die **erst durch (b)
entsteht**: `by_age_cluster` partitioniert dieselbe Personenmenge, die `unique_clients` zählt, also gilt

> Σ `persons`(`by_age_cluster`) = `unique_clients`

— eine publizierte Randsumme über **Personen**, mit derselben Rückrechenbarkeit wie K1 über
Ereignisse, nur ohne die Unschärfe der Ereignis-/Personen-Ungleichung. Da eine personenbedingt
unterdrückte Zelle zusätzlich `persons` ≤ *k*−1 verrät und jede gelistete Zeile `persons` ≥ 1 trägt,
kollabiert die Partition an den Rändern auf eine Welt. *Konstruktion bei k = 5:* `unique_clients` = 5
sichtbar, vier gelistete Altersgruppen, sämtlich unterdrückt ⇒ die einzige mit
1 ≤ `persons` ≤ 4 konsistente Partition von 5 auf vier Zeilen ist (2, 1, 1, 1) ⇒ **drei Altersgruppen
mit exakt einer Person** sind zeilenscharf bestimmt.

**Verbindliche Auflage — GESCHLOSSEN** (#1770,
in derselben Umsetzungswelle wie K1/K2):

1. **total-Doktrin auf die Personen-Randsumme ausgedehnt:** `unique_clients` **fällt**, sobald
   mindestens eine `by_age_cluster`-Zeile **personenbedingt** unterdrückt ist — analog zur
   bestehenden Randsummen-Regel R14 (`total < k` ⇒ Randsumme selbst unterdrücken) und zur
   Sekundär-Suppression A6.1. Ohne die publizierte Randsumme ist die Partition nicht mehr
   auflösbar. Umgesetzt als `drop_person_margin_on_person_suppression` in
   [`external_report.py`](../src/core/services/dashboard/external_report.py), aufgerufen von allen
   Wrappern; test-gepinnt in
   `test_external_report_service.py::TestPersonenkriteriumInDenWrappern::test_externer_bericht_unterdrueckt_zelle_einer_einzelnen_person`.
   Die Regel ist bewusst **schärfer** als der Fixpunkt-Guard: dessen Kriterium ist der exakte Pin,
   während hier schon die *Einengung* auf ein k-schmales Intervall genügt — in der Konstruktion oben
   hat jede Zelle noch zwei mögliche Werte (1 oder 2), und trotzdem steht fest, dass **jede**
   Altersgruppe weniger als *k* Personen umfasst. Der Guard ist die zweite Linie, nicht der Ersatz.
2. **K1-Fixpunkt-Guard erweitert:** Die Identität Σ `persons` = `unique_clients` ist Teil seines
   Inferenzmodells — `apply_residual_guard` führt sie als eigene Partition (`by_age_cluster:persons`)
   mit, deren Zellen nie publiziert werden und deren einzige Eskalation deshalb das Fallenlassen von
   `unique_clients` ist. Die a-priori-Schranken folgen aus der Zeilen-Existenz (jede gelistete
   Altersgruppe trägt ≥ 1 Person) und dem Unterdrückungsgrund (personenbedingt ⇒ ≤ *k*−1, sonst ≥ *k*).
3. **Restkanal Zeilen-Existenz** (bei der Übergabe ausgewiesen): dass ein Artefakt auch bei
   unterdrückten Zellen publiziert, **welche** Altersgruppen-Labels vorkommen, ist als Untergrenze
   `lo = 1` in beiden Inferenzstufen modelliert — bei Aufschlüsselungen mit **festen** Schlüsseln
   (`by_contact_stage`) dagegen `lo = 0`, denn dort wird auch eine Nullzeile emittiert und die bloße
   Existenz sagt nichts.

#### K5 — Zeilensortierung nach Anzahl als Rangkanal

`by_age_cluster` und `by_document_type` werden absteigend nach Anzahl erzeugt (`order_by("-count")`)
und nach dem Snapshot-Merge erneut so sortiert. `_suppress_small` setzt nur
`count=None`/`suppressed=True` und **verändert die Reihenfolge nicht**; alle Templates iterieren
unverändert (kein `dictsort`). Der Marker ist uniform, die **Position** nicht — die Zeilenreihenfolge
publiziert die Rangordnung der unterdrückten Zellen.

*Betroffen:* beide PDFs, externer Bericht (HTML + JSON), Berichts-Export **JSON und XML**. *Nicht
betroffen:* CSV (nur `records`), `by_category` (feste Kategorien-Ordnung), `by_contact_stage` (Dict mit
festen Schlüsseln). *Beleg:* über 28 560 Zell-Welten mit **0** Verletzungen gilt — sobald überhaupt
unterdrückt wird, ist die **letzte** Tabellenzeile unterdrückt, und die zuletzt gelistete unterdrückte
Zeile trägt **immer** einen Wert < k. Ohne jede Arithmetik veröffentlicht das Artefakt damit,
**welche** Altersgruppe bzw. **welcher** Dokumenttyp die Kleinstfallzahl trägt; zusätzlich sinkt die
Zuordnung von r unterdrückten Werten zu Labels von r! auf 1. Über 292 820 Welten (identische
Suppression, Unterschied nur in der Publikationsreihenfolge): Klassen mit ≥ 1 exakt gepinnter Zelle
**7 149 (Rang) vs. 152 (kanonisch)** — Faktor 47.

*Empfehlung — schließen (bestes Kosten-Nutzen-Verhältnis der Analyse, S):* nach `_suppress_small` eine
**wertunabhängige** Ausgabereihenfolge herstellen — unterdrückte Zeilen als Block in kanonischer
Ordnung ans Listenende (Altersgruppen in Modellreihenfolge, Dokumenttypen nach `(category, name)`);
sichtbare Zeilen dürfen count-sortiert bleiben, ihre Werte stehen ohnehin im Klartext. **Ein**
Eingriffspunkt, wirkt automatisch auf alle Templates plus JSON/XML; muss **nach** der Suppression
greifen, damit der Snapshot-/Hybrid-Pfad nicht driftet. Gemessen ohne neuen Kanal (numerisch identisch
zur Basislinie „alles in fester Labelordnung"); Bestandstests bleiben grün; der interne/On-Screen-
Rohpfad bleibt unberührt (dort ist das Ranking Nutzwert). **Keine Definitionsgrenze:** die Rangordnung
ist kein publizierter Aggregatwert, sondern ein darstellerisches Nebenprodukt der Query-Sortierung —
und sie untergräbt gezielt die Sekundärsuppression, deren einziger Zweck es ist, zwei
ununterscheidbare Unbekannte zu hinterlassen. **Restrisiko ehrlich ausweisen:** K5 senkt gepinnte
Zellen um Faktor ~47, eliminiert sie aber nicht — die Restfälle brauchen den Guard.

*Status: **mitigiert*** (#1767).
`_canonicalize_suppressed_order` in [`external_report.py`](../src/core/services/dashboard/external_report.py)
läuft als **einziger** Eingriffspunkt am Ende von `_suppress_small` — nach der Sekundär-Suppression,
damit der Snapshot-/Hybrid-Pfad nicht driftet. Sichtbare Zeilen behalten ihre count-sortierte
Reihenfolge, unterdrückte folgen als Block in kanonischer Ordnung (Altersgruppen aus
`Client.AgeCluster` abgeleitet, Dokumenttypen nach `name` — der frühere Erstschlüssel `category` ist
seit [ADR-040](adr/040-documenttype-category-entfernt.md) entfallen); Templates, JSON und XML erben
das ohne eigene Anpassung. Die Ordnung benennt seit
#1770 die **Aufrufstelle** über das
Pflicht-Keyword `order` —
`by_category` fällt (#1770) unter
`ORDER_CATEGORY` (Namensordnung): die Einfügereihenfolge der Konsolidierung ist **datenabhängig** —
eine Kategorie steht dort, wo ihr alphabetisch erster im Zeitraum vorhandener `report_key` steht (Ordnungsquelle ist der `report_key`, nicht der
`system_type`; #1849). Ohne kanonische Ordnung verriet die Zeilenposition, welcher Berichtsschlüssel hinter
einer unterdrückten Kleinstzelle steckt (zwei Welten mit identischen publizierten Werten,
„Vermittlung" aus `accompaniment` bzw. aus `referral`, ergaben verschiedene Positionen). `ORDER_INPUT`
gilt danach nur noch für Tabellen mit **fester Schlüsselordnung**: `by_contact_stage` (feste
Stufenfolge aus dem Modell) und die `records`-/`records_<feld>`-Tabellen der Beispiel-Backends
(Eingabeordnung des jeweiligen Backends, #1754).
Test-gepinnt in
`test_external_report_service.py::TestKanonischeOrdnungUnterdrueckterZeilen` (inkl. des geforderten
Ordnungstests „zwei Welten mit vertauschten Zellwerten erzeugen dieselbe Ausgabe") und
`::TestKanonischeOrdnungInDenWrappern`. K5 senkt für sich genommen nur die Pin-Rate; die Restfälle
deckt der Fixpunkt-Guard (K1, #1770
mitigiert).

*Nachtrag (#1772, Punkt 1): sichtbare
Kategoriezeilen ebenfalls kanonisch.* Die Freistellung „sichtbare Zeilen dürfen count-sortiert
bleiben" trägt nur, solange die sichtbare Ordnung aus den publizierten Werten **folgt**. Für
`by_category` tut sie das nicht: dort ordnet die Konsolidierung datenabhängig, und die Position einer
**sichtbaren** „Vermittlung"-Zeile verriet weiterhin, ob sie aus `accompaniment` oder aus `referral`
stammt — bei identischen publizierten Werten. Der Kanal ist klein (er betrifft nur die drei
Kategorien mit mehreren Quell-`report_key`s — Beratung, Versorgung, Vermittlung — und sagt nichts über Fallzahlen), aber er ist derselbe
Kanaltyp, den K5 schließt, und kostenlos zu schließen. `_ORDERS_CANONICAL_FOR_VISIBLE` in
[`external_report.py`](../src/core/services/dashboard/external_report.py) benennt die Ordnungen, deren
sichtbare Zeilen mitsortiert werden — heute genau `ORDER_CATEGORY`. `ORDER_DOCUMENT_TYPE` und
`ORDER_AGE_CLUSTER` bleiben bewusst draußen: deren `-count`-Ordnung ist aus den Werten ablesbar, und
die Tabellen werden als Rangliste gelesen. Test-gepinnt in
`test_external_report_service.py::TestKanonischeOrdnungUnterdrueckterZeilen::test_by_category_sichtbare_zeilen_sind_ebenfalls_kanonisch_geordnet`
(plus Gegenprobe an `by_document_type`) und Ende-zu-Ende in
`test_reporting_sample_report_1.py::TestZeilenpositionIstDatenunabhaengig`.

#### K6 — Die Unterdrückungs-*Entscheidung* der Vollständigkeitsmeldungen

M5 hat die **Zahlen** aus den Vollständigkeitsmeldungen entfernt, wenn sie klein sind — nicht aber die
Tatsache, **dass** entfernt wurde. `_prepare_issue` entscheidet über eine Disjunktion auf **Rohdaten**
(`template is None or count < k or complement < k`); beide Zweige sind invertierbar, weil die
Komplemente an publizierte Größen gekoppelt sind. Tragend ist dabei nicht die Disjunktion, sondern:
eine **bezifferte** Meldung publiziert implizit „mein Komplement ≥ k", also eine untere Schranke —
selbst wenn *jede* Alterszelle unterdrückt ist. Der Modellfehler sitzt in `_known_terms`: verborgene
Terme gelten dort als *unbestimmt*, obwohl sie auf ein k-breites Intervall eingegrenzt sind; ein
Term-**Zähler** kann das prinzipiell nicht abbilden.

*Betroffen:* Berichts-Export **JSON** und **XML**, On-Screen-**Panel**. *Nicht betroffen:* CSV (lässt
die Meldungen weg) und die **PDFs** (rendern keinen Vollständigkeits-Block) — die tragen weiterhin
K0–K5. *Beleg:* gegen den Originalcode reproduziert; 1 653 stabile Beobachtungsklassen, in denen die
Voll-Inversion einen verborgenen Term exakt pinnt und ein Referenz-Angreifer ohne diesen Kanal in
1 465 Fällen offen bleibt.

*Empfehlung — schließen (S):* den Vollständigkeits-Block **vollständig datenunabhängig** machen (beide
Meldungen **immer**, nie mit Zahl). Gemessen im selben Weltraum sinken die Klassen mit gepinntem
verborgenem Term von 13 082 (Status quo) über 5 931 (nur die Zahl weglassen) auf **804** — die
Restpins sind dann Struktureffekte aus K1–K3, nicht mehr der Meldungskanal. Damit entfallen
`_prepare_issue`-Prädikat, `complements`, `_known_terms`, `_term_status`, `_hide_term` und
`_close_identity_leak` **ersatzlos**. Kosten: der Bericht meldet die Lücke auch dann unbeziffert, wenn
es keine gibt — genau das ist für `_hide_term` bereits als gewollt dokumentiert. **Keine
Definitionsgrenze:** der Kanal entsteht nicht aus der unvermeidbaren Arithmetik publizierter
Randsummen, sondern aus einer selbst eingeführten, **datenabhängigen Verzweigung** im Suppressionscode.

*Status: **mitigiert*** (#1769).
`SampleReport1Backend.prepare_export` baut den Vollständigkeits-Block aus der festen Codeliste
`_EXPORT_ISSUE_CODES` statt aus `result.completeness`: **beide** Meldungen immer, **nie** mit Zahl, in
fester Reihenfolge — weder Gestalt noch Inhalt des Blocks hängen noch an einem Datum.
`_prepare_issue`, `complements`, `_known_terms`, `_term_status`, `_hide_term` und
`_close_identity_leak` sind samt der bezifferten Export-Vorlagen (`_EXPORT_MESSAGES`) und des
Fallback-Texts **ersatzlos gelöscht**, ihre msgids aus beiden Katalogen entfernt. Als Nebeneffekt
ist die Vier-Term-Identität ohne jede Nachbesserung geschlossen: beide Meldungs-Terme sind
grundsätzlich verborgen, es bleiben nie weniger als zwei Unbekannte. **Kosten (bewusst):** der Bericht
meldet die Lücke auch dann unbeziffert, wenn es keine gibt, und ein künftiger `validate_completeness`-Check
erreicht den Export erst, wenn sein Code in `_EXPORT_ISSUE_CODES` und `_SUPPRESSED_MESSAGES` aufgenommen
wird (fail-safe: lieber eine Meldung zu wenig als eine Kleinstfallzahl zu viel). Test-gepinnt in
`test_reporting_sample_report_1.py::TestVollstaendigkeitsblockIstDatenunabhaengig` (inkl. „zwei Welten mit
verschiedenen Lücken liefern denselben Block") und `test_reporting_views.py`
(`test_completeness_block_ist_datenunabhaengig`).

#### K2 — Auslösekriterium der Sekundär-Suppression

`_apply_secondary_suppression` ist an die **Zellenzahl** gekoppelt (`if len(suppressed) != 1: return`)
statt an die **Machbarkeitsspanne**. Bei ≥ 2 primär unterdrückten Zellen läuft die Regel damit **nie**
— obwohl gerade dann R = s (alle = 1) oder R = s·(k−1) die Zellen vollständig bestimmt; und läuft sie,
wählt sie mit `min(visible, key=count)` ausgerechnet die **kleinste** sichtbare Zelle, minimiert damit
das Residuum und verrät zusätzlich eine Obergrenze. `_suppress_stage_dict` trägt eine zweite Kopie
derselben Heuristik.

*Beleg:* der Nachbau wurde gegen die **echten** Funktionen über 28 560 bzw. 2 197 Welten verglichen
(0 Abweichungen). Isoliert auf die Fälle, in denen die Sekundärregel tatsächlich feuert: **27,5 %**
(264/960) mit exakt gepinnter Kleinstzelle — die Regel schützt in gut einem Viertel ihrer Einsätze gar
nicht.

*Empfehlung — schließen (M):* die Heuristik durch ein **Machbarkeits-Audit** ersetzen. Für eine
eindimensionale Tabelle mit exakt publizierter Randsumme ist die Spanne je unterdrückter Zelle
geschlossen berechenbar (keine NP-harte Optimierung); solange die kleinste Spanne zu eng ist, fällt
eine weitere sichtbare Zelle — die **größte** statt der kleinsten, weil das das Residuum und damit die
Mehrdeutigkeit maximiert (Gegenprobe: „kleinste fällt" hinterlässt 2,5 % Rest-Leak, „größte fällt"
0,2 %). `_suppress_stage_dict` auf dieselbe Funktion umstellen. **Kostenaussage korrigiert:** Das
Audit ist **nicht** billiger als der Status quo — über 130 320 Welten unterdrückt es in **keiner** Welt
weniger Zellen, in 13,5 % mehr, und die unterdrückte Fallmasse steigt von 15,4 % auf 27,8 %, weil
gezielt die größte Zelle geopfert wird. Das widerspricht direkt der Zusicherung des Bestandstests
`test_list_single_suppression_triggers_secondary` („größte bleibt sichtbar") und ist damit ein
**Produktentscheid**: die Spitzenkategorie verschwindet aus Träger-/Förderberichten. Günstigere
Variante mit denselben 0 Leaks im geprüften Raum: Audit-Schwelle „Spanne ≥ 2" statt „Spanne ≥ k−1".
**Restfall:** bei unterdrückter Randsumme (`total < k`, R14) argumentiert das Audit ins Leere; dafür
braucht es die eigene Regel „Tabellenkörper unterdrücken, wenn die Randsumme selbst unterdrückt ist" —
das ist K1, nicht K2.

*Status: **mitigiert*** (#1770,
**Produktentscheid: Variante „Spanne ≥ 2"**). `_audit_feasible_span` in
[`external_report.py`](../src/core/services/dashboard/external_report.py) misst die Machbarkeitsspanne
statt der Zellenzahl: solange eine unterdrückte Zelle **exakt** zurückrechenbar ist, fällt die
**kleinste** sichtbare Zelle, danach wird neu gerechnet — bis zum Fixpunkt. Die Spanne kommt aus
[`disclosure.py`](../src/core/services/dashboard/disclosure.py) (geschlossene Intervallformel, gegen
eine Brute-Force-Aufzählung nachgeprüft). Sechs Eigenschaften tragen das Ergebnis:

* **Schwelle „Spanne ≥ 2" statt „≥ k−1".** Die strengere Variante unterdrückt in 13,5 % der Welten
  mehr Zellen, ohne im geprüften Raum weniger Leaks zu hinterlassen. „Spanne ≥ 2" heißt genau: kein
  exakter Pin.
* **Es fällt die kleinste sichtbare Zelle** (User-Entscheid, Begründung und die daran gekoppelte
  Modellschranke im eigenen Unterabschnitt unten). Die Zusicherung „größte bleibt sichtbar" des
  Bestandstests `test_list_single_suppression_triggers_secondary` bleibt damit gültig.
* **Eine Funktion statt zweier Kopien.** `_suppress_stage_dict` trug eine zweite Fassung derselben
  Heuristik; sie ist ersatzlos entfallen und ruft dasselbe Audit. Der Architektur-Guard hält das offen
  nachprüfbar.
* **Die alte Guard-Annahme ist ersetzt.** Nach K0-b gilt „unterdrückt ⇒ Wert ≤ *k*−1" **nicht** mehr
  (eine Zelle kann personenbedingt bei beliebig großer Ereigniszahl fallen). Das Audit bildet die
  tatsächliche Semantik ab: Wert < *k* ⇒ [1, *k*−1]; personenbedingt gefallen ⇒ [*k*, ∞);
  Sekundär-Opfer ⇒ [*k*, kleinste verbliebene sichtbare Zelle] (Bedingung X, s. u.).
* **Randsumme ist ein Pflicht-Keyword.** Jede Aufrufstelle sagt, ob es eine publizierte Randsumme
  gibt, an der das Audit rechnen kann — der von der Analyse benannte **Restfall** (unterdrückte
  Randsumme ⇒ Audit läuft ins Leere) ist damit kein Sonderpfad, sondern der Normalfall
  `margin=None`. Dasselbe gilt für `by_age_cluster`: dort publiziert kein Artefakt eine
  Ereignis-Randsumme (siehe „Geprüft und nicht tragfähig"), das Audit läuft dort also nicht — der
  tragfähige Kanal dieser Tabelle ist die **Personen**-Randsumme (K0-b-Auflage 1).
* **Drei begründete Ausnahmen vom Befund-Kriterium** (in `disclosure.findings` dokumentiert): leeres
  Intervall (die Aufschlüsselung partitioniert ihre Randsumme nicht exakt — bei konsistenten Daten
  unmöglich), Pin auf 0 (ein leerer Bucket schützt niemanden) und Pin auf ≥ *k* **ohne**
  Personenkriterium (kein Kleinstwert, keine Personenlücke). Ohne die letzte Ausnahme würde der Guard
  ein Sekundär-Opfer mit 20 Ereignissen endlos nachunterdrücken.

Test-gepinnt in `test_external_report_service.py::TestSecondarySuppression`,
`test_disclosure_inference.py` und `test_reporting_disclosure_regression.py`.

##### Opferwahl: „kleinste sichtbare Zelle" — mit zwingender Kopplung (User-Entscheid)

Die Analyse empfahl, die **größte** sichtbare Zelle zu opfern, weil das das Residuum maximiert
(gemessen: „kleinste fällt" 2,5 %, „größte fällt" 0,2 % Rest-Leak). Diese Messung stammt aus einem
**einmalig** laufenden Audit. Die Umsetzung iteriert bis zum **Fixpunkt**, und dort sind beide
Opferregeln leakfrei — sie unterscheiden sich nur noch darin, welche und wie viele Zellen fallen.

**Entschieden: es fällt die kleinste sichtbare Zelle.** Grund ist die Utility: in einem typischen
Bericht (Randsumme 100, Zeilen 60/30/8/2) verschwände mit „größte fällt" die **60** — die
Spitzenkategorie aus jedem Träger-/Förderbericht, sobald überhaupt eine Kleinstzelle existiert —, mit
„kleinste fällt" nur die 8. Gemessen fällt die Spitzenkategorie damit **halb so oft**; der Preis ist
etwas mehr Kleinzellen-Suppression (es fallen tendenziell mehr, dafür kleine Zellen). Der
Bestandstest `test_list_single_suppression_triggers_secondary` („größte bleibt sichtbar") bleibt
damit unverändert gültig.

> **Bedingung X — nicht optional.** Die Opferregel und die a-priori-Schranke des Sekundär-Opfers im
> Guard-Modell sind **eine** Entscheidung. „Kleinste fällt" publiziert die Aussage *„dieses Opfer
> liegt unter jeder verbliebenen sichtbaren Zelle"* — eine **Obergrenze**, die ein Empfänger aus dem
> offenen Algorithmus ableiten kann. Sie muss in `_local_partition` gespiegelt sein
> (`hi = min(sichtbare)` statt `lo = max(k, größte sichtbare)`), sonst rechnet der Guard gegen ein zu
> schwaches Angreifermodell. **Belegt:** ohne die Spiegelung bleiben im Weltraum „3 Zeilen, Σ ≤ 14"
> zwei exakte Pins stehen (der Enumerationstest wird rot); mit ihr sind es 0 Pins in „3 Zeilen,
> Σ ≤ 14" **und** „4 Zeilen, Σ ≤ 16". Wer die Regel je zurückdreht, dreht **beides** zurück.
> Test-gepinnt in `test_external_report_service.py::TestSecondarySuppression::test_opferwahl_und_a_priori_schranke_sind_gekoppelt`
> (plus der Fall ohne sichtbare Zelle, in dem es keine ableitbare Obergrenze gibt).

Dieselbe Opferregel und ein **wertunabhängiger** Zweitschlüssel gelten im tabellenlokalen Audit
(`_audit_feasible_span`) **und** im Fixpunkt-Guard (`_suppress_smallest_visible`): beide nehmen das
Minimum über `(Wert, Zweitschlüssel)`. Zwei verschiedene Opferregeln in derselben Suppression wären
zwei Angreifermodelle, von denen mindestens eines nicht modelliert ist.

Welcher Zweitschlüssel das konkret ist, hängt am Aufrufer — wertunabhängig sind beide:

* **Zeilenpfad und Fixpunkt-Guard** (`_suppress_small` → `_order_tiebreak`) nehmen die kanonische
  K5-Ordnung; bei Wertgleichheit fällt die kanonisch **erste** Zelle.
* **Stufenpfad** (`_suppress_stage_dict`, `by_contact_stage`) nimmt `(-index,)` über die **feste**
  Schlüsselordnung des Modells; bei Wertgleichheit fällt dort also die **letzte** Stufe. Die
  K5-Ordnung kommt hier gar nicht zum Einsatz — die Eingabeordnung ist selbst schon wertunabhängig
  (`ORDER_INPUT`), es gibt keinen Rang, den sie verraten könnte.

#### K1 — Struktur-Kollaps / Residuum bei publizierter Randsumme

Randsumme und teilsupprimierte Aufschlüsselung stehen im selben Artefakt, und die Aufschlüsselung ist
**per Konstruktion die exakte Partition** der Randsumme: `Event.document_type` ist NOT NULL ⇒
`total_contacts` = Σ `by_document_type`; `Client.ContactStage` hat genau zwei Werte ⇒ anonym +
identifiziert + qualifiziert = `total_contacts`; im Beispiel-Sachbericht akkumuliert `total` exakt
dieselben Zeilen wie `by_category`. Damit ist R = Randsumme − Σ sichtbare Zellen exakt bekannt. Vier
Strukturfakten engen weiter ein: jede ausgewiesene Zeile hat Wert ≥ 1 (GROUP BY erzeugt keine
Nullzeilen); unterdrückt ⇒ Wert ≤ k−1 (außer bei Sekundärsuppression); bei ≥ 3 unterdrückten Zellen
sind **alle** primär; und die Zeilenreihenfolge ist eine Ordnungsaussage (K5). An beiden Rändern
kollabiert die Partition auf genau eine Welt.

*Betroffen:* beide PDFs, externer Bericht (HTML + JSON), Berichts-Export JSON + XML. *Nicht betroffen:*
CSV (kein `summary`) und das On-Screen-Panel (nur `preview_rows`). *Beleg:* drei durchgerechnete
Kleinst-Welten mit einwertigem Preimage; quantifiziert (5 Zeilen, Werte 1..8) enthalten **64,1 %** der
publizierenden Welten mindestens eine exakt rückrechenbare Zelle — Kontrollfall ohne
`-count`-Sortierung: **3,7 %**.

*Empfehlung — teilweise schließen:* **(1)** K5 (größter Effekt: senkt die Pin-Rate um ~92–94 % und
macht Restleaks zu Multimengen- statt zeilenscharfen Aussagen). **(2)** Residual-Guard als Fixpunkt
(M+): die Suppression führt nach Primär-/Sekundärdurchlauf die Inferenz des Empfängers selbst aus und
unterdrückt nach, solange irgendeine unterdrückte Zelle über alle konsistenten Welten konstant ist;
bleibt keine sichtbare Zelle übrig, fällt die Randsumme. **Nur gemeinsam mit K5** (Kopplungs-Warnung
oben). Verifizierte Fallstricke: die geschlossene Intervallformel allein reicht nicht (übersieht
Sortierung und gemischte Hypothesen), und sobald der Guard auch sichtbare Zellen unterdrückt, entfällt
die [1, k−1]-Schranke — dann braucht es eine Intervall-Formulierung statt naiver Aufzählung.
**Verworfen:** Bandausweis der Randsumme (controlled rounding), allein nicht ausreichend und teurer in
der Utility. *Utility-Kosten des Guards:* Sichten mit Randsumme **und** unterdrückten Zellen gehen im
Testmodell von 403 auf 151 zurück; K5 **vor** dem Guard ist auch utility-seitig richtig.

*Zusätzliche Invariante für Guard/Test:* Die Identität anonym + identifiziert + qualifiziert =
`total_contacts` hängt allein daran, dass `Client.ContactStage` genau zwei Werte hat. Ein dritter Wert
bräche sie **still** und verschöbe die Disclosure-Fläche.

*Status: **mitigiert*** (#1770).
`apply_residual_guard` in [`external_report.py`](../src/core/services/dashboard/external_report.py)
läuft als **letzter** Schritt jeder Artefakt-Erzeugung, führt die Empfänger-Inferenz über die fertige
Sicht selbst aus und eskaliert deterministisch, bis kein Befund mehr bleibt:

1. die **kleinste** sichtbare Zelle der betroffenen Aufschlüsselung fällt — dieselbe Opferregel und
   derselbe Zweitschlüssel wie im tabellenlokalen Audit (Auswahl bei Wertgleichheit über die
   kanonische K5-Ordnung, nie über die Eingabeordnung),
2. ist keine sichtbare Zelle mehr da, fällt die **Randsumme** — ohne sie gibt es keine Gleichung,
3. hilft auch das nicht oder ist die harte Iterationsgrenze erreicht, fällt fail-safe die **ganze
   Aufschlüsselung** (Key weg, nicht nur Werte auf `None` — eine genullte Zeile wäre selbst noch die
   Aussage „hier gibt es mindestens einen Fall").

**Nichtirreführung auch im Fail-safe:** Fällt eine Aufschlüsselung ganz, weisen die Templates das
**aus** („Angaben nach Dokumentationstyp unterdrückt (k-Anonymität).") statt in ihren
„keine Daten"-Zweig zu laufen — dieselbe Regel, die
#1768 für die Alterstabelle gesetzt hat.
„Keine Daten im Zeitraum" ist eine Aussage über die **Rohdaten** und wäre hier schlicht falsch; sie
publiziert obendrein die Randsumme 0, die R14 gerade verbirgt. Die Kehrseite ist bewusst gleich
gehandhabt wie bei der Alterstabelle: ein wirklich leerer Zeitraum bekommt denselben Hinweis, weil
`total_contacts` dort ebenfalls unterdrückt ist. Test-gepinnt in
`test_reporting_disclosure_regression.py::TestFailSafePfadIstNichtIrrefuehrend` (inkl. Gegenprobe,
dass eine vorhandene Tabelle unverändert ihre Zeilen zeigt).

*Erreichbar, nicht theoretisch:* 15 Dokumenttypen mit je einem Ereignis und drei gleich große
Kontaktstufen lassen `suppress_report_stats` auf `{'total_contacts': None, 'unique_clients': None}`
kollabieren — jede Zeile ist bei Randsumme 15 zwingend „1", und die vollständig sichtbare
Stufentabelle hält die Randsumme rekonstruierbar. Der Fall ist als Utility-Reserve notiert: eine
gezieltere Eskalation (statt der Randsumme eine Zelle der *rekonstruierenden* Schwestertabelle
opfern) würde die Dokumenttyp-Zeilen erhalten; sie ist nicht umgesetzt, weil die Fail-safe-Richtung
Vorrang hatte.

Terminierung: jeder Schritt unterdrückt mindestens eine weitere Zelle oder Randsumme und ist damit
monoton und endlich; `MAX_GUARD_ITERATIONS` ist die zweite Sicherung gegen eine künftige Erweiterung,
die diese Monotonie verliert.

Drei Eigenschaften unterscheiden ihn vom tabellenlokalen Audit (K2) und sind der Grund, warum es
beide braucht:

* **Er rechnet über Tabellen hinweg.** `by_document_type` und `by_contact_stage` teilen sich
  `total_contacts`; und eine *vollständig sichtbare* Aufschlüsselung stellt eine unterdrückte
  Randsumme wieder her (ihre Summe **ist** die Randsumme) — ohne diesen Schritt hielte der Guard das
  Fallenlassen der Randsumme für wirksam, während der Empfänger sie nebenan addiert.
* **Er kennt die Personen-Randsumme.** Σ `persons`(`by_age_cluster`) = `unique_clients` ist eine
  Partition wie jede andere (Auflage 2 aus K0-b).
* **Er rechnet nur mit publizierten Größen.** Innerhalb der Suppression ist die Herkunft jeder
  Unterdrückung bekannt und die Schranken sind entsprechend eng; hier zählt, was ein Empfänger sicher
  weiß, der Primär- und Sekundär-Unterdrückung nicht auseinanderhalten kann.

**Die Kopplungs-Warnung ist eingehalten:** K5 ist mit
#1767 ausgeliefert; der Guard läuft im Export nach der Kanonisierung.
Der Enumerationstest belegt beides empirisch — mit voll aktivem Audit und Guard, aber wieder
count-sortierter Ausgabe, pinnt die Aufzählung 250 Kleinstzellen; mit kanonischer Ordnung 0.

Test-gepinnt in `test_reporting_disclosure_regression.py` und
`test_architecture_guards_services.py::TestZentraleSuppressionsfunktion`.

#### K3 — Zwei Grundgesamtheiten nebeneinander

In jedem Artefakt stehen Aggregate über **verschiedenen Basismengen**: `total_contacts`,
`by_contact_stage` und `by_document_type` zählen alle Ereignisse, `by_age_cluster` und `unique_clients`
nur Ereignisse **mit** Personenbezug (im Beispiel-Sachbericht zählen `total`/`by_category` sogar nur
**gemappte** Ereignisse). Die Differenz ist **kein publiziertes Aggregat** und berührt daher keine
Suppressionsregel — ist aber aus zwei sichtbaren (also gerade nicht unterdrückten, weil großen)
Randsummen exakt ausrechenbar. Der Kanal erzeugt **kleine Zahlen aus großen**:
`W := total_contacts − Σ by_age_cluster` sind die Ereignisse ohne Personenbezug, und W darf beliebig
klein sein. Zweiter Schritt: weil `anonym` als „`is_anonymous` ODER `client IS NULL`" definiert ist,
folgt **W ≤ anonym** — eine harte Schranke *nach unten*, gegen die die Sekundärsuppression (die
Mehrdeutigkeit *nach oben* erzeugt) nichts ausrichtet.

*Beleg:* Sweep über 76 910 Welten — die zweite Grundgesamtheit erhöht die Fälle mit exakt bestimmtem
Stufenvektor um **+89 %**; über 15 728 distinkte Publikationen ist W fast durchweg exakt bestimmbar, in
**8 820** davon mit 0 < W < k, also als echte Kleinstfallzahl, **ohne dass eine Suppressionsregel
angesprochen hätte**. Der M5-Schutz `_close_identity_leak` verhinderte im Export-Pfad das exakte
Pinning, die Differenz blieb aber invariant; im PDF-Pfad läuft `prepare_export` überhaupt nicht.
*(#1769) Ein eigenes `_close_identity_leak` gibt es nicht: die Meldungs-Terme sind
**grundsätzlich** verborgen statt nur situativ.)*

*Severity-Korrektur (geprüft, nicht tragfähig):* Die Lesart „ungemappte Ereignisse ⇒ mindestens ein
Hausverbot" ist **falsch** — `_UNMAPPED_Q` wird aus `TYP_BINDUNG.unmapped_q()` abgeleitet und umfasst
(#1849) genau die Ereignisse, deren
Dokumenttyp **keinen** Berichtsschlüssel trägt (`report_key` leer/`NULL`): jeder einrichtungseigene
Typ ohne Schlüssel fällt hinein, ein Hausverbots-Typ mit `report_key = excluded` dagegen gar nicht.
Korrekt ist nur „mindestens ein Ereignis eines Typs ohne Berichts-Mapping".

*Empfehlung — teilweise schließen:* **(A1, M)** im Sachbericht `by_age_cluster` um eine Zeile „ohne
Personenbezug" ergänzen, sodass Σ = `total_contacts`; W wird damit eine reguläre, suppressionsfähige
Zelle (gemessen: exaktes W mit 0 < W < k von 8 820 auf **0**). **Drei Auflagen:** die Restzeile muss
**unbedingt** emittiert werden, auch bei W = 0 (sonst ist ihre bloße Existenz die Aussage W ≥ 1); die
Snapshot-Merges müssen sie für Snapshots ohne diese Werte **synthetisieren**, nicht bloß tolerieren; und die
Alterstabelle fällt damit **neu** unter K1/K2 — der Guard muss sie mit abdecken. **(A2)** Im
Beispiel-Sachbericht dieselbe Idee **nicht** naiv umsetzen: eine Zeile „ohne Berichts-Mapping" bei
unveränderter Randsumme erzeugt einen **neuen** exakten Kanal (485 von 840 durchgerechneten
Kleinzell-Welten werden pinnbar, heute 0), weil die Sekundärsuppression voraussetzt, dass die Zeilen
sich auf die publizierte Randsumme summieren; gangbar nur mit umgestellter Randsumme oder
teilmengenbewusster Sekundärsuppression. **(A3, S, unabhängig)** PDF und externer Bericht benennen die
Grundgesamtheit je Tabelle heute gar nicht — das ist nicht nur ein Leck, sondern inhaltlich
irreführend.

*Status (A1): **mitigiert*** (#1770).
`with_no_person_row` in [`external_report.py`](../src/core/services/dashboard/external_report.py) hängt
die Zeile „Ohne Personenbezug" an `by_age_cluster` an — in `build_external_report` und
`suppress_report_stats`, also in **jedem** Artefakt, das `total_contacts` publiziert. Die drei Auflagen
sind einzeln verankert:

1. **Immer emittiert, auch bei W = 0.** Datenunabhängige Existenz; W = 0 läuft als reguläre
   Kleinstzelle in die Suppression. Erschiene die Zeile nur bei W > 0, wäre ihre bloße Existenz die
   Aussage „W ≥ 1" — dieselbe datenabhängige Verzweigung, die K6 im Vollständigkeitsblock beseitigt hat.
2. **Synthetisiert statt gespeichert.** Die Zeile entsteht **an der Artefaktgrenze** aus
   `total_contacts` − Σ und wird nie aus einem Snapshot übernommen. Damit ist die
   Snapshot-Merge-Auflage stärker erfüllt als gefordert: es gibt keinen Alt-/Neu-Snapshot-Split, weil
   kein Snapshot die Zeile je trägt (`_merge_stats` summiert `total_contacts` und die Alterszeilen
   ohnehin konsistent, die Differenz über den gemergten Gesamtzeitraum ist exakt). `with_no_person_row`
   ist idempotent — ein künftiger Lesepfad, der die Zeile mitbrächte, zählte W sonst doppelt.
3. **Die Alterstabelle fällt neu unter K1/K2.** Sie deklariert `margin="total_contacts"` (vorher
   `margin=None`, das Machbarkeits-Audit lief dort gar nicht) und steht mit derselben Randsumme in
   `_EXTERNAL_REPORT_BREAKDOWNS` wie `by_document_type`/`by_contact_stage`.

Zwei Details, ohne die die Umsetzung fail-open wäre:

* **Die Restzeile trägt kein `persons`.** Ihre Äquivalenzklasse ist keine Personenmenge, sondern die
  Menge der Ereignisse ohne Personen-FK — untereinander nicht verknüpfbar (dieselbe Begründung wie bei
  `persons_by_contact_stage['anonym']`). Sie bleibt damit aus der Personen-Randsumme Σ `persons` =
  `unique_clients` heraus, die sonst still bräche.
* **Zeilen-Existenz-Untergrenze je Zelle** (`_row_min_value`): GROUP-BY-Zeilen tragen ≥ 1 Fall, die
  datenunabhängig emittierte Restzeile darf 0 sein. Eine pauschale `lo = 1` machte das Intervallmodell
  **unerfüllbar**, und ein leeres Intervall gilt in `disclosure.findings` als begründete Ausnahme — der
  Guard liefe fail-open, und zwar auch für die **Nachbarzellen**, deren Obergrenze über Σ `lo` der
  übrigen Zellen berechnet wird.

**Damit überholt:** der Eintrag unter „Geprüft und nicht tragfähig", `by_age_cluster` publiziere keine
Ereignis-Randsumme — er hatte genau diese Umsetzung als seinen Auslöser benannt.

*Status (A2): **entschieden — Option A, Status quo** (Maintainer-Entscheid 2026-08-04,
#1773 /
#1785).* Im Beispiel-Sachbericht bleibt es bei
`margin=None` und **ohne** Restzeile „ohne Berichtszuordnung"; die Begründung steht an
`_SAMPLE_REPORT_1_BREAKDOWNS` im Code. Der Entscheid kostet keine Schutzwirkung: der W-Kanal aus K3
existiert in diesem Artefakt nachweislich nicht (keine publizierte Größe entspricht Σ der
Alterstabelle oder „alle Ereignisse"), und die Grundgesamtheits-Ausweise aus A3 machen die
Doppeldeutigkeit der beiden Basismengen sichtbar — sie sind die Antwort auf die fachliche Frage, die
eine Restzeile beantworten sollte. Was der Verzicht kostet, ist allein die bezifferte
Vollständigkeitsangabe im Artefakt; der unbezifferte Vollständigkeitshinweis (K6) bleibt.
**Aus dem RFC ist damit kein „später" geworden, sondern ein Nein mit Wiedervorlagegrund:** die
Optionen B und C unten bleiben gangbar, aber keine wird ohne neuen Anlass gebaut. Wer die Zeile
ergänzen will, kommt an ihnen nicht vorbei — die **naive** Form ist nachweislich pinnbar
(485/840 Welten).

**Wichtig für die Wiedervorlage:** Das ist *keine* technische Unmöglichkeit. Die Analyse nennt **zwei**
gangbare Routen, und beide stehen weiterhin offen:

1. **Umgestellte Randsumme** — `total` zählt alle Ereignisse statt nur der gemappten; die Zeilen
   summieren sich dann wieder exakt auf die publizierte Randsumme. Kosten: ein Produktentscheid über
   die Kernkennzahl „Gesamtkontakte" des Berichts.
2. **Teilmengenbewusste Sekundärsuppression** — die Suppression lernt, dass eine Aufschlüsselung ihre
   Randsumme nur *teilweise* partitioniert, und rechnet mit `Σ Zeilen ≤ Randsumme` statt `=`. Kosten:
   ein zweites Inferenzmodell neben dem bestehenden Partitions-Modell in `disclosure.py`.

Nicht gangbar ist allein die **naive** Variante (Zeile bei unveränderter Randsumme): sie erzeugte
485 von 840 durchgerechneten Kleinzell-Welten als pinnbar (heute 0), weil die Sekundärsuppression
voraussetzt, dass die Zeilen sich auf die publizierte Randsumme summieren. Der W-Kanal aus K3 selbst
existiert im Beispiel-Sachbericht nicht — er hat keine Ereignis-Gesamtsumme, aus der W ableitbar wäre;
die Nicht-Umsetzung kostet dort also keine Schutzwirkung, sondern nur die Vollständigkeitsangabe.

*Verifikation:* `test_reporting_disclosure_regression.py::TestKleinstWeltEnumerationRestzeile` zählt den
Weltraum „zwei Altersgruppen ≥ 1 plus W ≥ 0, Σ ≤ 14" exakt auf und prüft, dass W über sein Preimage
nicht eindeutig ist. **Zwei Positiv-Kontrollen** belegen den Trefferfall statt ihn zu behaupten: der
Stand vor A1 (keine Restzeile — W nur als Differenz) und die von der Analyse verworfene **naive**
Fassung (Zeile emittiert, aber keine Randsumme deklariert, also kein Audit). Beide reißen; die
umgesetzte Form liefert 0 Pins. Belegt ist damit die eigentliche Aussage: **die Zeile allein löst
nichts** — tragend ist die Kombination aus datenunabhängiger Emission, deklarierter Randsumme und
K1/K2-Durchlauf. Zellenweise gepinnt in `test_external_report_service.py::TestRestzeileOhnePersonenbezug`.

*Status (A3): **mitigiert*** (#1770). Jede
Tabelle der Artefakte benennt ihre Grundgesamtheit, aufgesetzt auf die `data_source`-Infrastruktur aus
#1743 statt daneben: `table_note(population,
data_source)` in [`data_source.py`](../src/core/reporting/data_source.py) liefert
„Grundgesamtheit: … · Quelle: …" als **eine** Aussage — getrennte Ausweise wären zwei Halbaussagen.
Stabile Codes (`all_events`, `events_with_person`, `mapped_events`, `persons`) gehen an die
Maschinenformate (`report["metadata"]["populations"]`, Chart-JSON), die übersetzten Nominalphrasen an
HTML/PDF. Reporting-Backends deklarieren ihre Menge selbst (`DatasetBackend.population`, Default
`all_events`; `SampleReport1Backend` überschreibt auf `mapped_events`). **Auch das interne Dashboard**
trägt den Ausweis: es zeigt die Alterstabelle ohne die A1-Restzeile, zählt dort also nur Ereignisse mit
Personenbezug — ohne die Angabe läse jemand dieselbe Tabelle in zwei Bedeutungen (Suppression bleibt
dort bewusst aus, interner Rohpfad,
#1311). Test-gepinnt in
`test_reporting_population_stamp.py`.

#### K4 — Berichtsübergreifende Korrelation

Die Suppression ist ausschließlich **artefakt-lokal**: jede Ausgabe entscheidet neu gegen k, ohne
Wissen über bereits herausgegebene Artefakte. Es gibt kein Freigabe-Ledger und keine Bindung an eine
feste Zeitraum-Partition; `date_from`/`date_to` sind frei wählbar (nur Spannen-Cap 1096 Tage).

- **K4a — Zeitraum-Differenz.** Alle Aggregate sind über disjunkte Segmente exakt additiv; für A ⊂ B
  gilt zellenweise B − A = (B\A), inklusive aller Zellen, die ein direkter Bericht über B\A
  unterdrücken würde. *Beleg (Regelfall des externen Berichtswesens):* 12 Monats-PDFs + 1 Jahres-PDF —
  eine im März unterdrückte Zelle **inklusive Sekundär-Opfer** ergibt sich aus der Jahressumme minus
  den 11 sichtbaren Monatszellen. Verschärfung bei verschachtelten Zeiträumen (Jan–Mai und Jan–Jun,
  Juni = genau ein Ereignis): in **beiden** Artefakten ist keine einzige Zelle unterdrückt, und die
  Konsistenzklassen für den Juni schrumpfen über mehrere Freigaben auf **eine** — effektives k = 1 bei
  nominellem k = 5.
- **K4b — `unique_clients`-Inflation.** `unique_clients` ist als einziges Feld nicht additiv, wurde im
  Hybrid-Pfad aber summiert. Die k-Prüfung lief damit gegen eine aufgeblähte Zahl (Personen-Monate):
  3 Personen × 6 Monate à 1 Kontakt ⇒ PDF publizierte „Einzigartige Personen: 18", während der
  Live-Export mit distinct = 3 < k unterdrückte. **Ursache ist die monatsweise Segmentierung**, nicht
  der Snapshot-Inhalt; der „ca."-Hinweis existierte nur im internen Dashboard, in **keinem**
  exportierten Artefakt.

  *Status: **mitigiert*** (#1770).
  `_merge_stats`/`_merge_sample_report_1_stats` liefern **strukturell keine** Personenzahl mehr
  (`None` = unbekannt) — die Summe ist nicht abgeschaltet, sondern nicht mehr bildbar. Beide
  Hybrid-Pfade setzen stattdessen einen eigenen **Live-DISTINCT-Term über den Gesamtzeitraum** und
  weisen dessen Quelle getrennt aus (`unique_clients_data_source = live`, Fußnote am Wert in beiden
  PDFs — der Rest des Artefakts bleibt `snapshot_hybrid`; Muster aus
  #1743, Option c). Für retention-bereinigte
  Alt-Zeiträume fällt der Live-Wert niedriger aus als die Snapshot-Zeilen desselben PDF — bewusst
  akzeptiert und durch die Fußnote kenntlich; ein Rückfall auf die Summe wäre der Bypass. Der
  „ca."-Hinweis des internen Dashboards ist mit der Approximation entfallen (er beschrieb genau die
  Überzählung, die es nicht mehr gibt). Test-gepinnt in
  `test_statistics_hybrid.py::TestHybridPersonenzahlIstEigenerLiveTerm` und
  `::TestSachberichtPdfWeistDenLiveTermAus`.
- **K4c — Lesequellen-Divergenz.** PDFs lesen Snapshot-Hybrid, externer Bericht und Export live;
  `enforce_retention` friert Snapshots bewusst **vor** dem Soft-Delete ein. PDF minus Export ist damit
  ein Fingerabdruck des Gelöschten (Brute-Force über 45 Einzel-Löschkandidaten: genau 2 konsistent,
  beide derselben Kategorie).

  *Status: **mitigiert** (im Rahmen der Abwertung)*
  (#1770). Die drei belastbaren Maßnahmen
  sind umgesetzt: **(i)** Quelle **und Stand** stehen in jedem extern zirkulierenden Artefakt.
  #1743 hatte die *Quelle* ausgewiesen, nicht
  aber ihren *Stand* — „Monats-Aggregate" beantwortet nicht, von wann sie sind. Der hybride Lesepfad
  führt jetzt `snapshot_as_of` mit (jüngster `updated_at` der **tatsächlich beitragenden** Snapshots;
  `None`, wenn kein Segment aus einem Aggregat kam — dann behauptet das Artefakt keine Herkunft, die es
  nicht gibt), beide PDFs und das Dashboard rendern ihn. **(ii)** Dass eingefrorene Aggregate die
  Löschung überleben, steht im Löschkonzept und im Admin-Handbuch. **(iii)** Der Betriebshinweis „PDF
  und Live-Export desselben Zeitraums nicht an denselben Empfänger" steht im Admin-Handbuch (DE+EN).
  **Unverändert:** die Lesequellen bleiben getrennt — eine Vereinheitlichung ist weiterhin nicht
  geplant, K4b bleibt ihre Voraussetzung. Test-gepinnt in
  `test_reporting_population_stamp.py::TestSnapshotStandStempel`.

*Empfehlung (umgesetzt):* **K4b schließen (S–M, Priorität 1)** — in keinem extern zirkulierenden
Artefakt darf eine über Monate summierte Personenzahl als „Einzigartige Personen" erscheinen, und die
k-Prüfung darf nie gegen die Summe laufen; umgesetzt als Full-Range-**distinct** als eigener Term
(dasselbe Muster, das der Hybrid-Pfad für `top_clients` schon verwendet), gemeinsam mit dem
Quellen-Ausweis aus #1743. K4b bleibt
**Voraussetzung** für jede Vereinheitlichung der Lesequelle — die ist weiterhin nicht geplant. **K4c (S, abgewertet)** ist ein Spezialfall generischer
Snapshot-Staleness; belastbar sind (i) Quelle + Snapshot-Stand in jedes extern zirkulierende Artefakt
**stempeln** (die CSV-Metazeile ist das Muster), (ii) im Löschkonzept festhalten, dass eingefrorene
Aggregate die Löschung überleben, (iii) Handlungsanweisung „PDF und Live-Export desselben Zeitraums
nicht an denselben Empfänger". **K4a akzeptieren** (siehe unten), flankierend eine
**Freigabe-Übersicht** aus den ohnehin auditierten Exporten — mit zwei Auflagen: das `EXPORT`-Detail
braucht ein Empfänger-/Zweckfeld (sonst Alarmmüdigkeit bei jedem Überlapp), und die Historie darf nicht
von `auditlog_retention_months` gekappt werden, sonst täuscht die Übersicht Vollständigkeit vor.
**Nicht empfohlen:** ein Kalenderraster für Berichtszeiträume (bricht nicht-kalendarische
Förderzeiträume und **erhöht** die Ausnutzbarkeit der Partitions-Rückrechnung) sowie echtes
Query-Auditing/DP mit Budget (korrekt, aber unverhältnismäßig für ein Werkzeug, dessen Betreiberin
legitim Vollzugriff auf die Rohdaten hat).

*Status (K4a-Flankierung): **umgesetzt***
(#1770). Die **Freigabe-Übersicht**
(`/statistics/reports/releases/`, Rollen-Guard wie das Berichts-Panel) listet die herausgegebenen
Berichtszeiträume der Einrichtung und warnt bei Überschneidung; Service:
[`release_log.py`](../src/core/services/dashboard/release_log.py). Sie kann den Kanal **nicht
schließen** — K4a bleibt die akzeptierte Definitionsgrenze —, sie macht ihn sichtbar. Beide Auflagen
der Analyse sind erfüllt:

* **Empfänger-/Zweckfeld.** Beide Felder werden am Export optional erfasst und wandern in das
  bestehende `EXPORT`-Audit-`detail` (JSON, **keine** Schema-Migration auf dem append-only,
  hash-verketteten Log). Sie sind bewusst optional — ein Pflichtfeld erzwänge eine Angabe auch dort, wo
  der Export gar keine Herausgabe ist (Probelauf), und Pflichtfelder ohne Anlass werden mit „x"
  gefüllt. Freitext auf 200 Zeichen gekappt: der Log überlebt die Löschfristen und
  ist nachträglich nicht redigierbar.
* **Historie nicht von `auditlog_retention_months` gekappt.** Die Ausnahme hängt **nicht** an der
  Aktion `EXPORT` — die ist eine Sammelklasse — sondern an `RELEASE_EXPORT_Q` in
  [`release_log.py`](../src/core/services/dashboard/release_log.py): Aktion `EXPORT` **und**
  Berichtszeitraum im `detail` **und** der Marker „ein Artefakt hat das Haus verlassen". Dieselbe eine
  Definition speist Übersicht und Retention-Ausnahme; zwei Kopien wären zwei Wahrheiten (eine Zeile,
  die die Übersicht führt, aber das Pruning löscht, macht die Übersicht still unvollständig).

  Drei Klassen fallen damit ausdrücklich **nicht** unter die Ausnahme und bleiben der 24-Monats-Frist
  unterworfen: Art.-15-Datenauskünfte einzelner Personen, DSGVO-Dokument-Downloads (beide ohne
  Berichtszeitraum) und die **Bildschirm-Aufrufe** des externen Berichts (mit Zeitraum, aber ohne
  herausgegebenes Artefakt — sie werden bei jedem Reload auditiert und ließen den Log sonst unbegrenzt
  wachsen). *Implementierungsfalle, test-gepinnt:* der Marker wird per `has_key` geprüft, nicht per
  Wertvergleich — ein fehlender JSON-Schlüssel ergibt in SQL NULL, und `exclude(NULL)` hätte die
  markerlosen Zeilen aus der Löschmenge genommen statt sie hineinzunehmen.

  *Nachtrag (#1772, Punkt 2):
  `?format=json` **gehört zur Ansichts-Klasse**.* Die erste Fassung zählte den JSON-Abruf des externen
  Berichts zu den Freigaben („maschinenlesbares Artefakt"). Das trägt nicht: die Route ist dieselbe
  Bildschirmseite in einem anderen Serialisierungsformat — kein Empfänger-/Zweckformular, keine
  Download-Geste, und wie die HTML-Fassung wird sie bei **jedem** Aufruf auditiert. Genau die zwei
  Folgen, um derentwillen die HTML-Ansicht ausgenommen wurde (unbegrenztes prune-exemptes Wachstum,
  Verdrängung echter Downloads aus dem `RELEASE_LOG_LIMIT`-Fenster), galten damit unverändert. Wer den
  Datensatz als Datei herausgibt, nimmt den generischen Berichts-Download — der trägt die
  Freigabe-Felder und bleibt Freigabe, obwohl er ebenfalls JSON liefert.

  **Abwertung am Lesepfad.** Ansichts-Zeilen mit Freigabe-Gestalt können den Marker tragen; entfernen
  lässt er sich so wenig wie nachtragen (`detail` geht in den Ketten-Hash ein). Die Abwertung greift
  deshalb am **Lesepfad**: `_LEGACY_VIEW_Q` schließt die eindeutig erkennbare Gestalt dieser Zeilen aus
  (`target_type="ExternalReport"` **und** `detail.format="json"`), sodass die Übersicht künftig nur
  echte Freigaben zeigt und diese Zeilen zugleich die Retention-Ausnahme verlieren — sie laufen wie
  jede andere Ansicht nach `auditlog_retention_months` aus, nichts wird vorzeitig gelöscht. Die
  Ausnahme hängt an **beidem**, Ziel und Format, sonst nähme sie die echten JSON-Downloads mit;
  `has_key` steht wieder vor dem Wertvergleich (dieselbe NULL-Falle wie oben). Test-gepinnt in
  `test_report_release_log.py::TestUeberlappungswarnung::test_json_ansicht_ist_keine_freigabe`,
  `::test_json_altzeilen_mit_marker_sind_nicht_mehr_gelistet`,
  `::test_json_export_der_backend_pipeline_bleibt_eine_freigabe` und
  `TestExportUeberlebtDieAuditRetention::test_json_ansicht_ist_nicht_prune_exempt`.

  Ohne die Ausnahme täuschte die Übersicht Vollständigkeit vor: eine im vierten Jahr herausgegebene
  Jahresauswertung überschnitte sich mit Monatsberichten, die der Log nicht mehr kennt, und die Warnung
  bliebe stumm — ein stiller falscher Negativ, schlechter als längere Aufbewahrung.

  **DSGVO-Begründung (Zweckbindung, Art. 5 Abs. 1 lit. b + Abs. 2):** Zweck ist der Nachweis
  herausgegebener **Statistiken**, nicht die Beobachtung von Beschäftigten. Das `detail` dieser
  Teilmenge trägt Zeitraum, Format und die erfassten Empfänger-/Zweckangaben; die Artefakte selbst sind
  k-anonymisiert, es gibt dort keine Pseudonyme und keine Personenzeilen. **Was dauerhaft stehen
  bleibt, ist damit trotzdem nicht personenlos:** die auslösende Person steht wie in jedem
  Audit-Eintrag drin, und Empfänger/Zweck sind **ungeprüfter Freitext** — ihre Datenminimierung ist
  eine Betreiber-Obliegenheit (Empfehlung im Admin-Handbuch: Organisation statt Ansprechperson),
  keine technische Zusicherung. Unter dieser Maßgabe folgt die Aufbewahrung derselben
  Rechenschaftslogik wie `SECURITY_VIOLATION`.

**Grenze — kein Backfill möglich.** Als Freigabe zählt nur eine Zeile mit dem Marker
„herausgegebenes Artefakt". Zeilen ohne Marker sind weder prune-exempt noch in der Übersicht sichtbar, und eine
Überlappungswarnung kann gegen sie nicht feuern. **Nachtragen ist ausgeschlossen, nicht bloß unbequem:** `detail` geht in
den Ketten-Hash ein (`chain._canonical_payload`), eine nachträgliche Markierung bräche also die
HMAC-Kette ab der geänderten Zeile — genau das, was
`test_audit_chain.py::TestTamperDetection::test_inplace_detail_mutation_breaks_chain` als Manipulation
nachweist; sie zu „reparieren" hieße, die Kette vorwärts neu zu schreiben und ihre einzige Eigenschaft
aufzugeben. Dieselbe Doktrin gilt schon beim Pruning („die Kette wird nie umgeschrieben, der Checkpoint
legitimiert nur die Lücke"). Die Richtung ist fail-safe (lieber eine Freigabe zu wenig zeigen als eine
erfinden); die Übersichtsseite und das Admin-Handbuch benennen die Grenze ausdrücklich, statt
Vollständigkeit zu behaupten. Test-gepinnt in `test_report_release_log.py`
(`…_altzeilen_vor_der_marker_einfuehrung_…`, roh konstruierte Alt-Schema-Zeile ohne Marker).

**Bewusste Einschränkung der Warnung:** gemeldet werden nur **nicht-identische** Überschneidungen. Aus
zwei Berichten über *denselben* Zeitraum lässt sich keine Differenz bilden, ganz gleich, an wen sie
gingen — der zweite ist typischerweise ein erneuter Download. Der Kanal lebt von verschachtelten oder
teilweise überlappenden Zeiträumen (A ⊊ B), und genau die bleiben stehen; ohne die Einschränkung
produzierte die Übersicht Alarme ohne Aussage. **Zweiter blinder Fleck (neben der Rollout-Grenze oben):** die
Übersicht endet beim Download — eine weitergeleitete PDF-Datei oder ein abfotografierter
Bildschirm erscheinen nicht darin. Das ist eine organisatorische, keine technische Grenze und steht so
auf der Seite selbst. Test-gepinnt in `test_report_release_log.py`.

**Dritter blinder Fleck: das Auswertungsfenster** (#1772,
Punkt 3). `build_release_overview` liest die `RELEASE_LOG_LIMIT = 500` jüngsten Freigaben — die
Überlappungsprüfung ist quadratisch und braucht auf einer append-only wachsenden Tabelle eine Grenze.
Ältere Freigaben können damit keine Überschneidung mehr auslösen, und das ist derselbe stille falsche
Negativ wie bei der Rollout-Grenze. Der Seiten-Hinweis nennt die Zahl jetzt ausdrücklich (Kontext
`release_limit` aus der Konstanten, nicht im Text dupliziert); test-gepinnt in
`test_report_release_log.py::TestFreigabeUebersichtView::test_seite_nennt_die_mengen_grenze_der_auswertung`.
Die Abwertung der JSON-Ansicht (Punkt 2 oben) entlastet dasselbe Fenster: Ansichten verdrängen dort
keine Downloads mehr.

#### Akzeptierte Definitionsgrenzen

Drei Restkanäle sind **nicht** schließbar, ohne das Verfahren selbst aufzugeben. Sie sind bewusst
akzeptiert und gehören in die Kommunikation gegenüber Empfängern:

1. **Die Summe der unterdrückten Zellen ist exakt bekannt.** R = Randsumme − Σ sichtbare Zellen, und
   jede unterdrückte Zelle bleibt auf ein Teilintervall von [1, k−1] eingeschränkt. Das ist die
   Definition von Zell-Suppression: k-Anonymität schützt Merkmalskombinationen, nicht die
   Randsummen-Arithmetik. R zu eliminieren hieße, die Randsumme aufzugeben (in
   [ADR-023](adr/023-k-anonymization-statistik.md) aus Utility-Gründen verworfen) oder Rauschen/DP
   einzuführen (dort explizit vertagt). Ebenfalls untrennbar: der Ausweis „unterdrückt" verrät, dass es
   in dieser Aufschlüsselung eine Zelle < k gibt — und dieser Ausweis ist gewollt (Nichtirreführung).
2. **Ungleichungen zwischen Personen- und Ereigniszählungen.** `unique_clients` zählt **Personen**, die
   Tabellen zählen **Ereignisse**; zwischen zwei Maßeinheiten gibt es keine gemeinsame
   Grundgesamtheit, an der eine Zell-Suppression ansetzen könnte. Wer das schließen will, braucht
   kontrolliertes Runden/Intervallausweis statt Suppression — ein anderes SDC-Verfahren mit
   erheblichem Nutzenverlust. **Nicht** von dieser Akzeptanz gedeckt: K4b (das ist ein Bug, keine
   Grenze) und die Lesequellen-Divergenz K4c.
3. **Die Differenz mehrerer Freigaben über verschachtelte Zeiträume (K4a).** k-Anonymität ist eine
   Eigenschaft **einer** veröffentlichten Tabelle über **einer** Grundgesamtheit. Im Regelfall
   (Monatsberichte plus Jahresbericht) sind beide Artefakte für sich vollständig k-anonym — es gibt
   keine Zelle, an der eine artefakt-lokale Suppression ansetzen könnte. Der Kanal ist nicht durch M5
   entstanden, er ist der Familie inhärent, seit sie frei wählbare Zeiträume anbietet.

Formulierung für die Empfänger-Kommunikation:

> Die k-Anonymitäts-Zusicherung gilt **je Artefakt und je Zeitraum**. Werden für dieselbe Einrichtung
> Berichte über Teilzeiträume **und** über deren Vereinigung herausgegeben (z. B. Monatsberichte plus
> Jahresbericht), lässt sich jede in einem Teilbericht unterdrückte Zelle aus der Summe der übrigen
> exakt zurückrechnen — auch dann, wenn die Sekundärsuppression gegriffen hat, und auch dann, wenn in
> keinem der beteiligten Berichte eine einzige Zelle unterdrückt wurde.

Betriebshinweis: Berichtszeiträume nicht unter einem Monat wählen.

#### Geprüft und nicht tragfähig

Damit diese Punkte nicht erneut untersucht werden:

- **`by_age_cluster` als Residuum-Kanal (K1):** war widerlegt **für die Ereignis-Randsumme** — kein
  Artefakt publizierte eine Randsumme = Σ Altersgruppen (die Tabelle zählt nur Ereignisse *mit*
  Personenbezug, `total_contacts` alle), das Machbarkeits-Audit lief dort mit `margin=None`, also gar
  nicht. **Überholt seit K3-A1** (die Umsetzung, die dieser Eintrag selbst als Auslöser benannt hatte):
  mit der Restzeile „ohne Personenbezug" ist die Tabelle eine exakte Partition von `total_contacts`,
  deklariert diese Randsumme und läuft durch Audit und Fixpunkt-Guard. Der Eintrag steht nur noch als
  Historie hier — er beschreibt den Zustand vor
  #1770.
  **Nicht widerlegt ist die Personen-Randsumme:** seit K0-b gilt Σ `persons`(`by_age_cluster`) =
  `unique_clients`, und über *diese* Gleichung ist der Kanal tragfähig — geschlossen durch die
  total-Doktrin (`unique_clients` fällt bei der ersten personenbedingt unterdrückten Zeile) und
  zusätzlich im Inferenzmodell des Fixpunkt-Guards, siehe K0-b § Verbindliche Auflage. Der Eintrag
  hier bezieht sich also **ausschließlich** auf die Ereignis-Randsumme.
- **Primär/Sekundär allein an der Zeilenposition unterscheidbar (K5):** widerlegt in der generischen
  Form — in 40 % der Zwei-Zellen-Fälle sind beide primär; der Schluss braucht eine publizierte
  Untergrenze für die Summe.
- **„Residual-Guard allein bringt 0 Pins" (K1):** widerlegt — 24/853 Sichten behalten einen exakten
  Pin; die Aussage war zirkulär validiert (Kopplungs-Warnung).
- **„Machbarkeits-Audit kostet weniger Information als der Status quo" (K2):** widerlegt — es
  unterdrückt in keiner Welt weniger, in 13,5 % mehr Zellen.
- **CSV-Export** trägt K1, K3, K5 und K6 **nicht** (nur `records` + Metazeile); das
  **On-Screen-Panel** trägt K1 und K3 nicht, K6 **doch**.
- Frühere Zwischenstände nannten Rest-Pins, die sich bei vollständiger Preimage-Auswertung auflösten.
  Verbindlich ist nur das Kriterium „publizierte Randsumme ≤ Enumerationsgrenze".

#### Regressionsschutz

Einzelfall-Asserts haben diese Kanalklasse jahrelang nicht gefunden — jede einzelne Zusicherung war
für sich erfüllt, und trotzdem ließ die *Kombination* aus Randsumme, Zeilenordnung und
Suppressionsentscheidung Kleinstfallzahlen exakt zurückrechnen. Erforderlich und
(#1770) **umgesetzt**:

**(1) Kleinst-Welt-Enumerationstest** über die publizierte Sicht mit der Invariante „keine
unterdrückte Zelle ist über das Preimage eindeutig bestimmt" — in
`test_reporting_disclosure_regression.py::TestKleinstWeltEnumeration` (und dieselbe Invariante am
zweiten Wrapper, dem Beispiel-Sachbericht). Präzisierungen, die die Umsetzung nötig gemacht hat:

* **Auflage eingehalten:** gewertet werden nur Beobachtungen, deren publizierte Randsumme ≤
  **Enumerationsgrenze** ist. Geprüft werden **zwei** Welträume, beide exakt aufgezählt und nicht
  gestichprobt: `ENUMERATION_LIMIT = 14` bei drei nichtleeren Zeilen (364 Welten) und Σ ≤ 16 bei vier
  Zeilen (1820 Welten). Der zweite ist kein Luxus — die Sekundär-Suppression zeigt ihre
  Fixpunkt-Eigenschaft erst, wenn nach dem ersten Opfer noch mehr als eine sichtbare Zelle übrig ist.
  Ist die Randsumme unterdrückt, kennt der Empfänger sie nicht; dann sind auch Welten jenseits der
  Grenze konsistent, und ein hier „eindeutiges" Preimage wäre ein Artefakt der Aufzählung
  (Schein-Pin).
* **Die Invariante ist auf Kleinstfallzahlen geschärft:** ein exakt bestimmter Wert ≥ *k* ist kein
  Befund — das Sekundär-Opfer *darf* rückrechenbar sein, es wurde nur unterdrückt, um die Kleinstzelle
  zu schützen. Die Begründung steht bei den drei Ausnahmen in `disclosure.findings`.
* **Zwei Positiv-Kontrollen**, damit der Test seinen eigenen Trefferfall beweist: der K2-Stand vor
  #1770 erzeugt an derselben Aufzählung **54** gepinnte Kleinstzellen, derselbe *neue*
  Suppressionsstand mit count-sortierter Ausgabe (Stand vor #1767) **250** — letzteres ist der
  empirische Beleg der Kopplungs-Warnung.

**(2) Ordnungstest** (zwei Welten mit vertauschten Zellwerten müssen dieselbe Ausgabe erzeugen) — auf
Service-Ebene (#1767)
(`TestKanonischeOrdnungUnterdrueckterZeilen`), **zusätzlich an der Artefaktgrenze** in
`test_reporting_disclosure_regression.py::TestOrdnungAnDerArtefaktgrenze`: externer Bericht (HTML),
beide PDF-Datenpfade sowie JSON- und XML-Renderer. Der Service-Test allein hätte ein künftiges
`dictsort` im Template oder ein `sorted(...)` im Renderer nicht bemerkt.

**(3) Architektur-Guard** in
`test_architecture_guards_services.py::TestZentraleSuppressionsfunktion` (AST-basiert): keine
Eigenbau-Unterdrückung im ganzen Anwendungsbaum (nur `external_report.py` darf den
`suppressed`-Marker setzen), jede Aufrufstelle der zentralen Suppression deklariert `margin` **und**
`order`, `order` stammt aus der zentralen Ordnungsliste, und jedes `prepare_export` mit Randsumme
durchläuft den Fixpunkt-Guard. Damit ist auch die **Fail-open-Klasse aus K5** geschlossen: die
frühere formbasierte Ordnungserkennung hätte eine künftige count-sortierte Tabelle mit neuer
Zeilenform still unkanonisiert gelassen. Zwei Anker halten den Guard davor, selbst leerzulaufen.

**(4) K0-Test** (4 Personen à 5 Kontakte in 4 Clustern ⇒ keine sichtbare Alterszelle; 3 Personen ×
6 Monate ⇒ keine Personenzahl ≥ k im PDF) — in
`test_external_report_service.py::TestPersonenkriteriumInDenWrappern` und
`test_statistics_hybrid.py::TestSachberichtPdfWeistDenLiveTermAus`.

**(5) Invarianten-Test:** `test_client_contact_stage_choices` in
[`test_models.py`](../src/tests/test_models.py) pinnt, dass `Client.ContactStage` genau zwei Werte hat — die Identität
anonym + identifiziert + qualifiziert = `total_contacts` hängt allein daran, und ein dritter Wert
bräche sie sonst **still**.

---

## TOTP-Secret at rest Fernet-verschlüsselt (Issue #1362)

**Status:** Design-Entscheidung, produktiv verdrahtet ([ADR-031](adr/031-totp-secret-at-rest.md)).

### Beobachtung

`django-otp` speichert das TOTP-Secret (`otp_totp_totpdevice.key`) als
Klartext-Hex. Ein Leser eines DB-Dumps/einer Replica konnte damit jeden
Authenticator rekonstruieren — auch für MFA-pflichtige Admins. Das widersprach
der #790-Härtung (Hash-Backup-Codes, Fernet-PII).

### Maßnahme

Zugriff app-weit über das Proxy-Modell
[`EncryptedTOTPDevice`](../src/core/models/mfa.py); das Secret wird mit dem
vorhandenen MultiFernet ver-/entschlüsselt
([`totp.py`](../src/core/services/security/totp.py)). Eine idempotente,
reversible Datenmigration
([`0102_totp_secret_at_rest`](../src/core/migrations/0102_totp_secret_at_rest.py))
verschlüsselt Bestandsgeräte in place.

### Trade-offs / Grenzen

- **Schutz nur *at rest*.** Ein Angreifer mit gültigem `ENCRYPTION_KEY(S)` **und**
  DB-Lesezugriff (z. B. kompromittierter App-Prozess) kann weiterhin
  entschlüsseln — wie bei allen Fernet-Feldern. Transparente
  DB-/Volume-Verschlüsselung bleibt eine **ergänzende** Operator-Kontrolle
  (threat-model TB2/TB3, TB5), kein Ersatz.
- **Spaltenbreite.** `otp_totp_totpdevice.key` wird per reversiblem `RunSQL` von
  `varchar(80)` auf `varchar(255)` geweitet (ein Fernet-Token ist ~120–140
  Zeichen). Die DB-Spalte ist damit breiter als die `max_length=80` des
  Fremd-Modells — bewusst und beim Schreiben folgenlos (kein `full_clean` auf
  dem Secret-Pfad). Wir kapern **keine** `otp_totp`-Migrationsdatei.
- **Upgrade-Hazard bei `django-otp`-Bumps.** Ein künftiges `django-otp`-Release
  (Dependabot bumpt automatisch) könnte eine `otp_totp`-Migration mit
  `AlterField('key', max_length=80)` mitbringen. Django macht daraus
  `ALTER … TYPE varchar(80)`, das an den bereits gespeicherten ~140-Zeichen-
  Fernet-Tokens **hart scheitert** (`value too long for type character
  varying(80)`) und `migrate` blockiert — potenziell mitten im Deploy.
  **Gegenmaßnahme:** Beim `django-otp`-Bump die generierten Migrationen prüfen;
  bei einem `AlterField` auf `key` eine core-Nachfolge-Migration hinterhersetzen,
  die die Spalte (nach der `otp_totp`-Migration einsortiert) wieder auf
  `varchar(255)` weitet. Der Guard-Test `TestTotpKeyColumnWidthGuard` prüft die
  effektive Spaltenbreite und schlägt bei einer unbeabsichtigten Re-Verengung
  früh an. Siehe [ADR-031 § Consequences](adr/031-totp-secret-at-rest.md).

### Key-Rotation (MultiFernet)

Neuen Primärschlüssel in `ENCRYPTION_KEYS` **voranstellen** und alte zunächst behalten; ein Ring gilt installationsweit, nicht je Einrichtung. `reencrypt_fields` wickelt Datenbankfelder einschließlich TOTP-Secrets um, `reencrypt_files` schlüsselt referenzierte Dateianhänge chunkweise über ausschließlich verschlüsselte Zwischenablage und atomaren Austausch um. `reencrypt_files --verify` ohne `--facility` prüft alle referenzierten Versionen mit dem Primärschlüssel allein, einschließlich der formatabhängigen Chunk-Bindung. Erst nach fehlerfreier Feldrotation/Vollprüfung, Klärung der nicht erfassten ImportJob-Zwischendateien und Staging-Reste ohne DB-Zeile sowie tatsächlichem Ablauf alter Backup-Sätze darf der Altschlüssel entfernt werden; Prozesse mit der neuen Konfiguration neu erstellen, dann ungefiltert erneut prüfen. Ältere noch aufbewahrte Backups benötigen weiterhin ihre damaligen Schlüssel im getrennten Eskrow. Das Manifest ist Fortschritt, kein Integritätsbeweis; ein Kill kann die Audit-Sammelzeile verhindern. Rolle, Abbruch-/Konkurrenzverhalten und Abnahmebedingungen: [Ops-Runbook § 9.5](ops-runbook.md#dateirotation-und-altschlüssel-entfernen).

Der Rewrap ist **concurrency-sicher** gegenüber parallelen MFA-Logins: Ein
`verify_token` schreibt intern zwar ein Voll-`save()` inkl. `key`, doch
`EncryptedTOTPDevice.save()` lässt die `key`-Spalte bei einem Voll-`save()` auf
eine bestehende Zeile mit bereits verschlüsseltem Secret unangetastet — ein
Verify kann einen frischen Rewrap also nicht zurücksetzen (sonst bliebe das
Gerät still unter dem Alt-Schlüssel → Lockout beim Entfernen des Alt-Keys).
Meldet `reencrypt_fields` dennoch Geräte, die trotz Retries unter dem
Alt-Schlüssel blieben, den Lauf **wiederholen**, bis er ohne diese Meldung
endet (der Lauf ist idempotent).

### Verifikation

- [`src/tests/test_totp_secret_at_rest.py`](../src/tests/test_totp_secret_at_rest.py) —
  Roh-DB-Wert ohne Klartext-Hex, Verify-Pfad vor/nach Migration, Migration
  idempotent + reversibel, MultiFernet-Rotation, kein Custom-Admin-Leak,
  Rewrap-Concurrency (Verify-`save()` lässt `key` unberührt; `reencrypt_fields`
  meldet Kontention) sowie ein Guard-Test zur effektiven Spaltenbreite
  (Upgrade-Hazard).
- CHANGELOG-`[Unreleased]`-Eintrag mit Issue-Referenz.

---

## Username-Ratelimit bleibt Monitor-only statt hartem Fix (Issue #1372)

**Status:** Design-Entscheidung mit Rationale — Monitoring statt hartem Fix.

### Beobachtung

Der Login trägt zwei Rate-Limits ([`auth.py`](../src/core/views/auth.py), Refs #598):
ein IP-Limit (`5/m`, Schlüssel `ip`) und ein Username-Limit (`10/h`, Schlüssel = normalisierter
`POST['username']`). Das Username-Limit bucketet auf den eingegebenen Namen — **nicht** auf die
Herkunft. Wer einen (in einer Einrichtung semi-öffentlichen) Username kennt, kann mit 10
Login-POSTs/h den `10/h`-Eimer dieses Namens füllen; der/die Betroffene erhält dann bis zu einer
Stunde lang `429`, unabhängig von korrekten Credentials (Angriffsbild: **gezielter Victim-Lockout**).

### Was bereits umgesetzt ist

Die **Account-Lockout-Achse** — die die harte Sperre (`is_locked`, Refs #612)
erzeugt — zählt Fehlversuche
**je Quell-IP**. Fremde Falschpasswörter sperren den Login des Opfers von dessen eigener IP damit
**nicht** mehr; der frühere *harte* Victim-Lockout ist geschlossen.
Ausgenommen ist die Rolle `super_admin`: Sie wird nie gesperrt (Break-Glass-Konto, `is_locked` in `core.services.security.login_lockout`) und ist deshalb auf das IP- und Username-Limit angewiesen.

### Trade-off — warum das `10/h`-Username-Limit bleibt

Das Username-Limit ist bewusst **herkunftsunabhängig**: Es ist die Schranke gegen einen verteilten
Angriff auf **einen** Account von einem **Botnet mit rotierenden IPs** — genau der Fall, den das
per-IP-Limit (`5/m`) nicht abdeckt (jede Bot-IP bleibt für sich unter `5/m`). Diese
**Anti-Botnet-Garantie aus #598 bleibt
bestehen**. Würde man das Limit an die IP koppeln oder anheben, öffnete man genau diesen verteilten
Credential-Stuffing-Pfad wieder.

Bewusst **nicht** eingeführt: **kein CAPTCHA** und **keine externe Abhängigkeit** — wegen
Barrierefreiheit, Datenminimierung, Self-Host-Autarkie und der strikten same-origin-CSP (keine
Third-Party-Ressource).

### Restfläche (akzeptiert)

Es bleibt eine **weiche** Rest-DoS-Fläche: Ein Dritter, der den Username kennt, kann den
`10/h`-Eimer des Opfers füllen → dessen eigener Login-POST bekommt bis zu 1h lang `429`. Die Wirkung ist
deutlich begrenzt:

- **fixes 1h-Fenster**, **nicht** self-sustaining (läuft ohne weitere Angreifer-Aktion aus);
- erzeugt **keinen** `is_locked`-Zustand (kein Account-Lockout, keine Admin-Entsperrung nötig);
- **Recovery-Pfade bleiben frei**: Password-Reset und die übrigen Auth-Flows bucketen nicht auf
  denselben Username-Schlüssel und sind IP-basiert erreichbar.

### Monitoring statt hartem Fix

Statt die Restfläche hart zu schließen (und dabei die Anti-Botnet-Garantie zu opfern), wird die
Angriffs-**Signatur sichtbar** gemacht: `detect_distributed_login_attack`
([`breach_detection.py`](../src/core/services/compliance/breach_detection.py)) schreibt einen
`SECURITY_VIOLATION`-Breach-Alarm, sobald Fehlversuche gegen **einen** Account von **vielen
distinkten Quell-IPs** (Default `BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD`) im Fenster auflaufen — die
Victim-Lockout-/Distributed-Bruteforce-Signatur. Damit ist die Restfläche **bewusst Monitor-only**
abgedeckt: kein hartes Blocking, aber eine forensische Spur plus optionaler Webhook-Alarm.

### Trigger für Re-Evaluation

- Reale Missbrauchsfälle des `10/h`-Username-Limits als gezielter Victim-DoS (statt nur als
  Anti-Botnet-Schranke).
- Wenn progressive Delays / Proof-of-Work als datensparsame, abhängigkeitsfreie Alternative zu
  einem flachen `429` reif sind (dann Backoff statt hartem Limit erwägen).

### Verifikation

- Threat-Model-Zeile „**S** Login-Brute-Force" in [`threat-model.md`](threat-model.md) verweist auf
  diese Notiz.
- `detect_distributed_login_attack`-Tests in [`test_breach_detection.py`](../src/tests/test_breach_detection.py):
  Alarm über Schwelle, kein Alarm knapp darunter, viele Versuche von EINER IP sind kein
  Distributed-Signal, Dedup greift.

---

## SMTP-Timing-Orakel an Recovery- und Reset-Anfragen bleibt akzeptiertes Restrisiko (Issue #2115)

**Status:** Akzeptiertes Restrisiko mit Rationale — kein Code-Fix.

### Beobachtung

Die beiden anfragebasierten Versandpfade für E-Mail-Accounts antworten für existierende wie für
nicht existierende Adressen gleich (ein Redirect auf die bestätigende „gesendet"-Seite), unterscheiden
sich aber **zeitlich**: `LockoutRecoveryRequestView.form_valid`
([`lockout_recovery.py`](../src/core/views/lockout_recovery.py)) versendet bei einem Treffer eine
Recovery-Token-Mail via `send_mail(...)` synchron im Anfragepfad; die
`RateLimitedPasswordResetView` ([`auth.py`](../src/core/views/auth.py)) versendet den
Password-Reset-Link über Djangos `PasswordResetForm.save()` ebenfalls synchron. Über den echten
SMTP-Transport ist der Unterschied zwischen Treffer (Tokenbau + Template-Render + SMTP-Versand, bei der
Lockout-Recovery zusätzlich der Audit-Write) und Nicht-Treffer (im Wesentlichen ein Lookup) messbar — ein
**Account-Existenz-Orakel über das Antwort-Timing**.

### Was bereits umgesetzt ist

- IP-Ratelimit `5/m` (`ratelimit(key="ip", rate="5/m", method="POST", block=True)`) an beiden
  Endpunkten — `LockoutRecoveryRequestView.post` und `RateLimitedPasswordResetView.post`.
- Antwort-Parität: beide Views liefern für existierende wie nicht existierende Adressen dieselbe
  Response (Redirect), sodass die Antwort selbst keine Existenz-Information preisgibt.

### Trade-off — warum kein asynchroner Versand

Echte Timing-Parität verlangt einen **asynchronen Versand** (Queue/Worker), der den Mail-Versand aus
dem Anfragepfad herausnimmt. Das ist eine Architekturentscheidung (Queue-Infrastruktur, Retry- und
Monitoring-Modell), die für den aktuellen Reifegrad unverhältnismäßig ist. Ein **künstliches
Zeit-Padding** auf dem Nicht-Treffer-Pfad wäre fragil und deckt die reale SMTP-Latenzschwankung nicht
ab — unter Last bliebe das Orakel bestehen. Deshalb wird die Fläche bewusst als Restrisiko
akzeptiert, wie bei #1372.

### Restfläche (akzeptiert)

- **Bis zu 5 Proben pro Minute und IP** an beiden Endpunkten (IP-Ratelimit `5/m`).
- Kein Auth-Bypass: Es fließt keine Anmelde- oder Entsperr-Information, nur die
  Existenz-/Nicht-Existenz-Aussage einer E-Mail-Adresse.
- Gleiche Schadklasse wie das bereits akzeptierte Username-/Account-Orakel in
  #1372 — reine Existenzinformation ohne
  Folgeschaden.

### Trigger für Re-Evaluation

- Einführung einer asynchronen Mail-/Task-Infrastruktur (Queue/Worker).
- Auffällig viele Ratelimit-Treffer an diesen Endpunkten (`429`) im Monitoring.
- Anhebung oder Absenkung der IP-Ratelimits der Recovery-/Reset-Endpunkte.

### Verifikation

- Antwort-Parität und Nicht-Versand bei unbekannter Adresse: `test_post_unknown_email_renders_same_response_no_mail`
  ([`test_lockout_recovery.py`](../src/tests/test_lockout_recovery.py)).
- Versand bei bekannter Adresse mit identischer Redirect-Response: `test_post_known_email_sends_recovery_link`
  ([`test_lockout_recovery.py`](../src/tests/test_lockout_recovery.py)).
- IP-Rate-Limit des Reset-Pfads: `test_password_reset_post_rate_limited`
  ([`test_auth.py`](../src/tests/test_auth.py)).

---

## `window.offlineStore` bleibt bewusst unkapselt — CSP ist der Schutzwall (Issue #1343)

**Status:** Design-Entscheidung, bewusst nicht gekapselt. DSFA-/TOM-Einordnung in [ADR-022 § DSFA-/TOM-Einordnung des Offline-Pfads](adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343).

### Beobachtung

Der verschlüsselte Offline-Store exportiert seine **entschlüsselnden** Aggregatoren als globales `window.offlineStore` — u. a. `getDecrypted`, `listDecrypted`, `getOfflineClient`, `listOfflineEventsAggregated`, `listOfflineWorkItemsAggregated` ([`src/static/js/offline-db.js`](../src/static/js/offline-db.js) legt die Fassade an, [`src/static/js/offline-bundles.js`](../src/static/js/offline-bundles.js) hängt die Aggregatoren an). Jeder JavaScript-Code, der im Origin läuft, kann diese Methoden aufrufen und damit — bei geladenem Session-Key — Klartext-Offline-Daten lesen.

### Trade-off — warum keine Closure-Kapselung

Eine Kapselung (Closure statt `window`-Global, oder ein Capability-Token) wurde erwogen und **bewusst verworfen**:

- Die UI selbst — Offline-Viewer, In-Place-Zeitstrom (`/`), Aufgabenliste (`/workitems/`) — braucht genau diese entschlüsselnden Aggregatoren. Sie müssten also für den legitimen App-Code erreichbar bleiben und lägen damit im selben JS-Realm; ein Angreifer mit Skriptausführung im Origin käme über dieselben Referenzen (oder direkt an die IndexedDB) heran.
- Der reale Schutzwall gegen **fremden** JavaScript-Zugriff ist die strikte Content-Security-Policy `script-src 'self'` (kein `'unsafe-inline'`, kein `'unsafe-eval'`; per Architektur-Tests `test_architecture_guards_*` erzwungen, dazu Verbot von `|safe`/`mark_safe()`/Inline-`<script>`). Ohne Skriptausführung im Origin ruft niemand `window.offlineStore` auf.
- Eine Closure-Kapselung gegen einen Angreifer, der bereits Code im Origin ausführt, ist **Scheinsicherheit**: er liest die entschlüsselten Daten dann direkt aus der (entsperrten) IndexedDB. Die Kapselung würde eine Schutzwirkung suggerieren, die tatsächlich die CSP trägt.

**Was den Zugriff wirklich begrenzt:**
- **Gerätediebstahl:** non-extractable AES-GCM-256-Key + Idle-Wipe (siehe [ADR-022](adr/022-offline-snapshot-keys.md), [`crypto.js`](../src/static/js/crypto.js)) — ohne aktive Session nur Chiffretext.
- **Fremder Code im Browser:** `script-src 'self'` — kein Remote-/Inline-Script, keine `eval`-Auswertung.

### Eng gescopte Ausnahme — Umami-Analytics auf der Demo (Issue #1604)

Auf `demo.anlaufstelle.app` läuft nach **Opt-in-Einwilligung** ein Umami-Tracker samt Session-Recorder/Heatmap (Endpoint per Deploy-Env konfiguriert, #1701). Skript und Beacons brauchen `script-src`/`connect-src` für den Umami-Host — also genau die Lockerung, gegen die der Schutzwall oben schützt. Damit `script-src 'self'` als Schutz von `window.offlineStore` nicht global fällt, ist die Freigabe **maximal eng gescoped**:

- **nicht global**, sondern per [`DemoUmamiCSPRelaxMiddleware`](../src/core/middleware/demo_umami_csp_relax.py) als Response-Header-Rewrite (analog [`AdminCSPRelaxMiddleware`](../src/core/middleware/admin_csp_relax.py) für `/admin-mgmt/`);
- **nur bei `DEMO_MODE=True`** — prod/`devlive`/dev/test lockern nie (Architektur-Guard [`test_demo_umami_csp_relax.py`](../src/tests/test_demo_umami_csp_relax.py));
- **nur für authentifizierte Requests (Post-Login)** — die Login-/Anon-Seite bleibt strikt und damit tracking-frei;
- **nur für HTML-Dokumente** — JSON/CSV/Statik führen kein Skript aus.

Akzeptabel, weil der Offline-Bestand der Demo ausschließlich **synthetische Seed-Daten** sind (stündlicher Reset) — kein realer Art-9-Bestand hinter dem gelockerten `script-src`. Die interne Dev-Instanz `devlive` (dev.anlaufstelle.app, #1602) gibt den Umami-Host demgegenüber bewusst **global** frei (ohne Recorder/Consent, ohne Produktivdaten). Einordnung im Offline-Threat-Model: [ADR-022 § DSFA-/TOM-Einordnung](adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343), Abschnitt (c).

### Trigger für Re-Evaluation

- Die CSP müsste auf einer offline-tragenden Route `'unsafe-inline'`/`'unsafe-eval'` zulassen (dann fällt der Schutzwall, und Kapselung/Isolierung des Stores wird relevant).
- Ein dritter Ausführungskontext mit anderem Trust-Level (z. B. eingebettetes Widget/iframe fremden Ursprungs) erhielte Zugriff auf denselben `window`.
- Die Umami-CSP-Ausnahme würde über `DEMO_MODE`/Post-Login hinaus ausgeweitet (z. B. auf eine reale Instanz mit echten Offline-Daten) — dann ist der Trade-off neu zu bewerten.

### Verifikation

- Export-Stelle: [`src/static/js/offline-db.js`](../src/static/js/offline-db.js) (legt `window.offlineStore` an: `getDecrypted`, `listDecrypted`) und [`src/static/js/offline-bundles.js`](../src/static/js/offline-bundles.js) (`getOfflineClient`, `listOfflineEventsAggregated`, `listOfflineWorkItemsAggregated`)
- CSP-Erzwingung: [`src/tests/test_architecture_guards_templates.py`](../src/tests/test_architecture_guards_templates.py) und die weiteren `test_architecture_guards_*`
- Krypto-/Wipe-Kern: [`src/static/js/crypto.js`](../src/static/js/crypto.js), [ADR-022 § Akzeptierte Restrisiken](adr/022-offline-snapshot-keys.md#akzeptierte-restrisiken)

---

## Compliance-FKs auf User: `on_delete`-Härtung + echte DB-Constraints (Issue #1347)

**Status:** Behoben (Migration [`0104_ondelete_hardening_legalhold_workitem_deletionrequest`](../src/core/migrations/0104_ondelete_hardening_legalhold_workitem_deletionrequest.py)).

### Beobachtung (DAT-04)

Drei Compliance-relevante Foreign Keys auf `settings.AUTH_USER_MODEL` trugen `on_delete=CASCADE`: `LegalHold.created_by`, `WorkItem.created_by`, `DeletionRequest.requested_by`. Eine harte User-Löschung (Django-Admin/Shell; die App selbst hat aktuell keinen Hard-Delete-View, aber eine künftige DSGVO-Erasure würde genau diesen Pfad nutzen) hätte Legal Holds — den Nachweis einer Aufbewahrungspflicht — sowie Aufgaben- und Löschantrags-Historie mitgerissen. Bei einem Legal Hold ist das ein Spoliationsrisiko (Vernichtung von Beweismitteln/Nachweisen).

### Fix

- `LegalHold.created_by`: `CASCADE` → `PROTECT`. Eine User-Löschung schlägt mit `ProtectedError` fehl, solange der User noch Legal Holds erstellt hat — Compliance-Objekte können nicht "versehentlich" mitgelöscht werden.
- `WorkItem.created_by` / `DeletionRequest.requested_by`: `CASCADE` → `SET_NULL` (beide Felder `null=True`). Die fachliche Historie bleibt erhalten, nur der Personenbezug fällt weg — analog zum bereits bestehenden Muster bei `Case.created_by`/`Event.created_by`/`Client.created_by`.
- Betroffene Templates (`workitems/detail.html`, `deletion_requests/list.html`, `events/deletion_review.html`, `clients/deletion_review.html`) sichern die Anzeige jetzt explizit gegen `created_by`/`requested_by == None` ab (Fallback „–").

### DAT-02: fehlende Doppel-Hold-Sperre

`LegalHold` hatte keine Unique-Constraint auf `(facility, target_type, target_id)` — anders als `RetentionProposal` (`unique_active_retention_proposal`) und `DeletionRequest` (`unique_pending_deletion_request`). Neu: `unique_active_legal_hold`, eine Partial-Unique mit `condition=Q(dismissed_at__isnull=True)`. Ein bereits abgelaufener, aber noch nicht per `dismissed_at` aufgehobener Hold blockiert bewusst weiterhin einen neuen Hold auf dasselbe Ziel — die Bedingung kann `expires_at` nicht einbeziehen, weil ein Index-Prädikat in Postgres `IMMUTABLE` sein muss und `CURRENT_DATE` das nicht ist.

`RetentionHoldView.post` fängt die neue `IntegrityError` an `unique_active_legal_hold` gezielt ab (Savepoint via `transaction.atomic()`, analog zu `WorkItemCreateView`/`EventCreateView`) — zwei gleichzeitige oder per Doppel-Klick ausgelöste Hold-Anfragen auf dieselbe `pending` Proposal ergeben so einen sauberen `409` statt eines ungefangenen `500`. Jeder andere `IntegrityError` wird weiterhin re-raised.

### DAT-03: `on_delete` wirkt bei Django nur im Python-Collector

Django bildet `on_delete` standardmäßig **nicht** auf DB-Ebene ab — die von Django erzeugten FK-Constraints sind auf Postgres immer `NO ACTION`, unabhängig vom gewählten `on_delete`-Wert (verifiziert u. a. an `LegalHold.dismissed_by`, das schon vor dieser Migration `SET_NULL` war, dessen DB-Constraint aber `NO ACTION` blieb). Der Schutz war damit ausschließlich Python-seitig wirksam — Raw-SQL-Pfade (z. B. ein direktes `DELETE FROM core_user` durch ein Skript oder eine zukünftige Erasure-Routine, die den ORM-Collector umgeht) hätten die `on_delete`-Semantik nicht respektiert. Der Kommentar in `src/tests/test_cases_cascade.py` bezeichnete diesen rein ORM-seitigen Vertrag bisher irreführend als „DB-Level-Cascade-Vertrag" — korrigiert.

Migration 0104 zieht für **genau diese drei** Compliance-FKs echte `ON DELETE`-Constraints per `RunSQL` nach (`RESTRICT` für `LegalHold.created_by`, `SET NULL` für die beiden anderen), inklusive exaktem `reverse_sql`. Eine Umstellung **aller** ~60 FKs im Projekt auf DB-seitige `ON DELETE`-Constraints ist bewusst **nicht** Teil dieser Migration (Konstraint-Namen sind Djangos deterministischer Hash aus Tabelle+Spalte, pro FK zu ermitteln; DEFERRABLE-Semantik variiert) — als separates Follow-up unter #1350 vorgeschlagen.

Pro FK besteht die Migration aus einem DROP-RunSQL (löscht den ursprünglichen `NO ACTION`-Constraint) gefolgt von einem ADD-RunSQL (legt den neuen `RESTRICT`/`SET NULL`-Constraint an). Beim Zurückrollen führt Django die Operationen in umgekehrter Reihenfolge aus; deshalb legt das `reverse_sql` der DROP-Operation den `NO ACTION`-Constraint wieder an und das `reverse_sql` der ADD-Operation löscht den neuen Constraint. Vertauscht bräche ein Unapply (`migrate core 0103`) mit „constraint already exists" ab; beide Richtungen sind per Test abgedeckt.

### Verifikation

- ORM- und DB-Level-Tests: [`src/tests/test_ondelete_hardening.py`](../src/tests/test_ondelete_hardening.py) — deckt sowohl `user.delete()` über den Django-Collector als auch einen Raw-SQL-`DELETE FROM core_user` (der den Collector umgeht) ab.
- Migrations-Reversibilität: [`src/tests/test_migration_0104_reversible.py`](../src/tests/test_migration_0104_reversible.py) — fährt `migrate core 0103` und wieder `migrate core 0104` innerhalb der Test-Transaktion.
- Doppel-Hold-Handling: [`src/tests/test_retention_dashboard.py`](../src/tests/test_retention_dashboard.py) (`TestRetentionHoldView::test_double_submit_does_not_500`).
- Migration: [`src/core/migrations/0104_ondelete_hardening_legalhold_workitem_deletionrequest.py`](../src/core/migrations/0104_ondelete_hardening_legalhold_workitem_deletionrequest.py)

---

## Passkeys/WebAuthn als zweiter Faktor — Glue-Härtung (Issue #1492)

Passkeys werden über [`django-otp-webauthn`](https://github.com/Stormbase/django-otp-webauthn) als **zusätzlicher** zweiter Faktor neben TOTP angeboten (siehe [ADR-032](adr/032-webauthn-passkeys-2fa.md)). Drei bewusst gesetzte Härtungen:

- **Nur zweiter Faktor, kein passwordless:** `OTP_WEBAUTHN_ALLOW_PASSWORDLESS_LOGIN = False`, `WebAuthnBackend` **nicht** in `AUTHENTICATION_BACKENDS`. Die Ceremonies laufen ausschließlich in einer bereits per Passwort authentifizierten Session — ein Passkey allein loggt niemanden ein.
- **`mfa_verified`-Glue ist der kritische Punkt:** Unser `MFAEnforcementMiddleware` gated über das Session-Flag `mfa_verified`; die Bibliothek markiert nur über django-otps `otp_login`. Die Subklassen in [`src/core/views/mfa_webauthn.py`](../src/core/views/mfa_webauthn.py) setzen das Flag **ausschließlich** nach erfolgreicher **Assertion** (`complete_auth`) — dem Besitznachweis eines bereits eingerichteten Faktors. Die **Registrierung** setzt es ausdrücklich **nicht**: sie *verlangt* es bereits vorher (`check_can_register`, 403 ohne verifizierte Session) und auditiert bei 2xx nur (`WEBAUTHN_REGISTERED`). Ein Fehlschlag lässt das Flag unberührt — sonst entstünde ein Verify-Bypass. Positiv- **und** Negativpfad sind getestet ([`src/tests/test_mfa_webauthn.py`](../src/tests/test_mfa_webauthn.py) `TestMfaVerifiedGlue`). `complete_auth` rotiert (#2034) zusätzlich die **Django-Session-ID** (`cycle_key()`, [`session_rotation.py`](../src/core/services/security/session_rotation.py)) — nach `super().complete_auth(device)` und **vor** dem Setzen des Flags, damit der von `otp_login` gestempelte `otp_device_id` mitwandert und ein vor der Assertion entwendetes `sessionid`-Cookie die verifizierte Sitzung nicht erbt. Gemeint ist Djangos Session-ID, nicht der Offline-Krypto-„Session-Key“.
- **Passkey nur NEBEN TOTP (Downgrade-Schutz + Recovery-Anker):** Die Registrierung verlangt ein bestätigtes TOTP-Gerät (`check_can_register`). Damit existieren immer Backup-Codes (an der TOTP-Einrichtung provisioniert), kein „passkey-only"-Lockout ist möglich, und das Entfernen eines Passkeys (sudo-pflichtig, `MFAPasskeyDeleteView`) lässt stets TOTP + Backup-Codes zurück.

**Umgebungsbindung:** Passkeys sind an die RP-ID (Registrable Domain) gebunden. RP-ID/Origins werden pro Umgebung gesetzt (dev/test/e2e → `localhost`; prod/devlive/demo → aus `ALLOWED_HOSTS` abgeleitet). Eine spätere RP-ID-Änderung entwertet bestehende Credentials — Nutzer müssten neu registrieren.

### Verifikation

- Glue + Guard + Sudo-Entfernen + Prädikat: [`src/tests/test_mfa_webauthn.py`](../src/tests/test_mfa_webauthn.py)
- AuthZ-Deklaration der neuen Endpoints (Vollständigkeits-Gate): [`src/tests/_authz_expectations.py`](../src/tests/_authz_expectations.py)
- E2E gegen Chrome-Virtual-Authenticator: [`src/tests/e2e/test_mfa_webauthn.py`](../src/tests/e2e/test_mfa_webauthn.py)

---

## Passkey-Fehlversuche und Mutationslimits (#2529)

Die Passkey-Abschlussanfrage wird vor dem JSON-Parser auf 60/h je Benutzer begrenzt. Kryptographische Fehler und beim Server ankommende, durch fehlerhaftes JSON unterbrochene Abschlussanfragen zählen in dieses Limit. Bei einer unterbrochenen begonnenen Verifikation wird die Challenge verbraucht, kein MFA-/OTP-Sitzungsmarker gesetzt, und `WEBAUTHN_FAILED` enthält feste Ereignis-/Fehlercodes ohne Assertion. Ein rein lokaler Browserabbruch ohne Abschlussanfrage ist für den Server nicht beobachtbar.

Der Endpunkt-Guard `TestRateLimitOnAllMutations` durchläuft zur Laufzeit rekursiv den aktiven URL-Resolver und prüft die registrierten Callbacks für POST, PUT, PATCH und DELETE, einschließlich Admin-/WebAuthn-Routen, geerbten Mutationshandlern, Formularviews und Funktionen mit `require_POST`/`require_http_methods`. Er erkennt die üblichen Deklarationsformen (geerbte Handler, Formularviews, Methodenfilter und Projekt-Limit-Wrapper) und prüft dabei die effektiven Klassen-, Handler- und Methodenfilterbindungen sowie bekannte Django-Dispatch-Weiterleitungen über die MRO sowie das methodentreue Demo-Gate. Ein Limit zählt nur mit der prüfbaren Markierung des Projekt-Wrappers `core.utils.ratelimit`, konkreter endlicher Rate, passender HTTP-Methode und `block=True`; kopierte Attribute und übersprungene Eltern-Dispatches liefern keinen Nachweis. In den erkannten Deklarationsformen führen eigene Dispatches, Instanzbindungen oder dynamische Raten ohne nachgewiesenen äußeren Limiter oder Methodenfilter zum Fehler. Die Ausnahmeliste prüft URL-Name, POST-Methode und bekannte Zielklasse bzw. Zielfunktion: `logout` (`CustomLogoutView.post`, Sitzungsende einschließlich Schnell-verlassen), `admin:logout` (`AdminSite.logout`, Sitzungsende in der Verwaltung) und `core:client_rename_warning` (`ClientRenameWarningView.post`, lesende Warnprobe außerhalb der Update-Quote). Nicht erkannt werden u. a. eigene Prüf-Hooks von Auth-Mixins (`LoginRequiredMixin`, `PermissionRequiredMixin`, `UserPassesTestMixin`) vor der Drosselung und per `as_view(...)` ersetzte Dispatches unter dem URL-Namen `logout`; die Klassenausnahme belegt dort nicht den tatsächlich ausgeführten Dispatch. Das ist ein begrenzter Bindungs- und Konfigurationsnachweis im importierten Stand, keine Lastmessung oder semantische Prüfung von GET-Handlern und nachträglichen Bindungsänderungen. Konfigurations-POSTs unter `views/config/` und Passwortänderungen verwenden `RATELIMIT_MUTATION` (60/h je Benutzer); vorhandene Limits an eigenen Dispatches greifen vor deren Verarbeitung. Anonyme Passwort-/Einladungseinrichtung: 5/min je IP; Login-, Passwort- und Recovery-Formularviews teilen ihre Quoten zwischen schreibenden Methoden. Beide Passkey-Beginnpfade verwenden 60/h vor dem Parser (Registrierung je Benutzer, Anmeldung je Benutzer oder IP). Sprachwahl: 60/h je Benutzer oder IP. Verwaltungsrouten: 60/h je URL und Benutzer oder IP; Admin-Login: gemeinsame Schreibquote von 5/min je IP und 10/h je normalisiertem Benutzernamen. Caddy bleibt ohne Edge-Rate-Limit; die Anwendung liefert bei Überschreitung HTTP 429.

---

## MFA-Backup-Codes: Speicher- und Migrationsvertrag (Issues #2294, #2287, #2327)

**Status:** umgesetzt. Der Abschnitt hält fest, was in der Datenbank wirklich steht, welche Eingaben überhaupt als Geheimnis in Frage kommen und warum kein Wert aus der Datenbank als Code gilt.

### Was gespeichert wird

[`generate_backup_codes`](../src/core/services/security/mfa.py) erzeugt zehn Codes aus `secrets.token_urlsafe(16)` (22 Zeichen, 128 Bit Zufall). `StaticToken.token` enthält nur `base64url(HMAC-SHA256(server_key, code))[:16]` (96 Bit); der zweckgebundene Schlüssel wird aus `SECRET_KEY` abgeleitet. Ohne diesen Serverschlüssel ist ein Datenbank-Digest kein Offline-Prüforakel. Die vorübergehende Codeanzeige wird Fernet-verschlüsselt in der Sitzung gehalten.

### Gültigkeit, Wiederherstellung und Gerätewechsel

`verify_backup_code` akzeptiert nur das neue 22-Zeichen-Eingabeformat und vergleicht dessen HMAC, nie den gespeicherten Wert direkt. Einmalverbrauch bleibt atomar. Das schwache Altformat `xxxx-xxxx` und alle früheren SHA-Digests (auch für moderne Codes) sind ungültig; die historische Migration `0181` erzeugt keine heute gültigen Codes. Es gibt keine Altbestands-Migration. Vor Geräteverlust neue Codes mit gültigem TOTP erzeugen; ohne nutzbaren Faktor gilt der Admin-MFA-Reset. Auch eine `SECRET_KEY`-Rotation entwertet die Codes.

Anonyme Fehlversuche auf `/account/recovery/backup-code/` erhöhen die Gerätedrossel nicht. Der Pfad begrenzt mit 5/min je IP und 10/h je normalisiertem Benutzernamen; das Benutzerlimit bleibt bei wechselnden IPs wirksam. Die authentifizierte Gerätedrossel bleibt erhalten. Ein eingelöster Code entsperrt das Konto bzw. bestätigt den zweiten Faktor, ersetzt aber kein TOTP-Gerät.

**Neues Gerät einrichten** unter `/mfa/settings/` führt nach bereits bestätigter MFA und Sudo mit Passwort plus TOTP oder Backup-Code auf `/mfa/change-device/`. Erst die Quittung der neuen Backup-Codes entfernt die bisher bestätigten TOTP-Geräte; Passkeys bleiben. Der Abschluss schreibt `MFA_ENABLED` mit der Anzahl ersetzter TOTP-Geräte. Nach Sudo führt die freiwillige Deaktivierung auf eine GET-Bestätigungsseite, ihr POST deaktiviert den Faktor. Gerätewechsel und freiwillige Deaktivierung beenden andere angemeldete Sitzungen des Kontos nicht; bei Verlust oder Diebstahl eines Geräts mit möglicherweise offener Sitzung den Admin-Reset verwenden (Dienstanweisung der Einrichtung).

### Verifikation

- [Backup-Code-Härtung und anonyme Recovery](../src/tests/test_backup_code_hardening.py)
- [Gerätewechsel und Deaktivierungsbestätigung](../src/tests/test_mfa_device_change_flow.py)
- [Einmalverbrauch und Parallelverbrauch](../src/tests/test_mfa_backup_codes.py)
- [Einrichtungs-Gate und Gerätebindung](../src/tests/test_mfa_setup_reauth_guard.py)

---

## Attachment-Pipeline owner-generisch: direkte RLS-Policy statt Join (Issue #1738)

**Status:** umgesetzt ([ADR-039](adr/039-owner-generische-dateianhaenge.md), Migrationen `0129`–`0131`). Die Facility einer Anhang-Zeile sichert zusätzlich ein zusammengesetzter Fremdschlüssel aus Migration `0133` (#1780, siehe Restrisiko).

### Beobachtung

Ausgangspunkt war ein Modell, in dem die gesamte Attachment-Pipeline am Ereignis hing: `EventAttachment` hatte einen NOT-NULL-FK auf `Event`, **keine eigene `facility_id`**, und seine RLS-Policy lief als Join über `core_event`. Zwei Konsequenzen:

- **Policy-lose Zeilen waren strukturell möglich.** Eine Anhang-Zeile ohne (oder mit einem für die Session unsichtbaren) Ereignis fiel unter der Join-Policy durch *kein* `USING`-Prädikat — sie war nicht etwa gesperrt, sondern schlicht ungeschützt gegenüber der Frage, zu welcher Einrichtung sie gehört.
- **Datei-Felder an Personen- und Fall-Typen waren gesperrt** ([ADR-037](adr/037-konfigurationspakete-dynamische-felder.md) §9 „E8"), weil ein Upload dort keinen Speicherort gehabt hätte.

### Maßnahme

`EventAttachment` → `Attachment` mit **eigener `facility_id`** (NOT NULL) und **genau einem** Owner (`event`/`client`/`case`, `CheckConstraint attachment_exactly_one_owner`). Die Policy auf `core_attachment` vergleicht seit Migration `0131` **direkt** `facility_id = current_setting('app.current_facility_id', true)` — inklusive `is_super_admin`-OR-Branch, wie alle direkten Policies (#867). Das Rename (`0129`) fasst Policies/RLS-Flags/GRANTs nicht an: sie hängen an der Tabellen-OID und überleben es.

Zwei Sicherheitsflächen kommen dazu:

1. **Sichtbarkeits-Gate der Downloads (5 Stufen), fail-closed.** (1) Owner facility-gescopt + nicht im Papierkorb → 404; (2) Doc-Type-Sensitivity → **404** (nicht 403: die Zusatzfelder-Sektion ist für Unterberechtigte komplett abwesend, ein 403 bestätigte die Existenz eines Anhangs an einem Typ, den die Rolle nicht sehen darf; `document_type is None` ⇒ ebenfalls 404); (3) Anhang am Owner und nicht soft-deletet → 404; (4) Feld-Sensitivity → **403**; (5) Datei auf Disk → 404 + `logger.error`. **Rollen-Gefälle:** der Fall-Download verlangt Fachkraft-Rechte (`StaffRequiredMixin`) — Fälle sind für die Assistenz unsichtbar, ein Assistenz-Download wäre eine Gate-Umgehung; der Personen-Download steht ab Assistenz offen. Alle drei Routen teilen **einen** Ratelimit-Eimer (`attachment_download`): drei getrennte Eimer wären dreifaches Budget für dieselbe Fläche.
2. **Die zentrale Datei-Übersicht (`/attachments/`) scopt direkt über `Attachment.facility`.** Sie filtert Owner-Papierkorb, Doc-Type-Sensitivity (über eine `Coalesce`-Annotation über alle drei Owner, mit einem Sentinel für „Owner ohne Typ" ⇒ unsichtbar) und Feld-Sensitivity **vor** dem 200er-Slice, damit unsichtbare Zeilen keine sichtbaren aus dem Fenster drängen; fall-eigene Zeilen sind für die Assistenz komplett ausgeblendet.

### Restrisiko (akzeptiert)

**Die Facility einer Anhang-Zeile war zunächst Service-Layer-Konvention, kein DB-Constraint.** `facility` wird immer aus `owner.facility` abgeleitet und nie als freier Parameter entgegengenommen; ein Unit-Test pinnt `attachment.facility_id == owner.facility_id`. Ein Service-Bug hätte bis dahin eine Zeile mit fremder Facility anlegen können; sie wäre danach für die *richtige* Einrichtung unsichtbar und für die falsche sichtbar gewesen.

**Geschlossen seit Migration `0133`** (#1780): `core_event`/`core_client`/`core_case` tragen einen UNIQUE-Begleitindex `(id, facility_id)`, `core_attachment` je Owner-Sorte einen zusammengesetzten FK `(owner_fk, facility_id)` → Zieltabelle `(id, facility_id)`. Die divergente Kombination ist damit auf DB-Ebene unmöglich — unabhängig vom Aufrufweg (ORM, `objects.update()`, Roh-SQL). Zwei Eigenschaften sind tragend: `MATCH SIMPLE` (Postgres-Default) lässt die beiden ungenutzten Owner-Spalten je Zeile passieren, und `DEFERRABLE INITIALLY DEFERRED` (Schema-Konvention) verschiebt die Prüfung ans Transaktionsende — eine Verletzung schlägt beim COMMIT zu, nicht schon beim einzelnen `UPDATE`. Der roher-INSERT-Pfad war schon vorher abgedeckt (die `USING`-Klausel wirkt als implizites `WITH CHECK`); der Composite-FK deckt zusätzlich das nachträgliche Umhängen einer bestehenden Zeile ab.

**Audit-Zeilen mit `target_type="EventAttachment"`.** Das Log ist HMAC-verkettet und append-only — es wird **nie** migriert. Der Schreibpfad trägt `"Attachment"` und ein einheitliches Detail-Schema (`owner_type`/`owner_id`). Betreiberauswertungen müssen deshalb **beide** Strings und beide Detail-Schemata kennen; der Hinweis steht im [Admin-Guide § 7.4](admin-guide.md#74-audit-log).

**Der Rollback der Migrationskette wird mit dem ersten personen-/fall-eigenen Anhang unmöglich** (Reverse-Guards in `0130` und `0131`, bewusst fail-loud statt policy-verdeckter Zeilen). Betriebsseite: [Ops-Runbook § 2.2](ops-runbook.md#22-migration-zurueckrollen).

### Verifikation

- Cross-Tenant-Isolation je Richtung + roher Cross-Facility-`INSERT` gegen die Policy: [`test_rls_functional.py`](../src/tests/test_rls_functional.py) (`TestAttachmentRLSIsolation`), Tabellen-Inventar in [`test_rls.py`](../src/tests/test_rls.py).
- Migrationskette inkl. Reverse-Guard-Position: [`test_0129_rename_eventattachment_to_attachment.py`](../src/tests/migrations/test_0129_rename_eventattachment_to_attachment.py), [`test_0130_attachment_owner_facility.py`](../src/tests/migrations/test_0130_attachment_owner_facility.py), [`test_0131_rls_attachment.py`](../src/tests/migrations/test_0131_rls_attachment.py).
- Composite-FK-Härtung (Schema, NULL-Semantik `MATCH SIMPLE`, Preflight-Guard, Reverse-Vollständigkeit): [`test_0133_attachment_composite_fk.py`](../src/tests/migrations/test_0133_attachment_composite_fk.py).
- Download-Gate je Stufe und je Owner + IDOR-Proben: [`_authz_expectations.py`](../src/tests/_authz_expectations.py) (`client_attachment_download`, `case_attachment_download`), [`test_attachment_download_owner_routes.py`](../src/tests/test_attachment_download_owner_routes.py), [`test_compliance_attachment_gate.py`](../src/tests/test_compliance_attachment_gate.py).
- Übersicht (Sensitivity vor Slice, Assistenz ohne Fall-Zeilen, gefiltertes Dropdown): [`test_attachment_overview.py`](../src/tests/test_attachment_overview.py).
- Threat-Model: [`threat-model.md` § 2 Assets](threat-model.md#2-assets).

---

## Session-Timeout: Inaktivitäts-Fenster statt Absolut-Limit (Issue #1858)

**Warum eine eigene Middleware:** Mit `SESSION_SAVE_EVERY_REQUEST = False` (Refs #733) schreibt im Request-Pfad nichts die Session, und Django verlängert das Fenster nur bei Session-Writes; ohne Zusatz endete jede Sitzung **absolut** `session_timeout_minutes` nach dem Login. `SessionIdleRefreshMiddleware` ([`src/core/middleware/session_idle.py`](../src/core/middleware/session_idle.py)) liefert deshalb das dokumentierte **Inaktivitäts**-Fenster: authentifizierte Aktivität stempelt einen Erneuerungs-Zeitstempel, gedrosselt auf höchstens einen Session-Write je 10 % der konfigurierten Laufzeit (kein Rückfall in die #733-Write-Amplifikation). Abgemeldet wird nach 90–100 % der konfigurierten Inaktivitätszeit.

**Nicht als Aktivität zählen:** Offline-Queue-Replays (`X-Offline-Replay`) und die gesamte Offline-Sync-API (`/api/v1/offline/…` — Widerrufsliste, CSRF-Refresh, Bundle-Revalidierung). Die Sync-Sequenz feuert beim `online`-Event ohne Nutzerinteraktion; ohne den Ausschluss hielte ein Gerät mit flappender Verbindung seine Sitzung unbegrenzt offen.

**Bewusst eingegangene Restrisiken:**

- **Pre-MFA-Sessions** (Passwort passiert, `mfa_verified=False`) werden mitverlängert — ein Gate auf das Flag ist nicht möglich, weil es für Nutzer ohne MFA-Gerät dauerhaft `False` bleibt. Delta gering (Passwort-Besitzer könnten sich jederzeit neu anmelden).
- **Parallele Session-Writes:** bisher rein lesende Requests schreiben jetzt gelegentlich die Session (Last-Write-Wins-Semantik von `django_session`, winziges Race-Fenster bei parallelem Logout). Django-inhärent, durch die 10-%-Drosselung selten.

Das Restrisiko **kein absolutes Sitzungs-Maximum** ist mit #1868 aufgelöst (nächster Abschnitt). Damit ist auch der **Revocation-Scan-Rest** jenseits `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT` wieder nach oben begrenzt: eine aktiv weitergenutzte Session überdauert den Rechteentzug höchstens bis zu ihrem absoluten Ende (Kommentare in [`credentials.py`](../src/core/services/security/credentials.py); praktisch relevant erst bei >10 000 aktiven Sessions).

### Verifikation

- Erneuerung/Drosselung/Ausnahmen: [`test_middleware_session_idle.py`](../src/tests/test_middleware_session_idle.py) (Unit + Integration über den vollen Middleware-Stack).
- Handtest: `ENT-AUTH-07` in [manual-test-matrix-b.md](testing/manual-test-matrix-b.md).

---

## Absolutes Sitzungs-Maximum ab Login (Issue #1868)

**Warum überhaupt:** Ein Inaktivitäts-Fenster hat keine Obergrenze. Eine Sitzung lebt, solange häufiger als `session_timeout_minutes` irgendein authentifizierter Request kommt — ein entwendetes oder auf einem vergessenen Gerät zurückgelassenes Session-Cookie damit potenziell unbegrenzt. Das absolute Maximum zieht eine zweite, aktivitätsunabhängige Grenze ein.

**Konfiguration:** `Settings.session_absolute_hours` je Einrichtung, Default **12 Stunden**, zulässige Spanne 1–168 h (eine Woche). Die Cross-Field-Regel **Maximum > Inaktivitäts-Fenster** steht doppelt: als `Settings.clean()` (Admin-Formular, feldgenaue Meldung) und als DB-`CheckConstraint` `settings_session_absolute_cap` (Direct-Insert aus Shell, Fixture oder Datenmigration). Ohne sie wäre jede Sitzung schon absolut abgelaufen, bevor die Inaktivität greifen kann — eine stille Fehlkonfiguration, die im Betrieb wie ein Bug aussieht. Migration `0148` hebt das Maximum an, wo ein gespeichertes Inaktivitäts-Fenster es verletzen würde — ein Deploy soll keine vorhandene Einstellung verschärfen. **Eine getestete Ausnahme:** Liegt ein gespeichertes Fenster jenseits des Feld-Maximums von 168 h, lässt sich der Constraint nur anlegen, indem die Migration `session_timeout_minutes` auf 10 079 Minuten klemmt; das protokolliert sie als `WARNING` im Deploy-Log ([`0148_session_absolute_hours.py`](../src/core/migrations/0148_session_absolute_hours.py)).

**Mechanik:** Der Login stempelt die Deadline **einmal** in die Session (`ABSOLUTE_DEADLINE_KEY = "_session_absolute_deadline"`, Unix-Zeitstempel = Login + Cap) — im View, wo `facility.settings` ohnehin geladen ist, also ohne Extra-Query. Nutzer ohne Einrichtung (`super_admin`) und Einrichtungen ohne Settings-Zeile bekommen den Default; „kein Cap" ist kein erreichbarer Zustand. `SessionIdleRefreshMiddleware` prüft die Deadline **vor** der Erneuerungs-Logik und beendet die Sitzung per `django.contrib.auth.logout()` — Session-Zeile geflusht, `LOGOUT`-AuditLog über das `user_logged_out`-Signal, dieselbe Spur wie ein Abmelde-Klick. **Die Erneuerung stempelt die Deadline nie nach**, sonst wäre das Maximum nur ein zweites Sliding Window. Eine Änderung des Settings-Werts wirkt deshalb erst ab dem nächsten Login (so auch im Hilfetext des Feldes gesagt).

**Zwei bewusste Entwurfsentscheidungen:**

- **Das Cap kennt die Hintergrund-Sync-Ausnahmen des Inaktivitäts-Fensters nicht.** `X-Offline-Replay` und `/api/v1/offline/…` dürfen das Fenster nicht *verlängern* — aber ein abgelaufenes Maximum beendet die Sitzung auch dort. Sonst hätte ein dauersyncendes Gerät ein Schlupfloch am Cap vorbei.
- **Die Middleware antwortet nicht selbst mit einem Redirect.** Nach dem `logout()` läuft der Request anonym weiter und die getroffene View liefert ihren eigenen `LoginRequiredMixin`-302. Nur so kommt der Standardpfad zum Zug: [`HtmxSessionMiddleware`](../src/core/middleware/htmx_session.py) übersetzt ihn für HTMX in `200 + HX-Redirect` und lässt ihn für ein Offline-Replay als rohen 302 durch — daran hält der Queue-Klassifikator an, **ohne** die Zeile zu löschen ([ADR-030](adr/030-offline-sync-orchestration-replay-contract.md) §3, Refs #1419). Ein eigener Redirect aus der Middleware würde beides umgehen und im schlimmsten Fall einen ungesyncten Write still verwerfen.

**Sessions ohne den Schlüssel** bekommen beim nächsten Request einmalig den **Default**-Cap ab jetzt gestempelt, statt sofort ausgeloggt zu werden — ein Deploy soll keine Massenabmeldung auslösen, und der Facility-Wert wäre hier nur mit einem DB-Roundtrip pro Request zu haben. Betrifft zwei Fälle: **Sitzungen ohne Stempel** (angelegt von einem Anwendungsstand ohne Sitzungs-Maximum) und Anmeldungen über das **Django-Admin-Login** (`/admin-mgmt/login/`), das an `CustomLoginView` vorbeigeht. Der zweite Fall ist keine neue Lücke: dieser Pfad setzt schon vorher `session_timeout_minutes` nicht (dort greift `SESSION_COOKIE_AGE`) — der Cap wirkt dort also mit dem Default statt mit dem Facility-Wert, aber er wirkt.

**Was das Cap nicht leistet:** Es begrenzt die Server-Session, nicht das Zeitfenster eines Angreifers *innerhalb* dieser Session. Wer ein Cookie stiehlt, hat bis zur Deadline vollen Zugriff; die Grenze verhindert nur den unbegrenzten Dauerzugriff. Kürzere absolute Fenster sind je Einrichtung konfigurierbar, gegen den Preis regelmäßiger Neuanmeldungen mitten in der Arbeit.

### Verifikation

- Feld, Validator-Grenzen, `clean()`-Regel, DB-Constraint: [`test_models.py`](../src/tests/test_models.py) (`test_settings_session_absolute_*`, `test_settings_clean_rejects_cap_below_idle_timeout`).
- Login-Stempel (Facility-Wert, Default ohne Settings-Zeile, `super_admin`): [`test_views_auth_branches.py`](../src/tests/test_views_auth_branches.py) (`TestCustomLoginViewAbsoluteDeadline`).
- Durchsetzung inkl. Sync-Pfaden, HTMX-`HX-Redirect`, rohem 302 für Replays, Grace-Stempel: [`test_middleware_session_idle.py`](../src/tests/test_middleware_session_idle.py) (`TestSessionAbsoluteCap`, `TestSessionAbsoluteCapIntegration`).
- Migrations-Anpassung des Bestands (Anheben, 168-h-Klemmung): [`test_0148_session_absolute_hours.py`](../src/tests/migrations/test_0148_session_absolute_hours.py).
- Handtest: `ENT-AUTH-11` in [manual-test-matrix-b.md](testing/manual-test-matrix-b.md) (Abgrenzung zum Inaktivitäts-Fenster: `ENT-AUTH-07`).

---

## `lastActivity`-Stempel in `anlaufstelle-crypto.meta` nach dem Abmelden (Issue #1946)

**Status:** Akzeptierter Restbestand — kein Defekt. Der Eintrag ist **kein** Überbleibsel des Logout-Wipes (der räumt ihn nachweislich ab), sondern wird von der **Anmeldeseite** neu angelegt, auf der der Browser nach dem Abmelden landet.

### Beobachtung

Der Security-RC-Lauf 2026-08-18 (Mobile/PWA, Offline-Privacy-Failure-Modes; Laufprotokoll dev-only) hat nach dem Abmelden in der IndexedDB `anlaufstelle-crypto`, Store `meta`, einen einzelnen Eintrag `{key: "lastActivity", ts: <epoch ms>}` vorgefunden — bei ansonsten geleerten Offline-Beständen und entferntem `sessionKey`. Daraus wurde in #1946 geschlossen, der Aktivitäts-Stempel überlebe den Logout-Wipe, und als Maßnahme vorgeschlagen, ihn „in den Logout-Wipe aufzunehmen".

### Am Code widerlegt: der Wipe räumt den Stempel bereits ab

Diese Maßnahme wäre ein No-op (#1065): Schon jetzt löscht `clearSessionKey()` in [`crypto.js`](../src/static/js/crypto.js) den Stempel **zusammen** mit dem Schlüssel („Drop the activity stamp with the key"), und `wipeOfflineState()` — der gemeinsame Wipe-Pfad von Logout und Idle-Timeout — beginnt mit genau diesem Aufruf. Der Logout-Hook in [`sw-register.js`](../src/static/js/sw-register.js) ruft nichts anderes auf: er hält den nativen `POST /logout/` an, lässt `crypto_session.wipeOfflineState()` durchlaufen (harter 3-s-Deckel) und schickt erst danach ab (#1476 R2).

### Die tatsächliche Quelle: der Boot-Zweig des Idle-Gates

`crypto.js` wird **nicht nur** von `base.html` geladen, sondern auch von [`auth/login.html`](../src/templates/auth/login.html), [`auth/password_change.html`](../src/templates/auth/password_change.html) und der öffentlichen [`offline.html`](../src/templates/offline.html) — die Idle-Wipe-Mechanik muss auch auf abgemeldeten Seiten greifen können. Beim Laden startet das Modul eine eager `_loadKey()`-Hydrierung, die vor dem Rehydrieren den Idle-Gate `_enforceIdleWipeLocked()` fährt. Findet der **keinen** Stempel vor, legt er einen neuen an („No stamp yet … start the idle window now instead of wiping a possibly active session").

Nach dem Abmelden ist genau das die Lage: der Wipe hat den Store geleert, der `POST /logout/` navigiert auf `/login/`, dort läuft `crypto.js` erneut, findet einen leeren `meta`-Store vor — und stempelt. Der beobachtete Zeitstempel gehört damit **nicht** zur letzten Nutzung der App, sondern zum Aufruf der Anmeldeseite unmittelbar danach. Denselben Eintrag erzeugt der bloße Besuch von `/login/` oder `/offline/` durch jemanden, der sich nie angemeldet hat.

Der zweite denkbare Verursacher — `deriveSessionKey()` stempelt mit `_touchActivity(true)`, **bevor** es den Schlüssel ablegt (#1065-Invariante „Stamp BEFORE storing", damit ein veralteter Vor-Login-Stempel den neuen Schlüssel nicht sofort in einen Idle-Wipe reißt) — läuft zwar ebenfalls auf `login.html`, aber erst beim **Anmelden**. Für den nach dem **Abmelden** beobachteten Eintrag scheidet er aus.

### Bewertung

- **PII-frei.** Der Eintrag besteht aus dem Literal `lastActivity` und einer Epoch-Millisekunde. Kein Nutzer-, Einrichtungs- oder Klientenbezug; er unterscheidet nicht einmal, *wer* das Gerät benutzt hat.
- **Aussagekraft schwächer als angenommen.** Er belegt nicht „wann zuletzt mit der App gearbeitet wurde", sondern „wann zuletzt eine Seite dieser Herkunft geladen wurde, die `crypto.js` einbindet" — einschließlich `/login/` und `/offline/` ohne jede Anmeldung. Auf einem geteilten Diensthandy bleibt das eine schwache Spur, aber eine, die der Browser mit `history`, HTTP-Cache und dem ohnehin bewusst erhaltenen Precache (`CACHE_NAME` in `sw.js`, nur App-Shell-Assets) mehrfach redundant selbst führt.
- **Selbstheilend im Zweck.** Der Stempel wird bei jedem Wipe und jedem Passwortwechsel gelöscht und beim nächsten Seitenaufruf neu gesetzt; er wächst nicht und akkumuliert keine Historie.

### Was bewusst nicht umgesetzt wird

Ein **Stempel-Gate** — den Boot-Zweig nur dann stempeln lassen, wenn tatsächlich ein `sessionKey` in der IndexedDB liegt — wäre technisch möglich und würde die Spur auf abgemeldeten Seiten vollständig beseitigen. Er wird hier **nicht** gebaut:

- Der Boot-Stempel ist die Schutzhälfte des Idle-Gates gegen das Gegenteil: ohne ihn hätte ein Bestand ohne Stempel (Alt-Installation vor #1065, erster Lauf nach einem Cache-Wechsel) kein definiertes Idle-Fenster. Jede Änderung an dieser Stelle berührt einen sicherheitskritischen, mehrfach nachgehärteten Pfad (#1065, #1324, #1351/#1383, #1393, #1493).
- Der Gewinn steht dazu in keinem Verhältnis: eine Epoch-Millisekunde ohne Personenbezug, die dieselbe Information trägt wie ein Eintrag in der Browser-History.

Der **Precache** (`CACHE_NAME` in `sw.js`) bleibt aus demselben Ticket-Entscheid ebenfalls bestehen — er enthält ausschließlich App-Shell-Assets und Offline-Hüllen und ist die Grundlage des Offline-Starts.

### Trigger für Re-Evaluation

- `crypto.js` beginnt, im Stempel-Record mehr als den Zeitstempel abzulegen (Nutzer-, Einrichtungs- oder Sitzungsbezug) — dann ist die PII-Freiheit nicht mehr gegeben.
- Eine Betreiberin fordert für Kiosk-/geteilte Geräte einen nachweislich spurenfreien Abmeldezustand; dann ist das oben beschriebene Stempel-Gate die Maßnahme der Wahl.
- Der Idle-Gate wird ohnehin umgebaut — dann das Gate gleich mitziehen, statt den Pfad zweimal anzufassen.

### Verifikation

- [`tests/js/logout-wipe.test.mjs`](../tests/js/logout-wipe.test.mjs) § „lastActivity nach dem Logout (Refs #1946)" — drei Pins: (1) `wipeOfflineState()` entfernt Schlüssel **und** Stempel (widerlegt die Ticket-Prämisse), (2) der Boot einer schlüssellosen Seite legt den Stempel neu an, (3) die vollständige RC-Sequenz Wipe → Boot der Anmeldeseite ergibt einen Stempel, der **jünger** als der Wipe ist.
- Handtest: Eintrag zu #1946 in der Release-Hand-Check-Liste (dev-only) — Sichtprüfung des `meta`-Stores vor und nach dem Abmelden.

---

## Historien-Diff: Entschlüsselung für Berechtigte (Issue #1961)

### Ausgangslage

`EventHistory.data_before` und `data_after` enthalten für verschlüsselte dynamische Felder Fernet-Hüllen. [`history_diff.py`](../src/core/services/events/history_diff.py) maskierte solche Werte bislang rollenunabhängig als `●●●●●`, obwohl die vorgeschaltete Sensibilitätsprüfung schon zwischen erlaubten und gesperrten Feldern unterscheidet. Das machte den fachlichen Vorher-/Nachher-Vergleich gerade für HIGH-Felder wertlos. Außerdem erzeugt Fernet bei jedem Speichern einen neuen Token: Ein unveränderter Klarwert erschien deshalb als Änderung, weil nur die Ciphertexte verglichen wurden.

### Entscheidung und Freigabe

Bei der Anzeige gilt (#1961) die feste Vorrangregel `restricted > Datei-Marker > berechtigt ? Klartext : ●●●●● > Rohwert`. `may_decrypt` ist nur wahr, wenn ein Nutzerkontext vorliegt und [`user_can_see_field`](../src/core/services/compliance/sensitivity.py) das Feld für dessen Rolle freigibt. `user=None` bleibt damit fail-closed; eine fehlende Schlüsselkonfiguration wird weiterhin laut als `EncryptionKeyMissing` propagiert. Datei-Marker werden vor jedem Krypto-Aufruf erkannt und ausschließlich als `[Anhang]` angezeigt. Bei erlaubten Update-Feldern vergleicht der Service die entschlüsselten Werte, sodass reines Fernet-Rauschen keine Diff-Zeile mehr erzeugt.

Die Maintainer-Freigabe vom 23.08.2026 umfasst ausdrücklich zwei Folgen: Art.-9-Klartext erscheint damit in einem Oberflächenkontext, der ihn zuvor maskierte, und die Änderung wirkt ohne Stichtag auf **alle** vorhandenen Historieneinträge. Die Begründung ist die bereits bestehende Rollenparität: Dieselben berechtigten Rollen lesen denselben Wert in Ereignis-Detail und -Formular; der Art.-15-Export entschlüsselte historische Werte schon vorher ausdrücklich, weil ein Fernet-Token keine Auskunft ist ([`client/export.py`](../src/core/services/client/export.py)). Die alte Historien-Maskierung widersprach dieser Grenze, ohne eine eigene Autorisierungsregel zu bilden.

### Grenzen

- Ein berechtigter Nutzer sieht auch **frühere** Angaben, die später berichtigt wurden. Das ist Zweck der Änderungshistorie und muss in Rollenvergabe und Datenschutz-Folgenabschätzung berücksichtigt werden.
- Ein redigierter Soft-Delete speichert keine alten Werte; berechtigte Rollen sehen dort `– (gelöscht)`, nicht rekonstruierte Inhalte.
- Restricted- und No-User-Pfade entschlüsseln auch zum Vergleich nicht. Weil ihre Ciphertexte roh verglichen werden, kann dort weiter eine maskierte Diff-Zeile für einen unveränderten Wert erscheinen; Vertraulichkeit hat in diesen Pfaden Vorrang vor kosmetischer Rauschunterdrückung.
- Die Detailseite lädt weiterhin die vollständige Historie. Die zusätzliche Entschlüsselung ist linear in Einträge × verschlüsselte Felder; bei auffälligen realen Verläufen muss die Historie begrenzt oder lazy geladen werden.

### Verifikation

- [`test_history_diffs.py`](../src/tests/test_history_diffs.py) — vollständige Rollen-/Sensibilitätsmatrix, fail-closed ohne Nutzer, Datei-Marker, korrupter Token, fehlender Schlüssel, redigierter Delete und Fernet-Rauschunterdrückung.
- Release-Handtest zu #1961 in `docs/dev/hand-checks.md` (dev-only) — sichtbare Rollen-Gegenprobe auf der Ereignis-Detailseite.
- Browser-Abdeckung (#1983): `TestEventHistoryRoleVisibility` in [`e2e/test_zeitstrom_events.py`](../src/tests/e2e/test_zeitstrom_events.py).

---

## `Activity.summary` führt Pseudonym und Titel im Klartext (Issue #1944)

**Status: behoben (#1972).** Das Modell besitzt keine `summary`-Spalte mehr. Der Zeitstrahl zeigt Pseudonyme und Titel weiterhin, liest sie aber erst zur Anzeigezeit aus dem referenzierten Ziel.

### Speicher- und Rendering-Vertrag

Eine `Activity` speichert nur Einrichtung, handelnde Person, Verb, Zeitpunkt, die vorhandene Generic-FK aus `target_type`/`target_id` und `context`. Der Kontext hat das Schema `{"schema": 1, "action": "…"}`; `action` kommt aus dem geschlossenen Vokabular `Activity.Action`. Pseudonyme, Aufgaben-, Fall- und Meilenstein-Titel sowie Namen von Dokumentationstypen sind darin unzulässig. [`log_activity`](../src/core/services/dashboard/activity.py) nimmt deshalb keinen fertigen Anzeigetext mehr an.

[`render_activity_summary`](../src/core/services/dashboard/activity.py) übersetzt den Aktionscode und liest den aktuellen Zielwert. Dadurch ändern sich sichtbare Sätze nach einer Umbenennung mit dem Ziel, ohne dass eine historische Klartextkopie zurückbleibt. Fehlt das Ziel — etwa nach dem Löschen eines Meilensteins — entsteht ein generischer Satz wie „Meilenstein gelöscht" oder „Aufgabe erledigt". Aktivitätskarte, Zeitstrom-Revision und Art.-15-Export benutzen denselben Renderer; die Sätze sind damit erstmals vollständig übersetzbar.

### Migration `0163` und Löschpfade

Migration [`0163_activity_display_context`](../src/core/migrations/0163_activity_display_context.py) redigiert **alle** Freitexte der Spalte, markiert sie als `legacy_redacted` und entfernt anschließend die Spalte. Aus dem fertigen Satz ließen sich Aktionsvariante und Zielwert nicht verlässlich zurückgewinnen; eine scheinbar exakte Heuristik hätte falsch zugeordnete Historie erzeugt. Redigierte Zeilen bleiben mit Verb, Ziel und Zeitpunkt als generische Aktivität lesbar, ihr früherer Wortlaut ist bewusst nicht wiederherstellbar.

`_redact_activities` bleibt in beiden Anonymisierungspfaden als Defense-in-Depth verdrahtet. Für das aktuelle Schema wäre das nicht nötig: der Kontext enthält bereits keine PII, und Pseudonym/Titel werden am Ziel selbst redigiert. Der Helfer setzt betroffene Kontexte trotzdem auf `redacted`; so kann ein künftiger Schema-Zusatz nicht unbemerkt die Löschgrenze erweitern. Dasselbe gilt für Fall- und Merge-Tombstone-Pfade.

### Verifikation

- [`test_activity_rendering.py`](../src/tests/test_activity_rendering.py) — Speicherung ohne Zielwert, aktueller Zielwert, fehlendes Ziel, Übersetzung, Aktivitätskarte und Art.-15-Export.
- [`test_0163_activity_display_context.py`](../src/tests/migrations/test_0163_activity_display_context.py) — Alttext wird redigiert und die Spalte physisch entfernt.
- `TestActivityWriteSchema` in [`test_architecture_guards_services.py`](../src/tests/test_architecture_guards_services.py) — jede Produktions-Schreibstelle muss `action` setzen; `summary` ist verboten.
- [`test_pii_residue_sweep.py`](../src/tests/test_pii_residue_sweep.py) — `core_activity.context` ist als PII-freier, geschlossener Schemawert klassifiziert und enthält keinen Klienten-Sentinel.

---

## Precache-Shells sind auth-neutral (Issue #1966)

Der Service Worker precacht acht pk-lose Offline-Hüllen und serviert sie ohne Netz **in-place** an ihren kanonischen URLs (`/`, `/clients/`, `/clients/<uuid>/`, `/workitems/`, `/events/new/`, `/workitems/new/`, `/offline/conflicts/`, `/offline/conflicts/<uuid>/`). Sie sind bewusst **public**: ein Auth-Gate würde den install-time `cache.addAll` auf `/login/` umlenken und das Precaching atomar scheitern lassen — die ganze Shell-Liste hängt an einem einzigen Aufruf.

### Der geschlossene Befund

Der Precache-Fetch läuft same-origin und damit **mit** Cookies. Installierte oder aktualisierte sich der Service Worker während einer angemeldeten Sitzung, landete die personalisierte `base.html`-Variante im Cache: Klarname und Rollenbezeichnung aus der Sidebar, Organisations- und Einrichtungsname, `data-user-id`, `data-session-age` und der CSRF-Token. Der Precache wird **cache-first ohne Revalidierung** ausgeliefert; der Eintrag überlebt damit Sitzungsablauf, Browser-Abbruch, Sperrung und Gerätewechsel und wird danach jeder weiteren Sitzung auf demselben Gerät gezeigt. Der Logout-Wipe entschärft nur den Normalfall, nicht die Abbruchfälle.

### Die zugesicherte Eigenschaft

Die Shells erben (#1966) das strikt statische [`offline_base.html`](../src/templates/offline_base.html) statt `base.html`. Zugesichert ist nicht „weniger Chrome", sondern **Byte-Gleichheit**: dieselbe Shell-URL liefert anonym und angemeldet denselben Body. Das ist die einzige Formulierung, die sich testen lässt und die nicht bei der nächsten Template-Änderung stillschweigend erodiert.

Daraus folgt zwangsläufig, dass die Shells **kein** `<meta name="csrf-token">` mehr tragen: der maskierte Token unterscheidet sich schon zwischen zwei anonymen Abrufen. Die Replay-Konsumenten holen ihn zur Laufzeit vom dedizierten Endpoint (`csrfUtils.refresh()`, #1408) — der zuvor dokumentierte „eingefrorene CSRF-Token" ist damit keine Eigenschaft der Shells mehr. `crypto.js` fällt ohne `data-session-age` auf seinen dokumentierten Default zurück, und der Boot-Purge in `sw-register.js` gated seither auf den Session-Key statt auf `data-user-id`.

Die Navigation der Shells ist eine **feste** Leiste ([`partials/_offline_navigation.html`](../src/templates/partials/_offline_navigation.html)) mit genau den Zielen, die der Service Worker selbst precacht. Die reguläre Navigation kann dort nicht stehen: sie ist rollen- und personenabhängig, und ein Cache-Eintrag friert die Sicht des Erst-Installierenden ein. Die Rollenschranke greift beim Online-Aufruf am Server und beim Replay der Queue.

### Grenzen

- Die **Sprache** bleibt die des Precache-Fetches: die Shell trägt den zur Precache-Zeit aktiven Sprachcode. Das ist eine Anzeige-Eigenschaft ohne Personenbezug; wer die Sprache wechselt, sieht die Hülle bis zur nächsten Service-Worker-Generation in der alten Sprache.
- Der Bump-Guard für precachte Shells folgt `{% extends %}` noch nicht selbst (#1852); `offline_base.html` steht deshalb ausdrücklich in seiner Template-Liste.
- Jede Änderung am ausgelieferten Shell-Markup verlangt weiterhin einen `CACHE_NAME`-Bump — sonst bekämen genau die betroffenen Bestandsgeräte die neue Hülle nie.

### Verifikation

- [`test_offline_shell_neutrality.py`](../src/tests/offline/test_offline_shell_neutrality.py) — Byte-Gleichheit anonym vs. angemeldet für alle acht Shells, Abwesenheit von Name/Rolle/Einrichtung/`data-user-id`/CSRF-Meta, Vollständigkeit der Skriptkette und ein ableitungsbasierter Vollständigkeits-Check gegen die View-Klassen.
- [`test_pwa_views.py`](../src/tests/test_pwa_views.py) — Shell-Hash-Guard samt gepinntem `CACHE_NAME` aus [`sw.js`](../src/static/js/sw.js).
- [`tests/js/sw-boot-purge-gate.test.mjs`](../tests/js/sw-boot-purge-gate.test.mjs) — der Boot-Purge läuft ohne `data-user-id` weiter und bleibt ohne Session-Key aus.

---

## Facility-Archivierung: Lifecycle des Zugriffsentzugs (Issue #2067)

**Ausgangslage.** Werten Login und Middleware `Facility.is_active` nicht aus, ist das Flag kosmetisch: eine archivierte Einrichtung arbeitet unverändert weiter.

**Serverseitige Sperre (zwei Ebenen).** Beide sind nötig, keine ersetzt die andere:

1. **Login** — `CustomLoginView.form_valid` *und* `AdminLockoutAuthenticationForm.confirm_login_allowed` (zweite Login-Oberfläche unter `/admin-mgmt/login/`) weisen Konten archivierter Einrichtungen ab und schreiben `LOGIN_FAILED` mit `reason: facility_inactive`. Kein Credential-Widerruf: Archivieren ist ein organisatorischer Vorgang, kein Kompromittierungsverdacht.
2. **`FacilityScopeMiddleware`** — beendet auch **bereits laufende** Sitzungen mit HTTP 403. Ohne diese Ebene wirkte Archivieren erst beim nächsten Anmelden. `request.current_facility` wird dabei auf `None` gesetzt, also bleibt `app.current_facility_id` leer und die `facility_isolation`-Policies liefern als zweite Linie ebenfalls nichts.

**Freigestellt wird die Rolle, nicht der Pfad.** `super_admin` bleibt unbeschränkt, weil `FacilityAdmin` super-only ist (L1, #1375) und nur diese Rolle `is_active` zurücksetzen kann. Eine Pfad-Ausnahme für `/admin-mgmt/` ließe den `facility_admin` der archivierten Einrichtung weiter an Fachdaten, ohne dass er sie reaktivieren könnte. Die Pfad-Ausnahmen (`ARCHIVED_EXEMPT_URLS`) decken nur Sackgassen ab: Abmelden, Login, Statik, i18n, Health, `sw.js`, `manifest.json`.

**Der Offline-Pfad braucht eine eigene Zustellung.** Serverseitig dicht zu sein genügt hier nicht: bereits heruntergeladene Client-/Facility-Bundles liegen verschlüsselt in IndexedDB (`anlaufstelle-offline`), der abgeleitete Sitzungsschlüssel in `anlaufstelle-crypto`. Beides überlebt eine serverseitige Sperre — die Nutzerin könnte nach dem 403 offline gehen und weiterlesen.

Ein nacktes 403 darf dafür **nicht** als Purge-Signal dienen: 403 ist bewusst kein Invalidierungsgrund (#1354), weil echter Rechteentzug den Client als Login-Redirect erreicht und ein 403 sonst CSRF-/Rate-Limit-/Proxy-Rauschen ist — es würde den Außendienst-Cache bei jedem Schluckauf leeren.

Die Archiv-403 trägt deshalb zwei Marker, ausgewertet über drei Zustellwege:

| Mechanismus | Deckt ab | Grenze |
|---|---|---|
| Header `X-Facility-Archived: 1` | `revalidateCachedClient` / `revalidateCachedFacility` purgen gezielt darauf: `crypto_session.wipeOfflineState()` — CryptoKey **und** alle Dexie-Tabellen | greift nur, wenn der Offline-Verbund geladen ist und eine Revalidierung läuft |
| [`facility-archived-purge.js`](../src/static/js/facility-archived-purge.js) | HTMX-Antworten (über `htmx-errors.js` bei gesetztem Marker) und die Sperrseite `auth/facility_archived.html` (startet über das Attribut `data-facility-archived-purge`): ist der Offline-Verbund geladen, zuerst `wipeOfflineState()`, danach `indexedDB.deleteDatabase` für `anlaufstelle-offline` **und** `anlaufstelle-crypto` | braucht ausgeführtes JavaScript auf einer Seite der App; Cache Storage und Web Storage fasst es nicht an |
| `Clear-Site-Data: "storage", "cache"` | Navigations-Fall: der Browser räumt IndexedDB, Web Storage, Cache Storage und SW-Registrierungen selbst ab — derselbe Mechanismus wie beim Logout | von Safari nur unvollständig unterstützt; für die IndexedDB-Datenbanken deckt das die Sperrseite über das Purge-Modul ab, für Cache Storage bleibt die Grenze |

**Runtime-Cache des Service Workers.** Der separate `RUNTIME_CACHE_NAME` nimmt nur statische Same-Origin-Assets (`/static/...` sowie `/manifest.json`) mit einem Asset-Content-Type entgegen; `text/html`, HTMX-Responses und Antworten mit `Cache-Control: no-store` werden nicht gecacht (Refs #2201). Bei jedem `CACHE_NAME`-Bump wird der Runtime-Cache automatisch neu benannt, und alte Generationen räumt der `activate`-Handler ab. Dennoch ist der Runtime-Cache ein weiterer Klartext-Sink neben IndexedDB: ein dynamischer HTML-Eintrag könnte bis zum nächsten Bump PII enthalten; deshalb die Inhalts-Prüfung vor `cache.put`. Safari-Grenze: `Clear-Site-Data: "storage", "cache"` entfernt auch hier den Cache Storage nur unvollständig.

`"cookies"` wird bewusst **nicht** gesetzt: die Sitzung soll leben, damit der Abmelden-Knopf auf der Sperrseite trägt.

**Restrisiko.** Ungesynchronisierte lokale Änderungen fallen mit — bewusst, wie beim Rechteentzug (#1110/#1111): entschlüsselte PII darf einen Zugriffsentzug nicht überleben, und ein hängender Edit darf den Sicherheits-Purge nicht blockieren. **Ohne Serverkontakt greift keiner der drei Zustellwege.** Das Schadensbild ist dann differenzierter, als es zunächst scheint:

- Der **Server-Spiegel** (Personen-, Fall- und Ereignis-Bundles) verfällt spätestens mit der 48-h-TTL — `purgeExpiredBundles` verwirft ihn ohne `force`.
- **Ungesyncte Arbeit** (Queue-Zeilen, Events mit `localStatus` in *modified/new/conflict/dead*) überlebt das ausdrücklich: Weder der TTL-Purge (#1353) noch der Idle-Pfad löschen sie. Bei ungesyncten Daten verwirft der Idle-Wipe **nur den Schlüssel** und lässt den verschlüsselten Bestand stehen, damit ein Re-Login die Arbeit wieder abspielbar macht.
- Wirksam bleibt damit F-01: ohne Schlüssel ist der Rest nicht lesbar. Ein Klartext-Leck entsteht nicht, aber die Aussage „nach 48 h ist alles weg" wäre falsch.

Das ist dieselbe Grenze wie bei jedem anderen Widerrufspfad — die Zustellung setzt einen Serverkontakt voraus.

**Hard-Delete bleibt kein Ausweg.** Er scheitert fail-loud am `auditlog_immutable`-Trigger und rollt vollständig zurück (Django umschließt die gesamte Kaskade mit einer Transaktion — auch aus `manage.py shell`). Partielle Zerstörung entsteht nur durch selbst geschriebene Skripte, die Fachtabellen einzeln mit Zwischencommits leeren. Details im [Administrationshandbuch § 2.2c](admin-guide.md).

**Tests:** [`test_facility_lifecycle.py`](../src/tests/test_facility_lifecycle.py) (Login beider Oberflächen, laufende Sitzung, RLS-GUC, Rollen-Abgrenzung, Marker-Header, Sprache) und [`tests/js/facility-archived-purge.test.mjs`](../tests/js/facility-archived-purge.test.mjs) (Purge nur bei gesetztem Marker — beide Richtungen, damit der Fix keine #1354-Regression wird).

---

## Konten ohne Einrichtung: Wurzel-Guard statt 66 Einzelstellen (Issue #2090)

**Der Zustand ist legitim, nicht verboten.** `User.facility` ist `null=True` ([`user.py`](../src/core/models/user.py)): `super_admin` trägt die Bindung **per Design** nicht (so legt sie `manage.py create_super_admin` an, so steht sie im Seed), und die Benutzerverwaltung kann sie jedem Konto entziehen. Ihn wegzudefinieren (`null=False`) wäre keine Härtung, sondern bräche die Ersteinrichtung.

**Der Befund.** Ohne Wurzel-Guard wäre der Zustand für jede **andere** Rolle ein Minenfeld: `FacilityScopeMiddleware` setzt `request.current_facility = None`; liefe der Request danach weiter, zerfiele die Anwendung in zwei Fehlerklassen:

- `get_scoped_object()` wirft ohne Facility-Kontext **mit Absicht** `ValueError` — der Lookup wäre sonst ein Cross-Facility-IDOR ([`scoping.py`](../src/core/services/scoping.py)). **66 Aufrufstellen in 17 View-Modulen**, jede davon ein HTTP 500.
- Rund ein Dutzend Service-Funktionen dereferenzieren `facility.settings` bzw. `facility.pk` ungeprüft — diese Klasse ist die gefährlichere, weil sie **ohne pk in der URL** erreichbar ist: `/` (Zeitstrom), `/verwaltung/`, `/retention/`, `/events/new/`.

Weil `LOGIN_REDIRECT_URL = "/"` ist, sah so ein Konto den Serverfehler **direkt nach dem Login**, ohne je eine bedienbare Seite gesehen zu haben. Die Messung über die AuthZ-Tabelle: **71 der 257 Methodenzeilen** antworteten mit 5xx.

**Die Antwort ist ein Guard an der Wurzel, nicht 66 Einzelfixes.** Er sitzt in derselben Middleware wie die Archiv-Sperre — beide Zustände enden in „kein nutzbarer Einrichtungskontext", und die Render-Mechanik (Standalone-Template ohne `base.html`, `render_to_string` **ohne** `request`, CSRF-Token von Hand, `translation.override` plus explizites `Content-Language`) ist dieselbe und wird über `_render_block` geteilt. `get_scoped_object` behält seine `ValueError`-Semantik unverändert: sie ist das IDOR-Signal und bleibt test-gepinnt; der Guard macht sie nur unerreichbar.

**Antwortform: 403 mit erklärender Seite, ohne eigene Route.** Die Middleware rendert direkt (Muster von Djangos `permission_denied`). Eine eigene Route hätte eine Kaskade ausgelöst — harte Routenzahl, Backlink-Registry, Nav-Klassifizierung und 257 neue AuthZ-Matrix-Zellen — ohne einen einzigen Sicherheitsgewinn.

**Freigestellt wird die Rolle, nicht der Systempfad.** Dieselbe Doktrin wie bei der Archivierung: `super_admin` passiert den Guard; `/system/` und `/admin-mgmt/` stehen **nicht** auf der Pfad-Whitelist. Eine Pfad-Ausnahme ließe einen einrichtungslosen `facility_admin` in den Systembereich — die Rollen-Ausnahme nicht. Für ihn antwortet dort ohnehin `SuperAdminRequiredMixin` mit 403; es ändert sich nur die Quelle der Antwort.

**Die Pfad-Whitelist ist hergeleitet, nicht gewählt.** Freigestellt sind exakt die AuthZ-Kategorien `public` (anonym erreichbar) und `auth-flow` (jede angemeldete Rolle, inklusive des einrichtungslosen `super_admin`) aus [`_authz_expectations.py`](../src/tests/_authz_expectations.py). Die Regel dahinter: wer anonym auf eine Seite darf, darf angemeldet nicht schlechter gestellt sein — und was jede Rolle sehen darf, hängt nicht an der Einrichtung. Ohne die Liste sperrte der Guard Login, Abmelden, die MFA- und Passwortketten, Sudo, `/hilfe/`, `/account/` samt Lockout-Selbsthilfe, `/start/`, die Offline-Shells und `/health/` aus. Beide Richtungen hält [`test_facility_less_account.py`](../src/tests/test_facility_less_account.py) fest — sie sind der Grund, warum die Liste nicht zur Wunschliste wird.

Zwei Formen sind nötig: Präfixe für ganze Teilbäume (`/mfa/`, `/webauthn/`, `/account/`, …) und **exakte Pfade** für den `/offline/`-Baum. Dort teilen sich zwei Kategorien dieselbe Wurzel: neun PII-freie Shells (`public`, vom Service Worker precacht) und zwei facility-gebundene pk-Routen (`core:offline_client_detail`, `core:offline_conflict_review`, beide `offline-api`). Ein Präfix `/offline/` hätte die beiden mit durchgelassen.

**Kein Purge-Signal an den Offline-Client.** Die neue 403 trägt **weder** `X-Facility-Archived` **noch** `Clear-Site-Data` — und bekommt auch keinen eigenen Marker. Ein nacktes 403 ist (#1354) bewusst kein Invalidierungsgrund, und das ist hier genau richtig: bei der Archivierung endet der Zugriff auf Fachdaten **dauerhaft**, hier ist der Zustand typischerweise eine Fehlkonfiguration, die zurückgenommen wird. Ein Purge löschte Außendienst-Caches und die IndexedDB-Kryptoschlüssel — nicht reversibel. Ein zweiter Marker-Header wäre außerdem eine Einladung, ihn später mit derselben Purge-Semantik zu verdrahten.

**Beabsichtigte Verhaltensänderung.** Die Offline-APIs `core:offline_facility_bundle` und `core:offline_revocations` lieferten einem einrichtungslosen Konto bisher still ein **leeres** Bundle (`for_facility(None)`); jetzt antworten sie 403. Das ist die ehrlichere Antwort — ein leeres Bundle ist von „diese Einrichtung hat keine Daten" nicht unterscheidbar.

**Grenze.** Der Guard fängt den Zustand ab; er verhindert nicht, dass er entsteht. Die Anlage-Lücke im Django-Admin (`facility` ist dort `blank=True`, erzwungen wird sie nur für Nicht-`super_admin`s) bleibt offen und ist als Folge-Ticket zu führen. Ebenso bleibt die Navigation auf den Whitelist-Seiten vollständig sichtbar — jeder Link führt in die 403 statt zu verschwinden. Beides ist Kosmetik bzw. Prävention, keine Lücke im Guard.

**Tests:** [`test_facility_less_account.py`](../src/tests/test_facility_less_account.py) (Reproduktion beider Fehlerklassen, Sperrseite, RLS-GUC, Abgrenzung zur Archiv-Sperre, Sprache durch die volle Kette, Whitelist-Herleitung in beide Richtungen) und [`test_authz_facility_less.py`](../src/tests/test_authz_facility_less.py) — der systematische Sweep über **alle** Methodenzeilen der AuthZ-Tabelle (`EXPECTATIONS` in `_authz_expectations.py`). Weil das Vollständigkeits-Gate `test_every_named_pattern_is_declared` für jeden neuen benannten URL-Pattern einen Eintrag dort erzwingt, wird ein neuer View **automatisch** mitgeprüft, ohne dass eine zweite Liste zu pflegen wäre.

---

## Aufbewahrungssperre auf Merge-Tombstone wird abgewiesen (Issue #2144)

### Beobachtung

`create_hold_for_target` ([`legal_holds.py`](../src/core/retention/legal_holds.py)) sperrt für `target_type="Client"` (#2108/#2122) die Personenzeile — den gemeinsamen Koordinationspunkt mit Sofortpfad, Nacht-Batch, K-Anon-Bridge und Ereignis-Sweep. Gelesen wurde auf dieser gesperrten Zeile aber nichts: weder `merged_into_id` noch `is_deleted`. Eine Sperre auf die **Quelle** einer bereits vollzogenen Zusammenführung war damit still zulässig und wurde regulär als `LEGAL_HOLD` auditiert.

Sie deckte jedoch nichts mehr. Der Merge hängt Fälle und Ereignisse per `update(client=target)` an die Zielperson um ([`merge.py`](../src/core/services/client/merge.py)); die Zeilen behalten ihre pk, wechseln aber den Personenbezug. Die Deckungsmengen der Retention schauen ausschließlich auf den Tombstone: `collect_doomed_events`/`enforce_*` schließen `client_id__in={Quelle}` aus, `exclude_clients_with_active_holds` schließt `pk__in={Quelle}` aus ([`enforcement.py`](../src/core/retention/enforcement.py), [`legal_holds.py`](../src/core/retention/legal_holds.py)). Folge: die gewanderte Dokumentation wird am Ziel weiter redigiert, Papierkorb-Frist, Admin-Sofort-Tilgung und K-Anon-Bridge bleiben für das Ziel offen — trotz eines Holds „auf ihrer Person".

Verschärfend war das **Verspätungsmerkmal** irreführend: `history_already_redacted` prüft die Ereignisse der Zielperson des Holds, und die Quelle hat nach dem Merge keine mehr (`any(()) == False`). Der Hold meldete also „pünktlich" — auf einer Akte, über die er keine Aussage mehr trifft. Für die Spoliationsfrage ist genau dieses Merkmal der Nachweis.

Die **Gegenrichtung war seit ADR-045 sauber geschlossen**: `_validate_merge` weist eine Zusammenführung unter aktivem Hold ab und nennt im Docstring denselben Deckungsverlust als Begründung; eine bereits gemergte Quelle wird ebenfalls abgewiesen. Es fehlte nur die Rückrichtung — Hold **nach** Merge.

### Maßnahme

Auf der bereits gesperrten Personenzeile wird `merged_into_id` jetzt mitgelesen; ist es gesetzt, endet die Hold-Anlage mit einer deutschen `ValidationError`, die das Pseudonym der **Zielperson** nennt. Kein Hold, keine Auditzeile — fail-loud, wie ADR-050 D2 es für Hold-Pfade fordert. Derselbe Guard läuft im `Case`-Zweig auf der Personenzeile des Falls: Fälle selbst haben keinen Tombstone (sie wandern beim Merge mit), ihre Person kann aber einer sein.

**Abweisen statt umlenken.** Ein Umbiegen des Ziels auf `merged_into` wäre technisch möglich gewesen (mit `redirected_from` im Audit-Detail). Eine Aufbewahrungssperre ist aber ein bewusster Beweismittel-Akt: wer sie anordnet, soll die Merge-Kette selbst nachvollziehen und die Zielperson benennen, statt sie stillschweigend verschoben zu bekommen. Dieselbe Antwort gibt die Gegenrichtung im Merge-Pfad.

### Trade-off

Zwei Zustände bleiben bewusst **zulässig**:

- **Papierkorb** (`is_deleted=True`): Der Hold auf eine Person oder einen Fall im Papierkorb ist genau die Zusage aus Handbuch und Oberfläche — „ein Hold hält beide Stufen auf", Papierkorb-Frist *und* Tilgung. Eine Abweisung hätte den Schutz entfernt, auf dem #2123 (Fall-Tilgung unter Sperre) aufsetzt.
- **Bereits redigiert** (`anonymized_at`): Dieser Fall ist nicht still, sondern trägt seit jeher das Verspätungsmerkmal `history_already_redacted=True`. Er ist dokumentiert, nicht verloren — der Unterschied zum Tombstone, wo das Merkmal fälschlich „pünktlich" meldete.

Die Reichweite ist heute gering: es gibt keinen UI-Weg für direkte Personen-/Fall-Holds (`RetentionProposal.TargetType` kennt nur `EVENT`), `create_hold_for_target` ist nur per Service/Konsole erreichbar. Der Guard schließt einen latenten fail-open-Pfad, der bei jeder künftigen Verdrahtung klickbar würde.

### Verifikation

[`test_legal_hold_tombstone_guard.py`](../src/tests/test_legal_hold_tombstone_guard.py): Abweisung nach vollzogenem Merge (kein `LegalHold`, keine `LEGAL_HOLD`-Auditzeile, Zielpseudonym in der Meldung), Positivpfad (der Hold auf die Zielperson nimmt das gewanderte Ereignis aus `collect_doomed_events` und die Zielperson aus `exclude_clients_with_active_holds`), beide Grenzen (Papierkorb und unbekannte `target_id` bleiben zulässig), der `Case`-Zweig sowie ein beobachtender Zwei-Faden-Renn-Nachweis: eine Hold-Anlage, die in einen laufenden Merge hineinläuft, wartet am Personen-Anker und wird danach abgewiesen — statt still auf dem fertigen Tombstone zu landen.

---

## Fall-Hold hält auch den manuellen Löschpfad und den Historien-Sweep an (Issue #2197)

### Beobachtung

Ein Hold auf einen **Fall** schützt (#2122) die Ereignisse dieses Falls: `_lock_event_and_recheck_hold` ([`enforcement.py`](../src/core/retention/enforcement.py)) stellt sie unter der Sperre als `deferred` zurück, `exclude_cases_with_active_holds` zieht die Gegenrichtung. Zwei Ereignis-Pfade hatten diesen dritten Zweig nicht übernommen:

- **Der manuelle Löschpfad.** `_assert_no_active_hold` ([`crud.py`](../src/core/services/events/crud.py)) prüfte nur Ereignis- und Personen-Holds. Über ihn laufen das direkte Löschen durch die Fachkraft, die Rücknahme eines Strichlisten-Eintrags und der geprüfte Vier-Augen-Weg (`approve_deletion`). Ein Ereignis in einem gehaltenen Fall ließ sich damit per Klick leeren — und `delete_attachments_for` entfernt dabei die verschlüsselten Anhänge **physisch**, was kein Papierkorb zurückholt. Der eigene Docstring der Funktion versprach das Gegenteil: „ein manueller Klick darf nicht mehr dürfen als der Cron".
- **Der Historien-Sweep und Migration `0174`.** `lock_and_partition_events` ([`eventhistory.py`](../src/core/retention/eventhistory.py)) bildete die Hold-Menge aus `"Event"` und `"Client"`. Der Sweep redigierte die Verlaufsfassungen der Ereignisse eines gehaltenen Falls, während der Aufbewahrungslauf das lebende Ereignis (korrekt) stehen ließ — der Rahmen blieb, sein Inhalt nicht (Spoliation, Muster #1066).

### Maßnahme

`_assert_no_active_hold` bekommt den dritten Zweig (Fall-Hold) mit derselben Meldung wie die beiden anderen — fail-loud, ohne PII. Geprüft wird dabei die **unter der Sperre gelesene** Zuordnung, nicht die der übergebenen Instanz: `_assert_current_client_binding` liest nach dem `select_for_update` auf der Ereigniszeile `client_id` **und** `case_id` und gibt beides an den Guard weiter. Der Aufrufer hält das Ereignis womöglich seit Minuten im Speicher (der geprüfte Vier-Augen-Weg lädt es ungesperrt); wird es zwischenzeitlich über `assign_event_to_case` einem gehaltenen Fall **derselben Person** zugeordnet, trüge die Instanz noch die alte, fallslose Zuordnung — und der Guard sähe den Hold nie. Ein Wechsel des Falls ist dabei — anders als ein Wechsel der Person — **kein** Abbruchgrund: der Fall hängt an derselben, bereits gesperrten Person, und genau diese Zeile nimmt auch die Fall-Hold-Anlage als erste Stufe. Es fehlt also keine Sperre, nur der aktuelle Wert. `lock_and_partition_events` liest die Fall-Zugehörigkeit in der Revalidierung **nach** der Ereignissperre mit und nimmt `held_cases` ins Hold-Tripel auf.

Bewusst in der Revalidierung und nicht im Snapshot: ein Umhängen des Ereignisses in einen anderen Fall **derselben** Person lässt den Personenbezug unverändert; die Revalidierung aus Finding C greift dann nicht, und ein Fall-Wert von vor den Sperren wäre der falsche. Der Koordinationspunkt ist dieselbe Personenzeile, die Stufe 1 ohnehin sperrt — `_lock_case_and_check_history` nimmt sie beim Anlegen eines Fall-Holds als erste Stufe (kanonische Ordnung `core_client` → `core_case` → `core_event`).

Die Datenmigration `0174_redact_deleted_event_history_backlog` importiert denselben Baustein und ist damit ohne eine Zeile Migrationscode mitkorrigiert (ADR-044 §3: eine Fassung, keine Drift).

### Grenzen

- **Strichlisten-Rücknahme.** `undo_tally_event` verweigert die Fähigkeit schon vorher, wenn das Ereignis eine Person **oder** einen Fall trägt; ein Strichlisten-Ereignis ist anonym und fallslos. Der Fall-Zweig ist über diesen Pfad also gegenstandslos — das Ereignis überlebt, aber über die Fähigkeitsprüfung, nicht über das Hold-Gate.
- **Reichweite.** Fall-Holds entstehen weiterhin nur per Service/Konsole (`create_hold_for_target`, kein UI-Weg). Geschlossen wird ein latenter fail-open-Pfad, der bei jeder künftigen Verdrahtung klickbar würde.

### Hold-Kontrakt: Ziele und Meilensteine frieren **nicht** ein

Ausdrücklich entschieden (und hier festgehalten, damit die Frage nicht erneut gestellt wird): ein Fall-Hold sperrt `delete_milestone`/`toggle_milestone` ([`goals.py`](../src/core/services/case/goals.py)) **nicht**. Ziele und Meilensteine sind Arbeitsstand der laufenden Fallarbeit, keine Beweisdokumentation; der Legal Hold schützt die Dokumentation und ihre Historie vor Tilgung, nicht die Planung vor Fortschritt. Die Art.-18-Einschränkung deckt den Fall, in dem eine betroffene Person die Verarbeitung angehalten haben will — dafür steht `assert_processing_allowed`, und das greift dort. Eine zusätzliche Hold-Sperre auf Ziel-Mutationen wäre Überdeckung, die die Fallarbeit anhält, ohne ein Beweismittel zu sichern.

### Verifikation

[`test_event_soft_delete_fields.py`](../src/tests/test_event_soft_delete_fields.py) (`TestCaseHoldBlocksTheManualDeletePath`: Nutzdaten, Audit und die verschlüsselte Datei überleben; ein erst nach dem Lesen der Instanz zugeordneter gehaltener Fall hält ebenso an; Gegenproben für einen fremden Fall, ein fallloses Ereignis und eine Zuordnung zu einem **nicht** gehaltenen Fall), [`test_event_deletion_hold.py`](../src/tests/test_event_deletion_hold.py) (Vier-Augen-Weg: der Antrag bleibt offen, kein `DELETION_APPROVED` — auch dann, wenn die Fallzuordnung erst zwischen dem Laden des Ereignisses und der Sperre entsteht), [`test_tally_undo.py`](../src/tests/test_tally_undo.py) (`TestTallyUndoAndLegalHolds`: geerbtes Hold-Gate und die Begründung, warum der Fall-Zweig dort gegenstandslos ist) sowie [`test_eventhistory_retention.py`](../src/tests/test_eventhistory_retention.py) (`TestCaseHoldsAreHardToo`: Sweep stellt zurück, ungehaltener Fall wird weiter redigiert, ein während der Sperrwartezeit in einen gehaltenen Fall umgehängtes Ereignis wird gehalten; `TestBacklogMigrationHonoursHolds`: derselbe Schutz über Migration 0174).

---

## Aufheben einer Zusammenführung unter aktivem Hold wird abgewiesen (Issue #2200)

### Beobachtung

Die Gegenrichtung zu #2144. `_validate_merge` weist eine Zusammenführung unter aktivem Hold seit ADR-045 ab und nennt im Docstring den Grund: auf der Quellseite verlöre ein Personen-Hold still die Deckung über deren Ereignisse. `unmerge_client` ([`merge.py`](../src/core/services/client/merge.py)) prüfte Berechtigung, `merged_into`-Konsistenz, `anonymized_at` der Quelle und den Ziel-Tombstone — **keinen Hold**.

Beim Aufheben wandern Fälle, Ereignisse, Aufgaben und übernommene Anhänge zur Quelle zurück; ein Client-Hold auf der **Zielperson** bleibt dort stehen. `owner_has_active_hold` war vorher wahr, danach falsch — bei weiterhin aktivem Hold. Die zurückgewanderte Dokumentation fiel damit still in den regulären Aufbewahrungslauf: ein Beweismittel-Hold verlor seinen Umfang, ohne Fehler, ohne Hinweis, ohne Auditbezug (Spoliation über einen Umweg, Muster #1066). Ein Ereignis-Hold blieb erhalten (die pk ändert sich nicht) — betroffen war die **abgeleitete** Deckung über die Person, also genau die Ebene, auf der ein Hold für „alle Ereignisse dieser Person" gesetzt wird.

### Maßnahme

Auf den beiden bereits gesperrten, frisch gelesenen Personenzeilen (`_locked_clients` nimmt sie in einem Statement in kanonischer `pk`-Ordnung) wird jetzt `client_has_active_hold` für Ziel **und** Quelle gefragt; trifft es zu, endet das Aufheben mit derselben deutschen `ValidationError` wie der Merge. Fail-loud und ohne PII: die Meldung nennt keine Pseudonyme — der Hold-Zustand einer Person ist selbst eine Auskunft.

**Abweisen statt spiegeln.** Den Hold beim Aufheben auf die Quelle zu spiegeln (zweiter Hold, gleiche Begründung, Audit-Verweis) wäre technisch möglich. Eine Aufbewahrungssperre ist aber ein bewusster Beweismittel-Akt; ein still erzeugter Zweit-Hold wäre das Gegenteil — dasselbe Argument, mit dem #2144 das Umlenken auf die Zielperson verworfen hat. Bis eine ausdrückliche Übertragungsregel existiert, wartet das Aufheben auf das Ende der Sperre.

**Dieselbe Hold-Menge wie der Merge.** `client_has_active_hold` zählt neben dem Personen-Hold auch ein gehaltenes **Ereignis** und einen gehaltenen **Fall** dieser Person mit. Beim Fall reißt die Deckung tatsächlich nicht: die `case_id` des Ereignisses bleibt, und der Fall wandert als Ganzes zurück — der Fall-Hold deckt weiter. Das Abweisen ist dort also strenger als nötig. Eine zweite, engere Hold-Menge daneben wäre aber genau die Drift, die #1756 gekostet hat; die Überdeckung ist der bewusst gewählte Preis.

### Grenzen

- **Hold auf der Quelle ist über die Service-Wege nicht herstellbar.** `create_hold_for_target` weist eine Sperre auf einen Merge-Tombstone ab (#2144; `_assert_not_merge_tombstone`). Der Quell-Zweig ist damit Abwehr in der Tiefe für Alt- und Reparaturzustände, die nicht über die Service-Wege entstanden sind — der Guard hält sie trotzdem.
- **Reichweite.** `ClientUnmergeView` ist über den Papierkorb erreichbar (Anwendungsbetreuung); Personen-Holds entstehen weiterhin nur per Service/Konsole. Geschlossen wird ein Pfad, der bei jeder künftigen Verdrahtung klickbar würde.

### Verifikation

[`test_client_merge_workflow.py`](../src/tests/test_client_merge_workflow.py) (`TestUnmergeUnderLegalHold`: Hold auf dem Ziel — Ereignis bleibt beim Ziel, `owner_has_active_hold` bleibt wahr, kein `CLIENT_RESTORED` mit `unmerged_from`, kein `client_unmerged`-Log; Hold auf der Quelle über den direkten Modell-Bypass, nachdem `create_hold_for_target` ihn abgewiesen hat; zurückwanderndes gehaltenes Ereignis; gehaltener Fall; und die Gegenprobe ohne Hold, die zeigt, dass die Fall-Zuordnung das Aufheben unbeschadet übersteht) sowie [`test_client_merge_views.py`](../src/tests/test_client_merge_views.py) (HTTP: 302 auf den Papierkorb mit der Meldung, kein 500, keine Pseudonyme in der Meldung, die Zusammenführung bleibt bestehen).



---

## Weitere Einstiegspunkte

- [CONTRIBUTING.md § Facility-Scoping & Row Level Security](../CONTRIBUTING.md#facility-scoping--row-level-security)
- [docs/ops-runbook.md § 9](ops-runbook.md#9-row-level-security-rls) — RLS-Runbook, Rollen
- Tiefenanalyse-Audit 2026-04-21 (internes Code-Audit, dev-only) — Security-/DSGVO-/Perf-Audit mit zeilengenauer Verifikation
