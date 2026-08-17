"""Tests fuer OfflineZeitstromShellView (core/views/offline.py).

Refs #1542 (#1499, W3-D): pk-lose, PUBLIC Zeitstrom-Shell. Der Service
Worker precacht sie und serviert sie offline IN-PLACE an der kanonischen URL /
(W3-E). Der Shell selbst traegt kein PII — die lokale Chronik liest die
Alpine-Komponente client-seitig aus IndexedDB (listOfflineEventsAggregated).

Analog zu TestOfflineClientListShellView/TestOfflineWorkItemListShellView:
public, GET-only, Template-Smoke.
"""

from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse


@pytest.mark.django_db
class TestOfflineZeitstromShellView:
    def test_renders_pkless_zeitstrom_scaffold(self, client):
        response = client.get(reverse("core:offline_zeitstrom_shell"))
        assert response.status_code == 200

    def test_public_access(self, client):
        """Shell ist PII-frei und muss offline aus dem Cache servierbar sein —
        kein Server-Auth-Gate (die Chronik liegt verschluesselt in IDB, ein
        Auth-Gate wuerde den install-time cache.addAll auf /login/ umleiten und
        ihn atomar scheitern lassen)."""
        response = client.get(reverse("core:offline_zeitstrom_shell"))
        assert response.status_code == 200

    def test_is_get_only(self, client):
        """http_method_names = ["get"] — ein POST wird mit 405 abgewiesen."""
        response = client.post(reverse("core:offline_zeitstrom_shell"))
        assert response.status_code == 405

    def test_canonical_route(self, client):
        assert reverse("core:offline_zeitstrom_shell") == "/offline/zeitstrom/"


@pytest.mark.django_db
class TestOfflineZeitstromShellMarkup:
    """W3-D (#1542, #1499): offline_zeitstrom.html spiegelt
    feed_list.html/_event_card.html als Alpine ``x-for``-Chronik ueber die
    aggregierten Offline-Events (``listOfflineEventsAggregated``). Der Shell
    traegt kein PII — die Zeilen entstehen client-seitig; hier wird nur das
    statische Geruest geprueft (Render + node --check der JS).
    """

    @pytest.fixture
    def body(self, client):
        response = client.get(reverse("core:offline_zeitstrom_shell"))
        assert response.status_code == 200
        return response.content.decode()

    def test_alpine_root_and_container(self, body):
        assert 'x-data="offlineZeitstrom"' in body
        assert 'x-init="load"' in body
        assert 'data-testid="offline-zeitstrom"' in body

    def test_reinforced_offline_hint(self, body):
        assert "Offline-Ansicht" in body
        assert "Lokale Chronik" in body
        assert "nur die offline mitgenommenen Vorgänge" in body

    def test_x_for_over_events(self, body):
        assert 'x-for="ev in events"' in body
        assert 'data-testid="offline-zeitstrom-row"' in body

    def test_contact_badge_and_doctype(self, body):
        assert "Kontakt" in body
        assert 'x-text="ev.documentTypeName"' in body

    def test_occurred_at_precomputed_label(self, body):
        assert 'x-text="ev.occurredAtLabel"' in body

    def test_anonymous_marker_present(self, body):
        assert "Anonym" in body

    def test_person_link_is_canonical_string_not_url_tag(self, body):
        assert ':href="ev.href"' in body

    def test_empty_state_message(self, body):
        assert "Keine Einträge" in body

    def test_alpine_script_included(self, body):
        assert "js/offline-zeitstrom.js" in body

    def test_pii_free_tally_scaffold_has_cold_server_pending_timestamp_and_conflict_contract(self, body):
        assert 'x-data="offlineTally"' in body
        assert 'data-testid="offline-tally"' in body
        assert 'x-for="row in rows"' in body
        assert 'x-text="row.confirmedLabel"' in body
        assert 'x-text="row.pendingLabel"' in body
        assert 'x-text="snapshotAtLabel"' in body
        assert 'x-show="hasSnapshotAt"' in body
        assert 'data-testid="offline-tally-server-status"' in body
        assert 'data-testid="offline-tally-snapshot-at"' in body
        assert 'href="/offline/conflicts/"' in body
        assert "Serverstand" in body
        assert "ausstehend" in body
        assert "Nicht übertragbar" in body

    def test_online_tally_row_has_compact_no_wrap_contract_at_390px(self):
        source = (Path(settings.BASE_DIR) / "templates/core/zeitstrom/partials/tally_row.html").read_text()
        assert 'class="flex flex-wrap items-center gap-2 min-w-0"' in source
        assert "min-w-0 flex-1" in source
        assert "flex-none" in source

    def test_cold_tally_status_live_region_contains_type_and_both_values(self, body):
        assert 'data-testid="offline-tally-server-status"' in body
        assert 'role="status"' in body
        assert 'aria-live="polite"' in body
        assert 'aria-atomic="true"' in body
        assert 'class="sr-only" x-text="row.documentTypeName + \': \'"' in body
        status_start = body.index('data-testid="offline-tally-server-status"')
        type_start = body.index("row.documentTypeName + ': '", status_start)
        confirmed_start = body.index('x-text="row.confirmedLabel"', status_start)
        pending_start = body.index('x-text="row.pendingLabel"', status_start)
        assert status_start < type_start < confirmed_start < pending_start

    def test_tally_shell_contains_no_server_data_or_tally_values(self, body):
        assert "document_type_name" not in body
        assert "confirmed_count" not in body
        assert "data_json" not in body
        assert "idempotency_key" not in body

    def test_offline_tally_script_included(self, body):
        assert "js/offline-tally.js" in body
