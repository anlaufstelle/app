"""Protected episode and task details survive exports and leave no redaction residue."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Case
from core.services.client.export import export_client_data, render_client_export_html
from core.services.client.main import anonymize_client
from core.services.system.facility_export import build_facility_export
from tests.test_episode_workitem_standard_fields import standard_record as standard_record

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_article_15_details_are_decrypted(facility, lead_user, standard_record, case_open):
    kind, record, field = standard_record
    if kind == "workitem":
        record.client = case_open.client
        record.save(update_fields=["client"])
    data = export_client_data(case_open.client, facility, lead_user)
    rows = data["episodes" if kind == "episode" else "work_items"]
    row = next(row for row in rows if row["title"] == record.title)
    assert "description" not in row
    assert any(
        entry["slug"] == field.slug and entry["value"] == "Stored standard value" for entry in row["dynamic_data"]
    )


def test_article_15_pdf_template_includes_standard_value(facility, lead_user, standard_record, case_open):
    kind, record, _field = standard_record
    if kind == "workitem":
        record.client = case_open.client
        record.save(update_fields=["client"])
    html = render_client_export_html(case_open.client, facility, lead_user)
    assert "Stored standard value" in html
    assert "__encrypted__" not in html


def test_facility_export_details_are_decrypted(facility, lead_user, standard_record):
    kind, record, field = standard_record
    data = build_facility_export(facility)
    row = next(
        row
        for row in data["entities"]["episodes" if kind == "episode" else "work_items"]
        if row["id"] == str(record.pk)
    )
    assert "description" not in row
    assert row["document_type_id"] == str(record.document_type_id)
    assert row["data_json"][field.slug] == "Stored standard value"


def test_anonymization_clears_episode_and_task_dynamic_data(facility, lead_user, standard_record, case_open):
    kind, record, _field = standard_record
    if kind == "workitem":
        record.client = case_open.client
        record.save(update_fields=["client"])
    anonymize_client(case_open.client, lead_user)
    record.refresh_from_db()
    assert record.document_type_id is None
    assert record.data_json == {}


def test_task_dynamic_edit_updates_case_activity(facility, standard_record, case_open):
    kind, record, field = standard_record
    if kind != "workitem":
        return
    record.client = case_open.client
    record.save(update_fields=["client"])
    baseline = timezone.now() - timedelta(days=2)
    Case.objects.filter(facility=facility, pk=case_open.pk).update(last_activity_at=baseline)
    record.data_json = {field.slug: "Changed business detail"}
    record.save(update_fields=["data_json"])
    case_open.refresh_from_db()
    assert case_open.last_activity_at > baseline
