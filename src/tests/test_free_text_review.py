"""Public service contracts for role-scoped free-text reviews."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.utils import timezone

pytestmark = pytest.mark.django_db


def service():
    from core.services import free_text_review

    return free_text_review


def populate(request, target_type):
    fixture, field = {
        "Client": ("client_identified", "notes"),
        "Case": ("case_open", "description"),
        "Episode": ("episode", "description"),
        "WorkItem": ("sample_workitem", "description"),
        "Event": ("sample_event", "notiz"),
    }[target_type]
    obj = request.getfixturevalue(fixture)
    if target_type in {"Client", "Case", "Episode", "WorkItem"}:
        return obj, field
    if target_type == "Event":
        obj.data_json = {"notiz": "Private test content"}
        obj.save(update_fields=["data_json"])
    else:
        setattr(obj, field, "Private test content")
        obj.save(update_fields=[field])
    return obj, field


@pytest.mark.parametrize("target_type", ["Client", "Case", "Episode", "WorkItem", "Event"])
@pytest.mark.parametrize("role", ["assistant_user", "staff_user", "lead_user", "admin_user", "super_admin_user"])
def test_role_matrix(request, facility, target_type, role):
    obj, field = populate(request, target_type)
    user = request.getfixturevalue(role)
    if role == "super_admin_user":
        with pytest.raises(PermissionDenied):
            service().list_page(facility, user, target_type)
        return
    if target_type in {"Client", "Case", "Episode", "WorkItem"}:
        with pytest.raises(Http404):
            service().list_page(facility, user, target_type)
        with pytest.raises(Http404):
            service().get_entry(facility, user, target_type, obj.pk, field)
        with pytest.raises(Http404):
            service().mark_review(facility, user, target_type, obj.pk, field, timezone.localdate(), "stale")
        with pytest.raises(Http404):
            service().revoke_review(facility, user, target_type, obj.pk, field)
        return
    entries = service().list_page(facility, user, target_type).entries
    allowed = role != "assistant_user" or target_type in {"WorkItem", "Event"}
    assert [(entry.target_id, entry.field_key) for entry in entries] == ([(obj.pk, field)] if allowed else [])


@pytest.mark.parametrize("target_type", ["Event"])
@pytest.mark.parametrize("state", ["deleted", "inactive", "foreign"])
def test_unavailable_targets_are_hidden(request, facility, staff_user, target_type, state, second_facility):
    obj, field = populate(request, target_type)
    person = request.getfixturevalue("client_identified")
    if state == "foreign":
        scope = second_facility
        with pytest.raises(PermissionDenied):
            service().list_page(scope, staff_user, target_type)
        return
    setattr(person, "is_deleted" if state == "deleted" else "is_active", state == "deleted")
    person.save()
    assert service().list_page(facility, staff_user, target_type).entries == []
    with pytest.raises(Http404):
        service().get_entry(facility, staff_user, target_type, obj.pk, field)
    with pytest.raises(Http404):
        service().mark_review(facility, staff_user, target_type, obj.pk, field, timezone.localdate(), "stale")


@pytest.mark.parametrize("target_type", ["Event"])
def test_review_roundtrip_revocation_and_content_change(request, facility, staff_user, target_type):
    from core.models import AuditLog, FreeTextReview

    obj, field = populate(request, target_type)
    entry = service().get_entry(facility, staff_user, target_type, obj.pk, field)
    until = timezone.localdate() + timedelta(days=14)
    service().mark_review(facility, staff_user, target_type, obj.pk, field, until, entry.fingerprint)
    review = FreeTextReview.objects.for_facility(facility).get()
    assert review.reviewed_until == until
    assert review.reviewed_by == staff_user
    assert review.reviewed_at is not None
    assert review.content_fingerprint == entry.fingerprint
    assert "Private test content" not in str(review.__dict__)
    assert service().get_entry(facility, staff_user, target_type, obj.pk, field).is_reviewed
    # Repeated marking updates one unique row.
    service().mark_review(facility, staff_user, target_type, obj.pk, field, until, entry.fingerprint)
    assert FreeTextReview.objects.for_facility(facility).count() == 1
    service().revoke_review(facility, staff_user, target_type, obj.pk, field)
    assert not FreeTextReview.objects.for_facility(facility).exists()
    assert not service().get_entry(facility, staff_user, target_type, obj.pk, field).is_reviewed
    audit = list(AuditLog.objects.for_facility(facility).filter(action__startswith="free_text_review"))
    assert len(audit) == 3
    assert {row.action for row in audit} == {"free_text_reviewed", "free_text_review_revoked"}
    assert all("Private test content" not in str(row.detail) for row in audit)
    service().mark_review(facility, staff_user, target_type, obj.pk, field, until, entry.fingerprint)
    if target_type == "Event":
        obj.data_json = {"notiz": "Changed content"}
        obj.save(update_fields=["data_json"])
    else:
        setattr(obj, field, "Changed content")
        obj.save(update_fields=[field])
    assert not service().get_entry(facility, staff_user, target_type, obj.pk, field).is_reviewed
    with pytest.raises(ValidationError):
        service().mark_review(facility, staff_user, target_type, obj.pk, field, until, entry.fingerprint)


@pytest.mark.parametrize("target_type", ["Event"])
def test_personless_targets_remain_visible(request, facility, assistant_user, target_type):
    obj, field = populate(request, target_type)
    obj.client = None
    obj.save(update_fields=["client"])
    assert service().get_entry(facility, assistant_user, target_type, obj.pk, field).text == "Private test content"


def test_event_fields_fail_closed(facility, assistant_user, lead_user, sample_event, doc_type_contact):
    from core.models import DocumentType, DocumentTypeField, FieldTemplate

    for name, options in [
        ("Secret", {"is_encrypted": True, "sensitivity": "high"}),
        ("Restricted", {"sensitivity": "elevated"}),
        ("Numeric", {"field_type": "number"}),
    ]:
        field = FieldTemplate.objects.create(facility=facility, name=name, **options)
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=field)
    sample_event.data_json = {
        "notiz": "Visible",
        "secret": "Hidden",
        "restricted": "Hidden",
        "numeric": 42,
        "unknown": "Hidden",
        "dauer": {"__encrypted__": True, "value": "Hidden"},
    }
    sample_event.save()
    assert [entry.field_key for entry in service().list_page(facility, assistant_user, "Event").entries] == ["notiz"]
    assert {entry.field_key for entry in service().list_page(facility, lead_user, "Event").entries} == {
        "notiz",
        "restricted",
    }
    doc_type_contact.sensitivity = DocumentType.Sensitivity.ELEVATED
    doc_type_contact.save()
    assert service().list_page(facility, assistant_user, "Event").entries == []
    with pytest.raises(Http404):
        service().get_entry(facility, assistant_user, "Event", sample_event.pk, "notiz")


def test_expired_review_and_key_change(facility, staff_user, sample_event, settings):
    sample_event.data_json = {"notiz": "Original content"}
    sample_event.save()
    entry = service().get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    yesterday = timezone.localdate() - timedelta(days=1)
    with pytest.raises(ValidationError):
        service().mark_review(facility, staff_user, "Event", sample_event.pk, "notiz", yesterday, entry.fingerprint)
    service().mark_review(
        facility, staff_user, "Event", sample_event.pk, "notiz", timezone.localdate(), entry.fingerprint
    )
    settings.SECRET_KEY = "different-server-secret"
    assert not service().get_entry(facility, staff_user, "Event", sample_event.pk, "notiz").is_reviewed
