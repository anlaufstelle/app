"""E2E: Vier-Augen-Lösch-Workflow für Personen (Refs #626).

Manuell verifiziert per Playwright am 2026-04-30 — Tests aus den
beobachteten Schritten abgeleitet (CLAUDE.md "E2E-Tests aus
manueller Verifikation ableiten").
"""

import re

import pytest

pytestmark = pytest.mark.e2e


def _click_first_client_link(page, base_url):
    """Klickt den ersten Personen-Link in der Liste und wartet auf Detail."""
    page.goto(f"{base_url}/clients/")
    page.wait_for_load_state("domcontentloaded")
    # ``.client-list``-Scope vermeidet Sidebar/Nav-Links und ist gegenueber
    # Pseudonym-Format (Umlaute, neue Spitznamen-Pools) robust — Refs #761.
    page.locator(".client-list a[href^='/clients/']").first.click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))


def test_staff_can_request_client_deletion(staff_page, base_url):
    """Fachkraft sieht den Löschen-Beantragen-Button und kann einen Antrag stellen."""
    page = staff_page
    _click_first_client_link(page, base_url)

    # Button via data-testid (stabil gegenüber Layout-Änderungen).
    page.locator("[data-testid='client-delete-request-btn']").first.click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/delete/$"))

    page.locator("textarea[name='reason']").fill("E2E: Fachkraft beantragt Löschung")
    page.locator("#main-content button[type='submit']").click()

    # Redirect auf Detail mit Erfolgsmeldung
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
    assert (
        page.locator("text=Loeschantrag gestellt").first.is_visible()
        or page.locator("text=Löschantrag").first.is_visible()
    )


def test_assistant_does_not_see_delete_request_button(assistant_page, base_url):
    """Assistenten-Rolle hat den Löschen-Beantragen-Button nicht."""
    page = assistant_page
    _click_first_client_link(page, base_url)
    assert page.locator("[data-testid='client-delete-request-btn']").count() == 0


def test_full_four_eyes_workflow(staff_page, lead_page, authenticated_page, base_url):
    """End-to-End: Antrag → Genehmigung → Soft-Delete sichtbar → Restore.

    Drei Rollen-Pages werden parallel verwendet — entspricht dem manuellen
    Klickpfad: miriam beantragt, thomas genehmigt, admin restored.
    """
    # 1. Fachkraft beantragt Löschung
    page = staff_page
    page.goto(f"{base_url}/clients/")
    page.wait_for_load_state("domcontentloaded")
    # Zweite Person aus der Liste — die erste wird bereits in
    # ``test_staff_can_request_client_deletion`` angefasst (parallele E2E-Workers
    # haben separate DBs, der Index hier dient nur der Variation).
    page.locator(".client-list a[href^='/clients/']").nth(1).click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
    client_url = page.url
    client_pseudonym_match = page.locator("h1").first.text_content()
    pseudonym = client_pseudonym_match.strip() if client_pseudonym_match else ""

    page.locator("[data-testid='client-delete-request-btn']").first.click()
    page.wait_for_url(re.compile(r"/delete/$"))
    page.locator("textarea[name='reason']").fill("E2E full workflow")
    page.locator("#main-content button[type='submit']").click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

    # 2. Leitung sieht den Antrag in der Liste und genehmigt
    lead = lead_page
    lead.goto(f"{base_url}/deletion-requests/")
    lead.wait_for_load_state("domcontentloaded")
    # Erster Pruefen-Link in der Pending-Section
    lead.locator("a:has-text('Prüfen')").first.click()
    lead.wait_for_url(re.compile(r"/review/$"))
    assert lead.locator("text=Person-Löschantrag").first.is_visible()

    lead.locator("button:has-text('Genehmigen')").click()
    lead.wait_for_url(re.compile(r"/deletion-requests/$"))

    # 3. Detail-URL der Person muss jetzt 404 liefern (Soft-Delete)
    page.goto(client_url)
    assert page.locator("text=Page not found").first.is_visible() or "404" in page.title()

    # 4. Admin sieht die Person im Papierkorb und stellt sie wieder her
    admin = authenticated_page
    admin.goto(f"{base_url}/clients/trash/")
    assert admin.locator(f"text={pseudonym}").first.is_visible()
    admin.locator("button:has-text('Wiederherstellen')").first.click()
    admin.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

    # 5. Person ist wieder in der Liste sichtbar
    admin.goto(f"{base_url}/clients/")
    assert admin.locator(f"text={pseudonym}").first.is_visible()
