"""Tests für Client-Management."""

import pytest
from django.urls import reverse

from core.models import AuditLog, Client
from core.models.activity import Activity
from core.services.clients import update_client


@pytest.mark.django_db
class TestClientList:
    def test_client_list_renders(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_list"))
        assert response.status_code == 200

    def test_client_list_shows_clients(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_list"))
        assert client_identified.pseudonym in response.content.decode()

    def test_client_list_search_filter(self, client, staff_user, client_identified, client_qualified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_list"), {"q": "ID-01"})
        content = response.content.decode()
        assert "Test-ID-01" in content
        assert "Test-QU-01" not in content

    def test_client_list_stage_filter(self, client, staff_user, client_identified, client_qualified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_list"), {"stage": "qualified"})
        content = response.content.decode()
        assert "Test-QU-01" in content

    def test_client_list_facility_scoping(self, client, staff_user, facility, organization):
        """Clients anderer Facilities werden nicht angezeigt."""
        from core.models import Facility

        other_facility = Facility.objects.create(organization=organization, name="Andere")
        Client.objects.create(facility=other_facility, pseudonym="Fremder-01", created_by=staff_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:client_list"))
        assert "Fremder-01" not in response.content.decode()

    def test_client_list_htmx_returns_partial(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_list"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        # Partial should not extend base.html
        assert "<!DOCTYPE html>" not in response.content.decode()


@pytest.mark.django_db
class TestClientDetail:
    def test_client_detail_renders(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        assert response.status_code == 200
        assert client_identified.pseudonym in response.content.decode()

    def test_client_detail_shows_events(self, client, staff_user, sample_event, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        assert "Kontakt" in response.content.decode()

    def test_client_detail_qualified_creates_audit(self, client, staff_user, client_qualified):
        client.force_login(staff_user)
        client.get(reverse("core:client_detail", kwargs={"pk": client_qualified.pk}))
        assert AuditLog.objects.filter(
            action=AuditLog.Action.VIEW_QUALIFIED,
            target_type="Client",
            target_id=str(client_qualified.pk),
        ).exists()

    def test_client_detail_identified_no_audit(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.VIEW_QUALIFIED,
            target_type="Client",
        ).exists()


@pytest.mark.django_db
class TestClientCreate:
    def test_client_create_form_renders(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_create"))
        assert response.status_code == 200

    def test_client_create_success(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_create"),
            {"pseudonym": "Neuer-01", "contact_stage": "identified", "age_cluster": "unknown"},
        )
        assert response.status_code == 302
        new_client = Client.objects.get(pseudonym="Neuer-01")
        assert new_client.facility == facility
        assert new_client.created_by == staff_user

    def test_client_create_pseudonym_uniqueness(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_create"),
            {"pseudonym": client_identified.pseudonym, "contact_stage": "identified", "age_cluster": "unknown"},
        )
        assert response.status_code == 200  # Form error, no redirect
        content = response.content.decode()
        assert "existiert bereits" in content


@pytest.mark.django_db
class TestClientUpdate:
    def test_client_update_form_renders(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_update", kwargs={"pk": client_identified.pk}))
        assert response.status_code == 200

    def test_client_update_success(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", kwargs={"pk": client_identified.pk}),
            {
                "pseudonym": client_identified.pseudonym,
                "contact_stage": "qualified",
                "age_cluster": "18_26",
            },
        )
        assert response.status_code == 302
        client_identified.refresh_from_db()
        assert client_identified.contact_stage == Client.ContactStage.QUALIFIED

    def test_client_update_stage_change_creates_audit(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        client.post(
            reverse("core:client_update", kwargs={"pk": client_identified.pk}),
            {
                "pseudonym": client_identified.pseudonym,
                "contact_stage": "qualified",
                "age_cluster": "unknown",
            },
        )
        assert AuditLog.objects.filter(
            action=AuditLog.Action.STAGE_CHANGE,
            target_type="Client",
            target_id=str(client_identified.pk),
        ).exists()


@pytest.mark.django_db
class TestUpdateClientService:
    """Tests for the update_client service function."""

    def test_updates_fields(self, client_identified, staff_user):
        updated = update_client(
            client_identified,
            staff_user,
            pseudonym="Neues-Pseudonym",
            age_cluster=Client.AgeCluster.AGE_18_26,
        )
        updated.refresh_from_db()
        assert updated.pseudonym == "Neues-Pseudonym"
        assert updated.age_cluster == Client.AgeCluster.AGE_18_26

    def test_logs_updated_activity(self, client_identified, staff_user):
        update_client(client_identified, staff_user, notes="Neue Notiz")
        assert Activity.objects.filter(
            verb=Activity.Verb.UPDATED,
            target_id=client_identified.pk,
        ).exists()

    def test_stage_change_creates_audit(self, client_identified, staff_user):
        update_client(
            client_identified,
            staff_user,
            contact_stage=Client.ContactStage.QUALIFIED,
        )
        assert AuditLog.objects.filter(
            action=AuditLog.Action.STAGE_CHANGE,
            target_id=str(client_identified.pk),
        ).exists()

    def test_qualification_logs_qualified_activity(self, client_identified, staff_user):
        update_client(
            client_identified,
            staff_user,
            contact_stage=Client.ContactStage.QUALIFIED,
        )
        assert Activity.objects.filter(
            verb=Activity.Verb.QUALIFIED,
            target_id=client_identified.pk,
        ).exists()

    def test_no_stage_change_no_audit(self, client_identified, staff_user):
        update_client(client_identified, staff_user, notes="Nur Notiz")
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.STAGE_CHANGE,
        ).exists()

    def test_returns_updated_client(self, client_identified, staff_user):
        result = update_client(client_identified, staff_user, notes="Test")
        assert result.pk == client_identified.pk
        assert result.notes == "Test"
