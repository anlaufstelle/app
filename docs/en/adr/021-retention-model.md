# ADR-021: Retention model

Status: accepted. This English counterpart states the current decision and the
2026-10-10 addendum. The [German source](../../adr/021-retention-modell.md) retains
the original rationale and earlier dated history.

## Decision

Retention uses anonymous, identified, qualified and document-type strategies.
Anonymous and pseudonym-only events expire from their own event date; a newer
contact does not extend older events. A document-type setting is a **maximum
period (shortens only)**: the earliest deadline wins, never an extension.

Events with additional details require a proposal and approval by a person
with deletion confirmation permission before deletion. Their period starts at
case closure, without a case at the event date, and in an open case at the last
substantive activity (fallback creation). Changes to cases, events, tasks and
progress count; key rotation does not. Deferral protects until its date;
rejection permits a new proposal after a configurable 365 days. Automatic
approval after maximum deferrals remains opt-in.

The run creates the proposal. The facility must designate at least one active account in its own facility with the Manager or Application support role and deletion confirmation permission; without an approver, events with additional details are not deleted by the regular run when auto-approval is disabled. This permission is assigned independently of the role, but the retention interface is restricted to Manager and Application support. The “Approvers” compliance card counts active deletion-request approvers regardless of role; it does not establish that an account can operate the retention workflow.

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
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-11 -->
<!-- source-hash: 07e85a1eec925531dacbb5b079c62d3b02a94ee9 -->
