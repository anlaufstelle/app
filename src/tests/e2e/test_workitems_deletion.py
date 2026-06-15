"""E2E-Tests: Löschanträge — 4-Augen-Prinzip und Liste."""

import re

import pytest

from tests.e2e._selectors import (
    find_deletion_approve_button,
    find_deletion_reject_button,
    find_first_deletion_review_link,
)

pytestmark = pytest.mark.e2e

SUBMIT = "#main-content button[type='submit']"


def _create_qualified_event_and_request_deletion(page, base_url):
    """Event für Stern-42 erstellen und Löschantrag stellen."""
    page.goto(f"{base_url}/events/new/")
    page.wait_for_load_state("domcontentloaded")

    page.select_option("select[name='document_type']", label="Kontakt")
    page.wait_for_load_state("domcontentloaded")

    autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
    autocomplete.fill("Stern")
    page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
    page.locator("button:has-text('Stern-42')").click()

    page.locator(SUBMIT).click()
    page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

    assert re.search(r"/events/[0-9a-f-]+/$", page.url), f"Erwartete Event-Detail, bekam {page.url}"

    page.click("a:has-text('Löschen')")
    page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/delete/$"))

    reason_field = page.locator("textarea[name='reason']")
    if reason_field.count() > 0:
        reason_field.fill("E2E-Test: 4-Augen-Prinzip")

    page.locator(SUBMIT).click()
    page.wait_for_url(lambda url: "/delete/" not in url)

    assert page.locator("text=Löschantrag").first.is_visible()


class TestFourEyesPrincipleReview:
    """4-Augen-Prinzip: Löschantrag genehmigen/ablehnen."""

    @pytest.mark.smoke
    def test_lead_can_approve_deletion_request(self, authenticated_page, lead_page, base_url):
        """Lead (thomas) genehmigt von admin gestellten Löschantrag."""
        _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        lead_page.goto(f"{base_url}/deletion-requests/")
        lead_page.wait_for_load_state("domcontentloaded")

        assert lead_page.locator("h1").inner_text() == "Löschanträge"

        review_link = find_first_deletion_review_link(lead_page)
        assert review_link.is_visible()
        review_link.click()
        lead_page.wait_for_url(re.compile(r"/deletion-requests/[0-9a-f-]+/review/$"))

        find_deletion_approve_button(lead_page).click()

        # Refs #1119: Event-Genehmigung führt konsistent zurück in die
        # Löschantragsliste (vorher überraschend in den Zeitstrom).
        lead_page.wait_for_url(re.compile(r"/deletion-requests/$"), timeout=10000)
        assert lead_page.locator("text=Genehmigt").count() > 0

    def test_lead_can_reject_deletion_request(self, authenticated_page, lead_page, base_url):
        """Lead (thomas) lehnt von admin gestellten Löschantrag ab.

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
        _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        lead_page.goto(f"{base_url}/deletion-requests/")
        lead_page.wait_for_load_state("domcontentloaded")

        review_link = find_first_deletion_review_link(lead_page)
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
        """Admin kann eigenen Löschantrag nicht genehmigen."""
        _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        authenticated_page.goto(f"{base_url}/deletion-requests/")
        authenticated_page.wait_for_load_state("domcontentloaded")

        review_link = find_first_deletion_review_link(authenticated_page)
        if review_link.count() == 0:
            return  # Kein Link = korrekt blockiert

        review_link.click()
        authenticated_page.wait_for_url(re.compile(r"/deletion-requests/[0-9a-f-]+/review/$"))

        approve_btn = find_deletion_approve_button(authenticated_page)
        if approve_btn.count() > 0:
            approve_btn.click()
            authenticated_page.wait_for_load_state("domcontentloaded")
            assert (
                authenticated_page.locator("text=eigenen").count() > 0
                or authenticated_page.locator("text=nicht genehmigen").count() > 0
                or authenticated_page.locator("[role='alert']").count() > 0
            )

    def test_deletion_request_review_page_shows_event_details(self, authenticated_page, lead_page, base_url):
        """Review-Seite zeigt Event-Details für informierte Entscheidung."""
        _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        lead_page.goto(f"{base_url}/deletion-requests/")
        lead_page.wait_for_load_state("domcontentloaded")

        review_link = find_first_deletion_review_link(lead_page)
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
