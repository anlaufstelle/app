"""Event-Services-Paket — Re-Export-Hub (Refs #777, erweitert in #959).

Frueher lebten alle Funktionen in einem 715-LOC-Modul ``services/event.py``
mit fuenf Concerns. #777 schnitt sie in vier fokussierte
Submodule; Refs #959 hat zusaetzlich den Activity-Feed-Service aus dem
flachen ``services/feed.py`` ins Paket gezogen und den Kompatibilitaets-
Stub ``services/event.py`` entfernt.

Submodule:

- :mod:`.fields` — reine Helfer (Field-Template-Lookup, Marker-Parsing,
  Validation, Sensitivity-Filter, Stage-Index, Form-Splitting).
- :mod:`.context` — Detail-Context und Diff-Filter fuer Views (read-only).
- :mod:`.crud` — Event-Schreibpfade und Attachment-Mutationen.
- :mod:`.deletion` — 4-Augen-Loeschungs-Workflow.
- :mod:`.feed` — Activity-Feed-Aufbau (Zeitstrom).

Dieses ``__init__.py`` re-exportiert alle public Symbole. Aufrufer
schreiben ``from core.services.events import create_event``.
"""

from core.services.events.context import (
    _build_prior_versions,
    _format_field_display_value,
    build_attachment_context,
    build_event_detail_context,
    filtered_server_data_json,
    resolve_default_document_type,
)
from core.services.events.crud import (
    apply_attachment_changes,
    attach_files_to_new_event,
    create_event,
    decrypt_event_text_data,
    merge_update_payload,
    soft_delete_event,
    update_event,
)
from core.services.events.deletion import (
    approve_deletion,
    reject_deletion,
    request_deletion,
)
from core.services.events.feed import (
    _format_preview_value,
    build_feed_items,
    enrich_events_with_preview,
    get_time_range,
)
from core.services.events.fields import (
    CONTACT_STAGE_ORDER,
    _is_file_marker,
    _snapshot_field_metadata,
    _validate_data_json,
    build_field_template_lookup,
    build_redacted_delete_history,
    is_multi_file_marker,
    is_singleton_file_marker,
    normalize_file_marker,
    remove_restricted_fields,
    split_file_and_text_data,
    stage_index,
)
from core.services.events.history_diff import (
    ENCRYPTED_PLACEHOLDER,
    RESTRICTED_PLACEHOLDER,
    compute_event_diff,
)
from core.services.events.idempotency import (
    get_idempotent_result,
    normalize_idempotency_key,
    payload_fingerprint,
    remember_idempotent_result,
)
from core.services.events.tally import (
    build_tally_summary,
    current_tally_window,
    tally_document_types,
)

__all__ = [
    "CONTACT_STAGE_ORDER",
    "ENCRYPTED_PLACEHOLDER",
    "RESTRICTED_PLACEHOLDER",
    "_build_prior_versions",
    "_format_field_display_value",
    "_format_preview_value",
    "_is_file_marker",
    "_snapshot_field_metadata",
    "_validate_data_json",
    "apply_attachment_changes",
    "approve_deletion",
    "attach_files_to_new_event",
    "build_attachment_context",
    "build_event_detail_context",
    "build_feed_items",
    "build_field_template_lookup",
    "build_redacted_delete_history",
    "build_tally_summary",
    "compute_event_diff",
    "current_tally_window",
    "create_event",
    "decrypt_event_text_data",
    "enrich_events_with_preview",
    "filtered_server_data_json",
    "get_idempotent_result",
    "get_time_range",
    "is_multi_file_marker",
    "is_singleton_file_marker",
    "merge_update_payload",
    "normalize_file_marker",
    "normalize_idempotency_key",
    "payload_fingerprint",
    "reject_deletion",
    "remember_idempotent_result",
    "remove_restricted_fields",
    "request_deletion",
    "resolve_default_document_type",
    "soft_delete_event",
    "split_file_and_text_data",
    "stage_index",
    "tally_document_types",
    "update_event",
]
