"""
Development settings for Anlaufstelle.
"""

from .base import *  # noqa: F401, F403

# Dev-Default für SECRET_KEY — NUR in dev/test. prod.py erzwingt DJANGO_SECRET_KEY.
if not SECRET_KEY:  # noqa: F405
    SECRET_KEY = "django-insecure-dev-only-change-in-production"  # noqa: S105

# E-Mails in der Konsole ausgeben (kein SMTP nötig)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEBUG = True

ALLOWED_HOSTS = ["*"]

# DEV bewusst MFA-frei (Refs #1019): das Rollen-Default-Enforcement aus base.py
# wird hier abgeschaltet, damit die Seed-Logins (admin/thomas/superadmin) ohne
# TOTP arbeiten. Kaskadiert ueber Vererbung nach test.py UND e2e.py.
# ``mfa_required`` (User) und ``mfa_enforced_facility_wide`` (Facility) bleiben
# unberuehrt — MFA laesst sich in DEV also weiterhin gezielt testen.
MFA_ENFORCE_PRIVILEGED_ROLES = False

# WhiteNoise: Static Files ohne collectstatic bei jeder Aenderung
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True

# Encryption ist optional in Dev
if not ENCRYPTION_KEY:  # noqa: F405
    import logging

    logging.getLogger("core").warning("ENCRYPTION_KEY nicht gesetzt — Felder werden unverschlüsselt gespeichert.")
