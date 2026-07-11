"""MFA-Quote bei privilegierten Rollen (Refs #919, #958-M3)."""

from __future__ import annotations

from django.db.models import Count

from core.models.user import User
from core.services.compliance._types import _PRIVILEGED_ROLES, ComplianceCheck, ComplianceStatus


def _mfa_checks() -> list[ComplianceCheck]:
    """MFA-Quote bei privilegierten Rollen (super_admin/facility_admin/leitung)."""
    # Zaehlt ueber dieselbe Tabelle wie django-otps ``TOTPDevice``; das Proxy-
    # Modell (Refs #1362) ist die App-weite Zugriffsschicht.
    from core.models import EncryptedTOTPDevice as TOTPDevice

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
