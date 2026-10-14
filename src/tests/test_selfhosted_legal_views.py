"""Konfigurierbare Rechtstexte fuer selbst gehostete Instanzen (Refs #2076)."""

import logging
import os
import time
from unittest.mock import patch

import pytest
from django.urls import reverse

from core.views import legal
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

    def test_oversized_file_returns_404_and_warns(self, client, settings, tmp_path, caplog):
        oversized = tmp_path / "oversized-imprint.md"
        oversized.write_bytes(b"a" * (256 * 1024 + 1))
        settings.DEMO_MODE = False
        settings.LEGAL_IMPRINT_FILE = str(oversized)

        with caplog.at_level(logging.WARNING, logger="core.views.legal"):
            response = client.get(reverse("demo_imprint"))

        assert response.status_code == 404
        assert "LEGAL_IMPRINT_FILE" in caplog.text

    @pytest.mark.parametrize("contents", [b"", b" \n\t"])
    def test_empty_file_returns_404(self, client, settings, tmp_path, contents):
        empty = tmp_path / "empty-imprint.md"
        empty.write_bytes(contents)
        settings.DEMO_MODE = False
        settings.LEGAL_IMPRINT_FILE = str(empty)

        response = client.get(reverse("demo_imprint"))

        assert response.status_code == 404

    def test_memory_error_while_reading_returns_404(self, client, settings, tmp_path, monkeypatch):
        imprint = tmp_path / "imprint.md"
        imprint.write_text("# Impressum", encoding="utf-8")
        settings.DEMO_MODE = False
        settings.LEGAL_IMPRINT_FILE = str(imprint)

        def raise_memory_error(*args, **kwargs):
            raise MemoryError

        monkeypatch.setattr(legal, "_read_configured_bytes", raise_memory_error, raising=False)

        response = client.get(reverse("demo_imprint"))

        assert response.status_code == 404

    def test_unavailable_file_warns_only_once(self, client, settings, tmp_path, caplog):
        settings.DEMO_MODE = False
        settings.LEGAL_IMPRINT_FILE = str(tmp_path / "missing.md")
        warn_once = getattr(legal, "_warn_unavailable", None)
        if warn_once is not None:
            warn_once.cache_clear()

        with caplog.at_level(logging.WARNING, logger="core.views.legal"):
            client.get(reverse("demo_imprint"))
            client.get(reverse("demo_imprint"))

        warning_records = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING and record.name == "core.views.legal"
        ]
        assert len(warning_records) == 1


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

    @pytest.mark.timeout(2)
    def test_fifo_is_not_opened_by_global_template_context(self, client, settings, tmp_path):
        fifo = tmp_path / "imprint.pipe"
        os.mkfifo(fifo)
        settings.DEMO_MODE = False
        settings.LEGAL_IMPRINT_FILE = str(fifo)
        settings.LEGAL_PRIVACY_FILE = None

        body = client.get(reverse("login")).content.decode()

        assert 'href="/impressum/"' not in body

    def test_global_template_context_does_not_read_legal_content(self, client, legal_files):
        with patch(
            "core.views.legal.read_configured_legal_file",
            side_effect=AssertionError("global template context must not read legal content"),
        ):
            body = client.get(reverse("login")).content.decode()

        assert 'href="/impressum/"' in body

    def test_empty_or_non_regular_file_is_not_linked(self, client, settings, tmp_path):
        empty = tmp_path / "empty.md"
        empty.touch()
        settings.DEMO_MODE = False
        settings.LEGAL_IMPRINT_FILE = str(empty)
        settings.LEGAL_PRIVACY_FILE = "/dev/null"

        body = client.get(reverse("login")).content.decode()

        assert 'href="/impressum/"' not in body
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

    def test_adversarial_unclosed_links_render_within_bounded_time(self):
        """Thousands of unclosed link openers render in bounded time.

        Measured as CPU time of this process instead of wall time: a loaded
        host, for example many parallel test workers, stretches the wall time
        of the call but not the work the renderer does. The bound guards
        against super-linear rendering such as catastrophic backtracking, not
        against a slow host; a linear renderer stays far below it on this
        input.
        """
        source = "[a](" * 20_000

        started = time.process_time()
        rendered = str(render_legal_markdown(source))
        elapsed = time.process_time() - started

        assert rendered
        assert elapsed < 2.0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("> Quoted text", "<blockquote"),
        ("**Bold text**", "<strong>Bold text</strong>"),
        ("`Code text`", "<code>Code text</code>"),
        ("- **Bold** and `code`", "<strong>Bold</strong> and <code>code</code>"),
        ("[**Bold** and `code`](https://example.org)", "<strong>Bold</strong> and <code>code</code>"),
        ("**<script>**", "<strong>&lt;script&gt;</strong>"),
        ("`<img src=x onerror=alert(1)>`", "<code>&lt;img src=x onerror=alert(1)&gt;</code>"),
        ("> [x](javascript:alert)", "&gt;"),
        ("**unpaired and `unpaired", "**unpaired and `unpaired"),
    ],
)
def test_operator_markdown_subset_through_view(client, legal_files, source, expected):
    imprint, _privacy = legal_files
    imprint.write_text(source, encoding="utf-8")
    response = client.get(reverse("demo_imprint"))
    assert response.status_code == 200
    html = response.content.decode()
    if source.startswith("> ["):
        assert '<a href="javascript:' not in html
        assert "[x](javascript:alert)" in html
        assert "<blockquote" in html
    else:
        assert expected in html
    assert "<script>" not in html
    assert "<img src=x" not in html
