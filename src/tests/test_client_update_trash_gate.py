"""Deep-Link-Bearbeitung am Papierkorb vorbei (Refs #1737, Nebenbefund E).

``ClientUpdateView`` war die einzige Client-View ohne ``is_deleted=False``-
Scope: Detail antwortete 404, ``/clients/<pk>/edit/`` lieferte trotzdem 200
und ein POST schrieb den soft-gelöschten Datensatz fort. Das war zugleich der
einzige (unauffindbare) Ausweg aus der Blockade in #1737 — er wird durch die
Papierkorb-Aktion „Jetzt endgültig anonymisieren" ersetzt.
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def trashed_client(client_identified, admin_user):
    client_identified.soft_delete(user=admin_user)
    return client_identified


class TestClientUpdateViewRespectsTrash:
    def test_get_is_404_for_trashed_client(self, client, staff_user, trashed_client):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_update", args=[trashed_client.pk]))
        assert response.status_code == 404

    def test_post_is_404_for_trashed_client(self, client, staff_user, trashed_client):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[trashed_client.pk]),
            {"pseudonym": "Umbenannt", "contact_stage": "identified", "age_cluster": "unknown", "notes": ""},
        )
        assert response.status_code == 404
        trashed_client.refresh_from_db()
        assert trashed_client.pseudonym != "Umbenannt"

    def test_admin_is_also_blocked(self, client, admin_user, trashed_client):
        client.force_login(admin_user)
        assert client.get(reverse("core:client_update", args=[trashed_client.pk])).status_code == 404

    def test_restored_client_is_editable_again(self, client, staff_user, trashed_client):
        trashed_client.restore()
        client.force_login(staff_user)
        assert client.get(reverse("core:client_update", args=[trashed_client.pk])).status_code == 200
