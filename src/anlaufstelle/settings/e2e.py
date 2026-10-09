"""E2E-Test-Settings — Dev-Settings + eigene DB + Rate-Limiting deaktiviert.

Erbt von dev.py (PBKDF2-Hasher), damit Passwörter nach E2E-Tests
weiterhin im Dev-Server funktionieren. Test-Settings (MD5-Hasher)
dürfen hier NICHT verwendet werden, da Django bei jedem Login den
Hash automatisch auf den primären Hasher umschreibt.
"""

import os

from .dev import *  # noqa: F401, F403

# Eigene E2E-Datenbank — Dev-Daten bleiben unberührt
_db_name = os.environ.get("E2E_DATABASE_NAME", "anlaufstelle_e2e")
DATABASES["default"] = {**DATABASES["default"], "NAME": _db_name}  # noqa: F405

# Security links and WebAuthn origins follow the E2E server port (Refs #2531).
_e2e_origin = f"http://localhost:{os.environ.get('E2E_PORT_BASE', '8844')}"
if not os.environ.get("EXTERNAL_ORIGIN"):
    EXTERNAL_ORIGIN = _e2e_origin
OTP_WEBAUTHN_ALLOWED_ORIGINS = ["http://localhost:8000", _e2e_origin]

# Rate-Limiting deaktivieren (E2E-Tests machen viele Logins auf einer IP)
RATELIMIT_ENABLE = False

# Der E2E-Stack besitzt keinen Host-Backup-Cron (Refs #2021).
HEALTH_BACKUP_REQUIRED = False

# Ein frischer Checkout hat bis zum ersten Upload kein ``src/media``; nur
# Produktion/Container seeden ``/data/media`` aus dem Image (Refs #2119).
HEALTH_DISK_REQUIRED = False

# Session-Save-Verhalten (kein Write bei jedem Request) erbt unveraendert von
# base.py — dort bereits SESSION_SAVE_EVERY_REQUEST = False (Refs #1827: war
# hier redundant wiederholt).

# MFA-Rollen-Enforcement ist in E2E (wie dev) standardmäßig AUS, damit die
# Default-Logins MFA-frei bleiben. Der dedizierte enforced-Server (Refs #1019)
# setzt ``E2E_MFA_ENFORCE_ROLES=1`` und prüft damit den Rollen-Redirect.
MFA_ENFORCE_PRIVILEGED_ROLES = os.environ.get("E2E_MFA_ENFORCE_ROLES", "0") == "1"

# Demo-Bereichsauswahl im E2E nur auf ausdrücklichen Schalter (Refs #2285): der
# Standard-E2E-Lauf bleibt ohne Demo-Modus, die Demo-Specs skippen ohne den Schalter.
DEMO_MODE = os.environ.get("E2E_DEMO_MODE", "0") == "1"
