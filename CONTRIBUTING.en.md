> **[Deutsche Version / German version](CONTRIBUTING.md)**

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

Welcome! This guide explains how to set up the development environment, how the code is structured, and how to contribute changes.

---

## Table of Contents

1. [Development Environment Setup](#development-environment-setup)
2. [Make Targets](#make-targets)
3. [Coding Conventions](#coding-conventions)
4. [Tests](#tests)
5. [Pull Request Process](#pull-request-process)
6. [Architecture Overview](#architecture-overview)

---

## Development Environment Setup

### Prerequisites

- **Python 3.13** (recommended: via [pyenv](https://github.com/pyenv/pyenv))
- **PostgreSQL 16** (or Docker, see below)
- **Node.js 20+** (for Tailwind CSS)
- **Docker** (optional, for the database)

### Step by Step

**1. Clone the repository**

```bash
git clone https://github.com/anlaufstelle/app.git
cd app
```

**2. Set up the Python environment**

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes runtime + test/lint tools
# Or runtime only (e.g. for prod Docker build):
# pip install -r requirements.txt
```

> **Lock files:** `requirements.txt` / `requirements-dev.txt` are generated
> lock files with pinned transitive dependencies (via
> [pip-tools](https://github.com/jazzband/pip-tools)). Direct dependencies
> live in `requirements.in` / `requirements-dev.in`. After changing those:
> run `make deps-lock`. Details:
> [docs/ops-runbook.md § 8](docs/ops-runbook.md#8-dependencies-aktualisieren).

**3. Start the database**

With Docker (recommended):

```bash
make db
```

This starts a PostgreSQL 16 container with the following credentials:

| Variable  | Value         |
|-----------|---------------|
| DB name   | anlaufstelle  |
| User      | anlaufstelle  |
| Password  | anlaufstelle  |
| Port      | 5432          |

Alternatively, a locally installed PostgreSQL instance can be used. The connection URL must then be set in the `DATABASE_URL` environment variable.

**4. Configure environment variables**

Create a `.env` file in the project directory (or export the variables):

```bash
SECRET_KEY=dev-secret-key-bitte-aendern
DATABASE_URL=postgres://anlaufstelle:anlaufstelle@localhost:5432/anlaufstelle
DEBUG=true
ENCRYPTION_KEY=<32-byte-base64-key>
```

Generate an `ENCRYPTION_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**5. Run migrations**

```bash
make migrate
```

**6. Load seed data** (optional, for local development)

```bash
make seed                              # Default: small
python src/manage.py seed --scale medium   # more data including case management
python src/manage.py seed --scale large    # load-testing volume
python src/manage.py seed --flush          # flush existing data first
```

**Scale profiles overview:**

| Data | `small` (default) | `medium` | `large` |
|---|---|---|---|
| Facilities | 1 | 2 | 5 |
| Users / facility | 4 | 4 | 4 |
| Clients / facility | 7 | 40 | 500 |
| Events / facility | 25 | 750 | 10,000 |
| Cases | 3 | 12 | 50 |
| Episodes | — | 20 | 80 |
| Impact goals | — | 15 | 60 |
| Milestones / goal | — | 3 | 4 |
| WorkItems | 5 | 25 | 100 |
| DeletionRequests | — | 5 | 15 |
| RetentionProposals | 4 | 6 | 12 |
| Attachments (approx.) | 1–2 (50 %) | ~15 (25 %) | ~80 (10 %) |
| Time span | 80 days | 365 days | 3 years |

> **Note:** `small` does not include case management (no episodes, goals). Use `medium` when developing case management features.

Seed credentials: password `anlaufstelle2026`, roles `admin` / `leitung` / `fachkraft` / `assistenz`.

**7. Install Node dependencies** (for Tailwind CSS)

```bash
npm install
```

**8. Start the development server**

Run in two terminals in parallel:

```bash
# Terminal 1: Django server
make run

# Terminal 2: Tailwind in watch mode
make tailwind
```

The server is available at `https://localhost:8443` (self-signed certificate — accept the browser warning). Fallback without HTTPS: `make run-http` (port 8000).

---

## Make Targets

| Target           | Description                                                       |
|------------------|-------------------------------------------------------------------|
| `make db`        | Start PostgreSQL 16 container                                     |
| `make db-stop`   | Stop and remove PostgreSQL container                              |
| `make migrate`   | Run Django migrations                                             |
| `make run`       | Start dev server (gunicorn + HTTPS on `0.0.0.0:8443`)           |
| `make run-http`  | Fallback: Django runserver without HTTPS (`0.0.0.0:8000`)        |
| `make seed`      | Load seed data into the database                                  |
| `make tailwind`  | Compile Tailwind CSS in watch mode                                |
| `make tailwind-build` | Compile minified Tailwind CSS for production                 |
| `make lint`      | Check code with Ruff and verify formatting                        |
| `make typecheck` | mypy on `core/services` (strict) + baseline check (Refs [#741](https://github.com/tobiasnix/anlaufstelle/issues/741)) |
| `make test`      | Run unit and integration tests (excluding E2E)                    |
| `make test-e2e`  | Run end-to-end tests with Playwright                              |
| `make check`     | Run Django system checks and verify migration consistency         |
| `make ci`        | Full CI pipeline locally: `lint` + `check` + `test-parallel`      |
| `make test-focus T=<path>` | Single test file with fail-fast                          |
| `make test-parallel` | Unit and integration tests in parallel (pytest-xdist)         |
| `make test-e2e-parallel` | E2E tests in parallel (default 2 workers, configurable)   |
| `make test-e2e-smoke` | Smoke-tagged E2E tests only (~2-3 min)                       |
| `make deps-lock` | Regenerate lock files from `requirements*.in` (pip-tools)         |
| `make deps-check` | Verify lock files match `.in` (drift detection)                  |
| `make dev`       | Start database, run migrations, and start server (combined)       |

`make ci` should pass locally before every commit.

---

## Coding Conventions

### Python / Django

- **Python 3.13** with full type hints where appropriate.
- **Django 5.1+** — class-based views preferred, function views only for simple cases.
- Business logic belongs in `core/services/`, not in views or models.
- Models are split up: one model (or closely related models) per file under `core/models/`.
- Role-based access control via mixins from `core/views/mixins.py`.
- Do not introduce new dependencies without prior discussion.

### Facility Scoping & Row Level Security

Every new facility-scoped model must be protected on **both** defense lines:

1. **Django layer (first line):**
   - `facility = models.ForeignKey(Facility, ...)` on the model
   - `objects = FacilityScopedManager()` (from [`src/core/models/managers.py`](src/core/models/managers.py))
   - Views/services filter via `.for_facility(request.current_facility)`
2. **PostgreSQL RLS (second line, defense in depth):**
   - New migration following the pattern of [`src/core/migrations/0047_postgres_rls_setup.py`](src/core/migrations/0047_postgres_rls_setup.py): add the table to `DIRECT_TABLES` (or `JOIN_TABLES` if no direct `facility_id` column exists). The migration sets `ENABLE + FORCE ROW LEVEL SECURITY` plus a `facility_isolation` policy.
   - Add the table to `EXPECTED_TABLES` in [`src/tests/test_rls.py`](src/tests/test_rls.py) so the RLS setup test guarantees coverage.

Details: [docs/ops-runbook.md § 9](docs/ops-runbook.md). RLS only takes effect in production when the Django DB user is **not** a superuser (see [docs/coolify-deployment.md](docs/coolify-deployment.md)).

### Linting and Formatting

The project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
# Check
make lint

# Auto-fix
python -m ruff check src/ --fix
python -m ruff format src/
```

The Ruff configuration is located in `pyproject.toml`.

### Templates and Frontend

- Templates are located under `src/templates/`.
- HTMX for dynamic interactions, Alpine.js for lightweight UI logic.
- Tailwind CSS for styling — avoid creating custom CSS classes where possible.
- Adhere to accessibility standards (WCAG 2.1 AA).

### Conventional Commits

Commit messages follow the [Conventional Commits](https://www.conventionalcommits.org/) standard:

```
<type>(<scope>): <short description>

[optional body]

Refs #<issue-number>
```

**Types:**

| Type       | Usage                                           |
|------------|-------------------------------------------------|
| `feat`     | New feature                                     |
| `fix`      | Bug fix                                         |
| `test`     | Add or update tests                             |
| `docs`     | Documentation                                   |
| `chore`    | Maintenance, configuration, dependencies        |
| `refactor` | Refactoring without behavior change             |
| `style`    | Formatting, no logic change                     |

**Examples:**

```
feat(clients): add duplicate-detection on import

fix(events): prevent deletion of locked events

test(security): add field-sensitivity E2E tests

Refs #42
```

Commits are atomic: one logical change per commit. Push immediately after completing each task.

---

## Tests

### Unit and Integration Tests

```bash
make test
# equivalent to: python -m pytest -m "not e2e"
```

Tests are located under `src/tests/`. New tests go into the appropriate file or a new file following the pattern `test_<feature>.py`.

**Parallel:** `make test-parallel` uses pytest-xdist to run on all available CPU cores. `make ci` uses the parallel variant automatically.

### End-to-End Tests (Playwright)

```bash
make test-e2e
# equivalent to: python -m pytest -m e2e --browser chromium
```

E2E tests are located under `src/tests/e2e/`. They are marked with `@pytest.mark.e2e` and are automatically excluded from `make ci`, since they require a running server with seed data.

**Database:** E2E tests use a dedicated database (`anlaufstelle_e2e`) that is automatically created, flushed, and populated with seed data. Dev data in `anlaufstelle` remains untouched. Details: [docs/e2e-architecture.md](docs/e2e-architecture.md).

**Important:** Every new feature must include E2E tests that are developed alongside the feature — not added later. New E2E test files are named following the pattern `test_<stream_or_feature>.py`.

Fixtures for login, server setup, etc. are located in `src/tests/e2e/conftest.py`. Comprehensive runbook with checklists and troubleshooting: [docs/e2e-runbook.md](docs/e2e-runbook.md).

**Parallel E2E tests:** `make test-e2e-parallel` starts a separate gunicorn process per xdist worker on its own port with its own database. Default: 2 workers (`E2E_WORKERS=4 make test-e2e-parallel` for more). `--dist loadfile` keeps tests from the same file on the same worker.

**Smoke tests:** `make test-e2e-smoke` runs only E2E tests marked with `@pytest.mark.smoke` (~40 critical flows, ~2-3 min). Ideal for fast validation after feature implementation.

### Full CI Pipeline Locally

```bash
make ci
# equivalent to: lint + check + test-parallel
```

This pipeline must pass locally before every pull request.

---

## Pull Request Process

1. **Create a branch** — branch off `main`, use a descriptive branch name:
   ```bash
   git checkout -b feature/short-description
   # or
   git checkout -b fix/what-is-being-fixed
   ```

2. **Develop** — small, atomic commits; reference issue numbers (`Refs #N`).

3. **Verify locally:**
   ```bash
   make ci          # Lint, check, tests
   make test-e2e    # E2E tests
   ```
   Also manually verify in the browser that the change works as expected.

4. **Open a pull request:**
   - Title in Conventional Commits style (`feat: ...`, `fix: ...`)
   - Description: What was changed and why? Which issues are being closed?
   - Screenshot or demo if UI changes are included
   - Link to the associated GitHub issue

5. **Review:** At least one approval required. Feedback should be objective and constructive.

6. **Merge:** Squash-merge to `main` once CI is green and approval is given.

---

## Architecture Overview

### Roles

| Role        | Description                                        |
|-------------|----------------------------------------------------|
| `admin`     | Full access, system configuration                  |
| `leitung`   | Lead level, extended reports                       |
| `fachkraft` | Staff, core work with clients and events           |
| `assistenz` | Restricted access, supporting tasks                |

### Project Structure

```
src/
  manage.py
  anlaufstelle/          # Django project settings (settings, urls, wsgi)
  core/
    models/              # One model (or closely related models) per file
      organization.py    # Organization, Facility
      user.py            # User (extends AbstractUser)
      client.py          # Client
      document_type.py   # DocumentType, FieldTemplate, DocumentTypeField
      event.py           # Event
      event_history.py   # EventHistory
      workitem.py        # WorkItem, DeletionRequest
      time_filter.py     # TimeFilter
      case.py            # Case
      audit.py           # AuditLog
      settings.py        # Settings
    views/               # Class-based views, split by feature area
      aktivitaetslog.py  # AktivitaetslogView (home page)
      timeline.py        # TimelineView (event timeline)
      clients.py         # Client CRUD
      events.py          # Event CRUD + deletion workflow
      workitems.py       # WorkItem CRUD
      search.py          # Full-text search
      statistics.py      # Statistics + exports
      audit.py           # AuditLogListView
      auth.py            # Login/Logout/Password
      account.py         # User profile
      health.py          # HealthView
      mixins.py          # Role mixins
      pwa.py             # Service Worker
    services/            # Business logic (encryption, retention, ...)
  templates/             # Django templates (HTMX partials included)
  static/
    css/
      input.css          # Tailwind input file
      styles.css         # Compiled CSS (do not commit)
  tests/
    e2e/                 # Playwright E2E tests
      conftest.py        # Shared fixtures
      test_<feature>.py  # Tests per feature
```

### Key Design Decisions

- **Encryption:** Sensitive fields are encrypted at the application level (`core/services/`). The `ENCRYPTION_KEY` is required in production.
- **Audit log:** All security-relevant actions are written to `AuditLog`.
- **Retention:** The management command `enforce_retention` enforces retention periods.
- **HTMX-first:** Interactive UI elements are preferably implemented via HTMX partials to keep JavaScript minimal.
- **Service layer:** Views delegate logic to services — this makes testing easier and keeps views lean.

<!-- translation-source: CONTRIBUTING.md -->
<!-- translation-version: v0.10.2 -->
<!-- translation-date: 2026-05-01 -->
<!-- source-hash: 18c5c32 -->
