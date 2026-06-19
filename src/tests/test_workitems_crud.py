"""Tests fuer WorkItem-CRUD-Views und DeletionRequest-Listing.

Enthaelt die Cluster (Split aus ``test_workitems.py``):

* ``TestWorkItemCRUD`` — Create/Update/Detail-Views, ISO-Date-Prefill.
* ``TestDeletionRequestList`` — Loeschantrags-Liste fuer Lead/Admin.

Fixture ``workitem_open`` ist inline gehalten (kopiert aus dem Original-File),
weil pytest File-spezifische Fixtures nicht ueber Imports entdeckt.
"""

import pytest
from django.urls import reverse

from core.models import WorkItem


@pytest.fixture
def workitem_open(facility, client_identified, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        title="Offene Aufgabe",
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.NORMAL,
    )


@pytest.fixture
def workitem_in_progress(facility, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        assigned_to=staff_user,
        title="In Bearbeitung",
        status=WorkItem.Status.IN_PROGRESS,
        priority=WorkItem.Priority.NORMAL,
    )


@pytest.mark.django_db
class TestWorkItemCRUD:
    def test_create_get(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_create"))
        assert response.status_code == 200

    def test_create_post(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Neue Test-Aufgabe",
                "priority": "normal",
            },
        )
        assert response.status_code == 302
        assert WorkItem.objects.filter(title="Neue Test-Aufgabe", facility=facility).exists()

    def test_create_with_client(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Aufgabe mit Klientel",
                "priority": "normal",
                "client": str(client_identified.pk),
            },
        )
        assert response.status_code == 302
        wi = WorkItem.objects.get(title="Aufgabe mit Klientel")
        assert wi.client == client_identified

    def test_update_get(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_update", kwargs={"pk": workitem_open.pk}))
        assert response.status_code == 200

    def test_update_get_prefills_due_date_iso_format(self, client, staff_user, facility):
        """Bearbeiten-Formular muss `due_date` im ISO-Format rendern, sonst
        lässt das HTML5-Date-Input den Wert fallen (Refs #619)."""
        from datetime import date

        wi = WorkItem.objects.create(
            facility=facility,
            title="Mit Datum",
            due_date=date(2026, 5, 7),
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_update", kwargs={"pk": wi.pk}))
        assert response.status_code == 200
        assert 'value="2026-05-07"' in response.content.decode(), (
            "due_date muss als YYYY-MM-DD im value-Attribut stehen; sonst "
            "akzeptiert der Browser ihn nicht und das Feld erscheint leer."
        )

    def test_update_post(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": workitem_open.pk}),
            {
                "item_type": "task",
                "title": "Aktualisierter Titel",
                "priority": "urgent",
            },
        )
        assert response.status_code == 302
        workitem_open.refresh_from_db()
        assert workitem_open.title == "Aktualisierter Titel"
        assert workitem_open.priority == WorkItem.Priority.URGENT

    def test_detail_view(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": workitem_open.pk}))
        assert response.status_code == 200
        assert "Offene Aufgabe" in response.content.decode()


@pytest.mark.django_db
class TestWorkItemDetailActions:
    """Detailansicht-Aktionen für offene Aufgaben (Refs #1130).

    Verständlichere Benennung und ein direkter Erledigen-Pfad ohne den
    Zwischenstatus *In Bearbeitung*. Die Status-Transition-Logik selbst
    bleibt unverändert; getestet wird die in der Detailansicht angebotene
    Auswahl an Aktionen.
    """

    def test_open_task_offers_take_over_label(self, client, staff_user, workitem_open):
        """`Annehmen` wird in der Detailansicht zu `Aufgabe übernehmen`."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": workitem_open.pk}))
        body = response.content.decode()
        assert "Aufgabe übernehmen" in body

    def test_open_task_offers_direct_done_action(self, client, staff_user, workitem_open):
        """Offene Aufgaben bieten `Als erledigt markieren` ohne Umweg über
        `In Bearbeitung` an (eigenes Form mit status=done)."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": workitem_open.pk}))
        body = response.content.decode()
        assert "Als erledigt markieren" in body
        # Es existiert genau ein Form, das den Status direkt auf done setzt.
        assert body.count('value="done"') == 1

    def test_open_task_dismiss_relabelled_with_confirmation(self, client, staff_user, workitem_open):
        """`Verwerfen` wird zu `Als nicht relevant schließen` und erhält eine
        Bestätigung, die erklärt, dass nicht gelöscht und aus den offenen
        Aufgaben entfernt wird."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": workitem_open.pk}))
        body = response.content.decode()
        assert "Als nicht relevant schließen" in body
        assert "Verwerfen" not in body
        assert "data-confirm" in body
        # Die Bestätigung erklärt die fachlichen Garantien.
        assert "nicht gelöscht" in body
        assert "offenen Aufgaben" in body

    def test_direct_done_from_open_sets_completed(self, client, staff_user, workitem_open):
        """Der direkte Erledigen-Pfad (open → done) setzt Status und
        Abschlusszeitpunkt, ohne vorher in_progress zu durchlaufen."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "done"},
        )
        assert response.status_code == 302
        workitem_open.refresh_from_db()
        assert workitem_open.status == WorkItem.Status.DONE
        assert workitem_open.completed_at is not None

    def test_open_task_done_action_has_confirmation(self, client, staff_user, workitem_open):
        """Refs #1147: Der direkte Erledigen-Pfad (open → done) erhält in der
        Detailansicht eine Bestätigung, die erklärt, dass die Aufgabe als
        erledigt gespeichert wird und danach nicht mehr bei offenen Aufgaben
        erscheint. Wie bei #1130 läuft die Bestätigung CSP-konform über
        ``data-confirm`` am abschickenden Form (confirm-action.js)."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": workitem_open.pk}))
        body = response.content.decode()
        # Das Form, das den Status direkt auf done setzt, trägt eine Bestätigung.
        marker = 'data-confirm="Aufgabe als erledigt markieren?'
        assert marker in body
        confirm_text = body.split(marker, 1)[1].split('"', 1)[0]
        assert "als erledigt gespeichert" in confirm_text
        assert "offenen Aufgaben" in confirm_text

    def test_in_progress_task_done_action_has_confirmation(self, client, staff_user, workitem_in_progress):
        """Refs #1147: Auch der Erledigen-Pfad aus *In Bearbeitung* (in_progress
        → done) ist in der Detailansicht durch dieselbe Bestätigung abgesichert
        — konsistent mit dem direkten Pfad aus *Offen*."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": workitem_in_progress.pk}))
        body = response.content.decode()
        marker = 'data-confirm="Aufgabe als erledigt markieren?'
        assert marker in body
        confirm_text = body.split(marker, 1)[1].split('"', 1)[0]
        assert "als erledigt gespeichert" in confirm_text
        assert "offenen Aufgaben" in confirm_text


@pytest.mark.django_db
class TestDeletionRequestList:
    def test_lead_can_access(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 200
        assert "Löschanträge" in response.content.decode()

    def test_staff_cannot_access(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 403

    def test_admin_can_access(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 200

    def test_lists_pending_requests(self, client, lead_user, facility, staff_user, sample_event):
        from core.models import DeletionRequest

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Testgrund",
            requested_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert dr in response.context["pending_requests"]
