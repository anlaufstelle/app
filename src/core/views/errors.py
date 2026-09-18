"""Custom Error-Views (Refs #358, #699, #970, #1354).

Hier liegen Views, die Djangos Built-in-Error-Pages ersetzen — aktuell
die CSRF-Failure-View und der ``handler403`` (Ratelimited → 429).
400/404/500 nutzen Djangos Auto-Resolution via Templates ohne
View-Override.
"""

import logging

from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext, gettext_noop
from django.views.defaults import permission_denied as django_permission_denied
from django_ratelimit.exceptions import Ratelimited

from core.logging import mask_url

logger = logging.getLogger("django.security.csrf")

#: Hinweistext der 429-Antwort fuer HTMX-Aufrufer (Refs #1873).
#:
#: Der deutsche Quellstring ist zugleich die ``msgid`` — ``gettext_noop``
#: meldet ihn dem Katalog an, uebersetzt wird erst im Request (Sprache des
#: Nutzers). Bewusst allgemein formuliert („Anfragen", nicht
#: „Suchanfragen"): ``handler403`` bedient JEDE ratelimitierte Flaeche der
#: App, ein such-spezifischer Text waere bei einer Sammelaktion schlicht
#: falsch. Er nennt weder Treffer noch deren Abwesenheit — genau diese
#: Verwechslung war der gemeldete Fehler.
RATELIMIT_HINWEIS = gettext_noop("Zu viele Anfragen in kurzer Zeit. Bitte kurz warten und es erneut versuchen.")


def permission_denied(request, exception):
    """``handler403``: mappt ``Ratelimited`` auf HTTP 429 (Refs #1354, Befund K1c).

    django_ratelimits ``Ratelimited`` erbt von ``PermissionDenied`` und
    wuerde ohne diesen Handler als 403 ausgeliefert. Der Offline-Client
    deutet 403 aber als Rechteentzug und purgt lokale verschluesselte
    Bundles (``offline-store.js``, ``INVALIDATION_STATUSES``) — ein
    Rate-Limit-Treffer ist kein Rechteentzug und darf keine Client-Daten
    vernichten. Alle anderen ``PermissionDenied`` gehen unveraendert an
    Djangos Standard-403 (rendert ``403.html``).

    Die 429-Response bleibt schlicht (kein Template, kein ``Retry-After``,
    KISS): die Offline-JS-Clients werten nur den Statuscode aus.

    Refs #1873: **Nur** fuer HTMX-Aufrufer traegt sie zusaetzlich einen
    kurzen Klartext-Hinweis. ``htmx-errors.js`` zeigt einen solchen Body
    woertlich im Fehler-Toast an; ohne ihn sah ein Rate-Limit-Treffer in der
    Suche exakt aus wie ein Serverfehler — oder, wenn das alte Ergebnis
    stehen blieb, wie „keine Treffer". Ausserhalb von HTMX bleibt die Antwort
    nackt, damit die #1354-Entscheidung fuer die Offline-Clients unangetastet
    ist (bewacht von ``test_offline_bundle_api.py``).
    """
    if isinstance(exception, Ratelimited):
        if request.headers.get("HX-Request") == "true":
            return HttpResponse(
                gettext(RATELIMIT_HINWEIS),
                status=429,
                content_type="text/plain; charset=utf-8",
            )
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

    Refs #2228: Diese Zeile ist ein eigener Sink — die Server-Logdatei —, und
    sie lief bis dahin roh. Zwei Aenderungen heben sie auf denselben
    Scrub-Vertrag wie den Sentry-Pfad:

    * ``path``/``referer``/``origin`` laufen durch ``mask_url``: der
      Query-Teil faellt (dort steht der Suchbegriff, und ein Suchbegriff ist
      oft ein Klient*innen-Name), der Pfad wird gegen das Route-Schema
      aufgeloest (``/invite/<uidb64>/<token>/`` statt des live nutzbaren
      Tokens). Genau diese Zeile war der Anlass fuer die Pfad-Maskierung im
      Freitext (Refs #2198) — nur griff sie bisher erst auf dem Weg zum
      Tracker, nicht in der Logdatei.
    * Statt des Login-Namens steht die User-PK im ``extra`` — dasselbe
      ``user_pk``-Feld wie in den Einladungs-Log-Zeilen
      (``core/services/security/invite.py``, Refs #1949). Ein Login-Name ist
      in einer kleinen Einrichtung real-namensnah; die PK ordnet den Vorfall
      genauso eindeutig einem Konto zu, ohne Personenbezug im Klartext. Ohne
      Anmeldung bleibt das Feld weg — ``anonymous`` war ohnehin nur ein
      Platzhalter, und das Fehlen des Feldes sagt dasselbe.
    """
    user_pk = getattr(getattr(request, "user", None), "pk", None)
    logger.warning(
        "CSRF failure reason=%r path=%s referer=%s origin=%s htmx=%s",
        reason,
        mask_url(request.path),
        mask_url(request.META.get("HTTP_REFERER", "-")),
        mask_url(request.META.get("HTTP_ORIGIN", "-")),
        request.headers.get("HX-Request") == "true",
        extra={"user_pk": user_pk} if user_pk is not None else {},
    )
    return render(request, "403_csrf.html", status=403)
