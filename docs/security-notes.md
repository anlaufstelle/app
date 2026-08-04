# Security Notes

Dokumentiert bewusste Security-Entscheidungen, die beim Audit der Tiefenanalyse (internes Code-Audit 2026-04-21, dev-only) verifiziert und für die Roadmap festgehalten wurden. Dieses Dokument ist das „Warum wir das so lassen"-Gegenstück zur RLS- und Facility-Scoping-Doku in [CONTRIBUTING.md](../CONTRIBUTING.md#facility-scoping--row-level-security) und zum Ops-Runbook [docs/ops-runbook.md](ops-runbook.md).

---

## `core_user` bleibt RLS-frei (Finding)

**Status:** Design-Entscheidung, kein Fix notwendig.

### Beobachtung

Das `User`-Model hat einen `facility`-FK (nullable, `on_delete=SET_NULL`) in [`src/core/models/user.py`](../src/core/models/user.py), ist aber **nicht** in `DIRECT_TABLES` oder `JOIN_TABLES` der RLS-Setup-Migration [`0047_postgres_rls_setup.py`](../src/core/migrations/0047_postgres_rls_setup.py). Jede andere facility-gescopte Tabelle hat eine `facility_isolation`-Policy; `core_user` fehlt absichtlich.

### Begründung

Zwei Use-Cases sind auf Cross-Facility-Zugriff angewiesen:

1. **Login-Bootstrap.** Beim Anmelden ist die Session noch nicht an eine Facility gebunden — Django-`auth_login` sucht den User über `username` (oder E-Mail beim Password-Reset) und setzt erst danach die Facility in `request.session`. Eine facility-isolierte `core_user`-Policy würde den Login von **jedem** User scheitern lassen, solange der Session-Variablen-Setzer (die [`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py)) nicht schon läuft — klassischer Chicken-and-Egg.
2. **Cross-Facility-Administration.** Administratoren können User mehrerer Facilities verwalten (Passwort-Reset senden, Rollen anpassen, Token-Invite neu generieren). Das ist explizit gewünscht (siehe [`core.admin.users.UserAdmin`](../src/core/admin/users.py)), und eine facility-isolierte Policy würde diesen Admin-Workflow unterbinden.

### Andere Verteidigungslinien

Auch ohne RLS-Isolation auf `core_user` gilt:

- **Facility-Scoping im ORM-Layer:** Alle user-facing Views gehen über [`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py), die `request.current_facility` setzt. Queries, die User ausschließlich einer Facility liefern sollen, filtern explizit (z.B. in [`forms/workitems.py`](../src/core/forms/workitems.py): `User.objects.filter(facility=facility, ...)`).
- **Rollen-Gates:** [`AdminRequiredMixin`](../src/core/views/mixins.py) hält Cross-Facility-User-Management Admin-only.
- **AuditLog:** User-Role-Changes und Deaktivierungen werden via `post_save`-Signale geloggt (siehe [`signals/audit.py`](../src/core/signals/audit.py)); der handelnde Admin wird dabei über das `actor`-Feld erfasst und tamper-evident in die HMAC-Audit-Kette eingebunden — der Eintrag trägt das Ziel im `user`-Feld und den Verursacher in `actor`, statt beide zu vermengen (#1369).

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

- **AUTHZ-1 — Schreibpfad umgeht die Service-Schicht.** Add/Change/Delete auf `Client`/`Case`/`Event`/`WorkItem` liefen am Service-Layer vorbei und umgingen damit das Domänen-AuditLog (`CLIENT_UPDATE`/`EVENT_CREATE`/…), die `EventHistory`-Versionierung sowie die Vier-Augen-Löschung/Legal-Hold-/Retention-Workflows. Da ein Admin-Save diese Invarianten (u. a. Feld-Verschlüsselung, History-Diff) gar nicht replizieren kann, sind diese Fachobjekte im Admin jetzt **strikt read-only** ([`ReadOnlyDomainAdminMixin`](../src/core/admin/mixins.py) → `has_add/change/delete_permission = False`) — konsistent mit den bereits append-only gestellten Admins (`EventHistory`/`EventAttachment`/`AuditLog`/`DeletionRequest`). Konfig-Objekte ohne tamper-evidente Klientel-Historie (`DocumentType`/`FieldTemplate`/`QuickTemplate`/`TimeFilter`) bleiben schreibbar; `Settings`-Änderungen sind über `SettingsAdmin.save_model` → `log_settings_change` weiterhin auditiert.
- **AUTHZ-2 — Cross-Facility-PII-Read unauditiert.** Ein `super_admin` konnte über die Admin-Listen/Change-Views facility-übergreifend Klientel-PII lesen, ohne Spur — im Gegensatz zu `/system/`, wo jeder Zugriff als `SYSTEM_VIEW` protokolliert wird. [`AdminReadAuditMixin`](../src/core/admin/mixins.py) schreibt für super_admin-Reads jetzt einen `SYSTEM_VIEW`-Eintrag (über den Helper [`audit_admin_view`](../src/core/services/audit/helpers.py), `facility=None`, RLS-GUCs via `_set_session_vars` gesetzt — exakt wie `SystemAuditMixin`). Die Changelist erzeugt genau **einen** Sammel-Audit pro Request (nicht pro Zeile). Ein same-facility-Read durch einen `facility_admin` erzeugt **keinen** Audit — sein Queryset ist bereits facility-gescopt (kein Cross-Facility-Read möglich), sodass `app.is_super_admin` nicht fälschlich in seiner Session gesetzt wird.

Ein Architektur-Guard ([`TestDomainAdminReadOnlyGuard`](../src/tests/test_architecture_guards_audit.py)) lässt künftige Fach-Model-Admins ohne `ReadOnlyDomainAdminMixin` fehlschlagen; Verhaltensnachweis in [`test_admin_audit.py`](../src/tests/test_admin_audit.py).

**Restrisiko / Trade-off:** Die Read-only-Sperre entfernt den legitimen Notfall-Korrektur-Pfad über das Admin — bewusste Konsequenz der Entscheidung „Sperren statt Audit-Wiring", weil ein Admin-Save die Service-Invarianten nicht sauber replizieren kann. Notfall-Korrekturen laufen über die reguläre App (Service-Schicht, auditiert) bzw. den forensischen psql-Pfad (`anlaufstelle_admin`, BYPASSRLS).

---

## CSP `'unsafe-eval'` auf `/admin-mgmt/` (Issue #695)

**Status:** Design-Entscheidung, Workaround akzeptiert. Trigger-Liste für Re-Evaluation am Ende.

> **Als ADR dokumentiert:** Die Architektur-Entscheidung ist als [ADR-025](adr/025-csp-unsafe-eval-admin.md) festgehalten (inkl. der `text/html`-Verschärfung aus #1084). Dieser Abschnitt bleibt als ausführliche Trade-off- und Threat-Model-Notiz.

### Beobachtung

Die globale CSP setzt `script-src 'self'` (kein `'unsafe-eval'`, kein `'unsafe-inline'`) — alle App-Templates nutzen den `@alpinejs/csp`-Build mit registrierten `Alpine.data()`-Komponenten. Eine Ausnahme bildet die Django-Admin-UI auf `/admin-mgmt/`: das gevendor'te [`django-unfold`-Theme](https://github.com/unfoldadmin/django-unfold) (Version 0.91.0) liefert >20 Templates mit Inline-Function-Calls (`x-data="searchCommand()"`, `x-data="theme(...)"`, `x-data="searchDropdown()"`, `x-data="searchForm()"`) **und** Inline-Object-Expressions (`x-data="{rowOpen: false}"` u.ä.). Beide Patterns scheitern unter dem strikten `@alpinejs/csp`-Build, weil Alpine sie nur mit dynamischer Code-Auswertung parsen kann (klassischer Alpine-Build), die unter `script-src 'self'` ohne `'unsafe-eval'` blockiert ist.

[`AdminCSPRelaxMiddleware`](../src/core/middleware/admin_csp_relax.py) hängt `'unsafe-eval'` per Response-Header-Rewrite **ausschließlich** für Pfade unter `/admin-mgmt/` an. Die globale CSP bleibt unverändert — Login, Klient-CRUD, Zeitstrom, Statistik und alle anderen Routen laufen weiter mit `script-src 'self'`.

### Trade-off

Die saubere Alternative wäre Option 2 aus dem Issue: 20+ Unfold-Templates in [`src/templates/admin/`](../src/templates/) überschreiben + Shim-JS mit `Alpine.data('searchCommand', ...)`-Re-Implementierungen. Initialaufwand 1–2 Tage, dazu laufender Wartungs-Tax bei jedem `django-unfold`-Update. Im Issue-Body selbst auf v0.11 verschoben.

**Was wir aufgeben:**
- `script-src` auf Admin-Routes ist nicht mehr „strict" — Audit-Reports werden `'unsafe-eval'` flaggen
- Falls je eine XSS-Lücke in einem Admin-Template entsteht, hat ein Angreifer dort grösseren Sprengbereich als auf den restlichen Routes

**Was wir behalten:**
- `script-src` bleibt `'self'` — kein Remote-Script-Loading
- Architektur-Tests verbieten weiterhin `csrf_exempt`, `|safe`, `mark_safe()`, Inline-`<script>`-Blöcke im gesamten Repo (siehe `src/tests/test_architecture_guards_*.py`)
- `/admin-mgmt/` ist nur fuer Rollen `super_admin` und `facility_admin` erreichbar (Custom `AnlaufstelleAdminSite`, Refs #785). `lead`/`staff`/`assistant` werden geblockt — auch wenn `is_staff=True` gesetzt ist. Sudo-Mode-Pflicht: erster Zugriff redirected zu `/sudo/?next=/admin-mgmt/`. Facility-Scoping in ModelAdmin: `facility_admin` sieht nur Daten der eigenen Facility, `super_admin` sieht alle. Plus AuditLog auf alle Schreib-Aktionen, RLS gegen Cross-Facility-Leaks. MFA-Pflicht ist separates Issue #788.

### Threat-Model-Bewertung

Wer kann von der CSP-Lockerung profitieren?

1. **Externer Angreifer ohne Session** — kommt nicht auf `/admin-mgmt/`, Login-Lockout + MFA halten ([Threat Model TB1](threat-model.md#tb1--browser--caddydjango))
2. **Externer Angreifer mit valider Admin-Session** — hat ohnehin Vollzugriff; `'unsafe-eval'` ändert sein Schadenspotenzial nicht
3. **XSS in Admin-Template** — der einzige Pfad mit zusätzlichem Schaden. Heute nicht ausnutzbar, weil Architektur-Tests die typischen Injection-Punkte verbieten
4. **Malicious Insider mit Admin-Rolle** — braucht keinen CSP-Bypass

### Trigger für Re-Evaluation (dann Option 2 oder Option 4 angehen)

- `django-unfold` liefert upstream einen `@alpinejs/csp`-kompatiblen Build (Wartungs-Tax fällt weg)
- Externer Audit fordert „strict CSP überall" als verbindliches Akzeptanzkriterium
- Admin-UI-Surface wächst signifikant (z.B. geplante Custom-Admin-UI) — dann lohnt sich der Override-Pfad ohnehin
- Architektur-Tests, die die XSS-Vorbedingungen verhindern (`|safe`, `mark_safe`, Inline-`<script>`), würden für Admin-Templates aufgeweicht

### Verifikation

- Middleware-Logik: [`src/core/middleware/admin_csp_relax.py`](../src/core/middleware/admin_csp_relax.py) (regex-Rewrite nur auf Pfad-Präfix `/admin-mgmt/`)
- Trade-off-Begründung im Settings-Kommentar: [`base.py:246-266`](../src/anlaufstelle/settings/base.py#L246-L266)
- Threat-Model-Eintrag: [`threat-model.md` § TB1, Zeile *S Session-Hijack via XSS*](threat-model.md#tb1--browser--caddydjango)

---

## CSP `style-src` ohne `'unsafe-inline'` (Issue #1660)

**Status:** umgesetzt. `style-src` ist seit #1660 strikt — `'unsafe-inline'` ist entfernt, drei Sonderseiten laufen über sha256-Hashes, `/admin-mgmt/` behält eine eng gescopte Ausnahme.

### Ausgangslage

`'unsafe-inline'` in `style-src` war historisch gewachsen (Django-typisch, im Bestands-Audit als L10 „low-risk" eingestuft). Ein realer Style-Injection-Sink existierte nicht — die Erlaubnis schwächte aber die Defense-in-Depth: gelänge irgendwo eine HTML-/Attribut-Injektion, wären Inline-Styles ein zusätzlicher Hebel (UI-Redressing, Exfiltration über CSS-Attribut-Selektoren).

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

[`AdminCSPRelaxMiddleware`](../src/core/middleware/admin_csp_relax.py) hängt jetzt — analog zu `'unsafe-eval'` in `script-src` (siehe Abschnitt oben / [ADR-025](adr/025-csp-unsafe-eval-admin.md)) — `'unsafe-inline'` in `style-src` an, ausschließlich für HTML-Antworten unter `/admin-mgmt/`. Grund: `unfold/layouts/skeleton.html` rendert einen `<style id="unfold-theme-colors">`-Block, dessen Inhalt aus `UNFOLD["COLORS"]` **dynamisch** zusammengesetzt wird — ein statischer Hash ist prinzipiell unmöglich. Dazu kommen rund ein Dutzend `style=`-Attribute in Unfold-Templates (Progress-Bars, Avatare, Sidebar, Change-List). Ohne die Lockerung verlöre die Admin-UI ihr komplettes Farbschema. Die Trade-off-Bewertung ist dieselbe wie bei `'unsafe-eval'`: MFA-Pflicht, Rollen-Beschränkung, Sudo-Mode, Rate-Limit, AuditLog, RLS. Der Trigger für Re-Evaluation ist ebenfalls derselbe — ein CSP-taugliches Upstream-Unfold.

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
| [`Client.notes`](../src/core/models/client.py#L54-L63) | `Client` | Freie Notizen über die Person | hoch (Klarname-Risiko) |
| [`Case.description`](../src/core/models/case.py#L37-L46) | `Case` | Fall-Beschreibung | mittel |
| [`Episode.description`](../src/core/models/episode.py#L21-L29) | `Episode` | Episoden-Verlauf | mittel |
| [`WorkItem.description`](../src/core/models/workitem.py#L90-L98) | `WorkItem` | Aufgaben-Beschreibung | mittel |
| [`AuditLog.detail`](../src/core/models/audit.py#L181) | `AuditLog` | JSON mit `changed_fields` (nur Feldnamen), technischen Metadaten und `username` bei Login-/Rechte-Vorgängen | mittel (Beschäftigten-`username`); seit #1751 **keine** Klienten-PII mehr in neuen Einträgen |

### Was bereits umgesetzt ist (Audit-Item 16)

`help_text` aller fünf Felder warnt jetzt explizit: **"Nicht feldverschlüsselt — keine Klarnamen oder Art-9-Daten hier vermerken; sensible Inhalte gehören in ein FieldTemplate mit Sensitivity=HOCH."** Migration `0075_freetext_helptext_warnings` zieht das in alle Forms (Django renders `help_text` automatisch unter Form-Widgets).

### Was bewusst nicht jetzt umgesetzt wird (Audit-Item 17)

Feldweise Encryption (`Client.notes`, `Case.description` als verschlüsselte Felder) ist im als **§ B.5 Item 17, Aufwand M** klassifiziert. Voll-Implementation würde:
- Schema-Migration (TextField → JSONField mit `{"__encrypted__": True, "value": ...}`)
- Existing-Data-Migration (Bestand verschlüsseln)
- Search/Sort/Export anpassen — `Client.notes` wird in Detail-Views, Exports und ggf. Volltext-Suche gerendert
- Decrypt-Pfad in allen Views, die das Feld lesen

### Trade-off

**Aktueller Schutz:**
- Backup-Encryption AES-256-CBC + PBKDF2 mit `BACKUP_ENCRYPTION_KEY`
- RLS + Role-Mixin verhindern Cross-Facility-Reads
- DSGVO-Anonymize-Cascade (#715) säubert die **vier Fach-Freitexte** (`Client.notes`, `Case.description`, `Episode.description`, `WorkItem.description`) — **nicht** `AuditLog.detail`: das Audit-Log ist append-only (DB-Trigger aus Migration `0024` + HMAC-Kette), eine nachträgliche Redaktion ist dort strukturell unmöglich. Der einzige Hebel ist Minimierung **beim Schreiben**; seit #1751 enthalten neue Einträge deshalb keine Klienten-PII mehr (Dateiname → HMAC + Endung, kein Meilenstein-Titel, kein Pseudonym im Export-Eintrag). Alt-Zeilen bleiben unverändert stehen — siehe [§ Audit-Log-Restdaten überleben die Art.-17-Löschung](#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753)

**Wenn jetzt durchgezogen:** ~1–2 Tage Arbeit + Risiko in Search/Export, ohne Live-Feedback aus Pilot zu haben.

### Trigger für Re-Evaluation (Audit-Item 17 angehen)

- Pilot-Einrichtung beginnt v1.0-Roll-Out — dann wird das Risiko real
- Externer Audit fordert Encryption-at-Rest auch für freie Notizen
- DPA mit Pilot-Träger fordert Field-Level-Encryption explizit
- Backup-Storage wechselt zu System ohne Object-Lock/Encryption-at-Rest

### Verifikation

- Migration `0075` zeigt die neuen `help_text`-Werte in allen vier ModelForms (Client/Case/Episode/WorkItem). `make migrate` ohne Fehler.
- Threat-Model-Eintrag: [`threat-model.md` § TB2/TB3, Zeile *I Sensitive Felder ohne Encryption*](threat-model.md#tb2tb3--django--postgresql)

---

## Audit-Log-Restdaten überleben die Art.-17-Löschung (Issue #1753)

**Status:** Akzeptiertes Restrisiko, seit #1751 für **neue** Einträge abgemildert. Betriebliche Gegenmaßnahme: Aufbewahrungsfrist nicht abschalten.

### Beobachtung

Die Anonymisierung einer Person (Art. 17) räumt die Fach-Tabellen ab, **nicht** das Audit-Log: `AuditLog` ist append-only (DB-Trigger `auditlog_immutable` aus Migration `0024`, `ENABLE ALWAYS` seit `0107`, plus HMAC-Verkettung). `anonymize_client()` ([`src/core/services/client/main.py`](../src/core/services/client/main.py)) fasst es nicht an — und **kann** es nicht, ohne die Kette zu brechen. Was in einer Audit-Zeile steht, bleibt dort bis zum Pruning stehen.

Daraus folgen drei Fälle, in denen personenbeziehbare Restdaten die Löschung überdauern:

1. **Bis zum Pruning** — Regelfall: bis zu `Settings.auditlog_retention_months` (Default 24 Monate) nach dem Eintrag.
2. **Dauerhaft bei `auditlog_retention_months = 0`** — der Wert schaltet das Pruning ab ([`audit_pruning.py`](../src/core/retention/audit_pruning.py): `months <= 0` → No-op). Dann überlebt **jede** Audit-Zeile jede Löschung, unbegrenzt.
3. **Dauerhaft bei `SECURITY_VIOLATION`** — die Aktion steht in `PRUNE_EXEMPT_ACTIONS` und wird nie gepruned, unabhängig von der Frist. Dasselbe gilt für `RETENTION_RUN_COMPLETED` und `AUDIT_PRUNE_CHECKPOINT` (beide tragen nur System-Metadaten) sowie — seit #1770 — für `EXPORT`-Einträge, die eine **Berichts-Freigabe** sind (`RELEASE_EXPORT_Q`: Berichtszeitraum **und** herausgegebenes Artefakt; Datenbasis der Freigabe-Übersicht, siehe § Disclosure-Analyse K4a). Die Grenze hängt bewusst **nicht** an der Aktion: `EXPORT` ist eine Sammelklasse, unter der auch die Art.-15-Datenauskunft einer Person, die DSGVO-Dokument-Downloads (beide ohne Zeitraum) und jeder **Bildschirm-Aufruf** des externen Berichts (Zeitraum, aber kein Artefakt) laufen — die alle bleiben der Frist unterworfen. Was in der Berichts-Teilmenge dauerhaft stehen bleibt, sind Zeitraum, Format, die Empfänger-/Zweck-**Freitexte** und die handelnde Person; Klientenbezug ist dort nicht vorgesehen, aber die Freitextfelder sind Freitext — ihre Datenminimierung ist eine Betreiber-Obliegenheit (Empfehlung im Admin-Handbuch), keine technische Zusicherung.

### Was bereits umgesetzt ist (#1751, Punkt 5)

Weil nachträgliche Redaktion strukturell ausgeschlossen ist, ist **Minimierung beim Schreiben** der einzige Hebel. Alle `detail`-Schreibstellen wurden inventarisiert; drei führten Klienten-PII im Klartext und schreiben sie seither nicht mehr:

| Schreibstelle | Vorher | Jetzt |
|---|---|---|
| File-Vault-Reject (`SECURITY_VIOLATION`) | Original-Dateiname im Klartext (Uploads heißen im Feld regelmäßig nach der betroffenen Person) | `filename_hash` (stabiler HMAC, Wiederholungen bleiben korrelierbar) + `filename_ext` als Typmarker — zentral in [`log_attachment_violation`](../src/core/services/file_vault/audit.py), keine der neun Aufrufstellen kann die Minimierung umgehen |
| `MILESTONE_DELETE` | Meilenstein-`title` (derselbe Freitext, den die Anonymisierung tilgt) | nur `case_id`; Zuordnung über `target_id` |
| Art.-15-/20-Export | `pseudonym` | nur `format`; Zuordnung über `target_id` |

Der File-Vault-Fall war der schärfste: `SECURITY_VIOLATION` ist prune-exempt, der Klartextname hätte also Frist **und** Anonymisierung überlebt.

Bewusst **nicht** umgestellt ist der `username` bei fehlgeschlagenem Login (einziger Nachweis, *was* versucht wurde — bei Angriffen existiert der User nicht, `AuditLog.user` ist `NULL`) und bei Rollenwechsel/Deaktivierung/Löschbestätigung (wegen `on_delete=SET_NULL` die einzige dauerhafte Zuschreibung einer Rechte-Eskalation). Beides sind **Beschäftigten**-, keine Klientendaten.

### Restfläche (akzeptiert)

- **Alt-Zeilen bleiben unverändert.** Vor #1751 geschriebene Einträge tragen die Klartextwerte weiter; sie zu redigieren hieße, die HMAC-Kette zu brechen (und damit genau die Integritätszusicherung aufzugeben, wegen der das Log existiert). Sie verschwinden mit dem regulären Pruning.
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
- Threat-Model-Eintrag: [`threat-model.md` § 6 Bekannte offene Lücken](threat-model.md#6-bekannte-offene-lücken-verweis-auf-audit-reports)

---

## Offline-Bundles nach Anonymisierung: Revoke-Liste + 48-h-TTL als letzte Linie (Issue #1760)

**Status:** Aktiver Widerrufskanal seit #1760; das Restrisiko „dauerhaft offline gebliebenes Gerät" bleibt akzeptiert und ist durch die Bundle-TTL begrenzt.

### Beobachtung

Ein offline mitgenommener Personendatensatz liegt als AES-GCM-verschlüsseltes Bundle in der IndexedDB des Geräts ([ADR-022](adr/022-offline-snapshot-keys.md), [`offline.py`](../src/core/services/system/offline.py)) — mit Pseudonym, Notizen, Fall-Titeln und Ereignisdaten im entschlüsselbaren Klartext, solange die Session lebt. Wird die Person serverseitig anonymisiert, räumte das bis #1760 **nur den Server** ab. Kein Signal erreichte das Gerät:

- Die Revalidierung beim nächsten Online-Kontakt (`revalidateCachedClient` in [`offline-store.js`](../src/static/js/offline-store.js)) purgt lokal nur bei **401/404/410**. Eine anonymisierte Person existiert aber weiter (die Zeile bleibt für Statistik-Aggregate stehen) und liefert **200**.
- Und selbst dieses 200 wird bewusst **nicht** über den lokalen Stand geschrieben, solange zu der Person ungesyncte Änderungen offen sind (`"skipped"`-Zweig — sonst überschriebe ein Refresh nicht replizierte Arbeit). Genau dort überlebte der Klartext.

Damit blieb der gelöschte Datenbestand bis zu 48 h (`BUNDLE_TTL_SECONDS`) auf dem Gerät lesbar. Bezug: #1476 (R20), #1493.

### Maßnahme: personen-genaue Revoke-Liste

Der Server führt seit #1760 eine Widerrufsliste ([`OfflineClientRevocation`](../src/core/models/offline_revocation.py), Migrationen `0126`/`0127`). Beide Anonymisierungs-Einstiege — der harte Pfad in `anonymize_client()` und die K-Anon-Bridge — schreiben in derselben Transaktion wie die Redaktions-Kaskade einen Eintrag. Der Eintrag ist **klartextfrei**: nur Einrichtung, die (ehemalige) Personen-UUID und der Zeitstempel; `client_pk` ist bewusst kein Fremdschlüssel, damit der Widerruf die Zeile überdauern kann.

Der Client holt die Liste unter `GET /api/v1/offline/revocations/` (Rolle Assistenz+, RLS-gescoped, `no-store`, eigenes großzügiges Rate-Limit `RATELIMIT_OFFLINE_REVOCATIONS`) und wendet sie an — **bei jedem Sync** als *erstem* Schritt der Orchestrator-Sequenz und **beim Boot** als eigener, minimaler Lauf unter demselben Web Lock. Der Boot-Check ist notwendig, weil die Sync-Sequenz sonst nur ein `online`-Event oder replaybare Arbeit als Auslöser hätte — ein Gerät, das schlicht wieder benutzt wird, träfe nie auf die Liste. Sein Gate zählt alle vier Bestände, die der Purge tatsächlich anfasst (`clients`, `queue`, `drafts`, `events`); Entwürfe und ungesyncte Ereignisse können ohne gecachte Person existieren, und `conflict`/`dead`-Ereignisse lösen auch den Startup-Drain nicht aus. Der Boot-Poll ist client-seitig auf einen Lauf pro Minute gedrosselt (Zeitstempel in der `meta`-Tabelle), der Sync-Pfad bleibt ungedrosselt.

Drei Design-Entscheidungen mit Sicherheitsbezug:

1. **Widerruf läuft vor dem Replay — als *best effort*, nicht als Sperre.** Er steht als erster Schritt der Sync-Sequenz *vor* `replayQueue`/`replayAllModifiedEvents`; läge er danach, spielte der Replay die gequeuten Schreibzugriffe zu der gerade anonymisierten Person garantiert noch aus. **Er ist aber keine Vorbedingung:** schlägt der Abruf fehl (kein Netz, `429`, Login-Redirect), wird der Fehler geschluckt und der Replay läuft trotzdem — sonst blockierte ein Netzproblem die gesamte Offline-Synchronisation. Die Reihenfolge verkleinert das Fenster, sie schließt es nicht. **Harte Grenze bleiben die serverseitige Anonymisierung und die Bundle-TTL**, nicht dieser Kanal (siehe Restrisiko unten).
2. **Ungesyncte Arbeit wird verworfen, nicht als Dead-Letter erhalten** (fail-safe). Ein Dead-Letter behielte den entschlüsselbaren Klartext genau dort liegen, wo der Widerruf ihn entfernen soll; ein Replay träfe serverseitig ohnehin auf einen anonymisierten Datensatz. Es ist derselbe Löschgrund wie der Sicherheits-Purge bei Rechteentzug (F-10, #1110) und nutzt dieselbe Primitive (`removeOfflineClient(pk, {force: true})`) — kein zweiter Purge-Pfad. Betroffen sind Bundle-Spiegel, ungesyncte Ereignisse, Queue-Einträge und Entwürfe mit Personenbezug.
3. **Der Abruf wird nicht auditiert.** Die Antwort ist PII-frei (opake UUIDs bereits anonymisierter Personen) und wird bei jedem Sync gepollt — ein Eintrag je Abruf würde das append-only Log fluten, ohne einen Datenabfluss zu dokumentieren. Dieselbe Abwägung wie beim audit-freien 304-Zweig der Bundle-Endpunkte. Die Widerrufs-*Erzeugung* hängt am bestehenden `CLIENT_ANONYMIZED`- bzw. Retention-Entscheidungs-Audit.

**Aufbewahrung der Widerrufe:** `OFFLINE_REVOCATION_RETENTION_SECONDS` = 2 × Bundle-TTL (96 h), danach Prune im nächtlichen `enforce_retention`-Lauf. Begründung: ein Bundle ist höchstens 48 h gültig, ein Gerät kann aber exakt am Fristende wieder online gehen und muss den Eintrag dann noch sehen — der doppelte Abstand deckt diese Überlappung plus Uhren-Drift ab, ohne die Liste unbegrenzt wachsen zu lassen. Der Eintrag ist zwar klartextfrei, bleibt aber ein pseudonymer Bezug auf eine bereits gelöschte Person und darf nicht länger liegen, als er einen Zweck hat (Art. 5 Abs. 1 lit. e).

### Restrisiko (akzeptiert)

**Ein Gerät, das nach der Anonymisierung nie wieder online geht, erhält das Signal nicht.** Für diesen Fall bleibt die **48-h-Bundle-TTL die letzte Verteidigungslinie**: `purgeExpired`/`purgeExpiredBundles` verwerfen den Server-Spiegel rein lokal, sobald `expiresAt` überschritten ist, und der Idle-Wipe entzieht davor bereits den Schlüssel (Session-Dauer, Default 30 Min). Ein Widerruf kann diese Grenze prinzipiell nicht unterschreiten — jede Server→Gerät-Zustellung setzt Konnektivität voraus.

**Der Widerruf ist ein Best-Effort-Kanal, keine Garantie.** Er ist eine Verbesserung gegenüber „gar kein Signal", ersetzt aber weder die serverseitige Löschung noch die TTL:

- **Fail-open bei Transportfehlern.** Netzfehler, `429` oder ein Login-Redirect liefern `error`/`ratelimited`; es wird nichts gelöscht (ohne verlässliche Liste *darf* nichts gelöscht werden) — und der nachfolgende Replay läuft trotzdem. Ein gequeuter Schreibzugriff zu einer inzwischen anonymisierten Person kann in diesem Fenster also serverseitig ankommen. `EventForm.clean_client` prüft heute weder `is_active` noch `is_deleted`, weist ihn also nicht ab; eine serverseitige Ablehnung von Schreibzugriffen auf anonymisierte Personen ist als **eigenes Folge-Ticket** vorgemerkt und bewusst nicht Teil von #1760. Bis dahin gilt: der resultierende Eintrag hängt an einem bereits anonymisierten Datensatz und wird vom nächsten Anonymisierungs-/Retention-Lauf miterfasst.
- **Der Boot-Poll ist gedrosselt** (1×/Minute). Innerhalb dieses Fensters wirkt nur der Sync-Pfad.

Zwei weitere, bewusst nicht geschlossene Kanten:

- **Ungesyncte Ereignisse überleben den reinen TTL-Ablauf** (by design, #1353 — kein stiller Datenverlust). Für ein dauerhaft offline gebliebenes Gerät bedeutet das: der Bundle-Spiegel fällt nach 48 h weg, eine offline erfasste Notiz zu derselben Person bleibt bis zum nächsten Online-Kontakt liegen. Sie ist dann der Anlass, nicht das Ergebnis der Löschung — und der Widerruf greift, sobald das Gerät zurückkehrt.
- **Ohne Session-Key räumt der Widerruf nur die Bundle-Zeilen ab** (planer Delete), nicht Queue-/Entwurfs-Zeilen: deren Personenbezug steckt im verschlüsselten Umschlag. Ohne Schlüssel sind sie allerdings auch für einen Angreifer unlesbar; der nächste Lauf mit Schlüssel holt es nach (die Liste ist cursor-frei und damit idempotent).

### Verifikation

- [`test_offline_client_revocation.py`](../src/tests/test_offline_client_revocation.py) — Widerruf entsteht auf beiden Anonymisierungspfaden, Endpoint ist facility-gescoped/audit-frei, Prune-Fenster.
- [`tests/js/offline-revocations.test.mjs`](../tests/js/offline-revocations.test.mjs) — Client-Purge gegen echtes Dexie + echte Krypto (inkl. ungesyncter Arbeit und Entwürfe).
- [`tests/js/offline-revocation-boot.test.mjs`](../tests/js/offline-revocation-boot.test.mjs) — Boot-Gate über alle vier vom Purge betroffenen Tabellen (insb. `drafts`-only und `conflict`-Ereignisse ohne gecachte Person) plus die 1×/Minute-Drossel.
- [`e2e/test_offline_revocation_journey.py`](../src/tests/e2e/test_offline_revocation_journey.py) — Person mitgenommen → anonymisiert → Sync bzw. Boot purgt sie restlos; der gequeute Schreibzugriff wird nicht repliziert.
- Threat-Model: [`threat-model.md` § TB6](threat-model.md), Zeile „**I** Bundle überlebt die Löschung der Person".

---

## `Client.pseudonym` bleibt im Klartext bis post-v1.0 (Issue #717)

**Status:** Bewusste Defer-Entscheidung. Re-Evaluation per Trigger-Liste.

### Beobachtung

[`Client.pseudonym`](../src/core/models/client.py#L35-L39) ist ein
`CharField(max_length=100, db_index=True)` mit zusätzlichem
[`GinIndex` für `gin_trgm_ops`](../src/core/models/client.py#L89-L95).
Trigram-Fuzzy-Search läuft über [`src/core/services/dashboard/search.py`](../src/core/services/dashboard/search.py)
und ist eine zentrale UX-Funktion für Fachkräfte ("Marie" findet auch
"Maria-23"). Bei einem **Backup-Diebstahl** mit Klartext-Pseudonymen ist
direkte Wiedererkennung in Kontaktläden möglich
(Quelle: internes, dev-only).

### Was bereits umgesetzt ist

[`FieldTemplate`-Validator `Sensitivity=HIGH ⇒ is_encrypted=True`](../src/core/models/document_type.py#L243-L256)
erzwingt seit ,
dass HIGH-Felder **immer** encrypted sind. Dieser Teil von Blocker 4 ist
funktional + per Test verifiziert (siehe [`tests/test_field_template_validator.py`](../src/tests/test_field_template_validator.py)).

### Was bewusst nicht jetzt umgesetzt wird

`Client.pseudonym` per `EncryptedTextField` + HMAC-Lookup-Index. Das selbst stuft das in **§ B.5 Strukturell** als **Item 51,
Aufwand L, post-v1.0** ein:

> 51 | `Client.pseudonym`-Verschlüsselung mit HMAC-Lookup-Index | L | hoch

### Trade-off

**Wenn jetzt durchgezogen:**
- Trigram-Fuzzy-Search bricht — entweder UX-Verlust oder HMAC-Bucket-
 Fuzzy mit n-gram HMACs (deutlich teurere Implementierung)
- Sortierung `ordering=["pseudonym"]` ([client.py:82](../src/core/models/client.py#L82))
 liefert auf encrypted Bytes keine sinnvolle Reihenfolge
- `unique_facility_pseudonym`-Constraint braucht HMAC-basierten Unique-Index
- Sämtliche `pseudonym__icontains`-Filter quer durch Views/Forms/Tests
 müssen umgestellt werden

**Aktueller Schutz:**
- Backup ist AES-256-CBC + PBKDF2-verschlüsselt mit `BACKUP_ENCRYPTION_KEY`
 ([`backup.sh`](../scripts/ops/backup.sh)) — Klartext-Pseudonyme nur bei
 Schlüssel-Kompromittierung exponiert
- Off-Site-Hook (Refs #738)
 empfiehlt Object-Lock gegen Ransomware
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

**Status:** Aktiv ab v0.11. Detection-Lücke L2 aus einem internen Sicherheitsbericht 2026-04-26 (dev-only) geschlossen.

Die globale CSP enthält jetzt `report-uri /csp-report/` ([`base.py:266-275`](../src/anlaufstelle/settings/base.py#L266-L275)). Browser POSTen Verstöße als `application/csp-report` (CSP Level 2) oder `application/reports+json` (CSP Level 3) — die View [`CSPReportView`](../src/core/views/csp_report.py) parsed beide Formate, loggt strukturiert als WARNING auf dem `security.csp`-Logger und antwortet `204 No Content`.

**Härtung:**
- Rate-Limit `10/m` pro IP gegen Log-Flooding
- 32-KiB-Body-Cap gegen Payload-Spam
- `csrf_exempt` (Browser-Reports tragen keinen Token; Endpoint ist write-only ⇒ kein CSRF-Risiko)
- Eigener Logger-Namespace `security.csp` (außerhalb des `core`-Loggers, damit Sentry-Integration und pytest-caplog beide funktionieren)

**Detection-Pfad:** Primär landen CSP-Violations in der lokalen Log-Datei (Logger `security.csp`, Level `WARNING`, mit allen Feldern `blocked-uri`, `violated-directive`, `source-file`,...) und werden von dort per Caddy/Filebeat/Loki ausgewertet. Ist `SENTRY_DSN` gesetzt ([`prod.py`](../src/anlaufstelle/settings/prod.py)), erzeugen diese WARNINGs **keine eigenen Sentry-Events:** Die Sentry-`LoggingIntegration` ist bewusst nicht mit einem eigenen `event_level` konfiguriert, sodass ihr Default `event_level=ERROR` gilt — WARNINGs erscheinen daher nur als **Breadcrumb** an einem später erfassten Error-Event, nicht als eigenständige Violation-Meldung im Dashboard. Diese Breadcrumbs durchlaufen den `before_send`/`before_send_transaction`-Scrubber ([`_sentry.py`](../src/anlaufstelle/settings/_sentry.py)), bevor sie das Programm verlassen.

**Bewusste Entscheidung (Noise-/PII-Abwägung):** Kein explizites `event_level`-Capture für `security.csp`. CSP-Reports sind hochvolumig und teils browser-/angreifer-kontrolliert (`blocked-uri`/`source-file` können URLs mit Query-Strings tragen); sie als eigene Sentry-Events zu erfassen, würde Rauschen erzeugen und die Scrub-Grenze für frei befüllbare Report-Felder unnötig ausreizen. Wer CSP-Violations aktiv im Tracker auswerten will, richtet die Alarmierung stattdessen auf den `security.csp`-Log-Stream ein.

**Bewusste Trade-off-Entscheidung:** `report-to` (Reporting API + `Reporting-Endpoints`-Header) ist nicht konfiguriert — `report-uri` deckt alle relevanten Browser ab und wurde in #684 bewusst als ausreichende Implementierung gewählt. Falls die Browser-Spezifikation `report-uri` deprecaten sollte, muss `report-to` ergänzt werden.

---

## GitHub-Repo-Härtung: Workflow-Permissions, SHA-Pinning, Branch Protection (Issue #888)

**Status:** Permissions + SHA-Pinning umgesetzt; Branch-Protection-Ruleset wartet auf Repo-Public-Switch oder GitHub-Pro.

### Hintergrund

Am 11. Mai 2026 veröffentlichte ein Angreifer **84 bösartige Versionen** über 42 `@tanstack/*`-npm-Pakete, später ausgeweitet auf insgesamt ≈169 npm-Pakete und 4 PyPI-Pakete ([„Mini Shai-Hulud"](https://www.aikido.dev/blog/mini-shai-hulud-is-back-tanstack-compromised), [TanStack-Postmortem](https://tanstack.com/blog/npm-supply-chain-compromise-postmortem)). Anlaufstelle ist vom Vorfall selbst **nicht betroffen**:

| Quelle | Geprüft | IoC-Treffer |
|---|---|---|
| [`package-lock.json`](../package-lock.json) | 73 Pakete, alle mit `integrity` | keine |
| [`requirements.txt`](../requirements.txt) / [`requirements-dev.txt`](../requirements-dev.txt) | Django-/Playwright-Stack | keine |
| `.github/workflows/*` | kein `pull_request_target`, kein `id-token: write` | nicht ausnutzbar |

Trotzdem sind die folgenden drei Härtungen als Defense-in-Depth umgesetzt — sie greifen, sobald das Repo öffentlich wird (geplante Demo-Instanz).

### Umgesetzt: Minimal-`permissions:`-Blocks

Alle 7 Workflows haben jetzt einen expliziten `permissions:`-Block. Statt des Default-`GITHUB_TOKEN` mit Schreibrechten läuft jeder Workflow mit dem Minimum:

| Workflow | Top-Level / Job | Permissions |
|---|---|---|
| [`codeql.yml`](../.github/workflows/codeql.yml) | Job | `actions: read`, `contents: read`, `security-events: write` |
| `dev-image.yml` (dev-only) | Job | `contents: read`, `packages: write` (GHCR-Push) |
| [`e2e.yml`](../.github/workflows/e2e.yml) | Top-Level | `contents: read` |
| [`lint.yml`](../.github/workflows/lint.yml) | Top-Level | `contents: read` |
| `perf-nightly.yml` | Top-Level | `contents: read`, `issues: write` |
| [`release.yml`](../.github/workflows/release.yml) | Job | `contents: read`, `packages: write` (GHCR-Push) |
| [`test.yml`](../.github/workflows/test.yml) | Top-Level | `contents: read` |

`issues: write` in `perf-nightly` ist nötig, weil [`peter-evans/create-issue-from-file`](https://github.com/peter-evans/create-issue-from-file) bei einer Budget-Regression automatisch ein Issue anlegt.

### Umgesetzt: SHA-Pinning aller Actions

Alle 42 `uses:`-Referenzen über die 7 Workflows zeigen jetzt auf **40-stellige Commit-SHAs** statt auf Major-Tags. Format: `uses: <owner>/<action>@<sha> # vX.Y.Z`. Dependabot ist über das `github-actions`-Ökosystem in `.github/dependabot.yml` (dev-only) bereits konfiguriert (wöchentlich, Mo 06:00 Europe/Berlin) und aktualisiert SHA + Tag-Kommentar bei neuen Versionen automatisch.

**Warum:** Tags sind mutierbar. Bei einer hypothetischen Action-Repo-Übernahme könnte ein Angreifer einen Tag auf einen bösartigen Commit verschieben — bei Anlaufstelle würde die nächste CI-Run dann den bösartigen Code ausführen. SHA-Pinning blockt diesen Vektor; Dependabot übernimmt das laufende Update.

### Offen: Branch-Protection-Ruleset für `main`

Auf dem aktuellen privaten GitHub-Free-Repo sind Rulesets **nicht verfügbar** — `gh api repos/anlaufstelle/app/rulesets` liefert `403 Upgrade to GitHub Pro or make this repository public`. Sobald das Repo öffentlich wird (oder vorher per Pro-Upgrade), muss folgendes Ruleset auf `main` aktiv sein:

| Regel | Wert |
|---|---|
| Require a pull request before merging | aktiv |
| Required reviewers | 0 (Solo-Repo) — anheben sobald Team-Repo |
| Require status checks to pass | `lint`, `test`, `e2e` |
| Strict status checks (up-to-date with base) | aktiv |
| Block force pushes | aktiv |
| Restrict deletions | aktiv |
| Bypass-Berechtigte | nur Repo-Admin |

**Verifikation nach Aktivierung:** `gh api repos/<owner>/anlaufstelle/rulesets` zeigt das Ruleset; ein Push mit `--force-with-lease` auf einen Test-Branch muss serverseitig abgelehnt werden (nicht auf `main` testen).

### Trigger für Re-Evaluation

- **Repo wird öffentlich** oder GitHub-Pro wird gebucht — Ruleset sofort aktivieren.
- **Erster externer Contributor** öffnet einen Fork-PR — dann zusätzlich `pull_request_target` audit pflicht, `workflow_run`-Trigger meiden.
- **CodeQL-Findings** zu Workflow-Hygiene — über [GitHub Security-Tab](https://github.com/anlaufstelle/app/security) tracken (CodeQL läuft nur auf dem public App-Repo, siehe [`codeql.yml`](../.github/workflows/codeql.yml) `if`-Bedingung).

### Verifikation

- `grep -rE 'uses: [^@]+@v[0-9]+\s*$' .github/workflows/` muss leer sein → kein Tag-Pinning übrig.
- `grep -L 'permissions:' .github/workflows/*.yml` muss leer sein → jeder Workflow hat irgendwo Permissions (Top-Level oder Job-Level).
- CHANGELOG-`[Unreleased]`-Eintrag dokumentiert beide Maßnahmen mit Issue-Referenz.

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
> `by_contact_stage.qualifiziert` und `unique_clients` — werden gegen die **DISTINCT-Personenzahl**
> des Buckets im Berichtszeitraum geprüft, zusätzlich zur Ereigniszahl, und es gilt die strengere der
> beiden. Eine sichtbare Zelle „Unter 18: 7" bedeutet seither 7 *Kontakte* von **mindestens *k*
> Personen**; ist die Personenzahl unbekannt (Alt-Snapshot, retention-bereinigter Monat), wird
> unterdrückt.
> * **Ausnahme `by_contact_stage.anonym`** — hier ist die Prüfgröße **keine** Personenzahl, sondern
> „DISTINCT *verknüpfte* Personen **+** Anzahl der *unverknüpften* Ereignisse". Die Stufe ist als
> „`is_anonymous` ODER `client IS NULL`" definiert; Ereignisse ohne Klienten-FK tragen überhaupt
> keinen Personenbezug und sind untereinander nicht verknüpfbar, weshalb jedes als eigenes
> Individuum zählt (`statistics.py`, `persons_by_contact_stage`; gepinnt in
> `test_statistics.py::TestPersonenzaehlungImZeitraum::test_anonyme_stufe_zaehlt_ereignisse_ohne_personenbezug_je_einzeln`).
> **Konsequenz, die die Zusicherung einschränkt:** die dahinterliegende Personen-Äquivalenzklasse
> kann *kleiner* als *k* sein. Konstruktion bei *k* = 5: eine Person mit 12 `is_anonymous`-Ereignissen
> plus 4 unverknüpfte Ereignisse ergibt Prüfgröße 1 + 4 = 5 ≥ *k* — „anonym: 16" bleibt sichtbar,
> obwohl nur **eine** identifizierbare Person dahintersteht. Bewusst akzeptiert: die unverknüpften
> Ereignisse tragen keine identifizierenden Attribute, an denen eine Re-Identifikation ansetzen
> könnte; die Alternative (unverknüpfte Ereignisse mit 0 Personen bewerten) hätte die Stufe in jeder
> niedrigschwelligen Einrichtung dauerhaft unterdrückt, ohne jemanden zu schützen. Wer die
> Zusicherung zitiert, muss diese Stufe ausnehmen.
> * **Ereignisabgeleitete** Aufschlüsselungen (`by_document_type`, `by_category`, `total_contacts`)
> zählen weiterhin **Ereignisse** (`Count("id")`). Ihre Buckets sind Dokumenttypen, keine
> Personenmengen — eine Personenschwelle hätte dort keine Äquivalenzklasse, auf die sie sich bezöge.
>
> Frühere Fassungen dieses Abschnitts sprachen pauschal von „weniger als *k* **Personen**"; das galt
> bis #1770 für **keine** Zelle und gilt jetzt
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
9 Helfern, ein k-anonymisierter Klient behielt Klartext in `EventHistory`, `DeletionRequest.reason`
und `Activity.summary`. Zwei Ausnahmen sind bewusst hinterlegt (`_redact_live_events` — die
Eintrittsbedingung garantiert `active_events == 0`; `_redact_client_identity` — würde
`age_cluster` löschen und damit den Quasi-Identifikator zerstören, auf dem die k-Anonymität
beruht). Sperrvermerke blockieren seit #1756
**beide** Bulk-Einstiege; Begründung und Bucket-Entscheid in
[ADR-023 Update 2026-08-03](adr/023-k-anonymization-statistik.md).

### Disclosure-Analyse der Berichts-Artefakte (2026-08-03, Issue #1742)

**Status:** Analyse **abgeschlossen und umgesetzt** — **K5**
(#1767), **K0 (a, Fail-safe)**
(#1768), **K6**
(#1769) sowie **K0 (b, Zellkriterium)**,
**K4b**, **K2**, **K1**, **K3 (A1/A3)** und **K4c**
(#1770) sind **mitigiert**; **K4a** bleibt die
akzeptierte Definitionsgrenze, flankiert durch die Freigabe-Übersicht, und **K3-A2** ist ein bewusst
offener Produktentscheid (Spalte „Status" in der Übersicht). Sieben Kanäle untersucht, alle
**bestätigt**; drei sind Implementierungsfehler mit kleinem Aufwand, drei Teilaspekte sind als
k-Anonymitäts-**Definitionsgrenze** akzeptiert. Anlass war die M5-Erweiterung der Suppression auf die
Berichts-Exporte ([ADR-038 § 5](adr/038-reporting-plugin-backends.md)); die dort geschlossenen Kanäle
(Bezugsgrößen in Vollständigkeitsmeldungen, Vier-Term-Identität) sind verifiziert geschlossen, die
hier gelisteten liegen außerhalb dieses Scopes und bestehen größtenteils seit
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
| **K3** | zwei Grundgesamtheiten nebeneinander | teilweise; Ungleichungen akzeptieren | M | 3 | **mitigiert** — A1 + A3 (#1770); A2 = offener Produktentscheid |
| **K4c** | Lesequellen-Divergenz (Snapshot vs. live) | teilweise (Herkunft stempeln) | S | 4 | **mitigiert** (#1770) |
| **K4a** | Zeitraum-Differenz / Partitions-Rückrechnung | **akzeptieren + dokumentieren** || 4 | akzeptiert; Freigabe-Übersicht (#1770) |

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
soll (Einrichtung × Zeitraum × Altersgruppe), darf damit Größe 1 haben und wird trotzdem publiziert
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

*Status: **(a) mitigiert***, **(b) offen**
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
* **Unbekannt ⇒ unterdrückt.** Alt-Snapshots ohne `persons` und Monate, deren historische Personenzahl
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

**Damit korrigiert:** die frühere Zusicherung, Merkmalskombinationen mit weniger als *k* **Personen**
würden unterdrückt, gilt für die personenabgeleiteten Aufschlüsselungen wieder — jetzt auf Tabellen-
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
mit exakt einer Person** sind zeilenscharf bestimmt. Die frühere Fassung dieses Absatzes nannte nur die
„Existenz der Zeilen" und übersah die Kopplung an `unique_clients`.

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
ist kein publizierter Aggregatwert, sondern ein darstellerisches Nebenprodukt der Query-Sortierung
und sie untergräbt gezielt die Sekundärsuppression, deren einziger Zweck es ist, zwei
ununterscheidbare Unbekannte zu hinterlassen. **Restrisiko ehrlich ausweisen:** K5 senkt gepinnte
Zellen um Faktor ~47, eliminiert sie aber nicht — die Restfälle brauchen den Guard.

*Status: **mitigiert*** (#1767).
`_canonicalize_suppressed_order` in [`external_report.py`](../src/core/services/dashboard/external_report.py)
läuft als **einziger** Eingriffspunkt am Ende von `_suppress_small` — nach der Sekundär-Suppression,
damit der Snapshot-/Hybrid-Pfad nicht driftet. Sichtbare Zeilen behalten ihre count-sortierte
Reihenfolge, unterdrückte folgen als Block in kanonischer Ordnung (Altersgruppen aus
`Client.AgeCluster` abgeleitet, Dokumenttypen nach `(category, name)`); Templates, JSON und XML erben
das ohne eigene Anpassung. Die Ordnung benennt seit
#1770 die **Aufrufstelle** über das
Pflicht-Keyword `order` (die frühere Formheuristik war fail-open)
`by_category` fällt seit #1770 unter
`ORDER_CATEGORY` (Namensordnung): die Einfügereihenfolge der Konsolidierung ist **datenabhängig**
eine Kategorie steht dort, wo ihr alphabetisch erster im Zeitraum vorhandener `system_type` steht.
Ohne kanonische Ordnung verriet die Zeilenposition, welcher `system_type` hinter einer unterdrückten
Kleinstzelle steckt (zwei Welten mit identischen publizierten Werten, „Vermittlung" aus
`accompaniment` bzw. aus `referral`, ergaben verschiedene Positionen). `ORDER_INPUT` gilt danach nur
noch für Tabellen mit **fester Schlüsselordnung**: `by_contact_stage` und `ExampleBackend.records`.
Test-gepinnt in
`test_external_report_service.py::TestKanonischeOrdnungUnterdrueckterZeilen` (inkl. des geforderten
Ordnungstests „zwei Welten mit vertauschten Zellwerten erzeugen dieselbe Ausgabe") und
`::TestKanonischeOrdnungInDenWrappern`. K5 senkt für sich genommen nur die Pin-Rate; die Restfälle
deckt der Fixpunkt-Guard (K1, seit #1770
mitigiert).

#### K6 — Die Unterdrückungs-*Entscheidung* der Vollständigkeitsmeldungen

M5 hat die **Zahlen** aus den Vollständigkeitsmeldungen entfernt, wenn sie klein sind — nicht aber die
Tatsache, **dass** entfernt wurde. `_prepare_issue` entscheidet über eine Disjunktion auf **Rohdaten**
(`template is None or count < k or complement < k`); beide Zweige sind invertierbar, weil die
Komplemente an publizierte Größen gekoppelt sind. Tragend ist dabei nicht die Disjunktion, sondern:
eine **bezifferte** Meldung publiziert implizit „mein Komplement ≥ k", also eine untere Schranke
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
braucht es die eigene Regel „Tabellenkörper unterdrücken, wenn die Randsumme selbst unterdrückt ist"
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
Spitzenkategorie aus jedem Träger-/Förderbericht, sobald überhaupt eine Kleinstzelle existiert, mit
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

**Die Kopplungs-Warnung ist eingehalten:** K5 ist seit
#1767 ausgeliefert, der Guard kommt danach.
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
*(Seit #1769 ist `_close_identity_leak`
entfallen — der Schutz ist stärker geworden, nicht schwächer: die Meldungs-Terme sind jetzt
**grundsätzlich** verborgen statt nur situativ.)*

*Severity-Korrektur (geprüft, nicht tragfähig):* Die Lesart „ungemappte Ereignisse ⇒ mindestens ein
Hausverbot/eine Notiz" ist **falsch** — `_UNMAPPED_Q` umfasst auch `system_type IS NULL`, und jeder
einrichtungseigene Dokumenttyp ohne Systemtyp fällt hinein. Korrekt ist nur „mindestens ein Ereignis
eines Typs ohne Berichts-Mapping".

*Empfehlung — teilweise schließen:* **(A1, M)** im Sachbericht `by_age_cluster` um eine Zeile „ohne
Personenbezug" ergänzen, sodass Σ = `total_contacts`; W wird damit eine reguläre, suppressionsfähige
Zelle (gemessen: exaktes W mit 0 < W < k von 8 820 auf **0**). **Drei Auflagen:** die Restzeile muss
**unbedingt** emittiert werden, auch bei W = 0 (sonst ist ihre bloße Existenz die Aussage W ≥ 1); die
Snapshot-Merges müssen sie für Alt-Snapshots **synthetisieren**, nicht bloß tolerieren; und die
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

*Status (A2): **offener Produktentscheid, bewusst nicht umgesetzt***
(#1770, Status quo). Im Beispiel-Sachbericht
bleibt es bei `margin=None` und **ohne** Restzeile; die Begründung steht an
`_SAMPLE_REPORT_1_BREAKDOWNS` im Code.

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
 unterdrücken würde. *Beleg (Regelfall des externen Berichtswesens):* 12 Monats-PDFs + 1 Jahres-PDF
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
schließen** — K4a bleibt die akzeptierte Definitionsgrenze, sie macht ihn sichtbar. Beide Auflagen
der Analyse sind erfüllt:

* **Empfänger-/Zweckfeld.** Beide Felder werden am Export optional erfasst und wandern in das
 bestehende `EXPORT`-Audit-`detail` (JSON, **keine** Schema-Migration auf dem append-only,
 hash-verketteten Log). Sie sind bewusst optional — ein Pflichtfeld erzwänge eine Angabe auch dort, wo
 der Export gar keine Herausgabe ist (Probelauf), und Pflichtfelder ohne Anlass werden mit „x"
 gefüllt. Freitext auf 200 Zeichen gekappt: der Log überlebt seit dieser Welle die Löschfristen und
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

**Rollout-Grenze, ehrlich ausgewiesen — und kein Backfill möglich.** Der Marker
„herausgegebenes Artefakt" ist mit dieser Welle neu; **keine** vor dem Deploy geschriebene Zeile trägt
ihn. Bestands-Freigaben sind deshalb weder prune-exempt noch in der Übersicht sichtbar, und eine
Überlappungswarnung kann gegen sie nicht feuern — für bis zu `auditlog_retention_months` bleibt die
Übersicht damit unvollständig. **Nachtragen ist ausgeschlossen, nicht bloß unbequem:** `detail` geht in
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
Service-Ebene seit #1767
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

**(5) Offen:** ein Invarianten-Test, dass `Client.ContactStage` genau zwei Werte hat — die Identität
anonym + identifiziert + qualifiziert = `total_contacts` hängt allein daran, und ein dritter Wert
bräche sie **still**.

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

Neuen Primärschlüssel in `ENCRYPTION_KEYS` **voranstellen** (alten hinten
behalten) → Alt-Tokens bleiben lesbar. `python manage.py reencrypt_fields`
wickelt danach auch die TOTP-Secrets aktiv unter den neuen Schlüssel um; erst
dann darf der alte Schlüssel entfernt werden.

Der Rewrap ist **concurrency-sicher** gegenüber parallelen MFA-Logins: Ein
`verify_token` schreibt intern zwar ein Voll-`save()` inkl. `key`, doch
`EncryptedTOTPDevice.save()` lässt die `key`-Spalte bei einem Voll-`save()` auf
eine bestehende Zeile mit bereits verschlüsseltem Secret unangetastet — ein
Verify kann einen frischen Rewrap also nicht zurücksetzen (sonst bliebe das
Gerät still unter dem Alt-Schlüssel → Lockout beim Entfernen des Alt-Keys).
Meldet `reencrypt_fields` dennoch Geräte, die trotz Retries unter dem
Alt-Schlüssel blieben, den Lauf **wiederholen** und den Alt-Schlüssel erst nach
einem meldungsfreien Lauf entfernen (der Lauf ist idempotent).

### Verifikation

- [`src/tests/test_totp_secret_at_rest.py`](../src/tests/test_totp_secret_at_rest.py)
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
erzeugt — zählt Fehlversuche seit N9 
**je Quell-IP**. Fremde Falschpasswörter sperren den Login des Opfers von dessen eigener IP damit
**nicht** mehr; der frühere *harte* Victim-Lockout ist geschlossen.

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
`10/h`-Eimer des Opfers füllen → dessen eigener Login-POST bekommt bis zu 1h lang `429`. Gegenüber
dem früheren Zustand deutlich entschärft:

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

## `window.offlineStore` bleibt bewusst unkapselt — CSP ist der Schutzwall (Issue #1343)

**Status:** Design-Entscheidung, bewusst nicht gekapselt. DSFA-/TOM-Einordnung in [ADR-022 § DSFA-/TOM-Einordnung des Offline-Pfads](adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343).

### Beobachtung

Der verschlüsselte Offline-Store exportiert seine **entschlüsselnden** Aggregatoren als globales `window.offlineStore` — u. a. `getDecrypted`, `listDecrypted`, `getOfflineClient`, `listOfflineEventsAggregated`, `listOfflineWorkItemsAggregated` ([`src/static/js/offline-store.js`](../src/static/js/offline-store.js)). Jeder JavaScript-Code, der im Origin läuft, kann diese Methoden aufrufen und damit — bei geladenem Session-Key — Klartext-Offline-Daten lesen.

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

- Export-Stelle: [`src/static/js/offline-store.js`](../src/static/js/offline-store.js) (`window.offlineStore = { … }`)
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

Pro FK besteht die Migration aus einem DROP-RunSQL (löscht den ursprünglichen `NO ACTION`-Constraint) gefolgt von einem ADD-RunSQL (legt den neuen `RESTRICT`/`SET NULL`-Constraint an). Ein Review deckte auf, dass das `reverse_sql` der beiden Operationen zunächst vertauscht war — ein Unapply (`migrate core 0103`) brach dadurch mit „constraint already exists" ab, weil Djangos umgekehrte Ausführungsreihenfolge beim Zurückrollen zuerst versuchte, den bereits existierenden neuen Constraint erneut anzulegen. Korrigiert: `reverse_sql` der DROP-Operation legt den ursprünglichen `NO ACTION`-Constraint wieder an, `reverse_sql` der ADD-Operation löscht den neuen Constraint — beide Richtungen sind jetzt per Test abgedeckt.

### Verifikation

- ORM- und DB-Level-Tests: [`src/tests/test_ondelete_hardening.py`](../src/tests/test_ondelete_hardening.py) — deckt sowohl `user.delete()` über den Django-Collector als auch einen Raw-SQL-`DELETE FROM core_user` (der den Collector umgeht) ab.
- Migrations-Reversibilität: [`src/tests/test_migration_0104_reversible.py`](../src/tests/test_migration_0104_reversible.py) — fährt `migrate core 0103` und wieder `migrate core 0104` innerhalb der Test-Transaktion.
- Doppel-Hold-Handling: [`src/tests/test_retention_dashboard.py`](../src/tests/test_retention_dashboard.py) (`TestRetentionHoldView::test_double_submit_does_not_500`).
- Migration: [`src/core/migrations/0104_ondelete_hardening_legalhold_workitem_deletionrequest.py`](../src/core/migrations/0104_ondelete_hardening_legalhold_workitem_deletionrequest.py)

---

## Passkeys/WebAuthn als zweiter Faktor — Glue-Härtung (Issue #1492)

Passkeys werden über [`django-otp-webauthn`](https://github.com/Stormbase/django-otp-webauthn) als **zusätzlicher** zweiter Faktor neben TOTP angeboten (siehe [ADR-032](adr/032-webauthn-passkeys-2fa.md)). Drei bewusst gesetzte Härtungen:

- **Nur zweiter Faktor, kein passwordless:** `OTP_WEBAUTHN_ALLOW_PASSWORDLESS_LOGIN = False`, `WebAuthnBackend` **nicht** in `AUTHENTICATION_BACKENDS`. Die Ceremonies laufen ausschließlich in einer bereits per Passwort authentifizierten Session — ein Passkey allein loggt niemanden ein.
- **`mfa_verified`-Glue ist der kritische Punkt:** Unser `MFAEnforcementMiddleware` gated über das Session-Flag `mfa_verified`; die Bibliothek markiert nur über django-otps `otp_login`. Die Subklassen in [`src/core/views/mfa_webauthn.py`](../src/core/views/mfa_webauthn.py) setzen das Flag **ausschließlich** nach erfolgreicher Assertion/Registrierung (`complete_auth` bzw. `post`-2xx). Ein Fehlschlag lässt das Flag unberührt — sonst entstünde ein Verify-Bypass. Positiv- **und** Negativpfad sind getestet ([`src/tests/test_mfa_webauthn.py`](../src/tests/test_mfa_webauthn.py) `TestMfaVerifiedGlue`).
- **Passkey nur NEBEN TOTP (Downgrade-Schutz + Recovery-Anker):** Die Registrierung verlangt ein bestätigtes TOTP-Gerät (`check_can_register`). Damit existieren immer Backup-Codes (an der TOTP-Einrichtung provisioniert), kein „passkey-only"-Lockout ist möglich, und das Entfernen eines Passkeys (sudo-pflichtig, `MFAPasskeyDeleteView`) lässt stets TOTP + Backup-Codes zurück.

**Umgebungsbindung:** Passkeys sind an die RP-ID (Registrable Domain) gebunden. RP-ID/Origins werden pro Umgebung gesetzt (dev/test/e2e → `localhost`; prod/devlive/demo → aus `ALLOWED_HOSTS` abgeleitet). Eine spätere RP-ID-Änderung entwertet bestehende Credentials — Nutzer müssten neu registrieren.

### Verifikation

- Glue + Guard + Sudo-Entfernen + Prädikat: [`src/tests/test_mfa_webauthn.py`](../src/tests/test_mfa_webauthn.py)
- AuthZ-Deklaration der neuen Endpoints (Vollständigkeits-Gate): [`src/tests/_authz_expectations.py`](../src/tests/_authz_expectations.py)
- E2E gegen Chrome-Virtual-Authenticator: [`src/tests/e2e/test_mfa_webauthn.py`](../src/tests/e2e/test_mfa_webauthn.py)

---

## Weitere Einstiegspunkte

- [CONTRIBUTING.md § Facility-Scoping & Row Level Security](../CONTRIBUTING.md#facility-scoping--row-level-security)
- [docs/ops-runbook.md § 9](ops-runbook.md) — RLS-Runbook, Rollen, Kill-Switch
- Tiefenanalyse-Audit 2026-04-21 (internes Code-Audit, dev-only) — Security-/DSGVO-/Perf-Audit mit zeilengenauer Verifikation
