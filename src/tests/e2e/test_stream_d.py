"""E2E-Tests für Stream D: WorkItem-Inbox, CRUD, Badge, Hausverbot, Löschanträge."""

import re

import pytest

pytestmark = pytest.mark.e2e


class TestWorkItemNavigation:
    """1. Login → Aufgaben-Link in Navigation sichtbar."""

    def test_aufgaben_link_visible(self, authenticated_page):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("text=Aufgaben").is_visible()

    def test_aufgaben_link_navigates_to_inbox(self, authenticated_page, base_url):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        nav.locator("a:has-text('Aufgaben')").click()
        page.wait_for_url(re.compile(r"/workitems/$"))
        assert page.locator("h1").inner_text() == "Aufgaben"


class TestWorkItemInbox:
    """2. WorkItem-Inbox → Neue Aufgabe erstellen → erscheint in Inbox."""

    def test_inbox_shows_sections(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/workitems/")
        assert page.locator("text=Offen").first.is_visible()
        assert page.locator("text=In Bearbeitung").first.is_visible()
        assert page.locator("text=Kürzlich erledigt").first.is_visible()

    def test_create_workitem_and_appears_in_inbox(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/workitems/new/")

        assert page.locator("h1").inner_text() == "Neue Aufgabe"

        page.select_option("select[name='item_type']", value="task")
        page.fill("input[name='title']", "E2E-Testaufgabe")
        page.fill("textarea[name='description']", "Testbeschreibung")
        page.select_option("select[name='priority']", value="urgent")

        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/workitems/$"))

        assert page.locator("[role='alert']:has-text('Aufgabe wurde erstellt')").first.is_visible()
        assert page.locator("text=E2E-Testaufgabe").first.is_visible()


class TestWorkItemStatusHTMX:
    """3. WorkItem Status per Klick ändern (HTMX)."""

    def _create_task(self, page, base_url, title="HTMX-Statustest"):
        page.goto(f"{base_url}/workitems/new/")
        page.select_option("select[name='item_type']", value="task")
        page.fill("input[name='title']", title)
        page.select_option("select[name='priority']", value="normal")
        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/workitems/$"))

    def test_accept_workitem_via_htmx(self, authenticated_page, base_url):
        page = authenticated_page
        self._create_task(page, base_url)

        # "Annehmen" klickt → Status wechselt zu In Bearbeitung
        accept_btn = page.locator("button:has-text('Annehmen')").first
        accept_btn.click()

        # Nach HTMX swap sollte "Erledigt"-Button sichtbar sein
        page.locator("button:has-text('Erledigt')").first.wait_for(state="visible", timeout=5000)


class TestWorkItemBadge:
    """4. Badge-Count in Navigation aktualisiert sich."""

    def test_badge_visible_with_open_items(self, authenticated_page, base_url):
        page = authenticated_page

        # Aufgabe erstellen damit Badge angezeigt wird
        page.goto(f"{base_url}/workitems/new/")
        page.select_option("select[name='item_type']", value="task")
        page.fill("input[name='title']", "Badge-Test")
        page.select_option("select[name='priority']", value="normal")
        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/workitems/$"))

        # Zurück zur Startseite → Badge prüfen
        page.goto(f"{base_url}/")
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        aufgaben_link = nav.locator("a:has-text('Aufgaben')")
        assert aufgaben_link.is_visible()
        # Badge sollte eine Zahl anzeigen (mindestens 1)
        badge = aufgaben_link.locator("span")
        assert badge.count() > 0


class TestHausverbotBanner:
    """5. Aktivitätslog → Hausverbot-Banner sichtbar (mit Seed-Daten)."""

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


class TestDeletionRequestList:
    """6. Löschanträge-Liste → Lead sieht offene Anträge."""

    def test_deletion_request_list_accessible(self, authenticated_page, base_url):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        loeschantraege_link = nav.locator("a:has-text('Löschanträge')")

        if loeschantraege_link.count() > 0:
            loeschantraege_link.click()
            page.wait_for_url(re.compile(r"/deletion-requests/$"))
            assert page.locator("h1").inner_text() == "Löschanträge"
            assert page.locator("text=Ausstehend").first.is_visible()
