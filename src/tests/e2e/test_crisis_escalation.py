"""E2E-Test: Krisen-Eskalations-Workflow.

Refs Matrix SMK-A-CRIS-01, Refs #922 / #926 .

Eine Fachkraft erfasst ein Krisengespräch (DocumentType mit system_type="crisis",
sensitivity ≥ ELEVATED) für eine bekannte Person in unter 30 Sekunden. Anschließend
wird verifiziert, dass das Event existiert und nur Rollen mit ausreichender
Sensitivity-Berechtigung den Inhalt sehen.
"""

import re
import time

import pytest

from tests.e2e._selectors import find_client_link

pytestmark = pytest.mark.e2e


class TestCrisisEscalationWorkflow:
    """Quick-Capture + Sensitivity-Gate für Krisen-Events."""

    @pytest.mark.smoke
    def test_staff_can_record_crisis_event_quickly(self, authenticated_page, base_url):
        """Schnellerfassung eines Krisengesprächs mit Pseudonym Stern-42 < 30 s."""
        page = authenticated_page
        start = time.monotonic()

        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        # Krisengespräch hat einen ELEVATED-Sensitivity-DocumentType (Seed-Daten).
        page.select_option("select[name='document_type']", label="Krisengespräch")
        page.wait_for_load_state("domcontentloaded")

        # Klientel per Autocomplete wählen.
        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")
        page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
        page.locator("button:has-text('Stern-42')").click()

        # Krisen-Notiz ausfüllen (DocumentTypeField mit slug "notiz-krise",
        # encrypted=True). Lokalisierter Label-Text "Notiz (Krise)".
        page.fill("textarea[name='notiz-krise']", "E2E-Test: deeskalierende Maßnahmen vereinbart.")

        page.locator("#event-submit-btn").click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        elapsed = time.monotonic() - start
        assert elapsed < 30, f"Krisen-Erfassung dauerte {elapsed:.1f}s — Ziel < 30 s"
        # Detail-Seite zeigt den Krisen-Klienten und den DocType-Hinweis an.
        assert page.locator("text=Stern-42").first.is_visible()
        assert page.locator("text=Krisengespräch").first.is_visible()

    def test_assistant_cannot_see_crisis_event_detail(self, authenticated_page, assistant_page, base_url):
        """Eskalierter Sensitivity-DocType ist für Assistenz nicht sichtbar.

        Admin legt das Event an, Assistant ruft Detail-URL direkt auf — laut
        Rollen-Sensitivity-Matrix (`allowed_sensitivities_for_user`) ist
        ASSISTANT ausgeschlossen → 403 oder 404 (je nach View-Pfad).
        """
        admin = authenticated_page

        # Event als Admin anlegen.
        admin.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        admin.select_option("select[name='document_type']", label="Krisengespräch")
        admin.wait_for_load_state("domcontentloaded")

        autocomplete = admin.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")
        admin.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
        admin.locator("button:has-text('Stern-42')").click()
        admin.fill("textarea[name='notiz-krise']", "E2E-Test: Sensitivity-Gate")
        admin.locator("#event-submit-btn").click()
        admin.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))
        event_url = admin.url

        # Assistant öffnet die Detail-Seite direkt — muss blockiert sein.
        resp = assistant_page.goto(event_url)
        assert resp.status in (403, 404), (
            f"Assistenz erwartete 403/404 auf Krisen-Event, bekam {resp.status} (URL: {event_url})"
        )

    def test_client_detail_lists_crisis_event_for_lead(self, lead_page, base_url):
        """Klient-Detail einer Person mit Krisen-Event zeigt den Event-Eintrag.

        Lead-Rolle darf ELEVATED-Sensitivity sehen. Der Test verifiziert, dass
        nach einem Krisen-Eintrag das Klient-Detail die Krise im Aktivitätslog
        listet — die Eskalations-Information bleibt für Fall-Verantwortliche
        sichtbar.
        """
        # Annahme: Seed enthält bereits Krisen-Events für Stern-42 oder ein
        # vorheriger Test hat eines angelegt. Falls nicht, legen wir hier eins an.
        lead_page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        lead_page.select_option("select[name='document_type']", label="Krisengespräch")
        lead_page.wait_for_load_state("domcontentloaded")

        autocomplete = lead_page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")
        lead_page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
        lead_page.locator("button:has-text('Stern-42')").click()
        lead_page.fill("textarea[name='notiz-krise']", "E2E-Test: Lead-Sicht")
        lead_page.locator("#event-submit-btn").click()
        lead_page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # Zurück zum Klient-Detail navigieren.
        lead_page.goto(f"{base_url}/clients/?q=Stern-42", wait_until="domcontentloaded")
        find_client_link(lead_page, "Stern-42").click()
        lead_page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

        # Aktivitätslog / Events-Sektion zeigt mindestens ein Krisengespräch.
        assert lead_page.locator("text=Krisengespräch").first.is_visible()
