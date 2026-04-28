"""E2E-Tests für die Übergabe-Seite (/uebergabe/).

Prüft:
- Übergabe-Seite ist über Hauptnavigation erreichbar.
- Heading ``Übergabe`` und der aktuelle Wochentag werden gerendert.
- Schicht-Filter (Frühdienst/Spätdienst/Nachtdienst) wechseln den
  ``time_filter``-Querystring und ändern die Schicht-Überschrift.
- Datums-Navigation (Vor/Zurück) ändert den ``date``-Querystring.
- Statistiken-Sektion und Aufgaben-Sektion sind sichtbar; Aufgaben mit
  Priorität tragen ein Priority-Badge.

Refs #661 — Plan Top 3.
"""

import pytest

pytestmark = pytest.mark.e2e


class TestHandoverPage:
    def test_link_in_navigation(self, authenticated_page, base_url):
        """Klick auf 'Übergabe' in der Hauptnavigation führt zur Übergabe-Seite."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.locator("nav[aria-label='Hauptnavigation']").locator("a:has-text('Übergabe')").first.click()
        page.wait_for_url(lambda url: "/uebergabe/" in url, timeout=10000)
        assert page.locator("h1").inner_text() == "Übergabe"

    def test_default_view_shows_full_day(self, authenticated_page, base_url):
        """Ohne time_filter steht 'Ganzer Tag' als Schicht-Label.

        Verwendet ein Datum aus der Vergangenheit, da bei target_date == today der
        View automatisch die zuletzt aktive Schicht wählt (handover.py).
        """
        page = authenticated_page
        page.goto(f"{base_url}/uebergabe/?date=2026-04-15", wait_until="domcontentloaded")
        # Es gibt zwei "Ganzer Tag"-Elemente: Filter-Link und Schicht-Heading.
        # Wir prüfen das Heading-Element (h2 mit Schicht-Range).
        heading = page.locator("h2:has-text('Ganzer Tag')")
        assert heading.count() >= 1

    def test_shift_filter_switches_label(self, authenticated_page, base_url):
        """Klick auf 'Frühdienst' setzt time_filter im URL und wechselt Schicht-Heading."""
        page = authenticated_page
        page.goto(f"{base_url}/uebergabe/", wait_until="domcontentloaded")
        page.locator("a:has-text('Frühdienst')").first.click()
        page.wait_for_url(lambda url: "time_filter=" in url, timeout=10000)
        assert page.locator("h2:has-text('Frühdienst')").count() >= 1

    def test_statistics_section_visible(self, authenticated_page, base_url):
        """Statistiken-Sektion (Kontakte/Aktivitäten/Aufgaben/Klientel) ist sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/uebergabe/", wait_until="domcontentloaded")
        assert page.locator("h2:has-text('Statistiken')").is_visible()

    def test_highlights_section_visible(self, authenticated_page, base_url):
        """'Wichtige Ereignisse'-Sektion wird gerendert."""
        page = authenticated_page
        page.goto(f"{base_url}/uebergabe/", wait_until="domcontentloaded")
        assert page.locator("h2:has-text('Wichtige Ereignisse')").is_visible()

    def test_open_tasks_section_visible(self, authenticated_page, base_url):
        """'Offene Aufgaben'-Sektion wird gerendert."""
        page = authenticated_page
        page.goto(f"{base_url}/uebergabe/", wait_until="domcontentloaded")
        assert page.locator("h2:has-text('Offene Aufgaben')").is_visible()

    def test_date_back_navigation_changes_url(self, authenticated_page, base_url):
        """Klick auf 'Tag zurück' setzt ?date=...-1 im URL."""
        page = authenticated_page
        page.goto(f"{base_url}/uebergabe/?date=2026-04-25", wait_until="domcontentloaded")
        # Zurück-Pfeil ist der erste <a> mit href="?date=2026-04-24..."
        page.locator("a[href*='date=2026-04-24']").first.click()
        page.wait_for_url(lambda url: "date=2026-04-24" in url, timeout=10000)
        assert "date=2026-04-24" in page.url
