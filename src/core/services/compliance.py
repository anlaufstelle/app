"""Compliance-Aggregator-Service fuer das Compliance-Dashboard (Refs #919).

Aggregiert die elf Compliance-Checks aus dem Audit-2 §2.4 in eine
einheitliche ``ComplianceCheck``-Liste mit ``ok``/``warning``/
``critical``/``unknown``-Status, Nachricht, Detail und
Action-Hint. Wird konsumiert von :class:`core.views.system.compliance.
SystemComplianceView`.

Bewusst klein gehalten:

- Kein Caching im MVP (Performance-Budget ist 2 s, gemessen <500 ms).
- Kein eigenes Notification-System (Sentry hat das schon).
- Kein Auto-Resolution: ``ComplianceCheck`` ist Anzeige, keine Aktion.

Wer ein neues Check braucht: eine private ``_xxx_checks()``-Helper-
Funktion ergaenzen, in :func:`aggregate_checks` einfuegen. Pro Check
einen Test schreiben. Fertig.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from django.db.models import Count

from core.models import AuditLog
from core.models.user import User
from core.services import system_health
from core.services import virus_scan as virus_scan_service

logger = logging.getLogger(__name__)


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


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


def aggregate_checks() -> list[ComplianceCheck]:
    """Sammle alle Compliance-Checks fuer das Dashboard.

    Defensiv: jeder Helper schluckt seine eigenen Exceptions und liefert
    bei Fehler einen ``unknown``-ComplianceCheck mit der Exception als
    detail. Damit kippt ein Single-Failure das Dashboard nicht.
    """
    checks: list[ComplianceCheck] = []
    for helper in (
        _db_role_checks,
        _backup_checks,
        _clamav_checks,
        _retention_checks,
        _restore_checks,
        _mfa_checks,
        _migration_checks,
        _version_checks,
        _audit_event_checks,
    ):
        name = getattr(helper, "__name__", "unknown_helper")
        try:
            checks.extend(helper())
        except Exception as exc:  # noqa: BLE001 — Dashboard darf nicht kippen
            logger.exception("Compliance-Helper %s fehlgeschlagen", name)
            checks.append(
                ComplianceCheck(
                    key=f"_internal_{name}",
                    label=f"Interner Fehler: {name}",
                    category="System",  # pragma: no mutate
                    status=ComplianceStatus.UNKNOWN,
                    message="Check konnte nicht ausgefuehrt werden.",  # pragma: no mutate
                    detail=str(exc),
                    action_hint="Server-Log einsehen.",  # pragma: no mutate
                )
            )
    return checks


# ----------------------------------------------------------------------------
# Helper: pro Kategorie eine Funktion. Jede liefert 1+ ComplianceCheck.
# ----------------------------------------------------------------------------


def _db_role_checks() -> list[ComplianceCheck]:
    """Drei Checks: App-NOSUPERUSER, App-NOBYPASSRLS, Admin-Rolle vorhanden."""
    from core.management.commands.check_db_roles import check_db_roles

    role_checks, config_errors = check_db_roles()
    out: list[ComplianceCheck] = []

    for rc in role_checks:
        if rc.label == "App":
            # Zwei Checks aus einer Rolle (rolsuper, rolbypassrls).
            out.append(_db_role_attribute_check(rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"))
            out.append(
                _db_role_attribute_check(rc, "rolbypassrls", "db_role_app_nobypassrls", "App-DB-Rolle kein BYPASSRLS")
            )
        elif rc.label == "Admin":
            out.append(_db_role_admin_check(rc))

    # Config-Errors (z.B. POSTGRES_ADMIN_USER fehlt) als warning ausgeben —
    # die Admin-Rolle ist nicht zwingend, aber DSGVO-konformes Self-Hosting
    # erwartet eine separate Maintenance-Rolle.
    if config_errors and not any(rc.label == "Admin" for rc in role_checks):
        out.append(
            ComplianceCheck(
                key="db_role_admin_missing",
                label="Admin-DB-Rolle",  # pragma: no mutate
                category="Datenbank",  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message="Admin-/Maintenance-Rolle nicht konfiguriert.",  # pragma: no mutate
                detail="; ".join(config_errors),
                action_hint="POSTGRES_ADMIN_USER + POSTGRES_ADMIN_PASSWORD in .env setzen (siehe #902).",  # pragma: no mutate  # noqa: E501
            )
        )

    return out


def _db_role_attribute_check(role_check, attr: str, key: str, label: str) -> ComplianceCheck:
    """Hilfsfunktion: ein einzelnes Attribut der App-Rolle (rolsuper / rolbypassrls)."""
    expected = False  # App-Rolle: beide Attribute False
    actual = getattr(role_check, f"actual_{attr.replace('rol', '')}")
    if actual is None:
        return ComplianceCheck(
            key=key,
            label=label,
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message=f"Rolle '{role_check.role}' nicht in pg_roles gefunden.",
            action_hint="DB-Rollen-Setup pruefen — siehe deploy/postgres-init/01-app-role.sh.",  # pragma: no mutate
        )
    if actual == expected:
        return ComplianceCheck(
            key=key,
            label=label,
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.OK,
            message="Korrekt konfiguriert.",  # pragma: no mutate
            detail=f"{role_check.role}: {attr}={actual}",
        )
    return ComplianceCheck(
        key=key,
        label=label,
        category="Datenbank",  # pragma: no mutate
        status=ComplianceStatus.CRITICAL,
        message=f"Rolle '{role_check.role}' hat falsches Attributprofil.",
        detail=f"{attr}={actual} (erwartet: {expected})",
        action_hint="manage.py check_db_roles + Rollen-Init-Script pruefen (Refs #902).",  # pragma: no mutate
    )


def _db_role_admin_check(role_check) -> ComplianceCheck:
    """Admin-Rolle: NOSUPERUSER + BYPASSRLS erwartet."""
    if role_check.actual_super is None:
        return ComplianceCheck(
            key="db_role_admin",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message=f"Admin-Rolle '{role_check.role}' nicht in pg_roles gefunden.",
            action_hint="DB-Rollen-Setup pruefen — siehe deploy/postgres-init/01-app-role.sh.",  # pragma: no mutate
        )
    if role_check.ok:
        return ComplianceCheck(
            key="db_role_admin",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.OK,
            message="Admin-Rolle korrekt (NOSUPERUSER, BYPASSRLS).",  # pragma: no mutate
            detail=f"{role_check.role}: rolsuper={role_check.actual_super}, rolbypassrls={role_check.actual_bypassrls}",
        )
    return ComplianceCheck(
        key="db_role_admin",
        label="Admin-DB-Rolle",  # pragma: no mutate
        category="Datenbank",  # pragma: no mutate
        status=ComplianceStatus.CRITICAL,
        message="Admin-Rolle hat falsches Attributprofil.",  # pragma: no mutate
        detail="; ".join(role_check.problems()),
        action_hint="deploy/postgres-init/01-app-role.sh pruefen (Refs #902).",  # pragma: no mutate
    )


def _backup_checks() -> list[ComplianceCheck]:
    """Letzter Backup-Zeitpunkt aus settings.BACKUP_DIR."""
    info = system_health.last_backup_info()
    if info is None:
        return [
            ComplianceCheck(
                key="backup_age",
                label="Letztes Backup",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Kein Backup gefunden oder BACKUP_DIR nicht konfiguriert.",  # pragma: no mutate
                action_hint="settings.BACKUP_DIR pruefen, Cron-Job 'backup.sh' aktivieren.",  # pragma: no mutate
            )
        ]
    age_hours = info["age_hours"]
    detail = f"{info['path']} (Alter: {age_hours:.1f}h)"
    if age_hours <= 24:
        return [
            ComplianceCheck(
                key="backup_age",
                label="Letztes Backup",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=f"Backup juenger als 24h ({age_hours:.0f}h).",
                detail=detail,
            )
        ]
    if age_hours <= 72:
        return [
            ComplianceCheck(
                key="backup_age",
                label="Letztes Backup",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=f"Backup ist {age_hours:.0f}h alt (Schwelle: 72h).",
                detail=detail,
                action_hint="Backup-Cron pruefen, evtl. manuell ausloesen.",  # pragma: no mutate
            )
        ]
    return [
        ComplianceCheck(
            key="backup_age",
            label="Letztes Backup",  # pragma: no mutate
            category="Backup",  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=f"Backup ist {age_hours:.0f}h alt — Cron ausgefallen?",
            detail=detail,
            action_hint="Backup-Cron + Disk-Speicher pruefen, Restore-Test nachholen.",  # pragma: no mutate
        )
    ]


def _restore_checks() -> list[ComplianceCheck]:
    """Letzter dokumentierter Restore-Test (AuditLog ``RESTORE_VERIFIED``)."""
    entry = (
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED)
        .order_by("-timestamp")
        .only("timestamp", "detail")
        .first()
    )
    if entry is None:
        return [
            ComplianceCheck(
                key="restore_verified",
                label="Restore-Test",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Noch nie ein Restore-Test dokumentiert.",  # pragma: no mutate
                action_hint="manage.py mark_restore_verified --note '...' nach jedem Test ausfuehren.",  # pragma: no mutate # noqa: E501
            )
        ]
    now = datetime.now(tz=UTC)
    age_days = (now - entry.timestamp).days
    detail = f"Letzter Restore-Test: {entry.timestamp:%Y-%m-%d}; Notiz: {entry.detail.get('note') or '—'}"
    if age_days <= 90:
        return [
            ComplianceCheck(
                key="restore_verified",
                label="Restore-Test",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=f"Letzter Test vor {age_days} Tagen.",
                detail=detail,
            )
        ]
    if age_days <= 180:
        return [
            ComplianceCheck(
                key="restore_verified",
                label="Restore-Test",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=f"Letzter Test vor {age_days} Tagen — Auffrischung empfohlen.",
                detail=detail,
                action_hint="Restore gegen frische DB testen, dann mark_restore_verified ausfuehren.",  # pragma: no mutate # noqa: E501
            )
        ]
    return [
        ComplianceCheck(
            key="restore_verified",
            label="Restore-Test",  # pragma: no mutate
            category="Backup",  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=f"Letzter Test vor {age_days} Tagen — DSGVO Art. 32 verletzt.",
            detail=detail,
            action_hint="Restore-Test sofort nachholen und dokumentieren.",  # pragma: no mutate
        )
    ]


def _clamav_checks() -> list[ComplianceCheck]:
    """ClamAV-Erreichbarkeit + Signatur-Alter."""
    ping_ok = virus_scan_service.ping()
    reachability = ComplianceCheck(
        key="clamav_reachable",
        label="ClamAV erreichbar",  # pragma: no mutate
        category="Virus-Scan",  # pragma: no mutate
        status=ComplianceStatus.OK if ping_ok else ComplianceStatus.CRITICAL,
        message="Daemon antwortet." if ping_ok else "Daemon nicht erreichbar oder deaktiviert.",
        action_hint=None if ping_ok else "docker compose ps clamav; CLAMAV_ENABLED in .env pruefen.",
    )
    sig = virus_scan_service.signature_info()
    if sig is None:
        signature = ComplianceCheck(
            key="clamav_signature",
            label="ClamAV-Signatur",  # pragma: no mutate
            category="Virus-Scan",  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message="Signatur-Daten nicht abrufbar.",  # pragma: no mutate
            action_hint=None if not ping_ok else "ClamAV-Container neu starten, dann erneut pruefen.",
        )
    else:
        age = sig.get("age_days")
        version = sig.get("version") or "unbekannt"
        if age is None:
            signature = ComplianceCheck(
                key="clamav_signature",
                label="ClamAV-Signatur",  # pragma: no mutate
                category="Virus-Scan",  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Signatur-Datum nicht ermittelbar.",  # pragma: no mutate
                detail=f"Version: {version}",
            )
        elif age <= 7:
            signature = ComplianceCheck(
                key="clamav_signature",
                label="ClamAV-Signatur",  # pragma: no mutate
                category="Virus-Scan",  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=f"Signatur ist {age} Tag(e) alt.",
                detail=f"Version: {version}",
            )
        else:
            signature = ComplianceCheck(
                key="clamav_signature",
                label="ClamAV-Signatur",  # pragma: no mutate
                category="Virus-Scan",  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=f"Signatur ist {age} Tage alt (Schwelle: 7).",
                detail=f"Version: {version}",
                action_hint="freshclam-Cron im ClamAV-Container pruefen.",  # pragma: no mutate
            )
    return [reachability, signature]


def _retention_checks() -> list[ComplianceCheck]:
    """Retention-Cron LastRun aus AuditLog ``RETENTION_RUN_COMPLETED``."""
    entry = (
        AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED)
        .order_by("-timestamp")
        .only("timestamp", "detail")
        .first()
    )
    if entry is None:
        return [
            ComplianceCheck(
                key="retention_last_run",
                label="Retention-Lauf",  # pragma: no mutate
                category="Retention",  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Noch kein RETENTION_RUN_COMPLETED-Eintrag — Cron lief vielleicht nie.",  # pragma: no mutate
                action_hint="enforce_retention-Cron pruefen (Ops-Runbook §6).",  # pragma: no mutate
            )
        ]
    now = datetime.now(tz=UTC)
    age_days = (now - entry.timestamp).days
    detail = f"Letzter Lauf: {entry.timestamp:%Y-%m-%d %H:%M}"
    if age_days <= 7:
        status = ComplianceStatus.OK
        message = f"Letzter Lauf vor {age_days} Tag(en)."
        hint = None
    elif age_days <= 14:
        status = ComplianceStatus.WARNING
        message = f"Letzter Lauf vor {age_days} Tagen — Cron-Schedule pruefen."
        hint = "Cron muss alle 1-7 Tage laufen (Default: taeglich)."
    else:
        status = ComplianceStatus.CRITICAL
        message = f"Letzter Lauf vor {age_days} Tagen — Cron ausgefallen?"
        hint = "Cron-Status im Container pruefen + manuell ausfuehren."
    return [
        ComplianceCheck(
            key="retention_last_run",
            label="Retention-Lauf",  # pragma: no mutate
            category="Retention",  # pragma: no mutate
            status=status,
            message=message,
            detail=detail,
            action_hint=hint,
        )
    ]


def _mfa_checks() -> list[ComplianceCheck]:
    """MFA-Quote bei privilegierten Rollen (super_admin/facility_admin/leitung)."""
    from django_otp.plugins.otp_totp.models import TOTPDevice

    # Anzahl User pro Rolle und Anzahl mit confirmed TOTPDevice.
    privileged_total = User.objects.filter(role__in=_PRIVILEGED_ROLES, is_active=True).count()
    if privileged_total == 0:
        return [
            ComplianceCheck(
                key="mfa_privileged_quote",
                label="MFA-Quote (privilegierte Rollen)",  # pragma: no mutate
                category="MFA",  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Keine privilegierten User vorhanden.",  # pragma: no mutate
            )
        ]
    privileged_with_mfa = (
        TOTPDevice.objects.filter(confirmed=True, user__role__in=_PRIVILEGED_ROLES, user__is_active=True)
        .values("user_id")
        .annotate(n=Count("id"))
        .count()
    )
    percent = round(privileged_with_mfa / privileged_total * 100, 1)
    detail = f"{privileged_with_mfa} von {privileged_total} privilegierten Usern haben MFA aktiv."
    if percent >= 100:
        status = ComplianceStatus.OK
        message = "100 % der privilegierten Rollen haben MFA aktiv."
        hint = None
    elif percent >= 80:
        status = ComplianceStatus.WARNING
        message = f"{percent:g} % MFA-Quote — Lücke schliessen."
        hint = "Verbleibende User per Admin-UI zur MFA-Aktivierung anhalten."
    else:
        status = ComplianceStatus.CRITICAL
        message = f"Nur {percent:g} % der privilegierten Rollen haben MFA — Risiko."
        hint = "mfa_enforced_facility_wide aktivieren oder MFA pro User erzwingen."
    return [
        ComplianceCheck(
            key="mfa_privileged_quote",
            label="MFA-Quote (privilegierte Rollen)",  # pragma: no mutate
            category="MFA",  # pragma: no mutate
            status=status,
            message=message,
            detail=detail,
            action_hint=hint,
        )
    ]


def _migration_checks() -> list[ComplianceCheck]:
    """Pending Django-Migrationen."""
    pending = system_health.pending_migrations()
    if not pending:
        return [
            ComplianceCheck(
                key="migrations_pending",
                label="Migrationen",  # pragma: no mutate
                category="System",  # pragma: no mutate
                status=ComplianceStatus.OK,
                message="Alle Migrationen angewendet.",  # pragma: no mutate
            )
        ]
    listing = ", ".join(f"{app}.{name}" for app, name in pending[:5])
    if len(pending) > 5:
        listing += f" (+ {len(pending) - 5} weitere)"
    return [
        ComplianceCheck(
            key="migrations_pending",
            label="Migrationen",  # pragma: no mutate
            category="System",  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=f"{len(pending)} ausstehende Migration(en).",
            detail=listing,
            action_hint="docker compose exec web python manage.py migrate ausfuehren.",  # pragma: no mutate
        )
    ]


def _version_checks() -> list[ComplianceCheck]:
    """App-Version / Django-Version / Python-Version als Info-Karte."""
    versions = system_health.app_versions()
    message = f"App {versions['app_version']}, Django {versions['django_version']}, Python {versions['python_version']}"
    return [
        ComplianceCheck(
            key="versions",
            label="Versionen",  # pragma: no mutate
            category="System",  # pragma: no mutate
            status=ComplianceStatus.OK,
            message=message,
        )
    ]


def _audit_event_checks() -> list[ComplianceCheck]:
    """Kritische Audit-Events der letzten 24h."""
    cutoff = datetime.now(tz=UTC) - timedelta(hours=24)
    count = AuditLog.objects.filter(action__in=_CRITICAL_AUDIT_ACTIONS, timestamp__gte=cutoff).count()
    if count == 0:
        return [
            ComplianceCheck(
                key="critical_audit_events_24h",
                label="Kritische Audit-Events (24h)",  # pragma: no mutate
                category="Audit",  # pragma: no mutate
                status=ComplianceStatus.OK,
                message="Keine kritischen Events in den letzten 24h.",  # pragma: no mutate
                detail=f"Gemonitorte Aktionen: {', '.join(a.value for a in _CRITICAL_AUDIT_ACTIONS)}",
            )
        ]
    if count <= 5:
        return [
            ComplianceCheck(
                key="critical_audit_events_24h",
                label="Kritische Audit-Events (24h)",  # pragma: no mutate
                category="Audit",  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=f"{count} kritische Event(s) in 24h.",
                detail=(
                    "Im System-Audit-Log (/system/audit/) auf "
                    "SECURITY_VIOLATION / MFA_FAILED / USER_DEACTIVATED filtern."
                ),
                action_hint="Pro Eintrag den Kontext pruefen, ggf. Issue mit Label security eroeffnen.",  # pragma: no mutate  # noqa: E501
            )
        ]
    return [
        ComplianceCheck(
            key="critical_audit_events_24h",
            label="Kritische Audit-Events (24h)",  # pragma: no mutate
            category="Audit",  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=f"{count} kritische Event(s) in 24h — moeglicher Vorfall.",
            detail="Im System-Audit-Log dringend pruefen.",
            action_hint="Vorfall-Triage starten, ggf. DSGVO Art. 33 Meldung an Aufsichtsbehoerde.",  # pragma: no mutate
        )
    ]


__all__ = [
    "ComplianceCheck",
    "ComplianceStatus",
    "aggregate_checks",
]
