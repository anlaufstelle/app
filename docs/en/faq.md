# FAQ — retention, erasure, offline work and statistics

This English FAQ covers the Contacts list, the updated retention topic, the offline key lifetime
and revocation check addendum and the statistics guidance. Other topics remain in the
[German FAQ](../faq.md); see the [English user guide](user-guide.md) for daily work.

## Which date format should I enter?

With JavaScript loaded, fields follow the application language: German `DD.MM.YYYY`, English `YYYY-MM-DD`; times use the 24-hour clock (`HH:MM`, also `:SS` where second precision is supported). Correct invalid calendar dates and values outside the field bounds before saving. Without JavaScript, the native field uses the browser display.

## Contacts list

### Where is the list, and what can it not do?

Select **Contacts** directly below **Timeline** in the sidebar, or **More → Contacts** on mobile (`/events/`). The local Timeline link carries over day, documentation type and shift. From Assistant upwards, the list supports periods across multiple days and other team members’ entries, with the same facility scope and sensitivity limits as the Timeline. Case selection and display require Social worker or higher.

Search covers only **pseudonym and event search text**, from two characters onwards. Encrypted or effectively elevated/high field values, case titles and file contents are excluded. Person and case filters use selection fields without autocomplete or search suggestions. The list shows no field values and offers no export or bulk actions. List and filter requests create no audit entry. Filters stay in the address across pagination and Back/Forward. See the [Contacts list user guide](user-guide.md#contacts-list) and its [permissions table](user-guide.md#who-can-do-what).

## Retention and erasure

### How do retention periods work?

The regular daily `enforce_retention` run deletes anonymous and pseudonym-only
events after expiry. Events with additional details receive proposals;
two different eligible people with deletion confirmation permission must approve deletion. Deferral
protects until its date; rejection permits a new proposal no earlier than the
retry interval (default 365 days). Automatic approval remains opt-in.

The run creates the proposal. Manual approval requires two different active people from the same facility, each with the Manager or Application support role and deletion confirmation permission. The first approval leaves the proposal pending (PENDING, “1 of 2 approvals”); only the second, different person gives final approval (APPROVED). With only one eligible account, it remains at “1 of 2”; the first approval alone never permits deletion. Deferral, legal hold (including from person, case or event records) and rejection discard the first approval with an audit record; a new manual approval workflow requires both approvals again. The facility designates at least two eligible accounts. The “Approvers for the retention run” compliance card checks role and count (0 = critical, 1 = warning, at least 2 = OK). The “Approvers for deletion requests” card still counts active deletion-request approvers regardless of role. Exception: the facility may explicitly enable auto-approval after repeated deferrals (opt-in, off by default); after the configured maximum number of deferrals and expiry of the follow-up date, the run approves without a second person. Holds and Art. 18 restrictions protect this path as well.

Without a case the period starts at the event date; in an open case at last
substantive activity (fallback creation); in a closed case at closure. A document
type maximum period shortens only: the earliest deadline wins. Holds and Art. 18
protect retention deletion, person anonymization and history redaction. Lead and
facility admin create/lift holds directly in person, case and event records;
system administration can lift them in the system list.

Without events, the `retention_identified_days` inactivity period applies only to pseudonym-only people without open cases. The anchor is the latest person change (`updated_at`), substantive case activity (`last_activity_at`), task change or attachment change on the person or case (creation, replacement or removal). People with additional details without events are excluded from automatic anonymization; the proposal path currently supports only events, and automatic retention enforcement for these people is missing. Pseudonym-only people without events but with an open case are excluded from the inactivity path. The facility regularly reviews people with additional details without events and submits a deletion request where necessary; it records the review interval in the deletion concept. See [`anonymization.py`](../../src/core/retention/anonymization.py).
Completed deletion, merge and downgrade requests expire after a configurable
1095 days; pending requests remain. Audit without a facility expires after the
installation period `AUDITLOG_SYSTEM_RETENTION_MONTHS` (24 months, 0 = off).
Documented permanent exceptions remain protected. Facility audit rows for
targets under active holds survive cleanup.
Monthly statistics remain without expiry, including after manual event deletion.
Person records and trash show pending history versions after anonymization.

## Offline key lifetime and revocation check

**Offline key and revocation check** (#2528). The non-extractable `CryptoKey` derived from the password and server salt remains stored in browser IndexedDB across tab, browser and device restarts. “Non-extractable” prevents export through WebCrypto; protecting the stored key against access to the browser profile depends on **device encryption and screen locking**, which are mandatory for offline devices. Derivation freezes an absolute deadline using the facility's `session_absolute_hours` (default 12 hours). Activity and navigation never extend it. The app checks on load, before decryption/storage/replay, on tab return and every 60 seconds. At expiry it removes the key and displays “Offline key expired — please sign in online”; unsent work stays encrypted and becomes readable after another online sign-in with the same password and salt. The idle window still applies separately. Missing or invalid expiry metadata blocks access; bundles without valid `expiresAt` are expired. An observed backwards device clock locks the key, but clock manipulation remains a residual risk. A powered-off app cannot remotely delete the key.

An HTTP/network/processing error fetching revocations (including 500/429), or an invalid response structure, pauses queue and edit replay: “Synchronization is waiting for the revocation check”. The blocking state is stored in local IndexedDB. Each replay run also requires its own fresh, structurally valid, successfully applied and persisted revocation check; an earlier approval never authorizes a later run or another tab, even when metadata writes and fallback deletion both fail. Missing or unreadable metadata blocks replay; recovery from a read failure also requires another successful check. The next sync sequence checks again, as does a direct save from the offline client record. A failed check preserves unsent work in encrypted form. Existing idempotency keys remain intact, and the server gates (422 for revoked people, 409 for status conflicts) remain authoritative. Device encryption does not replace these server checks.

## Statistics FAQ

### 18a. Where can I find guidance on reports?

The [User Guide § 7](https://github.com/anlaufstelle/app/blob/main/docs/en/user-guide.md#7-statistics-and-export) explains how to create reports. Its overview covers these sections:

| Section | Contents |
|---|---|
| [The three tabs at a glance](https://github.com/anlaufstelle/app/blob/main/docs/en/user-guide.md#the-three-tabs-at-a-glance) | Tasks, shared period and reasons for differing figures |
| Statistics dashboard | Period selection, year navigation, charts and documentation type filter |
| Event list / PDF export | Personal event CSV in the Overview; report PDFs in Dataset reports |
| Example activity report | Fixed-layout example, explicitly not a binding official report |
| Dataset reports (CSV / JSON / XML / PDF) | Select card, preview, download; completeness notes and k-anonymity |
| General key figures | Dataset report card; live evaluation with small-count suppression |
| [Releases](https://github.com/anlaufstelle/app/blob/main/docs/en/user-guide.md#releases-manager--admin) | Marked file releases and overlap warnings |

**The main pitfall: two data sources.** The example activity report PDF reads monthly aggregates with live fallback; its preview and CSV/JSON/XML, and other reports, are live. Monthly aggregates can preserve event counts after retention deadlines, but distinct persons are counted live across the entire period, not added month by month. Reports state source and population; cards explain format differences.

For operating reports, see [Admin Guide § 8](https://github.com/anlaufstelle/app/blob/main/docs/en/admin-guide.md#8-statistics-snapshots--materialized-view). For developing backends, see the [plugin guide](https://github.com/anlaufstelle/app/blob/main/docs/en/reporting-plugins.md).

### 18c. Why does Statistics have three tabs — and why do they show different figures?

The three tabs serve different tasks:

- **Overview:** internal key figures, charts and tables without small-count suppression; top persons are shown by pseudonym. The **Event list (CSV, internal, with pseudonyms)** contains personal event records.
- **Dataset reports:** specialist evaluations with preview and CSV/JSON/XML/PDF, including **General key figures** and **Sum of number fields**. Small counts are suppressed and person pseudonyms are absent; this alone does not guarantee anonymity.
- **Releases:** marked file releases with reporting periods and warnings about distinct, overlapping periods of the same report. Previews and sharing outside the application are absent.

Switching tabs keeps the period. Releases lists releases regardless of it; the documentation type filter affects only the Overview and its event list.

Figures can differ due to **suppression**, different **populations** and **data sources**. Live queries see only current records. Monthly aggregates can preserve deleted event counts; the example activity report PDF uses them with live fallback, while its preview and other formats are live. Distinct persons are counted live across the entire period, not added from monthly values. Check source and population for each report and format.

Releases displays 50 entries per page; warning checks consider all matching releases still recorded for the same artifact. Identical periods do not trigger a warning but can differ after changes to records. Different recipients are organisationally less risky only if files are not combined. The history is not a complete record of all sharing.

Decision aid: [User Guide § 7 “The Three Tabs at a Glance”](https://github.com/anlaufstelle/app/blob/main/docs/en/user-guide.md#the-three-tabs-at-a-glance). Details: [Releases](https://github.com/anlaufstelle/app/blob/main/docs/en/user-guide.md#releases-manager--admin). Release rules: [Admin Guide § 8](https://github.com/anlaufstelle/app/blob/main/docs/en/admin-guide.md#releasing-reports-what-you-need-to-know).

<!-- translation-source: docs/faq.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-14 -->
<!-- source-hash: edc705222b96dfa6ea4f70f1f2e353348b3916a7 -->
