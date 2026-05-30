# Tiefenanalyse Anlaufstelle

Audit-Datum: 2026-04-28 
Repository: `https://github.com/anlaufstelle/app` 
Geprüfter Stand: `35e0f5b31e972c7345940433c572372d6b1736ee` (`chore: Release v0.10.2`, `2026-04-28T15:07:57+01:00`). `git ls-remote origin refs/heads/main` zeigte denselben SHA. 
Hinweis: Zeilenangaben beziehen sich auf diesen Commit. Metriken stammen aus lokaler Inventur per `git ls-files`, Python-AST und Shell-Zählungen.

## A. Executive Summary

- **Reifegrad:** fortgeschrittener Pre-Release/Pilot, nicht produktionsfreigegeben. Die README sagt ausdrücklich `Pre-Release (v0.10.2)` und `noch nicht für den Produktiveinsatz freigegeben` (`README.md:7`). Für eine kleine Piloteinrichtung ist der Funktionsumfang breit, aber ein kritischer Retention-/Historien-Befund blockiert produktive Nutzung in einer Sozialdaten-Domäne.
- **Top-Risiko 1:** Automatische Retention löscht Event-Nutzdaten aus `Event.data_json`, kopiert dieselben Werte aber vorher in das unveränderliche `EventHistory.data_before` (`src/core/services/retention.py:566-580`, `src/core/models/event_history.py:59-67`, `src/core/migrations/0012_eventhistory_append_only_trigger.py:10-32`). Das widerspricht der README-Aussage `automatische Löschung nach konfigurierbarer Frist` (`README.md:106-109`).
- **Top-Risiko 2:** RLS ist als Defense-in-Depth modelliert (`src/core/migrations/0047_postgres_rls_setup.py:21-64`, `src/core/middleware/facility_scope.py:47-55`), aber funktionales RLS-Filtering wird in Tests nicht als Non-Superuser geprüft (`src/tests/test_rls.py:1-9`, `src/tests/test_rls.py:166-172`). Zusätzlich ist die Statistik-Materialized-View bewusst ohne RLS (`src/tests/test_statistics_mv.py:323-352`).
- **Top-Risiko 3:** CSV-Export schreibt Pseudonyme und dynamische Feldwerte direkt in CSV-Zellen (`src/core/services/export.py:88-150`), ohne Spreadsheet-Formula-Escaping. Bei Exporten in Excel/LibreOffice ist das ein klassischer CSV-Injection-Pfad.
- **Stärke 1:** Authentifizierung und Security-Defaults sind überdurchschnittlich für einen Pre-Release: Login-Rate-Limits, Account-Lockout, MFA-Middleware und Produktions-Fail-Closed-Settings sind im Code belegt (`src/core/views/auth.py:42-90`, `src/core/services/login_lockout.py:15-38`, `src/core/middleware/mfa.py:45-63`, `src/anlaufstelle/settings/prod.py:37-75`).
- **Stärke 2:** Fachliche Erweiterbarkeit über `DocumentType`/`FieldTemplate` ist real implementiert, inklusive Sensitivität, Pflichtfeldern, Retention und Datei-Feldern (`src/core/models/document_type.py:18-92`, `src/core/models/document_type.py:113-190`). Das passt zum Ziel, unterschiedliche niedrigschwellige Einrichtungen ohne Programmierung zu konfigurieren (`README.md:72-81`).
- **Stärke 3:** Qualitätssicherung ist ernsthaft angelegt: 1.845 Testfunktionen, CI für pytest, deploy checks, pip-audit, lock drift, Ruff und Playwright-E2E (`.github/workflows/test.yml:28-94`, `.github/workflows/lint.yml:14-21`, `.github/workflows/e2e.yml:28-61`).
- **Hauptempfehlung:** Zuerst Retention/EventHistory korrigieren und regressionssicher testen; danach RLS als echte Non-Superuser-Integration testen, CSV-Export härten, Lösch-/Anonymisierungskonzept auf Clients/Cases/Episodes/User erweitern und Governance-Dokumente aktualisieren.

## B. Faktenblock

| Kategorie | Befund | Beleg |
|---|---:|---|
| Letzter Commit | `35e0f5b31e972c7345940433c572372d6b1736ee`, `chore: Release v0.10.2` | `git log -1`, remote verifiziert per `git ls-remote` |
| Contributors | 1 Contributor in lokaler Historie, 120 Commits | `git shortlog -sne HEAD` |
| Lizenz | AGPL-3.0-or-later | `pyproject.toml:1-6`, `README.md:134-138`, `LICENSE:540-550` |
| Python-Version | `>=3.13` | `pyproject.toml:1-6` |
| Django-Version | Direktvorgabe `Django>=5.1,<5.2`, Lock `django==5.1.15` | `requirements.in:1`, `requirements.txt:23` |
| Node/Frontend | Node `>=20`, Tailwind `^3.4.19` | `package.json:4-13` |
| Django-Apps | 1 App: `core` | `src/anlaufstelle/settings/base.py:24-39`, `src/core/apps.py:4-7` |
| Models | 25 Model-Klassen | AST-Inventur; App-Struktur in `CONTRIBUTING.md:391-437` |
| Views | 82 View-Klassen, 3 View-Funktionen | AST-Inventur; URL-Oberfläche in `src/core/urls.py:93-208` |
| Forms | 6 Form-Klassen | AST-Inventur; Beispiele `src/core/forms/events.py:49-124`, `src/core/forms/clients.py:13-37` |
| Templates | 86 Templates | `find src/templates -type f` |
| Tests | 150 Python-Testdateien, 1.845 Testfunktionen | `find src/tests -name '*.py'`, `grep '^def test_'` |
| Migrationen | 73 Migrationen ohne `__init__.py` | `git ls-files 'src/core/migrations/*.py' \| grep -v __init__` |
| LOC Python | 52.819 | lokale LOC-Inventur tracked text files |
| LOC Markdown | 9.666 | lokale LOC-Inventur |
| LOC Templates | 6.159 | lokale LOC-Inventur |
| LOC JavaScript | 3.917 | lokale LOC-Inventur |
| LOC YAML | 448 | lokale LOC-Inventur |
| Kritische Backend-Dependencies | `cryptography==46.0.7`, `psycopg-binary==3.3.3`, `gunicorn==25.3.0`, `django-csp==4.0`, `django-otp==1.7.0`, `django-ratelimit==4.1.0`, `pyclamd==0.4.0`, `python-magic==0.4.27`, `sentry-sdk==2.58.0`, `weasyprint==68.1`, `whitenoise==6.12.0` | `requirements.txt:19-88` |
| Dependency-Audit | lokal `pip-audit 2.10.0 --no-deps --disable-pip -r requirements.txt`: `No known vulnerabilities found`; CI führt `pip-audit -r requirements.txt` aus | `.github/workflows/test.yml:84-94` |
| Doku-Inventur | `README.md`, `CONTRIBUTING.md`, `LICENSE`, `SECURITY.md`, `CHANGELOG.md`, `docs/*` vorhanden; kein `CODE_OF_CONDUCT.md` in `git ls-files` | `git ls-files`-Inventur; vorhandene Doku z. B. `docs/admin-guide.md`, `docs/faq.md` |

## C. Befunde Nach Dimension

### 1. Architektur & Domain Design

**[SCHWERE: mittel] Ein-App-Monolith mit fachlichen Subpackages statt fachlich getrennten Django-Apps** 
Fundstelle: `src/anlaufstelle/settings/base.py:24-39`, `src/core/apps.py:4-7`, `CONTRIBUTING.md:391-437` 
Beobachtung: Installiert ist nur `core`; die fachliche Trennung erfolgt über Module wie `core/models/*.py`, `core/views/*.py`, `core/services/*.py`. 
Auswirkung: Für den aktuellen Umfang ist das überschaubar, aber fachliche Grenzen wie Klientel, Fälle, Statistik, Retention und Auth hängen in einem App-Namespace. Neue Einrichtungstypen werden eher konfiguriert als durch App-Grenzen isoliert. 
Empfehlung: Kein App-Split als Selbstzweck. Zuerst Service- und Modellgrenzen dokumentieren; bei neuen Domänen wie Abrechnung, externe Schnittstellen oder mehrstufige Trägerstrukturen eigene Apps einführen.

**[SCHWERE: mittel] Kontaktstufen-Doku und Datenmodell sind nicht deckungsgleich** 
Fundstelle: `README.md:56-64`, `src/core/models/client.py:16-19`, `src/core/models/event.py:24-65`, `src/core/services/event.py:476-488` 
Beobachtung: README beschreibt drei Kontaktstufen: anonym, identifiziert, qualifiziert. `Client.ContactStage` kennt nur `identified` und `qualified`; anonym ist als Event ohne Client plus `is_anonymous` modelliert. 
Auswirkung: Fachlich ist das plausibel für Strichlisten-/Kurzkontakte, aber die Doku suggeriert eine Client-Stufe `Anonym`. Das kann zu falschen Erwartungen bei Migration, Schulung und Auskunftsprozessen führen. 
Empfehlung: Doku und UI klar trennen: `anonymer Kontakt` ist Event-Level, `Klientel` beginnt erst mit Pseudonym.

**[SCHWERE: niedrig] URL-Design ist hypermedia-/aktionsorientiert, nicht REST-rein** 
Fundstelle: `src/core/urls.py:101-185` 
Beobachtung: Neben Ressourcenpfaden gibt es Aktionspfade wie `cases/<uuid:pk>/close/`, `assign-event/`, `retention/bulk-approve/` und `api/workitems/<uuid:pk>/status/`. 
Auswirkung: Für HTMX ist das praktikabel. Für externe API-Nutzung ist die Oberfläche nicht als stabile REST-API geeignet. 
Empfehlung: Aktions-URLs beibehalten, aber als interne Web-Routen dokumentieren; bei externer Integration separate API-Verträge definieren.

**[SCHWERE: info] Konfigurierbares Dokumentationsmodell ist real umgesetzt** 
Fundstelle: `src/core/models/document_type.py:18-92`, `src/core/models/document_type.py:113-190`, `src/core/forms/events.py:111-163` 
Beobachtung: Dokumentationstypen, Felder, Pflichtstatus, Sensitivität, Verschlüsselung, Statistik-Kategorie und Retention sind konfigurierbar. 
Auswirkung: Das ist der zentrale Erweiterungsmechanismus für Kontaktladen, Notschlafstelle, Streetwork und ähnliche Kontexte. 
Empfehlung: Dieses Modell als ADR/Fachmodell dokumentieren, damit spätere Erweiterungen nicht an ihm vorbei modellieren.

### 2. Codequalität & Wartbarkeit

**[SCHWERE: mittel] Type-Hints sind Konvention, aber nicht CI-erzwungen** 
Fundstelle: `CONTRIBUTING.md:224-229`, `pyproject.toml:8-16`, `.github/workflows/lint.yml:19-21` 
Beobachtung: CONTRIBUTING fordert Type Hints `wo sinnvoll`; `pyproject.toml` konfiguriert Ruff, aber kein mypy/pyright. CI prüft Ruff und Formatierung, keinen statischen Type-Checker. 
Auswirkung: Service-Funktionen mit sensibler Domänenlogik bleiben typseitig ungesichert. Das erhöht Wartungsrisiko bei Retention, Exports und Offline-Konflikten. 
Empfehlung: Pyright oder mypy schrittweise für `core/services` aktivieren; zunächst `strict = false`, aber CI-pflichtig.

**[SCHWERE: hoch] Zwei Löschpfade haben divergierende Historien-Semantik** 
Fundstelle: `src/core/services/event.py:582-596`, `src/core/services/retention.py:554-580`, `src/core/templatetags/history_tags.py:133-160` 
Beobachtung: Manuelles Soft-Delete schreibt in `EventHistory.data_before` nur `{"_redacted": True, "fields":...}`. Retention kopiert dagegen die vollständigen Event-Daten in `data_before`. 
Auswirkung: Eine fachlich gleiche Operation, Löschung, hat unterschiedliche Datenschutzwirkung. Das ist ein Wartbarkeitsfehler mit Compliance-Folge. 
Empfehlung: Eine gemeinsame `record_delete_history(event, redacted=True)`-Funktion verwenden und alle Löschpfade darauf zwingen.

**[SCHWERE: info] Migrations-Hygiene ist überwiegend kontrolliert** 
Fundstelle: `src/core/migrations/0012_eventhistory_append_only_trigger.py:8-40`, `src/core/migrations/0024_auditlog_immutable_trigger.py:8-25`, `src/core/migrations/0047_postgres_rls_setup.py:111-115` 
Beobachtung: SQL-Migrationen enthalten `reverse_sql`; RLS wird in Migrationen explizit angelegt und rückbaubar gemacht. 
Auswirkung: Schemaänderungen sind nachvollziehbar. Bei 73 Migrationen steigt aber die Notwendigkeit für periodische Squash-/Upgrade-Tests. 
Empfehlung: Vor 1.0 einen frischen Installationslauf von leerer Datenbank bis Head als Release-Gate dokumentieren.

**[SCHWERE: niedrig] Kommentare sind teilweise stale** 
Fundstelle: `src/anlaufstelle/settings/base.py:243-260`, `CHANGELOG.md:13-18` 
Beobachtung: Der Kommentar in `base.py` beschreibt akzeptiertes `unsafe-eval`; die tatsächliche CSP setzt `script-src` nur auf `self`. Das Changelog sagt, `unsafe-eval` sei global entfernt. 
Auswirkung: Security-relevante Kommentare dürfen nicht widersprüchlich sein, weil sie spätere Änderungen beeinflussen. 
Empfehlung: Kommentarblock aktualisieren und Admin-Ausnahme in `AdminCSPRelaxMiddleware` verlinken.

### 3. Sicherheit

**[SCHWERE: info] Auth, MFA und Session-Härtung sind konkret implementiert** 
Fundstelle: `src/core/views/auth.py:42-90`, `src/core/services/login_lockout.py:15-38`, `src/core/models/user.py:50-74`, `src/core/middleware/mfa.py:45-63`, `src/anlaufstelle/settings/prod.py:48-75` 
Beobachtung: Login hat IP- und Username-Rate-Limits, Lockout greift nach 10 Fehlversuchen in 15 Minuten, MFA kann pro User oder Facility erzwungen werden, Produktion setzt HSTS, Secure Cookies, CSRF-HTTPOnly und X-Frame-DENY. 
Auswirkung: Basisschutz gegen Brute Force, Session-Diebstahl und Clickjacking ist besser als bei typischen Prototypen. 
Empfehlung: Zusätzlich MFA-Enrollment- und Recovery-Prozesse organisatorisch dokumentieren.

**[SCHWERE: mittel] Autocomplete-Rate-Limit wirkt wahrscheinlich nicht blockierend** 
Fundstelle: `src/core/views/clients.py:196-234`, `src/core/views/search.py:19-44`, `src/tests/test_architecture.py:295-359` 
Beobachtung: `ClientAutocompleteView` nutzt `@ratelimit(... method="GET")` ohne `block=True` und prüft `request.limited` nicht. Die Suchviews setzen dagegen `block=True`. Der Architektur-Test deckt nur `post`-Handler ab. 
Auswirkung: Authentifizierte Nutzer können Pseudonyme einer Einrichtung leichter enumerieren. Das ist kein Cross-Tenant-Leak, aber in kleinen Einrichtungen personenbezogen relevant. 
Empfehlung: `block=True` ergänzen, Architekturtest auf sensible GET-Endpunkte erweitern und Antwortgröße/Query-Mindestlänge prüfen.

**[SCHWERE: hoch] RLS ist vorhanden, aber funktional nicht als Non-Superuser getestet** 
Fundstelle: `src/core/middleware/facility_scope.py:47-55`, `src/core/migrations/0047_postgres_rls_setup.py:77-115`, `src/tests/test_rls.py:1-9`, `src/tests/test_rls.py:166-172` 
Beobachtung: Tests prüfen RLS-Setup und Session-Variable, nicht echte Sichtbarkeit unter einer nicht privilegierten DB-Rolle. Die Testdatei dokumentiert selbst, dass der Test-DB-User Superuser ist. 
Auswirkung: Ein Fehler in Policy-Text, DB-Rollen-Setup oder Connection-Konfiguration könnte erst in Produktion auffallen. 
Empfehlung: CI-Job mit Non-Superuser-DB-Rolle ergänzen und Cross-Facility-Queries wirklich negativ testen.

**[SCHWERE: mittel] Statistik-Materialized-View umgeht RLS bewusst** 
Fundstelle: `src/core/migrations/0049_statistics_event_flat_mv.py:19-43`, `src/core/services/statistics.py:135-147`, `src/tests/test_statistics_mv.py:323-352` 
Beobachtung: Die MV enthält `facility_id`, hat aber keine RLS-Policy; Schutz erfolgt durch Service-`WHERE facility_id = %s`. 
Auswirkung: Das ist ein bewusstes Architektur-Risiko: ein künftiger direkter Query auf die MV ohne WHERE hätte Cross-Facility-Reichweite. 
Empfehlung: Entweder RLS/SECURITY-BARRIER-Alternative prüfen oder DB-View-Zugriff ausschließlich über eng gekapselte Repository-Funktionen plus Architekturtest erlauben.

**[SCHWERE: mittel] CSV-Export ist anfällig für Formula Injection** 
Fundstelle: `src/core/services/export.py:88-150` 
Beobachtung: Pseudonyme und dynamische Feldwerte werden direkt per `csv.writer` geschrieben. Es gibt keine Neutralisierung für Werte, die mit `=`, `+`, `-`, `@`, Tab oder CR beginnen. 
Auswirkung: Beim Öffnen in Tabellenkalkulationen können Formeln ausgeführt oder externe Requests ausgelöst werden. 
Empfehlung: CSV-Zellen mit gefährlichem Präfix durch führendes Apostroph oder Tab-sichere Escaping-Strategie neutralisieren; Regressionstest ergänzen.

**[SCHWERE: info] Upload-Pipeline ist defensiv angelegt** 
Fundstelle: `src/core/services/file_vault.py:123-160`, `src/core/services/file_vault.py:206-255`, `src/core/services/file_vault.py:258-333`, `src/core/services/virus_scan.py:93-133`, `src/core/views/attachments.py:21-46` 
Beobachtung: Uploads haben Extension-Whitelist, ClamAV vor Verschlüsselung, Magic-Byte-Prüfung, UUID-Speichername, Verschlüsselung und sichere Inline-MIME-Whitelist. 
Auswirkung: Datei-Upload ist ein hoher Angriffsbereich; hier sind mehrere Verteidigungslinien vorhanden. 
Empfehlung: `application/octet-stream`-Fallback regelmäßig mit Testdateien evaluieren, weil unbekannte libmagic-Erkennung toleriert wird.

### 4. Datenschutz & Sozialdatenschutz

**[SCHWERE: kritisch] Automatische Retention entfernt Daten nicht aus der unveränderlichen Historie** 
Fundstelle: `src/core/services/retention.py:566-580`, `src/core/services/event.py:582-596`, `src/core/models/event_history.py:43-67`, `src/core/templatetags/history_tags.py:133-160`, `docs/faq.md:427-433` 
Beobachtung: Retention kopiert volle `data_json`-Werte in `EventHistory.data_before`; EventHistory ist append-only und nicht löschbar. Die Anzeige rendert nicht-redaktierte Delete-Historie feldweise. 
Auswirkung: Speicherbegrenzung und Löschung sind materiell unvollständig. Für Art. 5 Abs. 1 lit. e, Art. 17 DSGVO, Sozialgeheimnis und § 67 SGB X ist das ein Blocker. 
Empfehlung: Retention-Delete-Historie redaktieren, bestehende Historien migrieren oder mit datenschutzrechtlicher Entscheidung löschen/anonymisieren, und Tests auf `EventHistory.data_before["_redacted"]` ergänzen.

**[SCHWERE: hoch] Lösch- und Anonymisierungsumfang ist unvollständig dokumentiert und technisch begrenzt** 
Fundstelle: `docs/faq.md:427-433`, `src/core/models/client.py:100-140`, `src/core/models/workitem.py:150-208`, `src/core/models/audit.py:104-112` 
Beobachtung: FAQ sagt, es gebe keinen manuellen Löschmechanismus für Clients, Cases/Episodes, User-Accounts; AuditLog ist unveränderlich; EventHistory kaskadiert nur bei Hard-Delete. DeletionRequest adressiert nur `Event`. 
Auswirkung: Betroffenenrechte lassen sich nur teilweise über die Anwendung erfüllen. Betreiber müssten Datenbank- oder Admin-Prozesse außerhalb des Systems definieren. 
Empfehlung: Lösch-/Anonymisierungs-Matrix je Datenklasse erstellen und technische Workflows für Client, Case, Episode, WorkItem, User und Historie ergänzen.

**[SCHWERE: mittel] Rechtsgrundlagen sind dokumentiert, aber nicht als Datenmodell geführt** 
Fundstelle: `docs/fachkonzept-anlaufstelle.md:398-402`, `docs/dsgvo-templates/informationspflichten.md:34-39`, `src/core/models/event.py:17-65`, `src/core/models/client.py:28-78` 
Beobachtung: Das Fachkonzept nennt § 67 SGB X, DSGVO Art. 9, SGB X §§ 67-85a und § 203 StGB. In Event/Client-Modellen gibt es kein Feld für konkrete Rechtsgrundlage, Einwilligungsstatus oder Informationspflichten-Stand. 
Auswirkung: Für niedrigschwellige Einrichtungen kann das korrekt sein, wenn Rechtsgrundlage pro Verarbeitungstätigkeit gilt. Bei heterogenen Angeboten oder Projekten fehlt aber technische Nachweisbarkeit. 
Empfehlung: Klären, ob Rechtsgrundlage tenant-/document-type-weit reicht; sonst `DocumentType` oder `Facility` um Rechtsgrundlagen-Konfiguration ergänzen.

**[SCHWERE: mittel] K-Anonymität schützt nicht alle Statistik-/Exportpfade** 
Fundstelle: `src/core/services/statistics.py:85-103`, `src/core/services/export.py:180-236`, `src/core/models/settings.py:91-107`, `docs/admin-guide.md:1006-1015` 
Beobachtung: Settings enthalten K-Anonymitäts-Schwelle; Live-Statistik gibt aber `top_clients` mit Pseudonym aus, und Jugendamt-Statistik aggregiert kleine Kategorien ohne sichtbare Suppression. 
Auswirkung: Kleine Fallzahlen können re-identifizierend wirken, besonders bei spezialisierten Angeboten oder Ein-Personen-Konstellationen. 
Empfehlung: K-Schwelle zentral auf Exporte und externe Berichte anwenden; `top_clients` nur intern anzeigen oder entfernen.

**[SCHWERE: info] Art. 15/20-Export existiert** 
Fundstelle: `src/core/views/clients.py:237-284`, `src/core/services/client_export.py:1-163`, `docs/admin-guide.md:1041-1050` 
Beobachtung: JSON- und PDF-Export für Client-Daten ist Lead/Admin-geschützt, rate-limitiert und audit-loggt Exporte. 
Auswirkung: Auskunft und Portabilität sind technisch angelegt. 
Empfehlung: Exportinhalt gegen Retention-/Historien-Fix erneut prüfen, damit gelöschte Werte nicht unbeabsichtigt in Auskunftspaketen fehlen oder fortbestehen.

### 5. Tests & Qualitätssicherung

**[SCHWERE: info] Test- und CI-Breite ist substanziell** 
Fundstelle: `.github/workflows/test.yml:28-94`, `.github/workflows/lint.yml:14-21`, `.github/workflows/e2e.yml:28-61`, `pyproject.toml:17-43` 
Beobachtung: Unit/Integration laufen mit pytest/coverage, E2E mit Playwright, Ruff in CI, `manage.py check --deploy`, Migrations-Check, pip-audit und Lock-Drift-Check sind konfiguriert. 
Auswirkung: Regressionen werden wahrscheinlicher früh gefunden als in typischen Einzelentwickler-Projekten. 
Empfehlung: CI-Status und Coverage-Artefakte als Release-Gate sichtbar machen.

**[SCHWERE: hoch] RLS-Testlücke bleibt die wichtigste QA-Lücke** 
Fundstelle: `src/tests/test_rls.py:1-9`, `src/tests/test_rls.py:166-172`, `CONTRIBUTING.md:231-243` 
Beobachtung: CONTRIBUTING fordert RLS für neue facility-scoped Models, aber Tests prüfen nicht das reale Runtime-Verhalten als Non-Superuser. 
Auswirkung: Eine zentrale Sicherheitsannahme ist nicht end-to-end abgesichert. 
Empfehlung: Dedizierte Postgres-Rolle ohne Superuser-Rechte in CI anlegen und Cross-Tenant-Queries als negative Tests ausführen.

**[SCHWERE: mittel] Rate-Limit-Architekturtest deckt sensible GET-Endpunkte nicht ab** 
Fundstelle: `src/tests/test_architecture.py:295-359`, `src/core/views/clients.py:199-234`, `src/core/views/search.py:19-44` 
Beobachtung: Architekturtest erzwingt Rate-Limits für `post`. Sensible GET-Endpunkte wie Autocomplete werden nicht systematisch geprüft. 
Auswirkung: Enumeration-Risiken können entstehen, obwohl die POST-Seite sauber ist. 
Empfehlung: Allowlist-/Denylist-Test für GET-Suche, Autocomplete, Export und Statistik ergänzen.

**[SCHWERE: mittel] Retention-Test prüft nicht, ob Historie redaktiert ist** 
Fundstelle: `src/tests/test_retention.py:149-157`, `src/core/services/retention.py:566-580` 
Beobachtung: Test bestätigt `is_deleted=True` und leeres `data_json`, aber nicht die `EventHistory`-Nutzdaten. 
Auswirkung: Genau der kritische Datenschutzfehler bleibt testseitig grün. 
Empfehlung: Test auf redaktierte Delete-Historie und Nichtanzeige alter Werte ergänzen.

**[SCHWERE: mittel] A11y-Tests sind punktuell, nicht WCAG-vollständig** 
Fundstelle: `src/tests/test_architecture.py:362-406`, `src/tests/e2e/test_layout.py:58-67`, `.github/workflows/e2e.yml:51-57` 
Beobachtung: Es gibt Guardrails für SVG-Textalternativen und Skip-Link-Test, aber keinen axe/pa11y/contrast/focus-after-HTMX-Test in CI. 
Auswirkung: WCAG 2.2 AA wird nicht automatisiert belastbar belegt. 
Empfehlung: Playwright + axe-core für Kernseiten und HTMX-Swap-Fokuspfade ergänzen.

### 6. Performance & Skalierbarkeit

**[SCHWERE: info] Indizes und Materialized View adressieren bekannte Lastpfade** 
Fundstelle: `src/core/models/event.py:73-80`, `src/core/models/case.py:70-78`, `src/core/models/audit.py:91-102`, `src/core/migrations/0049_statistics_event_flat_mv.py:19-43` 
Beobachtung: Event-, Case- und AuditLog-Listen haben Composite-Indizes; Statistik kann eine flache Materialized View nutzen. 
Auswirkung: Häufige Listen- und Statistikpfade sind nicht rein naiv implementiert. 
Empfehlung: Query-Pläne auf realistischen Datenmengen vor 1.0 dokumentieren.

**[SCHWERE: mittel] Event-Edit kann bei Datei-Feldern N+1-Queries erzeugen** 
Fundstelle: `src/core/views/events.py:331-345` 
Beobachtung: Für jeden File-Marker wird `event.attachments.filter(pk=...).first` in einer Schleife ausgeführt. 
Auswirkung: Bei mehreren Datei-Feldern oder Versionen wächst die Zahl der Queries linear. 
Empfehlung: Attachments vorab per `in_bulk` oder Prefetch-Map laden.

**[SCHWERE: mittel] Attachment-Liste ist hart auf 200 Einträge geschnitten** 
Fundstelle: `src/core/views/attachments.py:87-114` 
Beobachtung: Die View iteriert `attachments[:200]` und zeigt kein `has_more` oder Pagination-Konzept. 
Auswirkung: Große Einrichtungen verlieren Sichtbarkeit auf ältere Dateien; Performance ist begrenzt, aber UX und Auditierbarkeit leiden. 
Empfehlung: Server-side Pagination plus Filterstatus einführen.

**[SCHWERE: mittel] Statistik-MV ist Performance-Gewinn mit Security-Kapselungsbedarf** 
Fundstelle: `src/core/services/statistics.py:135-147`, `src/tests/test_statistics_mv.py:323-352` 
Beobachtung: Der Service filtert korrekt nach `facility_id`; die MV selbst ist policy-frei. 
Auswirkung: Performance-Optimierung erhöht die Anforderungen an Kapselung und Tests. 
Empfehlung: Direkten Zugriff auf MV außerhalb des Statistik-Service per Architekturtest verbieten.

### 7. Barrierefreiheit & UX

**[SCHWERE: info] Basis-A11y ist sichtbar adressiert** 
Fundstelle: `src/templates/base.html:27-29`, `src/templates/base.html:66-80`, `src/tests/test_architecture.py:362-406`, `CHANGELOG.md:75-78` 
Beobachtung: Skip-Link, `aria-label` für Navigation und SVG-A11y-Guard sind vorhanden. 
Auswirkung: Es gibt eine technische Grundlage, auf der WCAG-Arbeit aufbauen kann. 
Empfehlung: Nicht als abgeschlossen behandeln; Fokus, Tastaturpfade, Fehlermeldungen und Kontrast separat prüfen.

**[SCHWERE: mittel] `html lang` ist trotz Sprachumschaltung hart auf Deutsch gesetzt** 
Fundstelle: `src/templates/base.html:1-3`, `src/templates/base.html:199-204`, `src/anlaufstelle/settings/base.py:145-154` 
Beobachtung: Es gibt Deutsch/Englisch und Sprachbuttons; das Root-Element bleibt `lang="de"`. 
Auswirkung: Screen Reader und Browser-Lokalisierung sind für englische UI falsch. 
Empfehlung: `{% get_current_language as LANGUAGE_CODE %}` verwenden und `lang="{{ LANGUAGE_CODE }}"` setzen.

**[SCHWERE: mittel] Fokusmanagement nach HTMX-Swaps ist nicht belegt** 
Fundstelle: `src/core/views/mixins.py:61-83`, `src/templates/base.html:311-332`, `.github/workflows/e2e.yml:51-57` 
Beobachtung: HTMX-Partial-Rendering ist verbreitet, aber es gibt keinen sichtbaren Test für Fokus nach Swap, Dialog/Overlay-Fokus oder Tastaturreihenfolge. 
Auswirkung: Gerade Tablet-/Streetwork-Nutzung und Screen-Reader-Nutzung können brechen, ohne dass CI anschlägt. 
Empfehlung: E2E-Tests für Suche, Mobile-Menü, Retention-Bulk und Event-Formular mit Tastatur-only-Flows ergänzen.

### 8. Internationalisierung & Lokalisierung

**[SCHWERE: info] gettext und Übersetzungsdateien sind vorhanden** 
Fundstelle: `src/anlaufstelle/settings/base.py:145-158`, `src/tests/test_architecture.py:409-442`, `src/locale/de/LC_MESSAGES/django.po`, `src/locale/en/LC_MESSAGES/django.po` 
Beobachtung: Projekt nutzt `LANGUAGES`, `LOCALE_PATHS`, `.po/.mo` und einen Architekturtest gegen f-Strings in gettext. 
Auswirkung: I18n ist nicht nur kosmetisch angelegt. 
Empfehlung: Übersetzungsextraktion und `.po`-Aktualität als Release-Check aufnehmen.

**[SCHWERE: mittel] Sprachumschaltung und HTML-Lang widersprechen sich** 
Fundstelle: `src/templates/base.html:3`, `src/templates/base.html:199-204` 
Beobachtung: UI bietet DE/EN-Buttons; Dokument-Lang bleibt Deutsch. 
Auswirkung: Barrierefreiheit und Such-/Browser-Metadaten sind bei Englisch inkorrekt. 
Empfehlung: Siehe Dimension 7; zusätzlich Test für `lang` nach Sprachwechsel ergänzen.

**[SCHWERE: niedrig] Fachbegriffe sind deutsch domänennah, aber teils erklärungsbedürftig** 
Fundstelle: `src/core/models/document_type.py:32-42`, `src/core/models/workitem.py:15-23`, `docs/user-guide.md:253` 
Beobachtung: Begriffe wie Kontakt, Krisengespräch, Spritzentausch, Begleitung, Hinweis/Aufgabe und Pseudonym sind passend. Alias-/Spitznamen-Mehrfachführung wird nicht modelliert. 
Auswirkung: Für Teams mit vielen Aliasnamen pro Person kann das Pseudonym-Feld zu eng sein. 
Empfehlung: Aliasliste als optionales, nicht eindeutiges Child-Modell prüfen.

### 9. Deploy & Betrieb

**[SCHWERE: info] Docker-Produktionspfad hat sinnvolle Baseline** 
Fundstelle: `Dockerfile:17-64`, `docker-compose.prod.yml:1-78`, `Caddyfile:1-10`, `src/anlaufstelle/settings/prod.py:37-106` 
Beobachtung: Runtime läuft non-root, hat Healthcheck, Compose nutzt Postgres 16, ClamAV, Caddy, interne Netzwerke und Production-Settings fail-closed für Secrets/Hosts/Encryption. 
Auswirkung: Self-Hosting ist real vorbereitet, nicht nur README-Text. 
Empfehlung: Minimalbetrieb und Upgradepfad weiter für Nicht-IT-Träger testen.

**[SCHWERE: mittel] Migrationen laufen beim Containerstart, nicht zero-downtime** 
Fundstelle: `docker-entrypoint.sh:4-18` 
Beobachtung: EntryPoint nimmt Advisory Lock, führt `manage.py migrate --noinput` aus und startet danach Gunicorn. 
Auswirkung: Das verhindert parallele Migrationsrennen, aber nicht Downtime oder inkompatible Rolling-Deploys. 
Empfehlung: Für 1.0 Migrationsstrategie dokumentieren: pre-deploy migrate, backwards-compatible migrations, Rollback-Fall.

**[SCHWERE: mittel] Backup-Verifikation ist flach** 
Fundstelle: `scripts/backup.sh:28-81`, `scripts/backup.sh:95-120`, `scripts/restore.sh:34-49` 
Beobachtung: Backup ist verschlüsselt und `--verify` stellt in temporäre DB wieder her, prüft aber nur `SELECT COUNT(*) FROM core_facility`. Restore warnt und pipe't in bestehende DB. 
Auswirkung: Backup-Datei kann formal lesbar sein, ohne dass fachliche Tabellen, RLS, Trigger oder Attachments geprüft sind. 
Empfehlung: Restore-Drill mit Prüfsummen/Tabellenanzahlen, Attachment-Dateien und Applikations-Health nach Restore ergänzen.

**[SCHWERE: niedrig] Produktions-Compose verweist auf alten Image-Namespace** 
Fundstelle: `docker-compose.prod.yml:16-18`, `README.md:158-170` 
Beobachtung: Remote ist `github.com/anlaufstelle/app`; Compose nutzt `ghcr.io/anlaufstelle/app:latest`. 
Auswirkung: Das kann absichtlich historisch sein, wirkt aber für Betreiber wie Doku-/Release-Drift. 
Empfehlung: Image-Namespace dokumentieren oder auf `ghcr.io/anlaufstelle/app` umstellen.

### 10. Lizenz, Governance & Nachhaltigkeit

**[SCHWERE: info] AGPL ist korrekt als Projektlizenz eingebunden** 
Fundstelle: `pyproject.toml:1-6`, `README.md:134-138`, `LICENSE:540-550` 
Beobachtung: Lizenz ist `AGPL-3.0-or-later`; README erklärt Netzwerk-Offenlegung. 
Auswirkung: Lizenzgrundlage ist eindeutig. 
Empfehlung: Dependency-Lizenzscan als Release-Job ergänzen.

**[SCHWERE: mittel] AGPL-Netzwerkhinweis fehlt in der Weboberfläche** 
Fundstelle: `LICENSE:540-550`, `src/templates/auth/login.html:25-31`, `src/templates/base.html:338-352` 
Beobachtung: Login nennt `Open Source`, aber ohne Quellcode-Link. Das Basistemplate endet ohne Footer/Source-Angebot. AGPL §13 verlangt bei modifizierten Netzwerkversionen ein prominentes Angebot des Corresponding Source. 
Auswirkung: Betreiber modifizierter Instanzen können Compliance verfehlen, wenn sie keinen eigenen Hinweis ergänzen. 
Empfehlung: Footer/Account-Menü mit `Quellcode`-Link und Versionsangabe hinzufügen, per Env konfigurierbar.

**[SCHWERE: mittel] SECURITY.md ist stale und verweist auf falschen Repo-Namespace** 
Fundstelle: `SECURITY.md:7-25`, `README.md:7`, `CHANGELOG.md:9-18` 
Beobachtung: SECURITY unterstützt `0.9.x`, während aktueller Stand `0.10.2` ist. Advisory-Link zeigt auf `github.com/anlaufstelle/app`, nicht `github.com/anlaufstelle/app`. 
Auswirkung: Sicherheitsmeldungen können fehlgeleitet werden; Support-Matrix ist unklar. 
Empfehlung: SECURITY vor Pilotbetrieb aktualisieren und Security-Advisory-Link prüfen.

**[SCHWERE: mittel] Governance-Bausteine fehlen oder sind implizit** 
Fundstelle: `git ls-files`-Inventur, `CONTRIBUTING.md:350-377`, `docs/fachkonzept-anlaufstelle.md:866-869` 
Beobachtung: Kein `CODE_OF_CONDUCT.md`; kein DCO/CLA-Prozess in CONTRIBUTING. Das Fachkonzept nennt CLA nur als Voraussetzung für Dual-Licensing. 
Auswirkung: Bei externen Beiträgen sind Verhaltensregeln, Rechtekette und Erwartungsmanagement unvollständig. 
Empfehlung: Code of Conduct, DCO oder klare Copyright-Regel und Maintainer-/Triage-Prozess ergänzen.

**[SCHWERE: mittel] Release-Workflow baut Images ohne sichtbare SBOM/Signierung/Provenance** 
Fundstelle: `.github/workflows/release.yml:24-38` 
Beobachtung: Docker Buildx pusht Multi-Arch-Images und `latest`; keine Cosign-Signatur, kein SLSA/attestation, keine SBOM-Erzeugung. 
Auswirkung: Supply-Chain-Nachweis ist für Sozialdaten-Betreiber schwach. 
Empfehlung: SBOM (`syft`/BuildKit attestations), Cosign und Release-Checksums ergänzen.

### 11. Fachliche Eignung

**[SCHWERE: info] Kernrealität niedrigschwelliger Arbeit ist sichtbar modelliert** 
Fundstelle: `README.md:32-50`, `src/core/models/client.py:35-67`, `src/core/models/event.py:24-65`, `src/core/models/workitem.py:12-90`, `src/core/views/handover.py:13-57`, `src/core/models/organization.py:9-45` 
Beobachtung: Pseudonyme, anonyme Events, Kontakt-/Leistungsdokumentation, Hinweise/Aufgaben, Übergabe und mehrere Einrichtungen eines Trägers sind vorhanden. 
Auswirkung: Das System trifft viele reale Workflows in Kontaktladen, Notschlafstelle und Streetwork. 
Empfehlung: Mit Pilotdaten prüfen, welche Felder in Seed/Demos stigmatisierend oder zu grob sind.

**[SCHWERE: mittel] Mehrere Aliase pro Person sind nicht modelliert** 
Fundstelle: `src/core/models/client.py:35-39`, `src/core/forms/clients.py:16-37`, `grep`-Inventur ohne Alias-Modell; `docs/user-guide.md:253` 
Beobachtung: Pro Client gibt es ein eindeutiges Pseudonym je Facility; keine Alias-/Spitznamen-Tabelle. 
Auswirkung: In Streetwork und Drogenhilfe sind mehrere Namen pro Person üblich. Ein einziges eindeutiges Feld erzeugt Dubletten oder informelle Notizen. 
Empfehlung: Optionales `ClientAlias`-Modell mit Suchindex und Markierung `primary/obsolete` ergänzen.

**[SCHWERE: mittel] Anonymität ist Event-Level, nicht als fortführbarer anonymer Fall modelliert** 
Fundstelle: `src/core/models/client.py:16-19`, `src/core/models/event.py:24-65`, `src/core/services/event.py:476-488` 
Beobachtung: Wiederkehrende anonyme Kontakte ohne Pseudonym bleiben einzelne Events. 
Auswirkung: Das passt für Strichlisten, aber nicht für Fälle, bei denen Teams eine Person wiedererkennen, ohne schon ein Pseudonym vergeben zu wollen. 
Empfehlung: Prüfen, ob `anonymous cohort/contact token` fachlich gewollt ist; sonst Doku schärfen, dass Wiedererkennung ein Pseudonym erfordert.

**[SCHWERE: mittel] Berichtsfunktionen können Re-Identifizierung in kleinen Gruppen begünstigen** 
Fundstelle: `src/core/services/statistics.py:85-103`, `src/core/services/export.py:180-236`, `docs/admin-guide.md:1006-1015` 
Beobachtung: Top-Clients mit Pseudonym und kleine Aggregatzellen sind nicht sichtbar unterdrückt. 
Auswirkung: Förderberichte, Jugendamtsberichte oder Trägerberichte können bei kleinen Einrichtungen Rückschlüsse ermöglichen. 
Empfehlung: Externe Berichte standardmäßig k-anonymisieren und interne Detailstatistiken klar kennzeichnen.

**[SCHWERE: niedrig] Standort-/Fahrzeug-Streetwork ist nur über Facility abbildbar** 
Fundstelle: `src/core/models/organization.py:25-45`, `src/core/models/event.py:17-65` 
Beobachtung: Es gibt Organisation und Facility, aber kein eigenes Standort-, Tour- oder Fahrzeugmodell in den Kernmodellen. 
Auswirkung: Mobile Teams können Touren nur über Felder im DocumentType erfassen, nicht strukturiert auswerten. 
Empfehlung: Vor Erweiterung reale Pilot-Workflows prüfen; ggf. `Location/Route` als optionale Konfiguration einführen.

### 12. Dokumentation

**[SCHWERE: info] Dokumentation ist breit vorhanden** 
Fundstelle: `README.md:11-19`, `README.md:165-194`, `docs/admin-guide.md:922-1059`, `docs/faq.md:427-433`, `docs/dsgvo-templates/dsfa.md:73-90` 
Beobachtung: README, Admin-Guide, User-Guide, FAQ, Ops-Runbook, DSGVO-Templates, Fachkonzept, Screenshots und englische Kurzdocs sind vorhanden. 
Auswirkung: Zielgruppen Admin/Entwicklung/Betreiber werden zumindest angesprochen. 
Empfehlung: Doku-Gültigkeit mit Release-Checkliste koppeln.

**[SCHWERE: mittel] README-Quickstart enthält einen reproduzierbaren Pfadfehler** 
Fundstelle: `README.md:169-172` 
Beobachtung: `git clone https://github.com/anlaufstelle/app.git` erzeugt standardmäßig das Verzeichnis `app`, die README sagt danach `cd anlaufstelle`. 
Auswirkung: Erste Installation scheitert für neue Nutzer sofort, wenn sie den Befehl kopieren. 
Empfehlung: `git clone... anlaufstelle` oder `cd app` dokumentieren.

**[SCHWERE: mittel] Doku driftet gegenüber Release-Stand** 
Fundstelle: `SECURITY.md:7-25`, `CHANGELOG.md:29-73`, `README.md:7` 
Beobachtung: SECURITY nennt `0.9.x`, Changelog/README sind bei `0.10.2`. Advisory-Link zeigt auf alten Namespace. 
Auswirkung: Gerade Security-Dokumente müssen für Betreiber verlässlich sein. 
Empfehlung: Release-Check um Doku-Konsistenz für README, SECURITY, Compose-Image und User-Guide erweitern.

**[SCHWERE: mittel] ADRs fehlen als eigener Entscheidungsnachweis** 
Fundstelle: `CONTRIBUTING.md:441-445`, `docs/security-notes.md:24-32`, `docs/fachkonzept-anlaufstelle.md:398-406`; kein `docs/adr/` in `git ls-files` 
Beobachtung: Entscheidungen sind verstreut in Fachkonzept, Security Notes und CONTRIBUTING dokumentiert, aber nicht als ADR-Serie. 
Auswirkung: RLS-Ausnahmen, MV ohne RLS, AuditLog-Immutability und Retention-Tradeoffs sind auditrelevant und sollten versioniert entschieden sein. 
Empfehlung: ADRs für RLS, Löschung/Historie, Offline-Krypto, AGPL/Source-Angebot und Statistik-Anonymisierung anlegen.

## D. Priorisierte Maßnahmenliste

| Reihenfolge | Befund / Maßnahme | Aufwand | Impact |
|---:|---|---:|---:|
| 1 | Retention-Delete-Historie redaktieren und bestehende nicht-redaktierte `EventHistory`-DELETE-Einträge migrieren/anonymisieren | M | hoch |
| 2 | Regressionstests für Retention-Historie ergänzen | S | hoch |
| 3 | RLS-Integrationstest mit Non-Superuser-DB-Rolle in CI einführen | M | hoch |
| 4 | `ClientAutocompleteView` mit `block=True`, Mindestquery und GET-Rate-Limit-Test härten | S | mittel |
| 5 | CSV-Formula-Injection-Escaping zentral in Export-Service einbauen | S | mittel |
| 6 | K-Anonymität/Suppression für Jugendamt-/CSV-/PDF-Berichte und kleine Zellen anwenden | M | hoch |
| 7 | Lösch-/Anonymisierungs-Matrix für Client, Case, Episode, WorkItem, User, AuditLog, EventHistory umsetzen | L | hoch |
| 8 | SECURITY.md auf `0.10.x`/aktuellen Namespace aktualisieren | S | mittel |
| 9 | AGPL-Source-Link und Versionshinweis in UI ergänzen | S | mittel |
| 10 | README-Quickstart (`cd app` vs. `cd anlaufstelle`) korrigieren | S | mittel |
| 11 | Attachment-Edit N+1 und Attachment-List-Pagination beheben | S/M | mittel |
| 12 | `html lang` dynamisch setzen und testen | S | mittel |
| 13 | Playwright+Axe/Contrast/Fokus-Tests für Kernflows ergänzen | M | mittel |
| 14 | Release-Workflow um SBOM, Cosign/Provenance und Dependency-License-Scan ergänzen | M | mittel |
| 15 | Backup-Restore-Drill mit fachlichen Tabellen, Attachments, Trigger/RLS und Healthcheck ausbauen | M | mittel |
| 16 | ADRs für RLS, Retention/Historie, Statistik-MV und Offline-Krypto schreiben | M | mittel |

## E. Offene Fragen

- Läuft die produktive Datenbankrolle garantiert ohne Superuser-/Owner-Bypass-Rechte, und ist das in realen Deployments getestet? Relevant wegen `src/tests/test_rls.py:1-9`.
- Welche fachliche Entscheidung gilt für die Aufbewahrung von EventHistory und AuditLog bei Art.-17-Anfragen? Der Code verhindert Mutationen (`src/core/models/event_history.py:59-67`, `src/core/models/audit.py:104-112`), die FAQ nennt fehlende Löschmechanismen (`docs/faq.md:427-433`).
- Sind `top_clients` und kleine Jugendamt-Zellen ausschließlich intern gedacht, oder werden sie an Träger/Fördergeber weitergegeben? Relevant wegen `src/core/services/statistics.py:85-103` und `src/core/services/export.py:180-236`.
- Welche realen Datenmengen werden erwartet: Events pro Jahr, Attachments pro Facility, Anzahl Facilities pro Träger, gleichzeitige Nutzer? Ohne diese Werte bleiben Performance-Aussagen stichprobenhaft.
- Gibt es externe Datenschutzberatung, DSFA-Freigabe und AVV-Musterprüfung für konkrete Betreiber? Die Doku weist selbst auf Betreiberverantwortung und fehlendes formales Drittaudit hin (`docs/admin-guide.md:924-926`).
- Wird ein offizieller Container-Namespace `ghcr.io/anlaufstelle/app` geplant, oder bleibt `ghcr.io/anlaufstelle/app` verbindlich? Compose und Repository-Namespace weichen sichtbar ab (`docker-compose.prod.yml:16-18`).
- Sollen externe Beiträge über DCO, CLA oder einfache Copyright-Beibehaltung laufen? CONTRIBUTING beschreibt Review/Merge, aber keine Rechtekette (`CONTRIBUTING.md:350-377`).

## F. Was Bewusst Nicht Bewertet Wurde

- Keine vollständige manuelle WCAG-2.2-AA-Prüfung mit Screen Reader, Kontrastmessung und Tastaturnavigation; es wurden nur Code, Templates und vorhandene Tests geprüft.
- Keine Lasttests und keine Query-Plan-Messung auf produktionsähnlichen Datenmengen; Performance-Befunde sind codebasierte Stichproben.
- Keine Rechtsberatung und keine abschließende DSGVO-/SGB-X-Konformitätsfreigabe; bewertet wurde technische Unterstützbarkeit anhand des Repos.
- Keine Prüfung eines Live-Deployments, TLS-Zertifikate, realer Reverse-Proxy-Header oder produktiver Sentry-/Logging-Konfiguration.
- Keine vollständige Dependency-License-Compatibility-Prüfung. AGPL-Projektlizenz und kritische Dependencies wurden inventarisiert; ein automatischer Lizenzscan wurde nicht ausgeführt.
- Keine Verifikation aktueller GitHub-Actions-Ergebnisse im Remote-Repository; analysiert wurde die Workflow-Konfiguration und lokal `pip-audit` für gepinnte Requirements.
- Keine Bewertung realer Nutzungsqualität mit Sozialarbeiter*innen oder Klient*innen im Raum; Domain-Fit wurde aus Code, Doku und fachlicher Plausibilität abgeleitet.
