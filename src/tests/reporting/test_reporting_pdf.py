"""PDF downloads must use the prepared dataset and the existing release path."""

import csv
import dataclasses
import io
from datetime import date
from types import SimpleNamespace

import pytest
import weasyprint
from django.db import connection
from django.urls import reverse

from core.models import AuditLog
from core.reporting import registry
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec, stamp_threshold
from core.reporting.data_source import ALL_EVENTS, LIVE, table_note
from core.reporting.export import SuppressionContractError, export_dataset
from core.services.dashboard import suppress_report_stats
from core.services.dashboard.external_report import NO_PERSON_CLUSTER
from core.services.dashboard.release_log import build_release_overview

START = date(2026, 1, 1)
END = date(2026, 1, 31)


@pytest.fixture
def pdf_text(monkeypatch):
    """Inspect the laid-out text of the document that produces the real PDF."""
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


class PreparedBackend(DatasetBackend):
    slug = "pdf-contract"
    name = "PDF contract report"

    def get_field_specs(self):
        return [FieldSpec("label", "Category"), FieldSpec("count", "Count")]

    def build_dataset(self, facility, date_from, date_to):
        return DatasetResult(
            "aggregate",
            [{"label": "Small cell", "count": 2, "pseudonym": "RAW-PRIVATE"}],
            {"private": "RAW-PRIVATE", "count": 2},
            [CompletenessIssue("missing", "RAW completeness: 2", count=2)],
        )

    def prepare_export(self, facility, result):
        assert "k_anonymity_threshold" not in result.summary, "Suppression must run exactly once"
        return DatasetResult(
            "aggregate",
            [{"label": "Small cell", "count": None}, {"label": "Public cell", "count": 19}],
            stamp_threshold({"private": "SUMMARY-PRIVATE"}, 5),
            [CompletenessIssue("missing", "Unmapped entries remain uncounted", count=2)],
        )


class CustomLayoutBackend(PreparedBackend):
    def render_pdf(self, facility, result, *, date_from, date_to, generated_at):
        assert result.records[0]["count"] is None
        assert result.summary["k_anonymity_threshold"] == 5
        assert date_from == START and date_to == END
        assert generated_at.tzinfo is not None
        return b"%PDF-custom-layout"


def test_default_pdf_uses_prepared_projection_and_metadata(pdf_text):
    facility = SimpleNamespace(name="Facility title", settings=SimpleNamespace(facility_full_name=""))
    response = export_dataset(PreparedBackend(), facility, START, END, format="pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response["Content-Type"] == "application/pdf"
    assert "pdf-contract_2026-01-01_2026-01-31.pdf" in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    text = pdf_text[-1]
    for expected in (
        "Facility title",
        "PDF contract report",
        "01.01.2026",
        "31.01.2026",
        "5",
        "—",
        "19",
        "Unmapped entries remain uncounted",
        str(table_note(ALL_EVENTS, LIVE)),
    ):
        assert expected in text
    for private in ("RAW-PRIVATE", "SUMMARY-PRIVATE", "RAW completeness", "\n2\n"):
        assert private not in text
    csv_response = export_dataset(PreparedBackend(), facility, START, END, format="csv")
    rows = list(csv.reader(io.StringIO(b"".join(csv_response.streaming_content).decode())))
    assert rows[-2:] == [["Small cell", ""], ["Public cell", "19"]]


def test_custom_pdf_hook_receives_prepared_result_without_second_suppression():
    response = export_dataset(CustomLayoutBackend(), None, START, END, format="pdf")
    assert response.content == b"%PDF-custom-layout"


def test_pdf_rejects_missing_suppression_stamp_before_layout():
    class MissingStampBackend(CustomLayoutBackend):
        def prepare_export(self, facility, result):
            return result

        def render_pdf(self, *args, **kwargs):
            pytest.fail("Unprepared data reached the PDF hook")

    with pytest.raises(SuppressionContractError):
        export_dataset(MissingStampBackend(), None, START, END, format="pdf")


@pytest.mark.django_db
def test_pdf_preserves_primary_secondary_suppression_and_no_person_row(facility, pdf_text):
    class DisclosureBackend(PreparedBackend):
        def prepare_export(self, facility, result):
            protected = suppress_report_stats(
                facility,
                {
                    "total_contacts": 23,
                    "unique_clients": 20,
                    "by_contact_stage": {},
                    "persons_by_contact_stage": {},
                    "by_document_type": [{"name": "Small cell", "count": 2}, {"name": "Secondary cell", "count": 21}],
                    "by_age_cluster": [{"cluster": "27_plus", "label": "Adult cell", "count": 21, "persons": 20}],
                },
            )
            assert all(row["count"] is None for row in protected["by_document_type"])
            assert any(row["cluster"] == NO_PERSON_CLUSTER for row in protected["by_age_cluster"])
            rows = [{"label": row["name"], "count": row["count"]} for row in protected["by_document_type"]]
            rows.extend({"label": row["label"], "count": row["count"]} for row in protected["by_age_cluster"])
            return dataclasses.replace(result, records=rows, summary=stamp_threshold({}, 5), completeness=[])

    response = export_dataset(DisclosureBackend(), facility, START, END, format="pdf")
    assert response.content.startswith(b"%PDF-")
    text = pdf_text[-1]
    assert "Small cell\n—" in text
    assert "Secondary cell\n—" in text
    assert "Ohne Personenbezug\n—" in text
    assert "\n2\n" not in text and "\n21\n" not in text


@pytest.mark.django_db
@pytest.mark.parametrize("slug", [backend.slug for backend in registry.get_backends()])
def test_registered_backend_has_default_pdf(slug, facility, pdf_text):
    response = export_dataset(registry.get_backend(slug), facility, START, END, format="pdf")
    assert response.content.startswith(b"%PDF-")
    assert "Grundgesamtheit:" in pdf_text[-1]
    assert "Quelle:" in pdf_text[-1]


@pytest.mark.django_db
def test_pdf_download_is_a_release_with_backend_identity(client, lead_user, facility, monkeypatch, settings, pdf_text):
    class TimeoutBackend(PreparedBackend):
        def render_pdf(self, *args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                assert cursor.fetchone()[0] == "1234ms"
            return super().render_pdf(*args, **kwargs)

    settings.REPORTING_BACKEND_STATEMENT_TIMEOUT_MS = 1234
    monkeypatch.setitem(registry._REGISTRY, PreparedBackend.slug, TimeoutBackend())
    client.force_login(lead_user)
    response = client.get(
        reverse("core:statistics_report_export"),
        {
            "backend": PreparedBackend.slug,
            "format": "pdf",
            "date_from": START,
            "date_to": END,
            "recipient": "Funding organisation",
            "purpose": "Annual review",
        },
    )
    assert response.status_code == 200
    audit = AuditLog.objects.get(action=AuditLog.Action.EXPORT)
    assert audit.detail["backend"] == PreparedBackend.slug
    assert audit.detail["format"] == "pdf"
    assert audit.detail["artifact_released"] is True
    assert audit.detail["recipient"] == "Funding organisation"
    assert audit.detail["purpose"] == "Annual review"
    preview = client.get(
        reverse("core:statistics_report_panel"),
        {
            "backend": PreparedBackend.slug,
            "period": "frei",
            "date_from": START,
            "date_to": END,
        },
    )
    assert preview.status_code == 200
    assert preview.context["preview_rows"] == [["Small cell", None], ["Public cell", 19]]
    assert "Small cell\n—" in pdf_text[-1]
    assert "Public cell\n19" in pdf_text[-1]
    assert AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count() == 1
    releases = build_release_overview(facility)
    assert len(releases) == 1
    assert releases[0].backend == PreparedBackend.slug


@pytest.mark.django_db
def test_failed_pdf_hook_returns_no_download_or_release(client, lead_user, monkeypatch):
    class BrokenLayoutBackend(PreparedBackend):
        def render_pdf(self, *args, **kwargs):
            raise RuntimeError("Layout failure")

    monkeypatch.setitem(registry._REGISTRY, PreparedBackend.slug, BrokenLayoutBackend())
    client.force_login(lead_user)
    response = client.get(
        reverse("core:statistics_report_export"),
        {
            "backend": PreparedBackend.slug,
            "format": "pdf",
            "date_from": START,
            "date_to": END,
        },
    )
    assert response.status_code == 502
    assert "Content-Disposition" not in response
    assert not AuditLog.objects.filter(action=AuditLog.Action.EXPORT).exists()


@pytest.mark.django_db
def test_pdf_download_limit_remains_ten_per_hour(client, lead_user, monkeypatch, ratelimit_enabled):
    monkeypatch.setitem(registry._REGISTRY, PreparedBackend.slug, CustomLayoutBackend())
    client.force_login(lead_user)
    url = reverse("core:statistics_report_export")
    params = {"backend": PreparedBackend.slug, "format": "pdf", "date_from": START, "date_to": END}
    for _ in range(10):
        assert client.get(url, params).status_code == 200
    assert AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count() == 10
    denied = client.get(url, params)
    assert denied.status_code == 429
    assert "Content-Disposition" not in denied
    assert AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count() == 10


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_output", [None, iter([b"%PDF-incomplete"])], ids=["empty", "lazy"])
def test_invalid_pdf_hook_output_is_rejected_before_release(client, lead_user, monkeypatch, invalid_output):
    class InvalidLayoutBackend(PreparedBackend):
        def render_pdf(self, *args, **kwargs):
            return invalid_output

    monkeypatch.setitem(registry._REGISTRY, PreparedBackend.slug, InvalidLayoutBackend())
    client.force_login(lead_user)
    response = client.get(
        reverse("core:statistics_report_export"),
        {
            "backend": PreparedBackend.slug,
            "format": "pdf",
            "date_from": START,
            "date_to": END,
        },
    )
    assert response.status_code == 502
    assert "Content-Disposition" not in response
    assert not AuditLog.objects.filter(action=AuditLog.Action.EXPORT).exists()
