"""Per-Request-CSP-Relax für den Umami-Tracker auf der Demo (Refs #1604).

Auf ``demo.anlaufstelle.app`` soll — nach Einwilligung (Opt-in) — der Umami-
Tracker samt Session-Recorder/Heatmap laufen (``umami.example.org``). Das Tracker-
Skript und seine Beacons brauchen dafür ``script-src`` **und** ``connect-src``
für den Umami-Host.

Die globale CSP bleibt bewusst strikt ``script-src 'self'``: laut
[ADR-022](../../../docs/adr/022-offline-snapshot-keys.md) § (c) ist genau diese
Strenge der Schutzwall gegen fremden JavaScript-Zugriff auf ``window.offlineStore``
(entschlüsselte Art-9-Offline-Daten). Statt den Host global freizugeben (wie es
devlive für die interne Dev-Instanz tut), lockert diese Middleware die CSP
**eng gescoped**:

- **nur bei ``settings.DEMO_MODE=True``** (No-Op auf prod/devlive/dev/test),
- **nur für authentifizierte Requests** (Post-Login) — die **Login-Seite bleibt
  strikt** und damit tracking-frei (Issue-Anforderung #1604),
- **nur für HTML-Dokumente** (JSON/CSV/Statik führen kein Tracker-Skript aus).

Die Einwilligung selbst ist client-seitig (``umami-consent.js``): ohne
Zustimmung wird kein Skript injiziert. Die CSP-Freigabe ist nur die *Decke* —
sie triggert kein Tracking, sie erlaubt es nach Consent.

Die Middleware muss **vor** ``csp.middleware.CSPMiddleware`` registriert sein
(analog ``AdminCSPRelaxMiddleware``), damit sie in Outbound-Reihenfolge den
bereits gesetzten ``Content-Security-Policy``-Header noch umschreiben kann.
"""

from __future__ import annotations

import re

from django.conf import settings

UMAMI_HOST = "https://umami.example.org"


def _extend_directive(csp: str, directive: str, token: str) -> str:
    """Fügt ``token`` in die genannte CSP-Directive ein (idempotent).

    Ist die Directive nicht vorhanden oder der Token bereits enthalten, bleibt
    die Policy unverändert.
    """
    pattern = re.compile(rf"({re.escape(directive)}\s+[^;]*?)(?=\s*;|$)", re.IGNORECASE)

    def _repl(match: re.Match[str]) -> str:
        if token in match.group(1):
            return match.group(1)
        return f"{match.group(1)} {token}"

    return pattern.sub(_repl, csp, count=1)


class DemoUmamiCSPRelaxMiddleware:
    """Ergänzt den Umami-Host in ``script-src``/``connect-src`` — nur auf der
    Demo, nur Post-Login, nur für HTML."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Fail-closed-Gate: ausserhalb der Demo NIE lockern. Der Schutzwall
        # `script-src 'self'` (ADR-022) bleibt auf prod/devlive/dev/test intakt.
        if not getattr(settings, "DEMO_MODE", False):
            return response

        # Nur HTML-Dokumente führen ein Tracker-Skript aus (Content-Type-Check
        # zuerst — spart die Session-/User-Aufloesung bei Statik/JSON).
        if not response.headers.get("Content-Type", "").lower().startswith("text/html"):
            return response

        # Nur Post-Login: die Login-Seite (anonym) bleibt strikt/tracking-frei.
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return response

        for header in ("Content-Security-Policy", "Content-Security-Policy-Report-Only"):
            csp = response.headers.get(header)
            if not csp:
                continue
            csp = _extend_directive(csp, "script-src", UMAMI_HOST)
            csp = _extend_directive(csp, "connect-src", UMAMI_HOST)
            response.headers[header] = csp
        return response
