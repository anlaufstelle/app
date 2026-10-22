> This document summarizes the German domain concept ([Fachkonzept](../fachkonzept-anlaufstelle.md)).
> It is not a 1:1 translation. For the complete specification, refer to the German original.
> See also the [Glossary](glossary.md) for domain term translations.

# Domain Concept Summary

## What is Anlaufstelle?

Low-threshold social services -- drop-in centers, emergency shelters, street work teams, and day centers serving people experiencing homelessness or addiction -- document their work almost entirely on paper: handwritten visitor lists, A4 logbooks passed between shifts, and self-built Excel sheets. The semi-annual report to the public funding body regularly takes facility managers two to three weeks of manual data aggregation. This is not a failure of the facilities; it is a rational response to the absence of suitable tools.

Established commercial social-sector software targets large providers with hundreds of staff. Licenses are expensive, rollouts require weeks of consulting, and every system assumes that each person is registered with a real name and address. In reality, most contacts at low-threshold facilities are anonymous. Clients have the right not to disclose their identity. A system that asks for a name first has not understood the working reality.

Anlaufstelle fills this gap. It is an open-source specialized application for documentation, operations management, and impact measurement -- built around pseudonymization, contact stages, and the daily rhythms of low-threshold work. It is designed to be self-hosted with `docker compose up`, learnable in two to three hours, and affordable for donation-funded facilities with five to thirty staff members. Licensed under AGPL v3, it belongs to the community it serves.

### Who It's For

Anlaufstelle fits wherever people come in without an appointment and without a real name, and where most contacts stay anonymous or pseudonymous. The working pattern is contact → short documentation → (optional) casework → statistics; communication and scheduling happen outside the system. It is a poor fit when real-name files, correspondence with authorities, or live communication channels are the core of the work.

Beyond the original domain of low-threshold substance-use and homelessness services, curated domain packages are shipped for open social and life counseling (`beratungsstelle`), awareness teams at festivals and clubs (`awareness`), women's shelters and violence counseling (`frauenhaus`), and mobile and open youth work (`jugendarbeit`). Each package replaces the standard catalog rather than being loaded on top of it.

## Core Domain Concepts

### Personless Contacts and Contact Stages (Kontaktstufen)

The data model distinguishes personless events from two contact stages for people; personless contacts are not a contact stage of a person:

- **Personless contact** -- No linked person record. The event has no person in the data model (one visit, one service). That does not make it anonymous by content: depending on the documentation type it can carry free text, locations or attachments (for example the personless incident capture of the awareness package) that make a person identifiable. Most contacts at a drop-in center remain at this level.
- **Pseudonym only** (`identified`) -- The team assigns a pseudonym (e.g. "Maus"). Under this pseudonym, the system tracks a contact history and a timeline. The intended workflow keeps the mapping from pseudonym to real person in the minds of staff, and there is no required field for real names. The software does not technically prevent real names: pseudonyms, notes, free-text fields and attachments contain whatever the team enters.
- **With additional details** (`qualified`) -- Additional personal details are stored in dynamic fields. Field encryption and role-based visibility are separate rules. Social workers and above may raise the contact stage. Without existing events, Manager and Application support may directly lower it to "Pseudonym only". With existing events, direct lowering is blocked: Manager or Application support must submit a downgrade request. Approval requires another active person in the same facility with deletion confirmation permission; this may also be an Assistant or Social worker. Stage changes are logged.

Contact stages affect permitted [documentation types](glossary.md), deletion workflows, detail-view auditing and retention. Assistants may open person detail pages at either stage. Dynamic fields are visible only when the higher sensitivity of type and field is permitted for the role: Normal for Assistant, up to Elevated for Social worker, all levels for Manager and Application support. Internal person notes are visible only to Social worker and above. Encryption does not itself impose a role restriction.

### Timeline (Zeitstrom)

The [event](glossary.md) is the fundamental unit of documentation: a timestamped record of something that happened -- a brief visit, a crisis conversation, a needle exchange, an accompaniment to the social welfare office. Every event belongs to a documentation type and optionally to a person.

The start page shows events as a chronological stream, optionally filtered by a named [time filter](glossary.md). Time filters are saved time windows with a label (e.g. "Night shift 21:30--09:00") used by the timeline, handover and tally views, not statistics. Events do not reference them; shift handover notes do, with deletion protection and frozen window boundaries.

### Contacts list

The working list `/events/` complements the daily Timeline with combined period, documentation type, person, creator, own-entry, shift and anonymity filters. Assistant and higher can see other team members’ entries within the same facility and sensitivity limits as the Timeline. Case filtering and display require Social worker or higher. Deleted contacts and contacts in the trash are excluded. Results show metadata only, newest first, with pagination and mobile cards; filters remain in the URL across Back/Forward.

**Contacts** appears directly below **Timeline** in the sidebar and in the mobile More menu. Only the list route activates it; event detail pages remain assigned to Timeline. The local Timeline link also remains and carries over day, documentation type and shift.

Search stays limited to pseudonym and `Event.search_text`: only unencrypted fields with effective Normal sensitivity, excluding case titles and file contents. A search term also excludes events belonging to people in the trash. There is no autocomplete, export or bulk action, and list/filter requests create no audit entry. See [ADR-043](../adr/043-suchraum-volltextsuche.md), [ADR-016](../adr/016-search-postgres-only.md), #2521 and the [user guide](user-guide.md#contacts-list).

### Documentation Types (Dokumentationstypen)

Each facility configures its own set of documentation types (e.g. "Contact", "Crisis conversation", "Needle exchange", "Referral"). Each type defines which fields appear in the form, with every field carrying metadata:

| Attribute | Purpose |
|---|---|
| Data type | Text, number, date, single/multi select, boolean |
| Sensitivity level | Normal / elevated / high (on the type, optionally overridden per field) |
| Encryption flag | Field-level Fernet/AES-128 encryption |
| Report key | Mapping for reports and aggregations (`report_key`) |

Retention is configured on documentation types and in facility settings, not on individual fields.

Field values are stored as JSONB in PostgreSQL, keyed by stable `FieldTemplate.slug` values ([ADR-024](../adr/024-slug-based-identifiers.md)). A pre-configured [domain library](glossary.md) ships with the system so facilities do not start from an empty database.

### Cases and Outcome Goals

Not every contact is part of a case. Most interactions at a drop-in center are one-off visits. But when work with a person becomes sustained -- a counseling process, a housing referral spanning months -- a [case](glossary.md) can be opened to group related events. Cases contain [episodes](glossary.md) (distinct phases such as a crisis period or a referral process) and may track [outcome goals](glossary.md) and milestones to measure impact beyond simple activity counts.

### Work Items (Arbeitsinfos)

Separate from documentation, [work items](glossary.md) cover the operational layer: notes ("Mail for Maus is in the drawer") and tasks ("Follow up with Frau M. about the appointment"). They support everyday coordination, have their own lifecycle (open, in progress, done, discarded), and appear in each staff member's inbox. Work items have no separate automatic retention period. Personal content is redacted when the linked person is anonymized.

The CSV import wizard supports persons and events (`ImportJob.TargetType`); see the [import guide](admin-guide.md#212-import-wizard).

The handover view (`/?view=uebergabe`) also includes an encrypted shift note with its own configurable retention period (default: 90 days). It belongs to a shift date and optionally to a time filter.

## Data Model Overview

The data model follows a clear hierarchy with facility scoping as the primary boundary:

```
Organization (name and creation date, visible in navigation/system overview)
  +-- Facilities (multiple sites per installation -- primary scope for staff)
        +-- Users (staff with role assignments)
        +-- Clients (pseudonymized persons with contact stages)
        +-- Events (timestamped documentation entries, typed by DocumentType)
        +-- DocumentTypes (configurable, with FieldTemplates)
        +-- TimeFilters (named time windows for shift-based views)
        +-- WorkItems (notes and tasks with lifecycle)
        +-- Cases (grouping related events, with Episodes and OutcomeGoals)
        +-- AuditLog (append-only, immutable)
```

Clients, events, documentation types and work items carry a direct `facility_id`; episodes and outcome goals inherit their scope through the case, milestones through the goal. Clients have no direct organization foreign key. Middleware sets the current facility context for scoped managers. Multiple facilities can be created through Django admin or `setup_facility`; the organization name appears in navigation and the system overview. Organization logos and separate report branding are not implemented.

## Privacy Architecture

Anlaufstelle processes social data of highly vulnerable people. Privacy is not an add-on but a structural design principle. Where the release notes under Version Additions describe a protection differently (for example the offline key lifecycle or Row Level Security), the description in this section applies:

- **Pseudonymization by design.** There is no real-name field. The pseudonym is the primary identifier. The mapping to a real person exists only in staff knowledge.
- **Field-level encryption.** Sensitive fields (health data, counseling notes) are encrypted with Fernet/AES-128 before storage. The encryption key is not stored in the database. Key rotation is supported.
- **Offline cache.** Content taken offline is encrypted in the browser with AES-GCM-256. The key is derived at login from the password and a per-user offline salt (PBKDF2, 600,000 iterations, SHA-256) and stored as a non-extractable `CryptoKey` in its own IndexedDB, so it survives reloads and tab, browser and device restarts. Non-extractable only stops the key bytes from being exported; it does not prevent decryption: whoever holds the unlocked device and browser profile within the idle window can read the offline content through the app or the developer tools. Logout and password change wipe key and offline store. The idle check runs only while or when an app page executes. With unsynced work it removes only the key and keeps the ciphertext, which becomes readable again after the next login only if password and offline salt are unchanged -- the server rotates the salt on role change, on deactivation and on a correct-password login against a locked account, after which cached rows are discarded and unsynced entries show as undeliverable. Without unsynced work the idle check wipes key and store. Closing the tab or browser and restarting the device is not a wipe, and a device that is never reopened keeps key and ciphertext. Record and person IDs, sync status, timestamps and queue target URLs stay in clear text next to the encrypted content.
- **Retention periods.** Configurable defaults are 90 days for anonymous events, 365 days for “Pseudonym only” and 3650 days for “With additional details”. The first two use `occurred_at`; additional-detail events use the event date without a case, `closed_at` for closed cases and the last business activity for open cases (falling back to case creation, also when the closing timestamp is missing). An event type’s “Maximum period (only shortens)” runs from `occurred_at`; the earliest expiry applies without bypassing approval. Anonymous and pseudonym-only events expire automatically; additional-detail events require a retention proposal and approval before a subsequent run deletes them. Active legal holds on events, persons or cases protect affected records; an Art. 18 restriction protects the person’s events, anonymization and history redaction. Facility managers and facility administrators set and dismiss holds in all three records, with a reason and optional expiry, independently of proposals; system administrators can dismiss them at `/system/legal-holds/`. These actions are audited. Actual event deletion removes contents and attachments; both automatic and manual deletion carry counts into the monthly aggregate in the same transaction. Monthly aggregates have no expiry. Persons are anonymized after all their events are deleted; eventless persons at “Pseudonym only” qualify only without open cases, after the identified inactivity period (default 365 days) from the latest person change (`updated_at`), business case activity (`last_activity_at`), task change or person/case attachment change (creation, replacement or removal). Eventless persons “With additional details” are excluded from automatic anonymization: proposals and approval currently support events only, leaving no automatic deadline enforcement for these persons. Holds and Art. 18 protect both anonymization paths. Audit retention defaults to 24 months from each row’s timestamp, with a separate installation-wide period for rows without a facility; 0 disables the respective cleanup. Exempt actions and facility-scoped audit rows targeting held records remain.
- **Deletion requests.** Manual retention approval requires two different active people from the same facility, each with the Manager or Application support role and “Deletion confirmation” permission; the proposer cannot approve. The first approval leaves the proposal pending (“1 of 2 approvals”); only the second, different person gives final approval. With only one eligible account, manual final approval is impossible. Deferral, rejection and legal hold, including from records, discard the first approval with an audit record. The “Approvers for the retention run” compliance card checks role and count (0 critical, 1 warning, at least 2 OK); “Approvers for deletion requests” remains unchanged. Deferral protects until reconsideration; rejection prevents deletion under that proposal, with a new proposal allowed only after the configured retry period (default 365 days). Exception: automatic approval after repeated deferral is opt-in and off by default; it requires the maximum number of deferrals and expiry of the follow-up date. Downgrading “With additional details” to “Pseudonym only” with existing events also requires a request approved by another user with that permission. Completed deletion, merge and downgrade requests are removed after a configurable 1095 days from `reviewed_at`; open requests and requests without a completion timestamp remain. Their audit trail follows its own retention rules. Rectification requests have no technical retention period.
- **Audit log.** An append-only, immutable log records changes to people, cases, events and work items, including edits to episodes and outcome goals, linking an event to a case, and attachment uploads and erasure (creating and closing the sub-objects still is not covered); it also records login attempts and — of read access — opening the detail page of a person at the "With additional details" level, subject access requests and exports, file downloads, offline take-out and cross-facility reads by the system administration within the scope named in the role model. Ordinary list, detail and search requests deliberately leave no entry ([ADR-043](../adr/043-suchraum-volltextsuche.md)). Event edits deliberately leave two traces for different purposes: `EventHistory` retains before/after states for the event's local change history, while `AuditLog.EVENT_UPDATE` records only the documentation type and changed field identifiers facility-wide — never field values. Each audit row carries a per-facility HMAC chain so that later tampering or deletion of individual entries is detectable (`verify_audit_chain`, run nightly).
- **Breach detection.** Heuristic incident detection (GDPR Art. 33/34) flags mass deletions of person data, credential-stuffing bursts against unknown usernames, and distributed victim-lockout patterns, delivering SSRF-hardened webhook alerts. Error tracking stays off by default; when enabled, a scrubber strips PII before events leave the installation.
- **Facility scoping.** Middleware supplies the current facility context; scoped managers use the direct facility key, while episodes, goals and milestones are scoped through their parent relationships. PostgreSQL Row Level Security provides defense-in-depth on all facility-scoped tables, with session-variable-driven policies that enforce the tenant boundary at the database layer itself. Exception: sessions of the system administration set `app.is_super_admin`, and the policies then admit cross-facility access. The covered tables are derived as `EXPECTED_TABLES` in `src/tests/test_rls.py`; not covered are the tables outside RLS (`core_user`, `core_facility`, `core_organization`), the statistics materialized view, maintenance and migration runs under the `BYPASSRLS` admin role, and a wrongly set facility context.
- **Two-factor authentication.** TOTP-based 2FA is available and can be enforced per user or facility-wide. Single-use backup codes and an account lockout back it up, the privileged roles (`super_admin`/`facility_admin`) are enforced into MFA by default, and the TOTP secret itself is encrypted at rest (Fernet, ADR-031); a WebAuthn passkey can additionally be registered as a phishing-resistant second factor (ADR-032, no passwordless login).
- **Data subject rights.** Authorized staff handle access (PDF), portability (JSON), internal rectification requests, deletion requests (`DeletionRequest`, separate from retention proposals) and processing restrictions. Client self-service and self-registration are rejected ([ADR-013](../adr/013-dsgvo-art16-no-selfservice.md), [ADR-041](../adr/041-berichtigungsworkflow-intern.md)). Seven GDPR documentation templates (processing register, DPIA, DPA, TOMs, data-subject information, employee information, and a deletion concept) ship with the app ([`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/)).

- **Upload buffers.** Attachments are encrypted at rest with Fernet/AES-128 after a fail-closed ClamAV scan. Uploads exceeding 2.5 MiB are temporarily buffered in plaintext before scanning and encryption. The supplied Compose configurations place this buffer on RAM-backed tmpfs via `FILE_UPLOAD_TEMP_DIR=/tmp`; without an explicit directory, Django uses `tempfile.gettempdir()`, whose storage depends on the deployment. See [Ops Runbook § 5.7a](ops-runbook.md#57a-upload-temp-directory-plaintext-staging-buffer-refs-1661).
- **Rate limits.** Login attempts are limited to 5/minute per IP and 10/hour per username. Authenticated actions have per-view, per-user limits, among others: mutations 60/hour, frequent actions and offline bundle requests 120/hour each, offline revocation queries 600/hour. There is no global API user limit; the external API remains a target feature ([ADR-026](../adr/026-rate-limiting.md)).
- **Password hashing.** Username/password authentication uses Django’s default PBKDF2-SHA256 password hasher.

- **Statistics suppression and backups.** The configurable k-anonymity threshold is the minimum group size for statistical publication (default `k=5`), not the number of retained backups. Backup rotation uses separate time periods; see [Admin Guide § 3 — Backup and Recovery](admin-guide.md#3-backup-and-recovery). In the counseling-hours report, a duration field must be defined for minute contributions, but recorded values may still be absent or unusable. Defined fields do not guarantee published minutes: a row’s minutes are suppressed if its event count is suppressed or fewer than k events contribute usable durations. Secondary suppression can also hide larger event cells to prevent reconstruction of small groups from totals. If any row’s minutes are withheld, the overall minute total is withheld too. For example, two contacts with defined duration fields and 30 minutes each yield 60 internal minutes; at `k=5`, the published event count, minutes and overall minute total are suppressed.

## Role Model

Anlaufstelle uses five roles with increasing privileges (Refs #867, [ADR-018](../adr/018-rollenmodell-superadmin.md)):

| Role | German | Description |
|---|---|---|
| **Assistant** | Assistenz | Read access with limited documentation rights. Sees entries and fields of sensitivity "Normal" only -- regardless of the person's contact stage, so also for persons at "With additional details". Cannot edit other users' entries or delete events. |
| **Staff** | Fachkraft | Core documentation role. Can capture contacts, manage work items and search within their facility. Sees entries and fields up to sensitivity "Elevated" (not "High"), may edit every event visible to them and delete their own. |
| **Lead** | Leitung | Supervisory role. Everything Staff can do, plus pseudonym management, contact stage changes, statistics, exports, and deletion approvals. |
| **Application manager** | Anwendungsbetreuung (`facility_admin`) | Full control within one facility: user management, document-type configuration, settings, audit log, GDPR package. |
| **Super-Admin** | Systemadministration (`super_admin`) | Installation-wide system administration across all facilities; not bound to a single facility and no access to the regular application UI. Reads across facilities through the system area (`/system/`) and the admin interface (`/admin-mgmt/`, sudo required); Row Level Security lets these sessions through via `app.is_super_admin`. SYSTEM_VIEW records system-area calls and list/detail reads for all registered administrative models, including deletion requests, users, settings, audit log, snapshots, templates, organisation and facility; if logging fails, content is denied with HTTP 403. Event and event-history admin pages show metadata only, without field contents. |

Access is not purely role-based but context-dependent: what a user can see depends on the facility and on sensitivity -- the higher level of the documentation type and the field, compared with the role (Assistant: Normal; Staff: up to Elevated; Lead and Application manager: all levels). The contact stage of the person does not control visibility; it governs which documentation types may be used, four-eyes deletion, the audit entry when a person at "With additional details" is opened, and retention periods.

Event creation (`/events/new/`) saves drafts every 5 seconds in encrypted IndexedDB when a session key and the offline store are available; other forms do not share a general autosave feature. Synchronization runs in the open document, not through Service Worker Background Sync.

## Non-Functional Requirements

- **Accessibility target:** WCAG 2.1 Level AA for all core and operations interfaces. Keyboard navigation, screen reader support, minimum 4.5:1 contrast ratio.
- **Mobile / PWA:** Progressive Web App, installable on home screens. Optimized quick-capture for smartphones with large touch targets and autocomplete. Field-ready offline mode: staff take selected persons offline into an encrypted local cache, read and edit their records, and create new contacts and tasks without connectivity; the timeline and task list also render offline in-place at their canonical URLs from the local snapshot. Offline work syncs on reconnect with explicit, resolvable conflicts rather than silent overwrites, and permanently undeliverable entries surface in a dead-letter view instead of disappearing.
- **Internationalization:** Uses Django's `gettext` system; complete text coverage is a verification target. German (primary) and English are active. Language switcher in the UI. The package schema has no language key; multilingual package names remain a target feature.
- **External API target:** A facility-scoped REST API for provider integrations is not implemented; current JSON endpoints are session-bound internal endpoints.
- **Optional module targets:** Possible additions outside the core include a calendar, wiki, KDS export and interfaces to third-party systems.
- **Performance targets:** Page load < 1s (3G), contact save < 500ms, autocomplete < 300ms, six-month statistics query < 5s at 50,000 events.
- **Scaling target:** 5--30 concurrent users, up to 50,000 events/year per facility.
- **Deployment:** The production Compose stack includes Caddy, Django, PostgreSQL, Redis, mandatory ClamAV, cron and maintenance services. Hardware requirements: [admin guide § 1 — Prerequisites](admin-guide.md#hardware). Installation prerequisites: [admin guide § 1 — Prerequisites](admin-guide.md#prerequisites).
- **Availability:** 99% target. Health-check endpoint at `/health/` for monitoring integration.

### Authentication audit (#2527)

Known `super_admin` accounts without a facility raise a `SECURITY_VIOLATION` alarm with `reason=super_admin_bruteforce` after ten failed logins in fifteen minutes, immediately in the failed-login path and as a heuristic in a full `detect_breaches` scan (scheduled hourly). The same alarm is deduplicated per account for 24 hours. No account lock is introduced. At the regular `/login/` endpoint, failed passwords at or above the threshold receive HTTP 429 with progressive retry advice of 1, 2, 4 and at most 8 seconds (`Retry-After`); the server does not sleep and this advice does not reject a subsequent correct password. The existing IP/username rate limits remain effective and can still reject valid logins.

Successful TOTP and passkey verification records `MFA_VERIFIED` with the fixed method `totp` or `passkey`; failed factor checks retain `MFA_FAILED` or `WEBAUTHN_FAILED`. Consuming a backup code retains exactly its existing `BACKUP_CODES_USED` row. For signed-in accounts, a POST request to regenerate backup codes rejected for invalid TOTP, an unverified session or a missing TOTP device records `MFA_BACKUP_REGENERATE_DENIED` with the fixed reason `invalid_totp`, `unverified_session` or `missing_totp`. These audit details contain no codes, TOTP secrets or passkey assertions.

HTTP 429 rejections record `RATE_LIMITED` with a masked route class, a fixed reason (`request_rate` or `super_admin_backoff`), and the user/facility where already known. Unresolved paths are recorded only as `unresolved`; bodies, queries and actual URL tokens stay out of the details. The cache caps the trail at one row per route class and known account/facility in sixty seconds (anonymous requests share a bucket across all source IPs). If the audit write fails, the response stays 429 and an equally capped security log line names the route class, reason and known identifiers without request contents. Passkey failures and denied backup regeneration also count as critical audit events; successful verification and ordinary throttling alone do not trigger a compliance warning.

## Version Additions

Capabilities added since the base concept, release by release. Configuration and operational details live in the [admin guide](admin-guide.md) and the [CHANGELOG](../../CHANGELOG.md); this section summarizes the conceptual intent. Anlaufstelle is still a pre-release and **not yet cleared for production use**.

### v0.10.0 (2026-04-19)

Release v0.10.0 sharpens the low-threshold fit and strengthens the privacy architecture.

- **Offline mode (M6A).** Street work teams frequently operate without connectivity. An optional offline mode provides a client-side capture and read cache encrypted with AES-GCM-256. The key is a non-extractable `CryptoKey` derived from the user password via PBKDF2, persisted (not merely in-memory) in a dedicated IndexedDB for the duration of the server session; it is wiped on logout, password change, role revocation/deactivation, and on session-idle -- but not on tab close -- so a stolen device without an active session yields only ciphertext. Read, write-queue, and in-app offline-**edit** (with side-by-side conflict resolution and a dead-letter view for permanently undeliverable entries) are the accepted scope; the edit entry is wired and tested (#1111).
- **File vault.** Attachments on events are no longer stored in the clear. They are scanned by ClamAV before acceptance and encrypted at rest, extending field-level encryption to binary material.
- **Two-factor authentication.** TOTP-based second factor, configurable per user or enforced facility-wide, hardens login for facilities handling qualified data.
- **Fuzzy search.** Pseudonyms are often misremembered or misspelled. A typo-tolerant search based on PostgreSQL `pg_trgm` trigrams, with a per-facility similarity threshold, helps staff find known persons without resorting to broader disclosures.
- **Row Level Security.** PostgreSQL RLS policies on all facility-scoped tables (25 as of v0.23, including transitive scopes such as `OutcomeGoal`, `Milestone`, and `DocumentTypeField`) add a database-level safety net beneath the middleware filter. A session variable carries the current facility identity into the database, set per request and explicitly cleared on anonymous requests, so even a bypassed ORM layer cannot cross tenants.
- **Optimistic locking.** Client, Case, WorkItem, Settings, and Event records use version counters to prevent silent overwrites when two users edit the same record concurrently -- a realistic scenario in shift-based work.
- **Retention dashboard and legal hold.** The automated retention workflow gains a review surface: leads bulk approve, defer, or reject deletion proposals. A Legal Hold flag excludes individual records from deletion when investigations or disputes require preservation. K-anonymization is available as an alternative to hard deletion, preserving statistics while removing identifying detail.
- **Quick templates.** Admins maintain pre-filled event templates for recurring situations; templates are filtered by the user's role and the sensitivity of their fields so that assistants never see templates containing qualified-level content.
- **Token invite flow.** New users receive a one-time token link instead of a plaintext initial password. Invitees choose their own password on first use, closing a long-standing onboarding weak spot.

### v0.10.1 (2026-04-26)

- **Backup codes for 2FA.** When enabling TOTP, users receive ten single-use backup codes that may be entered at the 2FA login prompt instead of an authenticator code. Consumed codes are invalidated and recorded in the audit log. This closes a recovery gap that previously required an admin reset whenever a user lost their authenticator device.
- **Account lockout.** After ten failed login attempts within 15 minutes from the same source IP, the account is locked; the system administration account (`super_admin`) is exempt and only throttled by the rate limits. Admins unlock affected users from the profile screen; both events appear in the audit log. Combined with the per-route rate limits on every mutating endpoint (added systematically in v0.10.1), this raises the cost of credential-stuffing attacks against pseudonymized client lists.
- **Visual refresh.** The interface moved to a green-themed design with self-hosted DM Sans/Mono fonts (no Google CDN), consistent card patterns, KPI cards with monospaced numbers, and a mobile bottom navigation. The client list collapsed from a duplicated desktop/mobile render path into a single responsive grid.
- **Composite indexes** on AuditLog, Case, Event, and WorkItem accelerate the most common list filters (status combined with date).

### v0.10.2 (2026-04-28)

- **CSP migration to `@alpinejs/csp`.** The vendored Alpine build was replaced with the CSP variant; all inline `x-data="{...}"` objects became registered `Alpine.data()` components. With this change, `script-src 'unsafe-eval'` is removed from the global Content Security Policy. An architectural test prevents future regressions (no inline `x-data`, no ternaries / `||` / `&&` / method calls / object literals inside Alpine and HTMX directives).
- **AdminCSPRelaxMiddleware.** django-unfold ships its own Alpine build that uses `new AsyncFunction()` for the Cmd+K command palette and therefore needs `'unsafe-eval'` to initialize. A dedicated middleware now adds `'unsafe-eval'` only on `/admin-mgmt/*` routes — the privileged Django admin area, which is additionally protected by the MFA gate and the `admin` role. The strict global policy stays in place everywhere else.

### v0.11.0 (2026-05-05)

- **Django 6.0** migration (five upstream CVE fixes).
- **Sudo-mode re-authentication** gates the most sensitive actions behind a fresh credential check.
- **Breach detection** (GDPR Art. 33/34): heuristic incident detection with SSRF-hardened webhook alerts (off by default).
- **Four-eyes deletion workflow** with a dedicated audit trail for request/approve/reject.
- **Maintenance mode**, additional health checks, and a person-first terminology sweep across UI, accessibility, and translations.

### v0.12.0 (2026-05-12)

- **Five-role model** with an installation-wide Super-Admin and a cross-facility `/system/` area (ADR-018), replacing the earlier four-role scheme.
- **Two-user RLS-bootstrap split**: Postgres init provisions a separate admin user with `BYPASSRLS` so that migrations and seeding connect as `POSTGRES_ADMIN_USER`, while the application workers keep running on a non-bypass app user (self-hosters need the new env vars).
- **Pre-auth audit logs under RLS**: login attempts and anonymous reset requests are recorded before the facility session variable is set.

### v0.13.0 (2026-05-30)

- **Role-based work center (Arbeitszentrale)**: a per-role landing page that condenses the shift's action items onto existing data — no new models or permissions.
- **Three-role PostgreSQL model in production** (#902, ADR-020): production is lifted onto the same three-role topology as dev — a hardcoded `postgres` bootstrap superuser only provisions the app roles, the application role runs `NOSUPERUSER NOBYPASSRLS`, and a separate admin role runs `NOSUPERUSER BYPASSRLS` for migrations, seeding, and retention pruning; a new `check_db_roles` CLI verifies the topology at runtime (breaking change for self-hosters, who must add the new bootstrap/admin env vars).
- **Custom admin site** with a role gate and sudo requirement; only `super_admin`/`facility_admin` may enter, scoped to their own facility.
- **k-anonymization wired into the retention deletion path** and **privacy-preserving external reports** that drop pseudonym rankings and suppress small cells.
- **Compliance dashboard** with typed checks and **self-service lockout recovery** (token email, backup code, or password reset).
- **Supply-chain hardening**: SHA-pinned GitHub Actions with minimal workflow permissions.

### v0.14.0 (2026-06-11)

- **Privilege-escalation and admin-facility-scoping hardening**: a `facility_admin` can no longer reach `super_admin` rights; the facility foreign key is enforced as a single source of truth.
- **MFA default enforcement** for privileged roles; sudo mode requires a fresh second factor when TOTP is active.
- **Authenticated backups** (encrypt-then-MAC), **file-chunk binding v2** with downgrade detection, and **webhook SSRF hardening** (no redirects, DNS-rebinding protection).

### v0.15.0 (2026-06-16)

- **Database-wide PII-residue sweep** after deletion and retention closes silent plaintext remnants across facility-scoped tables.
- **Offline cache hardened to its accepted scope** (ADR-022): server-enforced TTL and revalidation, cache access revoked on role change/deactivation, and the crypto key bound to the session lifetime.
- **Deletion-approver pool decoupled from the Lead role** via a dedicated `can_confirm_deletion` right, so the four-eyes workflow no longer deadlocks with a single lead.
- **PostgreSQL 16 → 18** and Node 24 LTS in the build toolchain.

### v0.16.0 (2026-06-25)

- **Public demo instance** (`demo.anlaufstelle.app`): a `DEMO_MODE` banner and login panel showing the demo credentials and the next reset time, a `DemoGuardMiddleware` that blocks damaging actions (maintenance toggle, 2FA setup, password change, user management), an hourly reset, and email routed to the console. Demo builds are versioned per tagged release (ADR-028).
- **Two-column task work view** and a rate-limited system audit-log export; `cryptography` upgraded to 49.

### v0.20.0 (2026-07-10)

Collective pre-release bundling the skipped 0.17–0.19 — a complete offline field-readiness block plus the results of two security-review waves. No data-model breaks.

- **Offline field readiness**: new contacts/events and case tasks (including task status changes) can be created and edited offline, with resolvable status and field-edit conflicts and a dead-letter view for permanently undeliverable work; a single coordinated sync run per device (Web Lock) prevents duplicate replays (ADR-030).
- **HMAC-chained audit trail** with automated nightly tamper-evidence verification.
- **IP-bound login lockout** (a stranger's failed logins no longer lock the victim out from their own IP) and **k-anonymity hardening** across the external report artifacts.
- **Container-runtime hardening** (`no-new-privileges`, dropped capabilities, read-only filesystems) and **build integrity** (pip hash-pinning, digest-pinned base images).
- **Tailwind CSS 4** and **Python 3.14** as the production base.

### v0.22.0 (2026-07-15)

- **Offline "one world"** (offline V2, #1499): contacts and tasks can be captured offline even without a previously opened form, and the task list and timeline render offline in-place at their canonical URLs from the merged local snapshot.
- **Extended breach detection**: a mass client-deletion heuristic, anonymous login bursts per source IP, a distributed victim-lockout signature, and a secondary 24-hour low-and-slow window.
- **TOTP secrets encrypted at rest** (Fernet/MultiFernet, ADR-031); privilege-change audits now name the acting admin; the error-tracking scrubber closes remaining PII gaps (opt-in Sentry/GlitchTip).
- **Passkeys/WebAuthn as an additional second factor** (ADR-032, #1492): a passkey (device biometrics, smartphone, or FIDO2 security key) can be registered as a phishing-resistant second factor alongside the authenticator app; when several methods are set up, the 2FA login page offers a chooser. A passkey is only possible *in addition* to a configured TOTP device (backup codes stay the recovery path) and removing one requires sudo mode; passwordless login is deliberately disabled.
- **Serial capture of anonymous mass contacts** (#1349): a "Save & next contact" action creates the contact and immediately reopens a fresh, empty form with the documentation type pre-selected -- for quickly tallying many anonymous contacts without the detour via the detail page; the next contact starts anonymous with the first field focused.
- **Task overview status and time filters** (#1570): the task overview gains an explicit status filter (open / in progress / done / all) and -- effective only for "done" -- a time-range filter (last 7/30 days / all), so all completed tasks become reachable beyond the passive seven-day "recently done" preview; both combine with the existing assignment and facility scoping filters.

### v0.23.0 (2026-08-01)

- **Defense-in-depth wave**: the Content-Security-Policy now ships `style-src` without `'unsafe-inline'` (special pages via sha256 hashes, narrow admin-UI exception); statistics materialized-view access is funneled through a facility-enforcing helper; an AST guard fails CI on unscoped queries against the three non-RLS tables; upload temp files are pinned to tmpfs with a new deploy check.
- **Operations out of the box**: `scripts/initial-setup.sh` performs the full first-run setup idempotently; the health endpoint continuously reports whether the runtime DB connection could bypass row-level security (`db_role_posture`); `docker-compose.prod.yml` gains a supercronic cron sidecar for the six background jobs (backup stays host-side) and a Redis cache service (database-cache fallback unchanged).
- **Governance clarified**: the maintainer and governance documents describe the actual state — a single maintainer, no deputy currently designated — and funding-programme references were removed project-wide.
- **"Works ≠ correct" pilot**: the first synthetic-persona walk against the dev instance produced eleven triage candidates and directly led to two pre-release fixes (htmx settle attributes, admin CSP exception).

### v0.80.0 (2026-08-10)

The largest collective pre-release to date — the configurability milestones M0–M5 plus file attachments and data portability. The version jump from 0.23 to 0.80 signals the approach to 1.0.

- **Configuration UI (M0)**: facility admins manage documentation types, field templates and the new field groups under `/config/…` — including a drag-and-drop field board with two-level ordering; the Django admin stays the ops surface.
- **Conditional field logic (M1)**: show/hide rules between fields of a documentation type, maintained in a rule editor, evaluated identically live in the form and on the server.
- **Dynamic fields on persons and cases + configuration packages (M2)**: the field system moves beyond events; a facility's entire field setup can be exported/imported as a versioned YAML package (schema v1.1) with an idempotent loader.
- **Data portability (M4)**: CSV import wizard for persons, a complete facility export (JSON, schema v1) via `manage.py export_facility`, and an internal rectification workflow for Art. 16 GDPR requests.
- **Report backends as a plugin system (M5)**: one file per reporting standard behind a fixed interface with mandatory k-anonymity hooks; seven example backends ship with it.
- **File attachments on persons and cases**: the encrypted file vault, previously event-only, becomes owner-generic (exactly one owner per file — event, person or case) with a direct RLS policy.
- **Removed (breaking)**: the documentation-type "category" field is gone; report mappings live in their own `report_key` field (the `system_type` enum is down to `ban`/`crisis`, the slug fallback is gone); type names are now unique per facility.

### v0.81.0 (2026-08-18)

- **Tally counting becomes a full capture path**: facility admins opt individual documentation types into the lightweight "+1" tally counter; suitable types with a single required number field open a compact number form instead of the full record, a tally can be undone once within 60 seconds by its own anonymous author, and the whole path — including the number form — now works offline with server and pending counts shown separately.
- **The timeline updates itself**: visible feed entries, tally counts, the running shift and the focus box converge live within roughly twelve seconds for the viewing role, facility and active filters, and house-ban warning banners now converge the same way, without background polling that would otherwise keep the session open artificially.
- **Report key becomes its own field**: report assignment moves off the frozen `system_type` enum and its hidden slug fallback onto an explicit "report key" carried by both the documentation type and the field template, so a field decides on its own whether it feeds a given report instead of inheriting an unrelated type-level choice.
- **Navigation and search overhaul**: pressing Enter now opens a full search results page with true group totals, type filters and pagination instead of a dropdown-only affordance; two new area hubs, "Privacy" and "Administration", group previously scattered pages behind shared tabs, and the statistics area gains an Overview/External report/Record-level reports/Disclosures tab row that makes the external report reachable through navigation for the first time.

### v0.82.0 (2026-08-21)

- **Operational status becomes visible and honest**: the production stack finally hands the backup directory to the application container — until now the system area reported "no backup found" permanently, no matter how reliably the nightly job ran. A missing backup is shown as a warning there (it is the one step operators wire up themselves), while the machine-readable `/health/` endpoint keeps its meaning: "never ran" is deliberately not an outage, or every fresh installation would report a fault. The daily audit-chain integrity check now leaves a marker after a fully clean run and appears as its own status row; a run that finds tampering deliberately leaves **no** marker, so the row goes overdue instead of feigning success. Four contradictory staleness thresholds were consolidated into one rule derived from the actual cron intervals.
- **Data minimisation in the audit log is enforced, not just intended**: every `detail` key written to the append-only log must be declared with a justification of why it carries no client data. The check runs at write time, so the dozen call sites that build their payload dynamically are covered too — a purely static check would have missed sixteen keys. An undeclared key is refused loudly in development and CI, but only reported in production and still written: a missing audit entry is worse than an overly broad one, and the sharpest write points run exactly when something is already going wrong.
- **A facility created at runtime gets its settings row**: previously only the initial setup and migrations created it. Without that row, retention enforcement and statistics snapshots silently skipped the facility — meaning no retention period was applied to it at all.
- **Spreadsheet formula protection covers invisible characters**: a CSV cell whose formula character is preceded by a space, a non-breaking space or a zero-width character is now neutralised too. Spreadsheets discard those characters on import and evaluate the cell anyway. Re-importing an exported file remains deliberately out of scope, and the administrator handbook says so.
- **Rate limiting in search is recognisable as such**: hitting the limit produced a generic error toast, indistinguishable from a server fault or from "no results". It now carries its own wording.
- **Security support and supply-chain groundwork**: `SECURITY.md` states which release line receives fixes and for how long (rule-based while the project is pre-1.0, switching to dated end-of-support per minor at 1.0), every release build emits a CycloneDX software bill of materials alongside its notes, and the advisory handling process is written down. Private vulnerability reporting is enabled on the public repository, so the reporting path named in `SECURITY.md` actually works.

### v0.86.0 (2026-08-27)

- **Getting started takes one command**: `docker compose up` in a fresh clone brings up a fully seeded local system in under a minute — migrations and example data are applied automatically on first start only, an encryption key is generated per machine, and production/staging deployments deliberately keep their strict manual initialisation path. The contributor guide gains a five-minute quick start, a new architecture overview document, and the complete operations runbook is now available in English.
- **Subject-access answers are complete across all record types**: the Article 15 export now lists legal holds and deletion requests that target cases and activities — not only the person and their events — drawing from the same target definitions the Article 17 redaction uses, so the two can no longer drift apart. Congruence tests fail loudly if a future record type is added on one side only.
- **Unknown sensitivity values are always treated as most restrictive**: every visibility decision (lists, detail views, downloads, the offline bundle and the event search index) now hides fields whose sensitivity value is unrecognised, closing a path where the search index could have exposed text of an otherwise invisible field. Behaviour for all known values is unchanged and pinned by tests.
- **User creation in the technical admin works again**: the creation form demanded two password fields it never displayed, so saving always failed; the form no longer asks for a password at all — the application assigns one itself (e-mail invitation, or a displayed initial password where no address is given).
- **Governance is settled**: the maintainer documents record that no deputy is currently designated; the solo-maintainer model and its bus-factor risk are stated openly, and the security policy describes the actual state instead of announcing a deputy.
- **Contact capture no longer returns a server error for manipulated document-type values**, and the mobile offline test harness now exercises the same startup path real browsers take.

### v0.84.0 (2026-08-25)

- **Facility administration moves into the domain UI**: facility administrators can now invite and maintain their own users, edit facility-wide operational and security settings, maintain document-type presentation, and edit selection options through a validated row editor instead of raw JSON. All sensitive access-management paths require sudo re-authentication, remain facility-scoped, and produce actor/target-aware audit entries; the technical Django admin remains the bootstrap and emergency surface.
- **Duplicate persons can be merged without losing their history**: a direct merge is available for pseudonym-only records, while records with additional data require approval by a second person. Events, cases, tasks and watch-list state move to the surviving record; the source remains recoverable in the trash until retention expires, the operation can be undone during that window, and Article 15 exports describe both sides of the merge.
- **Cases gain a complete deletion lifecycle**: an accidental case can enter the trash, be restored with its episodes, goals and attachments, or be redacted after the shared retention period. The approval threshold follows the person's contact stage, legal holds postpone final erasure, linked events stay with the person, and offline/data-portability views honour the same boundary.
- **Attachments can be erased deliberately and audibly**: leads and facility administrators can permanently remove an uploaded file and all its versions after a dedicated confirmation step. Legal holds block the operation, the physical vault content is removed, and the immutable audit record keeps only non-reversible filename evidence rather than the plaintext name.
- **Credential and deployment boundaries are explicit**: password change, reset and initial setup share one documented side-effect matrix; security links use a configured canonical origin; trusted-proxy handling and access logs no longer retain query strings; database-role passwords can be rotated atomically on existing installations while privileged maintenance credentials stay out of the web container.
- **PII and audit hardening closes duplicate traces**: timeline activity stores structured, PII-free references instead of copied pseudonyms or titles; event, case-assignment, episode, outcome-goal and attachment mutations have dedicated audit actions; authorised history views can render encrypted field changes without weakening role checks. CSP reports now accept only the standard CSP reporting media types and reject empty payloads.

### v0.89.0 (2026-08-29)

- **Restriction of processing (Art. 18 GDPR) exists as a real mechanism**: a client record can carry a restriction marker with a documented legal ground; while it is set, every substantive write path — event capture and editing, CSV import, case operations, tasks, merging, attachment purge — is blocked at the service layer, while the data-subject-rights paths (rectification, deletion requests, subject access) deliberately stay open. Lifting the restriction requires a second person or a lead and is audited. The previous documentation conflated Art. 18 with the legal hold; both documents and the handbook now separate the two.
- **Anonymised timelines no longer carry exact timestamps**: events of anonymised records are generalised to the month (statistics already work at month granularity), the hard path resets the contact-stage quasi-identifier symmetrically, existing tombstones are backfilled by a migration, and the subject-access export states the generalisation instead of feigning precision.
- **The event history finally has a retention period**: per ADR-050 a nightly two-stage sweep first redacts the field values of versions belonging to deleted events and of versions older than a configurable period, then deletes long-redacted rows after a second configurable period — each batch leaving a permanent, pruning-exempt audit proof. Legal holds on the event or the person reliably defer both stages, late holds are marked as such, and the append-only guarantee is opened only table-scoped, batch-wise and owner-side, in the same lock order the application itself writes in.
- **Archiving a facility now means something**: users of an archived facility cannot sign in on either login surface, active sessions lose access immediately with a clear notice page, and previously downloaded offline data is purged from the device through three independent delivery paths — while an ordinary permission error still never touches the field worker's offline cache.
- **Cross-facility links are impossible at the database level**: composite foreign keys pin an event's client, case and document type to the event's own facility, closing the last gap that service-layer checks alone could not close against bulk or maintenance paths.
- **Schema and workflow hygiene**: facility names are unique (four maintenance commands can no longer silently act on the wrong facility — unknown names now fail loudly), rectification requests and quick templates protect their targets from cascading deletion, repeated visit tracking no longer rewrites rows within five minutes, and deletion, merge and bulk task paths share one canonical lock order backed by tests.

### v0.91.0 (2026-09-02)

- **A deletion request is no longer a one-way street**: the person who filed it can withdraw it while it is still pending — after a confirmation dialog that states nothing gets deleted. The request stays in the list with its own terminal state "Withdrawn" (so four-eyes approvers can still see why it left "Pending"), the withdrawal is audited, and foreign requests are unreachable on this path.
- **The application explains itself**: signed-in users reach role-relevant short help pages from the footer and both navigations, native context helpers cover contact stages, form conditions, retention periods, four-eyes review and offline work — all without JavaScript. Self-hosters can additionally mount their own legal pages (imprint, privacy policy) as size-capped UTF-8 files rendered through a linear, safe Markdown subset. Clients can now also be created inline from the contact form when a search comes up empty, without losing the form's state.
- **Reports only leave the building with their suppression threshold declared**: the k-anonymity minimum is enforced at runtime and stated on the export, instead of being an implicit convention.
- **Operations hardening**: the staging web process no longer runs as a database superuser; immediate anonymisation from the trash works again under the three-role model; ufw-docker is integrity-checked at server bootstrap; Redis accepts an optional password on top of the network boundary; bulk task actions are capped at 200 items; the production compose template starts again as shipped; dev deploys verify image versions fail-closed; and accounts without a facility see an explanation instead of a server error.
- **Accessibility and offline resilience**: the mobile search is usable again, screen readers announce where you are, list search fields carry accessible names, filters show their loading state, a full device storage no longer silently stops draft saving, a rolled-back server explains why offline data is unreachable, and pseudonym renames are warned about and marked in the history.
- **The mutation gate measures again what it should**: every parallel mutmut fork now gets its own test database — before this fix, a foreign failure could only flip a mutant towards "killed", so all earlier scores were upper bounds. The structurally unmeasured share (decorated functions) is now quantified alongside every score, and the v0.91.0 delta ran with a full survivor triage whose priority-1 gaps are pinned by dedicated tests.

### v0.95.0 (2026-09-07)

- **Retention holds close their race windows**: a hold ordered while the nightly deletion run, an immediate anonymisation, a case erasure or a proposal approval is already in flight now stops that operation instead of arriving too late; holds also protect activities and can no longer be placed on a person that has been merged into another record.
- **Accessibility is measured, not asserted**: forms, navigation, menus, tabs and lists meet the mechanical WCAG 2.1 AA criteria and announce their state to screen readers, an axe-core gate runs against the main pages, and the evidence document plus three manual-test cases cover what only a human can check.
- **Sign-in paths are hardened under concurrency**: the first login with an initial password leads to the password change instead of a 403, recovery links and backup codes stay single-use even when redeemed in parallel, and every unlock records the source IP.
- **A broken report backend no longer takes the building down**: statistics, exports, the monthly snapshot and the configuration pages isolate a failing report per facility, exports never leave partially, and every report query has a statement timeout.
- **Operations signal what they cannot see**: missing backup signals or an unmeasurable disk degrade the health status, audit high-water marks are anchored offsite and checked in the restore drill, host scripts use the host backup path, and compliance warns about unlimited audit retention.
- **Time and speed**: the nights of the clock change keep their real hours (shift end and event form both explain the doubled or skipped hour), person search uses trigram-friendly matching, and the timeline checks event visibility only within the own facility. The test suite was restructured with identical coverage.

### v0.99.8 (2026-10-23)

- **Four-eyes approval in the retention run**: manual deletion approvals need two different authorised people of the own facility whose rights are re-checked under lock; deferral, rejection and holds discard a first approval with an audit entry, and the run skips and reports approvals without two people unless the explicitly enabled auto-approval (off by default) applies after repeated deferrals, counting only deferrals by holders of the "deletion confirmation" right with no deferral of the same proposal still running; operators name at least two active accounts with the lead or application-support role and that right per facility, otherwise new manual approvals for "with additional details" events cannot be completed.
- **Revoked persons, access limits and security updates**: work surfaces, tasks, search and handover leave out content on deleted or inactive persons while keeping permitted entries without a person reference and respecting role visibility (aggregate statistics stay unchanged), new offline person bundles are refused for them, contacts can be linked only to open cases with a case type visible to the role, raster image uploads are checked against a total pixel, frame and buffer budget (no proven upper bound for the whole encoder memory), and Django 6.1.2, Pillow 13.0.0 and certifi are updated.
- **Forms, tasks and operations**: inputs and contact drafts survive save errors and conflicts (a draft is deleted only after a confirmed save, files must be selected again), stale forms no longer overwrite newer changes, new task assignments go only to active accounts, deactivated fields keep their values and field types in use cannot be switched incompatibly, plus fixes for episodes, accessibility, dates in the app language, backup verification, restore and the demo.

### v0.99.7 (2026-10-13)

- **Sensitive values, retention and audit**: events written through the application index only unencrypted, effectively normal fields, high-sensitivity types require explicit field levels (also in the Django admin), "with additional details" is deleted only after approval and records under a legal hold or an Art. 18 restriction are left out of the run, administrative access is audited and refused if the read audit fails, and review pages link only to records the reader may see.
- **Offline keys and second factor hardened**: offline keys are bound to the current derivation, their lifetime and revocation check are limited, second-factor and security checks are stricter, and attachments are re-encrypted safely across interruptions.
- **Reports, exports and everyday use**: one shared export bar with recipient and purpose for everything that leaves the statistics area, a PDF for record reports and protected small cells in period totals, a contacts list with filters, field protection visible in the configuration, and many fixes for the demo, offline feedback and translations.

### v0.99.6 (2026-10-08)

- **Second-factor reset by administrators**: facility administrators (for their own facility) and system administrators can reset another account's second factor after confirming their own login again; the reset is audited and ends the account's open sessions.
- **Audit, backup and data-protection hardening**: the audit chain no longer accepts unhashed rows, backups are written with restrictive file permissions, restoring a set that binds an audit anchor stops on a host without an anchor path unless explicitly overridden, the settings page requires a k-anonymity threshold of at least 2 and warns below 3 (a stored value of 1 stays effective and is flagged), new facilities get deletion approvers, and the compliance dashboard shows database roles, backup and retention runs — in the demo with dedicated texts.
- **Documentation and public source tree**: guides, templates and promises were checked against the code and brought to the actual state; the release build compares code blocks, rendering and leaks of the public tree, prepares boundaries in parallel, and every release is published to the public repository again.

### v0.99.5 (2026-10-01)

- **Second factor and account access hardened**: backup codes are stored hashed and the plaintext comparison is gone; re-enrolling the second factor, adding a passkey and the backup-code pages require the existing factor; setting a password via invitation or reset no longer ends in a server error; version and health details appear only after a complete login.
- **Consistent state and facility checks, clearer refusals**: milestones, goals, cases, tasks and further services check facility and current state themselves and never resurrect deleted or anonymised content; business refusals (restricted processing, invalid periods, parallel changes) appear as short hints instead of replacing the page, hint toasts are visible again in the production image, offline views report a revoked change honestly, and empty selects show a German placeholder.
- **Operations, backup and demo**: backup and restore drills only accept complete, coherent sets including the audit anchor; Compose stacks rotate container logs and keep maintenance mode across container recreation; invalid SMTP transport values stop the production start; the media-tree cleanup runs with a fixed budget; the demo offers a scenario catalogue with area selection instead of credentials; documentation catches up (import wizard, restore runbook, legal-text paths), and the package closes with the dependency batch plus two security bumps (`urllib3` 2.8.0, the Tailwind file watcher without `braces`). The concept document itself is unchanged in this release.

### v0.99.1 (2026-09-22)

- **Handover notes refresh live and explain roster gaps**: the handover block of the time stream now updates when another person saves a shift note, never overwriting an open draft, and states plainly when no shift is running in a roster gap while the last completed handover stays editable until the end of the following shift.
- **Empty detail fields are announced**: text, choice and number fields without a value carry a screen-reader-only placeholder on the detail pages of events, persons and cases; exports, PDFs and the change history are unaffected.
- **Platform**: Django 6.1 with the new `MAILERS` mail configuration; operator environment variables are unchanged. The concept document itself is unchanged in this release.

### v0.99.0 (2026-09-17)

- **A counselor persona for walk-in advice centers**: § 5 adds Amira, who documents anonymous short contacts within seconds and hands cases on to specialist services visibly for the team.
- **Historical upgrade notes removed**: the concept describes the current state only; upgrade paths between earlier pre-release versions are no longer part of the document.
- **Governance wording corrected**: the solo maintainer is described as the current state rather than the intended permanent setup, no deputy is named yet, and the former CRA classification was removed from the compliance documents.

### v0.96.0 (2026-09-08)

- **Anlaufstelle's target group is a working mode, not a facility type**: § 3 now leads with open social/life counseling and low-threshold advice centers, adds a fit/anti-fit table for further use cases, and documents four curated domain packages (`beratungsstelle`, `awareness`, `frauenhaus`, `jugendarbeit`) that replace, not extend, the standard catalog — mirrored in the FAQ, the README's "Who it's for" section, and the glossary.
- **Quick exit joins the glossary**: a header-bar action (button + keyboard shortcut) on every signed-in page that hides the content, ends the session and opens a neutral page, kept distinct from the separate opt-in privacy screen shown on window or tab switch.

<!-- translation-source: docs/fachkonzept-anlaufstelle.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-23 -->
<!-- source-hash: 68a13f46f665f522ec012c7708ad89d5f72e443d -->
