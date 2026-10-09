"""General key figures reuse the protected external report for every format."""

import csv
import io
import json
from datetime import date
from types import SimpleNamespace

import pytest

from core.reporting.export import export_dataset
from core.reporting.registry import get_backend
from core.services.dashboard.external_report import build_external_report
from core.views.statistics import build_report_panel_context

START = date(2026, 1, 1)
END = date(2026, 12, 31)


@pytest.mark.django_db
def test_general_figures_reuse_external_report_in_downloads_and_preview(facility):
    backend = get_backend("general-key-figures")
    report = build_external_report(facility, START, END)
    response = export_dataset(backend, facility, START, END, format="json")
    payload = json.loads(response.content)
    for key in ("total_contacts", "unique_clients", "by_contact_stage", "by_document_type", "by_age_cluster"):
        assert (key in payload["summary"]) == (key in report)
        if key in report:
            assert payload["summary"][key] == report[key]
    assert payload["summary"]["metadata"]["populations"] == report["metadata"]["populations"]
    assert payload["data_source"] == "live"
    assert backend.snapshot_enabled is False
    preview = build_report_panel_context(facility, backend, SimpleNamespace(date_from=START, date_to=END))
    assert preview["result"].records == payload["records"]
    response = export_dataset(backend, facility, START, END, format="csv")
    rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
    assert "unique_clients" in rows[0][0] and "persons" in rows[0][0]
    assert "live" in rows[0][0]
    xml = export_dataset(backend, facility, START, END, format="xml").content.decode()
    assert "<populations>" in xml and "<unique_clients>persons</unique_clients>" in xml


@pytest.mark.django_db
def test_general_population_metadata_does_not_label_person_counts_as_events(facility):
    backend = get_backend("general-key-figures")
    response = export_dataset(backend, facility, START, END, format="json")
    payload = json.loads(response.content)
    assert payload["population"] == "table_specific"
    assert payload["summary"]["metadata"]["populations"]["unique_clients"] == "persons"
