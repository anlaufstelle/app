"""System-Service Subpackage (Refs #959).

Buendelt System-/Admin-/Infra-Helfer, die vorher als sieben flache Module
unter ``services/`` lagen.

Module:

- :mod:`._db_admin`   — Postgres-RLS-Bypass-Helfer fuer Admin-Aktionen.
- :mod:`.settings`    — Settings-Update + Audit-Trail.
- :mod:`.health`      — System-Health-Komponenten (vorher
  services/system_health.py).
- :mod:`.field_types` — Form-Field-Type-Registry mit Spec-Dataclass.
- :mod:`.offline`     — Offline-Bundle-Build fuer Streetwork-Stage-2.
- :mod:`.bans`        — Hausverbot-Status pro Client.
- :mod:`.export`      — Generischer CSV/PDF-Export (Events, Jugendamt-Reports).
"""

from core.services.system._db_admin import (
    bypass_replication_triggers,
    has_rls_bypass_context,
)
from core.services.system.bans import get_active_bans, get_active_bans_for_client
from core.services.system.export import (
    JUGENDAMT_CATEGORY_MAP,
    _build_event_row,
    _build_header,
    _collect_field_templates,
    _get_events_queryset,
    _resolve_field_value,
    _sanitize_csv_cell,
    _stream_row,
    export_events_csv,
    generate_jugendamt_pdf,
    generate_report_pdf,
    get_jugendamt_statistics,
)
from core.services.system.field_types import (
    FIELD_TYPE_REGISTRY,
    FILE,
    MULTI_SELECT,
    SELECT,
    FieldTypeSpec,
    get_form_field_cls_for_file,
    get_spec,
)
from core.services.system.offline import (
    BUNDLE_SCHEMA_VERSION,
    BUNDLE_TTL_SECONDS,
    LOOKBACK_DAYS,
    MAX_EVENTS_PER_BUNDLE,
    _serialize_case,
    _serialize_document_type,
    _serialize_event,
    _serialize_field_template,
    _serialize_workitem,
    _visible_data_fields,
    build_client_offline_bundle,
)
from core.services.system.settings import (
    _AUDIT_EXEMPT,
    _AUDIT_FIELDS,
    log_settings_change,
    snapshot_settings,
    update_settings,
)

__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "BUNDLE_TTL_SECONDS",
    "FIELD_TYPE_REGISTRY",
    "FILE",
    "FieldTypeSpec",
    "JUGENDAMT_CATEGORY_MAP",
    "LOOKBACK_DAYS",
    "MULTI_SELECT",
    "SELECT",
    "MAX_EVENTS_PER_BUNDLE",
    "_build_event_row",
    "_build_header",
    "_collect_field_templates",
    "_get_events_queryset",
    "_resolve_field_value",
    "_stream_row",
    "_AUDIT_EXEMPT",
    "_AUDIT_FIELDS",
    "_sanitize_csv_cell",
    "_serialize_case",
    "_serialize_document_type",
    "_serialize_event",
    "_serialize_field_template",
    "_serialize_workitem",
    "_visible_data_fields",
    "build_client_offline_bundle",
    "bypass_replication_triggers",
    "export_events_csv",
    "generate_jugendamt_pdf",
    "generate_report_pdf",
    "get_active_bans",
    "get_form_field_cls_for_file",
    "get_active_bans_for_client",
    "has_rls_bypass_context",
    "get_jugendamt_statistics",
    "get_spec",
    "log_settings_change",
    "snapshot_settings",
    "update_settings",
]
