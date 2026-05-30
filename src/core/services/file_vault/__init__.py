"""File-Vault-Subpackage: Public API fuer Encryption-Storage.

Subpackage-Split (#910) aus den ehemaligen ``file_vault.py`` +
``file_vault_validation.py`` in vier thematische Module:

- :mod:`policy`  — Pre-Encrypt-Validation (Extension, Magic-Bytes, ClamAV)
- :mod:`audit`   — AuditLog-Sink fuer ``SECURITY_VIOLATION``-Eintraege
- :mod:`storage` — Hot-Path Storage/Retrieval (store, get_*, soft-delete, ...)
- :mod:`cleanup` — Delete-Pfade (Event-Loeschung, Orphan-Cron)

Die hier re-exportierten Namen bilden die stabile Public API. Aufrufer
sollen weiterhin ``from core.services.file_vault import <name>`` benutzen;
Submodule sind Implementierungsdetail.
"""

from __future__ import annotations

from core.services.file_vault.cleanup import (
    cleanup_orphan_storage_files,
    delete_event_attachments,
)
from core.services.file_vault.storage import (
    _facility_dir,  # noqa: F401 — verwendet in test_file_vault.py::TestStorageOrphanCleanup
    delete_attachment_file,
    get_attachment_path,
    get_current_entries_for_field,
    get_decrypted_file_stream,
    get_original_filename,
    soft_delete_attachment_chain,
    store_encrypted_file,
)

__all__ = [
    "cleanup_orphan_storage_files",
    "delete_attachment_file",
    "delete_event_attachments",
    "get_attachment_path",
    "get_current_entries_for_field",
    "get_decrypted_file_stream",
    "get_original_filename",
    "soft_delete_attachment_chain",
    "store_encrypted_file",
]
