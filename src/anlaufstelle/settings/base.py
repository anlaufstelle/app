"""
Django base settings for Anlaufstelle.

Gemeinsame Konfiguration für dev und prod.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR.parent / ".env")

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-change-in-production",
)

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
    # Project
    "core",
]

# --- Middleware ---

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.htmx_session.HtmxSessionMiddleware",
    "core.middleware.facility_scope.FacilityScopeMiddleware",
    "core.middleware.password_change.ForcePasswordChangeMiddleware",
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

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Session ---

SESSION_COOKIE_AGE = 1800  # 30 Minuten
SESSION_SAVE_EVERY_REQUEST = True

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

# --- Encryption ---
# MultiFernet key rotation: ENCRYPTION_KEYS is a comma-separated list of Fernet keys.
# The first key is used for encrypting, all keys are tried for decrypting.
# Fallback: if only ENCRYPTION_KEY (singular) is set, it is used as the single key.

ENCRYPTION_KEYS = os.environ.get("ENCRYPTION_KEYS", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

# --- Default PK ---

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Logging ---

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
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
