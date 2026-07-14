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
