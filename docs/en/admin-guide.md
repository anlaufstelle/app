> This is the English translation of [admin-guide.md](../admin-guide.md). The German version is the authoritative source.

# Anlaufstelle -- Admin Guide

This guide is intended for IT administrators of social service facilities who install, configure, and operate Anlaufstelle.

---

## Table of Contents

1. [Installation (Docker Compose)](#1-installation-docker-compose)
2. [Initial Configuration](#2-initial-configuration)
   - [Configure Mandatory Information (Imprint/Privacy)](#configure-mandatory-information-imprintprivacy)
   - 2.2c [Decommissioning a Facility: Archive Instead of Delete](#22c-decommissioning-a-facility-archive-instead-of-delete)
   - 2.5 [Configure Documentation Types](#25-configure-documentation-types)
   - 2.6 [Manage Selection Options (Field Templates)](#26-manage-selection-options-field-templates)
   - 2.6b [Fuzzy Search (pg_trgm)](#26b-fuzzy-search-pg_trgm)
   - 2.7 [Two-Factor Authentication (2FA)](#27-two-factor-authentication-2fa)
   - 2.8 [Quick Templates](#28-quick-templates)
   - 2.9 [Encrypted File Vault & Virus Scanning](#29-encrypted-file-vault--virus-scanning)
   - 2.10 [Offline Mode & Streetwork (M6A)](#210-offline-mode--streetwork-m6a)
   - 2.11 [CSV Import and Export: Formula Protection](#211-csv-import-and-export-formula-protection)
   - 2.12 [Import Wizard](#212-import-wizard)
3. [Backup and Recovery](#3-backup-and-recovery)
4. [Updates](#4-updates)
   - 4.1 [Follow Releases](#41-follow-releases)
5. [Monitoring](#5-monitoring)
   - 5.4 [CSP Debugging](#54-csp-debugging)
   - 5.5 [Compliance Dashboard](#55-compliance-dashboard)
   - 5.6 [Error Tracking & Data Minimization](#56-error-tracking--data-minimization)
   - 5.7 [Breach Detection Scan](#57-breach-detection-scan)
6. [Troubleshooting](#6-troubleshooting)
7. [GDPR Notes](#7-gdpr-notes)
   - 7.8 [Optimistic Locking](#78-optimistic-locking)
   - 7.9 [Row Level Security (RLS)](#79-row-level-security-rls)
8. [Statistics Snapshots & Materialized View](#8-statistics-snapshots--materialized-view)

---

## 1. Installation (Docker Compose)

### Prerequisites

**Mandatory for installation:**

- Docker Engine 24 or newer and Docker Compose v2 (as a plugin: `docker compose`)
- `git` and `openssl`
- Docker is also required for the Fernet-key container generator described below; host `python3` is needed only for the optional alternative command
- root/`sudo` rights to create the installation directory and configure host operations
- a publicly reachable server; DNS records for `DOMAIN` **and** `www.<DOMAIN>` must point to it
- ports 80 and 443 accessible from the internet

**Mandatory for ongoing operation:** `bash` (not just `sh`), `tar`, `gzip`/`gunzip`, `curl`, and an installed, running cron service for host backups. Optional tools are `jq` for JSON evaluation, `mail` for local alerts, and -- depending on the off-site target -- `rclone`, `aws`, or `scp`.

#### Hardware

Prerequisites:

- Docker Engine 24 or newer (see above)
- an HTTPS-capable domain; the delivered `Caddyfile` obtains the certificate automatically through Let's Encrypt ([Step 3](#step-3-review-the-caddyfile))

**CPU and disk space:** There are no measured minimum values for CPU, disk space or number of users. 1 vCPU and 10 GB of free disk space for images, database, virus signatures and attachments are an unmeasured starting point, not a commitment for any particular load; the local backups under `BACKUP_HOST_DIR` come on top ([§ 3](#3-backup-and-recovery)). Check load and disk usage during operation.

**Memory:** `docker-compose.prod.yml` caps every service with `mem_limit`: `clamav` 3 GB, `db` 2 GB, `web` 2 GB, `cron` 1 GB, `redis` 512 MB, `caddy` 512 MB, and the short-lived maintenance service `maintenance` 1 GB. These values are upper limits, not reservations; the actual demand depends mainly on the virus scanner, which keeps its signature database in memory. Measure the usage after the first start with `docker stats` ([§ 5.3](#53-container-status)) and size the host's memory accordingly.

### Step 1: Download Files

```bash
sudo install -d -o "$(id -u)" -g "$(id -g)" /opt/anlaufstelle
git clone https://github.com/anlaufstelle/app.git /opt/anlaufstelle
cd /opt/anlaufstelle
```

Throughout this guide, `/opt/anlaufstelle` is the **installation directory**. Use the complete repository or release tarball: in addition to `docker-compose.prod.yml` and `Caddyfile`, the production stack requires `.env.example`, PostgreSQL initialization, the cron schedule, and the setup and operations scripts in their directory structure.

### Step 2: Configure Environment Variables

> **Quoting rule:** `.env` is read both by Docker Compose and by the host backup scripts as a shell file. Put **every value in single quotes**, for example `TRUSTED_PROXY_CIDRS='203.0.113.0/24 198.51.100.0/24'`. This preserves spaces and `$` literally on both paths. The random values generated below contain no single quotes.

Create a `.env` file in the same directory as `docker-compose.prod.yml`:

```bash
cp .env.example .env   # if available, otherwise create manually
```

Minimal `.env` for production operation:

```dotenv
# Domain (must have a DNS record)
DOMAIN='anlaufstelle.meine-einrichtung.de'

# Django
DJANGO_SECRET_KEY='<long-random-string>'
DJANGO_AUDIT_HASH_KEY='<separate-long-random-string>'
DJANGO_SETTINGS_MODULE='anlaufstelle.settings.prod'
# localhost/127.0.0.1 are required for the container health check
# (http://localhost:8000/health/) — prod.py derives ALLOWED_HOSTS solely from
# this variable; without localhost /health/ raises a DisallowedHost 400.
ALLOWED_HOSTS='anlaufstelle.meine-einrichtung.de,localhost,127.0.0.1'

# Database (three-role model, Refs #902)
POSTGRES_DB='anlaufstelle'
POSTGRES_USER='anlaufstelle'                         # App role (Django runtime): NOSUPERUSER, NOBYPASSRLS
POSTGRES_PASSWORD='<at-least-32-characters>'
POSTGRES_ADMIN_USER='anlaufstelle_admin'             # Admin role (migrations/seed/retention): NOSUPERUSER, BYPASSRLS
POSTGRES_ADMIN_PASSWORD='<at-least-32-characters>'
POSTGRES_BOOTSTRAP_PASSWORD='<at-least-32-characters>'  # postgres bootstrap superuser (role setup only)

# Field encryption (required in production)
# Recommended: plural form for rotation; the first key is the write key, any others are read-only.
ENCRYPTION_KEYS='<fernet-key-1>'
```

Generate a separate value of at least 32 characters for **each** of the three database passwords; `change-me` placeholders are rejected. Run this command three times; each result is 48 characters:

```bash
openssl rand -hex 24
```

**Generate an encryption key (without host Python):**

```bash
docker run --rm --entrypoint python ghcr.io/anlaufstelle/app:latest \
  -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`latest` is an example value for a tag that is actually published. Whether a tag is currently pullable is shown by `docker manifest inspect ghcr.io/anlaufstelle/app:<tag>` (exit code 0 = published) or the [GHCR package page](https://github.com/anlaufstelle/app/pkgs/container/app). Release tags are published as images in the registry, including those that GitHub marks as pre-releases. You will find the state selected under [`APP_VERSION`](#full-environment-variable-reference) in `docker-compose.prod.yml` there; without registry access, fall back to the image built locally as described in the following section. The entrypoint override is required. If the host optionally has `python3` and the `cryptography` module installed, this alternative command generates the same key:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Build the image locally from the Dockerfile (without registry access):**

Anyone who wants the same state as `docker-compose.prod.yml` without registry access builds the image from the delivered `Dockerfile` in the installation directory:

```bash
docker build -t ghcr.io/anlaufstelle/app:local .
```

Then set `APP_VERSION='local'` in the `.env` file and repeat the encryption key step with the locally built tag:

```bash
docker run --rm --entrypoint python ghcr.io/anlaufstelle/app:local \
  -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`DJANGO_SECRET_KEY` and `DJANGO_AUDIT_HASH_KEY` each need their own random value of at least 64 characters; the following command produces 64 Base64 characters from 48 bytes of randomness and is run **separately** for each key — the same command appears as a comment in `.env.example`:

**Generate a secret key:**

```bash
openssl rand -base64 48 | tr -d '\n'
```

**Generate an audit hash key:**

```bash
openssl rand -base64 48 | tr -d '\n'
```

The `DJANGO_AUDIT_HASH_KEY` secures the audit hashes — pseudonymized entries (e.g. emails in password-reset audits) as well as the HMAC integrity chain of the audit log — and is deliberately kept separate from `DJANGO_SECRET_KEY` (see [`docs/disaster-recovery.md`](https://github.com/anlaufstelle/app/blob/main/docs/disaster-recovery.md)).

> **Important:** Store `ENCRYPTION_KEYS` and `DJANGO_SECRET_KEY` securely (e.g., in a password manager or secret management system). Without the keys, encrypted field data becomes permanently unreadable.

#### Full Environment Variable Reference

The following tables document application settings and operating variables (see [`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py) and [`prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py)).

**Application setting vs. passed-through setting:** `web` deliberately does not load all of `.env`; it receives the explicit allowlist delivered with #2219. `maintenance` and `cron`, by contrast, load the full file. The table describes the delivered state:

| Variable(s) | State |
|---|---|
| `DJANGO_SECRET_KEY`, `DJANGO_AUDIT_HASH_KEY`, `ALLOWED_HOSTS`, `EXTERNAL_ORIGIN`, `TRUSTED_PROXY_HOPS`, `TRUSTED_PROXY_CIDRS` | passed from `.env` to `web` |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_PORT` | passed from `.env` to `web`; the app role remains RLS-restricted |
| `ENCRYPTION_KEYS`/`ENCRYPTION_KEY`, `CLAMAV_ENABLED`, `CLAMAV_PORT`, `CLAMAV_TIMEOUT` | passed from `.env` to `web` |
| `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`, `EMAIL_*`, `DEFAULT_FROM_EMAIL` | passed from `.env` to `web` |
| `SUPPORT_CONTACT` | passed from `.env` to `web`; empty shows a generic reference to the facility's application support on the server-error page |
| `DJANGO_SETTINGS_MODULE`, `POSTGRES_HOST`, `CLAMAV_HOST` | `DJANGO_SETTINGS_MODULE` and `POSTGRES_HOST` are fixed in `docker-compose.prod.yml` to `anlaufstelle.settings.prod` and `db`; `CLAMAV_HOST` is configurable through `.env` |
| `MEDIA_ROOT`, `BACKUP_DIR`, `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, `REDIS_URL`, `SOURCE_CODE_URL`, `SOURCE_CODE_VERSION`, `DJANGO_AUDIT_HWM_PATH`, `DJANGO_HEALTH_BACKUP_REQUIRED`, `DJANGO_HEALTH_DISK_REQUIRED` | passed from `.env` to `web`; without an entry the Compose fallback from the "Default" column of the tables below applies |
| `REDIS_PASSWORD` | `redis` (password protection); Compose embeds the value in the `REDIS_URL` fallback of `web`, `cron` and `maintenance`, `web` does not receive the variable itself |
| `APP_VERSION`, `APP_IMAGE_DIGEST` | only select the image (`image:` in `docker-compose.prod.yml`); they do not reach `web` |
| `ANLAUFSTELLE_PROCESS_ROLE`, `DJANGO_LOAD_DOTENV`, `POSTGRES_APP_USER`, `FILE_UPLOAD_TEMP_DIR` | internal: fixed in `docker-compose.prod.yml` (`POSTGRES_APP_USER` derived from `POSTGRES_USER`); do not control them through `.env` |
| `DJANGO_HEALTH_DETAIL_TOKEN`, `LOG_FORMAT`, `FILE_VAULT_MAX_UPLOAD_BYTES`, `FILE_VAULT_MAX_UPLOAD_FILES`, `FILE_VAULT_MAX_IMAGE_PIXELS`, `FILE_VAULT_MAX_ARCHIVE_BYTES`, `FILE_VAULT_MAX_ARCHIVE_RATIO`, `FILE_VAULT_MAX_ARCHIVE_ENTRIES`, `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB`, `MAINTENANCE_FLAG_FILE`, `MAINTENANCE_ALLOW_IPS`, `MAINTENANCE_CACHE_TTL`, `MAINTENANCE_RETRY_AFTER`, `SUDO_MODE_TTL_SECONDS`, `DJANGO_INVITE_TOKEN_TIMEOUT`, `DJANGO_PASSWORD_RESET_TIMEOUT` | passed from `.env` to `web` |
| `MFA_ENFORCED_ROLES_IMMEDIATE`, `MFA_ENFORCED_ROLES_GRACE`, `OTP_WEBAUTHN_RP_ID`, `OTP_WEBAUTHN_ALLOWED_ORIGINS`, `DJANGO_AUDIT_DETAIL_STRICT`, `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT`, `REPORTING_BACKEND_STATEMENT_TIMEOUT_MS` | passed from `.env` to `web`; without an entry in `.env` the application defaults apply, a deliberately empty role list stays empty |
| `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD`, `POSTGRES_BOOTSTRAP_PASSWORD` | `maintenance`/initialization only, deliberately **not** `web` |
| `BREACH_*` | `cron` and `maintenance` (through `env_file`) |
| `TZ` | `cron` (Compose fallback `Europe/Berlin`; time zone of the cron schedule) and `maintenance` (through `env_file`); `web` does not receive `TZ` -- the application computes with `TIME_ZONE = "Europe/Berlin"` regardless |
| `BACKUP_HOST_DIR` | host scripts and volume mount; `BACKUP_DIR` is the read-only target in `web` |
| `LEGAL_IMPRINT_FILE`, `LEGAL_PRIVACY_FILE` | `web` only with the additional `compose.legal.yml`, as shown under [Mandatory information](#configure-mandatory-information-imprintprivacy) |

> Every variable assigned to `web` in this table reaches the service through the delivered allowlist; `APP_VERSION`/`APP_IMAGE_DIGEST` only affect the image selection, `BACKUP_HOST_DIR` acts through host scripts and the volume mount, and `LEGAL_IMPRINT_FILE`/`LEGAL_PRIVACY_FILE` only with the additional `compose.legal.yml`. A `compose.override.yml` is needed only for additional variable names not listed here.

Set configuration values in `.env`, then recreate `web` with the complete **file set**. As soon as a Compose invocation contains at least one explicit `-f`, Compose does not load `compose.override.yml` automatically. Every recreate command below therefore builds the list in the fixed order `docker-compose.prod.yml`, `compose.legal.yml` (if configured), `compose.override.yml` (if configured):

```bash
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d --force-recreate web
```

**Django & Hosts**

| Name | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | -- (required in prod) | Signs sessions/CSRF. Generate with `openssl rand -base64 48 \| tr -d '\n'`. |
| `DJANGO_AUDIT_HASH_KEY` | -- (required in prod) | HMAC key for pseudonymized audit entries (e.g. password-reset emails), separate from `DJANGO_SECRET_KEY` -- without its own key, the audit hash would otherwise silently fall back to `SHA256(SECRET_KEY)`, which a `SECRET_KEY` leak could then exploit retroactively; hence fail-closed. Generate it separately with `openssl rand -base64 48 \| tr -d '\n'`. |
| `DJANGO_SETTINGS_MODULE` | -- | In production: `anlaufstelle.settings.prod`. |
| `ALLOWED_HOSTS` | -- (required in prod) | Comma-separated host names, e.g. `anlaufstelle.example.de`. |
| `EXTERNAL_ORIGIN` | derived from `ALLOWED_HOSTS` | Fixed base of every security-relevant link -- the invitation setup link, the password-reset link, and the account-unlock link. Format `https://host[:port]`, without a path and without a trailing slash; a value carrying a path or missing the scheme is rejected at startup. Those links are therefore **no longer** built from the `Host` header of the incoming request. If `ALLOWED_HOSTS` lists exactly **one** public host name, the application derives `https://<host>` itself and the variable can be omitted -- `localhost`, `127.0.0.1` and `::1` do not count, they are only there for the container health check. With several public hosts or with subdomain patterns only (`.example.de`) it is **mandatory** -- otherwise the application refuses to start (#1967). |
| `TRUSTED_PROXY_HOPS` | `1` | Number of trusted proxies in front of the app (X-Forwarded-For evaluation). `0` = no proxy, `1` = Caddy only, `2` = CDN + Caddy. A value above `1` only takes effect **together with** `TRUSTED_PROXY_CIDRS` -- otherwise the application never sees more than one entry and falls back to the proxy IP (#1963). |
| `TRUSTED_PROXY_CIDRS` | empty | Address ranges of the proxies whose incoming `X-Forwarded-For` the reverse proxy may believe -- **space-separated only** (a comma is not a separator, and a netmask is not a valid notation), as CIDR (`203.0.113.0/24`, `2001:db8::/32`), as a single address, or with the keyword `private_ranges`. Leave empty as long as Caddy is the only proxy: Caddy then discards any value sent by the client and sets the header itself. In front of a CDN or load balancer the variable is a **prerequisite** for `TRUSTED_PROXY_HOPS > 1`; the same value populates the Caddy configuration and the application. An unusable range aborts startup (#1963). |
| `DJANGO_HEALTH_DETAIL_TOKEN` | -- | Token for the `X-Health-Token` header; authorizes monitoring callers to retrieve the detail fields of `/health/` (`database`, `virus_scanner`/`clamav`, `encryption_key`, `db_user_is_superuser`/`db_role_posture`, `version`, `cache`, `smtp`, `last_backup_age_hours`/`backup`/`backup_required`, `disk_free_pct`/`disk`/`disk_required`, `stale_jobs`). Without a token, `/health/` returns only `{"status": ...}` (see [§ 5.1](#51-health-endpoint), #1375). |

**Database (PostgreSQL — three-role model, #902)**

Anlaufstelle separates three DB roles: a hardcoded `postgres` bootstrap superuser (creates the app roles on first start only), the **app role** (`POSTGRES_USER`, `NOSUPERUSER NOBYPASSRLS` — Django runtime, protected by RLS), and the **admin role** (`POSTGRES_ADMIN_USER`, `NOSUPERUSER BYPASSRLS` — migrations, seed, retention pruning). The `.env` variables `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD` and `POSTGRES_BOOTSTRAP_PASSWORD` are mandatory.

| Name | Default | Description |
|---|---|---|
| `POSTGRES_DB` | -- | Database name (required: Compose passes the value through without a fallback; example in `.env.example`: `anlaufstelle`). |
| `POSTGRES_USER` | -- | App role (Django runtime), `NOSUPERUSER NOBYPASSRLS` (required: Compose passes the value through without a fallback; example in `.env.example`: `anlaufstelle`). |
| `POSTGRES_PASSWORD` | -- | App-role password; required, at least 32 characters and not a `change-me` placeholder in production. |
| `POSTGRES_ADMIN_USER` | -- | Admin role for migrations/seed/retention, `NOSUPERUSER BYPASSRLS` (required, no default; example value in `.env.example`: `anlaufstelle_admin`). |
| `POSTGRES_ADMIN_PASSWORD` | -- | Admin-role password; at least 32 characters and not a `change-me` placeholder. |
| `POSTGRES_BOOTSTRAP_PASSWORD` | -- | Password for the `postgres` bootstrap superuser (initial role setup only); at least 32 characters and not a `change-me` placeholder. |
| `POSTGRES_HOST` | `localhost` (via Compose: `db`) | DB host. |
| `POSTGRES_PORT` | `5432` | DB port. |

> **Tip:** `docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles` verifies the role topology at runtime (exit `0` = ok, `1` = incorrect attribute profile, `2` = configuration incomplete). The [Compliance Dashboard](#55-compliance-dashboard) uses the same check, and [`scripts/initial-setup.sh`](../../scripts/initial-setup.sh) runs it during first-time setup. In addition, the [health endpoint](#51-health-endpoint) continuously checks the connection **actually in use** (`db_role_posture`) -- `check_db_roles` verifies that the roles *exist* with the right attributes, not that the application connects as the app role (Refs #793).

**Field Encryption (MultiFernet rotation)**

| Name | Default | Description |
|---|---|---|
| `ENCRYPTION_KEYS` | -- | Comma-separated list of Fernet keys. The **first** key is the write key (new data is encrypted with it); all others are read-only (decrypt fallback during rotation). |
| `ENCRYPTION_KEY` | -- | Single-key operation (alternative to `ENCRYPTION_KEYS`); **deprecated**, a key ring is preferred. At least one of the two variables must be set in production; otherwise the app refuses to start. |

**Migrating from a single key to a key ring:** If only `ENCRYPTION_KEY` is set, `python manage.py check --deploy` reports the warning `core.W005` (#2240). The single key keeps working unchanged; it will be removed after version 1.0, after which `ENCRYPTION_KEYS` is the only supported variable. To switch over without re-encrypting any data:

1. Copy the current value of `ENCRYPTION_KEY` unchanged as the first and only entry into `ENCRYPTION_KEYS`: `ENCRYPTION_KEYS=<previous-value>`.
2. Remove `ENCRYPTION_KEY` from `.env`.
3. Recreate the web service with the full set of Compose files (as described earlier in this section) and run `python manage.py check --deploy` in the web container -- `core.W005` no longer appears.

The same key then decrypts all existing data; a later rotation follows the section "Key Rotation (`ENCRYPTION_KEYS`)".

**Virus Scanning (ClamAV, #524)**

| Name | Default (prod) | Description |
|---|---|---|
| `CLAMAV_ENABLED` | `true` | Enables virus scanning before upload encryption. Fail-closed: if the daemon is unreachable, the upload is rejected. `false` is permitted only for development and tests, not in production. |
| `CLAMAV_HOST` | `clamav` | Hostname of the ClamAV daemon (service name in the Compose network). Configure an external scanner through `CLAMAV_HOST` and `CLAMAV_PORT`; `CLAMAV_HOST` is passed through from `.env`. |
| `CLAMAV_PORT` | `3310` | TCP port of the clamd socket. |
| `CLAMAV_TIMEOUT` | `15` | Wall-clock timeout in seconds per scan invocation; MUST be below `GUNICORN_TIMEOUT`, otherwise the worker timeout kills the request instead of failing closed. |

**Logging**

| Name | Default | Description |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` enables structured logging via [`core.logging.JsonFormatter`](https://github.com/anlaufstelle/app/blob/main/src/core/logging.py) -- recommended for production with log aggregation. |

**Sentry (optional)**

| Name | Default | Description |
|---|---|---|
| `SENTRY_DSN` | -- | Enables error tracking (opt-in -- without a DSN, no telemetry at all leaves the installation). Sets `send_default_pii=False` and `include_local_variables=False`; a server-side scrubber removes request bodies/cookies/query strings and masks sensitive fields before an event leaves the installation. Mechanism **and limits** see [§ 5.6 Error Tracking & Data Minimization](#56-error-tracking--data-minimization). |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | Sample rate for performance traces (0.0--1.0). |

**Email (SMTP, production)**

| Name | Default | Description |
|---|---|---|
| `EMAIL_HOST` | Compose: empty (settings without the variable: `localhost`) | SMTP host for password-reset and invite emails. Without an entry in `.env`, `docker-compose.prod.yml` passes an **empty** value: every e-mail then fails to send, and `/health/` reports `disabled` in the `smtp` field without lowering the overall status. Always set it for operation. |
| `EMAIL_PORT` | `587` | SMTP port. |
| `EMAIL_HOST_USER` | -- | SMTP user. |
| `EMAIL_HOST_PASSWORD` | -- | SMTP password. |
| `EMAIL_USE_TLS` | `True` | Negotiate STARTTLS on the configured port (typically port 587). |
| `EMAIL_USE_SSL` | `False` | Implicit TLS/SMTPS (typically port 465). Mutually exclusive with `EMAIL_USE_TLS` — the application deliberately refuses to start with both set to `True`. |
| `DEFAULT_FROM_EMAIL` | `noreply@anlaufstelle.app` | Sender address. |

Since Django 6.1 the application configures email delivery internally via
Django's `MAILERS` setting instead of the former `EMAIL_*` settings.
**Nothing changes for operators:** the environment variables above keep their
names and are still read from `.env` — an existing `.env` stays valid without
any change.

**Django 6.1 requires an SMTP backend on production hosts** (#2326):
the deployment check `python manage.py check --deploy`, which the web container
runs on every start, aborts with the error `mail.E001` as soon as the default
mailer is a development-only backend (console, file, in-memory, dummy). The
production settings always use the SMTP backend, so startup does not depend on
a reachable relay. **Without a reachable SMTP server** the application starts,
but every invitation and password-reset e-mail fails on sending: **Verwaltung → Zugänge** shows the warning that the invitation could not be sent, `/health/`
reports `unreachable` in the `smtp` field (`disabled` with an empty `EMAIL_HOST`), and only the technical fallback via
an initial password remains (see "SMTP requirements and troubleshooting" in the
[token invite flow](#token-invite-flow-refs-528)). A console backend that only
writes e-mails to the container log is provided solely in the live-dev and demo
settings, where it is tied to an empty `EMAIL_HOST`.

**File Vault Upload Limits (#1268)**

Service-layer hard limits as defense-in-depth (independent of the per-facility configurable `Settings.max_file_size_mb`).

| Name | Default | Description |
|---|---|---|
| `FILE_VAULT_MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Hard per-file limit, applied before any full buffering. |
| `FILE_VAULT_MAX_UPLOAD_FILES` | `20` | Maximum number of files per file field (mass-upload brake). |
| `FILE_VAULT_MAX_IMAGE_PIXELS` | `40000000` | Maximum total pixels across all frames/pages of a raster image upload (also sets `PIL.Image.MAX_IMAGE_PIXELS`); additionally, fixed limits of 100 frames/pages and an estimated image-buffer budget of 256 MiB apply. |
| `FILE_VAULT_MAX_ARCHIVE_BYTES` | `209715200` (200 MB) | Zip-bomb protection: maximum sum of uncompressed entries (read from the zip directory). |
| `FILE_VAULT_MAX_ARCHIVE_RATIO` | `100` | Zip-bomb protection: maximum expansion ratio (uncompressed/compressed). |
| `FILE_VAULT_MAX_ARCHIVE_ENTRIES` | `10000` | Zip-bomb protection: maximum number of entries (read from the EOCD record). |
| `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB` | `2048` (2 GB) | Operator backstop for the **per-facility storage quota** (#2129) -- not a per-file limit but the sum of all attachments of one facility, trash included. `0` disables the backstop. See [Storage quotas](#storage-quotas). |

**Maintenance Mode (#700)**

| Name | Default | Description |
|---|---|---|
| `MAINTENANCE_FLAG_FILE` | `/media/anlaufstelle.maintenance` (production Compose) | Writable, persistent container path of the flag file; while it exists, the app responds with HTTP 503. A custom value in `.env` must also be reachable inside the container; remove existing host paths or change them to a persistent container path. |
| `MAINTENANCE_ALLOW_IPS` | -- | Comma-separated IP list for ops access during maintenance. |
| `MAINTENANCE_CACHE_TTL` | `5` | Caching duration (seconds) of the flag-file check. |
| `MAINTENANCE_RETRY_AFTER` | `600` | Value of the `Retry-After` header (seconds) while maintenance mode is active. |

**Breach Detection (#685)**

| Name | Default | Description |
|---|---|---|
| `BREACH_*` (variable family) | see runbook | Time windows and thresholds of the heuristic anomaly detection (failed logins, exports, deletions, long-term windows, distributed login attacks) as well as `BREACH_NOTIFICATION_WEBHOOK_URL` (optional escalation webhook; empty = off). Full list with defaults: [ops runbook § 6.5b](ops-runbook.md#65b-gdpr-art-3334--breach-notification-refs-685). |

**Two-Factor Authentication and Passkeys (see [§ 2.7](#27-two-factor-authentication-2fa))**

| Name | Default | Description |
|---|---|---|
| `MFA_ENFORCED_ROLES_IMMEDIATE` | `super_admin,facility_admin` | Comma-separated list of roles for which 2FA is mandatory immediately. Empty = this level off. |
| `MFA_ENFORCED_ROLES_GRACE` | `lead,staff,assistant` | Comma-separated list of roles for which 2FA becomes mandatory after a transition period of 30 days from the first sign-in. Empty = this level off. An unknown role name or a role in both lists aborts startup with `ImproperlyConfigured`. |
| `OTP_WEBAUTHN_RP_ID` | first concrete host from `ALLOWED_HOSTS` (otherwise the base domain of a wildcard entry) | Domain that passkeys are bound to. Changing it later makes registered passkeys unusable. |
| `OTP_WEBAUTHN_ALLOWED_ORIGINS` | `https://<host>` for every concrete host from `ALLOWED_HOSTS` | Comma-separated list of origins accepted for passkey checks. Required when `ALLOWED_HOSTS` contains wildcard entries only. |

**Misc**

| Name | Default | Description |
|---|---|---|
| `MEDIA_ROOT` | Compose: `/media/media` (settings without the variable: `<BASE_DIR>/media`) | Storage location for encrypted file attachments (see [§ 2.9](#29-encrypted-file-vault--virus-scanning)). In the stack, `/media` lives on the persistent `media` volume; a custom value must stay below `/media`, because outside of it the container file system is read-only (`/tmp` is volatile memory). |
| `FILE_UPLOAD_TEMP_DIR` | Django default (`tempfile.gettempdir()`) | Directory for the plaintext staging buffer of uploads > `FILE_UPLOAD_MAX_MEMORY_SIZE`, before they are encrypted into the file vault (see [§ 2.9](#29-encrypted-file-vault--virus-scanning)). Pinned to `/tmp` (tmpfs/RAM) in the shipped Compose files -- [ops runbook § 5.7a](ops-runbook.md#57a-upload-temp-directory-plaintext-staging-buffer-refs-1661). |
| `BACKUP_DIR` | `/backups` (Compose stack), otherwise `<repo>/backups` | Read-only mount target inside the container; the health endpoint and the backup row in the system area read the backup age from it. The host scripts ignore this variable. |
| `BACKUP_HOST_DIR` | `./backups` | Base directory on the host; relative values are resolved against the repository root. `backup.sh` writes to `daily/`, `weekly/`, and `monthly/` below it; `restore-drill.sh` reads from `daily/`. Compose mounts this directory read-only onto `BACKUP_DIR`, so the container sees the same files. |
| `APP_VERSION` | Compose fallback: most recently pinned release tag | Selects the release tag of the app image used by `docker-compose.prod.yml` (e.g. `v1.2.3`). Whether a tag is published is shown by `docker manifest inspect ghcr.io/anlaufstelle/app:<tag>` (exit code 0 = published) or the [GHCR package page](https://github.com/anlaufstelle/app/pkgs/container/app). The variable does not reach the `web` container; `/health/` shows the value baked in at image build time in the `version` detail field (token/authenticated callers only), which is `dev` for a locally built image. |
| `APP_IMAGE_DIGEST` | empty | Additionally binds the image to an immutable digest; written with a leading `@`, e.g. `@sha256:…`. Compose appends the value unchanged to the tag from `APP_VERSION` (Operations Runbook § 8.4a). |
| `GUNICORN_WORKERS` | `3` | Number of Gunicorn workers in the `web` service (read by `docker-entrypoint.sh`). |
| `GUNICORN_TIMEOUT` | `30` | Worker timeout in seconds; must be greater than `CLAMAV_TIMEOUT`. |
| `REDIS_URL` | Compose: `redis://redis:6379/1` (with `REDIS_PASSWORD` set: `redis://:<password>@redis:6379/1`) | Shared cache for rate limits and health-detail caching. A custom value points to an external Redis; a deliberately empty value (`REDIS_URL=`) switches to the database cache. |
| `REDIS_PASSWORD` | empty | Password of the delivered `redis` service; URL-safe characters only. When empty, `check --deploy` reports the warning `core.W004` (see [Step 4](#step-4-start-the-stack)). |
| `SOURCE_CODE_URL` | `https://github.com/anlaufstelle/app` | Source-code link in the footer (AGPL § 13). Forks and modified installations enter their own source mirror here. |
| `PROJECT_WEBSITE_URL` | `https://anlaufstelle.app` | Project link in the demo and after full authentication, and in help; an empty value or a scheme other than `http`/`https` hides it. Set as an environment variable of the app process. |
| `SOURCE_CODE_VERSION` | empty | Optional state (e.g. commit SHA) for the version stamp in GDPR documents; the footer still shows the source-code link but no source version. |
| `DJANGO_AUDIT_HWM_PATH` | Compose/`.env.example`: `/data/audit_hwm.json`; settings without the variable: empty | Signed audit anchor on the separate `audit-anchor` volume: `web` read-only, `cron`/`maintenance` read-write. Empty or unset uses the stack default; custom paths must stay on this volume. `backup.sh` aborts if the anchor is missing, empty or unreadable ([Ops Runbook § 6.7](ops-runbook.md#67-back-up-and-verify-the-audit-high-water-mark-refs-2022)). |
| `DJANGO_HEALTH_BACKUP_REQUIRED` | `true` | `true`: a missing backup signal lowers `/health/` to `degraded`. Use `false` only for instances without host backups; a stale backup still lowers the status. |
| `DJANGO_HEALTH_DISK_REQUIRED` | `true` | `true`: free space on `MEDIA_ROOT` that cannot be measured lowers `/health/` to `degraded`. Use `false` only for instances without a measurable `MEDIA_ROOT`; low free space still lowers the status. |
| `SUDO_MODE_TTL_SECONDS` | `900` | Validity duration (seconds) of the re-auth window for sensitive actions: disabling 2FA, setting up 2FA again while a factor exists, adding or removing a passkey, the person data export (JSON/PDF), **Verwaltung → Zugänge** (create, edit, send invitation, unlock, reset the second factor), the facility **Einstellungen**, the admin interface `/admin-mgmt/`, and in the system area the audit-log export, lifting locks and maintenance mode. |
| `DJANGO_AUDIT_DETAIL_STRICT` | `true` (production) | `true` rejects undeclared audit detail keys; an explicit `false` writes such keys with an ERROR log. Value validation for `recipient`/`purpose` remains active independently: at most 200 characters, no detected contact details or name pairs. |
| `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT` | `10000` | Maximum number of unexpired sessions of the whole installation searched when a sign-in against a lock, a role change, a deactivation or resending the invitation to an account with an initial password ends the sessions of an account. When the search reaches the limit it stops and reports this in the log at level ERROR. What happens to sessions of the account not found depends on the trigger: after a sign-in against a lock and after a role change they stay signed in (after the role change with the new role) until they end through inactivity or the maximum session duration. After a deactivation and after resending the invitation, which makes the initial password unusable, they still end on their next request, because authentication no longer accepts an inactive account or a changed password hash. Resetting the second factor searches no sessions and does not depend on this value. |
| `REPORTING_BACKEND_STATEMENT_TIMEOUT_MS` | `10000` | Time limit (milliseconds) per database query of a reporting backend; `0` disables the limit. |

### Step 3: Review the Caddyfile

The included `Caddyfile` automatically handles TLS certificates via Let's Encrypt and contains two site blocks:

```
{$DOMAIN} {
    reverse_proxy web:8000
    ...
}

www.{$DOMAIN} {
    redir https://{$DOMAIN}{uri} permanent
}
```

No changes are needed as long as `DOMAIN` is set in `.env` and DNS for both `DOMAIN` and `www.<DOMAIN>` points to this server.

### Step 4: Start the Stack

**Recommended -- a single command:** the bundled script [`scripts/initial-setup.sh`](../../scripts/initial-setup.sh) runs the complete first-time setup in the correct order (Refs #793):

```bash
set -a
. ./.env
set +a
BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-./backups}"
sudo install -d -m 2750 -o root -g 1000 "$BACKUP_HOST_DIR"
./scripts/initial-setup.sh
```

The example uses the recommended backup account, `root`, because backups run from root's cron. Group GID 1000 belongs to the container user `appuser`; mode `2750` gives it read and traverse access only and passes the group to new subdirectories. `backup.sh` creates new directories with `0750` and the dump, media archive, audit anchor and signatures with `0600`; only the manifest (`0640`) is readable by that group so that the "last backup" indicator can evaluate it. Never make backups readable by all users.

`initial-setup.sh` then runs **without `sudo`** as the operating account. With #2219, it checks and requires exactly this directory contract; it does not run `chown` or `chgrp` on an existing directory. An existing `BACKUP_HOST_DIR` with group 1000 and mode `2750` is accepted. If the directory is missing, the script creates it only when the invoking account may set GID 1000 (a group member or root); otherwise it aborts **before the first `up`** and prints `sudo install -d -m 2750 -o <invoking account> -g 1000 "$BACKUP_HOST_DIR"`; if backups run as a root cron job, put `root` there. If the group or mode is wrong, it aborts with `sudo chgrp 1000 "$BACKUP_HOST_DIR" && sudo chmod 2750 "$BACKUP_HOST_DIR"` as the correction command. Both messages show the resolved path instead of the variable; a relative `BACKUP_HOST_DIR` is resolved against the directory of the Compose file. It does not create the directory with root privileges itself. The script then starts the database, waits for readiness, creates the schema through the one-shot job, brings up the full stack, and verifies deployment settings and database roles. Finally it creates the super-admin interactively unless one already exists (see [§ 2.1](#21-initial-installation-create-a-super-admin-bootstrap)).

The script automatically detects a `compose.legal.yml` and a `compose.override.yml` in the same directory as `docker-compose.prod.yml` and appends them in that order -- identical to the full file set from the [environment variable reference](#full-environment-variable-reference) and every other recreate example in this guide. The file list actually used is printed at the start of the output (`Compose-Dateien: …`); without either file the stack runs with the base file only. The virus scanner's very first start downloads the complete signature database, which can take about three minutes and produces no interim output during that time -- the stack is not stuck, `web` is simply reported ready only afterwards. Check progress with `docker compose -f docker-compose.prod.yml logs clamav`.

The script is **idempotent** (a second run is safe: migrations become no-ops, an existing super-admin is skipped) and **fail-loud** -- if a step fails, it exits with a non-zero code and an explanation. Optional environment variables: `COMPOSE_FILE` (default `docker-compose.prod.yml`), `ENV_FILE` (default `.env`; passed to every `docker compose` call as `--env-file`), `ADMIN_USERNAME`, `ADMIN_EMAIL`, `WAIT_TIMEOUT`, `SKIP_SUPER_ADMIN=1`. It deliberately accepts **no** password -- `create_super_admin` prompts for it interactively ([ADR-018](../adr/018-rollenmodell-superadmin.md)).

> **Why the role verification belongs here:** if the application's database role runs as a PostgreSQL superuser, Row Level Security is **silently** bypassed -- facility isolation stops working without anything failing. The script checks this fail-loud; in addition, the health endpoint reports that state permanently as `degraded` (see [§ 5.1](#51-health-endpoint)).

**Manual (equivalent, for step-by-step control):** Django migrations do **not** run automatically on `web` service startup — they are a separate one-shot step (Refs #802). Create the backup directory beforehand as in the recommended path above with `sudo install -d -m 2750 -o root -g 1000 "$BACKUP_HOST_DIR"`: if it is missing, Docker creates it on the first start as `root:root` with mode `0755`, and a later run of `initial-setup.sh` aborts at the check for group 1000 and mode `2750`. For a fresh install, follow this order:

```bash
# 1. Start the database (+ ClamAV) first
docker compose -f docker-compose.prod.yml up -d db clamav

# 2. Create the schema once via the one-shot job
docker compose -f docker-compose.prod.yml run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 3. Start the full stack (web + caddy)
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d

# 4. Verify the security posture
docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles
```

If `REDIS_PASSWORD` remains empty (the template default), `python manage.py check --deploy` reports the `core.W004` warning (Redis without a password). This is expected in that configuration and is not an error. To avoid the warning, set `REDIS_PASSWORD`; Compose passes the value to Redis and embeds it in `REDIS_URL` for the web service.

For details on the migrate job (ownership normalization, advisory lock, expected output), see the canonical source [`docs/ops-runbook.md` § 1.2](ops-runbook.md#12-deploy-steps).

On first start:
- The PostgreSQL database is initialized
- Caddy requests a TLS certificate

Check the status:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs web
```

### Step 5: Health Check

```bash
curl https://anlaufstelle.meine-einrichtung.de/health/
```

Expected (anonymous) response -- it contains **only** the `status` field (#1375):

```json
{"status": "ok"}
```

**Normal immediately after installation:** The MFA quota in the compliance dashboard remains critical until every MFA-mandatory account has enrolled its second factor. “Critical audit events (24h)” can also show a warning after a fresh initial setup because the setup itself creates critical audit events. Background jobs that have never run initially appear as `unknown`: view refresh and breach scan first run at the next quarter-hour and half-hour, retention/audit verification/orphan cleanup daily from 03:00, and statistics snapshots at 04:00 on the next first day of a month. If a state remains past its first scheduled run, inspect `docker compose -f docker-compose.prod.yml logs cron`. An outdated ClamAV signature set is not a normal startup state. The “Admin DB role” card shows the result of the latest role check in the maintenance service; after step 5 of `scripts/initial-setup.sh` it is `ok` and names the time of the check. `unknown` means that no check result has been recorded yet (see § 5.5).

---

## 2. Initial Configuration

### Configure Mandatory Information (Imprint/Privacy)

Publicly reachable self-hosted instances need their own mandatory information under Section 5 DDG and their own information under Article 13 GDPR. Anlaufstelle deliberately does not ship generic boilerplate for this: the controller, contact channels, purposes, legal bases, recipients, and retention periods depend on the facility and its operation.

Create both texts as UTF-8 files on the host, for example at `./legal/impressum.md` and `./legal/datenschutz.md`. Then set both paths as seen from the web container:

```dotenv
LEGAL_IMPRINT_FILE='/run/legal/impressum.md'
LEGAL_PRIVACY_FILE='/run/legal/datenschutz.md'
```

Because `docker-compose.prod.yml` only passes explicitly allowlisted runtime variables to the web container, add a local `compose.legal.yml` next to `.env`:

```yaml
services:
  web:
    environment:
      LEGAL_IMPRINT_FILE: ${LEGAL_IMPRINT_FILE}
      LEGAL_PRIVACY_FILE: ${LEGAL_PRIVACY_FILE}
    volumes:
      - ./legal:/run/legal:ro
```

Restart the web service with the complete [file set from Section 1](#full-environment-variable-reference). `compose.legal.yml` is configured here; `compose.override.yml` is included if you configured it:

```bash
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d web
```

Each file must be a regular, non-empty UTF-8 file no larger than 256 KiB. The files deliberately support only a small Markdown subset: headings from `#` through `###`, paragraphs and blank lines, unordered lists with `-` or `*`, blockquote lines with `> `, bold text with `**Text**`, inline code with backticks, and links in the form `[Text](URL)`. Link text is limited to 256 characters and link targets to 2,048 characters; longer constructs are displayed as ordinary text. Link targets are limited to `http:`, `https:`, and `mailto:`; HTML and everything else are displayed as text as well. Legal text therefore cannot inject scripts or styles into the application.

When a path is unset, or a file is missing, not regular, empty, oversized, unreadable, or not valid UTF-8, the corresponding page responds with 404. File metadata is used to show the link only for regular, non-empty files within the size limit; the global page render never reads legal content for this check. The warning emitted at most once per broken configuration names only the affected setting, not the configured file path. After changing a text or mount, check `/impressum/` and `/datenschutzerklaerung/` while signed out. The special demo mode continues to use its own legal templates and ignores these two files.

### 2.1 Initial Installation: Create a Super-Admin (Bootstrap)

**Before any other configuration** -- as the final step of `./scripts/initial-setup.sh` -- create a **super-admin** (`role=super_admin`, "System administration"). This role is the only facility-spanning account and operates the installation. It is **not** created through the normal admin UI but through a dedicated interactive command with no default password:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_super_admin
```

> **Note:** if you used [`scripts/initial-setup.sh`](../../scripts/initial-setup.sh) ([§ 1, Step 4](#step-4-start-the-stack)), this step is already done -- the script invokes the same command at the end and skips it when a super-admin already exists.

The script interactively prompts for:

1. **Username** (freely chosen -- recommendation: not `admin`)
2. **Email address** (required -- used for recovery emails)
3. **Password** (enter twice -- no default, no demo value)

After successful completion, exactly one user with the role `super_admin` exists. Use this account to log in at `/login/`. The 2FA initial setup required immediately in production is complete only after the backup codes have been stored securely and acknowledged with the required checkbox; leaving early sends the next attempt back to `/mfa/setup/`. Then create the first facility and the first `facility_admin` -- in the Django admin (`/admin-mgmt/`, **Anlaufstelle Core > Facilities**) or with the setup script from [§ 2.2](#22-create-a-facility-and-application-support). On the first visit to `/admin-mgmt/`, sudo mode asks you to confirm the password again. The `/system/` area only displays the organization and its facilities; nothing is created there.

> **Important -- no default password:** For GDPR and security reasons, the application ships with **no** default password and no default username (unlike the former `make seed` workflow for development demos). Skipping this command leaves you with **no** usable login.

> **Note:** If a `super_admin` already exists, the command aborts without changes. A second super-admin is created only with `--force` (`python manage.py create_super_admin --force`); existing accounts remain untouched, and a username that is already taken is rejected.

#### Unlock an Account (CLI)

A `super_admin` is not locked by failed logins: the role is exempt from the account lockout (see [§ 2.7 Account Lockout](#account-lockout)) so that the break-glass account cannot be locked out deliberately. The sign-in rate limits (5 attempts per minute and IP address, 10 per hour and username) protect against brute force; once one is reached, the only remedy is to wait for the time window -- `unlock` does not lift it. The following command lifts the lock of a **facility-bound** account (`facility_admin`, `lead`, `staff`, `assistant`) when nobody is left with access to the facility's administration, for example the only locked `facility_admin`:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
  unlock <username>
```

The command unlocks the specified user and writes a `LOGIN_UNLOCK` AuditLog entry. With the username of a `super_admin` it has no effect: it still reports "Account-Sperre aufgehoben" (account lock lifted) but writes no audit entry, because there is nothing to lift. For facility-bound users, the UI path via the facility application support remains available in addition (see [§ 2.7 Account Lockout](#account-lockout)).

### 2.2 Create a Facility and Application support

After logging in as `super_admin`, create the first facility and a `facility_admin` (application support) for it in the Django admin (`/admin-mgmt/`, **Anlaufstelle Core > Facilities** and **Anlaufstelle Core > User**). Alternatively, the interactive setup script is available -- suitable for pure single-tenant installations where system administration and application management are the same person:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility
```

Without an option, `default` is selected. To create a facility with a bundled domain package, select it explicitly:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
  setup_facility --package awareness
```

`--package` and `--no-default-package` are mutually exclusive; an unknown package name is rejected with the sorted list of packages that are actually bundled.

> ⚠️ **Role prerequisite (#1883).** The command writes to tables with row-level security (settings, documentation types, field templates). The `NOBYPASSRLS` runtime role of `web` deliberately receives no admin secrets. The opt-in `maintenance` service connects with the BYPASSRLS role. The command checks this up front and aborts with a clear message **before** the first prompt appears. Background: [ops runbook § 9 DB role model](ops-runbook.md#9-row-level-security-rls). For a local development database whose role is a superuser anyway, the maintenance path is unnecessary.

The script interactively prompts for:

1. **Organization name** -- e.g., `Diakonie Musterstadt e.V.`
2. **Facility name** -- e.g., `Beratungsstelle Nord`
3. **Admin username** for the application support (`facility_admin`; the `admin` default can be replaced)
4. **Application support password** (enter twice)

Afterwards, the organization, facility, default settings, the **default catalog of documentation types**, the three **default shifts** (early, late, night), two **quick templates** on the "Kontakt" type, and the first `facility_admin` user are created. The facility is usable right away: without at least one documentation type, the type selector would be empty and not a single contact could be recorded.

> **All or nothing:** The run is a single database transaction. If it aborts (wrong DB role, dropped connection, catalog conflict), **nothing** half-created is left behind -- not even the admin user. Once the cause is fixed, the command can simply be repeated.

> **Minimum contact stage in the default catalog:** The "Beratungsgespräch" (counseling session) documentation type requires at least the contact stage "with additional details" -- a freshly created person, by contrast, starts at "pseudonym only". The first documentation attempt for this type is therefore rejected until the person's contact stage has been raised; the redisplayed form states where and how to fix it.

> **A repeat run leaves an existing catalog alone:** If the facility already carries documentation types or field templates (second run, facility created in the Django admin), the command does **not** apply the default catalog. Names, sort orders, colors, icons, and visibility levels maintained in the admin therefore stay untouched; the command says so at the end and points to `load_package --default` as the way to deliberately pull in the shipped state.

> **Opt-out `--no-default-package`:** If you do not want the default catalog (own configuration package, entirely custom types), append `--no-default-package`. The facility then starts **without** documentation types and only becomes usable for documentation after a package has been loaded (see [docs/packages.md](../packages.md)). The command says so explicitly at the end of the run.

> ⚠️ **Domain packages are replacements, not additions.** Load a domain package with `setup_facility --package <name>` against a fresh facility. `load_package --package <name>` aborts if it finds type or field slugs that do not belong to the selected package -- including during a dry run. Repeating the same package remains allowed. The default catalog is the existing-installation case: `--default` or `--package default` retains its documented write-back semantics.

> **Retrofitting an existing facility:** For a facility that already exists -- created in the Django admin under **Anlaufstelle Core > Facilities**, or deliberately started empty with `--no-default-package` -- the same default catalog can be applied afterwards:
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance \
>   load_package --default --facility <uuid> --dry-run
> ```
>
> `--dry-run` first shows only what would happen; without the flag the package is applied. The run never deletes anything, **but it does write the catalog properties of existing types and field templates back to the shipped state** (name, sort order, color, icon, visibility) -- unlike the initial setup, which does not touch an existing catalog at all. So look at it with `--dry-run` first; the full write-back table is in [docs/packages.md § 4](../packages.md). The facility UUID appears in the address bar of its detail page under **Anlaufstelle Core > Facilities** in the Django admin.

> **Note:** If an organization or facility with the given name already exists, it will be reused. An existing username will not be overwritten.

> **Adding a second facility in the Django admin (#1970):** Additional facilities are created at runtime under **Anlaufstelle Core > Facilities > Add facility** -- that is the only path in the user interface, and it is reserved for `super_admin`. Select the bundled catalog in the **Package** field; `default` is preselected. Saving creates the **same package-specific baseline** as `setup_facility --package <name>`: settings record, selected catalog, matching time filters, and the quick templates declared for that package. The new facility is documentable immediately; all that remains is to assign at least one account to it under **Anlaufstelle Core > User**. The baseline is created **exactly once, when the facility is added**: the package field is absent when the facility is edited later, and saving it again does not touch its stock -- not even when that stock is empty. Facilities that were created in the admin and stayed empty are therefore provisioned explicitly: the default catalog with `manage.py load_package --default --facility <uuid>` (command including the role override above), time filters and quick templates by hand under **Anlaufstelle Core > Time filter** and **Anlaufstelle Core > Quick templates**.

> **Organization as a branding shell:** The organization serves only as a carrier branding container (logo, name in reports). There is no org-admin and no cross-facility effect through it -- cross-facility access runs exclusively through `super_admin` and `/system/`. Architecture decision: [ADR-018](../adr/018-rollenmodell-superadmin.md).

### 2.2b Django Admin Interface

The admin interface is available at:

```
https://anlaufstelle.meine-einrichtung.de/admin-mgmt/
```

The first visit leads to sudo-mode re-authentication. Confirm the signed-in account's password there; the admin interface then opens.

Here you can manage:

| Area | Path in Admin | Description |
|---|---|---|
| Organizations | Anlaufstelle Core > Organizations | Parent entities (tenant level) |
| Facilities | Anlaufstelle Core > Facilities | Locations within an organization |
| Settings | Anlaufstelle Core > Settings | Configuration per facility |
| Users | Anlaufstelle Core > User | Technical fallback; maintained primarily under Management > Access |
| Documentation types | Anlaufstelle Core > Document types | Technical fallback; maintained primarily under Management > Document types |
| Time filters | Anlaufstelle Core > Time filter (technical maintenance) | Named time periods for reports. **A time filter that still has handover notes cannot be deleted** (#2042) -- the admin reports the protection error. Otherwise that shift's notes would vanish silently, bypassing both their retention period and the audit log. Renaming and reordering stay possible at any time; to delete, wait for the **Retention handover notes (days)** period to elapse, or deactivate the window instead of removing it |
| Audit log | Anlaufstelle Core > Audit logs | Immutable protocol |

### 2.2c Decommissioning a Facility: Archive Instead of Delete

When a location closes, **archiving** is the intended route -- not deletion. Under **Anlaufstelle Core > Facilities**, clear the **Active** checkbox and save. Only `super_admin` may do this; facility management is reserved for that role.

**What archiving does (as of #2067):**

- Sign-ins by accounts of that facility are rejected with the message "Your facility has been archived"; a `LOGIN_FAILED` entry with `reason: facility_inactive` is written to the audit log.
- **Sessions already running lose access immediately** -- not only at the next sign-in. Affected users see a block page with a sign-out button; every domain path answers with HTTP 403.
- At the database level the facility context (`app.current_facility_id`) is additionally cleared, so the row-level security policies (see [§ 7.9](#79-row-level-security-rls)) return no rows even if some path were to bypass the block.
- **The data is fully preserved.** Retention periods, deletion requests, and the audit log continue unchanged; setting **Active** again restores access.
- `super_admin` remains unrestricted -- otherwise reactivation itself would be blocked.

> **Why no hard delete? What actually happens.**
> Permanently deleting a facility is **not a supported operation**. It fails -- reliably:
>
> - `Facility` cascades into nearly every domain table, including `core_auditlog`. That table carries the DB trigger `auditlog_immutable`, which rejects every `UPDATE`/`DELETE`. The delete cascade therefore runs **into** a rejection midway.
> - **The abort is clean, not half-finished.** Django wraps the entire cascade -- signals, fast deletes, updates, and every batch -- in **one** transaction. This holds from `manage.py shell` as well: the shell itself is in autocommit, but the cascade opens its own transaction. The trigger error rolls it back completely. You see an error message and nothing is lost. The same applies to a single `DELETE` in `psql` -- one statement is one transaction.
> - In addition, a `pre_delete` guard blocks deletion as long as an active **legal hold** is attached to the facility.
>
> **The one constellation that does cause damage:** a hand-written cleanup script that empties the domain tables **one after another** and **commits in between** -- for example a loop over several `…objects.filter(facility=f).delete()` calls in a shell without an enclosing transaction. The tables emptied first are then already committed when the run dies at the audit trigger: events, clients, or attachments are gone while the log and the facility remain. No one can repair that state from within the application; it requires a backup ([§ 3.3](#33-recovery)). Do not write such scripts -- and if you must, wrap them in **one** transaction.
>
> Another reason not to use `psql` for this purpose: a direct `DELETE` does not run Django's Python cascade at all. The `pre_delete` guards (legal hold) do not apply there, and the application's cleanup logic is skipped.
>
> If you really want to remove a facility entirely, the route is: archive it, let the retention periods expire (see [§ 7.3](#73-retention-periods-and-enforce_retention)), and then take a backup -- not the cascade.

**User offboarding.** Archiving blocks *access* but does not clear the identity data of departed staff (first/last name, display name, email, phone). That second half of the lifecycle is handled separately: #2051 -- offboarding cleanup for departed staff. Until then, deactivate the affected accounts under **Management > Access**; the username and identifier are deliberately retained so the attribution in the audit log stays readable.

### 2.3 Settings per Facility

Under **Management → Settings** (groups "General and convenience", "Security" and "Data protection and retention periods"), you can configure the following key fields, among others; the complete field list is shown directly in the settings form:

| Field | Default | Description |
|---|---|---|
| Full name | -- | Displayed in reports |
| Default documentation type | -- | Pre-selected type for new entries |
| Session timeout (minutes) | 30 | Automatic logout after inactivity |
| Maximum session duration (hours) | 12 | Upper bound from sign-in, regardless of activity: after this time the session always ends and a new login is required. Allowed range 1-168; must be **greater** than the session timeout (converted to minutes), otherwise the form rejects the input. A change only takes effect at the **next** login -- sessions already running keep the deadline set when they signed in |
| Retention anonymous (days) | 90 | Deletion period for anonymous contacts |
| Retention identified (days) | 365 | Deletion period for contacts of clients at the "Pseudonym only" level |
| Retention qualified (days) | 3650 | Period for events with additional details: from case closure, without a case from the event date, for an open case from last substantive case activity (fallback creation); proposal and approval before deletion (§ 7.3) |
| Retention handover notes (days) | 90 | Deletion period for the manual shift notes in the handover (#2042). Deliberately short: a duty note is not a case record. The run deletes them **permanently** (no trash) |
| 2FA enforced for all users | false | Enforces TOTP 2FA for **all** users of this facility (see [Section 2.7](#27-two-factor-authentication-2fa)) |
| Quick-exit target | `https://www.wikipedia.org/` | Address opened by the **Quick exit** button and the shortcuts `Ctrl`+`Shift`+`X` / `Shift`+`Esc`. Required field, `http`/`https` only. Pick an innocuous public page without a cookie banner -- it ends up in the device history afterwards, and a consent dialog costs exactly the seconds the action is meant to save. The target does not learn where the visitor came from (no `Referer`) |
| Privacy screen on window or tab change | false | Covers the content as soon as the window loses focus or the tab moves to the background. Released by click or key press, without a timer |

> **Recommendation for women's shelters, violence counselling and awareness teams (#2136):** documentation there happens in the presence of endangering persons or in loud, public places. Turn the **privacy screen on** and review the **target page** for your region -- it should fit the device to an outsider and must not read as an "escape page". The button itself is always active and cannot be switched off; the shortcuts are fixed. Both fields are recorded in the audit log with old and new values (`SETTINGS_CHANGE`).
>
> **Unsaved input is lost** when someone triggers the action -- the sign-out clears the local drafts as always (`Clear-Site-Data`). This is stated in the user guide and in the button tooltip.
>
> **Limits you have to flank organizationally** (in full in (DE: [threat-model.md](../threat-model.md)), § "Restrisiken des Mitschauen-Schutzes"): the action only replaces the **last** history entry -- earlier page views of the same session stay in the browser history with path and **page title**, and the title of a client page contains the **pseudonym**. Instruct the team to clear the history at the end of the shift or to work in a private window on shared devices. The button also acts per window; if the connection drops at the moment of triggering, the server session only ends via the session timeout or the maximum session duration; and the deletion of the local offline key is capped at 500 ms -- if it does not succeed, the key remains until the next sign-out, the idle wipe or the expiry of the 48-hour offline authorization. Therefore also: close second windows and do not leave the device unattended after quitting.

> **Baseline of a new facility (#1970).** Besides the settings record, creating a facility -- in the Django admin as well as via `setup_facility` -- produces a deliberately small starting stock that you are free to overwrite afterwards:
>
> | Stock | Maintained in the admin under | Effect when missing |
> |---|---|---|
> | Default catalog of documentation types | Management > Document types | **Blocking:** without an event type the type selector in "Document contact" stays empty and nothing can be documented |
> | Default shifts: early (08-16), late (16-22), night (22-08) | Anlaufstelle Core > Time filter (technical maintenance) | Convenience: the timeline then evaluates the full day instead of a shift window; handover view and live tally have no shift reference |
> | Quick templates "Kurzkontakt" and "Beratungskontakt" on the "Kontakt" type | Anlaufstelle Core > Quick templates | Convenience: recurring contacts are filled in by hand (see [Section 2.8](#28-quick-templates)) |
>
> **Once at creation, not on every save:** if a facility already carries its own documentation types, time filters, or quick templates at the moment it is created, that group is left **untouched** -- the check runs per group, and a single existing object is enough. Every later save of the facility in the admin leaves all three groups alone anyway: labels and orderings maintained in the admin are never silently reset to the shipped state, and deleted default shifts do not come back on their own. Pulling things in is explicit: `manage.py load_package --default` fetches the shipped catalog state (see [Section 2.2](#22-create-a-facility-and-application-support)); time filters and quick templates you create yourself under **Anlaufstelle Core > Time filter** and **Anlaufstelle Core > Quick templates**. One exception: running the initial setup `setup_facility` again with the **same** organization and facility names provisions whatever is empty at that point -- if you deleted every time filter, you get the three default shifts back; the command states for each stock whether it created it or left it alone.

> **Every facility has exactly one settings record.** The row is created automatically as soon as the facility is created -- by the interactive `setup_facility`, when adding it in the admin under **Anlaufstelle Core > Facilities**, and (retroactively, for existing data) on every `migrate` run. This is not a formality: `enforce_retention` and `create_statistics_snapshots` **skip** a facility without a settings record -- no retention period would be enforced for it and no statistics snapshots would be created. Both jobs log such a case at `WARNING` level (`No settings for ..., skipping.`); if that message shows up in the log it is an operational fault, not noise.

### 2.4 Create Additional Users

The regular path for an application support is **Management > Access > Create account** (`/verwaltung/zugaenge/`). Creating or changing an account requires confirmation in sudo mode. The list and all actions are limited to the manager's own facility; the `super_admin` role can neither be viewed nor assigned there ([ADR-048](../adr/048-fachhoheit-zugaenge-hub.md)).

- **Username:** Login name (no real names)
- **Email:** Required for the invite flow (see below)
- **Display name:** Name shown in the user interface
- **Role:** One of the four facility-bound roles (see below)
- **2FA required:** Requires a second factor for this individual account
- **Deletion confirmation:** Adds the account to the approver pool for deletion requests

Existing accounts can be edited, deactivated, or reactivated there. Application support cannot deactivate or demote their own account. The product UI does not permanently delete accounts: deactivation is the regular lifecycle; permanent deletion remains a reviewed technical exception. `super_admin`, bootstrap, and emergency work stay under **Anlaufstelle Core > User** in the Django admin; there, a `super_admin` can also toggle **2FA required** for these accounts; the field sits in the same form and is therefore also visible when an application support edits a regular account of their own facility (in addition to the Management > Access route). Each such change appears in the audit log as `USER_ACCESS_UPDATED` with `mfa_required` among the changed fields. Quick templates and time filters also deliberately remain technical admin functions.

#### Conflict when editing an account

If an account has changed since the form was opened, saving is refused. A missing or invalid form baseline is also refused. Your inputs remain in the form; the current security state appears above them, including role, active status, required 2FA, deletion approval and the other editable fields. Review this state and adjust your inputs if needed. Only deliberately pressing **Save** again confirms your inputs against that current baseline; another intervening change causes another conflict (#2547).

#### Token Invite Flow (Refs #528)

New accounts are created **without** a clear-text password. Instead, the application sends an **invitation email** containing a personalized setup link (`/invite/<uid>/<token>/`) to the email address on file. The link takes the new user to the same password-setting form as the password reset, where they set a password themselves.

1. The application support creates the account with an email address under **Management > Access**.
2. The system calls [`send_invite_email`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/invite.py) and generates a setup token (a dedicated `invite_token_generator`, based on `uidb64` + token hash).
3. The user receives the email, clicks the link, and sets a password. This does **not** log them in automatically -- the confirmation page leads to the sign-in form via "Zur Anmeldung" ("Go to sign-in"), where they sign in with the new password for the first time.

**Token validity (L4, Refs #1375):** Invitation and password-reset tokens have **separate** validity periods:

- **Invitations** expire after `INVITE_TOKEN_TIMEOUT` (default **3 days** = 259200 s, adjustable via the `DJANGO_INVITE_TOKEN_TIMEOUT` env var) — a newly invited staff member realistically needs days to open the link.
- **Password resets** expire after `PASSWORD_RESET_TIMEOUT` (default **2 hours**, deviating from Django's default of 3 days; adjustable via the `DJANGO_PASSWORD_RESET_TIMEOUT` env var) — a reset link is a strong recovery primitive and should be short-lived.

Both generators are additionally separated by their own `key_salt` (a reset token is not valid on `/invite/` and vice versa) and invalidate the token automatically once the user has set their first password.

**Resend the setup link:** If the email did not arrive or the token has expired, the application support can issue a fresh token from the access detail view via **Resend invitation**. The button is only shown while account setup is still pending. For `super_admin` accounts and technical exceptions, the system administrator instead selects the relevant accounts under **Anlaufstelle Core → User** in the Django admin and chooses the **Einladung erneut senden (Setup-Link)** action. The action only processes accounts that have an email address and whose setup is still pending; any initial password that may still be usable is revoked.

**SMTP requirements and troubleshooting:** None of these values is technically
required — without any set, the application in the Compose stack starts with an
empty `EMAIL_HOST`, port `587`, STARTTLS enabled (`EMAIL_USE_TLS=True`), and the sender
`noreply@anlaufstelle.app`. With an empty `EMAIL_HOST` every send fails, and
`/health/` reports `disabled` in the `smtp` field without lowering the overall
status -- monitoring that only evaluates `status` therefore does not notice the
missing configuration. Operationally, i.e. for actual delivery, operators
must point `EMAIL_HOST` at a reachable relay; adjust `EMAIL_PORT` and
`EMAIL_USE_TLS` only if they need to deviate from the default.
`EMAIL_USE_TLS=True` negotiates **STARTTLS** on the configured port. For
implicit TLS/SMTPS (classically port 465), operators set `EMAIL_USE_SSL='True'`,
`EMAIL_USE_TLS='False'`, and `EMAIL_PORT=465` instead; a relay that only offers
port 465 can therefore be connected. Both switches set to `True` at the same
time are ruled out: the application then aborts startup with a corresponding
message. If the relay requires authentication, `EMAIL_HOST_USER` and
`EMAIL_HOST_PASSWORD` are operationally required as well. `DEFAULT_FROM_EMAIL`
must use a sender domain authorized for SPF, DKIM, and DMARC. `EXTERNAL_ORIGIN`
must also be correct so that invitation and reset links point to the right
public instance. [Operations Runbook § 5.8](ops-runbook.md#58-checking-smtp-delivery)
contains the test command and log access.

If account creation or resending reports that the invitation could not be
sent, check the SMTP values and web logs, then use **Resend invitation**. A
success message only proves handoff to the mail backend. If the message still
does not arrive, also check the spam folder, quarantine, and the provider's
SPF/DKIM/DMARC alignment. With an authenticated session or the `X-Health-Token`
header, `/health/` ([§ 5.1](#51-health-endpoint)) also reports the `smtp` field, showing whether the
configured server is reachable over TCP (`ok`/`unreachable`) — this checks
reachability only, not authentication or actual delivery, and remains
invisible to anonymous callers. Until this is fixed, the initial-password
route below is available as a technical emergency fallback.

**Technical fallback without email:** The product UI always requires an email address. Only the Django admin can create a user **without** an email address and then displays a one-time clear-text initial password. This path is **insecure** and should only be used as a last resort -- better: add the email address and resend the invitation.

> **Note:** `must_change_password` is set on **every** account created in the admin. Once the user completes the invitation setup step -- or a password reset later on -- the flag is cleared automatically (#1969); they chose the password themselves in that step and are not asked to replace it again right at their first login. The forced change therefore remains exactly where it is needed: for the fallback without an email address, where the admin knows the initial password.

#### Role Descriptions

> **Audience of this section:** the **bootstrap/administration view** -- which role you assign when creating users, and which bootstrap rights come with it. The professional rationale is in the [domain concept (Role)](../fachkonzept-anlaufstelle.md#role--die-rolle) (German), the practical "who may do what?" view in [User Guide § 9](user-guide.md#9-roles-and-permissions), the technical details (DB values, RLS scoping) in [FAQ § C](../faq.md#die-fünf-rollen) ([English offline excerpt](faq.md)) (German).

Anlaufstelle has five roles -- four facility-bound, one facility-spanning:

| Role | Title | Scope | Permissions |
|---|---|---|---|
| `super_admin` | System administration | facility-spanning (`/system/`) | Bootstrap, create facilities, pre-auth audit logs, backup access. Created via `manage.py create_super_admin` -- **not** through the admin UI. Every `/system/` view access generates a `SYSTEM_VIEW` AuditLog entry. |
| `facility_admin` | Application support | one facility | Full access within their own facility: user management, type/sensitivity configuration, audit log (`/audit/`), GDPR package. **Cannot** view other facilities. |
| `lead` | Manager | one facility | Reports, approve deletion requests, view all cases in the facility, data export |
| `staff` | Social worker | one facility | Record and edit contacts, cases, and documentation |
| `assistant` | Assistant | one facility | Limited data entry; view people at either contact stage, additional fields only at effective sensitivity "Normal"; no internal person notes |

> **System ↔ application separation:** `super_admin` manages hosting and bootstrap; `facility_admin` manages configuration and users **within** a facility. This separation is GDPR-relevant: the super-admin cannot view the substantive content of a facility without this being logged in the AuditLog (`SYSTEM_VIEW`). Architecture decision: [ADR-018](../adr/018-rollenmodell-superadmin.md).

### 2.5 Configure Documentation Types

> **Starting point:** A facility created via `setup_facility` **or** in the Django admin under **Anlaufstelle Core > Facilities** ([§ 2.2](#22-create-a-facility-and-application-support)) already ships with the bundled **default catalog** -- it is ready for documentation without any further step. The list is empty only if the catalog was declined with `--no-default-package` during setup, or if the facility predates #1970. Either state persists until you change it explicitly -- saving the facility in the admin does **not** pull the catalog in (that would silently override the opt-out). In that case run `manage.py load_package --default --facility <uuid>` (the command including the role override is in [§ 2.2](#22-create-a-facility-and-application-support)). The structure, field reference, and maintenance rules of a configuration package -- including one of your own instead of the bundled one -- are described in [docs/packages.md](../packages.md) (German). The sections below describe how to adjust the resulting set.

Under **Management > Document types** (`/config/document-types/`), you define which kinds of documentation can be recorded in the facility. The matching **Anlaufstelle Core > Document types** view in the Django admin remains available as a technical fallback, but is not the regular configuration path. Each type consists of:

- **Name and description**
- **Field templates** (free-text fields, selection fields, etc.)
- **Retention period** in days (event types only; applies in addition to the contact-stage period if specified -- whichever period expires first applies, so a longer type period does not extend the contact-stage period)

> **Age groups are not a field-template setting.** A person's age group always uses exactly **Under 18, 18–26, 27+, and Unknown** throughout the installation. Neither this administration view nor a configuration package can change the boundaries or labels per facility. The similarly named `alterscluster` selection field from the default package belongs to individual events (including anonymous contacts); changing its options does **not** change person age groups or the built-in age-group statistics. See [ADR-047](../adr/047-altersgruppen-systemweit-fix.md) (German) for the rationale and the conditions for revisiting this decision.

#### Uniqueness of the Name

The pair `(facility, name)` is unique: within one facility, each documentation-type name can be used only **once**. Trying to create a second type with the same name is rejected as a form error.

> **Configuration packages** (YAML) may contain the `category` key: packages remain valid and loadable, but the value is ignored and the load report flags it as a warning. See [docs/packages.md](../packages.md) (German).

#### Sensitivity Level

The sensitivity level controls which roles can access entries of this type:

| Level | Access | Usage |
|-------|--------|-------|
| **Normal** | All roles (including assistants) | General contacts, services |
| **Elevated** | Social worker, lead, application support | Counseling sessions, medical care |
| **High** | Manager and application support | Crisis interventions, highly sensitive data |

#### Tally

Under **Management > Document types**, open the relevant type and select **Edit**. The **"Include in tally"** checkbox explicitly enables it for the tally in the Zeitstrom; the detail view then shows the saved state. New types are not enabled by default. The setting takes effect only for active **event** types without a minimum contact stage; for person or case types the option is hidden and cleared when the type is saved.

- If the type has no required fields, `+1` directly creates an anonymous contact.
- If every required field is a number, `+1` opens a compact value form; the entered values are stored with exactly one anonymous contact. Optional fields are not captured in this workflow.
- If the type has a required field of another kind (for example text, selection, date, yes/no, or file), it does not appear in the tally.

Enabling a type does not override visibility rules: it appears only to roles that may see the type and all of its required fields. Check the sensitivity levels of the assigned field templates before enabling a type.

#### System Type

The system type links a documentation type to internal application logic. It is set at creation and **cannot be changed afterwards**.

The system type serves **one** purpose: **UI logic**. Accordingly there are exactly two values:

| System Type | Effect |
|-------------|--------|
| **Ban** | Activates a ban banner on the person page, dedicated filter in the timeline, count and highlight in the handover; feeds the example ban overview |
| **Crisis** | Displayed as a highlight in the handover (recent crisis events) |

> **Note:** Not every documentation type needs a system type -- most do without one. Whether a type lands in a report is answered by the "Appears in …" information in the detail and form views; the system type has nothing to do with it.

> **The list of values is frozen** (RFC #1748, decision of 2026-08-03). **No new system types will be added:** a value belongs here only if it drives application behaviour. Report mappings go through the report key.

#### Report Key

The **report key** (`report_key`) decides which report a documentation type appears in -- and in which row. It is a select field of its own on the documentation type form; **field templates have one too** (see below).

The choices come from the reports themselves: what a report evaluates is declared in code, and exactly those keys are offered. So you choose **which key** your type carries -- not what a key maps to. Your own type "Social counseling" with the key "Counseling" lands in the same report row as "Debt counseling" with the same key.

| Report key | Row in the example activity report |
|-------------|---------------|
| **Contact** | Contacts |
| **Counseling** | Counseling |
| **Crisis** | Counseling |
| **Medical** | Care |
| **Needle Exchange** | Care |
| **Accompaniment** | Referral |
| **Referral** | Referral |
| **Violent incident** | Incidents |
| **Protective measure** | Protection |
| **Group activity** | Group activities |
| **Supply (need)** | Care |
| **Explicitly belongs in no report** | *(excluded -- and **not** a defect)* |
| *(empty)* | *(appears in no report -- and is reported as a gap)* |

> The keys **Violent incident**, **Protective measure**, **Group activity** and **Supply (need)** come from the bundled domain packages for awareness work, women's shelters and youth work (package overview #2133, German). They are offered to **every** facility, regardless of which package is loaded: the value range comes from the report, not from the catalogue. A new row in the example activity report only appears once a type carrying the matching key has events; the figures of existing reports do not change.

For a **partial report mapping**, the documentation type and field template edit forms warn about missing type or field keys on event types. For field templates, they mention only missing mappings from requirements involving this template; a fully satisfied local alternative still suppresses the notice. For example, the syringe exchange balance needs the active fields “Total issued” (`ausgabe_stueck`) **and** “Total returned” (`rueckgabe_stueck`) on the same active type. A different complete type does not remove the notice for this type. Alternative report requirements are evaluated separately; one fully satisfied alternative is sufficient for report availability and suppresses the notice for this type even when other groups remain incomplete. For types, “Appears in” lists available reports whose counting logic includes this type; for separate groups, the group must be configured and the type must have its required fields with an unambiguous active read path. Deactivated types remain listed insofar as they contribute to counting. The syringe exchange balance also counts incomplete types and sums their existing usable numbers; only events without a usable number count as “without values” there. The service and crisis type distribution, however, counts only events with usable selection values; a type lacking a required group field retains the missing-field notice without listing the distribution under “Appears in”. The missing-field warning remains for active incomplete types. For field templates, the actual read path determines the listing: an active assigned template appears in a report that reads its values for this type, including incomplete or deactivated types. The type detail page also shows warnings. When creating a type, candidate reports and missing mappings appear under separate headings. A missing type key is mentioned only for an empty assignment; a different assigned key, including “Explicitly belongs to no report”, is not a remediable gap for that type alternative. Notices do not block saving or change keys; a key that has already been set remains immutable.

> **The report key does not affect the overview.** The key figures, charts and tables on the "Overview" tab (contacts over time, documentation types, age groups) count the events in the period **regardless of their report key** -- including events that no report evaluates. A type set to "Explicitly belongs in no report" is therefore omitted from reports that assign data through the report key (such as the example activity report), but it remains visible and continues to count in the overview. The "External report" tab does not use the report key either.
>
> **Two details belong with that rule.** First, reports about **application behaviour** continue to bind through the system type rather than the report key: the example entry-ban overview therefore shows entry-ban events even if their type is set to "Explicitly belongs in no report" -- and that is exactly how the bundled default catalogue is configured ([ADR-042](../adr/042-report-key-berichtszuordnung.md), *Implementation E4*, German). Second, the overview normally reads completed months from stored monthly aggregates, while dataset reports may calculate live; that can also make their figures diverge ([User Guide § 7](user-guide.md#7-statistics-and-export)).
>
> The overview and a report may therefore show different figures for the same period without anything being misconfigured: the overview answers "What happened?", while the reports answer "What goes into which report?". To narrow the overview to one type, use the "All documentation types" dropdown above the charts.

The example activity report is a customizable example or template -- not a binding or current official proof.

> **Empty and "explicitly excluded" are two different things.** Under "Reports" the report states how many events it does *not* show. Counted there are **only** types that make no statement at all (key empty) -- a type set to "Explicitly belongs in no report" is a complete statement and does **not** appear in the notice (#1819). Bans and notes are not counted; a facility that documents entry bans therefore does not see the notice permanently without anything being misconfigured.
>
> There is a **third** case since the update: if a type carries a key that no report (any longer) knows -- from a foreign configuration package, or after a typo -- the report says so separately. "I did map it, nothing just happens" is a different finding from "I said nothing".

> **Adding later yes, changing no.** An empty report key can be **added at any time** -- that is the repair path when a type unintentionally appeared in no report. A **set** key is fixed afterwards (including "Explicitly belongs in no report"): reports about past periods must not retroactively produce different figures. When you add a key, the form points out that it affects reports generated in the future across **all** periods.
>
> When **creating** a type the form suggests matching values: system type "Crisis" suggests the key "Crisis", "Ban" suggests "Explicitly belongs in no report". Both are suggestions, not constraints -- system type and report key are allowed to diverge on purpose.

> **Set incorrectly? The repair path uses a new type.** A report key that has been set cannot be corrected -- not in the form, the Django admin or a configuration package. If the wrong value was selected accidentally, create a **new** documentation type with the correct key and set the old type to inactive through its **"Active"** field. Remember that names must be unique (see "Name uniqueness" above), including against **inactive** types: the new type therefore needs another name, or you must rename the old one first. The old type will no longer be offered for new events; existing events remain unchanged on it -- and therefore in the report row where they were counted before. Also remember the old type's **quick templates** (§ 2.8): deactivating the type does **not** remove them from the event-creation page, so deactivate them separately. **Deletion is not a substitute:** a type with existing events cannot be deleted. A reporting period spanning the changeover date will contain both types, so plan the change for a period boundary whenever possible.
>
> **Interim:** These steps currently have to be performed manually. A guided process is recorded as a proposal (#1888); it does not change the one-time nature of the key.

**Report keys on field templates.** Reports that evaluate **field values** (minute totals, needle-exchange balance, distribution of selection fields) additionally need to know **which field** to read. For that, the field template carries its own report key (e.g. "Minutes") and the field name is free -- a numeric field called "Session duration" instead of "Duration" still reports its minutes. The field template has no free-text field "Statistics category"; no evaluation would read it.


The **Sum of number fields** report uses the `quantity_units` key ("Quantity") for a number field on event types. A field already carrying `ausgabe_stueck` cannot be changed to `quantity_units`, because a set report key is immutable. Create a new field template with the `quantity_units` key; existing event values are not migrated by this.

> **At most one field template per report key and documentation type.** Two fields with the key "Minutes" on the same type would mean double-counted minutes. The assignment is therefore rejected -- as is setting a key later that is already taken on a shared type; if the duplication arises anyway (e.g. through a configuration package), the report counts **neither** field and says so. The same field template on **different** types is fine, as are two similarly named fields with **different** keys.
>
> **A deliberate gap:** If a **field template** carries a key that no report knows, the report does *not* say so separately -- unlike for a documentation type. The reason is that no meaningful figure could be given there: a field with an unknown key simply yields no values, and nobody knows how many there should have been. The state stays visible through "Appears in …" on the field template itself, which then reads "Does not appear in any report."

#### Check the Binding Inventory

The "Appears in …" information answers the question for **one** documentation type or **one** field template. For a facility-wide view -- which type and field template appear in which report, and which appear in none -- use the **`inventory_report_bindings`** maintenance command:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
  inventory_report_bindings --facility <uuid-or-name>
```

Add `--format json --output <file>` to write the same inventory in machine-readable form. The command **does not change your configuration** -- it only reads it.

For **documentation types**, it distinguishes the same four states as the reports' completeness notice (assigned · explicitly excluded · unknown key · no statement). Only types whose entity type is **event** are listed, because report keys do not affect person and case types (see "Minimum contact stage"). For **field templates** -- where there is no entity-type restriction -- it reports only *bound* or *unbound*: the deliberate gap described above also applies here, so an unknown field key simply appears as unbound. A field template is considered bound as soon as its key matches a report available to your facility, even if it has not yet been assigned to a documentation type.

This is particularly useful after creating many types manually or loading a third-party configuration package, and as a cross-check before releasing a report for the first time.

> ⚠️ **Role requirement.** Like the other maintenance commands (`load_package`, `purge_orphan_field_data`, `reencrypt_fields`), this command must run with the **admin DB role** -- following the same pattern as [Ops Runbook § 3.5](ops-runbook.md#35-manual-execution); the roles themselves are explained by the database role model in [Ops Runbook § 9](ops-runbook.md#9-row-level-security-rls). Without that role it aborts with a clear message instead of producing an empty report: under the runtime role, a facility with no visible rows would look perfectly configured because of Row Level Security.

#### Minimum Contact Stage

Defines the minimum contact stage a person must have for an event of this type to be created. Example: Counseling sessions require at least "With additional details" because additional personal details about the person must be on record.

> **Effective for event types only (Refs #1058).** **Minimum contact stage**, **maximum period (shortens only), `retention_days`**, **system type** and **report key** only take effect on documentation types whose entity type is **event**: the contact-stage check only applies when creating events, and the retention run deletes events only. For **persons**, the facility-wide rules from the settings apply instead: the trash period (`client_trash_days`) and anonymisation once event retention has cleared out a person's records. **Cases have no retention period of their own** — the retention run never deletes a case by itself. It only acts on a case that has already been **deleted**: its trash period then expires after `client_trash_days` and the case is anonymised (Refs #1818, [ADR-044](../adr/044-fall-loeschpfad.md), which supersedes the earlier promise in [ADR-039](../adr/039-owner-generische-dateianhaenge.md) § 6). Without a deletion, a case's contents are still only redacted when the associated person is anonymised. `retention_qualified_days` counts from closure for a closed case, from last substantive case activity (fallback creation) for an open case, and from the event date without a case; events are deleted after proposal and approval, while the case remains (§ 7.3).
>
> The form **hides these four fields** as soon as you pick **person** or **case** as the entity type (Refs #1757) — so you no longer fill in fields that never apply. Hiding is purely visual: values already set stay unchanged on the record, and a field carrying an error message stays visible. Without JavaScript all fields remain visible and keep their explanatory help text.

#### Field-Level Sensitivity (`FieldTemplate.sensitivity`)

In addition to the document type's sensitivity, each individual field of a field template can receive its **own** sensitivity level (`FieldTemplate.sensitivity`). For field visibility the **maximum** of the document-type and field sensitivity applies -- a field flagged `HIGH` stays invisible to staff even if the document type itself is only `NORMAL`.

**Encryption and visibility have separate effects** (Refs #356): `is_encrypted` controls at-rest encryption of the field value, while `sensitivity` controls UI visibility; however, a field template with its own **High** level enforces encryption:

- A field may be stored encrypted on disk while still being visible in the UI at `NORMAL` sensitivity (e.g. contact data that all roles are allowed to see but that should not be stored in clear text).
- A non-encrypted field can still be restricted to Manager/Admin (e.g. statistical markers that are sensitive, but do not need to be stored encrypted).

#### Protection display and sensitivity changes

In a documentation type's **field board**, every field row shows the effective level, permitted roles, **Encrypted** or **Plaintext**, and **In search index** or **Not in search index**. Inherited fields show **same as type**; a higher field level shows **Field template:** with its own level, and the detail page counts fields with higher levels. The field-template list shows each template's own level and storage mode, plus its index status for each assigned type; without an assignment there is no search index.

In the field board, groups and fields can also be sorted with the keyboard using **Up** and **Down**. For fields, **Move to** selects the target group or **No group**; **Move** confirms the selection. After a successful change, focus returns to the control and the new position is announced. Dragging remains available.

A **High** type may not contain an inheriting field template: each assignment requires an explicit field level. Configuration and the package loader reject inheritance in High types. In the shipped packages, reporting fields in High types receive the **Elevated** field level and remain unencrypted for statistics; other fields receive **High** and are encrypted. A high **type level** alone does not encrypt every field value.

When a changed type level or changed field level/encryption setting is saved, or a field template is assigned or reassigned, affected events, clients and cases in the same facility are rewritten, including records in the trash. This applies to the administration forms, the normal model save path in Django admin and the package loader. When writing dynamic values or their search index, supported model and bulk paths within the limits described below hold shared protection locks through persistence; protection changes take exclusive locks and decide from the locked database state. Configuration, field encryption and the event search index change in **one transaction**; a failed rewrite leaves no partial transition. A shared field template affects all assigned types within the facility. Encrypted fields also store **No (`False`)** and **0** as ciphertext; newly encrypted booleans and integers retain their types after decryption. Merely lowering sensitivity does not decrypt existing ciphertext; if the template's encryption remains enabled, new values remain encrypted as well.

**Limits of model saves:** For events, clients and cases, `save(update_fields=…)` raises `ValueError` for a call containing `document_type` or `document_type_id` without `data_json`; events must also include `search_text`. Rejection applies even to empty payloads or file markers alone and happens before persistence. A full `save()` on an instance loaded without deferred fields or a partial call with all required columns remains available. **Instances with deferred fields (`defer`/`only`) raise `ValueError` on `save()` without explicit `update_fields`, before any field access or persistence.** Reload the complete instance or explicitly list the fields to persist; type changes still require the columns listed above. Event, client and case managers reject `bulk_create(update_conflicts=True, …)` with `ValueError`, even with complete `update_fields`, positional options or empty batches. Insert-only bulk creation remains available; `ignore_conflicts=True` does not update an existing row. The protected `bulk_update` path also remains available. Custom writes using raw SQL or `QuerySet.update()` on the type, field values or search text bypass model hooks and are outside this protection guarantee. Bulk configuration changes (`DocumentType`, `FieldTemplate` or their assignments via `QuerySet.update()`, `bulk_*` or SQL) bypass the High-type rule, reencryption and index rebuilding of individual saves. Change configuration only through administration or the package loader; after such bulk changes, with RLS bypass first inspect `python manage.py reencrypt_fields --dry-run` and `python manage.py rebuild_search_text --dry-run`, then run `python manage.py reencrypt_fields` and `python manage.py rebuild_search_text`. These maintenance commands do not replace validation of the High-type rule.

Documentation types, field templates, field assignments, field groups, field conditions and quick templates also reject implicit `save()` on instances partially loaded through `defer`/`only`, before the first field reload; fully loaded instances or explicit `update_fields` remain supported.

Event indices maintained through supported application write paths contain only unencrypted fields whose effective level is **Normal** after combining type **and** field sensitivity. Changing to Elevated/High through these application paths removes affected field values from the index, even when they remain stored as plaintext for reporting. Pseudonym search remains available within role permissions.

New before/after versions are protected according to current field encryption when inserted. Older stored versions continue to be encrypted separately with **`reencrypt_fields`**, using encrypted field templates in the same facility or the encryption status saved in history metadata, including fields that are no longer assigned. Redaction markers and file metadata are preserved; `--dry-run` writes nothing. The command can be repeated without double-encrypting values; existing ciphertext continues to rotate. Use the admin database role as described in § 2.9 "Key Rotation".

**Rebuild search text separately:** `rebuild_search_text` writes only the derived event index, leaving field values, timestamps and history intact. Without `--facility` it processes all facilities; `--facility <UUID>` selects one facility, and `--dry-run` reports the number of changes without writing. Another write run reports no index changes if source data has not changed. Like field rotation, the command requires an RLS bypass context; use the maintenance service in production:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance rebuild_search_text --dry-run
docker compose -f docker-compose.prod.yml run --rm maintenance rebuild_search_text --facility <UUID>
```

#### Deletion Protection for Existing Data

Fields that already have values stored in events, clients or cases **cannot simply be deleted**. Attempting to remove a field template triggers a protection mechanism that checks for stored values. If the field really has to go, a data migration must run first to clean up the values or move them into another field.

> **Pragmatic alternative:** Rather than deleting, disable the field in the document type mapping -- existing data is preserved, new events/clients/cases will no longer be offered the field.

##### Orphaned Field Values ("No longer assigned")

If only the **assignment** of a field template to a document type is removed (rather than the field template itself being deleted), the values already captured remain stored under their slug in the records (`data_json`). Such values are considered **orphaned**: for clients, cases and events they are shown in a separate **"No longer assigned fields"** section in the detail view and are listed in a dedicated section of the GDPR data access export (Art. 15) -- so they do not disappear unnoticed. The visibility and encryption of an orphaned value still follow its field template (which is kept alive by the deletion protection as long as data exists under its slug): a field once classified as "high"/"elevated" remains restricted in visibility even when orphaned.

To finally **remove** orphaned values (data minimisation, once they are no longer needed), use the maintenance command **`purge_orphan_field_data`**. It removes, from clients, cases and events, exactly those `data_json` keys whose slug no longer belongs to the current field assignment of their type; assigned values (including encrypted ones) are left untouched, and records without a document type are only counted. `--dry-run` shows the effect without writing. Like the other maintenance commands (e.g. `reencrypt_fields`, § 2.9), it must run with the **admin DB role** and otherwise aborts with a clear message (Row Level Security). Execution: see `docs/ops-runbook.md` § 3.5.

**Changing the type of a used field template:** Once the field slug occurs in stored client, case or event data (including trash), an event history snapshot or a quick template, only a compatible change between **Text** and **Textarea** is allowed. Multiline or unreadable textarea values cannot be narrowed to single-line text. This also applies to encrypted values, file markers and stored empty values. Other type changes are rejected without changing existing values. Deactivate the old template and create a new one instead. Without such usage, the field type remains editable; package updates keep their stricter rule that field types are immutable.

**Activation and report keys:** Each documentation type may have at most one active field template for the same report key. Reactivating a replaced template is rejected on conflict; the message names the documentation type and competing template. Administrative bulk activation also checks all selected templates, including conflicts within the selection, before activating any of them. Deactivation and conflict-free reactivation remain available.

**Deactivated field templates:** Inactive templates are not offered as inputs in event, client or case forms or in offline form definitions. Normal edits preserve their stored values unchanged, including encrypted values and file markers; existing visibility rules continue to apply. After conflict-free reactivation, the field becomes editable again. Conditions with an inactive source or target are omitted from the input form. If all conditions on an active required field disappear, that field becomes unconditionally required again; an inactive required field does not block saving. Reports count only active fields; values of deactivated fields remain in the record, disclosure and export, and are counted in reports again after conflict-free reactivation. Approved rectifications can also correct inactive client fields; the request and audit are marked as approved only after the actual stored target value has been checked.

### 2.6 Manage Selection Options (Field Templates)

Under **Management → Field templates**, you can edit the options of selection and multi-selection fields (Select / Multi-Select). Options are stored in the **Options (JSON)** field as a JSON array.

**Slug format:** Option slugs use lowercase letters `a–z`, digits `0–9`, and single hyphens between character groups, for example `beratung`, `18-26`, or `beratung-2`. Invalid examples are `-beratung` (leading hyphen), `beratung--hilfe` (double hyphens), and `beratung-` (trailing hyphen). Option slugs must be unique within a field. Documentation types and field templates get their slugs from their names automatically; field groups do as well. Umlauts become `ae`, `oe`, `ue`, and `ss`, underscores become hyphens, repeated hyphens collapse, and slugs are limited to 120 characters. A suffix such as `-2` is added on collision. Names that produce no slug are rejected; names made only of digits, such as `18-26`, are valid. Field templates also cannot use reserved slugs for fixed form fields. Documentation type and field template slugs are unique per facility; field group slugs are unique per documentation type. See the [package documentation](../packages.md) for details of the package format.

You can enter option slugs directly in the options editor. Use only `a–z`, `0–9`, and single hyphens.

**Encrypted selection fields:** For newly saved multiple selections, the list of option slugs survives encryption, reading and saving again. Previously selected inactive options remain checked in the edit forms for clients, cases and events and can be saved unchanged; unselected inactive options are not offered there. Encryption does not change role-based visibility.

Detail views show each multiple-selection label on its own line. The event CSV export uses comma-separated labels; the Article 15 PDF disclosure also shows labels, while event JSON in the Article 20 export contains an actual list of option slugs. Unknown slugs without a matching label remain readable as raw values. Existing text tokens are not reinterpreted as lists; no conversion of old data takes place. Refs #2520.

> **Applies only to Select / Multi-Select.** For other field types (Text, Textarea, Number, Date, Time, Yes/No, File), `options_json` is not evaluated -- please leave the field empty (`[]`).

#### Default Values (`default_value`)

In the **Default value** field of a field template, you can store a default that is pre-filled when **creating** a new event. When **editing** an existing event, the stored value always takes precedence.

| Field type | Format | Example |
|---|---|---|
| Text / Textarea | any string | `Standard note` |
| Number | integer | `15` |
| Date | ISO format `YYYY-MM-DD` | `2026-01-01` |
| Time | ISO format `HH:MM` or `HH:MM:SS` | `09:30` |
| Yes/No | `true` or `false` | `true` |
| Select | slug of an active option | `beratung` |
| Multi-Select | comma-separated list of active option slugs | `beratung, essen` |
| File | not supported | -- |

Precedence on create: **Quick template > Default value > empty**. Invalid values are rejected by `FieldTemplate.clean()` when saved in the admin.

**Schema of an option:**

```json
[
  {"slug": "beratung", "label": "Beratung", "is_active": true},
  {"slug": "essen", "label": "Essen", "is_active": true}
]
```

| Field | Description |
|---|---|
| `slug` | Technical identifier; unique within this field |
| `label` | Display name in forms and exports |
| `is_active` | `true` = selectable, `false` = deactivated |

Each option object may contain only `slug`, `label`, and the optional `is_active`; unknown keys are rejected.

#### Deactivate Options Instead of Deleting Them

When an option is no longer needed, set `is_active` to `false` instead of removing the option:

```json
{"slug": "sachspenden", "label": "Sachspenden", "is_active": false}
```

**Effects of deactivation:**

| Area | Behavior |
|---|---|
| Creating a new event | Option is **not** offered |
| Editing an existing event | Option remains visible with a "(deactivated)" marker; the value is preserved |
| CSV export | Label continues to be resolved correctly |
| Statistics | Existing values continue to be included in reports |

> **Important:** If an option slug is used in readable stored values including trash and event history, field conditions, quick templates, or a default that remains valid, the configuration form, model save, Django admin, and package loader reject renaming or removing it. Set `"is_active": false` instead; you can still change its label. Unused option slugs can be removed. Event, person, and case writes including the protected model and bulk paths, quick templates through model save, and field conditions through configuration services and model save check new selection references against current options under a shared configuration lock; unchanged existing selection values remain saveable. Direct `QuerySet.update` and bulk writes for quick templates and field conditions, and arbitrary SQL updates, are outside this protection; corrupted encrypted values cannot be counted as usage.

#### Numeric Range Bounds (`Minimum Value`/`Maximum Value`)

Field templates of type **Number** can carry optional bounds at the same location (**Management → Field templates**): **Minimum Value** and **Maximum Value**. They apply **only to future writes** -- creating, editing, and saving through the form or the service layer. Tightening a bound never rewrites an already-stored value: existing data outside the new bound stays unchanged in the database until it is corrected on its merits.

> **Before tightening a bound: run the read-only inventory.** In the `maintenance` service (admin DB role):
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance inventory_number_ranges --facility <UUID-or-name>
> ```
>
> The command changes nothing and lists, per finding, only the entity kind (clients/cases/events), the document type, the field slug, and a count -- never the stored value itself. "No values outside the configured numeric ranges found" means the field's existing data already fits the planned bound, so tightening it is safe.

**What the inventory reports:**

| Message | Meaning |
|---|---|
| `unter Minimalwert X` / `über Maximalwert X` (below minimum / above maximum) | A stored value falls outside the planned bound. |
| `keine Zahl` (not a number) | A combined finding for three distinguishable but deliberately identically reported cases: an empty **required** value, a non-numeric stored value, or a decryption failure (corrupted/foreign ciphertext). A decryption failure is **always** reported, even on an optional field. |
| *(no entry)* | An empty value on an **optional** field is not a finding -- "no value" is a valid state there, not a range violation. |

A finding is not a repair order for the inventory command itself: it only counts, it never writes. Correct flagged records on their merits -- open the affected record and enter a valid value, or clear an optional field -- before tightening the bound.

#### Save-Blocking Behavior for Existing Data

Once a bound is tightened, the central validator checks **every** write -- including editing a record whose numeric field was never touched: a form submission always carries all of its current field values on save, not only the ones that actually changed. If the stored value falls outside the new bound, the form rejects it like any regular input error ("Ensure this value is greater than or equal to X." / "Ensure this value is less than or equal to X.", where X is the configured minimum/maximum) and the **entire** save is refused -- even if the intended change targeted a completely different field. That is the validator working as designed, not a bug: the only way past it is to correct the offending value (or, if the field is optional, clear it). Only the individual blocked record is affected -- other records carrying the same field remain independently editable.

#### Safely Retracting a Bound

If a bound turns out too tight, retract it at the same location: loosen or clear **Minimum Value**/**Maximum Value** on the field template. That takes effect immediately for future writes; previously blocked records become saveable again as soon as their stored value fits the (now wider) bound -- without anyone having to change a value after the fact. If the bound is instead maintained through a configuration package, a separate tri-state rule applies there (an omitted key leaves an existing bound untouched; only an explicit `null` removes it) -- see [docs/packages.md](../packages.md).

### 2.6b Fuzzy Search (pg_trgm)

In addition to exact substring matching, the global person (pseudonym) search uses a **trigram-based fuzzy search** -- tolerant of typos and phonetic variants (e.g. "Schmidt" ↔ "Schmitt"). Implementation: [`src/core/services/dashboard/search.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/search.py), function `search_similar_clients`.

**Per-facility threshold:** Under **Management → Settings** (group "General and convenience") > field **"Fuzzy search threshold"** (`Settings.search_trigram_threshold`):

| Range | Default | Effect |
|---|---|---|
| `0.0`--`1.0` | `0.3` | Minimum similarity for a match. Lower values yield **more** but **less precise** matches; higher values are stricter. |

**Recommendation:** If there are too many false positives, raise the threshold incrementally (0.35, 0.4); if there are too few matches, lower it (0.25, 0.2).

#### Prerequisite: `pg_trgm` Extension

The PostgreSQL extension `pg_trgm` must be enabled. The standard deployment sets it up automatically via a Django migration. If it is missing (e.g. after a manual DB restore), enable it manually:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

References: #536, #581.

### 2.7 Two-Factor Authentication (2FA)

**Audit (#2527).** Successful TOTP and passkey verification records `MFA_VERIFIED` with the fixed method `totp` or `passkey`; failed factor checks retain `MFA_FAILED` or `WEBAUTHN_FAILED`. Consuming a backup code retains exactly its existing `BACKUP_CODES_USED` row. For signed-in accounts, a POST request to regenerate backup codes rejected for invalid TOTP, an unverified session or a missing TOTP device records `MFA_BACKUP_REGENERATE_DENIED` with the fixed reason `invalid_totp`, `unverified_session` or `missing_totp`. These audit details contain no codes, TOTP secrets or passkey assertions.

Anlaufstelle supports TOTP-based 2FA via [`django-otp`](https://django-otp-official.readthedocs.io/), complemented by passkeys and backup codes. Every user can enable 2FA themselves (`/mfa/settings/`); in addition, there are three enforcement levels.

**User-facing documentation:** [User Guide § 1 -- Two-Factor Authentication](user-guide.md#two-factor-authentication-2fa).

#### Configure Enforcement

| Level | Field | Admin location | Effect |
|---|---|---|---|
| **Single user** | `User.mfa_required` | Management > Access > *Account* > "2FA erforderlich" (2FA required) field | 2FA mandatory immediately for this user, disabling is blocked |
| **Facility-wide** | `Settings.mfa_enforced_facility_wide` | Management > Settings (`/verwaltung/einstellungen/`) > "2FA-Pflicht für alle Nutzer" (2FA mandatory for all users) field | 2FA mandatory immediately for **all** users of this facility |
| **Role default** | `MFA_ENFORCE_PRIVILEGED_ROLES` (settings module) + `MFA_ENFORCED_ROLES_IMMEDIATE` / `MFA_ENFORCED_ROLES_GRACE` (environment variables) | Deployment | 2FA mandatory by role alone -- enabled by default in production for all five roles |

Evaluation happens via [`User.is_mfa_enforced`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py) -- a user is considered enforced if **any** level applies. The MFA middleware blocks access to protected areas until the second factor (TOTP code, passkey or backup code) has been verified in the session.

**Configure the role default via environment variables** (#1639). Independently of single-user and facility-wide enforcement, the application already requires 2FA in production **by role alone**. The switch `MFA_ENFORCE_PRIVILEGED_ROLES` is not an environment variable but a value of the settings module: `True` in `base.py` and therefore in production, `False` in the development and demo settings, controllable via `E2E_MFA_ENFORCE_ROLES` in the E2E settings. Which roles it covers is controlled by two settings that can be overridden per env as a **comma-separated list**; `docker-compose.prod.yml` passes both to the `web` service (see [§ 1, environment variable reference](#full-environment-variable-reference)):

| Setting | Default | Effect |
|---|---|---|
| `MFA_ENFORCED_ROLES_IMMEDIATE` | `super_admin,facility_admin` | 2FA required **immediately** (no grace) -- without a second factor, an immediate redirect to `/mfa/setup/` |
| `MFA_ENFORCED_ROLES_GRACE` | `lead,staff,assistant` | 2FA required, but only **after a 30-day transition period** from the first sign-in (reminder banner from 14 days remaining); afterwards every sign-in without a second factor leads to `/mfa/setup/` |

The **Manager** role (`lead`) is mandatory by default (in the grace set) -- if staff/assistant are enforced, so is the hierarchically higher lead. Valid role names are `super_admin`, `facility_admin`, `lead`, `staff`, `assistant`; an **empty** list deliberately disables that level. An **unknown role name** or an **overlap** between the two lists aborts startup fail-closed with `ImproperlyConfigured` (no silent misconfiguration).

#### Reset 2FA for a User

If a person has lost their authenticator device and has no backup codes left, the application support or the system administration resets their second factor in the application (#2469):

- **Application support (`facility_admin`):** **Management > Access** > *Account* > under "Sicherheitsaktionen" (security actions) **Zweiten Faktor zurücksetzen** (reset second factor) (`/verwaltung/zugaenge/<id>/zweiter-faktor/`). The confirmation page lists the consequences; only submitting it performs the reset. The action is shown only while the account still has a factor.
- **System administration (`super_admin`):** Django admin (`/admin-mgmt/`) > **Anlaufstelle Core > User** > select accounts > action **Zweiten Faktor zurücksetzen (Authenticator-App, Passkeys, Backup-Codes)**. Every selected account is checked individually; refused accounts are reported and skipped.

Rules (in [`src/core/services/user_access.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/user_access.py), `reset_user_mfa`):

- **Sudo required:** Both paths require an open sudo window; without sudo the request first leads to the sudo page (password, plus a code if the acting account has a TOTP device) and then back to the confirmation.
- **Facility boundary:** An application support reaches only accounts of their own facility; an account of another facility cannot be found for them. The system administration reaches every account.
- **Role hierarchy:** An application support does not reset a `super_admin` account. The own account cannot be reset this way -- disabling under `/mfa/settings/` with its own rules applies there.
- **Effect:** All TOTP devices (including unconfirmed ones), all passkeys and all backup codes of the account are removed, and all sessions of the account end with their next request -- independently of `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT`, because the stored password hash is rewritten and Django signs out every session bound to the old hash. The password stays valid. If the sessions cannot be ended safely, nothing is removed and nothing is recorded.
- **Setting up again:** If 2FA is mandatory for the account, the next sign-in leads to `/mfa/setup/` -- immediately for single-user, facility-wide and immediate role enforcement, and for role enforcement with a transition period only once that period has expired. Without an obligation the person signs in with the password only until they set up 2FA again themselves.
- **Audit:** Every reset writes exactly one `MFA_RESET_BY_ADMIN` entry ("2FA durch Administration zurückgesetzt") with the target account (user and target fields), the acting person, the facility and the source IP. The detail holds the target account's username (`username`), the acting person's ID and username (`actor_id`, `actor_username`) and the number of removed TOTP devices, passkeys and backup codes (`totp_devices_removed`, `passkeys_removed`, `backup_codes_removed`), no key material. If the account had no factor left, no entry is written.

Reset the second factor only when the request demonstrably comes from the person themselves.

There are also **backup codes as a second factor** for exactly this recovery case (Refs #588). On 2FA setup the user receives 10 single-use codes that should be printed or stored in a password manager. The user must then confirm through the required checkbox that the codes have been stored securely; only then is the TOTP device confirmed and setup complete. Leaving the page first sends the next attempt back to `/mfa/setup/`. At the 2FA login prompt the user can enter a backup code instead of a TOTP code. Used codes are invalidated and recorded in the AuditLog (`BACKUP_CODES_USED`). If all 10 codes have been spent or lost, the reset by the administration above remains the fallback.

`django-otp` increasingly throttles TOTP and backup-code checks after multiple failed attempts. A correct code can also be rejected during the resulting waiting period; before resetting the device, wait for the lockout interval instead of immediately trying more codes.

**Changing the TOTP device** (#2529): Choose **Neues Gerät einrichten** (set up a new device) at `/mfa/settings/` (`/mfa/change-device/`). This is available even when 2FA is mandatory. The session must already be verified with the existing second factor; without an open sudo window, confirm again using the password and a current TOTP or unused backup code (`SUDO_MODE_TTL_SECONDS`, default 15 minutes). Scan the new QR code, confirm its TOTP code, securely store the new backup codes and acknowledge them. Only acknowledgement removes all previously confirmed TOTP devices and binds the session to the new device; existing passkeys remain. Completion records `MFA_ENABLED` with the number of replaced TOTP devices. A backup-code sign-in alone does not change devices. For voluntary **2FA deaktivieren** (disable 2FA), sudo now returns to a confirmation page; only its POST removes the factor. First-time setup still requires no sudo step; adding a passkey remains sudo-protected.

**Backup-code hardening:** Ten codes with 128 bits of randomness each are stored as 96-bit HMAC prefixes; the purpose-separated key derives from `SECRET_KEY`. Previously issued codes (including the earlier modern format with SHA digests) are invalid. Generate and securely store new codes using a current TOTP before losing the device; without a usable factor, the admin reset remains the fallback. Rotating `SECRET_KEY` also invalidates the codes. Anonymous failures at `/account/recovery/backup-code/` do not increase the device throttle; instead, limits are 5/min per IP and 10/h per normalized username. Authenticated device throttling remains in place.

**Session termination limit:** The scan checks at most `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT` (default 10,000) unexpired sessions across the installation. After a lock or role change, sessions not found may survive until inactivity expiry or the absolute session maximum; deactivation and an invalidated initial password apply on the next request, while the admin MFA reset is independent of the scan. A lock does not rotate the password hash, preserving password-reset and invitation links. A user-to-session index is an idea for after 1.0, not an existing feature.

#### Account Lockout

The lockout ([`src/core/services/security/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/login_lockout.py)) follows these rules:

- **Threshold and window:** **10 failed sign-ins within a sliding 15-minute window** lock the account. Attempts are counted per pair of account and source IP: failed attempts from another IP address do not lock sign-in from the person's own.
- **End:** The lock holds as long as at least 10 counted failed attempts of the account from this source IP lie within the last 15 minutes, and ends by itself once there are fewer. That can be much earlier than 15 minutes after the last failed attempt: if ten failed attempts are spread over nine minutes, the lock ends 15 minutes after the first, i.e. six minutes after the last. Without new counted failures it ends at the latest 15 minutes after the last counted one. The `LOGIN_FAILED` entries are counted -- a wrong password and, during the lock, also the correct one (`reason: "locked"`); such attempts keep the lock in place. Requests rejected by the rate limit (HTTP 429) do not reach the check and do not count. Failed attempts before an unlock (`LOGIN_UNLOCK`) no longer count.
- **Display and log:** Anyone who enters the correct password during the lock stays on the login page and sees the form error "Ihr Konto ist nach mehreren fehlgeschlagenen Versuchen temporär gesperrt. …" (your account is temporarily locked); this is recorded as `LOGIN_FAILED` with `reason: "locked"`. At the same time the account's offline key becomes invalid, and the application ends the sessions of the account that it finds in the session table. This search is limited to `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT` unexpired sessions of the whole installation (default 10,000). A session of the account beyond that limit stays valid until it ends through inactivity (session timeout) or the maximum session duration; the log reports the abort at level ERROR. There is no separate lock action in the AuditLog.
- **Rate limits (independent of the lock):** Sign-in accepts at most **5 attempts per minute per IP address** and **10 attempts per hour per username** ([`src/core/views/auth.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py), `CustomLoginView`); every submitted sign-in counts, including a successful one. The time windows are fixed, not sliding: the hourly window ends at a fixed point in time, not one hour after the first attempt. Beyond the limit sign-in responds with HTTP 429 until the time window has passed; no unlock lifts a rate limit. A 429 response writes no audit entry and does not count as a failed attempt for the lock. If ten failed attempts fall into the same hourly window, every sign-in with this username then gets the 429 response until the window ends. Whether the lock's form error appears afterwards depends solely on whether ten counted failed attempts still lie within the last 15 minutes; if the hourly window ends more than 15 minutes after the last counted failed attempt, the lock is already over.
- **`super_admin` exemption:** This role is never locked (the break-glass account must not be lockable deliberately); only the rate limits protect against brute force there. An unlock has no effect for it and writes no audit entry.

The recovery paths below therefore apply to facility-bound accounts:

**Path A -- UI (for facility-bound users):** The application support (`facility_admin`) of the facility can unlock the accounts of their own users:

1. **Management > Access** -- select the affected user in their own facility.
2. Under "Security actions" click **Unlock account**.
3. The unlock is recorded in the AuditLog as `LOGIN_UNLOCK` (the `LOGIN_FAILED` log itself is immutable thanks to the `auditlog_immutable` DB trigger).

The action requires an open sudo window. The system administration unlocks across facilities under **System > Sperrkonten** (locked accounts, `/system/lockouts/`, also with sudo). In the Django admin (**Anlaufstelle Core > User**) the same unlock remains available only as the list action "Account-Sperre aufheben (Login freigeben)" as a fallback.

**Path B -- CLI (for recovery):** If no other user has access to Path A (a locked `facility_admin` with no second `facility_admin`), the hosting operator unlocks the account directly on the server:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
  unlock <username>
```

The command works for the facility-bound roles (`facility_admin`, `lead`, `staff`, `assistant`) and writes a `LOGIN_UNLOCK` AuditLog entry. For a `super_admin` it has no effect (success message without an audit entry), because that role is never locked.

**Path C -- Self-service via email (Refs #869):** If the user has an email address on file, they can unlock themselves from the login page via the "Unlock account via email" or "Forgot password?" links -- both paths write a `LOGIN_UNLOCK` AuditLog entry. The dedicated unlock link at `/account/recovery/` sends a signed token valid for 30 minutes (no new model; `TimestampSigner` with salt `lockout-recovery.v1`). Anti-enumeration: the confirmation page is identical whether or not the email belongs to an account.

**Path D -- Self-service via MFA backup code:** If the user has 2FA enabled and has noted one of their backup codes, they can unlock themselves at `/account/recovery/backup-code/` using their username and code. Audit trail: `LOGIN_UNLOCK` (trigger `backup_code`) + `BACKUP_CODES_USED` (flow `lockout_recovery`). Each code is single-use -- the next recovery event consumes the next code.

The AuditLog holds every checked failed attempt (`LOGIN_FAILED`, with the correct password during the lock additionally `reason: "locked"`) and every unlock (`LOGIN_UNLOCK`). The start of the lock follows from the `LOGIN_FAILED` entries and does not write an entry of its own. Requests rejected by the rate limit (HTTP 429) receive a throttled `RATE_LIMITED` trail, including during the lock. Use the "Login failed" / "Account unlocked" filter for retrospective analysis. The `LOGIN_UNLOCK` detail block contains a `trigger` field: `admin` (Path A, System > Sperrkonten, the Django admin action and the CLI command from Path B -- the CLI does not write a value of its own), `password_reset` (Path C via standard reset), `invite_setup` (initial setup via the invitation link), `recovery_token` (Path C via dedicated link), `backup_code` (Path D); unlocks via password reset, invitation link, recovery token or backup code also record the source IP on the audit entry.

#### Audit Trail

These 2FA and sudo events each write one entry to the `AuditLog`:

- `MFA_ENABLED` -- completion of the TOTP setup with the acknowledgement of the backup codes (`count` = number of TOTP devices replaced in the process).
- `MFA_DISABLED` -- disabling at `/mfa/settings/` when a TOTP device or passkey was removed.
- `MFA_RESET_BY_ADMIN` -- reset by the administration (see above).
- `MFA_FAILED` -- wrong TOTP or backup code at the 2FA sign-in.
- `MFA_VERIFIED` -- successful TOTP or passkey check at the 2FA sign-in, with the method and no secrets.
- `MFA_BACKUP_REGENERATE_DENIED` -- backup-code regeneration refused because the session is unverified, the TOTP device is missing or the TOTP code is invalid.
- `BACKUP_CODES_GENERATED` -- backup codes generated during setup; `BACKUP_CODES_REGENERATED` -- regenerated at `/mfa/settings/`; `BACKUP_CODES_USED` -- backup code redeemed at the 2FA sign-in or for unlocking.
- `WEBAUTHN_REGISTERED`, `WEBAUTHN_REMOVED` -- passkey added or removed; `WEBAUTHN_FAILED` -- failed passkey check.
- `SUDO_MODE_ENTERED`, `SUDO_MODE_FAILED` -- sudo confirmation succeeded, or failed at the password or the second factor.

Requests rejected by the rate limit (HTTP 429) write at most one `RATE_LIMITED` row per path class, known account and facility within 60 seconds; anonymous requests share the bucket independently of the source IP. Use the filter under **Privacy → Audit log** to investigate failed verifications or activation/deactivation events.

#### Relevant Files

- Models: [`src/core/models/user.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py), [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py)
- Views: [`src/core/views/mfa.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mfa.py)
- Middleware: [`src/core/middleware/`](https://github.com/anlaufstelle/app/tree/main/src/core/middleware)

### 2.8 Quick Templates

**Quick templates** are pre-filled documentation templates that speed up recurring documentation patterns (e.g. "Counseling 30 min", "Standard check-in"). Social worker can apply them with one click on the "Document contact" page.

**User-facing documentation:** [User Guide § 3 -- Quick Templates](user-guide.md#quick-templates).

#### Manage Templates

Templates are maintained in the Django admin under **Anlaufstelle Core > Quick templates**.

| Field | Description |
|---|---|
| `facility` | Tenant-scoping -- each template belongs to exactly one facility |
| `document_type` | Which documentation type is pre-selected when the template is applied |
| `name` | Display name on the button (e.g. "Counseling 30 min") |
| `prefilled_data` | JSON object `{slug: value}` -- maps field slugs to default values |
| `sort_order` | Order in which buttons appear on the "Document contact" page |
| `is_active` | Only active templates are offered for use |
| `created_by` | User who created the template (informational only) |

#### `prefilled_data` -- Filter Rules

The service layer applies a **whitelist filter** before writing and again before applying a template ([`src/core/services/dashboard/quick_templates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/quick_templates.py)). Values are kept only if:

- the slug belongs to the selected documentation type,
- the effective sensitivity of the field is `NORMAL` (no `ELEVATED`/`HIGH` prefill),
- the field is not encrypted (`is_encrypted=False`) and is not a `FILE` field,
- for `SELECT`/`MULTI_SELECT` fields, the value matches an **active** option of the current field template.

This makes templates **self-healing**: if an admin later deactivates a select option, templates are not updated but silently drop the stale value next time they are applied. No data migration needed.

#### Role and Sensitivity Visibility

Quick templates are filtered per user using [`user_can_see_document_type`](https://github.com/anlaufstelle/app/blob/main/src/core/services/compliance/sensitivity.py). An assistant, who cannot see `ELEVATED`/`HIGH` documentation types, will not see templates for those types either. This guarantees the button list never exposes a template a user cannot actually apply.

#### Operational Notes

- Templates are a **convenience layer**, not a data source -- the resulting event must be reviewed and saved explicitly by the user, who can still edit every field.
- Applying a template **does not overwrite** existing form values; it only fills empty fields.
- There is currently **no custom UI** for template management beyond the Django admin. Users with the `facility_admin` role can maintain templates there (within their own facility).

#### Relevant Files

- Model: [`src/core/models/quick_template.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/quick_template.py)
- Service: [`src/core/services/dashboard/quick_templates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/quick_templates.py)
- View integration: [`src/core/views/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py) (`EventCreateView`)
- Tracking issue: #494

### 2.9 Encrypted File Vault & Virus Scanning

File attachments (photos, scans, documents) on **events, clients and cases** ([ADR-039](../adr/039-owner-generische-dateianhaenge.md) (German), Refs #1738 -- exactly one owner per file) are stored in an **encrypted vault**: scanned for viruses before being written to `MEDIA_ROOT`, then encrypted chunk-wise with **Fernet** (AES-128-CBC with HMAC-SHA256, [`cryptography.fernet`](https://cryptography.io/en/latest/fernet/)) using the `ENCRYPTION_KEYS` key material. Refs #524.

#### Upload Flow

1. A staff member picks a file in the form -- event ([`src/core/forms/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/events.py)), client or case ([`clients.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/clients.py)/[`cases.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/cases.py)).
2. The server validates file type and size (see `allowed_file_types` and `max_file_size_mb` in the facility settings -- default **10 MB**).
3. **ClamAV scan** before encryption:
   - Default: fail-closed. If the daemon is unreachable (`CLAMAV_ENABLED=true`, but no TCP connect), the upload is **rejected**.
   - Virus detected → the upload is discarded and an audit-log entry is written.
4a. **Content and extension must belong to the same family** (Refs #2128): the magic-bytes check rejects a file whose content is detected as an image but whose extension is not an image extension (`foto.docx`, `scan.odt`). Without that binding it was enough to name a photo `.docx` and declare `image/jpeg` correctly -- metadata stripping **and** decompression-bomb protection would both have waved it through.
4b. **Aggregate raster image budget** (Refs #2582): before decoding all frames and stripping metadata, the upload is checked against the total pixel limit (default **40 million**), at most **100 frames/pages** and an estimated buffer budget of **256 MiB**. WebP, PNG/APNG, GIF and ICO container headers are checked before opening an image can allocate pixel or disposal buffers. TIFF page headers are counted individually; APNG and WebP reserve a full canvas per frame, GIF also accounts for canvas expansion in later frames. The byte estimate accounts for concurrently live native decoder canvases, source images, clean frame copies, disposal, blend and handoff buffers according to the format, each with at least four bytes per pixel; the full reserve is conservatively applied to all frame areas. It is a conservative admission rule, **not a proven bound on total encoder RAM**. Exceeding any limit produces a validation message and a `SECURITY_VIOLATION` audit entry. Valid small multipage images remain allowed; GIF still remains byte-identical. Operator-approved SVG files are passed through unchanged without rasterization. Unreadable raster headers are rejected because their budget cannot be established.
4. **Metadata stripping** for image uploads (Refs #2128): JPEG, PNG, WebP and TIFF are re-encoded and lose their EXIF data (including the **GPS capture location**, device model/serial, capture timestamp, embedded thumbnail), PNG text chunks (`tEXt`/`iTXt`) and XMP/IPTC blocks. **Orientation is preserved** -- it is baked into the pixels, so portrait photos do not flip; the ICC colour profile is kept as well. What counts is the type **detected** in step 4a via magic bytes, not the file extension. Every non-image file (PDF, DOCX, ODT ...) stays byte-identical. **Always on, no toggle** -- the application is not a forensic evidence tool. If a file cannot be decoded despite matching magic bytes, the upload is rejected (fail-closed, audit reason `image_undecodable`).

> **GIF and BMP are deliberately not cleaned.** This is not an oversight: GIF89a supports both a *comment extension* and an *XMP application extension*, and BMP v5 can embed an ICC profile -- those blocks **are preserved**. A Pillow re-encode would re-quantize the GIF colour palette and change frame timing and disposal behaviour of animated files; that is a visible change to the picture for a metadata channel practice does not use (counselling photos are JPEG or PNG). **Recommendation:** to rule it out, leave `gif` and `bmp` out of `allowed_file_types`.
5. The payload is encrypted chunk-wise with Fernet (MultiFernet) (write key = first entry in `ENCRYPTION_KEYS`) and stored under `MEDIA_ROOT`.
6. Downloads happen exclusively through the protected Django view (no direct webserver access to `MEDIA_ROOT`).

> **Note on document metadata:** Stripping only applies to images. Metadata in PDF (`/Author`, `/Creator`) and OOXML files (`docProps/core.xml`, `dc:creator`) is left untouched -- those typically carry the real name of the **staff member** working on the file, not of the data subject. If that matters, clean the documents before uploading.

> **Plaintext staging buffer** (#1661): Uploads above `FILE_UPLOAD_MAX_MEMORY_SIZE` (default 2.5 MB) are buffered by Django as a plaintext temp file on disk (`FILE_UPLOAD_TEMP_DIR`) before step 3, ahead of scanning and encryption. In the shipped Compose files this buffer lives on a `/tmp` tmpfs (RAM) rather than persistent storage. Details, verification, and fallback options without tmpfs: [ops runbook § 5.7a](ops-runbook.md#57a-upload-temp-directory-plaintext-staging-buffer-refs-1661).

#### ClamAV Service (`docker-compose.prod.yml`)

The production Compose file includes a dedicated `clamav` container with the binary-smoke healthcheck `clamdscan --version`. The web service waits for `service_healthy` before starting. Reference: [`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml). Configurable via `CLAMAV_ENABLED` / `CLAMAV_HOST` / `CLAMAV_PORT` / `CLAMAV_TIMEOUT` (see [ENV reference in § 1](#full-environment-variable-reference)).

#### Health Check

In production the `/health/` endpoint checks ClamAV daemon reachability in addition to the database. If ClamAV is enabled but unreachable, the JSON payload reports `status: "degraded"` while the HTTP status remains **200**. Monitors must therefore evaluate the payload's `status` value; an HTTP-code-only check does not detect “uploads are currently unavailable”.

#### Key Rotation (`ENCRYPTION_KEYS`)

MultiFernet accepts a list of keys. To rotate:

1. **Generate a new key** and add it as the **first** entry in `ENCRYPTION_KEYS`: `ENCRYPTION_KEYS=new,old`. Newly written data is encrypted with `new`; existing data stays readable via `old`.
2. Run `reencrypt_fields` through the maintenance service; repeat after skipped-row or TOTP retry warnings until none remain.
3. Then run `reencrypt_files`, followed by **unfiltered** `reencrypt_files --verify`: every referenced attachment version, including deleted and superseded versions, must be fully readable using the primary key alone. A failure exit means: keep old keys, resolve the cause, rotate and verify again.
4. Let local and off-site backups containing old ciphertext expire under the applicable retention policies; keep their required keys in separate escrow until then. Only after complete verification and actual backup expiry, remove the old key, recreate every process that uses the keys with the new configuration, and run unfiltered `reencrypt_files --verify` again. The full maintenance procedure and its limits are in [Ops Runbook § 9.5](ops-runbook.md#file-rotation-and-old-key-removal).

> **Important:** Prepend the new key while retaining the old keys initially. `--facility` does not prove installation-wide key removability. There is **one key ring per installation**, no facility keys; successful file verification proves neither that every database field has been rotated nor that older backups remain readable.
> ⚠️ **Role prerequisite (Refs #1058).** `reencrypt_fields` reads and writes tables protected by row-level security. The `NOBYPASSRLS` runtime role of `web` has no facility context outside a request and would see **0 rows**. Always invoke it via the maintenance service ([ops runbook § 9.5](ops-runbook.md#95-append-only-trigger-bypass-via-owner-ddl-a14)):
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance reencrypt_fields --dry-run
> ```
>
> The command aborts with a clear error message when the bypass context is missing (fail-loud instead of "0 rotated, success reported"; #1058).

**What `reencrypt_fields` covers** (Refs #1058):

| Data | Scope |
|---|---|
| **`Event.data_json`** | **complete -- including soft-deleted events** (#1724); per row with a row lock and compare-and-swap |
| `EventHistory.data_before` / `data_after` | complete (append-only, updated through the replication bypass); per row with a row lock and compare-and-swap on the columns being changed (#2177) |
| `Attachment.original_filename_encrypted` | complete -- the encrypted file name of attachments on events, clients and cases (#1738); per row with a row lock and compare-and-swap |
| **File contents of attachments** (files under `MEDIA_ROOT`) | **not covered by `reencrypt_fields`** -- rotate separately with `reencrypt_files` and check with `--verify` (step 3) |
| TOTP secrets (`otp_totp_totpdevice.key`) | complete, per device with a row lock and compare-and-swap |
| **`Client.data_json`** | **complete -- including soft-deleted persons**; per row with a row lock and compare-and-swap |
| **`Case.data_json`** | **complete -- including soft-deleted cases**; per row with a row lock and compare-and-swap |
| `RectificationRequest.old_value` / `new_value` / `reason` / `review_note` | complete, per row with a row lock and compare-and-swap on the value columns being changed |
| `ShiftHandoverNote.note` (handover note) | complete, per row with a row lock and compare-and-swap (#2042) |

**Persons** and **cases** also carry encrypted extra fields in `data_json` ([ADR-037](../adr/037-konfigurationspakete-dynamische-felder.md)). The run rotates them as well and additionally heals plaintext leftovers in fields that the document type declares as encrypted.

For events, persons and cases, **soft-deleted records are rotated too**. That is deliberate: a record waiting in the trash must not stay behind under a retired key just because it is pending deletion (#1724).

> **Operational note:** The run protects every row it changes with a row lock and compare-and-swap. If a concurrent domain edit wins, the rotation skips its stale state and warns: "N Zeilen wegen paralleler Änderung übersprungen — Lauf wiederholen" (N rows skipped due to a concurrent change -- repeat the run). The rotation never overwrites an interim change, including redaction or anonymization runs that do not advance `updated_at`. This also covers the **event history** (#2177): if the nightly retention run or an Art. 17 GDPR anonymization redacts a history row while the rotation is running, the rotation skips that row and reports it as skipped -- the redacted state stands. Rows that are already redacted are no longer rotation candidates at all: they carry no encrypted values, only the redaction marker. Repeat the run until it completes without this warning; the old key stays in the ring until file rotation, full verification and backup expiry are complete (steps 3–4). A maintenance window remains advisable to reduce database load, but it is no longer required to prevent lost updates.

#### Upload Limit

- Per facility: `Settings.max_file_size_mb` (default **10 MB**) -- see [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py).
- Global per file: `FILE_VAULT_MAX_UPLOAD_BYTES` (default **50 MB**, adjustable in `.env`). The smaller of the two values applies.
- Per request: Caddy rejects requests above `request_body max_size` (**1100 MiB**, sized for 20 files of 50 MB per field) with HTTP 413. The value in the `Caddyfile` does not follow a changed per-file limit automatically; after raising `FILE_VAULT_MAX_UPLOAD_BYTES` or `FILE_VAULT_MAX_UPLOAD_FILES`, adjust it as well (Operations Runbook § 5.7).
- Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` (2.5 MB, fixed in `prod.py`) only limits form data without files and is no limit for attachments.

#### Storage Quotas

The upload limit above applies **per file**. Refs #2129 adds a cap on the **sum** of all file attachments **per facility**. The reason is multi-tenant operation: without a quota, one facility can fill the shared `MEDIA_ROOT` with nothing but legitimate uploads -- and the consequences (upload failures, backup failures) hit **every** facility on the instance. The disk check in `/health/` reports a full volume, it does not prevent one, and it knows nothing about facilities.

| Knob | Where | Meaning |
|---|---|---|
| `Settings.max_storage_mb` | Management → Settings, group "Files" | Quota of **this** facility in MB. Empty = no facility-specific quota. |
| `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB` | ENV (default **2048**) | Operator backstop for every facility without its own quota. `0` = no backstop. |

The **stricter** of the two always applies. If both are empty or `0`, the facility is unlimited.

**What counts.** Every attachment of the facility, **trash included** and including superseded versions: a removed attachment is only gone from the practitioner's view; the encrypted file stays on disk until it is physically purged. The display therefore reports the trash share separately and deliberately remains attachment-based -- that is the lever a full facility uses to free space again (irreversible deletion, see the file overview in the [user guide](user-guide.md)). During **enforcement of a new CSV import**, the source files of all already open CSV imports (`ImportJob`) count as well until the respective run is committed or aborted. A new **import** may therefore be rejected even while the displayed attachment usage remains below the limit. For **file attachments** this applies in two stages: the check before the virus scan sees the attachment total only; the storage step under the facility lock, however, uses the same shared total as the import -- attachments **and** open import files. An **attachment** may therefore be rejected in the storage step even while the displayed attachment usage remains below the limit.

**What happens when the quota is reached.** The attachment or CSV upload is rejected by the pre-check before it is encrypted; for file attachments and CSV import files the storage step re-checks the quota under the facility lock (zero overrun) -- the practitioner sees "Speicherkontingent der Einrichtung erschöpft: X von Y MB belegt (davon Z MB im Papierkorb)". It is logged under its own audit action **"Storage quota exceeded"** (`attachment_quota_exceeded`), deliberately **not** as a security violation: a full quota is an operational state, not an attack.

**Display and warning.** The administration hub (`/verwaltung/`) shows the row "Speicher -- X von Y MB belegt", the lead workspace shows the same row below its cards. From **90 %** usage an additional hint appears in the configuration warnings of the administration hub -- and the same sentence as a hint right below the lead's storage row.

Several files of **one** form count together: the check knows about the files already processed in the same submission and rejects the one that would break the quota.

**Concurrent uploads.** The quota holds even when arbitrarily many uploads of one facility run at the same time (#2178, #2259): the check before the virus scan, respectively before the import file is encrypted, only saves unnecessary work; the decision is made in the storage step, immediately before the attachment row or the import run is written -- there the total is read a second time under a short per-facility lock, across **file attachments and open CSV import files together**. The quota is therefore exceeded by **0 bytes**, including in the mixed case of an attachment and an import. Virus scan and encryption still run without any lock; only the short database part is serialized. If an upload is rejected at that point, the practitioner sees the same message as usual, neither an encrypted file nor a row is left behind, and the rejection appears in the audit log as "Storage quota exceeded".

#### Safe Downloads (RFC 5987)

All file downloads are delivered via the central helper [`safe_download_response`](https://github.com/anlaufstelle/app/blob/main/src/core/utils/downloads.py). The helper sets `Content-Disposition` with RFC-5987-encoded file names (Unicode-safe, no reverse-path traversal) and prevents browser MIME sniffing.

### 2.10 Offline Mode & Streetwork (M6A)

**Offline key and revocation check** (#2528). The non-extractable `CryptoKey` derived from the password and server salt remains stored in browser IndexedDB across tab, browser and device restarts. “Non-extractable” prevents export through WebCrypto; protecting the stored key against access to the browser profile depends on **device encryption and screen locking**, which are mandatory for offline devices. Derivation freezes an absolute deadline using the facility's `session_absolute_hours` (default 12 hours). Activity and navigation never extend it. The app checks on load, before decryption/storage/replay, on tab return and every 60 seconds. At expiry it removes the key and displays “Offline key expired — please sign in online”; unsent work stays encrypted and becomes readable after another online sign-in with the same password and salt. The idle window still applies separately. Missing or invalid expiry metadata blocks access; bundles without valid `expiresAt` are expired. An observed backwards device clock locks the key, but clock manipulation remains a residual risk. A powered-off app cannot remotely delete the key.

An HTTP/network/processing error fetching revocations (including 500/429), or an invalid response structure, pauses queue and edit replay: “Synchronization is waiting for the revocation check”. The blocking state is stored in local IndexedDB. Each replay run also requires its own fresh, structurally valid, successfully applied and persisted revocation check; an earlier approval never authorizes a later run or another tab, even when metadata writes and fallback deletion both fail. Missing or unreadable metadata blocks replay; recovery from a read failure also requires another successful check. The next sync sequence checks again, as does a direct save from the offline client record. A failed check preserves unsent work in encrypted form. Existing idempotency keys remain intact, and the server gates (422 for revoked people, 409 for status conflicts) remain authoritative. Device encryption does not replace these server checks.

For field work (e.g. streetwork), Anlaufstelle offers a **secure offline mode** with **client-side** end-to-end encryption of all data cached on the device. Refs #573, #576.

#### Cryptographic Design

| Aspect | Value |
|---|---|
| Algorithm | **AES-GCM-256** (WebCrypto-native) |
| Storage | **IndexedDB**: contents as AES-GCM ciphertext; index metadata (record and person IDs, edit status, timestamps, send-queue target URLs) in plaintext; the non-extractable key lives in a separate IndexedDB (`anlaufstelle-crypto`) |
| Key derivation | **PBKDF2** -- 600,000 iterations, SHA-256 |
| KDF input | User password + `User.offline_key_salt` (16 bytes, per-user, stored server-side) |
| Key lifetime | **Persisted** as a non-extractable `CryptoKey` (`extractable: false`) in its own IndexedDB (`anlaufstelle-crypto`) -- survives tab close and reload; limited by the idle window and the absolute cap from derivation, see "Consequences for Admins" |

Source: [`src/static/js/crypto.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/crypto.js) (client-side derivation/storage/idle wipe), [`src/core/signals/offline_invalidation.py`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/offline_invalidation.py) (server-side salt rotation on role change/deactivation).

#### Consequences for Admins

- **Key loss is unrecoverable:** If a user forgets their password, all offline data stored locally on their device becomes **permanently unreadable**. An admin-side reset of offline data is technically **impossible** -- the key only exists in the user's browser.
- **Sync before password change:** Users must synchronize offline data with the server **before** changing the password or logging out. Changing the password rotates the salt (new key); the same mechanism auto-rotates the salt on a role change or user deactivation (`offline_invalidation.py`), so a revoked privilege cannot persist offline.
- **The key is discarded on:** logout, password change (salt rotation), role revocation/deactivation (server-side salt rotation), and session expiry (idle key wipe, default 30 min) → stored ciphertexts are unreadable until the user authenticates again. **Simply closing the tab does NOT discard the key** -- it is reconstructed from IndexedDB on reopening, within the session lifetime (deliberate streetwork design, #1065/#1324).

#### Browser Requirements

- The WebCrypto API (`crypto.subtle`) and IndexedDB must be available.
- Supported: current versions of Firefox, Chrome, Edge, Safari.
- Not supported: outdated browsers without a modern crypto API (offline mode is grayed out in that case).

#### Streetwork Levels

- **Level 2 (Read cache):** Selected person dossiers are locally encrypted and cached before streetwork so they can be viewed offline.
- **Level 3 (Offline edit):** Events and notes can be captured offline and synchronize on the next online period. Conflicts between an offline edit and a concurrent server change are presented to the user as a **side-by-side diff** for manual resolution.

Newly encrypted multi-select values remain lists when editing events offline and replaying them after reconnecting; saving without changes preserves the selected options. Conflict review shows each selected label on its own line, including inactive options; if an option or field template cannot be resolved, it shows the slug. Role and field visibility checks apply before decrypting server values. Refs #2520.

### 2.11 CSV Import and Export: Formula Protection

Every CSV export (statistics export, dataset reports, audit-log export) neutralizes **formula injection**: a cell starting with `=`, `+`, `-`, `@`, a tab or a line break would be executed as a **formula** by Excel or LibreOffice when the file is opened. Such cells therefore receive a leading apostrophe (`'`) -- the spreadsheet displays the original value but does not execute it. Refs #719.

The protection also applies when **invisible characters** precede the formula character -- a space, a non-breaking space (NBSP), a byte-order mark or zero-width characters. Spreadsheets discard those characters on import and evaluate the cell anyway, so `" =1+1"` is apostrophized as well. The original value is preserved in full; nothing is trimmed. This covers value cells **and** header cells -- a field-template name is free text entered by the facility admin and therefore the same vector.

> **Important -- your own exports are not round-trip safe:** the CSV import does **not** strip the protective apostrophe again. Re-importing a previously exported file turns the value `=Text` permanently into `'=Text`. This is neither a bug nor an error reported by the import run, but a deliberate boundary: the import engine compares the **raw** file row against its error messages to keep cell contents (potentially client data) out of a run's result log. If it de-apostrophized first, that comparison would no longer match and a cell value could slip into the log -- data protection takes precedence over convenience here.
>
> **In practice:** use the CSV import to **migrate data from other systems**, not as a backup-and-restore path for your own data. For the latter, use the encrypted database and media backup provided by `backup.sh` (see [§ 3](#3-backup-and-recovery)). If you do need to re-import an exported value, remove the leading `'` in the spreadsheet beforehand.

If the documentation type or mapped field templates change while an import is in progress, the application rejects the dry run or commit and directs the user back to the mapping step. Removed or deactivated target fields are therefore never skipped silently. A commit always uses the **most recently confirmed mapping**, even if it was confirmed in another browser session.

The report exports in **JSON and XML** deliberately do not sanitize -- there is no formula context there, and CSV escapes would corrupt the machine-readable values.

Relevant files: [`src/core/services/system/export.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/system/export.py) (`_sanitize_csv_cell`), [`src/core/services/import_engine.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/import_engine.py).

### 2.12 Import Wizard

The import wizard under **Data import** (`/import/`) imports CSV files as people or events. It is visible only to the **facility admin** role. A file may contain at most 5 MB and 1,000 non-empty data rows; its first row must contain unique, non-empty column headings. UTF-8 and Windows-1252 are supported, as are semicolons, commas, and tabs as delimiters; encoding and delimiter can be detected automatically or selected explicitly. Events require an active documentation type.

Each record must contain exactly as many cells as the header; empty optional cells also need their delimiter. Too many or too few cells reject the entire file before any data is imported. A quote may open a field only at its start; quotes inside quoted fields must be doubled, and a closing quote may only be followed by the end of the field or record. Invalid quote positions and unclosed fields also reject the file. The message gives the physical line and logical record (including the header), never cell contents. UTF-8 with a BOM, correctly quoted delimiters and multiline fields remain valid. Preview, dry run and commit check the same complete file structure.

The wizard has four steps:

1. **File:** Choose people or events as the target, select the documentation type for events, and upload the CSV file.
2. **Mapping:** Map each required source column to a target field. Unmapped columns are ignored, and five sample values assist with the choice. A pseudonym is required for people; events require the timestamp and person pseudonym. Active custom fields of the selected documentation type are available too, but file fields are not.
3. **Dry run:** The wizard validates every row without retaining domain data. The summary lists data rows, records that would be created, and errors; the error list uses the spreadsheet row number including the header. Correct an invalid file and upload it again.
4. **Commit:** **Commit now** appears only after an error-free dry run. The commit is atomic: if an error has arisen in the meantime, no record is retained. The uploaded source file is deleted afterwards.

**Abort import** closes an open run and deletes its source file without importing domain data. Upload, successful commit, and abort are recorded in the audit log; the import overview retains the run's status, counters, and error report. When preparing the file, also observe the [formula protection and its round-trip limitation in § 2.11](#211-csv-import-and-export-formula-protection).

---

---

## 3. Backup and Recovery

### 3.1 Encrypted Database and Media Backup

The canonical production path is [`scripts/ops/backup.sh`](../../scripts/ops/backup.sh). Run it from the fixed installation directory. The account needs Docker access and write permission on `BACKUP_HOST_DIR`; the following examples therefore use `sudo`:

```bash
cd /opt/anlaufstelle
sudo sh -c 'umask 0027 && ./scripts/ops/backup.sh'
```

The script loads `.env`, backs up PostgreSQL **and** the encrypted file vault (`MEDIA_ROOT`), compresses both archives, encrypts them with AES-256-CBC, and writes an HMAC-SHA256 sidecar for each archive. Files are stored below `BACKUP_HOST_DIR` in `daily/`, `weekly/`, and `monthly/`; built-in rotation retains them for 7, 28, and 90 days respectively.

Prerequisites in `.env`:

- `POSTGRES_DB` and `POSTGRES_USER` for the database connection,
- `POSTGRES_ADMIN_USER` and `POSTGRES_ADMIN_PASSWORD` for the complete dump,
- a random, separately escrowed `BACKUP_ENCRYPTION_KEY`,
- `BACKUP_HOST_DIR` as the base directory on the host; the script writes exclusively there and ignores `BACKUP_DIR`, which is used only as the container mount target (see the [environment table](#full-environment-variable-reference)),
- `DJANGO_AUDIT_HWM_PATH` as the location of the audit anchor; `.env.example` ships `/data/audit_hwm.json` active out of the box.

**Mandatory step before the first backup — create the audit anchor.** The audit anchor is the signed file outside the database that makes truncation of the most recent entries and a wiped chain detectable in the first place. `backup.sh` can only include it once it exists, and it is created by the first green run of the chain verification. After starting the stack, therefore run that verification **twice in a row** and check the result before you create the first backup:

```bash
cd /opt/anlaufstelle
docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain
docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain
docker compose -f docker-compose.prod.yml exec -T cron test -s /data/audit_hwm.json
```

The second run is necessary because on a freshly set-up instance the system chain has no row yet during the first run and is therefore not anchored; only that run writes its success marker, and the second run anchors it. A backup made after a single run carries no anchor for the system chain and fails the required-anchor run of the recovery ([Disaster Recovery § 6 Step 4b](../disaster-recovery.md#schritt-4b--audit-hwm-zurückspielen-und-kette-prüfen)). The last command exits with code 0 as soon as the anchor is present. A complete backup set then consists of **seven** files: database dump and media archive with one `.hmac` sidecar each, the signed `.manifest` plus its `.hmac`, and `audit_hwm-<timestamp>.json`. If the anchor is missing, empty or unreadable through `cron`, backup exits with code 1 before publication — and the recovery step “restore the audit anchor and verify the chain” ((DE: [Disaster Recovery § 6 Step 4b](../disaster-recovery.md#schritt-4b--audit-hwm-zurückspielen-und-kette-prüfen))) cannot be performed with such a set.

The anchor lives on a separate volume mounted read-only by `web`. This separation does not protect against host/Docker access or the maintenance/owner role. The volume is created on first start; preserve and transfer an existing signed anchor as described in [Ops Runbook § 6.7](ops-runbook.md#67-back-up-and-verify-the-audit-high-water-mark-refs-2022) when updating an instance. The “Last backup” card checks the existence and size of the copy next to the manifest; it does not read the live anchor.

Root's cron needs write access, while `appuser` in the container (UID/GID 1000) must be able to read and traverse the directory so that health and compliance checks can find the backups. Before setup, create the configured `BACKUP_HOST_DIR` as `root:1000` with mode `2750`, exactly as in [Step 4](#step-4-start-the-stack). `initial-setup.sh` runs without `sudo`, accepts a correct existing directory, and does not change its owner or group; if the contract is violated, it aborts before the first `up` with the correction command documented there. `backup.sh` makes only the manifest readable by that group (`0640`); the dump, media archive, audit anchor and signatures stay with the owner (`0600`).

> **RLS is security-critical here:** `pg_dump` must run as `POSTGRES_ADMIN_USER`. The `POSTGRES_USER` application role is `NOBYPASSRLS`; a raw dump under that role fails at `FORCE ROW LEVEL SECURITY` or would not be a complete backup of facility-scoped data. Do not replace `backup.sh` with a custom `pg_dump -U anlaufstelle` command or the application role.

Keep `.env`, or at least `BACKUP_ENCRYPTION_KEY` and the complete `ENCRYPTION_KEYS` history, separate from the production host. Without the backup key the archive cannot be decrypted; without the field/vault keys its encrypted contents remain unreadable. Configure an off-site destination as well to protect against complete host loss; details are in [Ops Runbook § 6.6a](ops-runbook.md#66a-off-site-backup-sync-refs-738).

For off-site copies, also configure a destination lifecycle with at most 12 months, record the period in the deletion concept and information obligations, and check actual expiry; `backup.sh` deletes nothing there ([off-site retention](ops-runbook.md#off-site-retention)).

### 3.2 Set Up Automated Backups

The recommended option is the host's **root crontab**: it uses five time fields and the executing account has Docker access. First enable the cron service, inspect the host timezone, and create the log file with restricted permissions:

```bash
sudo systemctl enable --now cron
timedatectl status
sudo touch /var/log/anlaufstelle-backup.log
sudo chown root:adm /var/log/anlaufstelle-backup.log
sudo chmod 640 /var/log/anlaufstelle-backup.log
sudo crontab -e
```

Entry for a daily backup at 02:00, before retention jobs:

```cron
0 2 * * * cd /opt/anlaufstelle && umask 0027 && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1
```

`/etc/crontab` additionally requires the username `root` after the five time fields; do not copy the five-field line there unchanged. Also configure weekly rotation for `/var/log/anlaufstelle-backup.log` (for example eight compressed rotations with `copytruncate`) under `/etc/logrotate.d/`.

`backup.sh` creates all files of a run in a hidden work directory below `daily/` and moves them into the daily directory only after the run has completed — the signed manifest last. An aborted run therefore leaves no artifact behind; the “Last backup” state in the system overview and in the compliance area counts complete sets only (a manifest together with the files it names) and otherwise stays on “missing”.

Do not add a separate `find ... -delete` job: `backup.sh` rotates database, media, and HMAC files together. Verify the latest backup regularly:

```bash
cd /opt/anlaufstelle
sudo ./scripts/ops/backup.sh --verify
```

The verification run selects the most recent **set** through its signed manifest, enforces that binding (set ID, dump hash, the bound media archive with its sidecar and hash — the same check the restore performs), validates the HMAC before decrypting, restores the database dump into a temporary database, compares critical RLS tables with the live database, and checks the media archive **of that same set**. Without a manifest, or with a missing or foreign media archive, the run ends with an error instead of a warning. The more thorough quarterly restore drill is described in [ops runbook § 6.6](ops-runbook.md#66-backup-restore-drill-refs-720-739).

### 3.3 Recovery

[`scripts/ops/restore.sh`](../../scripts/ops/restore.sh) is only for the backup sets created by `backup.sh`. A `pg_restore` archive from another backup path is not compatible. A set consists of files with the same timestamp, which must all be in the same directory:

- `anlaufstelle_<timestamp>.manifest` with `.manifest.hmac`: the signed manifest (set ID, hashes of dump, media archive and audit anchor, row counts). **Mandatory**: if it is missing, `restore.sh` aborts before restoring anything; there is no way around the manifest.
- `anlaufstelle_<timestamp>.sql.gz.enc` with `.sql.gz.enc.hmac`: the encrypted database dump.
- `anlaufstelle_<timestamp>_media.tar.gz.enc` with `.hmac`: the encrypted media archive, if the manifest binds one.
- `audit_hwm-<timestamp>.json`: the mandatory audit anchor of the backup set.

Copying only the dump, the media archive and their sidecars is not enough to restore.

1. Schedule a maintenance window, block public access, and prepare an empty target database with the same role names as the backed-up system. The application role (`POSTGRES_USER`, `NOBYPASSRLS`) and the admin/owner role (`POSTGRES_ADMIN_USER`, `BYPASSRLS`) must exist before data is loaded. The complete destructive procedure for a new host is in (DE: [Disaster Recovery § 6](../disaster-recovery.md#6-wiederherstellung-auf-einem-fabrikfrischen-host)).

2. Ensure that the DB and web containers are running for the database and media restore while Caddy and cron remain stopped.

3. Start the restore from the repository root. The second argument is optional, but required for a complete recovery including file attachments. The backup files belong to the account `backup.sh` runs under (root in the recommended setup) and, except for the manifest, are readable only by that account (`0600`, including the `.hmac` signatures). Therefore run `restore.sh` as that owner, with `sudo` in the recommended setup; an operating account that only reads through the group cannot verify the set and aborts. The script reads the `.env` itself, so `sudo` loses no settings:

```bash
set -a
. ./.env
set +a
BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-./backups}"
sudo ./scripts/ops/restore.sh \
  "$BACKUP_HOST_DIR/daily/anlaufstelle_YYYY-MM-DD_HHMMSS.sql.gz.enc" \
  "$BACKUP_HOST_DIR/daily/anlaufstelle_YYYY-MM-DD_HHMMSS_media.tar.gz.enc"
```

Before even asking for confirmation, the script first verifies the manifest (HMAC), then the set binding (set ID, dump hash, media hash including an existing sidecar), then the database HMAC and, when the second argument was supplied, the media HMAC. It rejects a media archive the manifest does not bind. The script checks the set's audit anchor (`audit_hwm-<timestamp>.json` next to the dump) using the configured path or the Compose default: a missing or empty copy, or a hash that differs from the manifest, aborts the restore. After restoring the database it imports the anchor onto its own volume. Only `--skip-audit-anchor` on a host without a custom path explicitly permits a restore without anchor validation or import; such a run does not prove an anchored chain ((DE: [Disaster Recovery § 6 Step 4b](../disaster-recovery.md#schritt-4b--audit-hwm-zurückspielen-und-kette-prüfen)), German). Only after all preflight checks succeed and the operator enters `ja` does it restore the database; it repeats the corresponding check immediately before each archive is used, then replaces and extracts the media contents. The SQL dump is deliberately loaded through the local bootstrap superuser (`POSTGRES_SUPERUSER`, default `postgres`): only that role can reliably restore the included owner/`FORCE RLS` statements and data copies. If media extraction aborts, the media contents are incomplete and the full media restore must be repeated.

4. Run migrations with the admin role, then start the stack:

```bash
docker compose -f docker-compose.prod.yml run --rm \
  --entrypoint=/app/docker-migrate.sh maintenance
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d
```

5. Check database roles, health, and samples from both the database **and** file vault. See [Section 5](#5-monitoring), [Ops Runbook § 9](ops-runbook.md#9-row-level-security-rls), and the (DE: [disaster-recovery smoke verification](../disaster-recovery.md#7-smoke-verifikation-nach-restore)).

---

## 4. Updates

### 4.1 Follow Releases

New published versions appear on the [GitHub Releases page](https://github.com/anlaufstelle/app/releases); its [Atom feed](https://github.com/anlaufstelle/app/releases.atom) can be subscribed to in a feed reader without a GitHub account. With a GitHub account, choose **Watch → Custom → Releases** to subscribe only to these notifications. Check the channel weekly and always before a planned update. Before every update, read the target version's CHANGELOG section, especially **“Erforderliche Betreiber-Aktionen”** (“Required operator actions”), and complete it before or during deployment.

Releases also appear on the releases page as pre-releases (marked as such); the releases page and feed show every published state, with the matching image in the registry.

### 4.2 Pull New Image and Update the Stack

Migrations run as a separate one-shot job **before** the `web` service is restarted — it no longer runs migrations on startup itself (Refs #802). For details, see the canonical source [`docs/ops-runbook.md` § 1.2](ops-runbook.md#12-deploy-steps).

```bash
cd /opt/anlaufstelle

# 0. Choose the target: bring the repository to the release tag of the target
#    version and set APP_VERSION (together with APP_IMAGE_DIGEST if you pin the
#    digest) in the .env; verify digest and signature first (ops runbook § 8.4a).
#    Without a new value the pull fetches the same image again.
git fetch --tags && git checkout vX.Y.Z
grep -E '^APP_(VERSION|IMAGE_DIGEST)=' .env

compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# 1. Stop background jobs (cron uses application code and schema) and check that
#    no maintenance one-shot is still running (the output must be empty)
docker compose "${compose_files[@]}" stop cron
docker compose "${compose_files[@]}" ps --all --format '{{.Name}} {{.Status}}' maintenance

# 2. Download the new images (web, cron and maintenance use the same image)
docker compose "${compose_files[@]}" pull web cron maintenance

# 3. Run migrations as a one-shot job
docker compose "${compose_files[@]}" run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 4. Restart the stack (web + cron + caddy, DB stays up; brief downtime)
docker compose "${compose_files[@]}" up -d web cron caddy
```

`cron` is a separate service with application code from the same image. `up -d web caddy` does not replace it: retention, audit and statistics jobs would keep running old code against the new database. Therefore do not schedule the update into the windows of the night jobs (backup 02:00, retention 03:00 through session cleanup 05:00; schedule: [ops runbook § 3](ops-runbook.md#3-cron-jobs)).

Afterwards check that `web` and `cron` run the same image, run a read-only maintenance smoke test and check the logs. The block builds the complete file set itself, so that the newly created `maintenance` container also honors a configured `compose.override.yml`:

```bash
cd /opt/anlaufstelle

compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# Same image ID on both lines
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  $(docker compose "${compose_files[@]}" ps -q web cron)

# Read-only, with the admin role (changes nothing)
docker compose "${compose_files[@]}" run --rm maintenance check_db_roles
docker compose "${compose_files[@]}" run --rm maintenance enforce_retention --dry-run

docker compose "${compose_files[@]}" logs web --tail=50
```

### 4.3 Before an Update

- Always create and verify a current database and media backup first (see [Sections 3.1–3.2](#31-encrypted-database-and-media-backup)).
- Read the changelog for breaking changes and required configuration updates.

### 4.4 Rollback

If problems occur after an update:

```bash
cd /opt/anlaufstelle

# 1. Enter the previous version in the .env (do not edit docker-compose.prod.yml:
#    the versioned file would collide with the next update):
#      APP_VERSION=vX.Y.Z          (previous version; whether the tag is pullable:
#                                   docker manifest inspect ghcr.io/anlaufstelle/app:vX.Y.Z)
#      APP_IMAGE_DIGEST=@sha256:…  (digest of that version) or clear the line —
#                                   a set digest would otherwise be appended to the old tag
#    If the repository was checked out as well, go back to the previous tag too
#    (git checkout vX.Y.Z) so the Compose file and deploy/crontab match the image.

compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
# 2. Recreate web AND cron
docker compose "${compose_files[@]}" up -d web cron

# 3. Check the same image ID for web and cron
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  $(docker compose "${compose_files[@]}" ps -q web cron)

# Restore the database backup of the previous version (if necessary)
```

**Prerequisite: schema compatibility.** The image rollback changes only the code; `web`, `cron` and `maintenance` then run the old code against the schema of the newer version. That is only permitted if the migrations are backward compatible (check the changelog); otherwise roll the migration back or restore the backup ([ops runbook § 2](ops-runbook.md#2-rollback-procedure)).

---

## 5. Monitoring

### 5.1 Health Endpoint

Anlaufstelle provides a public health endpoint:

```
GET /health/
```

**Response during normal operation (HTTP 200, anonymous):**

```json
{"status": "ok"}
```

**Response on database/encryption-key error (HTTP 503, anonymous):**

```json
{"status": "error"}
```

The endpoint requires no authentication and is suitable for external monitoring systems. The anonymous response deliberately contains **only** the `status` field (recon hardening). Database and encryption-key failures (`error` + HTTP 503), an unreachable ClamAV scanner, and an RLS-bypassing database runtime role (`degraded` + HTTP 200) affect the anonymous result. `smtp`, backup, disk, cache, and `stale_jobs`, by contrast, are checked only for authenticated sessions or callers presenting the `X-Health-Token` header (= `DJANGO_HEALTH_DETAIL_TOKEN`); only they receive the detail fields. The complete, authoritative field reference is in the [monitoring guide](../monitoring-guide.md).

### 5.2 Monitoring Integration

**Uptime Kuma / Healthchecks.io:**

```
https://anlaufstelle.meine-einrichtung.de/health/
Expected HTTP status: 200
Expected response content: "status": "ok"
Check interval: 1 minute
```

This anonymous liveness check cannot detect a **missing or stale backup** or an SMTP outage. `DJANGO_HEALTH_DETAIL_TOKEN` reaches `web` through the delivered allowlist. Send the strong token as `X-Health-Token` and additionally evaluate the JSON `status` value; alternatively monitor backups and mail separately.

**curl-based cron check:**

```bash
#!/bin/bash
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
  https://anlaufstelle.meine-einrichtung.de/health/)
if [ "$RESPONSE" != "200" ]; then
  echo "Anlaufstelle health check failed: HTTP $RESPONSE" | \
    mail -s "ALERT: Anlaufstelle down" admin@meine-einrichtung.de
fi
```

### 5.3 Container Status

```bash
# Overview of all services
docker compose -f docker-compose.prod.yml ps

# Follow logs in real time
docker compose -f docker-compose.prod.yml logs -f

# Web logs only
docker compose -f docker-compose.prod.yml logs -f web

# Resource usage
docker stats
```

Container logs are bounded and rotate automatically in every delivered Compose file; the [Operations Runbook § 4](ops-runbook.md#4-log-analysis) documents the values, effect, and customization.

### 5.4 CSP Debugging

The Content Security Policy (CSP) is set **centrally in Django** via [`django-csp`](https://django-csp.readthedocs.io/) (see [`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py), `CONTENT_SECURITY_POLICY`). There is no second CSP configuration in the Caddyfile -- this is the only way to ensure that app and reverse-proxy policies do not drift apart.

**Inline scripts are not allowed.** All JavaScript logic lives in external files under `src/static/js/`, included via `<script src=…>` or through nonce-aware template tags.

**`script-src` global without `'unsafe-eval'`.** With the migration to the `@alpinejs/csp` build (v0.10.2), `'unsafe-eval'` has been removed from the global policy. All Alpine components are registered as `Alpine.data()` components in the modules under [`src/static/js/alpine/`](https://github.com/anlaufstelle/app/tree/main/src/static/js/alpine); architecture tests forbid inline `x-data="{...}"` and complex expressions (ternaries, `||`/`&&`, method calls, object literals) in Alpine and HTMX directives.

**Exception `/admin-mgmt/*` (Django admin):** django-unfold loads its own Alpine build that uses `new AsyncFunction()`-based evaluation for the Cmd+K search and therefore cannot initialize without `'unsafe-eval'`. The [`AdminCSPRelaxMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/) therefore appends `'unsafe-eval'` **per request only for admin routes** -- which are additionally protected by the MFA gate and the `facility_admin` (or `super_admin`) role. Outside the admin, the strict global policy stays active.

**`base-uri 'none'` and `form-action 'self'`** (#1904): The policy forbids every `<base>` element -- an injected `<base href>` would otherwise re-point all relative URLs of the page to a foreign origin -- and allows form targets, including `formaction` buttons, only on the application's own origin; Chromium also checks redirects that follow a submit. Both directives apply to every page including the error and maintenance pages and stay unchanged in the admin area. A violation shows up in the browser console (Chromium) as `Sending form data to '…' violates the following Content Security Policy directive: "form-action 'self'"` or `Refused to set the document's base URI to '…' because it violates the following Content Security Policy directive: "base-uri 'none'"`; the form is not submitted, and the browser reports the violation to `/csp-report/` (log entry `csp_violation`). If an installation needs a form that posts to another origin (for example an external sign-in or payment portal), add that origin to `form-action` in `CONTENT_SECURITY_POLICY` in `settings/base.py` -- not in the reverse proxy: a second CSP header there only narrows the policy further and never widens it.
For HTML responses under `/admin-mgmt/*`, the same middleware also rebuilds `style-src` as `'self' 'unsafe-inline'` and removes hash or nonce sources there; non-HTML responses and every other route keep the strict global policy. This narrowly scoped exception is necessary because django-unfold emits a `<style>` block generated dynamically from its color configuration and inline `style` attributes that cannot be allowed by a fixed hash and would otherwise leave the admin interface without its color scheme and layout.

**Typical error patterns in the browser console:**

- `Refused to execute inline script because it violates the following Content Security Policy directive` -- inline `<script>` block in a template. Move the script to a static JS file or include it via a nonce-aware template tag.
- `Refused to load the script … because it violates … directive: "script-src 'self'"` -- external script CDNs are not supported; all scripts must come from `self`.
- `Refused to evaluate a string as JavaScript because 'unsafe-eval' is not an allowed source` -- expected on regular routes (architecture violation); inside the admin area this indicates that the relax middleware did not match (check the route pattern in [`AdminCSPRelaxMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/)).

If CSP errors appear after an update: check the browser console for the **specific blocked URL/source** and decide whether to move the source into the template or adjust the CSP directive.

### 5.5 Compliance Dashboard

The super-admin area `/system/compliance/` (#919) consolidates typed health checks in a single dashboard: the three DB role checks (via `check_db_roles`, see the database variables in [§ 1](#1-installation-docker-compose)), the check that no application account carries Django's `is_superuser` flag (“App-User kein Django-Superuser”; it checks user accounts, not the database roles), backup age, restore-verified age, ClamAV ping and signature age, retention run age, audit log retention, background job freshness (audit chain check, statistics snapshot, breach scan, materialized view refresh), MFA quota (roles requiring MFA), pending Django migrations, app/Django/Python versions, and critical audit events from the last 24 hours.

If AuditLog retention is set to `0` months in one or more facilities, the **Retention** category shows one aggregated warning with the affected facility names; resolve it under **Management → Settings → Data protection and retention periods**.

Each check returns a status of `ok` / `warning` / `critical` / `unknown`; a single failing check does not bring down the whole dashboard. The area is **super-admin only** (`facility_admin` receives a `403`). The freshness thresholds for background jobs and the restore marker are documented in the [Ops Runbook](ops-runbook.md).

**Admin DB role: result of the maintenance service.** The admin/maintenance role cannot be checked from inside the web container -- the admin credentials are deliberately absent there (#1962: the `web` service receives an explicit environment allowlist without `POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD`). The check therefore runs in the maintenance service: `manage.py check_db_roles` runs on every deploy (`docker-migrate.sh`) and during the initial setup (step 5 of `scripts/initial-setup.sh`) and records its result as a system entry “DB-Rollen-Prüfung abgeschlossen” (`DB_ROLES_VERIFIED`) in the audit log (#2415). The **Admin DB role** card reads the latest entry: `ok` with the time of the check if the app role, the admin role and the ownership were correct; `critical` with the finding codes (for example `app_role_bypassrls` or `ownership`) if the check found errors; `warning` if not every role was configured or if the last successful check is older than 30 days -- if you deploy less often, run `docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles` manually when needed. The entry holds only the result and fixed finding codes, no role names and no credentials. As long as there is no entry, the card stays at `unknown` (neutral, grey) and names the same command as the way to verify it (#2310). This is also the case right after the very first migration: `docker-migrate.sh` checks the roles before `migrate`, when the audit table does not exist yet, and then skips the entry without aborting the gate; step 5 of `scripts/initial-setup.sh` writes the first entry. If no entry is written, the card keeps showing the previous state. This happens when the write fails (message `DB_ROLES_VERIFIED marker not written …` on stderr, for example because `docker-migrate.sh` checks the roles before `migrate` while the audit table still has the old schema), or when `docker-migrate.sh` already aborts earlier with `FATAL: Migrate-Connection umgeht RLS nicht …` and never starts `check_db_roles`. In both cases run the command named above, `docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles`, again (after `migrate` or after the fix): it records findings and an incomplete configuration even when the admin role has lost its RLS bypass; only an `ok` requires a connection that bypasses RLS. If the admin credentials are present in the process serving the dashboard, it checks the role directly; a wrong attribute profile is then reported as `critical`.

### 5.6 Error Tracking & Data Minimization

Error tracking (Sentry-compatible) is **fully opt-in**: the SDK is initialized only when `SENTRY_DSN` is set ([`prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py)). Without a DSN, **no telemetry** leaves the installation -- there is no hosted offering of this project, so data residency rests entirely with the operator. An absent or unreachable tracker never affects the application; errors still land in the server logs (see **Logging** in [§ 1](#1-installation-docker-compose)).

**Server-side SDK only.** Only the server SDK (`sentry-sdk[django]`) is used, no browser SDK. Session Replay, feedback widgets, client-side breadcrumbs and browser profiling do not exist in this project, so none of them can silently run along. The only features in use are server error events and optional trace sampling (`SENTRY_TRACES_SAMPLE_RATE`).

**What is sent, and what is removed or masked.** Once tracking is enabled, several data-minimization layers apply before an event leaves the installation:

- `send_default_pii=False` -- no cookies, no client IP.
- `include_local_variables=False` -- stack-frame locals are not captured (a frame could hold, e.g., a decrypted note).
- A server-side `before_send` hook removes request bodies, cookies and query strings, masks sensitive headers and keys (`Authorization`, passwords, tokens, …), runs exception and log messages plus the **breadcrumbs** built from log lines through a text scrubber (emails/tokens become placeholders), and masks free-form `extra` values.
- A `before_send_transaction` hook applies the same scrubbing to **performance transactions** (which bypass `before_send` in the SDK): the request context including `query_string` (search terms can be client names), span descriptions and data, and the transaction name are all covered.
- **URL-bearing fields are masked structurally** (#2198). This covers `request.url`, the `Referer` and `HX-Current-Url` headers, the transaction name, and paths inside log messages, span descriptions and breadcrumb data. Two steps: the query string is dropped -- that is where the search term sits, and a search term is often a client name that would otherwise have travelled along in the `Referer` of every following page. And the path is resolved against the Django route schema: `/invite/MTIz…/cx1a2b-…/` becomes `/invite/<uidb64>/<token>/`. The live invitation, password-reset and account-recovery tokens therefore no longer reach the tracker, while the route stays readable for debugging. A subpath installation (`SCRIPT_NAME`/`FORCE_SCRIPT_NAME` -- the prefix is stripped for resolution and put back afterwards) and a missing trailing slash are handled as well. If a path does not resolve at all (typical for `404`), a heuristic applies: path segments of 20 characters or more made of token characters become `[scrubbed]`, provided they carry a token signal -- a long uninterrupted run of characters (hash, base64) or the colon of the signed account-recovery tokens. Percent-encoded spellings count too: a browser sends the link in the `Referer` exactly as it appears in the address bar (`%3A` instead of `:`), so path segments are decoded in a controlled way before the check -- an encoded slash (`%2F`) stays part of the segment and never becomes a separator. A URL that sits in the middle of a log line -- for example in the CSRF-403 line, which records path and referer together -- is treated the same way there, and punctuation around the URL is preserved.
- **Only the pattern of a log line leaves the installation, never the interpolated value** (#2198). A log line consists of a template (`"Backend %s skipped (facility=%s)"`) and the values put into it. The SDK transmits both -- the template, the fully assembled line and the raw arguments -- and additionally turns every log line into a breadcrumb attached to later events. Whether an interpolated value holds a person's or facility's name cannot be told from the text, so a hard rule replaces pattern matching here: **the assembled line is replaced by the template and the arguments are dropped** -- in the event fields as well as in the breadcrumb, whose extra data is reduced to logger and level. What reaches the tracker is `"Backend %s skipped (facility=%s)"` plus the logger name and severity: enough to find the place in the code, without carrying a single interpolated value along. The breadcrumb's extra data is **rebuilt** rather than filtered -- only what the logging system itself assigns (logger name and severity) is kept; everything a log line can pass in freely via `extra` is dropped.
- **Person-related key-value pairs in free text** (error messages, span descriptions) are masked as well (#2198): `user=`, `username=`, `login=`, `email=`, `name=` -- including a prefix (`facility_name=`), multi-word values, quoted values and colon form (`"user": "Anna Mustermann"`). The value is removed up to the next separator or the next key; format placeholders (`user=%s`) are kept. Word endings such as `superuser=`, `filename=` or `hostname=` are unaffected. In structured fields the keys `user`, `username`, `login`, `name`, `full_name`, `display_name`, `facility_name` and `pseudonym` are matched **exactly**, so the technical identifiers `user_id`, `user_pk` and `request_id` stay readable.
- **Client IP and proxy chains** are masked (#2198): `remote_addr` from a CSP violation report as well as the headers `Forwarded` (RFC 7239), `X-Forwarded-For`, `X-Real-IP`, `True-Client-IP`, `CF-Connecting-IP` and `Via`. The SDK filters some of these itself, but not the standardized `Forwarded` header.

**Limits -- stated plainly.** The scrubbers are rule- and key-based (best-effort, defense-in-depth); they do not replace a content review of every individual event field. This includes the URL masking: it knows this application's routes, not the meaning of foreign values. A secret that a log line spells out as prose rather than as a path, a short path segment on a route that does not resolve, or a clear name inside a hand-written error message still pass through -- as does any value that a future route carries in its path without anyone having thought about it. The heuristic can also err the other way: a token-shaped segment on a non-resolving path is masked even if it was harmless -- the full line remains in the server logs either way. And a transaction name the application sets itself (`transaction_info.source` other than `url`) is left untouched. Two SDK features are **off** today and therefore out of scope: Sentry's own log transport (`enable_logs`) and streaming of individual trace segments -- both bypass the `before_send` hook and must not be switched on without a fresh review. The most important remaining limit follows from the log rule: what is protected is the **interpolated** value, not the template. Writing a name straight into the text of a log or error message (`"Anna Mustermann not found"` instead of `"%s not found"`) bypasses every automatism -- so for development the rule is: **no clear names in log templates and error texts**. A name inside a free-form error message with no key in front of it is equally undetectable. And note that all of this describes what reaches the tracker. The server log file carries the same lines, but with masked URL paths (#2228): the query part is dropped, a path appears as its route schema (`/invite/<uidb64>/<token>/`) or loses its token segments, and the line for a CSRF 403 names the account as `user_pk` instead of the login name -- `Referer` and `Origin` likewise appear without their query part. This holds in **both** output formats (`LOG_FORMAT=text` and `LOG_FORMAT=json`) and also for the lines the framework itself writes about rejected, forbidden and not-found requests. The client IP (`remote_addr`) of a CSP violation report is not masked: it is the only handle against abuse of that unauthenticated surface and is therefore deliberately readable (see **Logs** below). For maximum data minimization, choose one of the following levels:

1. **Leave `SENTRY_DSN` unset** -- recommended for high-sensitivity deployments, especially on older releases whose SDK scrubbers do not yet include all the layers above: no error-tracking processor, no data egress; errors still appear in the server logs.
2. **`SENTRY_TRACES_SAMPLE_RATE=0`** against a tracker **inside your own trust boundary** (e.g. self-hosted): disables performance transactions entirely and limits egress to scrubbed error events sent to an instance that is not an additional third party.

**Logs.** Server logs are PII-scrubbed in **both** formats -- the default `LOG_FORMAT=text` and `LOG_FORMAT=json` both run messages, tracebacks and the structured extra fields through the same scrubber (email addresses, passwords and token-like values become placeholders). Two fields are deliberately **not** scrubbed, because their purpose is to make technical security events attributable: the client IP (`remote_addr`) and the browser identification string (`user_agent`) of a CSP violation report. Both can identify a person -- if you need short log retention, plan for it on their account. The purely technical identifiers (`request_id`, `user_id`, `facility_id`, `user_pk`) also pass through unchanged: they contain no free text and merely reference a record, so there is nothing for the scrubber to catch. `user_pk` appears, for example, in the invitation log lines, which deliberately carry neither user name nor email address -- without that identifier the entry could no longer be attributed to an account. For GDPR contexts with log aggregation, `LOG_FORMAT=json` is still recommended (structured, machine-readable), but data minimization is no longer tied to that format.

**Self-hosted tracker (GlitchTip).** GlitchTip accepts standard Sentry DSNs, and this project uses only core SDK features (error events plus optional trace sampling). This combination was verified empirically: with `sentry-sdk[django]` `2.64.0` against GlitchTip `6.2.1` (Docker image `glitchtip/glitchtip:6`), server error events are transmitted, parsed plausibly (stack trace, tags, request context) and shown as issues; performance transactions (`SENTRY_TRACES_SAMPLE_RATE > 0`) are **accepted** as well and grouped into transaction groups -- they are neither silently dropped nor do they raise an error. The server-side `before_send`/`before_send_transaction` scrubber applies unchanged (no frame locals, stripped query strings/cookies/request bodies); the rule- and key-based limits described above still hold. The shipped version is currently `sentry-sdk[django]` `2.71.0` (`requirements.txt`); the check has not been repeated against it or other version combinations, and a third-party project naturally remains outside this project's control.

**Hosted Sentry service.** Before enabling a SaaS provider instead of an EU-based or self-hosted instance, verify its data location, retention period, and any third-country transfers and safeguards, and conclude a data processing agreement. List the provider and its subprocessors in section 6 of the DPA template in the GDPR package.

To connect a self-hosted tracker:

1. Run a GlitchTip instance and create a project there -- GlitchTip shows its DSN in the form `https://<public_key>@<glitchtip-host>/<project_id>`.
2. Pass that value to the app as `SENTRY_DSN` -- **no code change required**. Optionally set `SENTRY_TRACES_SAMPLE_RATE` (default `0.1`; `0` disables transactions entirely, see the escalation levels above).
3. Expectation: server error events appear as issues, optional trace sampling as transaction groups. Only the server SDK runs (no browser SDK, no Session Replay).

Operating the tracker instance (setup, backups, retention, upgrades) is operator infrastructure and covered by the [GlitchTip documentation](https://glitchtip.com/documentation) -- **out of scope** here; the tracker's database belongs inside the same trust boundary as the app data and needs the same access and retention rules.

> **Not legal advice:** This section describes technical measures. A data protection impact assessment and the record of processing activities remain with the operator as the controller.

### 5.7 Breach Detection Scan

The `detect_breaches` management command heuristically analyzes the audit log for anomalies (failed-login bursts, mass exports/deletions, distributed login attacks) and writes hits as audit events; optionally a webhook (`BREACH_NOTIFICATION_WEBHOOK_URL`) is notified. The windows/thresholds are controlled via the `BREACH_*` variables (see the [ENV reference in § 1](#full-environment-variable-reference) as well as [ops runbook § 6.5b](ops-runbook.md#65b-gdpr-art-3334--breach-notification-refs-685)).

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only if the `cron` service is not running; it starts the job through the `maintenance` service of the same compose file, the only service that receives the admin role (BYPASSRLS) (without Compose, reproduce it in your own orchestration: same image, entrypoint `python manage.py`, admin role). Setting up both makes the job run twice — details: [ops runbook § 3](ops-runbook.md#3-cron-jobs).

**Recommended cron (hourly, at minute 30):**

```cron
30 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance detect_breaches >> /var/log/anlaufstelle-breach.log 2>&1
```

> **Note:** The complete, authoritative list of all background jobs including their schedule (backup, retention, audit chain verification, statistics snapshots, orphan cleanup, breach scan, view refresh) is in the [ops runbook § 3](ops-runbook.md#3-cron-jobs). In the bundled compose stack they run in the `cron` sidecar (`deploy/crontab`); on the dev server they run as systemd timers via `dev-ops/deploy/install-timers.sh` — in both cases the breach scan runs hourly at minute 30.

---

## 6. Troubleshooting

### Problem: Web Service Does Not Start

**Symptom:** `docker compose -f docker-compose.prod.yml ps` shows `web` as `Restarting`/`Exit 1`, or the migrate/maintenance run aborts before `web` starts at all.

**Steps:**

```bash
# Check the configuration again without running migrations
docker compose -f docker-compose.prod.yml run --rm \
  --entrypoint sh maintenance -c 'python manage.py check --deploy'

# If web has already been created
docker compose -f docker-compose.prod.yml logs web
```

If `initial-setup.sh` aborts, its original error message is already in the terminal output. The first command above checks only the current Django configuration; it deliberately does not start another migration run.

Common causes:

| Error Message | Solution |
|---|---|
| `DJANGO_SECRET_KEY muss in Produktion gesetzt sein.` | Set `DJANGO_SECRET_KEY` in `.env` (see [Section 1.2](#step-2-configure-environment-variables)) |
| `ENCRYPTION_KEY oder ENCRYPTION_KEYS muss in Produktion gesetzt sein.` | Set `ENCRYPTION_KEYS` or `ENCRYPTION_KEY` in `.env` (see [Section 1.2](#step-2-configure-environment-variables)) |
| `DJANGO_AUDIT_HASH_KEY muss in Produktion gesetzt sein` | Set `DJANGO_AUDIT_HASH_KEY` (separate from the secret key) in `.env` (see [Section 1.2](#step-2-configure-environment-variables)) |
| `connection refused` (database) | Check whether `db` is running: `docker compose -f docker-compose.prod.yml ps db` |
| `django.db.utils.OperationalError` / `password authentication failed` | If roles and `.env` diverged after a password change, rotate passwords together with `rotate-db-passwords.sh`. During initial setup, first check [DB initialization aborted](#problem-db-initialization-aborted-roles-missing). |
| `ImproperlyConfigured` | Incomplete environment variables -- read logs for details |

### Problem: `web` Becomes `unhealthy`, `caddy` Does Not Start

The container healthcheck calls `http://localhost:8000/health/`. In the production Compose stack, only `error`/HTTP 503 (database or encryption key) and a rejected probe such as `DisallowedHost` cause this state; `degraded` does not. If `localhost` is missing from `ALLOWED_HOSTS`, Django rejects this internal probe and `caddy` remains in the `Created` state.

```bash
docker compose -f docker-compose.prod.yml logs web | grep DisallowedHost
```

Add `localhost,127.0.0.1` as shown in [Step 2](#step-2-configure-environment-variables), then recreate `web`.

### Problem: `cron` Is Shown as `unhealthy`

The `cron` sidecar has no HTTP endpoint; its image healthcheck must be disabled in the production Compose file. Confirm that you use the current `docker-compose.prod.yml`, then recreate the service:

```bash
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d --force-recreate cron
docker compose -f docker-compose.prod.yml ps cron
docker compose -f docker-compose.prod.yml logs cron
```

### Problem: ClamAV Signatures Are Stale

A signature set older than seven days is not a normal startup state. Inspect the update log and installed signature version:

```bash
docker compose -f docker-compose.prod.yml exec clamav tail -n 30 /var/log/clamav/freshclam.log
docker compose -f docker-compose.prod.yml exec clamav clamdscan --version
```

If the signature age cannot yet be determined, the dashboard status remains
`unknown`, avoiding a false warning during normal startup. The "ClamAV
signature" row then shows the first command above as its action hint. If the
state persists after startup, run it and inspect the log for DNS, download, or
mirror errors.

Signature downloads require the `clamav` service to have a working outbound network path (egress) to the signature server or configured mirror; that path was delivered with #2219. The update log must show a successful download. Repeated DNS/download errors require checking that egress path or the mirror. A green `clamdscan --version` healthcheck proves only that the binary starts, not that signatures are current; also inspect the displayed signature state in the compliance dashboard at `/system/compliance/`.

### Problem: TLS Certificate Is Not Issued

**Symptom:** Browser shows a certificate error, Caddy logs show ACME errors.

```bash
docker compose -f docker-compose.prod.yml logs caddy
```

Common causes:
- DNS record for `DOMAIN` does not yet point to the server IP
- Ports 80/443 are blocked by a firewall or hosting provider
- Let's Encrypt rate limit reached (max 5 certificates per domain per week)

### Problem: Login Fails

**Symptom:** User cannot log in.

The web interface shows failed logins: as the facility administrator open **Data protection > Audit log** (`/audit/`) and filter by the action "Login failed"; as `super_admin` use the system area `/system/audit/`. A query through `exec web … shell` is not suitable for this: without a facility context the database row security returns no row under the app role, so the diagnosis would always stay empty.

**Reset password:**

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py changepassword <benutzername>
```

### Problem: Database Connection Fails

```bash
# Test the application's TCP path with the credentials actually configured
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c \
  "from django.db import connection; connection.ensure_connection(); print(connection.connection.info.host)"

# Database health check status
docker inspect $(docker compose -f docker-compose.prod.yml ps -q db) | grep -A5 Health
```

### Problem: DB Initialization Aborted (Roles Missing)

If `01-app-role.sh` rejects a short password or placeholder during the first start of a **still-empty** cluster, the restarted database container may nevertheless later report `healthy`. The app/admin roles are then missing; PostgreSQL can misleadingly report this as `password authentication failed` during migration.

```bash
# Find the original initialization abort
docker compose -f docker-compose.prod.yml logs db | grep 01-app-role

# Check the application roles that exist
docker compose -f docker-compose.prod.yml exec db \
  psql -U postgres -d anlaufstelle -c \
  "SELECT rolname FROM pg_roles WHERE rolname IN ('anlaufstelle', 'anlaufstelle_admin') ORDER BY rolname;"
```

Only if this is the **first, demonstrably still-empty installation attempt**, correct all three passwords to at least 32 characters, discard the incompletely initialized volume, and start over:

```bash
docker compose -f docker-compose.prod.yml down -v
./scripts/initial-setup.sh
```

> **Data-loss warning:** `down -v` deletes the complete database and all Compose volumes of this stack. Never use this path on an installation that already contains data; stop and restore a verified backup or obtain expert help. Password rotation does not repair missing roles.

### Problem: Disk Space Full

```bash
# Analyze Docker storage usage
docker system df

# Remove unused containers, networks, and images (no volumes)
docker system prune -f

# Never append --volumes indiscriminately: pgdata/media may contain instance data.

# Database volume size
docker system df -v | grep pgdata
```

### Problem: Reports Show "Suppressed" or Dashes Instead of Figures

**Symptom:** A report shows dashes ("—" in the dataset-report preview) or the notice "suppressed" (external report, activity-report PDF) instead of figures -- possibly in almost every row.

**This is normally not a defect.** Aggregates below the facility's k-anonymity threshold are hidden so that a report cannot be used to identify individual people. In a facility with little data -- during testing, shortly after initial setup, or over a very short period -- practically every value may be below the threshold. Hiding an additional visible cell, a total row or an entire breakdown is part of the method: otherwise the suppressed value could be calculated by subtraction.

**User documentation:** [User Guide § 7 -- Statistics and Export](user-guide.md#7-statistics-and-export) explains the representation in each file format and the on-screen response (expand the period, rule out an empty period). Administrators should also know these three points:

1. **The threshold is a setting of its own, not a side effect of retention.** It is configured per facility under **Management → Settings** (group "Data protection and retention periods", `k_anonymity_threshold`, default 5) and applies to **every report artifact that leaves the organization** -- regardless of whether k-anonymization is enabled for the retention run. [§ 7.3](#73-retention-periods-and-enforce_retention) documents both fields together in the retention context, but they are independent.

2. **The internal statistics Overview is deliberately not suppressed** and can therefore help check whether any data exists (managers/application support only). The internal event list contains pseudonyms and is not anonymised statistics. There is no internal PDF mode. Figures may differ from Dataset reports; content from internal views requires a separate assessment before sharing.

3. **A boundary remains even when no suppression is visible:** the guarantee applies per artifact and period, not across multiple releases -- see [§ 8, "Releasing Reports: What You Need to Know"](#8-statistics-snapshots--materialized-view).

> **Important:** Lowering the threshold is a privacy decision, not a troubleshooting step. It affects every report that leaves the organization; document the change accordingly -- the audit log records it as "Settings changed" with the old and new values ([§ 7.4](#74-audit-log)).

### Problem: Users Report a Server Error With a Reference ID

**Symptom:** A user reports an error page showing a reference such as `A1B2-C3D4-E5F6`, sometimes together with the displayed contact note.

The same reference id appears in the matching server-log line (structured field `request_id`), without any account or facility reference:

```bash
docker compose -f docker-compose.prod.yml logs web | grep "<ID>"
```

This works regardless of `LOG_FORMAT`: the text log shows `request_id="<ID>"` after the message, the JSON log (`LOG_FORMAT=json`) carries it in the `request_id` field.

The displayed contact comes from the `SUPPORT_CONTACT` environment variable (empty shows a generic reference to the facility's application support, never a dead link). If Sentry is also configured (`SENTRY_DSN`), the same incident usually also produces a Sentry event -- but it is **not** automatically linked to the error page's reference id; match the two by timestamp and the request path visible in Sentry.


### Diagnostic Commands at a Glance

```bash
# All container logs from the last 100 lines
docker compose -f docker-compose.prod.yml logs --tail=100

# Django migration status
docker compose -f docker-compose.prod.yml exec web \
  python manage.py showmigrations

# Check Django configuration
docker compose -f docker-compose.prod.yml exec web \
  python manage.py check --deploy
```

---



### Authentication alarms and audit review (#2527)

Known `super_admin` accounts without a facility raise a `SECURITY_VIOLATION` alarm with `reason=super_admin_bruteforce` after ten failed logins in fifteen minutes, immediately in the failed-login path and as a heuristic in a full `detect_breaches` scan (scheduled hourly). The same alarm is deduplicated per account for 24 hours. No account lock is introduced. At the regular `/login/` endpoint, failed passwords at or above the threshold receive HTTP 429 with progressive retry advice of 1, 2, 4 and at most 8 seconds (`Retry-After`); the server does not sleep and this advice does not reject a subsequent correct password. The existing IP/username rate limits remain effective and can still reject valid logins.

HTTP 429 rejections record `RATE_LIMITED` with a masked route class, a fixed reason (`request_rate` or `super_admin_backoff`), and the user/facility where already known. Unresolved paths are recorded only as `unresolved`; bodies, queries and actual URL tokens stay out of the details. The cache caps the trail at one row per route class and known account/facility in sixty seconds (anonymous requests share a bucket across all source IPs). If the audit write fails, the response stays 429 and an equally capped security log line names the route class, reason and known identifiers without request contents. Passkey failures and denied backup regeneration also count as critical audit events; successful verification and ordinary throttling alone do not trigger a compliance warning.

## 7. GDPR Notes

Anlaufstelle was developed following the principle of **Privacy by Design** (Art. 25 GDPR, known in German law as DSGVO). This chapter describes the privacy-relevant technical measures and administrative obligations.

> **Disclaimer:** The following technical measures support GDPR compliance but **do not replace legal advice**. The operator is responsible for conducting a Data Protection Impact Assessment (Art. 35 GDPR / DSFA under German law), concluding a Data Processing Agreement with the hosting provider (Art. 28 GDPR / AVV under German law), and documenting processing activities (Art. 30 GDPR). The references throughout this section (DSGVO, BDSG, SGB X) are specific to German data protection law. The software has not undergone a formal third-party security audit.

### 7.1 Encryption

**Field Encryption (Fernet/AES-128):**

Sensitive fields in the database are stored encrypted using the `ENCRYPTION_KEY`. This serves as an additional protective measure beyond general database access controls.

**Visibility vs. encryption:** Field encryption (`is_encrypted`) and sensitivity (`sensitivity`) have separate effects; a field's own High level nevertheless enforces encryption. An encrypted field may be visible to all roles, and a non-encrypted field can be restricted to certain roles.

- The key is provided via the `ENCRYPTION_KEY` environment variable.
- In production, the application refuses to start if no key is set.
- Loss of the key means permanent loss of the encrypted data.

**Key rotation:** There is no automatic key rotation. The manual rotation is described in [§ 2.9 "Key Rotation"](#key-rotation-encryption_keys); it re-encrypts database fields with `reencrypt_fields` and attachments with `reencrypt_files`; remove old keys only after unfiltered verification and expiry of backups encrypted with them. Store all keys of the ring redundantly and separately from database backups.

**Transport encryption:** Caddy enforces HTTPS and automatically redirects HTTP to HTTPS.

**Security headers -- the application is authoritative:** `Strict-Transport-Security` (`max-age=31536000; includeSubDomains; preload`), `X-Content-Type-Options` (`nosniff`), `X-Frame-Options` (`DENY`) and `Referrer-Policy` (`strict-origin-when-cross-origin`) are set by the application only -- the values live in [`src/anlaufstelle/settings/prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py). Change any of them (the HSTS duration, for instance) there. They are deliberately **no longer** in the `Caddyfile`: set in both places, they reached the client twice, and a change in one place appeared to have no effect (#2314). Caddy now sets only what the application does not -- currently `X-Robots-Tag`. The same separation has always applied to the Content Security Policy (§ 5.4). This way the headers also apply when you put the application behind a reverse proxy of your own. All four headers are present on **every** response from the application -- including files served from `/static/` and the maintenance page (HTTP 503), neither of which goes through the full processing chain. The Content Security Policy is present on the maintenance page as well -- the same policy as on every other page (#2339).

Error responses generated by Caddy itself -- such as HTTP 413 for an oversized upload or 502/504 when the application is unavailable -- do not carry these headers; this is deliberately accepted because a second header source in the proxy would break the single-source contract and these responses contain no application data.

### 7.2 Pseudonymization

Pseudonymisation is the intended workflow: the application has no required field for real names, and persons are recorded under a pseudonym. It cannot technically prevent real names, though — pseudonyms, notes, free-text fields and attachments contain whatever the team enters or uploads; events without a person can also relate to a person through free text, locations or attachments. What matters is the facility's data-entry practice. In detail:

- Persons are referenced by internal IDs.
- Display names in the interface are configurable pseudonyms.
- Who sees which data depends not on the contact stage but on sensitivity (the higher level of the documentation type and the field) and on the role: Assistant sees "Normal", Social worker up to "Elevated", Manager and Application support all levels. The person detail page is open from Assistant upwards, also at "With additional details"; there the assistant sees, for example, contacts including their note, but no crisis conversation. The contact stage governs permitted documentation types, four-eyes deletion, the audit entry when the person is opened (`VIEW_QUALIFIED`), and retention periods.
- The system administration (`super_admin`) reads across facilities through the `/system/` area and the admin interface (`/admin-mgmt/`, sudo required), there persons, cases, events, work items and attachments of all facilities. Row Level Security lets these sessions through via `app.is_super_admin` (§ 7.9). The AuditLog records these reads as `SYSTEM_VIEW` for all registered administrative models, including deletion requests, users and configuration; if logging fails, content is denied with HTTP 403. Event and event-history admin pages show metadata only, without field contents.

### 7.3 Retention Periods and `enforce_retention`

Retention periods are configured per facility in the settings (see [Section 2.3](#23-settings-per-facility)).

The management command `enforce_retention` enforces the configured periods. Depending on the kind of data it soft-deletes, hard-deletes, anonymizes or redacts. When an event is soft-deleted, only the event row stays behind, marked as deleted (`is_deleted`, `deleted_at`); this step already removes its contents: it empties `data_json` and `search_text`, deletes the attachments including their encrypted files, and writes a `DELETE` entry to the event history that carries only the field names, no values. The older versions of the event in the history are redacted by the same run in the subsequent history step, without waiting for `eventhistory_detail_months`; deferred and redacted only by a later run are versions whose event, person or case is under a legal hold, whose person or event row is currently locked by another transaction (the step does not wait for foreign locks but skips them), or whose event was assigned to another person in the meantime; a later run finds them again through the same state (event deleted, version not yet redacted), and the run reports the number of affected events in its output (“Deferred … event(s) with held/locked history”) and in the run's audit entry as `deferred_history_events`. From the redaction on, `eventhistory_chronicle_months` runs (table below). The person is not affected as long as they still have an event that is not deleted; they are anonymized only afterwards (paragraph below the table). Anonymous events have no person:

| Kind of data | Setting | Default | Effect |
|---|---|---|---|
| Anonymous events | `retention_anonymous_days` | 90 days | Soft delete; reference date is the event time |
| Events of persons at the "Pseudonym only" level | `retention_identified_days` | 365 days | Soft delete; reference date is the event time |
| Events with additional details | `retention_qualified_days` | 3650 days | From case closure; without a case from the event date; open case from last substantive activity (fallback creation). Proposal and approval before deletion |
| Events of a documentation type with its own period | Maximum period (shortens only), `retention_days` on the documentation type | empty (no type period) | Soft delete; reference date is the event time. Applies in addition to the level period; whichever expires first wins |
| Activities (timeline) | `retention_activities_days` | 365 days | Hard delete |
| Handover notes (shift note) | `retention_handover_notes_days` | 90 days | Hard delete |
| Deleted persons and cases in the trash | `client_trash_days` | 30 days | Anonymization |
| Values of old event versions | `eventhistory_detail_months` | 24 months | Redaction; `0` turns off age-based redaction |
| Already redacted event versions | `eventhistory_chronicle_months` | 24 months after redaction | Hard delete; `0` = unlimited |
| Facility audit log | `auditlog_retention_months` | 24 months | Hard delete; active target holds protect it; `0` = off (§ 7.4) |
| Audit without a facility | `AUDITLOG_SYSTEM_RETENTION_MONTHS` (environment) | 24 months installation-wide | Hard delete under its own audit period; permanent exceptions remain, no target hold protection without a facility; `0` = off; only in a run not restricted to one facility |
| Completed deletion, merge and downgrade requests | `deletion_request_retention_days` | 1095 days from completion | Hard delete; pending requests remain, audit has its own period |

Persons whose events are then all deleted are anonymized in the same run. Without events, the `retention_identified_days` inactivity period applies only to pseudonym-only people without open cases. The anchor is the latest person change (`updated_at`), substantive case activity (`last_activity_at`), task change or attachment change on the person or case (creation, replacement or removal). People with additional details without events are excluded from automatic anonymization; the proposal path currently supports only events, and automatic retention enforcement for these people is missing. Pseudonym-only people without events but with an open case are excluded from the inactivity path. The facility regularly reviews people with additional details without events and submits a deletion request where necessary; it records the review interval in the deletion concept. See [`anonymization.py`](../../src/core/retention/anonymization.py). Holds and Art. 18 protect both paths. The other fields of the group "Datenschutz und Fristen" are not periods: `retention_auto_approve_after_defer` and `retention_max_defer_count` control how deferred deletion proposals are handled in the retention dashboard, `retention_use_k_anonymization` and `k_anonymity_threshold` control k-anonymization (both below).

> **The handover note is hard-deleted, not soft-deleted** (Refs #2042) -- like activities. What counts is the date of the last change (`updated_at`), not creation; key rotation deliberately does not advance that timestamp and therefore does not extend the period. There is **no** legal hold for handover notes: holds protect client, case and event records for ongoing proceedings, whereas the shift note is a duty note with no personal reference in the data model (see (DE: [security-notes.md](../security-notes.md))). Every run with matches writes an audit row (`DELETE`, category `handover_notes`) carrying the count and the period -- never any text.

**Manual execution:**

```bash
# Dry run (no deletion)
docker compose -f docker-compose.prod.yml run --rm maintenance \
  enforce_retention --dry-run

# Execute
docker compose -f docker-compose.prod.yml run --rm maintenance \
  enforce_retention

# Only a specific facility
docker compose -f docker-compose.prod.yml run --rm maintenance \
  enforce_retention --facility "Beratungsstelle Nord"
```

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only if the `cron` service is not running; it starts the job through the `maintenance` service of the same compose file, the only service that receives the admin role (BYPASSRLS) (without Compose, reproduce it in your own orchestration: same image, entrypoint `python manage.py`, admin role). Setting up both makes the job run twice — details: [ops runbook § 3](ops-runbook.md#3-cron-jobs).

**Set up as a daily cron job (recommended; 03:00, after the 02:00 backup):**

```cron
0 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1
```

Every execution that deletes records is automatically logged in the audit log.

#### Retention Dashboard

A **retention dashboard** is available under [`/retention/`](https://github.com/anlaufstelle/app/blob/main/src/core/views/retention.py) where Manager and Admin roles process deletion proposals. Refs #514, #515, #2425.

> **Regular run:** Anonymous and pseudonym-only events are deleted automatically.
> Events with additional details receive proposals; only approved proposals
> permit deletion. A type maximum cannot bypass approval. `--propose` remains
> an additional proposal run for every strategy.

| Dashboard action | Effect |
|---|---|
| **Release** | First eligible person: “1 of 2 approvals”; second, different eligible person: final approval. The next run deletes unless a hold or Art. 18 protects the target |
| **Defer** | Protects until deferred_until, then reopens. Automatic approval after maximum deferrals remains opt-in |
| **Reject** | Deletes nothing; new proposal no earlier than retention_rejected_retry_days (default 365 days) |

`retention_open_case_inactive_days` (default 365 days) controls the reminder for
inactive open cases. Activity means substantive changes to cases, events, tasks
and progress; key rotation does not count.

#### Legal Hold

Manager and facility admin create and lift holds directly in person, case and event
records (Legal Hold verwalten), with a reason, optional expiry and audit trail.
Person/case holds also protect their events. System administration can lift holds
at `/system/legal-holds/`. Proposal cards remain an additional path; no separate
proposal cron is required.

Active holds also protect audit rows of held targets. Art. 18 protects events,
person anonymization and history redaction in the retention run. Both forms of
protection are checked separately; manual four-eyes deletion under Art. 17 remains
a separate path (§ 7.5).

The run creates the proposal. Manual approval requires two different active people from the same facility, each with the Manager or Application support role and deletion confirmation permission. The first approval leaves the proposal pending (PENDING, “1 of 2 approvals”); only the second, different person gives final approval (APPROVED). With only one eligible account, it remains at “1 of 2”; the first approval alone never permits deletion. Deferral, legal hold (including from person, case or event records) and rejection discard the first approval with an audit record; a new manual approval workflow requires both approvals again. The facility designates at least two eligible accounts. The “Approvers for the retention run” compliance card checks role and count (0 = critical, 1 = warning, at least 2 = OK). The “Approvers for deletion requests” card still counts active deletion-request approvers regardless of role. Exception: the facility may explicitly enable auto-approval after repeated deferrals (opt-in, off by default); after the configured maximum number of deferrals and expiry of the follow-up date, the run approves without a second person. Only deferrals by people with deletion confirmation permission count, and only if no deferral of the same proposal is still running. The threshold is at least 1. Managers and Application support may still defer without this permission. Holds and Art. 18 restrictions protect this path as well.

Before operation, review periods and auto-approval (off by default). The request retention period is visible under Administration → Settings; the installation-wide audit period is set through the environment.

The actual deletion run executes an approved proposal (APPROVED) for events with additional details only with evidence of both approvals or the verified auto-approval exception. Incomplete evidence causes the event to be skipped and reported; its card says “Approval evidence incomplete – will not be deleted”. A dry run (`--dry-run`) counts candidates and does not replace the evidence check when deleting.

#### Retention of the Event History

Every save of an event writes a **history version**: who changed which fields to which values, and when. So that they do not outlive the deletion period of the event itself: an event whose `retention_identified_days` have expired is emptied, and its older versions must not keep every wording ever stored. `enforce_retention` therefore handles them in two stages ([ADR-050](../adr/050-eventhistory-retention.md)).

**Stage 1 — redaction.** The version remains, its *values* fall away. What stays: timestamp, acting person, action and the names of the affected fields. What disappears: the contents. In the event detail view and in the Art. 15 access record, "– (redacted)" appears in their place. Two triggers:

- **The event is deleted.** As soon as an event is marked deleted — by the retention period or manually — **all** of its versions lose their values on the next run. This needs no setting and cannot be switched off: it enforces the period that already applies to the event itself. It also covers events without a person (anonymous contacts), for which no history deletion path existed at all until now.
- **The version is old.** For events that are still **alive**, versions older than `eventhistory_detail_months` are redacted.

**Stage 2 — deletion of the chronicle.** An already redacted version is permanently deleted once its redaction dates back more than `eventhistory_chronicle_months`. This is measured from the moment of redaction, not from the original change — the question is "how long has this row already been empty". At this point no contents fall any more, only the chronicle itself.

| Setting in `Settings` | Default | Effect |
|---|---|---|
| `eventhistory_detail_months` | 24 months | How long the **values** of old versions of living events stay readable. `0` = **disabled** (the values stay readable indefinitely). Does not affect the "deleted event" branch. |
| `eventhistory_chronicle_months` | 24 months | How long an already redacted version is kept afterwards before it is deleted. `0` = **disabled**, in which case the chronicle grows without bound — that is a decision for indefinite retention and then belongs explicitly in the record of processing activities and the deletion concept. |

Both defaults match `auditlog_retention_months`: the history and the audit log answer the same question ("who changed something, and when") and should therefore end together. With the defaults, the version of a living event has a total lifetime of 24 months of values plus 24 months of chronicle; the version of a deleted event has 24 months of chronicle from the deletion.

**What to know before changing these:**

- **The most recent version of a living event is never redacted**, regardless of its age. The detail page therefore always keeps at least one readable last change — even for a record untouched for years.
- **A legal hold stops the redaction.** While the event (or its person) is under a hold, its versions stay unchanged; the run reports them as deferred and catches up once the hold is lifted or expires.
- **Redaction is final.** Old wordings can afterwards only be reconstructed from a backup. If you must be able to argue a rectification dispute about a years-old wording, set `eventhistory_detail_months` accordingly higher.
- **Side effect on key rotation:** redacted versions no longer carry encrypted content and are skipped by `reencrypt_fields`. The duration of a key rotation thus scales with the *unredacted window* instead of the total table size — it stays constant instead of growing every year.
- **Every batch leaves an audit trail entry** (action "Event history redacted") with count, cutoff and batch boundaries. This proof is **exempt** from `auditlog_retention_months` and is kept permanently.

With many versions of living events, a run may take longer than usual.

**Check: versions with a redaction timestamp that still carry values** (#2188). Key rotation (`reencrypt_fields`) locks the rows it writes back and therefore does not overwrite a version that has just been redacted with a freshly encrypted copy of its old values (#2177). A version that counts as redacted but carries contents again is not caught up by any retention run, because every run only touches versions that are not yet redacted. If your data stems from a restore or from a rotation without this lock, check it with `repair_eventhistory_redaction`:

- **Dry run first** (call without a switch): it counts the affected versions per facility and how many of them are deferred because of a legal hold, changes nothing and exits with code 0 even when it finds some. If it reports 0 everywhere, nothing else needs to be done.
- **Then repair with `--apply`:** under a row lock, the affected versions receive the redaction marker the retention run wrote back then (the anonymization marker for an anonymized person). The timestamp of the original redaction stays, so the stage 2 period does not shift. Every repaired version leaves its own entry in the audit log (action "Event history redacted", category `eventhistory_repaired`). Versions under a legal hold stay unchanged; a later call after the hold ends repairs them. A second run finds 0.
- **Admin DB role only** (maintenance service, like `enforce_retention`): under the app role the command aborts instead of reporting clean data. The calls are listed in the [operations runbook § 3.5](ops-runbook.md#35-manual-execution).

**The same check for events, persons and cases** (#2188). Key rotation also writes the additional fields of events, persons and cases back. If it runs at the same time as a deletion or anonymization, a deleted event, an anonymized person or an anonymized case does not get its old values back, because the rotation locks the row. If a record nevertheless carries old values again (for example in a restored data set), it still counts as deleted or anonymized — and because every run only touches records that are not yet done, none catches up on it. With `repair_eventhistory_redaction --target all` the same command checks these three stocks and the rectification requests of anonymized persons (see below) in addition to the history, in the order person, rectification request, case, event, history; individually with `--target client`, `rectification`, `case`, `event` or `eventhistory`. Without `--target` it checks the history only.

- **Dry run and `--apply` as above.** The output is in English and reports `found`, `repaired`, `deferred (legal hold)` and `skipped` per stock and facility.
- **The repair restores exactly the state that deletion or anonymization leaves:** empty additional fields, and for an event also an empty search index. The deletion or anonymization timestamp stays. The not yet deleted event of an anonymized person is checked as well, because the anonymization empties its content too.
- **Rectification requests of anonymized persons** (`--target rectification`): the anonymization empties the old value, the requested value, the reason and the review note of every rectification request of the person; a key rotation without a row lock could write these contents back in the same way. The check covers requests whose person is anonymized and that still carry one of these contents; the repair empties them again. Status, requesting and reviewing person as well as the request and review timestamps stay, as the anonymization leaves them.
- **Legal hold as on the respective deletion path:** an event stays unchanged under a hold on the event, its person or its case; a person under a hold on the person, one of their events or one of their cases; a case under a hold on the case, one of its events or its person; a rectification request under a hold on its person, one of the person's events or one of the person's cases.
- **Every repaired row leaves its own entry** in the audit log (action "Deletion", category `event_repaired`, `client_repaired`, `case_repaired` or `rectification_repaired`). If a run repairs many rows in one facility at once, the mass deletion detection (`BREACH_DELETE_THRESHOLD`) may fire — that is then this run.
- **The connection must own every table it checks** (admin DB role). An RLS bypass alone is not enough; without ownership the command aborts before it reads a single row. For rectification requests this includes the persons table, whose anonymization state selects them.

#### K-Anonymization Instead of Hard Delete

As an alternative to hard deletion, **k-anonymization** can be enabled per facility (Refs #535).

| Field in `Settings` | Default | Description |
|---|---|---|
| `retention_use_k_anonymization` | `False` | Replaces hard deletion with k-anonymization. |
| `k_anonymity_threshold` | `5` | Minimum group size per bucket. Higher values → stronger anonymization, less detail. The form accepts values from 2. A smaller value that is already stored keeps being computed with (`1` stays in effect, `0` computes with 5) and is logged. For every effective value below 3 the settings page, the external report and the report preview show a risk notice; at `1` it states that no figure is suppressed. |

Effect: instead of deleting the record, identifying attributes are aggregated or pseudonymized so that statistical analyses over historical periods **remain valid** without re-identifying individuals. A GDPR-compliant alternative to permanent deletion when historical reporting must continue.

### 7.4 Audit Log

The audit log is **append-only** and immutable. Among other things, it automatically records the following actions. The table is a selection; the first column gives the German label under which the action appears in the audit log's **Aktion** (action) filter, which shows the complete list.

| Action (filter) | Trigger |
|---|---|
| Anmeldung, Abmeldung (login, logout) | Every login/logout |
| Anmeldung fehlgeschlagen (failed login) | Incorrect password |
| Data at the "With additional details" level viewed | Opening the **person detail page** (`/clients/<id>/`) of a person at that level — not the person list, an event, a case or a search result |
| Export | Data export by a user — including data subject access requests for individual people and on-screen views of the external report. **Released report files** (CSV/PDF/JSON/XML with a reporting period) are a subset: only those are exempt from the retention deadline and appear in the [release overview](#8-statistics-snapshots--materialized-view) |
| Deletion | Manual or via `enforce_retention` |
| Level change | Change in a person's contact status |
| Settings changed | Changes to facility settings |
| Download | Downloading an attachment from the file vault |
| Event updated | A domain edit to an event; details contain only the documentation type and changed field identifiers |
| Episode updated | A change to an episode's title, description or time period |
| Outcome goal updated | A change to an outcome goal's title or description |
| Event linked to case | A new or changed link between an event and a case |
| Event unlinked from case | Removal of the link between an event and a case |
| Attachment uploaded | Successfully adding or replacing a file on a person, case or event |
| Attachment deleted | Successfully deleting an attachment version chain; a documentation-type switch produces one aggregate row |

These seven edit, link and attachment actions store **no field contents, free text
or plaintext file names**. Edit rows name only the affected field identifiers;
attachment uploads use an HMAC fingerprint, a safe extension and technical
metadata. They are routine domain work and are therefore not exempt from
retention -- the standard 24-month period described below applies.

> **What the audit log deliberately does _not_ record:** ordinary list and detail requests (person list, event, case, work item) and **every search query** — neither the search term nor a counter or timestamp. This is a deliberate trade-off: the search term is personal data itself and would survive any anonymization in the append-only log. The rationale, the rejected alternatives (hashing, counters) and the residual risk that comes with it are documented in [ADR-043](../adr/043-suchraum-volltextsuche.md) (German); the processing register at `/system/vvt/` states the same in its technical measures.

**View in the admin:**

Under **Privacy → Audit log** (or `/audit/`), logs can be filtered by action, user, and time period; there is no facility filter. The application support (`facility_admin`) has access for their own facility. Pre-auth audit logs without a facility reference (e.g. `LOGIN_FAILED` for an unknown username) are visible exclusively to the system administration (`super_admin`) in the `/system/` area -- see [ADR-007 Update 2026-05-10](../adr/007-auditlog-append-only.md).

> ⚠️ **Operator note for your own queries: attachments carry two `target_type` strings** (Refs #1738, [ADR-039](../adr/039-owner-generische-dateianhaenge.md), German). The model is called `Attachment` (formerly `EventAttachment`). The audit log is append-only and secured by an HMAC chain — **older rows are therefore never rewritten**. Anyone evaluating attachment actions (downloads, rejected uploads, deletions) must query both strings:
>
> ```sql
> SELECT * FROM core_auditlog WHERE target_type IN ('Attachment', 'EventAttachment');
> ```
>
> The same applies to the `detail` field: rows carry `owner_type` + `owner_id` (values `event`/`client`/`case`), older rows only `event_id`. Download entries for **event** attachments keep `event_id` in addition, so existing queries do not break. Filtering on `Attachment` alone, or on `detail->>'event_id'` alone, yields a **silently incomplete** result.

**Audit log retention:** The audit log has its own automatic retention periods, independent of the domain periods. `prune_auditlog` measures each entry's age from `timestamp` and deletes expired entries unless a permanent exception listed below or an active target hold applies. Each facility uses `auditlog_retention_months` (default 24 months); entries without a facility separately follow `AUDITLOG_SYSTEM_RETENTION_MONTHS` (also default 24 months). An `enforce_retention` run not restricted to one facility also prunes that system chain. Both periods use exact calendar-month arithmetic; `0` disables automatic audit deletion for the respective chain.

Domain periods start from their own anchor for each kind of data (table in [§ 7.3](#73-retention-periods-and-enforce_retention)). For events at the `qualified` level, the default level period is 3650 days: for a closed case from `closed_at` (if missing, from `last_activity_at`, falling back to `created_at`), without a case from `occurred_at`, and for an open case from `last_activity_at` (falling back to `created_at`). Open and caseless events can therefore expire even without a type period. The standard run creates a deletion proposal for expired events with additional details and deletes only after approval; holds and Art. 18 are still checked. A positive type maximum period (`DocumentType.retention_days`) counts from the event date and can only shorten the level period; it does not bypass approval.

An active legal hold also protects audit entries **with a facility** whose `target_type` and `target_id` identify a protected person (`Client`), case (`Case`) or event (`Event`). This includes directly held targets and targets protected through their person/case/event relationships as defined in [`audit_pruning.py`](../../src/core/retention/audit_pruning.py). The hold protects this target set; other target types, mere mentions in the detail and entries without a facility receive no protection from the audit period through it. Once a hold is lifted or expires, entries whose audit period has expired can be deleted in a later run.

Audit traces can therefore disappear before the related domain data. Even an audit period as long as the longest domain period does not necessarily cover the entire lifetime of the domain data: an event is recorded in year 0 and its case is closed in year 5. Without an earlier type deadline, protection or delay, the event is deleted after the ten-year level period and approval, around year 15; a ten-year audit period can delete the unprotected audit trace of its recording in year 10. Complete audit retention without routine pruning requires `0` for the respective audit chain; a hold protects only its target set. Other values must be weighed against the required duration of evidence retention.

The settings page compares the audit period, approximated as months × 30 days, with the domain periods (levels, activities, handover notes, trash, completed requests, history) and the type periods of all event documentation types of the facility, including inactive ones. If it is shorter than the longest of these periods, a notice shows both values and the longest period, with the type name for a type period. Domain periods where `0` means unlimited are included; level periods must be at least one day. With an audit period of `0`, the notice disappears. This comparison does not promise complete evidence over the lifetime of the domain data ([ADR-021, addendum 2026-10-10](adr/021-retention-model.md#addendum-2026-10-10)).

| Setting in `Settings` | Default | Effect |
|---|---|---|
| `auditlog_retention_months` | 24 months | Every `enforce_retention` run deletes expired entries of the facility unless a permanent exception or an active target hold protects them (exact calendar-month arithmetic, not a 30-day approximation). `0` = **disabled**, the log then grows without bound. |

Five classes are **exempt** from this period and are kept regardless of age (`PRUNE_EXEMPT_Q` in [`src/core/retention/audit_pruning.py`](https://github.com/anlaufstelle/app/blob/main/src/core/retention/audit_pruning.py)). Four of them depend on the action (`PRUNE_EXEMPT_ACTIONS`):

- `SECURITY_VIOLATION` — forensic evidence from breach detection,
- `RETENTION_RUN_COMPLETED` — the marker the compliance dashboard reads to show the last successful retention run,
- `AUDIT_PRUNE_CHECKPOINT` — the tombstone of the pruning itself,
- `EVENT_HISTORY_REDACTED` — the per-batch proof of the history sweep (see [§ 7.3 "Retention of the Event History"](#retention-of-the-event-history)). It documents that immutability was legitimately opened for exactly these rows; if it vanished before the rows it vouches for, a silent change to append-only-protected data would remain.

On top of that — **regardless of the action** — comes the fifth class: entries about a **released report file carrying a reporting period** (CSV/PDF/JSON/XML). Without that exemption the release overview would feign completeness. Such entries hold the period, the format, the acting person and the recorded recipient/purpose fields (heuristically validated free text), and are kept indefinitely. Plain on-screen views of the external report and subject access requests without a reporting period run under the same audit action but stay subject to the period — the distinction is documented in [§ 8 "Releasing Reports"](#releasing-reports-what-you-need-to-know).

**Checkpoint mechanics:** the audit log is chained via HMAC (every entry references the previous one). Pruning necessarily tears that chain. So that the integrity check does not report the gap as tampering, every prune run writes an `AUDIT_PRUNE_CHECKPOINT` entry recording the chain boundaries of the deleted rows, which legitimises the discontinuity. The chain itself is **never** rewritten — the log stays append-only.

> **Important:** the period only takes effect if `enforce_retention` actually runs (see [§ 7.3](#73-retention-periods-and-enforce_retention)). Deleted entries are gone for good — if you need longer evidence retention, define an **external** archiving strategy (e.g. following BSI recommendations or your Data Protection Impact Assessment) and export the log before the period kicks in.
>
> **Residual data-protection risk:** because the log is append-only, anonymising a person does **not** redact it — details and countermeasures in (DE: [`security-notes.md` § Audit-Log-Restdaten überleben die Art.-17-Löschung](../security-notes.md#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753)) (German).

### 7.5 Deletion Requests (Four-Eyes Principle / Dual Approval)

Completed requests remain visible for the configured period (default 1095 days). With existing events, downgrading from additional details to pseudonym only also requires a request and another person with deletion confirmation permission. Person records and trash display the number of pending history versions after anonymization; hold, Art. 18 or concurrent editing can defer regular nightly redaction.

Deletion requests for personal data are processed using the four-eyes principle (dual approval): a request must be approved by a **second authorized person** before data is permanently deleted. This protects against accidental or unauthorized deletion — including by privileged roles themselves.

**Approver pool — "deletion confirmation" permission** (Refs #1053): Who may approve is not derived from the user's role; instead, approvers are curated via the individually assignable user permission *deletion confirmation* (`can_confirm_deletion`). The first account created by `setup_facility` holds the permission. When an account is created under **Management > Access** or in the Django admin, "Löschbestätigung" is set to "Nach Rolle" (by role): leads and facility admins receive the permission, other roles do not; "Ja" (yes) and "Nein" (no) override the preset. Make sure **at least two active people** hold the permission so that a second person is available for every request; under **Management > Access** it can also be revoked or granted to, for example, an experienced staff member. The compliance dashboard (`/system/compliance/`) shows the number of active approvers per facility in the card "Genehmiger für Löschanträge" (approvers for deletion requests): none = critical, exactly one = warning (that person's own requests stay open), two or more = OK. A permission holder can never approve their own requests; the view, service, and database layers all enforce **requester ≠ approver**.

The immutable audit log tracks the permission from account creation on: the creation entry (`USER_CREATED`) records the initial value (`can_confirm_deletion` yes/no) — with the acting person when the account is created via Management → Access or the Django admin, as a system entry for the initial setup with `setup_facility`. Every later change (grant or revocation) is recorded as a separate `DELETION_CONFIRMER_CHANGED` entry with the old and new value, as is every approval. If an open request has no active approver other than the requester, the deletion request list displays a prominent warning ("No possible approver") — grant the permission to an additional person to resolve this.

#### Who sees the "Deletion requests" tab (Refs #1736)

Deletion requests live in the **"Deletion requests"** tab of the **"Privacy"** area (`/datenschutz/`). What the sidebar carries is not the individual tab but the area entry "Privacy" — it appears as soon as at least one of its tabs is visible. Visibility of the tab no longer follows the role alone, but actual need:

| Who | Sees the tab | Sees in the list |
|---|---|---|
| Manager, facility admin | yes | **all** requests of the facility |
| Holder of the *deletion confirmation* permission (without a lead role) | yes | **all** requests of the facility |
| Requester with at least one **open** request of their own | yes, while the request is open | **only their own** requests |
| Everyone else | no | — |

The last case is the **feedback channel**: whoever files a request can find it again via the navigation and via a notice banner on the affected record, and can see its status — without thereby gaining access to other people's reasons and target pseudonyms. Once the request is decided, the tab disappears again — and with it the "Privacy" area entry, unless another tab of that area stays visible.

A pending request is now visible on the record itself as well (a marker in the client list, a banner with date and requester on the client detail page); the "Request deletion" action is disabled while it is pending, and a second request is not possible.

#### Trash: "Anonymize permanently now" (Refs #1737)

An approved deletion moves the person into the **trash** (`/clients/trash/`, facility admin). They stay restorable there until the trash period (`client_trash_days`, default 30 days) expires and `enforce_retention` anonymizes them automatically.

Next to "Restore", that page offers the action **"Anonymize permanently now"**, which brings the anonymization forward instead of waiting for the period:

| Aspect | Behaviour |
|---|---|
| **Effect** | **Irreversible.** Pseudonym, notes, custom fields as well as case and event content are removed; what remains is an anonymous statistical aggregate. Restoring is **no longer** possible afterwards. A confirmation dialog states this explicitly. |
| **What happens immediately — and what with the nightly run** (Refs #2085) | **Immediately:** pseudonym, notes, custom fields, case and episode content, the content of the events themselves, the timestamp generalization and the file attachments. The person carries no personal data afterwards. **With the next retention run** (`enforce_retention`, 03:00 at night by default): the **older versions** of the event documentation — the version history that every save of an event creates. The reason is the database role split: those versions are write-protected, and the web process deliberately does **not** hold the required privileges (see `ops-runbook.md` § 9.5). The success message and the confirmation dialog say so explicitly. If an erasure has to be completed immediately, the system operators run the retention run once by hand. A **legal hold** that arrives within this window defers the versions — they are then kept in full. |
| **Purpose** | (a) **Immediate erasure under Art. 17** when a data subject insists on erasure without delay and 30 days of trash are not defensible. (b) **Resolving configuration blockers**: a person in the trash keeps their document type and field values and thereby blocks deleting that type or field template. Previously the only options were restoring (a short-lived data resurrection) or waiting. The blocker message in the configuration UI now names the trash share and links there. |
| **Limit: legal hold** | If an **active legal hold** exists on the person or on one of their events, the action is rejected with an error message — exactly like the nightly retention run (Refs #1066, #1756). A manual action must not override a hold. Lift the hold first, then anonymize. |
| **Limit: trash only** | The action applies exclusively to records that are already soft-deleted. The four-eyes request path remains the only way **into** the trash — there is no shortcut around approval. |
| **Trail** | Audit entry `CLIENT_ANONYMIZED` with `trigger="manual"`, distinguishable from the automatic run. |

The record stays soft-deleted afterwards (anonymized, not restored) and does not vanish from the trash listing — it simply no longer carries personal data. **"Restore" is no longer offered for that row afterwards** — the confirmation dialog's promise holds literally: the button disappears, and calling the restore URL directly is rejected with a message (Refs #2085). The person action now behaves like the case action, which has this guard as well ([ADR-044](../adr/044-fall-loeschpfad.md)). The success message therefore reads "Person wurde anonymisiert. Frühere Fassungen ihrer Ereignis-Dokumentation tilgt regulär der nächtliche Aufbewahrungslauf; was eine Aufbewahrungssperre oder eine gleichzeitige Bearbeitung zurückhält, holt ein späterer Lauf nach." — it deliberately does not claim that everything is already gone at that moment, nor does it promise a fixed deadline: the run defers versions under an active legal hold and skips records that another operation currently holds locked (Refs #2425). People in the trash can also not be edited via a direct URL (`/clients/<id>/edit/` returns 404, like the detail view; #1737).

#### Case trash and the two deletion paths (Refs #1818)

Since [ADR-044](../adr/044-fall-loeschpfad.md), a **single case** can be deleted — until then Art. 17 knew only two granularities (one event or the entire person). The path mirrors the one for persons, with four differences worth knowing:

| Question | Answer |
|---|---|
| **Who deletes?** | Social workers and above, from the case file ("More actions" → **Delete**). |
| **Four eyes or direct?** | **Staged by the client's contact stage** — at "With additional details" a deletion request is created that a second person holding the *deletion confirmation* right must approve; otherwise the case moves straight to the trash. The same staging already applies when deleting an **event** of that person. Approval happens in the same list under **Privacy → Deletion requests** as for persons and events. |
| **Which period?** | **None of its own.** `client_trash_days` (§ 2.3) applies — deliberately no second notion of a deadline that would have to be explained and maintained in the privacy notice and the impact assessment. |
| **What happens when it expires?** | The case is **redacted, not hard-deleted**: title, description, custom fields, episodes, outcome goals, milestones and the case's own files (including the encrypted blobs) are gone; the case row remains as an anonymous aggregate. A hard delete would take episodes, goals and milestones with it by cascade. |

The facility admin opens the **trash** (`/cases/trash/`) from the header of the case list; like the person trash it appears in no navigation. Each row offers **"Restore"** (which brings episodes, goals and attachments back in full) and **"Anonymize permanently now"** — the same bring-forward action as for persons, with the same limits: only from the trash, never twice, and **not** while an active legal hold exists (on the case, on one of its events, or on the person). A case that has already been anonymized for good is labelled as such and no longer offers "Restore" — it would only bring back an empty shell.

Three points that come up regularly in operation:

- **Assigned contacts stay with the person.** They neither block the deletion nor get deleted with it; their case assignment is only dropped at the final purge (until then a restore would be lossy).
- **Open cases can be deleted too.** A case created by mistake is always open; "closing" it first would be a false statement about a course of support that never happened.
- **A case in the trash blocks configuration changes** exactly like a person in the trash: it holds on to its document type and field values. The deletion dialogs under *Configuration* name that share and link to the case trash.

Audit trail: `CASE_SOFT_DELETED`, `CASE_RESTORED`, `CASE_ANONYMIZED` (with `trigger="manual"` or `"trash_days_expired"`), plus the request steps `DELETION_REQUESTED`/`DELETION_APPROVED`/`DELETION_REJECTED`. The **case title appears in no audit entry** — the log survives every later anonymization. The nightly retention run reports the additional line `Anonymized N trash-expired case(s)`.

#### Client merge: requests and trash tombstones (Refs #1876)

Since [ADR-045](../adr/045-personen-merge.md), duplicate client records can be **merged**. Relevant for operation:

- **Same request list, its own request kind.** A merge request runs through **Privacy → Deletion requests**, staged like a deletion: if one of the two clients carries "With additional details", a second person holding the *deletion confirmation* right decides; if both are at "Pseudonym only", the lead may merge directly. The list labels merge requests as **Merge → target**; the review page shows both records with their record counts. Only **one** destructive request runs per client at a time — deletion *or* merge.
- **The trash knows tombstones.** The dissolved source sits under `/clients/trash/` with the note "merged into <target>". Instead of "Restore" (which the service refuses — a plain restore would resurrect the record without its migrated data), the row offers **Revert merge**: the migrated events, cases, tasks and adopted attachments return; entries newly documented since the merge stay with the target. Once `client_trash_days` expires, the normal retention run redacts the tombstone like any trash client; the reference to the target remains as whereabouts evidence, and nothing can be reverted anymore.
- **Limit: legal hold.** If an **active legal hold** covers the target client or the source — including indirectly through one of their events or cases — **Revert merge** is refused with an error message (Refs #2200), exactly like merging itself. Reason: reverting moves the events back to the source while the hold stays on the target client — it would no longer cover them although it still applies. The hold is deliberately **not** mirrored onto the source silently: a legal hold is a deliberate act of preserving evidence. Lift the hold or let it expire first, then revert the merge.
- **Offline devices purge the source.** The merge revokes the offline bundles of the dissolved record (client-precise, as with a deletion). Events documented offline for the source but not yet transmitted are deliberately **not** rebooked to the target automatically — they land in the dead-letter list of offline conflicts for follow-up.
- **No mass-deletion alarm.** A clean-up campaign over old duplicates does not count into the mass-destruction detection (`CLIENT_MERGED` is its own, uncounted action) — this is consolidation, not destruction.

Audit trail: `CLIENT_MERGED` (on the source, with the reversal inventory in `detail`); reverting writes `CLIENT_RESTORED`; request and rejection use the generic `DELETION_REQUESTED`/`DELETION_REJECTED`, while approval deliberately writes **no** `DELETION_APPROVED`.

### 7.6 Data Subject Rights (Art. 15--20 GDPR)

The following administrative capabilities are available for handling requests from data subjects:

| Right | Action |
|---|---|
| Access (Art. 15) | Generate the data access record as JSON or PDF from the client view (lead or application support, re-authentication required) -- scope see below |
| Rectification (Art. 16) | File a rectification request in the person record (section "Berichtigung (Art. 16 DSGVO)" → "Berichtigung beantragen", Social worker and above); a Manager or Application support reviews it under **Berichtigungen** ([ADR-041](../adr/041-berichtigungsworkflow-intern.md)). The admin interface shows persons, cases, events and work items read-only |
| Erasure (Art. 17) | Submit a deletion request through the application (four-eyes principle) |
| Restriction (Art. 18) | Set a **restriction marker** on the client record — see below |
| Data portability (Art. 20) | Export function in the application |

#### Restriction of processing: the restriction marker (Art. 18 GDPR, Refs #2045)

Where a client contests the accuracy of their data, or where any other ground under Art. 18(1) applies, set a **restriction marker** on the client record. Its effect:

| Question | Answer |
|---|---|
| **What remains permitted?** | **Storage** (Art. 18(2)). The record is not deleted, not hidden, and stays in the access record and in statistics. |
| **What is blocked?** | Substantive work: the client can no longer be edited, nor assigned to a new contact, case or task. |
| **What is exempt?** | The **rectification workflow** (Art. 16, § 7.6). It is the path on which the contested accuracy is examined — a restriction that blocked it too would be a dead end. |
| **Who sets it?** | Social worker and above, from the client record. When a rectification request is filed, the marker is additionally **offered** (opt-in, not pre-selected). |
| **Who lifts it?** | A **second** authorised person or a **manager**. Whoever set the marker does not lift it alone. |
| **Visibility** | A permanent banner on the client record showing legal ground, timestamp and the person who set it; a "restricted" marker in the client list. |
| **Trail** | Audit entries `PROCESSING_RESTRICTED` and `PROCESSING_RESTRICTION_LIFTED`, each carrying the legal ground — never the contested matter itself. |

**Not to be confused with the legal hold** (§ 7.3): that one blocks *erasure* (Art. 17(3)), the restriction marker blocks *use*. The two are independent of each other.

**To be handled organisationally:** the software does not enforce the duty to inform the data subject **before** lifting the restriction (Art. 18(3)) — record it in your data subject request process.

#### History Diff: Decryption for Authorized Roles

On an event's detail page, **Change history** shows the stored before/after states. Encrypted fields are decrypted at display time if the current role may see the field according to the documentation type and field sensitivity: assistants up to **Normal**, social workers up to **Elevated**, and leads and application support up to **High**. Above the role boundary the value remains `[Eingeschränkt]`; without an authenticated user, an encrypted value stays fail-closed as `●●●●●`. File markers always appear as `[Anhang]`, never as a technical ID. These fixed history placeholders are currently displayed in German in both interface languages.

This display rule applies **retroactively to the entire existing history**. Authorized roles can therefore also read earlier values that were later rectified or removed. Before implementation, this access was explicitly approved for the same roles that can already see those values in the detail/edit views and in the Art. 15 access record (#1961); take it into account when assigning roles, maintaining the data-protection impact assessment, and handling access requests. A redacted deletion-history entry contains no old value and therefore only shows `– (gelöscht)`.

#### Scope of the Access Record (Art. 15)

The access record is generated from the client view as **JSON** (machine-readable, Art. 20) or as **PDF** (human-readable document). Both formats cover the same scope; the PDF summarizes but omits no category. Only roles with export permission (lead, application support) may trigger it; the request requires re-authentication (sudo mode), is rate-limited, and is itself recorded in the audit log.

Included (Refs #1752):

| Category | Content |
|---|---|
| Master data | Pseudonym, contact stage, age group, **notes**, active flag, k-anonymization flag, trash status (deleted at/by), creation and modification timestamps, custom fields including orphaned fields (decrypted) |
| Cases | Title, description, status, dates, case lead, trash status, custom fields |
| Deleted cases | Cases in the trash as a **separate section** (Refs #1818): the same fields plus "deleted at/by" and the purge timestamp — until the final anonymization they are stored data and belong in the access record |
| Episodes | Title, description, start/end per case |
| Outcome goals | Goals with milestones and achievement status |
| Events | Document type, timestamp, case/episode reference, field data (decrypted) |
| Deleted events | Existence of events not yet purged (trash) together with the **deletion timestamp and the user who triggered it** (Refs #2157) -- for a deletion by the retention run the user stays empty, because nobody acts there |
| Change history | Action, timestamp, editing user **and the before/after contents** -- including for deleted events |
| Deletion requests | Requests targeting events, cases **and the client record itself**, with reason and four-eyes review |
| Rectification requests | Art. 16 requests concerning the client (Refs #1796, [ADR-041](../adr/041-berichtigungsworkflow-intern.md)) in **all** states (pending, approved, rejected): the field concerned, previous and requested value (decrypted), reason, review note, requesting and reviewing user, timestamps |
| Retention | Legal holds (Art. 17(3) — **not** Art. 18) and open deletion proposals with due date and category |
| Restriction of processing | Art. 18 restriction marker (Refs #2045), where set: legal ground, timestamp and the person who set it |
| Tasks | Title, description, type, status, priority, assignment, dates |
| Activities | Activity feed entries about the client, their cases, events and tasks |
| Recently viewed | Timestamps at which the client record was opened or bookmarked |
| Merges | Provenance after duplicate merges (Refs #1876, [ADR-045](../adr/045-personen-merge.md) §7): which record was merged into this one and when, and whether it has already been anonymized for good; the access record of the dissolved record conversely states "Merged into" |
| Access log | Audit entries about the client, their cases and their events: action, timestamp, reference — including the steps of the four-eyes workflows (Refs #2048): deletion requests appear under the category “Löschantrag” (requested/approved/rejected, from the client, case and merge paths), rectification requests under “Berichtigung” |
| File attachments | **Metadata** for every attachment on the client, their cases and their events (Refs #1738): field name and field slug, decrypted filename, file size, MIME type, timestamp and uploading user, plus the state within the version chain (current yes/no, superseded at, removed at) -- **the complete chain including superseded and removed links** |

**On the structure of the attachment data (JSON):** client-owned attachments live under the top-level key `attachments` -- it is **always** present, possibly as an empty list. Case- and event-owned attachments appear as `attachments` **inside the respective entry** under `cases[i]` or `events[i]`, and only when non-empty (so the existing format for records without files stays unchanged); deleted events (`deleted_events`) carry them too. If a filename cannot be decrypted, the value reads `[verschlüsselt]` -- the record does not invent a name. The MIME type is the type **verified** at upload time; only existing rows without detection fall back to the browser-reported one. In the custom fields themselves, the technical file marker is replaced by the placeholder `[Dateianhang – Angaben unter „Dateianhänge"]`. **In the PDF**, client-owned attachments form their own section "Dateianhänge"; case- and event-owned attachments appear as a "Dateianhänge" column in the respective table.

**On the structure of the deleted cases (JSON):** they live under the top-level key `deleted_cases` -- it is **always** present, possibly as an empty list; `cases` lists only the **live** cases (#1818). Both lists carry `is_deleted`, `deleted_at`, `deleted_by` and `anonymized_at` per entry. The last value answers what `is_deleted` alone leaves open: if it is set, the case contents have been **removed irreversibly** (the row deliberately stays, § 7.5); if it is empty, the case merely sits in the trash and can be restored. Episodes and outcome goals of a deleted case stay in **one** list (`episodes`, `outcome_goals`) -- their case title maps them to either section. **In the PDF**, cases in the trash form their own section "Fälle im Papierkorb" directly after "Fälle".

**On the structure of the deleted events (JSON):** they live under the top-level key `deleted_events` -- it is **always** present, possibly as an empty list; `events` lists only the **live** events. Both lists carry `is_deleted`, `deleted_at` and `deleted_by` per entry. The event reference inside the change history deliberately stays narrow (document type, timestamp, trash flag) -- it only maps the change to its event; the deletion data lives in the `deleted_events` section itself. There is deliberately **no** `anonymized_at` here: unlike a case, an event's contents are already removed at deletion time, so there is no second period until the purge -- what remains is in the change history. **Events whose deletion data was not recorded carry `null` in both fields**: the data cannot be reconstructed retroactively (least of all who deleted the event) -- the access record states the gap instead of inventing a value. If `deleted_at` is set and `deleted_by` is empty, the retention run deleted the event (period expired, no acting user). **In the PDF**, the section „Gelöschte Ereignisse (Papierkorb)" carries the same two columns „Gelöscht am" and „Gelöscht von" as the section „Fälle im Papierkorb"; instead of an empty cell it shows „Aufbewahrungslauf" (system deletion) or „vor 0.96 nicht erfasst" (deletion data missing), so the data subject can tell the three cases apart. These fixed placeholders are currently displayed in German in both interface languages.

**On the structure of the merges (JSON):** they live under the top-level key `merged_from` -- it is **always** present, possibly as an empty list. Per merged record: `pseudonym`, `merged_at` and `anonymized`. The pseudonym is read from the **live** trash row: as long as the dissolved record sits unredacted in the trash, it appears in clear text; after the retention run purges it, only the redacted marker remains (`anonymized: true`) -- the access record thus carries the clear-text pseudonym exactly as long as the trash itself. Conversely, the access record of the dissolved record carries the field `merged_into` in its `client` block (present on tombstones only; "deleted at" is then also the merge timestamp), and a merge request in `deletion_requests` is marked with `merge_request: true`. **In the PDF**, the merges form their own section "Zusammenführungen" at the end, and the tombstone reference appears as the master-data row "Zusammengeführt in". **Important for Art. 15 requests covering the full history:** the access log of the dissolved record stays with its own dataset (append-only) and does **not** flow into the target's export -- answer such a request with **both** exports (target record and tombstone; the latter stays exportable via the trash or the record URL, even after redaction).

**On the structure of the rectification requests (JSON):** they live under the top-level key `rectification_requests` -- it is **always** present, possibly as an empty list. Per entry: `field_path` (the technical field identifier, `pseudonym`/`age_cluster`/`notes` or `data_json.<slug>`) and `field_label` (the human-readable field name; for a custom field removed in the meantime it falls back to the raw path), `status`, `old_value`/`new_value`, `reason`, `review_note`, `requested_by`/`reviewed_by`, `created_at`/`reviewed_at`. Values are decrypted and rendered readably: an age group appears as "18–26" instead of `18_26`, a select field with its option label. If the client has been anonymized in the meantime, all four value/free-text columns are cleared (Refs #1792) -- the request remains as four-eyes evidence with empty values (rendered as "—" in the PDF). If a value cannot be decrypted, it reads `[verschlüsselt]`. **In the PDF** the requests form their own section "Berichtigungsanträge" directly after the deletion requests; the corresponding audit rows (requested/approved/rejected) appear in the access log under the category "Berichtigung".

**Deliberately excluded -- review as a manual supplementary step:**

1. **Names of the accessing staff members in the access log.** The log carries action, timestamp and reference, but no actor identity: "who accessed what and when" is simultaneously an access log about employees and therefore third-party data (Art. 15(4) GDPR). The same applies to the recently-viewed list. If the data subject requests these details as well, a case-by-case assessment is required -- the raw data is available to the application support under `/audit/`.
2. **Binary contents** of any kind (files, images). The record names every file but does not ship it; release the file itself via the regular download from the detail view if required.

The PDF notice box states both points explicitly to the data subject -- the record does not claim a completeness it does not have.

### 7.7 Recommended Organizational Measures

- Maintain a **Record of Processing Activities** (Art. 30 GDPR / Verzeichnis der Verarbeitungstaetigkeiten), describing the use of Anlaufstelle as a processing activity.
- Ensure the hosting provider has signed a **Data Processing Agreement** (Art. 28 GDPR / Auftragsverarbeitungsvertrag -- AVV under German law).
- Conduct a **Data Protection Impact Assessment** (Art. 35 GDPR / Datenschutz-Folgenabschaetzung -- DSFA under German law) if your facility processes particularly sensitive data (e.g., health data, data of vulnerable individuals).
- Restrict admin access to the minimum necessary (**Least Privilege**).
- Enforce a password change wherever a password is known **outside** the application: for the fallback without an email address, account creation sets `must_change_password` itself; for a password you assign by hand afterwards, tick the **Passwort muss geändert werden** ("Password must be changed") field in the user edit form. For accounts **with** an email address the invitation flow replaces this measure -- the user picks their own password there, and the completed setup step clears the flag again (§ 2.4, #1969).
- Store backups and the `ENCRYPTION_KEY` in separate, secured locations.

### 7.8 Optimistic Locking

To protect against **silent overwrites** when two users edit the same record in parallel (two staff members have the same client/case open at the same time and save one after another), Anlaufstelle applies **optimistic locking** at the service layer. Refs #531.

**Affected models:** Client, Case, WorkItem, Settings, Event.

**Mechanics** ([`src/core/services/security/locking.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/locking.py)):

- Every form renders the current `updated_at` as a hidden field.
- On save, the helper `check_version_conflict(instance, expected_updated_at)` verifies whether the record has been modified in the meantime.
- A conflict raises a `ValidationError`. The standard HTML edit forms for clients, cases and events save nothing; while access remains permitted, the form stays bound to the submitted input. A separate “Current state” section shows only server values permitted for the role and marks differences. Review or copy the input, then deliberately save again: the form values replace the corresponding server values on the displayed baseline; another intervening change causes another conflict (#2539).
- Other save and upload errors also redisplay permitted text but keep the submitted version guard. Select files again; affected file fields are named. If access to the record is lost, the access rejection still applies. JSON/offline replay conflicts retain their existing response contract.

**Administrative notes:**
- There is no admin toggle to disable locking -- the protection is system-wide.
- If users see the conflict message frequently: review the workflow (separate concurrent editing, adjust processes) rather than work around the feature.

### 7.9 Row Level Security (RLS)

In addition to ORM-side facility scoping, **PostgreSQL row-level security** is enabled on **all facility-scoped tables** (enforced derivation-based via a test) as **defense-in-depth**. A buggy ORM query that forgets facility scoping still returns no foreign data, thanks to RLS. Refs #542, #586.

#### How It Works

- The middleware [`FacilityScopeMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/facility_scope.py) sets two Postgres session variables per request in one call: `app.current_facility_id` (ID of the signed-in person's facility) and `app.is_super_admin` (`'true'` for system administration sessions, otherwise empty), each via `set_config(…, false)` (session scope, not transaction scope).
- The RLS policies (migration [`0047_postgres_rls_setup`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py)) filter every row via `facility_id::text = current_setting('app.current_facility_id', true)`. Migration [`0085_rls_superadmin_bypass`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0085_rls_superadmin_bypass.py) adds the branch `OR current_setting('app.is_super_admin', true) = 'true'` to all of these policies: system administration sessions thereby bypass facility isolation (the AuditLog records their reads as `SYSTEM_VIEW`, § 7.2).
- **Fail-closed only without the super-admin branch:** if `app.current_facility_id` is empty or unset **and** `app.is_super_admin` is not `'true'`, `current_setting(..., true)` returns an empty value or NULL, the comparison matches nothing, and **no rows** are returned. If `app.is_super_admin` is `'true'`, the policy releases all rows, even with an empty facility scope; that is the normal case for system administration.
- The policies filter the app role (`POSTGRES_USER`, `NOBYPASSRLS`). The admin role (`POSTGRES_ADMIN_USER`, `BYPASSRLS`) used for migrations, maintenance and the retention run, as well as the bootstrap superuser, bypass them.
- The middleware sets both variables afresh on **every** request, including anonymous ones: there both are empty. This way connection pooling cannot pass on a leftover value from an earlier request.
- For an archived facility the middleware empties the facility scope only for requests it blocks (response 403). Not blocked are system administration requests and the paths in `ARCHIVED_EXEMPT_URLS` (`/login/`, `/logout/`, `/static/`, `/i18n/`, `/health/`, `/sw.js`, `/manifest.json`); for a signed-in staff account of the archived facility, `app.current_facility_id` still carries its facility's ID there.

#### Debugging in `psql`

```sql
-- Inspect the current facility scope of a session
SELECT current_setting('app.current_facility_id', true);

-- Set the variable manually for a debug session
SELECT set_config('app.current_facility_id', '<facility-uuid>', false);
```

With the app role and without the variables set, the protected tables visibly return **no rows** in `psql` -- this is by design. Under the admin role or the bootstrap superuser RLS does not apply; there you see all rows.

---

## 8. Statistics Snapshots & Materialized View

### Overview: Two Layers

Statistics reporting in Anlaufstelle uses **two different acceleration layers**:

1. **Materialized view** (`core_statistics_event_flat`) -- pre-aggregates current event data so the statistics page does not have to scan all events on every request. Refs #544.
2. **Statistics snapshots** -- monthly, persisted aggregates that keep counting the events removed by automatic deletion. Historical reports therefore also count events the retention run has deleted.

### Refresh the Materialized View

The materialized view is **not updated live** on every `INSERT` -- it must be rebuilt periodically via a management command:

```bash
# Default (non-blocking, uses CONCURRENTLY when possible)
docker compose -f docker-compose.prod.yml run --rm maintenance \
  refresh_statistics_view

# Blocking (if there is no UNIQUE index)
docker compose -f docker-compose.prod.yml run --rm maintenance \
  refresh_statistics_view --no-concurrent
```

Implementation: [`src/core/management/commands/refresh_statistics_view.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/refresh_statistics_view.py).

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only if the `cron` service is not running; it starts the job through the `maintenance` service of the same compose file, the only service that receives the admin role (BYPASSRLS) (without Compose, reproduce it in your own orchestration: same image, entrypoint `python manage.py`, admin role). Setting up both makes the job run twice — details: [ops runbook § 3](ops-runbook.md#3-cron-jobs).

**Recommended cron cadence:**

```cron
# Hourly (near real time) -- for heavily used statistics pages
0 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance refresh_statistics_view >> /var/log/anlaufstelle-statistics.log 2>&1

# Alternative: once a day at night (for one-off reports) -- pick one
# 0 1 * * * …
```

> **Note:** `CONCURRENTLY` does not block reads but requires a UNIQUE index on the materialized view (created by migration). The command automatically falls back to a non-concurrent refresh on error.

### What Are Statistics Snapshots?

Anlaufstelle computes statistics (dashboard, half-year reports, example activity report PDFs) from the event table by default (aggregated via the materialized view). When automatic data deletion (`enforce_retention`) removes old events, those events would disappear from the statistics.

**Statistics snapshots** persist monthly aggregates that keep counting events deleted by the retention run. Reports use hybrid logic: stored snapshots for past months, live data (from the materialized view) for the current month.

> **Important: snapshot protection does not cover every report.** The Overview reads event counts from monthly aggregates with live fallback; person metrics remain live. The Overview has no PDF export. On **Dataset reports**, only the **example activity report PDF** reads monthly aggregates with live fallback for missing snapshots and partial months; distinct persons are counted live across the entire period there too. Its preview and CSV/JSON/XML, and all formats of other reports, are live. After `enforce_retention` cleans up records, live figures can therefore be lower, down to zero. The effect depends on the population. **Retain released files yourself:** they cannot necessarily be reproduced identically later. Check the source for each format and the rules under [“Releasing Reports”](#releasing-reports-what-you-need-to-know).

### Automatic Capture

Snapshots are created automatically whenever `enforce_retention` deletes events: in the same transaction in which the run deletes an event, it adds the event to the aggregate of its month, including the current month -- if the run aborts, no event already deleted is missing from the aggregate, and if two runs overlap, every event counts exactly once. The deletion run extends the carry-over of deleted events and recalculates the month from that carry-over and the remaining events in the same transaction. This also counts events recorded after the previous snapshot without doubling contributions already counted. Every recalculation of a month with `create_statistics_snapshots` counts the remaining events and adds the deleted ones. If the run cannot add an event to the aggregate (for example because a report module raises an error), it does not delete the event, writes the error as `ERROR` with the event id to the application log and tries again on the next run -- until the cause is fixed, the deletion is delayed. The event deletion service (including four-eyes approval and tally undo) also carries deleted events into the monthly aggregate; they continue to count after recalculation. The "Statistics" card on the lead's dashboard shows the latest completed month as "last snapshot".

Recalculation requires the carry-over (`_deleted`) maintained by this version; data from versions before carry-over was introduced is not reconstructed.

### Periodic Cron Job (Recommended)

It is also recommended to create snapshots regularly via cron:

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only if the `cron` service is not running; it starts the job through the `maintenance` service of the same compose file, the only service that receives the admin role (BYPASSRLS) (without Compose, reproduce it in your own orchestration: same image, entrypoint `python manage.py`, admin role). Setting up both makes the job run twice — details: [ops runbook § 3](ops-runbook.md#3-cron-jobs).

```cron
# Monthly on the 1st at 04:00 (like the sidecar) -- captures the previous and historical months
0 4 1 * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance create_statistics_snapshots >> /var/log/anlaufstelle-snapshots.log 2>&1
```

### Initial Setup (Backfill)

During initial setup or when enabling snapshots later, snapshots can be created for all existing months:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance create_statistics_snapshots --backfill
```

**Note:** Backfill uses past months from the union of months with remaining events and stored snapshot months, including months containing only carried-over deleted events. Deleted data without such carry-over cannot be reconstructed retroactively. The regular run without a specific month also recalculates these past months and the previous month, picking up late entries and changes. The displayed calculation date (`updated_at`) changes only when recalculation changes the published aggregates or backend payloads; carry-over bookkeeping alone does not advance it. Person-count lower bounds are merged across segments by maximum rather than addition, keeping small cells suppressed.

### Additional Options

All calls run in the `maintenance` service. Under the app role (`exec web`) the run would see no facility settings, skip every facility ("No settings … skipping") and still end with exit 0.

```bash
# Preview (no changes)
docker compose -f docker-compose.prod.yml run --rm maintenance create_statistics_snapshots --dry-run

# Only a specific facility
docker compose -f docker-compose.prod.yml run --rm maintenance create_statistics_snapshots --facility "My Facility"

# Specific month
docker compose -f docker-compose.prod.yml run --rm maintenance create_statistics_snapshots --year 2026 --month 2
```

### Limitations

- **CSV export:** The CSV export still contains only existing events (no snapshot data), because it exports individual rows.
- **Top persons:** The ranking of the most active persons is always computed live and may change after a deletion.
- **Distinct persons:** The figure is counted **live** and distinctly across the whole reporting period, not summed from the monthly aggregates (#1770). For periods whose events were removed once the retention deadlines expired it therefore comes out lower than the other figures in the same report; the artefacts flag this at the figure.

### Releasing Reports: What You Need to Know

Dataset reports suppress small counts. Protection applies **per artifact and per period** and alone does not guarantee anonymity, particularly across several releases. The internal event list, in contrast, contains personal pseudonyms. The following operating rules apply to report files.

**1. Do not give the activity report PDF and a live export of the same period to the same recipient.**

Only the example activity report PDF reads **monthly aggregates** with live fallback; its distinct persons are counted live. Its preview and CSV/JSON/XML, and all formats of other Dataset reports, are **live**. Differences between stored event counts and current records can reveal information about records deleted in the meantime. Even individually suppressed files therefore do not automatically protect against inferences from their combined evaluation.

Every artefact therefore states **source and as-of date** ("Data source: monthly aggregates", "Monthly aggregates as of: …"). Check that line before you put two reports into the same folder.

**2. Do not choose reporting periods shorter than a month — and keep an eye on overlaps.**

If reports are released for the same facility covering sub-periods **and** their union (twelve monthly reports plus one annual report), every cell suppressed in a sub-report can be recomputed exactly from the sum of the others — even if **not a single** cell was suppressed in any of the reports involved. This is a property of the method, not a defect: k-anonymity is a property of *one* table over *one* population.

The **release overview** (Statistics → "Releases" tab, role: lead/application support) lists the reporting periods already released, taken from the audit trail, and marks overlaps. It prevents nothing — it shows you where to look. To keep the warning meaningful, fill in the optional **Recipient** and **Purpose** fields on export; also check possible combination by different recipients. Different recipients reduce the risk only under the organisational assumption that they do not combine the files; the application does not guarantee this.

> **Retention — three classes, not two.** Three different things are logged under the "Export" audit action, and only the first is a release in the sense of this overview:
>
> | Class | Example | Retention | In the overview? |
> |---|---|---|---|
> | **Released report file** | CSV/PDF/JSON/XML download with a reporting period | **indefinite** (not truncated by `auditlog_retention_months`) | yes |
> | Report viewed **on screen** | Recorded HTML/JSON screen views of the external report; the address redirects to the report card without an export audit | normal deadline | no |
> | Access request/download **without** a reporting period | data subject access request for a person (Art. 15), GDPR documents | normal deadline | no |
>
> A reporting period alone is therefore **not** enough — a file must actually have been released. Without the indefinite retention of the first class the overview would pretend to be complete; without the deadline on the second the audit log would grow without limit on every page view.
>
> The indefinitely retained entries contain the period, the format, your recipient/purpose entries and the person who triggered the export. Purpose limitation: evidence of released statistics.
>
> **Recipient and purpose are free-text fields.** Each accepts at most 200 characters; detected email/phone patterns and typical name pairs are rejected with HTTP 400 before release, even with `DJANGO_AUDIT_DETAIL_STRICT=false`. This heuristic cannot detect every personal detail (institutional prefixes such as “Stadt Musterstadt” remain allowed). Enter the **organisation** and a factual purpose, no client data or contact-person names: accepted values remain in the immutable audit log.

**3. The overview has two organisational gaps.**

**First: it starts when this logging was introduced.** A release is recognisable by a marker in the audit log. Releases without this marker are still in the audit log but cannot be recognised as releases there — they do not appear in the overview, are not exempt from the deadline and do not trigger an overlap warning. **Adding them retroactively is technically impossible:** the audit log is immutable and protected against later changes by a checksum chain; adding an entry would be indistinguishable from tampering. The page says so itself.

**Second: it ends at the download.** A forwarded PDF file, a photographed screen or a value copied by hand does not appear there. That is an organisational boundary.

**Period checks without an entry-count limit:** Overlap warnings consider every marked release of the same artifact definition in this facility whose reporting period touches the displayed one; identical periods do not trigger a warning. Display is limited to 50 entries per page, and the warning count refers to the current page. Older matching releases trigger warnings even beyond 500 entries; this report view does not show the acting person.

### Admin Interface

Snapshots are visible in the Django admin under **Statistics snapshots** (read-only). You can check which months have been captured and when the last update took place.

<!-- translation-source: docs/admin-guide.md -->
<!-- translation-version: v0.99.7 -->
<!-- translation-date: 2026-10-14 -->
<!-- translation-date: 2026-10-14 -->
<!-- source-hash: 5397ee74d277d8d23c43357fa4cbac66dc4e7549 -->
