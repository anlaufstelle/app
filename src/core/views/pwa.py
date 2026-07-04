"""PWA service worker, manifest and offline-fallback views."""

import logging
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse, HttpResponseNotFound
from django.template.loader import render_to_string
from django.views import View

logger = logging.getLogger(__name__)

# Refs #V5: /static/-String-Literale (in doppelten Anführungszeichen) innerhalb
# des APP_SHELL-Arrays. Bewusst eng gefasst, damit nur Precache-Eintraege
# ersetzt werden — HTML-Routen (/, /clients/, /manifest.json), bare Bezeichner
# (OFFLINE_FALLBACK_URL) und /static/-Vorkommen ausserhalb des Arrays
# (importScripts, fetch-Handler-Stringvergleich) bleiben unberuehrt.
_STATIC_LITERAL_RE = re.compile(r'"/static/(?P<path>[^"]+)"')


@lru_cache(maxsize=1)
def _read_service_worker():
    """Read and cache the service worker file content."""
    sw_path = Path(settings.BASE_DIR) / "static" / "js" / "sw.js"
    return sw_path.read_text()


def _resolve_app_shell(content: str) -> str:
    """Loest die /static/-Literale im APP_SHELL-Block auf die vom
    Staticfiles-Storage gelieferten URLs auf (Refs #V5).

    In Produktion (Manifest-Storage, DEBUG=False) werden daraus die GEHASHTEN
    URLs — exakt die, die ``{% static %}`` in den Templates erzeugt. Da
    ``caches.match`` URL-exakt vergleicht, wuerde der SW sonst die pre-cachten
    Assets der Offline-In-Place-Shell (/clients/<pk>/) offline nicht finden.

    Nur der APP_SHELL-Array-Block wird angefasst; HTML-Routen bleiben
    unberuehrt. Fehlt ein Eintrag im Manifest (``ValueError``), faellt die
    Aufloesung auf den ungehashten Original-Pfad zurueck — GET /sw.js darf nie
    500en (der finders-Guard-Test verhindert Drift an der Quelle). Die
    Aufloesung laeuft pro Request (16 Eintraege, billig) und wird bewusst NICHT
    gecacht: ein lru_cache ueber den aufgeloesten Inhalt wuerde Test-Overrides
    und Deploy-Zustaende (neue Hashes) einfrieren.
    """
    start = content.find("const APP_SHELL = [")
    if start == -1:
        return content
    end = content.find("];", start)
    if end == -1:
        return content

    def _replace(match: "re.Match[str]") -> str:
        path = match.group("path")
        try:
            return f'"{staticfiles_storage.url(path)}"'
        except ValueError:
            # Kein Manifest-Eintrag (z.B. Asset nicht collected) → ungehashter
            # Original-Pfad statt 500. Refs #V5.
            logger.warning("APP_SHELL: kein Manifest-Eintrag fuer '%s' — Fallback auf ungehashten Pfad.", path)
            return match.group(0)

    resolved_block = _STATIC_LITERAL_RE.sub(_replace, content[start:end])
    return content[:start] + resolved_block + content[end:]


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
        # Refs #V5: APP_SHELL-/static/-Eintraege pro Request auf die (in Prod
        # gehashten) Staticfiles-URLs aufloesen, damit der Precache exakt die
        # von {% static %} referenzierten Assets trifft.
        content = _resolve_app_shell(content)
        return HttpResponse(
            content,
            content_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )


class OfflineFallbackView(View):
    """GET /offline/ — Statisches Offline-Fallback-Template (Refs #701).

    Wird vom Service-Worker (sw.js) als App-Shell pre-cached und bei
    Navigation-Requests ohne Cache- und Netz-Hit als Fallback geliefert.
    Das Template enthaelt Inline-CSS — der Service-Worker hat keinen
    Zugriff auf das Static-Asset-Pipeline, wenn das Netz weg ist.
    """

    def get(self, request):
        return HttpResponse(
            render_to_string("offline.html"),
            content_type="text/html; charset=utf-8",
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
