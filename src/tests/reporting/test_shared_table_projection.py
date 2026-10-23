"""Public table-removal contracts across type aliases and canonical windows."""

import json
from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import DocumentTypeField, FieldTemplate, Settings
from core.reporting.export import export_dataset
from core.reporting.registry import get_backend
from tests._factories import make_document_type, make_event

pytestmark = pytest.mark.django_db


def _events(facility, user, name, month, amount):
    document_type = make_document_type(facility=facility, name=name)
    for _ in range(amount):
        make_event(
            facility=facility,
            document_type=document_type,
            client=None,
            created_by=user,
            occurred_at=timezone.make_aware(datetime(2026, month, 15, 12)),
            data_json={},
        )
    return document_type


def _public(facility, slug, end):
    return json.loads(export_dataset(get_backend(slug), facility, date(2026, 1, 1), end, format="json").content)


def test_quantity_row_existence_follows_the_shared_type_table_removal(facility, admin_user):
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 2})
    document_type = _events(facility, admin_user, "Sensitive type", 1, 1)
    field = FieldTemplate.objects.create(
        facility=facility, name="Quantity", field_type="number", report_key="quantity_units"
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    end = date(2026, 1, 31)
    general = _public(facility, "general-key-figures", end)
    assert not any(row["table"] == "by_document_type" for row in general["records"]), "Precondition: type table removed"
    quantity = _public(facility, "quantity-sum", end)
    assert quantity["records"] == [], "Nulling contributors must not expose a removed type's row existence"


def test_stricter_raw_table_contract_removes_equal_unit_aliases_in_other_windows(facility, admin_user, monkeypatch):
    from core.services.dashboard import external_report

    _events(facility, admin_user, "Alpha", 1, 6)
    _events(facility, admin_user, "Bravo", 3, 6)
    original = external_report._protect_external_statistics

    def stricter_contract(facility, start, end, raw, *, threshold):
        report = original(facility, start, end, raw, threshold=threshold)
        if start == date(2026, 1, 1) and end == date(2026, 3, 31):
            report.pop("by_document_type", None)
        return report

    monkeypatch.setattr(external_report, "_protect_external_statistics", stricter_contract)
    payload = _public(facility, "general-key-figures", date(2026, 1, 31))
    assert not any(row["table"] == "by_document_type" for row in payload["records"]), (
        "The January Alpha union must inherit table removal from its January–March alias"
    )
