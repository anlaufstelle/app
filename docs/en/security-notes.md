# Security notes — key rotation and authentication hardening

This excerpt translates the operational rotation guidance and its idempotency consequence in the [German security notes](../security-notes.md).

Prepend the new primary key to `ENCRYPTION_KEYS` and retain old keys initially. One ring serves the entire installation, not each facility. `reencrypt_fields` rewraps database fields including TOTP secrets; `reencrypt_files` streams referenced attachment chunks through encrypted staging and atomic replacement. Unfiltered `reencrypt_files --verify` reads every referenced version with the primary key alone, including format-dependent chunk binding. Remove old keys only after field rotation without warnings, full verification, resolving ImportJob temporary files and staging remnants without database rows, and actual expiry of affected local/off-site backups. Recreate processes with the shortened ring and verify again without filtering. Retained older backups require their historical keys in separate escrow. The manifest records progress, not integrity; a kill can prevent the audit summary. For roles, interruption/concurrency behaviour and acceptance follow [Ops Runbook § 9.5](ops-runbook.md#file-rotation-and-old-key-removal).

Old idempotency fingerprints cannot be rewrapped because the original request payload is no longer available. Removing their key disables payload collision comparison for those events, while duplicate detection remains. This documented loss of functionality is no additional reason to retain the key and is not a data-subject protection goal. Legacy bare SHA-256 values and fingerprints whose key is absent have no comparison basis and are treated as duplicate matches without payload comparison. Concurrent MFA verification preserves rewrapped TOTP secrets; repeat `reencrypt_fields` if it reports devices left under the old key after retries.

## Authentication, release metadata and guard coverage

Ten backup codes carry 128 bits each and are stored as purpose-separated 96-bit HMAC prefixes derived from `SECRET_KEY`. Earlier SHA digests and the old input format are invalid; rotating `SECRET_KEY` also invalidates codes. Anonymous recovery uses 5/min per IP and 10/h per normalized username without increasing device throttling. Authenticated device throttling and atomic single use remain. The [user guide](user-guide.md#two-factor-authentication-2fa) explains **Neues Gerät einrichten** (set up a new device): existing verified MFA plus sudo; only acknowledgement of new codes replaces confirmed TOTP devices, while passkeys remain.

Passkey completion is limited to 60/h per user before JSON parsing. Cryptographic failures and malformed completion requests received during a started verification count; an interrupted started verification consumes the challenge and records `WEBAUTHN_FAILED` with fixed event/error codes, without setting MFA/OTP markers. A local browser cancellation without a completion request is not observable by the server.

Recipient/purpose metadata accepts at most 200 characters and rejects detected email/phone patterns and typical name pairs before release with HTTP 400, without reflecting rejected values. Institutional prefixes such as “Stadt Musterstadt” remain allowed. This heuristic does not guarantee PII-free values; accepted text remains in the immutable audit log. Production defaults `DJANGO_AUDIT_DETAIL_STRICT=true`; an explicit `false` changes unknown-key handling but never disables this value validation.

The mutation AST guard recursively scans view modules, POST functions, inherited mutation handlers and form views, requiring effective method-specific limits with `block=True`. Configuration POSTs and password changes use 60/h per user; anonymous password/invitation setup uses 5/min per IP, language selection 60/h per user or IP. Caddy has no edge rate limit. Markup guards include installed Django-admin/Unfold templates with specific justified exceptions; the CSRF guard also scans package Python. The inline/Alpine guard remains limited to project templates.

<!-- translation-source: docs/security-notes.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-10 -->
<!-- source-hash: b5e585f481b44acce6482b71de7179a6467affe2 -->
