"""Middleware: Sets request.current_facility from the authenticated user.

Zusaetzlich wird die PostgreSQL-Session-Variable ``app.current_facility_id``
via ``SET LOCAL`` gesetzt — damit greifen die in Migration 0047
eingerichteten Row-Level-Security-Policies als zweite Verteidigungslinie
unterhalb der Django-Facility-Scoping-Schicht.

Refs #542.
"""

from django.db import connection


class FacilityScopeMiddleware:
    """Sets request.current_facility to the user's facility.

    Setzt ausserdem ``app.current_facility_id`` auf Postgres-Ebene, damit
    RLS-Policies pro Request greifen. Ohne gesetzte Variable liefern die
    Policies ``facility_id = current_setting(..., true)`` einen NULL-
    Vergleich und somit keine Zeilen — Defense-in-Depth.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            request.current_facility = getattr(request.user, "facility", None)
        else:
            request.current_facility = None

        if request.current_facility is not None and connection.vendor == "postgresql":
            # ``is_local=true`` -> SET LOCAL: nur innerhalb der aktuellen
            # Transaktion gueltig, wird beim naechsten Request zurueckgesetzt.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.current_facility_id', %s, true)",
                    [str(request.current_facility.pk)],
                )

        return self.get_response(request)
