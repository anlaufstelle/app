# Security notes — key rotation and idempotency

This excerpt translates the operational rotation guidance and its idempotency consequence in the [German security notes](../security-notes.md).

Prepend the new primary key to `ENCRYPTION_KEYS` and retain old keys initially. One ring serves the entire installation, not each facility. `reencrypt_fields` rewraps database fields including TOTP secrets; `reencrypt_files` streams referenced attachment chunks through encrypted staging and atomic replacement. Unfiltered `reencrypt_files --verify` reads every referenced version with the primary key alone, including format-dependent chunk binding. Remove old keys only after field rotation without warnings, full verification, resolving ImportJob temporary files and staging remnants without database rows, and actual expiry of affected local/off-site backups. Recreate processes with the shortened ring and verify again without filtering. Retained older backups require their historical keys in separate escrow. The manifest records progress, not integrity; a kill can prevent the audit summary. For roles, interruption/concurrency behaviour and acceptance follow [Ops Runbook § 9.5](ops-runbook.md#file-rotation-and-old-key-removal).

Old idempotency fingerprints cannot be rewrapped because the original request payload is no longer available. Removing their key disables payload collision comparison for those events, while duplicate detection remains. This documented loss of functionality is no additional reason to retain the key and is not a data-subject protection goal. Legacy bare SHA-256 values and fingerprints whose key is absent have no comparison basis and are treated as duplicate matches without payload comparison. Concurrent MFA verification preserves rewrapped TOTP secrets; repeat `reencrypt_fields` if it reports devices left under the old key after retries.

<!-- translation-source: docs/security-notes.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-10 -->
<!-- source-hash: 8a1ec917bc6580fe52e10d1b6b737bb35eeb617b -->
