"""Kritische Audit-Events der letzten 24h (Refs #919, #958-M3)."""

from __future__ import annotations

from datetime import timedelta

from django.utils.translation import gettext as _

from core.models import AuditLog
from core.services.compliance import _clock
from core.services.compliance._types import (
    _CRITICAL_AUDIT_ACTIONS,
    ComplianceCategory,
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
                label=_("Kritische Audit-Events (24h)"),  # pragma: no mutate
                category=ComplianceCategory.AUDIT,  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=_("Keine kritischen Events in den letzten 24h."),  # pragma: no mutate
                detail=_("Gemonitorte Aktionen: %(actions)s")
                % {"actions": ", ".join(a.value for a in _CRITICAL_AUDIT_ACTIONS)},
            )
        ]
    if count <= 5:
        return [
            ComplianceCheck(
                key="critical_audit_events_24h",
                label=_("Kritische Audit-Events (24h)"),  # pragma: no mutate
                category=ComplianceCategory.AUDIT,  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=_("%(count)s kritische Event(s) in 24h.") % {"count": count},  # pragma: no mutate
                detail=(
                    _(
                        "Im System-Audit-Log (/system/audit/) auf SECURITY_VIOLATION / MFA_FAILED / "
                        "USER_DEACTIVATED filtern."
                    )
                ),
                action_hint=_(
                    "Pro Eintrag den Kontext prüfen, ggf. Issue mit Label security eröffnen."
                ),  # pragma: no mutate  # noqa: E501
            )
        ]
    return [
        ComplianceCheck(
            key="critical_audit_events_24h",
            label=_("Kritische Audit-Events (24h)"),  # pragma: no mutate
            category=ComplianceCategory.AUDIT,  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=_("%(count)s kritische Event(s) in 24h — möglicher Vorfall.")
            % {"count": count},  # pragma: no mutate
            detail=_("Im System-Audit-Log dringend prüfen."),
            action_hint=_(
                "Vorfall-Triage starten, ggf. DSGVO Art. 33 Meldung an Aufsichtsbehörde."
            ),  # pragma: no mutate
        )
    ]
