# Disaster recovery — key rotation and escrow

This excerpt translates the key rotation requirements in the [German recovery guide](../disaster-recovery.md#4-schlüssel--und-secret-wiederbeschaffung).

An older backup set can contain database fields and attachments encrypted under several key generations. Restore requires every key used in that set in `ENCRYPTION_KEYS`, even after the live data has been fully rotated using `reencrypt_fields` and `reencrypt_files`. Rotation does not change existing backups. Keep the key history in escrow separately from backups until the last affected local or off-site set actually expires; longer retention requires longer key retention. A restore using only today's primary key can otherwise leave contents unreadable. Follow [Ops Runbook § 9.5](ops-runbook.md#file-rotation-and-old-key-removal), including its import/staging limits, before removing keys from the active ring. Backup encryption uses the separate `BACKUP_ENCRYPTION_KEY`; successful backup HMAC verification does not prove inner field or attachment decryption.

## Audit anchor and media verification

The audit high-water mark uses `DJANGO_AUDIT_HWM_PATH` or the Compose default `/data/audit_hwm.json`, including when the variable is empty or absent. It lives on the separate `audit-anchor` volume. Maintenance services write it; `web` has read access. Initialize the signed anchor before the first backup and preserve the original `DJANGO_AUDIT_HASH_KEY` for recovery. Follow [Ops Runbook § 6.7](ops-runbook.md#67-back-up-and-verify-the-audit-high-water-mark-refs-2022) for volume permissions, migration of an existing anchor and the limitation that this does not protect against the host or owner.

Current `backup.sh` aborts with exit code 1 before publishing a set if the anchor is missing, empty or unreadable. Older sets may lack `audit_hwm-<timestamp>.json`; restoring one is incomplete, and the anchor verification step cannot run. Check the set first and choose an older complete set if necessary.

`restore.sh` uses the configured anchor path or the default and aborts before changes if the set lacks the anchor copy. Restoration still requires confirmation (`--yes` or `RESTORE_CONFIRM=ja`). The explicit `--skip-audit-anchor` option restores without checking or importing the anchor; subsequent anchor verification cannot run. The production recovery drill uses the default even without a custom path: a missing or invalid copy produces `FAIL`, exit code 1 and no compliance evidence. The development backup format has no anchor and remains a drill with gaps (exit code 2). Record the source and reconstructed environment's actual anchor paths, whether custom or default, in the drill report.

Media archive verification runs in one disposable maintenance container while `web` and `cron` remain stopped. Its temporary working directory is on the persistent `/media` volume, outside `MEDIA_ROOT`, and is removed afterwards. The German guide's verification command uses `/media/media-verify-$RESTORE_TIMESTAMP` and `${MEDIA_ROOT_CONTAINER:-/media/media}`. Compare SHA-256 and size of the encrypted files with the authenticated archive; original filenames and plaintext sizes require the separate download check. See the [German recovery steps](../disaster-recovery.md#schritt-4--db-backup-entschlüsseln--wiederherstellen) for the complete procedure.

<!-- translation-source: docs/disaster-recovery.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-10 -->
<!-- source-hash: fef11d7643755eb7b3ced458b3b4e06be5496c00 -->
