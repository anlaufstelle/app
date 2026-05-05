# ADR-014: Encrypted File Vault

- **Status:** Accepted
- **Date:** 2026-05-01
- **Deciders:** Tobias Nix

## Context

Anhänge an Ereignisse (PDFs, Fotos, Scans) enthalten häufig die sensibelsten Personendaten — Ausweise, Atteste, Behördenschreiben. Anforderungen:

- **At-Rest-Verschlüsselung** identisch zur Feld-Encryption: ein Backup oder Disk-Diebstahl darf den Klartext nicht freigeben.
- **Streaming statt Vollkopie** beim Decrypt — ein 30-MB-Dokument darf den Web-Worker nicht für Sekunden blockieren.
- **Virus-Scan vor der Verschlüsselung** — ein verschlüsselter Anhang ist für ClamAV opak. Fail-closed: ist der Scanner aktiv und unerreichbar, wird der Upload abgelehnt.
- **Soft-Delete + Versionierung** — Refs [#622](https://github.com/tobiasnix/anlaufstelle/issues/622) Stage B verlangt mehrere Versionen pro Slot, damit Korrekturen nachvollziehbar sind.
- **Pfad-Isolation pro Facility** — eine Mehrmandanten-Installation darf nicht versehentlich Anhänge zwischen Trägern teilen.

## Decision

- **Speicherpfad** unter `MEDIA_ROOT/<facility-uuid>/<attachment-uuid>` (siehe `_facility_dir` in [`src/core/services/file_vault.py`](../../src/core/services/file_vault.py)). Pro Facility ein eigenes Verzeichnis; das Facility-Scoping ist im Pfad selbst kodiert.
- **Verschlüsselung** über dieselbe Fernet-Toolchain wie für Felder ([ADR-006](006-fernet-field-encryption.md)) — `encrypt_file`/`decrypt_file_stream` in [`src/core/services/encryption.py`](../../src/core/services/encryption.py). Kein separater Schlüssel, keine separaten Rotations-Pfade.
- **Reihenfolge beim Upload:** `safe_filename` → MIME-Magic-Bytes-Check → Virus-Scan → Verschlüsseln → Schreiben. Schlägt einer der Schritte fehl, bleibt nichts auf der Disk; der Service-Layer wickelt das in `transaction.atomic` ab (Refs [#584](https://github.com/tobiasnix/anlaufstelle/issues/584)).
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

- [`src/core/services/file_vault.py`](../../src/core/services/file_vault.py)
- [`src/core/services/encryption.py`](../../src/core/services/encryption.py)
- [`src/core/services/virus_scan.py`](../../src/core/services/virus_scan.py)
- [ADR-006](006-fernet-field-encryption.md) (gemeinsame Fernet-Toolchain)
- Commit `c609e86` (Einführung)
