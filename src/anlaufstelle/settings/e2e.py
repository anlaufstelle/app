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

# Sicherheitsrelevante Links zeigen auf den E2E-Server (Port 8844), nicht auf
# den Runserver-Default aus dev.py (Refs #1967). Eine explizit gesetzte
# EXTERNAL_ORIGIN bleibt unangetastet — dev.py hat sie dann bereits uebernommen.
if not os.environ.get("EXTERNAL_ORIGIN"):
    EXTERNAL_ORIGIN = "http://localhost:8844"

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
