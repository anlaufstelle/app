"""Test-spezifische Settings — optimiert für schnelle Test-Läufe."""

from .dev import *  # noqa: F401, F403

# Schneller Passwort-Hasher für Tests (PBKDF2 mit 870k Iterationen → MD5).
# Django's eigene Test-Suite nutzt die gleiche Strategie.
# PBKDF2 bleibt als Fallback, damit bestehende Hashes in der Dev-DB verifiziert werden können.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# Rate-Limiting in Tests deaktivieren (E2E-Tests machen viele Logins auf einer IP)
RATELIMIT_ENABLE = False

# Tests laufen ohne collectstatic — einfachen Storage ohne Manifest verwenden
STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
