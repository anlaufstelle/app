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


@pytest.mark.django_db
class TestOfflineClientListShellSI4:
    """SI-4 (#1532, #1499): offline_list.html spiegelt
    partials/table.html 1:1 als Alpine ``x-for``-Liste ueber die gecachten
    Klienten (``listOfflineClientsDetailed``). Der Shell traegt kein PII —
    die Zeilen entstehen client-seitig; hier wird nur das statische Geruest
    geprueft (Render + node --check der JS; Interaktion/Filter kommt SI-6,
    E2E SI-9).
    """

    @pytest.fixture
    def body(self, client):
        response = client.get(reverse("core:offline_client_list_shell"))
        assert response.status_code == 200
        return response.content.decode()

    def test_alpine_root_and_list_container(self, body):
        assert 'x-data="offlineClientList"' in body
        assert 'data-testid="offline-client-list"' in body

    def test_role_table_scaffold_with_x_for(self, body):
        assert 'role="table"' in body
        # SI-6: iteriert die gefilterte Sicht (Suche/Stufe/Alter), nicht die
        # Rohliste — bleibt aber der 1:1-Spiegel von partials/table.html.
        assert 'x-for="c in filteredClients"' in body
        assert 'role="rowgroup"' in body

    def test_grid_string_mirrors_table_partial_1to1(self, body):
        # Muss dem pro-Row-Grid aus partials/table.html exakt entsprechen.
        assert "sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.5fr)_auto]" in body

    def test_visibility_classes_mirrored(self, body):
        # hidden sm:/md:-Sichtbarkeit aus table.html gespiegelt.
        assert "hidden sm:grid" in body
        assert "hidden md:block" in body

    def test_row_and_link_testids_mirrored(self, body):
        assert 'data-testid="client-row"' in body
        assert 'data-testid="client-detail-link"' in body

    def test_column_headers(self, body):
        for header in ("Pseudonym", "Stufe", "Alter", "Letzter Kontakt"):
            assert header in body

    def test_badge_getters_referenced(self, body):
        assert "stageClass(c.contactStage)" in body
        assert "stageLabel(c)" in body

    def test_deactivated_marker_present(self, body):
        # is_active=False Klienten bleiben sichtbar mit "deaktiviert"-Markierung.
        assert "deaktiviert" in body

    def test_empty_state_message(self, body):
        assert "Keine Personen gefunden" in body

    def test_only_remove_no_take(self, body):
        # In der Offline-Liste nur "Entfernen" — "Mitnehmen" waere redundant.
        assert "Entfernen" in body
        assert "Mitnehmen" not in body

    def test_detail_link_is_canonical_string_not_url_tag(self, body):
        # Link auf /clients/<pk>/ als client-seitiger String (kein {% url %}).
        assert ':href="c.href"' in body

    def test_alpine_script_included(self, body):
        assert "js/offline-client-list.js" in body


@pytest.mark.django_db
class TestOfflineClientListShellSI6:
    """SI-6 (#1534, #1499): client-seitige Suche + Stufe-/Alter-Filter
    ueber die gecachten Klienten (Vorbild ``offline-home.js applyFilter``,
    Parallele zur Online-Filterleiste ``clients/list.html``). Rein clientseitig
    und reaktiv (Alpine ``x-model`` + ``filteredClients``-Getter) — hier wird
    nur das statische Geruest der Controls geprueft; die Filterlogik selbst
    faellt in JS (node --check) bzw. E2E (SI-9).
    """

    @pytest.fixture
    def body(self, client):
        response = client.get(reverse("core:offline_client_list_shell"))
        assert response.status_code == 200
        return response.content.decode()

    def test_filter_bar_container(self, body):
        assert 'data-testid="offline-client-filters"' in body

    def test_search_input_bound_to_alpine(self, body):
        assert 'data-testid="offline-client-search"' in body
        assert 'x-model="searchQuery"' in body

    def test_search_placeholder_matches_online(self, body):
        # Gleiches i18n-msgid wie die Online-Liste (clients/list.html).
        assert "Pseudonym suchen" in body

    def test_stage_filter_select_and_options(self, body):
        assert 'data-testid="offline-client-stage-filter"' in body
        assert 'x-model="stageFilter"' in body
        # Werte = Client.ContactStage.choices (1:1, wie _client_badge/stageClass).
        assert 'value="identified"' in body
        assert 'value="qualified"' in body
        assert "Alle Stufen" in body

    def test_age_filter_select_and_options(self, body):
        assert 'data-testid="offline-client-age-filter"' in body
        assert 'x-model="ageFilter"' in body
        # Werte = Client.AgeCluster.choices (1:1).
        assert 'value="u18"' in body
        assert 'value="18_26"' in body
        assert 'value="27_plus"' in body
        assert 'value="unknown"' in body
        assert "Alle Altersgruppen" in body

    def test_filter_bar_only_when_clients_cached(self, body):
        # Controls erst nach dem Laden mit >=1 gecachten Person (x-show).
        assert 'x-show="hasCachedClients"' in body
