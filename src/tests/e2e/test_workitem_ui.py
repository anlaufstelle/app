"""E2E-Tests: WorkItem-UI — Navigation, Inbox, Status, Badge, Klickbarkeit, Erledigungsdatum."""

import os
import re
import subprocess
import sys

import pytest

from tests.e2e._selectors import find_client_link

pytestmark = pytest.mark.e2e


def _python():
    return ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable


def _run_shell(code, e2e_env):
    """``manage.py shell --no-imports -c`` ausführen und stdout zurückgeben."""
    result = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", code],
        capture_output=True,
        text=True,
        env=e2e_env,
    )
    assert result.returncode == 0, f"shell failed: {result.stderr}\n{result.stdout}"
    return result.stdout


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
        assert page.locator("section h2", has_text="Offen").first.is_visible()
        assert page.locator("section h2", has_text="In Bearbeitung").first.is_visible()
        assert page.locator("section h2", has_text="Kürzlich erledigt").first.is_visible()

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
        find_client_link(authenticated_page, "Stern-42").click()
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
            [python, "src/manage.py", "shell", "--no-imports", "-c", script],
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


def _open_workitem_detail(page, base_url, title):
    """Aufgabe per UI anlegen und ihre Detail-Seite öffnen.

    Der Titel ist pro Test eindeutig, deshalb genügt ein exakter
    Text-Locator ohne ``.first`` (stabil gegen Seed-Drift, Refs #922).
    """
    _create_open_workitem(page, base_url, title)
    page.get_by_role("link", name=title, exact=True).click()
    page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/$"))
    page.wait_for_load_state("domcontentloaded")


class TestWorkItemDetailActions:
    """Detailansicht-Aktionen für offene Aufgaben (Refs #1130).

    Verständlichere Benennung (`Aufgabe übernehmen`), direkter Erledigen-Pfad
    (`Als erledigt markieren`) und abgesichertes Verwerfen mit Bestätigung
    (`Als nicht relevant schließen`). Abgeleitet aus der manuellen
    Playwright-Verifikation.
    """

    def test_open_detail_offers_three_named_actions(self, authenticated_page, base_url):
        page = authenticated_page
        _open_workitem_detail(page, base_url, "Detail-Aktionen-Test")
        assert page.locator("button:has-text('Aufgabe übernehmen')").is_visible()
        assert page.locator("button:has-text('Als erledigt markieren')").is_visible()
        assert page.locator("button:has-text('Als nicht relevant schließen')").is_visible()

    def test_mark_as_done_directly_from_open(self, authenticated_page, base_url):
        """`Als erledigt markieren` setzt offene Aufgabe direkt auf Erledigt
        (ohne Zwischenstatus) und zeigt das Abschlussdatum."""
        page = authenticated_page
        _open_workitem_detail(page, base_url, "Direkt-Erledigt-Test")
        page.click("button:has-text('Als erledigt markieren')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/$"))
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("text=Erledigt").first.is_visible()
        page.locator("text=Abgeschlossen am:").wait_for(state="visible", timeout=10000)

    def test_dismiss_requires_confirmation_and_can_be_cancelled(self, authenticated_page, base_url):
        """Abbruch des Bestätigungsdialogs lässt den Status auf Offen."""
        page = authenticated_page
        _open_workitem_detail(page, base_url, "Verwerfen-Abbruch-Test")
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.click("button:has-text('Als nicht relevant schließen')")
        # Kein Statuswechsel — die offenen Aktionen bleiben sichtbar.
        page.wait_for_timeout(500)
        assert page.locator("button:has-text('Als nicht relevant schließen')").is_visible()

    def test_dismiss_confirmed_sets_status_to_verworfen(self, authenticated_page, base_url):
        """Bestätigtes Verwerfen setzt den Status auf Verworfen und entfernt
        die offenen Aktionen."""
        page = authenticated_page
        _open_workitem_detail(page, base_url, "Verwerfen-Bestaetigt-Test")
        page.once("dialog", lambda dialog: dialog.accept())
        page.click("button:has-text('Als nicht relevant schließen')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/$"))
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("text=Verworfen").first.is_visible()
        assert page.locator("button:has-text('Als nicht relevant schließen')").count() == 0


# Eindeutiger Titel-Präfix, damit die Fokusbox-Items von Seed-/Fremdtest-Daten
# isoliert bleiben und gezielt wieder gelöscht werden können.
_ZFB = "ZFB-Fokus"


def _seed_focus_box_items(e2e_env):
    """Legt deterministische Aufgaben für jede Druckstufe an (Refs #1128).

    Eine überfällige, eine heute fällige und eine in Bearbeitung befindliche
    Aufgabe (drei sichtbare Gruppen) plus mehrere offene Normal-Aufgaben ohne
    Frist, die keinen Handlungsdruck erzeugen und nur den Transparenz-Zähler
    erhöhen. ``due_date`` in der Vergangenheit ist über die Shell möglich, über
    das UI-Formular dagegen bewusst gesperrt.
    """
    code = (
        "from core.models import WorkItem, Facility, User; "
        "from datetime import timedelta; "
        "from django.utils import timezone; "
        "f = Facility.objects.first(); "
        "u = User.objects.get(username='admin'); "
        "today = timezone.localdate(); "
        f"WorkItem.objects.filter(facility=f, title__startswith='{_ZFB}').delete(); "
        "WorkItem.objects.create(facility=f, created_by=u, "
        f"  title='{_ZFB} überfällig', status='open', priority='normal', "
        "  due_date=today - timedelta(days=2)); "
        "WorkItem.objects.create(facility=f, created_by=u, "
        f"  title='{_ZFB} heute', status='open', priority='normal', due_date=today); "
        "WorkItem.objects.create(facility=f, created_by=u, "
        f"  title='{_ZFB} laeuft', status='in_progress', priority='normal', "
        "  due_date=today + timedelta(days=30)); "
        "[WorkItem.objects.create(facility=f, created_by=u, "
        f"  title='{_ZFB} ohne Druck %d' % i, status='open', priority='normal') "
        "  for i in range(6)]; "
        f"print('OPEN', WorkItem.objects.filter(facility=f, title__startswith='{_ZFB}').count())"
    )
    out = _run_shell(code, e2e_env)
    assert "OPEN" in out, out


def _cleanup_focus_box_items(e2e_env):
    code = (
        "from core.models import WorkItem, Facility; "
        "f = Facility.objects.first(); "
        f"WorkItem.objects.filter(facility=f, title__startswith='{_ZFB}').delete(); "
        "print('CLEAN')"
    )
    _run_shell(code, e2e_env)


class TestZeitstromFocusBox:
    """Zeitstrom-Sidebar als Team-Fokusbox für Handlungsbedarf (Refs #1128)."""

    @pytest.mark.smoke
    def test_focus_box_groups_and_transparency(self, authenticated_page, base_url, e2e_env):
        """Gruppierte Anzeige (überfällig/heute/in Bearbeitung), Zähler und Übersichtslink."""
        _seed_focus_box_items(e2e_env)
        try:
            page = authenticated_page
            page.set_viewport_size({"width": 1400, "height": 1000})
            page.goto(f"{base_url}/")
            page.wait_for_load_state("domcontentloaded")

            box = page.locator("[data-testid='zeitstrom-focus-box']")
            box.wait_for(state="visible", timeout=10000)

            # Benennung macht den Zweck klar (h2 ist per CSS uppercase).
            assert "team-aufgaben mit handlungsbedarf" in box.inner_text().lower()

            # Gruppen mit Inhalt erscheinen, nach Handlungsdruck benannt.
            assert box.locator("[data-focus-group='overdue']").is_visible()
            assert box.locator("[data-focus-group='today']").is_visible()
            assert box.locator("[data-focus-group='in_progress']").is_visible()

            # Aufgaben landen in der erwarteten Gruppe.
            overdue = box.locator("[data-focus-group='overdue']")
            assert overdue.locator(f"text={_ZFB} überfällig").is_visible()
            today_group = box.locator("[data-focus-group='today']")
            assert today_group.locator(f"text={_ZFB} heute").is_visible()
            in_progress = box.locator("[data-focus-group='in_progress']")
            assert in_progress.locator(f"text={_ZFB} laeuft").is_visible()

            # Begrenzung ist transparent: Zähler weist auf weitere Aufgaben hin.
            more = box.locator("[data-testid='focus-box-more']")
            assert more.is_visible()
            assert re.search(r"\d+ von \d+ offenen Aufgaben angezeigt", more.inner_text())

            # Offene Normal-Aufgaben ohne Frist erzeugen keinen Druck → nicht in der Box.
            assert box.locator(f"text={_ZFB} ohne Druck 0").count() == 0
        finally:
            _cleanup_focus_box_items(e2e_env)

    @pytest.mark.smoke
    def test_focus_box_overview_link_navigates(self, authenticated_page, base_url):
        """Link „Zur Aufgabenübersicht" führt in die vollständige Aufgabenliste."""
        page = authenticated_page
        page.set_viewport_size({"width": 1400, "height": 1000})
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        link = page.locator("[data-testid='focus-box-overview-link']")
        link.wait_for(state="visible", timeout=10000)
        assert "Zur Aufgabenübersicht" in link.inner_text()
        link.click()
        page.wait_for_url(re.compile(r"/workitems/$"))
        assert page.locator("h1").inner_text() == "Aufgaben"
