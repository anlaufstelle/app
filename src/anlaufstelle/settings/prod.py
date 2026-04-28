"""
Production settings for Anlaufstelle.
"""

import os
import sys

import sentry_sdk
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401, F403
from .base import LOGGING  # noqa: F401

# --- Logging-Override für Produktion ---
# base.py setzt den ``core``-Logger auf DEBUG (dev-freundlich). In Produktion
# konservativer: INFO, damit keine Verbose-Detail-Statements mit potenzieller
# PII ins stdout/Sentry landen. PII-Scrubber im ``JsonFormatter`` greift
# zusätzlich als Defense-in-Depth (core/logging.py).
LOGGING["loggers"]["core"]["level"] = "INFO"

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
# SESSION-Cookie "Lax" (= Django-Default) explizit: Top-Level-Navigationen
# (Bookmarks, E-Mail-Links) sollen die Session mitschicken — sonst
# UX-Regression durch permanente Re-Logins. "Strict" wäre sicherer, aber
# für ein Alltags-Fachsystem zu restriktiv.
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
# CSRF-Cookie "Strict": Token wird nur bei same-origin-Form-Submits
# mitgesendet. Refs #598 S-5, Phase-2-Entscheidung 2026-04-21.
CSRF_COOKIE_SAMESITE = "Strict"
# CSRF-Cookie HTTPOnly: Verhindert, dass JavaScript den Token aus dem Cookie
# lesen kann — XSS-Mitigation. Der CSRF-Token wird für HTMX (via
# body[hx-headers]), PWA-Offline-Module (via window.getCsrfToken() /
# <meta name="csrf-token">) und Forms (via {% csrf_token %}) aus dem
# gerenderten HTML gelesen, nicht aus dem Cookie. Refs #602.
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
# Explizit Referrer-Policy setzen — Django-Default wäre "same-origin", aber
# Caddy liefert "strict-origin-when-cross-origin". Wir setzen hier den
# strengeren Wert auch auf Django-Ebene, damit der Header auch bei direktem
# Zugriff ohne Caddy (z.B. lokale Prod-Simulation) gesetzt ist. Refs #635.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
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
