"""
Live-Dev settings for dev.anlaufstelle.app (Refs #671).

Erbt von ``prod.py`` und uebernimmt alle Security-Defaults
(HSTS, Secure-Cookies, ENCRYPTION_KEY-Pflicht, SUDO_MODE_ENABLED-Guard,
ALLOWED_HOSTS-Pflicht, fail-closed checks).

Einziger Unterschied zu prod: ``EMAIL_BACKEND`` faellt auf den
Console-Backend zurueck. Auf dev.anlaufstelle.app brauchen wir keinen
echten SMTP-Server — ausgehende Mails landen im stdout des web-Containers
und sind ueber ``docker compose logs web`` einsehbar.

Nicht zu verwechseln mit ``settings/dev.py`` (lokale Entwicklung,
DEBUG=True, console-mail, dev-DB).

Verwendung:
    DJANGO_SETTINGS_MODULE=anlaufstelle.settings.devlive
"""

import copy

from .prod import *  # noqa: F401, F403

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# --- Umami Web-Analytics (Refs #1602) ---
# dev.anlaufstelle.app trackt die Post-Login-Seiten (base.html bindet
# partials/_umami.html ein). Die Login-Seite (login.html erbt NICHT von
# base.html) bleibt bewusst tracking-frei.
UMAMI_WEBSITE_ID = "00000000-0000-4000-8000-000000000001"
UMAMI_SCRIPT_SRC = "https://umami.example.org/main.js"

# CSP um den Umami-Host erweitern: script-src (Tracker-Skript) UND connect-src
# (Umami POSTet die Zaehl-/Event-Daten an denselben Host — ohne connect-src
# blockt die CSP den Beacon und es wird NIE gezaehlt; Lehre aus webseite#9).
#
# DEEP-COPY statt In-Place-Mutation: ``CONTENT_SECURITY_POLICY`` ist ein
# Modul-Level-Dict aus base.py, das via ``from .prod import *`` als DASSELBE
# Objekt in diesem Namespace liegt. Eine In-Place-Aenderung wuerde das geteilte
# base-Dict mutieren (Settings-Modul-Aliasing) und die strikte globale CSP
# aller anderen Settings-Module (inkl. der Architektur-Guard-Pruefung gegen
# base) aufweichen. Die Kopie bleibt lokal zu devlive.
CONTENT_SECURITY_POLICY = copy.deepcopy(CONTENT_SECURITY_POLICY)  # noqa: F405
_UMAMI_HOST = "https://umami.example.org"
CONTENT_SECURITY_POLICY["DIRECTIVES"]["script-src"] = [
    *CONTENT_SECURITY_POLICY["DIRECTIVES"]["script-src"],
    _UMAMI_HOST,
]
CONTENT_SECURITY_POLICY["DIRECTIVES"]["connect-src"] = [
    *CONTENT_SECURITY_POLICY["DIRECTIVES"]["connect-src"],
    _UMAMI_HOST,
]

# /health/ vom HTTPS-Redirect ausnehmen, damit der Container-internal
# Healthcheck (`urlopen('http://localhost:8000/health/')`) nicht in einen
# 302→HTTPS-Loop laeuft. Caddy macht TLS-Termination, aber Django sieht
# beim Healthcheck-Request keinen X-Forwarded-Proto → wuerde sonst
# SSL_REDIRECT triggern und der Healthcheck-Client kann lokal kein TLS.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]

# dev.anlaufstelle.app laeuft mit Demo-Daten ('make dev-seed') — Seed trotz
# prod-Vererbung explizit freigeschaltet. Refs #1040 (S1).
SEED_ALLOWED = True
