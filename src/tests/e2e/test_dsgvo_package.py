"""E2E-Tests: DSGVO-Dokumentationspaket."""

import pytest

pytestmark = pytest.mark.e2e


class TestDSGVOPackageNavigation:
    """Nav-Link und Seitenaufruf."""

    def test_admin_sees_nav_link(self, authenticated_page):
        """Admin sieht DSGVO-Paket in der Navigation."""
        nav = authenticated_page.locator("nav[aria-label='Hauptnavigation']")
        link = nav.locator("a:has-text('DSGVO-Paket')")
        assert link.count() == 1

    def test_lead_does_not_see_nav_link(self, lead_page):
        """Leitung sieht keinen DSGVO-Paket-Link."""
        nav = lead_page.locator("nav[aria-label='Hauptnavigation']")
        link = nav.locator("a:has-text('DSGVO-Paket')")
        assert link.count() == 0

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
            authenticated_page.locator("a:has-text('Herunterladen')").first.click()
        download = download_info.value
        assert download.suggested_filename.endswith(".md")

    def test_document_contains_facility_data(self, authenticated_page, base_url):
        """Heruntergeladenes Dokument enthält Einrichtungsdaten."""
        authenticated_page.goto(f"{base_url}/dsgvo/")

        with authenticated_page.expect_download() as download_info:
            authenticated_page.locator("a:has-text('Herunterladen')").first.click()
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
