"""Health endpoint for monitoring (no auth)."""

import logging
import os

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views import View

from core.services.virus_scan import ping as clamav_ping

logger = logging.getLogger(__name__)


class HealthView(View):
    """GET /health/ -- DB check + app version, no auth required.

    Wenn ``CLAMAV_ENABLED`` aktiv ist, wird zusätzlich die Erreichbarkeit des
    ClamAV-Daemons geprüft. Ein nicht erreichbarer Scanner wird als Warnung
    ausgewiesen, setzt den Gesamtstatus aber nicht auf ``error`` — die harte
    Fail-closed-Entscheidung trifft der Upload-Pfad im File-Vault.
    """

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            db_status = "connected"
            status = "ok"
            http_status = 200
        except Exception:
            db_status = "unavailable"
            status = "error"
            http_status = 503

        version = os.environ.get("APP_VERSION", "dev")

        payload = {
            "status": status,
            "database": db_status,
            "version": version,
        }

        if getattr(settings, "CLAMAV_ENABLED", False):
            if clamav_ping():
                payload["virus_scanner"] = "connected"
            else:
                payload["virus_scanner"] = "unavailable"
                # Uploads scheitern hart — daher als Warnung im Health-Status.
                if status == "ok":
                    payload["status"] = "degraded"
        else:
            payload["virus_scanner"] = "disabled"

        return JsonResponse(payload, status=http_status)
