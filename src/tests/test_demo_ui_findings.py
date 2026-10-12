"""Regression coverage for the demo preview and download controls."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.template.loader import render_to_string

from core.reporting.backends.general_key_figures import GeneralKeyFiguresBackend
from core.reporting.base import DatasetResult
from core.reporting.data_source import ALL_EVENTS
from core.views.statistics import build_report_panel_context


def test_general_figures_preview_uses_table_labels_without_changing_export():
    backend = GeneralKeyFiguresBackend()
    result = DatasetResult(
        "aggregate",
        [
            {"table": "total_contacts", "label": "Gesamtkontakte", "count": None},
            {"table": "by_contact_stage", "label": "Nur Pseudonym", "count": None},
        ],
        {"metadata": {"populations": {"total_contacts": ALL_EVENTS}}, "k_anonymity_threshold": 5},
        [],
    )
    period = SimpleNamespace(date_from=date(2026, 10, 1), date_to=date(2026, 10, 12))
    with patch.object(backend, "build_dataset", return_value=result):
        context = build_report_panel_context(None, backend, period)
    html = render_to_string("core/statistics/partials/report_panel.html", context)
    assert "total_contacts" not in html
    assert "by_contact_stage" not in html
    assert "Kontakte nach Kontaktstufe" in html
    assert "Gesamtkontakte" in html
    assert result.records[0]["table"] == "total_contacts"
