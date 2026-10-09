# Threat model — TB5 key rotation

This excerpt translates the key rotation assessment in [TB5 of the German threat model](../threat-model.md#tb5--off-site-backups).

`ENCRYPTION_KEYS` is one ring for the entire installation, with no facility keys. `reencrypt_fields` rotates database fields; `reencrypt_files` rotates referenced attachments. Unfiltered `--verify` authenticates every referenced version with the primary key alone. Rotation does not change older backup sets: a compromised old key can still decrypt their old inner ciphertext if the attacker also obtains the decrypted backup contents. Local/off-site retention and separate key escrow remain organisational prerequisites for key removal; unlimited off-site retention provides no proven finite removal date. ImportJob temporary files and staging remnants without database rows are outside attachment verification. Follow [Ops Runbook § 9.5](ops-runbook.md#file-rotation-and-old-key-removal).

<!-- translation-source: docs/threat-model.md -->
<!-- translation-version: v0.99.6 -->
<!-- translation-date: 2026-10-09 -->
<!-- source-hash: 12b7d5ce8fbce7d24282e76b7f091d853725c3d3 -->
