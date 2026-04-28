"""
Production settings for Anlaufstelle.
"""

import os
import sys

import sentry_sdk
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401, F403

# --- Sentry (optional, via SENTRY_DSN env var) ---

_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,
    )

# --- Media (encrypted attachments) ---

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))  # noqa: F405

DEBUG = False

# Fail-closed: Prod darf nicht mit unsicheren Defaults oder leerer Konfiguration starten.
if "collectstatic" not in sys.argv:
    if not SECRET_KEY:  # noqa: F405
        raise ImproperlyConfigured("DJANGO_SECRET_KEY muss in Produktion gesetzt sein.")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
if "collectstatic" not in sys.argv and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS muss in Produktion gesetzt sein (Kommaseparierte Liste).")

# --- Security ---

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# --- E-Mail (SMTP) ---
# Für Passwort-zurücksetzen und andere E-Mail-Funktionen.
# Konfiguration via Umgebungsvariablen:
#   EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
#   EMAIL_USE_TLS, DEFAULT_FROM_EMAIL
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@anlaufstelle.app")

# --- Database ---

CONN_MAX_AGE = 60

# --- Encryption Key (mandatory in production) ---
# Akzeptiert ENCRYPTION_KEYS (Plural, MultiFernet-Rotation) oder ENCRYPTION_KEY (Singular, Legacy).
# Mindestens eins muss gesetzt sein.
if "collectstatic" not in sys.argv and not (ENCRYPTION_KEY or ENCRYPTION_KEYS):  # noqa: F405
    raise ImproperlyConfigured(
        "ENCRYPTION_KEY oder ENCRYPTION_KEYS muss in Produktion gesetzt sein. "
        'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )

# --- Virenscan (Default: aktiv in Produktion) ---
# In Produktion ist der Virenscan standardmäßig aktiv und kann nur durch explizite
# Setzung von CLAMAV_ENABLED=false deaktiviert werden.
CLAMAV_ENABLED = os.environ.get("CLAMAV_ENABLED", "true").lower() in ("true", "1", "yes")
