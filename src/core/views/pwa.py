"""PWA service worker and manifest views."""

import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotFound
from django.views import View

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _read_service_worker():
    """Read and cache the service worker file content."""
    sw_path = Path(settings.BASE_DIR) / "static" / "js" / "sw.js"
    return sw_path.read_text()


@lru_cache(maxsize=1)
def _read_manifest():
    """Read and cache the manifest file content."""
    manifest_path = Path(settings.BASE_DIR) / "static" / "manifest.json"
    return manifest_path.read_text()


class ServiceWorkerView(View):
    """GET /sw.js — Service Worker mit Root-Scope."""

    def get(self, request):
        try:
            content = _read_service_worker()
        except FileNotFoundError:
            return HttpResponseNotFound("Service worker not found.")
        return HttpResponse(
            content,
            content_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )


class ManifestView(View):
    """GET /manifest.json -- PWA manifest served from root.

    Android Chrome checks whether the manifest scope is within the URL path
    of the manifest file. If the manifest is served from /static/manifest.json,
    Chrome does not accept scope '/'. Therefore the manifest must be served
    from /manifest.json.
    """

    def get(self, request):
        try:
            content = _read_manifest()
        except FileNotFoundError:
            return HttpResponseNotFound("Manifest not found.")
        return HttpResponse(
            content,
            content_type="application/manifest+json",
        )
