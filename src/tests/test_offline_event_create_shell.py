"""Tests fuer OfflineEventCreateShellView (core/views/offline.py).

Refs #1521 (#1499 SI-4): pk-lose, PUBLIC Event-Create-Shell. Der
Service Worker precacht sie und serviert sie offline IN-PLACE an der
kanonischen URL /events/new/ (SI-6). Der Shell selbst traegt kein PII — die
DocumentType-/Feld-Metadaten und die mitgenommenen Personen liest die
Alpine-Komponente ``offlineEventCreate`` client-seitig aus IndexedDB
(getOfflineFacility / listOfflineClientsDetailed).

Analog zu :class:`TestOfflineClientShellView` in test_pwa_views.py: public,
GET-only, Template-Smoke. Das Verhalten (Person-Picker, Kontaktstufen-Vorfilter,
Replay) deckt die E2E-Journey in SI-7 ab.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestOfflineEventCreateShellView:
    def test_renders_pkless_event_create_scaffold(self, client):
        response = client.get(reverse("core:offline_event_shell"))
        assert response.status_code == 200
        body = response.content.decode()
        # Alpine-Wurzel + Erfassungs-Formular-Hook.
        assert 'x-data="offlineEventCreate"' in body
        assert 'data-testid="offline-event-create-form"' in body
        # Person-Picker ("mitgenommene Person" | "— ohne Person —") und
        # Dokumentationstyp-Wahl sind im Scaffold verdrahtet.
        assert 'data-testid="offline-event-create-client"' in body
        assert 'data-testid="offline-event-create-doctype"' in body
        # Der pk-lose Feld-Loop kommt aus dem geteilten Partial
        # _offline_event_fields.html (beide Includer teilen ihn).
        assert 'x-for="field in editFields"' in body
        # Der Renderer wird geladen.
        assert "offline-create.js" in body

    def test_public_access(self, client):
        """Shell ist PII-frei und muss offline aus dem Cache servierbar sein —
        kein Server-Auth-Gate (die Metadaten liegen verschluesselt in IDB, ein
        Auth-Gate wuerde den install-time cache.addAll auf /login/ umleiten)."""
        response = client.get(reverse("core:offline_event_shell"))
        assert response.status_code == 200

    def test_is_get_only(self, client):
        """http_method_names = ["get"] — ein POST wird mit 405 abgewiesen."""
        response = client.post(reverse("core:offline_event_shell"))
        assert response.status_code == 405
