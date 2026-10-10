# FAQ — retention, erasure and offline key lifetime

This English FAQ covers the updated retention topic and the offline key lifetime
and revocation check addendum. Other topics remain in the
[German FAQ](../faq.md); see the [English user guide](user-guide.md) for daily work.

## Retention and erasure

### How do retention periods work?

The regular daily `enforce_retention` run deletes anonymous and pseudonym-only
events after expiry. Events with additional details receive proposals; another
person with deletion confirmation permission must approve deletion. Deferral
protects until its date; rejection permits a new proposal no earlier than the
retry interval (default 365 days). Automatic approval remains opt-in.

Without a case the period starts at the event date; in an open case at last
substantive activity (fallback creation); in a closed case at closure. A document
type maximum period shortens only: the earliest deadline wins. Holds and Art. 18
protect retention deletion, person anonymization and history redaction. Lead and
facility admin create/lift holds directly in person, case and event records;
system administration can lift them in the system list.

People without events use the pseudonym-only inactivity period from last update.
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

<!-- translation-source: docs/faq.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-10 -->
<!-- source-hash: 2f7ac4219b8d895fa97fc49de0b7aa9324fb3a59 -->
