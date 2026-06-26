"""WeasyPrint ``url_fetcher``-Restriktion — file://-SSRF Defense-in-Depth (Refs #1272, T8).

Bisher war der ``file://``-SSRF-/Local-File-Read-Vektor in den PDF-Exporten
**nur** dadurch verhindert, dass die PDF-Templates kein ``|safe`` enthalten
(injizierte Tags rendern inert) — fragiles Defense-in-Depth. WeasyPrints
Default-``url_fetcher`` liest ``file://``-URLs tatsaechlich vom lokalen
Dateisystem (verifiziert) und wuerde externe ``http(s)://``-Ressourcen abrufen.

Fix: Alle drei ``write_pdf``-Aufrufstellen uebergeben einen restriktiven
``url_fetcher``, der ``file://`` und externe Netzwerk-Fetches ablehnt und nur
inline ``data:``-URIs an den Default-Fetcher durchreicht:

- ``core.services.system.export.generate_report_pdf``
- ``core.services.system.export.generate_jugendamt_pdf``
- ``core.services.client.export.export_client_data_pdf``

Da die legitimen Templates keine externen Ressourcen referenzieren, wird der
Fetcher fuer regulaere Eingaben nie aufgerufen → byte-gleiche PDF-Ausgabe.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import weasyprint

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
        # Default-Fetcher delegiert — sonst koennte legitimes Inline-Markup brechen.
        resp = restricted_url_fetcher("data:text/plain;charset=utf-8,hello")
        assert resp is not None

    def test_file_url_is_a_real_vector_default_would_read_it(self):
        """Sanity: der Default-Fetcher LIEST file:// — ohne unseren Fetcher
        waere das ein Local-File-Read. Macht den Schutz nicht-trivial."""
        # Wenn dieser Default-Fetch fehlschluege, waere der ganze Schutz moot.
        resp = weasyprint.default_url_fetcher("file:///etc/hostname")
        assert resp is not None


class _CapturingHTML:
    """Faengt die ``weasyprint.HTML(...)``-kwargs ab und liefert Fake-PDF-Bytes."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def write_pdf(self, *args, **kwargs):
        return b"%PDF-1.7 fake"


@pytest.mark.django_db
class TestPdfCallSitesPassRestrictedFetcher:
    """Jede der drei ``write_pdf``-Aufrufstellen muss den restriktiven Fetcher
    an ``weasyprint.HTML(...)`` uebergeben (Red ohne den Fix)."""

    def test_generate_report_pdf_passes_fetcher(self, facility, monkeypatch):
        from core.services.system import export as sys_export

        monkeypatch.setattr(sys_export, "render_to_string", lambda *a, **k: "<html></html>")
        monkeypatch.setattr(sys_export.weasyprint, "HTML", _CapturingHTML)

        today = date.today()
        out = sys_export.generate_report_pdf(facility, today - timedelta(days=30), today, {"total_contacts": 0})

        assert out == b"%PDF-1.7 fake"
        assert _CapturingHTML.last_kwargs.get("url_fetcher") is sys_export.restricted_url_fetcher

    def test_generate_jugendamt_pdf_passes_fetcher(self, facility, monkeypatch):
        from core.services.system import export as sys_export

        monkeypatch.setattr(sys_export, "render_to_string", lambda *a, **k: "<html></html>")
        monkeypatch.setattr(sys_export.weasyprint, "HTML", _CapturingHTML)

        today = date.today()
        sys_export.generate_jugendamt_pdf(facility, today - timedelta(days=30), today)

        assert _CapturingHTML.last_kwargs.get("url_fetcher") is sys_export.restricted_url_fetcher

    def test_export_client_data_pdf_passes_fetcher(self, facility, client_identified, staff_user, monkeypatch):
        from core.services.client import export as client_export

        monkeypatch.setattr(client_export, "render_to_string", lambda *a, **k: "<html></html>")
        monkeypatch.setattr(client_export.weasyprint, "HTML", _CapturingHTML)

        client_export.export_client_data_pdf(client_identified, facility, staff_user)

        # Dieselbe Fetcher-Instanz wie in den System-Exporten (single source).
        from core.services.system.export import restricted_url_fetcher as sys_fetcher

        assert _CapturingHTML.last_kwargs.get("url_fetcher") is sys_fetcher
