"""E2E: Die Personensuche in den Formularen schweigt nie (Refs #1803, Konzept §3.8).

Der Befund war kein Rendering-Detail, sondern ein Zustand ohne Rückmeldung:
``this.show = data.length > 0`` liess das Listenfeld bei null Treffern
unsichtbar. Für die Nutzerin war das ununterscheidbar von „ich habe noch nicht
genug getippt" — und wenn ein Typwechsel die bereits getroffene Auswahl leerte,
verschwand eine Person wortlos, die eben noch dastand.

Beides ist Laufzeitverhalten und nur im Browser prüfbar; die Verdrahtung
(Texte im Markup, Rollen-Gate am „Person anlegen"-Sprung) prüft
``src/tests/test_client_autocomplete_empty_state.py``.
"""

import pytest

pytestmark = pytest.mark.e2e

EMPTY = "[data-testid='client-autocomplete-empty']"
PSEUDONYM_FIELD = "input[placeholder='Pseudonym eingeben...']"


class TestSilentAutocompleteSpeaksUp:
    def test_zero_hits_show_an_explicit_empty_state(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        page.locator(PSEUDONYM_FIELD).fill("zzz-gibt-es-nicht")

        empty = page.locator(EMPTY)
        empty.wait_for(state="visible", timeout=10000)
        assert "Keine Person mit diesem Pseudonym gefunden." in empty.inner_text()

    def test_a_single_character_stays_quiet(self, authenticated_page, base_url):
        """Unter der Zwei-Zeichen-Schwelle ist „nichts gefunden" keine Aussage —
        der Leerzustand darf dort NICHT erscheinen."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        page.locator(PSEUDONYM_FIELD).fill("z")

        page.wait_for_timeout(800)
        assert not page.locator(EMPTY).is_visible()

    def test_hits_show_the_list_and_not_the_empty_state(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        page.locator(PSEUDONYM_FIELD).fill("Stern")

        page.locator("#client-autocomplete-list").wait_for(state="visible", timeout=10000)
        assert not page.locator(EMPTY).is_visible()

    def test_the_empty_state_disappears_once_something_matches(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        field = page.locator(PSEUDONYM_FIELD)
        field.fill("zzz-gibt-es-nicht")
        page.locator(EMPTY).wait_for(state="visible", timeout=10000)

        field.fill("Stern")
        page.locator(EMPTY).wait_for(state="hidden", timeout=10000)
        page.locator("#client-autocomplete-list").wait_for(state="visible", timeout=10000)

    def test_the_task_form_has_the_same_empty_state(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")

        page.locator(PSEUDONYM_FIELD).fill("zzz-gibt-es-nicht")

        empty = page.locator(EMPTY)
        empty.wait_for(state="visible", timeout=10000)
        assert "Keine Person mit diesem Pseudonym gefunden." in empty.inner_text()
