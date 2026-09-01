"""Django-System-Checks für sicherheitsrelevante Settings.

Refs #1375 (L8), #1963: ``TRUSTED_PROXY_HOPS`` bestimmt, welcher Eintrag aus
``X-Forwarded-For`` als echte Client-IP interpretiert wird
(:func:`core.signals.audit.get_client_ip`). Ob mehr als ein Hop überhaupt
sinnvoll ist, entscheidet der Edge-Proxy: Caddy übernimmt ein eingehendes
``X-Forwarded-For`` nur von einem Peer aus ``trusted_proxies`` und ersetzt es
sonst durch dessen IP. Ohne deklariertes Proxy-Vertrauen kommt bei der App also
immer genau ein Eintrag an — ``TRUSTED_PROXY_HOPS > 1`` greift dann daneben und
fällt auf ``REMOTE_ADDR`` (die Proxy-IP) zurück, womit Ratelimit-Eimer
(key="ip"), Lockout-Achse N9, Audit-Client-IP und Maintenance-Allowlist auf
einen einzigen Wert kollabieren. Umgekehrt ist ein Hop zu viel bei tatsächlich
vertrauendem Edge genau der Spoofing-Fall aus #1375.

Django kann die reale Topologie nicht messen. Die Checks koppeln die Hop-Zahl
deshalb an ``TRUSTED_PROXY_CIDRS`` — dieselbe Variable, die die Caddy-Direktive
füllt —, warnen zusätzlich heuristisch bei ungewöhnlich hohen Werten und lehnen
Werte ab, an denen Caddy scheitern würde.

**Zu den IDs:** ``core.W001`` behält die mit #1375 veröffentlichte Bedeutung
(Hop-Zahl ungewöhnlich hoch) — Betreiber haben sie in ihren Runbook-Tabellen
stehen, eine Umwidmung würde dort still etwas anderes anzeigen. Die mit #1963
hinzugekommene Kopplung ``hops > 1`` ohne Proxy-Vertrauen bekommt darum die
neue ID ``core.W003``, der unbrauchbare Bereich die neue ID ``core.E003``.
``core.W004`` bezeichnet seit #2002 einen Redis-Cache ohne Passwort.
``core.E004`` bezeichnet seit #2002 eine syntaktisch ungültige Redis-Cache-URL.
"""

from __future__ import annotations

import re
import sys
from ipaddress import ip_network
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Warning, register

# Realistische Topologien: 0 (kein Proxy), 1 (Caddy), 2 (CDN → Caddy). Ab 3
# vorgeschalteten „vertrauenswürdigen" Hops ist eine Fehlkonfiguration deutlich
# wahrscheinlicher als ein echtes 3-Proxy-Setup — dann lieber einmal warnen.
_TRUSTED_PROXY_HOPS_WARN_THRESHOLD = 3

# Caddys ``trusted_proxies static`` kennt neben CIDR-Bereichen und nackten
# Adressen Schlüsselwörter, die die Direktive selbst expandiert. Sie sind keine
# IP-Notation und dürfen die Syntaxprüfung nicht auslösen.
_TRUSTED_PROXY_CIDR_KEYWORDS = frozenset({"private_ranges"})

# Caddy parst jeden Eintrag über Gos ``netip.ParsePrefix``/``ParseAddr``. Das
# ist an zwei Stellen strenger als Pythons ``ipaddress``: eine Netzmaske
# (``192.168.0.0/255.255.255.0``) und eine führende Null in der Präfixlänge
# (``2001:db8::/032``) lässt Python durchgehen, Go nicht. Beides am gepinnten
# Image nachgestellt (caddy 2.11.4, ``caddy validate``: "bad bits after slash").
# Ein Check, der mehr durchlässt als Caddy, gibt genau dann Entwarnung, wenn
# der Reverse-Proxy gleich sterben wird — also prüft er die Präfixlänge selbst.
_DEZIMALE_PRAEFIXLAENGE = re.compile(r"^(0|[1-9][0-9]*)$")

_CIDR_HINWEIS = (
    "Erlaubt ist genau das, was Caddys 'trusted_proxies static' parst: CIDR-Bereiche "
    "(203.0.113.0/24, 2001:db8::/32) mit dezimaler Präfixlänge ohne führende Null, nackte "
    "Adressen (dann /32 bzw. /128) und das Schlüsselwort 'private_ranges'. Eine Netzmaske "
    "(192.168.0.0/255.255.255.0) ist KEINE gültige Schreibweise. Mehrere Bereiche durch "
    "Leerzeichen trennen. Ein unbrauchbarer Wert bringt sonst den Reverse-Proxy nicht mehr hoch."
)

_CIDR_HINWEIS_KOMMA = (
    "Das Komma ist hier kein Trennzeichen: Der Wert füllt per {$TRUSTED_PROXY_CIDRS} die "
    "Caddy-Direktive 'trusted_proxies static', und Caddy zerlegt Direktiven-Argumente "
    "ausschließlich an Leerzeichen — der komplette Eintrag käme dort als EIN Bereich an, und "
    "der Reverse-Proxy verweigerte den Start. Mehrere Bereiche also durch Leerzeichen trennen: "
    "TRUSTED_PROXY_CIDRS='203.0.113.0/24 198.51.100.0/24'."
)


def _ist_caddy_bereich(eintrag: str) -> bool:
    """Akzeptiert genau das, was Caddys ``trusted_proxies static`` akzeptiert."""
    if eintrag in _TRUSTED_PROXY_CIDR_KEYWORDS:
        return True
    _, trenner, praefix = eintrag.rpartition("/")
    if trenner and not _DEZIMALE_PRAEFIXLAENGE.match(praefix):
        return False
    try:
        ip_network(eintrag, strict=False)
    except ValueError:
        return False
    return True


@register()
def check_trusted_proxy_hops(app_configs, **kwargs):
    """Validiert ``settings.TRUSTED_PROXY_HOPS`` (L8, Refs #1375, #1963)."""
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

    if hops > 1 and not getattr(settings, "TRUSTED_PROXY_CIDRS", []):
        messages.append(
            Warning(
                f"TRUSTED_PROXY_HOPS={hops} ohne konfiguriertes Proxy-Vertrauen.",
                hint=(
                    "Mehr als ein Hop ist nur belastbar, wenn der Edge-Proxy weiß, welchen "
                    "vorgelagerten Proxys er glauben darf. Caddy übernimmt ein eingehendes "
                    "X-Forwarded-For ausschließlich von einem Peer aus 'trusted_proxies' und "
                    "ersetzt es sonst durch dessen IP — bei leerem TRUSTED_PROXY_CIDRS kommt "
                    "also immer genau ein Eintrag an, und get_client_ip fällt auf REMOTE_ADDR "
                    "(die Proxy-IP) zurück: ein gemeinsamer Ratelimit-Eimer, eine Lockout-Achse "
                    "und eine Audit-IP für alle. Trage die Bereiche des vorgelagerten Proxys in "
                    "TRUSTED_PROXY_CIDRS ein (sie füllen zugleich die Caddy-Direktive) oder "
                    "setze TRUSTED_PROXY_HOPS auf 1."
                ),
                id="core.W003",
            )
        )

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
def check_trusted_proxy_cidrs(app_configs, **kwargs):
    """Validiert ``settings.TRUSTED_PROXY_CIDRS`` (Refs #1963).

    Der Wert wird per Env-Substitution in die Caddy-Direktive
    ``trusted_proxies static`` eingesetzt. Ein unbrauchbarer Bereich lässt Caddy
    den Start verweigern — und zwar erst, nachdem der ``web``-Container sich als
    healthy gemeldet hat (``depends_on``). Dieser Check zieht den Fehler in den
    Start der Anwendung vor, wo er im Deploy-Log an der erwarteten Stelle steht.
    """
    messages = []
    for eintrag in getattr(settings, "TRUSTED_PROXY_CIDRS", []):
        if _ist_caddy_bereich(eintrag):
            continue
        messages.append(
            Error(
                f"TRUSTED_PROXY_CIDRS enthält keinen gültigen IP-Bereich: {eintrag!r}.",
                hint=_CIDR_HINWEIS_KOMMA if "," in eintrag else _CIDR_HINWEIS,
                id="core.E003",
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


@register()
def check_file_upload_temp_dir_configured(app_configs, **kwargs):
    """Warnt, wenn ``FILE_UPLOAD_TEMP_DIR`` in Produktion nicht gesetzt ist (Refs #1661).

    Uploads oberhalb von ``FILE_UPLOAD_MAX_MEMORY_SIZE`` puffert Django als
    **Klartext**-Tempdatei auf Platte, bevor sie gescannt und verschlüsselt in
    den File-Vault wandern (``core/services/file_vault/storage.py``). Ohne
    explizites ``FILE_UPLOAD_TEMP_DIR`` fällt Django auf
    ``tempfile.gettempdir()`` zurück — in den mitgelieferten Compose-Dateien
    (docker-compose.prod/staging/demo/dev.yml) ist ``/tmp`` dort bereits ein
    tmpfs-Mount (RAM, Schreib-Scratch für den ``read_only``-Root-FS, #1374),
    der implizite Fallback landet also ohnehin im RAM. Auf einem
    Bare-Metal-/individuell orchestrierten Deploy ohne tmpfs würde derselbe
    Fallback den Klartext-Puffer dagegen still auf persistentem,
    unverschlüsseltem Storage ablegen.

    Nur eine **Warnung** (kein Error): anders als bei ``core.E002``/MEDIA_ROOT
    bricht ein fehlendes ``FILE_UPLOAD_TEMP_DIR`` den Betrieb nicht — es ist
    ein Härtungshinweis, kein Funktionsbruch. Fallback-Verhalten für
    Umgebungen ohne tmpfs: docs/ops-runbook.md § 5.7a.

    Nur unter ``DEBUG=False`` aktiv — dev/tests nutzen bewusst keinen
    expliziten Upload-Tmp-Pfad. Während ``collectstatic`` wird nicht gewarnt
    (analog zu den prod.py-Guards).
    """
    messages = []
    if settings.DEBUG or "collectstatic" in sys.argv:
        return messages

    if not getattr(settings, "FILE_UPLOAD_TEMP_DIR", None):
        messages.append(
            Warning(
                "FILE_UPLOAD_TEMP_DIR ist nicht gesetzt.",
                hint=(
                    "Uploads oberhalb von FILE_UPLOAD_MAX_MEMORY_SIZE puffert Django als "
                    "Klartext-Tempdatei auf Platte, bevor sie verschlüsselt in den File-Vault "
                    "wandern. Setze FILE_UPLOAD_TEMP_DIR auf ein tmpfs (RAM) oder "
                    "verschlüsseltes Volume, z. B. FILE_UPLOAD_TEMP_DIR=/tmp bei einem "
                    "gemounteten tmpfs (wie in docker-compose.prod.yml). Fallback-Hinweis für "
                    "Umgebungen ohne tmpfs: docs/ops-runbook.md § 5.7a."
                ),
                id="core.W002",
            )
        )

    return messages


def _redis_location_hat_passwort(location: str) -> bool:
    parsed = urlsplit(location)
    # unix://-Sockets und andere Backends kennen diese Redis-URL-Zusage nicht.
    if parsed.scheme.lower() not in {"redis", "rediss"}:
        return True
    return bool(parsed.password)


@register()
def check_redis_cache_requires_password(app_configs, **kwargs):
    """Warnt bei einem Redis-Cache ohne Passwort (Refs #2002)."""
    messages = []
    if settings.DEBUG or "collectstatic" in sys.argv:
        return messages

    default = settings.CACHES.get("default") or {}
    if "redis" not in default.get("BACKEND", "").lower():
        return messages

    locations = default.get("LOCATION") or ""
    if isinstance(locations, str):
        locations = [locations]
    try:
        ohne_passwort = [location for location in locations if not _redis_location_hat_passwort(location)]
    except ValueError:
        messages.append(
            Error(
                "Eine Redis-Cache-URL ist syntaktisch ungültig.",
                hint=(
                    "Prüfe REDIS_URL in der .env, insbesondere Schema und IPv6-Klammern "
                    "(z. B. redis://:passwort@[::1]:6379/1). Die konfigurierte URL wird "
                    "hier nicht ausgegeben, weil sie ein Passwort enthalten kann."
                ),
                id="core.E004",
            )
        )
        return messages
    if ohne_passwort:
        messages.append(
            Warning(
                "Der Redis-Cache ist ohne Passwort konfiguriert.",
                hint=(
                    "Setze REDIS_PASSWORD in der .env; Details: docs/ops-runbook.md § 5.4a. "
                    "Der Cache hält die Rate-Limit-Zähler einschließlich Client-IPs — ohne "
                    "Passwort bleibt allein die Docker-Netzgrenze als Zugriffsschutz."
                ),
                id="core.W004",
            )
        )
    return messages
