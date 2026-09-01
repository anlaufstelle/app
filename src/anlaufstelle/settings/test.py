"""Test-spezifische Settings — optimiert für schnelle Test-Läufe."""

import os

from .dev import *  # noqa: F401, F403

# Per-Fork-Test-DB fuer den Mutationslauf (Refs #2097).
#
# mutmut forkt EIN Kind pro Mutant und laesst bis ``max_children`` davon
# gleichzeitig laufen. Bis #2097 liefen alle diese pytest-Prozesse gegen
# dieselbe Test-DB (``addopts = "--reuse-db"``); ``transaction=True``-Tests
# committen, die Nachbarprozesse laufen in deren Zeilen und scheitern mit
# ``IntegrityError: duplicate key … (name)=(Teststelle)``. Weil mutmut die
# pytest-Exit-Codes 1 UND 3 auf ``killed`` mappt und ``-x`` die Session schon
# beim Setup-ERROR abbricht, kippt so ein Fremdfehler einen Mutanten immer nur
# von *survived* nach *killed* — der Score war strikt nach oben verzerrt.
#
# ``scripts/dev/run_mutmut.py`` vergibt je gleichzeitig laufendem Fork einen
# Slot und reicht ihn als ``MUTMUT_DB_SLOT`` durch; daraus wird hier
# ``test_<POSTGRES_DB>_mut<N>``. Der Suffix ist bewusst ``_mut`` und nicht
# ``_gw``: ``_gw<N>`` gehoert ``make test-parallel`` (pytest-xdist), und der
# Mutationslauf haelt keinen CI-Lock.
#
# Ohne die Variable (normaler ``pytest``-Lauf, ``make ci``, die in-process
# laufende Stats-Phase von mutmut) bleibt alles unveraendert — Django leitet
# den Namen dann wie bisher als ``test_<POSTGRES_DB>`` ab.
#
# Die Formel ist bewusst dupliziert (Gegenstueck:
# ``run_mutmut.py::slot_test_db_name``): dieses Modul laeuft im
# ``mutants/``-Tree und darf nichts aus ``scripts/`` importieren — ein
# Import-Fehler dort bricht das Test-Listing und der Lauf zaehlt null
# Mutanten (Refs #1600).
try:
    _mutmut_db_slot = int(os.environ.get("MUTMUT_DB_SLOT", "").strip())
except ValueError:
    _mutmut_db_slot = -1
if _mutmut_db_slot >= 0:
    DATABASES["default"] = {  # noqa: F405
        **DATABASES["default"],  # noqa: F405
        "TEST": {
            **DATABASES["default"].get("TEST", {}),  # noqa: F405
            "NAME": f"test_{DATABASES['default']['NAME']}_mut{_mutmut_db_slot}",  # noqa: F405
        },
    }

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
