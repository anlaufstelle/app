"""E2E-Tests: Klientel-Datenauskunft (Art. 15/20 DSGVO)."""

import subprocess
import uuid
from pathlib import Path

import pytest

from tests.e2e._helpers import enter_sudo_mode
from tests.e2e._helpers import python_bin as _python
from tests.e2e._selectors import find_client_link, open_desktop_overflow

pytestmark = pytest.mark.e2e


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def _seed_qualified_client(e2e_env):
    """Frische Person mit ``contact_stage='qualified'`` in Hauptstelle anlegen (Refs #1765).

    Der Audit-Auszug-Test brauchte bisher „Stern-42" aus dem Seed-Katalog, der
    zufaellig ebenfalls ``QUALIFIED`` ist — der Zugriffsprotokoll-Eintrag
    (``VIEW_QUALIFIED``, ausgeloest beim Aufruf des Personen-Details, Refs
    core/views/clients.py) entstand also nur als Nebeneffekt einer Seed-
    Entscheidung, die sich jederzeit aendern koennte, ohne dass der Test es
    bemerkt (leere Liste -> die ``for``-Schleife der Assertion liefe schlicht
    nicht an). Eine dedizierte, frisch angelegte Person macht den Eintrag
    unabhaengig vom Bestandskatalog deterministisch.
    """
    suffix = uuid.uuid4().hex[:8]
    pseudonym = f"E2E-Auskunft-{suffix}"
    script = (
        "from core.models import Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='emma');"
        f" Client.objects.create(facility=f, contact_stage='qualified', pseudonym='{pseudonym}', created_by=u)"
    )
    _shell(e2e_env, script)
    return pseudonym


class TestClientExportDropdown:
    """Datenauskunft-Aktionen auf der Klientel-Detailseite.

    Refs #1813: Ein eigenes „Datenauskunft"-Dropdown gibt es nicht mehr — die
    beiden Auskunfts-Downloads sind Eintraege des gemeinsamen ⋮-Menues „Weitere
    Aktionen" (Konzept #1801 §6.4: eine Overflow-Flaeche je Detailseite). Die
    Rollen-Aussage bleibt unveraendert: nur Leitung/Admin sehen sie.
    """

    def test_lead_sees_dropdown(self, lead_page, base_url):
        """Leitung sieht beide Auskunfts-Eintraege im ⋮-Menue."""
        lead_page.goto(f"{base_url}/clients/")
        find_client_link(lead_page, "Stern-42").click()
        lead_page.wait_for_url(lambda url: "/clients/" in url and "Stern-42" not in url or "/clients/" in url)
        overflow = open_desktop_overflow(lead_page, "client")
        assert overflow.locator("[data-testid='client-export-json-link']").is_visible()
        assert overflow.locator("[data-testid='client-export-pdf-link']").is_visible()

    def test_staff_does_not_see_dropdown(self, staff_page, base_url):
        """Fachkraft hat die Auskunfts-Eintraege nicht — auch nicht im ⋮-Menue."""
        staff_page.goto(f"{base_url}/clients/")
        find_client_link(staff_page, "Stern-42").click()
        staff_page.wait_for_url(lambda url: "/clients/" in url)
        # Gegenprobe mit geoeffnetem Menue: die Eintraege fehlen im Markup,
        # nicht bloss hinter einem zugeklappten Panel.
        open_desktop_overflow(staff_page, "client")
        assert staff_page.locator("[data-testid='client-export-json-link']").count() == 0
        assert staff_page.locator("[data-testid='client-export-pdf-link']").count() == 0


class TestClientExportJSON:
    """JSON-Export herunterladen."""

    def test_lead_can_download_json(self, lead_page, base_url):
        """Leitung kann JSON-Export herunterladen."""
        # Datenauskunft ist sudo-pflichtig (Refs #683) — vor dem Download einmal aktivieren.
        enter_sudo_mode(lead_page, base_url)

        lead_page.goto(f"{base_url}/clients/")
        find_client_link(lead_page, "Stern-42").click()
        lead_page.wait_for_url(lambda url: "/clients/" in url)
        # Refs #1813: Auskunft liegt im gemeinsamen ⋮-Menue der Aktionsleiste.
        desktop = open_desktop_overflow(lead_page, "client")

        with lead_page.expect_download() as download_info:
            desktop.locator("[data-testid='client-export-json-link']").click()
        download = download_info.value
        assert "datenauskunft" in download.suggested_filename
        assert download.suggested_filename.endswith(".json")

    def test_downloaded_json_covers_all_categories(self, lead_page, base_url, e2e_env):
        """Die heruntergeladene Datei traegt jede Auskunfts-Kategorie (Refs #1752).

        Refs #1765: der Audit-Auszug braucht eine Person, deren Zugriffs-
        protokoll-Eintrag deterministisch entsteht (nicht bloss zufaellig, weil
        der Katalog-Client „Stern-42" ebenfalls ``QUALIFIED`` ist) — sonst
        pruefte die Assertion unten ueber ``payload["audit_trail"]`` nichts,
        sobald die Liste leer waere.
        """
        import json

        pseudonym = _seed_qualified_client(e2e_env)
        enter_sudo_mode(lead_page, base_url)

        lead_page.goto(f"{base_url}/clients/")
        # Der Aufruf des Personen-Details loggt fuer QUALIFIED-Personen einen
        # VIEW_QUALIFIED-Audit-Eintrag (core/views/clients.py) — exakt der
        # garantierte Eintrag, den der Audit-Auszug unten braucht.
        find_client_link(lead_page, pseudonym).click()
        lead_page.wait_for_url(lambda url: "/clients/" in url)
        desktop = open_desktop_overflow(lead_page, "client")

        with lead_page.expect_download() as download_info:
            desktop.locator("[data-testid='client-export-json-link']").click()
        payload = json.loads(Path(download_info.value.path()).read_text(encoding="utf-8"))

        assert set(payload) >= {
            "client",
            "events",
            "deleted_events",
            "cases",
            "episodes",
            "outcome_goals",
            "event_history",
            "deletion_requests",
            "legal_holds",
            "retention_proposals",
            "work_items",
            "activities",
            "recent_visits",
            "audit_trail",
            "export_meta",
        }
        # Stammdaten tragen die mit #1752 ergaenzten Pflichtfelder.
        assert {"notes", "is_deleted", "deleted_at", "deleted_by", "k_anonymized", "updated_at"} <= set(
            payload["client"]
        )
        # Refs #1765: explizit non-vacuous — vorher konnte eine leere Liste die
        # folgende Schleife stillschweigend ueberspringen und der Test war
        # tautologisch gruen, ohne die Redaktion je zu pruefen.
        assert payload["audit_trail"], "erwartet mindestens den VIEW_QUALIFIED-Eintrag der frisch geseedeten Person"
        # Der Audit-Auszug bleibt ohne Beschaeftigten-Identitaet.
        for entry in payload["audit_trail"]:
            assert set(entry) == {"action", "category", "target_type", "timestamp"}


class TestClientExportPDF:
    """PDF-Export herunterladen."""

    @pytest.mark.smoke
    def test_lead_can_download_pdf(self, lead_page, base_url):
        """Leitung kann PDF-Export herunterladen."""
        # Datenauskunft ist sudo-pflichtig (Refs #683) — vor dem Download einmal aktivieren.
        enter_sudo_mode(lead_page, base_url)

        lead_page.goto(f"{base_url}/clients/")
        find_client_link(lead_page, "Stern-42").click()
        lead_page.wait_for_url(lambda url: "/clients/" in url)
        desktop = open_desktop_overflow(lead_page, "client")

        with lead_page.expect_download() as download_info:
            desktop.locator("[data-testid='client-export-pdf-link']").click()
        download = download_info.value
        assert "datenauskunft" in download.suggested_filename
        assert download.suggested_filename.endswith(".pdf")


class TestClientExportAccessControl:
    """Zugriffskontrolle für Direktzugriff."""

    def test_staff_direct_json_403(self, staff_page, base_url):
        """Fachkraft bekommt 403 bei direktem JSON-Zugriff."""
        staff_page.goto(f"{base_url}/clients/")
        # Click first client to get a valid client detail URL
        find_client_link(staff_page, "Stern-42").click()
        staff_page.wait_for_url(lambda url: "/clients/" in url)
        client_url = staff_page.url
        resp = staff_page.goto(f"{client_url}export/json/")
        assert resp.status == 403
