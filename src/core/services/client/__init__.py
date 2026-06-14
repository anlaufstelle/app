"""Client-Service Subpackage (Refs #959).

Buendelt alle Client-bezogenen Services, die vorher als drei flache
Module unter ``services/`` lagen:

* ``services/clients.py``        -> :mod:`core.services.client.main`
* ``services/client_export.py``  -> :mod:`core.services.client.export`
* ``services/dsgvo_package.py``  -> :mod:`core.services.client.dsgvo_package`

Module:

- :mod:`.main`           — Client-CRUD, Anonymisierung, Stage-Wechsel,
  Deletion-Request-Workflow, Restore.
- :mod:`.export`         — Auskunfts-Export nach DSGVO Art. 15
  (JSON + PDF).
- :mod:`.dsgvo_package`  — DSGVO-Templates fuer Betroffenenrechts-
  Auskunft (Verzeichnis, Footer-Builder).
"""

from core.services.client.dsgvo_package import (
    DOCUMENTS,
    TEMPLATE_DIR,
    _build_footer,
    _settings_hash,
    get_document_list,
    render_document,
)
from core.services.client.export import (
    _build_export_meta,
    _gather_cases,
    _gather_client_fields,
    _gather_deletion_requests,
    _gather_event_history,
    _gather_events,
    _gather_workitems,
    _serialize_event,
    export_client_data,
    export_client_data_pdf,
)
from core.services.client.main import (
    _delete_event_attachments_for_client,
    _redact_activities,
    _redact_cases_and_episodes,
    _redact_client_identity,
    _redact_deletion_requests,
    _redact_event_history,
    _redact_live_events,
    _redact_workitems,
    anonymize_client,
    anonymize_eligible_soft_deleted_clients,
    approve_client_deletion,
    create_client,
    get_client_or_none,
    reject_client_deletion,
    request_client_deletion,
    restore_client,
    track_client_visit,
    update_client,
)

__all__ = [
    "DOCUMENTS",
    "TEMPLATE_DIR",
    "_build_export_meta",
    "_build_footer",
    "_delete_event_attachments_for_client",
    "_gather_cases",
    "_gather_client_fields",
    "_gather_deletion_requests",
    "_gather_event_history",
    "_gather_events",
    "_gather_workitems",
    "_redact_activities",
    "_redact_cases_and_episodes",
    "_redact_client_identity",
    "_redact_deletion_requests",
    "_redact_event_history",
    "_redact_live_events",
    "_redact_workitems",
    "_serialize_event",
    "_settings_hash",
    "anonymize_client",
    "anonymize_eligible_soft_deleted_clients",
    "approve_client_deletion",
    "create_client",
    "export_client_data",
    "export_client_data_pdf",
    "get_client_or_none",
    "get_document_list",
    "reject_client_deletion",
    "render_document",
    "request_client_deletion",
    "restore_client",
    "track_client_visit",
    "update_client",
]
