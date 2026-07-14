"""No-Store-Cache-Middleware fuer authentifizierte Responses (Refs #1342).

DSGVO-Haertung Teil 1 (Teil 2, der Log-Scrubber, ist bereits auf main —
Refs #1500, ``core/logging.py``). Ohne ``Cache-Control: no-store`` landen
personenbezogene Antworten (Fach-Views, Offline-Bundles, Exports) im
Festplatten-Cache des Browsers und im bfcache — ein Folgenutzer desselben
Geraets (Kiosk, geteilter Laptop) koennte sie ueber Zurueck-Navigation oder
den Browser-Cache erneut sehen, auch nach Logout.

Blanket-Ansatz statt Einzel-View-Opt-in: JEDE Response an einen
authentifizierten User bekommt den Header, robust gegen kuenftige
PII-Views, die den Header vergessen. Bewusste Nebenwirkung: bfcache wird
fuer eingeloggte Seiten deaktiviert (Zurueck-Button laedt neu statt aus dem
Cache) — UX-Trade-off zugunsten des DSGVO-Schutzziels.

``setdefault`` statt hartem Ueberschreiben: Views mit einem bereits
gesetzten, bewusst gewaehlten ``Cache-Control``-Header (z.B.
``OfflineCsrfTokenView``, ``UserAdmin.response_add``) behalten ihren Wert.

Liegt NACH ``AuthenticationMiddleware`` (braucht ``request.user``).
"""

from __future__ import annotations


class NoStoreCacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            response.setdefault("Cache-Control", "no-store, private")

        return response
