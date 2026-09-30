"""Env-Var-Parsing-Helfer fuer die Settings-Module (Refs #1827).

``base.py``/``prod.py`` wiederholten drei Muster knapp 40x von Hand: 29x
``int(os.environ.get(name, "N"))``, 5x den Bool-Truthy-Check
(``.lower() in ("true", "1", "yes")``), 4x das Komma-Listen-Splitting
(``[x.strip() for x in os.environ.get(...).split(",") if x.strip()]``).

Bewusst ein eigenes, reines stdlib-Modul (wie ``_mfa_roles.py``, Refs #1639):
Settings-Module laufen vor dem App-Registry-Load und duerfen kein
``core``-Modell importieren; ein Django-freies Modul bleibt zirkularitaets-
frei und laesst sich isoliert (ohne Settings-Reimport) testen.
"""

import os
import re
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

# Dieselbe Werteliste wie ueberall im Projekt bereits verwendet — case-
# insensitiv, damit ".env"-Werte wie "True"/"TRUE"/"1"/"yes" gleichermassen
# als wahr gelten.
_TRUE_VALUES = ("true", "1", "yes")
_FALSE_VALUES = ("false", "0", "no")

# Trennzeichen der Caddyfile-Direktiven-Argumente (siehe :func:`env_tokens`).
# Zeilenumbrueche gehoeren bewusst NICHT dazu: sie beenden in einer Caddyfile
# die Direktive und lassen ``caddy adapt`` scheitern — ein Wert mit
# Zeilenumbruch bleibt darum ein (unbrauchbares) Token und faellt auf.
_CADDY_ARGUMENT_TRENNER = re.compile(r"[ \t]+")


def env_int(name: str, default: int) -> int:
    """``int(os.environ.get(name, default))`` — Default bleibt ein Python-``int``,
    keine erneute String-Umwandlung an der Aufrufstelle noetig."""
    return int(os.environ.get(name, str(default)))


def env_bool(name: str, *, default: bool) -> bool:
    """Truthy-Wert einer Env-Var gegen die projektweite Werteliste
    (``"true"``/``"1"``/``"yes"``, case-insensitiv)."""
    raw = os.environ.get(name, "true" if default else "false")
    return raw.lower() in _TRUE_VALUES


def env_bool_strict(name: str, *, default: bool) -> bool:
    """Parse a boolean environment variable and reject unknown values.

    A missing, empty, or whitespace-only value uses ``default``. Any other
    value must be one of the explicitly accepted true or false spellings.
    """
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    allowed = "/".join((*_TRUE_VALUES, *_FALSE_VALUES))
    raise ImproperlyConfigured(f"{name} must be one of: {allowed}.")


def env_csv(name: str, default: str = "") -> list[str]:
    """Kommaliste -> getrimmte, nicht-leere Eintraege (Reihenfolge erhalten,
    Duplikate NICHT entfernt — wie das bisherige Hand-Parsing)."""
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def env_tokens(name: str, default: str = "") -> list[str]:
    """Leerzeichen-/Tab-getrennte Liste -> getrimmte, nicht-leere Eintraege.

    Gebraucht fuer ``TRUSTED_PROXY_CIDRS`` (Refs #1963): derselbe ``.env``-Wert
    fuellt per ``{$TRUSTED_PROXY_CIDRS}`` die Caddy-Direktive
    ``trusted_proxies static`` UND dieses Setting. Zerlegt wird deshalb exakt
    so, wie **Caddy** zerlegt — der Caddyfile-Parser trennt Direktiven-Argumente
    an Leerzeichen und Tabs, an nichts sonst.

    Ausdruecklich NICHT am Komma (dafuer gibt es :func:`env_csv`): sonst saehe
    die Anwendung eine Liste, die es im Reverse-Proxy nie gab. ``a/24,b/24``
    waere hier zwei gueltige Bereiche, fuer Caddy dagegen ein einziger,
    unbrauchbarer — die System-Checks gaeben Entwarnung, waehrend Caddy den
    Start verweigert (am gepinnten Image nachgestellt, caddy 2.11.4: "parsing
    CIDR expression"). So bleibt das Komma im Token stehen und faellt
    ``core.checks.check_trusted_proxy_cidrs`` als ``core.E003`` auf, bevor
    irgendetwas startet.
    """
    return [token for token in _CADDY_ARGUMENT_TRENNER.split(os.environ.get(name, default).strip()) if token]


def env_origin(name: str, default: str = "") -> str:
    """Absolute Basis-Origin (``schema://host[:port]``) aus einer Env-Var.

    Wird fuer ``EXTERNAL_ORIGIN`` gebraucht (Refs #1967): die feste Basis der
    sicherheitsrelevanten Links (Einladung, Passwort-Reset, Konto-Entsperrung).
    Ein Pfadanteil — auch nur ein abschliessender Schraegstrich — wuerde beim
    Zusammensetzen mit dem ohnehin fuehrenden ``/`` des Link-Pfades eine
    doppelte oder verschobene URL ergeben, deshalb ist er nicht erlaubt.

    Leerer Wert bleibt leer (die Pflicht setzt ``settings/prod.py`` durch).
    Ein gesetzter, aber unbrauchbarer Wert loest ``ValueError`` aus, damit der
    Fehler beim Start auffaellt und nicht erst in einer versendeten Mail.

    Geprueft wird genau der String, der auch zurueckkommt: ``urlsplit`` entfernt
    ASCII-Zeilenumbrueche und Tabs VOR dem Parsen (WHATWG-Regel), ein Wert wie
    ``"https://host\\n.evil.example"`` haette die Pruefung sonst als sauberer
    Host bestanden und waere anschliessend roh in Link und Mail-Kontext
    gewandert. Innenliegende Steuer-/Leerzeichen sind deshalb hart verboten.
    """
    raw = os.environ.get(name, default).strip()
    if not raw:
        return ""
    if any(zeichen.isspace() or ord(zeichen) < 0x20 or ord(zeichen) == 0x7F for zeichen in raw):
        raise ValueError(
            f"{name} darf keine Leer- oder Steuerzeichen enthalten — sie werden beim "
            f"Parsen still entfernt und damit anders geprueft als verwendet (Wert: {raw!r})."
        )
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"{name} muss mit 'http://' oder 'https://' beginnen (Wert: {raw!r}).")
    if not parts.netloc:
        raise ValueError(f"{name} muss einen Hostnamen enthalten (Wert: {raw!r}).")
    if parts.path or parts.query or parts.fragment:
        raise ValueError(
            f"{name} darf nur Schema und Host tragen — kein Pfad, kein abschliessender "
            f"Schraegstrich, keine Query, kein Fragment (Wert: {raw!r})."
        )
    return raw
