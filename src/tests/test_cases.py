"""Tests for Case CRUD, event assignment, and navigation."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Event
from core.services.cases import (
    assign_event_to_case,
    close_case,
    create_case,
    remove_event_from_case,
    reopen_case,
    update_case,
)


@pytest.mark.django_db
class TestCaseService:
    def test_create_case(self, facility, staff_user, client_identified):
        case = create_case(
            facility=facility,
            user=staff_user,
            client=client_identified,
            title="Testfall",
            description="Beschreibung",
        )
        assert case.pk is not None
        assert case.status == Case.Status.OPEN
        assert case.created_by == staff_user
        assert case.client == client_identified
        assert case.facility == facility

    def test_create_case_with_lead_user(self, facility, staff_user, lead_user, client_identified):
        case = create_case(
            facility=facility,
            user=staff_user,
            client=client_identified,
            title="Mit Leitung",
            lead_user=lead_user,
        )
        assert case.lead_user == lead_user

    def test_create_case_validates_client_facility(self, facility, staff_user, other_facility):
        from core.models import Client

        other_client = Client.objects.create(
            facility=other_facility,
            pseudonym="Anderer-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        with pytest.raises(ValueError, match="Klientel"):
            create_case(
                facility=facility,
                user=staff_user,
                client=other_client,
                title="Falsch",
            )

    def test_update_case(self, facility, staff_user, case_open):
        updated = update_case(case_open, staff_user, title="Neuer Titel")
        assert updated.title == "Neuer Titel"

    def test_update_case_rejects_unknown_field(self, facility, staff_user, case_open):
        with pytest.raises(ValueError, match="Feld"):
            update_case(case_open, staff_user, status="closed")

    def test_close_case(self, facility, staff_user, case_open):
        closed = close_case(case_open, staff_user)
        assert closed.status == Case.Status.CLOSED
        assert closed.closed_at is not None

    def test_reopen_case(self, facility, staff_user, case_closed):
        reopened = reopen_case(case_closed, staff_user)
        assert reopened.status == Case.Status.OPEN
        assert reopened.closed_at is None

    def test_assign_event_to_case(self, facility, staff_user, case_open, sample_event):
        result = assign_event_to_case(case_open, sample_event, staff_user)
        result.refresh_from_db()
        assert result.case == case_open

    def test_assign_event_validates_facility(self, facility, staff_user, case_open, other_facility):
        from core.models import DocumentType

        other_doc = DocumentType.objects.create(facility=other_facility, name="Kontakt")
        other_event = Event.objects.create(
            facility=other_facility,
            document_type=other_doc,
            occurred_at=timezone.now(),
            data_json={},
        )
        with pytest.raises(ValueError, match="Einrichtung"):
            assign_event_to_case(case_open, other_event, staff_user)

    def test_remove_event_from_case(self, facility, staff_user, case_open, sample_event):
        assign_event_to_case(case_open, sample_event, staff_user)
        remove_event_from_case(sample_event, staff_user)
        sample_event.refresh_from_db()
        assert sample_event.case is None


@pytest.mark.django_db
class TestCaseListView:
    def test_case_list_renders(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"))
        assert response.status_code == 200
        assert "Fälle" in response.content.decode()

    def test_case_list_search(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"), {"q": "Offener"})
        assert response.status_code == 200
        assert "Offener Fall" in response.content.decode()

    def test_case_list_filter_status(self, client, staff_user, case_open, case_closed):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"), {"status": "open"})
        content = response.content.decode()
        assert "Offener Fall" in content
        assert "Geschlossener Fall" not in content

    def test_case_list_htmx_partial(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_list"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        # Should return partial, not full page
        assert "<!DOCTYPE" not in response.content.decode()

    def test_case_list_auth_required(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:case_list"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseCreateView:
    def test_case_create_form_renders(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_create"))
        assert response.status_code == 200
        assert "Neuer Fall" in response.content.decode()

    def test_case_create_post(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_create"),
            {
                "title": "Neuer Testfall",
                "description": "Beschreibung",
                "client": str(client_identified.pk),
            },
        )
        assert response.status_code == 302
        assert Case.objects.filter(title="Neuer Testfall").exists()

    def test_case_create_with_client_preselection(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_create") + f"?client={client_identified.pk}")
        assert response.status_code == 200
        assert client_identified.pseudonym in response.content.decode()

    def test_case_create_auth_required(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:case_create"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseDetailView:
    def test_case_detail_renders(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": case_open.pk}))
        assert response.status_code == 200
        assert "Offener Fall" in response.content.decode()

    def test_case_detail_facility_scoping(self, client, staff_user, other_facility):
        other_case = Case.objects.create(
            facility=other_facility,
            title="Anderer Fall",
            status=Case.Status.OPEN,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": other_case.pk}))
        assert response.status_code == 404


@pytest.mark.django_db
class TestCaseUpdateView:
    def test_case_update_form_renders(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_update", kwargs={"pk": case_open.pk}))
        assert response.status_code == 200

    def test_case_update_post(self, client, staff_user, case_open, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", kwargs={"pk": case_open.pk}),
            {
                "title": "Aktualisierter Titel",
                "description": "Neue Beschreibung",
                "client": str(client_identified.pk),
            },
        )
        assert response.status_code == 302
        case_open.refresh_from_db()
        assert case_open.title == "Aktualisierter Titel"


@pytest.mark.django_db
class TestCaseCloseView:
    def test_close_case_as_lead(self, client, lead_user, case_open):
        client.force_login(lead_user)
        response = client.post(reverse("core:case_close", kwargs={"pk": case_open.pk}))
        assert response.status_code == 302
        case_open.refresh_from_db()
        assert case_open.status == Case.Status.CLOSED

    def test_close_case_forbidden_for_staff(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.post(reverse("core:case_close", kwargs={"pk": case_open.pk}))
        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseReopenView:
    def test_reopen_case_as_lead(self, client, lead_user, case_closed):
        client.force_login(lead_user)
        response = client.post(reverse("core:case_reopen", kwargs={"pk": case_closed.pk}))
        assert response.status_code == 302
        case_closed.refresh_from_db()
        assert case_closed.status == Case.Status.OPEN

    def test_reopen_case_forbidden_for_staff(self, client, staff_user, case_closed):
        client.force_login(staff_user)
        response = client.post(reverse("core:case_reopen", kwargs={"pk": case_closed.pk}))
        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseAssignEventView:
    def test_assign_event_to_case(self, client, staff_user, case_open, sample_event):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_assign_event", kwargs={"pk": case_open.pk}),
            {"event_id": str(sample_event.pk)},
        )
        assert response.status_code == 200
        sample_event.refresh_from_db()
        assert sample_event.case == case_open

    def test_assign_event_validates_facility(self, client, staff_user, case_open, other_facility):
        from core.models import DocumentType

        other_doc = DocumentType.objects.create(facility=other_facility, name="Kontakt")
        other_event = Event.objects.create(
            facility=other_facility,
            document_type=other_doc,
            occurred_at=timezone.now(),
            data_json={},
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_assign_event", kwargs={"pk": case_open.pk}),
            {"event_id": str(other_event.pk)},
        )
        # Event from another facility should 404
        assert response.status_code == 404


@pytest.mark.django_db
class TestCaseRemoveEventView:
    def test_remove_event_from_case(self, client, staff_user, case_open, sample_event):
        sample_event.case = case_open
        sample_event.save()
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:case_remove_event",
                kwargs={"pk": case_open.pk, "event_pk": sample_event.pk},
            ),
        )
        assert response.status_code == 200
        sample_event.refresh_from_db()
        assert sample_event.case is None


@pytest.mark.django_db
class TestCasesForClientView:
    def test_cases_for_client_returns_json(self, client, staff_user, case_open, client_identified):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:cases_for_client"),
            {"client": str(client_identified.pk)},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Offener Fall"

    def test_cases_for_client_empty(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:cases_for_client"))
        assert response.status_code == 200
        assert response.json() == []
