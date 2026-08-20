"""Test-spezifische Settings — optimiert für schnelle Test-Läufe."""

from .dev import *  # noqa: F401, F403

# SudoMode (Refs #683) ist in Tests deaktiviert, damit bestehende RBAC-/
# Scope-Tests ohne explizite Sudo-Session durchlaufen. Tests, die das
# Mixin-Verhalten verifizieren, leben in test_sudo_mode.py und setzen
# das Setting per Override explizit auf True.
SUDO_MODE_ENABLED = False

# Refs #1944: Der Guard fuer die ``AuditLog.detail``-Schluessel ist im Testlauf
# hart scharf (dev.py setzt ihn schon, hier ohne Env-Ausweg) — ein Schreibpunkt
# mit nicht deklariertem Schluessel soll in CI scheitern, nicht erst im Betrieb
# eine ERROR-Zeile erzeugen.
AUDIT_DETAIL_STRICT = True

# Schneller Passwort-Hasher für Tests (PBKDF2 mit 870k Iterationen → MD5).
# Django's eigene Test-Suite nutzt die gleiche Strategie.
# PBKDF2 bleibt als Fallback, damit bestehende Hashes in der Dev-DB verifiziert werden können.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# Rate-Limiting in Tests deaktivieren (E2E-Tests machen viele Logins auf einer IP)
RATELIMIT_ENABLE = False

# Refs #1775: Djangos LocMem-Default deckelt auf ``MAX_ENTRIES = 300`` und
# verwirft bei Ueberschreitung jeden dritten Eintrag (``CULL_FREQUENCY = 3``).
# Die Tests, die das Rate-Limit gezielt aktivieren, legen ihren Zaehler in
# denselben Cache — faellt er einer Verdraengung zum Opfer, beginnt die
# Zaehlung bei null und der Test sieht statt 429 eine 200. Genau so fiel
# ``test_auth.TestRateLimiting.test_rate_limit_blocks_after_threshold`` im
# Parallel-Lauf einmalig aus: ein Flake, der wie ein echter Ratelimit-Ausfall
# aussieht und beim Nachlauf verschwindet. Ein grosszuegiges Limit kostet im
# Testlauf nichts (prozess-lokales Dict, das der Prozess ohnehin wegwirft).
# Der Regressionstest dazu: ``test_rate_limit_zaehler_ueberlebt_cache_druck``.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "OPTIONS": {"MAX_ENTRIES": 100_000},
    }
}

# Tests laufen ohne collectstatic — einfachen Storage ohne Manifest verwenden
STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
