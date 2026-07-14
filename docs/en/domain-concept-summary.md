> This document summarizes the German domain concept ([Fachkonzept](../fachkonzept-anlaufstelle.md)).
> It is not a 1:1 translation. For the complete specification, refer to the German original.
> See also the [Glossary](glossary.md) for domain term translations.

# Domain Concept Summary

## What is Anlaufstelle?

Low-threshold social services -- drop-in centers, emergency shelters, street work teams, and day centers serving people experiencing homelessness or addiction -- document their work almost entirely on paper: handwritten visitor lists, A4 logbooks passed between shifts, and self-built Excel sheets. The semi-annual report to the youth welfare office regularly takes facility managers two to three weeks of manual data aggregation. This is not a failure of the facilities; it is a rational response to the absence of suitable tools.

Established commercial social-sector software targets large providers with hundreds of staff. Licenses are expensive, rollouts require weeks of consulting, and every system assumes that each person is registered with a real name and address. In reality, most contacts at low-threshold facilities are anonymous. Clients have the right not to disclose their identity. A system that asks for a name first has not understood the working reality.

Anlaufstelle fills this gap. It is an open-source specialized application for documentation, operations management, and impact measurement -- built around pseudonymization, contact levels, and the daily rhythms of low-threshold work. It is designed to be self-hosted with `docker compose up`, learnable in two to three hours, and affordable for donation-funded facilities with five to fifteen staff members. Licensed under AGPL v3, it belongs to the community it serves.

## Core Domain Concepts

### Contact Levels (Kontaktstufen)

Anlaufstelle uses a three-tier model that reflects how relationships develop in low-threshold settings:

- **Anonymous** -- No pseudonym, no record. The system counts the contact (one visit, one service) but stores no personal data. Most contacts at a drop-in center remain at this level.
- **Identified** -- The team assigns a pseudonym (e.g. "Maus"). Under this pseudonym, the system tracks a contact history and a timeline. No real name enters the database; the mapping from pseudonym to real person exists only in the minds of staff.
- **Qualified** -- The person is in an active counseling or support process. Additional sensitive data (health information, counseling notes) may be recorded, subject to stricter access rules and encryption. The transition to this level is a deliberate decision by facility leadership and is logged by the system.

Contact levels govern which [documentation types](glossary.md) can be used, who may access the data, and how long records are retained.

### Timeline (Zeitstrom)

The [event](glossary.md) is the fundamental unit of documentation: a timestamped record of something that happened -- a brief visit, a crisis conversation, a needle exchange, an accompaniment to the social welfare office. Every event belongs to a documentation type and optionally to a person.

The start page shows events as a chronological stream, optionally filtered by a named [time filter](glossary.md). Time filters are saved time windows with a label (e.g. "Night shift 21:30--09:00") that facilities configure to match their working hours. They are pure UI configuration -- changing the filter does not change the underlying data.

### Documentation Types (Dokumentationstypen)

Each facility configures its own set of documentation types (e.g. "Contact", "Crisis conversation", "Needle exchange", "Referral"). Each type defines which fields appear in the form, with every field carrying metadata:

| Attribute | Purpose |
|---|---|
| Data type | Text, number, date, single/multi select, boolean |
| Sensitivity level | Low / medium / high |
| Encryption flag | Field-level Fernet/AES-128 encryption |
| Retention period | Automated deletion after N months |
| Statistics category | Mapping for reports and aggregations |

Field values are stored as JSONB in PostgreSQL, keyed by field UUID. A pre-configured [domain library](glossary.md) ships with the system so facilities do not start from an empty database.

### Cases and Outcome Goals

Not every contact is part of a case. Most interactions at a drop-in center are one-off visits. But when work with a person becomes sustained -- a counseling process, a housing referral spanning months -- a [case](glossary.md) can be opened to group related events. Cases contain [episodes](glossary.md) (distinct phases such as a crisis period or a referral process) and may track [outcome goals](glossary.md) and milestones to measure impact beyond simple activity counts.

### Work Items (Arbeitsinfos)

Separate from documentation, [work items](glossary.md) cover the operational layer: notes ("Mail for Maus is in the drawer") and tasks ("Follow up with Frau M. about the appointment"). They replace the analog handover logbook, have their own lifecycle (open, in progress, done, discarded), and appear in each staff member's inbox. Their retention periods are shorter than those of documentation records.

## Data Model Overview

The data model follows a clear hierarchy with facility scoping as the primary boundary:

```
Organization (background entity, one per installation)
  +-- Facility (the concrete site -- primary scope for all data)
        +-- Users (staff with role assignments)
        +-- Clients (pseudonymized persons with contact levels)
        +-- Events (timestamped documentation entries, typed by DocumentType)
        +-- DocumentTypes (configurable, with FieldTemplates)
        +-- TimeFilters (named time windows for shift-based views)
        +-- WorkItems (notes and tasks with lifecycle)
        +-- Cases (grouping related events, with Episodes and OutcomeGoals)
        +-- AuditLog (append-only, immutable)
```

Every entity carries a `facility_id` foreign key. A middleware filter ensures that every database query is scoped to the current facility, preventing cross-tenant data access. The Organization layer exists in the schema but is hidden in the UI -- it is a prepared extension point for providers operating multiple sites.

## Privacy Architecture

Anlaufstelle processes social data of highly vulnerable people. Privacy is not an add-on but a structural design principle:

- **Pseudonymization by design.** There is no real-name field. The pseudonym is the primary identifier. The mapping to a real person exists only in staff knowledge.
- **Field-level encryption.** Sensitive fields (health data, counseling notes) are encrypted with Fernet/AES-128 before storage. The encryption key is not stored in the database. Key rotation is supported.
- **Retention periods.** Configurable per contact level and documentation type. Anonymous contacts are aggregated after 12 months (counts kept, individual records deleted). Identified contacts are deleted after a configurable period (e.g. 36 months after last contact).
- **Deletion requests.** Deletion of qualified data requires a four-eyes approval (two authorized users).
- **Audit log.** An append-only, immutable log records all security-relevant actions: data access, modifications, deletions, login attempts, and exports. Since v0.20, each row carries a per-facility HMAC chain so that later tampering or deletion of individual entries is detectable (`verify_audit_chain`, run nightly).
- **Breach detection.** Heuristic incident detection (GDPR Art. 33/34) flags mass deletions of person data, credential-stuffing bursts against unknown usernames, and distributed victim-lockout patterns, delivering SSRF-hardened webhook alerts. Error tracking stays off by default; when enabled, a scrubber strips PII before events leave the installation.
- **Facility scoping.** Every query filters on `facility_id`, enforced by middleware. Since v0.10, PostgreSQL Row Level Security provides defense-in-depth on all facility-scoped tables, with session-variable-driven policies that enforce the tenant boundary at the database layer itself.
- **Two-factor authentication.** Since v0.10, TOTP-based 2FA is available and can be enforced per user or facility-wide. Single-use backup codes and an account lockout back it up, the privileged roles (`super_admin`/`facility_admin`) are enforced into MFA by default, and the TOTP secret itself is encrypted at rest (Fernet, ADR-031).
- **GDPR compliance.** Data subject rights (access, rectification, deletion, portability) are implemented as system functions. GDPR documentation templates (processing register, DPIA, DPA, TOMs) ship with the app ([`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/), since #784).

## Role Model

Anlaufstelle uses five roles with increasing privileges (Refs #867, [ADR-018](../adr/018-rollenmodell-superadmin.md)):

| Role | German | Description |
|---|---|---|
| **Assistant** | Assistenz | Read access with limited documentation rights. Cannot view qualified contact details or edit other users' entries. |
| **Staff** | Fachkraft | Core documentation role. Can capture contacts, manage work items, search, and access qualified data within their facility. |
| **Lead** | Leitung | Supervisory role. Everything Staff can do, plus pseudonym management, contact level changes, statistics, exports, and deletion approvals. |
| **Application manager** | Anwendungsbetreuung (`facility_admin`) | Full control within one facility: user management, document-type configuration, settings, audit log, GDPR package. |
| **Super-Admin** | Super-Admin (`super_admin`) | Installation-wide system administration across all facilities; not bound to a single facility, system area (`/system/`) only. |

Access is not purely role-based but context-dependent: what a user can see also depends on the contact level of the person, the sensitivity of the documentation type, and field-level sensitivity overrides.

## Non-Functional Requirements

- **Accessibility:** WCAG 2.1 Level AA for all core and operations interfaces. Keyboard navigation, screen reader support, minimum 4.5:1 contrast ratio.
- **Mobile / PWA:** Progressive Web App, installable on home screens. Optimized quick-capture for smartphones with large touch targets and autocomplete. Field-ready offline mode: staff take selected persons offline into an encrypted local cache, read and edit their records, and create new contacts and tasks without connectivity; the timeline and task list also render offline in-place at their canonical URLs from the local snapshot. Offline work syncs on reconnect with explicit, resolvable conflicts rather than silent overwrites, and permanently undeliverable entries surface in a dead-letter view instead of disappearing.
- **Internationalization:** Fully internationalized via Django's `gettext` system. German (primary) and English are active. Language switcher in the UI. Domain libraries carry a language tag for future multilingual seeds.
- **Performance targets:** Page load < 1s (3G), contact save < 500ms, autocomplete < 300ms, six-month statistics query < 5s at 50,000 events.
- **Scaling target:** 5--30 concurrent users, up to 50,000 events/year per facility.
- **Deployment:** Single `docker compose up` on a VPS, local server, or Raspberry Pi. Minimum: 1 vCPU, 1 GB RAM, 10 GB storage.
- **Availability:** 99% target. Health-check endpoint at `/health/` for monitoring integration.

## Version Additions

Capabilities added since the base concept, release by release. Configuration and operational details live in the [admin guide](../admin-guide.md) and the [CHANGELOG](../../CHANGELOG.md); this section summarizes the conceptual intent. Anlaufstelle is still a pre-release and **not yet cleared for production use**.

### v0.10.0 (2026-04-19)

Release v0.10.0 sharpens the low-threshold fit and strengthens the privacy architecture.

- **Offline mode (M6A).** Street work teams frequently operate without connectivity. An optional offline mode provides a client-side capture and read cache encrypted with AES-GCM-256. The key is a non-extractable `CryptoKey` derived from the user password via PBKDF2, persisted (not merely in-memory) in a dedicated IndexedDB for the duration of the server session; it is wiped on logout, password change, role revocation/deactivation, and on session-idle -- but not on tab close -- so a stolen device without an active session yields only ciphertext. Read, write-queue, and in-app offline-**edit** (with side-by-side conflict resolution and a dead-letter view for permanently undeliverable entries) are the accepted scope; the edit entry has been wired and tested since #1111 (no longer deferred).
- **File vault.** Attachments on events are no longer stored in the clear. They are scanned by ClamAV before acceptance and encrypted at rest, extending field-level encryption to binary material.
- **Two-factor authentication.** TOTP-based second factor, configurable per user or enforced facility-wide, hardens login for facilities handling qualified data.
- **Fuzzy search.** Pseudonyms are often misremembered or misspelled. A typo-tolerant search based on PostgreSQL `pg_trgm` trigrams, with a per-facility similarity threshold, helps staff find known persons without resorting to broader disclosures.
- **Row Level Security.** PostgreSQL RLS policies on all facility-scoped tables (23 as of v0.10.2, including transitive scopes such as `OutcomeGoal`, `Milestone`, and `DocumentTypeField`) add a database-level safety net beneath the middleware filter. A session variable carries the current facility identity into the database, set per request and explicitly cleared on anonymous requests, so even a bypassed ORM layer cannot cross tenants.
- **Optimistic locking.** Client, Case, WorkItem, Settings, and Event records use version counters to prevent silent overwrites when two users edit the same record concurrently -- a realistic scenario in shift-based work.
- **Retention dashboard and legal hold.** The automated retention workflow gains a review surface: leads bulk approve, defer, or reject deletion proposals. A Legal Hold flag excludes individual records from deletion when investigations or disputes require preservation. K-anonymization is available as an alternative to hard deletion, preserving statistics while removing identifying detail.
- **Quick templates.** Admins maintain pre-filled event templates for recurring situations; templates are filtered by the user's role and the sensitivity of their fields so that assistants never see templates containing qualified-level content.
- **Token invite flow.** New users receive a one-time token link instead of a plaintext initial password. Invitees choose their own password on first use, closing a long-standing onboarding weak spot.

### v0.10.1 (2026-04-26)

- **Backup codes for 2FA.** When enabling TOTP, users receive ten single-use backup codes that may be entered at the 2FA login prompt instead of an authenticator code. Consumed codes are invalidated and recorded in the audit log. This closes a recovery gap that previously required an admin reset whenever a user lost their authenticator device.
- **Account lockout.** After ten consecutive failed login attempts, the account is locked. Admins unlock affected users from the profile screen; both events appear in the audit log. Combined with the per-route rate limits on every mutating endpoint (added systematically in v0.10.1), this raises the cost of credential-stuffing attacks against pseudonymized client lists.
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

### Unreleased

- **Offline "one world"** (offline V2, #1499): contacts and tasks can be captured offline even without a previously opened form, and the task list and timeline render offline in-place at their canonical URLs from the merged local snapshot.
- **Extended breach detection**: a mass client-deletion heuristic, anonymous login bursts per source IP, a distributed victim-lockout signature, and a secondary 24-hour low-and-slow window.
- **TOTP secrets encrypted at rest** (Fernet/MultiFernet, ADR-031); privilege-change audits now name the acting admin; the error-tracking scrubber closes remaining PII gaps (opt-in Sentry/GlitchTip).

<!-- translation-source: docs/fachkonzept-anlaufstelle.md -->
<!-- translation-version: v0.20.0 -->
<!-- translation-date: 2026-07-13 -->
<!-- source-hash: 9575af2 -->
