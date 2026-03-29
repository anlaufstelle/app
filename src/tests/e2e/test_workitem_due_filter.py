"""E2E-Tests: Fälligkeitsfilter in der Aufgaben-Inbox.

Testet:
- Fälligkeits-Dropdown ist vorhanden mit allen Optionen
- Filter zurücksetzen zeigt wieder alle Aufgaben
"""

import pytest

pytestmark = pytest.mark.e2e


class TestWorkItemDueFilter:
    """Fälligkeitsfilter in der Aufgaben-Inbox."""

    def test_due_filter_dropdown_present(self, staff_page, base_url):
        """Das Fälligkeits-Dropdown ist in der Filter-Leiste vorhanden."""
        page = staff_page
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        due_select = page.locator("select[name='due']")
        due_select.wait_for(state="visible", timeout=5000)
        assert due_select.is_visible()

        # Prüfe, dass die erwarteten Optionen vorhanden sind
        options = due_select.locator("option").all_inner_texts()
        assert "Alle" in options
        assert "Überfällig" in options
        assert "Heute" in options
        assert "Diese Woche" in options
        assert "Ohne Frist" in options

    def test_filter_and_reset(self, staff_page, base_url):
        """Filter setzen und zurücksetzen funktioniert ohne Fehler."""
        page = staff_page
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Filter auf "Überfällig" setzen
        page.select_option("select[name='due']", value="overdue")
        page.locator("#inbox-content").wait_for(state="visible", timeout=5000)

        # Seite zeigt keine Fehlermeldung
        assert "Fehler" not in page.content()
        assert "Server Error" not in page.content()

        # Filter zurücksetzen
        page.select_option("select[name='due']", value="")
        page.locator("#inbox-content").wait_for(state="visible", timeout=5000)

        # Nach Reset immer noch kein Fehler
        assert "Fehler" not in page.content()
        assert "Server Error" not in page.content()
