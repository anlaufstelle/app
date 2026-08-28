"""Middleware: Sets request.current_facility from the authenticated user.

Zusaetzlich werden die PostgreSQL-Session-Variablen ``app.current_facility_id``
und ``app.is_super_admin`` gesetzt — damit greifen die in Migration 0047
eingerichteten Row-Level-Security-Policies (erweitert in 0085 um den
Superadmin-Bypass) als zweite Verteidigungslinie unterhalb der
Django-Facility-Scoping-Schicht.

Seit Refs #2067 setzt sie zusaetzlich ``Facility.is_active`` durch: eine
archivierte Einrichtung arbeitete bis dahin voll weiter, weil das Flag nirgends
gelesen wurde.

Refs #542, #586, #733, #867, #2067.
"""

from django.conf import settings
from django.db import connection
from django.http import HttpResponseForbidden
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.utils import translation

#: Pfade, die auch bei archivierter Einrichtung erreichbar bleiben — sonst
#: sitzt die Nutzerin in einer Sackgasse (kein Abmelden) oder die Sperrseite
#: rendert ohne Stylesheet. Bewusst KURZ: die Verwaltungspfade zum Reaktivieren
#: stehen NICHT drin, sie sind ueber die Rolle freigestellt (siehe
#: :meth:`FacilityScopeMiddleware._is_archived_blocked`).
ARCHIVED_EXEMPT_URLS = (
    "/login/",
    "/logout/",
    "/static/",
    "/i18n/",
    "/health/",
    "/sw.js",
    "/manifest.json",
)

#: Standalone-Seite ohne ``base.html``: die Navigation dort liest
#: Facility-gescopte Zaehler, die dieser Request per Definition nicht mehr
#: sehen darf.
ARCHIVED_TEMPLATE = "auth/facility_archived.html"

#: Maschinenlesbarer Marker auf JEDER Archiv-403 (Refs #2067, Cross-Review
#: Finding 1). Ohne ihn kaeme die Sperre beim Offline-Client nie an: ein nacktes
#: 403 ist dort seit Refs #1354 BEWUSST kein Invalidierungsgrund (echter
#: Rechteentzug erreicht den Client als Login-Redirect; ein 403 ist sonst
#: CSRF-/Rate-Limit-/Proxy-Rauschen und darf den Aussendienst-Cache nicht
#: leeren). Dieser Header unterscheidet die beiden Faelle, statt 403 pauschal
#: wieder zum Purge-Grund zu machen.
FACILITY_ARCHIVED_HEADER = "X-Facility-Archived"

#: Browser-nativer Zweitschlag (Refs #2067): raeumt IndexedDB (Krypto-Schluessel
#: UND Bundle-Store), Web Storage, Cache Storage und SW-Registrierungen ab —
#: genau der Mechanismus, den ``CustomLogoutView`` schon nutzt. Cookies bleiben
#: bewusst stehen (``"cookies"`` NICHT gesetzt), damit die Sitzung lebt und der
#: Abmelden-Knopf auf der Sperrseite traegt. Deckt den Navigations-Fall ab, in
#: dem gar kein JS des Offline-Verbunds laeuft; der Header oben deckt den
#: fetch-Fall ab. Beide zusammen, weil ``Clear-Site-Data`` nicht in jedem
#: Browser (Safari) vollstaendig greift.
CLEAR_SITE_DATA_VALUE = '"storage", "cache"'


class FacilityScopeMiddleware:
    """Sets request.current_facility to the user's facility.

    Setzt ausserdem ``app.current_facility_id`` und ``app.is_super_admin``
    auf Postgres-Ebene, damit RLS-Policies pro Request greifen. Ohne
    gesetzte Variablen liefern die Policies ``facility_id = current_setting(..., true)``
    einen NULL-Vergleich und somit keine Zeilen — Defense-in-Depth.

    Historisch wurde ``SET LOCAL`` (``is_local=true``) verwendet; das war
    transaktions-lokal und endete bereits mit der Statement-Transaktion
    des Middleware-Cursors, also noch bevor nachfolgende ORM-Queries in
    einem Request ueberhaupt liefen (Refs #586). Jetzt werden die
    Variablen session-weit gesetzt (``is_local=false``) und pro Request
    stets neu — auch fuer anonyme/non-super_admin User explizit auf ''
    geleert, damit Connection-Pooling keinen stehengebliebenen Wert aus
    einer frueheren authentifizierten Request leaken kann (Defense-in-
    Depth, Refs #733).

    Refs #867: ``app.is_super_admin`` wird zusaetzlich pro Request
    gesetzt — ``'true'`` bei super_admin, sonst ``''``. Die RLS-Policies
    aus 0085 erweitern den USING/WITH-CHECK-Filter um einen ``OR``-Branch,
    der bei gesetzter Var den Facility-Filter neutralisiert. Konsistente
    Always-Reset-Semantik wie bei current_facility_id.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_authenticated = hasattr(request, "user") and request.user.is_authenticated
        facility = getattr(request.user, "facility", None) if is_authenticated else None
        # Refs #2067: fail-closed bei archivierter Einrichtung. Die Pruefung
        # sitzt VOR der Zuweisung, damit ``current_facility`` (und damit die
        # RLS-GUC unten) leer bleibt — selbst wenn ein Pfad die Sperre umginge,
        # liefern die facility_isolation-Policies dann keine Zeile.
        blocked = self._is_archived_blocked(request, facility)
        request.current_facility = None if blocked else facility

        # Beide Variablen pro Request stets neu setzen — auch fuer
        # anonyme/non-super_admin User explizit auf '' leeren, damit
        # Connection-Pooling keinen stehengebliebenen Wert aus einer
        # frueheren authentifizierten Request leaken kann (Defense-in-
        # Depth, Refs #733, #867).
        if connection.vendor == "postgresql":
            facility_id = str(request.current_facility.pk) if (is_authenticated and request.current_facility) else ""
            is_super_admin = "true" if (is_authenticated and getattr(request.user, "is_super_admin", False)) else ""
            # ``is_local=false`` -> SET auf Session-Ebene, bleibt ueber
            # nachfolgende ORM-Queries dieses Requests hinweg gueltig.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.current_facility_id', %s, false), "
                    "       set_config('app.is_super_admin', %s, false)",
                    [facility_id, is_super_admin],
                )

        if blocked:
            # 403 statt Redirect: es gibt keinen Ort, an den die Nutzerin
            # sinnvoll umgeleitet werden koennte — die Sperre ist ein Zustand
            # ihrer Einrichtung, kein fehlender Schritt. Die Sitzung bleibt
            # bewusst bestehen, damit der Abmelden-Link auf der Seite traegt.
            # ``render_to_string`` ohne ``request``: die Context-Prozessoren
            # (workitem_counts, review_counts) fragen Facility-Daten ab, die
            # dieser Request gerade verloren hat. Das CSRF-Token fuer das
            # Abmelden-Formular kommt deshalb von Hand — ``get_token`` stempelt
            # ``request.META``, die CsrfViewMiddleware setzt das Cookie
            # ausgangsseitig (sie liegt vor dieser Middleware in MIDDLEWARE,
            # laeuft also nach ihr zurueck).
            # Refs #2067 (Cross-Review Finding 8): diese Middleware liegt VOR
            # ``UserLanguageMiddleware`` — die Sperrseite rendert also, bevor
            # die Sprachpraeferenz aktiviert wurde, und laege sonst immer auf
            # ``LANGUAGE_CODE``. Der User ist hier bereits bekannt, deshalb die
            # Praeferenz mit derselben Regel wie dort selbst aktivieren.
            lang = getattr(request.user, "preferred_language", "") or settings.LANGUAGE_CODE
            with translation.override(lang):
                body = render_to_string(ARCHIVED_TEMPLATE, {"csrf_token": get_token(request), "page_language": lang})
            response = HttpResponseForbidden(body)
            # Refs #2067 (Cross-Review N1): ``translation.override`` endet mit
            # dem Renderblock, und die aeussere ``LocaleMiddleware`` setzt
            # ``Content-Language`` danach aus der WIEDERHERGESTELLTEN
            # Request-Sprache — bei Praeferenz Englisch und ausgehandeltem
            # Deutsch stuenden sonst englischer Body und ``Content-Language:
            # de`` nebeneinander. Der Header wird deshalb explizit gesetzt;
            # ``LocaleMiddleware`` nutzt ``setdefault`` und laesst ihn stehen.
            response["Content-Language"] = lang
            # Refs #2067 (Cross-Review Finding 1): die Sperre muss auch den
            # OFFLINE-Pfad erreichen. Serverseitig sind API, HTMX, Exporte und
            # Replay ab hier dicht — bereits entschluesselt im Browser liegende
            # Bundles waren es nicht. Der Header laesst die Offline-
            # Revalidierung gezielt purgen (nacktes 403 bleibt harmlos, Refs
            # #1354), ``Clear-Site-Data`` raeumt zusaetzlich browser-nativ ab.
            response[FACILITY_ARCHIVED_HEADER] = "1"
            response["Clear-Site-Data"] = CLEAR_SITE_DATA_VALUE
            return response

        return self.get_response(request)

    @staticmethod
    def _is_archived_blocked(request, facility) -> bool:
        """Sperrt Requests von Nutzerinnen archivierter Einrichtungen.

        Drei Ausnahmen, jede mit eigenem Grund:

        * **keine Einrichtung** — anonyme Requests und Konten ohne Facility
          (typisch ``super_admin``) haben nichts, was archiviert sein koennte.
        * **``super_admin``** — die Rolle IST der Reaktivierungspfad:
          ``FacilityAdmin`` ist super-only (L1, Refs #1375), niemand sonst kann
          ``is_active`` wieder setzen. Freigestellt wird deshalb die Rolle und
          nicht der Pfad ``/admin-mgmt/``: eine Pfad-Ausnahme liesse den
          ``facility_admin`` der archivierten Einrichtung weiter an ihre
          Fachdaten, ohne dass er sie reaktivieren koennte.
        * **Sackgassen-Pfade** — Abmelden, Login, Statik, Health
          (:data:`ARCHIVED_EXEMPT_URLS`).
        """
        if facility is None or facility.is_active:
            return False
        if getattr(request.user, "is_super_admin", False):
            return False
        return not request.path.startswith(ARCHIVED_EXEMPT_URLS)
