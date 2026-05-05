> **[English version / Englische Version](CONTRIBUTING.en.md)**

# Contributing to Anlaufstelle

[![Lint](https://github.com/anlaufstelle/app/actions/workflows/lint.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/lint.yml)
[![Test](https://github.com/anlaufstelle/app/actions/workflows/test.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/test.yml)
[![E2E](https://github.com/anlaufstelle/app/actions/workflows/e2e.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/e2e.yml)

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/)
[![Django 5.1](https://img.shields.io/badge/django-5.1-green.svg)](https://www.djangoproject.com/)
[![PostgreSQL 16](https://img.shields.io/badge/postgresql-16-blue.svg)](https://www.postgresql.org/)
[![HTMX](https://img.shields.io/badge/htmx-%E2%9C%93-blue.svg)](https://htmx.org/)
[![Alpine.js](https://img.shields.io/badge/alpine.js-%E2%9C%93-blue.svg)](https://alpinejs.dev/)
[![Tailwind CSS](https://img.shields.io/badge/tailwindcss-%E2%9C%93-blue.svg)](https://tailwindcss.com/)

[![code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)
[![Last Commit](https://img.shields.io/github/last-commit/anlaufstelle/app)](https://github.com/anlaufstelle/app/commits/main)
[![Open Issues](https://img.shields.io/github/issues/anlaufstelle/app)](https://github.com/anlaufstelle/app/issues)

Willkommen! Diese Anleitung erklärt, wie du die Entwicklungsumgebung einrichtest, wie der Code strukturiert ist und wie du Änderungen beiträgst.

---

## Inhaltsverzeichnis

1. [Entwicklungsumgebung einrichten](#entwicklungsumgebung-einrichten)
2. [Make-Targets](#make-targets)
3. [Coding Conventions](#coding-conventions)
4. [Tests](#tests)
5. [Pull-Request-Prozess](#pull-request-prozess)
6. [Architektur-Überblick](#architektur-überblick)

---

## Entwicklungsumgebung einrichten

### Voraussetzungen

- **Python 3.13** (empfohlen: via [pyenv](https://github.com/pyenv/pyenv))
- **PostgreSQL 16** (oder Docker, s. u.)
- **Node.js 20+** (für Tailwind CSS)
- **Docker** (optional, für die Datenbank)

### Schritt-für-Schritt

**1. Repository klonen**

```bash
git clone https://github.com/anlaufstelle/app.git
cd anlaufstelle
```

**2. Python-Umgebung einrichten**

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # enthält Runtime + Test/Lint-Tools
# Alternativ nur Runtime (z.B. für Prod-Docker-Build):
# pip install -r requirements.txt
```

> **Lock-Files:** `requirements.txt` / `requirements-dev.txt` sind generierte
> Lock-Files mit gepinnten transitiven Abhängigkeiten (via
> [pip-tools](https://github.com/jazzband/pip-tools)). Direkte Abhängigkeiten
> stehen in `requirements.in` / `requirements-dev.in`. Nach einer Änderung
> dort: `make deps-lock` ausführen. Details:
> [docs/ops-runbook.md § 8](docs/ops-runbook.md#8-dependencies-aktualisieren).

**3. Datenbank starten**

Mit Docker (empfohlen):

```bash
make db
```

Das startet einen PostgreSQL-16-Container mit folgenden Zugangsdaten:

| Variable  | Wert          |
|-----------|---------------|
| DB-Name   | anlaufstelle  |
| User      | anlaufstelle  |
| Passwort  | anlaufstelle  |
| Port      | 5432          |

Alternativ kann eine lokal installierte PostgreSQL-Instanz verwendet werden. Die Verbindungs-URL muss dann in der Umgebungsvariable `DATABASE_URL` gesetzt werden.

**4. Umgebungsvariablen konfigurieren**

Lege eine `.env`-Datei im Projektverzeichnis an (oder exportiere die Variablen):

```bash
SECRET_KEY=dev-secret-key-bitte-aendern
DATABASE_URL=postgres://anlaufstelle:anlaufstelle@localhost:5432/anlaufstelle
DEBUG=true
ENCRYPTION_KEY=<32-Byte-Base64-Schlüssel>
```

Einen `ENCRYPTION_KEY` erzeugen:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**5. Migrationen ausführen**

```bash
make migrate
```

**6. Seed-Daten laden** (optional, für lokale Entwicklung)

```bash
make seed                              # Standard: small
python src/manage.py seed --scale medium   # mehr Daten inkl. Fallmanagement
python src/manage.py seed --scale large    # Lasttest-Volumen
python src/manage.py seed --flush          # vorhandene Daten vorher löschen
```

**Scale-Profile im Überblick:**

| Daten | `small` (Default) | `medium` | `large` |
|---|---|---|---|
| Einrichtungen | 1 | 2 | 5 |
| Users / Einrichtung | 4 | 4 | 4 |
| Clients / Einrichtung | 7 | 40 | 500 |
| Events / Einrichtung | 25 | 750 | 10.000 |
| Cases | 3 | 12 | 50 |
| Episoden | — | 20 | 80 |
| Wirkungsziele | — | 15 | 60 |
| Meilensteine / Ziel | — | 3 | 4 |
| WorkItems | 5 | 25 | 100 |
| DeletionRequests | — | 5 | 15 |
| RetentionProposals | 4 | 6 | 12 |
| Dateianhänge (ca.) | 1–2 (50 %) | ~15 (25 %) | ~80 (10 %) |
| Zeitraum | 80 Tage | 365 Tage | 3 Jahre |

> **Hinweis:** `small` enthält kein Fallmanagement (keine Episoden, Ziele). Für die Entwicklung am Fallmanagement `medium` verwenden.

Seed-Zugangsdaten: Passwort `anlaufstelle2026`, Rollen `admin` / `leitung` / `fachkraft` / `assistenz`.

**7. Node-Abhängigkeiten installieren** (für Tailwind CSS)

```bash
npm install
```

**8. Entwicklungsserver starten**

In zwei Terminals parallel:

```bash
# Terminal 1: Django-Server
make run

# Terminal 2: Tailwind im Watch-Modus
make tailwind
```

Der Server ist unter `https://localhost:8443` erreichbar (selbstsigniertes Zertifikat — Browserwarnung akzeptieren). Fallback ohne HTTPS: `make run-http` (Port 8000).

---

## Make-Targets

| Target           | Beschreibung                                                      |
|------------------|-------------------------------------------------------------------|
| `make db`        | PostgreSQL-16-Container starten                                   |
| `make db-stop`   | PostgreSQL-Container stoppen und entfernen                        |
| `make migrate`   | Django-Migrationen ausführen                                      |
| `make run`       | Dev-Server starten (gunicorn + HTTPS auf `0.0.0.0:8443`)        |
| `make run-http`  | Fallback: Django runserver ohne HTTPS (`0.0.0.0:8000`)           |
| `make seed`      | Seed-Daten in die Datenbank laden                                 |
| `make tailwind`  | Tailwind CSS im Watch-Modus kompilieren                           |
| `make tailwind-build` | Tailwind CSS für Produktion minifiziert kompilieren          |
| `make lint`      | Code mit Ruff prüfen und Formatierung kontrollieren               |
| `make typecheck` | mypy auf `core/services` (strikt) + Baseline-Check (Refs [#741](https://github.com/tobiasnix/anlaufstelle/issues/741)) |
| `make test`      | Unit- und Integrationstests ausführen (ohne E2E)                  |
| `make test-e2e`  | End-to-End-Tests mit Playwright ausführen                         |
| `make check`     | Django-Systemcheck und Migrations-Konsistenz prüfen               |
| `make ci`        | Vollständige CI-Pipeline lokal: `lint` + `check` + `test-parallel` |
| `make test-focus T=<pfad>` | Einzelne Testdatei mit Fail-Fast                         |
| `make test-parallel` | Unit- und Integrationstests parallel (pytest-xdist)            |
| `make test-e2e-parallel` | E2E-Tests parallel (Default 2 Worker, konfigurierbar)     |
| `make test-e2e-smoke` | Nur Smoke-markierte E2E-Tests (~2-3 min)                    |
| `make deps-lock` | Lock-Files aus `requirements*.in` neu erzeugen (pip-tools)        |
| `make deps-check` | Prüft, ob Lock-Files aktuell zu `.in` sind (Drift-Detektion)     |
| `make dev`       | Datenbank starten, migrieren und Server starten (kombiniert)      |

Vor jedem Commit sollte `make ci` lokal erfolgreich durchlaufen.

### Port-Übersicht und Prozess-Hygiene

| Port | Prozess | Gestartet von | Zweck |
|------|---------|---------------|-------|
| **8443** | gunicorn (HTTPS) | `make run` / `make dev` | Dev-Server (Standard) |
| **8844** | gunicorn (HTTP) | `make test-e2e` / E2E-conftest | E2E-Testserver Worker 0 (temporär) |
| **8845+** | gunicorn (HTTP) | `make test-e2e-parallel` | E2E-Testserver Worker 1+ (temporär) |
| **8000** | Django runserver | `make run-http` | Fallback ohne HTTPS |
| **5432** | PostgreSQL | `make db` (Docker) | Datenbank |

**Kollisionsschutz:** `make run` und `make run-http` beenden automatisch alte Prozesse auf ihrem Port, bevor sie starten. Der E2E-conftest räumt Port 8844 ebenfalls auf.

**Troubleshooting bei langsamer App:**

```bash
# Doppelte Server-Prozesse finden
ps aux | grep -E 'gunicorn|runserver' | grep -v grep

# Bestimmten Port freigeben
lsof -ti :8443 | xargs kill

# Alle gunicorn-Prozesse beenden
pkill -f gunicorn
```

---

## Coding Conventions

### Python / Django

- **Python 3.13** mit vollständigen Type Hints wo sinnvoll.
- **Django 5.1+** — Class-based Views bevorzugt, Funktions-Views nur für einfache Fälle.
- Business-Logik gehört in `core/services/`, nicht in Views oder Models.
- Models sind aufgeteilt: ein Model (oder eng verwandte Models) pro Datei unter `core/models/`.
- Rollen-Zugriffsschutz über Mixins aus `core/views/mixins.py`.
- Keine neuen Abhängigkeiten ohne vorherige Absprache einführen.

### Facility-Scoping & Row Level Security

Jedes neue facility-gescopte Model muss auf **beiden** Verteidigungslinien abgesichert sein:

1. **Django-Layer (erste Linie):**
   - `facility = models.ForeignKey(Facility, ...)` am Model
   - `objects = FacilityScopedManager()` (aus [`src/core/models/managers.py`](src/core/models/managers.py))
   - Views/Services filtern via `.for_facility(request.current_facility)`
2. **PostgreSQL-RLS (zweite Linie, Defense-in-Depth):**
   - Neue Migration nach dem Muster von [`src/core/migrations/0047_postgres_rls_setup.py`](src/core/migrations/0047_postgres_rls_setup.py): Tabelle zu `DIRECT_TABLES` hinzufügen (oder `JOIN_TABLES`, falls kein direktes `facility_id`-Feld vorhanden ist). Die Migration setzt `ENABLE + FORCE ROW LEVEL SECURITY` plus eine `facility_isolation`-Policy.
   - Tabelle in `EXPECTED_TABLES` in [`src/tests/test_rls.py`](src/tests/test_rls.py) ergänzen, damit der RLS-Setup-Test die Abdeckung garantiert.

Details: [docs/ops-runbook.md § 9](docs/ops-runbook.md). RLS greift in Produktion nur, wenn der Django-DB-User **kein** Superuser ist (siehe [docs/coolify-deployment.md](docs/coolify-deployment.md)).

### Linting und Formatierung

Das Projekt verwendet [Ruff](https://docs.astral.sh/ruff/) für Linting und Formatierung:

```bash
# Prüfen
make lint

# Automatisch korrigieren
python -m ruff check src/ --fix
python -m ruff format src/
```

Die Ruff-Konfiguration befindet sich in `pyproject.toml`.

### Templates und Frontend

- Templates liegen unter `src/templates/`.
- HTMX für dynamische Interaktionen, Alpine.js für leichtgewichtige UI-Logik.
- Tailwind CSS für Styling — keine eigenen CSS-Klassen anlegen, soweit möglich.
- Barrierefreiheit (WCAG 2.1 AA) beachten.

### Conventional Commits

Commit-Messages folgen dem [Conventional Commits](https://www.conventionalcommits.org/)-Standard:

```
<typ>(<scope>): <kurze Beschreibung>

[optionaler Body]

Refs #<issue-nummer>
```

**Typen:**

| Typ        | Verwendung                                      |
|------------|-------------------------------------------------|
| `feat`     | Neues Feature                                   |
| `fix`      | Bugfix                                          |
| `test`     | Tests hinzufügen oder anpassen                  |
| `docs`     | Dokumentation                                   |
| `chore`    | Wartungsarbeiten, Konfiguration, Dependencies   |
| `refactor` | Refactoring ohne Verhaltensänderung             |
| `style`    | Formatierung, kein Logik-Unterschied            |

**Beispiele:**

```
feat(clients): add duplicate-detection on import

fix(events): prevent deletion of locked events

test(security): add field-sensitivity E2E tests

Refs #42
```

Commits sind atomar: eine logische Änderung pro Commit. Direkt nach jeder Aufgabe zu pushen.

---

## Tests

### Unit- und Integrationstests

```bash
make test
# entspricht: python -m pytest -m "not e2e"
```

Tests liegen unter `src/tests/`. Neue Tests kommen in die passende Datei oder in eine neue Datei nach dem Muster `test_<feature>.py`.

**Parallel:** `make test-parallel` nutzt pytest-xdist für parallele Ausführung auf allen verfügbaren CPU-Kernen. `make ci` nutzt automatisch die parallele Variante.

### End-to-End-Tests (Playwright)

```bash
make test-e2e
# entspricht: python -m pytest -m e2e --browser chromium
```

E2E-Tests liegen unter `src/tests/e2e/`. Sie sind mit `@pytest.mark.e2e` markiert und werden bei `make ci` automatisch ausgeschlossen, da sie einen laufenden Server mit Seed-Daten benötigen.

**Datenbank:** E2E-Tests nutzen eine eigene Datenbank (`anlaufstelle_e2e`), die automatisch erstellt, geflusht und mit Seed-Daten befüllt wird. Dev-Daten in `anlaufstelle` bleiben unangetastet. Details: [docs/e2e-architecture.md](docs/e2e-architecture.md).

**Wichtig:** Jedes neue Feature muss E2E-Tests enthalten, die zusammen mit dem Feature entwickelt werden — nicht nachträglich. Neue E2E-Test-Dateien werden nach dem Muster `test_<stream_oder_feature>.py` benannt.

Fixtures für Login, Server-Setup u. ä. liegen in `src/tests/e2e/conftest.py`. Ausführliches Runbook mit Checklisten und Troubleshooting: [docs/e2e-runbook.md](docs/e2e-runbook.md).

**Parallele E2E-Tests:** `make test-e2e-parallel` startet pro xdist-Worker einen eigenen gunicorn-Prozess auf eigenem Port mit eigener Datenbank. Default: 2 Worker (`E2E_WORKERS=4 make test-e2e-parallel` für mehr). `--dist loadfile` hält Tests aus derselben Datei auf demselben Worker.

**Smoke-Tests:** `make test-e2e-smoke` führt nur mit `@pytest.mark.smoke` markierte E2E-Tests aus (~40 kritische Flows, ~2-3 min). Ideal zur schnellen Validierung nach Feature-Implementierung.

### Vollständige CI-Pipeline lokal

```bash
make ci
# entspricht: lint + check + test-parallel
```

Diese Pipeline muss vor jedem Pull Request lokal grün sein.

---

## Pull-Request-Prozess

1. **Branch erstellen** — von `main` branchen, sprechenden Branch-Namen verwenden:
   ```bash
   git checkout -b feature/kurze-beschreibung
   # oder
   git checkout -b fix/was-repariert-wird
   ```

2. **Entwickeln** — kleine, atomare Commits; Issue-Nummern referenzieren (`Refs #N`).

3. **Lokal verifizieren:**
   ```bash
   make ci          # Lint, Check, Tests
   make test-e2e    # E2E-Tests
   ```
   Außerdem manuell im Browser prüfen, dass die Änderung wie erwartet funktioniert.

4. **Pull Request öffnen:**
   - Titel im Conventional-Commits-Stil (`feat: ...`, `fix: ...`)
   - Beschreibung: Was wurde geändert und warum? Welche Issues werden geschlossen?
   - Screenshot oder Demo, wenn UI-Änderungen enthalten sind
   - Verlinkung des zugehörigen GitHub-Issues

5. **Review:** Mindestens ein Approval erforderlich. Feedback sachlich und konstruktiv.

6. **Mergen:** Squash-Merge auf `main`, sobald CI grün und Approval vorhanden.

---

## Architektur-Überblick

### Rollen

| Rolle       | Beschreibung                                       |
|-------------|----------------------------------------------------|
| `admin`     | Vollzugriff, Systemkonfiguration                   |
| `leitung`   | Leitungsebene, erweiterte Auswertungen             |
| `fachkraft` | Fachkräfte, Kernarbeit mit Klientel und Events     |
| `assistenz` | Eingeschränkter Zugriff, unterstützende Aufgaben   |

### Projektstruktur

```
src/
  manage.py
  anlaufstelle/          # Django-Projekteinstellungen (settings, urls, wsgi)
  core/
    models/              # Ein Model (oder eng verwandte Models) pro Datei
      organization.py    # Organization, Facility
      user.py            # User (erweitert AbstractUser)
      client.py          # Client
      document_type.py   # DocumentType, FieldTemplate, DocumentTypeField
      event.py           # Event
      event_history.py   # EventHistory
      workitem.py        # WorkItem, DeletionRequest
      time_filter.py     # TimeFilter
      case.py            # Case
      episode.py         # Episode
      outcome.py         # OutcomeGoal, Milestone
      audit.py           # AuditLog
      settings.py        # Settings
    views/               # Class-based Views, aufgeteilt nach Funktionsbereich
      aktivitaetslog.py  # AktivitaetslogView (Startseite)
      timeline.py        # TimelineView (Event-Timeline)
      clients.py         # Client CRUD
      events.py          # Event CRUD + Löschworkflow
      workitems.py       # WorkItem CRUD
      cases.py           # Case, Episode, Goal, Milestone
      search.py          # Volltextsuche
      statistics.py      # Statistiken + Exporte
      audit.py           # AuditLogListView
      auth.py            # Login/Logout/Passwort
      account.py         # Benutzerprofil
      health.py          # HealthView
      mixins.py          # Rollen-Mixins
      pwa.py             # Service Worker
    services/            # Business-Logik (Verschlüsselung, Retention, …)
  templates/             # Django-Templates (HTMX-Partials eingeschlossen)
  static/
    css/
      input.css          # Tailwind-Eingabedatei
      styles.css         # Kompiliertes CSS (nicht committen)
  tests/
    e2e/                 # Playwright E2E-Tests
      conftest.py        # Shared Fixtures
      test_<feature>.py  # Tests pro Feature
```

### Wichtige Designentscheidungen

- **Verschlüsselung:** Sensible Felder werden auf Anwendungsebene verschlüsselt (`core/services/`). Der `ENCRYPTION_KEY` ist Pflicht in Produktion.
- **Audit-Log:** Alle sicherheitsrelevanten Aktionen werden in `AuditLog` geschrieben.
- **Retention:** Das Management-Command `enforce_retention` setzt Löschfristen durch.
- **HTMX-First:** Interaktive UI-Elemente werden bevorzugt über HTMX-Partials realisiert, um JavaScript minimal zu halten.
- **Service-Schicht:** Views delegieren Logik an Services — das erleichtert Tests und hält Views schlank.
