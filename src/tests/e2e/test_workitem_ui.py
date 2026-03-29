"""E2E-Tests: WorkItems klickbar in Aktivitätslog/Klientel + Erledigungsdatum.

Testet:
- Aufgabe im Aktivitätslog-Sidebar anklicken → Detail öffnet sich
- Aufgabe erledigen → Erledigungsdatum wird angezeigt
"""

import re

import pytest

pytestmark = pytest.mark.e2e


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

    def test_completed_at_shown_after_done(self, authenticated_page, base_url):
        """Aufgabe erledigen → Erledigungsdatum wird auf Detail-Seite angezeigt."""
        import os
        import subprocess
        import sys

        python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable
        e2e_env = {**os.environ, "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.e2e"}

        # WorkItem direkt ueber Django-Shell als erledigt markieren
        script = (
            "from core.models import WorkItem; "
            "from django.utils import timezone; "
            "wi = WorkItem.objects.filter(status='open').first(); "
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

        page = authenticated_page
        page.goto(f"{base_url}/workitems/{wi_pk}/")
        page.wait_for_load_state("domcontentloaded")

        # Erledigungsdatum sollte sichtbar sein
        page.locator("text=Abgeschlossen am:").wait_for(state="visible", timeout=5000)
