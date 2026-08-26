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
