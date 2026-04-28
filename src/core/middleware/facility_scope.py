"""Middleware: Sets request.current_facility from the authenticated user.

Zusaetzlich wird die PostgreSQL-Session-Variable ``app.current_facility_id``
gesetzt — damit greifen die in Migration 0047 eingerichteten
Row-Level-Security-Policies als zweite Verteidigungslinie unterhalb der
Django-Facility-Scoping-Schicht.

Refs #542, #586.
"""

from django.db import connection


class FacilityScopeMiddleware:
    """Sets request.current_facility to the user's facility.

    Setzt ausserdem ``app.current_facility_id`` auf Postgres-Ebene, damit
    RLS-Policies pro Request greifen. Ohne gesetzte Variable liefern die
    Policies ``facility_id = current_setting(..., true)`` einen NULL-
    Vergleich und somit keine Zeilen — Defense-in-Depth.

    Historisch wurde ``SET LOCAL`` (``is_local=true``) verwendet; das war
    transaktions-lokal und endete bereits mit der Statement-Transaktion
    des Middleware-Cursors, also noch bevor nachfolgende ORM-Queries in
    einem Request ueberhaupt liefen (Refs #586). Jetzt wird die Variable
    session-weit gesetzt (``is_local=false``) und pro Request stets neu —
    auch fuer anonyme User explizit geleert, damit Connection-Pooling
    keinen stehengebliebenen Wert aus einer frueheren Request leaken kann.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_authenticated = hasattr(request, "user") and request.user.is_authenticated
        if is_authenticated:
            request.current_facility = getattr(request.user, "facility", None)
        else:
            request.current_facility = None

        # Nur bei authentifizierten Requests DB-Cursor oeffnen — Anonymous-
        # Routes (Login, Health, Static) greifen ohnehin nicht auf
        # facility-scoped Tabellen zu. Fuer authentifizierte User wird die
        # Variable stets neu gesetzt (auch auf leer, falls der User keine
        # Facility hat), damit eine recyclte Connection keinen
        # stehengebliebenen Wert aus einer frueheren Request leakt.
        if is_authenticated and connection.vendor == "postgresql":
            facility_id = str(request.current_facility.pk) if request.current_facility else ""
            # ``is_local=false`` -> SET auf Session-Ebene, bleibt ueber
            # nachfolgende ORM-Queries dieses Requests hinweg gueltig.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.current_facility_id', %s, false)",
                    [facility_id],
                )

        return self.get_response(request)
