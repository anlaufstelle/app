"""Custom Error-Views (Refs #358, #699, #970).

Hier liegen Views, die Djangos Built-in-Error-Pages ersetzen — aktuell
nur die CSRF-Failure-View. 400/403/404/500 nutzen Djangos
Auto-Resolution via Templates ohne View-Override.
"""

import logging

from django.shortcuts import render

logger = logging.getLogger("django.security.csrf")


def csrf_failure(request, reason=""):
    """CSRF-Failure-View — liefert ``403_csrf.html`` mit Status 403.

    Refs #699: Djangos Built-in-Page passt nicht zum Design-System und
    ist auf Englisch. Unsere Version nutzt ``base.html``-Inheritance
    (Tailwind, DE) und bietet Reload + Startseiten-Link.

    Refs #970: ``reason`` + Origin/Referer/HTMX-Header werden auf WARNING
    geloggt. Revidiert die urspruengliche #699-Entscheidung (``reason``
    fuer User irrelevant) — fuer DevOps ist die Information bei
    Production-Vorfaellen wie dem CSRF-403-nach-Login-Bug unverzichtbar.
    Der User sieht das Log nicht; es geht ausschliesslich an die
    Server-Logs.
    """
    logger.warning(
        "CSRF failure reason=%r path=%s referer=%s origin=%s htmx=%s user=%s",
        reason,
        request.path,
        request.META.get("HTTP_REFERER", "-"),
        request.META.get("HTTP_ORIGIN", "-"),
        request.headers.get("HX-Request") == "true",
        getattr(getattr(request, "user", None), "username", "") or "anonymous",
    )
    return render(request, "403_csrf.html", status=403)
