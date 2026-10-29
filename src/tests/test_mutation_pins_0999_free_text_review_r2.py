"""Pins for client-specific review cleanup within a facility.

Source: mutation delta of the release-0.99.9 checkpoint, 1 mutant.
Groups: correlated event ownership during client cleanup (1).
"""

import pytest
from django.utils import timezone

from core.models import FreeTextReview
from core.services.free_text_review import get_entry, mark_review, remove_target_reviews
from tests._factories import make_client, make_event

pytestmark = pytest.mark.django_db


def test_client_cleanup_keeps_another_clients_event_review(facility, staff_user, sample_event):
    """Removing one client's reviews preserves another client's marker: x_remove_target_reviews__mutmut_8."""
    other_client = make_client(facility=facility, pseudonym="Other person", created_by=staff_user)
    other_event = make_event(
        facility=facility,
        client=other_client,
        document_type=sample_event.document_type,
        occurred_at=timezone.now(),
        data_json={"notiz": "Other person's text"},
        created_by=staff_user,
    )
    markers = []
    for event in (other_event, sample_event):
        entry = get_entry(facility, staff_user, "Event", event.pk, "notiz")
        markers.append(
            mark_review(facility, staff_user, "Event", event.pk, "notiz", timezone.localdate(), entry.fingerprint)
        )
    kept = list(FreeTextReview.objects.for_facility(facility).filter(pk=markers[0].pk).values())

    remove_target_reviews(sample_event.client)

    assert not FreeTextReview.objects.for_facility(facility).filter(pk=markers[1].pk).exists()
    assert list(FreeTextReview.objects.for_facility(facility).values()) == kept
