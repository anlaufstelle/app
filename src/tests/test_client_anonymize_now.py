"""Papierkorb-Aktion „Jetzt endgültig anonymisieren" (Refs #1737, Option B).

Die Anonymisierung war per UI nirgends auslösbar: sie passierte nur nächtlich
nach Ablauf von ``Settings.client_trash_days``. Damit war eine Lösch-Blockade
durch eine Person im Papierkorb ohne Datenresurrektion (Wiederherstellen) oder
30 Tage Warten nicht auflösbar.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import AuditLog, DocumentType, Event, LegalHold
from core.services.client import anonymize_client_now

pytestmark = pytest.mark.django_db


@pytest.fixture
def trashed_client(client_identified, admin_user, doc_type_client):
    client_identified.document_type = doc_type_client
    client_identified.data_json = {"notiz-frei": "Klartext"}
    client_identified.notes = "Interne Notiz"
    client_identified.save(update_fields=["document_type", "data_json", "notes"])
    client_identified.soft_delete(user=admin_user)
    return client_identified


@pytest.fixture
def doc_type_client(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Personenbogen",
        slug="personenbogen",
        entity_type=DocumentType.EntityType.CLIENT,
    )


class TestAnonymizeClientNowService:
    def test_redacts_identity_and_dynamic_data(self, trashed_client, admin_user):
        anonymize_client_now(trashed_client, admin_user)
        trashed_client.refresh_from_db()
        assert trashed_client.pseudonym.startswith("Gelöscht-")
        assert trashed_client.notes == ""
        assert trashed_client.data_json == {}
        assert trashed_client.document_type_id is None
        # Der Datensatz bleibt im Papierkorb — anonymisiert, nicht auferstanden.
        assert trashed_client.is_deleted is True

    def test_writes_audit_entry_with_manual_trigger(self, trashed_client, admin_user):
        anonymize_client_now(trashed_client, admin_user)
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_ANONYMIZED,
            target_id=str(trashed_client.pk),
        ).first()
        assert entry is not None
        assert entry.user_id == admin_user.pk
        assert entry.detail.get("trigger") == "manual"

    def test_refuses_client_outside_trash(self, client_identified, admin_user):
        with pytest.raises(ValidationError):
            anonymize_client_now(client_identified, admin_user)

    def test_refuses_client_under_legal_hold(self, trashed_client, admin_user, facility, lead_user):
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=trashed_client.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        with pytest.raises(ValidationError):
            anonymize_client_now(trashed_client, admin_user)
        trashed_client.refresh_from_db()
        assert trashed_client.notes == "Interne Notiz"

    def test_refuses_when_an_event_is_under_legal_hold(
        self, trashed_client, admin_user, facility, lead_user, doc_type_contact
    ):
        from django.utils import timezone

        event = Event.objects.create(
            facility=facility,
            client=trashed_client,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
        )
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        with pytest.raises(ValidationError):
            anonymize_client_now(trashed_client, admin_user)


class TestAnonymizeClientNowView:
    def _url(self, client_obj):
        return reverse("core:client_anonymize", args=[client_obj.pk])

    def test_admin_can_trigger(self, client, admin_user, trashed_client):
        client.force_login(admin_user)
        response = client.post(self._url(trashed_client))
        assert response.status_code == 302
        trashed_client.refresh_from_db()
        assert trashed_client.data_json == {}
        assert trashed_client.notes == ""

    def test_lead_is_denied(self, client, lead_user, trashed_client):
        client.force_login(lead_user)
        assert client.post(self._url(trashed_client)).status_code == 403

    def test_staff_is_denied(self, client, staff_user, trashed_client):
        client.force_login(staff_user)
        assert client.post(self._url(trashed_client)).status_code == 403

    def test_non_trashed_client_is_404(self, client, admin_user, client_identified):
        client.force_login(admin_user)
        assert client.post(self._url(client_identified)).status_code == 404

    def test_legal_hold_yields_error_message_not_500(self, client, admin_user, trashed_client, facility, lead_user):
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=trashed_client.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        client.force_login(admin_user)
        response = client.post(self._url(trashed_client))
        assert response.status_code == 302
        msgs = [str(m.message) for m in response.wsgi_request._messages]
        assert any("Aufbewahrungssperre" in m or "Legal Hold" in m for m in msgs)


class TestTrashTemplateAction:
    def test_button_present_with_confirmation(self, client, admin_user, trashed_client):
        client.force_login(admin_user)
        body = client.get(reverse("core:client_trash")).content.decode()
        assert "client-anonymize-now-btn" in body
        assert "data-confirm=" in body
        assert reverse("core:client_anonymize", args=[trashed_client.pk]) in body
