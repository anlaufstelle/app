"""Centralised download-response builder with RFC 5987 Content-Disposition."""

from __future__ import annotations

from collections.abc import Iterator

from django.http import HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header


def safe_download_response(
    filename: str,
    content_type: str,
    content: bytes | str | Iterator,
    *,
    as_attachment: bool = True,
) -> HttpResponse | StreamingHttpResponse:
    """Build a Django response with correct Content-Disposition headers.

    Uses Django 5.0+ ``content_disposition_header()`` for RFC 5987 encoding
    so that Unicode filenames (umlauts, special chars) are transmitted safely.

    Parameters
    ----------
    filename:
        The user-visible download filename (may contain Unicode).
    content_type:
        MIME type for the ``Content-Type`` header.
    content:
        Response body — ``bytes`` or ``str`` produce an ``HttpResponse``,
        an iterator/generator produces a ``StreamingHttpResponse``.
    as_attachment:
        If ``True`` (default), the file is offered as a download.
        If ``False``, the file may be displayed inline by the browser.

    Refs #1342: setzt zentral ``Cache-Control: no-store, private`` — Exports
    tragen typischerweise personenbezogene Daten und duerfen nie im Browser-/
    Festplatten-Cache landen. Zentral hier statt in jeder Export-Call-Site
    (deckt alle heutigen und kuenftigen Aufrufer ab; redundant zur
    Blanket-``NoStoreCacheMiddleware``, falls die je entfernt/umgangen wird).
    """
    if isinstance(content, (bytes, str)):
        response = HttpResponse(content, content_type=content_type)
    else:
        response = StreamingHttpResponse(content, content_type=content_type)

    response["Content-Disposition"] = content_disposition_header(as_attachment, filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store, private"
    return response
