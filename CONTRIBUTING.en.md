> **[Deutsche Version / German version](CONTRIBUTING.md)**

# Contributing to Anlaufstelle

[![Lint](https://github.com/anlaufstelle/app/actions/workflows/lint.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/lint.yml)
[![Test](https://github.com/anlaufstelle/app/actions/workflows/test.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/test.yml)
[![E2E](https://github.com/anlaufstelle/app/actions/workflows/e2e.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/e2e.yml)

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![Django 6.1](https://img.shields.io/badge/django-6.1-green.svg)](https://www.djangoproject.com/)
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

1. [5-Minute Quick Start (Docker)](#5-minute-quick-start-docker)
2. [Development Environment Setup](#development-environment-setup)
3. [Make Targets](#make-targets)
4. [Coding Conventions](#coding-conventions)
5. [Plugin Development](#plugin-development)
6. [Tests](#tests)
7. [Pull Request Process](#pull-request-process)
8. [Licensing of Contributions](#licensing-of-contributions)
9. [Architecture Overview](#architecture-overview)

---

## 5-Minute Quick Start (Docker)

The fastest way to a running Anlaufstelle with sample data — for a first look, for reproducing
a bug, and as the entry point before your first pull request. The only prerequisite is
**Docker with Docker Compose**; you do not need Python, Node or PostgreSQL yet.

```bash
git clone https://github.com/anlaufstelle/app.git
cd app
docker compose up      # without Docker group permissions: sudo docker compose up
```

The local compose file brings up the database and the application, applies the migrations, and
loads the seed data on the **first** start (into an empty database only — a second start does
not re-seed). The one-off image build takes about a minute; after that the login page is
available within roughly 20 seconds at
**[http://localhost:8000/login/](http://localhost:8000/login/)**.

> **No preparation needed.** You neither create a `.env` nor export an `ENCRYPTION_KEY` — the
> local setup generates its own key on the first start and keeps it in a dedicated volume so
> that encrypted development data stays readable across restarts. Background and the boundary
> to the production path: [ADR-049](docs/adr/049-one-command-local-bootstrap.md) (German).
>
> **Local access only:** the web service deliberately binds to `127.0.0.1`, so there is no LAN
> access. That is intentional — the seed accounts use a publicly documented password.

**Signing in.** All seed accounts share the password `anlaufstelle2026`:

| Login | Role | What for |
|---|---|---|
| `admin` | Application administration (`facility_admin`) | Form configuration, user access, settings, data import |
| `emma` | Management (`lead`) | Statistics, reports, GDPR workflows |
| `miriam` | Practitioner (`staff`) | Clients, cases, day-to-day documentation |
| `superadmin` | System administration (`super_admin`) | `/system/`, installation-wide view |

The full list of the seven logins and the data volumes per profile is under
[Loading seed data](#development-environment-setup). The seed is fail-closed behind
`SEED_ALLOWED` and only runs under local/demo settings; the production compose file **never**
seeds and does not know the variable — initial setup there runs via
`manage.py create_super_admin`.

**Cleaning up:** `docker compose down` stops the stack, `docker compose down -v` also discards
the database (the next start then seeds afresh). Logs: `docker compose logs -f web`.

**Next.** For real development — tests, linting, Tailwind watch mode, a debugger — you need the
local toolchain from the next section. How the system is put together:
[docs/architecture.md](docs/architecture.md) (German).

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
> [docs/ops-runbook.md § 8](docs/en/ops-runbook.md#8-updating-dependencies).

**3. Start the database**

With Docker (recommended):

```bash
make db
```

This starts a PostgreSQL 18 container with the following credentials:

| Variable  | Value         |
|-----------|---------------|
| DB name   | anlaufstelle  |
| User      | anlaufstelle  |
| Password  | anlaufstelle  |
| Port      | 5432          |

The port is bound to `127.0.0.1:5432` only. To allow LAN access, configure that deliberately through your own Docker or firewall setup.

Alternatively, a locally installed PostgreSQL instance can be used. The connection is then set through the variables `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` and `POSTGRES_PORT` (defaults: `anlaufstelle`, `anlaufstelle`, empty, `localhost`, `5432`; under the development settings an empty password falls back to `anlaufstelle`). The application does not read a connection URL (`DATABASE_URL`).

**4. Configure environment variables**

Create a `.env` file in the project directory (or export the variables). With the Docker container from step 3 the two keys are enough; you only need the database variables for a different instance:

```bash
DJANGO_SECRET_KEY=dev-secret-key-bitte-aendern
ENCRYPTION_KEY=<Fernet key, see below>

# Only for your own PostgreSQL instance (example with different values):
POSTGRES_HOST=db.example.org
POSTGRES_PORT=5433
POSTGRES_DB=my_db
POSTGRES_USER=my_user
POSTGRES_PASSWORD=my-password
```

`DEBUG` is not an environment variable: the settings profile decides. `manage.py` starts with `anlaufstelle.settings.dev` (`DEBUG = True`); the production settings set `DEBUG = False`. Without `DJANGO_SECRET_KEY` the development settings use an insecure dev value, the production settings require it. Without `ENCRYPTION_KEY` the development settings run in plaintext mode with a loud warning (sensitive fields are stored unencrypted).

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
python src/manage.py seed --scale solo     # one facility, 500 events over 1000 days
python src/manage.py seed --flush          # flush existing data first
```

> **Environment guard:** `seed` only runs when the active settings module has `SEED_ALLOWED = True` (dev/test/e2e/devlive). Under `prod` settings the command aborts with a
> `CommandError` — demo logins and `--flush` are forbidden there; initial setup
> runs via `manage.py create_super_admin` (Refs #1040).

**Document types come from a package:** The catalogue (9 document types, 25 field
templates, field groups and assignments) lives declaratively in
[`src/core/packages/default.yaml`](src/core/packages/default.yaml) and is applied by
the seed through the package loader (`core.packages`) — the same path as
`manage.py load_package --file … --facility …`. Applying it is **idempotent**:
objects are looked up by `slug` (not by name), properties are re-synced on every
run (previously `get_or_create` — defaults only took effect on creation), and a
second run reports everything as unchanged.

The standard catalogue offers `Contact`, `Needle Exchange`, `Referral`, and `Note` in the tally with `tally_enabled: true`. For Needle Exchange, `+1` opens the compact issued/returned-numbers form because both fields are required; both quantities use `min_value: 0` in the catalogue, so no write path accepts negative values. The other three types have no required values and are counted directly.

> **Changing the catalogue:** edit the values in `default.yaml`, not in
> `core/seed/doc_types.py`. Existing `slug` values are **immutable** (migration
> 0117/#1710 plus a DB trigger) — a changed slug creates a duplicate in every
> existing facility instead of updating it. Always carry `system_type` where one is
> set (only `ban`/`crisis` remain), and always
> carry the `options` key for `select`/`multi_select`; **never** add a `conditions`
> key (not even `[]` — that would delete field conditions maintained through the
> UI). Never remove the **last** group of a document type from the catalogue: the
> seed only cleans up orphaned groups for types for which the package still
> declares at least one group — first take the fields out of the group (drop
> `group_slug`), but leave the empty group in place.

#### What the loader-based seed does to an existing facility

The seed is not a plain "create if missing" but a re-sync against
`default.yaml`. What that means in operation:

| Affected | Effect on the next seed run |
|---|---|
| `name`, `sensitivity`, `min_contact_stage` of a catalogue type | **reset** to the catalogue value |
| `icon`, `color`, `sort_order` of a catalogue type | **reset** to the catalogue value — but only if the package names the key; if absent, the facility's value stays |
| `system_type` of a catalogue type | set to the package value; a type without a system type may receive one, a set system type can neither change nor be removed (conflict, see below) |
| `report_key` of a catalogue type | applied only if the package names the key; if absent, the existing value is left untouched. Empty → set is allowed, set → different is a conflict. |
| `entity_type` of a catalogue type | immutable: if the existing type differs, the run aborts (conflict, see below) |
| `sort_order` of groups **and** field assignments — including types without groups | reset to the catalogue position; a field list reordered in the UI does not survive |
| `default_value` of a catalogue field template | **reset** to the package value (empty throughout the catalogue) — a default maintained through the config UI does **not** survive the seed. Package format v1 cannot express "do not touch" for this field (unlike `conditions`). |
| `min_value`, `max_value` of a catalogue numeric field template | reset to the optional package values. The migration only adds configuration columns; existing `data_json` values in events, clients and cases remain unchanged. Before tightening a range, run the read-only inventory `python src/manage.py inventory_number_ranges --facility <UUID-or-name>` with the admin DB role. |
| `tally_enabled` of a catalogue type | An explicit `true`/`false` is reset to the package value. If the key is absent, a tally opt-in maintained in the admin UI remains on an existing type; a new type defaults to `false`. |
| Fields a facility has **additionally** assigned to a catalogue type | left untouched (the loader only touches what the package declares) |
| `retention_days`, `description`, `is_active` (type) and `is_active` (template) | not part of schema v1 — neither set nor reset |

The `category` key on a type is no longer applied: the field no longer exists since
[ADR-040](docs/adr/040-documenttype-category-entfernt.md); the loader ignores the key and
reports a warning in the load report. The authoritative code is `_apply_document_type` in
[`src/core/packages/loader.py`](src/core/packages/loader.py); the package keys are described in
[docs/packages.md](docs/packages.md).

Three conflicts abort the **entire** seed run (fail-loud, all-or-nothing — nothing is
applied, not even for the remaining types):

* **A facility-owned field template on a catalogue slug.** If a facility created
  e.g. `dauer` itself as a text field, that collides with `field_type`/`is_encrypted`
  from the package — both are immutable after creation. Resolution: move the
  facility's own template to a different slug (create anew + migrate data) or change
  the catalogue slug.
* **A differing immutable value.** `entity_type`, a set `system_type` and a set `report_key`
  of an existing type cannot be changed; if the package differs (or omits the `system_type`),
  the loader reports a conflict.
* **A name collision after a rename.** If "Kontakt" was renamed to "Erstkontakt"
  through the UI and a *new* type "Kontakt" was created afterwards (which gets the
  slug `kontakt-2`), the seed wants to rename the type with slug `kontakt` back to
  "Kontakt" — violating `UniqueConstraint(facility, name)`. From then on
  **every** further seed run fails until this is cleaned up manually. Resolution:
  rename the facility's own type or adjust the name in the catalogue.

#### Scale profiles overview

The demo accounts advertised in the demo panel have no email address because the shared demo does not send mail; test password reset on dev with the mail console or Mailpit.

| Data | `small` (default) | `medium` | `large` | `solo` | `demo` |
|---|---|---|---|---|---|
| Facilities | 1 | 2 | 5 | 1 | 6 (scenario catalogue, Refs #2279) |
| Packages per facility (Refs #2148) | Main facility: `default` | + Zweigstelle Nord: `beratungsstelle` | + Zweigstelle Süd: `awareness`, Außenstelle Ost: `jugendarbeit`, Außenstelle West: `frauenhaus` | Main facility: `default` | `core.seed.scenarios.SCENARIOS`: Kontaktladen Wärmestube `default`, Beratungsstelle Falkenweg `beratungsstelle`, Beratungspraxis Lindenhof (solo) `beratungsstelle`, Awareness-Team Nachtfalter `awareness`, Jugendtreff Wilhelmshöhe `jugendarbeit`, Frauenhaus Amalie `frauenhaus` |
| Users (total) | 7 (1 super_admin + 6 facility users) | 13 (1 super_admin + 2×6) | 31 (1 super_admin + 5×6) | 7 (1 super_admin + 6 facility users) | 33 (1 super_admin + 5×6 team + 2 solo) |
| Users / facility | 6 (`admin`/`emma`/`miriam`/`markus`/`lena`/`felix`) | 6 | 6 | 6 | 6 (solo facility: 2 — case worker + backup) |
| Clients / facility | 7 | 40 | 500 | 30 | 18 (solo: 9) |
| Events / facility | 25 | 750 | 10,000 | 500 | 120 (solo: 60) |
| Cases | 3 | 12 | 50 | 10 | 6 (solo: 4) |
| Episodes | — | 20 | 80 | 15 | 8 (solo: 4) |
| Impact goals | — | 15 | 60 | 10 | 6 (solo: 3) |
| Milestones / goal | — | 3 | 4 | 3 | 3 (solo: 2) |
| WorkItems | 5 | 25 | 100 | 20 | 12 (solo: 8) |
| Quick Templates / facility | 6 | 6 | 6 | 6 | 6 |
| DeletionRequests | — | 5 | 15 | 4 | 2 (solo: 1) |
| RetentionProposals | 4 | 6 | 12 | 6 | 3 (solo: 2) |
| Attachments (approx.) | 1–2 (50 %) | ~15 (25 %) | ~80 (10 %) | share 25 % (counseling events) | ~5 (30 %) (solo: ~3) |
| Time span | 80 days | 365 days | 3 years | 1000 days | 270 days |
| Time filters per facility | Main facility 3 | Main facility 3, Nord 3 | Main facility 3, Nord 3, Süd 2, Ost 2, West 3 | Main facility 3 | Kontaktladen 3, Falkenweg 3, Lindenhof 0, Nachtfalter 2, Wilhelmshöhe 2, Amalie 3 |

> **Note:** `small` does not include case management (no episodes, goals). Use `medium` when developing case management features.
>
> **`demo`** (Refs #2279) provides all six demo scenarios (including solo operation) at once, independent of the data volume used by the other profiles — the sole source is `core.seed.scenarios.SCENARIOS`. Used by `dev-ops/deploy/demo-reseed.sh` (hourly reset on `demo.anlaufstelle.app`), but also usable locally (`python src/manage.py seed --scale demo`).

Seed credentials: password `anlaufstelle2026`, 7 logins (username → role): `superadmin` → `super_admin` (no facility assignment), `admin` → `facility_admin`, `emma` → `lead`, `miriam` → `staff`, `markus` → `staff`, `lena` → `assistant`, `felix` → `assistant`. All except `superadmin` belong to the default facility.

> **Production:** In production there is **no** default password and no default `super_admin`. Initial setup runs via `manage.py create_super_admin` (interactive, no default). Details: `docs/dev/dev-deployment.md` § Production-Bootstrap (dev-only) and [docs/admin-guide.md § 2.1 Erstinstallation](docs/en/admin-guide.md). Lockout recovery: `manage.py unlock <username>`.

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

**Shared host lock:** The locked targets require `flock` (util-linux), which is
not installed by default on macOS. `CI_LOCK` defaults to
`XDG_RUNTIME_DIR/anlaufstelle-ci.lock`, then `TMPDIR/anlaufstelle-ci.lock`, then
`/tmp/anlaufstelle-ci.lock`. The directory must exist. All sessions sharing the
E2E database or server ports must resolve the same absolute path; set `CI_LOCK`
consistently when runtime directories differ. Make exports `CI_LOCK` and
`CI_LOCK_TIMEOUT` (default 14400 seconds) to subprocesses, including mutation
cleanup; direct mutation-runner calls use the same fallback chain.

| Target           | Description                                                       |
|------------------|-------------------------------------------------------------------|
| `make db`        | Start PostgreSQL 18 container                                     |
| `make db-stop`   | Stop and remove PostgreSQL container                              |
| `make migrate`   | Run Django migrations                                             |
| `make run`       | Start dev server (gunicorn + HTTPS on `0.0.0.0:8443`)           |
| `make run-http`  | Fallback: Django runserver without HTTPS (`0.0.0.0:8000`)        |
| `make seed`      | Load seed data into the database                                  |
| `make tailwind`  | Compile Tailwind CSS in watch mode                                |
| `make tailwind-build` | Compile minified Tailwind CSS for production                 |
| `make lint`      | Check code with Ruff and verify formatting                        |
| `make typecheck` | mypy on `core/services` (strict) + baseline check (Refs #741) |
| `make test`      | Run unit and integration tests (excluding E2E)                    |
| `make test-unit-slice INDEX=1` | One of three unit slices weighted by measured file duration, with two workers and coverage without a per-slice threshold; the same split as `test.yml`. Produces `.coverage.1` and `unit-durations-1.txt`. |
| `make test-unit-combine TEST_RESULT=success` | Validate and combine all three files under `unit-coverage/unit-coverage-N/.coverage.N`; enforce 96% coverage on the union. `TEST_RESULT` must reflect the actual aggregate status of the three runs. |
| `make test-unit-weights DURATIONS="unit-durations-1.txt unit-durations-2.txt unit-durations-3.txt"` | Regenerate `scripts/ci/test_weights.json` from complete `--durations=0 --durations-min=0` logs. `python scripts/ci/test_slices.py --summary` shows files, tests and weight totals. |
| `make test-js`   | Run the Vitest suite (JS, `npm test`) deterministically/non-watch — part of `make ci` (Refs #1932) |
| `make test-e2e`  | Run end-to-end tests with Playwright                              |
| `make test-e2e-halves` | E2E in two serial halves (RAM-limited hosts, Refs #1787); the split comes from `scripts/e2e_slice.py` — the same key the CI matrix uses (Refs #1976) |
| `make test-e2e-slice-locked SLICES=8 INDEX=3` | Re-run a single slice of the CI split locally, under the CI lock (a single slice also occupies port 8844 and `anlaufstelle_e2e`; unlocked as `make test-e2e-slice`, only when no other E2E run is active) — the number comes from the job name (`E2E-Scheibe 3`); `python scripts/e2e_slice.py --slices 4 --summary` shows the distribution (Refs #1976) |
| `make check`     | Run Django system checks and verify migration consistency         |
| `make i18n-check` | Full `.po` reconciliation against the source tree (new/removed/semantically changed msgids) — part of `make ci` (Refs #1933) |
| `make ci`        | Full CI pipeline locally: `lint` + `check` + guards (deps/matrix/release-test/vendor-js/agent-docs) + `audit-js` + `i18n-check` + `typecheck` + `test-js` + `test-parallel` |
| `make test-focus T=<path>` | Single test file with fail-fast                          |
| `make test-parallel` | Unit and integration tests in parallel (pytest-xdist)         |
| `make test-e2e-parallel` | E2E tests in parallel (default 2 workers, configurable)   |
| `make test-e2e-smoke` | Smoke-tagged E2E tests only (~2-3 min)                       |
| `make deps-lock` | Regenerate lock files from `requirements*.in` (pip-tools)         |
| `make deps-check` | Verify lock files match `.in` (drift detection)                  |
| `make audit-js`  | `npm audit` against `package-lock.json` — blocking JS advisory gate from severity `AUDIT_LEVEL` (default `moderate`) upwards, part of `make ci`; needs access to the npm registry (Refs #1968) |
| `make dev`       | Start database, run migrations, and start server (combined)       |
| `make clean`     | Remove generated artefacts (`__pycache__`, `*.pyc`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `src/staticfiles`) — `src/media/` is left untouched (data loss risk) |

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

> **Activate the venv before EVERY push** (lesson from the v0.89.0 handoff): the `pre-push` hook invokes `pre-commit`. Without an activated venv **every** push aborts with `pre-commit not found. Did you forget to activate your virtualenv?`. On a **tag** push this is particularly treacherous — git then only reports `failed to push some refs`, the tag stays behind locally without a word, and it may not surface until several steps later at the tag-push gate (`release-verify-tags.sh`). So run `. .venv/bin/activate` (or a one-off `PATH="$PWD/.venv/bin:$PATH" git push …`) — **not** `--no-verify`, which bypasses the quick CI.

Where the GitHub Actions workflows run, their result is the definitive source of truth: contributors get them on their pull request in the public repository. Whether the workflows run in the internal development branch is a maintainer setting and not fixed in the repository; there `make ci` is the binding gate ([ADR-029](docs/adr/029-local-only-ci.md)). The pre-push hook only reduces the likelihood of red CI after the push.

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
```

A server on port 8844 belongs to its E2E run. Stop your own run in its terminal,
or wait for the other run to finish; do not terminate all Gunicorn processes.

---

## Coding Conventions

### Python / Django

- **Python 3.14** with full type hints where appropriate (codebase stays 3.13-compatible, see `requires-python`).
- **Django 6.1+** — class-based views preferred, function views only for simple cases.
- Business logic belongs in `core/services/`, not in views or models.
- Models are split up: one model (or closely related models) per file under `core/models/`.
- Role-based access control via mixins from `core/views/mixins.py` — available: `SuperAdminRequiredMixin` (only `/system/`), `FacilityAdminRequiredMixin` (admin of their own facility), `LeadOrAdminRequiredMixin`, `StaffRequiredMixin`, `AssistantOrAboveRequiredMixin`.
- User-facing German text follows the binding [language guidelines](docs/sprachleitlinie.md) (German).
- Do not introduce new dependencies without prior discussion.

### Facility Scoping & Row Level Security

Every new facility-scoped model must be protected on **both** defense lines:

1. **Django layer (first line):**
   - `facility = models.ForeignKey(Facility, ...)` on the model
   - `objects = FacilityScopedManager()` (from [`src/core/models/managers.py`](src/core/models/managers.py))
   - Views/services filter via `.for_facility(request.current_facility)`
2. **PostgreSQL RLS (second line, defense in depth):**
   - New migration following the pattern of [`src/core/migrations/0047_postgres_rls_setup.py`](src/core/migrations/0047_postgres_rls_setup.py): add the table to `DIRECT_TABLES` (or `JOIN_TABLES` if no direct `facility_id` column exists). The migration sets `ENABLE + FORCE ROW LEVEL SECURITY` plus a `facility_isolation` policy.
   - `EXPECTED_TABLES` in [`src/tests/test_rls.py`](src/tests/test_rls.py) is **derivation-based** (#1096): tables with a direct `facility` FK (DIRECT) are derived automatically from the model registry by the test suite — a new such model appears in `EXPECTED_TABLES` without any manual step, and the RLS coverage guard (`TestRLSCoverageGuard`) fails until the RLS migration **and** the PII classification exist. Only **JOIN-scoped** tables without a direct FK need a manual entry in the `JOIN_SCOPED_TABLES` constant; the auth-boundary exception (`core_user`) lives in `NOT_RLS_SCOPED`.
   - The architecture guard `TestRLSJoinScopeArchitectureGuard` (#2001) closes the blind spot of the derivation: if a model without its own `facility` FK references a DIRECT table via FK/O2O, its table must be listed in `JOIN_SCOPED_TABLES`. Otherwise, `pytest -m architecture` fails with a reference to migration pattern `0047_postgres_rls_setup.py` — even if neither a migration nor the list knows about the new table yet. Derivation is deliberately limited to the immediate edge to a DIRECT model; for deeper chains (JOIN → JOIN → DIRECT), the manual entry based on the complete facility chain remains mandatory.
   - The RLS-free tables (`core_user`, `core_facility`, `core_organization`) lack this second backstop by definition. The architecture guard `TestTenantModelScopeGuard` in [`src/tests/test_architecture_guards_models.py`](src/tests/test_architecture_guards_models.py) (#1658) covers them instead: a `User.objects`/`Facility.objects`/`Organization.objects` access in `core/views/`, `core/forms/` or `core/services/` without a facility scope fails CI. Deliberately global accesses (pre-auth lookups, super-admin metrics) belong in the allowlist of the respective scan section, with a reason; the rationale for the guard-vs.-manager decision is in [ADR-005](docs/adr/005-facility-scoping-and-rls.md).

Details: [docs/ops-runbook.md § 9](docs/en/ops-runbook.md). RLS only takes effect in production when the Django DB user is **not** a superuser (see `docs/dev/dev-deployment.md` (dev-only), primary path per [ADR-017](docs/adr/017-deployment-topology.md)).

#### Data migrations on RLS tables (Refs #1746)

Every `RunPython` data migration that reads from or writes to an RLS table calls the guard `assert_migration_role_bypasses_rls(schema_editor)` from [`src/core/utils/migration_guards.py`](src/core/utils/migration_guards.py) as its **first line**:

```python
from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def backfill(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    Client = apps.get_model("core", "Client")
    ...


class Migration(migrations.Migration):
    dependencies = [("core", "0122_backfill_backend_data")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
```

**Why:** per [ADR-020](docs/adr/020-three-role-postgres-model.md) migrations run as the admin role (`POSTGRES_ADMIN_USER`, `NOSUPERUSER BYPASSRLS`), where the `facility_isolation` policy does not apply. If a misconfiguration makes a migration run under the NOBYPASSRLS application role instead, it hits **0 rows silently** without `app.current_facility_id` set: the migration counts as successful, the backfill never happened. The guard turns that into a hard abort (`MigrationRoleError`) including the role name and a fix hint. No `SET ROLE`/GUC juggling inside the migration — the session GUC `app.is_super_admin` is **not** an accepted substitute (a deliberate distinction from `has_rls_bypass_context`, which does allow it for maintenance/cron paths).

The guard complements the deployment gate `current_connection_bypasses_rls` in [`docker-migrate.sh`](docker-migrate.sh) (#1375): that one checks the migrate **job** once up front and only inside the container deployment, this one sits **inside** the migration and protects every invocation path (manual `manage.py migrate`, restore drill, self-hosting without the script). The guard is present in **every** `RunPython` function that writes to an RLS table — including the existing migrations (#2114). Which functions those are is derived at runtime from `pg_class.relrowsecurity` by the architecture test [`src/tests/test_migration_rls_guard_coverage.py`](src/tests/test_migration_rls_guard_coverage.py), which enforces this for every new data migration; exceptions require a rationale in the test's exception list. Tests: [`src/tests/test_migration_rls_guard.py`](src/tests/test_migration_rls_guard.py) (effect), `test_migration_rls_guard_coverage.py` (coverage).

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
- New object pages follow the context-header and back-link convention (German, development documentation).

#### Updating Vendored JS Libraries (Refs #1076)

There is **no frontend bundler** — four libraries are checked in (vendored) as prebuilt `*.min.js` under [`src/static/js/`](src/static/js/) and loaded via `{% static %}`. So that they still stay on the Dependabot/CVE radar, they are tracked as **exactly pinned** `devDependencies` in [`package.json`](package.json):

| Library | npm package | vendored file | dist source in `node_modules/` |
|---|---|---|---|
| htmx | `htmx.org` | `htmx.min.js` | `dist/htmx.min.js` |
| Alpine.js (CSP build) | `@alpinejs/csp` | `alpine-csp.min.js` | `dist/cdn.min.js` |
| Dexie | `dexie` | `dexie.min.js` | `dist/dexie.min.js` |
| Chart.js | `chart.js` | `chart.min.js` | `dist/chart.umd.js` *(already minified)* |

> Note: Alpine's CSP build is the **separate** package `@alpinejs/csp`, not `alpinejs`. Chart.js does not ship its own `*.min.js` in the npm `dist`; the UMD build `chart.umd.js` is already minified.

**Update procedure** (e.g. after a Dependabot bump that only changes `package.json`/`package-lock.json`):

```bash
npm ci                                # bring node_modules/ up to package-lock state
make sync-vendor-js                   # copy dist builds into src/static/js/
git add package.json package-lock.json src/static/js
```

**Verification:** `make ci` runs the drift guard `make verify-vendor-js-sync` ([`scripts/verify_vendor_js_sync.py`](scripts/verify_vendor_js_sync.py)). It compares the version pinned in `package.json` against the version string in the checked-in `*.min.js` and fails on drift — so re-vendoring cannot be forgotten. The guard is a pure string comparison (no node/npm required). Additionally E2E smoke (`make test-e2e-smoke`), since the offline flows depend on Dexie.

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

- **Update EN in the same commit (binding, Refs #1215).** Whoever changes translatable strings — Django `{% trans %}`/`.po` under [`src/locale/`](src/locale/) or the mirrored EN docs (`*.en.md`, [`docs/en/`](docs/en/)) — updates the English counterpart in the **same commit**, not in a deferred sync commit. Consistent with the "i18n is its own commit" rule in `AGENTS.md` § Git & Commits (dev-only): DE and EN belong in *one* `chore(i18n):` commit, separate from the feature.
- **Stamp as backstop:** [`scripts/check_translation_versions.py`](scripts/check_translation_versions.py) requires a `translation-version` header == current minor for the EN docs (pre-commit hook and release check, hard gate, #1078). The stamp catches drift at release; the "same commit" rule prevents drift from arising.
- **Stamp convention (Refs #1552):** Every EN document carries the markers `translation-source` (path of the DE source), `translation-version`, `translation-date`, and `source-hash` at the end of the file. When you sync EN, set `source-hash` to `git hash-object <DE source>` (the blob hash of the corresponding German file) and update `translation-date` to the sync date. The guard runs `git hash-object` on the source itself and **fails hard** as soon as the stamped `source-hash` diverges (DE source changed since the last EN sync) — no warn mode. Short hashes (≥ 7 hex chars) are accepted as a prefix; a missing `source-hash` marker counts as an error.

#### Maintenance rhythm of the `.po` catalogs (Refs #1745)

Both catalogs — [`src/locale/de/LC_MESSAGES/django.po`](src/locale/de/LC_MESSAGES/django.po) and [`src/locale/en/LC_MESSAGES/django.po`](src/locale/en/LC_MESSAGES/django.po) — are maintained **together**, in one `chore(i18n):` commit:

- **When to regenerate:** `python src/manage.py makemessages -l de -l en --no-obsolete`, run from the **repository root** (that is where the catalog expects its `src/…` reference paths; `LOCALE_PATHS` points at `src/locale/`). At the latest at the end of a milestone or batch run, and before every release cut — not once the backlog has grown to months. Individual new strings may still be added as a catalog entry by hand; that does not replace the run, but it prevents a German fallback in the meantime.
- **Small changes:** For one new translatable string, the matching block may be added manually in **both** catalogs. Carry over the `msgid`, current `#:` reference(s), `msgstr` (identical in DE, translated in EN), and any `msgctxt`, plural, or format flags. Then run `msgfmt --check-format` and `python src/manage.py compilemessages`. This is the fast maintenance path, not a substitute for the periodic completeness check.
- **Full sync without reorder churn:** `python scripts/i18n_catalog.py check` (= `make i18n-check`) extracts DE and EN in a temporary repository copy and reports new or removed `msgid`s without changing files. Semantic drift exits with status 1; reference- and position-only changes are informational and green. First translate new or changed entries in **both** catalogs, or add the DE identity translation, then run `python scripts/i18n_catalog.py update`. `update` performs the same full sync, retains the order of every unchanged PO block, and validates both PO/MO pairs fully before writing; a write failure restores files already replaced. The report separates new (`+`), removed (`-`), and semantically changed (`~`) entries from reference noise; new entries are appended at the end of the catalog. **Mandatory gate (#1933):** `make i18n-check` is a fail-fast part of `make ci`/`make ci-locked` and specifically catches the loss of a `msgid` that is only referenced at runtime (e.g. a form validation message) — a case the other catalog gates (fuzzy/empty, version stamp, `.mo` freshness) don't cover, because the entry no longer exists there at all rather than merely being filled in incorrectly.
- **No translation call INSIDE an f-string expression.** `f"{gettext('Bericht')}"` is only extracted by newer `xgettext` versions (measured: 0.23.1 yes, 0.21 no); the CI toolchain (`ubuntu-24.04`, `gettext 0.21`) does not see the call and counts the f-string as a single string literal. The catalog reconciliation then judges differently per workstation — and worse: `make i18n-update` would silently drop that `msgid`, English translation included, from the catalog. So: hoist the call **before** the f-string and interpolate the variable (`report_label = gettext("Bericht")` … `f"{report_label}"`). Guard: [`src/tests/test_i18n_fstring_extraction_guard.py`](src/tests/test_i18n_fstring_extraction_guard.py) (Refs #1933).
- **DE is the source language ([ADR-027](docs/adr/027-i18n-strategy.md)).** Every entry is an identity translation (`msgstr == msgid`), so resolving a run is mechanical: `msgattrib --clear-fuzzy --empty --clear-previous <po> | msgen`. A German `msgstr` that differs from its `msgid` is a bug (usually a msgmerge fuzzy mismatch) — exactly what the duplicate guard in [`scripts/check_translations.py`](scripts/check_translations.py) targets (Refs #1348).
- **EN gets translated, not left fuzzy.** `msgfmt` discards fuzzy entries when compiling — the English interface falls back to German there. [`src/tests/test_i18n_catalog.py`](src/tests/test_i18n_catalog.py) forbids both `#, fuzzy` and empty `msgstr`. Take terminology from [`docs/en/glossary.md`](docs/en/glossary.md) and from what the catalog already uses.
- **Commit the compiled files:** run `python src/manage.py compilemessages` after every run; the `.mo` files live in the repository (deployment does not build them).
- **Baselines only go down.** The thresholds in `scripts/check_translations.py` (#1745) are 0 fuzzy / 0 untranslated for **both** languages. Raising them hides the next drift; the way past the threshold is translating, not re-tuning.

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

## Plugin Development

Two things can be extended **without** touching the core: **report backends** (code, one
module) and **configuration packages** (data, one YAML file). Neither needs a migration, a new
URL, a view, or an entry in the AuthZ matrix. Everything else — models, views, middleware — is
not a plugin system but ordinary core development following the rules above.

### Report backends (reporting plugins)

A report backend encapsulates the domain logic of **one** reporting standard behind a fixed
interface. The panel preview, the CSV/JSON/XML export, and snapshot storage only speak to that
interface and know nothing about the individual report. Practical consequence: **a new report
is a file.**

A module under [`src/core/reporting/backends/`](src/core/reporting/backends/) with a
`DatasetBackend` subclass and the `@register_backend` decorator automatically appears as a card
on the "Datensatz-Berichte" (dataset reports) tab and in all three export formats:

```python
# src/core/reporting/backends/my_report.py
from django.utils.translation import gettext_lazy as _

from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend


@register_backend("my-report")  # slug: ^[a-z0-9]+([-_][a-z0-9]+)*$, effectively immutable
class MyBackend(DatasetBackend):
    name = _("My report")  # display name — always gettext_lazy
    description = _("What this report counts, in one sentence.")
    snapshot_enabled = False  # True = raw aggregate is persisted monthly

    def get_field_specs(self) -> list[FieldSpec]:
        return [FieldSpec("label", _("Attribute")), FieldSpec("count", _("Count"))]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult: ...  # returns RAW numbers

    def prepare_export(
        self, facility, result: DatasetResult
    ) -> DatasetResult: ...  # k-anonymity, before anything leaves the house
```

Five points that experience shows tend to go wrong:

1. **`build_dataset()` is raw, `prepare_export()` is the safeguard.** Everything that ships —
   download **and** panel preview — passes through `prepare_export()` first. Anyone producing
   numbers that circulate externally must implement this hook, and must use the central
   suppression machinery instead of rebuilding it. The snapshot path deliberately does **not**
   call it (internal raw store) — which makes `snapshot_enabled` a data-protection contract,
   not a feature flag.
2. **Labels are `gettext_lazy`**, never German plain text — they end up as CSV headers and JSON
   labels in the English interface too.
3. **Registration happens at import time, exclusively via the decorator.** There is no
   configuration list. Discovery is lazy (only on the first `get_backends()`) so the import
   chain stays acyclic; a broken module raises a loud `ImproperlyConfigured` rather than
   leaving a silently incomplete registry.
4. **A slug is effectively immutable** — it appears in query parameters, file names, and with
   `snapshot_enabled` in the keys of `StatisticsSnapshot.backend_data`.
5. **Mapping runs through `report_key`**, never through a category
   ([ADR-042](docs/adr/042-report-key-berichtszuordnung.md)). There is exactly **one** documented
   exception: `ban-overview` deliberately binds through `system_type`
   (`TypBindung(bindet="system_type")`) because it is a highly sensitive behavioural report —
   bans must not leak into unrelated analyses through a freely selectable report key. If you
   copy that particular module as your template, switch `bindet` back to `report_key`; otherwise
   the registry lookup ("appears in …") and the key vocabulary silently ignore `system_type`
   bindings.

**Copy a template, do not start from scratch.** [`src/core/reporting/backends/`](src/core/reporting/backends/)
ships nine backends — all of them **examples**, not binding reporting standards, and each with
a different shape: `example` (minimal case), `sample-report-1` (full case with snapshot),
`monthly-trend` (time series), `client-metrics` (exact partition), `case-metrics` (independent
scalars), `ban-overview` (highly sensitive special case), `needle-exchange-balance` (sums over
field values), `counseling-hours` (row → total cascade), `service-distribution` (multi-select).
Find the case closest to yours and copy **that** module.

Check visibility:

```bash
python src/manage.py shell -c "from core.reporting.registry import get_backends; print([b.slug for b in get_backends()])"
```

**Full reference** — `DatasetBackend`/`DatasetResult`/`FieldSpec`/`CompletenessIssue`, "your own
backend in five steps", discovery rules, snapshot contract, k-anonymity, export formats and the
test checklist for new backends: [docs/en/reporting-plugins.md](docs/en/reporting-plugins.md)
(DE: [docs/reporting-plugins.md](docs/reporting-plugins.md)). Architecture decisions:
[ADR-038](docs/adr/038-reporting-plugin-backends.md),
[ADR-042](docs/adr/042-report-key-berichtszuordnung.md),
[ADR-023](docs/adr/023-k-anonymization-statistik.md).

### Configuration packages (YAML)

A configuration package describes a facility's field templates, document types, field groups,
field assignments and field conditions **declaratively**. Packages make form configuration
shareable: versionable, reviewable, exchangeable between operators — and **idempotent** to
apply. The bundled standard catalogue
[`src/core/packages/default.yaml`](src/core/packages/default.yaml) is itself a package and goes
through the same loader as any third-party one.

```bash
python src/manage.py load_package --file package.yaml --facility <uuid> --dry-run
python src/manage.py load_package --default --facility <uuid>      # apply the standard catalogue
```

Four rules that matter in practice:

1. **The slug is the identity**, not the name. Slugs are immutable after creation in both the
   model and a DB trigger; a changed slug creates a duplicate in **every** facility that carries the object
   instead of updating.
2. **Unknown keys are an error at every level** — a typo such as `sort_oder` should be noticed
   rather than silently ignored. Duplicate YAML keys likewise.
3. **The loader is a re-sync, not a "create if missing".** It writes package values back onto
   existing rows; which fields that affects and which it never touches is listed under
   [What the loader-based seed does to an existing facility](#what-the-loader-based-seed-does-to-an-existing-facility).
   Therefore: against existing data **always** run `--dry-run` first.
4. **All or nothing.** A conflict aborts the entire run; nothing is applied.

**Format reference, field-by-field semantics, loader buckets and the maintenance rules that
hurt:** [docs/packages.md](docs/packages.md) (German) ·
[ADR-037](docs/adr/037-konfigurationspakete-dynamische-felder.md) (package format) ·
[ADR-036](docs/adr/036-globale-block-sequenz-feldordnung.md) (field ordering) ·
[ADR-035](docs/adr/035-evaluator-semantik-bedingte-feldlogik.md) (field-condition operators).
The reverse direction — exporting an entire existing facility as versioned JSON
(`manage.py export_facility`) — is described in
[docs/facility-export-schema.md](docs/facility-export-schema.md) (German).

---

## Tests

### Test-Driven Development (Unit/Service)

The [assertion conventions](docs/testing/assertion-conventions.md) define how tests check behaviour precisely and remain effective under mutation testing.

As of 2026-05-20, test-driven development is mandatory for the unit/service layer. The required order **before** any service, form, model, or CBV change:

1. **Red** — write a pytest test in the appropriate file under `src/tests/` that describes the desired behavior but fails today. Run it with `pytest -x` and confirm it fails with the expected `AssertionError`.
2. **Green** — minimal implementation in `src/core/...` until exactly that test passes. No additional features, no premature generalization.
3. **Refactor** — clean up code (and the test if needed) while the suite stays green. Run `pytest -x` after each cleanup step.

**No "false green" — protect assertion strength** (Refs #1150): By default, a red test means the **root cause is in the code** and must be fixed there — not the test. **Weakening an existing assertion** (loosening it, removing it, or bending it to the actually broken behavior) is a **hard review stop**: permissible only if the *requirement* has changed (not the code), and then only with an explicit justification in the commit body. For **bugfixes**, write a **failing test first** that reproduces the bug *before* touching the code (skill `superpowers:systematic-debugging`). Background: 96 % coverage measures *executed*, not *asserted* lines — a weakened assertion stays green and only surfaces in the mutation run.

Example (service layer, hash helper in `core/services/audit/hash.py`, simplified; the real helper `hmac_hash_value` additionally resolves the key through a fallback):

```python
# Red — src/tests/test_hash_value_example.py
def test_hash_value_is_stable(settings):
    settings.AUDIT_HASH_KEY = "test-key"

    from core.services.audit.hash import hmac_hash_value

    assert hmac_hash_value("anlauf-2026-0001") == hmac_hash_value("anlauf-2026-0001")
    assert hmac_hash_value("anlauf-2026-0001") != hmac_hash_value("anlauf-2026-0002")
```

```bash
pytest src/tests/test_hash_value_example.py -x
# → ImportError while the helper is missing → expected Red.
```

```python
# Green — src/core/services/audit/hash.py (simplified)
import hashlib
import hmac

from django.conf import settings


def hmac_hash_value(value: str) -> str:
    key = settings.AUDIT_HASH_KEY.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()
```

```bash
pytest src/tests/test_hash_value_example.py -x
# → 1 passed → Green.
```

**Refactor:** for example, extract key resolution into a helper once a second hash use case emerges — the test stays green.

**Scope** (TDD required):

- Service, form, model, helper, CBV/view unit tests — everything under `src/tests/` outside of `src/tests/e2e/`.

**Exceptions** (manual-first / TDD-neutral):

- **E2E tests** in `src/tests/e2e/` are Playwright-driven — click through manually first (see `### End-to-End Tests (Playwright)`), then write tests from observation.
- Django migration generation, squash migrations, tooling/config patches (CI thresholds, allowlists, `pyproject.toml`).
- Pure Markdown/documentation changes.
- One-shot hygiene scripts without reuse.

Cross-reference: the test conventions in the following section [Unit and Integration Tests](#unit-and-integration-tests).

### Unit and Integration Tests

```bash
make test
# equivalent to: python -m pytest -m "not e2e"
```

Tests are located under `src/tests/`. The three largest families have their own sub-packages with a family `conftest.py` (Refs #1839): `src/tests/reporting/` (report backends), `src/tests/file_vault/` (file vault) and `src/tests/offline/` (offline bundle/shells); all other unit tests live flat in `src/tests/`. New tests go into the appropriate file or a new file following the pattern `test_<feature>.py` — into the family sub-package if one exists. Fixtures needed by a whole family belong in its `conftest.py`; suite-wide fixtures in `src/tests/conftest.py`. The mutation pin files (`test_mutation_followup_*.py`, `test_mutation_pins_*.py`) deliberately stay flat — their node IDs point at concrete mutants.

**Fixtures:** Put suite-wide fixtures in `src/tests/conftest.py` first; only when a whole family needs one (≥ 2 files) does it belong in the family's `conftest.py`. Special cases derive a file-local fixture of the same name (`def facility(facility)`) instead of copying the root fixture. Use pytest-django's `rf` for request objects.

**Value tables:** The same check logic × different inputs belongs in `@pytest.mark.parametrize` with descriptive `ids=` — patterns: `test_boundary_*`, `_authz_expectations.py`, `_entity_expectations.py`.

**Factories:** New tests that create `Event`, `DocumentType` or `Client` via `.objects.create(...)` should use the wrappers from `src/tests/_factories.py` (`make_event`, `make_document_type`, `make_client`) as soon as at least the fixed required fields per model are set explicitly — deviating cases (missing `client`/`created_by` on nullable fields, `**kwargs` forwarding) stay with direct `Model.objects.create(...)`. The factories deliberately create exactly one object and set no defaults for varying fields — whoever needs a field passes it explicitly via `**overrides`, so no test behavior silently depends on a factory default. Add new wrappers for further models only following the same pattern and after consulting the project maintainers.

**Parallel:** `make test-parallel` uses pytest-xdist to run on all available CPU cores. `make ci` uses the parallel variant automatically.

### End-to-End Tests (Playwright)

```bash
make test-e2e
# equivalent to: python -m pytest -m e2e --browser chromium
```

E2E tests are located under `src/tests/e2e/`. They are marked with `@pytest.mark.e2e` and are automatically excluded from `make ci`, since they require a running server with seed data.

**Database:** E2E tests use a dedicated database (`anlaufstelle_e2e`) that is automatically created, flushed, and populated with seed data. Dev data in `anlaufstelle` remains untouched. Details: [docs/e2e-architecture.md](docs/e2e-architecture.md).

**Important:** Every new feature must include E2E tests that are developed alongside the feature — not added later. New E2E test files are named following the pattern `test_<stream_or_feature>.py`.

Fixtures for login, server setup, etc. are located in `src/tests/e2e/conftest.py`; shared helpers in `src/tests/e2e/_helpers.py` (`do_real_login`, `wait_for_active_service_worker`, `SEED_PASSWORD`, `django_shell`). Seeds bind the target facility to the admin user (UUID primary keys) — never `order_by("pk").first()`. Comprehensive runbook with checklists and troubleshooting: [docs/e2e-runbook.md](docs/e2e-runbook.md).

**Parallel E2E tests:** `make test-e2e-parallel` starts a separate gunicorn process per xdist worker on its own port with its own database. Default: 2 workers (`E2E_WORKERS=4 make test-e2e-parallel` for more). `--dist loadfile` keeps tests from the same file on the same worker.

**Smoke tests:** `make test-e2e-smoke` runs only E2E tests marked with `@pytest.mark.smoke` (~40 critical flows, ~2-3 min). Ideal for fast validation after feature implementation.

### Test Layer Model During Development

Never run the full suite during development — work in layers, always with fail-fast (`pytest -x`):

1. **Focus:** `pytest src/tests/test_<affected_file>.py -x` — only tests in the changed area (for family sub-packages `pytest src/tests/<family>/test_<file>.py -x`, whole family: `pytest src/tests/<family> -x`). E2E focused: `pytest src/tests/e2e/test_<feature>.py -x`.
2. **Group:** Affected test files together: `pytest src/tests/test_a.py src/tests/test_b.py -x`.
3. **Parallel:** `make test-parallel` — full unit suite in parallel, before committing.
4. **Smoke:** `make test-e2e-smoke` — ~40 critical E2E flows (~2-3 min), once a feature is complete.
5. **E2E:** `make test-e2e-parallel` — full E2E suite in parallel, before pushing.

**Fail-fast always:** run tests with `pytest -x`. On failure, fix it, check whether related tests share the same root cause, then re-run the full suite. Re-runs: `pytest --lf -x` (last failed only) or `pytest --ff -x` (failed first).

**Wait strategy (E2E):** `domcontentloaded` or `wait_for_url()` — **never** `networkidle`.

### Full CI Pipeline Locally

```bash
make ci
# equivalent to: lint + check + guards (deps/matrix/release-test/vendor-js/agent-docs) + audit-js + i18n-check + typecheck + test-js + test-parallel
```

This pipeline must pass locally before every pull request.

**JS gate (`make test-js`, Refs #1932):** runs `npm test` (= `vitest run`, non-watch, deterministic) against the offline/queue/watcher suite under [`tests/js/`](tests/js/) and is a fail-fast part of `make ci`/`make ci-locked`. If `node_modules/` (no prior `npm ci`) or `package-lock.json` is missing, the target fails with a clear error message instead of a cryptic `vitest: not found`.

**JS advisory gate (`make audit-js`, Refs #1968):** runs `npm audit --audit-level=$(AUDIT_LEVEL)` against `package-lock.json` — the counterpart to `pip-audit` on the Python side, which JavaScript previously lacked entirely. It deliberately hangs in `make ci` and not only in the GitHub workflow: whether the workflows run in the internal development branch is a maintainer setting, and `make ci` is the binding gate there ([ADR-029](docs/adr/029-local-only-ci.md)); on the public repository the test workflow additionally runs `npm audit` on the pull request.

**This means the gate chain now needs network access.** `npm audit` does read the lockfile only (no `npm ci`, no `node_modules/` required), but it queries the advisory data from the npm registry. On a host without registry access `make ci` aborts in `audit-js` — right after `deps-check`, i.e. before a single test runs. Individual gates can still be run selectively in that case (`make lint`, `make typecheck`, `make test-parallel`).

Threshold `moderate` rather than `high`, because the advisory that prompted this gate was itself only `moderate`. The value lives in the Makefile as `AUDIT_LEVEL ?= moderate` and can be overridden: `make ci AUDIT_LEVEL=high` or `make audit-js AUDIT_LEVEL=high`. That is the emergency brake for the one case the gate cannot resolve on its own — a freshly published `moderate` advisory **with no fix available** blocks every commit from the moment it appears. The default stays blocking at `moderate`; raising it belongs in the pull-request description, not in the Makefile.

All Node packages in this project are `devDependencies` (Tailwind CLI + Vitest toolchain) — hence no `--omit=dev`, which would leave the gate empty. They are, however, also installed during the container build: the Node stage of the [`Dockerfile`](Dockerfile) calls `npm ci` without `--omit=dev`; only the Tailwind CLI is executed there, and only the generated `styles.css` ends up in the final image.

### Mutation Testing (mutmut)

Mutation testing checks how many synthetic code mutations the test suite actually detects. Configuration in `pyproject.toml` (`[tool.mutmut]`). Expected runtime: 3–6 h on `core.services` + `core.forms`. Therefore not a PR requirement, but done selectively per wave issue.

```bash
make mutation           # mutmut run via scripts/dev/run_mutmut.py
make mutation-report    # survivors list, non-interactive
```

`make mutation`, `make mutation-report`, `make mutation-delta BASE=<ref>` and
`make mutation-skipped` are maintainer/dev-only tools. In the public source tree
they report `not available in the public source tree (maintainer tool)` when the
stripped scripts are absent; that is not a mutation result. There is no reliable
resume: restarting `mutmut run` starts a new measurement and loses progress.

For longer runs on a sandbox with an OOM-killer or idle-killer, `scripts/dev/run_mutmut_watchdog.sh` (dev-only) is available:

```bash
# Default: 0 additional restarts, stall threshold 5 min, 2 mutmut workers
scripts/dev/run_mutmut_watchdog.sh

# More aggressive profile
MAX_RESTARTS=5 STALL_THRESHOLD=600 MUTMUT_MAX_CHILDREN=4 \
    scripts/dev/run_mutmut_watchdog.sh
```

The watchdog detects stalled or dead master processes. By default it stops without restarting (`MAX_RESTARTS=0`); explicitly enabled restarts begin a new measurement and lose progress. Exits 0 as soon as `mutants/mutmut-stats.json` has been written during the watchdog session. Background: Refs #937.

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
   - Description: What was changed and why? Which issues are being closed? The template [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) is mandatory here (see "PR body requirement" below).
   - Screenshot or demo if UI changes are included
   - Link to the associated GitHub issue

5. **Review:** At least one approval required. Feedback should be objective and constructive.

6. **Takeover:** Once CI is green and approval is given, the maintainer takes over your commits. External pull requests are not merged directly into `main` but taken over via the release mirror (see below).

### Code of Conduct

All contributors are bound by the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Harassment incidents should be reported confidentially to `kontakt@anlaufstelle.app` (Refs #836).

### How your PR gets merged (release mirror)

The `main` branch of this repository is a **release mirror**: it is rebuilt from the
internal development branch on every release (with purely internal development tooling
removed). External pull requests are therefore not merged directly into `main`. Instead:

1. Review happens right here on the PR.
2. Once approved, we take your commits over into the development branch — your Git author
   field (and with it your authorship) is preserved.
3. Your change appears on `main` with the next release; your PR is then closed with a
   reference to that release and credited in the CHANGELOG.

A PR showing as "closed" rather than "merged" is not a rejection — it is how the release
mirror works.

---

## Licensing of Contributions

### Inbound = outbound (AGPL-3.0)

This project is licensed under the [GNU AGPL v3](LICENSE). By submitting a contribution
(pull request, patch, documentation change) you license your contribution under the same
license ("inbound = outbound", see also [GitHub Terms of Service, section D.6](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#6-contributions-under-repository-license)).
You keep your copyright — there is no transfer of rights, and we do not require a
Contributor License Agreement (CLA).

### Developer Certificate of Origin (DCO)

With every commit you certify the [Developer Certificate of Origin 1.1](https://developercertificate.org/):
that you have the right to submit the contribution under the project license — because you
wrote it yourself or took it from compatibly licensed sources with license notices intact.

To do so, sign off every commit with your name and a valid email address:

    git commit -s

This appends a line `Signed-off-by: First Last <email@example.org>` to the commit message.
We cannot accept pull requests without a sign-off; a forgotten sign-off can be added with
`git commit --amend -s` or `git rebase --signoff`.

**Local check before opening the PR:** Check all intended PR commits with:

    make dco-check RANGE=origin/main..HEAD

The check is a local maintainer/dev-only tool. In the public source tree the
target reports that it is unavailable; inspect the `Signed-off-by` lines with
`git log --format=full origin/main..HEAD` instead. The unavailability message is
not a successful DCO check. There is no server-side enforcement; enabling the GitHub DCO
app remains a maintainer decision.

**Third-party material:** Do not include code or text from sources whose license is not
AGPL-compatible. Mark any adopted material (source + license) in the commit or PR.

**AI assistance:** Contributions may be created with AI tools (this project uses them
itself). You vouch for the rights to the result via your sign-off, as with any other
contribution — review AI output accordingly.

**PR body requirement:** The template [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) is mandatory for pull requests — purpose/Refs, changes, verification and the DCO confirmation belong in the PR body. Every commit carries `Signed-off-by: Name <email>`; if the sign-off is missing, we ask and only take over the PR once the sign-off is complete. There is deliberately no CI enforcement of the sign-off in this version.

### Attribution

Your authorship is preserved: when your commits are taken over into the internal
development branch, your Git author field (or a `Co-authored-by:` trailer) is kept. The
public `main` branch is a release mirror (see above, "How your PR gets merged"); there,
external contributions are credited by name in the release's [CHANGELOG](CHANGELOG.md).

---

## Architecture Overview

> **Detailed signpost:** [docs/architecture.md](docs/architecture.md) (German) — layering
> (views → services → models), security architecture, no-code configuration core, offline PWA
> and test landscape, each pointing to the ADR that owns the decision. The section here remains
> the short introduction to roles, directory structure and the design decisions you need for
> your first pull request.

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
      document_type.py   # DocumentType
      field_template.py  # FieldTemplate
      document_type_field.py  # DocumentTypeField
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
      zeitstrom.py       # ZeitstromView (home page) and its partial views
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
    conftest.py          # Suite-wide fixtures
    test_<feature>.py    # Unit/integration tests (flat)
    reporting/           # Family sub-package with its own conftest.py (Refs #1839)
    file_vault/          # ditto (file vault)
    offline/             # ditto (offline bundle/shells)
    e2e/                 # Playwright E2E tests
      conftest.py        # Shared fixtures
      test_<feature>.py  # Tests per feature
```

### Key Design Decisions

- **Encryption:** Sensitive fields are encrypted at the application level (`core/services/`). The `ENCRYPTION_KEY` is required in production.
- **Audit log:** All security-relevant actions are written to `AuditLog` — via the typed helpers in `core/services/audit/` (direct `AuditLog.objects.create(...)` calls are blocked by an architecture test). Every `detail` key must be declared with a rationale in `core/services/audit/detail_policy.py`; an undeclared key fails the test run. Plain-text free text does not belong there — use `hmac_hash_value` or the `target_id` instead (Refs #1944).
- **Retention:** The management command `enforce_retention` enforces retention periods.
- **HTMX-first:** Interactive UI elements are preferably implemented via HTMX partials to keep JavaScript minimal.
- **Service layer:** Views delegate logic to services — this makes testing easier and keeps views lean.

<!-- translation-source: CONTRIBUTING.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-22 -->
<!-- source-hash: d056ef22e6e969191d009c56732d72696dae8acc -->
