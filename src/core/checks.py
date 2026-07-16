"""Django-System-Checks für sicherheitsrelevante Settings.

Refs #1375 (L8): ``TRUSTED_PROXY_HOPS`` bestimmt, welcher Eintrag aus
``X-Forwarded-For`` als echte Client-IP interpretiert wird
(:func:`core.signals.audit.get_client_ip`). Ist der Wert **höher** als die
tatsächlich vorgeschalteten, vertrauenswürdigen Proxy-Hops, kann ein Client
den XFF-Header spoofen — er hängt selbst zusätzliche Fake-Hops an, die dann als
„vertrauenswürdig" gezählt werden. Das unterläuft alle IP-gebundenen
Sicherheitsmechanismen (Ratelimits key="ip", die Lockout-Achse N9, die
Audit-Client-IP). Django kann die reale Hop-Zahl nicht kennen; der Check warnt
daher heuristisch bei ungewöhnlich hohen Werten und lehnt offensichtlich
ungültige (negative) Werte ab.
"""

from __future__ import annotations

import sys
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Warning, register

# Realistische Topologien: 0 (kein Proxy), 1 (Caddy), 2 (CDN → Caddy). Ab 3
# vorgeschalteten „vertrauenswürdigen" Hops ist eine Fehlkonfiguration deutlich
# wahrscheinlicher als ein echtes 3-Proxy-Setup — dann lieber einmal warnen.
_TRUSTED_PROXY_HOPS_WARN_THRESHOLD = 3


@register()
def check_trusted_proxy_hops(app_configs, **kwargs):
    """Validiert ``settings.TRUSTED_PROXY_HOPS`` (L8, Refs #1375)."""
    messages = []
    hops = getattr(settings, "TRUSTED_PROXY_HOPS", 1)

    if not isinstance(hops, int) or hops < 0:
        messages.append(
            Error(
                f"TRUSTED_PROXY_HOPS muss ein nicht-negativer Integer sein (aktuell: {hops!r}).",
                hint=(
                    "0 = REMOTE_ADDR direkt (kein Reverse-Proxy). N = N-ter X-Forwarded-For-Eintrag "
                    "von rechts. Ein negativer/ungültiger Wert führt zu falscher Client-IP-Ermittlung."
                ),
                id="core.E001",
            )
        )
        return messages

    if hops >= _TRUSTED_PROXY_HOPS_WARN_THRESHOLD:
        messages.append(
            Warning(
                f"TRUSTED_PROXY_HOPS={hops} ist ungewöhnlich hoch.",
                hint=(
                    "Der Wert MUSS exakt der Anzahl vertrauenswürdiger Reverse-Proxys vor der App "
                    "entsprechen (typisch 1 = Caddy, 2 = CDN → Caddy). Ist er höher als die reale "
                    "Hop-Zahl, kann ein Client X-Forwarded-For spoofen (eigene Fake-Hops anhängen) "
                    "und so IP-Ratelimits, Login-Lockout (N9) und die Audit-Client-IP unterlaufen. "
                    "Stelle sicher, dass jeder gezählte Hop ein bekannter, vertrauenswürdiger Proxy ist."
                ),
                id="core.W001",
            )
        )

    return messages


@register()
def check_media_root_persistent(app_configs, **kwargs):
    """Validiert, dass ``MEDIA_ROOT`` in Produktion nicht unter ``BASE_DIR`` liegt (Refs #1453).

    Mit #1374 laufen die ``web``-Container ``read_only``. Ein prod-Deploy ohne
    gesetztes ``MEDIA_ROOT`` fällt in ``prod.py`` still auf ``BASE_DIR/media``
    (= ``/app/media``) zurück — das liegt auf dem schreibgeschützten Root-FS und
    ist zudem über ``docker compose pull`` nie persistent (vgl. #720). Der
    Container startet trotzdem healthy; erst der erste Datei-Upload bricht mit
    ``OSError: Read-only file system`` (HTTP 500). Dieser Deploy-Check fängt die
    Fehlkonfiguration laut beim Start statt leise beim ersten Upload.

    Nur unter ``DEBUG=False`` aktiv — dev/tests nutzen bewusst ``BASE_DIR/media``.
    Während ``collectstatic`` wird nicht abgebrochen (analog zu den prod.py-Guards).
    """
    messages = []
    if settings.DEBUG or "collectstatic" in sys.argv:
        return messages

    base_dir = Path(settings.BASE_DIR)
    media_root = Path(settings.MEDIA_ROOT)
    # Relativpfade löst Django relativ zum CWD (= BASE_DIR im Container) auf —
    # sie liegen damit ebenfalls unter BASE_DIR.
    if not media_root.is_absolute():
        media_root = base_dir / media_root

    try:
        under_base = media_root.resolve().is_relative_to(base_dir.resolve())
    except (ValueError, OSError):
        # is_relative_to wirft auf inkompatiblen Pfaden (z. B. verschiedene
        # Windows-Laufwerke) ValueError → dann liegt MEDIA_ROOT nicht unter BASE_DIR.
        under_base = False

    if under_base:
        messages.append(
            Error(
                f"MEDIA_ROOT ({settings.MEDIA_ROOT}) liegt unter BASE_DIR ({base_dir}) — "
                "in Produktion nicht persistent und unter read_only nicht beschreibbar.",
                hint=(
                    "Setze MEDIA_ROOT auf ein persistentes, beschreibbares Volume außerhalb "
                    "des Anwendungs-Verzeichnisses (z. B. MEDIA_ROOT=/data/media, wie in "
                    ".env.example). Ein Pfad unter BASE_DIR (/app) liegt auf dem read_only-"
                    "Root-FS der web-Container (#1374) und überlebt kein 'docker compose pull' "
                    "(#720) — Datei-Uploads scheitern sonst erst zur Laufzeit mit HTTP 500."
                ),
                id="core.E002",
            )
        )

    return messages
