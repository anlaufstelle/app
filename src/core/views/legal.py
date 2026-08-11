"""Rechtstexte der oeffentlichen Demo — Impressum + Datenschutz (Refs #1641).

Eigene Seiten der Demo (``demo.anlaufstelle.app``) statt Links auf die Webseite:
Die Demo verarbeitet mehr als die Marketing-Webseite (Umami-Opt-in-Tracking
inkl. Session-Recording, geteilte Demo-Logins, stuendlicher Reset), deshalb
braucht sie eigene, darauf zugeschnittene Rechtstexte.

Die Views sind **oeffentlich** (kein ``LoginRequiredMixin`` — die Rechtstexte
muessen von der Anmeldeseite aus ohne Login erreichbar sein) und **nur bei
``DEMO_MODE=True`` aktiv**: ausserhalb der Demo geben sie ``Http404`` zurueck
(request-zeitiger ``DEMO_MODE``-Check, analog ``DemoGuardMiddleware`` /
``DemoUmamiCSPRelaxMiddleware`` — kein import-zeitiges Verdrahten der Routen,
damit ``override_settings`` in Tests greift).
"""

from django.conf import settings
from django.http import Http404
from django.views.generic import TemplateView


class DemoOnlyTemplateView(TemplateView):
    """Basisklasse: liefert die Seite nur bei ``DEMO_MODE=True``, sonst 404.

    Der Check laeuft in ``dispatch`` (request-zeitig) statt ueber eine
    settings-abhaengige URL-Registrierung — so ist das Verhalten mit
    ``override_settings(DEMO_MODE=...)`` testbar und deckt sich mit dem Muster
    der Demo-Middlewares (``getattr(settings, "DEMO_MODE", False)``).
    """

    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "DEMO_MODE", False):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class DemoImprintView(DemoOnlyTemplateView):
    """GET /impressum/ — Impressum der Demo (nur ``DEMO_MODE``)."""

    template_name = "core/legal/imprint.html"


class DemoPrivacyView(DemoOnlyTemplateView):
    """GET /datenschutzerklaerung/ — Datenschutzerklaerung der Demo (nur ``DEMO_MODE``)."""

    template_name = "core/legal/privacy.html"
