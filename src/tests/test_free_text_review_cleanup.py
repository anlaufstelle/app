"""Review metadata disappears with erased or unavailable source records."""

import pytest
from django.utils import timezone

from core.models import FreeTextReview
from core.services.free_text_review import get_entry, mark_review
from tests.test_free_text_review import populate

pytestmark = pytest.mark.django_db


@pytest.fixture
def marked_targets(request, facility, staff_user):
    targets = {"Client": request.getfixturevalue("client_identified"), "Case": request.getfixturevalue("case_open")}
    for target_type in ["Event"]:
        obj, field = populate(request, target_type)
        entry = get_entry(facility, staff_user, target_type, obj.pk, field)
        mark_review(facility, staff_user, target_type, obj.pk, field, timezone.localdate(), entry.fingerprint)
        targets[target_type] = obj
    targets["Episode"] = request.getfixturevalue("episode")
    for target_type in ["Client", "Case", "Episode"]:
        FreeTextReview.objects.create(
            facility=facility,
            target_type=target_type,
            target_id=targets[target_type].pk,
            field_key="notes" if target_type == "Client" else "description",
            reviewed_until=timezone.localdate(),
            content_fingerprint="0" * 64,
        )
    return targets


@pytest.mark.parametrize("target_type", ["Event"])
def test_physical_delete_removes_markers(marked_targets, facility, target_type):
    obj = marked_targets[target_type]
    target_id = obj.pk
    obj.delete()
    assert (
        not FreeTextReview.objects.for_facility(facility).filter(target_type=target_type, target_id=target_id).exists()
    )


@pytest.mark.parametrize("state", ["trash", "inactive", "anonymize"])
def test_person_revocation_cleans_all_dependent_markers(marked_targets, facility, staff_user, state):
    person = marked_targets["Client"]
    if state == "trash":
        person.soft_delete(user=staff_user)
    elif state == "inactive":
        person.is_active = False
        person.save(update_fields=["is_active"])
    else:
        from core.services.client.main import anonymize_client

        anonymize_client(person, user=staff_user)
    assert not FreeTextReview.objects.for_facility(facility).exists()


@pytest.mark.parametrize("state", ["trash", "anonymize"])
def test_case_revocation_cleans_case_and_episode(marked_targets, facility, staff_user, state):
    case = marked_targets["Case"]
    if state == "trash":
        case.soft_delete(user=staff_user)
    else:
        from core.services.case.deletion import anonymize_case

        anonymize_case(case, user=staff_user, trigger="manual")
    assert not FreeTextReview.objects.for_facility(facility).filter(target_type__in=["Case", "Episode"]).exists()
    assert FreeTextReview.objects.for_facility(facility).filter(target_type="Event").exists()


def test_marked_event_soft_delete_removes_metadata(facility, staff_user, sample_event):
    from core.services.events.crud import soft_delete_event

    entry = get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    mark_review(facility, staff_user, "Event", sample_event.pk, "notiz", timezone.localdate(), entry.fingerprint)
    soft_delete_event(sample_event, staff_user)
    assert not FreeTextReview.objects.for_facility(facility).filter(target_type="Event").exists()
