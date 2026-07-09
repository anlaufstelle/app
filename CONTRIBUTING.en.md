> **[Deutsche Version / German version](CONTRIBUTING.md)**

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

- **Python 3.14** (recommended: via [pyenv](https://github.com/pyenv/pyenv))
- **PostgreSQL 18** (or Docker, see below)
- **Node.js 24+** (for Tailwind CSS)
- **Docker** (optional, for the database)

### Step by Step

**1. Clone the repository**

```bash
git clone https://github.com/anlaufstelle/app.git
cd app
```

**2. Set up the Python environment**

```bash
python3.14 -m venv .venv
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

This starts a PostgreSQL 18 container with the following credentials:

| Variable | Value |
|-----------|---------------|
| DB name | anlaufstelle |
| User | anlaufstelle |
| Password | anlaufstelle |
| Port | 5432 |

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

> **Environment guard:** `seed` only runs when the active settings module has `SEED_ALLOWED = True` (dev/test/e2e/devlive). Under `prod` settings the command aborts with a
> `CommandError` — demo logins and `--flush` are forbidden there; initial setup
> runs via `manage.py create_super_admin` (Refs #1040).

**Scale profiles overview:**

| Data | `small` (default) | `medium` | `large` |
|---|---|---|---|
| Facilities | 1 | 2 | 5 |
| Users (total) | 7 (1 super_admin + 6 facility users) | 13 (1 super_admin + 2×6) | 31 (1 super_admin + 5×6) |
| Users / facility | 6 (`admin`/`emma`/`miriam`/`markus`/`lena`/`felix`) | 6 | 6 |
| Clients / facility | 7 | 40 | 500 |
| Events / facility | 25 | 750 | 10,000 |
| Cases | 3 | 12 | 50 |
| Episodes || 20 | 80 |
| Impact goals || 15 | 60 |
| Milestones / goal || 3 | 4 |
| WorkItems | 5 | 25 | 100 |
| Quick Templates / facility | 6 | 6 | 6 |
| DeletionRequests || 5 | 15 |
| RetentionProposals | 4 | 6 | 12 |
| Attachments (approx.) | 1–2 (50 %) | ~15 (25 %) | ~80 (10 %) |
| Time span | 80 days | 365 days | 3 years |

> **Note:** `small` does not include case management (no episodes, goals). Use `medium` when developing case management features.

Seed credentials: password `anlaufstelle2026`, 7 logins (username → role): `superadmin` → `super_admin` (no facility assignment), `admin` → `facility_admin`, `emma` → `lead`, `miriam` → `staff`, `markus` → `staff`, `lena` → `assistant`, `felix` → `assistant`. All except `superadmin` belong to the default facility.

> **Production:** In production there is **no** default password and no default `super_admin`. Initial setup runs via `manage.py create_super_admin` (interactive, no default). Details: `docs/dev/dev-deployment.md` § Production-Bootstrap (dev-only) and [docs/admin-guide.md § 2.1 Erstinstallation](docs/admin-guide.md). Lockout recovery: `manage.py unlock <username>`.

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

| Target | Description |
|------------------|-------------------------------------------------------------------|
| `make db` | Start PostgreSQL 18 container |
| `make db-stop` | Stop and remove PostgreSQL container |
| `make migrate` | Run Django migrations |
| `make run` | Start dev server (gunicorn + HTTPS on `0.0.0.0:8443`) |
| `make run-http` | Fallback: Django runserver without HTTPS (`0.0.0.0:8000`) |
| `make seed` | Load seed data into the database |
| `make tailwind` | Compile Tailwind CSS in watch mode |
| `make tailwind-build` | Compile minified Tailwind CSS for production |
| `make lint` | Check code with Ruff and verify formatting |
| `make typecheck` | mypy on `core/services` (strict) + baseline check (Refs #741) |
| `make test` | Run unit and integration tests (excluding E2E) |
| `make test-e2e` | Run end-to-end tests with Playwright |
| `make check` | Run Django system checks and verify migration consistency |
| `make ci` | Full CI pipeline locally: `lint` + `check` + `test-parallel` |
| `make test-focus T=<path>` | Single test file with fail-fast |
| `make test-parallel` | Unit and integration tests in parallel (pytest-xdist) |
| `make test-e2e-parallel` | E2E tests in parallel (default 2 workers, configurable) |
| `make test-e2e-smoke` | Smoke-tagged E2E tests only (~2-3 min) |
| `make deps-lock` | Regenerate lock files from `requirements*.in` (pip-tools) |
| `make deps-check` | Verify lock files match `.in` (drift detection) |
| `make dev` | Start database, run migrations, and start server (combined) |
| `make clean` | Remove generated artefacts (`__pycache__`, `*.pyc`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `src/staticfiles`) — `src/media/` is left untouched (data loss risk) |

`make ci` should pass locally before every commit.

### Pre-Commit Hooks (optional)

A [`.pre-commit-config.yaml`](.pre-commit-config.yaml) is provided for fast drift detection before committing (Refs #820, #860). It checks `ruff` (lint + format), `makemigrations --check`, `mypy core/services`, the translation version header, and automatic `pip-compile` on `requirements*.in` changes.

```bash
.venv/bin/pip install pre-commit
pre-commit install                       # one-time: commit-stage hooks
pre-commit install --hook-type pre-push  # one-time: pre-push quick-CI (Refs #860)
pre-commit run --all-files               # run all commit-stage hooks against the repo
```

**Two stages:**

- **Commit-stage:** Ruff lint+format, `makemigrations --check`, `mypy`, translation version, `pip-compile` on lock-file drift. Runs in under 5 s.
- **Pre-push:** `make lint && make deps-check && make check` — the solo-maintainer replacement for required status checks. Branch protection with required status checks does not fire on a direct `git push` to `main`; the pre-push hook therefore catches exactly what would otherwise produce red CI after a push (lock drift, format drift, migration drift). Runs in ~10 s. Tests remain in CI.

CI on [`anlaufstelle/app`](https://github.com/anlaufstelle/app/actions) is the definitive source of truth; the pre-push hook only reduces the likelihood of red CI after a push.

### Port Overview and Process Hygiene

| Port | Process | Started by | Purpose |
|------|---------|------------|---------|
| **8443** | gunicorn (HTTPS) | `make run` / `make dev` | Dev server (default) |
| **8844** | gunicorn (HTTP) | `make test-e2e` / E2E conftest | E2E test server worker 0 (temporary) |
| **8845+** | gunicorn (HTTP) | `make test-e2e-parallel` | E2E test server worker 1+ (temporary) |
| **8000** | Django runserver | `make run-http` | Fallback without HTTPS |
| **5432** | PostgreSQL | `make db` (Docker) | Database |

**Collision protection:** `make run` and `make run-http` automatically terminate old processes on their port before starting. The E2E conftest also cleans up port 8844.

**Troubleshooting a slow app:**

```bash
# Find duplicate server processes
ps aux | grep -E 'gunicorn|runserver' | grep -v grep

# Free a specific port
lsof -ti :8443 | xargs kill

# Kill all gunicorn processes
pkill -f gunicorn
```

---

## Coding Conventions

### Python / Django

- **Python 3.14** with full type hints where appropriate (codebase stays 3.13-compatible, see `requires-python`).
- **Django 6.0+** — class-based views preferred, function views only for simple cases.
- Business logic belongs in `core/services/`, not in views or models.
- Models are split up: one model (or closely related models) per file under `core/models/`.
- Role-based access control via mixins from `core/views/mixins.py` — available: `SuperAdminRequiredMixin` (only `/system/`), `FacilityAdminRequiredMixin` (admin of their own facility), `LeadOrAdminRequiredMixin`, `StaffRequiredMixin`, `AssistantOrAboveRequiredMixin`.
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

Details: [docs/ops-runbook.md § 9](docs/ops-runbook.md). RLS only takes effect in production when the Django DB user is **not** a superuser (see `docs/dev/dev-deployment.md` (dev-only), primary path per [ADR-017](docs/adr/017-deployment-topology.md); [docs/coolify-deployment.md](docs/coolify-deployment.md) is an alternative platform guide).

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

#### HTMX & Live Regions (Refs #811)

To ensure HTMX success messages reach screen-reader users:

- There is exactly one **stable live region** in [`base.html`](src/templates/base.html): `#flash-messages` with `role="status" aria-live="polite" aria-atomic="true"`.
- HTMX responses announcing a success either target it via `hx-target="#flash-messages" hx-swap="innerHTML"` or via `hx-swap-oob="innerHTML:#flash-messages"`. Never replace the wrapper `<div>` itself via `outerHTML` — doing so re-instantiates the live region and loses the announcement trigger.
- When a bulk endpoint must force a full reload (e.g. WorkItem bulk, retention bulk), this happens via `HX-Redirect` — the subsequent page renders the Django `messages` framework back into `#flash-messages`.

#### URL Schema: HTMX Fragments vs. JSON APIs (Refs #848)

Endpoints are separated by response type — templates reference both exclusively via `{% url 'name' %}`:

- **HTML fragments (HTMX partials):** Path `/partials/<feature>/<action>/`. Examples: `partials/clients/autocomplete/`, `partials/retention/<uuid:pk>/approve/`. Response is always HTML, rendered with a `partials/` template.
- **JSON APIs:** Path `/api/v1/<feature>/<action>/`. Example: `api/v1/offline/bundle/client/<uuid:pk>/`. Response is JSON, consumed by service workers or JS `fetch()` calls.

New endpoints belong in one of these two path groups. URL names stay short and feature-specific (`client_autocomplete`, `offline_bundle`); path prefixes only change when the response form changes. Direct `fetch("/api/...")`-calls in JS may only target `/api/v1/` — HTMX partials are never consumed as JSON.

### Translations (i18n)

- **Update EN in the same commit (binding, Refs #1215).** Whoever changes translatable strings — Django `{% trans %}`/`.po` under [`src/locale/`](src/locale/) or the mirrored EN docs (`*.en.md`, [`docs/en/`](docs/en/)) — updates the English counterpart in the **same commit**, not in a deferred sync commit. Consistent with the "i18n is its own commit" rule in `CLAUDE.md` (dev-only): DE and EN belong in *one* `chore(i18n):` commit, separate from the feature.
- **Stamp as backstop:** [`scripts/check_translation_versions.py`](scripts/check_translation_versions.py) requires a `translation-version` header == current minor for the EN docs (pre-commit hook + `make release-gates`, hard gate since #1078). The stamp catches drift at release; the "same commit" rule prevents drift from arising.

### Conventional Commits

Commit messages follow the [Conventional Commits](https://www.conventionalcommits.org/) standard:

```
<type>(<scope>): <short description>

[optional body]

Refs #<issue-number>
```

**Types:**

| Type | Usage |
|------------|-------------------------------------------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `test` | Add or update tests |
| `docs` | Documentation |
| `chore` | Maintenance, configuration, dependencies |
| `refactor` | Refactoring without behavior change |
| `style` | Formatting, no logic change |

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

### Test-Driven Development (Unit/Service)

As of 2026-05-20, test-driven development is mandatory for the unit/service layer. The required order **before** any service, form, model, or CBV change:

1. **Red** — write a pytest test in the appropriate file under `src/tests/` that describes the desired behavior but fails today. Run it with `pytest -x` and confirm it fails with the expected `AssertionError`.
2. **Green** — minimal implementation in `src/core/...` until exactly that test passes. No additional features, no premature generalization.
3. **Refactor** — clean up code (and the test if needed) while the suite stays green. Run `pytest -x` after each cleanup step.

Example (service layer, pseudonym hashing from [Issue #844](https://github.com/anlaufstelle/app/issues/844)):

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
# → ModuleNotFoundError / ImportError → expected Red.
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

**Refactor:** for example, extract key resolution into a helper once a second hash use case emerges — the test stays green.

**Scope** (TDD required):

- Service, form, model, helper, CBV/view unit tests — everything under `src/tests/` outside of `src/tests/e2e/`.

**Exceptions** (manual-first / TDD-neutral):

- **E2E tests** in `src/tests/e2e/` remain Playwright-driven as before — click through manually first (see `### End-to-End Tests (Playwright)`), then write tests from observation.
- Django migration generation, squash migrations, tooling/config patches (CI thresholds, allowlists, `pyproject.toml`).
- Pure Markdown/documentation changes.
- One-shot hygiene scripts without reuse.

Cross-references: `CLAUDE.md § Tests & Verifikation` (dev-only) and skill `superpowers:test-driven-development`.

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

### Test Layer Model During Development

Never run the full suite during development — work in layers, always with fail-fast (`pytest -x`):

1. **Focus:** `pytest src/tests/test_<affected_file>.py -x` — only tests in the changed area. E2E focused: `pytest src/tests/e2e/test_<feature>.py -x`.
2. **Group:** Affected test files together: `pytest src/tests/test_a.py src/tests/test_b.py -x`.
3. **Parallel:** `make test-parallel` — full unit suite in parallel, before committing.
4. **Smoke:** `make test-e2e-smoke` — ~40 critical E2E flows (~2-3 min), once a feature is complete.
5. **E2E:** `make test-e2e-parallel` — full E2E suite in parallel, before pushing.

**Fail-fast always:** run tests with `pytest -x`. On failure, fix it, check whether related tests share the same root cause, then re-run the full suite. Re-runs: `pytest --lf -x` (last failed only) or `pytest --ff -x` (failed first).

**Wait strategy (E2E):** `domcontentloaded` or `wait_for_url()` — **never** `networkidle`.

### Full CI Pipeline Locally

```bash
make ci
# equivalent to: lint + check + test-parallel
```

This pipeline must pass locally before every pull request.

### Mutation Testing (mutmut)

Mutation testing checks how many synthetic code mutations the test suite actually detects. Configuration in `pyproject.toml` (`[tool.mutmut]`). Expected runtime: 3–6 h on `core.services` + `core.forms`. Therefore not a PR requirement, but done selectively per wave issue.

```bash
make mutation           # mutmut run via scripts/dev/run_mutmut.py
make mutation-report    # survivors list, non-interactive
```

`make mutation` is resumable (mutmut stores state in `mutants/**/*.py.meta` and automatically picks up where a previous run stopped on restart).

For longer runs on a sandbox with an OOM-killer or idle-killer, `scripts/dev/run_mutmut_watchdog.sh` (dev-only) is available:

```bash
# Default: 3 additional restarts, stall threshold 5 min, 2 mutmut workers
scripts/dev/run_mutmut_watchdog.sh

# More aggressive profile
MAX_RESTARTS=5 STALL_THRESHOLD=600 MUTMUT_MAX_CHILDREN=4 \
    scripts/dev/run_mutmut_watchdog.sh
```

The watchdog detects stalled or dead master processes and restarts `make mutation` — the resume mechanism takes over. Exits 0 as soon as `mutants/mutmut-stats.json` has been written during the watchdog session. Background: Refs #937.

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

### Code of Conduct

All contributors are bound by the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Harassment incidents should be reported confidentially to `kontakt@anlaufstelle.app` (Refs #836).

5. **Review:** At least one approval required. Feedback should be objective and constructive.

6. **Merge:** Squash-merge to `main` once CI is green and approval is given.

---

## Architecture Overview

### Roles

Anlaufstelle has five roles — four facility-bound, one facility-wide:

| Role | DB value | Scope | Description |
|------|----------|-------|-------------|
| System administration | `super_admin` | facility-wide (`/system/`) | Hosting, bootstrap, pre-auth audit logs. Created via `manage.py create_super_admin`. |
| Application management | `facility_admin` | one facility | Full access within their own facility (audit log, GDPR package, user management). |
| Lead | `lead` | one facility | Lead level, extended reports, approve deletion requests. |
| Staff | `staff` | one facility | Core work with clients and events. |
| Assistant | `assistant` | one facility | Restricted access, supporting tasks. |

Architecture decision: [ADR-018](docs/adr/018-rollenmodell-superadmin.md). Details on the RLS bypass for `super_admin`: [ADR-005 Update 2026-05-10](docs/adr/005-facility-scoping-and-rls.md).

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
      episode.py         # Episode
      outcome.py         # OutcomeGoal, Milestone
      audit.py           # AuditLog
      settings.py        # Settings
    views/               # Class-based views, split by feature area
      aktivitaetslog.py  # AktivitaetslogView (home page)
      timeline.py        # TimelineView (event timeline)
      clients.py         # Client CRUD
      events.py          # Event CRUD + deletion workflow
      workitems.py       # WorkItem CRUD
      cases.py           # Case, Episode, Goal, Milestone
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
<!-- translation-version: v0.16.0 -->
<!-- translation-date: 2026-06-12 -->
<!-- source-hash: da1fa91 -->
