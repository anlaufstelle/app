"""E2E-Tests: Statistik-Zugriffskontrolle für Assistenz.

Nur neuer Test: Assistenz kann /statistics/ nicht aufrufen.

Bereits abgedeckt in test_stream_e.py:
- Health-Endpoint, Statistik-Navigation, KPI-Karten, Tabellen
- HTMX-Zeitraum-Wechsel, CSV/PDF/Jugendamt-Export, Staff-Zugriff
"""

import pytest

pytestmark = pytest.mark.e2e


class TestAssistantCannotAccessStatistics:
    """Assistenz-Rolle hat keinen Zugriff auf Statistiken."""

    def test_assistant_no_statistics_access(self, assistant_page, base_url):
        resp = assistant_page.goto(f"{base_url}/statistics/")
        assert resp.status == 403

    def test_assistant_no_statistics_nav_link(self, assistant_page):
        nav = assistant_page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Statistik')").count() == 0
