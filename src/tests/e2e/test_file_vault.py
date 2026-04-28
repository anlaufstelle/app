"""E2E-Tests: Encrypted File Vault — Datei-Upload, Download, Übersicht."""

import os

import pytest

pytestmark = pytest.mark.e2e


# Minimaler gültiger PDF-Header, damit der Magic-Bytes-Check des Services
# (libmagic) den Upload als application/pdf erkennt (Refs #610).
_VALID_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


@pytest.fixture
def _test_pdf(tmp_path):
    """Create a test PDF file for upload."""
    path = tmp_path / "e2e-test.pdf"
    path.write_bytes(_VALID_PDF_BYTES)
    return str(path)


def _select_qualified_client(page, base_url, e2e_env):
    """Select the first qualified client via hidden input (bypasses autocomplete)."""
    import subprocess
    import sys

    python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable
    result = subprocess.run(
        [
            python,
            "src/manage.py",
            "shell",
            "-c",
            "from core.models import Client; "
            "c = Client.objects.filter(contact_stage='qualified').first(); "
            "print(c.pk if c else '')",
        ],
        capture_output=True,
        text=True,
        env=e2e_env,
    )
    client_pk = result.stdout.strip()
    if client_pk:
        page.evaluate(f"document.querySelector('input[name=\"client\"]').value = '{client_pk}'")
    return client_pk


class TestFileUploadAndDownload:
    """Upload a file via event form, verify on detail page, download it."""

    @pytest.mark.smoke
    def test_lead_uploads_file_and_downloads(self, lead_page, base_url, e2e_env, _test_pdf):
        """Lead creates event with file upload, sees it on detail, downloads it."""
        page = lead_page

        # Navigate to new event form
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        # Select Beratungsgespräch (has FILE field for Lead)
        page.select_option('select[name="document_type"]', label="Beratungsgespräch")
        page.wait_for_selector("text=Scan/Bescheid", timeout=10000)

        # Select a qualified client (required for Beratungsgespräch)
        _select_qualified_client(page, base_url, e2e_env)

        # Fill text fields
        page.fill('input[name="thema"]', "E2E Wohngeld-Test")

        # Upload file
        page.set_input_files('input[name="scan-bescheid"]', _test_pdf)

        # Submit
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=10000)

        # Verify success message
        assert page.locator("text=Kontakt wurde dokumentiert").count() >= 1

        # Verify file is shown on detail page
        assert page.locator("text=e2e-test.pdf").count() >= 1

        # Download file
        with page.expect_download() as download_info:
            page.locator("a:has-text('e2e-test.pdf')").click()
        download = download_info.value
        assert download.suggested_filename == "e2e-test.pdf"

        # Verify content
        download_path = download.path()
        with open(download_path, "rb") as f:
            content = f.read()
        assert content == _VALID_PDF_BYTES

    def test_download_content_matches_upload(self, lead_page, base_url, e2e_env, _test_pdf):
        """Downloaded file content matches the original upload."""
        page = lead_page

        # Create event with file
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option('select[name="document_type"]', label="Beratungsgespräch")
        page.wait_for_selector("text=Scan/Bescheid", timeout=10000)
        _select_qualified_client(page, base_url, e2e_env)
        page.fill('input[name="thema"]', "E2E Content-Check")
        page.set_input_files('input[name="scan-bescheid"]', _test_pdf)
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=10000)

        # Download and verify content byte-for-byte
        with page.expect_download() as download_info:
            page.locator("a:has-text('e2e-test.pdf')").click()
        download = download_info.value
        with open(download.path(), "rb") as f:
            assert f.read() == _VALID_PDF_BYTES

    def test_download_creates_audit_log(self, lead_page, base_url, e2e_env, _test_pdf):
        """Downloading a file creates an AuditLog entry."""
        import subprocess
        import sys

        page = lead_page
        python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable

        # Create event with file
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option('select[name="document_type"]', label="Beratungsgespräch")
        page.wait_for_selector("text=Scan/Bescheid", timeout=10000)
        _select_qualified_client(page, base_url, e2e_env)
        page.fill('input[name="thema"]', "E2E Audit-Check")
        page.set_input_files('input[name="scan-bescheid"]', _test_pdf)
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=10000)

        # Download
        with page.expect_download():
            page.locator("a:has-text('e2e-test.pdf')").click()

        # Check AuditLog via manage.py shell
        result = subprocess.run(
            [
                python,
                "src/manage.py",
                "shell",
                "-c",
                "from core.models import AuditLog; "
                "log = AuditLog.objects.filter(action='download').order_by('-timestamp').first(); "
                "print(f'{log.action}|{log.target_type}' if log else 'NONE')",
            ],
            capture_output=True,
            text=True,
            env=e2e_env,
        )
        assert "download|EventAttachment" in result.stdout.strip()


class TestFileEdit:
    """File replacement on event edit."""

    def test_replace_file_on_edit(self, lead_page, base_url, e2e_env, _test_pdf, tmp_path):
        """Replacing a file on edit keeps the old version as a superseded predecessor (Refs #587)."""
        page = lead_page

        # Create event with file
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option('select[name="document_type"]', label="Beratungsgespräch")
        page.wait_for_selector("text=Scan/Bescheid", timeout=10000)
        _select_qualified_client(page, base_url, e2e_env)
        page.fill('input[name="thema"]', "E2E Edit-Test")
        page.set_input_files('input[name="scan-bescheid"]', _test_pdf)
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=10000)

        # Go to edit
        page.click('a:has-text("Bearbeiten")')
        page.wait_for_load_state("domcontentloaded")

        # Upload replacement file
        replacement = tmp_path / "replacement.pdf"
        replacement.write_bytes(_VALID_PDF_BYTES)
        page.set_input_files('input[name="scan-bescheid"]', str(replacement))
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=10000)

        # Aktuelle Version ist „replacement.pdf"; die alte „e2e-test.pdf" wandert
        # in den Vorversionen-Akkordeon. Das Top-Level-Label zeigt nur noch die
        # aktuelle Version.
        assert page.locator("text=replacement.pdf").count() >= 1
        prior_container = page.locator("[data-testid='attachment-prior-versions']")
        assert prior_container.count() == 1
        # Akkordeon aufklappen, damit die Vorversion sichtbar wird.
        prior_container.locator("summary").click()
        assert "e2e-test.pdf" in prior_container.inner_text()

        # Download current version and verify content
        with page.expect_download() as download_info:
            page.locator("a:has-text('replacement.pdf')").click()
        download = download_info.value
        with open(download.path(), "rb") as f:
            assert f.read() == _VALID_PDF_BYTES

    def test_prior_version_stays_downloadable(self, lead_page, base_url, e2e_env, _test_pdf, tmp_path):
        """Nach Ersetzen muss die Vorversion weiterhin herunterladbar sein (Refs #587)."""
        page = lead_page

        # Original anlegen
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option('select[name="document_type"]', label="Beratungsgespräch")
        page.wait_for_selector("text=Scan/Bescheid", timeout=10000)
        _select_qualified_client(page, base_url, e2e_env)
        page.fill('input[name="thema"]', "E2E Vorversion-Download")
        page.set_input_files('input[name="scan-bescheid"]', _test_pdf)
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=10000)

        # Ersetzen
        page.click('a:has-text("Bearbeiten")')
        page.wait_for_load_state("domcontentloaded")
        replacement = tmp_path / "replacement.pdf"
        replacement.write_bytes(_VALID_PDF_BYTES)
        page.set_input_files('input[name="scan-bescheid"]', str(replacement))
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=10000)

        # Vorversion-Akkordeon aufklappen und Vorversion herunterladen
        prior_container = page.locator("[data-testid='attachment-prior-versions']")
        prior_container.locator("summary").click()
        with page.expect_download() as download_info:
            prior_container.locator("a:has-text('e2e-test.pdf')").click()
        download = download_info.value
        with open(download.path(), "rb") as f:
            assert f.read() == _VALID_PDF_BYTES


class TestFileOverview:
    """Central attachment list view."""

    def test_attachment_list_shows_uploaded_files(self, lead_page, base_url, e2e_env, _test_pdf):
        """After uploading a file, it appears in the Dateien overview."""
        page = lead_page

        # First create an event with a file
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option('select[name="document_type"]', label="Beratungsgespräch")
        page.wait_for_selector("text=Scan/Bescheid", timeout=10000)
        _select_qualified_client(page, base_url, e2e_env)
        page.fill('input[name="thema"]', "E2E Datei-Übersicht")
        page.set_input_files('input[name="scan-bescheid"]', _test_pdf)
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=10000)

        # Navigate to attachments list
        page.goto(f"{base_url}/attachments/")
        page.wait_for_load_state("domcontentloaded")

        # Verify file appears in the table
        assert page.locator("text=e2e-test.pdf").count() >= 1
        assert page.locator("text=Beratungsgespräch").count() >= 1

    def test_sidebar_has_dateien_link(self, lead_page, base_url):
        """Sidebar shows 'Dateien' navigation link."""
        page = lead_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        link = page.locator('[data-testid="nav-attachments"]')
        assert link.count() == 1
        assert link.get_attribute("href") == "/attachments/"


class TestFileSensitivity:
    """Sensitivity-based access control for file fields."""

    def test_staff_cannot_see_high_sensitivity_file_field(self, staff_page, base_url):
        """Staff cannot see FILE field with sensitivity='high' in form."""
        page = staff_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        page.select_option('select[name="document_type"]', label="Beratungsgespräch")
        # Wait for dynamic fields to load
        page.wait_for_selector("text=Dauer", timeout=10000)

        # Scan/Bescheid has sensitivity="high" — staff should NOT see it
        assert page.locator("text=Scan/Bescheid").count() == 0

    def test_lead_can_see_high_sensitivity_file_field(self, lead_page, base_url):
        """Lead can see FILE field with sensitivity='high' in form."""
        page = lead_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        page.select_option('select[name="document_type"]', label="Beratungsgespräch")
        page.wait_for_selector("text=Scan/Bescheid", timeout=10000)

        assert page.locator("text=Scan/Bescheid").count() >= 1
