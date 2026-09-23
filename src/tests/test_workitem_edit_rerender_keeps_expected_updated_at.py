"""Bearbeiten-Formular einer Aufgabe: Neu-Anzeige nach ungültiger Eingabe (Refs #2361).

Ablauf: Tab A lädt das Bearbeiten-Formular (Versions-Token A). Tab B ändert
danach ein Formularfeld (Titel). Tab A schickt eine ungültige Eingabe ab
(leerer Pflichttitel) — das Formular wird mit Fehlern erneut angezeigt.
Speichert Tab A danach gültig, muss die Änderung aus Tab B weiterhin als
Konflikt erkannt werden: die Neu-Anzeige darf den Versions-Token nicht
stillschweigend auf den Datenbankstand zum Zeitpunkt des fehlgeschlagenen
Absendens vorrücken, sonst überschreibt der zweite Versuch Tab B still.

Die Tests lesen die versteckten Felder aus dem gerenderten HTML (wie der
Browser), statt Feldnamen oder Werte fest zu verdrahten.
"""

from html.parser import HTMLParser

import pytest
from django.urls import reverse

from core.models import WorkItem
from core.services.case import update_workitem


class _HiddenInputs(HTMLParser):
    """Sammelt ``<input type="hidden">`` des Formulars (ohne CSRF)."""

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


def _hidden_values(response):
    parser = _HiddenInputs()
    parser.feed(response.content.decode())
    return parser.values


def _edit_url(workitem):
    return reverse("core:workitem_update", kwargs={"pk": workitem.pk})


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
def workitem(facility, client_identified, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.URGENT,
        title="Ursprünglicher Titel",
        description="Beschreibung aus dem ersten Laden.",
    )


def _load_form(client, workitem):
    response = client.get(_edit_url(workitem))
    assert response.status_code == 200
    hidden = _hidden_values(response)
    assert hidden.get("expected_updated_at"), "Formular muss den Versions-Token tragen"
    return hidden


def _change_title_in_other_tab(user, workitem):
    update_workitem(WorkItem.objects.get(pk=workitem.pk), user, title="Titel aus Tab B")
    fresh = WorkItem.objects.get(pk=workitem.pk)
    assert fresh.title == "Titel aus Tab B"
    return fresh


@pytest.mark.django_db
class TestInvalidRerenderKeepsVersionToken:
    def test_rerender_carries_the_posted_version_token(self, client, staff_user, workitem):
        client.force_login(staff_user)
        hidden = _load_form(client, workitem)
        snapshot = WorkItem.objects.get(pk=workitem.pk)
        fresh = _change_title_in_other_tab(staff_user, workitem)
        assert fresh.updated_at.isoformat() != hidden["expected_updated_at"]

        response = client.post(_edit_url(workitem), _form_payload(snapshot, hidden, title=""))

        assert response.status_code == 200, "Ungültige Eingabe wird inline erneut angezeigt"
        rerendered = _hidden_values(response)
        assert rerendered.get("expected_updated_at") == hidden["expected_updated_at"], (
            "Neu-Anzeige muss den Versions-Token aus dem ersten Laden behalten, nicht den aktuellen DB-Stand"
        )

    def test_second_submit_after_rerender_is_still_a_conflict(self, client, staff_user, workitem):
        client.force_login(staff_user)
        hidden = _load_form(client, workitem)
        snapshot = WorkItem.objects.get(pk=workitem.pk)
        _change_title_in_other_tab(staff_user, workitem)

        invalid = client.post(_edit_url(workitem), _form_payload(snapshot, hidden, title=""))
        assert invalid.status_code == 200
        rerendered = _hidden_values(invalid)

        response = client.post(
            _edit_url(workitem),
            _form_payload(snapshot, rerendered, title="Titel aus Tab A", priority=WorkItem.Priority.NORMAL),
        )

        assert response.status_code == 302
        assert response.url == _edit_url(workitem), "Konflikt führt zurück ins Formular, nicht in die Inbox"
        workitem.refresh_from_db()
        assert workitem.title == "Titel aus Tab B", "Parallele Änderung darf nicht still überschrieben werden"
        assert workitem.priority == WorkItem.Priority.URGENT, "Konflikt darf nichts teilweise schreiben"

    def test_rerender_keeps_form_state_token_alongside(self, client, staff_user, workitem):
        """Beide versteckten Werte reisen gemeinsam durch die Neu-Anzeige."""
        client.force_login(staff_user)
        hidden = _load_form(client, workitem)
        snapshot = WorkItem.objects.get(pk=workitem.pk)
        _change_title_in_other_tab(staff_user, workitem)

        response = client.post(_edit_url(workitem), _form_payload(snapshot, hidden, title=""))

        rerendered = _hidden_values(response)
        assert rerendered.get("expected_form_state")
        assert rerendered.get("expected_form_state") == hidden.get("expected_form_state")
        assert rerendered.get("expected_updated_at") == hidden["expected_updated_at"]

    def test_valid_submit_after_rerender_without_parallel_change_saves(self, client, staff_user, workitem):
        """Ohne parallele Änderung bleibt der zweite Versuch ein normales Speichern."""
        client.force_login(staff_user)
        hidden = _load_form(client, workitem)
        snapshot = WorkItem.objects.get(pk=workitem.pk)

        invalid = client.post(_edit_url(workitem), _form_payload(snapshot, hidden, title=""))
        assert invalid.status_code == 200
        rerendered = _hidden_values(invalid)

        response = client.post(_edit_url(workitem), _form_payload(snapshot, rerendered, title="Neuer Titel"))

        assert response.status_code == 302
        assert response.url == reverse("core:workitem_inbox")
        workitem.refresh_from_db()
        assert workitem.title == "Neuer Titel"

    def test_get_still_renders_current_version_token(self, client, staff_user, workitem):
        """Ohne vorangegangenes Absenden trägt das Formular den aktuellen Stand."""
        client.force_login(staff_user)
        fresh = _change_title_in_other_tab(staff_user, workitem)

        hidden = _load_form(client, workitem)

        assert hidden["expected_updated_at"] == fresh.updated_at.isoformat()

    def test_raw_json_invalid_submit_stays_422(self, client, staff_user, workitem):
        """Der Offline-Replay-Vertrag für ungültige Eingaben bleibt unverändert."""
        client.force_login(staff_user)
        hidden = _load_form(client, workitem)
        snapshot = WorkItem.objects.get(pk=workitem.pk)

        response = client.post(
            _edit_url(workitem),
            _form_payload(snapshot, hidden, title=""),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 422


@pytest.mark.django_db
class TestInvalidRerenderWithoutVersionToken:
    """Randfall: der ungültige Versuch trug keinen Versions-Token.

    Die Neu-Anzeige darf dann keinen frischen Token erzeugen — leer bleibt
    leer. Sonst verschafft ein ungültiger Zwischenversuch einem Absenden
    ohne Token nachträglich den aktuellen Stand und hebelt die
    Konfliktprüfung aus.
    """

    def _invalid_then_valid(self, client, staff_user, workitem, **headers):
        client.force_login(staff_user)
        hidden = _load_form(client, workitem)
        snapshot = WorkItem.objects.get(pk=workitem.pk)
        _change_title_in_other_tab(staff_user, workitem)
        hidden["expected_updated_at"] = ""

        invalid = client.post(_edit_url(workitem), _form_payload(snapshot, hidden, title=""), **headers)
        assert invalid.status_code == 200
        rerendered = _hidden_values(invalid)
        assert rerendered.get("expected_updated_at") == "", "Neu-Anzeige darf keinen frischen Token erzeugen"

        return client.post(_edit_url(workitem), _form_payload(snapshot, rerendered, title="Titel aus Tab A"), **headers)

    def test_htmx_submit_without_token_stays_rejected(self, client, staff_user, workitem):
        response = self._invalid_then_valid(client, staff_user, workitem, HTTP_HX_REQUEST="true")

        assert response.status_code == 409
        assert response.json()["error"] == "missing-token"
        workitem.refresh_from_db()
        assert workitem.title == "Titel aus Tab B", "Parallele Änderung darf nicht überschrieben werden"

    def test_html_submit_without_token_keeps_unchecked_save(self, client, staff_user, workitem):
        """Klassisches HTML ohne Token speichert ohne Versionsprüfung.

        Das ist das bestehende, beabsichtigte Verhalten des Formularpfads
        (kein Pflicht-Token außerhalb von JSON/HTMX) und wird hier nur als
        Randfall festgehalten: die Neu-Anzeige ändert daran nichts — sie
        erfindet insbesondere keinen Token, den der Browser nie hatte.
        """
        response = self._invalid_then_valid(client, staff_user, workitem)

        assert response.status_code == 302
        assert response.url == reverse("core:workitem_inbox")
        workitem.refresh_from_db()
        assert workitem.title == "Titel aus Tab A"
