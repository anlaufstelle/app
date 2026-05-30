# Tiefenanalyse Anlaufstelle

Stand: 2026-04-30 
Scope: lokale Repository-Analyse des Arbeitsstands `a2243312cdea06d6eae3c80e6d69ab445203fe65`. Es wurde kein Live-Deployment angegriffen und keine Rechtsberatung erstellt.

## A. Executive Summary

- Reifegrad: funktionierender Pre-Release/Pilot für kleine Einrichtungen, aber nicht als unbegleiteter Produktiveinsatz zu bewerten. Die README markiert v0.10.2 ausdrücklich als "funktionsfähig, aber noch nicht für den Produktiveinsatz freigegeben" (`README.md:5-7`).
- Die Architektur ist bewusst als eine Django-App `core` mit modularem Model-, View- und Service-Schnitt gebaut (`src/core/apps.py:4-7`, `src/core/models/__init__.py:1-19`, `docs/adr/002-cbvs-and-service-layer.md:13-18`). Das ist für die aktuelle Größe nachvollziehbar, ersetzt aber keine klaren Ownership-Grenzen zwischen Dokumentation, Retention, Statistik, Offline und Admin.
- Die Mandantentrennung ist technisch ernst genommen: Facility-Scoping in Views und Middleware, PostgreSQL-RLS mit `FORCE ROW LEVEL SECURITY`, funktionale Cross-Tenant-Tests unter `NOSUPERUSER` (`src/core/middleware/facility_scope.py:36-55`, `src/core/migrations/0047_postgres_rls_setup.py:77-91`, `src/tests/test_rls_functional.py:155-224`). Das größte Risiko liegt im Betrieb: der Produktions-DB-User darf kein Superuser sein, was in Coolify manuell nachgezogen werden muss (`docs/coolify-deployment.md:94-100`).
- Datenschutzfunktionen sind breit vorhanden: Field-Level-Verschlüsselung, Dateivault, Retention, Anonymisierung, AuditLog, Betroffenenexport (`src/core/models/event.py:86-112`, `src/core/services/file_vault.py:258-333`, `src/core/models/client.py:105-203`, `src/core/views/clients.py:241-295`). Gleichzeitig liegen wichtige Freitexte bewusst unverschlüsselt in `Client.notes` und `Case.description` (`src/core/models/client.py:54-63`, `src/core/models/case.py:37-45`).
- Der File-Vault ist defensiv umgesetzt: Erweiterungs-Whitelist, ClamAV fail-closed, Magic-Byte-Prüfung, UUID-Dateiname, verschlüsselte Ablage und Versionskette (`src/core/services/file_vault.py:65-102`, `src/core/services/file_vault.py:123-160`, `src/core/services/file_vault.py:206-333`). Die Dokumentation ist dazu aber uneinheitlich: README/User-Guide sprechen von AES-GCM, der Code nutzt Fernet/MultiFernet (`README.md:107-109`, `docs/user-guide.md:167-170`, `src/core/services/encryption.py:1-6`, `src/core/services/encryption.py:145-206`).
- Die Testsuite ist groß und zielgerichtet: 162 Testdateien, 1.953 Testfunktionen, 348 E2E-Testfunktionen; CI erzwingt Unit/Integration, Deploy-Check, pip-audit, Lockfile-Check, Ruff, Mypy auf Services, Playwright-E2E und CodeQL im Public Repo (`.github/workflows/test.yml:27-131`, `.github/workflows/lint.yml:14-36`, `.github/workflows/e2e.yml:27-57`, `.github/workflows/codeql.yml:11-38`).
- Es gibt konkrete Architekturguards gegen bekannte Fehlerklassen: ungescopte View-Queries, direkte Event-Loads, Encryption-Bypässe und fehlende Rate-Limits (`src/tests/test_architecture.py:9-50`, `src/tests/test_architecture.py:53-128`, `src/tests/test_architecture.py:372-548`).
- Dokumentation und Betriebsrunbooks sind ungewöhnlich umfangreich, aber mehrere sicherheits- und betriebskritische Stellen sind veraltet: Django 5.1 statt 6.0, Admin-URL `/admin/` statt `/admin-mgmt/`, "Anonym"-Option im User-Guide, Healthcheck-Feld `.clamav`, RLS-Tabellenzahl in der Release-Checkliste (`README.md:186-197`, `CONTRIBUTING.md:10-12`, `docs/admin-guide.md:234-240`, `docs/user-guide.md:141-145`, `docs/coolify-deployment.md:51-52`, `docs/release-checklist.md:70-83`).

Top-3 Stärken:

1. RLS plus funktionale Cross-Tenant-Tests unter Non-Superuser-Rolle (`src/tests/test_rls_functional.py:1-18`, `src/tests/test_rls_functional.py:155-224`).
2. Security-CI mit pip-audit, SBOM, Lockfile-Check, CodeQL, Dependabot (`.github/workflows/test.yml:84-131`, `.github/workflows/codeql.yml:11-38`, `.github/dependabot.yml:1-44`).
3. Dateivault mit mehreren serverseitigen Kontrollpunkten vor und nach der Verschlüsselung (`src/core/services/file_vault.py:258-333`).

Top-3 Risiken:

1. RLS wird bei Superuser-DB-Rollen wirkungslos; das ist dokumentiert, aber in Coolify initial manuell zu korrigieren (`src/core/migrations/0047_postgres_rls_setup.py:11-14`, `docs/coolify-deployment.md:94-100`).
2. Unverschlüsselte Freitextfelder können trotz Warnhinweis Art.-9- oder Sozialdaten aufnehmen (`src/core/models/client.py:54-63`, `src/core/models/case.py:37-45`).
3. Statistische PDF-Reports enthalten "Top 5 Personen" mit Pseudonymen und Kontaktzahlen; für externe Träger- oder Förderberichte ist das ein Re-Identifizierungsrisiko (`src/core/services/statistics.py:85-92`, `src/templates/core/export/report_pdf.html:95-113`).

## B. Faktenblock

### Repository-Fakten

| Punkt | Befund | Beleg |
|---|---:|---|
| Letzter Commit | `a2243312cdea06d6eae3c80e6d69ab445203fe65`, 2026-04-30 08:29:54 +0000, Tobias Nix, `docs: Sprachleitlinie 'Klientel -> Person' adoptieren (#604)` | `git log -1 --format` |
| Contributors | 2: Tobias Nix 689 Commits, dependabot[bot] 9 Commits | `git shortlog -sn HEAD` |
| Lizenz | AGPL-3.0-or-later | `pyproject.toml:1-6`, `LICENSE:1-6` |
| AGPL-Netzwerkhinweis im UI | Footer mit Quellcode- und Lizenzlink | `src/templates/base.html:224-230` |
| Python-Version | `>=3.13` | `pyproject.toml:1-6`, `.github/workflows/test.yml:28-34` |
| Django-Version | `django==6.0.4` | `requirements.txt:23-30` |
| Node/Tailwind | Node `>=20`, Tailwind `^3.4.19` | `package.json:4-13` |
| Django-Apps | 1 Projekt-App: `core` | `src/core/apps.py:4-7` |
| Models | 25 Model-Klassen, exportiert über `core.models` | `src/core/models/__init__.py:1-19`, `src/core/models/__init__.py:21-47` |
| View-Module | 29 Module in `src/core/views`; 93 View-/Mixin-Klassen per lokaler Zählung | lokale Shell-Zählung |
| Service-Module | 35 Module in `src/core/services` | lokale Shell-Zählung |
| Form-Module | 7 Module in `src/core/forms` | lokale Shell-Zählung |
| Templates | 88 HTML-Templates | lokale Shell-Zählung |
| Migrationen | 79 Migrationsdateien | lokale Shell-Zählung |
| Tests | 162 `test_*.py`-Dateien, 1.953 Testfunktionen; davon 348 E2E-Testfunktionen | lokale Shell-Zählung |
| Code of Conduct | keine `CODE_OF_CONDUCT*`-Datei gefunden | lokale Shell-Suche |

### LOC nach Sprache

Quelle: `tokei`-Ausgabe im Prompt.

| Sprache | Dateien | Lines | Code | Comments | Blanks |
|---|---:|---:|---:|---:|---:|
| Python | 402 | 56.585 | 45.338 | 2.316 | 8.931 |
| Markdown | 51 | 13.229 | 0 | 9.068 | 4.161 |
| HTML | 88 | 6.086 | 5.702 | 0 | 384 |
| JavaScript | 27 | 3.985 | 3.130 | 628 | 227 |
| JSON | 3 | 909 | 909 | 0 | 0 |
| Shell | 4 | 484 | 333 | 87 | 64 |
| YAML | 3 | 216 | 188 | 14 | 14 |
| CSS | 1 | 126 | 113 | 4 | 9 |
| Dockerfile | 1 | 74 | 41 | 17 | 16 |
| TOML | 1 | 95 | 61 | 23 | 11 |
| Makefile | 1 | 151 | 104 | 20 | 27 |
| Sonstige | 8 | 297 | 37 | 263 | 1 |
| Gesamt | 590 | 82.241 | 55.956 | 12.440 | 13.845 |

### Kritische Dependencies

| Dependency | Version | Relevanz | Beleg |
|---|---:|---|---|
| Django | 6.0.4 | Webframework, Auth, ORM, Security-Middleware | `requirements.txt:23-30` |
| cryptography | 47.0.0 | Fernet/MultiFernet für Feld- und Dateiverschlüsselung | `requirements.txt:19-20`, `src/core/services/encryption.py:1-12` |
| django-csp | 4.0 | CSP-Header | `requirements.txt:31-32`, `src/anlaufstelle/settings/base.py:282-314` |
| django-htmx | 1.27.0 | HTMX Middleware/Requesthandling | `requirements.txt:33-34`, `src/anlaufstelle/settings/base.py:35-42` |
| django-otp | 1.7.0 | TOTP und Backup-Codes | `requirements.txt:35-36`, `src/core/services/mfa.py:1-15` |
| django-ratelimit | 4.1.0 | Login-, Mutation- und Export-Rate-Limits | `requirements.txt:37-38`, `src/core/views/auth.py:42-44` |
| psycopg[binary] | 3.3.3 | PostgreSQL-Treiber | `requirements.txt:51-54` |
| pyclamd | 0.4.0 | ClamAV-Anbindung | `requirements.txt:55-56`, `src/core/services/virus_scan.py:93-142` |
| python-magic | 0.4.27 | Magic-Byte-Prüfung für Uploads | `requirements.txt:65-66`, `src/core/services/file_vault.py:206-255` |
| sentry-sdk[django] | 2.58.0 | optionales Error-Tracking, ohne Default-PII | `requirements.txt:69-70`, `src/anlaufstelle/settings/prod.py:21-29` |
| weasyprint | 68.1 | PDF-Reports und Exporte | `requirements.txt:81-82` |

## C. Befunde nach Dimension

### 1. Architektur & Domain Design

**[SCHWERE: info] Ein-App-Architektur mit Service-Layer ist bewusst gewählt**

Fundstelle: `src/core/apps.py:4-7`, `src/core/models/__init__.py:1-19`, `docs/adr/002-cbvs-and-service-layer.md:13-18`, `src/core/urls.py:93-208`

Beobachtung: Fachlich unterschiedliche Bereiche wie Personen, Ereignisse, Fälle, Retention, Statistik, Offline und DSGVO liegen in einer Django-App `core`. Innerhalb dieser App sind Models, Views, Forms und Services aber modularisiert. ADR-002 verlangt CBVs und Business-Logik in `src/core/services/`.

Auswirkung: Für den aktuellen Umfang ist der Schnitt pragmatisch. App-übergreifende Django-Grenzen, getrennte Migration-Historien oder getrennte App-Permissions entstehen dadurch aber nicht. Die Kohäsion hängt an Review-Disziplin und Architekturguards.

Empfehlung: Die Eine-App-Strategie beibehalten, solange sie aktiv durch Service-Ownership, Architekturtests und Modul-README gestützt wird. Bei weiterem Wachstum zuerst Retention/DSGVO, Statistik/Reporting und Offline als mögliche App-Kandidaten prüfen.

**[SCHWERE: mittel] Dokumentation und Domainmodell behandeln "anonym" unterschiedlich**

Fundstelle: `README.md:56-64`, `src/core/models/client.py:16-19`, `src/core/models/event.py:24-65`, `src/core/services/event.py:493-532`, `src/templates/core/events/create.html:140-145`, `docs/user-guide.md:141-145`

Beobachtung: Die README beschreibt drei Kontaktstufen inklusive "Anonym". Das `Client.ContactStage`-Enum enthält aber nur `identified` und `qualified`; anonyme Kontakte sind Event-Eigenschaft (`client=None`, `is_anonymous=True`). Die UI sagt: "Ohne Person wird der Kontakt als anonym gespeichert." Der User-Guide fordert dagegen das Aktivieren einer Option "Anonym".

Auswirkung: Das Code-Modell ist für niedrigschwellige Kurz- und Strichkontakte sinnvoll, aber die fachliche Sprache ist inkonsistent. Das erschwert Schulung, Support und Testfallbeschreibung.

Empfehlung: "Anonym" in der Doku konsequent als Event-Zustand, nicht als Personenkontaktstufe beschreiben. User-Guide und README an die echte UI anpassen.

**[SCHWERE: mittel] URLs sind überwiegend ressourcenorientiert, enthalten aber viele Verb-Endpunkte**

Fundstelle: `src/core/urls.py:101-180`

Beobachtung: Ressourcenpfade für Clients, Cases, Events, WorkItems und Statistiken sind klar benannt. Gleichzeitig gibt es viele Aktionspfade wie `close/`, `reopen/`, `assign-event/`, `bulk-approve/`, `bulk-defer/`, `hold/`, `dismiss/`, `bulk-status/`.

Auswirkung: Das ist in einem HTMX/CBV-System praktikabel, erhöht aber die Zahl von Autorisierungs- und Rate-Limit-Kanten. Die Architekturtests fangen einen Teil davon ab, aber neue Aktionspfade bleiben ein Review-Hotspot.

Empfehlung: Für jede neue Verb-Route verpflichtend dokumentieren: Rollen-Mixin, Facility-Scoping, Rate-Limit, AuditLog-Aktion und HTMX-/Full-Page-Verhalten.

### 2. Codequalität & Wartbarkeit

**[SCHWERE: info] Architekturtests decken relevante Wartbarkeits- und Sicherheitsregeln ab**

Fundstelle: `src/tests/test_architecture.py:9-50`, `src/tests/test_architecture.py:53-128`, `src/tests/test_architecture.py:372-548`

Beobachtung: Tests verhindern `Model.objects.all` in Views, direkte Event-Loads per `get_object_or_404(Event,...)`, Event-Encryption-Bypässe via `bulk_create`/`update(data_json=...)` und fehlende Rate-Limits auf POST-Handlern beziehungsweise sensiblen GET-Endpunkten.

Auswirkung: Das reduziert Regressionen in den riskantesten Mustern: IDOR, Sensitivity-Leaks, Klartextschreibpfade und Brute-Force-Flächen.

Empfehlung: Diese Tests als verpflichtende Gates behandeln und bei Ausnahmen eine kurze Begründung in der Allowlist erzwingen.

**[SCHWERE: mittel] Typprüfung ist bewusst inkrementell und lässt große Flächen aus**

Fundstelle: `pyproject.toml:45-95`, `.github/workflows/lint.yml:23-36`

Beobachtung: Mypy prüft in CI nur `src/core/services`. Die globale Konfiguration deaktiviert `check_untyped_defs`, ignoriert Missing Imports und nimmt Migrations, Tests und Settings aus.

Auswirkung: Business-Logik ist priorisiert, aber Views, Forms, Models und Template-nahe Kontexte können weiterhin Typfehler enthalten, die erst zur Laufzeit auffallen.

Empfehlung: Als nächste Strict-Zonen `core/forms`, dann `core/views/*` mit hoher Änderungsrate aufnehmen. Nicht das gesamte Repo auf einmal strict schalten.

**[SCHWERE: niedrig] Tooling-Pin ist uneinheitlich**

Fundstelle: `requirements-dev.in:1-13`, `.github/workflows/lint.yml:14-21`

Beobachtung: `requirements-dev.in` fordert `ruff>=0.15.12`, der Lint-Workflow installiert aber fest `ruff==0.15.11`.

Auswirkung: Lokale und CI-Formatierung können bei neuen Ruff-Regeln divergieren. Das ist kein Sicherheitsproblem, aber eine vermeidbare Reibung.

Empfehlung: Ruff in CI aus `requirements-dev.txt` installieren oder den Pin an dieselbe Version angleichen.

### 3. Sicherheit

**[SCHWERE: hoch] RLS ist stark umgesetzt, hängt im Betrieb aber an einer Non-Superuser-Rolle**

Fundstelle: `src/core/migrations/0047_postgres_rls_setup.py:11-14`, `src/core/migrations/0047_postgres_rls_setup.py:77-91`, `src/tests/test_rls_functional.py:1-18`, `src/tests/test_rls_functional.py:155-224`, `docs/coolify-deployment.md:94-100`, `docs/ops-runbook.md:766-790`

Beobachtung: Migration 0047 aktiviert RLS und `FORCE ROW LEVEL SECURITY`; die Middleware setzt `app.current_facility_id` pro Request. Die funktionalen Tests wechseln auf eine dedizierte `NOSUPERUSER`-Rolle. Gleichzeitig dokumentiert Coolify, dass der initiale `POSTGRES_USER` im offiziellen Image Superuser sein kann und manuell per `ALTER ROLE... NOSUPERUSER` korrigiert werden muss.

Auswirkung: Wenn Betreiber diesen Schritt vergessen, bleibt die Django-Schicht zwar wirksam, aber die wichtigste Defense-in-Depth-Schicht gegen Cross-Facility-Leaks wird von PostgreSQL umgangen.

Empfehlung: Beim App-Start oder in `setup_facility` eine harte Prüfung einbauen: aktueller DB-User darf in Produktion nicht `rolsuper=true` sein. Zusätzlich in `/health/` ein nicht-öffentliches oder admininternes RLS-Signal ausgeben.

**[SCHWERE: mittel] MFA-Backup-Codes haben nur 32 Bit Entropie**

Fundstelle: `src/core/services/mfa.py:21-24`, `src/core/services/mfa.py:41-53`, `src/core/views/mfa.py:138-179`

Beobachtung: Backup-Codes werden als `xxxx-xxxx` mit 8 Hex-Zeichen erzeugt, also 32 Bit Entropie. Die Codes werden an `StaticToken(device=device, token=code)` übergeben. Die Verifikation ist rate-limited, aber die Code-Länge bleibt niedrig für Wiederherstellungscodes.

Auswirkung: Online wird das Risiko durch Rate-Limits reduziert. Bei Datenbankzugriff oder Fehlkonfiguration ist die Code-Reserve schwach im Vergleich zu üblichen Recovery-Codes mit mindestens 128 Bit.

Empfehlung: Backup-Codes auf mindestens 128 Bit Entropie erhöhen, serverseitig nur gehashte Tokens speichern oder die konkrete Storage-Semantik von `django-otp` explizit verifizieren und dokumentieren.

**[SCHWERE: mittel] Passwort-Reset speichert E-Mail-Adressen im unveränderlichen AuditLog**

Fundstelle: `src/core/views/auth.py:111-139`, `src/core/models/audit.py:84-90`, `src/core/models/audit.py:105-113`, `src/core/migrations/0024_auditlog_immutable_trigger.py:8-24`, `src/core/models/settings.py:55-62`

Beobachtung: Jede akzeptierte Passwort-Reset-Anfrage schreibt `detail={"email": email}` ins AuditLog. AuditLog-Einträge sind append-only auf Model- und DB-Ebene und werden standardmäßig 24 Monate aufbewahrt.

Auswirkung: Auch falsch eingegebene oder fremde E-Mail-Adressen können langfristig in einem besonders geschützten, aber bewusst schwer änderbaren Log landen. Für DSGVO-Datenminimierung ist das unnötig, wenn `target_id` oder ein Hash für Forensik genügt.

Empfehlung: E-Mail im AuditLog durch normalisierten Hash ersetzen oder nur bei eindeutigem Match `target_id`/User speichern. Für Nicht-Matches kein personenbezogenes Detail persistieren.

**[SCHWERE: info] Upload-Sicherheit ist mehrschichtig und serverseitig**

Fundstelle: `src/core/forms/events.py:193-230`, `src/core/services/file_vault.py:65-102`, `src/core/services/file_vault.py:123-160`, `src/core/services/file_vault.py:206-333`, `src/anlaufstelle/settings/prod.py:103-106`

Beobachtung: Die Form prüft Erweiterung und Größe für UX. Der Service erzwingt Erweiterungs-Whitelist, ClamAV-Scan, Magic-Byte-Match, UUID-Speichername, Verschlüsselung und DB-Cleanup bei Fehlern. Produktion aktiviert ClamAV per Default.

Auswirkung: Direkte/programmgesteuerte Uploadpfade umgehen die Service-Regeln nicht, solange sie `store_encrypted_file` nutzen.

Empfehlung: Den Healthcheck-Drift zu ClamAV beheben (siehe Deploy-Befund) und Uploadtests um EICAR-/Scanner-unavailable-Szenarien in CI oder Smoke ergänzen.

### 4. Datenschutz & Sozialdatenschutz

**[SCHWERE: hoch] Unverschlüsselte Freitextfelder bleiben ein reales Sozialdatenrisiko**

Fundstelle: `src/core/models/client.py:54-63`, `src/core/models/case.py:37-45`, `src/core/models/document_type.py:243-248`

Beobachtung: `Client.notes` und `Case.description` sind normale Textfelder. Die Help-Texte warnen ausdrücklich, dass sie nicht feldverschlüsselt sind und keine Klarnamen oder Art.-9-Daten enthalten sollen. Hochsensitive dynamische Felder müssen dagegen verschlüsselt sein.

Auswirkung: In der Praxis niedrigschwelliger Arbeit werden Freitexte gerade in Stresssituationen für Diagnosen, Aufenthaltsorte, Gewaltvorfälle oder Klarnamen genutzt. Ein Warntext verhindert das nicht zuverlässig. Diese Daten landen dann im Klartext-Backup.

Empfehlung: Freitexte entweder entfernen, in verschlüsselte FieldTemplates migrieren oder mit expliziter "nicht für sensible Inhalte"-Interaktion und periodischer PII-/Art.-9-Review absichern. Für Fälle sollte eine verschlüsselte Fallnotiz als Default angeboten werden.

**[SCHWERE: mittel] Statistikberichte enthalten Pseudonym-Rankings**

Fundstelle: `src/core/services/statistics.py:85-92`, `src/templates/core/export/report_pdf.html:95-113`, `src/core/views/statistics.py:165-254`

Beobachtung: Die Statistik berechnet `top_clients` nach Pseudonym und Kontaktzahl. Der PDF-Report gibt "Top 5 Personen" mit Pseudonym und Kontakten aus. Statistikexporte sind Lead/Admin vorbehalten und rate-limited, können aber fachlich als externe Berichte genutzt werden.

Auswirkung: In kleinen Einrichtungen können wenige Kontakte, Spitznamen und Zeiträume Personen identifizierbar machen. Für Trägerberichte, Fördermittel oder Jugendamt-ähnliche Auswertungen ist das unnötig riskant.

Empfehlung: Pseudonym-Rankings aus Standard-PDFs entfernen oder explizit als interner Report kennzeichnen. Externe Reports sollten nur aggregierte, k-anonymisierte Werte enthalten.

**[SCHWERE: info] Retention und Anonymisierung sind technisch konkret implementiert**

Fundstelle: `src/core/models/settings.py:35-63`, `src/core/models/settings.py:100-117`, `src/core/models/client.py:105-203`, `src/core/management/commands/enforce_retention.py:46-118`

Beobachtung: Einstellungen enthalten Fristen für anonyme, identifizierte und qualifizierte Daten, AuditLog-Retention, K-Anonymität und Datei-Limits. `Client.anonymize` bereinigt Client, Fälle, Episoden, WorkItems, EventHistory, Attachments und DeletionRequests. `enforce_retention` verarbeitet Retention, Aktivitäten, Client-Anonymisierung und AuditLog-Pruning.

Auswirkung: Art.-5-Speicherbegrenzung und Art.-17-Löschung sind nicht nur in der Doku beschrieben, sondern als Jobs und Model-Operationen vorhanden. Die Wirksamkeit hängt aber an Cron/Operations.

Empfehlung: In der Adminoberfläche den letzten erfolgreichen `enforce_retention`-Lauf sichtbar machen und bei überfälligem Job warnen.

### 5. Tests & Qualitätssicherung

**[SCHWERE: info] CI deckt Test, Security-Audit, Lockfiles, Lint, Typecheck, E2E und CodeQL ab**

Fundstelle: `.github/workflows/test.yml:27-131`, `.github/workflows/lint.yml:14-36`, `.github/workflows/e2e.yml:27-57`, `.github/workflows/codeql.yml:11-38`, `.github/dependabot.yml:1-44`

Beobachtung: CI nutzt Python 3.13, PostgreSQL 16, pytest mit Coverage, Django `check --deploy`, `makemigrations --check`, `pip-audit`, CycloneDX-SBOM, Lockfile-Drift-Check, Ruff, Mypy Services, Playwright und CodeQL im öffentlichen Repo. Dependabot gruppiert Pip- und Actions-Updates.

Auswirkung: Supply-Chain- und Regressionserkennung sind überdurchschnittlich stark für ein Pre-Release dieser Größe.

Empfehlung: CI-Ergebnisse in Release-Checkliste und Security Policy weiterhin als harte Gates behandeln.

**[SCHWERE: info] RLS wird funktional und nicht nur strukturell getestet**

Fundstelle: `src/tests/test_rls_functional.py:1-18`, `src/tests/test_rls_functional.py:30-56`, `src/tests/test_rls_functional.py:155-224`

Beobachtung: Die Tests legen eine `NOSUPERUSER`-Rolle an, setzen `app.current_facility_id` und prüfen Cross-Tenant-0-Rows für Client, Event, AuditLog und Activity sowie Fail-closed bei leerer Facility.

Auswirkung: Das adressiert die häufige Schwäche von RLS-Tests, die versehentlich als Superuser laufen und deshalb falsche Sicherheit signalisieren.

Empfehlung: Zusätzlich Produktions-Smoke prüfen, ob der echte Django-DB-User `rolsuper=false` ist.

**[SCHWERE: mittel] Automatisierte Accessibility- und Lasttests fehlen als eigene Qualitätsschicht**

Fundstelle: `src/tests/e2e/test_mobile.py:1-24`, `src/tests/e2e/test_mobile.py:124-140`, `src/tests/e2e/test_mobile.py:219-223`, `src/tests/test_architecture.py:550-595`

Beobachtung: Es gibt mobile E2E-Tests für Viewport, Touch-Targets und Overflow sowie Architekturtests für SVG-Alternativtexte. Eine lokale Suche fand aber keine `axe`, `axe-core`, Lighthouse- oder Kontrast-Testintegration und keine dedizierten Lasttest-Workflows.

Auswirkung: Layout- und Strukturregressionen werden teilweise erkannt, WCAG-2.2-AA-Probleme wie Kontrast, Fokusreihenfolge nach HTMX-Swaps und Screenreader-Verhalten aber nicht systematisch.

Empfehlung: Playwright um axe-core-Smoke für Login, Zeitstrom, Event-Form, Client-Detail, Statistik und AuditLog ergänzen. Zusätzlich einfache Lastprofile für Suche, Zeitstrom und PDF-Export definieren.

### 6. Performance & Skalierbarkeit

**[SCHWERE: info] Kritische Listenpfade haben Indizes und Query-Limits**

Fundstelle: `src/core/models/event.py:73-81`, `src/core/models/case.py:83-87`, `src/core/models/audit.py:96-103`, `src/core/models/client.py:94-99`, `src/core/migrations/0049_statistics_event_flat_mv.py:19-43`

Beobachtung: Events, Cases, AuditLogs und Clients besitzen Indizes auf typische Filter-/Sortierpfade. Für Statistik existiert eine Materialized View mit Indizes.

Auswirkung: Die Datenbankstruktur berücksichtigt die häufigsten Listen- und Dashboard-Abfragen. Das ist relevant für kleine Träger mit schwacher Hardware.

Empfehlung: Materialized-View-Nutzung und Refresh-Zeit in Produktionsmetriken erfassen; bei größeren Instanzen Suche und PDF-Erzeugung zuerst messen.

**[SCHWERE: mittel] Volltextsuche über `data_json__icontains` ist teuer und unscharf**

Fundstelle: `src/core/services/search.py:15-98`

Beobachtung: Die Suche filtert Clients per `pseudonym__icontains`, nutzt Trigram für ähnliche Pseudonyme, sucht Events aber auch per `data_json__icontains=query` und filtert anschließend verschlüsselte oder nicht sichtbare Felder in Python heraus.

Auswirkung: JSONB-Substringsuche ist bei wachsenden Eventmengen teuer und kann verschlüsselte Marker/Token als Kandidaten scannen. Die nachgelagerte Python-Filterung schützt Sichtbarkeit, aber sie verlagert Last in die App und limitiert Ergebnisqualität.

Empfehlung: Eine explizite Suchspalte oder Search-Index-Tabelle für freigegebene, unverschlüsselte Felder einführen. Verschlüsselte und hochsensitive Felder nicht in die allgemeine Volltextsuche einbeziehen.

### 7. Barrierefreiheit & UX

**[SCHWERE: info] Es gibt konkrete A11y-Basics und mobile E2E-Abdeckung**

Fundstelle: `src/templates/base.html:27-29`, `src/templates/base.html:215-235`, `src/templates/core/events/create.html:99-145`, `src/tests/test_architecture.py:550-595`, `src/tests/e2e/test_mobile.py:124-140`, `src/tests/e2e/test_mobile.py:219-223`

Beobachtung: Base-Template enthält Skip-Link und semantischen Main-Bereich; die mobile Navigation hat ein ARIA-Label. Das Event-Formular nutzt Combobox/Listbox-Rollen für die Personensuche. Tests prüfen SVG-Alternativtexte, Touch-Target-Größe und mobilen Overflow.

Auswirkung: Die App adressiert reale Bedienbedingungen wie Smartphone-Nutzung und Tastatur-/Screenreader-Basics, aber nicht vollständig automatisiert.

Empfehlung: Fokusmanagement nach HTMX-Swaps und Formularfehler-Fokus als E2E-Regeln ergänzen.

**[SCHWERE: niedrig] HTML-Sprache ist trotz Sprachumschaltung statisch Deutsch**

Fundstelle: `src/templates/base.html:1-3`, `src/templates/base.html:199-204`, `src/anlaufstelle/settings/base.py:189-198`

Beobachtung: Das Template setzt `<html lang="de">`, während Settings und UI Deutsch/Englisch unterstützen.

Auswirkung: Bei englischer UI bleibt die Dokumentensprache für Screenreader, Übersetzungstools und Suchmaschinen falsch.

Empfehlung: `lang="{{ LANGUAGE_CODE|default:'de' }}"` oder eine entsprechende Context-Variable nutzen.

### 8. Internationalisierung & Lokalisierung

**[SCHWERE: info] i18n-Infrastruktur ist vorhanden**

Fundstelle: `src/anlaufstelle/settings/base.py:60-67`, `src/anlaufstelle/settings/base.py:189-198`, `src/core/middleware/user_language.py:10-34`, `src/templates/base.html:199-204`

Beobachtung: `LocaleMiddleware`, `UserLanguageMiddleware`, `LANGUAGES` und Sprachbuttons sind eingebunden. Lokale Auszählung ergab 887 gettext-Aufrufe.

Auswirkung: Die technische Grundlage für Deutsch/Englisch ist vorhanden.

Empfehlung: Übersetzungsstatus als CI-Check sichtbar machen, mindestens mit Warnung bei fuzzy/untranslated Zunahme.

**[SCHWERE: mittel] Übersetzungen sind nicht vollständig bereinigt**

Fundstelle: `src/locale/de/LC_MESSAGES/django.po`, `src/locale/en/LC_MESSAGES/django.po`, `src/templates/base.html:1-3`

Beobachtung: `msgfmt --statistics` meldet für Deutsch 502 übersetzte, 124 fuzzy und 225 unübersetzte Messages; für Englisch 811 übersetzte, 28 fuzzy und 12 unübersetzte Messages. Zusätzlich ist `html lang` statisch Deutsch.

Auswirkung: Fuzzy-Übersetzungen können fachlich falsche Begriffe erzeugen. In der Sozialarbeit sind Nuancen wie Person, Klientel, Bezugsperson, Hilfeplan und Kontaktstufe relevant.

Empfehlung: Fuzzy-Übersetzungen vor Release schließen und ein Glossar als Review-Kriterium nutzen. `html lang` dynamisieren.

### 9. Deploy & Betrieb

**[SCHWERE: info] Docker-, Proxy- und Backup-Grundlagen sind vorhanden**

Fundstelle: `Dockerfile:1-74`, `docker-compose.prod.yml:1-84`, `Caddyfile:1-11`, `.env.example:1-74`, `scripts/backup.sh:1-17`, `scripts/backup.sh:119-178`, `docs/ops-runbook.md:25-84`, `docs/ops-runbook.md:160-168`, `docs/ops-runbook.md:561-590`

Beobachtung: Das Image baut Wheels und Tailwind, läuft als Non-Root-User, hat einen Healthcheck und ein persistentes Media-Volume. Compose trennt interne und Frontend-Netze, Caddy setzt TLS-nahe Security-Header. Backup-Skripte sichern DB und Medien verschlüsselt, mit Rotation und Restore-Drill.

Auswirkung: Self-Hosting ist für eine kleine Organisation grundsätzlich dokumentiert und technisch vorbereitet.

Empfehlung: Betreiber-Checkliste um echte Pflichtprüfungen ergänzen: DB-User `NOSUPERUSER`, ClamAV erreichbar, Retention-Cron aktiv, Restore-Drill zuletzt erfolgreich.

**[SCHWERE: mittel] Healthcheck-Vertrag und ClamAV-Dokumentation driften auseinander**

Fundstelle: `src/core/views/health.py:16-56`, `docker-compose.prod.yml:33-37`, `docs/coolify-deployment.md:51-52`, `docs/coolify-deployment.md:127-131`, `docs/release-checklist.md:70-75`

Beobachtung: `/health/` gibt `virus_scanner=connected|unavailable|disabled` zurück und setzt bei nicht erreichbarem Scanner nur `status=degraded`, aber HTTP bleibt 200. Dokumente erwarten dagegen `clamav: ok/error` beziehungsweise `jq '.clamav'`. Compose prüft nur, ob die URL mit 200 antwortet.

Auswirkung: Ein ausgefallener Scanner kann im Orchestrator gesund erscheinen, obwohl Datei-Uploads fail-closed scheitern. Gleichzeitig führen veraltete Runbook-Befehle zu falschen Smoke-Ergebnissen.

Empfehlung: Health-Vertrag stabilisieren: entweder Feld `clamav` zusätzlich ausgeben oder Doku ändern. Für Docker-Healthcheck bei `status=degraded` optional Exit != 0 oder separate Readiness/Liveness-Semantik einführen.

**[SCHWERE: niedrig] Off-Site-Backup-Fehler brechen das Backup-Skript bewusst nicht ab**

Fundstelle: `scripts/backup.sh:181-219`, `docs/ops-runbook.md:544-559`

Beobachtung: Off-Site-Sync ist best-effort; Fehler werden geloggt, das Skript beendet sich aber mit Exit 0, damit lokale Backups nicht abgewertet werden.

Auswirkung: Ohne Logmonitoring kann Off-Site-Schutz über längere Zeit ausfallen, obwohl Cron keine Fehler meldet.

Empfehlung: Wrapper oder Monitoring ergänzen, das wiederholte `ERROR:... Off-Site`-Zeilen alarmiert.

### 10. Lizenz, Governance & Nachhaltigkeit

**[SCHWERE: info] AGPL-3.0 ist konsistent eingebunden**

Fundstelle: `pyproject.toml:1-6`, `LICENSE:1-6`, `README.md:137-145`, `src/templates/base.html:224-230`

Beobachtung: Projektmetadaten, Lizenzdatei, README und UI-Footer verweisen auf AGPL-3.0 beziehungsweise AGPL-3.0-or-later. Der Footer enthält Source- und Lizenzlinks.

Auswirkung: Die Netzwerk-Nutzungsklausel ist für gehostete Nutzung sichtbar adressiert.

Empfehlung: Bei Releases zusätzlich prüfen, dass veröffentlichte Container-Images auf denselben Source-Tag zeigen.

**[SCHWERE: mittel] Governance hängt stark an einem Maintainer**

Fundstelle: `SECURITY.md:39-49`, lokale `git shortlog -sn HEAD`-Auswertung

Beobachtung: SECURITY.md beschreibt Solo-Maintainer-Betrieb mit Best-Effort-SLA. Die lokale Commit-Auswertung zeigt 689 Commits von Tobias Nix und 9 von Dependabot.

Auswirkung: Für eine Fachanwendung mit Art.-9-/Sozialdaten ist Bus-Factor ein Betriebsrisiko, insbesondere bei Sicherheitsmeldungen und Releases.

Empfehlung: Mindest-Governance definieren: zweiter Maintainer für Security Advisories, Backup-Release-Rechte, veröffentlichte Triage-Regeln und regelmäßige Dependency-/Security-Rotation.

**[SCHWERE: niedrig] Code of Conduct, DCO oder CLA sind nicht als aktuelle Governance-Regeln auffindbar**

Fundstelle: `CONTRIBUTING.md:25-32`, `CONTRIBUTING.md:221-230`, lokale Suche nach `CODE_OF_CONDUCT*`, `DCO`, `CLA`

Beobachtung: CONTRIBUTING beschreibt Setup, Tests, PR-Prozess und Coding Conventions. Eine aktuelle Code-of-Conduct-Datei, DCO- oder CLA-Regel habe ich im Repo nicht gefunden.

Auswirkung: Für Open-Source-Beiträge ist unklar, welche Verhaltens- und Rechteübertragungsregeln gelten.

Empfehlung: Code of Conduct ergänzen. Für DCO/CLA bewusst entscheiden und in CONTRIBUTING dokumentieren; bei AGPL und möglichem Dual-Licensing ist diese Entscheidung besonders relevant.

### 11. Fachliche Eignung

**[SCHWERE: info] Niedrigschwellige Kontakte ohne Klarnamen sind im Kernmodell vorgesehen**

Fundstelle: `README.md:32-50`, `src/core/models/client.py:35-46`, `src/core/models/event.py:24-65`, `src/core/services/event.py:493-532`, `src/templates/core/events/create.html:140-145`

Beobachtung: Das Produkt richtet sich an Kontaktläden, Notschlafstellen und Streetwork. Das Model hat kein Namensfeld für Clients, Events können ohne Client anonym sein, und die Service-Logik normalisiert "kein Client" bei erlaubtem Dokumentationstyp zu anonym.

Auswirkung: Das passt besser zu niedrigschwelliger Arbeit als Systeme, die vollständige Stammdaten erzwingen.

Empfehlung: Die fachliche Sprache "Person", "Pseudonym", "anonymer Kontakt" und "Kontaktstufe" konsistent halten, damit Nutzende nicht versehentlich Personenakten für reine Strichkontakte anlegen.

**[SCHWERE: mittel] Mehrere Aliase oder Spitznamen pro Person sind nicht modelliert**

Fundstelle: `src/core/models/__init__.py:1-19`, `src/core/models/client.py:35-46`, `src/core/models/client.py:88-99`

Beobachtung: Der Client hat genau ein `pseudonym`; die Unique-Constraint gilt auf `facility+pseudonym`. Ein Alias-/Known-as-Modell ist in den exportierten Models nicht enthalten.

Auswirkung: In niedrigschwelliger Arbeit sind mehrere Spitznamen, Schreibweisen oder Szenenamen häufig. Ohne Alias-Modell wandern Varianten in Freitext oder es entstehen Dubletten.

Empfehlung: `ClientAlias` mit `client`, `alias`, `normalized_alias`, `source`, `created_at` ergänzen. Suche und Duplikaterkennung über Alias und Pseudonym laufen lassen.

**[SCHWERE: mittel] Externe Statistik- und Förderberichte brauchen strengere K-Anonymitätsregeln**

Fundstelle: `src/core/models/settings.py:100-117`, `src/core/services/statistics.py:85-92`, `src/templates/core/export/report_pdf.html:95-113`

Beobachtung: K-Anonymitätsparameter existieren in Settings, Standardstatistiken exportieren aber weiterhin Top-Pseudonyme. Für kleine Fallzahlen ist das fachlich nicht ausreichend anonym.

Auswirkung: Bei Förderberichten oder Trägerauswertungen kann eine Person über Spitzname, Kontaktzahl und Zeitraum re-identifizierbar sein.

Empfehlung: Externe Report-Profile einführen: keine Pseudonyme, Mindestzellgrößen, Unterdrückung kleiner Gruppen und klare Kennzeichnung "intern" vs. "extern".

### 12. Dokumentation

**[SCHWERE: info] Dokumentationsumfang ist hoch und adressiert verschiedene Zielgruppen**

Fundstelle: `README.md:131-145`, `docs/adr/002-cbvs-and-service-layer.md:1-37`, `docs/ops-runbook.md:25-84`, `docs/dsgvo-templates/av-vertrag.md`, `docs/dsgvo-templates/dsfa.md`

Beobachtung: README, Admin-Guide, User-Guide, ADRs, Threat Model, Ops-Runbook, Release-Checkliste, DSGVO-Vorlagen und Screenshots sind vorhanden.

Auswirkung: Betreiber und Entwickler haben eine gute Ausgangslage. Der Wert sinkt aber dort, wo Dokumentation nicht synchron zum Code ist.

Empfehlung: Doku-Drift als Release-Gate behandeln, nicht als Nacharbeit.

**[SCHWERE: mittel] Mehrere Doku-Stellen sind konkret veraltet oder widersprüchlich**

Fundstelle: `README.md:186-197`, `CONTRIBUTING.md:10-12`, `CONTRIBUTING.md:225-227`, `requirements.txt:23-30`, `docs/admin-guide.md:234-240`, `src/anlaufstelle/urls.py:24-31`, `docs/user-guide.md:141-145`, `src/templates/core/events/create.html:140-145`, `README.md:107-109`, `docs/user-guide.md:167-170`, `src/core/services/encryption.py:1-6`, `src/core/services/encryption.py:145-206`, `docs/release-checklist.md:70-83`, `docs/ops-runbook.md:766-790`

Beobachtung: README und CONTRIBUTING nennen Django 5.1, während Lockfile und CI Django 6.0 nutzen. Admin-Guide nennt `/admin/`, die URL-Konfiguration verwendet `/admin-mgmt/`. User-Guide beschreibt eine "Anonym"-Option, die UI nicht zeigt. README/User-Guide nennen AES-GCM für Anhänge, der Code verschlüsselt per Fernet. Release-Checkliste erwartet `.clamav` und 16 RLS-Zeilen, während Healthcheck und Ops-Runbook andere Fakten enthalten.

Auswirkung: Diese Drift betrifft nicht nur Stil, sondern Betrieb, Schulung und Sicherheitsvalidierung.

Empfehlung: Eine Doku-Konsistenz-Checkliste in den Release-Prozess aufnehmen: Versionsmatrix, Admin-URL, Health-JSON, RLS-Tabellenzahl, Kryptografiebegriffe, zentrale User-Flows.

## D. Priorisierte Maßnahmenliste

| Reihenfolge | Maßnahme | Aufwand | Impact | Bezug |
|---:|---|---|---|---|
| 1 | Prod-Startcheck: aktueller Django-DB-User darf nicht `rolsuper=true` sein; bei Prod fail-closed abbrechen | S | hoch | RLS-Befund |
| 2 | Healthcheck/Doku für ClamAV stabilisieren: `virus_scanner` vs. `clamav`, degraded-Semantik, Compose-Health | S | mittel | Deploy-Befund |
| 3 | Passwort-Reset-Audit minimieren: E-Mail durch Hash oder User-ID ersetzen | S | mittel | Sicherheitsbefund |
| 4 | Doku-Drift in README, CONTRIBUTING, Admin-Guide, User-Guide, Release-Checkliste korrigieren | S | mittel | Dokumentationsbefund |
| 5 | MFA-Backup-Codes auf mindestens 128 Bit erhöhen und Storage-Semantik dokumentieren | S | mittel | Sicherheitsbefund |
| 6 | Standard-PDF-Reports ohne Top-Pseudonyme ausgeben; internen Report separat kennzeichnen | M | hoch | Datenschutz/Domain-Fit |
| 7 | `Client.notes` und `Case.description` entschärfen: verschlüsselte Alternative oder Migration zu FieldTemplates | M/L | hoch | Datenschutzbefund |
| 8 | Alias-Modell für Personen ergänzen und Suche/Dublettenprüfung daran anbinden | M | mittel | Domain-Fit |
| 9 | JSONB-`icontains`-Suche durch expliziten Suchindex für erlaubte Felder ersetzen | M/L | mittel | Performance-Befund |
| 10 | `html lang` dynamisieren und fuzzy/untranslated `.po`-Einträge abbauen | S/M | mittel | A11y/i18n |
| 11 | Playwright um axe-core-Smokes und Fokusmanagement nach HTMX-Swaps erweitern | M | mittel | Tests/A11y |
| 12 | Mypy-Strict-Zonen auf Forms und ausgewählte Views ausweiten | M | mittel | Codequalität |
| 13 | Off-Site-Backup-Fehler alarmieren, obwohl lokales Backup erfolgreich bleibt | S | mittel | Deploy-Befund |
| 14 | Code of Conduct und DCO/CLA-Entscheidung dokumentieren | S | niedrig/mittel | Governance |
| 15 | Lastprofile für Suche, Zeitstrom, Statistik und PDF-Export einführen | M | mittel | Performance/QA |

## E. Offene Fragen

- Läuft die reale Produktionsdatenbank bereits mit einem `NOSUPERUSER`-Django-User? Das Repo dokumentiert den Schritt, kann aber den Ist-Zustand nicht beweisen.
- Werden Statistik-PDFs intern genutzt oder an Träger, Fördermittelgeber, Jugendamt oder externe Stellen weitergegeben?
- Welche realen Datenmengen sind zu erwarten: Events pro Monat, Personen pro Einrichtung, Attachments pro Event, gleichzeitige Nutzer?
- Müssen mehrere Aliase, Spitznamen oder Schreibweisen pro Person fachlich verbindlich abgebildet werden?
- Welche Rechtsgrundlagen setzen konkrete Betreiber ein: Einwilligung, gesetzliche Pflicht, Vertrag, SGB-X-Kontext? Die Templates ersetzen keine ausgefüllte Betreiber-DSFA.
- Gibt es eine definierte Incident-Rotation oder zweite Person für Security Advisories und kritische Releases?
- Welche Barrierefreiheitszielgruppe ist priorisiert: Screenreader, Tastatur, mobile Nutzung, leichte Sprache, mehrsprachige Teams?
- Soll ClamAV-Ausfall eine Liveness-Störung sein oder nur Readiness/Degraded? Das ist eine Betriebsentscheidung mit UX-Auswirkung.

## F. Bewusst nicht bewertet

- Kein Live-Penetrationstest, keine dynamische Security-Analyse und keine Prüfung eines realen Deployments.
- Keine juristische Bewertung von DSGVO, BDSG, SGB X oder § 203 StGB; nur technische und dokumentarische Beobachtungen.
- Keine vollständige Dependency-License-Compliance aller transitive Dependencies.
- Kein vollständiger `pip-audit`-Lauf im Rahmen dieser Analyse; bewertet wurde die CI-Konfiguration, nicht ein aktueller Vulnerability-Scan.
- Keine echte Lastmessung mit Produktionsdaten; Performancebefunde beruhen auf Codepfaden und Indizes.
- Keine visuelle Screenshot-Prüfung, keine Kontrastmessung und kein Screenreader-Test.
- Keine Aussage zur Aktualität der Screenshots oder zur tatsächlichen Bedienbarkeit durch konkrete Sozialarbeitende ohne Usability-Test.
