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
        # Refs #1149: Offen + In Bearbeitung sind eigene Spalten-Sektionen, der
        # erledigt-Rückblick liegt als aufklappbarer <details> darunter und führt
        # den Zeitraum (letzte 7 Tage) klar in der Beschriftung.
        assert page.locator("section h2", has_text="Offen").first.is_visible()
        assert page.locator("section h2", has_text="In Bearbeitung").first.is_visible()
        assert page.locator(
            "details[data-testid='recent-done'] summary", has_text="Erledigt in den letzten 7 Tagen"
        ).first.is_visible()

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

        # "Übernehmen" klickt → Status wechselt zu In Bearbeitung (Refs #1146)
        accept_btn = page.locator("button:has-text('Übernehmen')").first
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

        # Zum Klientel-Detail navigieren — mit Suchfilter statt ungefilterter
        # Liste: die ist mit 25/Seite paginiert und nach Pseudonym sortiert;
        # im Voll-Lauf schieben die von Offline-Tests angelegten
        # E2E-*-Klienten den Seed-Klienten Stern-42 sonst auf Seite 2.
        authenticated_page.goto(f"{base_url}/clients/?q=Stern-42")
        authenticated_page.wait_for_load_state("domcontentloaded")
        find_client_link(authenticated_page, "Stern-42").click()
        authenticated_page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

        # Aufgabe sollte klickbar sein
        workitem_link = authenticated_page.locator("a:has-text('Klientel-Klicktest')").first
        workitem_link.wait_for(state="visible", timeout=5000)
        workitem_link.click()

        authenticated_page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/$"))
        assert authenticated_page.locator("h1:has-text('Klientel-Klicktest')").is_visible()


class TestWorkItemListDateLabeling:
    """Refs #1133: In der Aufgabenliste ist das Datum eindeutig die Frist
    ('Fällig:' + Tooltip 'Zu erledigen bis'); das Erstellungsdatum erscheint
    nicht mehr in der Übersicht, bleibt aber in der Einzelansicht erhalten.

    Abgeleitet aus der manuellen Playwright-Verifikation gegen den E2E-Server.
    """

    _TITLE = "Frist-Label-Test #1133"
    # Eindeutiges, klar abgegrenztes Erstelldatum (lokalisiert: '14. März 2021').
    _BACKDATED = "14. März 2021"

    def _create_and_backdate(self, page, base_url, e2e_env):
        """Offene Aufgabe mit ferner Frist anlegen und created_at zurückdatieren."""
        page.goto(f"{base_url}/workitems/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option("select[name='item_type']", value="task")
        page.fill("input[name='title']", self._TITLE)
        page.select_option("select[name='priority']", value="normal")
        # Frist 30 Tage in der Zukunft → relative Anzeige ohne Schlüsselwort.
        future = page.evaluate(
            "() => { const d = new Date(); d.setDate(d.getDate() + 30); return d.toISOString().slice(0, 10); }"
        )
        page.fill("input[name='due_date']", future)
        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/workitems/$"))

        code = (
            "from core.models import WorkItem; "
            "from datetime import datetime, timezone; "
            f"wi = WorkItem.objects.filter(title='{self._TITLE}').first(); "
            "wi and WorkItem.objects.filter(pk=wi.pk).update("
            "  created_at=datetime(2021, 3, 14, 9, 5, tzinfo=timezone.utc)); "
            "print(wi.pk if wi else 'MISSING')"
        )
        wi_pk = _run_shell(code, e2e_env).strip().splitlines()[-1]
        assert wi_pk and wi_pk != "MISSING", "WorkItem nicht angelegt/auffindbar"
        return wi_pk

    @pytest.mark.smoke
    def test_list_labels_due_date_and_hides_creation_date(self, authenticated_page, base_url, e2e_env):
        page = authenticated_page
        self._create_and_backdate(page, base_url, e2e_env)

        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        row = page.locator("[id^='workitem-']", has_text=self._TITLE).first
        row.wait_for(state="visible", timeout=10000)

        # Frist eindeutig benannt: sichtbares Präfix + Tooltip an der Zeile.
        assert "Fällig:" in row.inner_text()
        assert row.locator("[title='Zu erledigen bis']").count() > 0

        # Erstellungsdatum erscheint in der Übersicht NICHT mehr.
        assert self._BACKDATED not in row.inner_text()

    def test_detail_still_shows_creation_date(self, authenticated_page, base_url, e2e_env):
        page = authenticated_page
        wi_pk = self._create_and_backdate(page, base_url, e2e_env)

        page.goto(f"{base_url}/workitems/{wi_pk}/")
        page.wait_for_load_state("domcontentloaded")

        body = page.locator("main").inner_text()
        # Label wird per CSS in Großbuchstaben dargestellt → case-insensitiv prüfen.
        assert "erstellt am" in body.lower()
        assert self._BACKDATED in body


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
        (ohne Zwischenstatus) und zeigt das Abschlussdatum.

        Refs #1147: Vor dem Statuswechsel auf erledigt erscheint eine
        Bestätigung — der bestätigte Pfad muss weiterhin auf Erledigt führen.
        """
        page = authenticated_page
        _open_workitem_detail(page, base_url, "Direkt-Erledigt-Test")
        page.once("dialog", lambda dialog: dialog.accept())
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


# Eindeutiger Titel-Präfix für die Bulk-Status-Konsistenz-Aufgaben.
_BULK = "BULK-Status #1134"


def _seed_done_items_for_admin(e2e_env, count=2):
    """Legt erledigte, der eingeloggten ``admin`` zugewiesene Aufgaben an.

    Zuweisung an ``admin`` (statt unassigned) ist wichtig: nur so sind die
    Items in der Default-Sicht *und* nach dem Wechsel auf In Bearbeitung in der
    (scoped) In-Bearbeitung-Liste sichtbar — sonst würde der Konsistenz-Check
    durch das Scoping verfälscht.

    Refs #1149: Vorab werden *alle* erledigten/verworfenen Aufgaben der Facility
    entfernt. „Kürzlich erledigt" zeigt jetzt nur die letzten fünf direkt an
    (Rest hinter einem zweiten Aufklapp-Bereich); ohne das Leeren könnten von
    früheren Tests übrig gebliebene erledigte Aufgaben die frisch angelegten aus
    der sichtbaren Vorschau verdrängen.
    """
    code = (
        "from core.models import WorkItem, Facility, User; "
        "from django.utils import timezone; "
        "u = User.objects.get(username='admin'); "
        "f = u.facility; "
        "WorkItem.objects.filter(facility=f, status__in=['done', 'dismissed']).delete(); "
        "[WorkItem.objects.create(facility=f, created_by=u, assigned_to=u, "
        f"  title='{_BULK} %d' % i, item_type='task', priority='normal', "
        "  status='done', completed_at=timezone.now()) "
        f"  for i in range({count})]; "
        f"print('SEEDED', WorkItem.objects.filter(facility=f, title__startswith='{_BULK}').count())"
    )
    out = _run_shell(code, e2e_env)
    assert "SEEDED" in out, out


def _cleanup_bulk_items(e2e_env):
    code = (
        "from core.models import WorkItem, Facility; "
        "f = Facility.objects.first(); "
        f"WorkItem.objects.filter(facility=f, title__startswith='{_BULK}').delete(); "
        "print('CLEAN')"
    )
    _run_shell(code, e2e_env)


class TestWorkItemBulkStatusConsistency:
    """Refs #1134: Bulk-Statuswechsel Erledigt → In Bearbeitung entfernt die
    Erledigt-Markierung konsistent in Liste und Detailansicht.

    Abgeleitet aus der manuellen Playwright-Verifikation gegen den E2E-Server.
    """

    @pytest.mark.smoke
    def test_bulk_done_to_in_progress_removes_done_marking(self, authenticated_page, base_url, e2e_env):
        _seed_done_items_for_admin(e2e_env, count=2)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")

            # Refs #1149: Die erledigten Aufgaben liegen im aufklappbaren Bereich
            # "Erledigt in den letzten 7 Tagen", der standardmäßig eingeklappt
            # ist — zum Auswählen erst aufklappen.
            done_section = page.locator("details[data-testid='recent-done']")
            _open_recent_done(page)
            row0 = done_section.locator("[id^='workitem-']", has_text=f"{_BULK} 0").first
            row1 = done_section.locator("[id^='workitem-']", has_text=f"{_BULK} 1").first
            row0.wait_for(state="visible", timeout=10000)
            row1.wait_for(state="visible", timeout=10000)
            row0.get_by_role("checkbox", name="Auswählen").check()
            row1.get_by_role("checkbox", name="Auswählen").check()

            # Bulk: Status → In Bearbeitung anwenden.
            page.select_option("#bulk-status", value="in_progress")
            page.locator("#bulk-status ~ button[type='submit']").click()

            page.wait_for_url(re.compile(r"/workitems/"))
            page.wait_for_load_state("domcontentloaded")
            assert page.locator("[role='alert']:has-text('Aufgaben aktualisiert')").first.is_visible()

            # Beide Aufgaben stehen jetzt in "In Bearbeitung" — nicht mehr unter
            # "Kürzlich erledigt".
            in_progress = page.locator("section", has=page.locator("h2", has_text="In Bearbeitung"))
            moved0 = in_progress.locator("[id^='workitem-']", has_text=f"{_BULK} 0").first
            moved0.wait_for(state="visible", timeout=10000)
            assert in_progress.locator("[id^='workitem-']", has_text=f"{_BULK} 1").first.is_visible()

            done_after = page.locator("details[data-testid='recent-done']")
            assert done_after.locator("[id^='workitem-']", has_text=f"{_BULK} 0").count() == 0
            assert done_after.locator("[id^='workitem-']", has_text=f"{_BULK} 1").count() == 0

            # In der In-Bearbeitung-Zeile gibt es keinen "Erledigt"-Status-Badge
            # mehr, sondern die Aktions-Buttons (Erledigt-Button + Zurücksetzen).
            assert moved0.get_by_role("button", name="Zurücksetzen").is_visible()

            # Detailansicht zeigt denselben Status und kein Abschlussdatum mehr.
            moved0.get_by_role("link", name=f"{_BULK} 0").click()
            page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/$"))
            page.wait_for_load_state("domcontentloaded")
            detail = page.locator("main").inner_text()
            assert "In Bearbeitung" in detail
            assert "Abgeschlossen am" not in detail
        finally:
            _cleanup_bulk_items(e2e_env)


# Eindeutiger Titel-Präfix für die "Alle"-Sicht-Regression (Refs #1134 Nachtest).
_BULK_FOREIGN = "BULK-Fremd #1134"


def _seed_done_item_assigned_to_other(e2e_env):
    """Legt eine erledigte Aufgabe an, die *einer anderen* Person zugewiesen ist.

    Refs #1134 (Nachtest): Solche fremd-zugewiesenen erledigten Aufgaben sind nur
    in der expliziten "Alle"-Sicht sichtbar/auswählbar — in der Default-Sicht
    ("Mir & unzugewiesene") blendet das Scoping sie aus. Genau hier brach der
    Bulk-Statuswechsel: Der Redirect verwarf die "Alle"-Sicht (leerer
    ``assigned_to``-Filter) und warf die Nutzerin in die Default-Eingrenzung
    zurück — die gerade auf In Bearbeitung gesetzte Aufgabe verschwand dort, statt
    sichtbar umzuziehen.
    """
    code = (
        "from core.models import WorkItem, User; "
        "from django.utils import timezone; "
        "admin = User.objects.get(username='admin'); "
        "other = User.objects.exclude(pk=admin.pk).filter(facility=admin.facility, is_active=True).first(); "
        "f = admin.facility; "
        # Refs #1149: erledigte Aufgaben leeren, damit das eine fremd-zugewiesene
        # Item in der „Alle"-Sicht in der Fünfer-Vorschau auftaucht (nicht im
        # zweiten Aufklapp-Bereich verschwindet).
        "WorkItem.objects.filter(facility=f, status__in=['done', 'dismissed']).delete(); "
        "WorkItem.objects.create(facility=f, created_by=admin, assigned_to=other, "
        f"  title='{_BULK_FOREIGN} 0', item_type='task', priority='normal', "
        "  status='done', completed_at=timezone.now()); "
        f"print('SEEDED', WorkItem.objects.filter(facility=f, title__startswith='{_BULK_FOREIGN}').count())"
    )
    out = _run_shell(code, e2e_env)
    assert "SEEDED 1" in out, out


def _cleanup_foreign_bulk_items(e2e_env):
    code = (
        "from core.models import WorkItem, Facility; "
        "f = Facility.objects.first(); "
        f"WorkItem.objects.filter(facility=f, title__startswith='{_BULK_FOREIGN}').delete(); "
        "print('CLEAN')"
    )
    _run_shell(code, e2e_env)


class TestWorkItemBulkStatusConsistencyAllView:
    """Refs #1134 (Nachtest): Bulk-Statuswechsel in der expliziten "Alle"-Sicht
    erhält die Sicht und zeigt die Aufgabe konsistent mit ihrem neuen Status.

    Der erste Fix (a64f669) scopte nur die Default-Sicht. In der "Alle"-Sicht
    (fremd-zugewiesene erledigte Aufgaben sichtbar) verwarf der Redirect aber den
    leeren ``assigned_to``-Filter und fiel auf die Default-Eingrenzung zurück —
    die umgezogene Aufgabe verschwand, Liste und Status liefen weiter auseinander.
    Abgeleitet aus der manuellen Playwright-Verifikation gegen den E2E-Server.
    """

    @pytest.mark.smoke
    def test_bulk_done_to_in_progress_in_all_view_keeps_item_visible(self, authenticated_page, base_url, e2e_env):
        _seed_done_item_assigned_to_other(e2e_env)
        try:
            page = authenticated_page
            # Explizite "Alle"-Sicht: nur hier ist die fremd-zugewiesene
            # erledigte Aufgabe sichtbar und auswählbar.
            page.goto(f"{base_url}/workitems/?assigned_to=")
            page.wait_for_load_state("domcontentloaded")

            # Refs #1149: erledigt-Bereich erst aufklappen, dann auswählen.
            done_section = page.locator("details[data-testid='recent-done']")
            _open_recent_done(page)
            row = done_section.locator("[id^='workitem-']", has_text=f"{_BULK_FOREIGN} 0").first
            row.wait_for(state="visible", timeout=10000)
            row.get_by_role("checkbox", name="Auswählen").check()

            # Bulk: Status → In Bearbeitung anwenden.
            page.select_option("#bulk-status", value="in_progress")
            page.locator("#bulk-status ~ button[type='submit']").click()

            # Der Redirect erhält die "Alle"-Sicht (assigned_to-Param explizit
            # vorhanden) — nicht der nackte Inbox-Pfad mit Default-Eingrenzung.
            page.wait_for_url(re.compile(r"/workitems/\?.*assigned_to="))
            page.wait_for_load_state("domcontentloaded")
            assert page.locator("[role='alert']:has-text('Aufgaben aktualisiert')").first.is_visible()

            # Die Aufgabe steht jetzt sichtbar in "In Bearbeitung" — nicht mehr
            # unter "Kürzlich erledigt" und nicht verschwunden.
            in_progress = page.locator("section", has=page.locator("h2", has_text="In Bearbeitung"))
            moved = in_progress.locator("[id^='workitem-']", has_text=f"{_BULK_FOREIGN} 0").first
            moved.wait_for(state="visible", timeout=10000)

            done_after = page.locator("details[data-testid='recent-done']")
            assert done_after.locator("[id^='workitem-']", has_text=f"{_BULK_FOREIGN} 0").count() == 0
        finally:
            _cleanup_foreign_bulk_items(e2e_env)


# Eindeutiger Titel-Präfix für die Erledigt-Bestätigungs-Aufgaben (Refs #1147).
_DONE_CONFIRM = "DoneConfirm #1147"


def _seed_items_for_done_confirm(e2e_env, status, count=1):
    """Legt der eingeloggten ``admin`` zugewiesene Aufgaben mit ``status`` an.

    Zuweisung an ``admin`` hält die Items in der Default-Sicht („Mir
    zugewiesen") sichtbar und auswählbar. Liefert nichts zurück — die Tests
    greifen die Items über den eindeutigen Titel-Präfix ab.
    """
    code = (
        "from core.models import WorkItem, User; "
        "u = User.objects.get(username='admin'); "
        "f = u.facility; "
        f"WorkItem.objects.filter(facility=f, title__startswith='{_DONE_CONFIRM}').delete(); "
        "[WorkItem.objects.create(facility=f, created_by=u, assigned_to=u, "
        f"  title='{_DONE_CONFIRM} %d' % i, item_type='task', priority='normal', "
        f"  status='{status}') for i in range({count})]; "
        f"print('SEEDED', WorkItem.objects.filter(facility=f, title__startswith='{_DONE_CONFIRM}').count())"
    )
    out = _run_shell(code, e2e_env)
    assert "SEEDED" in out, out


def _cleanup_done_confirm_items(e2e_env):
    code = (
        "from core.models import WorkItem, Facility; "
        "f = Facility.objects.first(); "
        f"WorkItem.objects.filter(facility=f, title__startswith='{_DONE_CONFIRM}').delete(); "
        "print('CLEAN')"
    )
    _run_shell(code, e2e_env)


def _status_of_done_confirm_item(e2e_env, index=0):
    """Liest den aktuellen Status eines Erledigt-Bestätigungs-Items aus der DB."""
    code = (
        "from core.models import WorkItem, Facility; "
        "f = Facility.objects.first(); "
        f"wi = WorkItem.objects.get(facility=f, title='{_DONE_CONFIRM} {index}'); "
        "print('STATUS', wi.status)"
    )
    out = _run_shell(code, e2e_env)
    line = next((line_ for line_ in out.splitlines() if line_.startswith("STATUS")), "")
    return line.replace("STATUS", "").strip()


class TestWorkItemDoneConfirmation:
    """Refs #1147: Bestätigung vor „Als erledigt markieren" — konsistent in
    Übersicht (Tabelle), Bulk-Aktion und Detailansicht.

    Abgeleitet aus der manuellen Playwright-Verifikation gegen den E2E-Server
    (Cancel/Accept je Kontext, Gegenprobe für Nicht-Erledigt-Bulk).
    """

    def test_detail_done_confirmation_can_be_cancelled(self, authenticated_page, base_url):
        """Detailansicht: Abbruch des Erledigt-Dialogs lässt den Status auf
        Offen — die offenen Aktionen bleiben sichtbar."""
        page = authenticated_page
        _open_workitem_detail(page, base_url, "Detail-Erledigt-Abbruch")
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.click("button:has-text('Als erledigt markieren')")
        page.wait_for_timeout(500)
        # Kein Statuswechsel: der Erledigen-Button ist weiter da.
        assert page.locator("button:has-text('Als erledigt markieren')").is_visible()

    def test_overview_done_confirmation_cancel_keeps_in_progress(self, authenticated_page, base_url, e2e_env):
        """Übersicht/Tabelle: der Erledigt-Button einer In-Bearbeitung-Aufgabe
        fragt nach (hx-confirm); Abbruch lässt den Status auf In Bearbeitung."""
        _seed_items_for_done_confirm(e2e_env, status="in_progress", count=1)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            row = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 0").first
            row.wait_for(state="visible", timeout=10000)
            page.once("dialog", lambda dialog: dialog.dismiss())
            row.get_by_role("button", name="Erledigt").click()
            page.wait_for_timeout(500)
            assert _status_of_done_confirm_item(e2e_env, 0) == "in_progress"
        finally:
            _cleanup_done_confirm_items(e2e_env)

    def test_overview_done_confirmation_accept_sets_done(self, authenticated_page, base_url, e2e_env):
        """Übersicht/Tabelle: bestätigter Erledigt-Dialog setzt den Status auf
        erledigt."""
        _seed_items_for_done_confirm(e2e_env, status="in_progress", count=1)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            row = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 0").first
            row.wait_for(state="visible", timeout=10000)
            page.once("dialog", lambda dialog: dialog.accept())
            row.get_by_role("button", name="Erledigt").click()
            # Auf den HTMX-Swap zur Erledigt-Markierung warten, dann DB prüfen.
            page.wait_for_timeout(800)
            assert _status_of_done_confirm_item(e2e_env, 0) == "done"
        finally:
            _cleanup_done_confirm_items(e2e_env)

    def test_bulk_done_confirmation_accept_sets_done(self, authenticated_page, base_url, e2e_env):
        """Bulk: Zielstatus erledigt fragt vor dem Speichern nach (Dialog mit
        Mehrzahl-Wortlaut); bestätigt werden alle ausgewählten Aufgaben auf
        erledigt gesetzt."""
        _seed_items_for_done_confirm(e2e_env, status="open", count=2)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            row0 = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 0").first
            row1 = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 1").first
            row0.wait_for(state="visible", timeout=10000)
            row1.wait_for(state="visible", timeout=10000)
            row0.get_by_role("checkbox", name="Auswählen").check()
            row1.get_by_role("checkbox", name="Auswählen").check()

            page.select_option("#bulk-status", value="done")
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#bulk-status ~ button[type='submit']").click()

            page.wait_for_url(re.compile(r"/workitems/"))
            page.wait_for_load_state("domcontentloaded")
            assert page.locator("[role='alert']:has-text('Aufgaben aktualisiert')").first.is_visible()
            assert _status_of_done_confirm_item(e2e_env, 0) == "done"
            assert _status_of_done_confirm_item(e2e_env, 1) == "done"
        finally:
            _cleanup_done_confirm_items(e2e_env)

    def test_bulk_done_confirmation_cancel_keeps_status(self, authenticated_page, base_url, e2e_env):
        """Bulk: Abbruch des Erledigt-Dialogs speichert nichts — die Auswahl
        bleibt unverändert (Status weiter offen)."""
        _seed_items_for_done_confirm(e2e_env, status="open", count=2)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            row0 = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 0").first
            row1 = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 1").first
            row0.wait_for(state="visible", timeout=10000)
            row1.wait_for(state="visible", timeout=10000)
            row0.get_by_role("checkbox", name="Auswählen").check()
            row1.get_by_role("checkbox", name="Auswählen").check()

            page.select_option("#bulk-status", value="done")
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.locator("#bulk-status ~ button[type='submit']").click()
            page.wait_for_timeout(500)
            assert _status_of_done_confirm_item(e2e_env, 0) == "open"
            assert _status_of_done_confirm_item(e2e_env, 1) == "open"
        finally:
            _cleanup_done_confirm_items(e2e_env)

    def test_bulk_non_done_status_has_no_confirmation(self, authenticated_page, base_url, e2e_env):
        """Gegenprobe: ein anderer Bulk-Zielstatus (In Bearbeitung) löst KEINE
        Bestätigung aus und wird direkt angewendet.

        Wird wider Erwarten ein Dialog gezeigt, schlägt der Test fehl: ein
        akzeptierender Handler würde ihn maskieren, daher dismiss — bei
        unerwartetem Dialog bliebe der Statuswechsel dann aus."""
        _seed_items_for_done_confirm(e2e_env, status="open", count=1)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            row0 = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 0").first
            row0.wait_for(state="visible", timeout=10000)
            row0.get_by_role("checkbox", name="Auswählen").check()

            page.select_option("#bulk-status", value="in_progress")
            # Kein Dialog erwartet: ein dismiss-Handler würde einen (fälschlich
            # gezeigten) Dialog abbrechen und den Statuswechsel verhindern.
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.locator("#bulk-status ~ button[type='submit']").click()

            page.wait_for_url(re.compile(r"/workitems/"))
            page.wait_for_load_state("domcontentloaded")
            assert _status_of_done_confirm_item(e2e_env, 0) == "in_progress"
        finally:
            _cleanup_done_confirm_items(e2e_env)


class TestWorkItemInboxRowActions:
    """Refs #1146: Die Tabellenaktionen einer offenen Aufgabe in der Übersicht
    sind mit der Einzelansicht (#1130/#1147) vereinheitlicht — „Übernehmen",
    „Als erledigt markieren" (direkt, mit Bestätigung) und „Als nicht relevant
    schließen" (abgesichert) statt der alten „Annehmen"/„Verwerfen".

    Abgeleitet aus der manuellen Playwright-Verifikation gegen den E2E-Server
    (Buttonbeschriftung, Accept beim Erledigen, Cancel/Accept beim Schließen).
    """

    def test_open_row_offers_renamed_actions(self, authenticated_page, base_url, e2e_env):
        """Die offene Zeile zeigt die drei vereinheitlichten Buttons; die alten
        Begriffe „Annehmen"/„Verwerfen" tauchen nicht mehr auf."""
        _seed_items_for_done_confirm(e2e_env, status="open", count=1)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            row = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 0").first
            row.wait_for(state="visible", timeout=10000)
            assert row.get_by_role("button", name="Übernehmen").is_visible()
            assert row.get_by_role("button", name="Als erledigt markieren").is_visible()
            assert row.get_by_role("button", name="Als nicht relevant schließen").is_visible()
            assert row.get_by_role("button", name="Annehmen").count() == 0
            assert row.get_by_role("button", name="Verwerfen").count() == 0
        finally:
            _cleanup_done_confirm_items(e2e_env)

    def test_open_row_mark_done_confirmation_accept_sets_done(self, authenticated_page, base_url, e2e_env):
        """Übersicht: „Als erledigt markieren" einer offenen Aufgabe fragt nach
        (hx-confirm); bestätigt wird der Status direkt auf erledigt gesetzt —
        ohne Zwischenschritt über In Bearbeitung."""
        _seed_items_for_done_confirm(e2e_env, status="open", count=1)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            row = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 0").first
            row.wait_for(state="visible", timeout=10000)
            page.once("dialog", lambda dialog: dialog.accept())
            row.get_by_role("button", name="Als erledigt markieren").click()
            page.wait_for_timeout(800)
            assert _status_of_done_confirm_item(e2e_env, 0) == "done"
        finally:
            _cleanup_done_confirm_items(e2e_env)

    def test_open_row_dismiss_confirmation_cancel_keeps_open(self, authenticated_page, base_url, e2e_env):
        """Übersicht: Abbruch des „Als nicht relevant schließen"-Dialogs lässt
        die Aufgabe unverändert offen."""
        _seed_items_for_done_confirm(e2e_env, status="open", count=1)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            row = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 0").first
            row.wait_for(state="visible", timeout=10000)
            page.once("dialog", lambda dialog: dialog.dismiss())
            row.get_by_role("button", name="Als nicht relevant schließen").click()
            page.wait_for_timeout(500)
            assert _status_of_done_confirm_item(e2e_env, 0) == "open"
        finally:
            _cleanup_done_confirm_items(e2e_env)

    def test_open_row_dismiss_confirmation_accept_sets_dismissed(self, authenticated_page, base_url, e2e_env):
        """Übersicht: bestätigtes „Als nicht relevant schließen" setzt den Status
        auf verworfen."""
        _seed_items_for_done_confirm(e2e_env, status="open", count=1)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            row = page.locator("[id^='workitem-']", has_text=f"{_DONE_CONFIRM} 0").first
            row.wait_for(state="visible", timeout=10000)
            page.once("dialog", lambda dialog: dialog.accept())
            row.get_by_role("button", name="Als nicht relevant schließen").click()
            page.wait_for_timeout(800)
            assert _status_of_done_confirm_item(e2e_env, 0) == "dismissed"
        finally:
            _cleanup_done_confirm_items(e2e_env)


# Refs #1149: Zwei-Spalten-Layout + aufklappbarer „Kürzlich erledigt"-Bereich.
_LAYOUT = "LAYOUT #1149"

_RECENT_DONE = "details[data-testid='recent-done']"


def _open_recent_done(page):
    """Klappt den „Kürzlich erledigt"-Bereich auf und stellt sicher, dass er
    offen *bleibt*.

    Auf ressourcenknappen CI-Containern kann ein einzelner Summary-Klick unter
    Last gelegentlich „durchrutschen" oder doppelt auslösen (toggle auf-zu). Statt
    blind einmal zu klicken, wird hier bis zu dreimal geklickt, bis das
    ``open``-Attribut gesetzt ist — ein echter Nutzer-Klick, nur idempotent
    abgesichert.
    """
    summary = page.locator(f"{_RECENT_DONE} > summary")
    details = page.locator(_RECENT_DONE)
    for _ in range(3):
        if details.get_attribute("open") is not None:
            return
        summary.click()
        page.wait_for_timeout(150)
    assert details.get_attribute("open") is not None, "Kürzlich-erledigt-Bereich ließ sich nicht aufklappen"


def _close_recent_done(page):
    """Gegenstück zu :func:`_open_recent_done` — klappt den Bereich sicher zu."""
    summary = page.locator(f"{_RECENT_DONE} > summary")
    details = page.locator(_RECENT_DONE)
    for _ in range(3):
        if details.get_attribute("open") is None:
            return
        summary.click()
        page.wait_for_timeout(150)
    assert details.get_attribute("open") is None, "Kürzlich-erledigt-Bereich ließ sich nicht einklappen"


def _seed_done_unassigned_for_admin(e2e_env, count):
    """Legt ``count`` erledigte, *nicht zugewiesene* Aufgaben in der Facility der
    eingeloggten ``admin`` an — so sind sie in der Default-Sicht sichtbar, ohne
    die Zahl der aktiven (offen/in Bearbeitung) Aufgaben zu verändern.

    Refs #1149: Vorab werden *alle* erledigten/verworfenen Aufgaben der Facility
    gelöscht, damit die Vorschau-/Aufklapp-Aufteilung deterministisch ist und
    ausschließlich die hier angelegten Aufgaben enthält (frühere Tests in
    derselben Session hinterlassen sonst erledigte Aufgaben in der Sitzungs-DB).
    """
    code = (
        "from core.models import WorkItem, User; "
        "from django.utils import timezone; "
        "u = User.objects.get(username='admin'); "
        "f = u.facility; "
        "WorkItem.objects.filter(facility=f, status__in=['done', 'dismissed']).delete(); "
        "[WorkItem.objects.create(facility=f, created_by=u, "
        f"  title='{_LAYOUT} %d' % i, item_type='task', priority='normal', "
        "  status='done', completed_at=timezone.now()) "
        f"  for i in range({count})]; "
        f"print('SEEDED', WorkItem.objects.filter(facility=f, title__startswith='{_LAYOUT}').count())"
    )
    out = _run_shell(code, e2e_env)
    assert "SEEDED" in out, out


def _cleanup_layout_items(e2e_env):
    code = (
        "from core.models import WorkItem, Facility; "
        "f = Facility.objects.first(); "
        f"WorkItem.objects.filter(facility=f, title__startswith='{_LAYOUT}').delete(); "
        "print('CLEAN')"
    )
    _run_shell(code, e2e_env)


class TestWorkItemTwoColumnLayout:
    """Refs #1149: Aufgabenübersicht als zweispaltige Arbeitsansicht (Offen +
    In Bearbeitung) mit aufklappbarem, eindeutig auf die letzten 7 Tage
    beschriftetem „Kürzlich erledigt"-Bereich.

    Abgeleitet aus der manuellen Playwright-Verifikation gegen den E2E-Server.
    """

    def test_open_and_in_progress_side_by_side_on_desktop(self, authenticated_page, base_url):
        """Auf breitem Viewport stehen die beiden Hauptspalten nebeneinander:
        die x-Position von „In Bearbeitung" liegt rechts von „Offen", nicht
        darunter."""
        page = authenticated_page
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        open_h = page.locator("section h2", has_text="Offen").first
        prog_h = page.locator("section h2", has_text="In Bearbeitung").first
        open_box = open_h.bounding_box()
        prog_box = prog_h.bounding_box()
        assert open_box and prog_box
        # Zweispaltig: gleiche Höhe (nebeneinander), unterschiedliche x-Position.
        assert prog_box["x"] > open_box["x"] + 50
        assert abs(prog_box["y"] - open_box["y"]) < 30

    def test_recent_done_collapsed_by_default_with_seven_day_label(self, authenticated_page, base_url, e2e_env):
        """„Kürzlich erledigt" ist standardmäßig eingeklappt; die Überschrift
        benennt den Zeitraum (letzte 7 Tage); ein Klick klappt die Liste auf."""
        _seed_done_unassigned_for_admin(e2e_env, count=2)
        try:
            page = authenticated_page
            page.set_viewport_size({"width": 1280, "height": 900})
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")

            section = page.locator("details[data-testid='recent-done']")
            summary = section.locator("summary", has_text="Erledigt in den letzten 7 Tagen")
            assert summary.is_visible()
            # Eingeklappt: das <details> trägt kein open-Attribut, die Items sind
            # nicht sichtbar.
            assert section.get_attribute("open") is None
            item = section.locator("[id^='workitem-']", has_text=f"{_LAYOUT} 0").first
            assert not item.is_visible()

            # Aufklappen → Items werden sichtbar.
            _open_recent_done(page)
            item.wait_for(state="visible", timeout=5000)
            assert item.is_visible()
        finally:
            _cleanup_layout_items(e2e_env)

    def test_recent_done_preview_caps_at_five_with_seven_day_expander(self, authenticated_page, base_url, e2e_env):
        """Bei mehr als fünf erledigten Aufgaben zeigt der aufgeklappte Bereich
        zunächst fünf; ein eindeutig auf „letzte 7 Tage" beschrifteter
        Aufklapp-Button blendet den Rest ein.

        Die Vorschau-Grenze (fünf) ist global; die genaue Rest-Anzahl hängt von
        ggf. vorhandenen Seed-Aufgaben ab. Geprüft wird daher: genau fünf
        Vorschau-Karten, Extra-Bereich anfangs zu, und nach dem Aufklappen sind
        *alle acht* eigens angelegten Aufgaben im Bereich auffindbar (keine geht
        verloren).
        """
        _seed_done_unassigned_for_admin(e2e_env, count=8)
        try:
            page = authenticated_page
            page.set_viewport_size({"width": 1280, "height": 900})
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")

            section = page.locator("details[data-testid='recent-done']")
            _open_recent_done(page)

            # Vorschau: genau fünf Karten sichtbar (Cap), der Rest steckt im noch
            # geschlossenen Extra-<details>.
            preview_cards = page.locator("details[data-testid='recent-done'] > div > .space-y-3 > [id^='workitem-']")
            preview_cards.first.wait_for(state="visible", timeout=5000)
            assert preview_cards.count() == 5

            extra = section.locator("details[data-testid='recent-done-extra']")
            expander = page.locator("details[data-testid='recent-done-extra'] > summary")
            # Beschriftung benennt den 7-Tage-Zeitraum (kein nacktes „Alle anzeigen").
            assert "der letzten 7 Tage anzeigen" in expander.inner_text()
            assert extra.get_attribute("open") is None

            # Aufklappen → die restlichen Aufgaben werden sichtbar; insgesamt sind
            # alle acht eigens angelegten Aufgaben im Bereich vorhanden.
            expander.click()
            extra.locator("[id^='workitem-']").first.wait_for(state="visible", timeout=5000)
            mine = section.locator("[id^='workitem-']", has_text=_LAYOUT)
            assert mine.count() == 8
            # Im Extra-Bereich liegen die über die fünf Vorschau-Karten
            # hinausgehenden eigenen Aufgaben (mindestens drei).
            assert extra.locator("[id^='workitem-']", has_text=_LAYOUT).count() >= 3
        finally:
            _cleanup_layout_items(e2e_env)

    def test_select_all_visible_skips_collapsed_done_items(self, authenticated_page, base_url, e2e_env):
        """Refs #1149: „Alle sichtbaren auswählen" erfasst die erledigten
        Aufgaben im *eingeklappten* Bereich nicht — die Auswahl bleibt
        nachvollziehbar (nur wirklich sichtbare Karten).

        Reihenfolge bewusst: Gegenprobe (aufgeklappt) zuerst, solange noch nichts
        ausgewählt ist und die sticky Bulk-Leiste das Layout nicht verschoben hat.
        Danach erst der eigentliche Skip-Test im eingeklappten Zustand.
        """
        _seed_done_unassigned_for_admin(e2e_env, count=6)
        try:
            page = authenticated_page
            page.set_viewport_size({"width": 1280, "height": 900})
            page.goto(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")

            section = page.locator("details[data-testid='recent-done']")

            # Gegenprobe: Im *aufgeklappten* Zustand erfasst „Alle sichtbaren
            # auswählen" die erledigten Vorschau-Karten. (Welche der sechs in der
            # Fünfer-Vorschau landen, hängt von der Sortierung ab — daher auf die
            # erste Vorschau-Karte warten, nicht auf einen festen Titel.)
            _open_recent_done(page)
            page.locator("details[data-testid='recent-done'] > div > .space-y-3 > [id^='workitem-']").first.wait_for(
                state="visible", timeout=5000
            )
            page.locator("#workitem-select-all").check()
            page.wait_for_timeout(200)
            assert section.locator("input[type=checkbox][name=workitem_ids]:checked").count() >= 1

            # Auswahl löschen und Bereich wieder einklappen.
            page.locator("#workitem-select-all").uncheck()
            page.wait_for_timeout(150)
            _close_recent_done(page)

            # Eigentlicher Test: eingeklappt darf keine erledigte Aufgabe in die
            # Auswahl geraten.
            page.locator("#workitem-select-all").check()
            page.wait_for_timeout(200)
            assert section.locator("input[type=checkbox][name=workitem_ids]:checked").count() == 0
        finally:
            _cleanup_layout_items(e2e_env)

    def test_mobile_single_column(self, mobile_authenticated_page, base_url):
        """Auf schmalem (mobilem) Viewport stehen Offen und In Bearbeitung
        untereinander (einspaltig): „In Bearbeitung" liegt unterhalb von
        „Offen", nicht rechts daneben."""
        page = mobile_authenticated_page
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        open_box = page.locator("section h2", has_text="Offen").first.bounding_box()
        prog_box = page.locator("section h2", has_text="In Bearbeitung").first.bounding_box()
        assert open_box and prog_box
        # Einspaltig: untereinander (y deutlich größer), nicht nebeneinander.
        assert prog_box["y"] > open_box["y"] + 30
        # „Kürzlich erledigt" bleibt auch mobil als aufklappbarer Bereich da.
        assert page.locator(
            "details[data-testid='recent-done'] summary", has_text="Erledigt in den letzten 7 Tagen"
        ).first.is_visible()
