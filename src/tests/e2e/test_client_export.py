"""E2E-Tests: Klientel-Datenauskunft (Art. 15/20 DSGVO)."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.e2e._helpers import enter_sudo_mode
from tests.e2e._selectors import find_client_link

pytestmark = pytest.mark.e2e


def _python():
    return ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable


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
    """Datenauskunft-Dropdown auf der Klientel-Detailseite."""

    def test_lead_sees_dropdown(self, lead_page, base_url):
        """Leitung sieht den Datenauskunft-Button."""
        lead_page.goto(f"{base_url}/clients/")
        find_client_link(lead_page, "Stern-42").click()
        lead_page.wait_for_url(lambda url: "/clients/" in url and "Stern-42" not in url or "/clients/" in url)
        btn = lead_page.locator("button:has-text('Datenauskunft')")
        assert btn.count() == 1

    def test_staff_does_not_see_dropdown(self, staff_page, base_url):
        """Fachkraft sieht keinen Datenauskunft-Button."""
        staff_page.goto(f"{base_url}/clients/")
        find_client_link(staff_page, "Stern-42").click()
        staff_page.wait_for_url(lambda url: "/clients/" in url)
        btn = staff_page.locator("button:has-text('Datenauskunft')")
        assert btn.count() == 0


class TestClientExportJSON:
    """JSON-Export herunterladen."""

    def test_lead_can_download_json(self, lead_page, base_url):
        """Leitung kann JSON-Export herunterladen."""
        # Datenauskunft ist sudo-pflichtig (Refs #683) — vor dem Download einmal aktivieren.
        enter_sudo_mode(lead_page, base_url)

        lead_page.goto(f"{base_url}/clients/")
        find_client_link(lead_page, "Stern-42").click()
        lead_page.wait_for_url(lambda url: "/clients/" in url)
        desktop = lead_page.locator(".hidden.md\\:flex")
        desktop.locator("button:has-text('Datenauskunft')").click()

        with lead_page.expect_download() as download_info:
            desktop.locator("a:has-text('JSON-Export')").click()
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
        desktop = lead_page.locator(".hidden.md\\:flex")
        desktop.locator("button:has-text('Datenauskunft')").click()

        with lead_page.expect_download() as download_info:
            desktop.locator("a:has-text('JSON-Export')").click()
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
        desktop = lead_page.locator(".hidden.md\\:flex")
        desktop.locator("button:has-text('Datenauskunft')").click()

        with lead_page.expect_download() as download_info:
            desktop.locator("a:has-text('PDF-Export')").click()
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
