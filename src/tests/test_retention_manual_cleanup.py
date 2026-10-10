"""Manual retention behavior through public paths (Refs #2524)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import EventHistory, StatisticsSnapshot
from core.services.dashboard import create_or_update_snapshot
from core.services.events import soft_delete_event
from tests._factories import make_event

pytestmark = pytest.mark.django_db


def test_manual_deletion_carries_month_exactly_once(facility, doc_type_contact, staff_user):
    event = make_event(
        facility=facility,
        document_type=doc_type_contact,
        created_by=staff_user,
        client=None,
        is_anonymous=True,
        occurred_at=timezone.now(),
        data_json={},
    )
    date = timezone.localtime(event.occurred_at)
    create_or_update_snapshot(facility, date.year, date.month)

    soft_delete_event(event, staff_user)
    create_or_update_snapshot(facility, date.year, date.month)

    snapshot = StatisticsSnapshot.objects.get(facility=facility, year=date.year, month=date.month)
    assert snapshot.data["total_contacts"] == 1
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        soft_delete_event(event, staff_user)
    create_or_update_snapshot(facility, date.year, date.month)
    snapshot.refresh_from_db()
    assert snapshot.data["total_contacts"] == 1


def test_person_page_shows_pending_history_without_exposing_content(
    client, staff_user, client_identified, facility, doc_type_contact
):
    event = make_event(
        facility=facility,
        document_type=doc_type_contact,
        created_by=staff_user,
        client=client_identified,
        occurred_at=timezone.now(),
        data_json={},
    )
    EventHistory.objects.create(event=event, action="create", data_after={"secret": "Do not render this"})
    client_identified.is_active = False
    client_identified.anonymized_at = timezone.now()
    client_identified.save()
    client.force_login(staff_user)

    response = client.get(reverse("core:client_detail", args=[client_identified.pk]))

    assert response.status_code == 200
    assert response.context["history_deferred"] == 1
    assert "Historie wird im Nachtlauf redigiert" in response.content.decode()
    assert "Do not render this" not in response.content.decode()


def test_event_delete_page_names_confirmation_permission(
    client, staff_user, client_qualified, facility, doc_type_contact
):
    event = make_event(
        facility=facility,
        document_type=doc_type_contact,
        created_by=staff_user,
        client=client_qualified,
        occurred_at=timezone.now(),
        data_json={},
    )
    client.force_login(staff_user)
    response = client.get(reverse("core:event_delete", args=[event.pk]))
    assert response.status_code == 200
    assert "Löschbestätigung" in response.content.decode()


def test_trash_exposes_pending_history_of_anonymised_person(
    client, admin_user, client_identified, facility, doc_type_contact
):
    event = make_event(
        facility=facility,
        document_type=doc_type_contact,
        created_by=admin_user,
        client=client_identified,
        occurred_at=timezone.now(),
        data_json={},
    )
    EventHistory.objects.create(event=event, action="create", data_after={"note": "Still held"})
    client_identified.soft_delete(user=admin_user)
    client_identified.anonymized_at = timezone.now()
    client_identified.save()
    client.force_login(admin_user)
    response = client.get(reverse("core:client_trash"))
    assert response.status_code == 200
    assert 'data-testid="client-history-status"' in response.content.decode()
    assert "Noch offene Historienfassung: 1" in response.content.decode()
    assert "Still held" not in response.content.decode()
