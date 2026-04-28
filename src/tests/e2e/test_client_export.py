"""E2E-Tests: Klientel-Datenauskunft (Art. 15/20 DSGVO)."""

import pytest

pytestmark = pytest.mark.e2e


class TestClientExportDropdown:
    """Datenauskunft-Dropdown auf der Klientel-Detailseite."""

    def test_lead_sees_dropdown(self, lead_page, base_url):
        """Leitung sieht den Datenauskunft-Button."""
        lead_page.goto(f"{base_url}/clients/")
        lead_page.locator("a:has-text('Stern-42')").first.click()
        lead_page.wait_for_url(lambda url: "/clients/" in url and "Stern-42" not in url or "/clients/" in url)
        btn = lead_page.locator("button:has-text('Datenauskunft')")
        assert btn.count() == 1

    def test_staff_does_not_see_dropdown(self, staff_page, base_url):
        """Fachkraft sieht keinen Datenauskunft-Button."""
        staff_page.goto(f"{base_url}/clients/")
        staff_page.locator("a:has-text('Stern-42')").first.click()
        staff_page.wait_for_url(lambda url: "/clients/" in url)
        btn = staff_page.locator("button:has-text('Datenauskunft')")
        assert btn.count() == 0


class TestClientExportJSON:
    """JSON-Export herunterladen."""

    def test_lead_can_download_json(self, lead_page, base_url):
        """Leitung kann JSON-Export herunterladen."""
        lead_page.goto(f"{base_url}/clients/")
        lead_page.locator("a:has-text('Stern-42')").first.click()
        lead_page.wait_for_url(lambda url: "/clients/" in url)
        desktop = lead_page.locator(".hidden.md\\:flex")
        desktop.locator("button:has-text('Datenauskunft')").click()

        with lead_page.expect_download() as download_info:
            desktop.locator("a:has-text('JSON-Export')").click()
        download = download_info.value
        assert "datenauskunft" in download.suggested_filename
        assert download.suggested_filename.endswith(".json")


class TestClientExportPDF:
    """PDF-Export herunterladen."""

    @pytest.mark.smoke
    def test_lead_can_download_pdf(self, lead_page, base_url):
        """Leitung kann PDF-Export herunterladen."""
        lead_page.goto(f"{base_url}/clients/")
        lead_page.locator("a:has-text('Stern-42')").first.click()
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
        staff_page.locator("a:has-text('Stern-42')").first.click()
        staff_page.wait_for_url(lambda url: "/clients/" in url)
        client_url = staff_page.url
        resp = staff_page.goto(f"{client_url}export/json/")
        assert resp.status == 403
