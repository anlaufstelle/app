# Technical and organisational measures — key rotation

This excerpt translates the rotation item of the [German TOM template](../../src/core/dsgvo_templates/toms.md); it is not a rendered GDPR document.

Key rotation without data loss (MultiFernet) covers database fields (`reencrypt_fields`) and attachments (`reencrypt_files`). Remove old keys only after unfiltered primary-key verification (`reencrypt_files --verify`), resolving import/staging files outside its scope, and expiry of affected local/off-site backups. Restart processes and verify again without a facility filter. One ring serves the installation; there are no facility keys. The manifest records progress, not integrity. Follow [Ops Runbook § 9.5](ops-runbook.md#file-rotation-and-old-key-removal).

<!-- translation-source: src/core/dsgvo_templates/toms.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-09 -->
<!-- source-hash: 16b2cc723d771553c2bad78da586e3bdd6c0e38f -->
