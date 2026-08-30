"""Konfigurierbare Rechtstexte fuer selbst gehostete Instanzen (Refs #2076)."""

import logging

import pytest
from django.urls import reverse

from core.views.legal import render_legal_markdown


@pytest.fixture
def legal_files(settings, tmp_path):
    imprint = tmp_path / "imprint.md"
    privacy = tmp_path / "privacy.md"
    imprint.write_text("# Betreiber-Impressum\n\nKontakt: [Team](mailto:hallo@example.org)", encoding="utf-8")
    privacy.write_text("# Eigene Datenschutzerklärung\n\nNur für diese Instanz.", encoding="utf-8")
    settings.DEMO_MODE = False
    settings.LEGAL_IMPRINT_FILE = str(imprint)
    settings.LEGAL_PRIVACY_FILE = str(privacy)
    return imprint, privacy


@pytest.mark.django_db
class TestSelfHostedLegalPages:
    def test_configured_files_are_publicly_rendered(self, client, legal_files):
        imprint_response = client.get(reverse("demo_imprint"))
        privacy_response = client.get(reverse("demo_privacy"))

        assert imprint_response.status_code == 200
        assert "Betreiber-Impressum" in imprint_response.content.decode()
        assert 'href="mailto:hallo@example.org"' in imprint_response.content.decode()
        assert privacy_response.status_code == 200
        assert "Eigene Datenschutzerklärung" in privacy_response.content.decode()

    def test_demo_template_wins_over_configured_operator_file(self, client, settings, tmp_path):
        configured = tmp_path / "imprint.md"
        configured.write_text("SELBSTHOSTER-MARKER", encoding="utf-8")
        settings.DEMO_MODE = True
        settings.DEMO_LEGAL_IMPRINT_URL = "/impressum/"
        settings.DEMO_LEGAL_PRIVACY_URL = "/datenschutzerklaerung/"
        settings.LEGAL_IMPRINT_FILE = str(configured)

        response = client.get(reverse("demo_imprint"))

        assert response.status_code == 200
        assert "EXTRA_TEMPLATE_DIR" in response.content.decode()
        assert "SELBSTHOSTER-MARKER" not in response.content.decode()

    def test_missing_configured_file_returns_404_without_path_leak(self, client, settings, tmp_path, caplog):
        missing_path = tmp_path / "secret-operator-path" / "imprint.md"
        settings.DEMO_MODE = False
        settings.LEGAL_IMPRINT_FILE = str(missing_path)

        with caplog.at_level(logging.WARNING, logger="core.views.legal"):
            response = client.get(reverse("demo_imprint"))

        assert response.status_code == 404
        assert str(missing_path) not in response.content.decode()
        assert "LEGAL_IMPRINT_FILE" in caplog.text


@pytest.mark.django_db
class TestLegalLinks:
    def test_links_are_absent_without_demo_or_operator_files(self, client, settings):
        settings.DEMO_MODE = False
        settings.LEGAL_IMPRINT_FILE = None
        settings.LEGAL_PRIVACY_FILE = None

        login_body = client.get(reverse("login")).content.decode()

        assert 'href="/impressum/"' not in login_body
        assert 'href="/datenschutzerklaerung/"' not in login_body

    def test_login_and_base_footer_link_configured_operator_pages(self, client, legal_files):
        login_body = client.get(reverse("login")).content.decode()
        legal_body = client.get(reverse("demo_imprint")).content.decode()

        for body in (login_body, legal_body):
            assert 'href="/impressum/"' in body
            assert 'href="/datenschutzerklaerung/"' in body

    def test_password_reset_page_inherits_configured_legal_links(self, client, legal_files):
        body = client.get(reverse("password_reset")).content.decode()

        assert 'href="/impressum/"' in body
        assert 'href="/datenschutzerklaerung/"' in body

    def test_only_existing_configured_page_is_linked(self, client, settings, tmp_path):
        imprint = tmp_path / "imprint.md"
        imprint.write_text("# Impressum", encoding="utf-8")
        settings.DEMO_MODE = False
        settings.LEGAL_IMPRINT_FILE = str(imprint)
        settings.LEGAL_PRIVACY_FILE = str(tmp_path / "missing-privacy.md")

        body = client.get(reverse("login")).content.decode()

        assert 'href="/impressum/"' in body
        assert 'href="/datenschutzerklaerung/"' not in body


class TestLegalMarkdownRenderer:
    def test_renders_supported_block_and_link_syntax(self):
        rendered = str(
            render_legal_markdown(
                "# Titel\n\nAbsatz mit [Web](https://example.org).\n\n- Eins\n- [Mail](mailto:team@example.org)"
            )
        )

        assert "<h1" in rendered
        assert "<p" in rendered
        assert "<ul" in rendered
        assert "<li" in rendered
        assert 'href="https://example.org"' in rendered
        assert 'href="mailto:team@example.org"' in rendered

    def test_escapes_html_and_rejects_non_whitelisted_link_schemes(self):
        rendered = str(
            render_legal_markdown(
                "<script>alert(1)</script>\n\n"
                "<style>body{display:none}</style>\n\n"
                "[JS](javascript:alert(1)) [Data](data:text/html,x) "
                "[FTP](ftp://example.org) [Relativ](/intern) "
                "[HTTPS](HTTPS://example.org)"
            )
        )

        assert "<script" not in rendered
        assert "<style" not in rendered
        assert "style=" not in rendered
        assert "&lt;script&gt;" in rendered
        assert "&lt;style&gt;" in rendered
        assert "javascript:" in rendered
        assert "data:text/html" in rendered
        assert "ftp://example.org" in rendered
        assert 'href="javascript:' not in rendered
        assert 'href="data:' not in rendered
        assert 'href="ftp:' not in rendered
        assert 'href="/intern"' not in rendered
        assert 'href="HTTPS://example.org"' in rendered
