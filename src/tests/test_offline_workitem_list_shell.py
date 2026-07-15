"""Tests fuer OfflineWorkItemListShellView (core/views/offline.py).

Refs #1541 (#1499, W3-C): pk-lose, PUBLIC Aufgabenlisten-Shell. Der
Service Worker precacht sie und serviert sie offline IN-PLACE an der
kanonischen URL /workitems/ (W3-E). Der Shell selbst traegt kein PII — die
mitgenommenen/offline erfassten Aufgaben liest die Alpine-Komponente
client-seitig aus IndexedDB (listOfflineWorkItemsAggregated).

Analog zu TestOfflineClientListShellView in test_offline_client_list_shell.py:
public, GET-only, Template-Smoke.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestOfflineWorkItemListShellView:
    def test_renders_pkless_workitem_list_scaffold(self, client):
        response = client.get(reverse("core:offline_workitem_list_shell"))
        assert response.status_code == 200

    def test_public_access(self, client):
        """Shell ist PII-frei und muss offline aus dem Cache servierbar sein —
        kein Server-Auth-Gate (die Aufgaben liegen verschluesselt in IDB, ein
        Auth-Gate wuerde den install-time cache.addAll auf /login/ umleiten und
        ihn atomar scheitern lassen)."""
        response = client.get(reverse("core:offline_workitem_list_shell"))
        assert response.status_code == 200

    def test_is_get_only(self, client):
        """http_method_names = ["get"] — ein POST wird mit 405 abgewiesen."""
        response = client.post(reverse("core:offline_workitem_list_shell"))
        assert response.status_code == 405

    def test_canonical_route(self, client):
        assert reverse("core:offline_workitem_list_shell") == "/offline/workitems/"


@pytest.mark.django_db
class TestOfflineWorkItemListShellMarkup:
    """W3-C (#1541, #1499): offline_workitem_list.html spiegelt
    inbox_content.html/_workitem_row.html als Alpine ``x-for``-Liste ueber die
    aggregierten Offline-Aufgaben (``listOfflineWorkItemsAggregated``). Der
    Shell traegt kein PII — die Zeilen entstehen client-seitig; hier wird nur
    das statische Geruest geprueft (Render + node --check der JS).
    """

    @pytest.fixture
    def body(self, client):
        response = client.get(reverse("core:offline_workitem_list_shell"))
        assert response.status_code == 200
        return response.content.decode()

    def test_alpine_root_and_list_container(self, body):
        assert 'x-data="offlineWorkItemList"' in body
        assert 'x-init="load"' in body
        assert 'data-testid="offline-workitem-list"' in body

    def test_reinforced_offline_hint(self, body):
        assert "Offline-Ansicht" in body
        assert "lokaler Ausschnitt der offline mitgenommenen Aufgaben" in body

    def test_role_table_scaffold_with_x_for(self, body):
        assert 'role="table"' in body
        assert 'x-for="wi in workitems"' in body
        assert 'role="rowgroup"' in body

    def test_row_testid_present(self, body):
        assert 'data-testid="offline-workitem-row"' in body

    def test_column_headers(self, body):
        for header in ("Aufgabe", "Person", "Status", "Fällig"):
            assert header in body

    def test_status_badges_use_precomputed_booleans(self, body):
        # CSP-Alpine (#693/#672) verbietet Method-Calls mit Argumenten UND
        # Binaer-Vergleiche in Direktiven -> Status/Prioritaet als pro-Zeile
        # vorberechnete Booleans (x-show), Anzeigetext bleibt im Template (i18n).
        assert 'x-show="wi.statusOpen"' in body
        assert 'x-show="wi.statusDone"' in body
        for label in ("Offen", "In Bearbeitung", "Erledigt", "Verworfen"):
            assert label in body

    def test_priority_badges(self, body):
        assert 'x-show="wi.priorityUrgent"' in body
        assert 'x-show="wi.priorityImportant"' in body
        assert "Dringend" in body
        assert "Wichtig" in body

    def test_personless_marker_present(self, body):
        # Anonyme/personlose Aufgaben klar markiert.
        assert "ohne Person" in body

    def test_due_date_uses_precomputed_label(self, body):
        assert 'x-text="wi.dueDateLabel"' in body

    def test_empty_state_message(self, body):
        assert "Keine Aufgaben gefunden" in body

    def test_alpine_script_included(self, body):
        assert "js/offline-workitem-list.js" in body

    def test_no_django_url_tag_leaks_for_rows(self, body):
        # Detail-/Personen-Link als client-seitiger String (kein {% url %}).
        assert ':href="wi.href"' in body
