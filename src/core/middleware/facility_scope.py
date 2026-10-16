"""Middleware: Sets request.current_facility from the authenticated user.

Zusaetzlich werden die PostgreSQL-Session-Variablen ``app.current_facility_id``
und ``app.is_super_admin`` gesetzt — damit greifen die in Migration 0047
eingerichteten Row-Level-Security-Policies (erweitert in 0085 um den
Superadmin-Bypass) als zweite Verteidigungslinie unterhalb der
Django-Facility-Scoping-Schicht.

Seit Refs #2067 setzt sie zusaetzlich ``Facility.is_active`` durch: eine
archivierte Einrichtung arbeitete bis dahin voll weiter, weil das Flag nirgends
gelesen wurde.

Seit Refs #2090 faengt sie den zweiten Zustand ohne nutzbaren
Einrichtungskontext an derselben Stelle ab: ein angemeldetes Konto **ohne**
Einrichtung, das nicht ``super_admin`` ist. ``User.facility`` ist ``null=True``
(``super_admin`` traegt die Bindung per Design nicht, die Benutzerverwaltung
kann sie jedem Konto entziehen) — fuer jede andere Rolle lief der Zustand
bisher in **HTTP 500**: ``get_scoped_object()`` wirft ohne
``request.current_facility`` mit Absicht ein ``ValueError`` (66 Aufrufstellen
in 17 View-Modulen), und rund ein Dutzend Service-Funktionen dereferenzieren
``facility.settings``/``facility.pk`` ungeprueft. Der Entscheid ist „an der
Wurzel abfangen statt in jeder View": die Antwort ist eine erklaerende 403
**ohne eigene Route** — sonst zoege sie Routenzahl, Backlink-Registry,
Nav-Klassifizierung und 257 AuthZ-Matrix-Zellen nach sich.

Refs #542, #586, #733, #867, #2067, #2090.
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

#: Refs #2090: Pfade, die auch ohne Einrichtungsbindung erreichbar bleiben.
#:
#: Nicht frei gewaehlt, sondern **abgeleitet** aus der AuthZ-Erwartungstabelle
#: (``src/tests/_authz_expectations.py``): es sind exakt die Kategorien
#: ``public`` (anonym erreichbar) und ``auth-flow`` (jede angemeldete Rolle,
#: inklusive des per Design einrichtungslosen ``super_admin``). Die Regel
#: dahinter: wer anonym auf eine Seite darf, darf angemeldet nicht schlechter
#: gestellt sein — und was jede Rolle sehen darf, haengt nicht an der
#: Einrichtung. ``src/tests/test_authz_facility_less.py`` haelt beide
#: Richtungen fest: jeder Praefix wird von mindestens einer solchen Route
#: getroffen, und keine facility-gebundene Route faellt darunter.
#:
#: ``/system/`` und ``/admin-mgmt/`` stehen bewusst NICHT drin — dieselbe
#: Doktrin wie bei :meth:`FacilityScopeMiddleware._is_archived_blocked`:
#: freigestellt wird die ROLLE, nicht der Pfad. Eine Pfad-Ausnahme liesse einen
#: einrichtungslosen ``facility_admin`` in den Systembereich; die Rollen-
#: Ausnahme nicht. Fuer ihn antwortet dort ohnehin ``SuperAdminRequiredMixin``
#: mit 403 — es aendert sich nur die Quelle der Antwort.
FACILITY_MISSING_EXEMPT_URLS = (
    # Sackgassen-Basis, deckungsgleich mit ARCHIVED_EXEMPT_URLS
    "/login/",
    "/logout/",
    "/static/",
    "/i18n/",
    "/health/",
    "/sw.js",
    "/manifest.json",
    # Anmelde- und Zweitfaktor-Ketten — ohne sie sperrt sich das Konto aus
    "/password-change/",
    "/password-reset/",
    "/invite/",
    "/mfa/",
    "/webauthn/",
    "/sudo/",
    "/auth/offline-key-salt/",
    # Eigenes Konto inklusive Lockout-Selbsthilfe (/account/recovery/...)
    "/account/",
    # Rollen-Einstieg, Hilfe (die Uebergabe steht als exakter Pfad unten:
    # unter /uebergabe/notiz/ liegt seit Refs #2042 eine facility-gebundene
    # Schreibroute, die hinter den Guard gehoert)
    "/start/",
    "/hilfe/",
    # Refresh-Pfad des Offline-Replays; die uebrigen /api/v1/offline/-Endpunkte
    # sind facility-gebunden und bleiben BEWUSST draussen.
    "/api/v1/offline/csrf/",
    # oeffentlich und PII-frei
    "/robots.txt",
    "/csp-report/",
    "/impressum/",
    "/datenschutzerklaerung/",
)

#: Refs #2090: der ``/offline/``-Baum, Pfad fuer Pfad statt als Praefix.
#:
#: Hier teilen sich zwei Kategorien dieselbe Wurzel: die neun PII-freien
#: Shells (``public``, vom Service Worker precacht) und zwei facility-gebundene
#: pk-Routen (``core:offline_client_detail`` unter ``/offline/clients/<uuid>/``
#: und ``core:offline_conflict_review`` unter ``/offline/conflicts/<uuid>/``,
#: beide ``offline-api``). Ein Praefix ``/offline/`` liesse die beiden mit
#: durch — genau das hat der Whitelist-Test in
#: ``src/tests/test_authz_facility_less.py`` aufgedeckt. Exakter Vergleich
#: haelt die Ableitung „freigestellt ist genau ``public`` + ``auth-flow``"
#: ohne eine einzige gepflegte Ausnahme dicht.
FACILITY_MISSING_EXEMPT_PATHS = (
    # Uebergabe-Redirect/-Ansicht (public, ALL_AUTH): exakt, damit die
    # Schreibroute /uebergabe/notiz/ (facility-write, Refs #2042) nicht mit
    # durch einen Praefix rutscht.
    "/uebergabe/",
    "/offline/",
    "/offline/clients/",
    "/offline/client-shell/",
    "/offline/event-shell/",
    "/offline/workitem-shell/",
    "/offline/workitems/",
    "/offline/zeitstrom/",
    "/offline/conflicts/",
    "/offline/conflict-shell/",
    # Demo-Bereichsauswahl (nur bei DEMO_MODE, sonst 404) — oeffentlich, PII-frei.
    # Exakt statt Praefix, damit kuenftige Routen unter /demo/ nicht still aus
    # dem Einrichtungs-Guard fallen (Refs #2285, #2351).
    "/demo/",
    "/demo/start/",
    # Session-only key confirmation needs no facility data (Refs #2602).
    "/demo/offline-key-ready/",
)

#: Refs #2090: Standalone-Seite ohne ``base.html`` — aus demselben Grund wie
#: :data:`ARCHIVED_TEMPLATE`. Sie traegt bewusst KEIN Purge-Skript: der Zustand
#: ist typischerweise eine Fehlkonfiguration, die zurueckgenommen wird (siehe
#: :meth:`FacilityScopeMiddleware._is_facility_missing_blocked`).
FACILITY_MISSING_TEMPLATE = "auth/facility_missing.html"


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
            response = self._render_block(request, ARCHIVED_TEMPLATE)
            # Refs #2067 (Cross-Review Finding 1): die Sperre muss auch den
            # OFFLINE-Pfad erreichen. Serverseitig sind API, HTMX, Exporte und
            # Replay ab hier dicht — bereits entschluesselt im Browser liegende
            # Bundles waren es nicht. Der Header laesst die Offline-
            # Revalidierung gezielt purgen (nacktes 403 bleibt harmlos, Refs
            # #1354), ``Clear-Site-Data`` raeumt zusaetzlich browser-nativ ab.
            response[FACILITY_ARCHIVED_HEADER] = "1"
            response["Clear-Site-Data"] = CLEAR_SITE_DATA_VALUE
            return response

        # Refs #2090: derselbe Zustand aus der anderen Richtung — ein Konto
        # ohne Einrichtung. Die Pruefung sitzt NACH dem ``set_config``-Block
        # (die GUC muss auch im Sperrfall geleert werden, Refs #733) und NACH
        # dem Archiv-Zweig (eine archivierte Einrichtung ist nicht „keine
        # Einrichtung" und bekommt weiterhin ihre eigene, sachlich richtige
        # Seite).
        if self._is_facility_missing_blocked(request, facility, is_authenticated):
            # Bewusst OHNE ``X-Facility-Archived`` und ``Clear-Site-Data``:
            # ein nacktes 403 ist fuer den Offline-Client seit Refs #1354 KEIN
            # Invalidierungsgrund, und das ist hier richtig so. Bei der
            # Archivierung endet der Zugriff auf Fachdaten dauerhaft; hier ist
            # der Zustand typischerweise eine Fehlkonfiguration, die
            # zurueckgenommen wird. Ein Purge loeschte Aussendienst-Caches und
            # die IndexedDB-Kryptoschluessel — nicht reversibel.
            return self._render_block(request, FACILITY_MISSING_TEMPLATE)

        return self.get_response(request)

    @staticmethod
    def _render_block(request, template):
        """Rendert eine Sperrseite als 403 — geteilt von beiden Sperren.

        ``render_to_string`` **ohne** ``request``: die Context-Prozessoren
        (``workitem_counts``, ``review_counts``) fragen Facility-Daten ab, die
        dieser Request per Definition nicht sehen darf. Das CSRF-Token fuer das
        Abmelden-Formular kommt deshalb von Hand — ``get_token`` stempelt
        ``request.META``, die ``CsrfViewMiddleware`` setzt das Cookie
        ausgangsseitig (sie liegt vor dieser Middleware in ``MIDDLEWARE``,
        laeuft also nach ihr zurueck).

        Refs #2102: der gemeinsame Footer (``components/_public_footer.html``)
        braucht ``SOURCE_CODE_URL`` (AGPL §13, Refs #835) und die
        Rechtstext-URLs. Statt den ganzen Prozessor-Stack ueber ``request=``
        einzuschalten — und damit genau die Facility-Abfragen oben — werden
        die beiden **facility-freien** Prozessoren einzeln aufgerufen. Beide
        lesen ausschliesslich Settings bzw. das Dateisystem, keine
        Mandantendaten. ``user`` bleibt bewusst draussen: ohne ihn (und ohne
        ``footer_authenticated``) rendert der Footer garantiert ohne Versions-,
        Build- und Hilfe-Anzeige (Refs #1901/#1050).

        Sprache: diese Middleware liegt VOR ``UserLanguageMiddleware``, die
        Seite rendert also, bevor die Praeferenz aktiviert waere, und laege
        sonst immer auf ``LANGUAGE_CODE``. Der User ist hier bereits bekannt,
        deshalb wird die Praeferenz mit derselben Regel wie dort selbst
        aktiviert. ``Content-Language`` wird explizit gesetzt, weil
        ``translation.override`` mit dem Renderblock endet und die aeussere
        ``LocaleMiddleware`` den Header sonst aus der WIEDERHERGESTELLTEN
        Request-Sprache setzte — englischer Body neben ``Content-Language:
        de``. Sie nutzt ``setdefault`` und laesst den Wert stehen.

        Refs #2067 (Cross-Review Finding 8 + N1), #2090.
        """
        from core import context_processors

        lang = getattr(request.user, "preferred_language", "") or settings.LANGUAGE_CODE
        context = {
            "csrf_token": get_token(request),
            "page_language": lang,
            **context_processors.source_code(request),
            **context_processors.legal_pages(request),
        }
        with translation.override(lang):
            body = render_to_string(template, context)
        response = HttpResponseForbidden(body)
        response["Content-Language"] = lang
        return response

    @staticmethod
    def _is_facility_missing_blocked(request, facility, is_authenticated) -> bool:
        """Sperrt Requests von angemeldeten Konten ohne Einrichtungsbindung.

        Drei Ausnahmen, jede mit eigenem Grund:

        * **anonym** — ohne Anmeldung ist „keine Einrichtung" kein Defekt,
          sondern der Normalzustand; die Views antworten dort mit dem
          Login-Redirect, und ein 403 wuerde ihn ersetzen.
        * **``super_admin``** — die Rolle traegt ``facility=None`` PER DESIGN
          (``create_super_admin``, Seed, Erstinstallation) und ist zugleich der
          einzige Reparaturpfad: nur sie kann einem Konto ueber
          ``/admin-mgmt/`` wieder eine Einrichtung zuordnen. Freigestellt wird
          deshalb die Rolle und nicht der Pfad — eine Pfad-Ausnahme fuer
          ``/system/``/``/admin-mgmt/`` liesse einen einrichtungslosen
          ``facility_admin`` in den Systembereich.
        * **Sackgassen- und Selbstbedienungspfade** — Abmelden, Anmelden, die
          Zweitfaktor- und Passwortketten, das eigene Konto, Hilfe und die
          Offline-Shells. Ohne sie sperrte der Guard das Konto komplett aus.
          Zwei Listen, weil zwei Formen noetig sind:
          :data:`FACILITY_MISSING_EXEMPT_URLS` als Praefix (ganze Teilbaeume)
          und :data:`FACILITY_MISSING_EXEMPT_PATHS` exakt (der
          ``/offline/``-Baum, in dem freie Shells und facility-gebundene
          pk-Routen dieselbe Wurzel teilen).

        Refs #2090.
        """
        if not is_authenticated or facility is not None:
            return False
        if getattr(request.user, "is_super_admin", False):
            return False
        if request.path in FACILITY_MISSING_EXEMPT_PATHS:
            return False
        return not request.path.startswith(FACILITY_MISSING_EXEMPT_URLS)

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
