"""E2E-Tests für Pagination: Audit-Log-Liste mit >50 Einträgen."""

import re

import pytest

pytestmark = pytest.mark.e2e


class TestAuditPagination:
    """Pagination auf Audit-Log-Liste (50 pro Seite)."""

    def test_audit_pagination_visible(self, authenticated_page, base_url):
        """Bei ausreichend Einträgen sind Pagination-Buttons sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        # Pagination-Container: "X–Y von Z Einträgen" + Seitennavigation
        # Nur sichtbar wenn >50 Einträge (1 Seite = 50)
        pagination = page.locator("text=/\\d+–\\d+ von \\d+ Einträgen/")
        if pagination.count() > 0:
            assert pagination.is_visible()
            # Seitennummer-Anzeige (z.B. "1 / 3")
            assert page.locator("text=/\\d+ \\/ \\d+/").is_visible()

    def test_audit_pagination_navigates(self, authenticated_page, base_url):
        """Klick auf 'Nächste Seite' → URL enthält ?page=2, Tabelle zeigt Einträge."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        # Nächste-Seite-Button (›) finden
        next_button = page.locator("a:has-text('›')")
        if next_button.count() == 0:
            pytest.skip("Nicht genug Audit-Einträge für Pagination (braucht >50)")

        next_button.click()
        page.wait_for_url(re.compile(r"page=2"))
        page.wait_for_load_state("domcontentloaded")

        # Tabelle auf Seite 2 enthält Einträge
        rows = page.locator("#audit-table table tbody tr")
        assert rows.count() > 0

        # Seitennummer zeigt "2 / X"
        assert page.locator("text=/2 \\/ \\d+/").is_visible()

    def test_audit_pagination_preserves_filters(self, authenticated_page, base_url):
        """Filter setzen + paginieren → Filter bleibt in URL erhalten."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/?action=login")
        page.wait_for_load_state("domcontentloaded")

        # Filter ist aktiv
        assert page.locator("select[name='action']").input_value() == "login"

        # Nächste-Seite-Button
        next_button = page.locator("a:has-text('›')")
        if next_button.count() == 0:
            pytest.skip("Nicht genug Login-Einträge für Pagination mit Filter")

        # URL des Nächste-Buttons prüfen: enthält sowohl page= als auch action=login
        href = next_button.get_attribute("href")
        assert "page=" in href, f"Pagination-Link fehlt page-Parameter: {href}"
        assert "action=login" in href, f"Pagination-Link verliert Filter: {href}"

    def test_pagination_info_text_format(self, authenticated_page, base_url):
        """Pagination zeigt 'X–Y von Z Einträgen' im korrekten Format."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        info = page.locator("text=/\\d+–\\d+ von \\d+ Einträgen/")
        if info.count() == 0:
            pytest.skip("Nicht genug Audit-Einträge für Pagination-Info")

        text = info.inner_text()
        # Format: "1–50 von 123 Einträgen"
        match = re.match(r"(\d+)–(\d+) von (\d+) Einträgen", text)
        assert match, f"Unerwartetes Pagination-Format: {text}"

        start, end, total = int(match.group(1)), int(match.group(2)), int(match.group(3))
        assert start == 1, f"Erste Seite sollte bei 1 starten, nicht {start}"
        assert end <= 50, f"Erste Seite sollte max 50 Einträge zeigen, nicht {end}"
        assert total > 0, "Gesamtanzahl sollte > 0 sein"
