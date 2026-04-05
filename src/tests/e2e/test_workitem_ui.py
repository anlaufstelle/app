"""E2E-Tests: WorkItem-UI — Navigation, Inbox, Status, Badge, Klickbarkeit, Erledigungsdatum."""

import re

import pytest

pytestmark = pytest.mark.e2e


class TestWorkItemNavigation:
    """Aufgaben-Link in Navigation sichtbar und navigierbar."""

    @pytest.mark.smoke
    def test_aufgaben_link_visible(self, authenticated_page):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("text=Aufgaben").is_visible()

    @pytest.mark.smoke
    def test_aufgaben_link_navigates_to_inbox(self, authenticated_page, base_url):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        nav.locator("a:has-text('Aufgaben')").click()
        page.wait_for_url(re.compile(r"/workitems/$"))
        assert page.locator("h1").inner_text() == "Aufgaben"


class TestWorkItemInbox:
    """WorkItem-Inbox → Neue Aufgabe erstellen → erscheint in Inbox."""

    def test_inbox_shows_sections(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/workitems/")
        assert page.locator("text=Offen").first.is_visible()
        assert page.locator("text=In Bearbeitung").first.is_visible()
        assert page.locator("text=Kürzlich erledigt").first.is_visible()

    @pytest.mark.smoke
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
    """WorkItem Status per Klick ändern (HTMX)."""

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
    """Badge-Count in Navigation aktualisiert sich."""

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


def _create_open_workitem(page, base_url, title="UI-Test-Aufgabe"):
    """Erstellt eine offene Aufgabe und kehrt zur Inbox zurück."""
    page.goto(f"{base_url}/workitems/new/")
    page.wait_for_load_state("domcontentloaded")
    page.select_option("select[name='item_type']", value="task")
    page.fill("input[name='title']", title)
    page.select_option("select[name='priority']", value="urgent")
    page.click("button:has-text('Speichern')")
    page.wait_for_url(re.compile(r"/workitems/$"))


class TestWorkItemClickableAktivitaetslog:
    """Aufgaben im Aktivitätslog-Sidebar sind klickbar und zeigen Detail."""

    def test_sidebar_workitem_links_to_detail(self, authenticated_page, base_url):
        """Aufgabe im Aktivitätslog-Sidebar anklicken öffnet Detail-Seite."""
        _create_open_workitem(authenticated_page, base_url, "Zeitstrom-Klicktest")

        authenticated_page.goto(f"{base_url}/")
        authenticated_page.wait_for_load_state("domcontentloaded")

        # Sidebar sollte die Aufgabe als Link enthalten
        workitem_link = authenticated_page.locator("a:has-text('Zeitstrom-Klicktest')").first
        workitem_link.wait_for(state="visible", timeout=5000)
        workitem_link.click()

        authenticated_page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/$"))
        assert authenticated_page.locator("h1:has-text('Zeitstrom-Klicktest')").is_visible()


class TestWorkItemClickableClientDetail:
    """Aufgaben in der Klientel-Detailansicht sind klickbar."""

    def test_client_detail_workitem_links_to_detail(self, authenticated_page, base_url):
        """Aufgabe in Klientel-Detail anklicken öffnet Detail-Seite."""
        # Aufgabe mit Klientel erstellen
        authenticated_page.goto(f"{base_url}/workitems/new/")
        authenticated_page.wait_for_load_state("domcontentloaded")
        authenticated_page.select_option("select[name='item_type']", value="task")
        authenticated_page.fill("input[name='title']", "Klientel-Klicktest")
        authenticated_page.select_option("select[name='priority']", value="normal")

        # Klientel auswählen (Autocomplete)
        autocomplete = authenticated_page.locator("input[placeholder='Pseudonym eingeben...']")
        if autocomplete.count() > 0:
            autocomplete.fill("Stern")
            authenticated_page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
            authenticated_page.locator("button:has-text('Stern-42')").click()

        authenticated_page.click("button:has-text('Speichern')")
        authenticated_page.wait_for_url(re.compile(r"/workitems/$"))

        # Zum Klientel-Detail navigieren
        authenticated_page.goto(f"{base_url}/clients/")
        authenticated_page.wait_for_load_state("domcontentloaded")
        authenticated_page.locator("a:has-text('Stern-42')").first.click()
        authenticated_page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

        # Aufgabe sollte klickbar sein
        workitem_link = authenticated_page.locator("a:has-text('Klientel-Klicktest')").first
        workitem_link.wait_for(state="visible", timeout=5000)
        workitem_link.click()

        authenticated_page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/$"))
        assert authenticated_page.locator("h1:has-text('Klientel-Klicktest')").is_visible()


class TestWorkItemCompletedAt:
    """Erledigungsdatum wird nach Abschluss einer Aufgabe angezeigt."""

    def test_completed_at_shown_after_done(self, authenticated_page, base_url, e2e_env):
        """Aufgabe erledigen → Erledigungsdatum wird auf Detail-Seite angezeigt."""
        import os
        import subprocess
        import sys

        page = authenticated_page

        # Erst einen offenen WorkItem per UI erstellen
        _create_open_workitem(page, base_url, "Erledigungstest")

        python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable

        # Diesen WorkItem per Django-Shell als erledigt markieren
        script = (
            "from core.models import WorkItem; "
            "from django.utils import timezone; "
            "wi = WorkItem.objects.filter(title='Erledigungstest', status='open').first(); "
            "wi.status = 'done'; "
            "wi.completed_at = timezone.now(); "
            "wi.save(); "
            "print(wi.pk)"
        )
        result = subprocess.run(
            [python, "src/manage.py", "shell", "-c", script],
            capture_output=True,
            text=True,
            env=e2e_env,
        )
        wi_pk = result.stdout.strip()
        assert wi_pk, f"WorkItem 'Erledigungstest' nicht gefunden: stderr={result.stderr}"

        page.goto(f"{base_url}/workitems/{wi_pk}/")
        page.wait_for_load_state("domcontentloaded")

        # Erledigungsdatum sollte sichtbar sein
        page.locator("text=Abgeschlossen am:").wait_for(state="visible", timeout=10000)
