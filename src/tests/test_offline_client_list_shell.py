"""Tests fuer OfflineClientListShellView (core/views/offline.py).

Refs #1531 (#1499 SI-3): pk-lose, PUBLIC Personenlisten-Shell. Der
Service Worker precacht sie und serviert sie offline IN-PLACE an der
kanonischen URL /clients/ (SI-5). Der Shell selbst traegt kein PII — die
mitgenommenen Personen liest die Alpine-Komponente client-seitig aus
IndexedDB (SI-6).

Analog zu TestOfflineClientShellView/TestOfflineEventCreateShellView in
test_pwa_views.py bzw. test_offline_event_create_shell.py: public,
GET-only, Template-Smoke.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestOfflineClientListShellView:
    def test_renders_pkless_client_list_scaffold(self, client):
        response = client.get(reverse("core:offline_client_list_shell"))
        assert response.status_code == 200

    def test_public_access(self, client):
        """Shell ist PII-frei und muss offline aus dem Cache servierbar sein —
        kein Server-Auth-Gate (die Personenliste liegt verschluesselt in IDB,
        ein Auth-Gate wuerde den install-time cache.addAll auf /login/
        umleiten und ihn atomar scheitern lassen)."""
        response = client.get(reverse("core:offline_client_list_shell"))
        assert response.status_code == 200

    def test_is_get_only(self, client):
        """http_method_names = ["get"] — ein POST wird mit 405 abgewiesen."""
        response = client.post(reverse("core:offline_client_list_shell"))
        assert response.status_code == 405

    def test_does_not_collide_with_client_detail_route(self, client):
        """offline/clients/ (Liste) vs. offline/clients/<uuid>/ (Detail,
        offline_client_detail) duerfen sich nicht ueberschneiden."""
        assert reverse("core:offline_client_list_shell") == "/offline/clients/"
