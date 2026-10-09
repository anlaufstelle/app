# Disaster recovery — key rotation and escrow

This excerpt translates the key rotation requirements in the [German recovery guide](../disaster-recovery.md#4-schlüssel--und-secret-wiederbeschaffung).

An older backup set can contain database fields and attachments encrypted under several key generations. Restore requires every key used in that set in `ENCRYPTION_KEYS`, even after the live data has been fully rotated using `reencrypt_fields` and `reencrypt_files`. Rotation does not change existing backups. Keep the key history in escrow separately from backups until the last affected local or off-site set actually expires; longer retention requires longer key retention. A restore using only today's primary key can otherwise leave contents unreadable. Follow [Ops Runbook § 9.5](ops-runbook.md#file-rotation-and-old-key-removal), including its import/staging limits, before removing keys from the active ring. Backup encryption uses the separate `BACKUP_ENCRYPTION_KEY`; successful backup HMAC verification does not prove inner field or attachment decryption.

<!-- translation-source: docs/disaster-recovery.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-09 -->
<!-- source-hash: 4d22a0bf8819d32236af258f77a3dad4d52263a3 -->
