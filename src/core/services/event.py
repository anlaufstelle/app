"""Re-Export-Stub fuer Bestands-Aufrufer (Refs #777).

Die Event-Services wurden in das Paket :mod:`core.services.events`
verschoben (Phase 1 von [#777](https://github.com/tobiasnix/anlaufstelle/issues/777)) — fuenf Concerns liegen jetzt in
``crud.py``, ``context.py``, ``deletion.py`` und ``fields.py``.

Diese Datei re-exportiert alle public Symbole, damit
``from core.services.event import create_event`` (Singular)
weiterlaeuft. Neue Aufrufer sollen direkt aus ``core.services.events``
importieren.
"""

from core.services.events import (
    CONTACT_STAGE_ORDER,
    _build_prior_versions,
    _format_field_display_value,
    _is_file_marker,
    _snapshot_field_metadata,
    _validate_data_json,
    apply_attachment_changes,
    approve_deletion,
    attach_files_to_new_event,
    build_event_detail_context,
    build_field_template_lookup,
    build_redacted_delete_history,
    create_event,
    filtered_server_data_json,
    is_multi_file_marker,
    is_singleton_file_marker,
    normalize_file_marker,
    reject_deletion,
    remove_restricted_fields,
    request_deletion,
    soft_delete_event,
    split_file_and_text_data,
    stage_index,
    update_event,
)

__all__ = [
    "CONTACT_STAGE_ORDER",
    "_build_prior_versions",
    "_format_field_display_value",
    "_is_file_marker",
    "_snapshot_field_metadata",
    "_validate_data_json",
    "apply_attachment_changes",
    "approve_deletion",
    "attach_files_to_new_event",
    "build_event_detail_context",
    "build_field_template_lookup",
    "build_redacted_delete_history",
    "create_event",
    "filtered_server_data_json",
    "is_multi_file_marker",
    "is_singleton_file_marker",
    "normalize_file_marker",
    "reject_deletion",
    "remove_restricted_fields",
    "request_deletion",
    "soft_delete_event",
    "split_file_and_text_data",
    "stage_index",
    "update_event",
]
