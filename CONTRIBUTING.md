> **[English version / Englische Version](CONTRIBUTING.en.md)**

# Contributing to Anlaufstelle

[![Lint](https://github.com/anlaufstelle/app/actions/workflows/lint.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/lint.yml)
[![Test](https://github.com/anlaufstelle/app/actions/workflows/test.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/test.yml)
[![E2E](https://github.com/anlaufstelle/app/actions/workflows/e2e.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/e2e.yml)

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![Django 6.0](https://img.shields.io/badge/django-6.0-green.svg)](https://www.djangoproject.com/)
[![PostgreSQL 18](https://img.shields.io/badge/postgresql-18-blue.svg)](https://www.postgresql.org/)
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
6. [Lizenz von Beiträgen](#lizenz-von-beiträgen)
7. [Architektur-Überblick](#architektur-überblick)

---

## Entwicklungsumgebung einrichten

### Voraussetzungen

- **Python 3.14** (empfohlen: via [pyenv](https://github.com/pyenv/pyenv))
- **PostgreSQL 18** (oder Docker, s. u.)
- **Node.js 24+** (für Tailwind CSS)
- **Docker** (optional, für die Datenbank)

### Schritt-für-Schritt

**1. Repository klonen**

```bash
git clone https://github.com/anlaufstelle/app.git
cd app
```

**2. Python-Umgebung einrichten**

```bash
python3.14 -m venv .venv
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

Das startet einen PostgreSQL-18-Container mit folgenden Zugangsdaten:

| Variable | Wert |
|-----------|---------------|
| DB-Name | anlaufstelle |
| User | anlaufstelle |
| Passwort | anlaufstelle |
| Port | 5432 |

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

> **Umgebungs-Guard:** `seed` läuft nur, wenn die Settings `SEED_ALLOWED = True`
> setzen (dev/test/e2e/devlive). Unter `prod`-Settings bricht das Command mit
> `CommandError` ab — Demo-Logins und `--flush` sind dort tabu; die
> Ersteinrichtung läuft über `manage.py create_super_admin` (Refs #1040).

**Scale-Profile im Überblick:**

| Daten | `small` (Default) | `medium` | `large` |
|---|---|---|---|
| Einrichtungen | 1 | 2 | 5 |
| Users (gesamt) | 7 (1 super_admin + 6 facility-User) | 13 (1 super_admin + 2×6) | 31 (1 super_admin + 5×6) |
| Users / Einrichtung | 6 (`admin`/`emma`/`miriam`/`markus`/`lena`/`felix`) | 6 | 6 |
| Clients / Einrichtung | 7 | 40 | 500 |
| Events / Einrichtung | 25 | 750 | 10.000 |
| Cases | 3 | 12 | 50 |
| Episoden || 20 | 80 |
| Wirkungsziele || 15 | 60 |
| Meilensteine / Ziel || 3 | 4 |
| WorkItems | 5 | 25 | 100 |
| Quick-Vorlagen / Einrichtung | 6 | 6 | 6 |
| DeletionRequests || 5 | 15 |
| RetentionProposals | 4 | 6 | 12 |
| Dateianhänge (ca.) | 1–2 (50 %) | ~15 (25 %) | ~80 (10 %) |
| Zeitraum | 80 Tage | 365 Tage | 3 Jahre |

> **Hinweis:** `small` enthält kein Fallmanagement (keine Episoden, Ziele). Für die Entwicklung am Fallmanagement `medium` verwenden.

Seed-Zugangsdaten: Passwort `anlaufstelle2026`, 7 Logins (Username → Rolle): `superadmin` → `super_admin` (keine `facility`-Zuordnung), `admin` → `facility_admin`, `emma` → `lead`, `miriam` → `staff`, `markus` → `staff`, `lena` → `assistant`, `felix` → `assistant`. Alle außer `superadmin` hängen an der Default-Einrichtung.

> **Production:** In Produktion gibt es **kein** Default-Passwort und keinen Default-`super_admin`. Die Erstinstallation läuft über `manage.py create_super_admin` (interaktiv, ohne Default). Details: `docs/dev/dev-deployment.md` § Production-Bootstrap (dev-only) und [docs/admin-guide.md § 2.1 Erstinstallation](docs/admin-guide.md). Lockout-Recovery: `manage.py unlock <username>`.

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

| Target | Beschreibung |
|------------------|-------------------------------------------------------------------|
| `make db` | PostgreSQL-18-Container starten |
| `make db-stop` | PostgreSQL-Container stoppen und entfernen |
| `make migrate` | Django-Migrationen ausführen |
| `make run` | Dev-Server starten (gunicorn + HTTPS auf `0.0.0.0:8443`) |
| `make run-http` | Fallback: Django runserver ohne HTTPS (`0.0.0.0:8000`) |
| `make seed` | Seed-Daten in die Datenbank laden |
| `make tailwind` | Tailwind CSS im Watch-Modus kompilieren |
| `make tailwind-build` | Tailwind CSS für Produktion minifiziert kompilieren |
| `make lint` | Code mit Ruff prüfen und Formatierung kontrollieren |
| `make typecheck` | mypy auf `core/services` (strikt) + Baseline-Check (Refs #741) |
| `make test` | Unit- und Integrationstests ausführen (ohne E2E) |
| `make test-e2e` | End-to-End-Tests mit Playwright ausführen |
| `make check` | Django-Systemcheck und Migrations-Konsistenz prüfen |
| `make ci` | Vollständige CI-Pipeline lokal: `lint` + `check` + Guards (deps/matrix/release-test/vendor-js/agent-docs) + `typecheck` + `test-parallel` |
| `make test-focus T=<pfad>` | Einzelne Testdatei mit Fail-Fast |
| `make test-parallel` | Unit- und Integrationstests parallel (pytest-xdist) |
| `make test-e2e-parallel` | E2E-Tests parallel (Default 2 Worker, konfigurierbar) |
| `make test-e2e-smoke` | Nur Smoke-markierte E2E-Tests (~2-3 min) |
| `make deps-lock` | Lock-Files aus `requirements*.in` neu erzeugen (pip-tools) |
| `make deps-check` | Prüft, ob Lock-Files aktuell zu `.in` sind (Drift-Detektion) |
| `make dev` | Datenbank starten, migrieren und Server starten (kombiniert) |
| `make clean` | Generierte Artefakte löschen (`__pycache__`, `*.pyc`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `src/staticfiles`) — `src/media/` bleibt unangetastet (Datenverlustrisiko) |

Vor jedem Commit sollte `make ci` lokal erfolgreich durchlaufen.

### Pre-Commit-Hooks (optional)

Für die schnelle Drift-Detektion vor dem Commit ist eine [`.pre-commit-config.yaml`](.pre-commit-config.yaml) hinterlegt (Refs #820, #860). Sie prüft `ruff` (lint + format), `makemigrations --check`, `mypy core/services`, den Translation-Version-Header und automatisches `pip-compile` bei `requirements*.in`-Änderungen.

```bash
.venv/bin/pip install pre-commit
pre-commit install                       # einmalig: commit-stage Hooks
pre-commit install --hook-type pre-push  # einmalig: pre-push Schnell-CI (Refs #860)
pre-commit run --all-files               # alle commit-stage Hooks gegen das Repo
```

**Zwei Stufen:**

- **Commit-Stage:** Ruff lint+format, `makemigrations --check`, `mypy`, Translation-Version, `pip-compile` bei Lock-File-Drift. Läuft in unter 5 s.
- **Pre-Push:** `make lint && make deps-check && make check` — der Solo-Maintainer-Ersatz für Required Status Checks. Branch Protection mit Required Status Checks greift bei direktem `git push` auf `main` nicht; der pre-push-Hook fängt deshalb genau das ab, was sonst rote CI nach Push produzieren würde (Lock-Drift, Format-Drift, Migrations-Drift). Läuft in ~10 s. Tests bleiben in CI.

CI auf [`anlaufstelle/app`](https://github.com/anlaufstelle/app/actions) ist die letztgültige Quelle der Wahrheit; der pre-push-Hook reduziert nur die Wahrscheinlichkeit roter CI nach Push.

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

- **Python 3.14** mit vollständigen Type Hints wo sinnvoll (Codebasis bleibt 3.13-kompatibel, siehe `requires-python`).
- **Django 6.0+** — Class-based Views bevorzugt, Funktions-Views nur für einfache Fälle.
- Business-Logik gehört in `core/services/`, nicht in Views oder Models.
- Models sind aufgeteilt: ein Model (oder eng verwandte Models) pro Datei unter `core/models/`.
- Rollen-Zugriffsschutz über Mixins aus `core/views/mixins.py` — verfügbar: `SuperAdminRequiredMixin` (nur `/system/`), `FacilityAdminRequiredMixin` (Admin der eigenen Facility), `LeadOrAdminRequiredMixin`, `StaffRequiredMixin`, `AssistantOrAboveRequiredMixin`.
- Keine neuen Abhängigkeiten ohne vorherige Absprache einführen.

### Facility-Scoping & Row Level Security

Jedes neue facility-gescopte Model muss auf **beiden** Verteidigungslinien abgesichert sein:

1. **Django-Layer (erste Linie):**
 - `facility = models.ForeignKey(Facility, ...)` am Model
 - `objects = FacilityScopedManager()` (aus [`src/core/models/managers.py`](src/core/models/managers.py))
 - Views/Services filtern via `.for_facility(request.current_facility)`
2. **PostgreSQL-RLS (zweite Linie, Defense-in-Depth):**
 - Neue Migration nach dem Muster von [`src/core/migrations/0047_postgres_rls_setup.py`](src/core/migrations/0047_postgres_rls_setup.py): Tabelle zu `DIRECT_TABLES` hinzufügen (oder `JOIN_TABLES`, falls kein direktes `facility_id`-Feld vorhanden ist). Die Migration setzt `ENABLE + FORCE ROW LEVEL SECURITY` plus eine `facility_isolation`-Policy.
 - `EXPECTED_TABLES` in [`src/tests/test_rls.py`](src/tests/test_rls.py) ist seit #1096 **ableitungsbasiert**: Tabellen mit direktem `facility`-FK (DIRECT) leitet die Test-Suite automatisch aus der Model-Registry ab — ein neues solches Model erscheint ohne Handgriff in `EXPECTED_TABLES`, und der RLS-Coverage-Guard (`TestRLSCoverageGuard`) failt, bis die RLS-Migration **und** die PII-Klassifikation existieren. Nur **JOIN-gescopte** Tabellen ohne direkten FK brauchen einen Hand-Eintrag in der `JOIN_SCOPED_TABLES`-Konstante; die Auth-Grenzen-Ausnahme (`core_user`) steht in `NOT_RLS_SCOPED`.

Details: [docs/ops-runbook.md § 9](docs/ops-runbook.md). RLS greift in Produktion nur, wenn der Django-DB-User **kein** Superuser ist (siehe `docs/dev/dev-deployment.md` (dev-only), primaerer Pfad nach [ADR-017](docs/adr/017-deployment-topology.md); [docs/coolify-deployment.md](docs/coolify-deployment.md) ist eine alternative Plattform-Anleitung).

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

#### Vendored JS-Libraries aktualisieren (Refs #1076)

Es gibt **keinen Frontend-Bundler** — vier Libraries werden als vorgebaute `*.min.js` unter [`src/static/js/`](src/static/js/) eingecheckt (vendored) und per `{% static %}` geladen. Damit sie trotzdem im Dependabot-/CVE-Radar liegen, sind sie als **exakt gepinnte** `devDependencies` in [`package.json`](package.json) geführt:

| Library | npm-Paket | vendored Datei | dist-Quelle in `node_modules/` |
|---|---|---|---|
| htmx | `htmx.org` | `htmx.min.js` | `dist/htmx.min.js` |
| Alpine.js (CSP-Build) | `@alpinejs/csp` | `alpine-csp.min.js` | `dist/cdn.min.js` |
| Dexie | `dexie` | `dexie.min.js` | `dist/dexie.min.js` |
| Chart.js | `chart.js` | `chart.min.js` | `dist/chart.umd.js` *(bereits minifiziert)* |

> Achtung: Das CSP-Build von Alpine ist das **eigene** Paket `@alpinejs/csp`, nicht `alpinejs`. Chart.js liefert im npm-`dist` keine eigene `*.min.js`; der UMD-Build `chart.umd.js` ist bereits minifiziert.

**Update-Ablauf** (z. B. nach einem Dependabot-Bump, der nur `package.json`/`package-lock.json` ändert):

```bash
npm ci                                # node_modules/ auf package-lock-Stand bringen
make sync-vendor-js                   # dist-Builds nach src/static/js/ kopieren
git add package.json package-lock.json src/static/js
```

**Verifikation:** `make ci` führt den Drift-Guard `make verify-vendor-js-sync` aus ([`scripts/verify_vendor_js_sync.py`](scripts/verify_vendor_js_sync.py)). Er vergleicht die in `package.json` gepinnte Version mit dem Versions-String im eingecheckten `*.min.js` und failt bei Drift — so kann das Re-Vendoring nicht vergessen werden. Der Guard ist reiner String-Vergleich (kein node/npm nötig). Zusätzlich E2E-Smoke (`make test-e2e-smoke`), da die Offline-Flows an Dexie hängen.

#### HTMX & Live-Regions (Refs #811)

Damit HTMX-Erfolgsmeldungen Screen-Reader-Nutzer*innen erreichen, gilt:

- Es existiert genau eine **stabile Live-Region** in [`base.html`](src/templates/base.html): `#flash-messages` mit `role="status" aria-live="polite" aria-atomic="true"`.
- HTMX-Antworten, die einen Erfolg ankündigen sollen, schwingen entweder per `hx-target="#flash-messages" hx-swap="innerHTML"` oder per `hx-swap-oob="innerHTML:#flash-messages"`. Den Wrapper-`<div>` selbst **nie** per `outerHTML` ersetzen — sonst wird die Live-Region neu instanziiert und der Announcement-Trigger verloren.
- Wenn ein Bulk-Endpoint einen vollständigen Reload erzwingen muss (z. B. WorkItem-Bulk, Retention-Bulk), läuft das über `HX-Redirect` — die Folge-Page rendert das Django-`messages`-Framework wieder in `#flash-messages`.

#### URL-Schema: HTMX-Fragmente vs. JSON-APIs (Refs #848)

Endpunkte werden nach Response-Typ getrennt — Templates referenzieren beide ausschließlich über `{% url 'name' %}`:

- **HTML-Fragmente (HTMX-Partials):** Pfad `/partials/<feature>/<action>/`. Beispiel: `partials/clients/autocomplete/`, `partials/retention/<uuid:pk>/approve/`. Antwort ist immer HTML, gerendert mit `partials/`-Template.
- **JSON-APIs:** Pfad `/api/v1/<feature>/<action>/`. Beispiel: `api/v1/offline/bundle/client/<uuid:pk>/`. Antwort ist JSON, von Service-Workern oder JS-`fetch()`-Calls konsumiert.

Neue Endpunkte gehören entsprechend in eine der beiden Pfad-Gruppen. URL-Namen bleiben kurz und feature-spezifisch (`client_autocomplete`, `offline_bundle`); Pfad-Prefixe wechseln nur dann, wenn die Response-Form wechselt. Direkte `fetch("/api/...")`-Aufrufe in JS dürfen nur unter `/api/v1/` gehen — HTMX-Partials werden niemals als JSON konsumiert.

### Übersetzungen (i18n)

- **EN im selben Commit nachziehen (verbindlich, Refs #1215).** Wer übersetzbare Strings ändert — Django-`{% trans %}`/`.po` unter [`src/locale/`](src/locale/) oder die gespiegelten EN-Dokumente (`*.en.md`, [`docs/en/`](docs/en/)) — zieht die englische Entsprechung im **selben Commit** nach, nicht in einem nachgelagerten Sync-Commit. Konsistent mit der i18n-als-eigener-Commit-Regel in `AGENTS.md` § Git & Commits (dev-only): DE und EN gehören in *einen* `chore(i18n):`-Commit, getrennt vom Feature.
- **Stempel als Backstop:** [`scripts/check_translation_versions.py`](scripts/check_translation_versions.py) erzwingt für die EN-Dokumente einen `translation-version`-Header == aktuelle Minor-Version (Pre-Commit-Hook + `make release-gates`, hartes Gate seit #1078). Der Stempel fängt Drift zum Release ab; die „im selben Commit"-Regel verhindert, dass Drift überhaupt entsteht.

### Conventional Commits

Commit-Messages folgen dem [Conventional Commits](https://www.conventionalcommits.org/)-Standard:

```
<typ>(<scope>): <kurze Beschreibung>

[optionaler Body]

Refs #<issue-nummer>
```

**Typen:**

| Typ | Verwendung |
|------------|-------------------------------------------------|
| `feat` | Neues Feature |
| `fix` | Bugfix |
| `test` | Tests hinzufügen oder anpassen |
| `docs` | Dokumentation |
| `chore` | Wartungsarbeiten, Konfiguration, Dependencies |
| `refactor` | Refactoring ohne Verhaltensänderung |
| `style` | Formatierung, kein Logik-Unterschied |

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

### Test-Driven Development (Unit/Service)

Seit 2026-05-20 ist Test-Driven Development für die Unit-/Service-Schicht verpflichtend. Reihenfolge **vor** jeder Service-, Form-, Model- oder CBV-Änderung:

1. **Red** — pytest-Test in der passenden Datei unter `src/tests/` schreiben, der das gewünschte Verhalten beschreibt, aber heute fehlschlägt. Test mit `pytest -x` ausführen und prüfen, dass er mit dem erwarteten `AssertionError` rot wird.
2. **Green** — minimale Implementation in `src/core/...`, bis genau dieser Test grün wird. Keine zusätzlichen Features, keine vorgezogene Verallgemeinerung.
3. **Refactor** — Code (und ggf. Test) aufräumen, während die Suite grün bleibt. `pytest -x` nach jedem Cleanup-Schritt.

**Kein „falsch grün" — Assertion-Stärke schützen** (Refs #1150): Ein roter Test bedeutet im Default, dass die **Root Cause im Code** zu fixen ist — nicht der Test. Eine **bestehende Assertion abzuschwächen** (lockern, entfernen, ans tatsächliche kaputte Verhalten nachziehen) ist ein **harter Review-Stopp**: zulässig nur, wenn sich die *Anforderung* geändert hat (nicht der Code), und dann mit expliziter Begründung im Commit-Body. Bei **Bugfixes** zuerst einen **failing-test-first** schreiben, der den Bug reproduziert, *bevor* der Code angefasst wird (Skill `superpowers:systematic-debugging`). Hintergrund: 96 % Coverage misst *ausgeführte*, nicht *geprüfte* Zeilen — eine abgeschwächte Assertion bleibt grün und fällt erst im Mutation-Run auf.

Beispiel (Service-Schicht, Pseudonym-Hashing aus [Issue #844](https://github.com/anlaufstelle/app/issues/844)):

```python
# Red — src/tests/test_pseudonym_hashing.py
def test_client_pseudonym_hash_is_stable():
    from django.conf import settings
    settings.PSEUDONYM_HMAC_KEY = "test-key"

    from core.services.audit_hash import hmac_pseudonym
    assert hmac_pseudonym("anlauf-2026-0001") == hmac_pseudonym("anlauf-2026-0001")
    assert hmac_pseudonym("anlauf-2026-0001") != hmac_pseudonym("anlauf-2026-0002")
```

```bash
pytest src/tests/test_pseudonym_hashing.py -x
# → ModuleNotFoundError / ImportError → erwartetes Red.
```

```python
# Green — src/core/services/audit_hash.py
import hmac, hashlib
from django.conf import settings

def hmac_pseudonym(pseudonym: str) -> str:
    key = settings.PSEUDONYM_HMAC_KEY.encode()
    return hmac.new(key, pseudonym.encode(), hashlib.sha256).hexdigest()
```

```bash
pytest src/tests/test_pseudonym_hashing.py -x
# → 1 passed → Green.
```

**Refactor:** etwa Auslagerung der Key-Auflösung in Helper, sobald ein zweiter Hash-Anwendungsfall existiert — Test bleibt grün.

**Geltungsbereich** (TDD-Pflicht):

- Service-, Form-, Model-, Helper-, CBV-/View-Unit-Tests, also alles unter `src/tests/` außerhalb von `src/tests/e2e/`.

**Ausnahmen** (manuell-first / TDD-neutral):

- **E2E-Tests** in `src/tests/e2e/` bleiben wie bisher Playwright-getrieben — erst manuell durchklicken (siehe `### End-to-End-Tests (Playwright)`), dann Tests aus der Beobachtung schreiben.
- Django-Migrations-Generierung, Squash-Migrations, Tooling-/Konfig-Patches (CI-Schwellen, Whitelists, `pyproject.toml`).
- Reine Markdown-/Doku-Änderungen.
- One-Shot-Hygiene-Skripte ohne Wiederverwendung.

Querverweise: `CLAUDE.md § Tests & Verifikation` (dev-only) und Skill `superpowers:test-driven-development`.

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

### Test-Schichten-Modell beim Entwickeln

Während der Entwicklung nie die volle Suite laufen lassen — in Schichten vorgehen, jeweils mit Fail-Fast (`pytest -x`):

1. **Fokus:** `pytest src/tests/test_<betroffene_datei>.py -x` — nur Tests im geänderten Bereich. E2E fokussiert: `pytest src/tests/e2e/test_<feature>.py -x`.
2. **Gruppe:** Betroffene Testdateien zusammen: `pytest src/tests/test_a.py src/tests/test_b.py -x`.
3. **Parallel:** `make test-parallel` — volle Unit-Suite parallel, vor dem Commit.
4. **Smoke:** `make test-e2e-smoke` — ~40 kritische E2E-Flows (~2-3 min), nach Feature fertig.
5. **E2E:** `make test-e2e-parallel` — volle E2E-Suite parallel, vor Push.

**Fail-Fast immer:** Tests mit `pytest -x` ausführen. Bei Fehlern fixen, prüfen ob verwandte Tests denselben Root Cause teilen, erst dann wieder Gesamtlauf. Re-Runs: `pytest --lf -x` (nur zuletzt fehlgeschlagene) oder `pytest --ff -x` (fehlgeschlagene zuerst).

**Wait-Strategie (E2E):** `domcontentloaded` oder `wait_for_url()` — **niemals** `networkidle`.

### Vollständige CI-Pipeline lokal

```bash
make ci
# entspricht: lint + check + Guards (deps/matrix/release-test/vendor-js/agent-docs) + typecheck + test-parallel
```

Diese Pipeline muss vor jedem Pull Request lokal grün sein.

### Mutation-Testing (mutmut)

Mutation-Testing prüft, wie viele synthetische Code-Mutationen die Test-Suite tatsächlich erkennt. Konfiguration in `pyproject.toml` (`[tool.mutmut]`). Erwartete Laufzeit: 3–6 h auf `core.services` + `core.forms`. Daher nicht PR-Pflicht, sondern punktuell pro Wellen-Issue.

```bash
make mutation           # mutmut run via scripts/dev/run_mutmut.py
make mutation-report    # Survivors-Liste, nicht-interaktiv
```

`make mutation` ist resume-fähig (mutmut speichert State in `mutants/**/*.py.meta` und springt bei Neustart automatisch dort weiter, wo der Vorlauf stehengeblieben ist).

Für längere Runs auf einer Sandbox, die OOM-Killer / Idle-Killer mitbringt, gibt es `scripts/dev/run_mutmut_watchdog.sh` (dev-only):

```bash
# Default: 3 zusätzliche Restarts, Stall-Threshold 5 min, 2 mutmut-Worker
scripts/dev/run_mutmut_watchdog.sh

# Aggressiveres Profil
MAX_RESTARTS=5 STALL_THRESHOLD=600 MUTMUT_MAX_CHILDREN=4 \
    scripts/dev/run_mutmut_watchdog.sh
```

Der Watchdog erkennt stillstehende oder gestorbene Master-Prozesse und startet `make mutation` neu — der Resume-Mechanismus übernimmt. Exit 0, sobald `mutants/mutmut-stats.json` während der Watchdog-Session geschrieben wurde. Aufruf-Hintergrund: Refs #937.

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

### Code of Conduct

Alle Mitwirkenden verpflichten sich auf den [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Belästigungsfälle melden Mitwirkende vertraulich an `kontakt@anlaufstelle.app` (Refs #836).

5. **Review:** Mindestens ein Approval erforderlich. Feedback sachlich und konstruktiv.

6. **Mergen:** Squash-Merge auf `main`, sobald CI grün und Approval vorhanden. Externe Pull Requests werden nicht direkt gemergt, sondern über den Release-Spiegel übernommen (siehe unten).

### Wie dein PR gemergt wird (Release-Spiegel)

Der `main`-Branch dieses Repositories ist ein **Release-Spiegel**: Er wird bei jedem Release
aus dem internen Entwicklungszweig aufgebaut (dabei werden rein interne
Entwicklungswerkzeuge entfernt). Externe Pull Requests werden deshalb nicht direkt auf
`main` gemergt, sondern:

1. Das Review findet ganz normal hier am PR statt.
2. Nach Freigabe übernehmen wir deine Commits in den Entwicklungszweig — dein
 Git-Author-Feld (und damit deine Urheberschaft) bleibt erhalten.
3. Mit dem nächsten Release erscheint deine Änderung auf `main`; dein PR wird dann mit
 Verweis auf das Release geschlossen und im CHANGELOG genannt.

Dass ein PR „geschlossen" statt „gemergt" angezeigt wird, ist also keine Ablehnung — es ist
die Mechanik des Release-Spiegels.

---

## Lizenz von Beiträgen

### Inbound = Outbound (AGPL-3.0)

Dieses Projekt ist unter der [GNU AGPL v3](LICENSE) lizenziert. Mit dem Einreichen eines
Beitrags (Pull Request, Patch, Doku-Änderung) lizenzierst du deinen Beitrag unter derselben
Lizenz („inbound = outbound", vgl. auch [GitHub Terms of Service, D.6](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#6-contributions-under-repository-license)).
Du behältst dein Urheberrecht — es findet keine Rechteübertragung statt, und wir verlangen
kein Contributor License Agreement (CLA).

### Developer Certificate of Origin (DCO)

Mit jedem Commit bestätigst du das [Developer Certificate of Origin 1.1](https://developercertificate.org/):
dass du das Recht hast, den Beitrag unter der Projektlizenz einzureichen — weil er von dir
stammt oder aus kompatibel lizenzierten Quellen mit intakten Lizenzhinweisen übernommen wurde.

Dazu signierst du jeden Commit mit deinem Namen und einer gültigen E-Mail-Adresse:

 git commit -s

Das fügt eine Zeile `Signed-off-by: Vorname Nachname <email@example.org>` an die
Commit-Message an. Pull Requests ohne Sign-off können wir nicht übernehmen; ein vergessenes
Sign-off lässt sich mit `git commit --amend -s` bzw. `git rebase --signoff` nachholen.

**Fremdmaterial:** Übernimm keinen Code und keine Texte aus Quellen, deren Lizenz nicht
AGPL-kompatibel ist. Kennzeichne Übernahmen (Quelle + Lizenz) im Commit oder PR.

**KI-Unterstützung:** Beiträge dürfen mit KI-Werkzeugen erstellt sein (dieses Projekt nutzt
sie selbst). Für die Rechte am Ergebnis bürgst du per Sign-off wie für jeden anderen
Beitrag — prüfe KI-Output entsprechend sorgfältig.

### Namensnennung

Deine Urheberschaft bleibt erhalten: Beim Übernehmen deiner Commits in den internen
Entwicklungszweig bleibt dein Git-Author-Feld (bzw. ein `Co-authored-by:`-Trailer) bestehen.
Der öffentliche `main`-Branch ist ein Release-Spiegel (s. o., „Wie dein PR gemergt wird");
dort werden externe Beiträge im [CHANGELOG](CHANGELOG.md) des jeweiligen Releases namentlich
genannt.

---

## Architektur-Überblick

### Rollen

Anlaufstelle kennt fünf Rollen — vier facility-gebunden, eine facility-übergreifend:

| Rolle | DB-Wert | Scope | Beschreibung |
|-------|---------|-------|--------------|
| Systemadministration | `super_admin` | facility-übergreifend (`/system/`) | Hosting, Bootstrap, Pre-Auth-AuditLogs. Wird über `manage.py create_super_admin` angelegt. |
| Anwendungsbetreuung | `facility_admin` | eine Einrichtung | Vollzugriff in der eigenen Facility (Audit-Log, DSGVO-Paket, Benutzerverwaltung). |
| Leitung | `lead` | eine Einrichtung | Leitungsebene, erweiterte Auswertungen, Löschanträge genehmigen. |
| Fachkraft | `staff` | eine Einrichtung | Kernarbeit mit Personen und Events. |
| Assistenz | `assistant` | eine Einrichtung | Eingeschränkter Zugriff, unterstützende Aufgaben. |

Architektur-Entscheidung: [ADR-018](docs/adr/018-rollenmodell-superadmin.md). Details zum RLS-Bypass für `super_admin`: [ADR-005 Update 2026-05-10](docs/adr/005-facility-scoping-and-rls.md).

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
