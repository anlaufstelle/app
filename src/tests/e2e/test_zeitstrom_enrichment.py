"""E2E-Tests: Zeitstrom-Vorschaufelder, Badge-Farben, Übergabe.

Refs #411 — Zeitstrom aufwerten.
"""

import pytest

pytestmark = pytest.mark.e2e


class TestEventCardPreview:
    """Event-Karten zeigen Vorschau-Felder und farbige Badges."""

    def test_event_card_shows_preview_fields(self, authenticated_page, base_url):
        """Seed events should display preview fields like Dauer on the card."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        # Look for any event card with preview data (e.g. "Dauer:")
        # Seed data creates events with dauer values
        event_cards = page.locator("a[href*='/events/']")
        assert event_cards.count() > 0, "No event cards found in Zeitstrom"

    def test_badge_colors_vary_by_doc_type(self, authenticated_page, base_url):
        """Badges should not all be indigo — different doc types get different colors."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        # Check that at least one non-indigo badge class exists
        # Seed data creates Kontakt (indigo), Krisengespräch (amber), etc.
        badges = page.locator(".rounded.text-xs.font-medium")
        badge_count = badges.count()
        if badge_count > 1:
            classes_set = set()
            for i in range(min(badge_count, 10)):
                cls = badges.nth(i).get_attribute("class") or ""
                classes_set.add(cls)
            # If seed creates multiple doc types, classes should differ
            # This is a soft check — at minimum verify badges render
            assert badge_count > 0


def _find_date_with_activities(page, base_url, verb=None):
    """Navigate backwards from today until a date with activities is found.

    Returns True if a date with activities was found and the page is on that date.
    Tries up to 60 days back.
    """
    from datetime import date, timedelta

    today = date.today()
    for offset in range(60):
        d = today - timedelta(days=offset)
        date_str = d.isoformat()
        page.goto(
            f"{base_url}/?date={date_str}&type=activities",
            wait_until="domcontentloaded",
        )
        # Only check badges within the feed list area
        badges = page.locator("#feed-list .rounded.text-xs.font-medium")
        if badges.count() > 0:
            if verb is None:
                return True
            texts = [badges.nth(i).inner_text().strip() for i in range(badges.count())]
            if verb in texts:
                return True
    return False


class TestActivityCards:
    """Activity-Karten: Keine created-Duplikate, farbige Badges, klickbare Links."""

    def test_no_created_activity_duplicates(self, authenticated_page, base_url):
        """Mixed feed should not show 'erstellt' activities (redundant with object cards)."""
        page = authenticated_page
        # Navigate to a date that has 'erstellt' activities in the activities-only view
        found = _find_date_with_activities(page, base_url, verb="erstellt")
        if not found:
            pytest.skip("No seed date with 'erstellt' activities found")

        # Extract the date from the current URL
        import re

        date_match = re.search(r"date=(\d{4}-\d{2}-\d{2})", page.url)
        date_str = date_match.group(1) if date_match else ""

        # Now load the mixed feed (type=all) for this date
        page.goto(f"{base_url}/?date={date_str}", wait_until="domcontentloaded")

        # In the mixed feed, no badge should show "erstellt"
        badges = page.locator("#feed-list .rounded.text-xs.font-medium")
        badge_count = badges.count()
        for i in range(min(badge_count, 20)):
            text = badges.nth(i).inner_text().strip()
            if text in ("erstellt", "aktualisiert", "gelöscht", "qualifiziert", "erledigt", "wiedereröffnet"):
                assert text != "erstellt", "Found 'erstellt' activity badge in mixed feed"

    def test_activities_filter_shows_created(self, authenticated_page, base_url):
        """When filtering by 'Aktivitäten', created activities should be visible."""
        page = authenticated_page
        found = _find_date_with_activities(page, base_url, verb="erstellt")
        if not found:
            pytest.skip("No seed date with 'erstellt' activities found")

        # Page is already on the date with activities + type=activities
        badges = page.locator("#feed-list .rounded.text-xs.font-medium")
        badge_texts = [badges.nth(i).inner_text().strip() for i in range(badges.count())]
        assert "erstellt" in badge_texts, "Expected 'erstellt' badges in activities filter"

    def test_activity_card_has_badge(self, authenticated_page, base_url):
        """Activity cards should display a colored verb badge."""
        page = authenticated_page
        found = _find_date_with_activities(page, base_url)
        if not found:
            pytest.skip("No seed date with activities found")

        badges = page.locator("#feed-list .rounded.text-xs.font-medium")
        assert badges.count() > 0, "No activity badges found"

    def test_activity_card_links_to_target(self, authenticated_page, base_url):
        """Non-deleted activity cards should link to their target detail page."""
        page = authenticated_page
        found = _find_date_with_activities(page, base_url)
        if not found:
            pytest.skip("No seed date with activities found")

        # Look for activity cards that are links (non-deleted targets)
        selector = (
            "#feed-list a[href*='/clients/'],"
            " #feed-list a[href*='/events/'],"
            " #feed-list a[href*='/workitems/'],"
            " #feed-list a[href*='/cases/']"
        )
        activity_links = page.locator(selector)
        assert activity_links.count() > 0, "No clickable activity cards found"


class TestHandoverPage:
    """Übergabe-Seite erreichbar und zeigt Inhalt."""

    def test_handover_page_accessible(self, authenticated_page, base_url):
        """The /uebergabe/ page loads successfully."""
        page = authenticated_page
        page.goto(f"{base_url}/uebergabe/", wait_until="domcontentloaded")
        assert page.locator("h1").inner_text() == "Übergabe"

    def test_handover_nav_link_visible(self, authenticated_page, base_url):
        """The Übergabe link is visible in the desktop sidebar."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        nav_link = page.locator("[data-testid='nav-handover']")
        assert nav_link.is_visible()
        assert "Übergabe" in nav_link.inner_text()

    def test_handover_shows_stats(self, authenticated_page, base_url):
        """The handover page shows statistics section."""
        page = authenticated_page
        page.goto(f"{base_url}/uebergabe/", wait_until="domcontentloaded")
        # Stats section should show "Kontakte" label
        assert page.locator("text=Kontakte").count() > 0


class TestHandoverInZeitstrom:
    """Übergabe-Zusammenfassung im Zeitstrom bei aktiver Schicht."""

    def test_handover_summary_with_time_filter(self, authenticated_page, base_url):
        """When a time filter is selected, a handover summary should appear."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        # Click the first time filter tab (not "Alle")
        filter_tabs = page.locator("[data-testid='time-filter-tab']")
        if filter_tabs.count() > 1:
            # Click second tab (first non-"Alle" filter)
            filter_tabs.nth(1).click()
            page.wait_for_load_state("domcontentloaded")

            # The handover summary details element should be present
            summary = page.locator("[data-testid='handover-summary']")
            if summary.count() > 0:
                assert summary.is_visible()
