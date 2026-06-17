"""E2E-Tests für die Übergabe (?view=uebergabe-Modus im Zeitstrom, Refs #1124).

Prüft:
- Übergabe-Seite ist über Hauptnavigation erreichbar.
- Heading ``Übergabe`` und der aktuelle Wochentag werden gerendert.
- Schicht-Filter (Frühdienst/Spätdienst/Nachtdienst) wechseln den
  ``time_filter``-Querystring und ändern die Schicht-Überschrift.
- Datums-Navigation (Vor/Zurück) ändert den ``date``-Querystring.
- Statistiken-Sektion ist sichtbar; eine allgemeine Aufgabenliste gibt es
  bewusst nicht mehr (Refs #1139).

Refs #661 — Plan Top 3.
"""

import pytest

pytestmark = pytest.mark.e2e


class TestHandoverPage:
    def test_uebergabe_tab_opens_mode(self, authenticated_page, base_url):
        """Klick auf den 'Übergabe'-Tab im Zeitstrom öffnet den ?view=uebergabe-Modus (Refs #1124)."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.locator("[data-testid='zeitstrom-tab-uebergabe']").click()
        page.wait_for_url(lambda url: "view=uebergabe" in url, timeout=10000)
        assert page.locator("h2:has-text('Statistiken')").is_visible()

    def test_default_view_shows_full_day(self, authenticated_page, base_url):
        """Ohne time_filter steht 'Ganzer Tag' als Schicht-Label.

        Verwendet ein Datum aus der Vergangenheit, da bei target_date == today der
        View automatisch die zuletzt aktive Schicht wählt (handover.py).
        """
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe&date=2026-04-15", wait_until="domcontentloaded")
        # Es gibt zwei "Ganzer Tag"-Elemente: Filter-Link und Schicht-Heading.
        # Wir prüfen das Heading-Element (h2 mit Schicht-Range).
        heading = page.locator("h2:has-text('Ganzer Tag')")
        assert heading.count() >= 1

    def test_shift_filter_switches_label(self, authenticated_page, base_url):
        """Klick auf 'Frühdienst' setzt time_filter im URL und wechselt Schicht-Heading."""
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        page.locator("a:has-text('Frühdienst')").first.click()
        page.wait_for_url(lambda url: "time_filter=" in url, timeout=10000)
        assert page.locator("h2:has-text('Frühdienst')").count() >= 1

    def test_statistics_section_visible(self, authenticated_page, base_url):
        """Statistiken-Sektion (Kontakte/Aufgaben/Personen) ist sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        assert page.locator("h2:has-text('Statistiken')").is_visible()

    def test_activities_kpi_removed(self, authenticated_page, base_url):
        """Refs #1122: Die KPI-Kachel 'Aktivitäten' ist aus der Übergabe entfernt."""
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe&date=2026-04-15", wait_until="domcontentloaded")
        # Nur innerhalb der Statistik-KPI-Kacheln prüfen.
        stats = page.locator("h2:has-text('Statistiken')").locator("xpath=following-sibling::*[1]")
        assert stats.get_by_text("Aktivitäten", exact=True).count() == 0
        assert stats.get_by_text("Kontakte", exact=True).count() >= 1

    def test_highlights_section_relabeled(self, authenticated_page, base_url):
        """Refs #1121: Sektion heißt 'Übergabe-relevante Hinweise' mit erklärendem Untertitel."""
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        assert page.locator("h2:has-text('Übergabe-relevante Hinweise')").is_visible()
        assert page.get_by_text("Krisen, Hausverbote und dringende Aufgaben").is_visible()
        assert page.locator("h2:has-text('Wichtige Ereignisse')").count() == 0

    def test_open_tasks_section_removed(self, authenticated_page, base_url):
        """Refs #1139: Die allgemeine 'Offene Aufgaben'-Sektion ist entfernt.

        Offene Aufgaben mit Handlungsbedarf gehören in die Aufgaben-Fokusbox
        bzw. die Aufgabenübersicht; die Übergabe zeigt nur noch
        schichtbezogene 'Übergabe-relevante Hinweise'.
        """
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        # Anker: Statistiken-Heading existiert in der Übergabe immer.
        page.locator("h2:has-text('Statistiken')").wait_for(state="visible", timeout=30000)
        assert page.locator("h2:has-text('Offene Aufgaben')").count() == 0
        assert page.locator("h2:has-text('Übergabe-relevante Hinweise')").count() == 1

    def test_date_back_navigation_changes_url(self, authenticated_page, base_url):
        """Klick auf 'Tag zurück' setzt ?date=...-1 im URL."""
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe&date=2026-04-25", wait_until="domcontentloaded")
        # Zurück-Pfeil ist der erste <a> mit href="?date=2026-04-24..."
        page.locator("a[href*='date=2026-04-24']").first.click()
        page.wait_for_url(lambda url: "date=2026-04-24" in url, timeout=10000)
        assert "date=2026-04-24" in page.url
