"""E2E-Tests: Option-Deaktivierung (is_active in options_json).

Tests:
- Deaktivierte Option ist bei neuem Event NICHT waehlbar
- Bestehendes Event mit deaktiviertem Wert zeigt Option mit (deaktiviert)
- Wert bleibt nach Speichern erhalten

Refs #1788: Die beiden mutierenden Tests griffen sich vorher über
``_find_event_with_sachspenden`` per ``.first()`` (ohne ``is_deleted``-Filter)
eines der beiden fest verdrahteten Seed-Events mit dem deaktivierten Wert
'sachspenden' (``core/seed/events.py::seed_events_small``, deterministisch
vorhanden — die Kombination existiert also verlässlich). Das eigentliche
Risiko ist ein anderes, dieselbe Klasse wie Refs #1787: ein FREMDES,
geteiltes Seed-Objekt, dessen zugrunde liegender Client oder dessen Event
selbst von einer anderen E2E-Datei im vollen seriellen Lauf soft-gelöscht
oder anderweitig mutiert wird — die Edit-Seite würde dann 404en, ohne dass
dieser Test dafür etwas kann. Beide Tests legen sich ihr Ziel-Event jetzt
selbst an (Muster ``_seed_active_ban`` aus test_ban_banner.py) und sind
damit von jedem Fremdzugriff entkoppelt.
"""

import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

SUBMIT = "#main-content button[type='submit']"

_SEED_SACHSPENDEN_EVENT_SHELL = r"""
from django.utils import timezone
from core.models import DocumentType, Event, Facility, User

facility = Facility.objects.get(name="Hauptstelle")
admin = User.objects.filter(facility=facility, role="facility_admin").first() or User.objects.first()

# "Kontakt" ist der Dokumentationstyp mit dem Feld "leistungen"
# (core/packages/default.yaml) — der Slug ist stabiler als der uebersetzte
# Name (und seit E4/#1850 traegt der Typ keinen system_type mehr).
kontakt_dt = DocumentType.objects.filter(facility=facility, slug="kontakt", is_active=True).first()
assert kontakt_dt is not None, "Kontakt-DocumentType fehlt in der E2E-DB"

# Kein Personenbezug noetig (client ist nullable, core/models/event.py) —
# das Event ist reiner Traeger fuer den deaktivierten Leistungen-Wert, ohne
# Kopplung an Seed-/Fremdklienten (Refs #1788). ``is_anonymous=True`` haelt
# die Service-Invariante aus ``create_event`` ein: client=None ohne
# Anonym-Flag ist eine Zeilenform, die kein Anwendungspfad je schreibt
# (Review-Befund #1788).
event = Event.objects.create(
    facility=facility,
    client=None,
    is_anonymous=True,
    document_type=kontakt_dt,
    created_by=admin,
    occurred_at=timezone.now(),
    data_json={"leistungen": ["sachspenden"]},
)
print(f"EVENT_PK={event.pk}")
"""

_CLEANUP_EVENT_SHELL = r"""
from core.models import Event
Event.objects.filter(pk='{pk}').delete()
"""


def _create_event_with_sachspenden(e2e_env) -> str:
    """Legt ein eigenes Kontakt-Event mit der deaktivierten Option 'sachspenden' an.

    Gibt die Event-PK (UUID als String) zurück.
    """
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _SEED_SACHSPENDEN_EVENT_SHELL],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Event-Seed fehlgeschlagen:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    event_pk = ""
    for line in result.stdout.splitlines():
        if line.startswith("EVENT_PK="):
            event_pk = line.split("=", 1)[1].strip()
    assert event_pk, f"Seed-Output unerwartet:\n{result.stdout}"
    return event_pk


def _cleanup_event(e2e_env, event_pk: str) -> None:
    """Wegwerf-Event wieder entfernen (best-effort, wie ``_deactivate_ban``)."""
    subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _CLEANUP_EVENT_SHELL.format(pk=event_pk)],
        env=e2e_env,
        check=False,
        capture_output=True,
    )


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

        event_uuid = _create_event_with_sachspenden(e2e_env)
        try:
            page.goto(f"{base_url}/events/{event_uuid}/edit/")
            page.wait_for_load_state("domcontentloaded")

            sachspenden_cb = page.locator("input[type='checkbox'][value='sachspenden']")
            sachspenden_cb.wait_for(state="attached")

            assert sachspenden_cb.is_visible(), "Deaktivierte Option muss im Formular sichtbar sein"
            assert sachspenden_cb.is_checked(), "Deaktivierte Option muss angehakt sein"

            label_text = page.locator("text=Sachspenden (deaktiviert)")
            assert label_text.is_visible(), "Label muss '(deaktiviert)' enthalten"
        finally:
            _cleanup_event(e2e_env, event_uuid)

    def test_inactive_value_preserved_on_save(self, authenticated_page, base_url, e2e_env):
        """Beim Speichern bleibt der deaktivierte Wert erhalten."""
        page = authenticated_page

        event_uuid = _create_event_with_sachspenden(e2e_env)
        try:
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
        finally:
            _cleanup_event(e2e_env, event_uuid)
