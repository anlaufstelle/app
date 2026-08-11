"""E2E: Sichtbarkeit und Auflösbarkeit des Personen-Löschworkflows.

Refs #1736 (Status am
Datensatz, Rückkanal für die Antragstellerin) und
#1737 (handlungs-
leitende Blocker-Meldung, Papierkorb-Aktion „Jetzt endgültig anonymisieren").

Jeder Test legt sich eine eigene Person an (eindeutiges Pseudonym), statt
Seed-Datensätze zu mutieren — die E2E-DB wird von mehreren Modulen geteilt.
Der Bestätigungsdialog der Papierkorb-Aktion läuft über ``window.confirm``
(``data-confirm`` + ``confirm-action.js``, CSP-konform) und braucht deshalb
einen ``page.once("dialog", …)``-Handler, sonst blockiert der Klick.
"""

import re
import uuid

import pytest

# Personen-Dokumentationstypen legt der Seed nicht an (nur entity_type=event) —
# der etablierte Config-UI-Helfer legt bei Bedarf einen eigenen an.
from .test_client_dynamic_fields import _create_entity_doctype

pytestmark = pytest.mark.e2e


def _create_client(page, base_url, prefix="E2E-Del"):
    """Neue Person über das Formular anlegen; liefert (pseudonym, detail_url)."""
    pseudonym = f"{prefix}-{uuid.uuid4().hex[:6]}"
    page.goto(f"{base_url}/clients/new/")
    page.fill('input[name="pseudonym"]', pseudonym)
    page.locator("#main-content button[type='submit']").click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
    return pseudonym, page.url


def _request_deletion(page, detail_url, reason):
    """Löschantrag über die Antragsseite stellen (Vier-Augen-Antrag).

    Die Aktion existiert in Desktop- und Mobil-Leiste (zwei Treffer); statt
    einen davon per ``.first`` zu greifen, wird die Antragsseite direkt
    angesteuert — dass die Aktion überhaupt (bzw. nicht mehr) angeboten wird,
    prüft ``TestPendingRequestIsVisible`` gezielt über die data-testids.
    """
    page.goto(detail_url.rstrip("/") + "/delete/")
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/delete/$"))
    page.locator("textarea[name='reason']").fill(reason)
    page.locator("#main-content button[type='submit']").click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))


def _approve_pending_request(lead, base_url, pseudonym):
    """Leitung genehmigt den Antrag zu genau diesem Pseudonym."""
    lead.goto(f"{base_url}/deletion-requests/")
    lead.wait_for_load_state("domcontentloaded")
    card = lead.locator(
        f"[data-testid='deletion-request-card']:has([data-testid='deletion-target-link']:text-is('{pseudonym}'))"
    )
    card.locator("[data-testid='deletion-review-link']").click()
    lead.wait_for_url(re.compile(r"/review/$"))
    lead.locator("button:has-text('Genehmigen')").click()
    lead.wait_for_url(re.compile(r"/deletion-requests/$"))


class TestPendingRequestIsVisible:
    """Refs #1736 (A): der Wartezustand steht am Datensatz, nicht nur in der
    entscheidenden Rolle."""

    def test_badge_and_banner_appear_on_detail(self, staff_page, base_url):
        page = staff_page
        _pseudonym, detail_url = _create_client(page, base_url)
        assert page.locator("[data-testid='client-deletion-pending-badge']").count() == 0

        _request_deletion(page, detail_url, "E2E: Status-Badge")

        assert page.locator("[data-testid='client-deletion-pending-badge']").first.is_visible()
        assert page.locator("[data-testid='client-deletion-pending-banner']").first.is_visible()
        # Aktion ist ersetzt, nicht mehr klickbar.
        assert page.locator("[data-testid='client-delete-request-pending']").count() >= 1
        assert page.locator("[data-testid='client-delete-request-btn']").count() == 0

    def test_badge_appears_in_client_list(self, staff_page, base_url):
        page = staff_page
        pseudonym, detail_url = _create_client(page, base_url)
        _request_deletion(page, detail_url, "E2E: Listen-Badge")

        page.goto(f"{base_url}/clients/?q={pseudonym}")
        page.wait_for_load_state("domcontentloaded")
        row = page.locator(f"[data-testid='client-row'][data-pseudonym='{pseudonym}']")
        assert row.locator("[data-testid='client-row-deletion-pending-badge']").first.is_visible()

    def test_second_request_is_not_reported_as_success(self, staff_page, base_url):
        page = staff_page
        _pseudonym, detail_url = _create_client(page, base_url)
        _request_deletion(page, detail_url, "E2E: erster Grund")

        # Direkter Aufruf der Antragsseite: statt des Formulars steht dort der
        # Hinweis auf den laufenden Antrag.
        page.goto(detail_url.rstrip("/") + "/delete/")
        assert page.locator("[data-testid='delete-request-already-pending']").first.is_visible()
        assert page.locator("textarea[name='reason']").count() == 0


class TestRequesterFeedbackChannel:
    """Refs #1736 (B): die Antragstellerin findet ihren Antrag wieder."""

    def test_navigation_and_list_open_for_requester(self, staff_page, base_url):
        page = staff_page
        pseudonym, detail_url = _create_client(page, base_url)
        _request_deletion(page, detail_url, "E2E: Rückkanal")

        # Banner verlinkt in die Liste; die Navigation trägt denselben Einstieg.
        page.goto(detail_url)
        assert page.locator("[data-testid='client-deletion-pending-link']").first.is_visible()

        # Refs #1813: „Löschanträge" ist kein flacher Sidebar-Eintrag mehr,
        # sondern ein Reiter des Hubs „Datenschutz". Fuer die Fachkraft mit
        # eigenem Antrag ist es der EINZIGE sichtbare Reiter — der Hub leitet
        # deshalb direkt auf die Liste weiter (Einzelreiter-Weiterleitung,
        # ``nav.first_visible_tab``). Genau dieser Rueckkanal wird geprueft.
        # Der Desktop-Testid ist eindeutig (mobil traegt der Eintrag
        # mobile-nav-datenschutz) — Strict-Mode-Klick ohne .first-Griff.
        page.locator("[data-testid='nav-datenschutz']").click()
        page.wait_for_url(re.compile(r"/deletion-requests/"))
        page.wait_for_load_state("domcontentloaded")
        assert page.locator(f"[data-testid='deletion-target-link']:text-is('{pseudonym}')").first.is_visible()
        # Eigene Anträge tragen den Hinweis auf die Zweitbestätigung.
        assert page.locator("[data-testid='deletion-own-request-hint']").count() >= 1


class TestTrashAnonymizeAction:
    """Refs #1737 (A/B): Blockade benennen und ohne Datenresurrektion auflösen."""

    def test_anonymize_now_requires_confirmation_and_redacts(self, staff_page, lead_page, authenticated_page, base_url):
        pseudonym, detail_url = _create_client(staff_page, base_url, prefix="E2E-Anon")
        _request_deletion(staff_page, detail_url, "E2E: Papierkorb-Aktion")
        _approve_pending_request(lead_page, base_url, pseudonym)

        admin = authenticated_page
        admin.goto(f"{base_url}/clients/trash/")
        admin.wait_for_load_state("domcontentloaded")
        # Papierkorb-Semantik ist erklärt (Refs #1736 F).
        assert admin.locator("[data-testid='client-trash-semantics-hint']").first.is_visible()

        row = admin.locator(f"tr:has-text('{pseudonym}')").first
        # window.confirm: ohne Handler blockiert der Klick den Test.
        admin.once("dialog", lambda dialog: dialog.accept())
        row.locator("[data-testid='client-anonymize-now-btn']").click()
        admin.wait_for_url(re.compile(r"/clients/trash/$"))

        # Pseudonym ist ersetzt — der Datensatz bleibt im Papierkorb.
        assert admin.locator(f"text={pseudonym}").count() == 0
        assert admin.locator("text=Gelöscht-").first.is_visible()

    def test_dismissed_dialog_keeps_the_record(self, staff_page, lead_page, authenticated_page, base_url):
        pseudonym, detail_url = _create_client(staff_page, base_url, prefix="E2E-Keep")
        _request_deletion(staff_page, detail_url, "E2E: Abbruch")
        _approve_pending_request(lead_page, base_url, pseudonym)

        admin = authenticated_page
        admin.goto(f"{base_url}/clients/trash/")
        admin.wait_for_load_state("domcontentloaded")
        row = admin.locator(f"tr:has-text('{pseudonym}')").first
        admin.once("dialog", lambda dialog: dialog.dismiss())
        row.locator("[data-testid='client-anonymize-now-btn']").click()

        admin.goto(f"{base_url}/clients/trash/")
        assert admin.locator(f"tr:has-text('{pseudonym}')").count() == 1

    def test_edit_deeplink_of_trashed_client_is_404(self, staff_page, lead_page, authenticated_page, base_url):
        """Refs #1737 (E): Bearbeiten am Papierkorb vorbei ist geschlossen."""
        pseudonym, detail_url = _create_client(staff_page, base_url, prefix="E2E-Gate")
        _request_deletion(staff_page, detail_url, "E2E: Deep-Link-Gate")
        _approve_pending_request(lead_page, base_url, pseudonym)

        response = authenticated_page.goto(detail_url.rstrip("/") + "/edit/")
        assert response is not None
        assert response.status == 404


class TestConfigBlockerNamesTheWayOut:
    """Refs #1737 (A): der Lösch-Dialog zeigt den Papierkorb-Anteil + Ausweg."""

    def test_document_type_delete_dialog_links_to_trash(self, staff_page, lead_page, authenticated_page, base_url):
        # Person mit Personen-Dokumentationstyp in den Papierkorb bringen. Der
        # Seed legt nur ``entity_type=event``-Typen an, das Personen-Select auf
        # ``/clients/new/`` hat dann <2 Optionen — deshalb den Typ hier selbst
        # anlegen (Refs #1737 A), statt den Test auf den Seed hoffen zu lassen
        # (frueher: ``pytest.skip`` → der Test lief nie).
        admin = authenticated_page
        doc_type_label = f"E2E-Blocker-Typ-{uuid.uuid4().hex[:6]}"
        _create_entity_doctype(admin, base_url, doc_type_label, "client")

        admin.goto(f"{base_url}/clients/new/")
        pseudonym = f"E2E-Blocker-{uuid.uuid4().hex[:6]}"
        admin.fill('input[name="pseudonym"]', pseudonym)
        admin.locator("select[name='document_type']").select_option(label=doc_type_label)
        admin.locator("#main-content button[type='submit']").click()
        admin.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        detail_url = admin.url

        _request_deletion(staff_page, detail_url, "E2E: Blocker-Meldung")
        _approve_pending_request(lead_page, base_url, pseudonym)

        admin.goto(f"{base_url}/config/document-types/?q={doc_type_label}")
        admin.wait_for_load_state("domcontentloaded")
        row = admin.locator(
            f"[data-testid='doctype-row']:has([data-testid='doctype-detail-link']:text-is('{doc_type_label}'))"
        )
        detail_href = row.locator("[data-testid='doctype-detail-link']").get_attribute("href")
        admin.goto(f"{base_url}{detail_href}delete/")

        hint = admin.locator("[data-testid='config-delete-trash-hint']")
        assert hint.first.is_visible()
        assert admin.locator("[data-testid='config-delete-trash-link']").first.is_visible()
        assert admin.locator("[data-testid='doctype-delete-confirm']").first.is_disabled()

    def test_client_list_link_reaches_trash(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")
        page.locator("[data-testid='client-trash-link']").click()
        page.wait_for_url(re.compile(r"/clients/trash/$"))
        assert page.locator("[data-testid='client-trash-semantics-hint']").first.is_visible()
