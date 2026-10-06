"""Anonymize-now message and confirmation make no deadline promise (Refs #2425).

The earlier versions of the event documentation are erased by the nightly
retention run as a rule, but its history sweep defers rows under a legal hold
and skips rows locked by another transaction (``skip_locked``); a later run
catches up. "At the latest with the nightly run" was a promise the code does
not keep.
"""

import html

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def trashed_client(client_identified, admin_user):
    client_identified.soft_delete(user=admin_user)
    return client_identified


def test_success_message_names_regular_run_and_catch_up(client, admin_user, trashed_client):
    client.force_login(admin_user)

    response = client.post(reverse("core:client_anonymize", args=[trashed_client.pk]))

    messages = " ".join(str(m.message) for m in response.wsgi_request._messages)
    assert "Person wurde anonymisiert." in messages
    assert "spätestens" not in messages
    assert "tilgt regulär der nächtliche Aufbewahrungslauf" in messages
    assert "Aufbewahrungssperre oder eine gleichzeitige Bearbeitung" in messages
    assert "holt ein späterer Lauf nach" in messages


def test_confirmation_dialog_names_regular_run_and_catch_up(client, admin_user, trashed_client):
    client.force_login(admin_user)

    body = html.unescape(client.get(reverse("core:client_trash")).content.decode())

    assert "spätestens mit dem nächtlichen Aufbewahrungslauf" not in body
    assert "folgen regulär mit dem nächtlichen Aufbewahrungslauf" in body
    assert "Aufbewahrungssperre oder eine gleichzeitige Bearbeitung" in body
