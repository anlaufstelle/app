"""Bearbeiten-Formular einer Aufgabe bei parallelem Statuswechsel (Refs #2331).

Ablauf wie im Handtest: Tab A rendert das Bearbeiten-Formular, Tab B setzt
die Aufgabe auf „Erledigt", Tab A ändert danach NUR die Priorität und
speichert. Der Status gehört nicht zu den Feldern des Formulars — der
Statuswechsel darf die Eingabe aus Tab A deshalb nicht als Konflikt
verwerfen. Ein paralleler Wechsel eines Formularfelds (z. B. Titel) bleibt
dagegen ein echter Konflikt und darf nichts still überschreiben.

Die Tests posten die versteckten Felder genau so, wie das gerenderte
Formular sie trägt (HTML-Parser statt fest verdrahteter Feldnamen) — sie
prüfen damit den Weg der Oberfläche, nicht einen einzelnen Mechanismus.
"""

from html.parser import HTMLParser

import pytest
from django.urls import reverse

from core.models import WorkItem
from core.services.case import update_workitem


class _HiddenInputs(HTMLParser):
    """Sammelt ``<input type="hidden">`` des Bearbeiten-Formulars (ohne CSRF)."""

    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attr = dict(attrs)
        name = attr.get("name")
        if attr.get("type") == "hidden" and name and name != "csrfmiddlewaretoken":
            self.values[name] = attr.get("value") or ""


def _render_edit_form(client, workitem):
    """Tab A: Formular laden und die versteckten Felder übernehmen."""
    response = client.get(reverse("core:workitem_update", kwargs={"pk": workitem.pk}))
    assert response.status_code == 200
    parser = _HiddenInputs()
    parser.feed(response.content.decode())
    assert "expected_updated_at" in parser.values, "Formular muss den Versions-Token tragen"
    return parser.values


def _form_payload(workitem, hidden, **overrides):
    """Sichtbare Formularfelder mit dem Stand zum Renderzeitpunkt + Änderungen."""
    payload = dict(hidden)
    payload.update(
        {
            "item_type": workitem.item_type,
            "title": workitem.title,
            "description": workitem.description or "",
            "priority": workitem.priority,
            "due_date": "",
            "remind_at": "",
            "recurrence": workitem.recurrence,
            "assigned_to": str(workitem.assigned_to_id or ""),
        }
    )
    payload.update(overrides)
    return payload


@pytest.fixture
def urgent_workitem(facility, client_identified, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.URGENT,
        title="Parallel bearbeitet",
        description="Langer Beschreibungstext, der nicht verloren gehen darf.",
    )


def _mark_done_in_other_tab(client, workitem):
    """Tab B: „Als erledigt markieren" über denselben Pfad wie die Oberfläche."""
    response = client.post(
        reverse("core:workitem_status_update", kwargs={"pk": workitem.pk}),
        {"status": WorkItem.Status.DONE},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    workitem.refresh_from_db()
    assert workitem.status == WorkItem.Status.DONE


@pytest.mark.django_db
class TestParallelStatusChangeKeepsEdit:
    def test_priority_edit_survives_parallel_status_change(self, client, staff_user, urgent_workitem):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, urgent_workitem)
        snapshot = WorkItem.objects.get(pk=urgent_workitem.pk)

        _mark_done_in_other_tab(client, urgent_workitem)

        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk}),
            _form_payload(snapshot, hidden, priority=WorkItem.Priority.NORMAL),
        )

        assert response.status_code == 302
        assert response.url == reverse("core:workitem_inbox"), "Speichern muss wie im Normalfall weiterleiten"
        urgent_workitem.refresh_from_db()
        assert urgent_workitem.status == WorkItem.Status.DONE, "Parallel gesetzter Status muss erhalten bleiben"
        assert urgent_workitem.completed_at is not None
        assert urgent_workitem.priority == WorkItem.Priority.NORMAL, "Eingabe aus Tab A darf nicht verloren gehen"
        assert urgent_workitem.description == snapshot.description

    def test_description_edit_survives_parallel_status_change(self, client, staff_user, urgent_workitem):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, urgent_workitem)
        snapshot = WorkItem.objects.get(pk=urgent_workitem.pk)

        _mark_done_in_other_tab(client, urgent_workitem)

        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk}),
            _form_payload(snapshot, hidden, description="Neuer, langer Text aus Tab A."),
        )

        assert response.status_code == 302
        urgent_workitem.refresh_from_db()
        assert urgent_workitem.status == WorkItem.Status.DONE
        assert urgent_workitem.description == "Neuer, langer Text aus Tab A."


@pytest.mark.django_db
class TestParallelFormFieldChangeStaysConflict:
    def test_parallel_title_change_is_reported_as_conflict(self, client, staff_user, urgent_workitem):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, urgent_workitem)
        snapshot = WorkItem.objects.get(pk=urgent_workitem.pk)

        # Tab B ändert ein Feld, das auch im Formular steht.
        update_workitem(WorkItem.objects.get(pk=urgent_workitem.pk), staff_user, title="Titel aus Tab B")

        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk}),
            _form_payload(snapshot, hidden, priority=WorkItem.Priority.NORMAL),
            follow=True,
        )

        assert response.redirect_chain[0][1] == 302
        assert response.redirect_chain[0][0] == reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk})
        assert "zwischenzeitlich bearbeitet" in response.content.decode()
        urgent_workitem.refresh_from_db()
        assert urgent_workitem.title == "Titel aus Tab B", "Paralleler Titel darf nicht still überschrieben werden"
        assert urgent_workitem.priority == WorkItem.Priority.URGENT, "Konflikt darf nichts teilweise schreiben"

    def test_parallel_title_change_same_field_is_conflict(self, client, staff_user, urgent_workitem):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, urgent_workitem)
        snapshot = WorkItem.objects.get(pk=urgent_workitem.pk)

        update_workitem(WorkItem.objects.get(pk=urgent_workitem.pk), staff_user, title="Titel aus Tab B")

        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk}),
            _form_payload(snapshot, hidden, title="Titel aus Tab A"),
        )

        assert response.status_code == 302
        assert response.url == reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk})
        urgent_workitem.refresh_from_db()
        assert urgent_workitem.title == "Titel aus Tab B"

    def test_status_and_title_changed_in_parallel_is_conflict(self, client, staff_user, urgent_workitem):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, urgent_workitem)
        snapshot = WorkItem.objects.get(pk=urgent_workitem.pk)

        _mark_done_in_other_tab(client, urgent_workitem)
        update_workitem(WorkItem.objects.get(pk=urgent_workitem.pk), staff_user, title="Titel aus Tab B")

        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk}),
            _form_payload(snapshot, hidden, priority=WorkItem.Priority.NORMAL),
        )

        assert response.status_code == 302
        assert response.url == reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk})
        urgent_workitem.refresh_from_db()
        assert urgent_workitem.priority == WorkItem.Priority.URGENT


@pytest.mark.django_db
class TestJsonEditContractUnchanged:
    """Der JSON-/HTMX-Weg (Offline-Replay) behält seinen Versionsvertrag."""

    def test_json_edit_after_parallel_status_change_stays_409(self, client, staff_user, urgent_workitem):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, urgent_workitem)
        snapshot = WorkItem.objects.get(pk=urgent_workitem.pk)

        _mark_done_in_other_tab(client, urgent_workitem)

        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk}),
            _form_payload(snapshot, hidden, priority=WorkItem.Priority.NORMAL),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "conflict"
        assert body["client_expected"] == hidden["expected_updated_at"]
        assert body["server_state"]["status"] == WorkItem.Status.DONE
        urgent_workitem.refresh_from_db()
        assert urgent_workitem.priority == WorkItem.Priority.URGENT

    def test_htmx_edit_after_parallel_status_change_stays_409(self, client, staff_user, urgent_workitem):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, urgent_workitem)
        snapshot = WorkItem.objects.get(pk=urgent_workitem.pk)

        _mark_done_in_other_tab(client, urgent_workitem)

        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk}),
            _form_payload(snapshot, hidden, priority=WorkItem.Priority.NORMAL),
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 409
        assert response.json()["error"] == "conflict"

    def test_json_edit_without_token_keeps_missing_token_contract(self, client, staff_user, urgent_workitem):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, urgent_workitem)
        snapshot = WorkItem.objects.get(pk=urgent_workitem.pk)
        hidden.pop("expected_updated_at")

        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": urgent_workitem.pk}),
            _form_payload(snapshot, hidden, priority=WorkItem.Priority.NORMAL),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 409
        assert response.json()["error"] == "missing-token"
        urgent_workitem.refresh_from_db()
        assert urgent_workitem.priority == WorkItem.Priority.URGENT
