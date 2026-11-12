"""A key rotation must not move the inactivity anchor of people.

``updated_at`` of a person and of their tasks is part of the anchor from which
the inactivity period up to anonymization runs. Re-encrypting stored values is
no business change: the rotation keeps ``updated_at`` and a person who is due
stays due.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core.models import Client, DocumentType, DocumentTypeField, FieldTemplate, WorkItem
from core.services.file_vault import generate_key, is_encrypted_value

pytestmark = pytest.mark.django_db

RETENTION_DAYS = 365


@pytest.fixture
def keys():
    return generate_key(), generate_key()


@pytest.fixture
def identified_period(settings_obj):
    settings_obj.retention_identified_days = RETENTION_DAYS
    settings_obj.save(update_fields=["retention_identified_days"])
    return settings_obj


@pytest.fixture
def doc_type_task_note(facility):
    doc_type = DocumentType.objects.create(
        facility=facility, name="Task note", entity_type=DocumentType.EntityType.WORKITEM
    )
    template = FieldTemplate.objects.create(
        facility=facility, name="Tasknote", field_type=FieldTemplate.FieldType.TEXTAREA, is_encrypted=True
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=template, sort_order=0)
    return doc_type, template.slug


def _rotate(keys):
    new_key, old_key = keys
    stdout, stderr = StringIO(), StringIO()
    with override_settings(ENCRYPTION_KEYS=f"{new_key},{old_key}"):
        call_command("reencrypt_fields", stdout=stdout, stderr=stderr)
    return stdout.getvalue() + stderr.getvalue()


def _run_retention(facility, keys):
    with override_settings(ENCRYPTION_KEYS=f"{keys[0]},{keys[1]}"):
        call_command("enforce_retention", facility=facility.name, stdout=StringIO())


def _just_due():
    return timezone.now() - timedelta(days=RETENTION_DAYS + 1)


def test_rotation_keeps_person_updated_at_and_person_stays_due(
    facility, doc_type_client_profile, staff_user, identified_period, keys
):
    with override_settings(ENCRYPTION_KEYS=keys[1]):
        person = Client.objects.create(
            facility=facility,
            pseudonym="Rotation-Anchor-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", "diagnose": "Rotation anchor"},
        )
    assert is_encrypted_value(Client.objects.values_list("data_json", flat=True).get(pk=person.pk)["diagnose"])
    Client.objects.filter(pk=person.pk).update(updated_at=_just_due())
    anchor = Client.objects.values_list("updated_at", flat=True).get(pk=person.pk)

    _rotate(keys)

    assert Client.objects.values_list("updated_at", flat=True).get(pk=person.pk) == anchor
    _run_retention(facility, keys)
    person.refresh_from_db()
    assert person.anonymized_at is not None


def test_rotation_keeps_task_updated_at_and_person_stays_due(
    facility, client_identified, doc_type_task_note, staff_user, identified_period, keys
):
    doc_type, slug = doc_type_task_note
    with override_settings(ENCRYPTION_KEYS=keys[1]):
        task = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            title="Rotation task",
            created_by=staff_user,
            document_type=doc_type,
            data_json={slug: "Rotation anchor"},
        )
    assert is_encrypted_value(WorkItem.objects.values_list("data_json", flat=True).get(pk=task.pk)[slug])
    # The task is the latest activity of the person and lies just past the period.
    Client.objects.filter(pk=client_identified.pk).update(updated_at=_just_due() - timedelta(days=100))
    WorkItem.objects.filter(pk=task.pk).update(updated_at=_just_due())
    anchor = WorkItem.objects.values_list("updated_at", flat=True).get(pk=task.pk)

    _rotate(keys)

    assert WorkItem.objects.values_list("updated_at", flat=True).get(pk=task.pk) == anchor
    _run_retention(facility, keys)
    client_identified.refresh_from_db()
    assert client_identified.anonymized_at is not None


def test_concurrent_business_write_still_wins_over_rotation(
    facility, doc_type_client_profile, staff_user, keys, monkeypatch
):
    """The rotation still compares ``updated_at``: a change between read and write wins."""
    from core.services.file_vault import rotation as rotation_module

    with override_settings(ENCRYPTION_KEYS=keys[1]):
        person = Client.objects.create(
            facility=facility,
            pseudonym="Rotation-Writer-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", "diagnose": "Before"},
        )
    Client.objects.filter(pk=person.pk).update(updated_at=_just_due())
    real = rotation_module._reencrypt_data_json

    def write_in_between(data):
        result = real(data)
        if isinstance(data, dict) and "diagnose" in data:
            # A business edit commits after the rotation read its candidate.
            current = Client.objects.get(pk=person.pk)
            current.data_json = {"wohnsituation": "Own flat", "diagnose": "After"}
            current.save(update_fields=["data_json", "updated_at"])
        return result

    monkeypatch.setattr(rotation_module, "_reencrypt_data_json", write_in_between)
    output = _rotate(keys)

    assert "1 Zeilen wegen paralleler Änderung übersprungen" in output
    assert "0 clients" in output
    person.refresh_from_db()
    assert person.data_json["wohnsituation"] == "Own flat"
    assert person.updated_at > _just_due() + timedelta(days=RETENTION_DAYS)
