> This document summarizes the German domain concept ([Fachkonzept](../fachkonzept-anlaufstelle.md)).
> It is not a 1:1 translation. For the complete specification, refer to the German original.
> See also the [Glossary](glossary.md) for domain term translations.

# Domain Concept Summary

## What is Anlaufstelle?

Low-threshold social services -- drop-in centers, emergency shelters, street work teams, and day centers serving people experiencing homelessness or addiction -- document their work almost entirely on paper: handwritten visitor lists, A4 logbooks passed between shifts, and self-built Excel sheets. The semi-annual report to the youth welfare office regularly takes facility managers two to three weeks of manual data aggregation. This is not a failure of the facilities; it is a rational response to the absence of suitable tools.

Commercial social-sector software (Vivendi, SoPart, Kilanka) targets large providers with hundreds of staff. Licenses are expensive, rollouts require weeks of consulting, and every system assumes that each person is registered with a real name and address. In reality, most contacts at low-threshold facilities are anonymous. Clients have the right not to disclose their identity. A system that asks for a name first has not understood the working reality.

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
- **Audit log.** An append-only, immutable log records all security-relevant actions: data access, modifications, deletions, login attempts, and exports.
- **Facility scoping.** Every query filters on `facility_id`, enforced by middleware. Since v0.10, PostgreSQL Row Level Security provides defense-in-depth on all facility-scoped tables, with session-variable-driven policies that enforce the tenant boundary at the database layer itself.
- **Two-factor authentication.** Since v0.10, TOTP-based 2FA is available and can be enforced per user or facility-wide.
- **GDPR compliance.** Data subject rights (access, rectification, deletion, portability) are implemented as system functions. GDPR documentation templates (processing register, DPIA, DPA) are on the roadmap.

## Role Model

Anlaufstelle uses four roles with increasing privileges:

| Role | German | Description |
|---|---|---|
| **Assistant** | Assistenz | Read access with limited documentation rights. Cannot view qualified contact details or edit other users' entries. |
| **Staff** | Fachkraft | Core documentation role. Can capture contacts, manage work items, search, and access qualified data within their facility. |
| **Lead** | Leitung | Supervisory role. Everything Staff can do, plus pseudonym management, contact level changes, statistics, exports, and deletion approvals. |
| **Admin** | Admin | Full system control. User management, documentation type configuration, system settings, and audit log access. |

Access is not purely role-based but context-dependent: what a user can see also depends on the contact level of the person, the sensitivity of the documentation type, and field-level sensitivity overrides.

## Non-Functional Requirements

- **Accessibility:** WCAG 2.1 Level AA for all core and operations interfaces. Keyboard navigation, screen reader support, minimum 4.5:1 contrast ratio.
- **Mobile / PWA:** Progressive Web App, installable on home screens. Optimized quick-capture for smartphones with large touch targets and autocomplete. Offline draft saving via Service Worker for the quick-capture form.
- **Internationalization:** Fully internationalized via Django's `gettext` system. German (primary) and English are active. Language switcher in the UI. Domain libraries carry a language tag for future multilingual seeds.
- **Performance targets:** Page load < 1s (3G), contact save < 500ms, autocomplete < 300ms, six-month statistics query < 5s at 50,000 events.
- **Scaling target:** 5--30 concurrent users, up to 50,000 events/year per facility.
- **Deployment:** Single `docker compose up` on a VPS, local server, or Raspberry Pi. Minimum: 1 vCPU, 1 GB RAM, 10 GB storage.
- **Availability:** 99% target. Health-check endpoint at `/health/` for monitoring integration.

## v0.10 Additions

Release v0.10.0 (2026-04-19) extends the domain concept with several capabilities that sharpen the low-threshold fit and strengthen the privacy architecture. Configuration details live in the [admin guide](../admin-guide.md); this section summarizes the conceptual intent.

- **Offline mode (M6A).** Street work teams frequently operate without connectivity. An optional offline mode provides a client-side capture and read cache encrypted with AES-GCM-256. The encryption key is derived from the user password via PBKDF2, lives only in browser memory, and is destroyed on logout -- no plaintext cache ever touches disk.
- **File vault.** Attachments on events are no longer stored in the clear. They are scanned by ClamAV before acceptance and encrypted at rest, extending field-level encryption to binary material.
- **Two-factor authentication.** TOTP-based second factor, configurable per user or enforced facility-wide, hardens login for facilities handling qualified data.
- **Fuzzy search.** Pseudonyms are often misremembered or misspelled. A typo-tolerant search based on PostgreSQL `pg_trgm` trigrams, with a per-facility similarity threshold, helps staff find known persons without resorting to broader disclosures.
- **Row Level Security.** PostgreSQL RLS policies on sixteen facility-scoped tables add a database-level safety net beneath the middleware filter. Session variables carry the current facility identity into the database, so even a bypassed ORM layer cannot cross tenants.
- **Optimistic locking.** Client, Case, WorkItem, Settings, and Event records use version counters to prevent silent overwrites when two users edit the same record concurrently -- a realistic scenario in shift-based work.
- **Retention dashboard and legal hold.** The automated retention workflow gains a review surface: leads bulk approve, defer, or reject deletion proposals. A Legal Hold flag excludes individual records from deletion when investigations or disputes require preservation. K-anonymization is available as an alternative to hard deletion, preserving statistics while removing identifying detail.
- **Quick templates.** Admins maintain pre-filled event templates for recurring situations; templates are filtered by the user's role and the sensitivity of their fields so that assistants never see templates containing qualified-level content.
- **Token invite flow.** New users receive a one-time token link instead of a plaintext initial password. Invitees choose their own password on first use, closing a long-standing onboarding weak spot.

<!-- translation-source: docs/fachkonzept-anlaufstelle.md -->
<!-- translation-version: v0.10.0 -->
<!-- translation-date: 2026-04-19 -->
<!-- source-hash: cd5148b -->
