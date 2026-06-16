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
- :mod:`.quick_templates` — Quick-Template-Dropdown-Daten fuer Event-Form.
"""

from core.services.dashboard.activity import log_activity
from core.services.dashboard.external_report import build_external_report
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
from core.services.dashboard.search import (
    search_clients_and_events,
    search_similar_clients,
)
from core.services.dashboard.snapshot import (
    _empty_jugendamt_stats,
    _empty_stats,
    _merge_jugendamt_stats,
    _merge_stats,
    _split_into_segments,
    create_or_update_snapshot,
    ensure_snapshots_for_months,
    get_jugendamt_statistics_hybrid,
    get_snapshot,
    get_statistics_hybrid,
    get_statistics_trend,
    is_multi_month_range,
)
from core.services.dashboard.statistics import (
    STATISTICS_MV_NAME,
    PeriodState,
    _flat_view_enabled,
    _parse_year,
    get_event_counts_by_month,
    get_statistics,
    parse_statistics_period,
)

__all__ = [
    "PeriodState",
    "STATISTICS_MV_NAME",
    "_empty_jugendamt_stats",
    "_empty_stats",
    "_flat_view_enabled",
    "_merge_jugendamt_stats",
    "_merge_stats",
    "_parse_year",
    "_split_into_segments",
    "apply_template",
    "build_external_report",
    "build_focus_box",
    "create_or_update_snapshot",
    "ensure_snapshots_for_months",
    "facility_admin_dashboard_context",
    "filter_prefilled_data",
    "get_event_counts_by_month",
    "get_jugendamt_statistics_hybrid",
    "get_snapshot",
    "get_statistics",
    "get_statistics_hybrid",
    "get_statistics_trend",
    "get_template_for_user",
    "get_templates_for_document_type",
    "is_multi_month_range",
    "lead_dashboard_context",
    "list_templates_for_user",
    "log_activity",
    "parse_statistics_period",
    "search_clients_and_events",
    "search_similar_clients",
    "staff_dashboard_context",
    "super_admin_dashboard_context",
]
