"""Gemeinsame CSP-Header-Rewrite-Mechanik der Per-Request-Relax-Middlewares.

Refs #1827: ``AdminCSPRelaxMiddleware`` und ``DemoUmamiCSPRelaxMiddleware``
lockern beide je eine CSP-Direktive per Header-Rewrite — vorher mit einem
generischen Anhaenge-Helfer (``demo_umami_csp_relax._extend_directive``) auf
der einen Seite und einer wortgleichen Hand-Regex (``admin_csp_relax.py``,
``_SCRIPT_RELAX``) auf der anderen. Beide brauchten ausserdem denselben
``for header in (CSP, CSP-Report-Only): ... if not csp: continue; ...``-Rumpf.
Der ``style-src``-Sonderfall (Hash-/Nonce-Tokens raus, ``'unsafe-inline'``
rein) bleibt bewusst lokal in ``admin_csp_relax.py`` — er ist keine
Anhaenge-Operation, sondern ein Umbau der Direktive.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_CSP_HEADERS = ("Content-Security-Policy", "Content-Security-Policy-Report-Only")


def extend_directive(csp: str, directive: str, token: str) -> str:
    """Fügt ``token`` in die genannte CSP-Direktive ein (idempotent).

    Ist die Direktive nicht vorhanden oder der Token bereits enthalten, bleibt
    die Policy unverändert.
    """
    pattern = re.compile(rf"({re.escape(directive)}\s+[^;]*?)(?=\s*;|$)", re.IGNORECASE)

    def _repl(match: re.Match[str]) -> str:
        if token in match.group(1):
            return match.group(1)
        return f"{match.group(1)} {token}"

    return pattern.sub(_repl, csp, count=1)


def rewrite_csp_headers(response, transform: Callable[[str], str]) -> None:
    """Wendet ``transform`` auf beide CSP-Header an — mutiert ``response`` in place.

    Beide Relax-Middlewares iterierten denselben Rumpf identisch (Header holen,
    bei fehlendem Header ueberspringen, Ergebnis zurueckschreiben); nur die
    Transformation dazwischen unterscheidet sich je Middleware.
    """
    for header in _CSP_HEADERS:
        csp = response.headers.get(header)
        if not csp:
            continue
        response.headers[header] = transform(csp)
