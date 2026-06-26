"""
Development settings for Anlaufstelle.
"""

import os
import sys

from .base import *  # noqa: F401, F403

# Dev-Default für SECRET_KEY — NUR in dev/test. prod.py erzwingt DJANGO_SECRET_KEY.
if not SECRET_KEY:  # noqa: F405
    SECRET_KEY = "django-insecure-dev-only-change-in-production"  # noqa: S105

# E-Mails in der Konsole ausgeben (kein SMTP nötig)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEBUG = True

ALLOWED_HOSTS = ["*"]

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
# Encryption ist in Dev optional, damit lokal ohne Key-Setup gearbeitet werden
# kann. ABER: ohne ENCRYPTION_KEY werden sensible Felder (inkl. besonderer
# Kategorien nach Art. 9 DSGVO) im KLARTEXT persistiert. Eine Staging-Box, die
# versehentlich auf dev-Settings läuft, täte das bisher still (nur eine
# einzeilige Log-Warnung). Daher:
#   1) eine unübersehbar laute Warnung (Banner auf stderr, nicht nur Log), und
#   2) ein opt-in-Guard REQUIRE_ENCRYPTION: ist er gesetzt (z. B. auf einer
#      Staging-/Nicht-lokal-Box), verweigert dev.py den Start ohne Key —
#      analog zum prod-Fail-Closed.
# Lokal (Default, Flag ungesetzt) bleibt der Start ohne Key bewusst möglich.
REQUIRE_ENCRYPTION = os.environ.get("REQUIRE_ENCRYPTION", "").lower() in ("true", "1", "yes")

if not ENCRYPTION_KEY:  # noqa: F405
    if REQUIRE_ENCRYPTION:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "REQUIRE_ENCRYPTION ist gesetzt, aber ENCRYPTION_KEY fehlt — dev-Settings "
            "verweigern den Start, statt Felder im Klartext zu speichern. Setze einen "
            "ENCRYPTION_KEY (Fernet-Key) oder entferne REQUIRE_ENCRYPTION für rein "
            "lokale Entwicklung mit Wegwerf-Daten."
        )

    import logging

    _plaintext_msg = (
        "KLARTEXT-MODUS: ENCRYPTION_KEY ist NICHT gesetzt — sensible Felder (inkl. "
        "besonderer Kategorien nach Art. 9 DSGVO) werden UNVERSCHLÜSSELT gespeichert. "
        "Nur für lokale Entwicklung mit Wegwerf-Daten zulässig. Für jede nicht-lokale "
        "Umgebung ENCRYPTION_KEY setzen oder mit REQUIRE_ENCRYPTION=1 hart absichern."
    )
    _border = "!" * 80
    logging.getLogger("core").warning("\n%s\n  %s\n%s", _border, _plaintext_msg, _border)
    # Zusätzlich direkt auf stderr: eine einzelne Log-Zeile ginge im Server-/
    # systemd-Output sonst leicht unter — das Risiko muss unübersehbar sein.
    print(f"\n{_border}\n  {_plaintext_msg}\n{_border}\n", file=sys.stderr)

# Demo-Seed in lokaler Entwicklung erlaubt (test.py/e2e.py erben). Refs #1040 (S1).
SEED_ALLOWED = True
