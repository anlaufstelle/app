"""HTTP approval stages and precise bulk decision messages."""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.models import Event
from core.services.retention import approve_proposal, create_proposal
from tests.test_retention_two_person_approval import proposal as proposal
from tests.test_retention_two_person_approval import snapshot

pytestmark = pytest.mark.django_db


def test_view_first_repeat_and_second(proposal, lead_user, admin_user, client):
    url = reverse("core:retention_approve", args=[proposal.pk])
    client.force_login(lead_user)
    first = client.post(url, HTTP_HX_REQUEST="true")
    assert first.status_code == 200
    assert "1 von 2 Freigaben" in first.content.decode()
    assert lead_user.username in first.content.decode()
    assert "Zweite Freigabe erteilen" in first.content.decode()
    before = snapshot(proposal)
    assert client.post(url, HTTP_HX_REQUEST="true").status_code == 409
    assert snapshot(proposal) == before
    client.force_login(admin_user)
    second = client.post(url, HTTP_HX_REQUEST="true")
    assert second.status_code == 200
    assert "Wird beim nächsten Lauf gelöscht" in second.content.decode()


def test_bulk_reports_first_final_and_skipped(proposal, lead_user, admin_user, client):
    event = Event.objects.get(pk=proposal.target_id)
    event.pk = None
    event.save()
    other = create_proposal(proposal.facility, "Event", event.pk, timezone.localdate(), {}, "qualified")[0]
    approve_proposal(proposal, lead_user)
    client.force_login(admin_user)
    response = client.post(reverse("core:retention_bulk_approve"), {"proposal_ids": [proposal.pk, other.pk]})
    text = " ".join(str(m) for m in get_messages(response.wsgi_request))
    assert "1 erste Freigabe erteilt" in text
    assert "1 Löschvorschlag endgültig freigegeben" in text
    client.get(reverse("core:retention_dashboard"))
    response = client.post(reverse("core:retention_bulk_approve"), {"proposal_ids": [other.pk]})
    text = " ".join(str(m) for m in get_messages(response.wsgi_request))
    assert "1 Löschvorschlag übersprungen" in text
    assert "endgültig freigegeben" not in text
