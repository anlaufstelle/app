"""E2E-Tests: Löschanträge — 4-Augen-Prinzip und Liste."""

import re
from urllib.parse import urlparse

import pytest

from tests.e2e._selectors import (
    find_deletion_approve_button,
    find_deletion_reject_button,
)

pytestmark = pytest.mark.e2e

SUBMIT = "#main-content button[type='submit']"


def _find_deletion_request_card(page, event_path):
    """Löschantrags-Karte für GENAU dieses Event, per Ziel-Link-Href gescopt.

    Refs #1788: ``find_first_deletion_review_link`` traf ``.first`` auf der
    Liste — bei mehreren offenen Anträgen (z. B. Rest eines anderen Tests
    derselben Datei) nicht zwingend den eigenen. Die Karte trägt laut
    ``core/deletion_requests/list.html`` (Zeile 16-30) ``data-testid``
    ``deletion-request-card`` und enthält den Ziel-Link aus
    ``partials/target.html`` mit ``href="{{ dr.target_url }}"`` — für Events
    ``reverse("core:event_detail", args=[pk])``, also exakt ``event_path``.
    ``.first`` als Sicherheitsnetz (Konvention aus 1b52bea7/Refs #1787), auch
    wenn ein Event-Pfad-Suffix praktisch eindeutig ist.
    """
    return page.locator(f"[data-testid='deletion-request-card']:has(a[href$='{event_path}'])").first


def _create_qualified_event_and_request_deletion(page, base_url):
    """Event für Stern-42 erstellen und Löschantrag stellen.

    Gibt den Event-Pfad zurück (z. B. ``/events/<uuid>/``) — Aufrufer scopen
    ihre Löschantrags-Karte darüber via ``_find_deletion_request_card``
    (Refs #1788) statt über das inzwischen entfernte, ungescopte
    ``find_first_deletion_review_link``.
    """
    page.goto(f"{base_url}/events/new/")
    page.wait_for_load_state("domcontentloaded")

    page.select_option("select[name='document_type']", label="Kontakt")
    page.wait_for_load_state("domcontentloaded")

    autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
    autocomplete.fill("Stern")
    page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
    page.locator("button:has-text('Stern-42')").click()

    # Refs #1349: primärer Button — SUBMIT ist auf der Event-Anlege-Seite
    # seit "Speichern & nächster Kontakt" mehrdeutig.
    page.locator("#event-submit-btn").click()
    page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

    assert re.search(r"/events/[0-9a-f-]+/$", page.url), f"Erwartete Event-Detail, bekam {page.url}"
    event_path = urlparse(page.url).path

    page.click("a:has-text('Löschen')")
    page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/delete/$"))

    reason_field = page.locator("textarea[name='reason']")
    if reason_field.count() > 0:
        reason_field.fill("E2E-Test: 4-Augen-Prinzip")

    page.locator(SUBMIT).click()
    page.wait_for_url(lambda url: "/delete/" not in url)

    assert page.locator("text=Löschantrag").first.is_visible()

    return event_path


class TestFourEyesPrincipleReview:
    """4-Augen-Prinzip: Löschantrag genehmigen/ablehnen."""

    @pytest.mark.smoke
    def test_lead_can_approve_deletion_request(self, authenticated_page, lead_page, base_url):
        """Lead (emma) genehmigt von admin gestellten Löschantrag."""
        event_path = _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        lead_page.goto(f"{base_url}/deletion-requests/")
        lead_page.wait_for_load_state("domcontentloaded")

        assert lead_page.locator("h1").inner_text() == "Löschanträge"

        card = _find_deletion_request_card(lead_page, event_path)
        card.wait_for(state="visible", timeout=5000)
        review_link = card.locator("[data-testid='deletion-review-link']")
        assert review_link.is_visible()
        review_link.click()
        lead_page.wait_for_url(re.compile(r"/deletion-requests/[0-9a-f-]+/review/$"))

        find_deletion_approve_button(lead_page).click()

        # Refs #1119: Event-Genehmigung führt konsistent zurück in die
        # Löschantragsliste (vorher überraschend in den Zeitstrom).
        lead_page.wait_for_url(re.compile(r"/deletion-requests/$"), timeout=10000)
        assert lead_page.locator("text=Genehmigt").count() > 0

    def test_lead_can_reject_deletion_request(self, authenticated_page, lead_page, base_url):
        """Lead (emma) lehnt von admin gestellten Löschantrag ab.

        Refs Matrix ENT-DEL-04 (Sektion E — Endanwender DSGVO).

        Verifiziert:
        - Flash-Message ``Löschantrag wurde abgelehnt.``
        - Antrag landet in der ``Abgelehnt``-Sektion der Liste mit
          ``Geprüft von: <reviewer>``.
        - Antragsbegründung (``dr.reason`` vom Antragsteller) bleibt sichtbar.

        **Hinweis zur Spec:** Der Matrix-Titel lautet „Antrag ablehnen mit
        Begründung". Die Schritte selbst (``POST mit action=reject``) und
        ``reject_deletion(dr, reviewer)`` zeigen aber, dass keine separate
        Reviewer-Begründung erwartet wird — gemeint ist die vorhandene
        Antragsbegründung. Das aktuelle Template hat dementsprechend kein
        eigenes Begründungs-Feld für die Ablehnung. Test deckt das
        implementierte Verhalten ab.
        """
        event_path = _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        lead_page.goto(f"{base_url}/deletion-requests/")
        lead_page.wait_for_load_state("domcontentloaded")

        card = _find_deletion_request_card(lead_page, event_path)
        card.wait_for(state="visible", timeout=5000)
        review_link = card.locator("[data-testid='deletion-review-link']")
        assert review_link.is_visible()
        review_link.click()
        lead_page.wait_for_url(re.compile(r"/deletion-requests/[0-9a-f-]+/review/$"))

        find_deletion_reject_button(lead_page).click()
        lead_page.wait_for_load_state("domcontentloaded")

        # Flash-Message muss explizit „abgelehnt" enthalten.
        flash = lead_page.locator("[role='status'] :text-matches('abgelehnt', 'i')").first
        flash.wait_for(state="visible", timeout=5000)

        # Antrag landet in der Abgelehnt-Sektion der Liste.
        lead_page.goto(f"{base_url}/deletion-requests/")
        lead_page.wait_for_load_state("domcontentloaded")
        rejected_section = lead_page.locator("section:has(h2:has-text('Abgelehnt'))")
        rejected_section.wait_for(state="visible", timeout=5000)
        # Mind. ein Eintrag in der Abgelehnt-Sektion.
        rejected_card = rejected_section.locator(".bg-surface").first
        assert rejected_card.is_visible(), "Kein abgelehnter Antrag in der Liste sichtbar."
        # Reviewer-Anzeige enthält den Reviewer-Namen oder Username.
        assert "Geprüft von" in rejected_section.inner_text()
        # Antragsbegründung bleibt sichtbar (vom Antragsteller bei Antrag gesetzt).
        assert "E2E-Test" in rejected_section.inner_text(), (
            "Antragsbegründung sollte in der Abgelehnt-Card sichtbar bleiben."
        )


class TestReviewerNotRequester:
    """Reviewer darf nicht der Antragsteller sein."""

    @pytest.mark.smoke
    def test_requester_cannot_approve_own_request(self, authenticated_page, base_url):
        """Admin kann eigenen Löschantrag nicht genehmigen.

        Refs #1787: Der Test hing vorher an ``find_first_deletion_review_link``
        — und **eigene** Anträge tragen gar keinen Prüfen-Link (die Karte
        rendert stattdessen ``deletion-own-request-hint``). ``.first`` zeigte
        also zwangsläufig auf einen FREMDEN Antrag; im Voll-Lauf war das der
        liegen gebliebene Personen-Löschantrag aus
        ``test_client_deletion_workflow.py`` — der Klick auf „Genehmigen"
        schob damit eine Seed-Person in den Papierkorb. Sichtbar wurde der
        Fehlgriff nie, weil die Erfolgsmeldung selbst ein ``[role='alert']``
        ist und die letzte Oder-Zusicherung deshalb immer griff.

        Jetzt wird am EIGENEN Antrag geprüft, was der Vier-Augen-Vertrag
        zusagt: keine Prüfen-Option, dafür der Hinweis auf die zweite Person.
        Der Service-Guard (``approve_deletion`` mit ``requested_by ==
        reviewer``) ist auf Unit-Ebene abgedeckt.
        """
        _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        authenticated_page.goto(f"{base_url}/deletion-requests/")
        authenticated_page.wait_for_load_state("domcontentloaded")

        own_card = authenticated_page.locator(
            "[data-testid='deletion-request-card']:has([data-testid='deletion-own-request-hint'])"
        ).first
        own_card.wait_for(state="visible", timeout=5000)
        assert own_card.locator("[data-testid='deletion-review-link']").count() == 0

    def test_deletion_request_review_page_shows_event_details(self, authenticated_page, lead_page, base_url):
        """Review-Seite zeigt Event-Details für informierte Entscheidung."""
        event_path = _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        lead_page.goto(f"{base_url}/deletion-requests/")
        lead_page.wait_for_load_state("domcontentloaded")

        card = _find_deletion_request_card(lead_page, event_path)
        card.wait_for(state="visible", timeout=5000)
        review_link = card.locator("[data-testid='deletion-review-link']")
        assert review_link.is_visible()
        review_link.click()
        lead_page.wait_for_url(re.compile(r"/deletion-requests/[0-9a-f-]+/review/$"))

        has_context = (
            lead_page.locator("text=Beantragt von").count() > 0
            or lead_page.locator("text=Begründung").count() > 0
            or lead_page.locator("text=Stern-42").count() > 0
        )
        assert has_context


class TestDeletionRequestList:
    """Löschanträge-Liste → Lead sieht offene Anträge."""

    def test_deletion_request_list_accessible(self, authenticated_page, base_url):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        loeschantraege_link = nav.locator("a:has-text('Löschanträge')")

        if loeschantraege_link.count() > 0:
            loeschantraege_link.click()
            page.wait_for_url(re.compile(r"/deletion-requests/$"))
            assert page.locator("h1").inner_text() == "Löschanträge"
            assert page.locator("text=Ausstehend").first.is_visible()
