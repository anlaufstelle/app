"""
Django base settings for Anlaufstelle.

Gemeinsame Konfiguration für dev und prod.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR.parent / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")

DEBUG = False

ALLOWED_HOSTS = []

# --- Apps ---

INSTALLED_APPS = [
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "django_htmx",
    "django_otp",
    "django_otp.plugins.otp_totp",
    # Project
    "core",
]

# --- Middleware ---

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "core.middleware.htmx_session.HtmxSessionMiddleware",
    "core.middleware.facility_scope.FacilityScopeMiddleware",
    "core.middleware.user_language.UserLanguageMiddleware",
    "core.middleware.password_change.ForcePasswordChangeMiddleware",
    "core.middleware.mfa.MFAEnforcementMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

# --- URLs ---

ROOT_URLCONF = "anlaufstelle.urls"

# --- Templates ---

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.workitem_counts",
            ],
        },
    },
]

# --- WSGI ---

WSGI_APPLICATION = "anlaufstelle.wsgi.application"

# --- Database ---

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "anlaufstelle"),
        "USER": os.environ.get("POSTGRES_USER", "anlaufstelle"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "anlaufstelle"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# --- Auth ---

AUTH_USER_MODEL = "core.User"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# --- Two-Factor Authentication (TOTP) ---

OTP_TOTP_ISSUER = "Anlaufstelle"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Session ---

SESSION_COOKIE_AGE = 1800  # 30 Minuten
SESSION_SAVE_EVERY_REQUEST = True

# --- Trusted Proxy Hops (Client-IP-Ermittlung) ---
# Anzahl der vertrauenswürdigen Proxy-Hops vor der Django-App. Bestimmt, welcher
# Eintrag aus X-Forwarded-For als echte Client-IP interpretiert wird.
#
#   0 → REMOTE_ADDR direkt verwenden (kein Reverse-Proxy, spoofing-sicher)
#   1 → Caddy-only (Default): split(",")[-1]
#   2 → CDN + Caddy (z.B. Cloudflare → Caddy): split(",")[-2]
#   N → Allgemein: split(",")[-N]
#
# X-Forwarded-For-Konvention: "client, proxy1, proxy2" — jeder Proxy hängt die IP
# an, von der er den Request bekommen hat. Bei N vertrauenswürdigen Proxies ist
# der N-te Eintrag von rechts die echte Client-IP.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))

# --- i18n ---

LANGUAGE_CODE = "de"

LANGUAGES = [
    ("de", "Deutsch"),
    ("en", "English"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_L10N = True
USE_TZ = True

# --- Static files ---

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# --- Media files (encrypted attachments — NOT served via Django URL) ---

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

# --- Encryption ---
# MultiFernet key rotation: ENCRYPTION_KEYS is a comma-separated list of Fernet keys.
# The first key is used for encrypting, all keys are tried for decrypting.
# Fallback: if only ENCRYPTION_KEY (singular) is set, it is used as the single key.

ENCRYPTION_KEYS = os.environ.get("ENCRYPTION_KEYS", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

# --- Virenscan (ClamAV, Issue #524) ---
# Prüft jede hochgeladene Datei VOR der Verschlüsselung gegen einen ClamAV-Daemon.
# In Dev/Test per Default deaktiviert; prod.py aktiviert es.
# Bei aktivem Scan und unerreichbarem Daemon wird der Upload abgewiesen (fail-closed).
CLAMAV_ENABLED = os.environ.get("CLAMAV_ENABLED", "false").lower() in ("true", "1", "yes")
CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "clamav")
CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", "3310"))
CLAMAV_TIMEOUT = int(os.environ.get("CLAMAV_TIMEOUT", "30"))

# --- Default PK ---

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Logging ---

LOG_FORMAT = os.environ.get("LOG_FORMAT", "text")  # "text" or "json"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
        "json": {
            "()": "core.logging.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_FORMAT == "json" else "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "core": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

# --- Content Security Policy (django-csp, Defense-in-Depth neben Caddy) ---

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'", "'unsafe-eval'"],  # Alpine.js benötigt unsafe-eval (new Function())
        "style-src": ["'self'", "'unsafe-inline'"],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
    }
}

# --- Django Unfold ---

UNFOLD = {
    "SITE_TITLE": "Anlaufstelle",
    "SITE_HEADER": "Anlaufstelle",
    "SITE_URL": "/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        "primary": {
            "50": "oklch(96.2% .018 272.314)",
            "100": "oklch(93% .042 272.804)",
            "200": "oklch(87% .082 274.713)",
            "300": "oklch(78.5% .145 274.713)",
            "400": "oklch(67.3% .222 274.713)",
            "500": "oklch(58.5% .269 274.713)",
            "600": "oklch(51.1% .262 276.966)",
            "700": "oklch(45.7% .24 277.023)",
            "800": "oklch(39.8% .195 277.366)",
            "900": "oklch(35.9% .152 278.697)",
            "950": "oklch(26.2% .112 280.572)",
        },
    },
}
