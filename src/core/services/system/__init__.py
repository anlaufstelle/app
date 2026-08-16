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
- :mod:`.export`      — Generischer CSV/PDF-Export (Events, Sachbericht-Reports).
"""

from core.services.system._db_admin import (
    bypass_replication_triggers,
    has_rls_bypass_context,
    rls_bypass_for_read,
)
from core.services.system.bans import get_active_bans, get_active_bans_for_client, get_active_bans_revision
from core.services.system.export import (
    _build_event_row,
    _build_header,
    _collect_field_templates,
    _get_events_queryset,
    _resolve_field_value,
    _sanitize_csv_cell,
    _stream_row,
    export_events_csv,
    generate_report_pdf,
    generate_sample_report_1_pdf,
)
from core.services.system.field_types import (
    FIELD_TYPE_REGISTRY,
    FILE,
    MULTI_SELECT,
    NUMBER,
    SELECT,
    FieldTypeSpec,
    get_form_field_cls_for_file,
    get_spec,
    validate_number_value,
)
from core.services.system.offline import (
    BUNDLE_SCHEMA_VERSION,
    BUNDLE_TTL_SECONDS,
    FACILITY_BUNDLE_SCHEMA_VERSION,
    LOOKBACK_DAYS,
    MAX_EVENTS_PER_BUNDLE,
    _serialize_case,
    _serialize_document_type,
    _serialize_event,
    _serialize_field_template,
    _serialize_workitem,
    _visible_data_fields,
    build_client_offline_bundle,
    build_facility_offline_bundle,
)
from core.services.system.offline_revocation import (
    OFFLINE_REVOCATION_MAX_ENTRIES,
    OFFLINE_REVOCATION_RETENTION_SECONDS,
    list_offline_client_revocations,
    prune_offline_client_revocations,
    revoke_offline_client_bundles,
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
    "FACILITY_BUNDLE_SCHEMA_VERSION",
    "FIELD_TYPE_REGISTRY",
    "FILE",
    "FieldTypeSpec",
    "LOOKBACK_DAYS",
    "MULTI_SELECT",
    "NUMBER",
    "SELECT",
    "MAX_EVENTS_PER_BUNDLE",
    "OFFLINE_REVOCATION_MAX_ENTRIES",
    "OFFLINE_REVOCATION_RETENTION_SECONDS",
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
    "build_facility_offline_bundle",
    "bypass_replication_triggers",
    "export_events_csv",
    "generate_sample_report_1_pdf",
    "generate_report_pdf",
    "get_active_bans",
    "get_active_bans_revision",
    "get_form_field_cls_for_file",
    "get_active_bans_for_client",
    "has_rls_bypass_context",
    "get_spec",
    "list_offline_client_revocations",
    "log_settings_change",
    "prune_offline_client_revocations",
    "revoke_offline_client_bundles",
    "rls_bypass_for_read",
    "snapshot_settings",
    "update_settings",
    "validate_number_value",
]
