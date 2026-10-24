"""Dynamic field contracts for episodes and work items."""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, DocumentType, Episode
from core.services.case.episodes import create_episode, episode_basis_token, update_episode
from core.services.case.workitems import create_workitem, update_workitem
from core.services.file_vault import safe_decrypt
from tests.test_episode_workitem_dynamic_data import carrier as carrier
from tests.test_episode_workitem_dynamic_data import make_record


def service_create(carrier, staff_user, **data):
    kind, _model, kwargs, doc_type, field, _assignment = carrier
    payload = {
        "title": "Service record",
        "document_type": doc_type,
        "data_json": {field.slug: "Service secret", "unknown": "Drop"},
    } | data
    if kind == "episode":
        return create_episode(kwargs["case"], staff_user, **payload)
    return create_workitem(kwargs["facility"], staff_user, **payload)


def service_update(record, staff_user, **data):
    if isinstance(record, Episode):
        return update_episode(record, staff_user, basis_token=episode_basis_token(record), **data)
    return update_workitem(record, staff_user, expected_updated_at=record.updated_at, **data)


@pytest.mark.django_db
def test_services_clean_create_and_update(carrier, staff_user):
    record = service_create(carrier, staff_user)
    field = carrier[4]
    record.refresh_from_db()
    assert set(record.data_json) == {field.slug}
    assert safe_decrypt(record.data_json[field.slug]) == "Service secret"
    service_update(record, staff_user, data_json={field.slug: "Updated secret", "unknown": "Drop"})
    record.refresh_from_db()
    assert set(record.data_json) == {field.slug}
    assert safe_decrypt(record.data_json[field.slug]) == "Updated secret"
    assert "Updated secret" not in str(
        list(AuditLog.objects.filter(target_id=str(record.pk)).values_list("detail", flat=True))
    )


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("invalid", ["entity", "facility", "missing"])
def test_services_reject_invalid_type(carrier, staff_user, operation, invalid):
    from core.models import Facility

    doc_type = None
    if invalid != "missing":
        facility = carrier[3].facility
        if invalid == "facility":
            facility = Facility.objects.create(organization=facility.organization, name="Other facility")
        doc_type = DocumentType.objects.create(
            facility=facility, name="Invalid type", entity_type="client" if invalid == "entity" else carrier[0]
        )
    record = make_record(carrier) if operation == "update" else None
    with pytest.raises(ValidationError):
        if record is None:
            service_create(carrier, staff_user, document_type=doc_type)
        else:
            service_update(record, staff_user, document_type=doc_type, data_json={carrier[4].slug: "Attempt"})


@pytest.mark.django_db
def test_workitem_creation_rejects_raw_type_id(facility, staff_user):
    doc_type = DocumentType.objects.create(facility=facility, name="Task type", entity_type="workitem")
    with pytest.raises(ValueError, match="document_type_id"):
        create_workitem(facility, staff_user, title="Raw type attempt", document_type_id=doc_type.pk)
