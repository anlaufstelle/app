"""Feste Basis-Origin fuer sicherheitsrelevante Links (Refs #1967).

Einladungs-, Passwort-Reset- und Entsperr-Links tragen ein Token und muessen
deshalb auf einen Host zeigen, den der Betrieb festgelegt hat — nicht auf den
Host aus dem eingehenden Request. ``ALLOWED_HOSTS`` sperrt in Produktion zwar
den Match-all-Eintrag ``*``, laesst Subdomain-Wildcards (``.example.org``) aber
ausdruecklich zu (``settings/prod.py``): zeigt in einem solchen Deployment ein
weiterer erlaubter Host auf die Anwendung, ginge der Token-Link mit genau
diesem Host raus.

``EXTERNAL_ORIGIN`` ist deshalb die einzige Quelle fuer Schema und Host solcher
Links. In Produktion ist das Setting Pflicht (ableitbar aus einem eindeutigen
``ALLOWED_HOSTS``-Eintrag), in Entwicklung und E2E auf den jeweiligen lokalen
Server vorbelegt. Der Request wird hier bewusst nirgends gelesen.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def get_external_origin() -> str:
    """Die konfigurierte Basis-Origin (``schema://host[:port]``).

    Fail-loud statt stiller Notloesung: ohne Origin gibt es keinen belastbaren
    Link, und ein auf ``localhost`` zeigender Token-Link waere fuer die
    Empfaengerin genauso wertlos wie ein relativer Pfad in einer E-Mail.
    """
    origin: str = getattr(settings, "EXTERNAL_ORIGIN", "") or ""
    if not origin:
        raise ImproperlyConfigured(
            "EXTERNAL_ORIGIN ist nicht gesetzt — ohne feste Basis-Origin lassen sich "
            "keine Einladungs-, Passwort-Reset- oder Entsperr-Links erzeugen."
        )
    return origin


def build_external_url(path: str) -> str:
    """Absolute URL aus der konfigurierten Origin und einem absoluten Pfad."""
    return f"{get_external_origin()}{path}"


def external_link_context() -> dict[str, str]:
    """``protocol``/``domain`` fuer die Mail-Templates.

    Djangos ``PasswordResetForm.save()`` baut den Reset-Link aus genau diesen
    beiden Kontext-Variablen (abgeleitet aus ``request.is_secure()`` und der
    aktuellen Site); ueber ``extra_email_context`` werden sie zuletzt gemergt
    und damit ueberschrieben. Die Einladungsmail nutzt dieselben zwei Namen.
    """
    parts = urlsplit(get_external_origin())
    return {"protocol": parts.scheme, "domain": parts.netloc}
