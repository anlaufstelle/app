"""Maintenance-Mode-Middleware (Refs #700).

Aktivierung per File-Flag:

    touch $MAINTENANCE_FLAG_FILE

Solange die Flag-Datei existiert, antwortet jeder Request mit
``HTTP 503`` + ``Retry-After``-Header und dem ``503.html``-Template,
ausgenommen Whitelist-Pfade (``/health/``, ``/static/...``) und
Whitelist-IPs (``MAINTENANCE_ALLOW_IPS``-Setting fuer Ops-Zugriff
waehrend der Wartung).

Per-Request-Kosten: ein ``os.path.exists``-Check, gecached fuer
``MAINTENANCE_CACHE_TTL`` Sekunden (Default 5s). Damit kostet ein
laufender Maintenance-Mode kaum etwas, wenn er nicht aktiv ist —
fuer abgeschaltete Setups fasst der Check die Disk gar nicht an,
weil ``MAINTENANCE_FLAG_FILE = None`` als Default deaktiviert.
"""

from __future__ import annotations

import os
import time

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string

# Default-Whitelist — Pfade, die auch im Wartungsmodus erreichbar bleiben muessen.
_DEFAULT_WHITELIST_PREFIXES = ("/health/", "/static/")


class MaintenanceModeMiddleware:
    """Antwortet mit 503, solange die ``MAINTENANCE_FLAG_FILE`` existiert."""

    # Klassen-Cache: (last_check_ts, file_exists). Geteilt ueber alle
    # Threads/Workers im selben Prozess; bei Multi-Worker-Setups (gunicorn)
    # checkt jeder Worker maximal alle TTL Sekunden.
    _cache: tuple[float, bool] | None = None

    def __init__(self, get_response):
        self.get_response = get_response
        self.flag_file = getattr(settings, "MAINTENANCE_FLAG_FILE", None)
        self.allow_ips = set(getattr(settings, "MAINTENANCE_ALLOW_IPS", []) or [])
        self.cache_ttl = getattr(settings, "MAINTENANCE_CACHE_TTL", 5)
        self.retry_after = getattr(settings, "MAINTENANCE_RETRY_AFTER", 600)

    def __call__(self, request):
        if not self._is_maintenance_active():
            return self.get_response(request)

        # Whitelist-Pfade: Health-Check + Static-Assets bleiben offen,
        # sonst meldet auch der eigene Health-Check 503 und der Loadbalancer
        # zieht den Container raus.
        if request.path.startswith(_DEFAULT_WHITELIST_PREFIXES):
            return self.get_response(request)

        # IP-Whitelist fuer Ops/Admin-Zugriff waehrend der Wartung.
        if self._client_ip(request) in self.allow_ips:
            return self.get_response(request)

        return HttpResponse(
            render_to_string("503.html"),
            status=503,
            content_type="text/html; charset=utf-8",
            headers={"Retry-After": str(self.retry_after)},
        )

    def _is_maintenance_active(self) -> bool:
        """Cached File-Exists-Check."""
        if not self.flag_file:
            return False
        now = time.monotonic()
        cache = type(self)._cache
        if cache is not None and now - cache[0] < self.cache_ttl:
            return cache[1]
        exists = os.path.exists(self.flag_file)
        type(self)._cache = (now, exists)
        return exists

    @staticmethod
    def _client_ip(request) -> str:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")
