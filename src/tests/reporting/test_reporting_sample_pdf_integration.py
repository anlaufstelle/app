"""The sample PDF keeps its hybrid source behind the generic download path."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
import weasyprint

from core.models import StatisticsSnapshot
from core.reporting.export import export_dataset
from core.reporting.registry import get_backend
from core.views.statistics import build_report_panel_context

START = date(2026, 1, 1)
END = date(2026, 1, 31)


@pytest.fixture
def pdf_text(monkeypatch):
    texts = []

    def write_pdf(html, *args, **kwargs):
        document = html.render()
        texts.append(
            "\n".join(
                box.text for page in document.pages for box in page._page_box.descendants() if hasattr(box, "text")
            )
        )
        return document.write_pdf(*args, **kwargs)

    monkeypatch.setattr(weasyprint.HTML, "write_pdf", write_pdf)
    return texts


@pytest.mark.django_db
def test_sample_generic_pdf_reads_hybrid_and_keeps_live_other_formats(facility, pdf_text):
    StatisticsSnapshot.objects.create(
        facility=facility,
        year=2026,
        month=1,
        backend_data={
            "sample-report-1": {
                "total": 29,
                "unique_clients": 0,
                "by_category": [["Kontakte", 29]],
                "by_age_cluster": [],
            }
        },
    )
    backend = get_backend("sample-report-1")
    response = export_dataset(backend, facility, START, END, format="pdf")
    assert response.content.startswith(b"%PDF-")
    assert "Vorlage" in pdf_text[-1]
    assert "29" in pdf_text[-1]
    assert "Monats-Aggregate" in pdf_text[-1]
    live = json.loads(export_dataset(backend, facility, START, END, format="json").content)
    assert live["summary"]["total"] is None
    assert live["data_source"] == "live"
    preview = build_report_panel_context(facility, backend, SimpleNamespace(date_from=START, date_to=END))
    assert preview["result"].summary["total"] is None
    notes = dict(preview["format_source_notes"])
    assert "Monats-Aggregate" in str(notes["PDF"])
    assert "Live-Abfrage" in str(notes["CSV"])
