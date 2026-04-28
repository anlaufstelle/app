"""Per-Request-CSP-Relax für die Django-Admin-UI.

django-unfold lädt seinen eigenen Alpine.js-Build (classical, mit ``new
AsyncFunction()``-basierter Expression-Auswertung). Unsere globale CSP
``script-src 'self'`` blockt das mit ``unsafe-eval`` violations — Folge:
Admin-Modals (z.B. die Cmd+K-Suche-Overlay ``searchCommand``) initialisieren
nicht und bleiben sichtbar, weil ``x-show="openCommandResults"`` nie greift.
Das blockiert E2E-Tests, die Admin-Actions klicken (Refs E2E-Flakiness in
v0.10.1, scheitert auf den Admin-Unlock-Tests).

Statt die globale CSP aufzuweichen, wird ``unsafe-eval`` hier nur für die
``/admin-mgmt/``-Pfade ergänzt — diese Routes sind ohnehin auf authentifizierte
Admin/Lead-Rollen mit MFA beschränkt, der Trade-off ist akzeptabel.

Die Middleware muss **nach** ``csp.middleware.CSPMiddleware`` registriert sein,
damit der bereits gesetzte ``Content-Security-Policy``-Header umgeschrieben
werden kann.
"""

from __future__ import annotations

import re

ADMIN_PREFIX = "/admin-mgmt/"
_SCRIPT_SRC_RE = re.compile(r"(script-src\s+[^;]*?)(?=\s*;|$)", re.IGNORECASE)


class AdminCSPRelaxMiddleware:
    """Ergänzt ``unsafe-eval`` in script-src für Admin-Routes."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith(ADMIN_PREFIX):
            return response

        for header in ("Content-Security-Policy", "Content-Security-Policy-Report-Only"):
            csp = response.headers.get(header)
            if not csp or "'unsafe-eval'" in csp:
                continue
            new_csp = _SCRIPT_SRC_RE.sub(
                lambda m: f"{m.group(1)} 'unsafe-eval'",
                csp,
                count=1,
            )
            response.headers[header] = new_csp
        return response
