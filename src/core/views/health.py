"""Health endpoint for monitoring (no auth)."""

import logging
import os

from django.db import connection
from django.http import JsonResponse
from django.views import View

logger = logging.getLogger(__name__)


class HealthView(View):
    """GET /health/ -- DB check + app version, no auth required."""

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

        return JsonResponse(
            {"status": status, "database": db_status, "version": version},
            status=http_status,
        )
