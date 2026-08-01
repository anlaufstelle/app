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

I3 (Refs #1376), Revert nach Review 2026-07-17: Ein frueherer Versuch hat die
Lockerung auf authentifizierte Requests eingeschraenkt, in der Annahme,
``/admin-mgmt/login/`` laede keinen Unfold-Alpine-Build, der ``unsafe-eval``
braucht. Das ist empirisch falsch: ``admin/login.html`` erbt ueber
``unfold/layouts/unauthenticated.html`` von ``unfold/layouts/skeleton.html``,
und dieses Skeleton setzt ``x-data="theme(...)"`` auf ``<html>`` und laedt
``unfold/js/alpine/alpine.js`` **unconditional** — also auch fuer die anonym
erreichbare Login-Seite. Ohne ``unsafe-eval`` wirft Alpines Evaluator beim
Auswerten von ``theme()`` einen ``EvalError``, bevor ``x-cloak`` entfernt
wird; die Login-Seite bliebe komplett ``display:none`` (leere Seite). Die
Lockerung muss deshalb unabhaengig vom Auth-Status greifen; I3 ist mit dem
aktuellen Unfold-Alpine-Build nicht umsetzbar, ohne die Login-Seite zu
zerstoeren (offen bleibt nur eine tiefere Unfold-Anpassung, z. B. CSP-sicherer
Alpine-Build oder Verzicht auf die ``theme()``-Bindung im unauthentifizierten
Skeleton — siehe #1376).

Refs #1660 (I5 aus #1376): Dieselbe eng gescopte Ausnahme gilt seit der
``style-src``-Härtung auch für ``'unsafe-inline'`` in ``style-src``. Die
**eigene** App kommt ohne Inline-Styles aus (Guard:
``tests/test_architecture_guards_templates.py::TestNoInlineStylesGuard``), das
gevendorte django-unfold nicht: ``unfold/layouts/skeleton.html`` rendert einen
``<style id="unfold-theme-colors">``-Block, dessen Inhalt aus der
``UNFOLD["COLORS"]``-Konfiguration **dynamisch** zusammengesetzt wird — ein
statischer sha256-Hash ist damit prinzipiell unmöglich. Dazu kommen rund ein
Dutzend ``style="…"``-Attribute in Unfold-Templates (Progress-Bars, Avatare,
Sidebar). Ohne die Lockerung verlöre die Admin-UI ihr komplettes Farbschema.
Der Trade-off ist derselbe wie bei ``unsafe-eval``: ``/admin-mgmt/`` ist auf
``super_admin``/``facility_admin`` mit MFA- und Sudo-Mode-Pflicht beschränkt,
rate-limited, auditiert und durch RLS gegen Cross-Facility-Leaks abgesichert.

Die Middleware muss **nach** ``csp.middleware.CSPMiddleware`` registriert sein,
damit der bereits gesetzte ``Content-Security-Policy``-Header umgeschrieben
werden kann.
"""

from __future__ import annotations

import re

ADMIN_PREFIX = "/admin-mgmt/"
# (Directive, Token) — pro Direktive genau eine Lockerung, idempotent.
_RELAXATIONS = (
    (re.compile(r"(script-src\s+[^;]*?)(?=\s*;|$)", re.IGNORECASE), "'unsafe-eval'"),
    (re.compile(r"(style-src\s+[^;]*?)(?=\s*;|$)", re.IGNORECASE), "'unsafe-inline'"),
)


class AdminCSPRelaxMiddleware:
    """Ergänzt ``unsafe-eval`` in script-src und ``unsafe-inline`` in style-src
    für Admin-Routes."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith(ADMIN_PREFIX):
            return response

        # S4 (Refs #1084): Relax nur fuer HTML-Dokumente — JSON-/CSV-Responses
        # unter /admin-mgmt/ (Autocomplete, Exporte) fuehren kein Script aus
        # und behalten die strikte globale CSP.
        if not response.headers.get("Content-Type", "").lower().startswith("text/html"):
            return response

        for header in ("Content-Security-Policy", "Content-Security-Policy-Report-Only"):
            csp = response.headers.get(header)
            if not csp:
                continue
            for pattern, token in _RELAXATIONS:
                # Idempotent: der Token darf nur genau einmal in seiner
                # Direktive landen (Middleware kann bei Retry/Rewrite doppelt
                # laufen). Geprueft wird gegen die Direktive selbst, nicht
                # gegen die ganze Policy — 'unsafe-inline' in style-src darf
                # 'unsafe-eval' in script-src nicht blockieren.
                match = pattern.search(csp)
                if match is None or token in match.group(1):
                    continue
                csp = pattern.sub(lambda m, _t=token: f"{m.group(1)} {_t}", csp, count=1)
            response.headers[header] = csp
        return response
