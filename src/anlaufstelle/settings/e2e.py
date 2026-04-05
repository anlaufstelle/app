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

# Rate-Limiting deaktivieren (E2E-Tests machen viele Logins auf einer IP)
RATELIMIT_ENABLE = False

# Session nicht bei jedem Request speichern (reduziert DB-Writes und
# verhindert Background-Netzwerkaktivität, die networkidle-Waits stört)
SESSION_SAVE_EVERY_REQUEST = False
