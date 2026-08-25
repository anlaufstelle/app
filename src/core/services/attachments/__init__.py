"""Anhang-Domaenen-Services — owner-generisch (M24/AP2, Refs #1738).

Seit ADR-039 haengt ein ``Attachment`` an genau EINEM Owner (Ereignis, Person
oder Fall). Die Schreib- und Anzeige-Logik dafuer lag bis M24 in
``services/events/{crud,context}.py`` und war dort nur historisch beheimatet —
sie liest vom Owner ausschliesslich ``facility``, ``data_json`` und den
geteilten Reverse-Accessor ``attachments``.

Submodule:

- :mod:`.crud`    — Schreibpfade (Staging-Finalisierung, REMOVE/REPLACE/ADD,
  Marker-Erhalt, Typwechsel-Verwurf, ersatzloses Loeschen einer Kette).
- :mod:`.context` — Read-only Anzeige-Kontexte + Download-URL-Aufloesung.

``services/events`` re-exportiert die Alt-Namen weiter; dieses Paket ist die
neue Adresse fuer alle drei Owner.
"""

from core.services.attachments.context import (
    OWNER_DOWNLOAD_ROUTES,
    attachment_download_url,
    build_attachment_context,
    build_prior_versions,
    resolve_file_entries,
)
from core.services.attachments.crud import (
    AttachmentPurgeBlockedError,
    apply_attachment_changes,
    discard_type_switch_file_chains,
    finalize_staged_files,
    preserve_file_markers,
    purge_attachment_chain,
    replace_upload_key,
    stage_attachment_uploads,
    stage_uploads,
)

__all__ = [
    "OWNER_DOWNLOAD_ROUTES",
    "AttachmentPurgeBlockedError",
    "apply_attachment_changes",
    "attachment_download_url",
    "build_attachment_context",
    "build_prior_versions",
    "discard_type_switch_file_chains",
    "finalize_staged_files",
    "stage_uploads",
    "preserve_file_markers",
    "purge_attachment_chain",
    "replace_upload_key",
    "resolve_file_entries",
    "stage_attachment_uploads",
]
