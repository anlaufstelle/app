"""Compliance-Aggregator-Service fuer das Compliance-Dashboard (Refs #919).

Refs #958 (M-3) — vorher war alles in einem 589-LoC-Modul ``services/compliance.py``;
jetzt thematisch gesplittet (db_roles, backup, clamav, retention, mfa, system_info,
audit_events), analog zum bereits erfolgten ``services/events/``-Subpackage.

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
from core.services.compliance.audit_events import _audit_event_checks
from core.services.compliance.backup import _backup_checks, _restore_checks
from core.services.compliance.breach_detection import (
    _validate_webhook_url,
    detect_failed_login_burst,
    detect_mass_delete,
    detect_mass_export,
    record_finding,
    run_all_detections,
)
from core.services.compliance.clamav import _clamav_checks
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


def aggregate_checks() -> list[ComplianceCheck]:
    """Sammle alle Compliance-Checks fuer das Dashboard.

    Defensiv: jeder Helper schluckt seine eigenen Exceptions und liefert
    bei Fehler einen ``unknown``-ComplianceCheck mit der Exception als
    detail. Damit kippt ein Single-Failure das Dashboard nicht.

    Helper werden ueber den Modul-Namespace aufgerufen (``_db_role_checks``
    etc.), damit Tests ``patch.object(compliance, "_db_role_checks", ...)``
    weiterhin nutzen koennen.
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


__all__ = [
    "ComplianceCheck",
    "ComplianceStatus",
    "PROCESSING_ACTIVITIES",
    "ROLE_MAX_SENSITIVITY",
    "SENSITIVITY_RANK",
    "_CRITICAL_AUDIT_ACTIONS",
    "_PRIVILEGED_ROLES",
    "_audit_event_checks",
    "_backup_checks",
    "_clamav_checks",
    "_db_role_admin_check",
    "_db_role_attribute_check",
    "_db_role_checks",
    "_mfa_checks",
    "_migration_checks",
    "_restore_checks",
    "_retention_checks",
    "_validate_webhook_url",
    "_version_checks",
    "aggregate_checks",
    "allowed_sensitivities_for_user",
    "count_clients_in_bucket",
    "detect_failed_login_burst",
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
    "run_all_detections",
    "user_can_see_document_type",
    "user_can_see_event",
    "user_can_see_field",
]
