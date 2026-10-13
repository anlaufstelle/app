"""Approved corrections must persist even after the target field is deactivated."""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, RectificationRequest
from core.services.client import update_client
from core.services.client.rectification import approve_rectification, request_rectification
from core.services.file_vault import safe_decrypt
from tests.test_sensitivity_transitions import make_record

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("deactivate", ["before_request", "before_approval", "never"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_approved_rectification_persists_requested_value(facility, staff_user, lead_user, deactivate, encrypted):
    record, field = make_record(facility, staff_user, "client", value="Incorrect old value")
    field.is_encrypted = encrypted
    field.save(update_fields=["is_encrypted"])
    record.refresh_from_db()
    if deactivate == "before_request":
        field.is_active = False
        field.save(update_fields=["is_active"])
    request = request_rectification(record, staff_user, f"data_json.{field.slug}", "Corrected value", "Correction")
    if deactivate == "before_approval":
        field.is_active = False
        field.save(update_fields=["is_active"])
    approve_rectification(request, lead_user, "Verified correction")
    request.refresh_from_db()
    record.refresh_from_db()
    assert safe_decrypt(record.data_json[field.slug]) == "Corrected value"
    assert request.status == RectificationRequest.Status.APPROVED
    assert (
        AuditLog.objects.for_facility(facility)
        .filter(action=AuditLog.Action.RECTIFICATION_APPROVED, target_id=str(request.pk))
        .exists()
    )
    # The private approval context must be closed before ordinary editing resumes.
    if deactivate != "never":
        update_client(record, lead_user, data_json={field.slug: "Ordinary edit"})
        record.refresh_from_db()
        assert safe_decrypt(record.data_json[field.slug]) == "Corrected value"


@pytest.mark.parametrize("failure", ["no_write", "save_error", "wrong_value"])
def test_failed_rectification_leaves_request_pending_without_success_audit(
    facility, staff_user, lead_user, monkeypatch, failure
):
    record, field = make_record(facility, staff_user, "client", value="Incorrect old value")
    request = request_rectification(record, staff_user, f"data_json.{field.slug}", "Corrected value", "Correction")
    field.is_active = False
    field.save(update_fields=["is_active"])

    before_audit = AuditLog.objects.for_facility(facility).count()

    def failed_update(*args, **kwargs):
        if failure == "save_error":
            raise ValidationError("Persistence failed")
        if failure == "wrong_value":
            # A successful write of another value must also roll back its audit.
            kwargs["notes"] = "Unexpected persisted note"
            update_client(*args, **kwargs)
            type(record).objects.for_facility(facility).filter(pk=record.pk).update(
                data_json={field.slug: "Wrong stored value"}
            )

    monkeypatch.setattr("core.services.client.main.update_client", failed_update)
    with pytest.raises(ValidationError):
        approve_rectification(request, lead_user)
    request.refresh_from_db()
    record.refresh_from_db()
    assert request.status == RectificationRequest.Status.PENDING
    assert request.reviewed_by is None
    assert request.reviewed_at is None
    assert safe_decrypt(record.data_json[field.slug]) == "Incorrect old value"
    assert record.notes != "Unexpected persisted note"
    assert AuditLog.objects.for_facility(facility).count() == before_audit
    assert (
        not AuditLog.objects.for_facility(facility)
        .filter(action=AuditLog.Action.RECTIFICATION_APPROVED, target_id=str(request.pk))
        .exists()
    )


def test_ordinary_edit_preserves_inactive_value(facility, staff_user):
    record, field = make_record(facility, staff_user, "client", value="Retained value")
    field.is_active = False
    field.save(update_fields=["is_active"])
    update_client(record, staff_user, data_json={field.slug: "Ordinary edit"})
    record.refresh_from_db()
    assert safe_decrypt(record.data_json[field.slug]) == "Retained value"


@pytest.mark.parametrize("active", [False, True])
def test_invalid_number_correction_is_rejected_before_approval(facility, staff_user, lead_user, active):
    from core.models import FieldTemplate

    field = FieldTemplate.objects.create(facility=facility, name="Number", field_type="number", is_active=active)
    record, _ = make_record(facility, staff_user, "client", field=field, value=45)
    request = request_rectification(record, staff_user, f"data_json.{field.slug}", "Invalid number", "Correction")
    with pytest.raises(ValidationError):
        approve_rectification(request, lead_user)
    request.refresh_from_db()
    record.refresh_from_db()
    assert record.data_json[field.slug] == 45
    assert request.status == RectificationRequest.Status.PENDING
    assert (
        not AuditLog.objects.for_facility(facility)
        .filter(action=AuditLog.Action.RECTIFICATION_APPROVED, target_id=str(request.pk))
        .exists()
    )
