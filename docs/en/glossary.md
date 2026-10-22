# Glossary — Domain Terms (DE↔EN)

> Bilingual glossary of domain-specific terms used in Anlaufstelle.
> German terms appear in the codebase, database, and German documentation.
> English terms are used in the UI (when set to English) and English documentation.

---

## Term table (A–Z)

| Deutsch | English | Definition |
|---------|---------|------------|
| Account-Lockout | Account lockout | Automatic account lock after 10 failed login attempts within a sliding 15-minute window, counted per pair of account and source IP address. The lock state is not stored separately but derived from the `LOGIN_FAILED` AuditLog entries; the lock therefore ends by itself once fewer than 10 counted failed attempts lie within the window -- without new failed attempts at the latest 15 minutes after the last one. Requests rejected by the rate limit (HTTP 429) are not counted. A sign-in with the correct password during the lock is recorded as `LOGIN_FAILED` with `reason: "locked"` and shown as a form error. `super_admin` is exempt from this account lock; sign-in rate limits (5/min per IP address, 10/h per username, beyond that HTTP 429) and 2FA are unaffected. Unlocking via self-service on the login page or by the administration (Management > Access, System area > Locked accounts, CLI `unlock`) writes a `LOGIN_UNLOCK` entry; earlier `LOGIN_FAILED` entries no longer count afterwards. Implemented in [`src/core/services/security/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/login_lockout.py). |
| AdminCSPRelaxMiddleware | Admin CSP relax middleware | CSP relaxation for HTML responses under `/admin-mgmt/`: adds `'unsafe-eval'` to `script-src` for Alpine bundled with django-unfold, and `'unsafe-inline'` to `style-src` (without hash/nonce tokens). The global CSP stays strict. Admin access requires System administration (`super_admin`) or Application support (`facility_admin`) and a confirmed session when sudo mode is enabled; the admin sign-in page is accessible anonymously. See [`src/core/middleware/admin_csp_relax.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/admin_csp_relax.py). |
| Altersgruppe | Age group | Coarse age group for a person (under 18, 18–26, 27+, or unknown). The catalog is fixed system-wide and cannot be configured per facility ([ADR-047](../adr/047-altersgruppen-systemweit-fix.md), German). Used for statistics without requiring an exact date of birth. The field is still called `age_cluster` in code (per the language guideline, code stays outside). |
| Anlaufstelle | Drop-in center | Name of the application. Also colloquial for the facility itself — the place where people come for help. |
| Arbeitsinfo | Work item | Collective term for notes and tasks — operational entries that are separate from case documentation. See: WorkItem. |
| Arbeitszentrale | Work center | Overview for the Manager role at `/start/`, with tiles for deletion requests, retention proposals and statistics. Only managers see the sidebar link. Social workers and assistants are redirected to the Timeline (`/`), Application support to Management (`/verwaltung/`), and System administration to the system area (`/system/`). The Timeline (`/`) remains the default operational view. |
| Audit-Trail | Audit trail | Immutable log of all security-relevant actions in the system: access, changes, deletions, login attempts. Serves GDPR compliance and traceability. See: AuditLog. |
| AuditLog | Audit log | Technical term for the audit trail. A dedicated entity stored separately from case documentation. Immutable (append-only). |
| Backup-Codes | Backup codes | Ten single-use recovery codes for 2FA emergencies, each with 128 bits of randomness. Stored as 16 Base64url characters of a purpose-separated HMAC-SHA256 (96 bits), key derived from `SECRET_KEY`; earlier SHA digests and the old input format are rejected, key changes invalidate codes. At 2FA sign-in they replace the TOTP code but do not change devices; “Neues Gerät einrichten” (set up a new device) uses sudo for that. Consumption records `BACKUP_CODES_USED`. Anonymous unlocking: 5/min per IP and 10/h per normalized username, without increasing the device throttle. Without a valid factor or code, the admin reset (`MFA_RESET_BY_ADMIN`) remains. |
| Benannter Zeitfilter | Named time filter | A saved working period with a label (e.g. "Night shift 21:30–09:00"), start time and end time. The facility-scoped `TimeFilter` model provides quick filtering in the Timeline and statistics. |
| Betreiber-Empfehlung | Operator recommendation | A recommendation for operating a facility that a package cannot capture (e.g. retention periods, k-threshold, time windows, quick templates, roles). Documented in the respective package guide (`docs/packages-<domain>.md`) rather than in the package itself. |
| Case | Case | A case — a bracket around related work with a person. Contains episodes, assignments, and optionally outcome goals. |
| Chronik | Timeline | The chronological sequence of all events documented for a person. The timeline is the primary view of a person in Anlaufstelle. |
| ClamAV | ClamAV | Open-source malware scanner running as a sidecar service. All file uploads are scanned before being encrypted and stored. Fail-closed: uploads are rejected if ClamAV is unreachable. |
| Client | Person | Technical term for a person in the system. Managed under a pseudonym. Has a contact stage that determines their lifecycle in the system. |
| CSP | Content Security Policy | Browser-level security policy that restricts which sources (scripts, styles, images, frames) the page may load. Anlaufstelle sets the policy centrally in Django via [`django-csp`](https://django-csp.readthedocs.io/) (see [`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py)). The global `script-src` does not carry `'unsafe-eval'`; the only exception is `/admin-mgmt/*` via `AdminCSPRelaxMiddleware`. |
| Dexie.js | Dexie.js | Minimal IndexedDB wrapper used for the secure offline store. Vendored under `src/static/js/dexie.min.js` (Apache-2.0, v4.4.6). |
| DocumentType | Document type | A configurable category of events (e.g. "Contact", "Crisis counseling", "Needle exchange"). Defines fields, sensitivity, and retention period. |
| DocumentTypeField | Document type field | Association of a field template (FieldTemplate) with a document type (DocumentType). Determines field order in the form and allows reuse of field templates across multiple types. |
| Domänen-Paket | Domain package | The technical realization of the domain library: a versionable YAML catalog of preconfigured document types and fields for a specific facility type, applied via `setup_facility --package <name>` or `manage.py load_package`. See: Domain library, [docs/packages.md](../packages.md). |
| Domänenbibliothek | Domain library | Preconfigured set of document types for a specific facility type (e.g. "Low-threshold addiction services"). Seed data loaded during initial setup. Technically realized as a domain package. |
| DSGVO | GDPR | General Data Protection Regulation — the European regulation for the protection of personal data. Together with social data protection (SGB X), the central legal framework for Anlaufstelle. |
| Einrichtung | Facility | A concrete site or location. The primary scope boundary for social workers members. All entities have a foreign key to Facility. |
| Episode | Episode | A distinguishable phase within a case: e.g. a crisis phase, a referral process. |
| Ereignis | Event | The central building block of documentation. A timestamped entry recording an occurrence. Belongs to a document type and optionally to a person. See: Event. |
| Event | Event | Technical term for an occurrence record (Ereignis). See: Ereignis. |
| Externer Bericht | External report | Privacy-friendly statistics report at `/statistics/external/` (Refs #921): no pseudonym ranking, K-anonymity suppression of small aggregates, privacy-profile header; HTML or JSON. Manager/Admin only; each call audit-logged as `EXPORT`. |
| Facility | Facility | Technical term for an institution or site (Einrichtung). The primary scope boundary for social workers. All entities carry a foreign key to Facility. |
| FieldTemplate | Field template | Defines a field within a document type: name, data type, required flag, options, encryption, statistics mapping. |
| File Vault | Encrypted file vault | File attachments stored encrypted at rest (Fernet/AES-128). Files are scanned by ClamAV before encryption. Downloads use a central safe-download helper with RFC-5987 Content-Disposition. |
| Fuzzy-Schwellwert | Fuzzy threshold / Trigram threshold | Per-facility similarity cutoff (`Settings.search_trigram_threshold`, 0.0–1.0, default ~0.3). Lower = more matches, more noise. |
| Fuzzy-Suche | Fuzzy search | Typo-tolerant search using PostgreSQL `pg_trgm` trigram similarity. Finds "Müller" when searching for "Muller". |
| Hausverbot | Site ban | A ban prohibiting a person from entering a facility, with reason, validity period, and issuer. Modeled in Anlaufstelle as a document type with system type `ban`. |
| Inbox | Inbox | Personal overview of all open notes and due tasks for the logged-in social workers member. Part of the operations layer. |
| IndexedDB | IndexedDB | Browser-native database used by the offline store. Holds AES-GCM-encrypted events, drafts, and cached people. |
| JSONB | JSONB | PostgreSQL data type for binary JSON. Used in Anlaufstelle to store event field values together with the event record. Indexable (GIN index), queryable, performant. |
| K-Anonymisierung | K-anonymization | Used two ways: (a) bucket suppression of small aggregates in the external report; (b) the retention path (`retention_use_k_anonymization`) that generalizes the person record when its group is large enough and retains the age group for statistics. See [ADR-023](../adr/023-k-anonymization-statistik.md). |
| k-Anon-Retention | k-anon retention | The retention path that k-anonymizes the person record. With the option enabled and at least k people in the equivalence class, the age group is retained; otherwise regular anonymization applies. Both paths redact the associated free-text cascade, the k-anon path in the bridge layer. The `k_anonymize_client` primitive itself only changes the person record. See details below. |
| Kontaktstufe | Contact stage | How much is known about a person in the system. The data model has two contact stages (`Client.ContactStage`): **Pseudonym only** (value `identified` — only the pseudonym is on file) and **With additional details** (value `qualified` — further personal details, visibility by role and sensitivity; opening the detail page is logged, not every access to the details — the edit form shows them without writing an entry). The values stay `identified`/`qualified` and carry the API, exports, and the CSV data contract. Determines permitted document types, deletion workflows and retention periods; visibility depends on role and sensitivity. A purely anonymous contact (count only) is not a contact stage but a person-less event (`Event.is_anonymous`). |
| Legal Hold | Legal hold | A separate retention hold entry on an event, person or case, not a flag on the record. Excludes the target and, for a person or case, their events from automated retention deletion and anonymization while the hold is active, for example during ongoing proceedings. See details below. |
| Materialized View | Materialized view | Pre-aggregated PostgreSQL view (`core_statistics_event_flat`) that backs the statistics dashboard so reports do not re-scan the entire event table on every request (Refs #544). Refreshed periodically via management command, ideally `CONCURRENTLY` (requires the unique index that the migration provides). |
| Milestone | Milestone | A concrete step toward an outcome goal. |
| Offline-Modus (M6A) | Offline mode (M6A) | Client-side encrypted offline capture for streetwork. Uses AES-GCM-256 with a PBKDF2-derived (600 000 iterations, SHA-256) non-extractable `CryptoKey` held in a dedicated IndexedDB (`anlaufstelle-crypto`); the raw key bytes cannot be exported through WebCrypto. Deleted on logout and password change. After session idle, the app removes the key the next time it runs; unsent work stays encrypted (locked until the next sign-in), otherwise the offline store is deleted as well. Role revocation and deactivation rotate the salt on the server, so the old data can no longer be decrypted after the next sign-in. Closing the tab or restarting the device deletes nothing. Within the idle window, the app can open offline without signing in again. Read, write-queue, **and** in-app offline-**edit** (incl. the conflict/dead-letter view at `/offline/conflicts/`) are the accepted production scope. See [ADR-022](../adr/022-offline-snapshot-keys.md). |
| Optimistic Locking | Optimistic locking | Concurrency control using `updated_at`. Prevents silent overwrites on Client, Case, WorkItem, Settings and Event. An outdated timestamp produces a conflict; reload the current state and enter your changes again. |
| Organisation | Organization | The carrier or parent entity — the top level of the hierarchy. Exactly one organization exists, created automatically and hidden in the UI. Serves as a prepared scope for future multi-carrier support. A branding shell ([ADR-018](../adr/018-rollenmodell-superadmin.md), variant b1); no cross-facility visibility through the organization. |
| Outcome | Outcome | The result of working with a person. Not the activity ("347 contacts") but the change ("stable housing situation achieved"). |
| OutcomeGoal | Outcome goal | What should be achieved through the work with a person. Assigned to a case. |
| Personenbezogene Daten | Personal data (PII) | Information that identifies a natural person directly (name, address, person ID) or indirectly via *quasi-identifiers* (age, region, subject in combination) — Art. 4(1) GDPR. Health/social data are *special categories* (Art. 9). Highest leak risk: free text. |
| Pseudonym | Pseudonym | A name assigned by the team to identify a person in the system. Primary identifier in Anlaufstelle. The intended workflow keeps the mapping to the real name in staff knowledge; there is no required field for real names. The system does not technically prevent real names — pseudonyms, notes, free-text fields and attachments contain whatever the team enters. |
| Pseudonymisierung | Pseudonymization | Replaces direct identifiers with a pseudonym; the individual record persists and stays re-identifiable with additional knowledge. Weaker than anonymization (see K-anonymization). |
| PWA | Progressive Web App | Installable web app for smartphones and desktops. Provides home-screen install, an app-like shell, and offline behavior coordinated by the Service Worker. Used in Anlaufstelle for the streetwork quick-capture flow. |
| Quasi-Identifikator | Quasi-identifier | An attribute that does not identify on its own but does in combination with others. The k-anonymization equivalence class is `(facility, age_cluster, contact_stage)` ([ADR-023](../adr/023-k-anonymization-statistik.md)). |
| Quick Template | Quick template | Pre-filled event template maintained by admins. Applied by clicking a button on "Document contact"; fills empty fields only; self-heals against deactivated select options. |
| Quick-Exit | Quick exit | Quick-exit action in the header bar of every signed-in page (button + keyboard shortcut `Ctrl+Shift+X` / `Shift+Esc`): instantly hides the content, ends the session, and opens a neutral, facility-configured page. Separately, the optional privacy screen (facility setting) hides the content behind a neutral surface on window or tab switch, until a key press or click releases it. Not a disguise mode, no interface rebranding. A data-protection term, see [data-protection.md](data-protection.md). |
| Retention Proposal | Retention proposal | Individual deletion/anonymization suggestion surfaced on the retention dashboard. Can be approved, deferred, or rejected in bulk. |
| Role | Role | Determines which actions a user may perform. Five roles: System administration (`super_admin`, installation-wide system control, no facility), Application support (`facility_admin`, full access within one facility), Manager (`lead`, professional leadership), Social worker (`staff`), Assistant (`assistant`). Only System administration works across facilities. |
| Row Level Security (RLS) | Row Level Security (RLS) | PostgreSQL protection on facility-scoped tables. Policies read `app.current_facility_id` and fail closed for regular users without a facility. System administration is allowed across facilities through `app.is_super_admin`. Both variables are set per request session-wide (not via `SET LOCAL`) and cleared for anonymous requests. See [`src/core/middleware/facility_scope.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/facility_scope.py) and [`src/tests/test_rls.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_rls.py). |
| Scope | Scope | Visibility boundary. Accessible data depends on facility, role and sensitivity. The contact stage affects permitted document types and deletion workflows. |
| Sensitivität | Sensitivity | Classification of a document type or field regarding its protection level. Controls which roles may access field values. Configurable per document type and per field (`FieldTemplate.sensitivity`). Independent of field encryption (`is_encrypted`). |
| Service Worker | Service worker | Browser script coordinating offline caching, queue replay, and PWA behavior. See `src/static/js/sw.js`. |
| Soft-Delete-Strategie | Soft-delete strategy | Retention rules apply per event: `anonymous` and `identified` count from `occurred_at`; `qualified` counts from `occurred_at` without a case, from `closed_at` for a closed case, or from the last professional case activity for an open case (falling back to case creation, also when the closing timestamp is missing). A documentation-type period applies additionally; whichever expires first applies. The daily run deletes anonymous and pseudonym-only events automatically; qualified events receive proposals and require manual approval by two different eligible people with deletion confirmation permission before a later run deletes them. Exception: auto-approval after repeated deferrals is opt-in and off by default. Active holds and Art. 18 restrictions block deletion. A person is anonymized once all their events are deleted; Without events, the `retention_identified_days` inactivity period applies only to pseudonym-only people without open cases. The anchor is the latest person change (`updated_at`), substantive case activity (`last_activity_at`), task change or attachment change on the person or case (creation, replacement or removal). People with additional details without events are excluded from automatic anonymization; the proposal path currently supports only events, and automatic retention enforcement for these people is missing. Pseudonym-only people without events but with an open case are excluded from the inactivity path. The facility regularly reviews people with additional details without events and submits a deletion request where necessary; it records the review interval in the deletion concept. See [`anonymization.py`](../../src/core/retention/anonymization.py). Holds and Art. 18 protect both paths. See [ADR-021](../adr/021-retention-modell.md). |
| Speicherbegrenzung | Storage limitation | GDPR Art. 5(1)(e) principle: do not keep personal data longer than necessary for the purpose. Implemented via the retention model ([ADR-021](../adr/021-retention-modell.md)). |
| TimeFilter | Time filter | Technical term for a named time filter. Belongs to a facility and defines a time window (start time, end time) with a label. |
| Token-Einladung | Token invite | Token-based invitation flow: an administrator creates an account, the invited person receives an email with a one-time link and sets their own password. The link is valid for 3 days by default; configurable via `DJANGO_INVITE_TOKEN_TIMEOUT` (seconds). |
| TOTP / 2FA | TOTP / 2FA | Time-based one-time passwords via `django-otp`, complemented by passkeys and backup codes. In production mandatory for all five roles through the role default: immediately for `super_admin` and `facility_admin` (`MFA_ENFORCED_ROLES_IMMEDIATE`), after a 30-day transition period from the first sign-in for `lead`, `staff` and `assistant` (`MFA_ENFORCED_ROLES_GRACE`). Additionally enforceable immediately per user (`User.mfa_required`) or facility-wide (`Settings.mfa_enforced_facility_wide`). Codes valid for 30 s. |
| User | User | A social workers member — a person who works with the system. Has credentials and a role assignment within a facility. |
| WorkItem | Work item | An operational entry (note or task) with its own lifecycle and optional priority. Separate from case documentation. |
| Zeitfenster (kalendarisch vs. rollierend) | Time window (calendar vs. rolling) | Two kinds of period statement that must never share a word (Refs #1804). *Calendar* = a unit tied to the calendar (ISO week Mon–Sun, calendar month, calendar year); calendar words are reserved for these. *Rolling* = a window of fixed length that moves with today; it is named after its length (*Last 30 days*, *Next 7 days*). Both ends are inclusive and the length matches the name: *Last 30 days* = `today−29 … today`. |
| Timeline | Timeline | The chronological flow of all events, unfiltered or filtered by time period, person, or document type. The core metaphor of documentation in Anlaufstelle. |

## K-anonymity in detail

**K-anonymity** is a privacy principle against re-identification in statistics
and reports. An analysis is *k-anonymous* when each person is indistinguishable
from at least **k − 1** other people with respect to the characteristics being
analyzed — each combination of characteristics therefore occurs **at least k times**.

**Everyday example (k = 5):** A statistic groups contacts by *age group* and
*gender*. If a facility has only **one** person in the combination
“under 18 / diverse”, that row would effectively reveal a single person even
though the statistic appears “anonymous”. With k = 5, a group of fewer than
5 people is **suppressed** (no numeric value, but “—” or `suppressed`) to reduce
the risk of attributing an aggregate to individual people.

**In Anlaufstelle:**

- The threshold is configurable per facility through
  [`k_anonymity_threshold`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py)
  (**default 5**).
- Privacy-friendly external reports
  ([`core/services/dashboard/external_report.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/external_report.py))
  apply the threshold to all aggregates and remove pseudonym rankings entirely.
- Optionally, the retention path k-anonymizes the person record (setting
  `retention_use_k_anonymization`) if its equivalence class includes at least k
  people; otherwise regular anonymization applies. **Both paths redact the same
  free-text cascade** on cases, episodes and tasks (in the retention bridge
  layer); the `k_anonymize_client` primitive itself only changes the person record.
  See **k-anon retention** below.

Distinction: **Pseudonymization** replaces direct identifiers with a pseudonym
(the individual record persists); **k-anonymity** additionally protects against
re-identification through *combinations* of indirect characteristics in aggregates.

## Data protection terms

**Personal data / PII** — Information that identifies a natural person directly
(name, address, person ID) or indirectly through *quasi-identifiers* (age, region,
subject in combination), under Art. 4(1) GDPR. Health/social data are *special
categories* (Art. 9). Highest leak risk: free text.

**Pseudonymization** — Replaces direct identifiers with a pseudonym; the individual
record persists and can be re-identified with additional knowledge. Weaker than
anonymization. For the distinction from **k-anonymity**, see above.

**Storage limitation** — GDPR Art. 5(1)(e) principle: do not keep personal data
longer than necessary for the purpose. Implemented in Anlaufstelle through the
retention model ([ADR-021](../adr/021-retention-modell.md)).

**Soft-delete strategies** — Four strategies per event: anonymous and
identified from the event date; qualified from case closure, without a case
from the event date, in an open case from the last substantive activity
(fallback creation). The document type setting is a **maximum period (shortens
only)**; the earliest deadline wins. The regular run creates proposals for
additional details and only deletes after manual approval by two different eligible people with
deletion confirmation permission. Deferral protects until its date; rejection
allows a new proposal only after the retry interval.

The run creates the proposal. Manual approval requires two different active people from the same facility, each with the Manager or Application support role and deletion confirmation permission. The first approval leaves the proposal pending (PENDING, “1 of 2 approvals”); only the second, different person gives final approval (APPROVED). With only one eligible account, it remains at “1 of 2”; the first approval alone never permits deletion. Deferral, legal hold (including from person, case or event records) and rejection discard the first approval with an audit record; a new manual approval workflow requires both approvals again. The facility designates at least two eligible accounts. The “Approvers for the retention run” compliance card checks role and count (0 = critical, 1 = warning, at least 2 = OK). The “Approvers for deletion requests” card still counts active deletion-request approvers regardless of role. Exception: the facility may explicitly enable auto-approval after repeated deferrals (opt-in, off by default); after the configured maximum number of deferrals and expiry of the follow-up date, the run approves without a second person. Holds and Art. 18 restrictions protect this path as well. Automatic approval is
opt-in. Without events, the `retention_identified_days` inactivity period applies only to pseudonym-only people without open cases. The anchor is the latest person change (`updated_at`), substantive case activity (`last_activity_at`), task change or attachment change on the person or case (creation, replacement or removal). People with additional details without events are excluded from automatic anonymization; the proposal path currently supports only events, and automatic retention enforcement for these people is missing. Pseudonym-only people without events but with an open case are excluded from the inactivity path. The facility regularly reviews people with additional details without events and submits a deletion request where necessary; it records the review interval in the deletion concept. See [`anonymization.py`](../../src/core/retention/anonymization.py). People with events are anonymized after all events are deleted. Holds and Art. 18
protect these retention paths.

**Legal hold** — A separate hold entry on an event, person or case. Lead and
facility admin create and lift it in the record; system administration can
lift it in the system list. It protects the target and related events, plus
audit rows of protected targets. Creation and lifting have an audit trail
([ADR-021](../adr/021-retention-modell.md)).

**k-anon retention** — A variant of the retention path: once all a person's events
are soft-deleted and no active legal hold exists, the person record can be
k-anonymized (setting `retention_use_k_anonymization`, threshold
`k_anonymity_threshold`). The equivalence class must include at least k people;
otherwise regular anonymization applies. In the k-anon path, age group and contact
stage remain available for statistics; pseudonym, notes and dynamic details are
redacted. The bridge layer also removes the associated free-text cascade,
attachments and offline bundles, as in the regular path; the `k_anonymize_client`
primitive only changes the person record. This differs from **bucket suppression**
in external reports. Details: [ADR-021](../adr/021-retention-modell.md),
[ADR-023](../adr/023-k-anonymization-statistik.md).

**Quasi-identifier / equivalence class** — Characteristics that do not identify
on their own but do in combination. The k-anonymization equivalence class is
`(facility, age_cluster, contact_stage)`
([ADR-023](../adr/023-k-anonymization-statistik.md)).

**Offline snapshot keys** — Client-side encryption of the offline read cache
(streetwork mode, domain concept § 16). On sign-in, a **non-extractable**
AES-GCM-256 `CryptoKey` is derived from the user's password through PBKDF2
(600,000 iterations, SHA-256) and kept in a dedicated IndexedDB
(`anlaufstelle-crypto`). The raw key bytes cannot be exported, but the key itself
remains usable in the browser. After the idle window expires, the app removes it
the next time it runs (only the key when unsent work exists, otherwise the data
as well). A stolen device yields only ciphertext once the key has been removed;
within the idle window, the app opens offline without signing in again. Closing
the tab or restarting the device deletes nothing. Server bundles are limited
(50 events, 90 days, 48-hour TTL) and apply visibility rules before serialization.
Details: [ADR-022](../adr/022-offline-snapshot-keys.md).


## Offline key lifetime

**Offline key and revocation check** (#2528). The non-extractable `CryptoKey` derived from the password and server salt remains stored in browser IndexedDB across tab, browser and device restarts. “Non-extractable” prevents export through WebCrypto; protecting the stored key against access to the browser profile depends on **device encryption and screen locking**, which are mandatory for offline devices. Derivation freezes an absolute deadline using the facility's `session_absolute_hours` (default 12 hours). Activity and navigation never extend it. The app checks on load, before decryption/storage/replay, on tab return and every 60 seconds. At expiry it removes the key and displays “Offline key expired — please sign in online”; unsent work stays encrypted and becomes readable after another online sign-in with the same password and salt. The idle window still applies separately. Missing or invalid expiry metadata blocks access; bundles without valid `expiresAt` are expired. An observed backwards device clock locks the key, but clock manipulation remains a residual risk. A powered-off app cannot remotely delete the key.

An HTTP/network/processing error fetching revocations (including 500/429), or an invalid response structure, pauses queue and edit replay: “Synchronization is waiting for the revocation check”. The blocking state is stored in local IndexedDB. Each replay run also requires its own fresh, structurally valid, successfully applied and persisted revocation check; an earlier approval never authorizes a later run or another tab, even when metadata writes and fallback deletion both fail. Missing or unreadable metadata blocks replay; recovery from a read failure also requires another successful check. The next sync sequence checks again, as does a direct save from the offline client record. A failed check preserves unsent work in encrypted form. Existing idempotency keys remain intact, and the server gates (422 for revoked people, 409 for status conflicts) remain authoritative. Device encryption does not replace these server checks.

<!-- translation-source: docs/glossar.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-14 -->
<!-- source-hash: 1e7ba93051829313781597e7ad2fb821194aaf2a -->
