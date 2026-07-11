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
- **AuditLog:** User-Role-Changes und Deaktivierungen werden via `post_save`-Signale geloggt (siehe [`signals/audit.py`](../src/core/signals/audit.py)).

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
| [`AuditLog.detail`](../src/core/models/audit.py#L83) | `AuditLog` | JSON mit `username`, `pseudonym`, `changed_fields` | mittel (Pseudonym + username) |

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
- DSGVO-Anonymize-Cascade (#715) säubert auch diese Felder

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

Anlaufstelle wendet **K-Anonymität** an: Merkmalskombinationen mit weniger als *k*
Personen werden im Aggregat unterdrückt (`count=None`, `suppressed=True`). Schwelle
pro Einrichtung über [`Settings.k_anonymity_threshold`](../src/core/models/settings.py)
(Default **5**). Verdrahtet in den datenschutzfreundlichen externen Berichten
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
- **Beispiel-Sachbericht / Jugendamt-PDF** (`generate_jugendamt_pdf` → `suppress_jugendamt_stats`) — das am ehesten extern zirkulierende Artefakt, Refs #1278.
- **Halbjahres-Sachbericht-PDF** im Standard-(externen-)Modus (`generate_report_pdf` → `suppress_report_stats`); `?internal=1` mit INTERN-Banner (Lead/Admin) bleibt roh — Security-Review R4.
- **Randsummen** (`total_contacts`/`total`) unterhalb der Schwelle sind selbst Kleinstfallzahlen und werden ebenfalls unterdrückt — Security-Review R14.

Gemeinsame Logik (Single Source of Truth): `_suppress_small` / `_suppress_stage_dict` /
`_apply_secondary_suppression` in [`external_report.py`](../src/core/services/dashboard/external_report.py);
die drei Public-Wrapper (`build_external_report`, `suppress_jugendamt_stats`,
`suppress_report_stats`) normalisieren nur die jeweilige Datenform — kein Copy-Paste
der Suppression selbst.

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
([`0101_totp_secret_at_rest`](../src/core/migrations/0101_totp_secret_at_rest.py))
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

## Weitere Einstiegspunkte

- [CONTRIBUTING.md § Facility-Scoping & Row Level Security](../CONTRIBUTING.md#facility-scoping--row-level-security)
- [docs/ops-runbook.md § 9](ops-runbook.md) — RLS-Runbook, Rollen, Kill-Switch
- Tiefenanalyse-Audit 2026-04-21 (internes Code-Audit, dev-only) — Security-/DSGVO-/Perf-Audit mit zeilengenauer Verifikation
