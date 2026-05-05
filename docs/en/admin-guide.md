> This is the English translation of [admin-guide.md](../admin-guide.md).
> The German version is the authoritative source. Last synced: 2026-04-28 (v0.10.2).

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
6. [Troubleshooting](#6-troubleshooting)
7. [GDPR Notes](#7-gdpr-notes)
   - 7.8 [Optimistic Locking](#78-optimistic-locking)
   - 7.9 [Row Level Security (RLS)](#79-row-level-security-rls)
8. [Statistics Snapshots & Materialized View](#8-statistics-snapshots--materialized-view)

---

## 1. Installation (Docker Compose)

> **Alternative: Coolify on Hetzner CX22** -- For the recommended deployment via [Coolify](https://coolify.io/) (including TLS, backups, and ClamAV), see the separate guide [`docs/coolify-deployment.md`](https://github.com/tobiasnix/anlaufstelle/blob/main/docs/coolify-deployment.md). The following Docker Compose instructions still apply for manual deployments.

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

> **Important:** Store `ENCRYPTION_KEYS` and `DJANGO_SECRET_KEY` securely (e.g., in a password manager or secret management system). Without the keys, encrypted field data becomes permanently unreadable.

#### Full Environment Variable Reference

All environment variables that the application evaluates at runtime (see [`src/anlaufstelle/settings/base.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/anlaufstelle/settings/base.py) and [`prod.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/anlaufstelle/settings/prod.py)):

**Django & Hosts**

| Name | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | -- (required in prod) | Signs sessions/CSRF. Generate with `secrets.token_urlsafe(50)`. |
| `DJANGO_SETTINGS_MODULE` | -- | In production: `anlaufstelle.settings.prod`. |
| `ALLOWED_HOSTS` | -- (required in prod) | Comma-separated host names, e.g. `anlaufstelle.example.de`. |
| `TRUSTED_PROXY_HOPS` | `1` | Number of trusted proxies in front of the app (X-Forwarded-For evaluation). `0` = no proxy, `1` = Caddy only, `2` = CDN + Caddy. |

**Database (PostgreSQL)**

| Name | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `anlaufstelle` | Database name. |
| `POSTGRES_USER` | `anlaufstelle` | DB user. |
| `POSTGRES_PASSWORD` | `anlaufstelle` | DB password (set securely in production!). |
| `POSTGRES_HOST` | `localhost` (via Compose: `db`) | DB host. |
| `POSTGRES_PORT` | `5432` | DB port. |

**Field Encryption (MultiFernet rotation)**

| Name | Default | Description |
|---|---|---|
| `ENCRYPTION_KEYS` | -- | Comma-separated list of Fernet keys. The **first** key is the write key (new data is encrypted with it); all others are read-only (decrypt fallback during rotation). |
| `ENCRYPTION_KEY` | -- | Legacy single key. At least one of the two variables must be set in production; otherwise the app refuses to start. |

**Virus Scanning (ClamAV, [#524](https://github.com/tobiasnix/anlaufstelle/issues/524))**

| Name | Default (prod) | Description |
|---|---|---|
| `CLAMAV_ENABLED` | `true` | Enables virus scanning before upload encryption. Fail-closed: if the daemon is unreachable, the upload is rejected. |
| `CLAMAV_HOST` | `clamav` | Hostname of the ClamAV daemon (service name in the Compose network). |
| `CLAMAV_PORT` | `3310` | TCP port of the clamd socket. |
| `CLAMAV_TIMEOUT` | `30` | Timeout in seconds per scan invocation. |

**Logging**

| Name | Default | Description |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` enables structured logging via [`core.logging.JsonFormatter`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/logging.py) -- recommended for production with log aggregation. |

**Sentry (optional)**

| Name | Default | Description |
|---|---|---|
| `SENTRY_DSN` | -- | If set, Sentry is initialized (PII is **not** sent, `send_default_pii=False`). |
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

**Misc**

| Name | Default | Description |
|---|---|---|
| `MEDIA_ROOT` | `<BASE_DIR>/media` | Storage location for encrypted file attachments (see [§ 2.9](#29-encrypted-file-vault--virus-scanning)). |

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
| Facility-wide 2FA enforcement | false | Enforces TOTP 2FA for **all** users of this facility (see [Section 2.7](#27-two-factor-authentication-2fa)) |

### 2.4 Create Additional Users

Under **Core > Users > Add user** in the admin:

- **Username:** Login name (no real names)
- **Email:** Required for the invite flow (see below)
- **Display name:** Name shown in the user interface
- **Role:** One of the four roles (see below)
- **Facility:** Assignment to a facility

#### Token Invite Flow (Refs [#528](https://github.com/tobiasnix/anlaufstelle/issues/528))

New accounts are created **without** a clear-text password. Instead, the application sends an **invitation email** containing a personalized setup link to the email address on file. The link takes the new user to the standard password-reset form, where they set a password themselves.

1. Admin creates a user with an email address in the admin.
2. The system calls [`send_invite_email`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/invite.py) and generates a token (Django's `default_token_generator`, based on `uidb64` + token hash).
3. The user receives the email, clicks the link, sets a password, and is logged in.

**Token validity:** Django's default is `PASSWORD_RESET_TIMEOUT = 259200` seconds (3 days). In Anlaufstelle this can be raised up to 7 days via the Django setting -- the token generator also invalidates the token automatically once the user has set their first password.

**Resend the setup link:** If the email did not arrive or the token has expired, the admin can issue a fresh token from the user detail view via "Resend setup link" (or the equivalent "Resend invitation" button) and have a new email sent.

**Fallback without email:** If a user is created **without** an email address, the admin generates a one-time clear-text initial password for backwards compatibility, which is displayed in the admin interface after saving. This path is **insecure** and should only be used as a last resort -- better: add the email address and resend the invitation.

> **Note:** `must_change_password` is set automatically by the invite flow, but is redundant for the token flow -- the password is chosen by the user in the setup step anyway.

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

#### Field-Level Sensitivity (`FieldTemplate.sensitivity`)

In addition to the document type's sensitivity, each individual field of a field template can receive its **own** sensitivity level (`FieldTemplate.sensitivity`). For field visibility the **maximum** of the document-type and field sensitivity applies -- a field flagged `HIGH` stays invisible to staff even if the document type itself is only `NORMAL`.

**Decoupling Encryption ↔ Visibility** (Refs [#356](https://github.com/tobiasnix/anlaufstelle/issues/356)): The two flags `is_encrypted` (at-rest encryption of the value in the database) and `sensitivity` (visibility in the UI) are configurable **independently**:

- A field may be stored encrypted on disk while still being visible in the UI at `NORMAL` sensitivity (e.g. contact data that all roles are allowed to see but that should not be stored in clear text).
- A non-encrypted field can still be restricted to Lead/Admin (e.g. statistical markers that are sensitive, but do not need to be stored encrypted).

#### Deletion Protection for Existing Data

Fields that already have values stored in events **cannot simply be deleted**. Attempting to remove a field in the admin triggers a protection mechanism that checks for stored values. If the field really has to go, a data migration must run first to clean up the values or move them into another field.

> **Pragmatic alternative:** Rather than deleting, disable the field in the document type mapping -- existing data is preserved, new events will no longer be offered the field.

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

In addition to exact substring matching, the global client (pseudonym) search uses a **trigram-based fuzzy search** -- tolerant of typos and phonetic variants (e.g. "Schmidt" ↔ "Schmitt"). Implementation: [`src/core/services/search.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/search.py), function `search_similar_clients`.

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

References: [#536](https://github.com/tobiasnix/anlaufstelle/issues/536), [#581](https://github.com/tobiasnix/anlaufstelle/issues/581).

### 2.7 Two-Factor Authentication (2FA)

Anlaufstelle supports TOTP-based 2FA via [`django-otp`](https://django-otp-official.readthedocs.io/). Every user can enable 2FA themselves (`/mfa/settings/`); in addition, there are two enforcement levels.

**User-facing documentation:** [User Guide § 1 -- Two-Factor Authentication](user-guide.md#two-factor-authentication-2fa).

#### Configure Enforcement

| Level | Field | Admin location | Effect |
|---|---|---|---|
| **Single user** | `User.mfa_required` | Core > Users > *User* > "MFA required" field | 2FA mandatory for this user, disabling is blocked |
| **Facility-wide** | `Settings.mfa_enforced_facility_wide` | Core > Settings > *Facility* > "Facility-wide 2FA enforcement" field | 2FA mandatory for **all** users of this facility |

Evaluation happens via [`User.is_mfa_enforced`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/user.py) -- a user is considered enforced if **either** level applies. The MFA middleware blocks access to protected areas until a TOTP code has been verified in the session.

#### Reset 2FA for a User

If a staff member loses their authenticator device, an administrator has to delete the TOTP device so they can set it up again:

1. Open **Admin > OTP > TOTP devices**.
2. Select the affected user's device and delete it.
3. Inform the user: on their next login they will be redirected to `/mfa/setup/` (if `is_mfa_enforced=True`) or can re-enable 2FA voluntarily.

Since v0.10.1 there are also **backup codes as a second factor** for exactly this recovery case (Refs [#588](https://github.com/tobiasnix/anlaufstelle/issues/588)). On 2FA setup the user receives 10 single-use codes that should be printed or stored in a password manager -- at the 2FA login prompt the user can enter a backup code instead of a TOTP code. Used codes are invalidated and recorded in the AuditLog (`MFA_BACKUP_CODE_USED`). If all 10 codes have been spent or lost, the admin reset above remains the fallback.

#### Account Lockout

After **10 failed login attempts** the account is automatically locked (the login service reads the threshold from [`src/core/services/login_lockout.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/login_lockout.py)). The locked user sees an information page and can no longer sign in until an admin unlocks the account:

1. **Admin > Core > Users** -- select the affected user.
2. In the user profile under "Account status" click **Unlock account**.
3. The unlock is recorded in the AuditLog as `LOGIN_UNLOCK` (the `LOGIN_FAILED` log itself is immutable thanks to the `auditlog_immutable` DB trigger).

Lock, unlock, and any attempts during the lock window are all written to the AuditLog -- use the "Login failed" / "Account unlocked" filter for retrospective analysis.

#### Audit Trail

All 2FA events are recorded in the `AuditLog` (actions: `MFA_ENABLED`, `MFA_DISABLED`, `MFA_FAILED`). Use the filter in **Core > Audit logs** to investigate failed verifications or activation/deactivation events.

#### Relevant Files

- Models: [`src/core/models/user.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/user.py), [`src/core/models/settings.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/settings.py)
- Views: [`src/core/views/mfa.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/mfa.py)
- Middleware: [`src/core/middleware/`](https://github.com/tobiasnix/anlaufstelle/tree/main/src/core/middleware)

### 2.8 Quick Templates

**Quick templates** are pre-filled documentation templates that speed up recurring documentation patterns (e.g. "Counseling 30 min", "Standard check-in"). Staff can apply them with one click on the "New Contact" page.

**User-facing documentation:** [User Guide § 3 -- Quick Templates](user-guide.md#quick-templates).

#### Manage Templates

Templates are maintained in the Django admin under **Core > Quick Templates**.

| Field | Description |
|---|---|
| `facility` | Tenant-scoping -- each template belongs to exactly one facility |
| `document_type` | Which documentation type is pre-selected when the template is applied |
| `name` | Display name on the button (e.g. "Counseling 30 min") |
| `prefilled_data` | JSON object `{slug: value}` -- maps field slugs to default values |
| `sort_order` | Order in which buttons appear on the "New Contact" page |
| `is_active` | Only active templates are offered for use |
| `created_by` | User who created the template (informational only) |

#### `prefilled_data` -- Filter Rules

The service layer applies a **whitelist filter** before writing and again before applying a template ([`src/core/services/quick_templates.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/quick_templates.py)). Values are kept only if:

- the slug belongs to the selected documentation type,
- the effective sensitivity of the field is `NORMAL` (no `ELEVATED`/`HIGH` prefill),
- the field is not encrypted (`is_encrypted=False`) and is not a `FILE` field,
- for `SELECT`/`MULTI_SELECT` fields, the value matches an **active** option of the current field template.

This makes templates **self-healing**: if an admin later deactivates a select option, templates are not updated but silently drop the stale value next time they are applied. No data migration needed.

#### Role and Sensitivity Visibility

Quick templates are filtered per user using [`user_can_see_document_type`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/sensitivity.py). An assistant, who cannot see `ELEVATED`/`HIGH` documentation types, will not see templates for those types either. This guarantees the button list never exposes a template a user cannot actually apply.

#### Operational Notes

- Templates are a **convenience layer**, not a data source -- the resulting event must be reviewed and saved explicitly by the user, who can still edit every field.
- Applying a template **does not overwrite** existing form values; it only fills empty fields.
- There is currently **no custom UI** for template management beyond the Django admin. Staff with the `admin` role can maintain templates there.

#### Relevant Files

- Model: [`src/core/models/quick_template.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/quick_template.py)
- Service: [`src/core/services/quick_templates.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/quick_templates.py)
- View integration: [`src/core/views/events.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/events.py) (`EventCreateView`)
- Tracking issue: [#494](https://github.com/tobiasnix/anlaufstelle/issues/494)

### 2.9 Encrypted File Vault & Virus Scanning

File attachments (photos, scans, documents) on events are stored in an **encrypted vault**: scanned for viruses before being written to `MEDIA_ROOT`, then symmetrically encrypted with the `ENCRYPTION_KEYS` key material using AES-GCM. Refs [#524](https://github.com/tobiasnix/anlaufstelle/issues/524).

#### Upload Flow

1. A staff member picks a file in the event form ([`src/core/forms/events.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/forms/events.py)).
2. The server validates file type and size (see `allowed_file_types` and `max_file_size_mb` in the facility settings -- default **10 MB**).
3. **ClamAV scan** before encryption:
   - Default: fail-closed. If the daemon is unreachable (`CLAMAV_ENABLED=true`, but no TCP connect), the upload is **rejected**.
   - Virus detected → the upload is discarded and an audit-log entry is written.
4. The payload is encrypted with AES-GCM (write key = first entry in `ENCRYPTION_KEYS`) and stored under `MEDIA_ROOT`.
5. Downloads happen exclusively through the protected Django view (no direct webserver access to `MEDIA_ROOT`).

#### ClamAV Service (`docker-compose.prod.yml`)

The production Compose file includes a dedicated `clamav` container with a healthcheck (`clamdcheck.sh`). The web service waits for `service_healthy` before starting. Reference: [`docker-compose.prod.yml`](https://github.com/tobiasnix/anlaufstelle/blob/main/docker-compose.prod.yml). Configurable via `CLAMAV_ENABLED` / `CLAMAV_HOST` / `CLAMAV_PORT` / `CLAMAV_TIMEOUT` (see [ENV reference in § 1](#full-environment-variable-reference)).

#### Health Check

In production the `/health/` endpoint checks ClamAV daemon reachability in addition to the database. If ClamAV is enabled but unreachable, the endpoint signals an error state (HTTP 503) -- this lets external monitors detect "uploads are currently unavailable" as well.

#### Key Rotation (`ENCRYPTION_KEYS`)

MultiFernet accepts a list of keys. To rotate:

1. **Generate a new key** and add it as the **first** entry in `ENCRYPTION_KEYS`: `ENCRYPTION_KEYS=new,old`. Newly written data is encrypted with `new`; existing data stays readable via `old`.
2. Optional: run `python manage.py reencrypt_fields` to re-encrypt existing fields with the new key (see [`src/core/management/commands/reencrypt_fields.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/reencrypt_fields.py)).
3. After successful re-encryption, remove the old key from the list.

> **Important:** Never **replace** the old key instead of **appending** the new one -- any still-unrotated data would become unreadable.

#### Upload Limit

- Per facility: `Settings.max_file_size_mb` (default **10 MB**) -- see [`src/core/models/settings.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/settings.py).
- Global: Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` applies as an additional hard limit. Raise it via ENV or settings override if larger uploads must be allowed.

#### Safe Downloads (RFC 5987)

All file downloads are delivered via the central helper [`safe_download_response`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/utils/downloads.py). The helper sets `Content-Disposition` with RFC-5987-encoded file names (Unicode-safe, no reverse-path traversal) and prevents browser MIME sniffing.

### 2.10 Offline Mode & Streetwork (M6A)

For field work (e.g. streetwork), Anlaufstelle offers a **secure offline mode** with **client-side** end-to-end encryption of all data cached on the device. Refs [#573](https://github.com/tobiasnix/anlaufstelle/issues/573), [#576](https://github.com/tobiasnix/anlaufstelle/issues/576).

#### Cryptographic Design

| Aspect | Value |
|---|---|
| Algorithm | **AES-GCM-256** (WebCrypto-native) |
| Storage | **IndexedDB** (AES ciphertext only, **never** plaintext) |
| Key derivation | **PBKDF2** -- 600,000 iterations, SHA-256 |
| KDF input | User password + `User.offline_key_salt` (16 bytes, per-user, stored server-side) |
| Key lifetime | In-memory only (`CryptoKey` with `extractable: false`) |

Source: [`src/static/js/crypto.js`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/static/js/crypto.js), [`src/core/services/offline_keys.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/offline_keys.py).

#### Consequences for Admins

- **Key loss is unrecoverable:** If a user forgets their password, all offline data stored locally on their device becomes **permanently unreadable**. An admin-side reset of offline data is technically **impossible** -- the key only exists in the user's browser.
- **Sync before password change:** Users must synchronize offline data with the server **before** changing the password or logging out. Changing the password rotates the salt (new key).
- **Tab close, logout, password change** → the in-memory key is discarded → stored ciphertexts are unreadable until the user authenticates again.

#### Browser Requirements

- The WebCrypto API (`crypto.subtle`) and IndexedDB must be available.
- Supported: current versions of Firefox, Chrome, Edge, Safari.
- Not supported: legacy browsers without a modern crypto API (offline mode is grayed out in that case).

#### Streetwork Levels

- **Level 2 (Read cache):** Selected client dossiers are locally encrypted and cached before streetwork so they can be viewed offline.
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

### 5.4 CSP Debugging

The Content Security Policy (CSP) is set **centrally in Django** via [`django-csp`](https://django-csp.readthedocs.io/) (see [`src/anlaufstelle/settings/base.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/anlaufstelle/settings/base.py), `CONTENT_SECURITY_POLICY`). The previously redundant CSP configuration in the Caddyfile has been removed -- this is the only way to ensure that app and reverse-proxy policies do not drift apart.

**Inline scripts are not allowed.** All JavaScript logic lives in external files under `src/static/js/`, included via `<script src=…>` or through nonce-aware template tags.

**`script-src` global without `'unsafe-eval'`.** With the migration to the `@alpinejs/csp` build (v0.10.2), `'unsafe-eval'` has been removed from the global policy. All Alpine components are registered as `Alpine.data()` components in [`src/static/js/alpine-components.js`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/static/js/alpine-components.js); architecture tests forbid inline `x-data="{...}"` and complex expressions (ternaries, `||`/`&&`, method calls, object literals) in Alpine and HTMX directives.

**Exception `/admin-mgmt/*` (Django admin):** django-unfold loads its own Alpine build that uses `new AsyncFunction()`-based evaluation for the Cmd+K search and therefore cannot initialize without `'unsafe-eval'`. The [`AdminCSPRelaxMiddleware`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/middleware/) therefore appends `'unsafe-eval'` **per request only for admin routes** -- which are additionally protected by the MFA gate and the `admin` role. Outside the admin, the strict global policy stays active.

**Typical error patterns in the browser console:**

- `Refused to execute inline script because it violates the following Content Security Policy directive` -- inline `<script>` block in a template. Move the script to a static JS file or include it via a nonce-aware template tag.
- `Refused to load the script … because it violates … directive: "script-src 'self'"` -- external script CDNs are not supported; all scripts must come from `self`.
- `Refused to evaluate a string as JavaScript because 'unsafe-eval' is not an allowed source` -- expected on regular routes (architecture violation); inside the admin area this indicates that the relax middleware did not match (check the route pattern in [`AdminCSPRelaxMiddleware`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/middleware/)).

If CSP errors appear after an update: check the browser console for the **specific blocked URL/source** and decide whether to move the source into the template or adjust the CSP directive.

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

**Visibility vs. encryption:** A field's encryption (`is_encrypted`) and sensitivity level (`sensitivity`) are configurable independently. An encrypted field may be visible to all roles, and a non-encrypted field can be restricted to certain roles.

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

#### Retention Dashboard

A **retention dashboard** is available under [`/retention/`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/retention.py) where Lead and Admin roles can efficiently process deletion proposals generated by `enforce_retention`. Refs [#514](https://github.com/tobiasnix/anlaufstelle/issues/514), [#515](https://github.com/tobiasnix/anlaufstelle/issues/515).

| Bulk Action | Effect |
|---|---|
| **Approve** | The proposal is cleared for deletion. On the next retention run the record is actually deleted (or k-anonymized, see below). |
| **Defer** | The proposal is postponed by a configurable period (default 30 days) and reappears in the **next retention run**. After exceeding `retention_max_defer_count` (default 2), the item is either forced to a decision or -- if `retention_auto_approve_after_defer=True` -- auto-approved. |
| **Reject** | The proposal is permanently discarded (record is kept). |

#### Legal Hold

Individual records can be protected from automatic deletion via **Legal Hold** (e.g. during ongoing investigations, audits, or data subject requests). The flag is tracked on the record or in a `LegalHold` entry and is respected by the retention job -- a record with an active Legal Hold is **never** proposed for deletion, even if its retention period has expired.

Legal Holds are managed in the retention dashboard and in the Django admin (see [`src/core/models/retention.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/retention.py)).

#### K-Anonymization Instead of Hard Delete

As an alternative to hard deletion, **k-anonymization** can be enabled per facility (Refs [#535](https://github.com/tobiasnix/anlaufstelle/issues/535)).

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

### 7.8 Optimistic Locking

To protect against **silent overwrites** when two users edit the same record in parallel (two staff members have the same client/case open at the same time and save one after another), Anlaufstelle applies **optimistic locking** at the service layer. Refs [#531](https://github.com/tobiasnix/anlaufstelle/issues/531).

**Affected models:** Client, Case, WorkItem, Settings, Event.

**Mechanics** ([`src/core/services/locking.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/locking.py)):

- Every form renders the current `updated_at` as a hidden field.
- On save, the helper `check_version_conflict(instance, expected_updated_at)` verifies whether the record has been modified in the meantime.
- On conflict a `ValidationError` is raised; the view redirects the user with the message:
  > *"The record has been modified in the meantime. Please reload the page."*

**Administrative notes:**
- There is no admin toggle to disable locking -- the protection is system-wide.
- If users see the conflict message frequently: review the workflow (separate concurrent editing, adjust processes) rather than work around the feature.

### 7.9 Row Level Security (RLS)

In addition to ORM-side facility scoping, **PostgreSQL row-level security** is enabled on **18 facility-scoped tables** as **defense-in-depth**. A buggy ORM query that forgets facility scoping still returns no foreign data, thanks to RLS. Refs [#542](https://github.com/tobiasnix/anlaufstelle/issues/542), [#586](https://github.com/tobiasnix/anlaufstelle/issues/586).

#### How It Works

- The middleware [`FacilityScopeMiddleware`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/middleware/facility_scope.py) sets the Postgres session variable `app.current_facility_id` per request via `SELECT set_config('app.current_facility_id', <id>, false)` (session scope, not transaction scope).
- The RLS policies (migration [`0047_postgres_rls_setup`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/migrations/0047_postgres_rls_setup.py)) filter every row via `facility_id = current_setting('app.current_facility_id', true)`.
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

1. **Materialized view** (`core_statistics_event_flat`) -- pre-aggregates current event data so the statistics page does not have to scan all events on every request. Refs [#544](https://github.com/tobiasnix/anlaufstelle/issues/544).
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

Implementation: [`src/core/management/commands/refresh_statistics_view.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/refresh_statistics_view.py).

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

Anlaufstelle computes statistics (dashboard, half-year reports, youth welfare office PDFs) from the event table by default (aggregated via the materialized view). When automatic data deletion (`enforce_retention`) removes old events, those events would disappear from the statistics.

**Statistics snapshots** persist monthly aggregates before events are deleted. Reports use hybrid logic: stored snapshots for past months, live data (from the materialized view) for the current month.

### Automatic Capture

Snapshots are created automatically whenever `enforce_retention` deletes events -- the affected months are captured right before the deletion.

### Periodic Cron Job (Recommended)

It is also recommended to create snapshots regularly via cron:

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
- **Top clients:** The ranking of the most active clients is always computed live and may change after a deletion.
- **Distinct clients:** The distinct-client count across multiple months is an approximation (sum, not an exact distinct count).

### Admin Interface

Snapshots are visible in the Django admin under **Statistics snapshots** (read-only). You can check which months have been captured and when the last update took place.

<!-- translation-source: docs/admin-guide.md -->
<!-- translation-version: v0.10.2 -->
<!-- translation-date: 2026-04-28 -->
