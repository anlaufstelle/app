"""Positive disclosure checks at preview and download boundaries."""

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Client, DocumentType, Event, StatisticsSnapshot
from core.reporting.registry import get_backend
from core.services.dashboard import suppress_sample_report_1_stats
from core.services.dashboard.external_report import NO_PERSON_CLUSTER, build_external_report
from core.services.dashboard.release_log import build_release_overview
from tests.reporting.test_reporting_sample_pdf_integration import END, START
from tests.reporting.test_reporting_sample_pdf_integration import pdf_text as pdf_text


@pytest.fixture
def disclosure_world(facility, staff_user):
    occurred_at = timezone.make_aware(datetime(2026, 1, 15))
    for name, key, count in (("Tiny", "contact", 2), ("Middle", "crisis", 9), ("Large", "medical", 13)):
        doc_type = DocumentType.objects.create(facility=facility, name=name, report_key=key)
        for index in range(count):
            person = (
                None
                if name == "Tiny"
                else Client.objects.create(
                    facility=facility,
                    pseudonym=f"PRIVATE-{name}-{index}",
                    contact_stage=Client.ContactStage.QUALIFIED,
                    age_cluster=Client.AgeCluster.AGE_27_PLUS,
                    created_by=staff_user,
                )
            )
            Event.objects.create(
                facility=facility,
                client=person,
                document_type=doc_type,
                occurred_at=occurred_at,
                data_json={},
                created_by=staff_user,
            )
    return facility


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["general-key-figures", "sample-report-1"])
@pytest.mark.parametrize("format_name", ["csv", "json", "xml", "pdf"])
def test_download_disclosure_and_release(client, lead_user, disclosure_world, pdf_text, slug, format_name):
    facility = disclosure_world
    client.force_login(lead_user)
    params = {
        "backend": slug,
        "date_from": START,
        "date_to": END,
        "format": format_name,
        "recipient": "Example organisation",
        "purpose": "Annual figures",
    }
    response = client.get(reverse("core:statistics_report_export"), params)
    assert response.status_code == 200
    content = b"".join(response.streaming_content) if response.streaming else response.content
    assert "Content-Disposition" in response
    if format_name == "pdf":
        assert content.startswith(b"%PDF-")
        text = pdf_text[-1]
        assert "Grundgesamtheit" in text
        assert "—" in text
        assert not re.search(r"\b2\b", text)
    elif format_name == "csv":
        rows = list(csv.reader(io.StringIO(content.decode())))
        assert "live" in rows[0][0]
        numeric = [cell for row in rows[2:] for cell in row if cell.isdigit()]
        assert all(int(cell) >= 5 for cell in numeric)
    elif format_name == "json":
        payload = json.loads(content)
        assert payload["data_source"] == "live"
        assert all(row["count"] is None or row["count"] >= 5 for row in payload["records"])
        assert all(not re.search(r"\d", issue["message"]) for issue in payload["completeness"])
        assert all("count" not in issue for issue in payload["completeness"])
    else:
        root = ET.fromstring(content)  # noqa: S314 — trusted output of the local export renderer
        assert root.attrib["data_source"] == "live"
        for count in root.findall("./records/record/count"):
            assert not count.text or int(count.text) >= 5
        assert all(not re.search(r"\d", issue.text or "") for issue in root.findall("./completeness/issue"))
    text = pdf_text[-1] if format_name == "pdf" else content.decode()
    assert "PRIVATE-" not in text
    releases = build_release_overview(facility)
    assert len(releases) == 1
    assert releases[0].backend == slug
    assert releases[0].format == format_name
    assert releases[0].recipient == "Example organisation"
    assert releases[0].purpose == "Annual figures"
    assert AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count() == 1


@pytest.mark.django_db
def test_general_preview_retains_residual_and_secondary_suppression(client, lead_user, disclosure_world):
    facility = disclosure_world
    report = build_external_report(facility, START, END)
    client.force_login(lead_user)
    response = client.get(
        reverse("core:statistics_report_panel"),
        {"backend": "general-key-figures", "period": "frei", "date_from": START, "date_to": END},
    )
    assert response.status_code == 200
    result = response.context["result"]
    assert result.summary["by_age_cluster"] == report["by_age_cluster"]
    rest = next(row for row in result.summary["by_age_cluster"] if row["cluster"] == NO_PERSON_CLUSTER)
    assert rest["count"] is None
    assert sum(row["count"] is None for row in result.summary["by_document_type"]) >= 2
    assert result.summary["metadata"]["populations"]["unique_clients"] == "persons"
    assert result.summary["metadata"]["populations"]["by_age_cluster"] == "all_events"
    assert "PRIVATE-" not in response.content.decode()
    assert not AuditLog.objects.filter(action=AuditLog.Action.EXPORT).exists()


@pytest.mark.django_db
def test_sample_hybrid_suppression_preserves_secondary_guard(facility, client, lead_user, pdf_text):
    raw = {
        "total": 24,
        "unique_clients": 0,
        "by_category": [["Kontakte", 2], ["Beratung", 9], ["Versorgung", 13]],
        "by_age_cluster": [],
    }
    StatisticsSnapshot.objects.create(facility=facility, year=2026, month=1, backend_data={"sample-report-1": raw})
    expected = suppress_sample_report_1_stats(facility, raw)
    assert sum(row["count"] is None for row in expected["by_category"]) >= 2
    client.force_login(lead_user)
    response = client.get(
        reverse("core:statistics_report_export"),
        {"backend": "sample-report-1", "format": "pdf", "date_from": START, "date_to": END},
    )
    assert response.status_code == 200
    assert "Monats-Aggregate" in pdf_text[-1]
    assert not re.search(r"\b2\b", pdf_text[-1])
    assert "—" in pdf_text[-1]
    assert get_backend("sample-report-1").snapshot_enabled


@pytest.mark.django_db
def test_general_live_json_keeps_publishable_no_person_row(disclosure_world, staff_user):
    from core.reporting.export import export_dataset

    facility = disclosure_world
    doc_type = DocumentType.objects.get(facility=facility, name="Tiny")
    for _index in range(7):
        Event.objects.create(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=timezone.make_aware(datetime(2026, 1, 15)),
            data_json={},
            created_by=staff_user,
        )
    response = export_dataset(get_backend("general-key-figures"), facility, START, END, format="json")
    payload = json.loads(response.content)
    rest = next(row for row in payload["summary"]["by_age_cluster"] if row["cluster"] == NO_PERSON_CLUSTER)
    assert rest["count"] == 9
    assert any(row["table"] == "by_age_cluster" and row["count"] == 9 for row in payload["records"])


@pytest.mark.django_db
def test_sample_live_preview_is_digit_free_and_never_a_release(client, lead_user, disclosure_world):
    client.force_login(lead_user)
    response = client.get(
        reverse("core:statistics_report_panel"),
        {"backend": "sample-report-1", "period": "frei", "date_from": START, "date_to": END},
    )
    assert response.status_code == 200
    result = response.context["result"]
    assert all(row["count"] is None or row["count"] >= 5 for row in result.records)
    assert sum(row["count"] is None for row in result.records) >= 2
    assert result.completeness
    assert all(issue.count is None and not re.search(r"\d", str(issue.message)) for issue in result.completeness)
    assert "Quelle je Format" in response.content.decode()
    assert "PDF: Monats-Aggregate" in response.content.decode()
    assert not AuditLog.objects.filter(action=AuditLog.Action.EXPORT).exists()


@pytest.mark.django_db
def test_broken_sample_hybrid_payload_produces_no_file_or_release(client, lead_user, facility):
    StatisticsSnapshot.objects.create(
        facility=facility,
        year=2026,
        month=1,
        backend_data={"sample-report-1": {"total": 29, "by_category": [["Kontakte"]]}},
    )
    client.force_login(lead_user)
    response = client.get(
        reverse("core:statistics_report_export"),
        {"backend": "sample-report-1", "format": "pdf", "date_from": START, "date_to": END},
    )
    assert response.status_code == 502
    assert "Content-Disposition" not in response
    assert not AuditLog.objects.filter(action=AuditLog.Action.EXPORT).exists()
