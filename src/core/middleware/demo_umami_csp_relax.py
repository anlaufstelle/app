"""Per-Request-CSP-Relax für den Umami-Tracker auf der Demo (Refs #1604, #1701).

Auf ``demo.anlaufstelle.app`` soll — nach Einwilligung (Opt-in) — der Umami-
Tracker samt Session-Recorder/Heatmap laufen. Das Tracker-Skript und seine
Beacons brauchen dafür ``script-src`` **und** ``connect-src`` für den
Umami-Host. Der Host ist der Origin des per Deploy-Env konfigurierten
Tracker-Skripts (``settings.UMAMI_SCRIPT_SRC``, Refs #1701) — der Quelltext
trägt keinen instanzspezifischen Host; ohne konfiguriertes Skript ist die
Middleware ein No-Op (fail-closed).

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

from urllib.parse import urlsplit

from django.conf import settings

from core.middleware._csp import extend_directive, rewrite_csp_headers


def _umami_host() -> str:
    """Origin des per Deploy-Env konfigurierten Tracker-Skripts (Refs #1701).

    Leer, wenn kein (vollstaendiges, absolutes) Skript konfiguriert ist — die
    Middleware bleibt dann No-Op und die CSP strikt.
    """
    script_src = getattr(settings, "UMAMI_SCRIPT_SRC", "")
    if not script_src:
        return ""
    split = urlsplit(script_src)
    if not (split.scheme and split.netloc):
        return ""
    return f"{split.scheme}://{split.netloc}"


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

        # Ohne konfiguriertes Tracker-Skript gibt es nichts freizugeben —
        # fail-closed, die strikte CSP bleibt unveraendert (Refs #1701).
        umami_host = _umami_host()
        if not umami_host:
            return response

        def _relax(csp: str) -> str:
            csp = extend_directive(csp, "script-src", umami_host)
            return extend_directive(csp, "connect-src", umami_host)

        rewrite_csp_headers(response, _relax)
        return response
