"""Custom Error-Views (Refs #358, #699, #970, #1354).

Hier liegen Views, die Djangos Built-in-Error-Pages ersetzen — aktuell
die CSRF-Failure-View und der ``handler403`` (Ratelimited → 429).
400/404/500 nutzen Djangos Auto-Resolution via Templates ohne
View-Override.
"""

import logging

from django.http import HttpResponse
from django.shortcuts import render
from django.views.defaults import permission_denied as django_permission_denied
from django_ratelimit.exceptions import Ratelimited

logger = logging.getLogger("django.security.csrf")


def permission_denied(request, exception):
    """``handler403``: mappt ``Ratelimited`` auf HTTP 429 (Refs #1354, Befund K1c).

    django_ratelimits ``Ratelimited`` erbt von ``PermissionDenied`` und
    wuerde ohne diesen Handler als 403 ausgeliefert. Der Offline-Client
    deutet 403 aber als Rechteentzug und purgt lokale verschluesselte
    Bundles (``offline-store.js``, ``INVALIDATION_STATUSES``) — ein
    Rate-Limit-Treffer ist kein Rechteentzug und darf keine Client-Daten
    vernichten. Alle anderen ``PermissionDenied`` gehen unveraendert an
    Djangos Standard-403 (rendert ``403.html``).

    Die 429-Response ist bewusst schlicht (kein Template, kein
    ``Retry-After``, KISS): die JS-Clients werten nur den Statuscode aus.
    """
    if isinstance(exception, Ratelimited):
        return HttpResponse(status=429)
    return django_permission_denied(request, exception)


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
