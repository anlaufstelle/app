"""Hybrid source equations through the public PDF export service."""

from datetime import date

import pytest

from core.models import StatisticsSnapshot
from core.reporting.export import export_dataset
from core.reporting.registry import get_backend
from tests.reporting.test_reporting_sample_pdf_integration import pdf_text as pdf_text


@pytest.mark.django_db
def test_pdf_snapshot_prefix_difference_cannot_recover_a_single_event(facility, pdf_text):
    for month, amount in ((1, 10), (3, 1)):
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2026,
            month=month,
            backend_data={
                "sample-report-1": {
                    "total": amount,
                    "unique_clients": 0,
                    "by_category": [["Kontakte", amount]],
                    "by_age_cluster": [],
                }
            },
        )
    import re

    totals = []
    for end in (date(2026, 3, 31), date(2026, 2, 28)):
        response = export_dataset(get_backend("sample-report-1"), facility, date(2026, 1, 1), end, format="pdf")
        assert response.content.startswith(b"%PDF-")
        match = re.search(r"Gesamtkontakte \(ohne Notizen/Hausverbote\):\s*(unterdrückt|[0-9]+)", pdf_text[-1])
        assert match, "Read the total from the actual PDF layout"
        totals.append(None if match[1] == "unterdrückt" else int(match[1]))
    whole, prefix = totals
    possible = {whole - prefix} if whole is not None and prefix is not None else set(range(1, 5))
    assert 1 in possible and len(possible) >= 2, "The two PDFs reveal the March snapshot singleton"


@pytest.mark.django_db
def test_partial_snapshot_month_withholds_source_membership_and_row_existence(facility, pdf_text):
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
    response = export_dataset(
        get_backend("sample-report-1"), facility, date(2026, 1, 2), date(2026, 1, 31), format="pdf"
    )
    assert response.content.startswith(b"%PDF-")
    assert "Angaben nach Leistungskategorie unterdrückt" in pdf_text[-1]
    assert "Keine relevanten Kontakte" not in pdf_text[-1]


@pytest.mark.django_db
def test_current_month_snapshot_does_not_replace_the_hybrid_live_fallback(facility, admin_user, monkeypatch, pdf_text):
    from datetime import datetime

    from django.utils import timezone

    from tests._factories import make_document_type, make_event

    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 1, 31))
    document_type = make_document_type(facility=facility, name="Contacts", report_key="contact")
    for _ in range(5):
        make_event(
            facility=facility,
            document_type=document_type,
            client=None,
            created_by=admin_user,
            occurred_at=timezone.make_aware(datetime(2026, 1, 15)),
            data_json={},
        )
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
    response = export_dataset(
        get_backend("sample-report-1"), facility, date(2026, 1, 1), date(2026, 1, 31), format="pdf"
    )
    assert response.content.startswith(b"%PDF-")
    import re

    assert re.search(r"Gesamtkontakte \(ohne Notizen/Hausverbote\):\s*5\b", pdf_text[-1])
