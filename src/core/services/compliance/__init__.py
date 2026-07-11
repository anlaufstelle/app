"""Compliance-Aggregator-Service fuer das Compliance-Dashboard (Refs #919).

Refs #958 (M-3) — vorher war alles in einem 589-LoC-Modul ``services/compliance.py``;
jetzt thematisch gesplittet (db_roles, backup, clamav, retention, mfa, system_info,
audit_events), analog zum bereits erfolgten ``services/events/``-Subpackage.

Aggregiert die elf Compliance-Checks aus §2.4 in eine
einheitliche ``ComplianceCheck``-Liste mit ``ok``/``warning``/
``critical``/``unknown``-Status, Nachricht, Detail und
Action-Hint. Wird konsumiert von :class:`core.views.system.compliance.
SystemComplianceView`.

Bewusst klein gehalten:

- Kein Caching im MVP (Performance-Budget ist 2 s, gemessen <500 ms).
- Kein eigenes Notification-System (Sentry hat das schon).
- Kein Auto-Resolution: ``ComplianceCheck`` ist Anzeige, keine Aktion.

Wer ein neues Check braucht: eine private ``_xxx_checks()``-Helper-
Funktion in einem passenden Submodul ergaenzen, im ``__init__.py``
importieren, in :func:`aggregate_checks` einfuegen. Pro Check einen
Test schreiben. Fertig.
"""

from __future__ import annotations

import logging

from core.services.compliance._types import (
    _CRITICAL_AUDIT_ACTIONS,
    _PRIVILEGED_ROLES,
    ComplianceCheck,
    ComplianceStatus,
)
from core.services.compliance.app_superuser import _app_superuser_checks
from core.services.compliance.audit_events import _audit_event_checks
from core.services.compliance.backup import _backup_checks, _restore_checks
from core.services.compliance.breach_detection import (
    _validate_webhook_url,
    detect_anonymous_login_bursts,
    detect_distributed_login_attack,
    detect_failed_login_burst,
    detect_mass_client_destruction,
    detect_mass_delete,
    detect_mass_export,
    record_finding,
    record_system_finding,
    run_all_detections,
    run_system_detections,
)
from core.services.compliance.clamav import _clamav_checks
from core.services.compliance.cron import (
    _breach_scan_checks,
    _mv_refresh_checks,
    _snapshot_checks,
)
from core.services.compliance.db_roles import (
    _db_role_admin_check,
    _db_role_attribute_check,
    _db_role_checks,
)
from core.services.compliance.k_anonymization import (
    count_clients_in_bucket,
    is_k_anonymous,
    k_anonymize_client,
)
from core.services.compliance.mfa import _mfa_checks
from core.services.compliance.retention import _retention_checks
from core.services.compliance.sensitivity import (
    ROLE_MAX_SENSITIVITY,
    SENSITIVITY_RANK,
    allowed_sensitivities_for_user,
    effective_sensitivity,
    get_visible_attachment_or_404,
    get_visible_event_or_404,
    user_can_see_document_type,
    user_can_see_event,
    user_can_see_field,
)
from core.services.compliance.system_info import _migration_checks, _version_checks
from core.services.compliance.vvt import (
    PROCESSING_ACTIVITIES,
    get_activity,
    get_processing_activities,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


def _run_helpers(helpers) -> list[ComplianceCheck]:
    """Ruft Check-Helper defensiv auf und sammelt ihre Ergebnisse.

    Jeder Helper schluckt seine eigenen Exceptions; faengt er doch eine,
    liefert ``_run_helpers`` einen ``unknown``-ComplianceCheck mit der
    Exception als detail. Damit kippt ein Single-Failure weder das
    Compliance-Dashboard noch die /system/-Uebersicht.

    Aufrufer uebergeben die Helper als Tuple, das **zur Aufrufzeit** aus
    dem Modul-Namespace gebildet wird — so greifen
    ``patch.object(compliance, "_db_role_checks", ...)`` in Tests weiterhin.
    """
    checks: list[ComplianceCheck] = []
    for helper in helpers:
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


def aggregate_checks() -> list[ComplianceCheck]:
    """Sammle alle Compliance-Checks fuer das Dashboard.

    Defensiv via :func:`_run_helpers` — ein Single-Failure kippt das
    Dashboard nicht. Helper werden ueber den Modul-Namespace aufgerufen
    (``_db_role_checks`` etc.), damit Tests
    ``patch.object(compliance, "_db_role_checks", ...)`` weiterhin nutzen.
    """
    return _run_helpers(
        (
            _db_role_checks,
            _app_superuser_checks,
            _backup_checks,
            _clamav_checks,
            _retention_checks,
            _snapshot_checks,
            _breach_scan_checks,
            _mv_refresh_checks,
            _restore_checks,
            _mfa_checks,
            _migration_checks,
            _version_checks,
            _audit_event_checks,
        )
    )


def cron_job_checks() -> list[ComplianceCheck]:
    """Gebuendelte Last-Run-Checks der Hintergrundjobs (Refs #977).

    Teilmenge von :func:`aggregate_checks` — nur die fuenf per
    systemd-Timer laufenden Jobs (Backup, Retention, Snapshots,
    Breach-Scan, MV-Refresh). Der Restore-Test bleibt bewusst aussen vor:
    er ist ein manueller Operator-Workflow, kein Cron-Job, und gehoert
    aufs Compliance-Dashboard.

    Wiederverwendung der bestehenden Helper haelt /system/-Uebersicht und
    Compliance-Dashboard DRY. Tuple wird zur Aufrufzeit gebildet, damit
    ``patch.object`` in Tests greift.
    """
    return _run_helpers(
        (
            _backup_checks,
            _retention_checks,
            _snapshot_checks,
            _breach_scan_checks,
            _mv_refresh_checks,
        )
    )


__all__ = [
    "ComplianceCheck",
    "ComplianceStatus",
    "PROCESSING_ACTIVITIES",
    "ROLE_MAX_SENSITIVITY",
    "SENSITIVITY_RANK",
    "_CRITICAL_AUDIT_ACTIONS",
    "_PRIVILEGED_ROLES",
    "_app_superuser_checks",
    "_audit_event_checks",
    "_backup_checks",
    "_breach_scan_checks",
    "_clamav_checks",
    "_mv_refresh_checks",
    "_db_role_admin_check",
    "_db_role_attribute_check",
    "_db_role_checks",
    "_mfa_checks",
    "_migration_checks",
    "_restore_checks",
    "_retention_checks",
    "_snapshot_checks",
    "_validate_webhook_url",
    "_version_checks",
    "aggregate_checks",
    "cron_job_checks",
    "allowed_sensitivities_for_user",
    "count_clients_in_bucket",
    "detect_anonymous_login_bursts",
    "detect_distributed_login_attack",
    "detect_failed_login_burst",
    "detect_mass_client_destruction",
    "detect_mass_delete",
    "detect_mass_export",
    "effective_sensitivity",
    "get_activity",
    "get_processing_activities",
    "get_visible_attachment_or_404",
    "get_visible_event_or_404",
    "is_k_anonymous",
    "k_anonymize_client",
    "record_finding",
    "record_system_finding",
    "run_all_detections",
    "run_system_detections",
    "user_can_see_document_type",
    "user_can_see_event",
    "user_can_see_field",
]
