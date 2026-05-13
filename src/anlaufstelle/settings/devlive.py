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

from .prod import *  # noqa: F401, F403

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# /health/ vom HTTPS-Redirect ausnehmen, damit der Container-internal
# Healthcheck (`urlopen('http://localhost:8000/health/')`) nicht in einen
# 302→HTTPS-Loop laeuft. Caddy macht TLS-Termination, aber Django sieht
# beim Healthcheck-Request keinen X-Forwarded-Proto → wuerde sonst
# SSL_REDIRECT triggern und der Healthcheck-Client kann lokal kein TLS.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
