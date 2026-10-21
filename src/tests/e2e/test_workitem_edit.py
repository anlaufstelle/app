"""E2E-Tests für WorkItem-Bearbeitung: Titel/Priorität ändern, Berechtigungen."""

import re
import subprocess
import sys
import uuid
from datetime import date, timedelta

import pytest

from tests.e2e._selectors import action_bar

from ._date_helpers import date_field, expect_date_value, fill_date

pytestmark = pytest.mark.e2e


def _create_overdue_task_for(username: str, title: str, e2e_env) -> str:
    """Lege eine offene, überfällige Aufgabe an, ``assigned_to`` = ``username``.

    Gibt die PK als String zurück, damit der Test direkt zum Edit-Formular
    navigieren kann. ``assigned_to`` ist nötig, damit der editierende User die
    Aufgabe laut ``can_user_mutate_workitem`` bearbeiten darf (Refs #1131).
    """
    result = subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "--no-imports",
            "-c",
            (
                "from datetime import timedelta; from django.utils import timezone; "
                "from core.models import User, WorkItem; "
                f"u = User.objects.get(username='{username}'); "
                "past = timezone.localdate() - timedelta(days=10); "
                "wi = WorkItem.objects.create(facility=u.facility, created_by=u, "
                f"assigned_to=u, title='{title}', priority='normal', status='open', "
                "item_type='task', due_date=past, remind_at=past); "
                "print(wi.pk)"
            ),
        ],
        env=e2e_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().splitlines()[-1]


def _create_urgent_task_for(username: str, title: str, e2e_env) -> str:
    """Offene Aufgabe mit Priorität „Dringend“, ``created_by``/``assigned_to`` = ``username``."""
    result = subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "--no-imports",
            "-c",
            (
                "from core.models import User, WorkItem; "
                f"u = User.objects.get(username='{username}'); "
                "wi = WorkItem.objects.create(facility=u.facility, created_by=u, "
                f"assigned_to=u, title='{title}', priority='urgent', status='open', "
                "item_type='task', description='Langer Text, der nicht verloren gehen darf.'); "
                "print(wi.pk)"
            ),
        ],
        env=e2e_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().splitlines()[-1]


def _read_status_and_priority(pk: str, e2e_env) -> tuple[str, str]:
    result = subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "--no-imports",
            "-c",
            (
                "from core.models import WorkItem; "
                f"wi = WorkItem.objects.get(pk='{pk}'); "
                "print(wi.status + '|' + wi.priority)"
            ),
        ],
        env=e2e_env,
        capture_output=True,
        text=True,
        check=True,
    )
    status, priority = result.stdout.strip().splitlines()[-1].split("|")
    return status, priority


def _cleanup_task_by_title(title: str, e2e_env) -> None:
    subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "--no-imports",
            "-c",
            (f"from core.models import WorkItem; WorkItem.objects.filter(title='{title}').delete()"),
        ],
        env=e2e_env,
        capture_output=True,
        text=True,
    )


class TestWorkItemEdit:
    """WorkItem-Edit-Formular: Laden, Bearbeiten, Speichern."""

    def test_edit_form_loads_with_existing_data(self, authenticated_page, base_url, e2e_env):
        """Edit-Formular zeigt aktuellen Titel vorausgefüllt.

        Refs #1788: Vorher navigierte der Test über die erste Aufgabe der
        Inbox (``#inbox-content a[href*='/workitems/']").first``). Der Seed
        garantiert für admin im Einzellauf keine offene Aufgabe, und selbst
        wenn eine existiert, kann ``.first`` im eingeklappten
        ``<details data-testid="recent-done">`` landen. Wie die
        UI-basierten Nachbartests dieser Datei legt der Test jetzt eine
        eigene Aufgabe an und navigiert gezielt über deren Titel.
        """
        page = authenticated_page
        unique_title = f"E2E-Edit-{uuid.uuid4().hex[:6]}"
        try:
            page.goto(f"{base_url}/workitems/new/")
            page.fill('input[name="title"]', unique_title)
            page.select_option('select[name="item_type"]', index=1)
            page.locator("#main-content button[type='submit']").click()
            page.wait_for_url(f"{base_url}/workitems/")

            page.locator(f"a:has-text('{unique_title}')").click()
            page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
            page.click("a:has-text('Bearbeiten')")
            page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))

            # Titel-Feld ist mit dem eigenen Titel vorausgefüllt.
            title_value = page.locator('input[name="title"]').input_value()
            assert title_value == unique_title, "Titel-Feld sollte vorausgefüllt sein"

            # Priorität-Dropdown ist sichtbar
            assert page.locator('select[name="priority"]').is_visible()
        finally:
            # Refs #1788 (Review-Befund): keine Zustands-Akkumulation in der
            # geteilten E2E-DB zuruecklassen — wie die Nachbartests.
            _cleanup_task_by_title(unique_title, e2e_env)

    def test_edit_title_and_priority_saves(self, authenticated_page, base_url):
        """Titel + Priorität ändern → Speichern → Inbox/Detail zeigt neue Werte."""
        page = authenticated_page

        # Erst neuen WorkItem erstellen, um ihn dann zu editieren
        unique_title = f"E2E-WI-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/workitems/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="item_type"]', index=1)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(f"{base_url}/workitems/")

        # WorkItem in Inbox finden und zur Detail-Seite navigieren
        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))

        # Bearbeiten
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))

        updated_title = f"E2E-Updated-{uuid.uuid4().hex[:6]}"
        page.fill('input[name="title"]', updated_title)
        page.select_option('select[name="priority"]', value="important")
        page.locator("#main-content button[type='submit']").click()

        # Redirect zur Inbox
        page.wait_for_url(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Aktualisierter Titel in Inbox sichtbar
        assert page.locator(f"text={updated_title}").is_visible()

    def test_edit_due_date_saves(self, authenticated_page, base_url):
        """Fristdatum setzen → Speichern → Detail zeigt Datum."""
        page = authenticated_page

        # Neuen WorkItem erstellen
        unique_title = f"E2E-Due-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/workitems/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="item_type"]', index=1)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(f"{base_url}/workitems/")

        # Zur Detail-Seite → Bearbeiten
        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))

        # Relative to today: the form refuses a changed date in the past, and the
        # detail page shows the full date only beyond the next calendar month.
        due = date.today() + timedelta(days=70)
        fill_date(page, 'input[name="due_date"]', due.isoformat())
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(f"{base_url}/workitems/")

        # Detail-Seite prüfen: Fristdatum angezeigt
        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        assert page.locator(f"text={due:%d.%m.%Y}").first.is_visible()

    def test_due_date_input_present_alongside_presets(self, authenticated_page, base_url):
        """Free text entry and presets share the language display and ISO transport.

        Refs #1135, #2603: the application-language widget replaces the native
        picker. Check visibility, editability, validity, presets and persistence.
        """
        page = authenticated_page

        # Neue Aufgabe anlegen, dann ihr Edit-Formular oeffnen.
        unique_title = f"E2E-Native-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/workitems/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="item_type"]', index=1)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(f"{base_url}/workitems/")

        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))

        # (a) Visible, editable application-language date field.
        due = date_field(page, 'input[name="due_date"]')
        assert due.is_visible()
        assert due.get_attribute("type") == "text"
        assert not due.is_disabled()
        assert due.get_attribute("readonly") is None

        # (a) Schnelloptionen sind weiterhin vorhanden (Koexistenz).
        assert page.locator("button:has-text('Heute')").is_visible()

        # (b) A freely chosen date is valid through the visible text control.
        arbitrary = page.evaluate(
            "() => { const d = new Date(); d.setDate(d.getDate() + 10); return d.toLocaleDateString('en-CA'); }"
        )
        fill_date(page, 'input[name="due_date"]', arbitrary)
        assert due.evaluate("el => el.validity.valid")

        # (c) The preset updates the same visible control and ISO transport.
        today_iso = page.evaluate("() => new Date().toLocaleDateString('en-CA')")
        page.click("button:has-text('Heute')")
        expect_date_value(page, 'input[name="due_date"]', today_iso)

        # (d) Frei gewaehltes Datum speichern und auf der Detailseite pruefen.
        fill_date(page, 'input[name="due_date"]', arbitrary)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(f"{base_url}/workitems/")

        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        # Check both display and transport after reopening the saved form.
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))
        expect_date_value(page, 'input[name="due_date"]', arbitrary)

    def test_overdue_task_unchanged_due_date_is_browser_valid_and_saves(self, authenticated_page, base_url, e2e_env):
        """Überfällige Aufgabe: ``min`` ist abgesenkt → Browser akzeptiert das
        unveränderte Vergangenheitsdatum, andere Felder lassen sich speichern.

        Regressions-Guard für #1131: Vorher blockierte ``min=heute`` die
        HTML5-Native-Validation, sodass das überfällige Item in der Oberfläche
        gar nicht speicherbar war (``form.checkValidity() == false``).
        """
        page = authenticated_page
        title = f"E2E-Overdue-{uuid.uuid4().hex[:6]}"
        wi_pk = _create_overdue_task_for("admin", title, e2e_env)
        try:
            page.goto(f"{base_url}/workitems/{wi_pk}/edit/")
            page.wait_for_load_state("domcontentloaded")

            # The prefilled past date equals the ISO minimum and is valid.
            due = date_field(page, 'input[name="due_date"]')
            assert due.input_value() != ""
            assert due.get_attribute("min") == page.locator('input[name="due_date"]').input_value()
            expect_date_value(page, 'input[name="due_date"]', due.get_attribute("min"))
            assert due.evaluate("el => el.validity.valid")
            assert page.evaluate(
                "() => document.querySelector('input[name=\"due_date\"]').closest('form').checkValidity()"
            )

            # Anderes Feld ändern, Fälligkeitsdatum unangetastet → speichern.
            updated_title = f"{title}-EDIT"
            page.fill('input[name="title"]', updated_title)
            page.locator("#main-content button[type='submit']").click()
            page.wait_for_url(f"{base_url}/workitems/")
            page.wait_for_load_state("domcontentloaded")
            assert page.locator(f"text={updated_title}").first.is_visible()
        finally:
            _cleanup_task_by_title(title, e2e_env)
            _cleanup_task_by_title(f"{title}-EDIT", e2e_env)

    def test_overdue_task_cannot_move_to_different_past_date(self, authenticated_page, base_url, e2e_env):
        """Überfällige Aufgabe aktiv auf ein *anderes* Vergangenheitsdatum
        verschieben → serverseitig abgelehnt (#1131).

        Der Browser lässt einen Wert ``>= min`` (= das Bestandsdatum) zu, der
        immer noch in der Vergangenheit liegen kann; ``clean()`` fängt den
        geänderten Vergangenheitswert über ``changed_data`` ab.
        """
        page = authenticated_page
        title = f"E2E-OverduePast-{uuid.uuid4().hex[:6]}"
        wi_pk = _create_overdue_task_for("admin", title, e2e_env)
        try:
            page.goto(f"{base_url}/workitems/{wi_pk}/edit/")
            page.wait_for_load_state("domcontentloaded")

            # Bestandsdatum (min) ist 10 Tage zurück; 5 Tage zurück ist >= min,
            # also browserseitig erlaubt, aber weiterhin in der Vergangenheit.
            five_days_ago = page.evaluate(
                "() => { const d = new Date(); d.setDate(d.getDate() - 5); return d.toISOString().slice(0,10); }"
            )
            fill_date(page, 'input[name="due_date"]', five_days_ago)
            # remind_at leeren, damit nicht die remind>due-Regel zuerst feuert.
            fill_date(page, 'input[name="remind_at"]', "")
            page.locator("#main-content button[type='submit']").click()

            # Bleibt auf dem Edit-Formular, Server-Fehler sichtbar.
            page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))
            assert page.locator("#id_due_date-error").is_visible()
            assert "Vergangenheit" in page.locator("#id_due_date-error").inner_text()
        finally:
            _cleanup_task_by_title(title, e2e_env)


class TestWorkItemEditPermissions:
    """Berechtigungsprüfungen für WorkItem-Edit."""

    def test_assistant_cannot_edit_workitem(self, assistant_page, base_url, e2e_env):
        """Assistenz bekommt 403 auf WorkItem-Edit-URL.

        Refs #1788: Vorher holte sich der Test die WorkItem-UUID über einen
        ``.first``-Klick in der Admin-Inbox (``authenticated_page``) — mit
        demselben fragilen Muster wie
        ``test_edit_form_loads_with_existing_data``: kein garantiertes
        offenes WorkItem im Einzellauf, potenzieller Treffer im
        eingeklappten ``<details data-testid="recent-done">``. Für einen
        reinen 403-Check auf die Edit-URL ist die konkrete Aufgabe egal —
        die Aufgabe wird deshalb per ORM angelegt (Muster
        ``_create_overdue_task_for``), ohne dass eine Admin-Browsersession
        nötig ist.
        """
        title = f"E2E-Assistant403-{uuid.uuid4().hex[:6]}"
        wi_pk = _create_overdue_task_for("admin", title, e2e_env)
        try:
            # Als Assistenz auf Edit zugreifen → 403
            resp = assistant_page.goto(f"{base_url}/workitems/{wi_pk}/edit/")
            assert resp.status == 403
        finally:
            _cleanup_task_by_title(title, e2e_env)


class TestWorkItemEditParallelStatusChange:
    """Refs #2331: Bearbeiten-Formular in Tab A, Statuswechsel in Tab B."""

    def test_priority_edit_survives_status_change_in_other_tab(self, staff_page, base_url, e2e_env):
        tab_a = staff_page
        title = f"E2E-Parallel-{uuid.uuid4().hex[:6]}"
        wi_pk = _create_urgent_task_for("miriam", title, e2e_env)
        tab_b = tab_a.context.new_page()
        try:
            # Tab A: Formular öffnen, noch nicht speichern.
            tab_a.goto(f"{base_url}/workitems/{wi_pk}/edit/")
            tab_a.wait_for_load_state("domcontentloaded")
            assert tab_a.locator('select[name="priority"]').input_value() == "urgent"

            # Tab B: „Als erledigt markieren“ bestätigen.
            tab_b.goto(f"{base_url}/workitems/{wi_pk}/")
            tab_b.wait_for_load_state("domcontentloaded")
            tab_b.once("dialog", lambda dialog: dialog.accept())
            action_bar(tab_b, "workitem").locator("button:has-text('Als erledigt markieren')").click()
            tab_b.locator("text=Abgeschlossen am:").wait_for(state="visible", timeout=10000)
            assert _read_status_and_priority(wi_pk, e2e_env) == ("done", "urgent")

            # Tab A: nur die Priorität ändern und speichern.
            tab_a.bring_to_front()
            tab_a.select_option('select[name="priority"]', value="normal")
            tab_a.locator("#main-content button[type='submit']").click()
            tab_a.wait_for_url(f"{base_url}/workitems/")
            tab_a.wait_for_load_state("domcontentloaded")
            assert tab_a.locator("text=zwischenzeitlich bearbeitet").count() == 0

            assert _read_status_and_priority(wi_pk, e2e_env) == ("done", "normal")
        finally:
            tab_b.close()
            _cleanup_task_by_title(title, e2e_env)


def _read_title(pk: str, e2e_env) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "--no-imports",
            "-c",
            (f"from core.models import WorkItem; print(WorkItem.objects.get(pk='{pk}').title)"),
        ],
        env=e2e_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return result.stdout.strip().splitlines()[-1]


class TestWorkItemEditRerenderKeepsConflict:
    """Refs #2361: Neu-Anzeige nach ungültiger Eingabe verliert den Konflikt nicht."""

    def test_title_change_in_other_tab_survives_invalid_then_valid_submit(self, staff_page, base_url, e2e_env):
        tab_a = staff_page
        title = f"E2E-Rerender-{uuid.uuid4().hex[:6]}"
        title_b = f"{title}-TabB"
        wi_pk = _create_urgent_task_for("miriam", title, e2e_env)
        edit_url = f"{base_url}/workitems/{wi_pk}/edit/"
        tab_b = tab_a.context.new_page()
        try:
            # Tab A: Formular öffnen, noch nicht speichern.
            tab_a.goto(edit_url)
            tab_a.wait_for_load_state("domcontentloaded")

            # Tab B: denselben Titel über das Formular ändern und speichern.
            tab_b.goto(edit_url)
            tab_b.wait_for_load_state("domcontentloaded")
            tab_b.fill('input[name="title"]', title_b)
            tab_b.locator("#main-content button[type='submit']").click()
            tab_b.wait_for_url(f"{base_url}/workitems/")
            assert _read_title(wi_pk, e2e_env) == title_b

            # Tab A: leeren Titel absenden (Browser-Pflichtfeldprüfung aus,
            # damit der Server die ungültige Eingabe erneut anzeigt).
            tab_a.bring_to_front()
            title_input = tab_a.locator('input[name="title"]')
            title_input.evaluate("el => el.removeAttribute('required')")
            title_input.fill("")
            with tab_a.expect_response(lambda r: r.url == edit_url and r.request.method == "POST") as resp:
                tab_a.locator("#main-content button[type='submit']").click()
            assert resp.value.status == 200
            tab_a.wait_for_load_state("domcontentloaded")

            # Tab A: gültigen Titel eintragen und erneut speichern.
            tab_a.fill('input[name="title"]', f"{title}-TabA")
            with tab_a.expect_response(lambda r: r.url == edit_url and r.request.method == "POST") as resp:
                tab_a.locator("#main-content button[type='submit']").click()
            assert resp.value.status == 302, "Konflikt führt per Redirect zurück ins Formular"
            tab_a.locator("text=zwischenzeitlich bearbeitet").wait_for(state="visible", timeout=10000)

            assert _read_title(wi_pk, e2e_env) == title_b
        finally:
            tab_b.close()
            _cleanup_task_by_title(title, e2e_env)
            _cleanup_task_by_title(title_b, e2e_env)
