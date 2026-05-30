"""Compliance-Datentypen + Konstanten (Refs #919, #958-M3).

Aus dem ehemaligen ``core/services/compliance.py`` extrahiert. Submodule
importieren von hier, ``__init__.py`` re-exportiert ``ComplianceCheck`` und
``ComplianceStatus`` fuer Aufrufer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.models import AuditLog
from core.models.user import User


class ComplianceStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ComplianceCheck:
    """Ein einzelner Compliance-Check (Refs #919).

    - ``key`` ist stabil und maschinen-lesbar (z.B. ``"db_role_app"``);
      Tests + Templates verlassen sich darauf.
    - ``label`` ist UI-Text (z.B. ``"App-DB-Rolle"``).
    - ``category`` gruppiert Checks in der UI (z.B. ``"Datenbank"``).
    - ``status`` einer von vier Enum-Werten.
    - ``message`` ist die kurze Statusmeldung.
    - ``detail`` ist optionale Zusatz-Info (Wert, Schwellwert, Erklaerung).
    - ``action_hint`` ist die empfohlene Korrekturmassnahme bei
      ``warning``/``critical``.
    """

    key: str
    label: str
    category: str
    status: ComplianceStatus
    message: str
    detail: str | None = None
    action_hint: str | None = None


# Liste der "kritischen" AuditLog-Aktionen fuer Check #11 (Audit-Events
# seit 24h). LOGIN_FAILED ist bewusst NICHT enthalten — der ist Teil
# der normalen Tippfehler-Rate und wuerde das Dashboard zu rot machen.
# Brute-Force-Erkennung passiert oberhalb in breach_detection.py und
# erzeugt dann SECURITY_VIOLATION.
_CRITICAL_AUDIT_ACTIONS = (
    AuditLog.Action.SECURITY_VIOLATION,
    AuditLog.Action.MFA_FAILED,
    AuditLog.Action.USER_DEACTIVATED,
)

# Rollen, fuer die MFA-Quote erhoben wird (Audit: "privilegierte Rollen").
# STAFF/ASSISTANT bleiben aussen vor — die werden ueber
# ``mfa_enforced_facility_wide`` adressiert.
_PRIVILEGED_ROLES = (
    User.Role.SUPER_ADMIN,
    User.Role.FACILITY_ADMIN,
    User.Role.LEAD,
)
