"""
Development settings for Anlaufstelle.
"""

import contextlib
import os
import sys
from pathlib import Path

from ._keyring import split_key_ring as _split_key_ring
from .base import *  # noqa: F401, F403
from .base import _validate_encryption_key_ring

# Dev-Default für SECRET_KEY — NUR in dev/test. prod.py erzwingt DJANGO_SECRET_KEY.
if not SECRET_KEY:  # noqa: F405
    SECRET_KEY = "django-insecure-dev-only-change-in-production"  # noqa: S105

# Dev/Test-Default für POSTGRES_PASSWORD — NUR in dev/test (kaskadiert nach
# test.py/e2e.py). base.py hat seinen schwachen Default "anlaufstelle" entfernt
# (Refs #1591), damit prod ohne gesetztes Passwort nicht still darauf zurueckfaellt.
# Lokal (Docker-Container mit POSTGRES_PASSWORD=anlaufstelle) braucht die
# DB-Verbindung ohne .env-Wert aber weiterhin diesen Fallback.
if not DATABASES["default"]["PASSWORD"]:  # noqa: F405
    DATABASES["default"]["PASSWORD"] = "anlaufstelle"  # noqa: F405, S105

# E-Mails in der Konsole ausgeben (kein SMTP nötig).
# Seit Django 6.1 über ``MAILERS`` statt ``EMAIL_BACKEND`` (Refs #1662);
# kaskadiert nach test.py und e2e.py.
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}

DEBUG = True

# Refs #1944: In der Entwicklung laut scheitern, wenn ein Audit-Schreibpunkt
# einen nicht deklarierten ``detail``-Schluessel mitbringt (kaskadiert nach
# test.py und e2e.py). Im Betrieb bleibt der Guard meldend, siehe base.py.
AUDIT_DETAIL_STRICT = env_bool("DJANGO_AUDIT_DETAIL_STRICT", default=True)  # noqa: F405

# Lokale Entwicklung laeuft ohne Host-Backup-Cron (Refs #2021).
HEALTH_BACKUP_REQUIRED = False

# Ein frischer Checkout hat bis zum ersten Upload kein ``src/media``; nur
# Produktion/Container seeden ``/data/media`` aus dem Image (Refs #2119).
HEALTH_DISK_REQUIRED = False

ALLOWED_HOSTS = ["*"]

# Sicherheitsrelevante Links (Einladung, Passwort-Reset, Entsperrung) zeigen
# lokal auf den Runserver-Default. Ohne Vorbelegung gaebe es hier keine Origin
# und damit keinen Link — die Pflicht aus prod.py laesst sich in DEV nicht
# sinnvoll spiegeln (Refs #1967). Per EXTERNAL_ORIGIN ueberschreibbar.
EXTERNAL_ORIGIN = EXTERNAL_ORIGIN or "http://localhost:8000"  # noqa: F405

# DEV bewusst MFA-frei (Refs #1019): das Rollen-Default-Enforcement aus base.py
# wird hier abgeschaltet, damit die Seed-Logins (admin/emma/superadmin) ohne
# TOTP arbeiten. Kaskadiert ueber Vererbung nach test.py UND e2e.py.
# ``mfa_required`` (User) und ``mfa_enforced_facility_wide`` (Facility) bleiben
# unberuehrt — MFA laesst sich in DEV also weiterhin gezielt testen.
MFA_ENFORCE_PRIVILEGED_ROLES = False

# WhiteNoise: Static Files ohne collectstatic bei jeder Aenderung
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True

# --- Feldverschlüsselung in Dev (Refs #1276) ---
# Encryption is optional in dev so that local work needs no key setup.
# BUT: without a key ring (ENCRYPTION_KEYS), sensitive fields (including special
# categories under GDPR Art. 9) are persisted in PLAINTEXT. A staging box that
# accidentally runs on dev settings used to do this silently (a single-line
# log warning only). Hence:
#   1) a loud warning (banner on stderr, not just the log), and
#   2) an opt-in guard REQUIRE_ENCRYPTION: when set (e.g. on a staging or
#      non-local box), dev.py refuses to start without a key, mirroring the
#      fail-closed behavior of prod.
# Locally (default, flag unset) starting without a key stays possible on purpose.
REQUIRE_ENCRYPTION = os.environ.get("REQUIRE_ENCRYPTION", "").lower() in ("true", "1", "yes")

# --- Lokaler One-Command-Stack: Schlüssel aus einer Datei (Refs #1062) ---
# ``docker compose exec``/``run`` startet einen neuen Prozess mit der
# Container-KONFIGURATION — nicht mit der Umgebung von PID 1. Ein Schlüssel,
# den nur die Startshell des Webdienstes exportiert, wäre für jedes spätere
# ``manage.py``-Kommando unsichtbar: es liefe still in den Klartext-Modus
# unten und schriebe Art.-9-Felder unverschlüsselt, während die Lesepfade an
# bereits verschlüsselten Daten mit ``EncryptionKeyMissing`` abbrechen.
#
# The local compose stack therefore passes a PATH instead of a value; every
# process in the container reads the same key from the same file. Strictly
# opt-in: without ``LOCAL_ENCRYPTION_KEY_FILE`` nothing changes, not even in
# ``test.py``/``e2e.py``, which inherit from here. The file content becomes the
# only entry of the key ring; a set ``ENCRYPTION_KEYS`` still wins.
_LOCAL_ENCRYPTION_KEY_FILE = os.environ.get("LOCAL_ENCRYPTION_KEY_FILE", "")


if not _split_key_ring(ENCRYPTION_KEYS) and _LOCAL_ENCRYPTION_KEY_FILE:  # noqa: F405
    # Fehlt die Datei oder ist sie unlesbar, geht es unverändert in den
    # Klartext-/``REQUIRE_ENCRYPTION``-Pfad unten — laut statt still.
    with contextlib.suppress(OSError):
        ENCRYPTION_KEYS = Path(_LOCAL_ENCRYPTION_KEY_FILE).read_text(encoding="utf-8").strip()
        _validate_encryption_key_ring(ENCRYPTION_KEYS)

if not _split_key_ring(ENCRYPTION_KEYS):  # noqa: F405
    if REQUIRE_ENCRYPTION:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "REQUIRE_ENCRYPTION is set but ENCRYPTION_KEYS is empty — dev settings "
            "refuse to start instead of storing fields in plaintext. Set ENCRYPTION_KEYS "
            "(comma-separated Fernet key ring) or remove REQUIRE_ENCRYPTION for purely "
            "local development with throwaway data."
        )

    import logging

    _plaintext_msg = (
        "PLAINTEXT MODE: ENCRYPTION_KEYS is NOT set - sensitive fields (including "
        "special categories under GDPR Art. 9) are stored UNENCRYPTED. "
        "Acceptable only for local development with throwaway data. For any non-local "
        "environment set ENCRYPTION_KEYS or enforce it with REQUIRE_ENCRYPTION=1."
    )
    _border = "!" * 80
    logging.getLogger("core").warning("\n%s\n  %s\n%s", _border, _plaintext_msg, _border)
    # Zusätzlich direkt auf stderr: eine einzelne Log-Zeile ginge im Server-/
    # systemd-Output sonst leicht unter — das Risiko muss unübersehbar sein.
    print(f"\n{_border}\n  {_plaintext_msg}\n{_border}\n", file=sys.stderr)

# Demo-Seed in lokaler Entwicklung erlaubt (test.py/e2e.py erben). Refs #1040 (S1).
SEED_ALLOWED = True

# --- Passkeys/WebAuthn (ADR-032, Refs #1492) ---
# Lokal ist die Relying Party ``localhost`` (Browser behandeln localhost als
# secure context, auch ohne HTTPS). Origins decken den Dev-Server (:8000) und
# den E2E-Server (:8844, via e2e.py geerbt) ab. Kaskadiert nach test.py/e2e.py.
OTP_WEBAUTHN_RP_ID = "localhost"
OTP_WEBAUTHN_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://localhost:8844",
]
