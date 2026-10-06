# ADR-014: Encrypted File Vault

- **Status:** Accepted
- **Date:** 2026-05-01
- **Deciders:** Tobias Nix

## Context

Anhänge an Ereignisse (PDFs, Fotos, Scans) enthalten häufig die sensibelsten Personendaten — Ausweise, Atteste, Behördenschreiben. Anforderungen:

- **At-Rest-Verschlüsselung** identisch zur Feld-Encryption: ein Backup oder Disk-Diebstahl darf den Klartext nicht freigeben.
- **Streaming statt Vollkopie** beim Decrypt — ein 30-MB-Dokument darf den Web-Worker nicht für Sekunden blockieren.
- **Virus-Scan vor der Verschlüsselung** — ein verschlüsselter Anhang ist für ClamAV opak. Fail-closed: ist der Scanner aktiv und unerreichbar, wird der Upload abgelehnt.
- **Soft-Delete + Versionierung** — Refs #622 Stage B verlangt mehrere Versionen pro Slot, damit Korrekturen nachvollziehbar sind.
- **Pfad-Isolation pro Facility** — eine Mehrmandanten-Installation darf nicht versehentlich Anhänge zwischen Trägern teilen.

## Decision

- **Speicherpfad** unter `MEDIA_ROOT/<facility-uuid>/<attachment-uuid>` (siehe `_facility_dir` in [`src/core/services/file_vault/`](../../src/core/services/file_vault/)). Pro Facility ein eigenes Verzeichnis; das Facility-Scoping ist im Pfad selbst kodiert.
- **Verschlüsselung** über dieselbe Fernet-Toolchain wie für Felder ([ADR-006](006-fernet-field-encryption.md)) — `encrypt_file`/`decrypt_file_stream` in [`src/core/services/file_vault/encryption.py`](../../src/core/services/file_vault/encryption.py). Kein separater Schlüssel, keine separaten Rotations-Pfade.
- **Reihenfolge beim Upload:** `safe_filename` → MIME-Magic-Bytes-Check → Virus-Scan → Verschlüsseln → Schreiben. Schlägt einer der Schritte fehl, bleibt nichts auf der Disk; der Service-Layer wickelt das in `transaction.atomic` ab (Refs #584).
- **Streaming-Decrypt** liefert einen Iterator von Klartext-Chunks (`get_decrypted_file_stream`), den `safe_download_response` direkt an Django reicht — kein Vollkopie-RAM-Profil.
- **Stage B Versionierung:** ein Marker `__files__` in `Event.data_json` listet `entry_id`-UUIDs; jeder Entry zeigt auf einen Anhang plus optional eine `superseded_by`-Kette. Soft-Delete via `deleted_at` statt physischer Löschung — Retention räumt später auf.

## Consequences

- **+** Identische Schlüsselstrategie wie für Felder — ein Schlüssel-Rotations-Lauf reicht für Felder + Anhänge.
- **+** Streaming-Decrypt skaliert auf Anhänge bis weit über RAM-Größe.
- **+** Pro-Facility-Verzeichnisse machen Backup- und Retention-Pfade pro Träger trivial.
- **+** Fail-closed-Virus-Scan verhindert, dass ein deaktivierter Scanner unbemerkt zur Klartext-Lücke wird.
- **−** Synchroner Virus-Scan blockiert den Upload-Request um die Scan-Latenz (~50–500 ms je nach ClamAV-Last). Akzeptiert: Async-Scan würde den Klartext temporär persistieren — gefährlicher als die Latenz.
- **−** Speicherpfad enthält Facility-UUID — nicht human-readable. Backups via `tar` bleiben möglich, manuelles Aufräumen schwieriger; Operator nutzt das `cleanup_orphan_attachments`-Kommando.

## Alternatives considered

- **Object-Storage (S3/MinIO) als Default:** Verworfen — bringt eine zusätzliche Infrastruktur-Abhängigkeit für die On-Prem-Zielinstallationen. S3-Backend bleibt eine Option in einer späteren ADR.
- **Async-Virus-Scan via Task-Queue:** Verworfen — der Upload müsste den Klartext zwischenspeichern, oder die App müsste einen "scanning"-Zustand auf der UI führen. Beide Optionen schwächen das Bedrohungsmodell.
- **Verschlüsselung per Block-Device (LUKS):** Schützt nicht gegen DB-Dump + Datei-System-Read am laufenden System; nur gegen physischen Diebstahl. Ergänzend nutzbar, aber kein Ersatz.

## References

- [`src/core/services/file_vault/`](../../src/core/services/file_vault/)
- [`src/core/services/file_vault/encryption.py`](../../src/core/services/file_vault/encryption.py)
- [`src/core/services/file_vault/virus_scan.py`](../../src/core/services/file_vault/virus_scan.py)
- [ADR-006](006-fernet-field-encryption.md) (gemeinsame Fernet-Toolchain)
- Commit `c609e86` (Einführung)

## Nachtrag (2026-10-06): Scan und Verschlüsselung vor der Transaktion, Cleanup-Kommando, Owner (Refs #2499)

- **Transaktionsgrenze:** Die Decision sagt, der Service-Layer wickle die Upload-Kette „in `transaction.atomic`“ ab. Seit #1345 ist der Ablauf zweistufig ([`file_vault/storage.py`](../../src/core/services/file_vault/storage.py)). `prepare_encrypted_upload` prüft, scannt (ClamAV) und verschlüsselt auf Disk **vor** jeder schreibenden Transaktion und liefert einen `StagedUpload`. Erst `commit_staged_upload` legt `Attachment` und Audit-Zeile atomar an. Scheitert dieser Schritt, räumt er die verschlüsselte Datei wieder ab. So hält kein langsamer Scan den Advisory Lock der Audit-Kette ([ADR-007](007-auditlog-append-only.md)). „Nichts bleibt auf der Disk“ gilt damit für Fehler im Scan und beim Commit. Bricht der Prozess zwischen beiden Schritten ab, bleibt eine verwaiste `.enc`-Datei liegen; sie entfernt der nächtliche Cleanup, sobald sie älter als die Mindestwartezeit ist (`--min-age-seconds`).
- **Klartext vor dem Scan:** Uploads über `FILE_UPLOAD_MAX_MEMORY_SIZE` (2,5 MB in [`settings/prod.py`](../../src/anlaufstelle/settings/prod.py)) puffert Django vor dem Scan als Klartext-Tempdatei in `FILE_UPLOAD_TEMP_DIR`. In den mitgelieferten Compose-Dateien ist das ein tmpfs-Mount (RAM). Die Consequence „Async-Scan würde den Klartext temporär persistieren“ ist also eine Abwägung gegen eine **zusätzliche** Persistenz. Kurzzeitig gibt es auch heute einen Klartext-Puffer.
- **Cleanup-Kommando:** Es heißt `cleanup_orphan_storage_files`, nicht `cleanup_orphan_attachments`. Es läuft täglich um 04:30 per [`deploy/crontab`](../../deploy/crontab).
- **Owner:** Anhänge hängen nicht mehr nur an Ereignissen. Seit [ADR-039](039-owner-generische-dateianhaenge.md) gehört jede `Attachment`-Zeile genau einem Ereignis, einer Person oder einem Fall, mit eigener `facility_id` und direkter RLS-Policy. Die Lösch- und Redaktionsregeln für Fall-Anhänge stehen in [ADR-044](044-fall-loeschpfad.md).
