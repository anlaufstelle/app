> This is the English translation of [admin-guide.md](../admin-guide.md).
> The German version is the authoritative source. Last synced: 2026-03-28 (v0.9.0).

# Anlaufstelle -- Admin Guide

This guide is intended for IT administrators of social service facilities who install, configure, and operate Anlaufstelle.

---

## Table of Contents

1. [Installation (Docker Compose)](#1-installation-docker-compose)
2. [Initial Configuration](#2-initial-configuration)
   - 2.5 [Configure Documentation Types](#25-configure-documentation-types)
   - 2.6 [Manage Selection Options (Field Templates)](#26-manage-selection-options-field-templates)
3. [Backup and Recovery](#3-backup-and-recovery)
4. [Updates](#4-updates)
5. [Monitoring](#5-monitoring)
6. [Troubleshooting](#6-troubleshooting)
7. [GDPR Notes](#7-gdpr-notes)

---

## 1. Installation (Docker Compose)

### Prerequisites

- Docker Engine 24 or newer
- Docker Compose v2 (as a plugin: `docker compose`)
- Publicly reachable server with a DNS record for your domain
- Ports 80 and 443 must be accessible from the internet

### Step 1: Download Files

```bash
git clone https://github.com/anlaufstelle/app.git
cd anlaufstelle
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
DJANGO_SETTINGS_MODULE=anlaufstelle.settings.prod
ALLOWED_HOSTS=anlaufstelle.meine-einrichtung.de

# Database
POSTGRES_DB=anlaufstelle
POSTGRES_USER=anlaufstelle
POSTGRES_PASSWORD=<secure-database-password>

# Field encryption (required in production)
ENCRYPTION_KEY=<fernet-key>
```

**Generate an encryption key:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Generate a secret key:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

> **Important:** Store `ENCRYPTION_KEY` and `DJANGO_SECRET_KEY` securely (e.g., in a password manager or secret management system). Without the `ENCRYPTION_KEY`, encrypted field data becomes permanently unreadable.

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

```bash
docker compose -f docker-compose.prod.yml up -d
```

On first start:
- The PostgreSQL database is initialized
- Django migrations are applied automatically
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

Expected response:

```json
{"status": "ok", "database": "connected", "version": "dev"}
```

---

## 2. Initial Configuration

### 2.1 Create a Facility and Admin User

Run the interactive setup script:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py setup_facility
```

The script interactively prompts for:

1. **Organization name** -- e.g., `Diakonie Musterstadt e.V.`
2. **Facility name** -- e.g., `Beratungsstelle Nord`
3. **Admin username** -- default: `admin`
4. **Admin password** (enter twice)

Afterwards, the organization, facility, default settings, and the first admin user are created.

> **Note:** If an organization or facility with the given name already exists, it will be reused. An existing username will not be overwritten.

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
| Retention anonymous (days) | 90 | Deletion period for anonymous contacts |
| Retention identified (days) | 365 | Deletion period for identified contacts |
| Retention qualified (days) | 3650 | Deletion period after case closure |

### 2.4 Create Additional Users

Under **Core > Users > Add user** in the admin:

- **Username:** Login name (no real names)
- **Display name:** Name shown in the user interface
- **Role:** One of the four roles (see below)
- **Facility:** Assignment to a facility
- **Password must be changed:** Recommended for new accounts

#### Role Descriptions

| Role | Title | Permissions |
|---|---|---|
| `admin` | Administrator | Full access to all areas and settings |
| `lead` | Lead / Manager | Reports, approve deletion requests, view all cases |
| `staff` | Staff / Social worker | Record and edit contacts, cases, and documentation |
| `assistant` | Assistant | Limited data entry, no access to qualified data |

### 2.5 Configure Documentation Types

Under **Core > Documentation types**, you define which kinds of documentation can be recorded in the facility. Each type consists of:

- **Name and description**
- **Field templates** (free-text fields, selection fields, etc.)
- **Retention period** in days (overrides the global facility setting if specified)

#### Category

The category groups documentation types for filtering and the **statistics page**:

| Category | Purpose | Example |
|----------|---------|---------|
| **Contact** | Direct contacts with clients | Counseling session, crisis intervention |
| **Service** | Services provided | Needle exchange, accompaniment |
| **Admin** | Administrative processes | Ban, referral |
| **Note** | Free-form notes | Observations, memos |

> **Note:** The category is used on the statistics page for grouping by documentation type. For the youth welfare office export, the **system type** is used instead (see below).

#### Sensitivity Level

The sensitivity level controls which roles can access entries of this type:

| Level | Access | Usage |
|-------|--------|-------|
| **Normal** | All roles (including assistants) | General contacts, services |
| **Elevated** | Staff, lead, admin | Counseling sessions, medical care |
| **High** | Lead and admin only | Crisis interventions, highly sensitive data |

#### System Type

The system type links a documentation type to internal application logic. It is set at creation and **cannot be changed afterwards**.

Currently, the system type serves two purposes:

**1. UI logic** (only Ban and Crisis):

| System Type | Effect |
|-------------|--------|
| **Ban** | Activates a ban banner on the client page, dedicated filter in the timeline, count and highlight in the handover |
| **Crisis** | Displayed as a highlight in the handover (recent crisis events) |

**2. Youth welfare office export** (mapping to report categories):

| System Type | Export Category |
|-------------|---------------|
| **Contact** | Contacts |
| **Counseling** | Counseling |
| **Crisis** | Counseling |
| **Medical** | Care |
| **Needle Exchange** | Care |
| **Accompaniment** | Referral |
| **Referral** | Referral |
| **Note** | *(excluded)* |

> **Note:** Not every documentation type needs a system type. Types without a system type have no special internal logic and are excluded from the youth welfare office export, but work normally for documentation.

#### Minimum Contact Stage

Defines the minimum contact stage a client must have for an event of this type to be created. Example: Counseling sessions require at least "Qualified" because the client's identity must be known.

### 2.6 Manage Selection Options (Field Templates)

Under **Core > Field templates**, you can edit the options of selection and multi-selection fields (Select / Multi-Select). Options are stored in the **Options json** field as a JSON array.

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

```bash
# Download the latest image
docker compose -f docker-compose.prod.yml pull

# Restart the stack (brief downtime)
docker compose -f docker-compose.prod.yml up -d
```

The `web` service automatically runs pending database migrations on startup. Check the logs afterwards:

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

**Response during normal operation (HTTP 200):**

```json
{
  "status": "ok",
  "database": "connected",
  "version": "dev"
}
```

**Response on database error (HTTP 503):**

```json
{
  "status": "error",
  "database": "unavailable",
  "version": "dev"
}
```

The endpoint requires no authentication and is suitable for external monitoring systems.

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

- The key is provided via the `ENCRYPTION_KEY` environment variable.
- In production, the application refuses to start if no key is set.
- Loss of the key means permanent loss of the encrypted data.

**Key rotation:** Automatic key rotation is not currently implemented. Store the key redundantly and separately from database backups.

**Transport encryption:** Caddy enforces HTTPS with HSTS (`max-age=31536000`). HTTP is automatically redirected to HTTPS.

### 7.2 Pseudonymization

The application stores **no real names** in the database. Client data is captured in pseudonymized form:

- Clients are referenced by internal IDs.
- Display names in the interface are configurable pseudonyms.
- Qualified (identifiable) data is only visible to authorized roles (Lead, Admin).

### 7.3 Retention Periods and `enforce_retention`

Retention periods are configured per facility in the settings (see [Section 2.3](#23-settings-per-facility)).

The management command `enforce_retention` enforces the configured periods by soft-deleting expired records:

| Strategy | Setting | Default |
|---|---|---|
| Anonymous contacts | `retention_anonymous_days` | 90 days |
| Identified contacts | `retention_identified_days` | 365 days |
| Qualified cases (after closure) | `retention_qualified_days` | 3650 days (10 years) |

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

**Set up as a daily cron job (recommended):**

```cron
0 2 * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1
```

Every execution that deletes records is automatically logged in the audit log.

### 7.4 Audit Log

The audit log is **append-only** and immutable. It automatically records:

| Event | Trigger |
|---|---|
| Login / Logout | Every login/logout |
| Failed login | Incorrect password |
| Qualified data viewed | Access to identifiable client data |
| Export | Data export by a user |
| Deletion | Manual or via `enforce_retention` |
| Level change | Change in a client's contact status |
| Settings changed | Changes to facility settings |

**View in the admin:**

Under **Core > Audit logs**, logs can be filtered by action, facility, user, and time period. Only administrators have access.

**Audit log retention:** The audit log itself is not subject to automatic deletion within the application. In accordance with your internal documentation requirements (e.g., following BSI recommendations or your Data Protection Impact Assessment), you should establish an external archiving strategy.

### 7.5 Deletion Requests (Four-Eyes Principle / Dual Approval)

Deletion requests for client data are processed using the four-eyes principle (dual approval): a request must be approved by a Lead or Admin before data is permanently deleted. This protects against accidental or unauthorized deletion.

### 7.6 Data Subject Rights (Art. 15--20 GDPR)

The following administrative capabilities are available for handling requests from data subjects:

| Right | Action |
|---|---|
| Access (Art. 15) | View client data and audit log in the admin; export if needed |
| Rectification (Art. 16) | Edit fields directly in the admin |
| Erasure (Art. 17) | Submit a deletion request through the application (four-eyes principle) |
| Data portability (Art. 20) | Export function in the application |

### 7.7 Recommended Organizational Measures

- Maintain a **Record of Processing Activities** (Art. 30 GDPR / Verzeichnis der Verarbeitungstaetigkeiten), describing the use of Anlaufstelle as a processing activity.
- Ensure the hosting provider has signed a **Data Processing Agreement** (Art. 28 GDPR / Auftragsverarbeitungsvertrag -- AVV under German law).
- Conduct a **Data Protection Impact Assessment** (Art. 35 GDPR / Datenschutz-Folgenabschaetzung -- DSFA under German law) if your facility processes particularly sensitive data (e.g., health data, data of vulnerable individuals).
- Restrict admin access to the minimum necessary (**Least Privilege**).
- Enable regular password changes by setting `must_change_password = True` for newly created users.
- Store backups and the `ENCRYPTION_KEY` in separate, secured locations.

<!-- translation-source: docs/admin-guide.md -->
<!-- translation-version: v0.9.0 -->
<!-- translation-date: 2026-03-28 -->
<!-- source-hash: 4f05b8d -->
