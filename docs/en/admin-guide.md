> This is the English translation of [admin-guide.md](../admin-guide.md).
> Most recently synchronized on 2026-08-15 (§ 2.5 tally opt-in and inline numeric capture, #1637, #1906).
> The German version is the authoritative source. Last content syncs: 2026-07-07 (§ 2.10 offline key-lifecycle, #1417), 2026-07-11 (§ 5.6 Error Tracking & Data Minimization, #1500) and 2026-07-14 (§ 1 `DJANGO_AUDIT_HASH_KEY`, § 1.4/§ 4.1 one-shot migrate job, #802), followed by a full re-sync to the current German source on 2026-07-14 (§ 1 audit-hash-key generation & extended ENV reference, § 1.4 token-invite token separation [L4, #1375], § 5.1 real `/health/` payloads, § 5.7 breach-detection scan, § 7.9 RLS on 22 tables, #1551), 2026-07-16 (§ 5.6 GlitchTip verified empirically + how-to, #1511), 2026-07-17 (§ 2.7 configurable MFA enforcement role sets, § 5.5 MFA quota wording, #1639) 2026-08-01 (§ 1 `FILE_UPLOAD_TEMP_DIR` ENV reference, § 2.9 plaintext upload staging buffer, #1661) and 2026-08-01 (§ 1 Step 4 one-command `scripts/initial-setup.sh`, § 1 DB-role tip, § 2.1 bootstrap note, § 5.1 `db_user_is_superuser`/`db_role_posture` health fields, #793) 2026-08-01 (§ 5.6 GlitchTip verification wording decoupled from the moving SDK pin, #1701) 2026-08-03 (§ 7.6 scope of the Art. 15 access record and its deliberate exclusions, #1752) 2026-08-03 (§ 7.4 audit-log retention period, prune-exempt actions and checkpoint mechanics, #1753) and 2026-08-03 (§ 7.5 visibility of the deletion-requests menu entry and the trash action "Anonymize permanently now", #1736, #1737) and 2026-08-03 (§ 2 system-type roles, frozen value list and the `note` correction, #1748) and 2026-08-04 (§ 2 event-only fields are hidden for person/case entity types, #1757) and 2026-08-04 (§ 7.4 fourth prune-exempt class "released report files", § 7.9 RLS table count 22 → 25, § 8 release overview and the operational note on releasing reports, #1770, #1760) 2026-08-04 (§ 2.5 the "Category" field was removed, uniqueness is now `(facility, name)`, #1749) and 2026-08-04 (two gaps the 2026-07-14 re-sync had missed: the audience note in § 2.4 "Role Descriptions" and the `DJANGO_AUDIT_HASH_KEY` row in § 6 troubleshooting, #1785) and 2026-08-05 (§ 7.6 rectification requests (Art. 16) are part of the Art. 15 access record, #1796) and 2026-08-09 (§ 7.9 RLS table count 25 → 27 after `ImportJob`/`RectificationRequest`, #1806) and 2026-08-11 (§§ 2.3, 2.4, 2.5, 2.8, 7.2, 7.3: renamed contact levels "Pseudonym only"/"With additional details" and the "Document contact" page title, #1805) and 2026-08-13 (§ 2.3 new setting "Maximum session duration (hours)", #1868) and 2026-08-13 (§ 2.5 `inventory_report_bindings`, #1848; § 7.5 deletion-requests location now the Privacy area, #1813; § 7.6 "age group" label (translation-only fix; the wording dated from the #1752/#1796 syncs)).

# Anlaufstelle -- Admin Guide

This guide is intended for IT administrators of social service facilities who install, configure, and operate Anlaufstelle.

---

## Table of Contents

1. [Installation (Docker Compose)](#1-installation-docker-compose)
2. [Initial Configuration](#2-initial-configuration)
 - 2.5 [Configure Documentation Types](#25-configure-documentation-types)
 - 2.6 [Manage Selection Options (Field Templates)](#26-manage-selection-options-field-templates)
 - 2.6b [Fuzzy Search (pg_trgm)](#26b-fuzzy-search-pg_trgm)
 - 2.7 [Two-Factor Authentication (2FA)](#27-two-factor-authentication-2fa)
 - 2.8 [Quick Templates](#28-quick-templates)
 - 2.9 [Encrypted File Vault & Virus Scanning](#29-encrypted-file-vault--virus-scanning)
 - 2.10 [Offline Mode & Streetwork (M6A)](#210-offline-mode--streetwork-m6a)
3. [Backup and Recovery](#3-backup-and-recovery)
4. [Updates](#4-updates)
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

> **Alternative: Coolify on Hetzner CX22** -- For the recommended deployment via [Coolify](https://coolify.io/) (including TLS, backups, and ClamAV), see the separate guide [`docs/coolify-deployment.md`](https://github.com/anlaufstelle/app/blob/main/docs/coolify-deployment.md). The following Docker Compose instructions still apply for manual deployments.

### Prerequisites

- Docker Engine 24 or newer
- Docker Compose v2 (as a plugin: `docker compose`)
- Publicly reachable server with a DNS record for your domain
- Ports 80 and 443 must be accessible from the internet

### Step 1: Download Files

```bash
git clone https://github.com/anlaufstelle/app.git
cd app
```

Alternatively, download only the required production files:

```bash
curl -O https://raw.githubusercontent.com/anlaufstelle/app/main/docker-compose.prod.yml
curl -O https://raw.githubusercontent.com/anlaufstelle/app/main/Caddyfile
```

### Step 2: Configure Environment Variables

Create a `.env` file in the same directory as `docker-compose.prod.yml`:

```bash
cp .env.example .env   # if available, otherwise create manually
```

Minimal `.env` for production operation:

```dotenv
# Domain (must have a DNS record)
DOMAIN=anlaufstelle.meine-einrichtung.de

# Django
DJANGO_SECRET_KEY=<long-random-string>
DJANGO_AUDIT_HASH_KEY=<separate-long-random-string>
DJANGO_SETTINGS_MODULE=anlaufstelle.settings.prod
ALLOWED_HOSTS=anlaufstelle.meine-einrichtung.de

# Database (three-role model, Refs #902)
POSTGRES_DB=anlaufstelle
POSTGRES_USER=anlaufstelle                         # App role (Django runtime): NOSUPERUSER, NOBYPASSRLS
POSTGRES_PASSWORD=<secure-database-password>
POSTGRES_ADMIN_USER=anlaufstelle_admin             # Admin role (migrations/seed/retention): NOSUPERUSER, BYPASSRLS
POSTGRES_ADMIN_PASSWORD=<secure-admin-password>
POSTGRES_BOOTSTRAP_PASSWORD=<secure-bootstrap-password>  # postgres bootstrap superuser (role setup only)

# Field encryption (required in production)
# Recommended: plural form for rotation; the first key is the write key, any others are read-only.
ENCRYPTION_KEYS=<fernet-key-1>
```

**Generate an encryption key:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Generate a secret key:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

**Generate an audit hash key:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

The `DJANGO_AUDIT_HASH_KEY` secures the audit hashes — pseudonymized entries (e.g. emails in password-reset audits) as well as the HMAC integrity chain of the audit log — and is deliberately kept separate from `DJANGO_SECRET_KEY` (see [`docs/disaster-recovery.md`](https://github.com/anlaufstelle/app/blob/main/docs/disaster-recovery.md)).

> **Important:** Store `ENCRYPTION_KEYS` and `DJANGO_SECRET_KEY` securely (e.g., in a password manager or secret management system). Without the keys, encrypted field data becomes permanently unreadable.

#### Full Environment Variable Reference

All environment variables that the application evaluates at runtime (see [`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py) and [`prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py)):

**Django & Hosts**

| Name | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | -- (required in prod) | Signs sessions/CSRF. Generate with `secrets.token_urlsafe(50)`. |
| `DJANGO_AUDIT_HASH_KEY` | -- (required in prod) | HMAC key for pseudonymized audit entries (e.g. password-reset emails), separate from `DJANGO_SECRET_KEY` -- without its own key, the audit hash would otherwise silently fall back to `SHA256(SECRET_KEY)`, which a `SECRET_KEY` leak could then exploit retroactively; hence fail-closed. Generate with: `python -c "import secrets; print(secrets.token_urlsafe(64))"`. |
| `DJANGO_SETTINGS_MODULE` | -- | In production: `anlaufstelle.settings.prod`. |
| `ALLOWED_HOSTS` | -- (required in prod) | Comma-separated host names, e.g. `anlaufstelle.example.de`. |
| `TRUSTED_PROXY_HOPS` | `1` | Number of trusted proxies in front of the app (X-Forwarded-For evaluation). `0` = no proxy, `1` = Caddy only, `2` = CDN + Caddy. |
| `DJANGO_HEALTH_DETAIL_TOKEN` | -- | Token for the `X-Health-Token` header; authorizes monitoring callers to retrieve the detail fields of `/health/` (`database`, `virus_scanner`/`clamav`, `encryption_key`, `db_user_is_superuser`/`db_role_posture`, `version`, `smtp`, backup age, disk free, `stale_jobs`). Without a token, `/health/` returns only `{"status": ...}` (see [§ 5.1](#51-health-endpoint), #1375). |

**Database (PostgreSQL — three-role model, #902)**

Since v0.12, Anlaufstelle separates three DB roles: a hardcoded `postgres` bootstrap superuser (creates the app roles on first start only), the **app role** (`POSTGRES_USER`, `NOSUPERUSER NOBYPASSRLS` — Django runtime, protected by RLS), and the **admin role** (`POSTGRES_ADMIN_USER`, `NOSUPERUSER BYPASSRLS` — migrations, seed, retention pruning). **Breaking change for self-hosters:** The three new `.env` variables are mandatory.

| Name | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `anlaufstelle` | Database name. |
| `POSTGRES_USER` | `anlaufstelle` | App role (Django runtime), `NOSUPERUSER NOBYPASSRLS`. |
| `POSTGRES_PASSWORD` | `anlaufstelle` | Password for the app role (set securely in production!). |
| `POSTGRES_ADMIN_USER` | `anlaufstelle_admin` | Admin role for migrations/seed/retention, `NOSUPERUSER BYPASSRLS`. |
| `POSTGRES_ADMIN_PASSWORD` | -- | Password for the admin role. |
| `POSTGRES_BOOTSTRAP_PASSWORD` | -- | Password for the `postgres` bootstrap superuser (initial role setup only). |
| `POSTGRES_HOST` | `localhost` (via Compose: `db`) | DB host. |
| `POSTGRES_PORT` | `5432` | DB port. |

> **Tip:** `python manage.py check_db_roles` verifies the role topology at runtime (exit `0` = ok, `1` = incorrect attribute profile, `2` = configuration incomplete). The [Compliance Dashboard](#55-compliance-dashboard) uses the same check, and [`scripts/initial-setup.sh`](../../scripts/initial-setup.sh) runs it during first-time setup. In addition, the [health endpoint](#51-health-endpoint) continuously checks the connection **actually in use** (`db_role_posture`) -- `check_db_roles` verifies that the roles *exist* with the right attributes, not that the application connects as the app role (Refs #793).

**Field Encryption (MultiFernet rotation)**

| Name | Default | Description |
|---|---|---|
| `ENCRYPTION_KEYS` | -- | Comma-separated list of Fernet keys. The **first** key is the write key (new data is encrypted with it); all others are read-only (decrypt fallback during rotation). |
| `ENCRYPTION_KEY` | -- | Legacy single key. At least one of the two variables must be set in production; otherwise the app refuses to start. |

**Virus Scanning (ClamAV, #524)**

| Name | Default (prod) | Description |
|---|---|---|
| `CLAMAV_ENABLED` | `true` | Enables virus scanning before upload encryption. Fail-closed: if the daemon is unreachable, the upload is rejected. |
| `CLAMAV_HOST` | `clamav` | Hostname of the ClamAV daemon (service name in the Compose network). |
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
| `EMAIL_HOST` | `localhost` | SMTP host for password-reset and invite emails. |
| `EMAIL_PORT` | `587` | SMTP port. |
| `EMAIL_HOST_USER` | -- | SMTP user. |
| `EMAIL_HOST_PASSWORD` | -- | SMTP password. |
| `EMAIL_USE_TLS` | `True` | Enable STARTTLS. |
| `DEFAULT_FROM_EMAIL` | `noreply@anlaufstelle.app` | Sender address. |

**File Vault Upload Limits (#1268)**

Service-layer hard limits as defense-in-depth (independent of the per-facility configurable `Settings.max_file_size_mb`).

| Name | Default | Description |
|---|---|---|
| `FILE_VAULT_MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Hard per-file limit, applied before any full buffering. |
| `FILE_VAULT_MAX_UPLOAD_FILES` | `20` | Maximum number of files per file field (mass-upload brake). |
| `FILE_VAULT_MAX_IMAGE_PIXELS` | `40000000` | Decompression-bomb protection: maximum pixel count of an image upload (also sets `PIL.Image.MAX_IMAGE_PIXELS`). |
| `FILE_VAULT_MAX_ARCHIVE_BYTES` | `209715200` (200 MB) | Zip-bomb protection: maximum sum of uncompressed entries (read from the zip directory). |
| `FILE_VAULT_MAX_ARCHIVE_RATIO` | `100` | Zip-bomb protection: maximum expansion ratio (uncompressed/compressed). |
| `FILE_VAULT_MAX_ARCHIVE_ENTRIES` | `10000` | Zip-bomb protection: maximum number of entries (read from the EOCD record). |

**Maintenance Mode (#700)**

| Name | Default | Description |
|---|---|---|
| `MAINTENANCE_FLAG_FILE` | -- (empty = off) | Path of a flag file; while it exists, the app responds with HTTP 503. Without a value the middleware is a no-op. |
| `MAINTENANCE_ALLOW_IPS` | -- | Comma-separated IP list for ops access during maintenance. |
| `MAINTENANCE_CACHE_TTL` | `5` | Caching duration (seconds) of the flag-file check. |
| `MAINTENANCE_RETRY_AFTER` | `600` | Value of the `Retry-After` header (seconds) while maintenance mode is active. |

**Breach Detection (#685)**

| Name | Default | Description |
|---|---|---|
| `BREACH_*` (variable family) | see runbook | Time windows and thresholds of the heuristic anomaly detection (failed logins, exports, deletions, long-term windows, distributed login attacks) as well as `BREACH_NOTIFICATION_WEBHOOK_URL` (optional escalation webhook; empty = off). Full list with defaults: [ops runbook § 6.5b](../ops-runbook.md). |

**Misc**

| Name | Default | Description |
|---|---|---|
| `MEDIA_ROOT` | `<BASE_DIR>/media` | Storage location for encrypted file attachments (see [§ 2.9](#29-encrypted-file-vault--virus-scanning)). |
| `FILE_UPLOAD_TEMP_DIR` | Django default (`tempfile.gettempdir()`) | Directory for the plaintext staging buffer of uploads > `FILE_UPLOAD_MAX_MEMORY_SIZE`, before they are encrypted into the file vault (see [§ 2.9](#29-encrypted-file-vault--virus-scanning)). Pinned to `/tmp` (tmpfs/RAM) in the shipped Compose files -- [ops runbook § 5.7a](../ops-runbook.md). |
| `BACKUP_DIR` | `<repo>/backups` | Storage directory for the encrypted DB backups (`.sql.gz.enc`); the health endpoint reads the backup age from it. |
| `APP_VERSION` | `dev` | Version string that `/health/` exposes in the `version` detail field (token/authenticated callers only). |
| `SUDO_MODE_TTL_SECONDS` | `900` | Validity duration (seconds) of the re-auth window for sensitive actions (MFA disable, GDPR export). |

### Step 3: Review the Caddyfile

The included `Caddyfile` automatically handles TLS certificates via Let's Encrypt:

```
{$DOMAIN} {
    reverse_proxy web:8000
    ...
}
```

No changes are needed as long as `DOMAIN` is set in the `.env` file.

### Step 4: Start the Stack

**Recommended -- a single command:** the bundled script [`scripts/initial-setup.sh`](../../scripts/initial-setup.sh) runs the complete first-time setup in the correct order (Refs #793):

```bash
./scripts/initial-setup.sh
```

It starts the database and waits for it to become ready, creates the schema via the one-shot job, brings up the full stack, and then **verifies the security posture**: `manage.py check --deploy` (deployment settings) and `manage.py check_db_roles` (database roles and ownership). Finally it creates the super-admin interactively -- unless one already exists (see [§ 2.1](#21-initial-installation-create-a-super-admin-bootstrap)).

The script is **idempotent** (a second run is safe: migrations become no-ops, an existing super-admin is skipped) and **fail-loud** -- if a step fails, it exits with a non-zero code and an explanation. Optional environment variables: `COMPOSE_FILE` (default `docker-compose.prod.yml`), `ADMIN_USERNAME`, `ADMIN_EMAIL`, `WAIT_TIMEOUT`, `SKIP_SUPER_ADMIN=1`. It deliberately accepts **no** password -- `create_super_admin` prompts for it interactively ([ADR-018](../adr/018-rollenmodell-superadmin.md)).

> **Why the role verification belongs here:** if the application's database role runs as a PostgreSQL superuser, Row Level Security is **silently** bypassed -- facility isolation stops working without anything failing. The script checks this fail-loud; in addition, the health endpoint reports that state permanently as `degraded` (see [§ 5.1](#51-health-endpoint)).

**Manual (equivalent, for step-by-step control):** Django migrations do **not** run automatically on `web` service startup — they are a separate one-shot step (Refs #802). For a fresh install, follow this order:

```bash
# 1. Start the database (+ ClamAV) first
docker compose -f docker-compose.prod.yml up -d db clamav

# 2. Create the schema once via the one-shot job
docker compose -f docker-compose.prod.yml run --rm \
    --entrypoint=/app/docker-migrate.sh web

# 3. Start the full stack (web + caddy)
docker compose -f docker-compose.prod.yml up -d

# 4. Verify the security posture
docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
docker compose -f docker-compose.prod.yml exec web python manage.py check_db_roles
```

For details on the migrate job (ownership normalization, advisory lock, expected output), see the canonical source [`docs/ops-runbook.md` § 1.2](../ops-runbook.md#12-deploy-schritte).

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

Expected (anonymous) response -- since #1375 it contains **only** the `status` field:

```json
{"status": "ok"}
```

---

## 2. Initial Configuration

### 2.1 Initial Installation: Create a Super-Admin (Bootstrap)

**Before any other configuration** -- directly after the first `docker compose up` -- create a **super-admin** (`role=super_admin`, "System administration"). This role is the only facility-spanning account and operates the installation. It is **not** created through the normal admin UI but through a dedicated interactive command with no default password:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_super_admin
```

> **Note:** if you used [`scripts/initial-setup.sh`](../../scripts/initial-setup.sh) ([§ 1, Step 4](#step-4-start-the-stack)), this step is already done -- the script invokes the same command at the end and skips it when a super-admin already exists.

The script interactively prompts for:

1. **Username** (freely chosen -- recommendation: not `admin`)
2. **Email address** (required -- used for recovery emails)
3. **Password** (enter twice -- no default, no demo value)
4. **Display name**

After successful completion, exactly one user with the role `super_admin` exists. Use this account to log in at `/login/` and then create the first facility and the first `facility_admin` through the `/system/` area.

> **Important -- no default password:** For GDPR and security reasons, the application ships with **no** default password and no default username (unlike the former `make seed` workflow for development demos). Skipping this command leaves you with **no** usable login.

> **Note:** The command is idempotent in the sense that an existing `super_admin` is not overwritten. To create a second super-admin, run the command again -- existing accounts remain untouched.

#### Unlock an Account (CLI)

If a `super_admin` is locked out by failed login attempts (default: 10 attempts, see [§ 2.7 Account Lockout](#account-lockout)) and there is **no** second super-admin, the lock can only be lifted via CLI -- no other user has access to the bootstrap tool:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py unlock <username>
```

The command unlocks the specified user, writes a `LOGIN_UNLOCK` AuditLog entry, and works for all roles (`facility_admin`, `lead`, `staff`, `assistant` as well). For facility-bound users, the UI path via the facility application manager remains available in addition (see [§ 2.7 Account Lockout](#account-lockout)).

### 2.2 Create a Facility and Application Manager

After logging in as `super_admin`, create the first facility and a `facility_admin` (application manager) for it through the `/system/` area. Alternatively, the interactive setup script is available -- suitable for pure single-tenant installations where system administration and application management are the same person:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py setup_facility
```

The script interactively prompts for:

1. **Organization name** -- e.g., `Diakonie Musterstadt e.V.`
2. **Facility name** -- e.g., `Beratungsstelle Nord`
3. **Application manager username** (`facility_admin`)
4. **Application manager password** (enter twice)

Afterwards, the organization, facility, default settings, and the first `facility_admin` user are created.

> **Note:** If an organization or facility with the given name already exists, it will be reused. An existing username will not be overwritten.

> **Organization as a branding shell:** The organization serves only as a carrier branding container (logo, name in reports). There is no org-admin and no cross-facility effect through it -- cross-facility access runs exclusively through `super_admin` and `/system/`. Architecture decision: [ADR-018](../adr/018-rollenmodell-superadmin.md).

### 2.2 Django Admin Interface

The admin interface is available at:

```
https://anlaufstelle.meine-einrichtung.de/admin/
```

Here you can manage:

| Area | Path in Admin | Description |
|---|---|---|
| Organizations | Core > Organizations | Parent entities (tenant level) |
| Facilities | Core > Facilities | Locations within an organization |
| Settings | Core > Settings | Configuration per facility |
| Users | Core > Users | User accounts and roles |
| Documentation types | Core > Documentation types | Configurable templates |
| Time filters | Core > Time filters | Named time periods for reports |
| Audit log | Core > Audit logs | Immutable protocol |

### 2.3 Settings per Facility

Under **Core > Settings** in the admin, you can configure the following for each facility:

| Field | Default | Description |
|---|---|---|
| Full name | -- | Displayed in reports |
| Default documentation type | -- | Pre-selected type for new entries |
| Session timeout (minutes) | 30 | Automatic logout after inactivity |
| Maximum session duration (hours) | 12 | Upper bound from sign-in, regardless of activity: after this time the session always ends and a new login is required. Allowed range 1-168; must be **greater** than the session timeout (converted to minutes), otherwise the form rejects the input. A change only takes effect at the **next** login -- sessions already running keep the deadline set when they signed in |
| Retention anonymous (days) | 90 | Deletion period for anonymous contacts |
| Retention identified (days) | 365 | Deletion period for contacts of clients at the "Pseudonym only" level |
| Retention qualified (days) | 3650 | Deletion period after case closure ("With additional details" level) |
| Facility-wide 2FA enforcement | false | Enforces TOTP 2FA for **all** users of this facility (see [Section 2.7](#27-two-factor-authentication-2fa)) |

### 2.4 Create Additional Users

Under **Core > Users > Add user** in the admin:

- **Username:** Login name (no real names)
- **Email:** Required for the invite flow (see below)
- **Display name:** Name shown in the user interface
- **Role:** One of the five roles (see below)
- **Facility:** Assignment to a facility

#### Token Invite Flow (Refs #528)

New accounts are created **without** a clear-text password. Instead, the application sends an **invitation email** containing a personalized setup link (`/invite/<uid>/<token>/`) to the email address on file. The link takes the new user to the same password-setting form as the password reset, where they set a password themselves.

1. Admin creates a user with an email address in the admin.
2. The system calls [`send_invite_email`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/invite.py) and generates a setup token (a dedicated `invite_token_generator`, based on `uidb64` + token hash).
3. The user receives the email, clicks the link, sets a password, and is logged in.

**Token validity (L4, Refs #1375):** Invitation and password-reset tokens have **separate** validity periods:

- **Invitations** expire after `INVITE_TOKEN_TIMEOUT` (default **3 days** = 259200 s, adjustable via the `DJANGO_INVITE_TOKEN_TIMEOUT` env var) — a newly invited staff member realistically needs days to open the link.
- **Password resets** expire after `PASSWORD_RESET_TIMEOUT` (default **2 hours**, formerly Django's default of 3 days; adjustable via the `DJANGO_PASSWORD_RESET_TIMEOUT` env var) — a reset link is a strong recovery primitive and should be short-lived.

Both generators are additionally separated by their own `key_salt` (a reset token is not valid on `/invite/` and vice versa) and invalidate the token automatically once the user has set their first password.

**Resend the setup link:** If the email did not arrive or the token has expired, the admin can issue a fresh token from the user detail view via "Resend setup link" (or the equivalent "Resend invitation" button) and have a new email sent.

**Fallback without email:** If a user is created **without** an email address, the admin generates a one-time clear-text initial password for backwards compatibility, which is displayed in the admin interface after saving. This path is **insecure** and should only be used as a last resort -- better: add the email address and resend the invitation.

> **Note:** `must_change_password` is set automatically by the invite flow, but is redundant for the token flow -- the password is chosen by the user in the setup step anyway.

#### Role Descriptions

> **Audience of this section:** the **bootstrap/administration view** -- which role you assign when creating users, and which bootstrap rights come with it. The professional rationale is in the [domain concept (Role)](../fachkonzept-anlaufstelle.md#role--die-rolle) (German), the practical "who may do what?" view in [User Guide § 9](user-guide.md#9-roles-and-permissions), the technical details (DB values, RLS scoping) in [FAQ § C](../faq.md#die-fünf-rollen) (German).

Anlaufstelle has five roles -- four facility-bound, one facility-spanning:

| Role | Title | Scope | Permissions |
|---|---|---|---|
| `super_admin` | System administration | facility-spanning (`/system/`) | Bootstrap, create facilities, pre-auth audit logs, backup access. Created via `manage.py create_super_admin` -- **not** through the admin UI. Every `/system/` view access generates a `SYSTEM_VIEW` AuditLog entry. |
| `facility_admin` | Application manager | one facility | Full access within their own facility: user management, type/sensitivity configuration, audit log (`/audit/`), GDPR package. **Cannot** view other facilities. |
| `lead` | Lead / Manager | one facility | Reports, approve deletion requests, view all cases in the facility, data export |
| `staff` | Staff / Social worker | one facility | Record and edit contacts, cases, and documentation |
| `assistant` | Assistant | one facility | Limited data entry, no access to client data at the "With additional details" level |

> **System ↔ application separation:** `super_admin` manages hosting and bootstrap; `facility_admin` manages configuration and users **within** a facility. This separation is GDPR-relevant: the super-admin cannot view the substantive content of a facility without this being logged in the AuditLog (`SYSTEM_VIEW`). Architecture decision: [ADR-018](../adr/018-rollenmodell-superadmin.md).

### 2.5 Configure Documentation Types

Under **Core > Documentation types**, you define which kinds of documentation can be recorded in the facility. Each type consists of:

- **Name and description**
- **Field templates** (free-text fields, selection fields, etc.)
- **Retention period** in days (overrides the global facility setting if specified)

#### Uniqueness of the Name

The pair `(facility, name)` is unique: within one facility, each documentation-type name can be used only **once**. Trying to create a second type with the same name is rejected as a form error.

> **Changed on 2026-08-04 (#1749, [ADR-040](../adr/040-documenttype-category-entfernt.md)):** The **Category** field (Contact/Service/Admin/Note) has been **removed** -- together with the form field, the list filter and the "Category" columns in the statistics dashboard and the report PDF. It was a pure display grouping with no effect on the export or on the ban/crisis logic; the **system type** was and remains authoritative for that (see below). Its role in the uniqueness key is gone as well: the same name in two categories used to be allowed (e.g. "Note" as Admin *and* as Note), and no longer is. During the update the migration checks this up front and **aborts** if a facility carries such duplicate names -- rename them beforehand; merging them automatically would be a data decision only you can make.
>
> In **configuration packages** (YAML) the `category` key may stay: packages remain valid and loadable, but the value is ignored and the load report flags it as a warning. See [docs/packages.md](../packages.md) (German).

#### Sensitivity Level

The sensitivity level controls which roles can access entries of this type:

| Level | Access | Usage |
|-------|--------|-------|
| **Normal** | All roles (including assistants) | General contacts, services |
| **Elevated** | Staff, lead, application manager | Counseling sessions, medical care |
| **High** | Lead and application manager | Crisis interventions, highly sensitive data |

#### Tally

The **"Include in tally"** checkbox explicitly enables a documentation type for the tally in the Zeitstrom. New types are not enabled by default. The setting takes effect only for active **event** types without a minimum contact stage; it has no effect on person or case types.

- If the type has no required fields, `+1` directly creates an anonymous contact.
- If every required field is a number, `+1` opens a compact value form; the entered values are stored with exactly one anonymous contact. Optional fields are not captured in this workflow.
- If the type has a required field of another kind (for example text, selection, date, yes/no, or file), it does not appear in the tally.

Enabling a type does not override visibility rules: it appears only to roles that may see the type and all of its required fields. Check the sensitivity levels of the assigned field templates before enabling a type.

#### System Type

The system type links a documentation type to internal application logic. It is set at creation and **cannot be changed afterwards**.

> **Changed on 2026-08-12 (#1849, [ADR-042](../adr/042-report-key-berichtszuordnung.md)):** The **report mapping now has a field of its own** -- the **report key** (see the next section). The system type only drives application logic. If you previously brought a type into the example activity report via the system type, you will find it there unchanged: the update gave every existing type the matching report key. **The hardcoded slug fallback is gone** -- a type without a report key appears in no report, even if its slug is one of the package slugs.

The system type serves **one** purpose: **UI logic**. Accordingly there are exactly two values:

| System Type | Effect |
|-------------|--------|
| **Ban** | Activates a ban banner on the person page, dedicated filter in the timeline, count and highlight in the handover; feeds the example ban overview |
| **Crisis** | Displayed as a highlight in the handover (recent crisis events) |

> **Note:** Not every documentation type needs a system type -- most do without one. Whether a type lands in a report is answered by the "Appears in …" information in the detail and form views; the system type has nothing to do with it.

> **Changed on 2026-08-12 (#1850, [ADR-042](../adr/042-report-key-berichtszuordnung.md)):** Until this update the selection had **nine** values; the other seven (Contact, Medical, Needle Exchange, Accompaniment, Counseling, Referral, Note) were pure report mappings and have been without effect since the report key arrived. They are now **removed**. On existing types the system type was cleared in the process -- the report mapping has been living in the report key since the previous update and stays untouched; **the numbers in your reports do not change.** What you will notice: the form now offers only "Ban" and "Crisis", and the detail view of an affected type shows "–" for the system type.

> **The list of values is frozen** ([RFC #1748](https://github.com/anlaufstelle/app/issues/1748), decision of 2026-08-03). **No new system types will be added:** a value belongs here only if it drives application behaviour. Report mappings go through the report key.

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
| **Explicitly belongs in no report** | *(excluded -- and **not** a defect)* |
| *(empty)* | *(appears in no report -- and is reported as a gap)* |

The example activity report is a customizable example or template -- not a binding or current official proof.

> **Empty and "explicitly excluded" are two different things.** Under "Reports" the report states how many events it does *not* show. Counted there are **only** types that make no statement at all (key empty) -- a type set to "Explicitly belongs in no report" is a complete statement and does **not** appear in the notice (#1819). Until the update, bans and notes were counted; a facility that documents entry bans saw the notice permanently without anything being misconfigured.
>
> There is a **third** case since the update: if a type carries a key that no report (any longer) knows -- from a foreign configuration package, or after a typo -- the report says so separately. "I did map it, nothing just happens" is a different finding from "I said nothing".

> **Adding later yes, changing no.** An empty report key can be **added at any time** -- that is the repair path when a type unintentionally appeared in no report. A **set** key is fixed afterwards (including "Explicitly belongs in no report"): reports about past periods must not retroactively produce different figures. When you add a key, the form points out that it affects reports generated in the future across **all** periods.
>
> When **creating** a type the form suggests matching values: system type "Crisis" suggests the key "Crisis", "Ban" suggests "Explicitly belongs in no report". Both are suggestions, not constraints -- system type and report key are allowed to diverge on purpose.

**Report keys on field templates.** Reports that evaluate **field values** (minute totals, needle-exchange balance, distribution of selection fields) additionally need to know **which field** to read. Until the update that only worked through the field slug: naming your numeric field "Session duration" instead of "Duration" produced a report that permanently showed zero minutes -- without any feedback. Since the update the field template carries its own report key (e.g. "Minutes") and the name is free. The former free-text field **"Statistics category"** on the field template was removed in the same step: no evaluation ever read it.

> **At most one field template per report key and documentation type.** Two fields with the key "Minutes" on the same type would mean double-counted minutes. The assignment is therefore rejected -- as is setting a key later that is already taken on a shared type; if the duplication arises anyway (e.g. through a configuration package), the report counts **neither** field and says so. The same field template on **different** types is fine, as are two similarly named fields with **different** keys.
>
> **A deliberate gap:** If a **field template** carries a key that no report knows, the report does *not* say so separately -- unlike for a documentation type. The reason is that no meaningful figure could be given there: a field with an unknown key simply yields no values, and nobody knows how many there should have been. The state stays visible through "Appears in …" on the field template itself, which then reads "Does not appear in any report."

**All bindings at a glance.** If you would rather not check "Appears in …" type by type, use the maintenance command **`inventory_report_bindings`** (#1848). For one facility (`--facility`, mandatory) it lists which report key sits in which reporting backend and which documentation types and field templates carry it -- and which carry none and therefore appear in no report; `--format json` and `--output` write the result to a file in machine-readable form. It is pure information and changes no data, but like the other maintenance commands (e.g. `purge_orphan_field_data`, below) it must run with the **admin DB role** and otherwise aborts with a clear message (Row Level Security). Execution: see `docs/ops-runbook.md` § 3.5.

#### Minimum Contact Stage

Defines the minimum contact stage a person must have for an event of this type to be created. Example: Counseling sessions require at least "With additional details" because additional personal details about the person must be on record.

> **Effective for event types only (Refs #1058).** **Minimum contact stage**, **retention period (`retention_days`)**, **system type** and **report key** only take effect on documentation types whose entity type is **event**: the contact-stage check only applies when creating events, and the retention run deletes events only. For **persons**, the facility-wide rules from the settings apply instead: the trash period (`client_trash_days`) and anonymisation once event retention has cleared out a person's records. **Cases have no retention period of their own at all** — the retention run never touches them; their contents are only redacted when the associated person is anonymised ([ADR-039](../adr/039-owner-generische-dateianhaenge.md) § 6). `retention_qualified_days` does key off a case being closed, but it deletes that case's events, not the case.
>
> The form **hides these four fields** as soon as you pick **person** or **case** as the entity type (Refs #1757) — so you no longer fill in fields that never apply. Hiding is purely visual: values already set stay unchanged on the record, and a field carrying an error message stays visible. Without JavaScript all fields remain visible and keep their explanatory help text as before.

#### Field-Level Sensitivity (`FieldTemplate.sensitivity`)

In addition to the document type's sensitivity, each individual field of a field template can receive its **own** sensitivity level (`FieldTemplate.sensitivity`). For field visibility the **maximum** of the document-type and field sensitivity applies -- a field flagged `HIGH` stays invisible to staff even if the document type itself is only `NORMAL`.

**Decoupling Encryption ↔ Visibility** (Refs #356): The two flags `is_encrypted` (at-rest encryption of the value in the database) and `sensitivity` (visibility in the UI) are configurable **independently**:

- A field may be stored encrypted on disk while still being visible in the UI at `NORMAL` sensitivity (e.g. contact data that all roles are allowed to see but that should not be stored in clear text).
- A non-encrypted field can still be restricted to Lead/Admin (e.g. statistical markers that are sensitive, but do not need to be stored encrypted).

#### Deletion Protection for Existing Data

Fields that already have values stored in events, clients or cases **cannot simply be deleted**. Attempting to remove a field template triggers a protection mechanism that checks for stored values. If the field really has to go, a data migration must run first to clean up the values or move them into another field.

> **Pragmatic alternative:** Rather than deleting, disable the field in the document type mapping -- existing data is preserved, new events/clients/cases will no longer be offered the field.

##### Orphaned Field Values ("No longer assigned")

If only the **assignment** of a field template to a document type is removed (rather than the field template itself being deleted), the values already captured remain stored under their slug in the records (`data_json`). Such values are considered **orphaned**: for clients, cases and events they are shown in a separate **"No longer assigned fields"** section in the detail view and are listed in a dedicated section of the GDPR data access export (Art. 15) -- so they do not disappear unnoticed. The visibility and encryption of an orphaned value still follow its field template (which is kept alive by the deletion protection as long as data exists under its slug): a field once classified as "high"/"elevated" remains restricted in visibility even when orphaned.

To finally **remove** orphaned values (data minimisation, once they are no longer needed), use the maintenance command **`purge_orphan_field_data`**. It removes, from clients, cases and events, exactly those `data_json` keys whose slug no longer belongs to the current field assignment of their type; assigned values (including encrypted ones) are left untouched, and records without a document type are only counted. `--dry-run` shows the effect without writing. Like the other maintenance commands (e.g. `reencrypt_fields`, § 2.9), it must run with the **admin DB role** and otherwise aborts with a clear message (Row Level Security). Execution: see `docs/ops-runbook.md` § 3.5.

### 2.6 Manage Selection Options (Field Templates)

Under **Core > Field templates**, you can edit the options of selection and multi-selection fields (Select / Multi-Select). Options are stored in the **Options json** field as a JSON array.

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
| `slug` | Technical identifier (immutable after creation) |
| `label` | Display name in forms and exports |
| `is_active` | `true` = selectable, `false` = deactivated |

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

> **Important:** Do not remove options from the JSON if events with that value already exist. Use `"is_active": false` instead. This keeps historical data consistent and exports complete.

### 2.6b Fuzzy Search (pg_trgm)

In addition to exact substring matching, the global person (pseudonym) search uses a **trigram-based fuzzy search** -- tolerant of typos and phonetic variants (e.g. "Schmidt" ↔ "Schmitt"). Implementation: [`src/core/services/search.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/search.py), function `search_similar_clients`.

**Per-facility threshold:** Under **Core > Settings > *Facility*** > field **"Fuzzy search threshold"** (`Settings.search_trigram_threshold`):

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

Anlaufstelle supports TOTP-based 2FA via [`django-otp`](https://django-otp-official.readthedocs.io/). Every user can enable 2FA themselves (`/mfa/settings/`); in addition, there are two enforcement levels.

**User-facing documentation:** [User Guide § 1 -- Two-Factor Authentication](user-guide.md#two-factor-authentication-2fa).

#### Configure Enforcement

| Level | Field | Admin location | Effect |
|---|---|---|---|
| **Single user** | `User.mfa_required` | Core > Users > *User* > "MFA required" field | 2FA mandatory for this user, disabling is blocked |
| **Facility-wide** | `Settings.mfa_enforced_facility_wide` | Core > Settings > *Facility* > "Facility-wide 2FA enforcement" field | 2FA mandatory for **all** users of this facility |
| **Role default** | `MFA_ENFORCE_PRIVILEGED_ROLES` (+ `MFA_ENFORCED_ROLES_IMMEDIATE` / `MFA_ENFORCED_ROLES_GRACE`) | Environment variables (deployment) | 2FA mandatory by role alone -- enabled by default in production |

Evaluation happens via [`User.is_mfa_enforced`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py) -- a user is considered enforced if **any** level applies. The MFA middleware blocks access to protected areas until a TOTP code has been verified in the session.

**Configure the role default via environment variables** (#1639). Independently of single-user and facility-wide enforcement, the application already requires 2FA in production **by role alone** (`MFA_ENFORCE_PRIVILEGED_ROLES=True`, the production default; deliberately off in dev/test/e2e/demo). Which roles this covers is controlled by two settings that can be overridden per env as a **comma-separated list**:

| Setting | Default | Effect |
|---|---|---|
| `MFA_ENFORCED_ROLES_IMMEDIATE` | `super_admin,facility_admin` | 2FA required **immediately** (no grace) -- without a second factor, an immediate redirect to `/mfa/setup/` |
| `MFA_ENFORCED_ROLES_GRACE` | `lead,staff,assistant` | 2FA required, but only **after a 30-day transition period** (reminder banner from 14 days remaining) so existing users are not locked out |

The **Lead** role (`lead`) is mandatory by default (in the grace set) -- if staff/assistant are enforced, so is the hierarchically higher lead. Valid role names are `super_admin`, `facility_admin`, `lead`, `staff`, `assistant`; an **empty** list deliberately disables that level. An **unknown role name** or an **overlap** between the two lists aborts startup fail-closed with `ImproperlyConfigured` (no silent misconfiguration).

#### Reset 2FA for a User

If a staff member loses their authenticator device, an administrator has to delete the TOTP device so they can set it up again:

1. Open **Admin > OTP > TOTP devices**.
2. Select the affected user's device and delete it.
3. Inform the user: on their next login they will be redirected to `/mfa/setup/` (if `is_mfa_enforced=True`) or can re-enable 2FA voluntarily.

Since v0.10.1 there are also **backup codes as a second factor** for exactly this recovery case (Refs #588). On 2FA setup the user receives 10 single-use codes that should be printed or stored in a password manager -- at the 2FA login prompt the user can enter a backup code instead of a TOTP code. Used codes are invalidated and recorded in the AuditLog (`MFA_BACKUP_CODE_USED`). If all 10 codes have been spent or lost, the admin reset above remains the fallback.

#### Account Lockout

After **10 failed login attempts** the account is automatically locked (the login service reads the threshold from [`src/core/services/security/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/login_lockout.py)). The locked user sees an information page and can no longer sign in. There are four recovery paths:

**Path A -- UI (for facility-bound users):** The application manager (`facility_admin`) of the facility can unlock the accounts of their own users:

1. **Admin-mgmt > Core > Users** -- select the affected user in their own facility.
2. In the user profile under "Account status" click **Unlock account**.
3. The unlock is recorded in the AuditLog as `LOGIN_UNLOCK` (the `LOGIN_FAILED` log itself is immutable thanks to the `auditlog_immutable` DB trigger).

**Path B -- CLI (for recovery, especially super-admins):** If no other user in the system has access (single-super-admin lockout, or a locked `facility_admin` with no second `facility_admin`), the hosting operator unlocks the account directly on the server:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py unlock <username>
```

The command works for all roles (`super_admin`, `facility_admin`, `lead`, `staff`, `assistant`), writes a `LOGIN_UNLOCK` AuditLog entry, and is the only recovery option for locked super-admins.

**Path C -- Self-service via email (Refs #869):** If the user has an email address on file, they can unlock themselves from the login page via the "Unlock account via email" or "Forgot password?" links -- both paths write a `LOGIN_UNLOCK` AuditLog entry. The dedicated unlock link at `/account/recovery/` sends a signed token valid for 30 minutes (no new model; `TimestampSigner` with salt `lockout-recovery.v1`). Anti-enumeration: the confirmation page is identical whether or not the email belongs to an account.

**Path D -- Self-service via MFA backup code:** If the user has 2FA enabled and has noted one of their backup codes, they can unlock themselves at `/account/recovery/backup-code/` using their username and code. Audit trail: `LOGIN_UNLOCK` (trigger `backup_code`) + `BACKUP_CODES_USED` (flow `lockout_recovery`). Each code is single-use -- the next recovery event consumes the next code.

Lock, unlock, and any attempts during the lock window are all written to the AuditLog -- use the "Login failed" / "Account unlocked" filter for retrospective analysis. The `LOGIN_UNLOCK` detail block contains a `trigger` field: `admin` (admin action), `cli` (CLI command), `password_reset` (Path C via standard reset), `recovery_token` (Path C via dedicated link), `backup_code` (Path D).

#### Audit Trail

All 2FA events are recorded in the `AuditLog` (actions: `MFA_ENABLED`, `MFA_DISABLED`, `MFA_FAILED`). Use the filter in **Core > Audit logs** to investigate failed verifications or activation/deactivation events.

#### Relevant Files

- Models: [`src/core/models/user.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py), [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py)
- Views: [`src/core/views/mfa.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mfa.py)
- Middleware: [`src/core/middleware/`](https://github.com/anlaufstelle/app/tree/main/src/core/middleware)

### 2.8 Quick Templates

**Quick templates** are pre-filled documentation templates that speed up recurring documentation patterns (e.g. "Counseling 30 min", "Standard check-in"). Staff can apply them with one click on the "Document contact" page.

**User-facing documentation:** [User Guide § 3 -- Quick Templates](user-guide.md#quick-templates).

#### Manage Templates

Templates are maintained in the Django admin under **Core > Quick Templates**.

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

The service layer applies a **whitelist filter** before writing and again before applying a template ([`src/core/services/quick_templates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/quick_templates.py)). Values are kept only if:

- the slug belongs to the selected documentation type,
- the effective sensitivity of the field is `NORMAL` (no `ELEVATED`/`HIGH` prefill),
- the field is not encrypted (`is_encrypted=False`) and is not a `FILE` field,
- for `SELECT`/`MULTI_SELECT` fields, the value matches an **active** option of the current field template.

This makes templates **self-healing**: if an admin later deactivates a select option, templates are not updated but silently drop the stale value next time they are applied. No data migration needed.

#### Role and Sensitivity Visibility

Quick templates are filtered per user using [`user_can_see_document_type`](https://github.com/anlaufstelle/app/blob/main/src/core/services/sensitivity.py). An assistant, who cannot see `ELEVATED`/`HIGH` documentation types, will not see templates for those types either. This guarantees the button list never exposes a template a user cannot actually apply.

#### Operational Notes

- Templates are a **convenience layer**, not a data source -- the resulting event must be reviewed and saved explicitly by the user, who can still edit every field.
- Applying a template **does not overwrite** existing form values; it only fills empty fields.
- There is currently **no custom UI** for template management beyond the Django admin. Users with the `facility_admin` role can maintain templates there (within their own facility).

#### Relevant Files

- Model: [`src/core/models/quick_template.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/quick_template.py)
- Service: [`src/core/services/quick_templates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/quick_templates.py)
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
4. The payload is encrypted chunk-wise with Fernet (MultiFernet) (write key = first entry in `ENCRYPTION_KEYS`) and stored under `MEDIA_ROOT`.
5. Downloads happen exclusively through the protected Django view (no direct webserver access to `MEDIA_ROOT`).

> **Plaintext staging buffer** (#1661): Uploads above `FILE_UPLOAD_MAX_MEMORY_SIZE` (default 2.5 MB) are buffered by Django as a plaintext temp file on disk (`FILE_UPLOAD_TEMP_DIR`) before step 3, ahead of scanning and encryption. In the shipped Compose files this buffer lives on a `/tmp` tmpfs (RAM) rather than persistent storage. Details, verification, and fallback options without tmpfs: [ops runbook § 5.7a](../ops-runbook.md).

#### ClamAV Service (`docker-compose.prod.yml`)

The production Compose file includes a dedicated `clamav` container with a healthcheck (`clamdcheck.sh`). The web service waits for `service_healthy` before starting. Reference: [`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml). Configurable via `CLAMAV_ENABLED` / `CLAMAV_HOST` / `CLAMAV_PORT` / `CLAMAV_TIMEOUT` (see [ENV reference in § 1](#full-environment-variable-reference)).

#### Health Check

In production the `/health/` endpoint checks ClamAV daemon reachability in addition to the database. If ClamAV is enabled but unreachable, the endpoint signals an error state (HTTP 503) -- this lets external monitors detect "uploads are currently unavailable" as well.

#### Key Rotation (`ENCRYPTION_KEYS`)

MultiFernet accepts a list of keys. To rotate:

1. **Generate a new key** and add it as the **first** entry in `ENCRYPTION_KEYS`: `ENCRYPTION_KEYS=new,old`. Newly written data is encrypted with `new`; existing data stays readable via `old`.
2. Optional: run `python manage.py reencrypt_fields` to re-encrypt existing fields with the new key (see [`src/core/management/commands/reencrypt_fields.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/reencrypt_fields.py)). A `--dry-run` reports how many records would be affected without writing anything. **The run must use the admin/owner role** -- see the box below.
3. After successful re-encryption, remove the old key from the list.

> **Important:** Never **replace** the old key instead of **appending** the new one -- any still-unrotated data would become unreadable.

> ⚠️ **Role prerequisite (Refs #1058).** `reencrypt_fields` reads and writes tables protected by row-level security. The runtime role of the `web` container is `NOBYPASSRLS` and has no facility context outside a request -- a call without a role override would see **0 rows**. Always invoke it with the admin role ([ops runbook § 9.5](../ops-runbook.md)):
>
> ```bash
> docker compose exec \
>   -e POSTGRES_USER=$POSTGRES_ADMIN_USER \
>   -e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD \
>   web python manage.py reencrypt_fields --dry-run
> ```
>
> Since #1058 the command aborts with a clear error message when the bypass context is missing (fail-loud instead of "0 rotated, success reported"). On older builds without that guard: **never run step 3 after a run that reports `0` everywhere** -- the old key would leave the list although nothing was rotated.

**What `reencrypt_fields` covers** (Refs #1058):

| Data | Scope |
|---|---|
| **`Event.data_json`** | **complete -- including soft-deleted events** (#1724) |
| `EventHistory.data_before` / `data_after` | complete (append-only, updated through the replication bypass) |
| `Attachment.original_filename_encrypted` | complete -- attachments on events, clients and cases (the model was named `EventAttachment` until #1738) |
| TOTP secrets (`otp_totp_totpdevice.key`) | complete, per device with a row lock and compare-and-swap |
| **`Client.data_json`** | **complete -- including soft-deleted persons** |
| **`Case.data_json`** | **complete -- including soft-deleted cases** |

Since dynamic fields became entity-wide (ADR-037), **persons** and **cases** also carry encrypted extra fields in `data_json`. The run rotates them as well and additionally heals plaintext leftovers in fields that the document type declares as encrypted.

For events, persons and cases, **soft-deleted records are rotated too**. That is deliberate: a record waiting in the trash must not stay behind under a retired key just because it is pending deletion. The former gap for soft-deleted events (the `is_deleted=False` filter) has been closed since #1724.

> **Operational note:** The run takes **no** row lock for events, persons, cases and attachments (unlike the TOTP rewrap). With concurrent editing during rotation a lost update is theoretically possible -- run the rotation in a maintenance window, not during regular operation.

#### Upload Limit

- Per facility: `Settings.max_file_size_mb` (default **10 MB**) -- see [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py).
- Global: Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` applies as an additional hard limit. Raise it via ENV or settings override if larger uploads must be allowed.

#### Safe Downloads (RFC 5987)

All file downloads are delivered via the central helper [`safe_download_response`](https://github.com/anlaufstelle/app/blob/main/src/core/utils/downloads.py). The helper sets `Content-Disposition` with RFC-5987-encoded file names (Unicode-safe, no reverse-path traversal) and prevents browser MIME sniffing.

### 2.10 Offline Mode & Streetwork (M6A)

For field work (e.g. streetwork), Anlaufstelle offers a **secure offline mode** with **client-side** end-to-end encryption of all data cached on the device. Refs #573, #576.

#### Cryptographic Design

| Aspect | Value |
|---|---|
| Algorithm | **AES-GCM-256** (WebCrypto-native) |
| Storage | **IndexedDB** (AES ciphertext only, **never** plaintext) |
| Key derivation | **PBKDF2** -- 600,000 iterations, SHA-256 |
| KDF input | User password + `User.offline_key_salt` (16 bytes, per-user, stored server-side) |
| Key lifetime | **Persisted** as a non-extractable `CryptoKey` (`extractable: false`) in its own IndexedDB (`anlaufstelle-crypto`) -- survives tab close and reload; coupled to the server session age (idle key wipe), see "Consequences for Admins" |

Source: [`src/static/js/crypto.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/crypto.js) (client-side derivation/storage/idle wipe), [`src/core/signals/offline_invalidation.py`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/offline_invalidation.py) (server-side salt rotation on role change/deactivation).

#### Consequences for Admins

- **Key loss is unrecoverable:** If a user forgets their password, all offline data stored locally on their device becomes **permanently unreadable**. An admin-side reset of offline data is technically **impossible** -- the key only exists in the user's browser.
- **Sync before password change:** Users must synchronize offline data with the server **before** changing the password or logging out. Changing the password rotates the salt (new key); the same mechanism auto-rotates the salt on a role change or user deactivation (`offline_invalidation.py`), so a revoked privilege cannot persist offline.
- **The key is discarded on:** logout, password change (salt rotation), role revocation/deactivation (server-side salt rotation), and session expiry (idle key wipe, default 30 min) → stored ciphertexts are unreadable until the user authenticates again. **Simply closing the tab does NOT discard the key** -- it is reconstructed from IndexedDB on reopening, within the session lifetime (deliberate streetwork design, #1065/#1324).

#### Browser Requirements

- The WebCrypto API (`crypto.subtle`) and IndexedDB must be available.
- Supported: current versions of Firefox, Chrome, Edge, Safari.
- Not supported: legacy browsers without a modern crypto API (offline mode is grayed out in that case).

#### Streetwork Levels

- **Level 2 (Read cache):** Selected person dossiers are locally encrypted and cached before streetwork so they can be viewed offline.
- **Level 3 (Offline edit):** Events and notes can be captured offline and synchronize on the next online period. Conflicts between an offline edit and a concurrent server change are presented to the user as a **side-by-side diff** for manual resolution.

---

## 3. Backup and Recovery

### 3.1 Database Backup

All application data resides in PostgreSQL. Create regular dumps:

```bash
docker compose -f docker-compose.prod.yml exec db \
  pg_dump -U anlaufstelle anlaufstelle \
  > backup_$(date +%Y%m%d_%H%M%S).sql
```

With compression (recommended):

```bash
docker compose -f docker-compose.prod.yml exec db \
  pg_dump -U anlaufstelle -Fc anlaufstelle \
  > backup_$(date +%Y%m%d_%H%M%S).dump
```

> **Important:** Back up the `.env` file (especially `ENCRYPTION_KEY`) separately in a different secure location. Without the key, encrypted fields become unreadable after a restore.

### 3.2 Set Up Automated Backups

Recommended cron example for daily backups at 03:00 (on the host):

```cron
0 3 * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U anlaufstelle -Fc anlaufstelle \
  > /mnt/backup/anlaufstelle/backup_$(date +\%Y\%m\%d).dump
```

Delete backups older than 30 days:

```cron
0 4 * * * find /mnt/backup/anlaufstelle/ -name "*.dump" -mtime +30 -delete
```

### 3.3 Recovery

1. Stop the stack:

```bash
docker compose -f docker-compose.prod.yml down
```

2. Delete the database volume (caution: this deletes all data):

```bash
docker volume rm anlaufstelle_pgdata
```

3. Restart the stack (creates an empty database):

```bash
docker compose -f docker-compose.prod.yml up -d db
```

4. Restore the backup:

```bash
# SQL dump:
docker compose -f docker-compose.prod.yml exec -T db \
  psql -U anlaufstelle anlaufstelle < backup_20260101_030000.sql

# Or compressed format:
docker compose -f docker-compose.prod.yml exec -T db \
  pg_restore -U anlaufstelle -d anlaufstelle backup_20260101.dump
```

5. Start the web service:

```bash
docker compose -f docker-compose.prod.yml up -d
```

6. Perform a health check (see [Section 5](#5-monitoring)).

---

## 4. Updates

### 4.1 Pull New Image and Update the Stack

Migrations run as a separate one-shot job **before** the `web` service is restarted — it no longer runs migrations on startup itself (Refs #802). For details, see the canonical source [`docs/ops-runbook.md` § 1.2](../ops-runbook.md#12-deploy-schritte).

```bash
# 1. Download the latest image
docker compose -f docker-compose.prod.yml pull web

# 2. Run migrations as a one-shot job
docker compose -f docker-compose.prod.yml run --rm \
    --entrypoint=/app/docker-migrate.sh web

# 3. Restart the stack (web + caddy, DB stays up; brief downtime)
docker compose -f docker-compose.prod.yml up -d web caddy
```

Check the logs afterwards:

```bash
docker compose -f docker-compose.prod.yml logs web --tail=50
```

### 4.2 Before an Update

- Always create a current database backup first (see [Section 3.1](#31-database-backup)).
- Read the changelog for breaking changes and required configuration updates.

### 4.3 Rollback

If problems occur after an update:

```bash
# Revert to a specific image tag
# In docker-compose.prod.yml: image: ghcr.io/anlaufstelle/app:v1.2.3
docker compose -f docker-compose.prod.yml up -d

# Restore the database backup from before the update (if necessary)
```

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

The endpoint requires no authentication and is suitable for external monitoring systems. Since #1375 the anonymous response deliberately contains **only** the `status` field (recon hardening): subsystem failures remain visible anonymously via `status`/HTTP code (`degraded` on scanner/SMTP problems as well as on an RLS-bypassing database runtime role, `error` + 503 on database/key failure), but all detail fields (`database`, `virus_scanner`/`clamav`, `encryption_key`, `db_user_is_superuser`/`db_role_posture`, `version`, `smtp`, `last_backup_age_hours`, `disk_free_pct`, `stale_jobs`) are served by `/health/` only to authenticated sessions or callers presenting the `X-Health-Token` header (= `DJANGO_HEALTH_DETAIL_TOKEN`). The complete, authoritative field reference (all fields, values, status codes, and thresholds) is in the [monitoring guide](../monitoring-guide.md).

### 5.2 Monitoring Integration

**Uptime Kuma / Healthchecks.io:**

```
https://anlaufstelle.meine-einrichtung.de/health/
Expected HTTP status: 200
Expected response content: "status": "ok"
Check interval: 1 minute
```

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

### 5.4 CSP Debugging

The Content Security Policy (CSP) is set **centrally in Django** via [`django-csp`](https://django-csp.readthedocs.io/) (see [`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py), `CONTENT_SECURITY_POLICY`). The previously redundant CSP configuration in the Caddyfile has been removed -- this is the only way to ensure that app and reverse-proxy policies do not drift apart.

**Inline scripts are not allowed.** All JavaScript logic lives in external files under `src/static/js/`, included via `<script src=…>` or through nonce-aware template tags.

**`script-src` global without `'unsafe-eval'`.** With the migration to the `@alpinejs/csp` build (v0.10.2), `'unsafe-eval'` has been removed from the global policy. All Alpine components are registered as `Alpine.data()` components in [`src/static/js/alpine-components.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine-components.js); architecture tests forbid inline `x-data="{...}"` and complex expressions (ternaries, `||`/`&&`, method calls, object literals) in Alpine and HTMX directives.

**Exception `/admin-mgmt/*` (Django admin):** django-unfold loads its own Alpine build that uses `new AsyncFunction()`-based evaluation for the Cmd+K search and therefore cannot initialize without `'unsafe-eval'`. The [`AdminCSPRelaxMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/) therefore appends `'unsafe-eval'` **per request only for admin routes** -- which are additionally protected by the MFA gate and the `facility_admin` (or `super_admin`) role. Outside the admin, the strict global policy stays active.

**Typical error patterns in the browser console:**

- `Refused to execute inline script because it violates the following Content Security Policy directive` -- inline `<script>` block in a template. Move the script to a static JS file or include it via a nonce-aware template tag.
- `Refused to load the script … because it violates … directive: "script-src 'self'"` -- external script CDNs are not supported; all scripts must come from `self`.
- `Refused to evaluate a string as JavaScript because 'unsafe-eval' is not an allowed source` -- expected on regular routes (architecture violation); inside the admin area this indicates that the relax middleware did not match (check the route pattern in [`AdminCSPRelaxMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/)).

If CSP errors appear after an update: check the browser console for the **specific blocked URL/source** and decide whether to move the source into the template or adjust the CSP directive.

### 5.5 Compliance Dashboard

The super-admin area `/system/compliance/` (#919) consolidates typed health checks in a single dashboard: the three DB role checks (via `check_db_roles`, see the database variables in [§ 1](#1-installation-docker-compose)), backup age, restore-verified age, ClamAV ping and signature age, retention run age, background job freshness (statistics snapshot, breach scan, materialized view refresh), MFA quota (roles requiring MFA), pending Django migrations, app/Django/Python versions, and critical audit events from the last 24 hours.

Each check returns a status of `ok` / `warning` / `critical` / `unknown`; a single failing check does not bring down the whole dashboard. The area is **super-admin only** (`facility_admin` receives a `403`). The freshness thresholds for background jobs and the restore marker are documented in the [Ops Runbook](../ops-runbook.md).

### 5.6 Error Tracking & Data Minimization

Error tracking (Sentry-compatible) is **fully opt-in**: the SDK is initialized only when `SENTRY_DSN` is set ([`prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py)). Without a DSN, **no telemetry** leaves the installation -- there is no hosted offering of this project, so data residency rests entirely with the operator. An absent or unreachable tracker never affects the application; errors still land in the server logs (see **Logging** in [§ 1](#1-installation-docker-compose)).

**Server-side SDK only.** Only the server SDK (`sentry-sdk[django]`) is used, no browser SDK. Session Replay, feedback widgets, client-side breadcrumbs and browser profiling do not exist in this project, so none of them can silently run along. The only features in use are server error events and optional trace sampling (`SENTRY_TRACES_SAMPLE_RATE`).

**What is sent, and what is removed or masked.** Once tracking is enabled, several data-minimization layers apply before an event leaves the installation:

- `send_default_pii=False` -- no cookies, no client IP.
- `include_local_variables=False` -- stack-frame locals are not captured (a frame could hold, e.g., a decrypted note).
- A server-side `before_send` hook removes request bodies, cookies and query strings, masks sensitive headers and keys (`Authorization`, passwords, tokens, …), runs exception and log messages plus the **breadcrumbs** built from log lines through a text scrubber (emails/tokens become placeholders), and masks free-form `extra` values.
- A `before_send_transaction` hook applies the same scrubbing to **performance transactions** (which bypass `before_send` in the SDK): the request context including `query_string` (search terms can be client names), span descriptions and data, and the transaction name are all covered.

**Limits -- stated plainly.** The scrubbers are rule- and key-based (best-effort, defense-in-depth); they do not replace a content review of every individual event field. For maximum data minimization, choose one of the following levels:

1. **Leave `SENTRY_DSN` unset** -- recommended for high-sensitivity deployments, especially on older releases whose SDK scrubbers do not yet include all the layers above: no error-tracking processor, no data egress; errors still appear in the server logs.
2. **`SENTRY_TRACES_SAMPLE_RATE=0`** against a tracker **inside your own trust boundary** (e.g. self-hosted): disables performance transactions entirely and limits egress to scrubbed error events sent to an instance that is not an additional third party.

**Logs.** Server logs are PII-scrubbed in **both** formats -- the default `LOG_FORMAT=text` and `LOG_FORMAT=json` both run messages and tracebacks through the same scrubber. For GDPR contexts with log aggregation, `LOG_FORMAT=json` is still recommended (structured, machine-readable), but data minimization is no longer tied to that format.

**Self-hosted tracker (GlitchTip).** GlitchTip accepts standard Sentry DSNs, and this project uses only core SDK features (error events plus optional trace sampling). This combination was verified empirically: with `sentry-sdk[django]` `2.64.0` against GlitchTip `6.2.1` (Docker image `glitchtip/glitchtip:6`), server error events are transmitted, parsed plausibly (stack trace, tags, request context) and shown as issues; performance transactions (`SENTRY_TRACES_SAMPLE_RATE > 0`) are **accepted** as well and grouped into transaction groups -- they are neither silently dropped nor do they raise an error. The server-side `before_send`/`before_send_transaction` scrubber applies unchanged (no frame locals, stripped query strings/cookies/request bodies); the rule- and key-based limits described above still hold. Other version combinations are untested, and a third-party project naturally remains outside this project's control.

To connect a self-hosted tracker:

1. Run a GlitchTip instance and create a project there -- GlitchTip shows its DSN in the form `https://<public_key>@<glitchtip-host>/<project_id>`.
2. Pass that value to the app as `SENTRY_DSN` -- **no code change required**. Optionally set `SENTRY_TRACES_SAMPLE_RATE` (default `0.1`; `0` disables transactions entirely, see the escalation levels above).
3. Expectation: server error events appear as issues, optional trace sampling as transaction groups. Only the server SDK runs (no browser SDK, no Session Replay).

Operating the tracker instance (setup, backups, retention, upgrades) is operator infrastructure and covered by the [GlitchTip documentation](https://glitchtip.com/documentation) -- **out of scope** here; the tracker's database belongs inside the same trust boundary as the app data and needs the same access and retention rules.

> **Not legal advice:** This section describes technical measures. A data protection impact assessment and the record of processing activities remain with the operator as the controller.

### 5.7 Breach Detection Scan

The `detect_breaches` management command heuristically analyzes the audit log for anomalies (failed-login bursts, mass exports/deletions, distributed login attacks) and writes hits as audit events; optionally a webhook (`BREACH_NOTIFICATION_WEBHOOK_URL`) is notified. The windows/thresholds are controlled via the `BREACH_*` variables (see the [ENV reference in § 1](#full-environment-variable-reference) as well as [ops runbook § 6.5b](../ops-runbook.md)).

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only to deployments **without** that compose file (Coolify, k8s, custom orchestration). Setting up both makes the job run twice — details and migration steps: [ops runbook § 3](../ops-runbook.md#3-cron-jobs).

**Recommended cron (hourly, at minute 30):**

```cron
30 * * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py detect_breaches \
  >> /var/log/anlaufstelle-breach.log 2>&1
```

> **Note:** The complete, authoritative list of all background jobs including their schedule (backup, retention, audit chain verification, statistics snapshots, orphan cleanup, breach scan, view refresh) is in the [ops runbook § 3](../ops-runbook.md#3-cron-jobs). In the bundled compose stack they run in the `cron` sidecar (`deploy/crontab`); on the dev server they run as systemd timers via `dev-ops/deploy/install-timers.sh` — in both cases the breach scan runs hourly at minute 30.

---

## 6. Troubleshooting

### Problem: Web Service Does Not Start

**Symptom:** `docker compose ps` shows `web` as `Restarting` or `Exit 1`.

**Steps:**

```bash
docker compose -f docker-compose.prod.yml logs web
```

Common causes:

| Error Message | Solution |
|---|---|
| `ENCRYPTION_KEY must be set in production` | Set `ENCRYPTION_KEY` in `.env` (see [Section 1.2](#step-2-configure-environment-variables)) |
| `DJANGO_AUDIT_HASH_KEY muss in Produktion gesetzt sein` | Set `DJANGO_AUDIT_HASH_KEY` (separate from the secret key) in `.env` (see [Section 1.2](#step-2-configure-environment-variables)) |
| `connection refused` (database) | Check if the `db` service is running: `docker compose ps db` |
| `django.db.utils.OperationalError` | Verify database credentials in `.env` |
| `ImproperlyConfigured` | Incomplete environment variables -- read logs for details |

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

```bash
# Check audit log for failed logins
docker compose -f docker-compose.prod.yml exec web \
  python manage.py shell -c "
from core.models import AuditLog
for e in AuditLog.objects.filter(action='login_failed').order_by('-timestamp')[:10]:
    print(e.timestamp, e.user, e.ip_address)
"
```

Or in the admin under **Core > Audit logs**, filtered by the action "Login failed".

**Reset password:**

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py changepassword <benutzername>
```

### Problem: Database Connection Fails

```bash
# Test direct DB connection
docker compose -f docker-compose.prod.yml exec db \
  psql -U anlaufstelle -c "\l"

# Database health check status
docker inspect anlaufstelle-db-1 | grep -A5 Health
```

### Problem: Disk Space Full

```bash
# Analyze Docker storage usage
docker system df

# Clean up old images and unused volumes
docker system prune -f

# Database volume size
docker system df -v | grep pgdata
```

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

## 7. GDPR Notes

Anlaufstelle was developed following the principle of **Privacy by Design** (Art. 25 GDPR, known in German law as DSGVO). This chapter describes the privacy-relevant technical measures and administrative obligations.

> **Disclaimer:** The following technical measures support GDPR compliance but **do not replace legal advice**. The operator is responsible for conducting a Data Protection Impact Assessment (Art. 35 GDPR / DSFA under German law), concluding a Data Processing Agreement with the hosting provider (Art. 28 GDPR / AVV under German law), and documenting processing activities (Art. 30 GDPR). The references throughout this section (DSGVO, BDSG, SGB X) are specific to German data protection law. The software has not undergone a formal third-party security audit.

### 7.1 Encryption

**Field Encryption (Fernet/AES-128):**

Sensitive fields in the database are stored encrypted using the `ENCRYPTION_KEY`. This serves as an additional protective measure beyond general database access controls.

**Visibility vs. encryption:** A field's encryption (`is_encrypted`) and sensitivity level (`sensitivity`) are configurable independently. An encrypted field may be visible to all roles, and a non-encrypted field can be restricted to certain roles.

- The key is provided via the `ENCRYPTION_KEY` environment variable.
- In production, the application refuses to start if no key is set.
- Loss of the key means permanent loss of the encrypted data.

**Key rotation:** Automatic key rotation is not currently implemented. Store the key redundantly and separately from database backups.

**Transport encryption:** Caddy enforces HTTPS with HSTS (`max-age=31536000`). HTTP is automatically redirected to HTTPS.

### 7.2 Pseudonymization

The application stores **no real names** in the database. Personal data is captured in pseudonymized form:

- Persons are referenced by internal IDs.
- Display names in the interface are configurable pseudonyms.
- Data at the "With additional details" contact level (identifiable) is only visible to authorized roles (Lead, Application manager). The system administration (`super_admin`) can access such data only through the `/system/` area; every such access is logged in the AuditLog (`SYSTEM_VIEW`).

### 7.3 Retention Periods and `enforce_retention`

Retention periods are configured per facility in the settings (see [Section 2.3](#23-settings-per-facility)).

The management command `enforce_retention` enforces the configured periods by soft-deleting expired records:

| Strategy | Setting | Default |
|---|---|---|
| Anonymous contacts | `retention_anonymous_days` | 90 days |
| Contacts at the "Pseudonym only" level | `retention_identified_days` | 365 days |
| Cases at the "With additional details" level (after closure) | `retention_qualified_days` | 3650 days (10 years) |

**Manual execution:**

```bash
# Dry run (no deletion)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention --dry-run

# Execute
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention

# Only a specific facility
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention --facility "Beratungsstelle Nord"
```

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only to deployments **without** that compose file (Coolify, k8s, custom orchestration). Setting up both makes the job run twice — details and migration steps: [ops runbook § 3](../ops-runbook.md#3-cron-jobs).

**Set up as a daily cron job (recommended):**

```cron
0 2 * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1
```

Every execution that deletes records is automatically logged in the audit log.

#### Retention Dashboard

A **retention dashboard** is available under [`/retention/`](https://github.com/anlaufstelle/app/blob/main/src/core/views/retention.py) where Lead and Admin roles can efficiently process deletion proposals generated by `enforce_retention`. Refs #514, #515.

| Bulk Action | Effect |
|---|---|
| **Approve** | The proposal is cleared for deletion. On the next retention run the record is actually deleted (or k-anonymized, see below). |
| **Defer** | The proposal is postponed by a configurable period (default 30 days) and reappears in the **next retention run**. After exceeding `retention_max_defer_count` (default 2), the item is either forced to a decision or -- if `retention_auto_approve_after_defer=True` -- auto-approved. |
| **Reject** | The proposal is permanently discarded (record is kept). |

#### Legal Hold

Individual records can be protected from automatic deletion via **Legal Hold** (e.g. during ongoing investigations, audits, or data subject requests). The flag is tracked on the record or in a `LegalHold` entry and is respected by the retention job -- a record with an active Legal Hold is **never** proposed for deletion, even if its retention period has expired.

Legal Holds are managed in the retention dashboard and in the Django admin (see [`src/core/models/retention.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/retention.py)).

#### K-Anonymization Instead of Hard Delete

As an alternative to hard deletion, **k-anonymization** can be enabled per facility (Refs #535).

| Field in `Settings` | Default | Description |
|---|---|---|
| `retention_use_k_anonymization` | `False` | Replaces hard deletion with k-anonymization. |
| `k_anonymity_threshold` | `5` | Minimum group size per bucket. Higher values → stronger anonymization, less detail. |

Effect: instead of deleting the record, identifying attributes are aggregated or pseudonymized so that statistical analyses over historical periods **remain valid** without re-identifying individuals. A GDPR-compliant alternative to permanent deletion when historical reporting must continue.

### 7.4 Audit Log

The audit log is **append-only** and immutable. It automatically records:

| Event | Trigger |
|---|---|
| Login / Logout | Every login/logout |
| Failed login | Incorrect password |
| Data at the "With additional details" level viewed | Access to identifiable personal data |
| Export | Data export by a user — including data subject access requests for individual people and on-screen views of the external report. **Released report files** (CSV/PDF/JSON/XML with a reporting period) are a subset: only those are exempt from the retention deadline and appear in the [release overview](#8-statistics-snapshots--materialized-view) |
| Deletion | Manual or via `enforce_retention` |
| Level change | Change in a person's contact status |
| Settings changed | Changes to facility settings |

**View in the admin:**

Under **Core > Audit logs** (or `/audit/`), logs can be filtered by action, facility, user, and time period. The application manager (`facility_admin`) has access for their own facility. Pre-auth audit logs without a facility reference (e.g. `LOGIN_FAILED` for an unknown username) are visible exclusively to the system administration (`super_admin`) in the `/system/` area -- see [ADR-007 Update 2026-05-10](../adr/007-auditlog-append-only.md).

> ⚠️ **Operator note for your own queries: attachments carry two `target_type` strings** (Refs #1738, [ADR-039](../adr/039-owner-generische-dateianhaenge.md), German). Milestone 24 renamed the model `EventAttachment` to `Attachment`. The audit log is append-only and secured by an HMAC chain — **existing rows are therefore never migrated**. Anyone evaluating attachment actions (downloads, rejected uploads, deletions) must query both strings:
>
> ```sql
> SELECT * FROM core_auditlog WHERE target_type IN ('Attachment', 'EventAttachment');
> ```
>
> The same applies to the `detail` field: new rows carry `owner_type` + `owner_id` (values `event`/`client`/`case`), older rows only `event_id`. Download entries for **event** attachments keep `event_id` in addition, so existing queries do not break. Filtering on `Attachment` alone, or on `detail->>'event_id'` alone, yields a **silently incomplete** result.

**Audit log retention:** The audit log **does** have its own automatic retention period inside the application — deliberately longer than every domain retention period, so that forensics still finds traces after an incident that was noticed late ([ADR-021](../adr/021-retention-modell.md), German).

| Setting in `Settings` | Default | Effect |
|---|---|---|
| `auditlog_retention_months` | 24 months | Every `enforce_retention` run deletes entries older than this period (exact calendar-month arithmetic, not a 30-day approximation). `0` = **disabled**, the log then grows without bound. |

Four classes are **exempt** from this period and are kept regardless of age (`PRUNE_EXEMPT_Q` in [`src/core/retention/audit_pruning.py`](https://github.com/anlaufstelle/app/blob/main/src/core/retention/audit_pruning.py)). Three of them depend on the action (`PRUNE_EXEMPT_ACTIONS`):

- `SECURITY_VIOLATION` — forensic evidence from breach detection,
- `RETENTION_RUN_COMPLETED` — the marker the compliance dashboard reads to show the last successful retention run,
- `AUDIT_PRUNE_CHECKPOINT` — the tombstone of the pruning itself.

On top of that — **regardless of the action** — comes the fourth class: entries about a **released report file carrying a reporting period** (CSV/PDF/JSON/XML). Without that exemption the release overview would feign completeness. Such entries hold the period, the format, the acting person and the recorded recipient/purpose fields (unvalidated free text), and are kept indefinitely. Plain on-screen views of the external report and subject access requests without a reporting period run under the same audit action but stay subject to the period — the distinction is documented in [§ 8 "Releasing Reports"](#releasing-reports-what-you-need-to-know).

**Checkpoint mechanics:** the audit log is chained via HMAC (every entry references the previous one). Pruning necessarily tears that chain. So that the integrity check does not report the gap as tampering, every prune run writes an `AUDIT_PRUNE_CHECKPOINT` entry recording the chain boundaries of the deleted rows, which legitimises the discontinuity. The chain itself is **never** rewritten — the log stays append-only.

> **Important:** the period only takes effect if `enforce_retention` actually runs (see [§ 7.3](#73-retention-periods-and-enforce_retention)). Deleted entries are gone for good — if you need longer evidence retention, define an **external** archiving strategy (e.g. following BSI recommendations or your Data Protection Impact Assessment) and export the log before the period kicks in.
>
> **Residual data-protection risk:** because the log is append-only, anonymising a person does **not** redact it — details and countermeasures in [`security-notes.md` § Audit-Log-Restdaten überleben die Art.-17-Löschung](../security-notes.md#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753) (German).

### 7.5 Deletion Requests (Four-Eyes Principle / Dual Approval)

Deletion requests for personal data are processed using the four-eyes principle (dual approval): a request must be approved by a **second authorized person** before data is permanently deleted. This protects against accidental or unauthorized deletion — including by privileged roles themselves.

**Approver pool — "deletion confirmation" permission** (Refs #1053): Who may approve is not derived from the user's role; instead, approvers are curated via the individually assignable user permission *deletion confirmation* (`can_confirm_deletion`). On first rollout, existing leads and facility admins receive the permission automatically. Afterwards, the facility admin can grant or revoke it in user management (Administration → Users) as needed — for example, granting it to an experienced staff member so that a second approver is always available even if there is only one lead. A permission holder can never approve their own requests; the view, service, and database layers all enforce **requester ≠ approver**.

Every grant and revocation of the permission, as well as every approval, is recorded in the immutable audit log. If an open request has no active approver other than the requester, the deletion request list displays a prominent warning ("No possible approver") — grant the permission to an additional person to resolve this.

#### Who sees the "Deletion requests" tab (Refs #1736)

Deletion requests live in the **"Deletion requests"** tab of the **"Privacy"** area (`/datenschutz/`). What the sidebar carries is not the individual tab but the area entry "Privacy" — it appears as soon as at least one of its tabs is visible. Visibility of the tab no longer follows the role alone, but actual need:

| Who | Sees the tab | Sees in the list |
|---|---|---|
| Lead, facility admin | yes | **all** requests of the facility |
| Holder of the *deletion confirmation* permission (without a lead role) | yes | **all** requests of the facility |
| Requester with at least one **open** request of their own | yes, while the request is open | **only their own** requests |
| Everyone else | no ||

The last case is the **feedback channel**: whoever files a request can find it again via the navigation and via a notice banner on the affected record, and can see its status — without thereby gaining access to other people's reasons and target pseudonyms. Once the request is decided, the tab disappears again — and with it the "Privacy" area entry, unless another tab of that area stays visible.

A pending request is now visible on the record itself as well (a marker in the client list, a banner with date and requester on the client detail page); the "Request deletion" action is disabled while it is pending, and a second request is not possible.

#### Trash: "Anonymize permanently now" (Refs #1737)

An approved deletion moves the person into the **trash** (`/clients/trash/`, facility admin). They stay restorable there until the trash period (`client_trash_days`, default 30 days) expires and `enforce_retention` anonymizes them automatically.

Next to "Restore", that page offers the action **"Anonymize permanently now"**, which brings the anonymization forward instead of waiting for the period:

| Aspect | Behaviour |
|---|---|
| **Effect** | **Irreversible.** Pseudonym, notes, custom fields as well as case and event content are removed; what remains is an anonymous statistical aggregate. Restoring is **no longer** possible afterwards. A confirmation dialog states this explicitly. |
| **Purpose** | (a) **Immediate erasure under Art. 17** when a data subject insists on erasure without delay and 30 days of trash are not defensible. (b) **Resolving configuration blockers**: a person in the trash keeps their document type and field values and thereby blocks deleting that type or field template. Previously the only options were restoring (a short-lived data resurrection) or waiting. The blocker message in the configuration UI now names the trash share and links there. |
| **Limit: legal hold** | If an **active legal hold** exists on the person or on one of their events, the action is rejected with an error message — exactly like the nightly retention run (Refs #1066, #1756). A manual action must not override a hold. Lift the hold first, then anonymize. |
| **Limit: trash only** | The action applies exclusively to records that are already soft-deleted. The four-eyes request path remains the only way **into** the trash — there is no shortcut around approval. |
| **Trail** | Audit entry `CLIENT_ANONYMIZED` with `trigger="manual"`, distinguishable from the automatic run. |

The record stays soft-deleted afterwards (anonymized, not restored) and does not vanish from the trash listing — it simply no longer carries personal data. Since #1737, people in the trash can also no longer be edited via a direct URL (`/clients/<id>/edit/` returns 404, like the detail view).

### 7.6 Data Subject Rights (Art. 15--20 GDPR)

The following administrative capabilities are available for handling requests from data subjects:

| Right | Action |
|---|---|
| Access (Art. 15) | Generate the data access record as JSON or PDF from the client view (lead or application manager, re-authentication required) -- scope see below |
| Rectification (Art. 16) | Edit fields directly as application manager in the admin UI |
| Erasure (Art. 17) | Submit a deletion request through the application (four-eyes principle) |
| Data portability (Art. 20) | Export function in the application |

#### Scope of the Access Record (Art. 15)

The access record is generated from the client view as **JSON** (machine-readable, Art. 20) or as **PDF** (human-readable document). Both formats cover the same scope; the PDF summarizes but omits no category. Only roles with export permission (lead, application manager) may trigger it; the request requires re-authentication (sudo mode), is rate-limited, and is itself recorded in the audit log.

Included (Refs #1752):

| Category | Content |
|---|---|
| Master data | Pseudonym, contact stage, age group, **notes**, active flag, k-anonymization flag, trash status (deleted at/by), creation and modification timestamps, custom fields including orphaned fields (decrypted) |
| Cases | Title, description, status, dates, case lead, trash status, custom fields |
| Episodes | Title, description, start/end per case |
| Outcome goals | Goals with milestones and achievement status |
| Events | Document type, timestamp, case/episode reference, field data (decrypted) |
| Deleted events | Existence and timestamp of events not yet purged (trash) |
| Change history | Action, timestamp, editing user **and the before/after contents** -- including for deleted events |
| Deletion requests | Requests targeting events **and the client record itself**, with reason and four-eyes review |
| Rectification requests | Art. 16 requests concerning the client (Refs #1796, [ADR-041](../adr/041-berichtigungsworkflow-intern.md)) in **all** states (pending, approved, rejected): the field concerned, previous and requested value (decrypted), reason, review note, requesting and reviewing user, timestamps |
| Retention | Legal holds (Art. 18) and open deletion proposals with due date and category |
| Tasks | Title, description, type, status, priority, assignment, dates |
| Activities | Activity feed entries about the client, their cases, events and tasks |
| Recently viewed | Timestamps at which the client record was opened or bookmarked |
| Access log | Audit entries about the client and their events: action, timestamp, reference |
| File attachments | **Metadata** for every attachment on the client, their cases and their events (Refs #1738): field name and field slug, decrypted filename, file size, MIME type, timestamp and uploading user, plus the state within the version chain (current yes/no, superseded at, removed at) -- **the complete chain including superseded and removed links** |

**On the structure of the attachment data (JSON):** client-owned attachments live under the top-level key `attachments` -- it is **always** present, possibly as an empty list. Case- and event-owned attachments appear as `attachments` **inside the respective entry** under `cases[i]` or `events[i]`, and only when non-empty (so the existing format for records without files stays unchanged); deleted events (`deleted_events`) carry them too. If a filename cannot be decrypted, the value reads `[verschlüsselt]` -- the record does not invent a name. The MIME type is the type **verified** at upload time; only legacy rows without detection fall back to the browser-reported one. In the custom fields themselves, the technical file marker is replaced by the placeholder `[Dateianhang – Angaben unter „Dateianhänge"]`. **In the PDF**, client-owned attachments form their own section "Dateianhänge"; case- and event-owned attachments appear as a "Dateianhänge" column in the respective table.

**On the structure of the rectification requests (JSON):** they live under the top-level key `rectification_requests` -- it is **always** present, possibly as an empty list. Per entry: `field_path` (the technical field identifier, `pseudonym`/`age_cluster`/`notes` or `data_json.<slug>`) and `field_label` (the human-readable field name; for a custom field removed in the meantime it falls back to the raw path), `status`, `old_value`/`new_value`, `reason`, `review_note`, `requested_by`/`reviewed_by`, `created_at`/`reviewed_at`. Values are decrypted and rendered readably: an age group appears as "18–26" instead of `18_26`, a select field with its option label. If the client has been anonymized in the meantime, all four value/free-text columns are cleared (Refs #1792) -- the request remains as four-eyes evidence with empty values (rendered as "—" in the PDF). If a value cannot be decrypted, it reads `[verschlüsselt]`. **In the PDF** the requests form their own section "Berichtigungsanträge" directly after the deletion requests; the corresponding audit rows (requested/approved/rejected) appear in the access log under the category "Berichtigung".

**Deliberately excluded -- review as a manual supplementary step:**

1. **Names of the accessing staff members in the access log.** The log carries action, timestamp and reference, but no actor identity: "who accessed what and when" is simultaneously an access log about employees and therefore third-party data (Art. 15(4) GDPR). The same applies to the recently-viewed list. If the data subject requests these details as well, a case-by-case assessment is required -- the raw data is available to the application manager under `/audit/`.
2. **Binary contents** of any kind (files, images). The record names every file but does not ship it; release the file itself via the regular download from the detail view if required.

The PDF notice box states both points explicitly to the data subject -- the record does not claim a completeness it does not have.

### 7.7 Recommended Organizational Measures

- Maintain a **Record of Processing Activities** (Art. 30 GDPR / Verzeichnis der Verarbeitungstaetigkeiten), describing the use of Anlaufstelle as a processing activity.
- Ensure the hosting provider has signed a **Data Processing Agreement** (Art. 28 GDPR / Auftragsverarbeitungsvertrag -- AVV under German law).
- Conduct a **Data Protection Impact Assessment** (Art. 35 GDPR / Datenschutz-Folgenabschaetzung -- DSFA under German law) if your facility processes particularly sensitive data (e.g., health data, data of vulnerable individuals).
- Restrict admin access to the minimum necessary (**Least Privilege**).
- Enable regular password changes by setting `must_change_password = True` for newly created users.
- Store backups and the `ENCRYPTION_KEY` in separate, secured locations.

### 7.8 Optimistic Locking

To protect against **silent overwrites** when two users edit the same record in parallel (two staff members have the same client/case open at the same time and save one after another), Anlaufstelle applies **optimistic locking** at the service layer. Refs #531.

**Affected models:** Client, Case, WorkItem, Settings, Event.

**Mechanics** ([`src/core/services/locking.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/locking.py)):

- Every form renders the current `updated_at` as a hidden field.
- On save, the helper `check_version_conflict(instance, expected_updated_at)` verifies whether the record has been modified in the meantime.
- On conflict a `ValidationError` is raised; the view redirects the user with the message:
 > *"The record has been modified in the meantime. Please reload the page."*

**Administrative notes:**
- There is no admin toggle to disable locking -- the protection is system-wide.
- If users see the conflict message frequently: review the workflow (separate concurrent editing, adjust processes) rather than work around the feature.

### 7.9 Row Level Security (RLS)

In addition to ORM-side facility scoping, **PostgreSQL row-level security** is enabled on **all facility-scoped tables** (currently 27, enforced derivation-based via a test) as **defense-in-depth**. A buggy ORM query that forgets facility scoping still returns no foreign data, thanks to RLS. Refs #542, #586.

#### How It Works

- The middleware [`FacilityScopeMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/facility_scope.py) sets the Postgres session variable `app.current_facility_id` per request via `SELECT set_config('app.current_facility_id', <id>, false)` (session scope, not transaction scope).
- The RLS policies (migration [`0047_postgres_rls_setup`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py)) filter every row via `facility_id = current_setting('app.current_facility_id', true)`.
- **Fail-closed:** if the variable is empty or unset, `current_setting(..., true)` returns NULL, the comparison fails, and **no rows** are returned.
- The middleware opens the DB cursor **only for authenticated requests**; anonymous routes (login, health check, static files) remain unaffected.
- On every request the value is set fresh (including empty), so connection pooling cannot leak a leftover value from an earlier request.

#### Debugging in `psql`

```sql
-- Inspect the current facility scope of a session
SELECT current_setting('app.current_facility_id', true);

-- Set the variable manually for a debug session
SELECT set_config('app.current_facility_id', '<facility-uuid>', false);
```

Without the variable set, the protected tables visibly return **no rows** in `psql` -- this is by design.

---

## 8. Statistics Snapshots & Materialized View

### Overview: Two Layers

Statistics reporting in Anlaufstelle uses **two different acceleration layers**:

1. **Materialized view** (`core_statistics_event_flat`) -- pre-aggregates current event data so the statistics page does not have to scan all events on every request. Refs #544.
2. **Statistics snapshots** -- monthly, persisted aggregates that are captured **before** automatic deletion of old events. Historical reports stay correct even after GDPR-mandated deletion.

### Refresh the Materialized View

The materialized view is **not updated live** on every `INSERT` -- it must be rebuilt periodically via a management command:

```bash
# Default (non-blocking, uses CONCURRENTLY when possible)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py refresh_statistics_view

# Blocking (if there is no UNIQUE index -- legacy schemas)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py refresh_statistics_view --no-concurrent
```

Implementation: [`src/core/management/commands/refresh_statistics_view.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/refresh_statistics_view.py).

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only to deployments **without** that compose file (Coolify, k8s, custom orchestration). Setting up both makes the job run twice — details and migration steps: [ops runbook § 3](../ops-runbook.md#3-cron-jobs).

**Recommended cron cadence:**

```cron
# Hourly (near real time) -- for heavily used statistics pages
0 * * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py refresh_statistics_view \
  >> /var/log/anlaufstelle-statistics.log 2>&1

# Alternative: once a day at night (for one-off reports) -- pick one
# 0 1 * * * …
```

> **Note:** `CONCURRENTLY` does not block reads but requires a UNIQUE index on the materialized view (created by migration). The command automatically falls back to a non-concurrent refresh on error.

### What Are Statistics Snapshots?

Anlaufstelle computes statistics (dashboard, half-year reports, example activity report PDFs) from the event table by default (aggregated via the materialized view). When automatic data deletion (`enforce_retention`) removes old events, those events would disappear from the statistics.

**Statistics snapshots** persist monthly aggregates before events are deleted. Reports use hybrid logic: stored snapshots for past months, live data (from the materialized view) for the current month.

### Automatic Capture

Snapshots are created automatically whenever `enforce_retention` deletes events -- the affected months are captured right before the deletion.

### Periodic Cron Job (Recommended)

It is also recommended to create snapshots regularly via cron:

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only to deployments **without** that compose file (Coolify, k8s, custom orchestration). Setting up both makes the job run twice — details and migration steps: [ops runbook § 3](../ops-runbook.md#3-cron-jobs).

```bash
# Monthly on the 1st at 02:00 -- captures the previous month
0 2 1 * * cd /path/to/app && python manage.py create_statistics_snapshots
```

### Initial Setup (Backfill)

During initial setup or when enabling snapshots later, snapshots can be created for all existing months:

```bash
python manage.py create_statistics_snapshots --backfill
```

**Note:** The backfill only captures events that are still present -- already deleted data cannot be reconstructed retroactively.

### Additional Options

```bash
# Preview (no changes)
python manage.py create_statistics_snapshots --dry-run

# Only a specific facility
python manage.py create_statistics_snapshots --facility "My Facility"

# Specific month
python manage.py create_statistics_snapshots --year 2026 --month 2
```

### Limitations

- **CSV export:** The CSV export still contains only existing events (no snapshot data), because it exports individual rows.
- **Top persons:** The ranking of the most active persons is always computed live and may change after a deletion.
- **Distinct persons:** The figure is counted **live** and distinctly across the whole reporting period, not summed from the monthly aggregates (#1770). For periods whose events were removed once the retention deadlines expired it therefore comes out lower than the other figures in the same report; the artefacts flag this at the figure.

### Releasing Reports: What You Need to Know

Statistics artefacts are k-anonymised (values below the threshold are suppressed). That assurance holds **per artefact and per period** — not across several releases. Three operating rules follow from this.

**1. Do not give the activity report PDF and a live export of the same period to the same recipient.**

The PDFs read from the **monthly aggregates** (snapshots); the external report and the CSV/JSON/XML exports query the events **live**. Both routes are intentional: the aggregates outlive the retention deadlines, the live query counts more accurately across the whole period. Precisely for that reason they are not identical — and **the difference between the two figures is a fingerprint of what has been deleted in the meantime**. Anyone who puts both artefacts for the same period side by side can infer individual deleted records from it; k-anonymity does not protect against this, because each artefact on its own is fully anonymous.

Every artefact therefore states **source and as-of date** ("Data source: monthly aggregates", "Monthly aggregates as of: …"). Check that line before you put two reports into the same folder.

**2. Do not choose reporting periods shorter than a month — and keep an eye on overlaps.**

If reports are released for the same facility covering sub-periods **and** their union (twelve monthly reports plus one annual report), every cell suppressed in a sub-report can be recomputed exactly from the sum of the others — even if **not a single** cell was suppressed in any of the reports involved. This is a property of the method, not a defect: k-anonymity is a property of *one* table over *one* population.

The **release overview** (Statistics → "Releases" tab, role: lead/facility administration) lists the reporting periods already released, taken from the audit trail, and marks overlaps. It prevents nothing — it shows you where to look. To keep the warning meaningful, fill in the optional **Recipient** and **Purpose** fields on export; overlapping periods sent to *different* recipients are harmless, sent to the same one they are not.

> **Retention — three classes, not two.** Three different things are logged under the "Export" audit action, and only the first is a release in the sense of this overview:
>
> | Class | Example | Retention | In the overview? |
> |---|---|---|---|
> | **Released report file** | CSV/PDF/JSON/XML download with a reporting period | **indefinite** (not truncated by `auditlog_retention_months`) | yes |
> | Report viewed **on screen** | opening the external report in the browser — even with a period, and on every reload | normal deadline | no |
> | Access request/download **without** a reporting period | data subject access request for a person (Art. 15), GDPR documents | normal deadline | no |
>
> A reporting period alone is therefore **not** enough — a file must actually have been released. Without the indefinite retention of the first class the overview would pretend to be complete; without the deadline on the second the audit log would grow without limit on every page view.
>
> The indefinitely retained entries contain the period, the format, your recipient/purpose entries and the person who triggered the export. Purpose limitation: evidence of released statistics.
>
> **Recipient and purpose are free-text fields.** The application does not check their content and cannot change it later (the audit log is immutable). Enter the **organisation**, not the name of a contact person — otherwise a person's name stays in the log permanently.

**3. The overview has two known gaps.**

**First: it starts when it was introduced.** The marker that identifies a release only exists as of this release. Releases from before that are still in the audit log but cannot be recognised as releases there — they do not appear in the overview, are not exempt from the deadline and do not trigger an overlap warning. **Adding them retroactively is technically impossible:** the audit log is immutable and protected against later changes by a checksum chain; adding an entry would be indistinguishable from tampering. So expect older releases to be missing for the first few months after the update — the page says so itself.

**Second: it ends at the download.** A forwarded PDF file, a photographed screen or a value copied by hand does not appear there. That is an organisational boundary.

### Admin Interface

Snapshots are visible in the Django admin under **Statistics snapshots** (read-only). You can check which months have been captured and when the last update took place.

<!-- translation-source: docs/admin-guide.md -->
<!-- translation-version: v0.80.0 -->
<!-- translation-date: 2026-08-15 -->
<!-- source-hash: bd42a1607c111cff49fcf28e96acf1b1ded1105d -->
