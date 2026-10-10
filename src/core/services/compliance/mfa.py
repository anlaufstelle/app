"""MFA-Quote bei privilegierten Rollen (Refs #919, #958-M3)."""

from __future__ import annotations

from django.db.models import Count
from django.utils.translation import gettext as _

from core.models.user import User, mfa_enforced_roles
from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus


def _mfa_checks() -> list[ComplianceCheck]:
    """MFA-Quote der MFA-pflichtigen Rollen.

    Refs #1478 (COD-12): Der Nenner ist an die gemeinsame Quelle
    ``mfa_enforced_roles()`` gebunden (kein separates ``_PRIVILEGED_ROLES``-
    Duplikat mehr) — gezaehlt wird genau die Rollenmenge, die auch tatsaechlich
    MFA-erzwungen ist. Der Nenner folgt damit exakt den konfigurierbaren Sets
    ``MFA_ENFORCED_ROLES_IMMEDIATE``/``MFA_ENFORCED_ROLES_GRACE`` (Refs #1639):
    eine Rolle im Quoten-Nenner ist immer auch eine erzwungene Rolle, sodass
    keine dauerhaft unschliessbare Quoten-Luecke entstehen kann.
    """
    # Zaehlt ueber dieselbe Tabelle wie django-otps ``TOTPDevice``; das Proxy-
    # Modell (Refs #1362) ist die App-weite Zugriffsschicht.
    from core.models import EncryptedTOTPDevice as TOTPDevice

    enforced_roles = mfa_enforced_roles()

    # Anzahl User pro Rolle und Anzahl mit confirmed TOTPDevice.
    privileged_total = User.objects.filter(role__in=enforced_roles, is_active=True).count()
    if privileged_total == 0:
        return [
            ComplianceCheck(
                key="mfa_privileged_quote",
                label=_("MFA-Quote (MFA-pflichtige Rollen)"),  # pragma: no mutate
                category=ComplianceCategory.MFA,  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message=_("Keine MFA-pflichtigen User vorhanden."),  # pragma: no mutate
            )
        ]
    privileged_with_mfa = (
        TOTPDevice.objects.filter(confirmed=True, user__role__in=enforced_roles, user__is_active=True)
        .values("user_id")
        .annotate(n=Count("id"))
        .count()
    )
    percent = round(privileged_with_mfa / privileged_total * 100, 1)
    detail = _("%(privileged_with_mfa)s von %(privileged_total)s MFA-pflichtigen Usern haben MFA aktiv.") % {
        "privileged_with_mfa": privileged_with_mfa,
        "privileged_total": privileged_total,
    }
    if percent >= 100:
        status = ComplianceStatus.OK
        message = _("%(percent)s %% der MFA-pflichtigen Rollen haben MFA aktiv.") % {"percent": f"{percent:g}"}
        hint = None
    elif percent >= 80:
        status = ComplianceStatus.WARNING
        message = _("%(percent)s %% MFA-Quote — Lücke schließen.") % {"percent": f"{percent:g}"}
        hint = _("Verbleibende User per Admin-UI zur MFA-Aktivierung anhalten.")
    else:
        status = ComplianceStatus.CRITICAL
        message = _("Nur %(percent)s %% der MFA-pflichtigen Rollen haben MFA — Risiko.") % {"percent": f"{percent:g}"}
        hint = _("mfa_enforced_facility_wide aktivieren oder MFA pro User erzwingen.")
    return [
        ComplianceCheck(
            key="mfa_privileged_quote",
            label=_("MFA-Quote (MFA-pflichtige Rollen)"),  # pragma: no mutate
            category=ComplianceCategory.MFA,  # pragma: no mutate
            status=status,
            message=message,
            detail=detail,
            action_hint=hint,
        )
    ]
