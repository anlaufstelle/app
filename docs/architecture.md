# Architektur-Überblick

Der Einstieg für neue Mitwirkende: **wo liegt was, welche Regel gilt wo, und wo steht die
ausführliche Begründung?** Dieses Dokument ist ein *Wegweiser*, keine Enzyklopädie — jeder
Abschnitt endet mit den Links, die den Gegenstand vollständig behandeln. Was hier steht, ist
aus dem Code abgeleitet; im Zweifel gilt der Code (siehe
[CONTRIBUTING.md § Coding Conventions](../CONTRIBUTING.md#coding-conventions)).

- **Setup, Make-Targets, Coding Conventions, PR-Prozess:** [CONTRIBUTING.md](../CONTRIBUTING.md)
- **Warum die Architektur so aussieht:** [docs/adr/](adr/) — der aktuelle Entscheidungsindex steht in [README.md](adr/README.md)
- **Fachliches Domänenkonzept:** [fachkonzept-anlaufstelle.md](fachkonzept-anlaufstelle.md)
- **Betrieb:** [ops-runbook.md](ops-runbook.md) · [admin-guide.md](admin-guide.md)

---

## 1. Der Stack in einem Absatz

Django 6.1 auf Python 3.14, PostgreSQL 18 als einzige Datenbank ([ADR-004](adr/004-postgresql-only.md)),
Frontend server-gerendert mit HTMX + Alpine.js + Tailwind statt SPA
([ADR-003](adr/003-htmx-alpine-tailwind.md)). Kein Task-Queue-Broker: PDFs entstehen synchron
im Request ([ADR-010](adr/010-sync-pdf-generation.md)), Hintergrundarbeit läuft als
`manage.py`-Kommando im Cron-Sidecar ([ops-runbook § 3](ops-runbook.md#3-cron-jobs)).
Ausgeliefert wird als Docker Compose ([ADR-017](adr/017-deployment-topology.md)); der lokale
Stack startet dabei mit einem Kommando und seedet sich selbst, der produktive **nie**
([ADR-049](adr/049-one-command-local-bootstrap.md)).

---

## 2. Schichten — der Weg eines Requests

```
URLconf                src/core/urls/            core: — ein Namespace, _config, _help, _offline, _system
   ▼
Middleware             src/core/middleware/      Facility-Scope → RLS-Session-Variablen,
                                                 MFA-Zwang, Passwortwechsel, Sitzungsfristen
   ▼
View (CBV)             src/core/views/           Rollen-Mixin + Formular + Rendern.
                                                 Keine Business-Logik, kein direkter Schreibpfad.
   ▼
Service                src/core/services/        Die Fachentscheidung. Schreibt, validiert,
                                                 auditiert — der einzige Ort dafür.
   ▼
Model / Manager        src/core/models/          Ein Model je Datei, FacilityScopedManager,
                                                 DB-Constraints und -Trigger als letzte Linie.
```

**Die drei Regeln dieser Schichtung:**

1. **Views delegieren, Services entscheiden.** Ein View liest den Request, wählt ein Formular
   und ruft einen Service. Models importieren `core.services` nie auf Modul-Ebene — der Guard
   `TestServiceLayerDirectionGuard` (`src/tests/test_architecture_guards_services.py`) friert
   die Richtung ein. Begründung: [ADR-002](adr/002-cbvs-and-service-layer.md).
2. **Audit läuft über die typisierten Helfer.** Geschrieben wird ausschließlich über
   `log_audit_event` und seine Geschwister aus
   [`core/services/audit/`](../src/core/services/audit/); ein direktes
   `AuditLog.objects.create(...)` außerhalb der Allowlist blockt
   `TestAuditLogCreationAllowlist`. Jeder `detail`-Schlüssel muss in
   `core/services/audit/detail_policy.py` deklariert sein, Klartext-Freitext gehört nicht
   hinein (`hmac_hash_value` statt Klartext). Der Log ist append-only per DB-Trigger und
   hash-verkettet — [ADR-007](adr/007-auditlog-append-only.md).
3. **Jede Mutation ist ratelimited.** Jede CBV mit `def post(...)` trägt einen
   `@ratelimit`-Decorator; die Ausnahmen sind eine begründete Allowlist im Guard
   `TestRateLimitOnAllMutations`. Tarife: [ADR-026](adr/026-rate-limiting.md).

**Rollen und Zugriff.** Fünf Rollen, vier davon an genau eine Einrichtung gebunden, eine
installationsweit ([ADR-018](adr/018-rollenmodell-superadmin.md)). Das Gate hängt am View
(`core/views/mixins.py`: `SuperAdminRequiredMixin`, `FacilityAdminRequiredMixin`,
`LeadOrAdminRequiredMixin`, `StaffRequiredMixin`, `AssistantOrAboveRequiredMixin`) und am
User-Modell (`is_super_admin`, `is_facility_admin`, …). Achtung, häufige Fehlannahme:
`is_facility_admin` ist eine **exakte** Rollenprüfung — die Systemadministration ist *kein*
Facility-Admin und sieht die Fach-UI einer Einrichtung nicht.

**Navigation ist eine Registry, kein Template-Detail.** Label, Ziel-Route, Sichtbarkeits-Gate,
Aktiv-Familie und Rücklink stehen einmal in [`core/nav.py`](../src/core/nav.py); Sidebar,
mobiles Menü und Bereichsreiter lesen dieselben Objekte. Wer eine Seite hinzufügt, trägt sie
dort ein — sonst schlägt `src/tests/test_nav_registry.py` zu.

**Der Django-Admin ist eine zweite Anwendungshülle**, kein Bereich dieser Anwendung: eigene
`AnlaufstelleAdminSite` unter `/admin-mgmt/` mit Rollen-Gate und Sudo-Modus
([ADR-019](adr/019-custom-admin-site-sudo.md)). Fachobjekte sind dort weitgehend read-only
([security-notes](security-notes.md)); strukturelle Objekte (Organisation, Einrichtung,
Benutzer) werden dort angelegt.

---

## 3. Sicherheitsarchitektur — die fünf tragenden Elemente

| Element | Kurzform | Vertiefung |
|---|---|---|
| **Facility-Scoping + RLS** | `FacilityScopeMiddleware` setzt pro Request `app.current_facility_id` und `app.is_super_admin` als Postgres-Session-Variablen; die RLS-Policies auf allen facility-gebundenen Tabellen filtern darüber. Ohne gesetzte Variablen liefern die Policies **keine** Zeilen. Django-Scoping (`FacilityScopedManager`) ist die erste, RLS die zweite Linie. | [ADR-005](adr/005-facility-scoping-and-rls.md) · [ops-runbook § 9](ops-runbook.md#9-row-level-security-rls) · [CONTRIBUTING § Facility-Scoping](../CONTRIBUTING.md#facility-scoping--row-level-security) |
| **Drei DB-Rollen** | Bootstrap-Superuser legt an; die **App-Rolle läuft mit `NOSUPERUSER NOBYPASSRLS`** — genau daran hängt die Wirksamkeit von RLS —, die Admin-Rolle mit `BYPASSRLS` ist Kommandos wie `export_facility` vorbehalten. Ownership ist von der Laufzeit-Rolle getrennt. | [ADR-020](adr/020-three-role-postgres-model.md) · [ADR-033](adr/033-runtime-owner-role-separation.md) |
| **Feldverschlüsselung** | Fernet/MultiFernet (AES-128-CBC + HMAC) auf Anwendungsebene für sensible Feldwerte, Freitexte, Dateiinhalte und das TOTP-Secret. MultiFernet trägt die Schlüsselrotation. Ohne `ENCRYPTION_KEY` startet Produktion nicht. | [ADR-006](adr/006-fernet-field-encryption.md) · [ADR-014](adr/014-encrypted-file-vault.md) · [ADR-031](adr/031-totp-secret-at-rest.md) |
| **Sensitivity fail-closed** | Dokumentationstyp und Feldvorlage tragen einen Sensibilitätsrang (`normal` < `elevated` < `high`); jede Rolle hat ein Maximum. Ein **unbekannter** Rang bekommt in `core/services/compliance/sensitivity.py` bewusst einen Rang *oberhalb* jedes Rollenmaximums und fällt damit für alle aus der Sicht — nicht auf `normal` zurück. Owner ohne Dokumentationstyp ebenso (Sentinel). | `core/services/compliance/sensitivity.py` · [ADR-039 § 12](adr/039-owner-generische-dateianhaenge.md) |
| **Zweiter Faktor + Sudo-Modus** | TOTP mit gehashten Backup-Codes, optional Passkeys/WebAuthn. In Produktion ist MFA für Systemadministration und Anwendungsbetreuung **sofort** verpflichtend, für die übrigen Rollen nach 30 Tagen Frist. Sensible Aktionen (Zugänge, Einstellungen, Admin) verlangen zusätzlich eine frische Re-Auth im Sudo-Modus. | [ADR-015](adr/015-mfa-totp.md) · [ADR-032](adr/032-webauthn-passkeys-2fa.md) · [ADR-019](adr/019-custom-admin-site-sudo.md) |

Angriffsfläche, Vertrauensgrenzen und die bewusst offen gelassenen Lücken:
[threat-model.md](threat-model.md). Einzelentscheidungen mit Begründung, warum etwas *nicht*
gehärtet wurde: [security-notes.md](security-notes.md). DSGVO-Wegweiser (Artikel → Mechanismus
→ Code): [datenschutz.md](datenschutz.md).

---

## 4. Konfigurierbarkeit — der No-Code-Kern

Jede Einrichtung beschreibt ihre Dokumentationspraxis selbst; es wird dafür kein Code
geschrieben und keine Migration gebaut.

```
DocumentType          ein Formular („Kontakt", „Beratung")
  ├─ entity_type      event | client | case   ← unveränderbar nach dem Anlegen
  ├─ sensitivity      normal | elevated | high
  ├─ report_key       Achse der Berichtszuordnung (ADR-042)
  └─ Blöcke in EINER globalen Sequenz (ADR-036):
       FieldGroup ──── DocumentTypeField ──► FieldTemplate
       (Abschnitt)     (Zuordnung + Position)  (wiederverwendbares Feld,
                                                 facility-weit, 9 Feldtypen)
       FieldCondition  „zeige Feld X nur, wenn Y = Z" (ADR-035)
```

Die Werte selbst liegen als `data_json` am Ereignis, an der Person oder am Fall; Lesen,
Schreiben und Validieren laufen über [`core/services/dynamic_data.py`](../src/core/services/dynamic_data.py)
— entity-neutral, mit Ver-/Entschlüsselung je Feldvorlage.

**Konfigurationspakete (YAML).** Ein Paket beschreibt Feldvorlagen, Dokumentationstypen,
Gruppen, Zuordnungen und Bedingungen einer Einrichtung deklarativ und wird **idempotent**
eingespielt (`manage.py load_package`). Der mitgelieferte Standardkatalog
[`src/core/packages/default.yaml`](../src/core/packages/default.yaml) ist selbst ein Paket —
derselbe Loader stattet damit auch jede frisch angelegte Einrichtung aus
(`core/services/facility_bootstrap.py`). Identität ist der **Slug**, nicht der Name, und Slugs
sind unveränderlich. Format, Loader-Semantik und die Regeln, die weh tun:
[packages.md](packages.md) · [ADR-037](adr/037-konfigurationspakete-dynamische-felder.md).

**Berichts-Backends als Plugins.** Ein neuer Bericht ist eine Datei: ein Modul unter
[`src/core/reporting/backends/`](../src/core/reporting/backends/) mit einer
`DatasetBackend`-Unterklasse und dem `@register_backend`-Decorator. Lazy-Auto-Discovery
registriert es, die generischen Endpunkte `statistics/reports/panel/` und
`statistics/reports/export/` bedienen es — keine Migration, keine URL, kein View. Alles, was
das Haus verlässt, läuft vorher durch `prepare_export()` (K-Anonymität). Vollständige
API-Referenz, „eigenes Backend in fünf Schritten" und Test-Checkliste:
[reporting-plugins.md](reporting-plugins.md) (EN: [en/reporting-plugins.md](en/reporting-plugins.md)) ·
[ADR-038](adr/038-reporting-plugin-backends.md) · [ADR-023](adr/023-k-anonymization-statistik.md).

**Datenübernahme und -herausgabe.** CSV-Import als Assistent (`/import/`, Zuordnung →
Probelauf → Übernahme), Gesamtexport einer Einrichtung als versioniertes JSON
(`manage.py export_facility`, [facility-export-schema.md](facility-export-schema.md)),
Berichtigungen nach Art. 16 als **interner** Vier-Augen-Workflow statt Self-Service
([ADR-041](adr/041-berichtigungsworkflow-intern.md), ergänzt
[ADR-013](adr/013-dsgvo-art16-no-selfservice.md)).

---

## 5. Offline-PWA — Streetwork ohne Netz

Dieselbe Anwendung, offline lauffähig; kein zweiter Client, kein eigenes Datenmodell.

| Baustein | Datei | Aufgabe |
|---|---|---|
| Service Worker | [`static/js/sw.js`](../src/static/js/sw.js) | App-Shell cache-first, HTML/HTMX network-first. Serviert offline die **In-Place-Shells** (`/offline/…`) an den kanonischen URLs, sodass `/clients/`, `/events/new/` und der Zeitstrom offline dieselben Adressen behalten. Multipart-Uploads werden bewusst **nicht** gequeued (503 statt stillem Verlust). |
| Verschlüsselte Ablage | [`offline-db.js`](../src/static/js/offline-db.js) + `crypto.js` | Dexie/IndexedDB; der Inhalt jeder Zeile liegt als `{iv, ct}`-Umschlag vor, Ordnungsmerkmale (`pk`, `clientPk`, `localStatus`, Zeitstempel, Queue-`url`) im Klartext daneben. Der Session-Key wird beim Login clientseitig aus dem Passwort plus einem serverseitigen Salt abgeleitet und als non-extractable `CryptoKey` in IndexedDB gespeichert. Nach Ablauf der Leerlaufzeit löscht die App beim nächsten Ausführen den Key — bei ungesendeter Arbeit nur den Key, sonst auch den Store. Schließen oder Geräteneustart löscht nichts. |
| Verbund-Dateien (Bundles) | `api/v1/offline/bundle/client/<pk>/`, `api/v1/offline/bundle/facility/` | Was ein Gerät mitnimmt: personenbezogenes Bundle (bewusst gepinnt) und ein personenloses Facility-Bundle (Katalog für die Offline-Erfassung). Gegenrichtung: `api/v1/offline/revocations/` sagt, was gelöscht werden **muss**. |
| Sync-Orchestrator | [`sync-orchestrator.js`](../src/static/js/sync-orchestrator.js) | Ein origin-weiter Web Lock, unter dem die gesamte Replay-Sequenz läuft — Queue, geänderte Ereignisse, dann Revalidierung. Pro Origin höchstens **eine** Sync-Sequenz, auch über mehrere Tabs; Key-Wipe-Signale gehen per BroadcastChannel. |
| Konfliktbehandlung | `/offline/conflicts/` | Was beim Replay kollidiert, wird nie still verworfen, sondern zur Entscheidung vorgelegt; Dead-Letter statt Endlos-Retry. |

Schlüssel- und Snapshot-Modell: [ADR-022](adr/022-offline-snapshot-keys.md). Replay-Contract,
Reihenfolge-Garantien und Dead-Letter-Semantik: [ADR-030](adr/030-offline-sync-orchestration-replay-contract.md).

---

## 6. Test-Landschaft

| Schicht | Umfang | Aufruf |
|---|---|---|
| Unit / Service / Form / CBV | Testdateien unter `src/tests/`, außerhalb von `e2e/` | `make test-parallel` (bzw. `make test`) |
| Architektur-Guards | `src/tests/test_architecture_guards_*.py` — Schichtrichtung, Audit-Allowlist, Rate-Limit-Pflicht, Template-/CSP-Regeln, RLS-Abdeckung | Teil des normalen Testlaufs |
| End-to-End (Playwright) | Testdateien unter `src/tests/e2e/`, gunicorn auf Port 8844 im seriellen Default | `make test-e2e-locked` — **serieller Default**; optional `make test-e2e-parallel-locked` mit eigener DB und eigenem Port je Worker |
| JS-Unit (Vitest) | Suiten (`*.test.mjs`) unter `tests/js/` für die sicherheitskritischen Offline-Module (jsdom + fake-indexeddb) | `make test-js` |
| Mutation-Testing | `core.services` + `core.forms`, punktuell statt PR-Pflicht | `make mutation` |

Sudo-Gates liegen in `core/services/security/sudo_mode.py` (`RequireSudoModeMixin`)
und in der AdminSite, nicht in einer Sudo-Middleware.

`make ci` bündelt Lint, Django-Checks, alle Drift-Guards, i18n-Prüfung, mypy und die
Testläufe. Die schweren Ziele gibt es zusätzlich als `-locked`-Varianten, die global
serialisieren — mehrere gleichzeitige E2E-Läufe teilen sich sonst Port 8844 und die
E2E-Datenbank. TDD ist Pflicht für alles außerhalb von `src/tests/e2e/`; Details und
Schichten-Modell: [CONTRIBUTING § Tests](../CONTRIBUTING.md#tests),
[e2e-architecture.md](e2e-architecture.md), [e2e-runbook.md](e2e-runbook.md). Warum die CI
lokal und nicht auf GitHub läuft: [ADR-029](adr/029-local-only-ci.md).

---

## 7. Wo was liegt

```
src/
  anlaufstelle/          Projekteinstellungen (settings/{base,dev,devlive,test,e2e,demo,prod}.py), Root-URLconf
  core/
    models/              ein Model je Datei; FacilityScopedManager, Slug-Immutabilität
    views/               nur CBVs; views/config/ (Formular-Konfiguration), views/system/ (/system/)
    services/            Business-Logik: audit/, compliance/, config/, events/, file_vault/,
                         security/, system/, dynamic_data.py, retention.py …
    retention/           Retention-Strategien, Vorschläge, Legal Holds, Redaktion und Audit-Pruning
    forms/               ModelForms inkl. der dynamischen Feld-Formulare
    middleware/          Facility-Scope/RLS, MFA, Sitzungsfristen, Wartungsmodus
    packages/            YAML-Loader + Standardkatalog (default.yaml)
    reporting/           base.py (Vertrag), registry.py (Discovery), backends/ (die Berichte)
    nav.py               Navigations-Registry (Sidebar, Reiter, Rücklinks)
    management/commands/ enforce_retention, export_facility, load_package, setup_facility, seed …
    migrations/          inkl. der RLS-Policy-Migrationen
  templates/             base.html + core/<feature>/, partials/ für HTMX-Antworten
  static/js/             HTMX/Alpine-Glue plus die Offline-PWA-Module
  tests/                 Unit-/Service-/Guard-Tests; tests/e2e/ die Playwright-Suite
tests/js/                Vitest-Suiten der Offline-Module
docs/                    diese Dokumentation, docs/adr/ die Entscheidungen
```

---

## 8. Nächste Schritte

- **Erste Änderung:** [CONTRIBUTING § 5-Minuten-Quick-Start](../CONTRIBUTING.md#5-minuten-quick-start-docker)
- **Eigenes Plugin:** [CONTRIBUTING § Plugin-Entwicklung](../CONTRIBUTING.md#plugin-entwicklung)
- **Eine Entscheidung nachschlagen:** [docs/adr/README.md](adr/README.md) (Index + wann eine
  neue ADR fällig ist)
- **Begriffe:** [glossar.md](glossar.md) (Datenschutz/Compliance) ·
  [Fachkonzept § 14](fachkonzept-anlaufstelle.md#14-glossar) (Domäne)
