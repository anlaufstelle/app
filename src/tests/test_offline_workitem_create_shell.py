"""Tests fuer OfflineWorkItemCreateShellView (core/views/offline.py).

Refs #1522 (#1499 SI-5): pk-lose, PUBLIC WorkItem-Create-Shell. Der
Service Worker precacht sie und serviert sie offline IN-PLACE an der kanonischen
URL /workitems/new/ (SI-6). Der Shell selbst traegt kein PII — das
assignable_users-Roster (Staff+-Marker) und die mitgenommenen Personen liest die
Alpine-Komponente ``offlineWorkItemCreate`` client-seitig aus IndexedDB
(getOfflineFacility / listOfflineClientsDetailed).

Analog zu :class:`TestOfflineEventCreateShellView`: public, GET-only,
Template-Smoke. Das Verhalten (Staff+-Gate, Person-Picker, Standalone, Replay)
deckt die E2E-Journey in SI-7 ab. Der Staff+-Gate (Assistenz ohne
assignable_users sieht die Form nicht -> kein 403-"revoked"-Replay, weil
WorkItemCreateView = StaffRequiredMixin) ist Alpine-seitig verdrahtet und wird
hier als Scaffold-Hook (offline-workitem-create-gate) belegt.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestOfflineWorkItemCreateShellView:
    def test_renders_pkless_workitem_create_scaffold(self, client):
        response = client.get(reverse("core:offline_workitem_shell"))
        assert response.status_code == 200
        body = response.content.decode()
        # Alpine-Wurzel + Erfassungs-Formular-Hook.
        assert 'x-data="offlineWorkItemCreate"' in body
        assert 'data-testid="offline-workitem-create-form"' in body
        # Person-Picker ("mitgenommene Person" | "— ohne Person —", Standalone).
        assert 'data-testid="offline-workitem-create-client"' in body
        # Der geteilte WorkItem-Feld-Loop kommt aus dem bestehenden Partial
        # _offline_workitem_fields.html (Titel-Feld als Marker).
        assert 'data-testid="offline-wi-input-title"' in body
        # Staff+-Gate: Assistenz (leeres assignable_users) sieht die Form nicht,
        # sondern einen Hinweis (kein 403-Sackgassen-Replay).
        assert 'data-testid="offline-workitem-create-gate"' in body
        # Der Renderer wird geladen.
        assert "offline-create.js" in body

    def test_public_access(self, client):
        """Shell ist PII-frei und muss offline aus dem Cache servierbar sein —
        kein Server-Auth-Gate (das Roster liegt verschluesselt in IDB, ein
        Auth-Gate wuerde den install-time cache.addAll auf /login/ umleiten)."""
        response = client.get(reverse("core:offline_workitem_shell"))
        assert response.status_code == 200

    def test_is_get_only(self, client):
        """http_method_names = ["get"] — ein POST wird mit 405 abgewiesen."""
        response = client.post(reverse("core:offline_workitem_shell"))
        assert response.status_code == 405
