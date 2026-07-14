"""File-Vault-Subpackage: Public API fuer Encryption-Storage.

Subpackage-Split (#910, erweitert in #959) aus den ehemaligen
``file_vault.py`` + ``file_vault_validation.py`` in thematische Module:

- :mod:`policy`     — Pre-Encrypt-Validation (Extension, Magic-Bytes, ClamAV)
- :mod:`audit`      — AuditLog-Sink fuer ``SECURITY_VIOLATION``-Eintraege
- :mod:`storage`    — Hot-Path Storage/Retrieval (store, get_*, soft-delete, ...)
- :mod:`cleanup`    — Delete-Pfade (Event-Loeschung, Orphan-Cron)
- :mod:`encryption` — Fernet-Verschluesselung fuer Felder + Streams (Refs #959)
- :mod:`virus_scan` — ClamAV-Pre-Encrypt-Scan (Refs #524, Refs #959)

Die hier re-exportierten Namen bilden die stabile Public API. Aufrufer
sollen weiterhin ``from core.services.file_vault import <name>`` benutzen;
Submodule sind Implementierungsdetail.
"""

from __future__ import annotations

from core.services.file_vault.cleanup import (
    cleanup_orphan_storage_files,
    delete_event_attachments,
)
from core.services.file_vault.encryption import (
    CHUNK_SIZE,
    FILE_FORMAT_VERSION,
    EncryptionError,
    EncryptionKeyMissing,
    decrypt_field,
    decrypt_file_stream,
    encrypt_event_data,
    encrypt_field,
    encrypt_file,
    generate_key,
    get_fernet,
    is_encrypted_value,
    safe_decrypt,
)
from core.services.file_vault.storage import (
    StagedUpload,
    _facility_dir,  # noqa: F401 — verwendet in test_file_vault.py::TestStorageOrphanCleanup
    commit_staged_upload,
    delete_attachment_file,
    get_attachment_path,
    get_current_entries_for_field,
    get_decrypted_file_stream,
    get_original_filename,
    prepare_encrypted_upload,
    soft_delete_attachment_chain,
    store_encrypted_file,
)
from core.services.file_vault.virus_scan import (
    ScanResult,
    VirusScannerUnavailableError,
    ping,
    scan_file,
    signature_info,
)
from core.services.file_vault.virus_scan import (
    ping as clamav_ping,
)

__all__ = [
    "CHUNK_SIZE",
    "EncryptionError",
    "EncryptionKeyMissing",
    "FILE_FORMAT_VERSION",
    "ScanResult",
    "StagedUpload",
    "VirusScannerUnavailableError",
    "clamav_ping",
    "cleanup_orphan_storage_files",
    "commit_staged_upload",
    "decrypt_field",
    "decrypt_file_stream",
    "delete_attachment_file",
    "delete_event_attachments",
    "encrypt_event_data",
    "encrypt_field",
    "encrypt_file",
    "generate_key",
    "get_attachment_path",
    "get_current_entries_for_field",
    "get_decrypted_file_stream",
    "get_fernet",
    "get_original_filename",
    "is_encrypted_value",
    "ping",
    "prepare_encrypted_upload",
    "safe_decrypt",
    "scan_file",
    "signature_info",
    "soft_delete_attachment_chain",
    "store_encrypted_file",
]
