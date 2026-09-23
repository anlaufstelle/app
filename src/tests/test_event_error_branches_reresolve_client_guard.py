"""Ereignis-Neuanzeige löst die Person nach Service-Ablehnung erneut auf."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import Client, Event
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE, restrict_processing
from core.views import events as event_views


def _payload(document_type, person):
    return {
        "document_type": str(document_type.pk),
        "client": str(person.pk),
        "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        "dauer": "15",
        "notiz": "Bleibt ungespeichert",
    }


def _assert_rejected_without_prefill(response, person, document_type):
    assert response.status_code == 200
    assert response.context["client_id"] == ""
    assert response.context["client_pseudonym"] == ""
    html = response.content.decode()
    assert 'data-initial-client-id=""' in html
    assert 'data-initial-pseudonym=""' in html
    assert person.pseudonym not in html
    assert not Event.objects.filter(document_type=document_type, client=person).exists()


@pytest.mark.django_db
def test_inner_service_rejection_reresolves_person(
    client,
    staff_user,
    client_identified,
    doc_type_contact,
    monkeypatch,
):
    original_stage_uploads = event_views.stage_uploads

    @contextmanager
    def restrict_before_saving(facility, user, file_fields):
        with original_stage_uploads(facility, user, file_fields) as staged:
            restrict_processing(
                client_identified,
                staff_user,
                reason=Client.RestrictionReason.ACCURACY_DISPUTED,
            )
            yield staged

    monkeypatch.setattr(event_views, "stage_uploads", restrict_before_saving)
    client.force_login(staff_user)

    response = client.post(
        reverse("core:event_create"),
        _payload(doc_type_contact, client_identified),
    )

    _assert_rejected_without_prefill(response, client_identified, doc_type_contact)


@pytest.mark.django_db
def test_outer_service_rejection_reresolves_person(
    client,
    staff_user,
    client_identified,
    doc_type_contact,
    monkeypatch,
):
    @contextmanager
    def reject_during_staging(facility, user, file_fields):
        restrict_processing(
            client_identified,
            staff_user,
            reason=Client.RestrictionReason.ACCURACY_DISPUTED,
        )
        raise ValidationError(PROCESSING_RESTRICTED_MESSAGE)
        yield  # pragma: no cover

    monkeypatch.setattr(event_views, "stage_uploads", reject_during_staging)
    client.force_login(staff_user)

    response = client.post(
        reverse("core:event_create"),
        _payload(doc_type_contact, client_identified),
    )

    _assert_rejected_without_prefill(response, client_identified, doc_type_contact)
