# Data protection impact assessment — offline key lifetime

This excerpt translates the offline key persistence and revocation item in section 4.1 of the German DPIA template; it is not a rendered GDPR document.

**Offline key and revocation check** (#2528). The non-extractable `CryptoKey` derived from the password and server salt remains stored in browser IndexedDB across tab, browser and device restarts. “Non-extractable” prevents export through WebCrypto; protecting the stored key against access to the browser profile depends on **device encryption and screen locking**, which are mandatory for offline devices. Derivation freezes an absolute deadline using the facility's `session_absolute_hours` (default 12 hours). Activity and navigation never extend it. The app checks on load, before decryption/storage/replay, on tab return and every 60 seconds. At expiry it removes the key and displays “Offline key expired — please sign in online”; unsent work stays encrypted and becomes readable after another online sign-in with the same password and salt. The idle window still applies separately. Missing or invalid expiry metadata blocks access; bundles without valid `expiresAt` are expired. An observed backwards device clock locks the key, but clock manipulation remains a residual risk. A powered-off app cannot remotely delete the key.

An HTTP/network/processing error fetching revocations (including 500/429) pauses queue and edit replay: “Synchronization is waiting for the revocation check”. The next sync sequence checks again; only `ok` releases replay. Existing idempotency keys remain intact, and the server gates (422 for revoked people, 409 for status conflicts) remain authoritative. Device encryption does not replace these server checks.

<!-- translation-source: src/core/dsgvo_templates/dsfa.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-09 -->
<!-- source-hash: a92103373a39227680b7ba1b8820783839e6c409 -->
