# Threat model — TB5 key rotation

This excerpt translates the key rotation assessment in [TB5 of the German threat model](../threat-model.md#tb5--off-site-backups).

`ENCRYPTION_KEYS` is one ring for the entire installation, with no facility keys. `reencrypt_fields` rotates database fields; `reencrypt_files` rotates referenced attachments. Unfiltered `--verify` authenticates every referenced version with the primary key alone. Rotation does not change older backup sets: a compromised old key can still decrypt their old inner ciphertext if the attacker also obtains the decrypted backup contents. Local/off-site retention and separate key escrow remain organisational prerequisites for key removal; unlimited off-site retention provides no proven finite removal date. ImportJob temporary files and staging remnants without database rows are outside attachment verification. Follow [Ops Runbook § 9.5](ops-runbook.md#file-rotation-and-old-key-removal).


## Offline key lifetime and revocation check

**Offline key and revocation check** (#2528). The non-extractable `CryptoKey` derived from the password and server salt remains stored in browser IndexedDB across tab, browser and device restarts. “Non-extractable” prevents export through WebCrypto; protecting the stored key against access to the browser profile depends on **device encryption and screen locking**, which are mandatory for offline devices. Derivation freezes an absolute deadline using the facility's `session_absolute_hours` (default 12 hours). Activity and navigation never extend it. The app checks on load, before decryption/storage/replay, on tab return and every 60 seconds. At expiry it removes the key and displays “Offline key expired — please sign in online”; unsent work stays encrypted and becomes readable after another online sign-in with the same password and salt. The idle window still applies separately. Missing or invalid expiry metadata blocks access; bundles without valid `expiresAt` are expired. An observed backwards device clock locks the key, but clock manipulation remains a residual risk. A powered-off app cannot remotely delete the key.

An HTTP/network/processing error fetching revocations (including 500/429) pauses queue and edit replay: “Synchronization is waiting for the revocation check”. The next sync sequence checks again; only `ok` releases replay. Existing idempotency keys remain intact, and the server gates (422 for revoked people, 409 for status conflicts) remain authoritative. Device encryption does not replace these server checks.

<!-- translation-source: docs/threat-model.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-09 -->
<!-- source-hash: 8c0a8ad826282f00285ebe41ee2740cafe3db295 -->
