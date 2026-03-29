"""E2E-Tests: Klientel-Filter nach Stage und Altersgruppe.

Seed-Klientel:
- Stern-42: QUALIFIED, AGE_18_26
- Wolke-17: QUALIFIED, AGE_27_PLUS
- Blitz-08: IDENTIFIED, U18
- Regen-55: IDENTIFIED, AGE_27_PLUS
- Wind-33: QUALIFIED, AGE_18_26
- Nebel-71: IDENTIFIED, UNKNOWN
- Sonne-99: QUALIFIED, AGE_27_PLUS
"""

import pytest

pytestmark = pytest.mark.e2e


class TestClientListFilter:
    """Klientel-Liste: Filterung nach Stage und Altersgruppe."""

    def test_filter_by_stage_qualified(self, authenticated_page, base_url):
        """Filter nach Stage 'qualified' zeigt nur qualifizierte Klientel."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/?stage=qualified")
        page.wait_for_load_state("domcontentloaded")

        # Qualifizierte Klientel sichtbar
        assert page.locator("a:has-text('Stern-42')").first.is_visible()
        assert page.locator("a:has-text('Wolke-17')").first.is_visible()

        # Identifizierte Klientel nicht sichtbar
        assert page.locator("a:has-text('Blitz-08')").count() == 0

    def test_filter_by_stage_identified(self, authenticated_page, base_url):
        """Filter nach Stage 'identified' zeigt nur identifizierte Klientel."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/?stage=identified")
        page.wait_for_load_state("domcontentloaded")

        # Identifizierte Klientel sichtbar
        assert page.locator("a:has-text('Blitz-08')").first.is_visible()
        assert page.locator("a:has-text('Regen-55')").first.is_visible()

        # Qualifizierte Klientel nicht sichtbar
        assert page.locator("a:has-text('Stern-42')").count() == 0

    def test_no_stage_filter_shows_all(self, authenticated_page, base_url):
        """Ohne Stage-Filter werden Klientel beider Stufen gefunden."""
        page = authenticated_page

        # Qualifizierte via Suche erreichbar
        page.goto(f"{base_url}/clients/?q=Stern")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("a:has-text('Stern-42')").first.is_visible()

        # Identifizierte via Suche erreichbar
        page.goto(f"{base_url}/clients/?q=Blitz")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("a:has-text('Blitz-08')").first.is_visible()

    def test_filter_by_age_cluster(self, authenticated_page, base_url):
        """Filter nach Altersgruppe per URL-Parameter."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/?age=u18")
        page.wait_for_load_state("domcontentloaded")

        # Blitz-08 ist U18
        assert page.locator("a:has-text('Blitz-08')").first.is_visible()
        # Stern-42 ist AGE_18_26 → nicht sichtbar
        assert page.locator("a:has-text('Stern-42')").count() == 0
