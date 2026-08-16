"""Tests für ``inventory_number_ranges`` (Refs #1914)."""

from io import StringIO

import pytest
from cryptography.fernet import Fernet
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate


def _configured_number_type(facility):
    document_type = DocumentType.objects.create(facility=facility, name="Spritzentausch")
    field_template = FieldTemplate.objects.create(
        facility=facility,
        name="Ausgabe",
        field_type=FieldTemplate.FieldType.NUMBER,
        min_value=0,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field_template)
    return document_type, field_template


@pytest.mark.parametrize("value", [10**1000, str(10**1000), f" -{10**1000} "])
def test_inventory_normalizes_arbitrarily_large_integers_without_float_conversion(value):
    from core.management.commands.inventory_number_ranges import _normalize_number_value

    assert _normalize_number_value(value) == int(value)


@pytest.fixture
def fernet_key():
    return Fernet.generate_key().decode()


@pytest.mark.django_db
def test_inventory_reports_existing_negative_values_without_changing_them(facility, staff_user, monkeypatch):
    from core.management.commands import inventory_number_ranges as command_module

    monkeypatch.setattr(command_module, "_has_rls_bypass_context", lambda: True)
    document_type, field_template = _configured_number_type(facility)
    event = Event.objects.create(
        facility=facility,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={field_template.slug: -3},
        is_anonymous=True,
        created_by=staff_user,
    )
    output = StringIO()

    call_command("inventory_number_ranges", "--facility", str(facility.pk), stdout=output)

    assert "Ausgabe" not in output.getvalue()
    assert f"{field_template.slug}: 1 (unter Minimalwert 0)" in output.getvalue()
    event.refresh_from_db()
    assert event.data_json[field_template.slug] == -3


@pytest.mark.django_db
def test_inventory_fails_loud_without_rls_bypass(facility, monkeypatch):
    from core.management.commands import inventory_number_ranges as command_module

    monkeypatch.setattr(command_module, "_has_rls_bypass_context", lambda: False)

    with pytest.raises(CommandError, match="RLS-gefilterte"):
        call_command("inventory_number_ranges", "--facility", str(facility.pk))


@pytest.mark.django_db
def test_inventory_accepts_encrypted_numeric_string_and_avoids_per_entity_field_queries(
    facility, staff_user, fernet_key, monkeypatch
):
    from core.management.commands import inventory_number_ranges as command_module

    monkeypatch.setattr(command_module, "_has_rls_bypass_context", lambda: True)
    document_type, field_template = _configured_number_type(facility)
    field_template.is_encrypted = True
    field_template.save(update_fields=["is_encrypted"])

    with override_settings(ENCRYPTION_KEY=fernet_key):
        Event.objects.create(
            facility=facility,
            document_type=document_type,
            occurred_at=timezone.now(),
            data_json={field_template.slug: 3},
            is_anonymous=True,
            created_by=staff_user,
        )
        with CaptureQueriesContext(connection) as small_context:
            call_command("inventory_number_ranges", "--facility", str(facility.pk), stdout=StringIO())

        for _ in range(5):
            Event.objects.create(
                facility=facility,
                document_type=document_type,
                occurred_at=timezone.now(),
                data_json={field_template.slug: 3},
                is_anonymous=True,
                created_by=staff_user,
            )
        output = StringIO()
        with CaptureQueriesContext(connection) as large_context:
            call_command("inventory_number_ranges", "--facility", str(facility.pk), stdout=output)

    assert "keine Zahl" not in output.getvalue()
    assert len(large_context) == len(small_context)


@pytest.mark.django_db
def test_inventory_reports_encrypted_arbitrarily_large_integer_as_range_violation(
    facility, staff_user, fernet_key, monkeypatch
):
    from core.management.commands import inventory_number_ranges as command_module

    monkeypatch.setattr(command_module, "_has_rls_bypass_context", lambda: True)
    document_type, field_template = _configured_number_type(facility)
    field_template.is_encrypted = True
    field_template.max_value = 10
    field_template.save(update_fields=["is_encrypted", "max_value"])

    with override_settings(ENCRYPTION_KEY=fernet_key):
        Event.objects.create(
            facility=facility,
            document_type=document_type,
            occurred_at=timezone.now(),
            data_json={field_template.slug: 10**1000},
            is_anonymous=True,
            created_by=staff_user,
        )
        output = StringIO()
        call_command("inventory_number_ranges", "--facility", str(facility.pk), stdout=output)

    assert f"{field_template.slug}: 1 (über Maximalwert 10)" in output.getvalue()
