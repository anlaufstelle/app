"""E2E-Tests für WorkItem-Bearbeitung: Titel/Priorität ändern, Berechtigungen."""

import re
import subprocess
import sys
import uuid

import pytest

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

    def _navigate_to_first_workitem_edit(self, page, base_url):
        """Hilfsfunktion: Navigiert zum Edit-Formular des ersten WorkItems."""
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Erste Aufgabe in der Inbox anklicken → Detail
        page.locator("#inbox-content a[href*='/workitems/']").first.click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))

        # "Bearbeiten"-Link klicken
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))

    def test_edit_form_loads_with_existing_data(self, authenticated_page, base_url):
        """Edit-Formular zeigt aktuellen Titel vorausgefüllt."""
        page = authenticated_page
        self._navigate_to_first_workitem_edit(page, base_url)

        # Titel-Feld ist nicht leer
        title_value = page.locator('input[name="title"]').input_value()
        assert title_value != "", "Titel-Feld sollte vorausgefüllt sein"

        # Priorität-Dropdown ist sichtbar
        assert page.locator('select[name="priority"]').is_visible()

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

        # Fristdatum setzen
        page.fill('input[name="due_date"]', "2026-12-31")
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(f"{base_url}/workitems/")

        # Detail-Seite prüfen: Fristdatum angezeigt
        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        assert page.locator("text=31.12.2026").first.is_visible()

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

            # Prefill ist das Vergangenheitsdatum, ``min`` darauf abgesenkt →
            # Browser-Native-Validation hält den Wert für gültig.
            due = page.locator('input[name="due_date"]')
            assert due.input_value() != ""
            assert due.get_attribute("min") == due.input_value()
            assert page.evaluate("() => document.querySelector('input[name=\"due_date\"]').validity.valid")
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
            page.fill('input[name="due_date"]', five_days_ago)
            # remind_at leeren, damit nicht die remind>due-Regel zuerst feuert.
            page.fill('input[name="remind_at"]', "")
            page.locator("#main-content button[type='submit']").click()

            # Bleibt auf dem Edit-Formular, Server-Fehler sichtbar.
            page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))
            assert page.locator("#id_due_date-error").is_visible()
            assert "Vergangenheit" in page.locator("#id_due_date-error").inner_text()
        finally:
            _cleanup_task_by_title(title, e2e_env)


class TestWorkItemEditPermissions:
    """Berechtigungsprüfungen für WorkItem-Edit."""

    def test_assistant_cannot_edit_workitem(self, assistant_page, authenticated_page, base_url):
        """Assistenz bekommt 403 auf WorkItem-Edit-URL."""
        # Als Admin eine gültige WorkItem-UUID holen
        admin = authenticated_page
        admin.goto(f"{base_url}/workitems/")
        admin.wait_for_load_state("domcontentloaded")
        admin.locator("#inbox-content a[href*='/workitems/']").first.click()
        admin.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        wi_pk = re.search(r"/workitems/([0-9a-f-]+)/", admin.url).group(1)

        # Als Assistenz auf Edit zugreifen → 403
        resp = assistant_page.goto(f"{base_url}/workitems/{wi_pk}/edit/")
        assert resp.status == 403
