# Technical and organisational measures — key rotation

This excerpt translates the rotation item of the [German TOM template](../../src/core/dsgvo_templates/toms.md); it is not a rendered GDPR document.

Key rotation without data loss (MultiFernet) covers database fields (`reencrypt_fields`) and attachments (`reencrypt_files`). Remove old keys only after unfiltered primary-key verification (`reencrypt_files --verify`), resolving import/staging files outside its scope, and expiry of affected local/off-site backups. Restart processes and verify again without a facility filter. One ring serves the installation; there are no facility keys. The manifest records progress, not integrity. Follow [Ops Runbook § 9.5](ops-runbook.md#file-rotation-and-old-key-removal).


## Offline key persistence and revocation check

**Offline key and revocation check** (#2528). The non-extractable `CryptoKey` derived from the password and server salt remains stored in browser IndexedDB across tab, browser and device restarts. “Non-extractable” prevents export through WebCrypto; protecting the stored key against access to the browser profile depends on **device encryption and screen locking**, which are mandatory for offline devices. Derivation freezes an absolute deadline using the facility's `session_absolute_hours` (default 12 hours). Activity and navigation never extend it. The app checks on load, before decryption/storage/replay, on tab return and every 60 seconds. At expiry it removes the key and displays “Offline key expired — please sign in online”; unsent work stays encrypted and becomes readable after another online sign-in with the same password and salt. The idle window still applies separately. Missing or invalid expiry metadata blocks access; bundles without valid `expiresAt` are expired. An observed backwards device clock locks the key, but clock manipulation remains a residual risk. A powered-off app cannot remotely delete the key.

An HTTP/network/processing error fetching revocations (including 500/429), or an invalid response structure, pauses queue and edit replay: “Synchronization is waiting for the revocation check”. The blocking state is stored in local IndexedDB. Each replay run also requires its own fresh, structurally valid, successfully applied and persisted revocation check; an earlier approval never authorizes a later run or another tab, even when metadata writes and fallback deletion both fail. Missing or unreadable metadata blocks replay; recovery from a read failure also requires another successful check. The next sync sequence checks again, as does a direct save from the offline client record. A failed check preserves unsent work in encrypted form. Existing idempotency keys remain intact, and the server gates (422 for revoked people, 409 for status conflicts) remain authoritative. Device encryption does not replace these server checks.

<!-- translation-source: src/core/dsgvo_templates/toms.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-10 -->
<!-- source-hash: 8831158ebc8700e2f316c3d2086b8491bb9b450b -->
