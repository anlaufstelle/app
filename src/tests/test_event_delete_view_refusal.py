"""Deleting an event directly ends with the service's refusal on the page, not with a server error.

The delete service decides under the person and event row locks: a legal hold
on the event or its person, or a person binding changed by a parallel merge,
refuses the deletion there. The view shows that message and returns to the
event page; the event keeps its content, and no deletion history or audit row
is written. The cause is the one the service named at the locked state — the
view does not look at the event again. Any other error still propagates.

Observed behaviour only: status, redirect target, flash message and stored rows.
"""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from core.models import AuditLog, Client, Event, EventHistory, LegalHold
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_LEGAL_HOLD = (
    "Für dieses Ereignis besteht eine Aufbewahrungssperre (Legal Hold) — "
    "es kann nicht gelöscht werden, solange die Sperre gilt."
)
_BINDING_CHANGED = (
    "Die Personenzuordnung dieses Ereignisses wurde zwischenzeitlich geändert — "
    "bitte erneut öffnen und die Löschung wiederholen."
)


def _delete(client, event):
    return client.post(reverse("core:event_delete", kwargs={"pk": event.pk}), {"reason": ""})


def _deletion_traces(event):
    return (
        EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).count(),
        AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_type="Event", target_id=str(event.pk)).count(),
    )


def _assert_refused(response, event, message):
    assert response.status_code == 302
    assert response.url == reverse("core:event_detail", kwargs={"pk": event.pk})
    assert [str(m) for m in get_messages(response.wsgi_request)] == [message]
    stored = Event.objects.get(pk=event.pk)
    assert stored.is_deleted is False
    assert stored.data_json == event.data_json
    assert _deletion_traces(event) == (0, 0)


@pytest.mark.parametrize("hold_on", ["Event", "Client"])
def test_legal_hold_is_refused_with_its_message(client, facility, staff_user, lead_user, sample_event, hold_on):
    target_id = sample_event.pk if hold_on == "Event" else sample_event.client_id
    LegalHold.objects.create(
        facility=facility, target_type=hold_on, target_id=target_id, reason="Court case", created_by=lead_user
    )
    client.force_login(staff_user)

    response = _delete(client, sample_event)

    _assert_refused(response, sample_event, _LEGAL_HOLD)


def test_person_binding_changed_under_the_lock_is_refused_with_its_message(client, facility, staff_user, sample_event):
    other_person = Client.objects.create(facility=facility, pseudonym="Merge-Target", created_by=staff_user)
    client.force_login(staff_user)

    with mitschnitt() as recording:
        recording.nach_sperre(
            "core_client", lambda: Event.objects.filter(pk=sample_event.pk).update(client=other_person)
        )
        response = _delete(client, sample_event)

    assert recording.offene_schranken() == []
    _assert_refused(response, sample_event, _BINDING_CHANGED)


def test_local_staff_deletes_as_before(client, staff_user, sample_event):
    client.force_login(staff_user)

    response = _delete(client, sample_event)

    assert response.status_code == 302
    assert response.url == reverse("core:zeitstrom")
    assert Event.objects.get(pk=sample_event.pk).is_deleted is True
    assert _deletion_traces(sample_event) == (1, 1)


def test_unexpected_error_propagates(client, staff_user, sample_event, monkeypatch):
    def broken_delete(event, user):
        raise RuntimeError("unexpected")

    monkeypatch.setattr("core.views.events.soft_delete_event", broken_delete)
    client.force_login(staff_user)

    with pytest.raises(RuntimeError, match="unexpected"):
        _delete(client, sample_event)
