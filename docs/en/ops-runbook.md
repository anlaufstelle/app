> This is the English translation of [ops-runbook.md](../ops-runbook.md).
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
# Source: .env.example, docs/coolify-deployment.md.
for var in POSTGRES_BOOTSTRAP_PASSWORD POSTGRES_ADMIN_USER POSTGRES_ADMIN_PASSWORD; do
    val=$(grep -E "^${var}=" .env | cut -d= -f2-)
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

> **⚠️ Operator action for updates from < 0.23:** Since
> #794, the stack ships the background jobs
> as a **`cron` sidecar** ([§3.2](#32-cron-sidecar-standard)) — previously they had to be
> set up via a host crontab. Anyone who still has the entries from the old §3.2 in the host
> cron will now run them **twice**. Remove those entries before the sidecar's
> first start; the **only exception is the backup**, which
> continues to run host-side ([§3.3](#33-host-cron-backup--remaining-host-jobs)).
> Migration steps: [§3.3](#33-host-cron-backup--remaining-host-jobs).

> **⚠️ The backup still does NOT run automatically.** The sidecar covers every job
> that runs as a `manage.py` command inside the container. `backup.sh`, by contrast, runs
> `docker compose exec` (pg_dump in the db container, tar in the web container) and
> would need the Docker socket inside the sidecar for that — which would be root-equivalent on the
> host and would undo the runtime hardening of the entire stack (`cap_drop: ALL`,
> `no-new-privileges`, `read_only`). The backup therefore stays **one**
> host crontab entry, see [§3.3](#33-host-cron-backup--remaining-host-jobs).
> Without it, an installation has **no backup** — with no error message, just silence.
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
| `cleanup_orphan_storage_files` | Daily 04:30 (after snapshots) | Sidecar | Delete orphaned `.enc` files with no `Attachment` **and no open `ImportJob` record** (owner-independent: event, person, and case attachments; CSV import uploads under `<facility>/imports/` are protected via `ImportJob.storage_filename` while the run is open — `commit_import`/`abort_import` clear the field, finished runs do not protect it any longer); MUST run as the admin DB role (BYPASSRLS), otherwise fail-loud — without the bypass the run would also see 0 `ImportJob` rows and would delete the open uploads of every facility (Refs #662, #1554, #1738, #1060) |
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
breach detection, and MV refresh run without any further setup.

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
`.env` must have `POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD` set.

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
container does not prove that the jobs *work*. The proof of effectiveness
comes from the app itself: every job writes a
`*_RUN_COMPLETED` marker to the AuditLog after a successful run, which the compliance dashboard
(`/system/compliance/`, category "Background jobs") and `stale_jobs` in the
[`/health/` payload](../monitoring-guide.md) (German) evaluate. In addition, host
monitoring should alert on failures in the `cron` logs — in particular
on `verify_audit_chain`, which ends with exit code != 0 on suspected tampering.

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

```cron
# Anlaufstelle - Host cron (backup + hygiene). Everything else is handled by the
# cron sidecar from §3.2 - do NOT set these entries up twice!
# Set up in /etc/crontab or via `crontab -e` on the host

# Backup (daily 02:00, before retention in the sidecar at 03:00)
0 2 * * * cd /opt/anlaufstelle && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1

# Invite token audit (weekly Sun 05:00, details see section 10)
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health check (every 5 minutes)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

#### Migrating from the Old Host Crontab (Operator Action)

Installations from before release 0.23 have all jobs in the host cron. After the update,
the jobs that the sidecar has taken over would otherwise run twice — mostly
harmless in domain terms (the commands are idempotent), but it produces duplicate
breach webhooks, duplicate audit markers, and unnecessary load. Therefore:

```bash
# 1. Back up the current crontab
crontab -l > ~/crontab.vor-0.23.bak

# 2. REMOVE all lines that call one of these commands:
#      enforce_retention · verify_audit_chain · create_statistics_snapshots
#      cleanup_orphan_storage_files · detect_breaches · refresh_statistics_view
crontab -e

# 3. Leave the backup, invite-audit, and health-check lines in place (see block above).

# 4. Update the stack and bring up the sidecar
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

# 5. Verify: sidecar is running, schedule loaded
docker compose -f docker-compose.prod.yml logs cron | head -30
```

The next day, check in the compliance dashboard (`/system/compliance/`, "Background jobs")
that the markers are fresh — that's the evidence that the jobs
actually ran through.

<details>
<summary>Old host crontab (complete) — only for setups WITHOUT the sidecar</summary>

Relevant if the stack is run without `docker-compose.prod.yml` (e.g.
Coolify, k8s) and therefore no `cron` service exists.

```cron
# Anlaufstelle - Operational cron jobs (legacy: without the cron sidecar)
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
# .enc files with no Attachment record - owner-independent, i.e.
# event, person, and case attachments, Refs #662/#1554/#1738)
# MUST run with the admin DB role (BYPASSRLS), otherwise the command aborts
# fail-loud (§9 DB role model): as an RLS-filtered app role, the run would see
# 0 registered attachments and would delete EVERY .enc file as an orphan.
30 4 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance cleanup_orphan_storage_files >> /var/log/anlaufstelle-orphan-cleanup.log 2>&1

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

**Note the order:** Backup (02:00) → retention (03:00) → audit chain verification (03:30) → snapshots (04:00, monthly) → breach detection/MV refresh (hourly). The backup must run before retention, so that deleted data is included in the backup. The audit chain verification runs after retention, so that the checkpoint boundaries freshly written by `prune_auditlog` are already in the chain. The MV refresh uses `CONCURRENTLY` and does not block running readers. The sidecar schedule in `deploy/crontab` reflects exactly this order — which is why the `TZ` setting from [§3.2](#32-cron-sidecar-standard) is operationally relevant, not cosmetic.

### 3.4 Dev (systemd Timers)

On `dev.anlaufstelle.app`, the jobs do **not** run via a host crontab, but as
systemd timers, installed by `dev-ops/deploy/install-timers.sh` (dev-only) —
invoked on **every** deploy by `dev-ops/deploy/deploy-dev.sh` (dev-only)
(idempotent). The dev stack runs via `docker-compose.dev.yml` and deliberately
does **not** get the `cron` sidecar from [§3.2](#32-cron-sidecar-standard):
running both in parallel would execute every job twice, and here the timers are
additionally the test case for the host scheduler path that Coolify/k8s setups
without our compose file use.

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

# Clean up orphaned dynamic field values (Refs #1725)
# Removes data_json keys whose slug is no longer attached to the document type,
# for persons/cases/events. Assigned (including encrypted) values
# are left untouched; records without a type are only counted. MUST run with the
# admin DB role (BYPASSRLS) — otherwise fail-loud (§ 9 DB role model).
#
# Orphaned FILE markers are deliberately SKIPPED for ALL three entities
# and reported separately as "file orphan(s) skipped", NOT deleted: the
# data_json marker is the only bridge to the encrypted attachment
# row. (Until M24 the rule effectively only affected events - file fields were
# locked out of person/case types; since Refs #1738, persons and
# cases can also carry FILE orphans and are spared in the same way.)
# This file cleanup runs via the attachment deletion paths, or
# cleanup_orphan_storage_files (after the attachment row is deleted), not
# via this command - the skipped count therefore does not count as
# completed file minimization.
docker compose -f docker-compose.prod.yml run --rm maintenance \
 purge_orphan_field_data --dry-run # preview
docker compose -f docker-compose.prod.yml run --rm maintenance \
 purge_orphan_field_data # execute

# Facility export (Refs #1060, M4 / GDPR Art. 20): a facility's complete domain
# data as a versioned JSON (schema v1, reference
# docs/facility-export-schema.md). MUST run with the admin DB role
# (BYPASSRLS) - otherwise fail-loud instead of silently empty lists.
#
# DATA-PROTECTION WARNING: the output file contains DECRYPTED plaintext
# values (including Art.-9 content) - treat it like a DB backup: only
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
# field level) and which document types and field templates carry it -
# or which carry none and therefore appear in no report. Pure
# information, does not change any data. MUST run with the admin DB role
# (BYPASSRLS) - otherwise fail-loud (§ 9 DB role model): under the app role
# without a facility context, an empty/filtered inventory would look like a
# flawlessly configured facility.
docker compose -f docker-compose.prod.yml run --rm maintenance \
 inventory_report_bindings \
 --facility "Beratungsstelle Nord" # text report to stdout
docker compose -f docker-compose.prod.yml run --rm \
 -v "$PWD/exports:/exports" maintenance inventory_report_bindings \
 --facility "Beratungsstelle Nord" --format json --output /exports/report-bindings.json

# Initial facility setup (Refs #1883): interactively create the organization,
# facility, settings, default catalog, and first facility_admin.
# MUST run with the admin DB role (BYPASSRLS) - the command writes to
# core_settings and (with the default catalog) to core_documenttype/-fieldtemplate/
# -documenttypefield/-fieldgroup/-fieldcondition, all FORCE-RLS tables
# with a USING-only policy that PostgreSQL also checks on INSERT. The
# guard triggers BEFORE the first prompt, so nobody types a
# password first and then fails at the write access. The whole run is ONE
# transaction: an abort leaves no half-created facility behind.
# --no-default-package leaves out the catalog (the facility starts without
# document types and can only be documented on after load_package).
# If the facility already carries document types or field templates, the
# catalog stays UNTOUCHED - initial setup, not a catalog update (that's what
# the next command is for).
docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility

# Apply the default catalog to an EXISTING facility (Refs #1883):
# functionally identical to "load_package --file <…>/default.yaml", just without
# the container path. Never deletes anything, but writes catalog properties of
# existing rows back to the package state (resync table in
# docs/packages.md) (German) - so review it first with --dry-run. Repeatable,
# same role prerequisite.
docker compose -f docker-compose.prod.yml run --rm maintenance \
 load_package --default --facility <uuid> --dry-run
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

### 5.4a Cache Backend / Multi-Worker (Refs #795)

As soon as `GUNICORN_WORKERS > 1` is running (see [5.4](#54-gunicorn-timeout)), `django-ratelimit` (login/sudo/health brute-force protection) and the health detail caching need a **cross-process** cache. Otherwise (Django's default `LocMemCache`), each worker holds its own counter and the effective rate limits multiply by the worker count.

**Default: the bundled `redis` service.**
`docker-compose.prod.yml` starts a Redis on the internal network and sets `REDIS_URL=redis://redis:6379/1` for `web` and `cron`; `settings/prod.py` then switches to Django's built-in `RedisCache` (no `django-redis` needed). Properties of the service:

- **No host port, no password.** The cache sits exclusively on the `internal` network; the network boundary is the access control. Anyone who does publish the port is running an open, unauthenticated Redis.
- **No persistence** (`--save ""`, `--appendonly no`, no volume). The cache holds only regenerable data with a short TTL. Persistence would be a data-protection *minus* here: it would write rate-limit keys (including client IPs, user IDs) to disk, where no retention policy captures them. A Redis restart only costs a cold cache.
- **Memory capped** (`--maxmemory 256mb`, `allkeys-lru`), so the container does not run into `mem_limit` OOM.

**Fallback (unchanged): `DatabaseCache`.**
If `REDIS_URL` is empty or unset, `prod.py` uses the shared `DatabaseCache`. The cache table `anlaufstelle_cache` is created via migration (`0092`) and comes up automatically as part of the normal one-shot migrate job (`docker-migrate.sh`, see [1.2](#12-deploy-steps)) — **no** separate `createcachetable` step needed. The fallback is functionally equivalent (also cross-process, `RATELIMIT_USE_CACHE=default`), but costs a DB round trip per rate-limit counter. **Redis is therefore a performance decision here, not a correctness one.**

Deliberately turning Redis off (e.g. a very small deployment, where every MB of RAM counts):

```bash
# in .env - empty, not commented out:
REDIS_URL=
```

```bash
docker compose -f docker-compose.prod.yml up -d --scale redis=0
```

**External Redis** instead of the bundled one: simply point `REDIS_URL` in `.env` at your own endpoint (`redis://redis.internal:6379/1`); the compose value is only a fallback for an unset variable. The bundled service can then be scaled to 0 as above.

**Which backend is currently running?** The `/health/` endpoint reports it for internal/token callers:

```bash
curl -sf -H "X-Health-Token: $HEALTH_DETAIL_TOKEN" https://$DOMAIN/health/ \
 | python3 -c 'import json,sys; print(json.load(sys.stdin)["cache"])'
# {'backend': 'django.core.cache.backends.redis.RedisCache', 'status': 'ok'}
```

This is not a cosmetic field: if `REDIS_URL` is set but the `redis` package is not present in the image (typical when an **older** `APP_VERSION` image is run with the new compose file), `prod.py` deliberately falls back to `DatabaseCache` and writes a warning to the container log — the stack stays operational, but runs slower. `cache.backend` makes this state visible. `cache.status = error` (backend configured but unreachable) sets the overall status to `degraded`, because the rate-limit protection is then also ineffective.

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
```

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
3. **Optional webhook** — `BREACH_NOTIFICATION_WEBHOOK_URL` env var; receives a POST with a JSON payload (kind, count, threshold, audit_id) on every new finding.
4. **Deduplication** — within 24h, the same finding (kind + user) is only reported once in the `AuditLog`.

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

Counterpart to § 6.5b: that section is about a **data breach in an operated instance** (GDPR Art. 33/34), this one is about a **vulnerability in the software itself**. The public commitment on this is in [SECURITY.md](../../SECURITY.md) (Meldewege, SLA, support rule — German); this section is the internal process behind it.

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

**Failure mode** (Refs #797): If the off-site sync fails (network down, invalid credentials, disk full on the off-site host), `backup.sh` logs an `ERROR`. A **state file** `$BACKUP_STATE_DIR/.offsite_state` (default: next to the backups) counts consecutive failures. On the **second** consecutive failure, the script exits with exit code 1 — the cron/Coolify job turns red and the operator sees the incident immediately, without having to scan the logs. Successful runs reset the counter.

**Sentry hook (optional):** If `SENTRY_DSN` AND a `BACKUP_SENTRY_HOOK` script are set, `backup.sh` calls the hook on every off-site failure (arguments: description, off-site target). The hook is a free operator choice — e.g. `sentry-cli send-event` or a custom `curl` script. Without a hook, it stays at log + exit code.

### 6.6 Backup Restore Drill (Refs #720, #739)

Verifies that the latest backup is fully restorable and that the lines of defense (RLS, AuditLog immutability trigger) are preserved. **Recommendation: quarterly via cron + alert email on failure.**

Since OPS-05 (Refs #1336), the script covers **both** shipped backup formats. The choice is made via the `--dev`/`--prod` flag or `$DRILL_FORMAT`; without one, the format is auto-detected from the existing backup files (default `dev`, backward-compatible — the existing dev timer runs unchanged). If **both** formats are present at the same time, the drill aborts and requires an explicit choice:

- **`--dev`** — `dev-ops/deploy/backup.sh`, which actually runs on `dev.anlaufstelle.app` (Refs #981): `pg_dump --format=custom` → `pg_restore`, AES-256-CBC, source `$BACKUP_DIR/dump-*.pgc.enc` (default `/var/backups/anl`), stack `docker-compose.dev.yml`.
- **`--prod`** — `scripts/ops/backup.sh`, the canonical path shipped to self-hosters: plain SQL + `gzip` + A4.3 HMAC sidecar → `psql`, source `backups/daily/anlaufstelle_*.sql.gz.enc`, stack `docker-compose.prod.yml`. The HMAC sidecar is verified BEFORE decrypting.

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
| 3 | Spot-check per table (`core_facility`, `core_client`, `core_event`, `core_auditlog`, `core_workitem`) — are counts queryable? |
| 3b | **Completeness guard (fail-closed):** row comparison of the critical RLS tables (`core_client`, `core_event`, `core_workitem`, `core_auditlog`) between the throwaway DB and the live DB (`$POSTGRES_DB`) — `Live>0` but `Restore=0` turns it red. Catches the incomplete `--prod` restore that the `psql` exit code (without `ON_ERROR_STOP`) swallows (Refs #1336) |
| 4 | RLS policy check — `SELECT COUNT(*) FROM pg_class WHERE relname LIKE 'core_%' AND relrowsecurity = true` must yield `>= 18` (a conservative floor in the script; the actual count is higher, see section 9) |
| 5 | `auditlog_immutable` trigger exists AND blocks a raw UPDATE |
| 6 | Cleanup: drop the throwaway DB (via trap) |
| 7 | **On full success (all steps including 3b):** `mark_restore_verified` in the web container → compliance marker |

Output: one `OK` / `FAIL` entry per step. Exit code != 0 on any `FAIL`. On `FAIL`, check backup integrity immediately — a failed trigger check (step 5) is critical, because AuditLog immutability then no longer applies after a restore.

> **Benign exception during the restore (step 2):** The dump contains a `REFRESH MATERIALIZED VIEW core_statistics_event_flat`, which on restore fails against `FORCE ROW LEVEL SECURITY` on `core_event` (`--dev`: `pg_restore: errors ignored on restore: 1`). This only affects the statistics cache, not the payload data — the mv-refresh timer rebuilds it hourly. The `--dev` drill reports this as `OK … MV refresh skipped (benign)` and only fails on **other** errors. In the `--prod` branch, `psql` deliberately runs without `ON_ERROR_STOP` (the same MV refresh error is tolerated), which means the exit code alone does not prove completeness — the fail-closed proof is delivered by the row comparison in **step 3b**, before the restore is marked as verified.

**Compliance marker** (Refs #919): On success, step 7 automatically writes a `RESTORE_VERIFIED` AuditLog entry (`manage.py mark_restore_verified`), which the compliance dashboard (`/system/compliance/`) uses as an age indicator:

- ≤ 90 days → `ok`
- ≤ 180 days → `warning`
- older → `critical` (GDPR Art. 32(1)(c) violated — restorability no longer demonstrated).

Without a drill ever having run, the dashboard stays at `unknown`. It can still be updated manually (e.g. after a documented out-of-band restore):

```bash
docker compose -f docker-compose.dev.yml --env-file .env.dev exec web \
 python manage.py mark_restore_verified --note "Out-of-band restore $(date +%Y-%m-%d)"
```

**Cron suggestion:**

```cron
# Quarterly (1st month of the quarter, 03:30 after the 02:00 backup). Without a flag
# the format is auto-detected; on a self-hoster with prod backups, set --prod
# explicitly so a missing backup doesn't silently fall back to --dev:
30 3 1 1,4,7,10 * cd /opt/anlaufstelle && bash scripts/ops/restore-drill.sh --prod \
 >> /var/log/anlaufstelle-restore-drill.log 2>&1 \
 || mail -s "Restore-Drill FAIL" ops@example.com < /var/log/anlaufstelle-restore-drill.log
```

#### Manual Sentinel Drill for Media Volume Persistence

Complements `restore-drill.sh` with the container recreate aspect:

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
reads the JSON `status` and reports unhealthy on `degraded` — Coolify/k8s see
the incident, but the load balancer does not pull the pod out.

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
# (dev/test) - with an open range, e.g. "Django>=5.1,<5.2".

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

### 8.5 Troubleshooting

| Problem | Cause | Solution |
|---------|---------|--------|
| `deps-check` fails even though nothing changed | Different pip-tools version between dev and CI | `pip install --upgrade pip-tools` and rerun `make deps-lock` |
| `pip-compile` can't find a matching version | Range constraint too strict | Loosen the range in `.in`, e.g. `>=X,<Y+1` |
| A transitive dependency pulls a vulnerable version | A direct dependency pins an old version | Add the direct dependency explicitly and bump it |

---
