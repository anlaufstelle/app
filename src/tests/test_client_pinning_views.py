"""Refs #1786: Ursprungs-Pinning der Personenzuordnung auf der UI-Fläche.

Die Service-Grenze (``update_case``/``update_workitem``) liegt in
``test_cases.py`` bzw. ``test_workitems_service_helpers.py``. Hier steht die
Fläche darüber: manipulierte POSTs dürfen die Zuordnung nicht umbiegen, und
das Bearbeiten-Formular darf sie gar nicht erst als änderbares Feld anbieten.
"""

import pytest
from django.urls import reverse

from core.models import WorkItem


@pytest.fixture
def workitem_pinned(facility, client_identified, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title="Aufgabe mit Person",
    )


@pytest.fixture
def workitem_unassigned(facility, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=None,
        created_by=staff_user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title="Personenlose Aufgabe",
    )


@pytest.mark.django_db
class TestCaseClientPinning:
    def test_case_update_post_cannot_reassign_client(self, client, staff_user, case_open, client_qualified):
        """Manipulierter POST mit anderer lebender Person derselben Einrichtung ändert nichts."""
        client.force_login(staff_user)
        original = case_open.client_id
        response = client.post(
            reverse("core:case_update", kwargs={"pk": case_open.pk}),
            {
                "title": "Umgehängter Titel",
                "description": "Beschreibung",
                "client": str(client_qualified.pk),
            },
        )
        # Formfehler statt Redirect-Erfolg — der Versuch bleibt sichtbar.
        assert response.status_code == 200
        # Exakt die Fehlermeldung, nicht der Read-only-Hinweis daneben.
        assert "Die Personenzuordnung kann nach dem Anlegen nicht geändert werden." in response.content.decode()
        case_open.refresh_from_db()
        assert case_open.client_id == original
        assert case_open.title != "Umgehängter Titel"

    def test_case_update_post_without_client_still_saves(self, client, staff_user, case_open):
        """Das Read-only-Formular sendet kein ``client`` mehr — der Edit muss trotzdem durchgehen."""
        client.force_login(staff_user)
        original = case_open.client_id
        response = client.post(
            reverse("core:case_update", kwargs={"pk": case_open.pk}),
            {"title": "Neuer Titel", "description": "Beschreibung"},
        )
        assert response.status_code == 302
        case_open.refresh_from_db()
        assert case_open.title == "Neuer Titel"
        assert case_open.client_id == original

    def test_case_update_form_renders_readonly_client(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_update", kwargs={"pk": case_open.pk}))
        content = response.content.decode()
        assert response.status_code == 200
        assert 'name="client"' not in content
        assert case_open.client.pseudonym in content

    def test_case_create_form_still_offers_client_field(self, client, staff_user):
        """Das Pinning gilt erst ab dem Anlegen — die Neuanlage braucht die Auswahl."""
        client.force_login(staff_user)
        response = client.get(reverse("core:case_create"))
        assert 'name="client"' in response.content.decode()


@pytest.mark.django_db
class TestWorkItemClientPinning:
    def test_workitem_update_post_cannot_reassign_client(self, client, staff_user, workitem_pinned, client_qualified):
        client.force_login(staff_user)
        original = workitem_pinned.client_id
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": workitem_pinned.pk}),
            {
                "item_type": WorkItem.ItemType.TASK,
                "title": "Umgehängte Aufgabe",
                "priority": WorkItem.Priority.NORMAL,
                "client": str(client_qualified.pk),
            },
        )
        assert response.status_code == 200
        # Exakt die Fehlermeldung, nicht der Read-only-Hinweis daneben.
        assert "Die Personenzuordnung kann nach dem Anlegen nicht geändert werden." in response.content.decode()
        workitem_pinned.refresh_from_db()
        assert workitem_pinned.client_id == original
        assert workitem_pinned.title != "Umgehängte Aufgabe"

    def test_workitem_update_post_cannot_clear_client(self, client, staff_user, workitem_pinned):
        """Leeres ``client``-Feld darf die bestehende Zuordnung nicht entfernen."""
        client.force_login(staff_user)
        original = workitem_pinned.client_id
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": workitem_pinned.pk}),
            {
                "item_type": WorkItem.ItemType.TASK,
                "title": "Ohne Person",
                "priority": WorkItem.Priority.NORMAL,
                "client": "",
            },
        )
        assert response.status_code == 302
        workitem_pinned.refresh_from_db()
        assert workitem_pinned.client_id == original
        assert workitem_pinned.title == "Ohne Person"

    def test_workitem_update_post_can_attach_client(self, client, staff_user, workitem_unassigned, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": workitem_unassigned.pk}),
            {
                "item_type": WorkItem.ItemType.TASK,
                "title": "Jetzt mit Person",
                "priority": WorkItem.Priority.NORMAL,
                "client": str(client_identified.pk),
            },
        )
        assert response.status_code == 302
        workitem_unassigned.refresh_from_db()
        assert workitem_unassigned.client_id == client_identified.pk

    def test_workitem_update_form_renders_readonly_client(self, client, staff_user, workitem_pinned):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_update", kwargs={"pk": workitem_pinned.pk}))
        content = response.content.decode()
        assert response.status_code == 200
        assert 'name="client"' not in content
        assert workitem_pinned.client.pseudonym in content

    def test_workitem_update_form_renders_autocomplete_when_unassigned(self, client, staff_user, workitem_unassigned):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_update", kwargs={"pk": workitem_unassigned.pk}))
        assert response.status_code == 200
        assert 'name="client"' in response.content.decode()
