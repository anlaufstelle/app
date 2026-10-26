"""Review metadata disappears with erased or unavailable source records."""

import pytest
from django.utils import timezone

from core.models import FreeTextReview
from core.services.free_text_review import get_entry, mark_review
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
