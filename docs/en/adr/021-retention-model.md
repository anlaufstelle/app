# ADR-021: Retention model

Status: accepted. This English counterpart states the current decision and the
2026-10-10 addendum. The [German source](../../adr/021-retention-modell.md) retains
the original rationale and earlier dated history.

## Decision

Retention uses anonymous, identified, qualified and document-type strategies.
Anonymous and pseudonym-only events expire from their own event date; a newer
contact does not extend older events. A document-type setting is a **maximum
period (shortens only)**: the earliest deadline wins, never an extension.

Events with additional details require a proposal and manual approval by two different eligible people
with deletion confirmation permission before deletion. Their period starts at
case closure, without a case at the event date, and in an open case at the last
substantive activity (fallback creation). Changes to cases, events, tasks and
progress count; key rotation does not. Deferral protects until its date;
rejection permits a new proposal after a configurable 365 days. Automatic
approval after maximum deferrals remains opt-in.

The run creates the proposal. Manual approval requires two different active people from the same facility, each with the Manager or Application support role and deletion confirmation permission. The first approval leaves the proposal pending (PENDING, “1 of 2 approvals”); only the second, different person gives final approval (APPROVED). With only one eligible account, it remains at “1 of 2”; the first approval alone never permits deletion. Deferral, legal hold (including from person, case or event records) and rejection discard the first approval with an audit record; a new manual approval workflow requires both approvals again. The facility designates at least two eligible accounts. The “Approvers for the retention run” compliance card checks role and count (0 = critical, 1 = warning, at least 2 = OK). The “Approvers for deletion requests” card still counts active deletion-request approvers regardless of role. Exception: the facility may explicitly enable auto-approval after repeated deferrals (opt-in, off by default); after the configured maximum number of deferrals and expiry of the follow-up date, the run approves without a second person. Holds and Art. 18 restrictions protect this path as well.

## Addendum 2026-10-10

The original qualified approval decision is enforced again by the regular run
(#2524). The description
from 2026-10-06 is superseded:

- Lead and facility admin create and lift holds in person, case and event
  records, with an audit trail. System administration can lift holds in the
  system list. Holds and Art. 18 protect retention deletion, person
  anonymization and history redaction. A unilateral downgrade with existing
  events cannot bypass approval.
- Held targets in facility audit rows survive pruning. Authenticated HMAC checkpoints retain
  every surviving chain boundary. The audit period is independent of business
  periods: 24 months per facility and separately 24 months installation-wide
  without a facility; each 0 disables pruning. Permanent exceptions remain.
  A facility-restricted run does not prune the system chain. Complete retention
  without routine audit pruning still requires 0; a hold protects its targets.
- Without events, the `retention_identified_days` inactivity period applies only to pseudonym-only people without open cases. The anchor is the latest person change (`updated_at`), substantive case activity (`last_activity_at`), task change or attachment change on the person or case (creation, replacement or removal). People with additional details without events are excluded from automatic anonymization; the proposal path currently supports only events, and automatic retention enforcement for these people is missing. Pseudonym-only people without events but with an open case are excluded from the inactivity path. The facility regularly reviews people with additional details without events and submits a deletion request where necessary; it records the review interval in the deletion concept. See [`anonymization.py`](../../../src/core/retention/anonymization.py).
  Completed deletion, merge and downgrade requests expire after a configurable
  1095 days from completion. No legacy migration or cleanup is introduced.
- Automatic and manual event deletion carry monthly counts in the same
  transaction. Aggregates remain without expiry as a statistical remainder,
  not an immutable archive. Person records and trash show pending history
  versions after anonymization.
- There is no additional case/file/handover-note period. Immediate file purge
  remains an individual authorized decision checking holds and Art. 18.

## Evidence

[Retention services](../../../src/core/retention/),
[event deletion](../../../src/core/services/events/crud.py),
[GDPR templates](../../../src/core/dsgvo_templates/) and
[the German references](../../adr/021-retention-modell.md#references).

<!-- translation-source: docs/adr/021-retention-modell.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-14 -->
<!-- source-hash: 3b62d191372a61b46573e986a8c280dc5809b9d3 -->
