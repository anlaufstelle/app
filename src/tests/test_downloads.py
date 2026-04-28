"""Unit tests for core.utils.downloads — safe_download_response helper."""

from __future__ import annotations

import pytest
from django.http import HttpResponse, StreamingHttpResponse

from core.utils.downloads import safe_download_response


class TestSafeDownloadResponse:
    """Tests for the safe_download_response helper."""

    def test_ascii_filename(self):
        """Plain ASCII filename produces a standard Content-Disposition header."""
        response = safe_download_response("report.pdf", "application/pdf", b"%PDF")
        assert response["Content-Disposition"] == 'attachment; filename="report.pdf"'

    def test_unicode_filename(self):
        """Filename with umlauts uses RFC 5987 filename* encoding."""
        response = safe_download_response("Ärztebericht_März.pdf", "application/pdf", b"%PDF")
        header = response["Content-Disposition"]
        # Must use filename* with UTF-8 percent-encoding
        assert "filename*=utf-8''" in header
        assert "%C3%84rztebericht" in header  # Ä
        assert "M%C3%A4rz" in header  # ä

    def test_nosniff_header(self):
        """Response always includes X-Content-Type-Options: nosniff."""
        response = safe_download_response("data.csv", "text/csv", b"a,b,c")
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_content_type_is_set(self):
        """Content-Type matches the provided MIME type."""
        response = safe_download_response("data.json", "application/json", b"{}")
        assert response["Content-Type"] == "application/json"

    def test_bytes_content_returns_http_response(self):
        """Bytes content yields a regular HttpResponse."""
        response = safe_download_response("file.bin", "application/octet-stream", b"\x00\x01")
        assert isinstance(response, HttpResponse)
        assert not isinstance(response, StreamingHttpResponse)
        assert response.content == b"\x00\x01"

    def test_str_content_returns_http_response(self):
        """String content yields a regular HttpResponse."""
        response = safe_download_response("doc.md", "text/markdown", "# Hello")
        assert isinstance(response, HttpResponse)
        assert not isinstance(response, StreamingHttpResponse)

    def test_iterator_content_returns_streaming_response(self):
        """Iterator content yields a StreamingHttpResponse."""

        def chunks():
            yield b"chunk1"
            yield b"chunk2"

        response = safe_download_response("big.bin", "application/octet-stream", chunks())
        assert isinstance(response, StreamingHttpResponse)
        assert b"".join(response.streaming_content) == b"chunk1chunk2"

    def test_inline_disposition(self):
        """as_attachment=False produces an inline Content-Disposition."""
        response = safe_download_response("photo.png", "image/png", b"PNG", as_attachment=False)
        assert response["Content-Disposition"] == 'inline; filename="photo.png"'

    @pytest.mark.parametrize(
        "filename",
        [
            'file"name.txt',
            "file name.txt",
            "file\tname.txt",
        ],
    )
    def test_special_chars_in_filename(self, filename):
        """Filenames with special characters do not break the header."""
        response = safe_download_response(filename, "text/plain", b"data")
        header = response["Content-Disposition"]
        assert "attachment" in header
        # Must have some form of filename indication
        assert "filename" in header
