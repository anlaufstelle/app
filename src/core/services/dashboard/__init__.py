"""Dashboard-Service Subpackage (Refs #959).

Buendelt UI-Daten-Aggregation, die vorher als sieben flache Module unter
``services/`` lag.

Module:

- :mod:`.main`            — Dashboard-Aggregation pro Rolle (Cards/Widgets).
- :mod:`.statistics`      — Statistik-Berechnungen (Hybrid Snapshot/Live).
- :mod:`.snapshot`        — Statistik-Snapshot-Persistenz (monatliche Frozen-Werte).
- :mod:`.activity`        — Activity-Logging fuer Zeitstrom.
- :mod:`.search`          — Globale Suche (Klienten + Events).
- :mod:`.external_report` — Externe Berichte mit K-Anon-Schutz.
- :mod:`.release_log`     — Freigabe-Uebersicht herausgegebener Berichtszeitraeume.
- :mod:`.quick_templates` — Quick-Template-Dropdown-Daten fuer Event-Form.
"""

from core.services.dashboard.activity import log_activity, render_activity_summary
from core.services.dashboard.external_report import (
    build_external_report,
    suppress_report_stats,
    suppress_sample_report_1_stats,
)
from core.services.dashboard.focus_box import build_focus_box
from core.services.dashboard.main import (
    facility_admin_dashboard_context,
    lead_dashboard_context,
    staff_dashboard_context,
    super_admin_dashboard_context,
)
from core.services.dashboard.quick_templates import (
    apply_template,
    filter_prefilled_data,
    get_template_for_user,
    get_templates_for_document_type,
    list_templates_for_user,
)
from core.services.dashboard.release_log import (
    EXTERNAL_REPORT_TARGET,
    RELEASE_FIELD_MAX_LENGTH,
    RELEASE_LOG_LIMIT,
    Release,
    build_release_overview,
    release_detail,
)
from core.services.dashboard.search import (
    MIN_QUERY_LENGTH,
    client_search_queryset,
    event_search_queryset,
    search_clients_and_events,
    search_result_counts,
    search_similar_clients,
)
from core.services.dashboard.snapshot import (
    _empty_sample_report_1_stats,
    _empty_stats,
    _merge_sample_report_1_stats,
    _merge_stats,
    _split_into_segments,
    aggregate_trend_segments,
    carry_deleted_event,
    create_or_update_snapshot,
    get_sample_report_1_statistics_hybrid,
    get_snapshot,
    get_statistics_hybrid,
    get_statistics_trend,
)
from core.services.dashboard.statistics import (
    CANONICAL_PERIODS,
    DEFAULT_PERIOD,
    PERIOD_ALIASES,
    PERIOD_FREI,
    PERIOD_JAHR,
    PERSONS_KEY,
    STATISTICS_MAX_CUSTOM_RANGE_DAYS,
    STATISTICS_MV_NAME,
    VIEW_DATENSAETZE,
    InvalidStatisticsPeriodError,
    PeriodState,
    _flat_view_enabled,
    _parse_year,
    _query_statistics_mv,
    count_unique_clients,
    event_queryset_for_period,
    get_event_counts_by_month,
    get_statistics,
    get_statistics_for_events,
    normalize_period,
    parse_statistics_period,
    parse_statistics_view,
    validate_statistics_range,
)

__all__ = [
    "InvalidStatisticsPeriodError",
    "MIN_QUERY_LENGTH",
    "PERSONS_KEY",
    "EXTERNAL_REPORT_TARGET",
    "RELEASE_FIELD_MAX_LENGTH",
    "RELEASE_LOG_LIMIT",
    "Release",
    "PeriodState",
    "STATISTICS_MAX_CUSTOM_RANGE_DAYS",
    "STATISTICS_MV_NAME",
    "_empty_sample_report_1_stats",
    "_empty_stats",
    "_flat_view_enabled",
    "_merge_sample_report_1_stats",
    "_merge_stats",
    "_parse_year",
    "_query_statistics_mv",
    "_split_into_segments",
    "aggregate_trend_segments",
    "apply_template",
    "build_external_report",
    "build_focus_box",
    "build_release_overview",
    "carry_deleted_event",
    "client_search_queryset",
    "count_unique_clients",
    "create_or_update_snapshot",
    "event_queryset_for_period",
    "event_search_queryset",
    "facility_admin_dashboard_context",
    "filter_prefilled_data",
    "get_event_counts_by_month",
    "get_sample_report_1_statistics_hybrid",
    "get_snapshot",
    "get_statistics",
    "get_statistics_for_events",
    "get_statistics_hybrid",
    "get_statistics_trend",
    "get_template_for_user",
    "get_templates_for_document_type",
    "lead_dashboard_context",
    "list_templates_for_user",
    "log_activity",
    "render_activity_summary",
    "CANONICAL_PERIODS",
    "DEFAULT_PERIOD",
    "PERIOD_ALIASES",
    "PERIOD_FREI",
    "PERIOD_JAHR",
    "VIEW_DATENSAETZE",
    "normalize_period",
    "parse_statistics_period",
    "parse_statistics_view",
    "release_detail",
    "search_clients_and_events",
    "search_result_counts",
    "search_similar_clients",
    "staff_dashboard_context",
    "super_admin_dashboard_context",
    "suppress_sample_report_1_stats",
    "suppress_report_stats",
    "validate_statistics_range",
]
