"""
Development settings for Anlaufstelle.
"""

from .base import *  # noqa: F401, F403

# E-Mails in der Konsole ausgeben (kein SMTP nötig)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEBUG = True

ALLOWED_HOSTS = ["*"]

# WhiteNoise: Static Files ohne collectstatic bei jeder Aenderung
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True

# Encryption ist optional in Dev
if not ENCRYPTION_KEY:  # noqa: F405
    import logging

    logging.getLogger("core").warning("ENCRYPTION_KEY nicht gesetzt — Felder werden unverschlüsselt gespeichert.")
