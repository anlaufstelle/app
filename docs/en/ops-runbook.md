> This is the English translation of [ops-runbook.md](../ops-runbook.md).
> Most recently synchronized on 2026-09-10 (§ 6.6 the production restore drill checks `TMPDIR` capacity, verifies and probe-extracts the matching media archive in one pass, and only marks success after step 6 while the sentinel drill remains a separate volume-persistence check, #2195; §§ 3.1/5.5 open CSV import source files count during quota enforcement while the usage display remains attachment-based, #2180; the alternative deployment path reworded to "k8s"/"custom orchestration", #2202; pre-1.0 migration/historical framing removed -- the pre-0.23 host-crontab migration subsection, the database-role version-dated markers, and the pre-1.0-installation framing in the role-split recovery section (§ 9.6) are now described as current state or as a misconfiguration-recovery path, #2205).
> Previously synchronized on 2026-09-07 (§ 3.5 bundled package selection and replacement-not-addition guard, #2134).
> The German version is the authoritative source.

# Operations Runbook

Operations manual for Anlaufstelle. Complements the [Admin Guide](admin-guide.md) with operational procedures for deployment, rollback, cron jobs, log analysis, and troubleshooting.

**Stack:** Django 6.0 / Gunicorn / PostgreSQL 18 / Caddy 2 / Docker Compose

---

## Table of Contents

1. [Deployment Procedure](#1-deployment-procedure)
2. [Rollback Procedure](#2-rollback-procedure)
3. [Cron Jobs](#3-cron-jobs)
4. [Log Analysis](#4-log-analysis)
5. [Troubleshooting](#5-troubleshooting)
6. [Emergency Procedures](#6-emergency-procedures)
7. [ClamAV Virus Scanning](#7-clamav-virus-scanning)
8. [Updating Dependencies](#8-updating-dependencies)
9. [Row Level Security (RLS)](#9-row-level-security-rls)
10. [Invite Token Hygiene](#10-invite-token-hygiene)
11. [Statistics Materialized View](#11-statistics-materialized-view)
12. [Trust Boundary](#12-trust-boundary-refs-841)
13. [PostgreSQL Major Upgrade (16 → 18)](#13-postgresql-major-upgrade-16--18)

---

## 1. Deployment Procedure

### 1.1 Pre-Deploy Checks

```bash
# Create a current backup
./scripts/ops/backup.sh

# Verify the backup file (not empty)
ls -lh backups/daily/

# Check the changelog for breaking changes
# Read the release notes (new env vars, changed migrations)

# Health check of the running system
curl -sf https://$DOMAIN/health/ | python3 -m json.tool

# Note the current image tag (for rollback)
docker compose -f docker-compose.prod.yml images web

# Check the mandatory env vars of the 3-role PostgreSQL model (Refs #902, ADR-005/017):
# The bootstrap superuser ``postgres`` uses them to create the app role
# (NOSUPERUSER, NOBYPASSRLS) and the admin role (NOSUPERUSER, BYPASSRLS)
# (deploy/postgres-init/01-app-role.sh). If they are missing or still set to
# change-me-*, DB init or the migrate job fails.
# Source: .env.example.
# The staging stack runs the same check against .env.staging instead of .env
# (ENV_FILE=.env.staging, template .env.staging.example) — Refs #2086.
ENV_FILE="${ENV_FILE:-.env}"
for var in POSTGRES_BOOTSTRAP_PASSWORD POSTGRES_ADMIN_USER POSTGRES_ADMIN_PASSWORD; do
    val=$(grep -E "^${var}=" "$ENV_FILE" | cut -d= -f2-)
    case "$val" in
        ""|change-me*) echo "✗ $var missing or still at its default"; ;;
        *)             echo "✓ $var set"; ;;
    esac
done
```

### 1.2 Deploy Steps

Refs #802 (C-34): Migrations run as a separate one-shot job **before** the rolling restart. The web entrypoint now only does `collectstatic` + `gunicorn` — no more migrations.

> **Initial setup instead of a deploy?** This section describes the *ongoing* deploy. For the **first** installation there is [`scripts/initial-setup.sh`](../../scripts/initial-setup.sh) (Refs #793): start the DB and wait for readiness → migrate one-shot → bring up the stack → `check --deploy` → `check_db_roles` → super-admin bootstrap. Idempotent and fail-loud; instructions in [docs/admin-guide.md § 1 Step 4](admin-guide.md).

```bash
# 1. Pull the new image
docker compose -f docker-compose.prod.yml pull web

# 2. Run migrations as a one-shot job (an advisory lock holds back
#    parallel jobs; a long-running RunPython does not block any worker).
docker compose -f docker-compose.prod.yml run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 3. Restart the stack (web + caddy, DB stays up).
#    The entrypoint now only runs collectstatic + gunicorn.
docker compose -f docker-compose.prod.yml up -d web caddy
```

**Expected downtime:** ~3-5 seconds (only the Gunicorn restart). Migrations run beforehand in the one-shot job — the web container only comes up once the schema is ready. With several web replicas, none of them wait on the migrate lock anymore.

> **Ownership normalization (Refs #1085, #1026):** The one-shot job still runs `normalize_db_ownership` after `migrate`. Since A1.4, the **DB owner is the admin role** itself; since `migrate` runs as that very role, `REASSIGN OWNED BY current_user TO db_owner` is a **no-op** (`current_user == db_owner`). The app runtime no longer gets its table rights via ownership, but via a DML grant (`ALTER DEFAULT PRIVILEGES … TO app` in the init script, § 9). The step remains as an idempotent safety net; a failure aborts the deploy fail-fast before new web replicas go live.

### 1.3 Post-Deploy Verification

```bash
# Container status
docker compose -f docker-compose.prod.yml ps

# Check the migration log
docker compose -f docker-compose.prod.yml logs web --tail=50

# Health check
curl -sf https://$DOMAIN/health/
# Expected response: {"status": "ok", "database": "connected", "version": "..."}

# Verify migration status
docker compose -f docker-compose.prod.yml exec web \
  python manage.py showmigrations | grep '\[ \]'
# No pending migrations → empty output

# Django deployment check
docker compose -f docker-compose.prod.yml exec web \
  python manage.py check --deploy
```

### 1.4 Compose Stack Reconciliation

Refs #2086. All four deployment stacks run the same three-role PostgreSQL model (canonical description: [§ 9 "DB Role Model (Gold Standard)"](#db-role-model-gold-standard)). The table is target state **and** actual state — if one stack drifts, its behaviour diverges silently and no test reports it:

| Stack | `deploy/postgres-init` mounted | `db` bootstraps as | `web` connects as | `maintenance` | `cron` | Env template |
|---|---|---|---|---|---|---|
| `docker-compose.prod.yml` | yes | `postgres` (bootstrap superuser) | app role `${POSTGRES_USER}` (allowlist) | yes | yes | `.env.example` |
| `docker-compose.dev.yml` | yes | `postgres` | app role (allowlist) | yes | no (systemd timers, § 3.4) | `.env.dev.example` |
| `docker-compose.demo.yml` | yes | `postgres` | app role (allowlist) | yes | no (hourly reset) | `.env.demo.example` |
| `docker-compose.staging.yml` | yes (since #2086) | `postgres` (since #2086) | app role (allowlist, since #2086) | yes (since #2086) | yes (since #2086) | `.env.staging.example` (since #2086) |

Invariants that hold for **every** stack:

- The `db` service loads **no** `env_file`; it sets its `environment:` block via Compose interpolation — otherwise `POSTGRES_USER` would be the app role and therefore the bootstrap superuser.
- Every stack sets `REQUIRE_ADMIN_DB_ROLE: "true"`, which rules out the path without a separate admin role (where the app role becomes owner itself).
- The app role is nowhere an owner and nowhere `SUPERUSER`/`BYPASSRLS`.
- `web` loads **no** `env_file` but an explicit runtime allowlist (Refs #1962). Among the **application services**, only `maintenance` and `cron` see admin/bootstrap secrets; the `db` service necessarily receives them as well (`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `ADMIN_DB_PASSWORD`) — it is the service that creates the roles. It gets only those values and no application secrets (`DJANGO_SECRET_KEY`, `ENCRYPTION_KEY*`).
- On **staging**, the `web` healthcheck additionally verifies the **actual** connection role on every start and continuously afterwards (`SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user`). The plain HTTP check was not sufficient: on a bypassing role `/health/` reports `degraded` but still answers HTTP 200, and the `db_role_posture` detail field is only served in the token/session payload. Refs #2086.
- On **staging**, the **exposure** is bound to this too, not just the display: `caddy` depends on `web` with `depends_on: {condition: service_healthy, restart: true}` (Compose >= v2.17 — without `restart: true`, Compose would leave an already running Caddy routing after a `web` update), and `Caddyfile.staging` **actively** probes the upstream for `db_role_posture`, sending `X-Health-Token` for it. `DJANGO_HEALTH_DETAIL_TOKEN` is therefore mandatory on this stack — and more strictly than "otherwise 502": the `{$…}` placeholder is substituted while **adapting** the Caddyfile, so without a value the adaptation aborts and **`caddy` does not start at all** (verified against `caddy:2-alpine` v2.11.4). The diagnosis then lives in `docker compose logs caddy`. **Operational rule:** after any change to `web`, act on the whole stack (`docker compose ... up -d`), not just `up -d web` — otherwise Caddy is not restarted and the gate does not engage.

Until #2086, `docker-compose.staging.yml` was the sole exception: no init mount, `env_file` on `db` **and** `web`. The web process connected as the bootstrap superuser there, `FORCE ROW LEVEL SECURITY` was ineffective, and staging validated precisely the failure class it exists for the least. The guard `TestStagingStackRoleSeparation` in `src/tests/test_architecture_guards_ops.py` pins the new state.

---

## 2. Rollback Procedure

### 2.1 Reset the Image (without DB Rollback)

If the new version has application bugs but the migrations are backward-compatible:

```bash
# 1. Pin the image tag in docker-compose.prod.yml
#    image: ghcr.io/anlaufstelle/app:v1.2.3   (previous version)

# 2. Restart the stack
docker compose -f docker-compose.prod.yml up -d web

# 3. Verify
curl -sf https://$DOMAIN/health/
```

### 2.2 Roll Back a Migration

If a migration has changed data or schema in an incompatible way:

```bash
# 1. Look at the migration status
docker compose -f docker-compose.prod.yml exec web \
  python manage.py showmigrations core

# 2. Roll back to a specific migration
#    Example: back to migration 0042
docker compose -f docker-compose.prod.yml exec web \
  python manage.py migrate core 0042

# 3. Then deploy the old image (see 2.1)
```

**Important:** Not all migrations are backward-compatible. `RunPython` operations without `reverse_code` cannot be rolled back. Always check the changelog for irreversible migrations before an update.

#### Special Case: Attachment Chain `0129`–`0131` (Owner-Generic File Attachments) + Hardening `0133`

Milestone 24 (#1738,
[ADR-039](../adr/039-owner-generische-dateianhaenge.md) (German)) restructures the attachment table.
Three migrations belong together and must not be split apart — **`0130` and `0131` must never be
shipped in separate releases**; `0133` adds the composite-FK hardening
afterwards and is independently reversible:

| Migration | What it does |
|---|---|
| `0129_rename_eventattachment_to_attachment` | A plain `RenameModel` (`core_eventattachment` → `core_attachment`). Policies, RLS flags, and GRANTs are attached to the table OID and **survive** the rename — nothing touches the policy here. |
| `0130_attachment_owner_facility` | New column `facility_id` (nullable → backfilled from `event.facility` under the **admin/owner role**, BYPASSRLS → NOT NULL), `event_id` becomes nullable, `client_id`/`case_id` are added, a `CheckConstraint` `attachment_exactly_one_owner`, an index. **Reverse guard as the last operation** (so first in reverse). |
| `0131_rls_attachment` | Policy swap on `core_attachment`: a **direct** facility clause instead of a join over `core_event`, including the `is_super_admin` branch. The reverse restores the join policy and deliberately leaves RLS **active**; it carries its own rollback guard. |
| `0133_attachment_composite_fk` | Follow-up hardening (#1780): companion UNIQUE indexes `(id, facility_id)` on `core_event`/`core_client`/`core_case`, plus a composite FK each on `core_attachment (owner_fk, facility_id)`. **Can fail on existing data** — a forward preflight reports divergent rows (owner in facility A, row with `facility_id` from B) per owner kind, with counts, before the `ALTER TABLE` runs. Align such rows before migrating again, or remove them together with their `.enc` file. The reverse is complete and carries **no** rollback guard (it only undoes the hardening). |

> ⚠️ **Rollback restriction.** `migrate core 0128` (or the single step
> `migrate core 0130`) is **only allowed as long as no person- or case-owned
> attachments exist**. Both rollback guards otherwise abort with a
> `RuntimeError` — deliberately: under the restored join policy, those rows
> would be policy-hidden (invisible but present). With the first
> person-/case-owned attachment, the chain's rollback is therefore final.
> The only option left is a full DB restore (§ 2.3) — a **backup before the
> migration is mandatory** (DDL, no file moves, no media downtime window).

**Post-deploy checks** (as the admin role in `psql`, see § 9.2):

```sql
-- 1. The policy sits directly on the facility column (no longer a join over
--    core_event) AND carries the is_super_admin bypass branch:
SELECT polname, pg_get_expr(polqual, polrelid) AS using_clause
FROM pg_policy
WHERE polrelid = 'core_attachment'::regclass;
-- expected: current_setting('app.current_facility_id', true) against facility_id
--           plus an OR branch on current_setting('app.is_super_admin', true)
--           NO "EXISTS (SELECT ... FROM core_event ...)"

-- 2. Backfill complete (must return 0):
SELECT count(*) FROM core_attachment WHERE facility_id IS NULL;
```

If check 2 returns more than `0`, or check 1 still shows the join clause, the
chain has **not** run to completion — do not deploy further, clarify the
state first.

### 2.3 Full Rollback (DB Restore)

Last resort when a migration rollback is not possible:

```bash
# 1. Stop the stack
docker compose -f docker-compose.prod.yml down

# 2. Restore the backup — DB plus optionally media
./scripts/ops/restore.sh \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS.sql.gz.enc \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS_media.tar.gz.enc

# 3. Set the old image tag in docker-compose.prod.yml

# 4. Start the stack
docker compose -f docker-compose.prod.yml up -d

# 5. Verify
curl -sf https://$DOMAIN/health/
```

**Note on media (Refs #720):** The `media:` volume in
[`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml)
persists `MEDIA_ROOT` across container recreates. On a DB rollback,
media (`*_media.tar.gz.enc`) may also need to be restored, if the backup
reflects a different state than the current `media:` volume. On a pure
image rollback without a DB change, `media:` typically remains
valid — then no media restore is needed.

---

## 3. Cron Jobs

> This chapter is the **authoritative list of background jobs**; the [Admin Guide](admin-guide.md) points here.

> **⚠️ The backup still does NOT run automatically.** The sidecar covers every job
> that runs as a `manage.py` command inside the container. `backup.sh`, by contrast, runs
> `docker compose exec` (pg_dump in the db container, tar in the web container) and
> would need the Docker socket inside the sidecar for that — which would be root-equivalent on the
> host and would undo the runtime hardening of the entire stack (`cap_drop: ALL`,
> `no-new-privileges`, `read_only`). The backup therefore stays **one**
> host crontab entry, see [§3.3](#33-host-cron-backup--remaining-host-jobs).
> Without it, an installation has **no backup** — with no error message, just silence;
> `initial-setup.sh` warns when it cannot find such an entry.
> The compliance dashboard shows the run status per job
> ([`/system/compliance/`](admin-guide.md), category "Background jobs", super admins
> only) — the [`/health/` endpoint](../monitoring-guide.md) (German) also aggregates it roughly
> (`stale_jobs`, `last_backup_age_hours`).

### 3.1 Overview

Column **Where**: `Sidecar` = runs out of the box in the `cron` container ([§3.2](#32-cron-sidecar-standard)),
`Host` = must be set up by the operator in the host cron ([§3.3](#33-host-cron-backup--remaining-host-jobs)).

| Job | Recommended time | Where | Purpose |
|-----|----------------|----|-------|
| `backup.sh` | Daily 02:00 | **Host** | Encrypted DB and media backup with rotation; optional off-site sync (Refs #720, #738) |
| `enforce_retention` | Daily 03:00 | Sidecar | Soft-delete expired events, anonymize clients |
| `verify_audit_chain` | Daily 03:30 (after retention) | Sidecar | Verify the AuditLog's HMAC integrity chain; exit code != 0 on suspected tampering (Refs #1070). A fully green run writes the last-run marker `AUDIT_CHAIN_VERIFIED` — visible as the "Audit chain check" row under `/system/` and in the compliance dashboard (Refs #1872). A tamper finding writes **no** marker, so the indicator runs overdue |
| `create_statistics_snapshots` | Monthly, day 1, 04:00 | Sidecar | Save monthly aggregates before events are deleted |
| `cleanup_orphan_storage_files` | Daily 04:30 (after snapshots) | Sidecar | Delete orphaned `.enc` files with no `Attachment` **and no open `ImportJob` record** (owner-independent: event, person, and case attachments; CSV import uploads under `<facility>/imports/` are protected via `ImportJob.storage_filename` and count during quota enforcement while the run is open — `commit_import`/`abort_import` clear the field, finished runs do not protect it any longer); MUST run as the admin DB role (BYPASSRLS), otherwise fail-loud — without the bypass the run would also see 0 `ImportJob` rows and would delete the open uploads of every facility (Refs #662, #1554, #1738, #1060, #2180) |
| `clearsessions` | Daily 05:00 | Sidecar | Delete expired rows from `django_session` (storage limitation under Art. 5(1)(e) GDPR) |
| `detect_breaches` | Hourly at:30 | Sidecar | Heuristics-based breach detection (failed-login burst / mass export / mass delete) → AuditLog SECURITY_VIOLATION + optional webhook (Refs #685) |
| `refresh_statistics_view` | Hourly at:15 | Sidecar | Refresh the materialized view `core_statistics_event_flat` (statistics dashboard) |
| Invite token audit | Weekly, Sun 05:00 | Host | Track down orphaned invite user accounts (see [10](#10-invite-token-hygiene)); a plain `psql` query — the app container has no psql client |
| Health check | Every 5 minutes | Host | Check availability (external monitoring, does not belong inside the monitored stack) |

### 3.2 Cron Sidecar (Standard)

The `cron` service in
[`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml)
runs the same app image as `web`, but starts the cron daemon **supercronic**
inside it instead of Gunicorn, and works through the job definitions from
[`deploy/crontab`](https://github.com/anlaufstelle/app/blob/main/deploy/crontab)
(mounted read-only into the container). After `docker compose up -d`,
retention, audit chain verification, snapshots, orphan cleanup,
session cleanup, breach detection, and MV refresh run without any further setup.

**Why supercronic and not `cron`/`crond`:** classic cron daemons
daemonize themselves away (the container would have no foreground process), log to
syslog instead of stdout (no `docker compose logs`), and need setuid bits that
collide with `no-new-privileges` + `cap_drop: ALL`. supercronic is a
statically linked Go binary that addresses exactly these points. It gets
pinned by version and sha256 into the image
([`deploy/fetch_supercronic.py`](https://github.com/anlaufstelle/app/blob/main/deploy/fetch_supercronic.py),
verified in the Docker builder).

**DB role:** All jobs in the sidecar run as the **admin role** (BYPASSRLS) — the
service sets `POSTGRES_USER=${POSTGRES_ADMIN_USER}` for this. Outside of an
HTTP request, no `app.current_facility_id` GUC is set; as an
RLS-filtered app role, `enforce_retention`, `verify_audit_chain`, and
`cleanup_orphan_storage_files` would abort fail-loud (§9 DB role model). This is why
`.env` must have `POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD` set. Django's
facility-independent `clearsessions` command does not require the RLS bypass,
but runs under the same service role.

**Time zone:** The service sets `TZ` (default `Europe/Berlin`, matching
`settings.TIME_ZONE`). Without `TZ`, the container would run in UTC and "03:00" would
be 04:00/05:00 local time depending on the season — the promised order
(host backup 02:00 **before** retention 03:00) would then no longer be guaranteed.
For a different time zone: `TZ=...` in `.env`.

```bash
# Is the sidecar running?
docker compose -f docker-compose.prod.yml ps cron

# What did it do most recently? (supercronic logs every start/exit to stdout)
docker compose -f docker-compose.prod.yml logs --tail=100 cron

# Trigger a job immediately, outside its schedule (same role as in the sidecar):
docker compose -f docker-compose.prod.yml exec cron python manage.py refresh_statistics_view
```

**Monitoring:** supercronic has no health endpoint — a running
container does not prove that the jobs *work*. Instrumented application
commands write a `*_RUN_COMPLETED` marker to the AuditLog after a successful
run, which the compliance dashboard (`/system/compliance/`, category
"Background jobs") and `stale_jobs` in the [`/health/`
payload](../monitoring-guide.md) (German) evaluate. Django's standard
`clearsessions` command writes no such marker; only its start and exit code
appear in the supercronic log. Host monitoring should therefore alert on
failures in the `cron` logs — in particular on `verify_audit_chain`, which ends
with exit code != 0 on suspected tampering.

**Changes to the schedule** belong in `deploy/crontab` (repo, versioned);
afterwards `docker compose -f docker-compose.prod.yml restart cron`. The guard
`src/tests/test_ops_cron_sidecar.py` keeps the file in sync with the table in §3.1.

### 3.3 Host Cron: Backup + Remaining Host Jobs

Two jobs stay host-side because they cannot run in the sidecar:

- **`backup.sh`** — needs `docker compose exec` (pg_dump in the db container, tar
  in the web container), and therefore the Docker socket. A socket mount in the sidecar
  would be root-equivalent on the host.
- **Invite token audit / health check** — a `psql` query and an external
  `curl` check, respectively; the app container doesn't carry the former, and the latter should
  deliberately run *outside* the monitored stack.

**Backup paths:** `BACKUP_HOST_DIR` is the base directory on the host
(default: `<repo>/backups`; relative values such as `./backups` are
resolved against the repository root). `scripts/ops/backup.sh` writes to
its `daily/`, `weekly/`, and `monthly/` subdirectories; the prod
branch of `restore-drill.sh` reads from the same `daily/` directory.
`BACKUP_DIR` is exclusively the read-only mount target inside the
`web` container (default: `/backups`) and is deliberately ignored by
both host scripts. Compose mounts `BACKUP_HOST_DIR` at `BACKUP_DIR` so
that `/health/` sees the same files.

```cron
# Anlaufstelle – Host cron (backup + hygiene). Everything else is handled by the
# cron sidecar from §3.2 — do NOT set these entries up twice!
# Set up in /etc/crontab or via `crontab -e` on the host

# Backup (daily 02:00, before retention in the sidecar at 03:00)
0 2 * * * cd /opt/anlaufstelle && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1

# Invite token audit (weekly Sun 05:00, details see section 10)
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health check (every 5 minutes)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

The host cron entries above run in the **host's system time zone** (not in the
sidecar `TZ` variable from [§3.2](#32-cron-sidecar-standard)). If it differs
from `Europe/Berlin` (for example, on UTC hosts), `0 2 * * *` lands at 03:00 or
04:00 local time depending on the season, so the promised "backup before
retention (03:00)" ordering flips or degenerates into a tie. Set the host time
zone to `Europe/Berlin` or move the host cron time earlier accordingly.

<details>
<summary>Complete host crontab — alternative for setups WITHOUT the sidecar</summary>

Relevant if the stack is run without `docker-compose.prod.yml` (e.g.
k8s, custom orchestration) and therefore no `cron` service exists.

```cron
# Anlaufstelle – Operational cron jobs (alternative: without the cron sidecar)
# Set up in /etc/crontab or via `crontab -e` on the host

# Backup (daily 02:00, before retention)
0 2 * * * cd /opt/anlaufstelle && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1

# Retention enforcement (daily 03:00)
# MUST run with the admin DB role (BYPASSRLS), otherwise the command aborts
# fail-loud (§9 DB role model) instead of falsely reporting 0 processed rows
# as RETENTION_RUN_COMPLETED.
0 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1

# Audit chain verification (daily 03:30, after retention; Refs #1070)
# MUST run with the admin DB role (BYPASSRLS), otherwise the command aborts
# fail-loud (§9 DB role model) instead of falsely reporting "intact".
30 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain >> /var/log/anlaufstelle-audit-verify.log 2>&1

# Statistics snapshots (monthly on the 1st at 04:00)
0 4 1 * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance create_statistics_snapshots >> /var/log/anlaufstelle-snapshots.log 2>&1

# Orphan storage cleanup (daily 04:30, after the snapshots; remove orphaned
# .enc files with no Attachment record — owner-independent, i.e.
# event, person, and case attachments, Refs #662/#1554/#1738)
# MUST run with the admin DB role (BYPASSRLS), otherwise the command aborts
# fail-loud (§9 DB role model): as an RLS-filtered app role, the run would see
# 0 registered attachments and would delete EVERY .enc file as an orphan.
30 4 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance cleanup_orphan_storage_files >> /var/log/anlaufstelle-orphan-cleanup.log 2>&1

# Remove expired Django sessions (daily 05:00)
0 5 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance clearsessions >> /var/log/anlaufstelle-clearsessions.log 2>&1

# Breach detection scan (hourly at minute 30, Refs #685)
30 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance detect_breaches >> /var/log/anlaufstelle-breach.log 2>&1

# Materialized view refresh (hourly at minute 15, details see section 11)
15 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance refresh_statistics_view >> /var/log/anlaufstelle-mv.log 2>&1

# Invite token audit (weekly Sun 05:00, details see section 10)
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health check (every 5 minutes)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

```

</details>

**Note the order:** Backup (02:00) → retention (03:00) → audit chain verification (03:30) → snapshots (04:00, monthly) → orphan cleanup (04:30) → session cleanup (05:00) → breach detection/MV refresh (hourly). The backup must run before retention, so that deleted data is included in the backup. The audit chain verification runs after retention, so that the checkpoint boundaries freshly written by `prune_auditlog` are already in the chain. The MV refresh uses `CONCURRENTLY` and does not block running readers. The sidecar schedule in `deploy/crontab` reflects exactly this order — which is why the `TZ` setting from [§3.2](#32-cron-sidecar-standard) is operationally relevant, not cosmetic.

### 3.4 Dev (systemd Timers)

On `dev.anlaufstelle.app`, the jobs do **not** run via a host crontab, but as
systemd timers, installed by `dev-ops/deploy/install-timers.sh` (dev-only) —
invoked on **every** deploy by `dev-ops/deploy/deploy-dev.sh` (dev-only)
(idempotent). The dev stack runs via `docker-compose.dev.yml` and deliberately
does **not** get the `cron` sidecar from [§3.2](#32-cron-sidecar-standard):
running both in parallel would execute every job twice, and here the timers are
additionally the test case for the host scheduler path that k8s setups
or setups with custom orchestration without our compose file use.

> **Note (Refs #980):** Previously, `bootstrap.sh` installed the timers inline. Since `bootstrap.sh` only runs during initial provisioning, timers added later never made it onto the running host (the backup cron stayed absent). The installation now lives in `install-timers.sh` and runs on every `deploy-dev.sh`.

| Timer | OnCalendar | Command |
|-------|-----------|---------|
| `anlaufstelle-backup.timer` | `*-*-* 02:00` | `dev-ops/deploy/backup.sh` |
| `anlaufstelle-retention.timer` | `*-*-* 03:00` | `… exec -T web python manage.py enforce_retention` |
| `anlaufstelle-audit-verify.timer` | `*-*-* 03:30` | `… exec -T web python manage.py verify_audit_chain` |
| `anlaufstelle-snapshots.timer` | `*-*-01 04:00` | `… exec -T web python manage.py create_statistics_snapshots` |
| `anlaufstelle-orphan-cleanup.timer` | `*-*-* 04:30` | `… exec -T web python manage.py cleanup_orphan_storage_files` |
| `anlaufstelle-breach.timer` | `*-*-* *:30` | `… exec -T web python manage.py detect_breaches` |
| `anlaufstelle-mv-refresh.timer` | `*-*-* *:15` | `… exec -T web python manage.py refresh_statistics_view` |

**Activation on already-running servers:** A regular `deploy-dev.sh` run
installs the timers along with it. A one-off, immediate catch-up also works directly:

```bash
sudo bash /opt/anlaufstelle/dev-ops/deploy/install-timers.sh # idempotent, as root
systemctl list-timers "anlaufstelle-*" # 7 timers with a NEXT time
systemctl start anlaufstelle-mv-refresh.service # trigger once manually
journalctl -u anlaufstelle-mv-refresh.service -n 20
```

The compliance dashboard (`/system/compliance/`, category "Background jobs") additionally
shows the run status per job — `unknown`/`warning`/`critical` if a timer is not running.

> **Note:** `install-timers.sh` installs these 7 timers. The maintenance timers run via `run-as-admin.sh` with the admin DB role (BYPASSRLS) — including `cleanup_orphan_storage_files` (Refs #662, #1554), which otherwise aborts fail-loud (§9 DB role model).

### 3.5 Manual Execution

```bash
# Retention dry run (no deletion)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention --dry-run

# Run retention
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention

# Only one facility
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention --facility "Beratungsstelle Nord"

# Snapshots dry run
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_statistics_snapshots --dry-run

# Catch up snapshots for all existing months (initial setup)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_statistics_snapshots --backfill

# Snapshot for a specific month
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_statistics_snapshots --year 2026 --month 3

# Partial failures (Refs #2118): an error in one facility or one month no
# longer aborts the run. Every affected facility/month appears as an error
# line in the log (host cron: /var/log/anlaufstelle-snapshots.log; sidecar:
# `docker compose logs cron` resp. the application log) and on stderr. The
# run itself then ends with exit code != 0 plus a summary — a cron run that
# ends red therefore means "at least one snapshot is missing", not
# "everything failed". After fixing the cause, catch up the affected month
# with --year/--month (example above).

# Clean up orphaned dynamic field values (Refs #1725)
# Removes data_json keys whose slug is no longer attached to the document type,
# for persons/cases/events. Assigned (including encrypted) values
# are left untouched; records without a type are only counted. MUST run with the
# admin DB role (BYPASSRLS) — otherwise fail-loud (§ 9 DB role model).
#
# Orphaned FILE markers are deliberately SKIPPED for ALL three entities
# and reported separately as "file orphan(s) skipped", NOT deleted: the
# data_json marker is the only bridge to the encrypted attachment
# row. (Until M24 the rule effectively only affected events — file fields were
# locked out of person/case types; since Refs #1738, persons and
# cases can also carry FILE orphans and are spared in the same way.)
# This file cleanup runs via the attachment deletion paths, or
# cleanup_orphan_storage_files (after the attachment row is deleted), not
# via this command — the skipped count therefore does not count as
# completed file minimization.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  purge_orphan_field_data --dry-run # preview
docker compose -f docker-compose.prod.yml run --rm maintenance \
  purge_orphan_field_data # execute

# Facility export (Refs #1060, M4 / GDPR Art. 20): a facility's complete domain
# data as a versioned JSON (schema v1, reference
# docs/facility-export-schema.md). MUST run with the admin DB role
# (BYPASSRLS) — otherwise fail-loud instead of silently empty lists.
#
# DATA-PROTECTION WARNING: the output file contains DECRYPTED plaintext
# values (including Art.-9 content) — treat it like a DB backup: only
# write it to encrypted destinations, hand it over only to authorized people, then
# delete it securely. Runtime scales with the number of attachments (SHA-256 is
# streamed over the decrypted contents); --no-file-hashes
# skips the hashing (sha256: null).
mkdir -p./exports && chmod 700./exports
docker compose -f docker-compose.prod.yml run --rm \
  -v "$PWD/exports:/exports" maintenance export_facility \
  --facility "Beratungsstelle Nord" --output /exports/facility-export.json --indent 2

# Inventory of report bindings (Refs #1848): for ONE facility, lists
# which report key sits in which report backend (type and
# field level) and which document types and field templates carry it
# or which carry none and therefore appear in no report. Pure
# information, does not change any data. MUST run with the admin DB role
# (BYPASSRLS) — otherwise fail-loud (§ 9 DB role model): under the app role
# without a facility context, an empty/filtered inventory would look like a
# flawlessly configured facility.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  inventory_report_bindings \
  --facility "Beratungsstelle Nord" # text report to stdout
docker compose -f docker-compose.prod.yml run --rm \
  -v "$PWD/exports:/exports" maintenance inventory_report_bindings \
  --facility "Beratungsstelle Nord" --format json --output /exports/report-bindings.json

# Initial facility setup (Refs #1883, #2134): interactively create the
# organization, facility, settings, selected catalog, and first facility_admin.
# MUST run with the admin DB role (BYPASSRLS) — the command writes to
# core_settings and (with the default catalog) to core_documenttype/-fieldtemplate/
# -documenttypefield/-fieldgroup/-fieldcondition, all FORCE-RLS tables
# with a USING-only policy that PostgreSQL also checks on INSERT. The
# guard triggers BEFORE the first prompt, so nobody types a
# password first and then fails at the write access. The whole run is ONE
# transaction: an abort leaves no half-created facility behind.
# With no option, --package default applies. A domain package is loaded as a
# REPLACEMENT against the fresh facility via --package <name>.
# --package and --no-default-package are mutually exclusive.
# --no-default-package leaves out the catalog (the facility starts without
# document types and can only be documented on after load_package).
# If the facility already carries document types or field templates, the
# catalog stays UNTOUCHED — initial setup, not a catalog update (that's what
# the next command is for).
docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility

# Create a new facility directly with a bundled domain package:
docker compose -f docker-compose.prod.yml run --rm maintenance \
  setup_facility --package awareness

# Apply the default catalog to an EXISTING facility (Refs #1883):
# functionally identical to "load_package --file <…>/default.yaml", just without
# the container path. Never deletes anything, but writes catalog properties of
# existing rows back to the package state (resync table in
# docs/packages.md) (German) — so review it first with --dry-run. Repeatable,
# same role prerequisite.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  load_package --default --facility <uuid> --dry-run

# A bundled domain package only goes into a catalog-free facility.
# Foreign type/field slugs abort even a dry run; repeating the same package
# remains allowed. The command's messages remain German.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  load_package --package awareness --facility <uuid> --dry-run
```

---

## 4. Log Analysis

### 4.1 Log Access

```bash
# All container logs (last 200 lines)
docker compose -f docker-compose.prod.yml logs --tail=200

# Follow web logs live
docker compose -f docker-compose.prod.yml logs -f web

# Caddy logs (reverse proxy / TLS)
docker compose -f docker-compose.prod.yml logs -f caddy

# Database logs
docker compose -f docker-compose.prod.yml logs -f db

# Logs since a specific point in time
docker compose -f docker-compose.prod.yml logs --since="2026-03-30T08:00:00" web
```

### 4.2 JSON Logging Format

With `LOG_FORMAT=json` in `.env`, Django emits structured JSON logs:

```json
{
  "ts": "2026-03-30T10:15:22.123456+00:00",
  "level": "INFO",
  "logger": "core",
  "module": "views",
  "message": "...",
  "request_id": "abc123",
  "user_id": 42,
  "facility_id": 1,
  "exception": "Traceback..."
}
```

| Field | Description |
|------|-------------|
| `ts` | ISO 8601 timestamp (UTC) |
| `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `logger` | Logger name (`django`, `core`, `gunicorn.error`, `security.csp`) |
| `module` | Python module |
| `request_id` | Request ID (if available) |
| `user_id` | Logged-in user (if available) |
| `facility_id` | Active facility (if available) |
| `exception` | Full traceback on errors |
| `csp_violation` | CSP violation details as an object (`blocked-uri`, `document-uri`, `violated-directive`, …), only on `security.csp` |
| `csp_violations_total` | Only on the `csp_violation_truncated` line: how many violations the report contained |
| `csp_violations_dropped` | Only on the `csp_violation_truncated` line: how many of those were **not** logged (cap: 10 per request) |
| `remote_addr` | Client IP of the report (proxy-aware via `TRUSTED_PROXY_HOPS`) |
| `user_agent` | User agent of the report (control characters stripped, capped at 512 characters) |

The last five fields come from the CSP report endpoint (`/csp-report/`,
logger `security.csp`) and, since #1943,
appear in **both** formats — including the default `LOG_FORMAT=text`, there as
`field="value"` pairs after the message. Before that, only the
bare `csp_violation` line was visible for each report. Two report types are
operationally relevant here:

- `csp_violation` — a single violation.
- `csp_violation_truncated` — a request contained more than 10 violations;
  the first 10 are logged, the rest dropped. If this line piles up,
  it's either a badly broken CSP or a flooding attempt: check the
  `remote_addr` against the rate limit (10 requests/min/IP).

**Filtering JSON logs (with jq):**

```bash
# Errors only
docker compose -f docker-compose.prod.yml logs web --no-log-prefix 2>&1 \
  | grep '^{' | jq 'select(.level == "ERROR")'

# Errors for a specific user
docker compose -f docker-compose.prod.yml logs web --no-log-prefix 2>&1 \
  | grep '^{' | jq 'select(.user_id == 42)'

# Latest exceptions
docker compose -f docker-compose.prod.yml logs web --no-log-prefix 2>&1 \
  | grep '^{' | jq 'select(.exception != null) | {ts, message, exception}'
```

### 4.3 Typical Error Patterns

| Log pattern | Meaning | Action |
|------------|-----------|--------|
| `OperationalError: could not connect to server` | DB unreachable | Check the DB container, see [5.1](#51-db-connection-failed) |
| `ENCRYPTION_KEY must be set` | Env var missing | Check `.env` |
| `relation "core_*" does not exist` | Migrations not run | `python manage.py migrate` |
| `OSError: write error` | Disk full | Free up disk space, see [5.5](#55-disk-space-full) |
| `[CRITICAL] WORKER TIMEOUT` | Gunicorn worker timeout | See [5.4](#54-gunicorn-timeout) |

---

## 5. Troubleshooting

### 5.1 DB Connection Failed

**Symptom:** `OperationalError: could not connect to server` or the health check reports `"database": "unavailable"`.

```bash
# 1. DB container status
docker compose -f docker-compose.prod.yml ps db

# 2. DB health check
docker inspect $(docker compose -f docker-compose.prod.yml ps -q db) | grep -A5 Health

# 3. Test a direct connection
docker compose -f docker-compose.prod.yml exec db \
  pg_isready -U $POSTGRES_USER

# 4. Check DB logs
docker compose -f docker-compose.prod.yml logs db --tail=50

# 5. Restart the DB container
docker compose -f docker-compose.prod.yml restart db

# 6. Wait until healthy, then restart web
docker compose -f docker-compose.prod.yml restart web
```

### 5.2 Migration Lock

**Symptom:** The deploy hangs at `migrate`, no progress in the logs.

The entrypoint uses `pg_advisory_lock(1)` to prevent parallel migrations. A lock stays stuck if a previous migrate process crashed.

```bash
# 1. Show pending locks
docker compose -f docker-compose.prod.yml exec db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT pid, granted, objid FROM pg_locks WHERE locktype = 'advisory';"

# 2. Release the lock manually
docker compose -f docker-compose.prod.yml exec db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT pg_advisory_unlock(1);"

# 3. Restart the web container
docker compose -f docker-compose.prod.yml restart web
```

### 5.3 TLS Certificate

**Symptom:** Browser shows a certificate error, HTTPS unreachable.

```bash
# 1. Check Caddy logs
docker compose -f docker-compose.prod.yml logs caddy --tail=50

# 2. Common causes:
# - DNS does not point to the server IP:
nslookup $DOMAIN

# - Ports 80/443 blocked:
curl -v http://$DOMAIN 2>&1 | head -20

# - Let's Encrypt rate limit (max 5/week/domain):
# → wait 1 week or use the staging CA

# 3. Reset Caddy's TLS data (forces re-issuance)
docker compose -f docker-compose.prod.yml exec caddy \
  caddy reload --config /etc/caddy/Caddyfile
```

### 5.4 Gunicorn Timeout

**Symptom:** `[CRITICAL] WORKER TIMEOUT (pid: NNN)` in the logs, requests abort with 502.

```bash
# 1. Check the current configuration
docker compose -f docker-compose.prod.yml exec web \
  printenv | grep GUNICORN

# Defaults: GUNICORN_WORKERS=3, GUNICORN_TIMEOUT=30

# 2. Increase the timeout (in .env)
# GUNICORN_TIMEOUT=60

# 3. Adjust the worker count (rule of thumb: 2 * CPU cores + 1)
# GUNICORN_WORKERS=5

# 4. Restart
docker compose -f docker-compose.prod.yml restart web
```

**Finding the root cause:** Long-running requests point to slow DB queries. Use the Django Debug Toolbar (dev only) or `EXPLAIN ANALYZE`.

### 5.4a Cache Backend / Multi-Worker (Refs #795, #2002)

As soon as `GUNICORN_WORKERS > 1` is running (see [5.4](#54-gunicorn-timeout)), `django-ratelimit` (login/sudo/health brute-force protection) and the health detail caching need a **cross-process** cache. Otherwise (Django's default `LocMemCache`), each worker holds its own counter and the effective rate limits multiply by the worker count.

**Default: the bundled `redis` service.**
`docker-compose.prod.yml` starts a Redis on the internal network and sets `REDIS_URL=redis://redis:6379/1` for `web`, `maintenance`, and `cron`; `settings/prod.py` then switches to Django's built-in `RedisCache` (no `django-redis` needed). Properties of the service:

- **No host port, optional `requirepass` as a second barrier** (Refs #2002). The `internal` network remains the first access control. When `REDIS_PASSWORD` is set in `.env`, Compose starts the service with `--requirepass` and automatically embeds the credentials in all three `REDIS_URL` defaults; only `REDIS_PASSWORD` needs to be set in `.env`. If the variable is empty or unset, Redis and the previous default URL continue unchanged without a password, and the `core.W004` startup check points this out. The second barrier matters because the cache holds rate-limit keys, including client IPs.
- **No persistence** (`--save ""`, `--appendonly no`, no volume). The cache holds only regenerable data with a short TTL. Persistence would be a data-protection *minus* here: it would write rate-limit keys (including client IPs, user IDs) to disk, where no retention policy captures them. A Redis restart only costs a cold cache.
- **Memory capped** (`--maxmemory 256mb`, `allkeys-lru`), so the container does not run into `mem_limit` OOM.

Use only URL-safe characters for `REDIS_PASSWORD`: `@`, `:`, `/`, and `#` would otherwise break the embedded URL. A suitable value can be generated with `openssl rand -hex 32`. A syntactically invalid Redis URL aborts startup with `core.E004` and a clear hint; the message does not print the URL because it may contain a password.

The environment-variable route is not a Docker secret: in the `redis` container, the password appears as `--requirepass` in the process command line and therefore under `/proc`; in the consumers' Docker container configuration it is part of `REDIS_URL`. Both are visible through `docker inspect` or to people with Docker daemon access. Docker access is root-equivalent anyway and must be restricted accordingly.

To rotate the password, change the value and recreate Redis plus the continuously running consumers; the cache contains only regenerable data. `maintenance` is profile-bound and picks up the new value on its next ad-hoc start:

```bash
docker compose -f docker-compose.prod.yml up -d redis web cron
```

**Fallback (unchanged): `DatabaseCache`.**
If `REDIS_URL` is empty or unset, `prod.py` uses the shared `DatabaseCache`. The cache table `anlaufstelle_cache` is created via migration (`0092`) and comes up automatically as part of the normal one-shot migrate job (`docker-migrate.sh`, see [1.2](#12-deploy-steps)) — **no** separate `createcachetable` step needed. The fallback is functionally equivalent (also cross-process, `RATELIMIT_USE_CACHE=default`), but costs a DB round trip per rate-limit counter. **Redis is therefore a performance decision here, not a correctness one.**

Deliberately turning Redis off (e.g. a very small deployment, where every MB of RAM counts):

```bash
# in .env — empty, not commented out:
REDIS_URL=
```

```bash
docker compose -f docker-compose.prod.yml up -d --scale redis=0
```

**External Redis** instead of the bundled one: point `REDIS_URL` in `.env` at your own endpoint (`redis://:password@redis.internal:6379/1`) and include that endpoint's credentials in the URL yourself. When `REDIS_URL` is set explicitly, `REDIS_PASSWORD` only affects the now-unused bundled service. The Compose value is only a fallback for an unset variable; the bundled service can then be scaled to 0 as above.

**Which backend is currently running?** The `/health/` endpoint reports it for internal/token callers:

```bash
curl -sf -H "X-Health-Token: $HEALTH_DETAIL_TOKEN" https://$DOMAIN/health/ \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["cache"])'
# {'backend': 'django.core.cache.backends.redis.RedisCache', 'status': 'ok'}
```

This is not a cosmetic field: if `REDIS_URL` is set but the `redis` package is not present in the image (typical when an **older** `APP_VERSION` image is run with the new compose file), `prod.py` deliberately falls back to `DatabaseCache` and writes a warning to the container log — the stack stays operational, but runs slower. `cache.backend` makes this state visible; the payload never shows `LOCATION` and therefore never exposes an embedded password. `cache.status = error` (backend configured but unreachable) sets the overall status to `degraded`, because the rate-limit protection is then also ineffective.

**No separate `ratelimit` cache alias.** The original plan envisioned a second alias on Redis DB `/2`. It was not implemented: `django-ratelimit` hangs off `RATELIMIT_USE_CACHE`, and no code reads `caches["ratelimit"]` — the alias would be dead configuration and, in the `DatabaseCache` fallback, would additionally need a second cache table (i.e. another migration). DB index `/2` stays free for a later separation.

### 5.5 Disk Space Full

```bash
# Docker disk usage
docker system df

# Largest volumes
docker system df -v | grep -E 'VOLUME|pgdata|caddy'

# Clean up old images and build cache
docker system prune -f

# Check old backup files
du -sh /opt/anlaufstelle/backups/*/

# Attachment usage per facility (size of the MEDIA_ROOT subfolder)
du -sh /opt/anlaufstelle/media/*/
```

> **Monitoring is no substitute for a quota** (#2129). The disk check in `/health/` (`HEALTH_DISK_REQUIRED`) reports **that** the volume is filling up -- it does not prevent it and knows nothing about facilities. In multi-tenant operation that is not enough: a single facility can fill the shared `MEDIA_ROOT` with nothing but legitimate uploads, and the consequences (upload and backup failures) then hit everyone. Set a per-facility storage quota:
>
> - `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB` (ENV, default **2048**) -- backstop for every facility without its own quota; `0` disables it.
> - `Settings.max_storage_mb` per facility (Administration → Settings, group "Files") -- the stricter of the two always applies.
>
> The **trash counts**: a removed attachment keeps its space until it is physically purged. Source files of open CSV imports also count during enforcement until the run is committed or aborted; the administration overview remains attachment-based. When a facility runs full, irreversible deletion of old attachments (lead/administration) is the fastest lever -- the administration overview reports the trash share separately. Details: [admin guide § 2.9 "Storage quotas"](admin-guide.md#storage-quotas).

### 5.6 Web Container Does Not Start (Restart Loop)

```bash
# 1. Exit code and logs
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs web --tail=100

# 2. Common errors
# "ImproperlyConfigured" → missing env vars in .env
# "ENCRYPTION_KEY must be set" → ENCRYPTION_KEY missing in .env
# "connection refused" → DB not ready yet

# 3. Verify env vars
docker compose -f docker-compose.prod.yml exec web printenv | sort

# 4. Run the Django check manually
docker compose -f docker-compose.prod.yml exec web \
  python manage.py check --deploy
```

### 5.7 Upload Size Limit (Caddy `request_body`)

**Symptom:** `413 Request Entity Too Large` on a file upload (attachments/replace), even though the upload is below the limit configured in the app.

Refs #1363 (N10 + review follow-up): Caddy already rejects request bodies above `1100MiB` at the edge (defense-in-depth against disk fill/OOM, before Django/Gunicorn even sees the body). The app-side hard limit is `FILE_VAULT_MAX_UPLOAD_BYTES` (default 50 MiB, `settings/base.py`) — but file upload fields may contain **several files per field** per `FILE_VAULT_MAX_UPLOAD_FILES` (default 20, `MultipleFileInput`/Refs #622). The Caddy cap is therefore sized for the worst case of a full multi-file field: `FILE_VAULT_MAX_UPLOAD_BYTES * FILE_VAULT_MAX_UPLOAD_FILES` (= 1000 MiB by default) + 10% margin for multipart overhead (boundaries, field headers, CSRF token, other form fields in the same request). A cap sized for only ONE file (the original `55MiB`) rejects legitimate, already-supported multi-file uploads at the edge, even though every single file would pass all app-side size/type checks.

**Residual risk (deliberately documented, not fixed):** A `DocumentType` — configurable only by `facility_admin`/`super_admin` — can carry **several** FILE fields; each independently carries up to `FILE_VAULT_MAX_UPLOAD_FILES` files. The theoretical worst case per request then grows by another `FILE_VAULT_MAX_UPLOAD_BYTES * FILE_VAULT_MAX_UPLOAD_FILES` for each additional FILE field. Deployments with multi-field file document types should scale the Caddy cap up accordingly (no automatic reconciliation).

```bash
# 1. Check the current Caddy value (all four variants: Caddyfile,
# Caddyfile.dev, Caddyfile.demo, Caddyfile.staging)
grep -A2 request_body Caddyfile*

# 2. Check the app-side limit (if overridden via env)
docker compose -f docker-compose.prod.yml exec web \
  printenv | grep FILE_VAULT_MAX_UPLOAD
```

A test guard keeps both settings in sync with the static Caddy value (developer repo: `src/tests/test_architecture_guards_ops.py::TestCaddyUploadBodyLimit`) — it fails as soon as `FILE_VAULT_MAX_UPLOAD_BYTES`/`FILE_VAULT_MAX_UPLOAD_FILES` are changed without updating the Caddy cap to match.

**IMPORTANT — maintainer handoff:** The Caddy value is statically stored in the Caddyfile and does **not** automatically follow when `FILE_VAULT_MAX_UPLOAD_BYTES` **or** `FILE_VAULT_MAX_UPLOAD_FILES` is overridden via env. When changing either of the two app limits (or for a deployment with multi-field file document types, see the residual risk above), update the value in the relevant Caddyfile manually and reload Caddy:

```bash
docker compose -f docker-compose.prod.yml exec caddy \
  caddy reload --config /etc/caddy/Caddyfile
```

**Rollout note:** This change itself also needs a Caddy reload after the deploy (command above, adjust the file path depending on the compose variant) — a plain web container restart does not load a new Caddyfile config.

### 5.7a Upload Temp Directory (Plaintext Staging Buffer, Refs #1661)

**Background:** Django buffers uploads above `FILE_UPLOAD_MAX_MEMORY_SIZE` (default 2.5 MB) as a **plaintext temp file** on disk, before `core/services/file_vault/storage.py` scans and encrypts them. Without an explicit `FILE_UPLOAD_TEMP_DIR`, Django falls back to `tempfile.gettempdir()` (usually `/tmp`). This is only a short window, but unencrypted client content sits on disk for that moment.

**Mitigation:** `docker-compose.prod.yml`/`staging.yml`/`demo.yml`/`dev.yml` already mount `/tmp` in the `web` container as **tmpfs** (RAM) — necessary because the container runs `read_only` (Refs #1374). `FILE_UPLOAD_TEMP_DIR=/tmp` is set there explicitly in the `environment:` block of the respective compose file, and demonstrably pins Django's upload buffer to this tmpfs, instead of relying on Python's `TMPDIR` resolution order (a `TMPDIR` set accidentally later would otherwise silently redirect the implicit fallback away from tmpfs to a persistent volume). The temp file's content therefore only exists volatile in RAM, and is gone on container restart — no cleanup cron job needed.

```bash
# Verify that /tmp in the web container is actually a tmpfs
docker compose -f docker-compose.prod.yml exec web mount | grep "on /tmp"
# expected: "tmpfs on /tmp type tmpfs (rw,...)"

# Check the currently set value
docker compose -f docker-compose.prod.yml exec web printenv FILE_UPLOAD_TEMP_DIR
```

**Fallback without tmpfs:** On a bare-metal deploy or an individually orchestrated environment without a tmpfs mount (no Docker Compose, a custom Kubernetes manifest, etc.), `FILE_UPLOAD_TEMP_DIR` alone has no effect if the referenced path is not a tmpfs — the plaintext buffer then lands on the same storage as the rest of the filesystem. Options for operators without tmpfs:

1. **Mount your own tmpfs** and point `FILE_UPLOAD_TEMP_DIR` at it (preferred) — e.g. `mount -t tmpfs -o size=512M,mode=1777 tmpfs /path/to/upload-tmp` and set `FILE_UPLOAD_TEMP_DIR=/path/to/upload-tmp`.
2. **Use an encrypted volume** (LUKS/dm-crypt or similar) as the temp target, if RAM capacity is tight for large uploads.
3. **Leave the variable unset** and deliberately accept the residual risk — the short plaintext window then stays on the regular (unencrypted) filesystem storage. `python manage.py check --deploy` warns in this case (`core.W002`) at startup, so the decision is made visibly instead of passing silently.

A full RAM buffer instead of disk streaming (setting `FILE_UPLOAD_MAX_MEMORY_SIZE` very high) is **not** a substitute: that would keep large uploads entirely in process memory (DoS risk with several parallel uploads) instead of closing the time window.

### 5.8 Checking SMTP Delivery

Production uses Django's SMTP backend. In `.env`, `EMAIL_HOST`, `EMAIL_PORT`,
`EMAIL_USE_TLS`, and `DEFAULT_FROM_EMAIL` must match the relay. If it requires
authentication, `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` are required too.
`EXTERNAL_ORIGIN` must also contain the public base URL so that invitation and
reset links are valid. The sender should be authorized for SPF, DKIM, and
DMARC.

Before the first onboarding, replace the example address and send Django's
standard test message from the running web container:

```bash
docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py sendtestemail test-address@example.org
```

Exit code 0 proves handoff to the SMTP backend, not delivery. Also check the
inbox, spam/quarantine, and the raw header for `spf=pass`, `dkim=pass`, and
`dmarc=pass`. If an exception occurs or the exit code is non-zero, correct the
configuration and inspect the web logs:

```bash
docker compose -f docker-compose.prod.yml logs web --tail=100
```

Under **Administration > Access**, a backend error produces the literal German
warning “Zugang wurde angelegt, die Einladung konnte aber nicht versendet
werden” or “Einladung konnte nicht versendet werden”. The invitation can then
be retried with **Resend invitation**. If no message arrives despite a success
message, investigate the SMTP provider or recipient. As a technical emergency
fallback, the Django admin can create an account without an email address and
display a one-time initial password.

---

## 6. Emergency Procedures

### 6.1 Quick Stop (Entire Stack)

```bash
docker compose -f docker-compose.prod.yml down
```

**Stop only web (DB keeps running):**

```bash
docker compose -f docker-compose.prod.yml stop web caddy
```

### 6.2 Immediate Backup Before a Critical Intervention

```bash
# Quick backup (unencrypted, for immediate restore)
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U $POSTGRES_USER -Fc $POSTGRES_DB \
  > /tmp/emergency_$(date +%Y%m%d_%H%M%S).dump

# Encrypted backup via script./scripts/ops/backup.sh
```

### 6.3 Emergency Database Access

```bash
# Interactive psql shell
docker compose -f docker-compose.prod.yml exec db \
  psql -U $POSTGRES_USER $POSTGRES_DB

# Run a single query
docker compose -f docker-compose.prod.yml exec db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT count(*) FROM core_event WHERE is_deleted = false;"
```

### 6.4 Reset Password

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py changepassword <username>
```

### 6.5 Sentry (Optional Error Tracking)

If `SENTRY_DSN` is configured in `.env`, unhandled exceptions are automatically reported to Sentry. Configuration:

```dotenv
SENTRY_DSN=https://...@sentry.io/...
SENTRY_TRACES_SAMPLE_RATE=0.1
```

### 6.5b GDPR Art. 33/34 — Breach Notification (Refs #685)

Anlaufstelle must report data breaches to the supervisory authority within **72 hours** (Art. 33) and inform affected people directly when there is high risk (Art. 34). The code supports this through:

1. **Detection** — `python manage.py detect_breaches` runs hourly (cron) and writes an `AuditLog.Action.SECURITY_VIOLATION` entry for every finding.
2. **Heuristics** (all configurable via settings):
   - `failed_login_burst` — > `BREACH_FAILED_LOGIN_THRESHOLD` (default 20) failed attempts per user within `BREACH_DETECTION_WINDOW_MINUTES` (default 60 min)
   - `mass_export` — > `BREACH_EXPORT_THRESHOLD` (default 10) exports per user within the window
   - `mass_delete` — > `BREACH_DELETE_THRESHOLD` (default 50) deletes facility-wide within the window
   - `mfa_failure_burst` (Refs #2033) — `BREACH_MFA_FAILURE_THRESHOLD` (default 10) failed second-factor verifications per user within the window; `MFA_FAILED` (TOTP) and `WEBAUTHN_FAILED` (passkey) count **together**, otherwise the threshold could be evaded by switching methods. Long window: `BREACH_MFA_FAILURE_THRESHOLD_LONG` (default 30). The threshold is deliberately lower than the one for `failed_login_burst`: whoever fails at the second factor already has the first one — the more serious attacker position, and OTP errors are rare by design.
3. **Optional webhook** — `BREACH_NOTIFICATION_WEBHOOK_URL` env var; receives a POST with a JSON payload (kind, count, threshold, audit_id) on every new finding.
4. **Deduplication** — within 24h, the same finding (kind + user) is only reported once in the `AuditLog`.
5. **Detection warns, it does not lock out** — every heuristic is monitor-only: a hit writes a `SECURITY_VIOLATION` entry and optionally fires the webhook; it revokes **no** access and changes no password. False positives are therefore planned for and tolerable: a user with a series of typos on the authenticator, or a passkey the browser keeps rejecting, can trigger `mfa_failure_burst` (the threshold of 10 is deliberately low) — the consequence is a reviewable entry, never a lockout. Hard lockout remains the job of the independent account lockout (10 failed **logins** in 15 min, `manage.py unlock <username>`) and of the rate limits. In practice this means during triage in § "Manual Process": first check whether the time series of failures looks human (a few minutes, one source IP, success afterwards) or automated (evenly spread over hours, no success, possibly several IPs). Whoever permanently raises the threshold loses exactly the signal from scenario A7 (patient guessing of the second factor with a stolen password session) — so prefer tuning thresholds per instance over disabling the heuristic.

#### Manual Process on SECURITY_VIOLATION

1. **T+0** — Check the `AuditLog` detail, identify affected users/data.
2. **T+24h** — Escalate the matter to the [DPO/data protection officer](https://github.com/anlaufstelle/app/blob/main/SECURITY.md), produce a risk assessment. If the responsible primary person is unavailable, the designated **backup** takes over escalation and reporting ([MAINTAINERS.md](../../MAINTAINERS.md) (German), [GOVERNANCE.md § Escalation Path](../../GOVERNANCE.md) (German)) — the same backup who secures the CRA 24-hour early-warning deadline; as long as they are not named, this remains an open bus-factor-1 item.
3. **T+72h** — Report to the supervisory authority (Bavaria: BayLDA; other German states analogously) + notify affected people if applicable.
4. **Post-incident** — Lessons learned + adjust the heuristic thresholds.

Cron entry (see § 3 Cron Jobs):

```cron
30 * * * * cd /opt/anlaufstelle && \
    .venv/bin/python src/manage.py detect_breaches \
    --settings=anlaufstelle.settings.prod \
    >> /var/log/anlaufstelle-breach-detection.log 2>&1
```

### 6.5c Security Advisory — Report, Assessment, Publication (Refs #1895)

Counterpart to § 6.5b: that section is about a **data breach in an operated instance** (GDPR Art. 33/34), this one is about a **vulnerability in the software itself**. The public commitment on this is in [SECURITY.md](../../SECURITY.md) (reporting channels, SLA, support rule); this section is the internal process behind it.

**Precondition:** "Private vulnerability reporting" must be enabled on `anlaufstelle/app` (Repo → *Settings* → *Advanced Security* → *Private vulnerability reporting* → *Enable*, or `gh api -X PUT repos/anlaufstelle/app/private-vulnerability-reporting`). This setting only exists for **public** repositories — the non-public repositories of the release pipeline are therefore out of scope. As long as it's off, the GHSA form link in `SECURITY.md` leads nowhere and **only the email route works** — status check:

```bash
gh api repos/anlaufstelle/app/private-vulnerability-reporting # {"enabled": true} expected
```

| # | Step | Who | Target |
|---|---------|-----|----------|
| 1 | **Intake** — report via GHSA form or email; confirm receipt, request reproduction steps | Maintainer, or the backup if unavailable ([MAINTAINERS.md](../../MAINTAINERS.md) (German)) | ≤ 5 business days |
| 2 | **Assessment** — reproduce, CVSS v3.1 base score, classify critical/high/medium/low, result to the reporter | Maintainer/backup | ≤ 10 business days |
| 3 | Create a **GHSA draft** (*Security* → *Advisories* → *New draft security advisory* on `anlaufstelle/app`), enter affected + patched versions, credit as the reporter wishes; for critical/high, request a CVE ID via GitHub | Maintainer/backup | with step 2 |
| 4 | **Fix** — failing test first, patch release of the **current** minor series (support rule in `SECURITY.md`: no backports), commit with a `sec:` prefix and a reference to the advisory | Maintainer/backup | critical/high: 14–30 days |
| 5 | **Publication** — publish the advisory (patch + 7-day grace period), a `Security` entry in the [CHANGELOG](../../CHANGELOG.md) with the GHSA/CVE number, hall-of-fame credit | Maintainer/backup | patch + 7 days |
| 6 | **No fix** — justify the rejection (out-of-scope list in `SECURITY.md`), close the matter cleanly with the reporter | Maintainer/backup | ≤ 10 business days |

**Coupling to the CRA reporting duty:** If the vulnerability is **actively exploited**, the regulatory 24h/72h deadlines from [`docs/compliance/cra-einordnung.md` § 3](../compliance/cra-einordnung.md) (German) additionally apply — they don't replace the cadence above, but run in parallel and earlier. The trigger is therefore not "critical score" but "evidence of exploitation in the wild".

**Boundary:** A GHSA describes a vulnerability **in the Anlaufstelle code**. For vulnerable dependencies, § 8.3 applies (`pip-audit`/Dependabot) — there, the upstream advisory already exists, and a dedicated GHSA would be a duplicate report. A dedicated advisory is only warranted if the application exposes the upstream gap and operators therefore need to act.

### 6.5a Maintenance Mode (Refs #700)

Before and during migrations/deploys, switch the stack to 503 so
users see a consistent custom page instead of connection errors.

```bash
# Activate (creates a file flag)
make maintenance-on

# Run the deploy / migration
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

# Deactivate (removes the file flag)
make maintenance-off
```

`make maintenance-on/off` uses `$MAINTENANCE_FLAG_FILE` (env var) or defaults to `/tmp/anlaufstelle.maintenance`. Whitelist:

- `/health/` and `/static/...` stay reachable (load balancer / reverse proxy)
- `MAINTENANCE_ALLOW_IPS` (comma-separated) allows ops access via `X-Forwarded-For` — e.g. for your own admin laptop during the maintenance window

The `Retry-After` header (default 600s) helps browsers + load balancers react sensibly. The flag file is checked per worker at most every `MAINTENANCE_CACHE_TTL` seconds (default 5s) — per-request cost is practically zero when it's off.

### 6.6a Off-Site Backup Sync (Refs #738)

Local backups in `backups/daily/` survive container recreates, but not the complete loss of the prod host (hardware failure, ransomware, provider outage). `backup.sh` optionally syncs to an off-site target after every rotation.

```bash
# .env: configure the off-site target (one of three formats)
BACKUP_OFFSITE_TARGET=rclone:hetzner-bucket:anlaufstelle/backups
# or
BACKUP_OFFSITE_TARGET=s3://anlaufstelle-backups/prod
# or
BACKUP_OFFSITE_TARGET=backup-user@offsite.example.com:/backups/anlaufstelle
```

**Recommended:** Enable object lock / write-once policy on the bucket — ransomware then cannot overwrite encrypted backups.

**Failure mode** (Refs #797): If the off-site sync fails (network down, invalid credentials, disk full on the off-site host), `backup.sh` logs an `ERROR`. A **state file** `$BACKUP_STATE_DIR/.offsite_state` (default: next to the backups) counts consecutive failures. On the **second** consecutive failure, the script exits with exit code 1 — the cron job turns red and the operator sees the incident immediately, without having to scan the logs. Successful runs reset the counter.

**Sentry hook (optional):** If `SENTRY_DSN` AND a `BACKUP_SENTRY_HOOK` script are set, `backup.sh` calls the hook on every off-site failure (arguments: description, off-site target). The hook is a free operator choice — e.g. `sentry-cli send-event` or a custom `curl` script. Without a hook, it stays at log + exit code.

### 6.6 Backup Restore Drill (Refs #720, #739)

Verifies that the latest backup is fully restorable and that the lines of defense (RLS, AuditLog immutability trigger) are preserved. The concrete quarterly host-cron recommendation is below.

Since OPS-05 (Refs #1336), the script covers **both** shipped backup formats. The choice is made via the `--dev`/`--prod` flag or `$DRILL_FORMAT`; without one, the format is auto-detected from the existing backup files (default `dev`, backward-compatible — the existing dev timer runs unchanged). If **both** formats are present at the same time, the drill aborts and requires an explicit choice:

- **`--dev`** — `dev-ops/deploy/backup.sh`, which actually runs on `dev.anlaufstelle.app` (Refs #981): `pg_dump --format=custom` → `pg_restore`, AES-256-CBC, source `$DEV_BACKUP_DIR/dump-*.pgc.enc` (default `/var/backups/anl`), stack `docker-compose.dev.yml`.
- **`--prod`** — `scripts/ops/backup.sh`, the canonical path shipped to self-hosters: plain SQL + `gzip` + A4.3 HMAC sidecar → `psql`, source `$BACKUP_HOST_DIR/daily/anlaufstelle_*.sql.gz.enc` (default: `<repo>/backups/daily`), stack `docker-compose.prod.yml`. The HMAC sidecar is verified BEFORE decrypting.

Both formats are restored as the Postgres superuser (`postgres`, via local socket trust in the db container) — needed for `CREATE DATABASE`, and it also bypasses RLS/FORCE-RLS during the restore.

**Compatibility of backup format × restore path** — the two backup worlds are *not* interchangeable; a drill against the wrong format fails. But the drill picks the matching path for each format:

| Backup source | Dump format | Encryption | File(s) | Restore tool | Restore drill |
|---|---|---|---|---|---|
| `dev-ops/deploy/backup.sh` (runs on `dev.anlaufstelle.app`) | `pg_dump -Fc` (custom) | AES-256-CBC (no HMAC) | `dump-*.pgc.enc` | `pg_restore` | ✓ `restore-drill.sh --dev` (glob `dump-*.pgc.enc`) |
| `scripts/ops/backup.sh` (prod schema) | plain SQL + gzip | AES-256-CBC **+ HMAC-SHA256** sidecar (Refs #1024) | `*.sql.gz.enc` + `.hmac` | `psql` (`scripts/ops/restore.sh`) | ✓ `restore-drill.sh --prod` (HMAC check before decrypt, Refs #1336); `backup.sh --verify` remains a lightweight alternative |

Why no cross-restore: The `--dev` branch searches by glob for `dump-*.pgc.enc` and pipes into `pg_restore` — a plain-SQL backup (`*.sql.gz.enc`) is simply not found this way and is also not a valid `pg_restore` archive. The `--prod` branch searches for `anlaufstelle_*.sql.gz.enc`, checks the `.hmac` sidecar, and restores via `gunzip | psql` — a `dump-*.pgc.enc` carries no sidecar (`backup_verify_hmac` aborts) and is binary format. A run against the wrong format therefore fails. Anyone who doesn't want to run a full drill uses `scripts/ops/backup.sh --verify` for the prod path (HMAC + probe decrypt + row comparison against the live DB).

The backup files are `0600` and owned by root — therefore run **as root**. Without a flag, the script auto-detects the format; explicitly via `--dev`/`--prod` (or `DRILL_FORMAT=dev|prod`):

```bash
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh # auto-detect the format
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --prod # force the prod format (*.sql.gz.enc)
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --dev # force the dev format (dump-*.pgc.enc)
```

| Step | Check |
|---|---|
| 0 | Find the newest backup in the format-specific directory (`--dev`: `dump-*.pgc.enc`; `--prod`: `anlaufstelle_*.sql.gz.enc`) |
| 1 | Create a fresh throwaway DB (`anlaufstelle_drill_<pid>`) |
| 2 | Decrypt + restore the backup — `--prod` first checks the HMAC sidecar, then `gunzip \| psql` (plain SQL); `--dev` `pg_restore` (custom format) |
| 2b | `--prod` only and when `DJANGO_AUDIT_HWM_PATH` is set: copy the newest `audit_hwm-*.json` into the drill container and load its HMAC signature; missing or manipulated → `FAIL`, while the remaining restore checks continue. If the variable is unset, step 2b is skipped with `INFO` and the drill can still exit with code 0 ([§6.7](#67-back-up-and-verify-the-audit-high-water-mark-refs-2022)) |
| 3 | Spot-check per table (`core_facility`, `core_client`, `core_event`, `core_auditlog`, `core_workitem`) — are counts queryable? |
| 3b | **Completeness guard (fail-closed):** row comparison of the critical RLS tables (`core_client`, `core_event`, `core_workitem`, `core_auditlog`) between the throwaway DB and the live DB (`$POSTGRES_DB`) — `Live>0` but `Restore=0` turns it red. Catches the incomplete `--prod` restore that the `psql` exit code (without `ON_ERROR_STOP`) swallows (Refs #1336) |
| 4 | RLS policy check — `SELECT COUNT(*) FROM pg_class WHERE relname LIKE 'core_%' AND relrowsecurity = true` must yield `>= 18` (a conservative floor in the script; the actual count is higher, see section 9) |
| 5 | `auditlog_immutable` trigger exists AND blocks a raw UPDATE |
| 6 | `--prod` only: verify the media archive matching the selected database backup via HMAC, probe-extract it into a host temporary directory in one pass, and compare the Tar listing produced during extraction with the extracted result; output `Medien: N Dateien, M Bytes, HMAC ok`. The target needs roughly twice the archive size free beforehand; `TMPDIR=/path/with/space` redirects the step to another filesystem. If the matching archive is absent, skip the step with `INFO` as a valid database-only case. The trap cleans up the temporary directory and throwaway database. |
| 7 | **On full success (all steps including 3b and 6):** `mark_restore_verified` in the web container → compliance marker |

Output: one `OK` / `FAIL` entry per executed step; optional skipped checks report `INFO`. Exit code != 0 on any `FAIL`. On `FAIL`, check backup integrity immediately — a failed trigger check (step 5) is critical, because AuditLog immutability then no longer applies after a restore.

> **Benign exception during the restore (step 2):** The dump contains a `REFRESH MATERIALIZED VIEW core_statistics_event_flat`, which on restore fails against `FORCE ROW LEVEL SECURITY` on `core_event` (`--dev`: `pg_restore: errors ignored on restore: 1`). This only affects the statistics cache, not the payload data — the mv-refresh timer rebuilds it hourly. The `--dev` drill reports this as `OK … MV-Refresh übersprungen (benigne)` (German, from the script's own log output: "MV refresh skipped (benign)") and only fails on **other** errors. In the `--prod` branch, `psql` deliberately runs without `ON_ERROR_STOP` (the same MV refresh error is tolerated), which means the exit code alone does not prove completeness — the fail-closed proof is delivered by the row comparison in **step 3b**, before the restore is marked as verified.

**Compliance marker** (Refs #919): On success, step 7 automatically writes a `RESTORE_VERIFIED` AuditLog entry (`manage.py mark_restore_verified`), which the compliance dashboard (`/system/compliance/`) uses as an age indicator:

- ≤ 90 days → `ok`
- ≤ 180 days → `warning`
- older → `critical` (GDPR Art. 32(1)(c) violated — restorability no longer demonstrated).

Without a drill ever having run, the dashboard stays at `unknown`. It can still be updated manually (e.g. after a documented out-of-band restore):

```bash
docker compose -f docker-compose.dev.yml --env-file .env.dev exec web \
    python manage.py mark_restore_verified --note "Out-of-band restore $(date +%Y-%m-%d)"
```

**Host cron suggestion (not installed automatically):** The backup files are
mode `0600` and owned by root. Therefore, put this one-liner in the **host's
root crontab**, not in the `cron` sidecar or an unprivileged user's crontab. It
runs at 03:30 on the first day of each quarter, after the 02:00 backup. Keep
the `--prod` flag: without it the script auto-detects the format and would
silently fall back to `--dev` if no prod backup is present. Replace
`ops@example.com` and first make sure that the host command `mail` can deliver
to the operations mailbox.

```cron
30 3 1 1,4,7,10 * cd /opt/anlaufstelle && bash scripts/ops/restore-drill.sh --prod >> /var/log/anlaufstelle-restore-drill.log 2>&1 || mail -s "Restore-Drill FAIL" ops@example.com < /var/log/anlaufstelle-restore-drill.log
```

#### Manual Sentinel Drill for Media Volume Persistence

Automated step 6 checks the integrity and probe extraction of the media **backup**. This separate sentinel drill checks only whether the live media **volume** survives a container recreation; it does not replace the backup test.

```bash
# 1. Create a sentinel file in MEDIA_ROOT
docker compose -f docker-compose.prod.yml exec web \
    sh -c 'echo "drill-$(date +%s)" > /data/media/.restore-drill'

# 2. Recreate the stack (delete containers — volume stays)
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d

# 3. The sentinel verifies volume persistence
docker compose -f docker-compose.prod.yml exec web \
    cat /data/media/.restore-drill
# -> expected output: drill-<timestamp>
```

Delete the sentinel afterwards: `rm /data/media/.restore-drill` in the container.

---

### 6.7 Back Up and Verify the Audit High-Water Mark (Refs #2022)

The audit chain check can detect tail truncation and a completely emptied chain only if its last green state remains anchored outside the database. Set the recommended path in the persistent `/data` volume in `.env`:

```dotenv
DJANGO_AUDIT_HWM_PATH=/data/audit_hwm.json
```

The path deliberately sits next to `MEDIA_ROOT=/data/media`: this makes it persistently writable for `cron`/`maintenance` under the read-only container filesystem without mixing it with user uploads. The `web` service never reads the file and does not need the variable in its explicit environment allowlist.

A green `verify_audit_chain` run writes the count and last hash for each chain there. The file is HMAC-signed with `DJANGO_AUDIT_HASH_KEY` and contains no plaintext case data, so additional encryption is unnecessary. `scripts/ops/backup.sh` exports it through `docker compose exec -T cron cat …` as `backups/daily/audit_hwm-<timestamp>.json`, rotates it with the database/media backups, and includes it in all three off-site paths (rclone, S3, SCP). If the path is unset or the file does not exist yet, the database/media backup remains valid, but the script logs the missing HWM anchor.

When `DJANGO_AUDIT_HWM_PATH` is set, `scripts/ops/restore-drill.sh --prod` copies the newest HWM file into the drill container's `/tmp` and loads it through `HighWaterMarkStore.load()`. A then-missing file or invalid signature produces `FAIL` and therefore exit code 1; the drill still runs the remaining restore, RLS, and trigger checks so a second failure is not hidden. If the variable is unset, the drill skips step 2b with an `INFO` line; that alone does not prevent exit code 0 or the restore marker.

---

## 7. ClamAV Virus Scanning

Every file upload into the Encrypted File Vault is scanned against a
ClamAV daemon **before** encryption ([Issue #524](https://github.com/anlaufstelle/app/issues/524)).
The production compose file [`docker-compose.prod.yml`](../../docker-compose.prod.yml)
starts the scanner automatically with:

```yaml
clamav:
  image: clamav/clamav:stable
  volumes:
    - clamav-db:/var/lib/clamav
```

### 7.1 Configuration

Env vars (defaults, see [`src/anlaufstelle/settings/base.py`](../../src/anlaufstelle/settings/base.py)):

```dotenv
CLAMAV_ENABLED=true # default in prod.py
CLAMAV_HOST=clamav # service name on the compose network
CLAMAV_PORT=3310
CLAMAV_TIMEOUT=15 # seconds; MUST be < GUNICORN_TIMEOUT (Refs #1283)
```

In development/test, the scan is disabled (`CLAMAV_ENABLED=false`), so
no ClamAV instance is required.

### 7.2 Fail-Closed

If the scan is active and the daemon is unreachable, every upload is rejected with a
`ValidationError`, and an `AuditLog` row is written with
`Action.SECURITY_VIOLATION` (`reason=virus_scanner_unavailable`).
Findings are logged with `reason=virus_detected` and the reported signature.

### 7.3 Health Check

`GET /health/` additionally reports `"virus_scanner": "connected"|"unavailable"|"disabled"`
as well as the alias `"clamav": "ok"|"error"|"disabled"` (Refs #798).
With an active scanner and an unreachable daemon, the overall status is set to
`"degraded"`, the HTTP status stays 200 (the hard block happens
on upload, not on the health check). The container healthcheck in the Dockerfile
reads the JSON `status` and reports unhealthy on `degraded` — k8s or custom
orchestration see the incident, but the load balancer does not pull the pod out.

### 7.4 Database Updates (Signatures)

The official `clamav/clamav:stable` image updates the signatures
automatically via `freshclam` inside the container. Persistence happens in the
named volume `clamav-db`. No additional cron job needed.

### 7.5 Manual Check

```bash
# Is the daemon reachable?
docker compose -f docker-compose.prod.yml exec web \
  python -c "from core.services.virus_scan import ping; print(ping)"

# EICAR test against the running scanner (manual, verification only):
docker compose -f docker-compose.prod.yml exec clamav \
  sh -c 'echo "X5O!P%@AP[4\\PZX54(P^)7CC)7}\$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!\$H+H*" | clamdscan -'
```

---

## 8. Updating Dependencies

For reproducible builds, the project uses [pip-tools](https://github.com/jazzband/pip-tools).
The direct dependencies are in `requirements.in` / `requirements-dev.in`,
the fully pinned lock files `requirements.txt` / `requirements-dev.txt`
are generated from them and committed.

### 8.1 Adding or Changing a Dependency

```bash
# 1. Add the dependency to requirements.in (runtime) or requirements-dev.in
# (dev/test) — with an open range, e.g. "Django>=5.1,<5.2".

# 2. Regenerate the lock files (pip-tools must be installed):
make deps-lock

# 3. Review the diff (which transitive packages were added, which versions were
# bumped?), check for known breaking changes.
git diff requirements.txt requirements-dev.txt

# 4. Verify locally
pip install -r requirements-dev.txt
make ci

# 5. Commit the .in and .txt files together (atomically).
git add requirements.in requirements-dev.in requirements.txt requirements-dev.txt
git commit -m "chore: bump <package> to <version>"
```

### 8.2 CI Drift Check

The GitHub workflow [`test.yml`](../../.github/workflows/test.yml) contains a
`lock-check` job that runs `pip-compile` for both lock files and fails on
drift — so nobody can accidentally change `requirements.in`
without updating the lock file to match.

Local variant (without a commit):

```bash
make deps-check
```

### 8.3 Security Updates (pip-audit)

The `audit` job in CI runs `pip-audit` against `requirements.txt`. If the
job reports CVEs, follow this process:

```bash
# 1. requirements.in: raise the affected package to the minimum safe version
# 2. make deps-lock
# 3. make ci + manual verification
# 4. Commit with a "security:" prefix, reference the advisory
```

### 8.4 SBOM (Software Bill of Materials)

The `audit` job additionally generates an SBOM in CycloneDX JSON format
(`sbom.json`) and uploads it as a workflow artifact. Retention: 90 days.

The SBOM is needed for compliance requirements (public
funding, public procurement, supplier audits) and lists all direct and
transitive Python dependencies with versions, licenses, and CVE status at
build time.

**Durable release artifact (Refs #1895):** The CI artifact expires after 90 days — for an older release, it was then no longer possible to prove which components it contained. But CRA Annex I Part II No. 1 requires exactly that. The release build therefore additionally produces a **durable** SBOM `sbom-vX.Y.Z.json` (CycloneDX 1.6) from the lock files of the built tree — offline, without network access, and byte-identical on repetition. Scope: all Python distributions from `requirements.txt` (direct **and** transitive) plus all npm packages from `package-lock.json`. The four vendored frontend libs (htmx, Alpine CSP, Dexie, Chart.js) are listed in it as `required`, even though they are marked `dev` in the lockfile — they are shipped (`src/static/js/`) and run in users' browsers. Build/test tooling stays `optional`. Until `1.0`, the artifact stays with the maintainer (there is no GitHub release page until then) and is available via the contact in [SECURITY.md](../../SECURITY.md); from `1.0` on, it is attached to the GitHub release.

```bash
# Download the SBOM from the last successful test workflow run
gh run download --name sbom-cyclonedx --dir /tmp/sbom-latest

# Inspect the content
jq '.bomFormat, .specVersion, (.components | length)' /tmp/sbom-latest/sbom.json
```

Local generation (without CI):

```bash
pip install pip-audit
pip-audit -r requirements.txt --format cyclonedx-json -o sbom.json
```

### 8.4a Image Signature, Provenance, and Digest Pinning

The release push attaches an SBOM attestation and a provenance attestation
(`mode=max`) to the image manifest and signs the image digest with keyless
Cosign. The signature is recorded in the Sigstore transparency log; no
long-lived signing keys are required.

The project therefore creates three distinct, complementary SBOMs:

| SBOM | Scope and retention |
|------|---------------------|
| CI artifact from the `audit` job | Python dependencies; 90 days |
| Durable release SBOM `sbom-vX.Y.Z.json` | Python and npm dependencies from the lock files; durable evidence under CRA Annex I Part II No. 1 |
| Image SBOM as an attestation | Additionally covers base-image layers and system packages on the image manifest |

The image SBOM does not replace the durable release SBOM. Multi-platform
pushes with `docker/build-push-action@v7` through the Buildx container driver
already produced minimum provenance (`mode=min`); the release workflow raises
it to `mode=max` and adds the SBOM attestation.

Operators can verify the digest, keyless signature, and attestation data
without access to the source repository:

```bash
# Resolve the tag digest
docker buildx imagetools inspect ghcr.io/anlaufstelle/app:vX.Y.Z --format '{{ .Manifest.Digest }}'
# Verify the keyless signature
cosign verify ghcr.io/anlaufstelle/app@sha256:… \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/anlaufstelle/app/\.github/workflows/release\.yml@refs/tags/v'
# Attestations (SBOM/provenance) on the manifest
docker buildx imagetools inspect ghcr.io/anlaufstelle/app:vX.Y.Z --format '{{ json .SBOM }}'
```

`APP_VERSION=vX.Y.Z` remains the readable release tag. Setting
`APP_IMAGE_DIGEST=@sha256:…` additionally and immutably binds the app image in
`docker-compose.prod.yml` to the verified digest. PostgreSQL, Redis, ClamAV,
and Caddy are already pinned there with `@sha256`; the app image was the only
gap. On every version change, `APP_VERSION` and `APP_IMAGE_DIGEST` must be
updated together, or the stack will continue to start the old digest despite
the new tag.

If the GHCR package is private, the demo host needs authentication using
`docker login ghcr.io`. Docker stores these credentials persistently in
`~/.docker/config.json` for the demo-host user; file permissions and token
scope must therefore be restricted accordingly.

### 8.5 Troubleshooting

| Problem | Cause | Solution |
|---------|---------|--------|
| `deps-check` fails even though nothing changed | Different pip-tools version between dev and CI | `pip install --upgrade pip-tools` and rerun `make deps-lock` |
| `pip-compile` can't find a matching version | Range constraint too strict | Loosen the range in `.in`, e.g. `>=X,<Y+1` |
| A transitive dependency pulls a vulnerable version | A direct dependency pins an old version | Add the direct dependency explicitly and bump it |

---

## 9. Row Level Security (RLS)

PostgreSQL RLS on all facility-scoped tables (currently
**25**, enforced test-derived —
[`test_rls.py`](../../src/tests/test_rls.py)) protects
against cross-facility leaks (defense-in-depth below the Django scoping
layer). The set of tables grows with every new facility-scoped model; a fixed
number here therefore goes stale regularly — the derivation in `test_rls.py` is
the source of truth. Every policy compares `facility_id` against the session
variable `app.current_facility_id`, which the
[`FacilityScopeMiddleware`](../../src/core/middleware/facility_scope.py) sets per
request via `set_config(..., is_local=false)`.

**Fail-closed:** If the variable is unset or empty (`NULL`),
the policies return **no rows** — not even for the table
owner (`FORCE ROW LEVEL SECURITY`). Bypass only for superuser DB
roles; the Django DB user must therefore **not** be a superuser in
production.

**Direct instead of join policy on `core_attachment` (Refs
#1738,
[ADR-039](../adr/039-owner-generische-dateianhaenge.md) (German)):** File attachments hung, until
Milestone 24, off the event as `core_eventattachment` with no `facility_id` of their own;
their policy was a join over `core_event`. Since migration `0131`,
`core_attachment` carries its own `facility_id` and the usual **direct** policy
(including the `is_super_admin` branch). Gain: an attachment row with no event was
policy-**less** under the join policy; now the isolation applies regardless of the
owner (event, person, or case). The set of tables does not change
because of this — only the name and construction of the policy. Post-deploy check and
the rollback restriction of the `0129`–`0131` chain: [§ 2.2](#22-roll-back-a-migration).

**CI guarantee (Refs #718):**
[`src/tests/test_rls_functional.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_rls_functional.py)
loads a dedicated Postgres role ``rls_test_role`` (``NOSUPERUSER``)
and verifies, via ``SET ROLE``, cross-tenant 0-rows for Client, Event,
AuditLog, and Activity. This means the RLS protection has been functionally
tested in CI — not just the existence of the policies. If the
production DB user accidentally becomes a superuser, the cross-
tenant test in CI does **not** fail (it uses the explicit test role),
but the smoke query from § 9.2 below must return ``rolsuper =
false`` for the Django DB user in prod.

**Continuous monitoring instead of a one-off smoke query (Refs
#793):** The
health endpoint now runs exactly this check on **every** call, on the
live connection (``SELECT rolsuper, rolbypassrls … WHERE rolname =
current_user``). If the runtime role bypasses RLS, ``status`` falls permanently to
``degraded`` (HTTP stays 200) and token callers see the fields
``db_user_is_superuser``/``db_role_posture``; the detail additionally appears as a
``WARNING`` in the log (logger ``core.views.health``). This means a
misconfigured cluster shows up in monitoring without anyone having to run the
smoke query manually — field reference:
[docs/monitoring-guide.md](../monitoring-guide.md) (German). Boundary: ``check_db_roles``
checks that the app/admin role **exist** in the catalog with the right
attributes; the health endpoint checks **as whom** the application
is actually connecting.

### DB Role Model (Gold Standard)

**This is the canonical description of the PostgreSQL role model** — other
deployment documents point here instead of repeating it. It applies to **all**
deployment stacks (prod, dev, demo and — since
#2086 — staging too);
the per-stack reconciliation lives in [§ 1.4](#14-compose-stack-reconciliation).
The source of truth is the init script
[`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh)
(Refs #863,
#902,
[ADR-005](../adr/005-facility-scoping-and-rls.md) (German), [ADR-017](../adr/017-deployment-topology.md) (German)).

The official `postgres:18` image automatically makes the bootstrap login created
via the container's `POSTGRES_USER` a superuser. This login cannot strip its own
privileges — that's why the container runs with `POSTGRES_USER=postgres` as the
bootstrap, and the init script creates **two** unprivileged application roles:

| Role (`.env` variable) | Example name | Attributes | What it's for |
|---|---|---|---|
| `postgres` (bootstrap, `POSTGRES_BOOTSTRAP_PASSWORD`) | `postgres` | SUPERUSER | Only DB init + emergency maintenance. **Not** for runtime/operator tasks. |
| `POSTGRES_USER` (app) | `anlaufstelle` | `NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB`, **not the owner** — only DML (`SELECT/INSERT/UPDATE/DELETE`) + sequence/schema `USAGE` | Django runtime. RLS policies apply strictly (`FORCE ROW LEVEL SECURITY`). |
| `POSTGRES_ADMIN_USER` (admin/maintenance/**owner**) | `anlaufstelle_admin` | `NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB`, **DB/object owner** | `migrate`, `seed`, retention/anonymization cron, initial setup (`setup_facility`), and package import (`load_package`). As owner, maintenance bypasses the append-only triggers via DDL (A1.4, § 9.5). |

Properties that the init script guarantees, verified in code:

- Both roles are **NOSUPERUSER** — no path to privilege escalation.
- The app role is **NOBYPASSRLS** and **not the owner** (A1.4, Refs
  #1026); it can therefore neither
  disable the append-only triggers via `ALTER TABLE … DISABLE TRIGGER`
  nor — lacking a `SET PARAMETER` grant — bypass them via
  `session_replication_role`. `FORCE ROW LEVEL SECURITY` also applies
  on top (fail-closed, § 9 above).
- The admin role is **BYPASSRLS** (for migrations/maintenance) and **owner** of
  the database + all objects — needed for `DROP POLICY`/`ALTER TABLE` in
  migrations (BYPASSRLS alone is not enough for DDL) and for the
  append-only trigger bypass in the maintenance path. `ALTER DEFAULT PRIVILEGES FOR ROLE
  admin … TO app` automatically grants the app DML rights to every migrated
  table/sequence.

Operator/maintenance tasks run exclusively in the opt-in `maintenance`
service; only it loads the full `.env` and overrides
`POSTGRES_USER`/`POSTGRES_PASSWORD` with the admin role there. `web`, by contrast,
gets an explicit runtime allowlist and sees neither the admin nor the bootstrap
secret (Refs #1962). The
migrate job [`docker-migrate.sh`](../../docker-migrate.sh) runs in this service. The
role gate `check_db_roles` checks the stable app role via
`POSTGRES_APP_USER` (Refs #1017), so the env override doesn't confuse it.

**Mandatory `.env` variables** (template [`.env.example`](../../.env.example)):
`POSTGRES_BOOTSTRAP_PASSWORD`, `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD`
(in addition to the app variables). The pre-deploy check in § 1.1 verifies
that they are set and no longer at `change-me-*`.

**Naming contract — two variable names for the same role.** The app role is
addressed under **two** names, and both must be fed from `${POSTGRES_USER}`:

| Variable | Where it is set | Who reads it |
|---|---|---|
| `APP_DB_USER` | `environment:` of the **`db`** service | [`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh) — **creates** the role under this name |
| `POSTGRES_APP_USER` | `environment:` of **`web`**, **`maintenance`**, **`cron`** | `check_db_roles` resp. `src/core/services/compliance/db_roles.py` — **checks** under this name; the same name is the addressee of conditional `GRANT`s |

If the second name is missing, the check resp. the `GRANT` is skipped silently
(no error, no log) — the defect only surfaces months later in operations.

**Existing clusters: the Compose fix is not retroactive.** The init script is
**init-only** and runs solely when an empty `PGDATA` is created. On an already
existing volume (e.g. `*_pgdata_staging`) it neither creates new roles nor
aligns existing ones with the env. Two ways forward:

1. **Fresh volume** — dump/restore per
   [§ 13.1](#131-prodstaging--preserving-data-maintenance-window). The simpler,
   already rehearsed path; the init script builds the target roles itself.
2. **In-place rebuild** per
   [§ 9.6](#96-rolling-out-the-runtimeowner-role-split-to-existing-installations-a14).
   **Careful — a plain ownership move is not enough there:** on a cluster that
   came up without the init script there is no admin role at all, and the app
   role is the **bootstrap role**, which cannot be stripped of its privileges
   (`The bootstrap superuser must have the SUPERUSER attribute`). § 9.6
   diagnoses the starting position and describes the complete path per case,
   including role creation; password rotation alone
   (`scripts/ops/rotate-db-passwords.sh`, § "DB Password Rotation" below)
   creates **no** roles.

**Until one of these two paths has been carried out, the old superuser
situation stays live on a running staging server** — the repository fix alone
changes nothing there. The `web` healthcheck does make it visible, though: the
container never turns `healthy` there, and `caddy` does not release the runtime.

**Verification** after the first startup — the gate checks both roles:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py check_db_roles
# expected (role labels are German, from the command's own output):
# App-Rolle rolsuper=False, rolbypassrls=False
# Admin-Rolle rolsuper=False, rolbypassrls=True (exit code 0)
```

On the staging stack via the `maintenance` one-shot (the profile is opt-in, and
`--env-file` is mandatory there):

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging \
    run --rm maintenance check_db_roles
```

### DB Password Rotation (All Three Roles)

Refs #1964. A change
to `.env` alone rotates **no** password in an existing PostgreSQL
cluster: [`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh)
only runs when first initializing an empty volume, and creates missing
roles. It stays deliberately init-only; an `ALTER ROLE` branch there would be a
half-effective promise, because it gets skipped on every existing volume.

The canonical, tested way is:

```bash
cd /opt/anlaufstelle./scripts/ops/rotate-db-passwords.sh

# dev/demo use their own compose/env file:
COMPOSE_FILE=docker-compose.dev.yml ENV_FILE=.env.dev \./scripts/ops/rotate-db-passwords.sh
```

**Prerequisites:** announce a maintenance window; make sure no
backup/restore job is currently running; have a current, verified backup
state; `.env` must have mode `600` (or stricter). The script needs Docker
access and `openssl`, but does **not** print any password to stdout/stderr or in
process arguments. It generates three independent random values and
internally, as the bootstrap superuser, issues a transaction following this
pattern:

The rotation is **not a deployment**: all `compose run`/`up` steps use
`--pull never` and therefore exactly the already-verified local images. This way
neither a moved tag can switch the code mid-maintenance-window, nor can a
registry outage block rotation or rollback. Always deploy and verify a new
image separately before the maintenance window.

```sql
BEGIN;
ALTER ROLE "anlaufstelle" PASSWORD '<new app password>';
ALTER ROLE "anlaufstelle_admin" PASSWORD '<new admin password>';
ALTER ROLE "postgres" PASSWORD '<new bootstrap password>';
COMMIT;
```

The placeholders are **not** something to copy-paste. Never write passwords
into shell history, a ticket, or the runbook log; the script only passes SQL and
login secrets via stdin. A manual emergency run must use the same order
and a single transaction.

#### Order and Full List of Consumers

| Step/consumer | Role | What the rotation does |
|---|---|---|
| `web` | App (`POSTGRES_PASSWORD`) | Stop before the DB change; start with the new password after the env swap and wait for health. |
| `cron` | Admin (`POSTGRES_ADMIN_PASSWORD`) | Stop/start together with `web`, so no retention/audit job reconnects with the old login. In dev/demo without `cron`, only this step is skipped. |
| `maintenance` / [`docker-migrate.sh`](../../docker-migrate.sh) | Admin | Run after the DB recreate as a short-lived one-shot with the new env. This checks `check_db_roles`, migrations, and the ownership normalization from #1085. |
| `db` | Bootstrap (`POSTGRES_BOOTSTRAP_PASSWORD`) | Change all three roles in one transaction; then **recreate** the container with the unchanged data volume, so its container env also no longer holds the old bootstrap secret. |
| [`scripts/ops/backup.sh`](../../scripts/ops/backup.sh) | Admin | No daemon restart: the host script reads `.env` on every new run. Therefore do not start it in parallel during the rotation. |
| Restore path (`restore.sh`, `restore-drill.sh`, [Disaster Recovery](../disaster-recovery.md) (German)) | Bootstrap/Admin | No persistent consumer; every later run reads the new `.env`. Escrow/emergency documents must be updated immediately. |

The script first writes a mode-identical env file in the same directory,
then stops the persistent consumers, rotates the DB, tests all **new**
logins, swaps `.env` atomically, recreates `db`, runs the real migrate path,
and starts `web`/`cron`. Only after that do three negative tests run over the
compose network host `db`, and therefore PostgreSQL's SCRAM password
authentication. The loopback path in the DB container is not suitable for
this, because official PostgreSQL volumes may carry a `trust` rule there.
Expected closing lines (role names may differ, secrets never appear). The script's messages are German and not localized, so they are reproduced here verbatim — match your terminal output against these lines literally, not against a translation:

```text
[db-password-rotation] Negativtest gruen: alter Login fuer Rolle anlaufstelle wird mit Auth-Fehler abgewiesen.
[db-password-rotation] Negativtest gruen: alter Login fuer Rolle anlaufstelle_admin wird mit Auth-Fehler abgewiesen.
[db-password-rotation] Negativtest gruen: alter Login fuer Rolle postgres wird mit Auth-Fehler abgewiesen.
[db-password-rotation] Rotation abgeschlossen. Backup-/Restore-Jobs lesen beim naechsten Lauf die neue Env-Datei.
```

(English: "Negative test green: old login for role \<X> is rejected with an auth error." / "Rotation complete. Backup/restore jobs will read the new env file on their next run.")

A plain "connection failed" doesn't count: the negative test only accepts
PostgreSQL's `password authentication failed`; before that, the same three
roles were successfully tested with the new passwords. If any
step fails, the exit trap resets DB passwords and `.env` to the old values,
recreates `db` if needed, and starts the previously stopped consumers.
A warning **"Rollback was incomplete"** is a hard operational alert:
do not trigger any further restart until the DB and `.env` have been reconciled manually.

**Bootstrap decision:** `postgres` stays `LOGIN`, but gets rotated along with the
others and stays outside `web`, `cron`, and normal operator jobs. `NOLOGIN` is
deliberately **not** set: this same role is the only controlled path for
a later rotation and the factory-fresh restore path. Without a second
superuser, it could not be safely reactivated from within the application after
`NOLOGIN`; that would shift the emergency risk rather than reduce it.

**Immediate follow-up:** Replace the three new DB passwords in the separate
password escrow and in the DR contact card, mark the old state as revoked,
and only then close the maintenance window. An old escrow state
would make backup/restore and emergency commands against the existing cluster
fail precisely when the local `.env` is no longer reachable.

### 9.1 Symptoms of an RLS Problem

- The UI shows empty lists even though rows exist in the DB.
- ORM queries suddenly return 0 rows.
- No exceptions show up in the logs — RLS filters silently.
- `audit` tables stay empty for new facility assignments.

### 9.2 Diagnosis in `psql`

```sql
-- Check the current session variable (NULL = the middleware did not set it)
SELECT current_setting('app.current_facility_id', true);

-- List all RLS-enabled tables
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relname LIKE 'core_%' AND relrowsecurity;

-- Show a table's policies
SELECT polname, polcmd, pg_get_expr(polqual, polrelid) AS using_clause
FROM pg_policy
WHERE polrelid = 'core_client'::regclass;
```

### 9.3 Manual Debug Queries with Facility Context

Run individual debug queries against the DB **without** bypassing RLS —
set only for the running transaction:

```sql
BEGIN;
SET LOCAL app.current_facility_id = '<facility-uuid-or-pk>';
SELECT count(*) FROM core_client;
--... further queries...
COMMIT; -- or ROLLBACK, SET LOCAL ends with the transaction
```

**Caution:** Never use `SET app.current_facility_id` (without `LOCAL`) in
a shared admin connection — otherwise the value stays valid for
subsequent queries in that session.

### 9.4 Common Causes

| Finding | Cause | Fix |
|--------|---------|-----|
| `current_setting(...)` returns `NULL` | The request came through with no auth user (e.g. a management command, cron) | Set it deliberately via `set_config` or `SET LOCAL` before the query |
| The variable is set, but the list is empty anyway | The user is scoped to the wrong facility | Check `SELECT facility_id FROM core_user WHERE id=<user_id>` |
| Migration `0047` not applied | `showmigrations core` shows `[ ]` | `python manage.py migrate core 0047` |
| An admin query via `./manage.py shell` returns 0 rows | The shell session does not set the variable | Before queries: `connection.cursor().execute("SET app.current_facility_id = %s", [fid])` |

Refs #542,
#586.

### 9.5 Append-Only Trigger Bypass via Owner DDL (A1.4)

GDPR Art. 17 anonymization (`Client.anonymize`), audit pruning
(`prune_auditlog`), and `backfill_audit_chain`/`reencrypt_fields` need to briefly
bypass the append-only triggers (`auditlog_immutable`, `eventhistory_no_update`/`_no_delete`)
for their legitimate writes. Since A1.4 (Refs
#1026), this no longer
goes through `session_replication_role = replica` (that no longer silently fires
an `ENABLE ALWAYS` trigger); instead, `bypass_replication_triggers()`
disables the triggers specifically via **owner DDL** `ALTER TABLE … DISABLE TRIGGER`
and restores them via `ENABLE ALWAYS` — all inside **one**
`transaction.atomic()` (SIGKILL-safe).

Consequence for operations: The maintenance path needs **owner rights**. The
management commands `enforce_retention`, `backfill_audit_chain`,
`reencrypt_fields`, and `load_package` (M2, Refs
#1058 — the RLS bypass guard
otherwise aborts fail-loud) must therefore run as the **owner/admin role**
(`POSTGRES_ADMIN_USER`) — this is already set up that way via the dedicated
`maintenance` service (retention cron § 6). If such a
command accidentally runs as the app role, the bypass aborts with `must be owner of
table core_eventhistory` (since the ordering reversal in
#2065,
`core_eventhistory` comes first in `_APPEND_ONLY_TRIGGERS`) — fail-loud, no
silent audit loss.

**The Art. 17 immediate action is not a maintenance path** (Refs
#2085). The trash
action "Anonymize permanently now" runs **interactively in the web process**
and therefore as the app role — there is no owner DDL path and no maintenance
window there. Until 0.90 it ran straight into the bypass: the first
`ALTER TABLE … DISABLE TRIGGER` failed, the `@transaction.atomic` bracket rolled
back the **entire** anonymization including its audit entry, and the user saw an
**HTTP 500** — the person stayed untouched and the erasure request went
unfulfilled. This never showed on development or test systems, because they run
the single-role model where the same connection is the owner.

Since #2085 the path is **split in two**, and the app role gets **no** new
privilege:

| What | When | Who |
|---|---|---|
| Pseudonym, notes, custom fields, case/episode/live event content, timestamp generalization, attachments | **immediately, in the request** | app role (ordinary DML) |
| **Earlier versions of the event documentation** (`core_eventhistory`) | **with the next `enforce_retention` run** (cron, 03:00) | admin/owner role in the `maintenance` service |

The follow-up is the fourth candidate set of the event-history sweep from
[ADR-050](../adr/050-eventhistory-retention.md) (proof category
`eventhistory_anonymized_client`). It finds the rows via the `Client.anonymized_at`
stamp that the anonymization itself sets — so there is no second piece of state
that could drift from reality. It therefore inherits everything the sweep
already provides: **hold-aware** (a legal hold arriving after the anonymization
defers the history), **idempotent** (`redacted_at IS NULL` as the condition),
batched, and with a proof row per batch.

> **Operationally relevant:** Between the action and the nightly run, the
> **earlier versions** of that person's event documentation are still in the
> database with their values — at most until the next run, i.e. 03:00 by
> default. The user interface and the handbook say so explicitly. If an Art. 17
> erasure has to be completed immediately, run once by hand afterwards:
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance enforce_retention
> ```
>
> A `SECURITY DEFINER` function that would open the bypass to the app role for
> exactly this step was built in #2085 and **rejected again**: its body had to
> read the facility from `app.current_facility_id` — a custom GUC the calling
> role can set itself — and it did not know the spoliation guard from
> `anonymize_client_now`. Both paths were reproduced against PostgreSQL.
> [ADR-033](../adr/033-runtime-owner-role-separation.md) and ADR-050 M-8
> therefore remain in force unchanged.

The same role prerequisite applies to **RunPython backfills on RLS tables**:
they run under the migration role and, since
#1746, are safeguarded as their
first line by `assert_migration_role_bypasses_rls(schema_editor)`.
The `facility_id` backfill of the attachment migration `0130`
(#1738) uses this
guard — if `migrate` accidentally runs as the app role, it aborts fail-loud,
instead of silently backfilling **0 rows** and reporting the migration as
successful. `docker-migrate.sh` therefore runs exclusively in the maintenance service.

The former `GRANT SET ON PARAMETER session_replication_role` is **no longer
needed** and has been removed from the init script. On existing DBs it can
be revoked as a cleanup step (optional, least privilege):

```sql
-- as superuser (postgres), in the application DB, optional:
REVOKE SET ON PARAMETER session_replication_role FROM anlaufstelle_admin;
```

**Lock behavior & maintenance window (important).** `ALTER TABLE … DISABLE/ENABLE
TRIGGER` takes a `ShareRowExclusiveLock` on the table and **holds it until
the commit** of the enclosing transaction. This lock conflicts with the
`RowExclusiveLock` of ordinary `INSERT`s — while a bypass run is open,
**parallel audit/EventHistory `INSERT`s block** (nearly every mutating
request). The former `session_replication_role = replica` did not take this
table lock; the owner-DDL switch (A1.4) is here a deliberate
regression in favor of security. `Client.anonymize()` additionally has a
**lock-upgrade deadlock risk** (first a `RowExclusiveLock` from the
EventHistory `INSERT`s, then a `ShareRowExclusiveLock` from the `DISABLE`).

**Since 0.89 the nightly retention run is a REGULAR bypass caller**
(ADR-050, Refs #2065):
the EventHistory sweep redacts expired history versions and deletes
long-redacted ones. Three properties confine the impact to the maintenance
window:

- **Batched.** One bypass block per 500 rows, not one per row as in the key
  rotation — the number of `ShareRowExclusiveLock` intervals drops by orders
  of magnitude.
- **Only `core_eventhistory`.** The sweep opens exclusively the two triggers
  on that table; on `core_auditlog` it no longer takes any DDL lock at all,
  only the `RowExclusiveLock` of its own proof `INSERT`. Audit `INSERT`s of
  running requests therefore do **not** block.
- **Canonical lock order** `core_client` → `core_event` →
  `core_eventhistory` → `core_auditlog`, which is exactly the order the
  application writes in anyway. Until 0.89, `_APPEND_ONLY_TRIGGERS` listed
  `core_auditlog` first — an ABBA cycle against every mutating request.
  Person and event rows of a batch are locked with
  `SELECT … FOR UPDATE SKIP LOCKED` **before** the trigger bypass opens:
  otherwise the run would hold the table lock while waiting for a foreign row
  lock. The legal-hold creation locks the same rows before it writes — which
  is why a hold on an **event or person** reliably stops the sweep and is
  never overtaken by it. If a batch still aborts with `deadlock detected`, it
  is retried idempotently with backoff; the run does not fail.

Operational rules that follow from this:

- Run the bypass commands `enforce_retention` (retention cron, § 6),
  `backfill_audit_chain`, and `reencrypt_fields` during **maintenance/off-peak
  hours** with low write load — not during peak time. This does not apply to the
  **interactive** Art. 17 action: it no longer takes any DDL lock in the web
  process (see above); its history redaction rides along with the nightly run.
- Set a **`lock_timeout` as a role/session default** for the maintenance/owner
  role, so that a blocked `ALTER TABLE` does not wait indefinitely,
  but aborts fail-loud and stays retryable:

  ```sql
  -- as superuser (postgres): limits the lock wait time of maintenance runs.
  ALTER ROLE anlaufstelle_admin SET lock_timeout = '5s';
  ```

  Deliberately **not** hardcoded in the bypass code (`SET LOCAL lock_timeout`): a
  fixed value would, without an operational profile, turn legitimate `anonymize`
  runs that may run synchronously in the request into spurious hard fails, and a
  solid regression test would need two competing sessions (flaky in CI). The
  timeout is therefore operationally adjustable (ADR-033 § Consequences).

**Verification** (the app role CANNOT disable triggers, the owner role can):

```sql
-- App role (not the owner) -> must fail:
SET ROLE anlaufstelle; -- POSTGRES_USER
ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable; -- ERROR: must be owner
RESET ROLE;
-- Trigger dormant state -> 'A' (ENABLE ALWAYS):
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgname IN ('auditlog_immutable','eventhistory_no_update','eventhistory_no_delete');
```

### 9.6 Rolling Out the Runtime/Owner Role Split to Existing Installations (A1.4)

**Fresh DBs** pick up the split automatically
([`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh):
admin becomes owner, app only gets DML). An **already-running installation with
a misconfigured role setup** (ownership still with the app role, or no separate
admin role) needs a **one-off, maintainer-led** rebuild — brief downtime
recommended (no write requests during it).

> **The simpler, already rehearsed alternative:** dump → fresh volume → restore
> per [§ 13.1](#131-prodstaging--preserving-data-maintenance-window). The init
> script then builds the target roles correctly by itself and there is no
> special case. The in-place rebuild below is only worth it when the maintenance
> window for dump/restore would be too long. Refs
> #2086.

#### 9.6.0 Diagnose first — there are three different starting positions

The path depends on **which role is the cluster's bootstrap role**. The
bootstrap role is the one created by `initdb` (`pg_authid.oid = 10`); it
**cannot** strip its own privileges. As a superuser, in the application DB:

The example names below are **placeholders** — substitute the real values of
`POSTGRES_USER` and `POSTGRES_ADMIN_USER` from your env file.

```sql
-- Which role is the bootstrap role (can NEVER become NOSUPERUSER)?
SELECT rolname AS bootstrap_role FROM pg_authid WHERE oid = 10;
-- Full attribute profile of the roles involved.
SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreaterole, rolcreatedb
  FROM pg_roles
 WHERE rolname IN (current_user, 'anlaufstelle', 'anlaufstelle_admin');
-- Who owns the database?
SELECT pg_get_userbyid(datdba) AS db_owner FROM pg_database WHERE datname = current_database;
-- Which application objects belong to NEITHER app nor admin? (> 0 means the
-- rebuild is incomplete — exactly what a plain ownership move misses.)
SELECT pg_get_userbyid(c.relowner) AS owner, count(*)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r','p','S','v','m','f')
 GROUP BY 1 ORDER BY 2 DESC;
```

| Finding | Case | Path |
|---|---|---|
| App **and** admin role exist with the correct attribute profile, only ownership still sits with the app role | **A** | § 9.6.1 (ownership move only) |
| No admin role, app role is privileged but **not** the bootstrap role | **B** | § 9.6.2 |
| No admin role, and the app role **is** the bootstrap role (`oid = 10`) | **C** | § 9.6.3 — the app **must** move to a new role name |

> **Partial states:** The three cases describe clean starting positions. An
> aborted earlier rebuild can leave mixtures ("admin already exists, app is
> still privileged", "objects still belong to the bootstrap role"). The building
> blocks below are individually idempotent: role creation only adds what is
> missing and then normalizes attributes and password unconditionally; the
> ownership loops only pick up objects not yet moved; grants are repeatable. The
> case C block is repeatable as a whole — including after a run that aborted
> midway. In such a case run the case matching the **bootstrap finding**
> (B or C) and let the already-completed steps pass through. The last query
> above is then the measure: it must show the admin role only.

**Case C occurs when no init script is mounted:** if `db` loads the full env
without [`deploy/postgres-init/`](../../deploy/postgres-init/), the Postgres image
creates `${POSTGRES_USER}` itself via `initdb --username="$POSTGRES_USER"` —
the app role *is* then the bootstrap role, and an admin role is never created.

#### 9.6.1 Case A — ownership move only

Procedure as the **bootstrap superuser**, in the application DB:

```sql
-- 0. Before: stop the web service / put it into maintenance (no app writes).
-- 1. Transfer ownership of the database + all objects to the admin role.
ALTER DATABASE anlaufstelle OWNER TO anlaufstelle_admin;
REASSIGN OWNED BY anlaufstelle TO anlaufstelle_admin; -- tables/sequences/views
-- 2. App role: ownership gone, only DML left.
GRANT USAGE ON SCHEMA public TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO anlaufstelle;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anlaufstelle;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anlaufstelle;
-- 3. Harden triggers (otherwise migration 0107 does it on the next deploy):
-- can be skipped if docker-migrate.sh runs immediately afterward.
```

Then run **§ 9.6.4 first** (green `check_db_roles` before `web` starts again),
then deploy as normal — `docker-migrate.sh` applies migration `0107` if not
already done — and verify with § 9.5.

#### 9.6.2 Case B — admin role missing, app role is not the bootstrap role

Here the app role can be stripped of its privileges in place. As the
**bootstrap superuser**, in the application DB (example names as in
`.env.staging.example`):

```sql
-- 0. Before: stop web, cron and maintenance (no app writes).
-- A password change does NOT terminate existing sessions — drop any
-- remaining connections of the old role explicitly:
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname = current_database AND usename = 'anlaufstelle' AND pid <> pg_backend_pid;
BEGIN; -- everything below is transactional; nothing is left half-done on error
-- 1. Create the missing admin/owner role. Password >= 32 characters.
CREATE ROLE anlaufstelle_admin WITH LOGIN NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB
    PASSWORD '<new-admin-password>';
GRANT ALL ON SCHEMA public TO anlaufstelle_admin;
-- 2. Transfer ownership of the database + all objects to the admin role.
ALTER DATABASE anlaufstelle OWNER TO anlaufstelle_admin;
GRANT ALL PRIVILEGES ON DATABASE anlaufstelle TO anlaufstelle_admin;
REASSIGN OWNED BY anlaufstelle TO anlaufstelle_admin;
-- 3. Strip the app role — THIS is the step a plain ownership move does not do.
ALTER ROLE anlaufstelle NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
ALTER ROLE anlaufstelle PASSWORD '<new-app-password>';
-- 4. App role: DML only.
GRANT CONNECT ON DATABASE anlaufstelle TO anlaufstelle;
GRANT USAGE ON SCHEMA public TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO anlaufstelle;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anlaufstelle;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anlaufstelle;
COMMIT;
```

> **Should `REASSIGN OWNED BY` abort here as well** (the role then owns
> extension or system objects): use the selective ownership loops from
> § 9.6.3 step 4 — they work for any role.

Afterwards set in the env file: `POSTGRES_PASSWORD` (new app password),
`POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD` (new admin role) **and
`POSTGRES_BOOTSTRAP_PASSWORD`** — the new staging stack requires all three
(`settings/prod.py` rejects a `maintenance` process without them, and the init
script checks them). All three >= 32 characters, no `change-me` prefix. Finish
with § 9.6.4.

#### 9.6.3 Case C — the app role is the bootstrap role

**The bootstrap role cannot be stripped of its privileges.** PostgreSQL rejects
this explicitly (verified on PostgreSQL 18.4):

```text
ALTER ROLE anlaufstelle NOSUPERUSER;
ERROR: permission denied to alter role
DETAIL: The bootstrap superuser must have the SUPERUSER attribute.
```

So `ALTER ROLE … NOSUPERUSER` on the existing app role is **not a viable path**
here — the application must move to a **new role name**. The old role stays a
superuser and takes exactly the position that `postgres` holds in the target
model: bootstrap and emergency maintenance, **never** runtime.

> **`REASSIGN OWNED BY` is NOT usable here.** On the bootstrap role it aborts,
> because that role owns system-required objects — verified on PostgreSQL 18.4
> with a bootstrap-initialized cluster and the Anlaufstelle object inventory:
>
> ```text
> REASSIGN OWNED BY anlaufstelle TO anlaufstelle_admin;
> ERROR: cannot reassign ownership of objects owned by role anlaufstelle
> because they are required by the database system
> ```
>
> The move therefore runs **selectively** via the catalog: application objects
> only, and explicitly **without** extension-owned objects
> (`pg_depend.deptype = 'e'` — e.g. the `gtrgm` type from `pg_trgm`). A blanket
> move does not remove the extension membership, but it does set a member
> managed by the extension to a different owner on its own authority; such
> objects belong to the extension, not to the rebuild. Indexes need no step of their
> own, they follow their table. Run the script with `psql` (`\gexec` is a psql
> command); it runs entirely in **one transaction** — `CREATE ROLE` and
> `ALTER DATABASE … OWNER TO` are transactional too (verified).

```sql
-- 0. Before: stop web, cron and maintenance.
-- A password change does NOT terminate existing sessions — drop any
-- remaining connections of the old bootstrap/app role explicitly:
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname = current_database AND usename = 'anlaufstelle' AND pid <> pg_backend_pid;

-- Variables. CAUTION: never put a comment after a \set. psql does not treat it
-- as a comment but as further arguments and appends them to the value — from
-- "anlaufstelle -- old role" you get the role name "anlaufstelle--oldrole",
-- and EVERY ownership loop below finds zero objects without anything failing.
\set ON_ERROR_STOP on
\set old_role anlaufstelle
\set app_role anlaufstelle_app
\set admin_role anlaufstelle_admin
\set dbname anlaufstelle_staging

-- Control output BEFORE the rebuild: exactly the four names must appear here,
-- with nothing appended. If anything differs, abort.
\echo 'old_role =[':old_role']'
\echo 'app_role =[':app_role']'
\echo 'admin_role=[':admin_role']'
\echo 'dbname =[':dbname']'

BEGIN;

-- 1. Create the app role if it is missing, then set attributes + password
-- unconditionally. This form is idempotent: it also runs through after an
-- aborted earlier attempt in which the role already exists.
SELECT format('CREATE ROLE %I',:'app_role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname =:'app_role')
\gexec
ALTER ROLE:"app_role" WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB
    PASSWORD '<new-app-password>';

-- 2. Admin/owner role, same pattern.
SELECT format('CREATE ROLE %I',:'admin_role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname =:'admin_role')
\gexec
ALTER ROLE:"admin_role" WITH LOGIN NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB
    PASSWORD '<new-admin-password>';
GRANT ALL ON SCHEMA public TO:"admin_role";

-- 3. Database ownership.
ALTER DATABASE:"dbname" OWNER TO:"admin_role";
GRANT ALL PRIVILEGES ON DATABASE:"dbname" TO:"admin_role";

-- 4. Move object ownership selectively (replacement for REASSIGN OWNED BY).
-- 4a) Relations: tables, sequences, views, materialized views, foreign tables.
SELECT format('ALTER %s %I.%I OWNER TO %I',
              CASE c.relkind WHEN 'S' THEN 'SEQUENCE'
                             WHEN 'v' THEN 'VIEW'
                             WHEN 'm' THEN 'MATERIALIZED VIEW'
                             WHEN 'f' THEN 'FOREIGN TABLE'
                             ELSE 'TABLE' END,
              n.nspname, c.relname,:'admin_role')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND c.relkind IN ('r','p','S','v','m','f')
   AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND n.nspname NOT LIKE 'pg\_toast%'
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
 ORDER BY c.relkind DESC
\gexec
-- 4b) Routines (functions, procedures, aggregates).
SELECT format('ALTER ROUTINE %I.%I(%s) OWNER TO %I',
              n.nspname, p.proname, pg_get_function_identity_arguments(p.oid),:'admin_role')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE p.proowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
\gexec
-- 4c) Own types and domains (table row types follow their table).
SELECT format('ALTER %s %I.%I OWNER TO %I',
              CASE t.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
              n.nspname, t.typname,:'admin_role')
  FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
 WHERE t.typowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND t.typtype IN ('e','d','c','r','b') AND t.typrelid = 0 AND t.typelem = 0
   AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
\gexec
-- 4d) Own schemas. From PG15 on, 'public' belongs to pg_database_owner and
-- therefore follows the new DB owner automatically — nothing to do then.
SELECT format('ALTER SCHEMA %I OWNER TO %I', n.nspname,:'admin_role')
  FROM pg_namespace n
 WHERE n.nspowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
\gexec

-- 5. New app role: DML only.
GRANT CONNECT ON DATABASE:"dbname" TO:"app_role";
GRANT USAGE ON SCHEMA public TO:"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE:"admin_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO:"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE:"admin_role" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO:"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO:"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO:"app_role";

-- 6. The old bootstrap role is now a pure bootstrap/emergency role:
-- set a fresh, long password and never use it as runtime again.
ALTER ROLE:"old_role" PASSWORD '<new-bootstrap-password>';
COMMIT;
```

**Final inventory right afterwards — across all four moved classes**, not just
relations. For the four moved classes `0` is expected. The fifth row reports the
**extension members** that the rebuild deliberately leaves alone: there a value
**> 0 is the normal case**, not an error.

```sql
SELECT 'relations' AS class, count(*) AS with_old_role
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND n.nspname NOT LIKE 'pg\_toast%'
   AND c.relkind IN ('r','p','S','v','m','f')
   AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
UNION ALL
SELECT 'routines', count(*)
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
UNION ALL
SELECT 'types', count(*)
  FROM pg_type ty JOIN pg_namespace n ON n.oid = ty.typnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND ty.typtype IN ('e','d','c','r','b') AND ty.typrelid = 0 AND ty.typelem = 0
   AND ty.typowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_type'::regclass AND d.objid = ty.oid AND d.deptype = 'e')
UNION ALL
SELECT 'schemas', count(*)
  FROM pg_namespace n
 WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
   AND n.nspowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
UNION ALL
-- Extension members are DELIBERATELY left untouched by the rebuild (see above).
-- They are listed separately so the inventory does not falsely report
-- "everything with the admin role": this value is expected to be > 0.
SELECT 'extension objects (by design)', count(*)
  FROM pg_depend d
  LEFT JOIN pg_class c ON d.classid = 'pg_class'::regclass AND c.oid = d.objid
  LEFT JOIN pg_proc p ON d.classid = 'pg_proc'::regclass AND p.oid = d.objid
  LEFT JOIN pg_type ty ON d.classid = 'pg_type'::regclass AND ty.oid = d.objid
 WHERE d.deptype = 'e'
   AND COALESCE(c.relowner, p.proowner, ty.typowner) = (SELECT oid FROM pg_roles WHERE rolname =:'old_role');
```

> **What the extension row means for maintenance.** The extension and all its
> objects remain owned by the **old bootstrap role** — `pg_extension` still
> records them there. Measured on PostgreSQL 18.4 after a case C rebuild:
> `extension_owner = anlaufstelle`, 34 extension objects still with that role,
> all application objects with the admin role. Consequence: the admin role
> (`NOSUPERUSER BYPASSRLS`) cannot perform **any extension maintenance** —
> `ALTER EXTENSION pg_trgm SET SCHEMA …` and `DROP EXTENSION pg_trgm` both fail
> with `must be owner of extension pg_trgm`. PostgreSQL has **no**
> `ALTER EXTENSION … OWNER TO` (syntax error), so ownership cannot be
> transferred cleanly after the fact.
>
> **In practice:** a later `ALTER EXTENSION … UPDATE` (e.g. after a major
> upgrade) requires a **superuser** — the old bootstrap role or the `postgres`
> role created in § 9.6.3. Both are kept anyway; their passwords therefore
> belong in the documentation and must not be lost. As a superuser the command
> runs through (verified). Nothing changes for day-to-day operation: the app
> role keeps using the extension (`similarity()` checked), only administering
> the extension stays with the superuser.
> **A precondition that is not technically enforced:** the four loops work on
> the snapshot of their respective `SELECT`. An object that a **parallel**
> session creates under the old role *after* that query is not in the command
> list and stays with the old role. Stopping the services and
> `pg_terminate_backend` above do not prevent a **reconnect**. For the
> maintenance window this means: **no concurrent DDL.** To enforce it, lock the
> old role beforehand in its **own, committed** transaction — inside the main
> block it has no effect, because other sessions only see the change after the
> `COMMIT`:
>
> ```sql
> ALTER ROLE anlaufstelle NOLOGIN; -- before the main block, own transaction
> --... main block...
> ALTER ROLE anlaufstelle LOGIN; -- release again afterwards
> ```
>
> The inventory above has the final word in any case: if it shows `> 0`
> anywhere, simply run the main block again — it is idempotent.

Then switch the env file over — the part that is easy to forget:

| Variable | New value |
|---|---|
| `POSTGRES_USER` | `anlaufstelle_app` (**changed**) |
| `POSTGRES_PASSWORD` | `<new-app-password>` |
| `POSTGRES_ADMIN_USER` | `anlaufstelle_admin` |
| `POSTGRES_ADMIN_PASSWORD` | `<new-admin-password>` |
| `POSTGRES_BOOTSTRAP_PASSWORD` | `<new-bootstrap-password>` (password of the **old** role) |

> **Why not keep the old name?** Because that very name is the bootstrap role
> and stays `SUPERUSER` forever. If the runtime kept using it, the fix would be
> ineffective — `FORCE ROW LEVEL SECURITY` does not apply to superusers. From
> #2086 on, the staging
> stack's `web` healthcheck would notice and never report the container
> `healthy`.

> **The `postgres` role is not optional after case C.** A cluster created this
> way has no role of that name. For the `pg_isready` call in the `db`
> healthcheck that would not matter (it does not authenticate) — but the
> shipped rotation tool
> [`scripts/ops/rotate-db-passwords.sh`](../../scripts/ops/rotate-db-passwords.sh)
> connects hard-coded as `-U postgres` and rotates hard-coded
> `ALTER ROLE "postgres"`. Without the role it fails to connect; created merely
> pro forma, it rotates the password of a role nobody uses — and **not** the one
> of the old bootstrap role that the env table above points at.
>
> Therefore, right after case C:
>
> ```sql
> CREATE ROLE postgres WITH LOGIN SUPERUSER PASSWORD '<new-bootstrap-password>';
> ```
>
> with the **same** password as the old bootstrap role, so that
> `POSTGRES_BOOTSTRAP_PASSWORD` is correct for both. The rotation tool then
> rotates `postgres`; the old bootstrap role's password is untouched by it and
> must be carried along **by hand** on every rotation:
>
> ```sql
> ALTER ROLE anlaufstelle PASSWORD '<new-bootstrap-password>';
> ```
>
> Reworking the tool for a configurable bootstrap role name is deliberately not
> part of #2086 — there
> is a follow-up ticket for it. The clean way out remains dump/restore into a
> fresh volume: there the bootstrap role is called `postgres` again.

#### 9.6.4 Completion (mandatory for all cases)

The rebuild only counts as finished once the role gate is green — **before**
`web` starts again:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles
# or staging:
docker compose -f docker-compose.staging.yml --env-file .env.staging \
    run --rm maintenance check_db_roles
# expected (role labels are German, from the command's own output):
# App-Rolle rolsuper=False, rolbypassrls=False
# Admin-Rolle rolsuper=False, rolbypassrls=True (exit code 0)
```

A non-zero exit means the rebuild is incomplete — do **not** start `web`, work
through the message instead. Only then deploy; the migrate one-shot runs the
same gate again, and on staging the `web` healthcheck additionally verifies the
actual connection role continuously.

Since #2086 the gate no
longer only checks that the app role is **not** the owner, but that the admin
role **is**. An object that still belongs to the old bootstrap role after a
half-completed rebuild is therefore caught — before, exactly that state passed
green.

**Fail-loud gate (Refs #1026):**
Since A1.4, `check_db_roles` **additionally checks ownership on top of the attribute check**.
As long as the move above has not run yet (the app role is still the DB/table
owner), the gate reports `FAIL … Ownership-Umzug … § 9.6` (German, from the check's own message: "ownership move … § 9.6") and aborts with **exit 1**
— `docker-migrate.sh` stops the deploy before new web replicas go live.
This prevents the earlier silent fail-open, where the deploy went green despite
still-app-owned tables (the attribute profile of app/admin alone stays the
same through the ownership move). Once the move has been carried out (admin is
the owner), the gate is green again.

> **Order for a pending role move (Refs
> #1738):** First run the
> ownership move above, **then** deploy. The `0129`–`0131` attachment chain
> needs both — owner rights for the DDL (`DROP`/`CREATE POLICY`, `ALTER TABLE`)
> and BYPASSRLS for the `facility_id` backfill in `0130`. If `migrate` still
> runs under the app role, `check_db_roles` already stops the deploy (exit 1); a
> half-applied state between `0130` and `0131` therefore cannot occur.

> **Note on `normalize_db_ownership`:** The one-shot migrate job still calls
> `normalize_db_ownership` — a **no-op** in the new model, because `migrate` runs as
> the admin, which equals the DB owner (`current_user == db_owner`). It remains as an
> idempotent safety net.

---

## 10. Invite Token Hygiene

The invite flow ([`src/core/services/security/invite.py`](../../src/core/services/security/invite.py))
uses its own `invite_token_generator` — **no** persistent
token table. The setup link uses the same confirmation view as the
password reset, but is kept separate from it by its own `key_salt`, and is bound to
`INVITE_TOKEN_TIMEOUT` (default: 3 days). After it expires, the token
is silently rejected by the signer; there are **no** database rows that
need cleaning up.

**What does need cleaning up instead:** user accounts created via invite
whose recipient never redeemed the link — these accounts
stay around with an unusable password.

### 10.1 Identifying Stale Invite Users

```sql
-- All user accounts that have never logged in and have not set a usable
-- password, older than 7 days. A management command deliberately does
-- not exist — manual review before deletion is mandatory
-- (GDPR Art. 6 / 17).
SELECT id, username, email, date_joined, facility_id
FROM core_user
WHERE last_login IS NULL
  AND NOT (password LIKE 'pbkdf2_%') -- unusable = set_unusable_password
  AND date_joined < now - interval '7 days'
ORDER BY date_joined;
```

### 10.2 Resending the Setup Link

Regular way: as `facility_admin`, open **Administration > Access**, select the
account, and under "Security actions" click **Resend invitation**. This path
is limited to the manager's own facility, requires sudo
re-authentication, and writes an attributed `USER_INVITE_SENT` audit entry. It is
only available for an account that is not yet fully set up and has an email
address.

Only if the application UI is unreachable in an emergency does the direct
technical dispatch remain as a fallback:

```bash
# Django shell: send a new invite link to an existing user
docker compose -f docker-compose.prod.yml exec web \
  python manage.py shell -c "
from core.models import User
from core.services.security import send_invite_email
send_invite_email(User.objects.get(username='<username>'))
"
```

The shell fallback has neither the facility/status guard nor the
actor attribution of the application UI. Check the target account and facility
beforehand and document the intervention separately.

### 10.3 Deleting Orphaned Accounts

In regular operation, an orphaned account is deactivated under
**Administration > Access**. A permanent deletion is not a normal CRUD step,
because of possible references and audit attribution. Only after review and
consultation with facility leadership does the technical special path remain:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py shell -c "
from core.models import User
User.objects.filter(username='<username>').delete
"
```

**Cron recommendation:** the weekly audit as a plain report query (see section
[3.2](#32-cron-sidecar-standard)) — no automatic deletion, since an
undelivered invite can also be a mail problem.

Refs #528.

---

## 11. Statistics Materialized View

The materialized view `core_statistics_event_flat`
pre-aggregates all event facts for the statistics dashboard. The refresh runs via a
management command and uses `REFRESH MATERIALIZED VIEW CONCURRENTLY`, so
running readers are not blocked
([`refresh_statistics_view.py`](../../src/core/management/commands/refresh_statistics_view.py)).

### 11.1 Manual Refresh

```bash
# Concurrent refresh (default — non-blocking)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py refresh_statistics_view

# Force a blocking refresh (fallback without the UNIQUE index)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py refresh_statistics_view --no-concurrent
```

If `CONCURRENTLY` fails (e.g. because the UNIQUE index is missing after
a schema migration), the command logs a warning and repeats
the refresh without `CONCURRENTLY` — the view stays consistent, but
readers block for the duration of the refresh.

### 11.2 Cron Recommendation

Hourly at minute 15 (see [3.2](#32-cron-sidecar-standard)). With high
event frequency, this can be tightened to every 15 minutes; with low
traffic, every 6 hours is also sufficient. The dashboard tolerates slight staleness.

### 11.3 Distinction from Snapshots

| Artifact | Purpose | Command | Frequency |
|----------|-------|--------|----------|
| `core_statistics_event_flat` (MV) | Current runtime aggregates for the dashboard | `refresh_statistics_view` | Hourly |
| `core_statisticssnapshot` (table) | Persistent monthly aggregates (outlives event retention) | `create_statistics_snapshots` | Monthly |

Both jobs are independent — snapshots stay correct even without the MV,
and the MV can be refreshed even without snapshots.

### 11.4 Health Check

```sql
-- Look up the last refresh time
SELECT schemaname, matviewname, last_refresh
FROM pg_stat_user_tables
JOIN pg_matviews ON relname = matviewname
WHERE matviewname = 'core_statistics_event_flat';

-- Check the row count (should roughly match the core_event count)
SELECT count(*) FROM core_statistics_event_flat;
```

Refs #544.

---

## 12. Trust Boundary (Refs #841)

### 12.1 SECURE_PROXY_SSL_HEADER

`settings/prod.py:48` sets:

```python
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

This lets Django recognize HTTPS even behind the Caddy proxy. Precondition: **Caddy strips any incoming `X-Forwarded-Proto` header from the client and sets it itself** to the actual connection state.

**Important:** If the app is exposed **directly** (without a reverse proxy), this setting MUST be removed. Otherwise an attacker can set `X-Forwarded-Proto: https` themselves — `request.is_secure()` then returns `True` even though the connection is plaintext; HSTS/secure-cookie logic is defeated.

In `Caddyfile` (and the variants `Caddyfile.dev`, `Caddyfile.demo`, `Caddyfile.staging`), `X-Forwarded-Proto` is **not set explicitly** — the bare directive is enough:

```
reverse_proxy web:8000
```

Caddy's `reverse_proxy` sets `X-Forwarded-Proto` (as well as `X-Forwarded-For`/`X-Forwarded-Host`) itself by default. `SECURE_PROXY_SSL_HEADER` therefore relies on this Caddy default behavior, **not** on an explicit `header_up` directive. (An additional `header_up X-Forwarded-Proto {scheme}` would be redundant.)

**What "by default" depends on (Refs #1963):** Whether Caddy adopts an incoming `X-Forwarded-*` value or discards it is decided solely by the `trusted_proxies` list (§ 12.2). If the request comes from a peer that is **not** listed there — the normal case, Caddy is the edge — Caddy discards the supplied value and sets all three headers itself; `X-Forwarded-For` then contains exactly **one** entry, namely the actual peer IP. If the peer, on the other hand, is in `trusted_proxies`, Caddy keeps the existing value: for `X-Forwarded-For`, it **appends** the peer IP on the right; for `X-Forwarded-Proto`/`-Host`, it adopts the upstream value unchanged.

For `SECURE_PROXY_SSL_HEADER`, this means: the assumption holds as long as no upstream proxy is listed in `trusted_proxies` — and otherwise only if that proxy correctly sets `X-Forwarded-Proto` in turn. Whoever populates the list thereby extends the chain of trust to that upstream proxy.

### 12.2 TRUSTED_PROXY_HOPS and TRUSTED_PROXY_CIDRS

In addition, `.env` sets (default `1`):

```
TRUSTED_PROXY_HOPS=1
```

Explained in `.env.example`. The value counts from the right: [`signals.audit.get_client_ip`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/audit.py) takes the `TRUSTED_PROXY_HOPS`-th `X-Forwarded-For` entry from the right, and falls back to `REMOTE_ADDR` if the header has fewer entries.

**`hops > 1` has no effect without configured proxy trust** (Refs #1963). It follows from § 12.1: without an entry in `trusted_proxies`, the app **always** receives exactly **one** `X-Forwarded-For` entry. `TRUSTED_PROXY_HOPS=2` then finds no second entry and falls back to `REMOTE_ADDR` — which is Caddy's container IP, i.e. **a single value for every client**: one shared rate-limit bucket, one shared lockout axis (N9), one audit IP, and one maintenance-allowlist decision for the entire internet (the same class of bug as security finding N1). Conversely: if the hop count is higher than the number of proxies that Caddy actually trusts, the resolver picks a client-controlled entry — the spoofing case from #1375.

The two values therefore belong together. In front of a CDN/load balancer:

```
# Ranges of the upstream proxy — space-separated, CIDR or a bare address
TRUSTED_PROXY_CIDRS=203.0.113.0/24 2001:db8::/32
TRUSTED_PROXY_HOPS=2
```

The same variable fills both the Caddy directive `trusted_proxies static` in the global `servers` block of the Caddyfiles (passed through in the `caddy` service of the compose files) **and** `settings.TRUSTED_PROXY_CIDRS`. This gives exactly one source; the edge and the application cannot drift apart.

The combination is backed by two system checks in [`core/checks.py`](https://github.com/anlaufstelle/app/blob/main/src/core/checks.py):

| ID | Trigger | Meaning |
|----|-----------|-----------|
| `core.E001` | `TRUSTED_PROXY_HOPS` negative/not an integer | Startup aborts |
| `core.W001` | `hops >= 3` | Heuristic: more than CDN + Caddy is rarely real (unchanged since #1375) |
| `core.W003` | `hops > 1`, but `TRUSTED_PROXY_CIDRS` empty | **new** — the hop count has no effect (see above) |
| `core.E003` | An entry in `TRUSTED_PROXY_CIDRS` is not a valid IP range for Caddy | **new** — startup aborts; otherwise **Caddy** refuses to start, and only after the `web` container has already been reported healthy |

`core.W001` keeps its meaning as published with #1375; the two checks added with #1963 get their own IDs, so an existing runbook table doesn't silently start showing something else.

**Syntax (`core.E003`):** The value is parsed by **Caddy**, not by the application — the Caddyfile parser splits directive arguments only on spaces/tabs. A comma is therefore not a separator (`203.0.113.0/24,198.51.100.0/24` is, for Caddy, ONE unusable range), and Caddy does not understand netmasks (`192.168.0.0/255.255.255.0`). The check mirrors Caddy's parser exactly (Go's `netip.ParsePrefix`, including leading zeros like `/032`) — whatever passes here starts the reverse proxy; whatever fails here would have killed it.

`trusted_proxies_strict` is additionally present in every global `servers` block: since Caddy 2.8, Caddy then reads the client IP headers from the **right** instead of the left, thereby taking the entry set by the proxy instead of the client-controlled one — the same reading direction that `get_client_ip` uses. The pinned image is `caddy:2-alpine` at version 2.11.4 (digest in `docker-compose.prod.yml`), and the directive is present there.

### 12.3 EXTERNAL_ORIGIN (Refs #1967)

The production check only forbids the match-all entry `*` in `ALLOWED_HOSTS`; subdomain patterns like `.example.org` remain explicitly allowed (§ `settings/prod.py`). If, in such a deployment, another allowed host points at the application, a token link would go out with **that** host — the request's `Host` header would then be part of the security boundary.

The three productive link sources — the invitation setup link ([`services/security/invite.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/invite.py)), the password reset email ([`views/auth.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py)), and the unlock email ([`views/lockout_recovery.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/lockout_recovery.py)) — therefore no longer read the request. Scheme and host come exclusively from `EXTERNAL_ORIGIN` ([`services/security/origin.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/origin.py)):

```
EXTERNAL_ORIGIN=https://anlaufstelle.example.de
```

- **Format:** scheme (`http`/`https`) + host + optional port. No path, no trailing slash, no query. An invalid value aborts startup, instead of surfacing in an already-sent email.
- **Derivation:** If `ALLOWED_HOSTS` carries exactly **one** public hostname, `https://<host>` is derived from it (the same pattern as `OTP_WEBAUTHN_RP_ID`) — the normal case of "one domain behind Caddy" does not need the variable.
- **Loopback doesn't count:** `localhost`, `127.0.0.1`, and `::1` are in `ALLOWED_HOSTS` only so the container healthcheck (`http://localhost:8000/health/`) doesn't get a `DisallowedHost` 400 — the bundled deploy templates therefore list them alongside the domain. They are disregarded for the derivation; they would never be a candidate as the basis of a token link anyway.
- **If `web` never becomes healthy, there are exactly two causes** (Refs #2077): either the loopback entry is missing from `ALLOWED_HOSTS` — the healthcheck then gets a `DisallowedHost` 400 — or `SECURE_REDIRECT_EXEMPT` is not in effect: otherwise `SECURE_SSL_REDIRECT=True` answers the healthcheck call `http://localhost:8000/health/` with a 301 to `https://`, and the healthcheck client cannot speak TLS locally. `web` then never turns healthy and `caddy` (`condition: service_healthy`) never starts at all. Both halves have been set in `settings/prod.py` and the `.env*.example` templates since #2077; on an older, hand-maintained `.env` they must be added. Quick check inside the container: `python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/').status)"` must return `200`, not 301 or 400.
- **Fail-closed:** With several public hosts, a pure wildcard config, or only loopback entries, there is no unambiguous answer; prod then does not start, and names `EXTERNAL_ORIGIN` in the abort message. Deliberately no guessing: a wrongly guessed origin makes every token link unusable.
- **The `ALLOWED_HOSTS` check itself was not tightened** — subdomain patterns remain allowed, they are now simply irrelevant for link construction.

On a domain change, `EXTERNAL_ORIGIN` must be updated along with it: already-sent links keep pointing at the old address and expire after their validity period (`DJANGO_PASSWORD_RESET_TIMEOUT` or `DJANGO_INVITE_TOKEN_TIMEOUT`).

---

## 13. PostgreSQL Major Upgrade (16 → 18)

Refs #1039. PostgreSQL major versions have **incompatible data directories** — starting the new image in place on the old volume is impossible. Chosen and locally rehearsed path: **dump → fresh PG18 cluster → restore**. (`pg_upgrade` was rejected: the Alpine image only contains one binary major version, and PG18's `initdb` enables data checksums by default — a 16-series cluster without checksums would first have to be converted for that.)

**Changed image layout from 18 on:** `PGDATA` now lives under `/var/lib/postgresql/18/docker`, and the image declares `VOLUME /var/lib/postgresql`. Since #1039, the compose files therefore mount the parent directory (`…:/var/lib/postgresql` instead of `…/data`). If the new build is accidentally started against an old 16-series volume, the entrypoint aborts **in a controlled way, with no data loss** ("there appears to be PostgreSQL data in: /var/lib/postgresql" + a hint).

### 13.1 Prod/Staging — Preserving Data (Maintenance Window)

Staging is identical, using `docker-compose.staging.yml`, `.env.staging`, and the volume `*_pgdata_staging`. The order is safety-critical: **roles before data** — the init script creates the app/admin role in the fresh cluster before the restore re-establishes ownership/grants (ADR-020). For staging this only holds from #2086 onwards: before that the stack did not mount `deploy/postgres-init` at all (§ 1.4).

```bash
# 0. Activate maintenance mode (§ 6) + stop web — DB stays reachable for the dump
docker compose -f docker-compose.prod.yml stop web

# 1. Final encrypted backup (§ 1.1) + an unencrypted working dump.
# Dump as the admin role (BYPASSRLS) over the container socket — like backup.sh../scripts/ops/backup.sh
docker compose -f docker-compose.prod.yml exec -T db \
    sh -c 'pg_dump -U "$POSTGRES_ADMIN_USER" -Fc -Z 6 "$POSTGRES_DB"' > /tmp/upgrade-pg16.dump

# 2. Stop the DB, pull a volume tarball as a byte-exact rollback anchor
# (check the volume name: docker volume ls | grep pgdata)
docker compose -f docker-compose.prod.yml stop db
docker run --rm -v anlaufstelle_pgdata:/from -v "$PWD":/to alpine \
    tar czf /to/pgdata-pg16.tar.gz -C /from.

# 3. Remove the old DB container + volume — only AFTER steps 1+2!
docker compose -f docker-compose.prod.yml rm -f db
docker volume rm anlaufstelle_pgdata

# 4. Bring in the new release state (postgres:18-alpine + new mount)
git pull # or the release package

# 5. Bring up only the DB: a fresh initdb (PG 18, checksums on) + the init script
# creates the app/admin role. The log must be error-free.
docker compose -f docker-compose.prod.yml up -d db
docker compose -f docker-compose.prod.yml logs db | grep -icE 'error|fatal' # 0

# 6. Restore as the bootstrap superuser via the container socket (bypasses RLS/FORCE-RLS,
# like scripts/ops/restore-drill.sh; --clean/--if-exists is safe to rerun)
docker compose -f docker-compose.prod.yml exec -T db \
    sh -c 'pg_restore -U postgres -d "$POSTGRES_DB" --clean --if-exists' < /tmp/upgrade-pg16.dump

# 7. Verify: role gate, RLS active, spot checks
docker compose -f docker-compose.prod.yml run --rm -T maintenance check_db_roles # exit 0
docker compose -f docker-compose.prod.yml exec -T db sh -c \
    'psql -U postgres -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity"' # > 0

# 8. Migrations as a one-shot (usually a no-op) + bring up the stack + health (§ 1.2/1.3)
docker compose -f docker-compose.prod.yml run --rm --entrypoint=/app/docker-migrate.sh maintenance
docker compose -f docker-compose.prod.yml up -d web caddy
curl -sf https://$DOMAIN/health/ | python3 -m json.tool

# 9. Turn off maintenance mode. The working dump contains PLAINTEXT data — delete
# it immediately; discard the tarball after a grace period (e.g. 1 week).
rm /tmp/upgrade-pg16.dump
```

If `pg_restore` reports a warning on `REFRESH MATERIALIZED VIEW core_statistics_event_flat`, the data is still complete — afterwards, run the MV refresh once (§ 11).

### 13.2 Rollback

Primarily via the volume tarball (the exact 16-series byte state), alternatively the encrypted backup (§ 6):

```bash
# 1. Roll the compose state back: postgres:16-alpine + mount …:/var/lib/postgresql/data
git checkout <previous-state> -- docker-compose.prod.yml

# 2. Remove the 18-series volume, restore the 16-series bytes
docker compose -f docker-compose.prod.yml rm -f db
docker volume rm anlaufstelle_pgdata && docker volume create anlaufstelle_pgdata
docker run --rm -v anlaufstelle_pgdata:/to -v "$PWD":/from alpine \
    tar xzf /from/pgdata-pg16.tar.gz -C /to

# 3. Bring up the stack + health
docker compose -f docker-compose.prod.yml up -d
```

### 13.3 dev.anlaufstelle.app — Data Is Disposable

`pgdata_dev` only holds demo seed data (Refs #1039): no restore needed.

```bash
cd /opt/anlaufstelle
docker compose -f docker-compose.dev.yml --env-file .env.dev stop db web
docker compose -f docker-compose.dev.yml --env-file .env.dev rm -f db
docker volume rm anlaufstelle_pgdata_dev
# Deploy the new state (make deploy-dev from the operator machine): fresh
# 18-series cluster + roles via the init script, migrate job, stack start.
# Afterward, demo data: make dev-seed SEED_ARGS="--flush --scale=medium"
```

The complete path (init roles before data, superuser restore, `check_db_roles` exit 0, FORCE-RLS active, app role without bypass, MV refresh, rollback to 16) was rehearsed locally in Docker as part of #1039.

### 13.4 Future Major Upgrades (18 → 19 and Beyond) — Forward Reference

> **Placeholder (Refs #1039).** The **dump → fresh cluster → restore** path described above is the **generic pattern for every** PostgreSQL major jump, not just 16→18. Future upgrades (18→19 etc.) follow the same steps from §13.1/§13.2; essentially only the version numbers, the `postgres:<N>-alpine` image, and possibly the `PGDATA` mount path need adjusting, should a new image layout move the directory again (as the move to `/var/lib/postgresql/18/docker` did for 18).

Before the next major jump, check:

- **Release notes of the target major** for incompatible changes (data format, removed functions, changed defaults — e.g. data checksums by default for 18).
- **Image layout/`VOLUME` path** of the new `postgres:<N>-alpine` — if it changes, update the mounts in all compose files (`prod`/`staging`/`dev`), analogous to #1039.
- **Availability of `pg_dump`/`pg_restore` of the target major** in the image (cross-version dump compatibility).

This note is deliberately kept brief; the detailed, locally rehearsed procedure stays in §13.1–§13.3 and will be made concrete under a new issue when the next actual upgrade happens.

---

## Quick Reference

```text
Create backup./scripts/ops/backup.sh
Restore backup./scripts/ops/restore.sh <file.sql.gz.enc>
Health check curl -sf https://$DOMAIN/health/
Container status docker compose -f docker-compose.prod.yml ps
Live logs docker compose -f docker-compose.prod.yml logs -f web
Migration status docker compose -f docker-compose.prod.yml exec web python manage.py showmigrations
Django deploy check docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
Retention dry run docker compose -f docker-compose.prod.yml exec web python manage.py enforce_retention --dry-run
Snapshot dry run docker compose -f docker-compose.prod.yml exec web python manage.py create_statistics_snapshots --dry-run
MV refresh docker compose -f docker-compose.prod.yml exec web python manage.py refresh_statistics_view
Check RLS session var psql -c "SELECT current_setting('app.current_facility_id', true);"
Stop the stack docker compose -f docker-compose.prod.yml down
Start the stack docker compose -f docker-compose.prod.yml up -d
Regenerate lock files make deps-lock
Check lock-file drift make deps-check
```

<!-- translation-source: docs/ops-runbook.md -->
<!-- translation-version: v0.96.0 -->
<!-- translation-date: 2026-09-10 -->
<!-- source-hash: c1e0bac6d3238cd7ed60a1ff5b123110b214023e -->
