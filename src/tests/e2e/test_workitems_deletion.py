"""E2E-Tests: 4-Augen-Prinzip bei Löschanträgen.

Testet:
- DeletionRequest erstellen UND reviewen (Genehmigen/Ablehnen)
- Reviewer ≠ Antragsteller wird erzwungen

Bereits abgedeckt in test_stream_d.py:
- WorkItem create, inbox, status HTMX, badge, Hausverbot-Banner
"""

import re

import pytest

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

    def test_lead_can_approve_deletion_request(self, authenticated_page, lead_page, base_url):
        """Lead (thomas) genehmigt von admin gestellten Löschantrag."""
        _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        lead_page.goto(f"{base_url}/deletion-requests/")
        lead_page.wait_for_load_state("domcontentloaded")

        assert lead_page.locator("h1").inner_text() == "Löschanträge"

        review_link = lead_page.locator("a:has-text('Prüfen')").first
        assert review_link.is_visible()
        review_link.click()
        lead_page.wait_for_url(re.compile(r"/deletion-requests/[0-9a-f-]+/review/$"))

        approve_btn = lead_page.locator("button[value='approve'], button:has-text('Genehmigen')")
        approve_btn.first.click()
        lead_page.wait_for_load_state("domcontentloaded")

        assert lead_page.locator("text=genehmigt").count() > 0 or lead_page.url == f"{base_url}/"

    def test_lead_can_reject_deletion_request(self, authenticated_page, lead_page, base_url):
        """Lead (thomas) lehnt von admin gestellten Löschantrag ab."""
        _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        lead_page.goto(f"{base_url}/deletion-requests/")
        lead_page.wait_for_load_state("domcontentloaded")

        review_link = lead_page.locator("a:has-text('Prüfen')").first
        assert review_link.is_visible()
        review_link.click()
        lead_page.wait_for_url(re.compile(r"/deletion-requests/[0-9a-f-]+/review/$"))

        reject_btn = lead_page.locator("button[value='reject'], button:has-text('Ablehnen')")
        reject_btn.first.click()
        lead_page.wait_for_load_state("domcontentloaded")

        assert lead_page.locator("text=abgelehnt").count() > 0 or lead_page.url == f"{base_url}/"


class TestReviewerNotRequester:
    """Reviewer darf nicht der Antragsteller sein."""

    def test_requester_cannot_approve_own_request(self, authenticated_page, base_url):
        """Admin kann eigenen Löschantrag nicht genehmigen."""
        _create_qualified_event_and_request_deletion(authenticated_page, base_url)

        authenticated_page.goto(f"{base_url}/deletion-requests/")
        authenticated_page.wait_for_load_state("domcontentloaded")

        review_link = authenticated_page.locator("a:has-text('Prüfen')").first
        if review_link.count() == 0:
            return  # Kein Link = korrekt blockiert

        review_link.click()
        authenticated_page.wait_for_url(re.compile(r"/deletion-requests/[0-9a-f-]+/review/$"))

        approve_btn = authenticated_page.locator("button[value='approve'], button:has-text('Genehmigen')")
        if approve_btn.count() > 0:
            approve_btn.first.click()
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

        review_link = lead_page.locator("a:has-text('Prüfen')").first
        assert review_link.is_visible()
        review_link.click()
        lead_page.wait_for_url(re.compile(r"/deletion-requests/[0-9a-f-]+/review/$"))

        has_context = (
            lead_page.locator("text=Beantragt von").count() > 0
            or lead_page.locator("text=Begründung").count() > 0
            or lead_page.locator("text=Stern-42").count() > 0
        )
        assert has_context
