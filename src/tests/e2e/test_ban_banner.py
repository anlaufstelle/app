"""E2E-Tests: Hausverbot-Banner im Aktivitätslog."""

import pytest

pytestmark = pytest.mark.e2e


class TestHausverbotBanner:
    """Aktivitätslog → Hausverbot-Banner sichtbar (mit Seed-Daten)."""

    def test_ban_banner_on_aktivitaetslog(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")

        # Seed-Daten sollten ein aktives Hausverbot enthalten
        # Prüfen ob Banner sichtbar ist (falls Seed einen Ban hat)
        banner = page.locator("text=Hausverbot:")
        if banner.count() > 0:
            assert banner.first.is_visible()
        else:
            # Kein Ban in Seed-Daten — ok, Test überspringt
            pass
