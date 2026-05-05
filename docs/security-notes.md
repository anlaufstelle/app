# Security Notes

Dokumentiert bewusste Security-Entscheidungen, die beim Audit der Tiefenanalyse (siehe [docs/audits/2026-04-21-tiefenanalyse-v0.10.md](audits/2026-04-21-tiefenanalyse-v0.10.md)) verifiziert und für die Roadmap festgehalten wurden. Dieses Dokument ist das „Warum wir das so lassen"-Gegenstück zur RLS- und Facility-Scoping-Doku in [CONTRIBUTING.md](../CONTRIBUTING.md#facility-scoping--row-level-security) und zum Ops-Runbook [docs/ops-runbook.md](ops-runbook.md).

---

## `core_user` bleibt RLS-frei (Finding S-2)

**Status:** Design-Entscheidung, kein Fix notwendig.

### Beobachtung

Das `User`-Model hat einen `facility`-FK (nullable, `on_delete=SET_NULL`) in [`src/core/models/user.py`](../src/core/models/user.py), ist aber **nicht** in `DIRECT_TABLES` oder `JOIN_TABLES` der RLS-Setup-Migration [`0047_postgres_rls_setup.py`](../src/core/migrations/0047_postgres_rls_setup.py). Jede andere facility-gescopte Tabelle hat eine `facility_isolation`-Policy; `core_user` fehlt absichtlich.

### Begründung

Zwei Use-Cases sind auf Cross-Facility-Zugriff angewiesen:

1. **Login-Bootstrap.** Beim Anmelden ist die Session noch nicht an eine Facility gebunden — Django-`auth_login` sucht den User über `username` (oder E-Mail beim Password-Reset) und setzt erst danach die Facility in `request.session`. Eine facility-isolierte `core_user`-Policy würde den Login von **jedem** User scheitern lassen, solange der Session-Variablen-Setzer (die [`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py)) nicht schon läuft — klassischer Chicken-and-Egg.
2. **Cross-Facility-Administration.** Administratoren können User mehrerer Facilities verwalten (Passwort-Reset senden, Rollen anpassen, Token-Invite neu generieren). Das ist explizit gewünscht (siehe [`core.admin.UserAdmin`](../src/core/admin.py)), und eine facility-isolierte Policy würde diesen Admin-Workflow unterbinden.

### Andere Verteidigungslinien

Auch ohne RLS-Isolation auf `core_user` gilt:

- **Facility-Scoping im ORM-Layer:** Alle user-facing Views gehen über [`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py), die `request.current_facility` setzt. Queries, die User ausschließlich einer Facility liefern sollen, filtern explizit (z.B. in [`forms/workitems.py`](../src/core/forms/workitems.py): `User.objects.filter(facility=facility, ...)`).
- **Rollen-Gates:** [`AdminRequiredMixin`](../src/core/views/mixins.py) hält Cross-Facility-User-Management Admin-only.
- **AuditLog:** User-Role-Changes und Deaktivierungen werden via `post_save`-Signale geloggt (siehe [`signals/audit.py`](../src/core/signals/audit.py)).

### Verifikation

Der Retro-Audit [#600](https://github.com/tobiasnix/anlaufstelle/issues/600) hat die Liste der RLS-geschützten Tabellen vollständig durchgegangen und `core_user` bewusst **nicht** ergänzt — zusammen mit `core_organization`, `core_facility`, `core_user_groups`, `core_user_user_permissions` bleibt die Bootstrap/Auth-Schicht außerhalb der per-Facility-Policy.

---

## `SESSION_COOKIE_SAMESITE = "Lax"` bleibt (Finding S-5)

**Status:** Design-Entscheidung mit Rationale.

`CSRF_COOKIE_SAMESITE` ist in [`prod.py`](../src/anlaufstelle/settings/prod.py) auf `"Strict"` gesetzt, `SESSION_COOKIE_SAMESITE` bleibt bei `"Lax"`. Grund: **Password-Reset-E-Mail-Links** (Cross-Site → same-site nach Klick) müssen den Session-Cookie mitbringen, sonst landet der User auf der Login-Seite, obwohl der Token noch gültig ist. Django-Default `"Lax"` lässt genau diesen GET-Cross-Site-Flow durch, während POST-Formulare und Fetch-Requests weiter geblockt werden. `"Strict"` würde den E-Mail-Link-Flow aufbrechen.

Wenn wir den Token-Link-Flow (Invite, Passwort-Reset, 2FA-Backup-Download) jemals auf strikt trennen (eigener Host / eigene Subdomain), kann `"Strict"` greifen.

---

## AuditLog `facility_id` ist nullable (Design)

`AuditLog.facility` ist `null=True` (siehe [`models/audit.py`](../src/core/models/audit.py)), weil System-weite Events (z.B. fehlgeschlagene Logins vor dem Facility-Context) keine Facility haben. Diese Zeilen matchen die `facility_isolation`-Policy **nicht** — sie sind nur für RLS-bypassende Rollen (Superuser, direkte DB-Admins) sichtbar. Application-Code ruft NULL-Audit-Logs ohnehin nicht über facility-scoped Views ab.

---

## CSP `'unsafe-eval'` auf `/admin-mgmt/` (Issue [#695](https://github.com/tobiasnix/anlaufstelle/issues/695))

**Status:** Design-Entscheidung, Workaround akzeptiert. Trigger-Liste für Re-Evaluation am Ende.

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
- Architektur-Tests verbieten weiterhin `csrf_exempt`, `|safe`, `mark_safe()`, Inline-`<script>`-Blöcke im gesamten Repo (siehe [`src/tests/test_architecture.py`](../src/tests/test_architecture.py))
- `/admin-mgmt/` ist Admin/Lead-only, MFA-pflichtig, rate-limited, AuditLog auf alle Schreib-Aktionen, RLS gegen Cross-Facility-Leaks

### Threat-Model-Bewertung

Wer kann von der CSP-Lockerung profitieren?

1. **Externer Angreifer ohne Session** — kommt nicht auf `/admin-mgmt/`, Login-Lockout + MFA halten ([Threat Model TB1](threat-model.md#tb1--browser--caddydjango))
2. **Externer Angreifer mit valider Admin-Session** — hat ohnehin Vollzugriff; `'unsafe-eval'` ändert sein Schadenspotenzial nicht
3. **XSS in Admin-Template** — der einzige Pfad mit zusätzlichem Schaden. Heute nicht ausnutzbar, weil Architektur-Tests die typischen Injection-Punkte verbieten
4. **Malicious Insider mit Admin-Rolle** — braucht keinen CSP-Bypass

### Trigger für Re-Evaluation (dann Option 2 oder Option 4 angehen)

- `django-unfold` liefert upstream einen `@alpinejs/csp`-kompatiblen Build (Wartungs-Tax fällt weg)
- Externer Audit fordert „strict CSP überall" als verbindliches Akzeptanzkriterium
- Admin-UI-Surface wächst signifikant (z.B. NLnet-M0 Custom Admin) — dann lohnt sich der Override-Pfad ohnehin
- Architektur-Tests, die die XSS-Vorbedingungen verhindern (`|safe`, `mark_safe`, Inline-`<script>`), würden für Admin-Templates aufgeweicht

### Verifikation

- Middleware-Logik: [`src/core/middleware/admin_csp_relax.py`](../src/core/middleware/admin_csp_relax.py) (regex-Rewrite nur auf Pfad-Präfix `/admin-mgmt/`)
- Trade-off-Begründung im Settings-Kommentar: [`base.py:246-266`](../src/anlaufstelle/settings/base.py#L246-L266)
- Threat-Model-Eintrag: [`threat-model.md` § TB1, Zeile *S Session-Hijack via XSS*](threat-model.md#tb1--browser--caddydjango)

---

## Klartext-Freitexte ausserhalb des Sensitivity-Modells (Issue [#716](https://github.com/tobiasnix/anlaufstelle/issues/716))

**Status:** Inventarisierung + UI-Warnung umgesetzt; feldweise Encryption deferred bis post-v1.0 (Audit-Item 17).

### Inventar (Master-Audit Blocker 3)

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

Feldweise Encryption (`Client.notes`, `Case.description` als verschlüsselte Felder) ist im Master-Audit als **§ B.5 Phase 3 Item 17, Aufwand M** klassifiziert. Voll-Implementation würde:
- Schema-Migration (TextField → JSONField mit `{"__encrypted__": True, "value": ...}`)
- Existing-Data-Migration (Bestand verschlüsseln)
- Search/Sort/Export anpassen — `Client.notes` wird in Detail-Views, Exports und ggf. Volltext-Suche gerendert
- Decrypt-Pfad in allen Views, die das Feld lesen

### Trade-off

**Aktueller Schutz:**
- Backup-Encryption AES-256-CBC + PBKDF2 mit `BACKUP_ENCRYPTION_KEY`
- RLS + Role-Mixin verhindern Cross-Facility-Reads
- DSGVO-Anonymize-Cascade ([#715](https://github.com/tobiasnix/anlaufstelle/issues/715)) säubert auch diese Felder

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

## `Client.pseudonym` bleibt im Klartext bis post-v1.0 (Issue [#717](https://github.com/tobiasnix/anlaufstelle/issues/717))

**Status:** Bewusste Defer-Entscheidung. Re-Evaluation per Trigger-Liste.

### Beobachtung

[`Client.pseudonym`](../src/core/models/client.py#L35-L39) ist ein
`CharField(max_length=100, db_index=True)` mit zusätzlichem
[`GinIndex` für `gin_trgm_ops`](../src/core/models/client.py#L89-L95).
Trigram-Fuzzy-Search läuft über [`services/search.py`](../src/core/services/search.py#L100-L130)
und ist eine zentrale UX-Funktion für Fachkräfte ("Marie" findet auch
"Maria-23"). Bei einem **Backup-Diebstahl** mit Klartext-Pseudonymen ist
direkte Wiedererkennung in Kontaktläden möglich — Master-Audit Blocker 4
([Quelle](audits/anlaufstelle-audit-master.md)).

### Was bereits umgesetzt ist

[`FieldTemplate`-Validator `Sensitivity=HIGH ⇒ is_encrypted=True`](../src/core/models/document_type.py#L243-L256)
erzwingt seit Tier-2 ([Commit `9b7d318`](https://github.com/tobiasnix/anlaufstelle/commit/9b7d318)),
dass HIGH-Felder **immer** encrypted sind. Dieser Teil von Blocker 4 ist
funktional + per Test verifiziert (siehe [`tests/test_field_template_validator.py`](../src/tests/test_field_template_validator.py)).

### Was bewusst nicht jetzt umgesetzt wird

`Client.pseudonym` per `EncryptedTextField` + HMAC-Lookup-Index. Das
Master-Audit selbst stuft das in **§ B.5 Strukturell** als **Item 51,
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
  ([`backup.sh`](../scripts/backup.sh)) — Klartext-Pseudonyme nur bei
  Schlüssel-Kompromittierung exponiert
- Off-Site-Hook (Refs [#738](https://github.com/tobiasnix/anlaufstelle/issues/738))
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

## CSP-Reporting (Issue [#684](https://github.com/tobiasnix/anlaufstelle/issues/684))

**Status:** Aktiv ab v0.11. Detection-Lücke L2 aus dem [Sicherheitsbericht 2026-04-26](audits/2026-04-26-security-bestand.md) geschlossen.

Die globale CSP enthält jetzt `report-uri /csp-report/` ([`base.py:266-275`](../src/anlaufstelle/settings/base.py#L266-L275)). Browser POSTen Verstöße als `application/csp-report` (CSP Level 2) oder `application/reports+json` (CSP Level 3) — die View [`CSPReportView`](../src/core/views/csp_report.py) parsed beide Formate, loggt strukturiert als WARNING auf dem `security.csp`-Logger und antwortet `204 No Content`.

**Härtung:**
- Rate-Limit `10/m` pro IP gegen Log-Flooding
- 32-KiB-Body-Cap gegen Payload-Spam
- `csrf_exempt` (Browser-Reports tragen keinen Token; Endpoint ist write-only ⇒ kein CSRF-Risiko)
- Eigener Logger-Namespace `security.csp` (außerhalb des `core`-Loggers, damit Sentry-Integration und pytest-caplog beide funktionieren)

**Detection-Pfad:** Wenn `SENTRY_DSN` gesetzt ist ([`prod.py`](../src/anlaufstelle/settings/prod.py)), nimmt Sentry's Logging-Integration die WARNINGs automatisch mit — Violations erscheinen mit `csp_violation`-Tag und allen Feldern (`blocked-uri`, `violated-directive`, `source-file`, ...) im Sentry-Dashboard. Ohne Sentry landen sie in der lokalen Log-Datei und können per Caddy/Filebeat/Loki nachgelesen werden.

**Bewusste Lücke:** `report-to` (Reporting API + `Reporting-Endpoints`-Header) ist noch nicht konfiguriert — `report-uri` allein deckt alle relevanten Browser ab. Falls die Browser-Spezifikation `report-uri` deprecaten sollte, muss `report-to` ergänzt werden.

---

## Weitere Einstiegspunkte

- [CONTRIBUTING.md § Facility-Scoping & Row Level Security](../CONTRIBUTING.md#facility-scoping--row-level-security)
- [docs/ops-runbook.md § 9](ops-runbook.md) — RLS-Runbook, Rollen, Kill-Switch
- [docs/audits/2026-04-21-tiefenanalyse-v0.10.md](audits/2026-04-21-tiefenanalyse-v0.10.md) — Security-/DSGVO-/Perf-Audit mit zeilengenauer Verifikation
