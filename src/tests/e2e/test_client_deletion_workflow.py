"""E2E: Vier-Augen-Lösch-Workflow für Personen (Refs #626).

Manuell verifiziert per Playwright am 2026-04-30 — Tests aus den
beobachteten Schritten abgeleitet (Projekt-Konvention: E2E-Tests
aus manueller Verifikation ableiten, nicht vorab schreiben).

Jeder mutierende Test legt sich eine **eigene** Person an (Refs #1787).
Vorher griffen die Tests über die Personenliste die erste bzw. zweite
Person ab — die ist nach ``Client.Meta.ordering = ["pseudonym"]``
deterministisch die Seed-Person ``Blitz-08``. Der dabei entstehende
Löschantrag blieb ausstehend liegen und wurde später von
``test_workitems_deletion.py`` versehentlich genehmigt (dort zeigte
``find_first_deletion_review_link`` auf den ältesten FREMDEN Antrag, weil
eigene Anträge gar keinen Prüfen-Link tragen). ``Blitz-08`` lag danach im
Papierkorb: in der Autocomplete weiterhin sichtbar (die filtert nur
``is_active``), aber jeder Schreibzugriff scheiterte am Widerrufs-Gate
(Refs #1771, ``resolve_writable_client``) — die fünf ``Blitz-08``-Tests in
``test_zeitstrom_events.py`` liefen im Voll-Lauf reproduzierbar in
``wait_for_url``-Timeouts.
"""

import re

import pytest

from .test_client_deletion_status import _create_client

pytestmark = pytest.mark.e2e


def _click_first_client_link(page, base_url):
    """Klickt den ersten Personen-Link in der Liste und wartet auf Detail.

    Nur für **lesende** Tests — mutierende Tests legen sich eine eigene
    Person an (siehe Modul-Docstring).
    """
    page.goto(f"{base_url}/clients/")
    page.wait_for_load_state("domcontentloaded")
    # ``.client-list``-Scope vermeidet Sidebar/Nav-Links und ist gegenueber
    # Pseudonym-Format (Umlaute, neue Spitznamen-Pools) robust — Refs #761.
    page.locator(".client-list a[href^='/clients/']").first.click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))


def test_staff_can_request_client_deletion(staff_page, base_url):
    """Fachkraft sieht den Löschen-Beantragen-Button und kann einen Antrag stellen."""
    page = staff_page
    _create_client(page, base_url, prefix="E2E-Req")

    # Button via data-testid (stabil gegenüber Layout-Änderungen).
    page.locator("[data-testid='client-delete-request-btn']").first.click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/delete/$"))

    page.locator("textarea[name='reason']").fill("E2E: Fachkraft beantragt Löschung")
    page.locator("#main-content button[type='submit']").click()

    # Redirect auf Detail mit Erfolgsmeldung (Refs #1144: korrekter Umlaut)
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
    assert page.locator("text=Löschantrag gestellt").first.is_visible()


def test_assistant_does_not_see_delete_request_button(assistant_page, base_url):
    """Assistenten-Rolle hat den Löschen-Beantragen-Button nicht."""
    page = assistant_page
    _click_first_client_link(page, base_url)
    assert page.locator("[data-testid='client-delete-request-btn']").count() == 0


def test_full_four_eyes_workflow(staff_page, lead_page, authenticated_page, base_url):
    """End-to-End: Antrag → Genehmigung → Soft-Delete sichtbar → Restore.

    Drei Rollen-Pages werden parallel verwendet — entspricht dem manuellen
    Klickpfad: miriam beantragt, emma genehmigt, admin restored.
    """
    # 1. Fachkraft beantragt Löschung — für eine EIGENE Wegwerf-Person
    #    (Refs #1787, Begründung im Modul-Docstring).
    page = staff_page
    pseudonym, client_url = _create_client(page, base_url, prefix="E2E-4Eyes")

    page.locator("[data-testid='client-delete-request-btn']").first.click()
    page.wait_for_url(re.compile(r"/delete/$"))
    page.locator("textarea[name='reason']").fill("E2E full workflow")
    page.locator("#main-content button[type='submit']").click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

    # 2. Leitung genehmigt GENAU diesen Antrag — Karten-Scope
    #    (``data-testid='deletion-request-card'``) statt ``.first``, sonst
    #    entscheidet die Liste über einen fremden Antrag mit.
    lead = lead_page
    lead.goto(f"{base_url}/deletion-requests/")
    lead.wait_for_load_state("domcontentloaded")
    card = lead.locator(f"[data-testid='deletion-request-card']:has-text('{pseudonym}')").first
    card.wait_for(state="visible", timeout=5000)
    card.locator("[data-testid='deletion-review-link']").click()
    lead.wait_for_url(re.compile(r"/review/$"))
    assert lead.locator("text=Person-Löschantrag").first.is_visible()

    lead.locator("button:has-text('Genehmigen')").click()
    lead.wait_for_url(re.compile(r"/deletion-requests/$"))

    # 3. Detail-URL der Person muss jetzt 404 liefern (Soft-Delete)
    page.goto(client_url)
    assert page.locator("text=Page not found").first.is_visible() or "404" in page.title()

    # 4. Admin sieht die Person im Papierkorb und stellt GENAU SIE wieder her
    admin = authenticated_page
    admin.goto(f"{base_url}/clients/trash/")
    row = admin.locator(f"tr:has-text('{pseudonym}')").first
    row.wait_for(state="visible", timeout=5000)
    row.locator("button:has-text('Wiederherstellen')").click()
    admin.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

    # 5. Person ist wieder in der Liste sichtbar
    admin.goto(f"{base_url}/clients/")
    assert admin.locator(f"text={pseudonym}").first.is_visible()
