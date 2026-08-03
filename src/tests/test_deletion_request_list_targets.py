"""Löschanträge-Liste: Ziel mit Pseudonym/Titel statt „Client"/„Event" (Refs #1736 E).

Die Liste zeigte als Ziel nur ``dr.target_type`` — ohne Pseudonym und ohne
Link war nicht erkennbar, um wen oder was es geht. Die Auflösung respektiert
die Sensibilitäts-Sichtbarkeit von Ereignissen: ein Ereignis, das die Rolle
nicht sehen darf, bleibt generisch und unverlinkt.
"""

import pytest
from django.urls import reverse

from core.models import DocumentType
from core.services.client import approve_client_deletion, request_client_deletion
from core.services.events import request_deletion

pytestmark = pytest.mark.django_db

LIST_URL_NAME = "core:deletion_request_list"


def _pending(response):
    return list(response.context["pending_requests"])


class TestClientTargets:
    def test_label_is_pseudonym(self, client, staff_user, lead_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Grund")
        client.force_login(lead_user)
        dr = _pending(client.get(reverse(LIST_URL_NAME)))[0]
        assert dr.target_label == client_identified.pseudonym

    def test_links_to_client_detail(self, client, staff_user, lead_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Grund")
        client.force_login(lead_user)
        dr = _pending(client.get(reverse(LIST_URL_NAME)))[0]
        assert dr.target_url == reverse("core:client_detail", args=[client_identified.pk])

    def test_pseudonym_rendered_in_page(self, client, staff_user, lead_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Grund")
        client.force_login(lead_user)
        body = client.get(reverse(LIST_URL_NAME)).content.decode()
        assert client_identified.pseudonym in body
        assert "deletion-target-link" in body

    def test_soft_deleted_client_has_label_but_no_link(
        self, client, staff_user, lead_user, admin_user, client_identified
    ):
        dr = request_client_deletion(client_identified, staff_user, "Grund")
        approve_client_deletion(dr, lead_user)
        client.force_login(admin_user)
        approved = list(client.get(reverse(LIST_URL_NAME)).context["approved_requests"])[0]
        assert approved.target_label == client_identified.pseudonym
        assert approved.target_url == ""


class TestEventTargets:
    def test_label_names_document_type(self, client, staff_user, lead_user, sample_event):
        request_deletion(sample_event, staff_user, "Grund")
        client.force_login(lead_user)
        dr = _pending(client.get(reverse(LIST_URL_NAME)))[0]
        assert sample_event.document_type.name in dr.target_label
        assert dr.target_url == reverse("core:event_detail", args=[sample_event.pk])

    def test_invisible_event_stays_generic(self, client, staff_user, assistant_user, sample_event):
        """Sensibilität schlägt Bequemlichkeit: kein Typname für eine Rolle,
        die das Ereignis nicht sehen darf (Refs #1725-Muster)."""
        sample_event.document_type.sensitivity = DocumentType.Sensitivity.HIGH
        sample_event.document_type.save(update_fields=["sensitivity"])
        request_deletion(sample_event, staff_user, "Grund")
        assistant_user.can_confirm_deletion = True
        assistant_user.save(update_fields=["can_confirm_deletion"])
        client.force_login(assistant_user)
        dr = _pending(client.get(reverse(LIST_URL_NAME)))[0]
        assert sample_event.document_type.name not in dr.target_label
        assert dr.target_url == ""


class TestMissingTarget:
    def test_unknown_target_gets_neutral_label(self, client, lead_user, facility, staff_user):
        import uuid

        from core.models import DeletionRequest

        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=uuid.uuid4(),
            reason="Ziel weg",
            requested_by=staff_user,
        )
        client.force_login(lead_user)
        dr = _pending(client.get(reverse(LIST_URL_NAME)))[0]
        assert dr.target_label
        assert dr.target_url == ""
