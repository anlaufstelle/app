"""E2E: Enter im Suchfeld führt in die Vollsuche (Refs #1803, Konzept §3.2).

Bis hierher war ``/search/`` ausschließlich mit der Maus erreichbar — über die
Fußzeile des Dropdowns. Beide Suchfelder standen in einem ``<div>``, nicht in
einem ``<form>``; einen Enter-Handler gab es nirgends. Wer tippte und Enter
drückte, blieb stehen.

Die zweite Aussage des Moduls ist die **Entkopplung**: der HTMX-Live-Trigger
hängt jetzt am ``input``-Event. Enter ändert den Feldwert nicht, feuert also
kein ``input`` — ein Tastendruck erzeugt genau eine Navigation und keinen
zweiten, parallel laufenden Request auf denselben Endpunkt. Der Test misst das
am echten Netzwerkverkehr, nicht am Markup (das prüft
``src/tests/test_search_field.py``).
"""

import pytest
from playwright.sync_api import expect

from ._selectors import open_mobile_search_overlay

pytestmark = pytest.mark.e2e


class TestEnterOpensTheFullSearch:
    def test_enter_in_the_sidebar_navigates_to_the_search_page(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        search_input = page.locator("[data-testid='global-search-input']")
        search_input.fill("Stern")
        search_input.press("Enter")

        page.wait_for_url("**/search/?q=Stern", timeout=10000)
        assert page.locator("h1").inner_text() == "Suche"
        expect(page.locator("#search-results")).to_contain_text("Stern-42", timeout=10000)

    def test_enter_below_the_two_character_threshold_stays_put(self, authenticated_page, base_url):
        """Ein einzelnes Zeichen ist kein Suchbegriff — die Vollseite würde nur
        den Hinweiszustand zeigen. Der Submit wird deshalb unterdrückt."""
        page = authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        before = page.url

        search_input = page.locator("[data-testid='global-search-input']")
        search_input.fill("S")
        search_input.press("Enter")

        page.wait_for_timeout(600)
        assert page.url == before, f"Enter unter der Schwelle hat navigiert: {page.url}"

    def test_enter_does_not_also_fire_the_live_dropdown_request(self, authenticated_page, base_url):
        """Das Doppel-Request-Risiko des Formular-Wrappers, am Netzverkehr gemessen.

        Mit dem alten ``keyup``-Trigger hätte derselbe Enter-Anschlag den
        HTMX-Request auf ``/partials/search/global/`` UND die Formular-Navigation
        ausgelöst. Der Zähler beginnt erst nach dem Befüllen des Feldes, damit
        nur der Enter-Anschlag selbst gemessen wird.
        """
        page = authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        search_input = page.locator("[data-testid='global-search-input']")
        search_input.fill("Stern")
        # Den Debounce des Fuell-Vorgangs vollstaendig auslaufen lassen.
        page.wait_for_timeout(900)

        dropdown_requests = []
        page.on("request", lambda request: dropdown_requests.append(request.url))

        search_input.press("Enter")
        page.wait_for_url("**/search/?q=Stern", timeout=10000)

        live = [url for url in dropdown_requests if "/partials/search/global/" in url]
        assert live == [], f"Enter hat zusaetzlich den Live-Endpunkt gerufen: {live}"

    def test_enter_in_the_mobile_overlay_navigates_too(self, mobile_authenticated_page, base_url):
        """Refs #1861: der Weg ins Feld geht jetzt ueber einen echten Klick.

        ``.fill()`` setzt den Wert ohne Maus-Event — genau deshalb blieb dieser
        Test gruen, waehrend ein Tap ins Feld das Overlay real wieder schloss
        (das offene Mehr-Dropdown fing den Klick per ``click.away`` ab). Ohne
        den Klick prueft der Test den Enter-Weg auf einer Flaeche, die
        Nutzerinnen so nie erreichten.
        """
        page = mobile_authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        overlay = open_mobile_search_overlay(page)

        search_input = page.locator("[data-testid='mobile-search-input']")
        search_input.click()
        expect(overlay).to_be_visible()
        search_input.fill("Stern")
        search_input.press("Enter")

        page.wait_for_url("**/search/?q=Stern", timeout=10000)
        assert page.locator("h1").inner_text() == "Suche"

    def test_enter_on_the_search_page_keeps_the_query(self, authenticated_page, base_url):
        """Auch das Feld auf ``/search/`` selbst steht jetzt in einem Formular."""
        page = authenticated_page
        page.goto(f"{base_url}/search/?q=Stern")
        page.wait_for_load_state("domcontentloaded")

        search_input = page.locator("[data-testid='search-page-input']")
        search_input.fill("Stern-42")
        search_input.press("Enter")

        page.wait_for_url("**/search/?q=Stern-42", timeout=10000)
        expect(page.locator("#search-results")).to_contain_text("Stern-42", timeout=10000)
