"""E2E-Tests: Option-Deaktivierung (is_active in options_json).

Tests:
- Deaktivierte Option ist bei neuem Event NICHT waehlbar
- Bestehendes Event mit deaktiviertem Wert zeigt Option mit (deaktiviert)
- Wert bleibt nach Speichern erhalten
"""

import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

SUBMIT = "#main-content button[type='submit']"


def _find_event_with_sachspenden(e2e_env):
    """Find UUID of an event containing 'sachspenden' via Django shell."""
    result = subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "-c",
            "from core.models import Event\n"
            "e = Event.objects.filter(data_json__leistungen__contains=['sachspenden']).first()\n"
            "print(e.pk if e else '')",
        ],
        capture_output=True,
        text=True,
        env=e2e_env,
    )
    return result.stdout.strip() or None


class TestOptionDeactivation:
    """Deaktivierte Optionen in Select/MultiSelect-Feldern."""

    def test_inactive_option_not_in_new_event(self, authenticated_page, base_url):
        """Neues Event: deaktivierte Option 'Sachspenden' ist NICHT waehlbar."""
        page = authenticated_page

        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        page.select_option("select[name='document_type']", label="Kontakt")
        # Warten auf HTMX-geladene Felder
        page.locator("input[name='leistungen']").first.wait_for(state="attached")

        checkboxes = page.locator("input[type='checkbox'][name='leistungen']")
        values = [checkboxes.nth(i).get_attribute("value") for i in range(checkboxes.count())]

        assert "beratung" in values, "Aktive Option 'beratung' muss vorhanden sein"
        assert "sachspenden" not in values, "Deaktivierte Option 'sachspenden' darf nicht vorhanden sein"

    def test_edit_event_shows_inactive_with_label(self, authenticated_page, base_url, e2e_env):
        """Event mit deaktiviertem Wert bearbeiten: Option sichtbar mit '(deaktiviert)'."""
        page = authenticated_page

        event_uuid = _find_event_with_sachspenden(e2e_env)
        assert event_uuid, "Seed-Event mit 'sachspenden' nicht gefunden"

        page.goto(f"{base_url}/events/{event_uuid}/edit/")
        page.wait_for_load_state("domcontentloaded")

        sachspenden_cb = page.locator("input[type='checkbox'][value='sachspenden']")
        sachspenden_cb.wait_for(state="attached")

        assert sachspenden_cb.is_visible(), "Deaktivierte Option muss im Formular sichtbar sein"
        assert sachspenden_cb.is_checked(), "Deaktivierte Option muss angehakt sein"

        label_text = page.locator("text=Sachspenden (deaktiviert)")
        assert label_text.is_visible(), "Label muss '(deaktiviert)' enthalten"

    def test_inactive_value_preserved_on_save(self, authenticated_page, base_url, e2e_env):
        """Beim Speichern bleibt der deaktivierte Wert erhalten."""
        page = authenticated_page

        event_uuid = _find_event_with_sachspenden(e2e_env)
        assert event_uuid, "Seed-Event mit 'sachspenden' nicht gefunden"

        page.goto(f"{base_url}/events/{event_uuid}/edit/")
        page.wait_for_load_state("domcontentloaded")

        sachspenden_cb = page.locator("input[type='checkbox'][value='sachspenden']")
        sachspenden_cb.wait_for(state="attached")
        assert sachspenden_cb.is_checked()

        page.locator(SUBMIT).click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # Zurueck zum Edit
        page.goto(f"{base_url}/events/{event_uuid}/edit/")
        page.wait_for_load_state("domcontentloaded")

        sachspenden_cb = page.locator("input[type='checkbox'][value='sachspenden']")
        sachspenden_cb.wait_for(state="attached")
        assert sachspenden_cb.is_checked(), "Deaktivierter Wert muss nach Speichern erhalten bleiben"
