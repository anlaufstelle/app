> This is the English translation of [admin-guide.md](../admin-guide.md).
> Synchronization update 2026-09-22: § 1 Step 2 states the length/character-set requirement once for `DJANGO_SECRET_KEY`/`DJANGO_AUDIT_HASH_KEY` and names `openssl` as the primary command shared with `.env.example` (#2313).
> Synchronization update 2026-09-22: the SMTP environment reference explains that Django 6.1 requires an SMTP backend on production hosts (`check --deploy`, `mail.E001`), what happens without a reachable mail server, and that the console backend exists only in the live-dev and demo settings (#2326).
> Synchronization update 2026-09-21: the fresh-install health-check note explains that “Critical audit events (24h)” can warn normally because initial setup itself creates critical audit events (#2247).
> Synchronization update 2026-09-18: § 5.6 states that the server log file now carries the same lines with masked URL paths -- query part dropped, route schema or token segments removed, the account as `user_pk` instead of the login name, in both output formats and in framework lines -- while the client IP of a CSP report stays readable (#2228).
> Synchronization update 2026-09-21: the ClamAV troubleshooting section explains that an unknown signature age remains `unknown` during startup and that the dashboard shows the freshclam log command as its action hint (#2246).
> Synchronization update 2026-09-21: § 2.9 storage quotas -- the paragraph on what happens when the quota is reached now states that the second check under the facility lock applies to file attachments and CSV import files alike (#2259).
> Synchronization update 2026-09-21: § 2.9 storage quotas -- the paragraph on what counts now states the two stages for file attachments: the check before the virus scan sees the attachment total only, while the storage step under the facility lock uses the same shared total as the import (attachments plus open import files) (#2259).
> Synchronization update 2026-09-21: § 2.9 storage quotas -- the deliberate limit for CSV import files is gone: both storage paths decide under the same short per-facility lock and on the same total (file attachments plus open import files), so concurrent CSV uploads and the mixed case of an attachment and an import exceed the quota by 0 bytes, and the rejection is audited (#2259).
> Synchronization update 2026-09-18: the SMTP environment reference lists `EMAIL_USE_SSL` and the SMTP requirements state that implicit TLS/SMTPS on port 465 is supported and mutually exclusive with `EMAIL_USE_TLS` (#2267).
> Synchronization update 2026-09-18: the SMTP environment reference notes that the application configures email delivery through Django's `MAILERS` setting since Django 6.1 while the environment variable names stay unchanged (#1662).
> Synchronization update 2026-09-15: § 2.3 the quick-exit target row now states that the target does not learn the origin of the jump (no `Referer` header) (#2179).
> Synchronization update 2026-09-15: § 2.4 documents the Django-admin resend action for pending `super_admin` invitations (#2223).
> Synchronization update 2026-09-15: mandatory backup-code acknowledgement and OTP throttling during initial 2FA setup, the manual backup umask, and the container-internal freshclam result log now mirror the German operator guidance (#2248).
> Synchronization update 2026-09-15: § 2.9 storage quotas -- the former deliberate limit for two concurrent submissions is gone for **attachments**: they are enforced fail-closed in the storage step under a short per-facility lock, so concurrent attachment uploads exceed the quota by 0 bytes, and the rejection is audited; CSV import files keep only the check before saving, open import files count neither towards the attachment total nor in the attachment pre-check, and the size of that remaining deviation depends on the number and size of the import files open at the same time rather than on the per-file limit (#2178).
> Synchronization update 2026-09-15: the production Compose allowlist, configurable ClamAV host, detail-token routing, delivered ClamAV egress path, and critical-only production healthcheck semantics are described as the current state (#2173).
> Previously synchronized on 2026-09-11: the expected `core.W004` warning when Redis has no password, the root-cron backup-directory contract, complete optional Compose file sets for every start and recreate command, ClamAV production/external-scanner limits, and signature-download egress diagnostics (#2218).
> Most recently synchronized on 2026-09-11 (§§ 1--6 fresh-install prerequisites, complete source acquisition, quoted environment values, key and DB-password generation, production setting routing, setup sequence, monitoring boundaries, backup scheduling, and troubleshooting, plus the RLS wording in § 7.9, #2218). Previously synchronized on 2026-09-10 (§ 3.3 both database and optional media HMAC sidecars are verified before confirmation and repeated immediately before use, plus recovery guidance after interrupted media extraction, #2195; § 7.5 client merge: the legal-hold limit when reverting a merge -- an active hold on the target or the source, including indirectly through one of their events or cases, refuses **Revert merge**, and the hold is deliberately not mirrored onto the source, #2200; § 2.9 open CSV import source files count during quota enforcement while the display remains attachment-based, #2180; § 2.11 running imports reject changed documentation types or field templates and commit the most recently confirmed mapping, #2196, #2199; § 5.6 URL-bearing event fields are masked structurally -- query string dropped, path resolved against the route schema including subpath prefix and missing trailing slash, token-signal heuristic for unresolvable paths including percent-encoded tokens; log entries and log breadcrumbs are reduced to their template, person key-value pairs, structured person keys, the client IP and the proxy headers including `Forwarded` are scrubbed -- plus the corresponding limits, #2198; § 2.3/§ 2.5 the retention setting for handover notes (days) and the hard-delete of manual shift notes in the retention run, plus the note that a time filter with handover notes can only be deleted after the retention period, #2042; § 2.4 `super_admin` can change **MFA required** for super-admin/bootstrap accounts under **Core > Users**, and each change is recorded as `USER_ACCESS_UPDATED` with `mfa_required` -- the field is also visible to application managers editing regular accounts, #2172; the alternative-deployment box in § 1 is removed and the cron-job notes in § 2.9 are reworded without the alternative deployment path, #2202; pre-1.0 migration/historical framing removed -- the EXTERNAL_ORIGIN migration box, three dated "Changed" boxes (documentation-type category, report key, system type), the outdated `ENCRYPTION_KEY`/email-less-fallback wording, and several version-dated markers are now described as current state, #2205).
> Previously synchronized on 2026-09-08 (§ 2.9 the key-rotation coverage table and the operational note now cover `EventHistory` as well: the history columns are rotated per row with a row lock and compare-and-swap, a redaction or Art. 17 anonymization committed during the run wins and the row is reported as skipped, and rows that are already redacted are no longer rotation candidates, #2177).
> Previously synchronized on 2026-09-08 (§ 2.9 row-lock/compare-and-swap protection, contention warnings, retry guidance and the maintenance-window recommendation for key rotation, #2131; § 7.6 the Art. 15 access record now states the deletion timestamp and the triggering user for events in the trash, plus the `deleted_events` structure note and the `null` boundary for events deleted before 0.96, #2157). Previously synchronized on 2026-08-30 (§ 2 self-hosted imprint/privacy files, safe Markdown subset, read-only mount and 404 behavior, #2076). Previously synchronized on 2026-08-28 (§ 2.2c the hard-delete description corrected -- Django wraps the whole delete cascade in one transaction, so the trigger error rolls back completely; partial destruction only arises from hand-written scripts that commit between tables, #2067). Previously synchronized on 2026-08-28 (§ 2.2c archiving a facility now actually blocks access -- sign-in, running sessions and the RLS context -- plus the raw-shell hard-delete pitfall around the `auditlog_immutable` trigger and the cross-reference to user offboarding, #2067). Previously synchronized on 2026-08-28 (§ 5.6 the hosted-Sentry third-country/DPA check and the DPA-template subprocessor cross-reference, #2052). Previously synchronized on 2026-08-28 (§§ 1/2.11/3/4.2 the RLS-safe `backup.sh`/`restore.sh` path for database and media backups, verification, cron, and recovery, #2074). Previously synchronized on 2026-08-25 (§ 7.4 the seven audit actions for domain edits, case links and attachments, their data-minimized details and regular retention, #1860). Previously synchronized on 2026-08-24 (§§ 2.4/2.7 the facility-scoped Access UI, sudo requirement, invitation, role, MFA, deletion-confirmation, deactivation and unlock flows, #1871). Previously synchronized on 2026-08-24 (§ 2.5 the person age-group catalog is fixed system-wide and distinct from configurable event fields, #1877). Previously synchronized on 2026-08-24 (§ 7.6 authorized roles see decrypted encrypted values in event history, including the retroactive scope and redacted-delete boundary, #1961). Previously synchronized on 2026-08-24 (§§ 1.4/2.2/4.1 privileged database commands now run through the dedicated maintenance service and `web` never receives admin/bootstrap secrets, #1962). Previously synchronized on 2026-08-22 (§ 2.2b the Django admin interface section is renumbered -- the guide carried two sections numbered 2.2, and §§ 2.1/2.3/2.5 all cross-reference "§ 2.2"; § 2.5 points to the configuration-package reference, #1894; § 1 `TRUSTED_PROXY_CIDRS` is space-separated only and the two new start-up checks `core.W003`/`core.E003` (#1963)). Previously synchronized on 2026-08-21 (§ 2.2 admin DB role for `setup_facility`, the default catalog on cold start, the `--no-default-package` opt-out, a repeat run leaving an existing catalog alone, and retrofitting an existing facility, #1883; §§ 2.1/2.2 facilities are created in the Django admin -- the `/system/` area only displays them; § 2.5 starting point note); § 1 `EXTERNAL_ORIGIN` as the fixed base of security-relevant links and the migration note for existing installations (#1967); §§ 2.2/2.3/2.5 facilities created in the Django admin now start with the default catalog, shifts and quick templates (#1970). Previously on 2026-08-20 (§ 2.11 CSV import and export: formula protection against leading whitespace/zero-width characters and the deliberate round-trip boundary, #1945; § 2.3 every facility has exactly one settings record and what happens without it, #1884).
> Previously synchronized on 2026-09-07 (§ 2.3 the two shoulder-surfing settings -- quick-exit target and privacy screen, plus the organizational limits: browser history with pseudonym page titles, per-window effect, dropped connection, capped deletion of the local offline key, #2136; § 2.3 the two shoulder-surfing settings -- quick-exit target and privacy screen, plus the organizational limits: browser history with pseudonym page titles, per-window effect, dropped connection, #2136; § 2.3 the two shoulder-surfing settings -- quick-exit target and privacy screen, #2136; § 2.2 bundled package selection in CLI and the facility add form, replacement-not-addition guard, #2134; § 2.5 report-key table: the four domain keys `incident`/`protection`/`group_activity`/`supply` and the note on their origin in the domain packages, #2135). Previously synchronized on 2026-08-30 (§ 2 self-hosted imprint/privacy files, safe Markdown subset, read-only mount and 404 behavior, #2076). Previously synchronized on 2026-08-28 (§ 2.2c the hard-delete description corrected -- Django wraps the whole delete cascade in one transaction, so the trigger error rolls back completely; partial destruction only arises from hand-written scripts that commit between tables, #2067). Previously synchronized on 2026-08-28 (§ 2.2c archiving a facility now actually blocks access -- sign-in, running sessions and the RLS context -- plus the raw-shell hard-delete pitfall around the `auditlog_immutable` trigger and the cross-reference to user offboarding, #2067). Previously synchronized on 2026-08-28 (§ 5.6 the hosted-Sentry third-country/DPA check and the DPA-template subprocessor cross-reference, #2052). Previously synchronized on 2026-08-28 (§§ 1/2.11/3/4.2 the RLS-safe `backup.sh`/`restore.sh` path for database and media backups, verification, cron, and recovery, #2074). Previously synchronized on 2026-08-25 (§ 7.4 the seven audit actions for domain edits, case links and attachments, their data-minimized details and regular retention, #1860). Previously synchronized on 2026-08-24 (§§ 2.4/2.7 the facility-scoped Access UI, sudo requirement, invitation, role, MFA, deletion-confirmation, deactivation and unlock flows, #1871). Previously synchronized on 2026-08-24 (§ 2.5 the person age-group catalog is fixed system-wide and distinct from configurable event fields, #1877). Previously synchronized on 2026-08-24 (§ 7.6 authorized roles see decrypted encrypted values in event history, including the retroactive scope and redacted-delete boundary, #1961). Previously synchronized on 2026-08-24 (§§ 1.4/2.2/4.1 privileged database commands now run through the dedicated maintenance service and `web` never receives admin/bootstrap secrets, #1962). Previously synchronized on 2026-08-22 (§ 2.2b the Django admin interface section is renumbered -- the guide carried two sections numbered 2.2, and §§ 2.1/2.3/2.5 all cross-reference "§ 2.2"; § 2.5 points to the configuration-package reference, #1894; § 1 `TRUSTED_PROXY_CIDRS` is space-separated only and the two new start-up checks `core.W003`/`core.E003` (#1963)). Previously synchronized on 2026-08-21 (§ 2.2 admin DB role for `setup_facility`, the default catalog on cold start, the `--no-default-package` opt-out, a repeat run leaving an existing catalog alone, and retrofitting an existing facility, #1883; §§ 2.1/2.2 facilities are created in the Django admin -- the `/system/` area only displays them; § 2.5 starting point note); § 1 `EXTERNAL_ORIGIN` as the fixed base of security-relevant links and the migration note for existing installations (#1967); §§ 2.2/2.3/2.5 facilities created in the Django admin now start with the default catalog, shifts and quick templates (#1970). Previously on 2026-08-20 (§ 2.11 CSV import and export: formula protection against leading whitespace/zero-width characters and the deliberate round-trip boundary, #1945; § 2.3 every facility has exactly one settings record and what happens without it, #1884).
> The German version is the authoritative source. Most recently synced on 2026-08-22 (§ 2 the retention run does act on cases once they are deleted, § 7.5 case trash and the two deletion paths, § 7.6 the `deleted_cases` section of the Art. 15 access record, #1818). Previous content syncs: 2026-07-07 (§ 2.10 offline key-lifecycle, #1417), 2026-07-11 (§ 5.6 Error Tracking & Data Minimization, #1500) and 2026-07-14 (§ 1 `DJANGO_AUDIT_HASH_KEY`, § 1.4/§ 4.1 one-shot migrate job, #802), followed by a full re-sync to the current German source on 2026-07-14 (§ 1 audit-hash-key generation & extended ENV reference, § 1.4 token-invite token separation [L4, #1375], § 5.1 real `/health/` payloads, § 5.7 breach-detection scan, § 7.9 RLS on 22 tables, #1551), 2026-07-16 (§ 5.6 GlitchTip verified empirically + how-to, #1511), 2026-07-17 (§ 2.7 configurable MFA enforcement role sets, § 5.5 MFA quota wording, #1639) 2026-08-01 (§ 1 `FILE_UPLOAD_TEMP_DIR` ENV reference, § 2.9 plaintext upload staging buffer, #1661) and 2026-08-01 (§ 1 Step 4 one-command `scripts/initial-setup.sh`, § 1 DB-role tip, § 2.1 bootstrap note, § 5.1 `db_user_is_superuser`/`db_role_posture` health fields, #793) 2026-08-01 (§ 5.6 GlitchTip verification wording decoupled from the moving SDK pin, #1701) 2026-08-03 (§ 7.6 scope of the Art. 15 access record and its deliberate exclusions, #1752) 2026-08-03 (§ 7.4 audit-log retention period, prune-exempt actions and checkpoint mechanics, #1753) and 2026-08-03 (§ 7.5 visibility of the deletion-requests menu entry and the trash action "Anonymize permanently now", #1736, #1737) and 2026-08-03 (§ 2 system-type roles, frozen value list and the `note` correction, #1748) and 2026-08-04 (§ 2 event-only fields are hidden for person/case entity types, #1757) and 2026-08-04 (§ 7.4 fourth prune-exempt class "released report files", § 7.9 RLS table count 22 → 25, § 8 release overview and the operational note on releasing reports, #1770, #1760) 2026-08-04 (§ 2.5 the "Category" field was removed, uniqueness is now `(facility, name)`, #1749) and 2026-08-04 (two gaps the 2026-07-14 re-sync had missed: the audience note in § 2.4 "Role Descriptions" and the `DJANGO_AUDIT_HASH_KEY` row in § 6 troubleshooting, #1785) and 2026-08-05 (§ 7.6 rectification requests (Art. 16) are part of the Art. 15 access record, #1796) and 2026-08-09 (§ 7.9 RLS table count 25 → 27 after `ImportJob`/`RectificationRequest`, #1806) and 2026-08-11 (§§ 2.3, 2.4, 2.5, 2.8, 7.2, 7.3: renamed contact levels "Pseudonym only"/"With additional details" and the "Document contact" page title, #1805) and 2026-08-13 (§ 2.3 new setting "Maximum session duration (hours)", #1868) and 2026-08-13 (§ 2.5 `inventory_report_bindings`, #1848; § 7.5 deletion-requests location now the Privacy area, #1813; § 7.6 "age group" label (translation-only fix; the wording dated from the #1752/#1796 syncs)) and 2026-08-15 (§ 2.5 tally opt-in and inline numeric capture, #1637, #1906) and 2026-08-18 (§ 5.6 which log fields are exempt from PII scrubbing, #1943) and 2026-08-18 (§ 2.6 numeric range bound guidance for facility admins after tightening `min_value`/`max_value`, #1930) and 2026-08-20 (§ 5.6 technical identifiers -- including the new `user_pk` log field -- pass the PII scrubber unchanged, #1949) and 2026-08-20 (§ 1 `BACKUP_DIR`/`BACKUP_HOST_DIR` ENV reference for the production Compose stack, #1872) and 2026-08-21 (§ 2.4 and § 7.7 the forced password change is cleared once the invitation setup or a password reset completes; § 2.4 step 3 the invitation setup link does not sign the user in, #1969).

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

### Prerequisites

**Mandatory for installation:**

- Docker Engine 24 or newer and Docker Compose v2 (as a plugin: `docker compose`)
- `git` and `openssl`
- Docker is also required for the Fernet-key container generator described below; host `python3` is needed only for the optional alternative command
- root/`sudo` rights to create the installation directory and configure host operations
- a publicly reachable server; DNS records for `DOMAIN` **and** `www.<DOMAIN>` must point to it
- ports 80 and 443 accessible from the internet

**Mandatory for ongoing operation:** `bash` (not just `sh`), `tar`, `gzip`/`gunzip`, `curl`, and an installed, running cron service for host backups. Optional tools are `jq` for JSON evaluation, `mail` for local alerts, and -- depending on the off-site target -- `rclone`, `aws`, or `scp`.

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
docker run --rm --entrypoint python ghcr.io/anlaufstelle/app:v0.96.0 \
  -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`v0.96.0` is an example value. Use the same release tag that you selected via [`APP_VERSION`](#full-environment-variable-reference) for `docker-compose.prod.yml`; available tags are listed on the [releases page](https://github.com/anlaufstelle/app/releases). The entrypoint override is required. If the host optionally has `python3` and the `cryptography` module installed, this alternative command generates the same key:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
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
| `DJANGO_SETTINGS_MODULE`, `POSTGRES_HOST`, `CLAMAV_HOST` | `DJANGO_SETTINGS_MODULE` and `POSTGRES_HOST` are fixed in `docker-compose.prod.yml` to `anlaufstelle.settings.prod` and `db`; `CLAMAV_HOST` is configurable through `.env` |
| `DJANGO_HEALTH_DETAIL_TOKEN`, `LOG_FORMAT`, `FILE_VAULT_MAX_UPLOAD_BYTES`, `FILE_VAULT_MAX_UPLOAD_FILES`, `FILE_VAULT_MAX_IMAGE_PIXELS`, `FILE_VAULT_MAX_ARCHIVE_BYTES`, `FILE_VAULT_MAX_ARCHIVE_RATIO`, `FILE_VAULT_MAX_ARCHIVE_ENTRIES`, `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB`, `MAINTENANCE_FLAG_FILE`, `MAINTENANCE_ALLOW_IPS`, `MAINTENANCE_CACHE_TTL`, `MAINTENANCE_RETRY_AFTER`, `SUDO_MODE_TTL_SECONDS`, `DJANGO_INVITE_TOKEN_TIMEOUT`, `DJANGO_PASSWORD_RESET_TIMEOUT` | passed from `.env` to `web` |
| `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD`, `POSTGRES_BOOTSTRAP_PASSWORD` | `maintenance`/initialization only, deliberately **not** `web` |
| `BREACH_*`, `TZ` | `cron`; `TZ` also sets the containers' time base |
| `BACKUP_HOST_DIR` | host scripts and volume mount; `BACKUP_DIR` is the read-only target in `web` |
| `LEGAL_IMPRINT_FILE`, `LEGAL_PRIVACY_FILE` | `web` only with the additional `compose.legal.yml`, as shown under [Mandatory information](#configure-mandatory-information-imprintprivacy) |

> Every variable assigned to `web` in this table reaches the service through the delivered allowlist; `BACKUP_HOST_DIR` acts through host scripts and the volume mount, and `LEGAL_IMPRINT_FILE`/`LEGAL_PRIVACY_FILE` only with the additional `compose.legal.yml`. A `compose.override.yml` is needed only for additional variable names not listed here.

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
| `DJANGO_HEALTH_DETAIL_TOKEN` | -- | Token for the `X-Health-Token` header; authorizes monitoring callers to retrieve the detail fields of `/health/` (`database`, `virus_scanner`/`clamav`, `encryption_key`, `db_user_is_superuser`/`db_role_posture`, `version`, `smtp`, backup age, disk free, `stale_jobs`). Without a token, `/health/` returns only `{"status": ...}` (see [§ 5.1](#51-health-endpoint), #1375). |

**Database (PostgreSQL — three-role model, #902)**

Anlaufstelle separates three DB roles: a hardcoded `postgres` bootstrap superuser (creates the app roles on first start only), the **app role** (`POSTGRES_USER`, `NOSUPERUSER NOBYPASSRLS` — Django runtime, protected by RLS), and the **admin role** (`POSTGRES_ADMIN_USER`, `NOSUPERUSER BYPASSRLS` — migrations, seed, retention pruning). The `.env` variables `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD` and `POSTGRES_BOOTSTRAP_PASSWORD` are mandatory.

| Name | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `anlaufstelle` | Database name. |
| `POSTGRES_USER` | `anlaufstelle` | App role (Django runtime), `NOSUPERUSER NOBYPASSRLS`. |
| `POSTGRES_PASSWORD` | `anlaufstelle` | App-role password; at least 32 characters and not a `change-me` placeholder in production. |
| `POSTGRES_ADMIN_USER` | `anlaufstelle_admin` | Admin role for migrations/seed/retention, `NOSUPERUSER BYPASSRLS`. |
| `POSTGRES_ADMIN_PASSWORD` | -- | Admin-role password; at least 32 characters and not a `change-me` placeholder. |
| `POSTGRES_BOOTSTRAP_PASSWORD` | -- | Password for the `postgres` bootstrap superuser (initial role setup only); at least 32 characters and not a `change-me` placeholder. |
| `POSTGRES_HOST` | `localhost` (via Compose: `db`) | DB host. |
| `POSTGRES_PORT` | `5432` | DB port. |

> **Tip:** `python manage.py check_db_roles` verifies the role topology at runtime (exit `0` = ok, `1` = incorrect attribute profile, `2` = configuration incomplete). The [Compliance Dashboard](#55-compliance-dashboard) uses the same check, and [`scripts/initial-setup.sh`](../../scripts/initial-setup.sh) runs it during first-time setup. In addition, the [health endpoint](#51-health-endpoint) continuously checks the connection **actually in use** (`db_role_posture`) -- `check_db_roles` verifies that the roles *exist* with the right attributes, not that the application connects as the app role (Refs #793).

**Field Encryption (MultiFernet rotation)**

| Name | Default | Description |
|---|---|---|
| `ENCRYPTION_KEYS` | -- | Comma-separated list of Fernet keys. The **first** key is the write key (new data is encrypted with it); all others are read-only (decrypt fallback during rotation). |
| `ENCRYPTION_KEY` | -- | Single-key operation (alternative to `ENCRYPTION_KEYS`); a key ring is preferred. At least one of the two variables must be set in production; otherwise the app refuses to start. |

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
| `EMAIL_HOST` | `localhost` | SMTP host for password-reset and invite emails. |
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
reports `unreachable` in the `smtp` field, and only the technical fallback via
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
| `FILE_VAULT_MAX_IMAGE_PIXELS` | `40000000` | Decompression-bomb protection: maximum pixel count of an image upload (also sets `PIL.Image.MAX_IMAGE_PIXELS`). |
| `FILE_VAULT_MAX_ARCHIVE_BYTES` | `209715200` (200 MB) | Zip-bomb protection: maximum sum of uncompressed entries (read from the zip directory). |
| `FILE_VAULT_MAX_ARCHIVE_RATIO` | `100` | Zip-bomb protection: maximum expansion ratio (uncompressed/compressed). |
| `FILE_VAULT_MAX_ARCHIVE_ENTRIES` | `10000` | Zip-bomb protection: maximum number of entries (read from the EOCD record). |
| `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB` | `2048` (2 GB) | Operator backstop for the **per-facility storage quota** (#2129) -- not a per-file limit but the sum of all attachments of one facility, trash included. `0` disables the backstop. See [Storage quotas](#storage-quotas). |

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
| `BACKUP_DIR` | `/backups` (Compose stack), otherwise `<repo>/backups` | Read-only mount target inside the container; the health endpoint and the backup row in the system area read the backup age from it. The host scripts ignore this variable. |
| `BACKUP_HOST_DIR` | `./backups` | Base directory on the host; relative values are resolved against the repository root. `backup.sh` writes to `daily/`, `weekly/`, and `monthly/` below it; `restore-drill.sh` reads from `daily/`. Compose mounts this directory read-only onto `BACKUP_DIR`, so the container sees the same files. |
| `APP_VERSION` | `dev` | Release tag of the app image selected by `docker-compose.prod.yml`, e.g. `v0.96.0`; available tags are listed on the [releases page](https://github.com/anlaufstelle/app/releases). `/health/` exposes the value in the `version` detail field (token/authenticated callers only). |
| `SUDO_MODE_TTL_SECONDS` | `900` | Validity duration (seconds) of the re-auth window for sensitive actions (MFA disable, GDPR export). |

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

The example uses the recommended backup account, `root`, because backups run from root's cron. Group GID 1000 belongs to the container user `appuser`; mode `2750` gives it read and traverse access only and passes the group to new subdirectories. Use `umask 0027` for backup runs so that new directories and files remain readable by that group. Never make backups readable by all users.

`initial-setup.sh` then runs **without `sudo`** as the operating account. With #2219, it checks and requires exactly this directory contract; it does not run `chown` or `chgrp` on an existing directory. An existing `BACKUP_HOST_DIR` with group 1000 and mode `2750` is accepted. If the directory is missing, the script creates it only when the invoking account may set GID 1000 (a group member or root); otherwise it aborts **before the first `up`** and prints `sudo install -d -m 2750 -o root -g 1000 "$BACKUP_HOST_DIR"`. If the group or mode is wrong, it aborts with `sudo chgrp 1000 "$BACKUP_HOST_DIR" && sudo chmod 2750 "$BACKUP_HOST_DIR"` as the correction command. It does not create the directory with root privileges itself. The script then starts the database, waits for readiness, creates the schema through the one-shot job, brings up the full stack, and verifies deployment settings and database roles. Finally it creates the super-admin interactively unless one already exists (see [§ 2.1](#21-initial-installation-create-a-super-admin-bootstrap)).

The script is **idempotent** (a second run is safe: migrations become no-ops, an existing super-admin is skipped) and **fail-loud** -- if a step fails, it exits with a non-zero code and an explanation. Optional environment variables: `COMPOSE_FILE` (default `docker-compose.prod.yml`), `ADMIN_USERNAME`, `ADMIN_EMAIL`, `WAIT_TIMEOUT`, `SKIP_SUPER_ADMIN=1`. It deliberately accepts **no** password -- `create_super_admin` prompts for it interactively ([ADR-018](../adr/018-rollenmodell-superadmin.md)).

> **Why the role verification belongs here:** if the application's database role runs as a PostgreSQL superuser, Row Level Security is **silently** bypassed -- facility isolation stops working without anything failing. The script checks this fail-loud; in addition, the health endpoint reports that state permanently as `degraded` (see [§ 5.1](#51-health-endpoint)).

**Manual (equivalent, for step-by-step control):** Django migrations do **not** run automatically on `web` service startup — they are a separate one-shot step (Refs #802). For a fresh install, follow this order:

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

**Normal immediately after installation:** The MFA quota in the compliance dashboard remains critical until every MFA-mandatory account has enrolled its second factor. “Critical audit events (24h)” can also show a warning after a fresh initial setup because the setup itself creates critical audit events. Background jobs that have never run initially appear as `unknown`: view refresh and breach scan first run at the next quarter-hour and half-hour, retention/audit verification/orphan cleanup daily from 03:00, and statistics snapshots at 04:00 on the next first day of a month. If a state remains past its first scheduled run, inspect `docker compose -f docker-compose.prod.yml logs cron`. An outdated ClamAV signature set is not a normal startup state.

---

## 2. Initial Configuration

### Configure Mandatory Information (Imprint/Privacy)

Publicly reachable self-hosted instances need their own mandatory information under Section 5 DDG and their own information under Article 13 GDPR. Anlaufstelle deliberately does not ship generic boilerplate for this: the controller, contact channels, purposes, legal bases, recipients, and retention periods depend on the facility and its operation.

Create both texts as UTF-8 files on the host, for example at `./legal/impressum.md` and `./legal/datenschutz.md`. Then set both paths as seen from the web container:

```dotenv
LEGAL_IMPRINT_FILE=/run/legal/impressum.md
LEGAL_PRIVACY_FILE=/run/legal/datenschutz.md
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

Each file must be a regular, non-empty UTF-8 file no larger than 256 KiB. The files deliberately support only a small Markdown subset: headings from `#` through `###`, paragraphs and blank lines, unordered lists with `-` or `*`, and links in the form `[Text](URL)`. Link text is limited to 256 characters and link targets to 2,048 characters; longer constructs are displayed as ordinary text. Link targets are limited to `http:`, `https:`, and `mailto:`; HTML and everything else are displayed as text as well. Legal text therefore cannot inject scripts or styles into the application.

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

After successful completion, exactly one user with the role `super_admin` exists. Use this account to log in at `/login/`. The 2FA initial setup required immediately in production is complete only after the backup codes have been stored securely and acknowledged with the required checkbox; leaving early sends the next attempt back to `/mfa/setup/`. Then create the first facility and the first `facility_admin` -- in the Django admin (`/admin-mgmt/`, **Core > Facilities**) or with the setup script from [§ 2.2](#22-create-a-facility-and-application-manager). On the first visit to `/admin-mgmt/`, sudo mode asks you to confirm the password again. The `/system/` area only displays the organization and its facilities; nothing is created there.

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

After logging in as `super_admin`, create the first facility and a `facility_admin` (application manager) for it in the Django admin (`/admin-mgmt/`, **Core > Facilities** and **Core > Users**). Alternatively, the interactive setup script is available -- suitable for pure single-tenant installations where system administration and application management are the same person:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility
```

Without an option, `default` is selected. To create a facility with a bundled domain package, select it explicitly:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
  setup_facility --package awareness
```

`--package` and `--no-default-package` are mutually exclusive; an unknown package name is rejected with the sorted list of packages that are actually bundled.

> ⚠️ **Role prerequisite (#1883).** The command writes to tables with row-level security (settings, documentation types, field templates). The `NOBYPASSRLS` runtime role of `web` deliberately receives no admin secrets. The opt-in `maintenance` service connects with the BYPASSRLS role. The command checks this up front and aborts with a clear message **before** the first prompt appears. Background: [ops runbook § 9 DB role model](../ops-runbook.md). For a local development database whose role is a superuser anyway, the maintenance path is unnecessary.

The script interactively prompts for:

1. **Organization name** -- e.g., `Diakonie Musterstadt e.V.`
2. **Facility name** -- e.g., `Beratungsstelle Nord`
3. **Admin username** for the application manager (`facility_admin`; the `admin` default can be replaced)
4. **Application manager password** (enter twice)

Afterwards, the organization, facility, default settings, the **default catalog of documentation types**, the three **default shifts** (early, late, night), two **quick templates** on the "Kontakt" type, and the first `facility_admin` user are created. The facility is usable right away: without at least one documentation type, the type selector would be empty and not a single contact could be recorded.

> **All or nothing:** The run is a single database transaction. If it aborts (wrong DB role, dropped connection, catalog conflict), **nothing** half-created is left behind -- not even the admin user. Once the cause is fixed, the command can simply be repeated.

> **A repeat run leaves an existing catalog alone:** If the facility already carries documentation types or field templates (second run, facility created in the Django admin), the command does **not** apply the default catalog. Names, sort orders, colors, icons, and visibility levels maintained in the admin therefore stay untouched; the command says so at the end and points to `load_package --default` as the way to deliberately pull in the shipped state.

> **Opt-out `--no-default-package`:** If you do not want the default catalog (own configuration package, entirely custom types), append `--no-default-package`. The facility then starts **without** documentation types and only becomes usable for documentation after a package has been loaded (see [docs/packages.md](../packages.md)). The command says so explicitly at the end of the run.

> ⚠️ **Domain packages are replacements, not additions.** Load a domain package with `setup_facility --package <name>` against a fresh facility. `load_package --package <name>` aborts if it finds type or field slugs that do not belong to the selected package -- including during a dry run. Repeating the same package remains allowed. The default catalog is the existing-installation case: `--default` or `--package default` retains its documented write-back semantics.

> **Retrofitting an existing facility:** For a facility that already exists -- created in the Django admin under **Core > Facilities**, or deliberately started empty with `--no-default-package` -- the same default catalog can be applied afterwards:
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance \
>   load_package --default --facility <uuid> --dry-run
> ```
>
> `--dry-run` first shows only what would happen; without the flag the package is applied. The run never deletes anything, **but it does write the catalog properties of existing types and field templates back to the shipped state** (name, sort order, color, icon, visibility) -- unlike the initial setup, which does not touch an existing catalog at all. So look at it with `--dry-run` first; the full write-back table is in [docs/packages.md § 4](../packages.md). The facility UUID appears in the address bar of its detail page under **Core > Facilities** in the Django admin.

> **Note:** If an organization or facility with the given name already exists, it will be reused. An existing username will not be overwritten.

> **Adding a second facility in the Django admin (#1970):** Additional facilities are created at runtime under **Core > Facilities > Add facility** -- that is the only path in the user interface, and it is reserved for `super_admin`. Select the bundled catalog in the **Package** field; `default` is preselected. Saving creates the **same package-specific baseline** as `setup_facility --package <name>`: settings record, selected catalog, matching time filters, and the quick templates declared for that package. The new facility is documentable immediately; all that remains is to assign at least one account to it under **Core > Users**. The baseline is created **exactly once, when the facility is added**: the package field is absent when the facility is edited later, and saving it again does not touch its stock -- not even when that stock is empty. Facilities that were created in the admin before this version and stayed empty are therefore provisioned explicitly: the default catalog with `manage.py load_package --default --facility <uuid>` (command including the role override above), time filters and quick templates by hand under **Core > Time filters** and **Core > Quick templates**.

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
| Organizations | Core > Organizations | Parent entities (tenant level) |
| Facilities | Core > Facilities | Locations within an organization |
| Settings | Core > Settings | Configuration per facility |
| Users | Core > Users | Technical fallback; maintained primarily under Administration > Access |
| Documentation types | Core > Documentation types | Technical fallback; maintained primarily under Administration > Documentation types |
| Time filters | Core > Time filters | Named time periods for reports. **A time filter that still has handover notes cannot be deleted** (#2042) -- the admin reports the protection error. Otherwise that shift's notes would vanish silently, bypassing both their retention period and the audit log. Renaming and reordering stay possible at any time; to delete, wait for the **Retention handover notes (days)** period to elapse, or deactivate the window instead of removing it |
| Audit log | Core > Audit logs | Immutable protocol |

### 2.2c Decommissioning a Facility: Archive Instead of Delete

When a location closes, **archiving** is the intended route -- not deletion. Under **Core > Facilities**, clear the **Active** checkbox and save. Only `super_admin` may do this; facility management is reserved for that role.

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

**User offboarding.** Archiving blocks *access* but does not clear the identity data of departed staff (first/last name, display name, email, phone). That second half of the lifecycle is handled separately: [#2051 -- offboarding cleanup for departed staff](https://github.com/anlaufstelle/app/issues/2051). Until then, deactivate the affected accounts under **Administration > Access**; the username and identifier are deliberately retained so the attribution in the audit log stays readable.

### 2.3 Settings per Facility

Under **Core > Settings** in the admin, you can configure the following key fields, among others; the complete field list is shown directly in the admin form:

| Field | Default | Description |
|---|---|---|
| Full name | -- | Displayed in reports |
| Default documentation type | -- | Pre-selected type for new entries |
| Session timeout (minutes) | 30 | Automatic logout after inactivity |
| Maximum session duration (hours) | 12 | Upper bound from sign-in, regardless of activity: after this time the session always ends and a new login is required. Allowed range 1-168; must be **greater** than the session timeout (converted to minutes), otherwise the form rejects the input. A change only takes effect at the **next** login -- sessions already running keep the deadline set when they signed in |
| Retention anonymous (days) | 90 | Deletion period for anonymous contacts |
| Retention identified (days) | 365 | Deletion period for contacts of clients at the "Pseudonym only" level |
| Retention qualified (days) | 3650 | Deletion period after case closure ("With additional details" level) |
| Retention handover notes (days) | 90 | Deletion period for the manual shift notes in the handover (#2042). Deliberately short: a duty note is not a case record. The run deletes them **permanently** (no trash) |
| Facility-wide 2FA enforcement | false | Enforces TOTP 2FA for **all** users of this facility (see [Section 2.7](#27-two-factor-authentication-2fa)) |
| Quick-exit target | `https://www.wikipedia.org/` | Address opened by the **Quick exit** button and the shortcuts `Ctrl`+`Shift`+`X` / `Shift`+`Esc`. Required field, `http`/`https` only. Pick an innocuous public page without a cookie banner -- it ends up in the device history afterwards, and a consent dialog costs exactly the seconds the action is meant to save. The target does not learn where the visitor came from (no `Referer`) |
| Privacy screen on window or tab change | false | Covers the content as soon as the window loses focus or the tab moves to the background. Released by click or key press, without a timer |

> **Recommendation for women's shelters, violence counselling and awareness teams (#2136):** documentation there happens in the presence of endangering persons or in loud, public places. Turn the **privacy screen on** and review the **target page** for your region -- it should fit the device to an outsider and must not read as an "escape page". The button itself is always active and cannot be switched off; the shortcuts are fixed. Both fields are recorded in the audit log with old and new values (`SETTINGS_CHANGE`).
>
> **Unsaved input is lost** when someone triggers the action -- the sign-out clears the local drafts as always (`Clear-Site-Data`). This is stated in the user guide and in the button tooltip.
>
> **Limits you have to flank organizationally** (in full in [threat-model.md](../threat-model.md), § "Restrisiken des Mitschauen-Schutzes"): the action only replaces the **last** history entry -- earlier page views of the same session stay in the browser history with path and **page title**, and the title of a client page contains the **pseudonym**. Instruct the team to clear the history at the end of the shift or to work in a private window on shared devices. The button also acts per window; if the connection drops at the moment of triggering, the server session only ends via the session timeout or the maximum session duration; and the deletion of the local offline key is capped at 500 ms -- if it does not succeed, the key remains until the next sign-out, the idle wipe or the expiry of the 48-hour offline authorization. Therefore also: close second windows and do not leave the device unattended after quitting.

> **Baseline of a new facility (#1970).** Besides the settings record, creating a facility -- in the Django admin as well as via `setup_facility` -- produces a deliberately small starting stock that you are free to overwrite afterwards:
>
> | Stock | Maintained in the admin under | Effect when missing |
> |---|---|---|
> | Default catalog of documentation types | Administration > Documentation types | **Blocking:** without an event type the type selector in "New entry" stays empty and nothing can be documented |
> | Default shifts: early (08-16), late (16-22), night (22-08) | Core > Time filters | Convenience: the timeline then evaluates the full day instead of a shift window; handover view and live tally have no shift reference |
> | Quick templates "Kurzkontakt" and "Beratungskontakt" on the "Kontakt" type | Core > Quick templates | Convenience: recurring contacts are filled in by hand (see [Section 2.8](#28-quick-templates)) |
>
> **Once at creation, not on every save:** if a facility already carries its own documentation types, time filters, or quick templates at the moment it is created, that group is left **untouched** -- the check runs per group, and a single existing object is enough. Every later save of the facility in the admin leaves all three groups alone anyway: labels and orderings maintained in the admin are never silently reset to the shipped state, and deleted default shifts do not come back on their own. Pulling things in is explicit: `manage.py load_package --default` fetches the shipped catalog state (see [Section 2.2](#22-create-a-facility-and-application-manager)); time filters and quick templates you create yourself under **Core > Time filters** and **Core > Quick templates**. One exception: running the initial setup `setup_facility` again with the **same** organization and facility names provisions whatever is empty at that point -- if you deleted every time filter, you get the three default shifts back; the command states for each stock whether it created it or left it alone.

> **Every facility has exactly one settings record.** The row is created automatically as soon as the facility is created -- by the interactive `setup_facility`, when adding it in the admin under **Core > Facilities**, and (retroactively, for existing data) on every `migrate` run. This is not a formality: `enforce_retention` and `create_statistics_snapshots` **skip** a facility without a settings record -- no retention period would be enforced for it and no statistics snapshots would be created. Both jobs log such a case at `WARNING` level (`No settings for ..., skipping.`); if that message shows up in the log it is an operational fault, not noise.

### 2.4 Create Additional Users

The regular path for an application manager is **Administration > Access > Create account** (`/verwaltung/zugaenge/`). Creating or changing an account requires confirmation in sudo mode. The list and all actions are limited to the manager's own facility; the `super_admin` role can neither be viewed nor assigned there ([ADR-048](../adr/048-fachhoheit-zugaenge-hub.md)).

- **Username:** Login name (no real names)
- **Email:** Required for the invite flow (see below)
- **Display name:** Name shown in the user interface
- **Role:** One of the four facility-bound roles (see below)
- **MFA required:** Requires a second factor for this individual account
- **Deletion confirmation:** Adds the account to the approver pool for deletion requests

Existing accounts can be edited, deactivated, or reactivated there. Application managers cannot deactivate or demote their own account. The product UI does not permanently delete accounts: deactivation is the regular lifecycle; permanent deletion remains a reviewed technical exception. `super_admin`, bootstrap, and emergency work stay under **Core > Users** in the Django admin; there, a `super_admin` can also toggle **MFA required** for these accounts; the field sits in the same form and is therefore also visible when an application manager edits a regular account of their own facility (in addition to the Administration > Access route). Each such change appears in the audit log as `USER_ACCESS_UPDATED` with `mfa_required` among the changed fields. Quick templates and time filters also deliberately remain technical admin functions.

#### Token Invite Flow (Refs #528)

New accounts are created **without** a clear-text password. Instead, the application sends an **invitation email** containing a personalized setup link (`/invite/<uid>/<token>/`) to the email address on file. The link takes the new user to the same password-setting form as the password reset, where they set a password themselves.

1. The application manager creates the account with an email address under **Administration > Access**.
2. The system calls [`send_invite_email`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/invite.py) and generates a setup token (a dedicated `invite_token_generator`, based on `uidb64` + token hash).
3. The user receives the email, clicks the link, and sets a password. This does **not** log them in automatically -- the confirmation page leads to the sign-in form via "Zur Anmeldung" ("Go to sign-in"), where they sign in with the new password for the first time.

**Token validity (L4, Refs #1375):** Invitation and password-reset tokens have **separate** validity periods:

- **Invitations** expire after `INVITE_TOKEN_TIMEOUT` (default **3 days** = 259200 s, adjustable via the `DJANGO_INVITE_TOKEN_TIMEOUT` env var) — a newly invited staff member realistically needs days to open the link.
- **Password resets** expire after `PASSWORD_RESET_TIMEOUT` (default **2 hours**, deviating from Django's default of 3 days; adjustable via the `DJANGO_PASSWORD_RESET_TIMEOUT` env var) — a reset link is a strong recovery primitive and should be short-lived.

Both generators are additionally separated by their own `key_salt` (a reset token is not valid on `/invite/` and vice versa) and invalidate the token automatically once the user has set their first password.

**Resend the setup link:** If the email did not arrive or the token has expired, the application manager can issue a fresh token from the access detail view via **Resend invitation**. The button is only shown while account setup is still pending. For `super_admin` accounts and technical exceptions, the system administrator instead selects the relevant accounts under **Core → Users** in the Django admin and chooses the **Einladung erneut senden (Setup-Link)** action. The action only processes accounts that have an email address and whose setup is still pending; any initial password that may still be usable is revoked.

**SMTP requirements and troubleshooting:** None of these values is technically
required — without any set, the application starts with `EMAIL_HOST=localhost`,
port `587`, STARTTLS enabled (`EMAIL_USE_TLS=True`), and the sender
`noreply@anlaufstelle.app`. Operationally, i.e. for actual delivery, operators
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

> **Starting point:** A facility created via `setup_facility` **or** in the Django admin under **Core > Facilities** ([§ 2.2](#22-create-a-facility-and-application-manager)) already ships with the bundled **default catalog** -- it is ready for documentation without any further step. The list is empty only if the catalog was declined with `--no-default-package` during setup, or if the facility predates #1970. Either state persists until you change it explicitly -- saving the facility in the admin does **not** pull the catalog in (that would silently override the opt-out). In that case run `manage.py load_package --default --facility <uuid>` (the command including the role override is in [§ 2.2](#22-create-a-facility-and-application-manager)). The structure, field reference, and maintenance rules of a configuration package -- including one of your own instead of the bundled one -- are described in [docs/packages.md](../packages.md) (German). The sections below describe how to adjust the resulting set.

Under **Administration > Documentation types** (`/config/document-types/`), you define which kinds of documentation can be recorded in the facility. The matching **Core > Documentation types** view in the Django admin remains available as a technical fallback, but is not the regular configuration path. Each type consists of:

- **Name and description**
- **Field templates** (free-text fields, selection fields, etc.)
- **Retention period** in days (overrides the global facility setting if specified)

> **Age groups are not a field-template setting.** A person's age group always uses exactly **Under 18, 18–26, 27+, and Unknown** throughout the installation. Neither this administration view nor a configuration package can change the boundaries or labels per facility. The similarly named `alterscluster` selection field from the default package belongs to individual events (including anonymous contacts); changing its options does **not** change person age groups or the built-in age-group statistics. See [ADR-047](../adr/047-altersgruppen-systemweit-fix.md) (German) for the rationale and the conditions for revisiting this decision.

#### Uniqueness of the Name

The pair `(facility, name)` is unique: within one facility, each documentation-type name can be used only **once**. Trying to create a second type with the same name is rejected as a form error.

> **Configuration packages** (YAML) may contain the `category` key: packages remain valid and loadable, but the value is ignored and the load report flags it as a warning. See [docs/packages.md](../packages.md) (German).

#### Sensitivity Level

The sensitivity level controls which roles can access entries of this type:

| Level | Access | Usage |
|-------|--------|-------|
| **Normal** | All roles (including assistants) | General contacts, services |
| **Elevated** | Staff, lead, application manager | Counseling sessions, medical care |
| **High** | Lead and application manager | Crisis interventions, highly sensitive data |

#### Tally

Under **Administration > Documentation types**, open the relevant type and select **Edit**. The **"Include in tally"** checkbox explicitly enables it for the tally in the Zeitstrom; the detail view then shows the saved state. New types are not enabled by default. The setting takes effect only for active **event** types without a minimum contact stage; for person or case types the option is hidden and cleared when the type is saved.

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
| **Violent incident** | Incidents |
| **Protective measure** | Protection |
| **Group activity** | Group activities |
| **Supply (need)** | Care |
| **Explicitly belongs in no report** | *(excluded -- and **not** a defect)* |
| *(empty)* | *(appears in no report -- and is reported as a gap)* |

> The keys **Violent incident**, **Protective measure**, **Group activity** and **Supply (need)** come from the bundled domain packages for awareness work, women's shelters and youth work ([package overview #2133](https://github.com/anlaufstelle/app/issues/2133), German). They are offered to **every** facility, regardless of which package is loaded: the value range comes from the report, not from the catalogue. A new row in the example activity report only appears once a type carrying the matching key has events; the figures of existing reports do not change.

> **The report key does not affect the overview.** The key figures, charts and tables on the "Overview" tab (contacts over time, documentation types, age groups) count the events in the period **regardless of their report key** -- including events that no report evaluates. A type set to "Explicitly belongs in no report" is therefore omitted from reports that assign data through the report key (such as the example activity report), but it remains visible and continues to count in the overview. The "External report" tab does not use the report key either.
>
> **Two details belong with that rule.** First, reports about **application behaviour** continue to bind through the system type rather than the report key: the example entry-ban overview therefore shows entry-ban events even if their type is set to "Explicitly belongs in no report" -- and that is exactly how the bundled default catalogue is configured ([ADR-042](../adr/042-report-key-berichtszuordnung.md), *Implementation E4*, German). Second, the overview normally reads completed months from stored monthly aggregates, while dataset reports may calculate live; that can also make their figures diverge ([User Guide § 7](user-guide.md#7-statistics-and-export)).
>
> The overview and a report may therefore show different figures for the same period without anything being misconfigured: the overview answers "What happened?", while the reports answer "What goes into which report?". To narrow the overview to one type, use the "All documentation types" dropdown above the charts.

The example activity report is a customizable example or template -- not a binding or current official proof.

> **Empty and "explicitly excluded" are two different things.** Under "Reports" the report states how many events it does *not* show. Counted there are **only** types that make no statement at all (key empty) -- a type set to "Explicitly belongs in no report" is a complete statement and does **not** appear in the notice (#1819). Until the update, bans and notes were counted; a facility that documents entry bans saw the notice permanently without anything being misconfigured.
>
> There is a **third** case since the update: if a type carries a key that no report (any longer) knows -- from a foreign configuration package, or after a typo -- the report says so separately. "I did map it, nothing just happens" is a different finding from "I said nothing".

> **Adding later yes, changing no.** An empty report key can be **added at any time** -- that is the repair path when a type unintentionally appeared in no report. A **set** key is fixed afterwards (including "Explicitly belongs in no report"): reports about past periods must not retroactively produce different figures. When you add a key, the form points out that it affects reports generated in the future across **all** periods.
>
> When **creating** a type the form suggests matching values: system type "Crisis" suggests the key "Crisis", "Ban" suggests "Explicitly belongs in no report". Both are suggestions, not constraints -- system type and report key are allowed to diverge on purpose.

> **Set incorrectly? The repair path uses a new type.** A report key that has been set cannot be corrected -- not in the form, the Django admin or a configuration package. If the wrong value was selected accidentally, create a **new** documentation type with the correct key and set the old type to inactive through its **"Active"** field. Remember that names must be unique (see "Name uniqueness" above), including against **inactive** types: the new type therefore needs another name, or you must rename the old one first. The old type will no longer be offered for new events; existing events remain unchanged on it -- and therefore in the report row where they were counted before. Also remember the old type's **quick templates** (§ 2.8): deactivating the type does **not** remove them from the event-creation page, so deactivate them separately. **Deletion is not a substitute:** a type with existing events cannot be deleted. A reporting period spanning the changeover date will contain both types, so plan the change for a period boundary whenever possible.
>
> **Interim:** These steps currently have to be performed manually. A guided process is recorded as a proposal (#1888); it does not change the one-time nature of the key.

**Report keys on field templates.** Reports that evaluate **field values** (minute totals, needle-exchange balance, distribution of selection fields) additionally need to know **which field** to read. Until the update that only worked through the field slug: naming your numeric field "Session duration" instead of "Duration" produced a report that permanently showed zero minutes -- without any feedback. Since the update the field template carries its own report key (e.g. "Minutes") and the name is free. The former free-text field **"Statistics category"** on the field template was removed in the same step: no evaluation ever read it.

> **At most one field template per report key and documentation type.** Two fields with the key "Minutes" on the same type would mean double-counted minutes. The assignment is therefore rejected -- as is setting a key later that is already taken on a shared type; if the duplication arises anyway (e.g. through a configuration package), the report counts **neither** field and says so. The same field template on **different** types is fine, as are two similarly named fields with **different** keys.
>
> **A deliberate gap:** If a **field template** carries a key that no report knows, the report does *not* say so separately -- unlike for a documentation type. The reason is that no meaningful figure could be given there: a field with an unknown key simply yields no values, and nobody knows how many there should have been. The state stays visible through "Appears in …" on the field template itself, which then reads "Does not appear in any report."

#### Check the Binding Inventory

The "Appears in …" information answers the question for **one** documentation type or **one** field template. For a facility-wide view -- which type and field template appear in which report, and which appear in none -- use the **`inventory_report_bindings`** maintenance command:

```bash
docker compose -f docker-compose.prod.yml exec \
  -e POSTGRES_USER=$POSTGRES_ADMIN_USER \
  -e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD \
  web python manage.py inventory_report_bindings --facility <uuid-or-name>
```

Add `--format json --output <file>` to write the same inventory in machine-readable form. The command **does not change your configuration** -- it only reads it.

For **documentation types**, it distinguishes the same four states as the reports' completeness notice (assigned · explicitly excluded · unknown key · no statement). Only types whose entity type is **event** are listed, because report keys do not affect person and case types (see "Minimum contact stage"). For **field templates** -- where there is no entity-type restriction -- it reports only *bound* or *unbound*: the deliberate gap described above also applies here, so an unknown field key simply appears as unbound. A field template is considered bound as soon as its key matches a report available to your facility, even if it has not yet been assigned to a documentation type.

This is particularly useful after creating many types manually or loading a third-party configuration package, and as a cross-check before releasing a report for the first time.

> ⚠️ **Role requirement.** Like the other maintenance commands (`load_package`, `purge_orphan_field_data`, `reencrypt_fields`), this command must run with the **admin DB role** -- following the same pattern as [Ops Runbook § 3.5](../ops-runbook.md#35-manuelle-ausfuehrung); the roles themselves are explained by the database role model in [Ops Runbook § 9](../ops-runbook.md#9-row-level-security-rls). Without that role it aborts with a clear message instead of producing an empty report: under the runtime role, a facility with no visible rows would look perfectly configured because of Row Level Security.

#### Minimum Contact Stage

Defines the minimum contact stage a person must have for an event of this type to be created. Example: Counseling sessions require at least "With additional details" because additional personal details about the person must be on record.

> **Effective for event types only (Refs #1058).** **Minimum contact stage**, **retention period (`retention_days`)**, **system type** and **report key** only take effect on documentation types whose entity type is **event**: the contact-stage check only applies when creating events, and the retention run deletes events only. For **persons**, the facility-wide rules from the settings apply instead: the trash period (`client_trash_days`) and anonymisation once event retention has cleared out a person's records. **Cases have no retention period of their own** — the retention run never deletes a case by itself. It only acts on a case that has already been **deleted**: its trash period then expires after `client_trash_days` and the case is anonymised (Refs #1818, [ADR-044](../adr/044-fall-loeschpfad.md), which supersedes the earlier promise in [ADR-039](../adr/039-owner-generische-dateianhaenge.md) § 6). Without a deletion, a case's contents are still only redacted when the associated person is anonymised. `retention_qualified_days` does key off a case being closed, but it deletes that case's events, not the case.
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

#### Numeric Range Bounds (`Minimum Value`/`Maximum Value`)

Field templates of type **Number** can carry optional bounds at the same location (**Core > Field Templates**): **Minimum Value** and **Maximum Value**. They apply **only to future writes** -- creating, editing, and saving through the form or the service layer. Tightening a bound never rewrites an already-stored value: existing data outside the new bound stays unchanged in the database until it is corrected on its merits.

> **Before tightening a bound: run the read-only inventory.** With the admin DB role:
>
> ```bash
> python src/manage.py inventory_number_ranges --facility <UUID-or-name>
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
| **Single user** | `User.mfa_required` | Administration > Access > *Account* > "MFA required" field | 2FA mandatory for this user, disabling is blocked |
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

There are also **backup codes as a second factor** for exactly this recovery case (Refs #588). On 2FA setup the user receives 10 single-use codes that should be printed or stored in a password manager. The user must then confirm through the required checkbox that the codes have been stored securely; only then is the TOTP device confirmed and setup complete. Leaving the page first sends the next attempt back to `/mfa/setup/`. At the 2FA login prompt the user can enter a backup code instead of a TOTP code. Used codes are invalidated and recorded in the AuditLog (`MFA_BACKUP_CODE_USED`). If all 10 codes have been spent or lost, the admin reset above remains the fallback.

`django-otp` increasingly throttles TOTP and backup-code checks after multiple failed attempts. A correct code can also be rejected during the resulting waiting period; before resetting the device, wait for the lockout interval instead of immediately trying more codes.

#### Account Lockout

After **10 failed login attempts** the account is automatically locked (the login service reads the threshold from [`src/core/services/security/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/login_lockout.py)). The locked user sees an information page and can no longer sign in. There are four recovery paths:

**Path A -- UI (for facility-bound users):** The application manager (`facility_admin`) of the facility can unlock the accounts of their own users:

1. **Administration > Access** -- select the affected user in their own facility.
2. Under "Security actions" click **Unlock account**.
3. The unlock is recorded in the AuditLog as `LOGIN_UNLOCK` (the `LOGIN_FAILED` log itself is immutable thanks to the `auditlog_immutable` DB trigger).

**Path B -- CLI (for recovery, especially super-admins):** If no other user in the system has access (single-super-admin lockout, or a locked `facility_admin` with no second `facility_admin`), the hosting operator unlocks the account directly on the server:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py unlock <username>
```

The command works for all roles (`super_admin`, `facility_admin`, `lead`, `staff`, `assistant`), writes a `LOGIN_UNLOCK` AuditLog entry, and is the only recovery option for locked super-admins.

**Path C -- Self-service via email (Refs #869):** If the user has an email address on file, they can unlock themselves from the login page via the "Unlock account via email" or "Forgot password?" links -- both paths write a `LOGIN_UNLOCK` AuditLog entry. The dedicated unlock link at `/account/recovery/` sends a signed token valid for 30 minutes (no new model; `TimestampSigner` with salt `lockout-recovery.v1`). Anti-enumeration: the confirmation page is identical whether or not the email belongs to an account.

**Path D -- Self-service via MFA backup code:** If the user has 2FA enabled and has noted one of their backup codes, they can unlock themselves at `/account/recovery/backup-code/` using their username and code. Audit trail: `LOGIN_UNLOCK` (trigger `backup_code`) + `BACKUP_CODES_USED` (flow `lockout_recovery`). Each code is single-use -- the next recovery event consumes the next code.

Lock, unlock, and any attempts during the lock window are all written to the AuditLog -- use the "Login failed" / "Account unlocked" filter for retrospective analysis. The `LOGIN_UNLOCK` detail block contains a `trigger` field: `admin` (admin action), `cli` (CLI command), `password_reset` (Path C via standard reset), `recovery_token` (Path C via dedicated link), `backup_code` (Path D); unlocks via recovery token or backup code also record the source IP on the audit entry.

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
4a. **Content and extension must belong to the same family** (Refs #2128): the magic-bytes check rejects a file whose content is detected as an image but whose extension is not an image extension (`foto.docx`, `scan.odt`). Without that binding it was enough to name a photo `.docx` and declare `image/jpeg` correctly -- metadata stripping **and** decompression-bomb protection would both have waved it through.
4. **Metadata stripping** for image uploads (Refs #2128): JPEG, PNG, WebP and TIFF are re-encoded and lose their EXIF data (including the **GPS capture location**, device model/serial, capture timestamp, embedded thumbnail), PNG text chunks (`tEXt`/`iTXt`) and XMP/IPTC blocks. **Orientation is preserved** -- it is baked into the pixels, so portrait photos do not flip; the ICC colour profile is kept as well. What counts is the type **detected** in step 4a via magic bytes, not the file extension. Every non-image file (PDF, DOCX, ODT...) stays byte-identical. **Always on, no toggle** -- the application is not a forensic evidence tool. If a file cannot be decoded despite matching magic bytes, the upload is rejected (fail-closed, audit reason `image_undecodable`).

> **GIF and BMP are deliberately not cleaned.** This is not an oversight: GIF89a supports both a *comment extension* and an *XMP application extension*, and BMP v5 can embed an ICC profile -- those blocks **are preserved**. A Pillow re-encode would re-quantize the GIF colour palette and change frame timing and disposal behaviour of animated files; that is a visible change to the picture for a metadata channel practice does not use (counselling photos are JPEG or PNG). **Recommendation:** to rule it out, leave `gif` and `bmp` out of `allowed_file_types`.
5. The payload is encrypted chunk-wise with Fernet (MultiFernet) (write key = first entry in `ENCRYPTION_KEYS`) and stored under `MEDIA_ROOT`.
6. Downloads happen exclusively through the protected Django view (no direct webserver access to `MEDIA_ROOT`).

> **Note on document metadata:** Stripping only applies to images. Metadata in PDF (`/Author`, `/Creator`) and OOXML files (`docProps/core.xml`, `dc:creator`) is left untouched -- those typically carry the real name of the **staff member** working on the file, not of the data subject. If that matters, clean the documents before uploading.

> **Plaintext staging buffer** (#1661): Uploads above `FILE_UPLOAD_MAX_MEMORY_SIZE` (default 2.5 MB) are buffered by Django as a plaintext temp file on disk (`FILE_UPLOAD_TEMP_DIR`) before step 3, ahead of scanning and encryption. In the shipped Compose files this buffer lives on a `/tmp` tmpfs (RAM) rather than persistent storage. Details, verification, and fallback options without tmpfs: [ops runbook § 5.7a](../ops-runbook.md).

#### ClamAV Service (`docker-compose.prod.yml`)

The production Compose file includes a dedicated `clamav` container with the binary-smoke healthcheck `clamdscan --version`. The web service waits for `service_healthy` before starting. Reference: [`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml). Configurable via `CLAMAV_ENABLED` / `CLAMAV_HOST` / `CLAMAV_PORT` / `CLAMAV_TIMEOUT` (see [ENV reference in § 1](#full-environment-variable-reference)).

#### Health Check

In production the `/health/` endpoint checks ClamAV daemon reachability in addition to the database. If ClamAV is enabled but unreachable, the JSON payload reports `status: "degraded"` while the HTTP status remains **200**. Monitors must therefore evaluate the payload's `status` value; an HTTP-code-only check does not detect “uploads are currently unavailable”.

#### Key Rotation (`ENCRYPTION_KEYS`)

MultiFernet accepts a list of keys. To rotate:

1. **Generate a new key** and add it as the **first** entry in `ENCRYPTION_KEYS`: `ENCRYPTION_KEYS=new,old`. Newly written data is encrypted with `new`; existing data stays readable via `old`.
2. Optional: run `python manage.py reencrypt_fields` to re-encrypt existing fields with the new key (see [`src/core/management/commands/reencrypt_fields.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/reencrypt_fields.py)). A `--dry-run` reports how many records would be affected without writing anything. **The run must use the admin/owner role** -- see the box below.
3. After successful re-encryption, remove the old key from the list.

> **Important:** Never **replace** the old key instead of **appending** the new one -- any still-unrotated data would become unreadable.

> ⚠️ **Role prerequisite (Refs #1058).** `reencrypt_fields` reads and writes tables protected by row-level security. The `NOBYPASSRLS` runtime role of `web` has no facility context outside a request and would see **0 rows**. Always invoke it via the maintenance service ([ops runbook § 9.5](../ops-runbook.md)):
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance reencrypt_fields --dry-run
> ```
>
> Since #1058 the command aborts with a clear error message when the bypass context is missing (fail-loud instead of "0 rotated, success reported"). On older builds without that guard: **never run step 3 after a run that reports `0` everywhere** -- the old key would leave the list although nothing was rotated.

**What `reencrypt_fields` covers** (Refs #1058):

| Data | Scope |
|---|---|
| **`Event.data_json`** | **complete -- including soft-deleted events** (#1724); per row with a row lock and compare-and-swap |
| `EventHistory.data_before` / `data_after` | complete (append-only, updated through the replication bypass); per row with a row lock and compare-and-swap on the columns being changed (#2177) |
| `Attachment.original_filename_encrypted` | complete -- attachments on events, clients and cases (the model was named `EventAttachment` until #1738); per row with a row lock and compare-and-swap |
| TOTP secrets (`otp_totp_totpdevice.key`) | complete, per device with a row lock and compare-and-swap |
| **`Client.data_json`** | **complete -- including soft-deleted persons**; per row with a row lock and compare-and-swap |
| **`Case.data_json`** | **complete -- including soft-deleted cases**; per row with a row lock and compare-and-swap |
| `RectificationRequest.old_value` / `new_value` / `reason` / `review_note` | complete, per row with a row lock and compare-and-swap on the value columns being changed |

Since dynamic fields became entity-wide (ADR-037), **persons** and **cases** also carry encrypted extra fields in `data_json`. The run rotates them as well and additionally heals plaintext leftovers in fields that the document type declares as encrypted.

For events, persons and cases, **soft-deleted records are rotated too**. That is deliberate: a record waiting in the trash must not stay behind under a retired key just because it is pending deletion. The former gap for soft-deleted events (the `is_deleted=False` filter) has been closed since #1724.

> **Operational note:** The run protects every row it changes with a row lock and compare-and-swap. If a concurrent domain edit wins, the rotation skips its stale state and warns: "N Zeilen wegen paralleler Änderung übersprungen — Lauf wiederholen" (N rows skipped due to a concurrent change -- repeat the run). The rotation never overwrites an interim change, including redaction or anonymization runs that do not advance `updated_at`. Since #2177 this also covers the **event history**: if the nightly retention run or an Art. 17 GDPR anonymization redacts a history row while the rotation is running, the rotation skips that row and reports it as skipped -- the redacted state stands. Rows that are already redacted are no longer rotation candidates at all: they carry no encrypted values, only the redaction marker. Repeat it and retain the old key until a run completes without this warning. A maintenance window remains advisable to reduce database load, but it is no longer required to prevent lost updates.

#### Upload Limit

- Per facility: `Settings.max_file_size_mb` (default **10 MB**) -- see [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py).
- Global: Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` applies as an additional hard limit. Raise it via ENV or settings override if larger uploads must be allowed.

#### Storage Quotas

The upload limit above applies **per file**. Refs #2129 adds a cap on the **sum** of all file attachments **per facility**. The reason is multi-tenant operation: without a quota, one facility can fill the shared `MEDIA_ROOT` with nothing but legitimate uploads -- and the consequences (upload failures, backup failures) hit **every** facility on the instance. The disk check in `/health/` reports a full volume, it does not prevent one, and it knows nothing about facilities.

| Knob | Where | Meaning |
|---|---|---|
| `Settings.max_storage_mb` | Administration → Settings, group "Files" | Quota of **this** facility in MB. Empty = no facility-specific quota. |
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
- Not supported: outdated browsers without a modern crypto API (offline mode is grayed out in that case).

#### Streetwork Levels

- **Level 2 (Read cache):** Selected person dossiers are locally encrypted and cached before streetwork so they can be viewed offline.
- **Level 3 (Offline edit):** Events and notes can be captured offline and synchronize on the next online period. Conflicts between an offline edit and a concurrent server change are presented to the user as a **side-by-side diff** for manual resolution.

### 2.11 CSV Import and Export: Formula Protection

Every CSV export (statistics export, dataset reports, audit-log export) neutralizes **formula injection**: a cell starting with `=`, `+`, `-`, `@`, a tab or a line break would be executed as a **formula** by Excel or LibreOffice when the file is opened. Such cells therefore receive a leading apostrophe (`'`) -- the spreadsheet displays the original value but does not execute it. Refs #719.

Since #1945 the protection also applies when **invisible characters** precede the formula character -- a space, a non-breaking space (NBSP), a byte-order mark or zero-width characters. Spreadsheets discard those characters on import and evaluate the cell anyway, so `" =1+1"` is apostrophized as well. The original value is preserved in full; nothing is trimmed. This covers value cells **and** header cells -- a field-template name is free text entered by the facility admin and therefore the same vector.

> **Important -- your own exports are not round-trip safe:** the CSV import does **not** strip the protective apostrophe again. Re-importing a previously exported file turns the value `=Text` permanently into `'=Text`. This is neither a bug nor an error reported by the import run, but a deliberate boundary: the import engine compares the **raw** file row against its error messages to keep cell contents (potentially client data) out of a run's result log. If it de-apostrophized first, that comparison would no longer match and a cell value could slip into the log -- data protection takes precedence over convenience here.
>
> **In practice:** use the CSV import to **migrate data from other systems**, not as a backup-and-restore path for your own data. For the latter, use the encrypted database and media backup provided by `backup.sh` (see [§ 3](#3-backup-and-recovery)). If you do need to re-import an exported value, remove the leading `'` in the spreadsheet beforehand.

If the documentation type or mapped field templates change while an import is in progress, the application rejects the dry run or commit and directs the user back to the mapping step. Removed or deactivated target fields are therefore never skipped silently. A commit always uses the **most recently confirmed mapping**, even if it was confirmed in another browser session.

The report exports in **JSON and XML** deliberately do not sanitize -- there is no formula context there, and CSV escapes would corrupt the machine-readable values.

Relevant files: [`src/core/services/system/export.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/system/export.py) (`_sanitize_csv_cell`), [`src/core/services/import_engine.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/import_engine.py).

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
- `BACKUP_HOST_DIR` as the base directory on the host; the script writes exclusively there and ignores `BACKUP_DIR`, which is used only as the container mount target (see the [environment table](#complete-environment-variable-reference)).

Root's cron needs write access, while `appuser` in the container (UID/GID 1000) must be able to read and traverse the directory so that health and compliance checks can find the backups. Before setup, create the configured `BACKUP_HOST_DIR` as `root:1000` with mode `2750`, exactly as in [Step 4](#step-4-start-the-stack). `initial-setup.sh` runs without `sudo`, accepts a correct existing directory, and does not change its owner or group; if the contract is violated, it aborts before the first `up` with the correction command documented there. `umask 0027` preserves group readability for new content without making backups world-readable.

> **RLS is security-critical here:** `pg_dump` must run as `POSTGRES_ADMIN_USER`. The `POSTGRES_USER` application role is `NOBYPASSRLS`; a raw dump under that role fails at `FORCE ROW LEVEL SECURITY` or would not be a complete backup of facility-scoped data. Do not replace `backup.sh` with a custom `pg_dump -U anlaufstelle` command or the application role.

Keep `.env`, or at least `BACKUP_ENCRYPTION_KEY` and the complete `ENCRYPTION_KEYS` history, separate from the production host. Without the backup key the archive cannot be decrypted; without the field/vault keys its encrypted contents remain unreadable. Configure an off-site destination as well to protect against complete host loss; details are in [Ops Runbook § 6.6a](../ops-runbook.md#66a-off-site-backup-sync-refs-738).

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

Do not add a separate `find ... -delete` job: `backup.sh` rotates database, media, and HMAC files together. Verify the latest backup regularly:

```bash
cd /opt/anlaufstelle
sudo ./scripts/ops/backup.sh --verify
```

The verification run validates the HMAC before decryption, restores the latest database dump into a temporary database, compares critical RLS tables with the live database, and checks the latest media archive. The more comprehensive quarterly restore drill is documented in [Ops Runbook § 6.6](../ops-runbook.md#66-backup-restore-drill-refs-720-739).

### 3.3 Recovery

[`scripts/ops/restore.sh`](../../scripts/ops/restore.sh) is only for the plain-SQL archives (`*.sql.gz.enc`) created by `backup.sh`, with the `.hmac` sidecar directly beside each archive. A `pg_restore` archive from another backup path is not compatible.

1. Schedule a maintenance window, block public access, and prepare an empty target database with the same role names as the backed-up system. The application role (`POSTGRES_USER`, `NOBYPASSRLS`) and the admin/owner role (`POSTGRES_ADMIN_USER`, `BYPASSRLS`) must exist before data is loaded. The complete destructive procedure for a new host is in [Disaster Recovery § 6](../disaster-recovery.md#6-wiederherstellung-auf-einem-fabrikfrischen-host).

2. Ensure that the DB and web containers are running for the database and media restore while Caddy and cron remain stopped.

3. Start the restore from the repository root. The second argument is optional, but required for a complete recovery including file attachments:

```bash
set -a
. ./.env
set +a
BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-./backups}"
./scripts/ops/restore.sh \
  "$BACKUP_HOST_DIR/daily/anlaufstelle_YYYY-MM-DD_HHMMSS.sql.gz.enc" \
  "$BACKUP_HOST_DIR/daily/anlaufstelle_YYYY-MM-DD_HHMMSS_media.tar.gz.enc"
```

Before even asking for confirmation, the script first verifies the database HMAC and, when the second argument was supplied, the media HMAC. Only after both preflight checks succeed and the operator enters `ja` does it restore the database; it repeats the corresponding check immediately before each archive is used, then replaces and extracts the media contents. The SQL dump is deliberately loaded through the local bootstrap superuser (`POSTGRES_SUPERUSER`, default `postgres`): only that role can reliably restore the included owner/`FORCE RLS` statements and data copies. If media extraction aborts, the media contents are incomplete and the full media restore must be repeated.

4. Run migrations with the admin role, then start the stack:

```bash
docker compose -f docker-compose.prod.yml run --rm \
  --entrypoint=/app/docker-migrate.sh maintenance
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d
```

5. Check database roles, health, and samples from both the database **and** file vault. See [Section 5](#5-monitoring), [Ops Runbook § 9](../ops-runbook.md#9-row-level-security-rls), and the [disaster-recovery smoke verification](../disaster-recovery.md#7-smoke-verifikation-nach-restore).

---

## 4. Updates

### 4.1 Pull New Image and Update the Stack

Migrations run as a separate one-shot job **before** the `web` service is restarted — it no longer runs migrations on startup itself (Refs #802). For details, see the canonical source [`docs/ops-runbook.md` § 1.2](../ops-runbook.md#12-deploy-schritte).

```bash
# 1. Download the latest image
docker compose -f docker-compose.prod.yml pull web

# 2. Run migrations as a one-shot job
docker compose -f docker-compose.prod.yml run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 3. Restart the stack (web + caddy, DB stays up; brief downtime)
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d web caddy
```

Check the logs afterwards:

```bash
docker compose -f docker-compose.prod.yml logs web --tail=50
```

With an additional file configured, use the same complete file set built above:

```bash
docker compose "${compose_files[@]}" logs web --tail=50
```

### 4.2 Before an Update

- Always create and verify a current database and media backup first (see [Sections 3.1–3.2](#31-encrypted-database-and-media-backup)).
- Read the changelog for breaking changes and required configuration updates.

### 4.3 Rollback

If problems occur after an update:

```bash
# Revert to a specific image tag
# In docker-compose.prod.yml: image: ghcr.io/anlaufstelle/app:v1.2.3
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d

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

If AuditLog retention is set to `0` months in one or more facilities, the **Retention** category shows one aggregated warning with the affected facility names; resolve it under **Core → Settings → Retention**.

Each check returns a status of `ok` / `warning` / `critical` / `unknown`; a single failing check does not bring down the whole dashboard. The area is **super-admin only** (`facility_admin` receives a `403`). The freshness thresholds for background jobs and the restore marker are documented in the [Ops Runbook](../ops-runbook.md).

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

**Limits -- stated plainly.** The scrubbers are rule- and key-based (best-effort, defense-in-depth); they do not replace a content review of every individual event field. This includes the URL masking: it knows this application's routes, not the meaning of foreign values. A secret that a log line spells out as prose rather than as a path, a short path segment on a route that does not resolve, or a clear name inside a hand-written error message still pass through -- as does any value that a future route carries in its path without anyone having thought about it. The heuristic can also err the other way: a token-shaped segment on a non-resolving path is masked even if it was harmless -- the full line remains in the server logs either way. And a transaction name the application sets itself (`transaction_info.source` other than `url`) is left untouched. Two SDK features are **off** today and therefore out of scope: Sentry's own log transport (`enable_logs`) and streaming of individual trace segments -- both bypass the `before_send` hook and must not be switched on without a fresh review. The most important remaining limit follows from the log rule: what is protected is the **interpolated** value, not the template. Writing a name straight into the text of a log or error message (`"Anna Mustermann not found"` instead of `"%s not found"`) bypasses every automatism -- so for development the rule is: **no clear names in log templates and error texts**. A name inside a free-form error message with no key in front of it is equally undetectable. And note that all of this describes what reaches the tracker. The server log file carries the same lines, but since #2228 with masked URL paths: the query part is dropped, a path appears as its route schema (`/invite/<uidb64>/<token>/`) or loses its token segments, and the line for a CSRF 403 names the account as `user_pk` instead of the login name -- `Referer` and `Origin` likewise appear without their query part. This holds in **both** output formats (`LOG_FORMAT=text` and `LOG_FORMAT=json`) and also for the lines the framework itself writes about rejected, forbidden and not-found requests. The client IP (`remote_addr`) of a CSP violation report is not masked: it is the only handle against abuse of that unauthenticated surface and is therefore deliberately readable (see **Logs** below). For maximum data minimization, choose one of the following levels:

1. **Leave `SENTRY_DSN` unset** -- recommended for high-sensitivity deployments, especially on older releases whose SDK scrubbers do not yet include all the layers above: no error-tracking processor, no data egress; errors still appear in the server logs.
2. **`SENTRY_TRACES_SAMPLE_RATE=0`** against a tracker **inside your own trust boundary** (e.g. self-hosted): disables performance transactions entirely and limits egress to scrubbed error events sent to an instance that is not an additional third party.

**Logs.** Server logs are PII-scrubbed in **both** formats -- the default `LOG_FORMAT=text` and `LOG_FORMAT=json` both run messages, tracebacks and the structured extra fields through the same scrubber (email addresses, passwords and token-like values become placeholders). Two fields are deliberately **not** scrubbed, because their purpose is to make technical security events attributable: the client IP (`remote_addr`) and the browser identification string (`user_agent`) of a CSP violation report. Both can identify a person -- if you need short log retention, plan for it on their account. The purely technical identifiers (`request_id`, `user_id`, `facility_id`, `user_pk`) also pass through unchanged: they contain no free text and merely reference a record, so there is nothing for the scrubber to catch. `user_pk` appears, for example, in the invitation log lines, which deliberately carry neither user name nor email address -- without that identifier the entry could no longer be attributed to an account. For GDPR contexts with log aggregation, `LOG_FORMAT=json` is still recommended (structured, machine-readable), but data minimization is no longer tied to that format.

**Self-hosted tracker (GlitchTip).** GlitchTip accepts standard Sentry DSNs, and this project uses only core SDK features (error events plus optional trace sampling). This combination was verified empirically: with `sentry-sdk[django]` `2.64.0` against GlitchTip `6.2.1` (Docker image `glitchtip/glitchtip:6`), server error events are transmitted, parsed plausibly (stack trace, tags, request context) and shown as issues; performance transactions (`SENTRY_TRACES_SAMPLE_RATE > 0`) are **accepted** as well and grouped into transaction groups -- they are neither silently dropped nor do they raise an error. The server-side `before_send`/`before_send_transaction` scrubber applies unchanged (no frame locals, stripped query strings/cookies/request bodies); the rule- and key-based limits described above still hold. Other version combinations are untested, and a third-party project naturally remains outside this project's control.

**Hosted Sentry service.** Before enabling a SaaS provider instead of an EU-based or self-hosted instance, verify its data location, retention period, and any third-country transfers and safeguards, and conclude a data processing agreement. List the provider and its subprocessors in section 6 of the DPA template in the GDPR package.

To connect a self-hosted tracker:

1. Run a GlitchTip instance and create a project there -- GlitchTip shows its DSN in the form `https://<public_key>@<glitchtip-host>/<project_id>`.
2. Pass that value to the app as `SENTRY_DSN` -- **no code change required**. Optionally set `SENTRY_TRACES_SAMPLE_RATE` (default `0.1`; `0` disables transactions entirely, see the escalation levels above).
3. Expectation: server error events appear as issues, optional trace sampling as transaction groups. Only the server SDK runs (no browser SDK, no Session Replay).

Operating the tracker instance (setup, backups, retention, upgrades) is operator infrastructure and covered by the [GlitchTip documentation](https://glitchtip.com/documentation) -- **out of scope** here; the tracker's database belongs inside the same trust boundary as the app data and needs the same access and retention rules.

> **Not legal advice:** This section describes technical measures. A data protection impact assessment and the record of processing activities remain with the operator as the controller.

### 5.7 Breach Detection Scan

The `detect_breaches` management command heuristically analyzes the audit log for anomalies (failed-login bursts, mass exports/deletions, distributed login attacks) and writes hits as audit events; optionally a webhook (`BREACH_NOTIFICATION_WEBHOOK_URL`) is notified. The windows/thresholds are controlled via the `BREACH_*` variables (see the [ENV reference in § 1](#full-environment-variable-reference) as well as [ops runbook § 6.5b](../ops-runbook.md)).

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only to deployments **without** that compose file (k8s, custom orchestration). Setting up both makes the job run twice — details: [ops runbook § 3](../ops-runbook.md#3-cron-jobs).

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

1. **The threshold is a setting of its own, not a side effect of retention.** It is configured per facility in the admin under **Core → Settings** (`k_anonymity_threshold`, default 5) and applies to **every report artifact that leaves the organization** -- regardless of whether k-anonymization is enabled for the retention run. [§ 7.3](#73-retention-periods-and-enforce_retention) documents both fields together in the retention context, but they are independent.

2. **Two views are deliberately not suppressed** and can therefore be used to check whether any data exists at all: the on-screen **statistics overview** and the PDF export in internal mode (`?internal=1`, with an "INTERNAL" banner; both restricted to lead/administration roles). It is expected that the overview may show different figures from a report. Forwarding content from these internal views falls outside the guarantee.

3. **A boundary remains even when no suppression is visible:** the guarantee applies per artifact and period, not across multiple releases -- see [§ 8, "Releasing Reports: What You Need to Know"](#8-statistics-snapshots-materialized-view).

> **Important:** Lowering the threshold is a privacy decision, not a troubleshooting step. It affects every report that leaves the organization; document the change accordingly -- the audit log records it as "Settings changed" with the old and new values ([§ 7.4](#74-audit-log)).

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
| Handover notes (shift note) | `retention_handover_notes_days` | 90 days |

> **The handover note is hard-deleted, not soft-deleted** (Refs #2042) -- like activities. What counts is the date of the last change (`updated_at`), not creation; key rotation deliberately does not advance that timestamp and therefore does not extend the period. There is **no** legal hold for handover notes: holds protect client, case and event records for ongoing proceedings, whereas the shift note is a duty note with no personal reference in the data model (see [security-notes.md](../security-notes.md)). Every run with matches writes an audit row (`DELETE`, category `handover_notes`) carrying the count and the period -- never any text.

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

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only to deployments **without** that compose file (k8s, custom orchestration). Setting up both makes the job run twice — details: [ops runbook § 3](../ops-runbook.md#3-cron-jobs).

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

> **Distinction from Art. 18 GDPR (Refs #2045):** A legal hold is **not** a restriction of processing under Art. 18. It blocks **erasure** (Art. 17(3)) and leaves day-to-day work on the record untouched. An Art. 18 restriction is the reverse: the data stays stored, but substantive work on the client record is blocked. That is what the separate **restriction marker** on the client record is for. The two are independent of each other.

#### Retention of the Event History

Every save of an event writes a **history version**: who changed which fields to which values, and when. Until version 0.89 these versions had **no retention period** — they outlived the deletion period of the event itself. Concretely: an event whose `retention_identified_days` have expired is emptied, but its earlier versions kept every wording ever stored. The period was formally enforced and factually undermined. Since [ADR-050](../adr/050-eventhistory-retention.md), `enforce_retention` closes this gap in two stages.

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

The first run after the update may take longer than usual: it works through the backlog of versions of living events. The versions of already deleted events were handled by the migration.

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
| Data at the "With additional details" level viewed | Opening the **person detail page** (`/clients/<id>/`) of a person at that level — not the person list, an event, a case or a search result |
| Export | Data export by a user — including data subject access requests for individual people and on-screen views of the external report. **Released report files** (CSV/PDF/JSON/XML with a reporting period) are a subset: only those are exempt from the retention deadline and appear in the [release overview](#8-statistics-snapshots--materialized-view) |
| Deletion | Manual or via `enforce_retention` |
| Level change | Change in a person's contact status |
| Settings changed | Changes to facility settings |
| File download | Downloading an attachment from the file vault |
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

Five classes are **exempt** from this period and are kept regardless of age (`PRUNE_EXEMPT_Q` in [`src/core/retention/audit_pruning.py`](https://github.com/anlaufstelle/app/blob/main/src/core/retention/audit_pruning.py)). Four of them depend on the action (`PRUNE_EXEMPT_ACTIONS`):

- `SECURITY_VIOLATION` — forensic evidence from breach detection,
- `RETENTION_RUN_COMPLETED` — the marker the compliance dashboard reads to show the last successful retention run,
- `AUDIT_PRUNE_CHECKPOINT` — the tombstone of the pruning itself,
- `EVENT_HISTORY_REDACTED` — the per-batch proof of the history sweep (see [§ 7.3 "Retention of the Event History"](#retention-of-the-event-history)). It documents that immutability was legitimately opened for exactly these rows; if it vanished before the rows it vouches for, a silent change to append-only-protected data would remain.

On top of that — **regardless of the action** — comes the fifth class: entries about a **released report file carrying a reporting period** (CSV/PDF/JSON/XML). Without that exemption the release overview would feign completeness. Such entries hold the period, the format, the acting person and the recorded recipient/purpose fields (unvalidated free text), and are kept indefinitely. Plain on-screen views of the external report and subject access requests without a reporting period run under the same audit action but stay subject to the period — the distinction is documented in [§ 8 "Releasing Reports"](#releasing-reports-what-you-need-to-know).

**Checkpoint mechanics:** the audit log is chained via HMAC (every entry references the previous one). Pruning necessarily tears that chain. So that the integrity check does not report the gap as tampering, every prune run writes an `AUDIT_PRUNE_CHECKPOINT` entry recording the chain boundaries of the deleted rows, which legitimises the discontinuity. The chain itself is **never** rewritten — the log stays append-only.

> **Important:** the period only takes effect if `enforce_retention` actually runs (see [§ 7.3](#73-retention-periods-and-enforce_retention)). Deleted entries are gone for good — if you need longer evidence retention, define an **external** archiving strategy (e.g. following BSI recommendations or your Data Protection Impact Assessment) and export the log before the period kicks in.
>
> **Residual data-protection risk:** because the log is append-only, anonymising a person does **not** redact it — details and countermeasures in [`security-notes.md` § Audit-Log-Restdaten überleben die Art.-17-Löschung](../security-notes.md#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753) (German).

### 7.5 Deletion Requests (Four-Eyes Principle / Dual Approval)

Deletion requests for personal data are processed using the four-eyes principle (dual approval): a request must be approved by a **second authorized person** before data is permanently deleted. This protects against accidental or unauthorized deletion — including by privileged roles themselves.

**Approver pool — "deletion confirmation" permission** (Refs #1053): Who may approve is not derived from the user's role; instead, approvers are curated via the individually assignable user permission *deletion confirmation* (`can_confirm_deletion`). On first rollout, existing leads and facility admins receive the permission automatically. Afterwards, the facility admin can grant or revoke it under **Administration > Access** as needed — for example, granting it to an experienced staff member so that a second approver is always available even if there is only one lead. A permission holder can never approve their own requests; the view, service, and database layers all enforce **requester ≠ approver**.

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
| **What happens immediately — and what with the nightly run** (Refs #2085) | **Immediately:** pseudonym, notes, custom fields, case and episode content, the content of the events themselves, the timestamp generalization and the file attachments. The person carries no personal data afterwards. **With the next retention run** (`enforce_retention`, 03:00 at night by default): the **earlier versions** of the event documentation — the version history that every save of an event creates. The reason is the database role split: those versions are write-protected, and the web process deliberately does **not** hold the required privileges (see `ops-runbook.md` § 9.5). The success message and the confirmation dialog say so explicitly. If an erasure has to be completed immediately, the system operators run the retention run once by hand. A **legal hold** that arrives within this window defers the versions — they are then kept in full. |
| **Purpose** | (a) **Immediate erasure under Art. 17** when a data subject insists on erasure without delay and 30 days of trash are not defensible. (b) **Resolving configuration blockers**: a person in the trash keeps their document type and field values and thereby blocks deleting that type or field template. Previously the only options were restoring (a short-lived data resurrection) or waiting. The blocker message in the configuration UI now names the trash share and links there. |
| **Limit: legal hold** | If an **active legal hold** exists on the person or on one of their events, the action is rejected with an error message — exactly like the nightly retention run (Refs #1066, #1756). A manual action must not override a hold. Lift the hold first, then anonymize. |
| **Limit: trash only** | The action applies exclusively to records that are already soft-deleted. The four-eyes request path remains the only way **into** the trash — there is no shortcut around approval. |
| **Trail** | Audit entry `CLIENT_ANONYMIZED` with `trigger="manual"`, distinguishable from the automatic run. |

The record stays soft-deleted afterwards (anonymized, not restored) and does not vanish from the trash listing — it simply no longer carries personal data. **"Restore" is no longer offered for that row afterwards** — the confirmation dialog's promise holds literally: the button disappears, and calling the restore URL directly is rejected with a message (Refs #2085). The person action now behaves like the case action, which has had this guard since [ADR-044](../adr/044-fall-loeschpfad.md). The success message therefore reads "Person wurde anonymisiert. Frühere Fassungen ihrer Ereignis-Dokumentation werden spätestens mit dem nächtlichen Aufbewahrungslauf getilgt." — it deliberately does not claim that everything is already gone at that moment. Since #1737, people in the trash can also no longer be edited via a direct URL (`/clients/<id>/edit/` returns 404, like the detail view).

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
| Access (Art. 15) | Generate the data access record as JSON or PDF from the client view (lead or application manager, re-authentication required) -- scope see below |
| Rectification (Art. 16) | Edit fields directly as application manager in the admin UI |
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
| **Who sets it?** | Staff and above, from the client record. When a rectification request is filed, the marker is additionally **offered** (opt-in, not pre-selected). |
| **Who lifts it?** | A **second** authorised person or a **manager**. Whoever set the marker does not lift it alone. |
| **Visibility** | A permanent banner on the client record showing legal ground, timestamp and the person who set it; a "restricted" marker in the client list. |
| **Trail** | Audit entries `PROCESSING_RESTRICTED` and `PROCESSING_RESTRICTION_LIFTED`, each carrying the legal ground — never the contested matter itself. |

**Not to be confused with the legal hold** (§ 7.3): that one blocks *erasure* (Art. 17(3)), the restriction marker blocks *use*. The two are independent of each other.

**To be handled organisationally:** the software does not enforce the duty to inform the data subject **before** lifting the restriction (Art. 18(3)) — record it in your data subject request process.

#### History Diff: Decryption for Authorized Roles

On an event's detail page, **Change history** shows the stored before/after states. Encrypted fields are decrypted at display time if the current role may see the field according to the documentation type and field sensitivity: assistants up to **Normal**, social workers up to **Elevated**, and leads and application managers up to **High**. Above the role boundary the value remains `[Eingeschränkt]`; without an authenticated user, an encrypted value stays fail-closed as `●●●●●`. File markers always appear as `[Anhang]`, never as a technical ID. These fixed history placeholders are currently displayed in German in both interface languages.

This display rule applies **retroactively to the entire existing history**. Authorized roles can therefore also read earlier values that were later rectified or removed. Before implementation, this access was explicitly approved for the same roles that can already see those values in the detail/edit views and in the Art. 15 access record (#1961); take it into account when assigning roles, maintaining the data-protection impact assessment, and handling access requests. A redacted deletion-history entry contains no old value and therefore only shows `– (gelöscht)`.

#### Scope of the Access Record (Art. 15)

The access record is generated from the client view as **JSON** (machine-readable, Art. 20) or as **PDF** (human-readable document). Both formats cover the same scope; the PDF summarizes but omits no category. Only roles with export permission (lead, application manager) may trigger it; the request requires re-authentication (sudo mode), is rate-limited, and is itself recorded in the audit log.

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
| Deletion requests | Requests targeting events **and the client record itself**, with reason and four-eyes review |
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

**On the structure of the deleted cases (JSON):** they live under the top-level key `deleted_cases` -- it is **always** present, possibly as an empty list; since #1818, `cases` lists only the **live** cases. Both lists carry `is_deleted`, `deleted_at`, `deleted_by` and `anonymized_at` per entry. The last value answers what `is_deleted` alone leaves open: if it is set, the case contents have been **removed irreversibly** (the row deliberately stays, § 7.5); if it is empty, the case merely sits in the trash and can be restored. Episodes and outcome goals of a deleted case stay in **one** list (`episodes`, `outcome_goals`) -- their case title maps them to either section. **In the PDF**, cases in the trash form their own section "Fälle im Papierkorb" directly after "Fälle".

**On the structure of the deleted events (JSON):** they live under the top-level key `deleted_events` -- it is **always** present, possibly as an empty list; `events` lists only the **live** events. Both lists carry `is_deleted`, `deleted_at` and `deleted_by` per entry. The event reference inside the change history deliberately stays narrow (document type, timestamp, trash flag) -- it only maps the change to its event; the deletion data lives in the `deleted_events` section itself. There is deliberately **no** `anonymized_at` here: unlike a case, an event's contents are already removed at deletion time, so there is no second period until the purge -- what remains is in the change history. **Events deleted before version 0.96 carry `null` in both fields**: the data was not recorded back then and cannot be reconstructed retroactively (least of all who deleted the event) -- the access record states the gap instead of inventing a value. If `deleted_at` is set and `deleted_by` is empty, the retention run deleted the event (period expired, no acting user). **In the PDF**, the section „Gelöschte Ereignisse (Papierkorb)" carries the same two columns „Gelöscht am" and „Gelöscht von" as the section „Fälle im Papierkorb"; instead of an empty cell it shows „Aufbewahrungslauf" (system deletion) or „vor 0.96 nicht erfasst" (pre-existing records), so the data subject can tell the three cases apart. These fixed placeholders are currently displayed in German in both interface languages.

**On the structure of the merges (JSON):** they live under the top-level key `merged_from` -- it is **always** present, possibly as an empty list. Per merged record: `pseudonym`, `merged_at` and `anonymized`. The pseudonym is read from the **live** trash row: as long as the dissolved record sits unredacted in the trash, it appears in clear text; after the retention run purges it, only the redacted marker remains (`anonymized: true`) -- the access record thus carries the clear-text pseudonym exactly as long as the trash itself. Conversely, the access record of the dissolved record carries the field `merged_into` in its `client` block (present on tombstones only; "deleted at" is then also the merge timestamp), and a merge request in `deletion_requests` is marked with `merge_request: true`. **In the PDF**, the merges form their own section "Zusammenführungen" at the end, and the tombstone reference appears as the master-data row "Zusammengeführt in". **Important for Art. 15 requests covering the full history:** the access log of the dissolved record stays with its own dataset (append-only) and does **not** flow into the target's export -- answer such a request with **both** exports (target record and tombstone; the latter stays exportable via the trash or the record URL, even after redaction).

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
- Enforce a password change wherever a password is known **outside** the application: for the fallback without an email address, account creation sets `must_change_password` itself; for a password you assign by hand afterwards, tick the **Passwort muss geändert werden** ("Password must be changed") field in the user edit form. For accounts **with** an email address the invitation flow replaces this measure -- the user picks their own password there, and the completed setup step clears the flag again (§ 2.4, #1969).
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

In addition to ORM-side facility scoping, **PostgreSQL row-level security** is enabled on **all facility-scoped tables** (enforced derivation-based via a test) as **defense-in-depth**. A buggy ORM query that forgets facility scoping still returns no foreign data, thanks to RLS. Refs #542, #586.

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

# Blocking (if there is no UNIQUE index)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py refresh_statistics_view --no-concurrent
```

Implementation: [`src/core/management/commands/refresh_statistics_view.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/refresh_statistics_view.py).

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only to deployments **without** that compose file (k8s, custom orchestration). Setting up both makes the job run twice — details: [ops runbook § 3](../ops-runbook.md#3-cron-jobs).

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

> **Important: snapshot protection does not cover every report.** It preserves the **monthly aggregates** that feed the statistics overview (key figures, tables and timeline), its PDF export and the activity-report PDFs. Of the reports on the **"Dataset reports"** tab, only the **example activity report** is snapshot-enabled, and even there only its **PDF**: its CSV/JSON/XML files -- like every other report on that tab -- recalculate from the current data on **every** request. Once `enforce_retention` has cleaned up a past period, their figures for that period will be lower than on the first download -- potentially zero, without an error message, because that is not a defect. The extent depends on the report: it evaluates events, cases or people and changes only when records in **its** population are deleted or anonymized; the population is stated above each preview. **If you give such a file to a funding body or other recipient, retain the generated file yourself** -- it cannot be reproduced identically later. Each report states where it reads from ("Data source: …"); the other considerations for external release are explained below under ["Releasing Reports"](#releasing-reports-what-you-need-to-know).

### Automatic Capture

Snapshots are created automatically whenever `enforce_retention` deletes events -- the affected months are captured right before the deletion.

### Periodic Cron Job (Recommended)

It is also recommended to create snapshots regularly via cron:

> **Already automated (Refs #794):** The bundled `cron` sidecar in `docker-compose.prod.yml` runs this job out of the box. The crontab example below applies only to deployments **without** that compose file (k8s, custom orchestration). Setting up both makes the job run twice — details: [ops runbook § 3](../ops-runbook.md#3-cron-jobs).

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
<!-- translation-version: v0.99.1 -->
<!-- source-hash: bb2c1b55e64e72c8831cbf9c3ad5d494dd9f8d8d -->
