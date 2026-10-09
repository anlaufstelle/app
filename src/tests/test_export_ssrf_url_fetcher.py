"""WeasyPrint ``url_fetcher`` restriction — file:// SSRF defence in depth (Refs #1272, T8).

Previously the ``file://`` SSRF / local file read vector in the PDF exports
was prevented **only** because the PDF templates contain no ``|safe``
(injected tags render inert) — fragile defence in depth. WeasyPrint's default
``url_fetcher`` actually reads ``file://`` URLs from the local file system
(verified) and would fetch external ``http(s)://`` resources.

Fix: all ``write_pdf`` call sites pass a restrictive ``url_fetcher`` that
rejects ``file://`` and external network fetches and hands only inline
``data:`` URIs to the default fetcher:

- ``core.reporting.pdf.render_dataset_pdf`` (generic report export)
- ``core.services.system.export.render_sample_report_1_pdf`` (sample report layout)
- ``core.services.client.export.export_client_data_pdf``

Since the legitimate templates reference no external resources, the fetcher
is never called for regular input → byte-identical PDF output.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import weasyprint
import weasyprint.urls

from core.services.system.export import restricted_url_fetcher


class TestRestrictedUrlFetcherUnit:
    """Der Fetcher lehnt file://- und Netzwerk-Schemata ab, erlaubt nur data:."""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "file://localhost/etc/hostname",
            "http://169.254.169.254/latest/meta-data/",
            "https://evil.example/secret",
            "ftp://internal-host/secret",
            "//evil.example/protocol-relative",
        ],
    )
    def test_refuses_file_and_network_schemes(self, url):
        with pytest.raises(ValueError):
            restricted_url_fetcher(url)

    def test_allows_inline_data_uri(self):
        # data:-URIs sind inline (kein Datei-/Netzwerk-Fetch) und werden an den
        # restriktiven ``URLFetcher`` delegiert — sonst koennte legitimes
        # Inline-Markup brechen. Response schliessen (WeasyPrint tut das im
        # Prod-Pfad selbst), damit unter ``filterwarnings=error`` (Refs #1609)
        # keine ResourceWarning aus dem GC-Finalizer failt.
        resp = restricted_url_fetcher("data:text/plain;charset=utf-8,hello")
        try:
            assert resp is not None
        finally:
            resp.close()

    def test_data_uri_does_not_emit_deprecation_warning(self):
        # Regression #1610: der interne data:-Fetch darf nicht mehr ueber das in
        # WeasyPrint 69.0 deprecatete ``default_url_fetcher`` laufen (Removal in
        # 69.0 angekuendigt) — sondern ueber eine ``URLFetcher``-Instanz.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            resp = restricted_url_fetcher("data:text/plain;charset=utf-8,hello")
        try:
            assert resp is not None
        finally:
            resp.close()

    def test_file_url_is_a_real_vector_default_would_read_it(self):
        """Sanity: ein permissiver ``URLFetcher`` LIEST file:// — ohne unseren
        restriktiven Fetcher waere das ein Local-File-Read. Macht den Schutz
        nicht-trivial."""
        # Ein URLFetcher ohne data:-Whitelist (WeasyPrint-Default erlaubt
        # file://) liest die Datei tatsaechlich — genau diesen Zugriff blockt
        # restricted_url_fetcher. Response schliessen, damit unter
        # ``filterwarnings=error`` (Refs #1609) keine ResourceWarning failt.
        resp = weasyprint.urls.URLFetcher().fetch("file:///etc/hostname")
        try:
            assert resp is not None
        finally:
            resp.close()


class _CapturingHTML:
    """Faengt die ``weasyprint.HTML(...)``-kwargs ab und liefert Fake-PDF-Bytes."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def write_pdf(self, *args, **kwargs):
        return b"%PDF-1.7 fake"


@pytest.mark.django_db
class TestPdfCallSitesPassRestrictedFetcher:
    """Every ``write_pdf`` call site must hand the restrictive fetcher
    to ``weasyprint.HTML(...)`` (red without the fix)."""

    def test_generic_dataset_pdf_passes_fetcher(self, facility, monkeypatch):
        from core.reporting import pdf as dataset_pdf
        from core.reporting.export import export_dataset
        from core.reporting.registry import get_backend

        monkeypatch.setattr(dataset_pdf, "render_to_string", lambda *a, **k: "<html></html>")
        monkeypatch.setattr(weasyprint, "HTML", _CapturingHTML)

        today = date.today()
        export_dataset(get_backend("general-key-figures"), facility, today - timedelta(days=30), today, format="pdf")

        assert _CapturingHTML.last_kwargs.get("url_fetcher") is restricted_url_fetcher

    def test_sample_report_1_pdf_passes_fetcher(self, facility, monkeypatch):
        from core.services.system import export as sys_export

        monkeypatch.setattr(sys_export, "render_to_string", lambda *a, **k: "<html></html>")
        monkeypatch.setattr(sys_export.weasyprint, "HTML", _CapturingHTML)

        from core.reporting.export import export_dataset
        from core.reporting.registry import get_backend

        today = date.today()
        export_dataset(get_backend("sample-report-1"), facility, today - timedelta(days=30), today, format="pdf")

        assert _CapturingHTML.last_kwargs.get("url_fetcher") is sys_export.restricted_url_fetcher

    def test_export_client_data_pdf_passes_fetcher(self, facility, client_identified, staff_user, monkeypatch):
        from core.services.client import export as client_export

        monkeypatch.setattr(client_export, "render_to_string", lambda *a, **k: "<html></html>")
        monkeypatch.setattr(client_export.weasyprint, "HTML", _CapturingHTML)

        client_export.export_client_data_pdf(client_identified, facility, staff_user)

        # Dieselbe Fetcher-Instanz wie in den System-Exporten (single source).
        from core.services.system.export import restricted_url_fetcher as sys_fetcher

        assert _CapturingHTML.last_kwargs.get("url_fetcher") is sys_fetcher
