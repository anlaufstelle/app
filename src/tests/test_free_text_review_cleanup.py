"""Review metadata disappears with erased or unavailable source records."""

import pytest
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, DocumentTypeField, FieldTemplate, FreeTextReview
from core.services.file_vault import is_encrypted_value, safe_decrypt
from core.services.free_text_review import get_entry, mark_review
from tests._factories import make_document_type, make_event
from tests.test_free_text_review import populate

pytestmark = pytest.mark.django_db


@pytest.fixture
def marked_event(request, facility, staff_user):
    obj, field = populate(request, "Event")
    entry = get_entry(facility, staff_user, "Event", obj.pk, field)
    mark_review(facility, staff_user, "Event", obj.pk, field, timezone.localdate(), entry.fingerprint)
    return obj


def test_physical_delete_removes_event_marker(marked_event, facility):
    target_id = marked_event.pk
    assert FreeTextReview.objects.for_facility(facility).filter(target_type="Event", target_id=target_id).exists()
    marked_event.delete()
    assert not FreeTextReview.objects.for_facility(facility).exists()


@pytest.mark.parametrize("state", ["trash", "inactive", "anonymize"])
def test_person_revocation_cleans_event_marker(marked_event, facility, staff_user, state):
    person = marked_event.client
    if state == "trash":
        person.soft_delete(user=staff_user)
    elif state == "inactive":
        person.is_active = False
        person.save(update_fields=["is_active"])
    else:
        from core.services.client.main import anonymize_client

        anonymize_client(person, user=staff_user)
    assert not FreeTextReview.objects.for_facility(facility).exists()


@pytest.mark.parametrize("fixture", ["case_open", "episode", "sample_workitem"])
def test_removed_targets_do_not_own_review_markers(request, marked_event, facility, staff_user, fixture):
    from django.http import Http404

    target = request.getfixturevalue(fixture)
    with pytest.raises(Http404):
        get_entry(facility, staff_user, type(target).__name__, target.pk, "description")
    target.delete()
    assert list(FreeTextReview.objects.for_facility(facility).values_list("target_type", "target_id")) == [
        ("Event", marked_event.pk)
    ]


def test_marked_event_soft_delete_removes_metadata(facility, staff_user, sample_event):
    from core.services.events.crud import soft_delete_event

    entry = get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    mark_review(facility, staff_user, "Event", sample_event.pk, "notiz", timezone.localdate(), entry.fingerprint)
    soft_delete_event(sample_event, staff_user)
    assert not FreeTextReview.objects.for_facility(facility).filter(target_type="Event").exists()


@pytest.fixture
def marked_field_world(facility, staff_user, sample_event, second_facility, second_facility_user):
    field = FieldTemplate.objects.for_facility(facility).get(slug="notiz")
    other_field = FieldTemplate.objects.create(facility=facility, name="Other review", field_type="textarea")
    DocumentTypeField.objects.create(document_type=sample_event.document_type, field_template=other_field)
    sample_event.data_json[field.slug] = "Primary event content"
    sample_event.data_json[other_field.slug] = "Other field content"
    sample_event.save(update_fields=["data_json"])
    other_type = make_document_type(facility=facility, name="Other review type")
    DocumentTypeField.objects.create(document_type=other_type, field_template=field)
    second_event = make_event(
        facility=facility,
        client=None,
        document_type=other_type,
        occurred_at=timezone.now(),
        data_json={field.slug: "Second event content"},
        created_by=staff_user,
    )
    foreign_type = make_document_type(facility=second_facility, name="Foreign review type")
    foreign_field = FieldTemplate.objects.create(
        facility=second_facility, name="Foreign review", slug=field.slug, field_type="textarea"
    )
    DocumentTypeField.objects.create(document_type=foreign_type, field_template=foreign_field)
    foreign_event = make_event(
        facility=second_facility,
        client=None,
        document_type=foreign_type,
        occurred_at=timezone.now(),
        data_json={field.slug: "Foreign event content"},
        created_by=second_facility_user,
    )
    reviews = []
    for scope, user, event, key in [
        (facility, staff_user, sample_event, field.slug),
        (facility, staff_user, second_event, field.slug),
        (facility, staff_user, sample_event, other_field.slug),
        (second_facility, second_facility_user, foreign_event, field.slug),
    ]:
        entry = get_entry(scope, user, "Event", event.pk, key)
        reviews.append(mark_review(scope, user, "Event", event.pk, key, timezone.localdate(), entry.fingerprint))
    return field, (sample_event, second_event), reviews


@pytest.mark.parametrize("write", ["full", "partial", "high_sensitivity"])
def test_encryption_upgrade_removes_only_own_field_reviews(marked_field_world, facility, second_facility, write):
    field, events, reviews = marked_field_world
    kept = {
        scope.pk: list(FreeTextReview.objects.for_facility(scope).filter(pk=review.pk).values())
        for scope, review in [(facility, reviews[2]), (second_facility, reviews[3])]
    }
    audit_before = list(AuditLog.objects.for_facility(facility).values())
    if write == "high_sensitivity":
        field.sensitivity = "high"
        field.save(update_fields=["sensitivity"])
    else:
        field.is_encrypted = True
        field.save(update_fields=["is_encrypted"] if write == "partial" else None)
    assert not FreeTextReview.objects.for_facility(facility).filter(field_key=field.slug).exists()
    encrypted_values = {}
    for event in events:
        event.refresh_from_db()
        assert is_encrypted_value(event.data_json[field.slug])
        encrypted_values[event.pk] = event.data_json[field.slug]
    field.sensitivity = "normal"
    field.is_encrypted = False
    field.save(update_fields=["sensitivity", "is_encrypted"])
    assert not FreeTextReview.objects.for_facility(facility).filter(field_key=field.slug).exists()
    for scope in (facility, second_facility):
        assert list(FreeTextReview.objects.for_facility(scope).values()) == kept[scope.pk]
    assert list(AuditLog.objects.for_facility(facility).values()) == audit_before
    for event, text in zip(events, ["Primary event content", "Second event content"], strict=True):
        event.refresh_from_db()
        assert event.data_json[field.slug] == encrypted_values[event.pk]
        assert safe_decrypt(event.data_json[field.slug]) == text


def test_unpersisted_encryption_flag_keeps_reviews(marked_field_world, facility):
    field, events, _reviews = marked_field_world
    before = list(FreeTextReview.objects.for_facility(facility).values())
    field.is_encrypted = True
    field.help_text = "Updated help"
    field.save(update_fields=["help_text"])
    field.refresh_from_db()
    assert not field.is_encrypted
    assert list(FreeTextReview.objects.for_facility(facility).values()) == before
    for event in events:
        event.refresh_from_db()
        assert not is_encrypted_value(event.data_json[field.slug])


def test_encryption_upgrade_cleanup_rolls_back_with_configuration(marked_field_world, facility):
    field, events, _reviews = marked_field_world
    before = list(FreeTextReview.objects.for_facility(facility).values())
    with pytest.raises(ValueError, match="Abort configuration change"), transaction.atomic():
        field.is_encrypted = True
        field.save(update_fields=["is_encrypted"])
        assert not FreeTextReview.objects.for_facility(facility).filter(field_key=field.slug).exists()
        raise ValueError("Abort configuration change")
    field.refresh_from_db()
    assert not field.is_encrypted
    assert list(FreeTextReview.objects.for_facility(facility).values()) == before
    for event in events:
        event.refresh_from_db()
        assert not is_encrypted_value(event.data_json[field.slug])
