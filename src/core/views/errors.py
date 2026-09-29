"""Custom error views (Refs #358, #699, #970, #1354, #1904).

This module holds views that replace Django's built-in error pages — the
CSRF failure view, ``handler403`` (Ratelimited -> 429), and ``handler500``
(reference id + operator contact). 400/404 still use Django's
auto-resolution via templates, without a view override.
"""

import logging
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import get_template
from django.utils.translation import gettext, gettext_noop
from django.views.defaults import permission_denied as django_permission_denied
from django_ratelimit.exceptions import Ratelimited

from core.logging import mask_paths_in_text, mask_url

logger = logging.getLogger("django.security.csrf")
server_error_logger = logging.getLogger(__name__)

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
    * ``reason`` traegt bei Referer-/Origin-Ablehnung die **vollstaendige**
      abgewiesene URL: Django setzt sie in ``REASON_BAD_REFERER`` bzw.
      ``REASON_BAD_ORIGIN`` ein (``django/middleware/csrf.py``). Die
      maskierten Einzelfelder ``referer=``/``origin=`` verhindern diese
      zweite Kopie nicht — der Grund laeuft deshalb an der Quelle durch
      ``mask_paths_in_text``. Die Fehlerklasse („Referer checking failed …
      does not match any trusted origins.") bleibt dabei erhalten, nur
      Query und Token-Segmente fallen weg.
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
        mask_paths_in_text(reason) if isinstance(reason, str) else reason,
        mask_url(request.path),
        mask_url(request.META.get("HTTP_REFERER", "-")),
        mask_url(request.META.get("HTTP_ORIGIN", "-")),
        request.headers.get("HX-Request") == "true",
        extra={"user_pk": user_pk} if user_pk is not None else {},
    )
    return render(request, "403_csrf.html", status=403)


def _generate_reference_id():
    """Build a random, human-readable reference id for a server error.

    Refs #1904: deliberately unrelated to the request or any stored data — a
    pure random token whose only job is to let the same incident be found
    again in the server log (matching ``request_id`` below) and named to the
    application's operator. ``secrets.token_hex(6)`` gives 12 hex digits
    (48 bits), grouped in blocks of four for easier reading and copying over
    the phone, e.g. ``A1B2-C3D4-E5F6``.
    """
    raw = secrets.token_hex(6).upper()
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def _support_contact():
    """Read the optional operator contact from settings (Refs #1904).

    Returns a plain dict with exactly the two pieces of information the
    ``500.html`` template needs: the configured text (empty when unset) and,
    only for a value that passes Django's email validator, a ``mailto:``
    href. A phone number or any other free-text contact is shown as plain
    text without a link. No facility or account data is involved — this is a
    single installation-wide operator setting.
    """
    contact = getattr(settings, "SUPPORT_CONTACT", "") or ""
    href = None
    if contact:
        try:
            validate_email(contact)
        except ValidationError:
            href = None
        else:
            href = f"mailto:{contact}"
    return {"text": contact, "href": href}


def server_error(request, template_name="500.html"):
    """``handler500``: adds a non-sensitive reference id and an operator contact.

    Refs #1904. Renders WITHOUT the request (``get_template(...).render(context)``
    instead of the ``render()`` shortcut) so this handler carries no context
    processors and never touches the database — the same failure that
    triggers a 500 may be the database connection itself, or a context
    processor that queries it. The context passed to the template holds
    exactly two values: the reference id and the resolved operator contact,
    both free of account, facility or request data.

    The log line is written before the render attempt, so the reference id
    reaches the log even if rendering the template itself fails; the
    fallback in that case is a plain-text body carrying the same id (never a
    second, template-free error page that could itself fail).
    """
    reference_id = _generate_reference_id()
    server_error_logger.error(
        "Unhandled server error method=%s path=%s",
        request.method,
        mask_url(request.path),
        extra={"request_id": reference_id},
    )
    context = {"reference_id": reference_id, "contact": _support_contact()}
    try:
        body = get_template(template_name).render(context)
    except Exception:
        fallback = gettext("Serverfehler. Bitte versuchen Sie es später erneut. Referenz-ID: %(reference_id)s") % {
            "reference_id": reference_id
        }
        return HttpResponse(fallback, status=500, content_type="text/plain; charset=utf-8")
    return HttpResponse(body, status=500)
