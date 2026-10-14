"""Approval compares requested values with their actual storage representation."""

from datetime import date, time

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, FieldTemplate, RectificationRequest
from core.services.client import update_client
from core.services.client.rectification import approve_rectification, request_rectification
from core.services.dynamic_data import validate_data_json
from core.services.file_vault import encrypt_field, is_encrypted_value, safe_decrypt
from tests.test_sensitivity_transitions import make_record

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("active", [True, False], ids=["active", "inactive"])
@pytest.mark.parametrize("encrypted", [True, False], ids=["encrypted", "plain"])
@pytest.mark.parametrize(
    "new_value", [46.0, 46, 46.5, 0.0, 0], ids=["float", "int", "fraction", "float_zero", "int_zero"]
)
def test_approved_number_rectification_matches_storage(facility, staff_user, lead_user, active, encrypted, new_value):
    field = FieldTemplate.objects.create(
        facility=facility, name="Recorded number", field_type="number", is_active=active, is_encrypted=encrypted
    )
    record, _ = make_record(facility, staff_user, "client", field=field, value=45)
    assert validate_data_json(record.document_type, {field.slug: new_value}) == {field.slug: new_value}
    request = request_rectification(record, staff_user, f"data_json.{field.slug}", new_value, "Correction")

    approve_rectification(request, lead_user, "Verified correction")

    request.refresh_from_db()
    record.refresh_from_db()
    stored = record.data_json[field.slug]
    expected = str(new_value) if encrypted and isinstance(new_value, float) else new_value
    assert safe_decrypt(stored) == expected
    assert is_encrypted_value(stored) is encrypted
    assert request.status == RectificationRequest.Status.APPROVED
    assert request.reviewed_by == lead_user
    assert request.reviewed_at is not None
    assert (
        AuditLog.objects.for_facility(facility)
        .filter(action=AuditLog.Action.RECTIFICATION_APPROVED, target_id=str(request.pk))
        .count()
        == 1
    )
    if not active:
        update_client(record, lead_user, data_json={field.slug: 999})
        record.refresh_from_db()
        assert safe_decrypt(record.data_json[field.slug]) == expected


@pytest.mark.parametrize("active", [True, False], ids=["active", "inactive"])
@pytest.mark.parametrize("encrypted", [True, False], ids=["encrypted", "plain"])
@pytest.mark.parametrize(
    ("field_type", "old_value", "new_value", "expected"),
    [
        ("text", "45", "46.0", "46.0"),
        ("multi_select", ["housing"], ["housing", "work"], ["housing", "work"]),
        ("date", "2026-01-01", date(2026, 2, 2), "2026-02-02"),
        ("time", "08:00:00", time(9, 30), "09:30:00"),
        ("boolean", True, False, False),
    ],
    ids=["text", "list", "date", "time", "boolean"],
)
def test_approved_other_rectification_preserves_value_types(
    facility, staff_user, lead_user, active, encrypted, field_type, old_value, new_value, expected
):
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Recorded value",
        field_type=field_type,
        is_active=active,
        is_encrypted=encrypted,
        options_json=[{"slug": "housing", "label": "Housing"}, {"slug": "work", "label": "Work"}]
        if field_type == "multi_select"
        else [],
    )
    record, _ = make_record(facility, staff_user, "client", field=field, value=old_value)
    request = request_rectification(record, staff_user, f"data_json.{field.slug}", new_value, "Correction")

    approve_rectification(request, lead_user)

    request.refresh_from_db()
    record.refresh_from_db()
    assert safe_decrypt(record.data_json[field.slug]) == expected
    assert type(safe_decrypt(record.data_json[field.slug])) is type(expected)
    assert is_encrypted_value(record.data_json[field.slug]) is encrypted
    assert request.status == RectificationRequest.Status.APPROVED
    assert (
        AuditLog.objects.for_facility(facility)
        .filter(action=AuditLog.Action.RECTIFICATION_APPROVED, target_id=str(request.pk))
        .count()
        == 1
    )


@pytest.mark.parametrize("active", [True, False], ids=["active", "inactive"])
@pytest.mark.parametrize("encrypted", [True, False], ids=["encrypted", "plain"])
@pytest.mark.parametrize("failure", ["old_value", "wrong_value", "plaintext_number_text"])
def test_failed_number_rectification_rolls_back_without_approval(
    facility, staff_user, lead_user, monkeypatch, active, encrypted, failure
):
    field = FieldTemplate.objects.create(
        facility=facility, name="Recorded number", field_type="number", is_active=active, is_encrypted=encrypted
    )
    record, _ = make_record(facility, staff_user, "client", field=field, value=45)
    request = request_rectification(record, staff_user, f"data_json.{field.slug}", 46.0, "Correction")
    before_data = record.data_json.copy()
    before_notes = record.notes
    before_updated_at = record.updated_at
    before_audit = AuditLog.objects.for_facility(facility).count()
    before_review_note = request.review_note

    def update_with_wrong_storage(*args, **kwargs):
        kwargs["notes"] = "Unexpected persisted note"
        update_client(*args, **kwargs)
        wrong_value = {"old_value": 45, "wrong_value": 47.0, "plaintext_number_text": "46.0"}[failure]
        stored = encrypt_field(wrong_value) if encrypted and failure != "plaintext_number_text" else wrong_value
        type(record).objects.for_facility(facility).filter(pk=record.pk).update(data_json={field.slug: stored})

    monkeypatch.setattr("core.services.client.main.update_client", update_with_wrong_storage)
    with pytest.raises(ValidationError):
        approve_rectification(request, lead_user)

    request.refresh_from_db()
    record.refresh_from_db()
    assert request.status == RectificationRequest.Status.PENDING
    assert request.reviewed_by is None
    assert request.reviewed_at is None
    assert request.review_note == before_review_note
    assert record.data_json == before_data
    assert safe_decrypt(record.data_json[field.slug]) == 45
    assert record.notes == before_notes
    assert record.updated_at == before_updated_at
    assert AuditLog.objects.for_facility(facility).count() == before_audit
    assert (
        not AuditLog.objects.for_facility(facility)
        .filter(action=AuditLog.Action.RECTIFICATION_APPROVED, target_id=str(request.pk))
        .exists()
    )
    if not active:
        update_client(record, lead_user, data_json={field.slug: 999})
        record.refresh_from_db()
        assert safe_decrypt(record.data_json[field.slug]) == 45
