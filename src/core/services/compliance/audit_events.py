"""Kritische Audit-Events der letzten 24h (Refs #919, #958-M3)."""

from __future__ import annotations

from datetime import timedelta

from core.models import AuditLog
from core.services.compliance import _clock
from core.services.compliance._types import (
    _CRITICAL_AUDIT_ACTIONS,
    ComplianceCheck,
    ComplianceStatus,
)


def _audit_event_checks() -> list[ComplianceCheck]:
    """Kritische Audit-Events der letzten 24h."""
    cutoff = _clock.now() - timedelta(hours=24)
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
                message=f"{count} kritische Event(s) in 24h.",  # pragma: no mutate
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
            message=f"{count} kritische Event(s) in 24h — moeglicher Vorfall.",  # pragma: no mutate
            detail="Im System-Audit-Log dringend pruefen.",
            action_hint="Vorfall-Triage starten, ggf. DSGVO Art. 33 Meldung an Aufsichtsbehoerde.",  # pragma: no mutate
        )
    ]
