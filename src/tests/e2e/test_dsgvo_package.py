"""E2E-Tests: DSGVO-Dokumentationspaket."""

import re

import pytest

from ._selectors import find_first_dsgvo_download_link, sidebar_item, subnav_tab

pytestmark = pytest.mark.e2e


class TestDSGVOPackageNavigation:
    """Nav-Einstieg und Seitenaufruf.

    Refs #1813 (Konzept #1801 §4): „DSGVO-Paket" ist kein flacher
    Sidebar-Eintrag mehr, sondern ein Reiter des Bereichs-Hubs „Datenschutz".
    Die Rollen-Aussage bleibt: Anwendungsbetreuung erreicht die Seite ueber die
    Navigation, die Leitung sieht den Reiter nicht.
    """

    def test_admin_sees_nav_link(self, authenticated_page, base_url):
        """Admin erreicht das DSGVO-Paket ueber den Hub „Datenschutz"."""
        page = authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        hub = sidebar_item(page, "datenschutz")
        assert hub.count() == 1
        hub.click()
        page.wait_for_url(re.compile(r"/datenschutz/"))

        tab = subnav_tab(page, "dsgvo")
        assert tab.count() == 1
        tab.click()
        page.wait_for_url(re.compile(r"/dsgvo/"))
        assert page.locator("h1:has-text('DSGVO-Dokumentationspaket')").is_visible()

    def test_lead_does_not_see_nav_link(self, lead_page, base_url):
        """Leitung sieht den DSGVO-Reiter nicht — auch nicht im Hub."""
        page = lead_page
        page.goto(f"{base_url}/datenschutz/")
        page.wait_for_load_state("domcontentloaded")

        # Gegenprobe: der Hub selbst steht der Leitung offen (sonst waere die
        # Abwesenheit des Reiters trivial), nur der DSGVO-Reiter fehlt.
        assert sidebar_item(page, "datenschutz").count() == 1
        assert subnav_tab(page, "dsgvo").count() == 0

    def test_admin_can_access_page(self, authenticated_page, base_url):
        """Admin kann DSGVO-Paket-Seite aufrufen."""
        authenticated_page.goto(f"{base_url}/dsgvo/")
        heading = authenticated_page.locator("h1:has-text('DSGVO-Dokumentationspaket')")
        assert heading.count() == 1


class TestDSGVODocumentDownload:
    """Dokumente herunterladen."""

    def test_admin_can_download_document(self, authenticated_page, base_url):
        """Admin kann ein DSGVO-Dokument herunterladen."""
        authenticated_page.goto(f"{base_url}/dsgvo/")

        with authenticated_page.expect_download() as download_info:
            find_first_dsgvo_download_link(authenticated_page).click()
        download = download_info.value
        assert download.suggested_filename.endswith(".md")

    def test_document_contains_facility_data(self, authenticated_page, base_url):
        """Heruntergeladenes Dokument enthält Einrichtungsdaten."""
        authenticated_page.goto(f"{base_url}/dsgvo/")

        with authenticated_page.expect_download() as download_info:
            find_first_dsgvo_download_link(authenticated_page).click()
        download = download_info.value
        content = download.path().read_text(encoding="utf-8")
        # Placeholder should be replaced
        assert "{{ facility_name }}" not in content

    def test_page_shows_5_documents(self, authenticated_page, base_url):
        """Seite zeigt 5 Dokumente."""
        authenticated_page.goto(f"{base_url}/dsgvo/")
        rows = authenticated_page.locator("tbody tr")
        assert rows.count() == 5


class TestDSGVOAccessControl:
    """Zugriffskontrolle."""

    def test_lead_direct_access_403(self, lead_page, base_url):
        """Leitung bekommt 403 bei direktem Zugriff."""
        resp = lead_page.goto(f"{base_url}/dsgvo/")
        assert resp.status == 403

    def test_staff_direct_access_403(self, staff_page, base_url):
        """Fachkraft bekommt 403 bei direktem Zugriff."""
        resp = staff_page.goto(f"{base_url}/dsgvo/")
        assert resp.status == 403

    def test_lead_document_download_403(self, lead_page, base_url):
        """Leitung bekommt 403 beim Dokument-Download."""
        resp = lead_page.goto(f"{base_url}/dsgvo/toms/")
        assert resp.status == 403
