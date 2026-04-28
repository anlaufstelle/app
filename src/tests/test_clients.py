"""Tests für Client-Management."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import AuditLog, Case, Client, Episode, WorkItem
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


@pytest.mark.django_db
class TestOptimisticLockingClient:
    """Tests for optimistic locking on Client updates (Refs #531)."""

    def test_optimistic_locking_client_conflict(self, client_identified, staff_user):
        """A stale ``expected_updated_at`` must raise ValidationError."""
        stale = "2000-01-01T00:00:00+00:00"
        with pytest.raises(ValidationError):
            update_client(
                client_identified,
                staff_user,
                notes="Parallel-Edit",
                expected_updated_at=stale,
            )
        # DB unchanged
        client_identified.refresh_from_db()
        assert client_identified.notes != "Parallel-Edit"

    def test_optimistic_locking_client_success_with_current_timestamp(self, client_identified, staff_user):
        """Matching ``expected_updated_at`` must succeed."""
        client_identified.refresh_from_db()
        current = client_identified.updated_at.isoformat()
        update_client(
            client_identified,
            staff_user,
            notes="OK-Edit",
            expected_updated_at=current,
        )
        client_identified.refresh_from_db()
        assert client_identified.notes == "OK-Edit"

    def test_optimistic_locking_client_none_disables_check(self, client_identified, staff_user):
        """``expected_updated_at=None`` bypasses the check for backwards compatibility."""
        update_client(
            client_identified,
            staff_user,
            notes="Legacy-Caller",
            expected_updated_at=None,
        )
        client_identified.refresh_from_db()
        assert client_identified.notes == "Legacy-Caller"


@pytest.mark.django_db
class TestClientAnonymize:
    """Regression tests for Client.anonymize() — Refs #529.

    Ensures anonymization covers cascading personal data in cases, episodes
    and all workitems (not only open/in_progress ones)."""

    def test_anonymize_clears_client_fields(self, client_identified):
        client_identified.notes = "Sensible Notiz"
        client_identified.age_cluster = Client.AgeCluster.AGE_18_26
        client_identified.save(update_fields=["notes", "age_cluster"])

        client_identified.anonymize()
        client_identified.refresh_from_db()

        assert client_identified.pseudonym.startswith("Gelöscht-")
        assert client_identified.notes == ""
        assert client_identified.age_cluster == Client.AgeCluster.UNKNOWN
        assert client_identified.is_active is False

    def test_anonymize_clears_case_title(self, facility, client_identified, staff_user):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Sensibler Falltitel mit Klarnamen",
            description="Detailreiche, personenbezogene Beschreibung.",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )

        client_identified.anonymize()
        case.refresh_from_db()

        assert "Klarnamen" not in case.title
        assert case.title.startswith("[Anonymisiert ")
        assert case.description == ""

    def test_anonymize_clears_closed_case_title(self, facility, client_identified, staff_user):
        """Closed cases must also be anonymized."""
        from django.utils import timezone

        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Abgeschlossener Fall mit Klarnamen",
            description="Sensible Historie.",
            status=Case.Status.CLOSED,
            closed_at=timezone.now(),
            created_by=staff_user,
        )

        client_identified.anonymize()
        case.refresh_from_db()

        assert "Klarnamen" not in case.title
        assert case.description == ""

    def test_anonymize_clears_episode_notes(self, facility, client_identified, staff_user):
        from django.utils import timezone

        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Fall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        episode = Episode.objects.create(
            case=case,
            title="Sensible Episode mit Klarnamen",
            description="Freitext mit Personenbezug.",
            started_at=timezone.now().date(),
            created_by=staff_user,
        )

        client_identified.anonymize()
        episode.refresh_from_db()

        assert "Klarnamen" not in episode.title
        assert episode.description == ""

    def test_anonymize_clears_closed_workitems(self, facility, client_identified, staff_user):
        """Workitems with status DONE/DISMISSED must also be anonymized (Refs #529)."""
        done = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.DONE,
            title="Erledigte Aufgabe mit Klarnamen",
            description="Sensible Beschreibung.",
        )
        dismissed = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.DISMISSED,
            title="Verworfene Aufgabe mit Klarnamen",
            description="Weitere sensible Daten.",
        )

        client_identified.anonymize()
        done.refresh_from_db()
        dismissed.refresh_from_db()

        assert done.title == "Aufgabe (anonymisiert)"
        assert done.description == ""
        assert dismissed.title == "Aufgabe (anonymisiert)"
        assert dismissed.description == ""

    def test_anonymize_clears_open_workitems(self, facility, client_identified, staff_user):
        """Regression: open/in_progress workitems continue to be anonymized."""
        open_item = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Offene Aufgabe mit Klarnamen",
            description="Details.",
        )
        in_progress = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.IN_PROGRESS,
            title="Laufende Aufgabe",
            description="Weiteres.",
        )

        client_identified.anonymize()
        open_item.refresh_from_db()
        in_progress.refresh_from_db()

        assert open_item.title == "Aufgabe (anonymisiert)"
        assert open_item.description == ""
        assert in_progress.title == "Aufgabe (anonymisiert)"
        assert in_progress.description == ""
