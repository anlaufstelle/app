"""Undoing a tally entry ends with the delete service's refusal, not with a server error.

The undo deletes the entry through the regular delete service, which refuses
under its row locks while a legal hold rests on the entry. An htmx call gets
the short refusal without a swap (the tally row stays as it is, the global
error toast shows the message); a plain form post returns to its target with
the message. The entry keeps existing, and no deletion history or audit row is
written. Any other error still propagates.

Observed behaviour only: status, headers, body, redirect target, flash message
and stored rows.
"""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from core.models import AuditLog, EventHistory, LegalHold
from core.services.events.tally_undo import mint_tally_undo_token
from tests.test_tally_undo import _tally_event

pytestmark = pytest.mark.django_db

_LEGAL_HOLD = (
    "Für dieses Ereignis besteht eine Aufbewahrungssperre (Legal Hold) — "
    "es kann nicht gelöscht werden, solange die Sperre gilt."
)


@pytest.fixture
def held_tally_event(facility, staff_user, lead_user, doc_type_contact):
    event = _tally_event(facility, staff_user, doc_type_contact)
    LegalHold.objects.create(
        facility=facility, target_type="Event", target_id=event.pk, reason="Court case", created_by=lead_user
    )
    return event


def _undo(client, event, *, htmx=False):
    return client.post(
        reverse("core:tally_undo"),
        {"token": mint_tally_undo_token(event), "next": reverse("core:zeitstrom")},
        headers={"HX-Request": "true"} if htmx else {},
    )


def _assert_untouched(event):
    event.refresh_from_db()
    assert event.is_deleted is False
    assert not EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).exists()
    assert not AuditLog.objects.filter(
        action=AuditLog.Action.DELETE, target_type="Event", target_id=str(event.pk)
    ).exists()


def test_htmx_undo_under_a_legal_hold_is_refused_without_a_swap(client, staff_user, held_tally_event):
    client.force_login(staff_user)

    response = _undo(client, held_tally_event, htmx=True)

    assert response.status_code == 409
    assert response["HX-Reswap"] == "none"
    assert response.content.decode() == _LEGAL_HOLD
    _assert_untouched(held_tally_event)


def test_form_undo_under_a_legal_hold_returns_with_the_message(client, staff_user, held_tally_event):
    client.force_login(staff_user)

    response = _undo(client, held_tally_event)

    assert response.status_code == 302
    assert response.url == reverse("core:zeitstrom")
    assert [str(m) for m in get_messages(response.wsgi_request)] == [_LEGAL_HOLD]
    _assert_untouched(held_tally_event)


def test_unexpected_error_propagates(client, staff_user, held_tally_event, monkeypatch):
    def broken_undo(facility, user, token):
        raise RuntimeError("unexpected")

    monkeypatch.setattr("core.views.events.undo_tally_event", broken_undo)
    client.force_login(staff_user)

    with pytest.raises(RuntimeError, match="unexpected"):
        _undo(client, held_tally_event, htmx=True)
