"""Compliance-Datentypen + Konstanten (Refs #919, #958-M3).

Aus dem ehemaligen ``core/services/compliance.py`` extrahiert. Submodule
importieren von hier, ``__init__.py`` re-exportiert ``ComplianceCheck`` und
``ComplianceStatus`` fuer Aufrufer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.models import AuditLog


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
# erzeugt dann SECURITY_VIOLATION. SUDO_MODE_FAILED dagegen ist wie
# MFA_FAILED ein seltener Re-Auth-Fehlversuch einer bereits
# authentifizierten Session (Refs #1084).
#
# NICHT hier hinein gehoeren die Last-Run-Marker der Cron-Jobs
# (``RETENTION_RUN_COMPLETED``, ``MV_REFRESH_COMPLETED``,
# ``AUDIT_CHAIN_VERIFIED`` …, Refs #1872): sie melden einen **erfolgreichen**
# Lauf. Auf dieser Liste wuerde jeder planmaessige Nachtlauf das Dashboard
# gelb faerben. Ihre Ueberwachung ist der Last-Run-Check in
# ``compliance/cron.py`` — dort ist das *Ausbleiben* das Signal, nicht das
# Vorhandensein.
#
# Ebenfalls bewusst draussen (Refs #1818): die drei Fall-Loeschaktionen
# (``CASE_SOFT_DELETED``/``CASE_RESTORED``/``CASE_ANONYMIZED``) — exakt aus dem
# Grund, aus dem auch ihre Personen-Pendants (``CLIENT_SOFT_DELETED`` …) nicht
# hier stehen: eine einzelne Loeschung ist der vorgesehene Ablauf, kein Vorfall;
# auf dieser Liste faerbte jeder korrekt bearbeitete Loeschantrag das Dashboard.
# Die *Menge* ist die Auffaelligkeit, und die zaehlt eine Ebene hoeher:
# ``detect_mass_client_destruction`` (``compliance/breach_detection.py``) sieht
# ueber ``DELETION_APPROVED`` auch genehmigte Fall-Loeschungen und erzeugt bei
# Ueberschreitung eine ``SECURITY_VIOLATION`` — die steht auf dieser Liste.
# Bekannte Grenze: der direkte Loeschweg nicht-qualifizierter Faelle (P1c)
# schreibt keine ``DELETION_APPROVED``-Zeile und laeuft deshalb nicht in diesen
# Zaehler.
#
# Ebenfalls bewusst draussen (Refs #1876): ``CLIENT_MERGED``. Eine
# Zusammenfuehrung ist der vorgesehene Weg, Alt-Duplikate zu konsolidieren —
# auf dieser Liste faerbte jede Aufraeum-Kampagne das Dashboard. Anders als
# die Loeschgenehmigung schreibt der Merge zudem bewusst weder
# ``CLIENT_SOFT_DELETED`` noch ``DELETION_APPROVED``: er soll auch in
# ``detect_mass_client_destruction`` nicht als Vernichtung zaehlen — die
# Daten wandern zum Ziel, sie verschwinden nicht (ADR-045).
#
# Ebenfalls bewusst draussen (Refs #1957): die sieben Bearbeitungs-/
# Zuordnungs-Aktionen rund um Ereignisse, Episoden, Wirkungsziele und Anhaenge.
# Sie sind der Normalfall eines Arbeitstages — auf dieser Liste faerbte jede
# bearbeitete Beratungsdokumentation das Dashboard und machte Check #11 als
# Signal wertlos. Die Aktionen sind Nachweis, nicht Alarm.
# Ebenfalls bewusst draussen (Refs #1965): ``CREDENTIALS_REVOKED``. Ein
# Passwortwechsel, ein Self-Service-Reset und ein Invite-Setup sind der
# vorgesehene Ablauf, kein Vorfall — auf dieser Liste faerbte jede turnusmaessige
# Passworthygiene das Dashboard und machte Check #11 als Signal wertlos. Der
# angriffsnahe Nachbarfall bleibt sichtbar: gehaeufte Fehlversuche laufen
# weiterhin ueber ``LOGIN_FAILED`` in den Lockout, dessen Missbrauchs-Muster
# ``detect_*`` in ``compliance/breach_detection.py`` als ``SECURITY_VIOLATION``
# meldet — und die steht auf dieser Liste.
_CRITICAL_AUDIT_ACTIONS = (
    AuditLog.Action.SECURITY_VIOLATION,
    AuditLog.Action.MFA_FAILED,
    AuditLog.Action.SUDO_MODE_FAILED,
    AuditLog.Action.USER_DEACTIVATED,
)

# Hinweis (Refs #1478, COD-12): Der fruehere ``_PRIVILEGED_ROLES``-Nenner der
# MFA-Quote wurde entfernt — er duplizierte die Rollenmenge und zaehlte LEAD
# unabhaengig davon, ob LEAD tatsaechlich MFA-erzwungen ist. Die Quote in
# ``compliance.mfa._mfa_checks`` bindet den Nenner jetzt direkt an die
# gemeinsame Quelle ``core.models.user.mfa_enforced_roles()``.
