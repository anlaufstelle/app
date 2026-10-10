"""Django-User-Flag-Check: kein App-``User`` hat ``is_superuser=True`` (Refs #1297).

Friert die mit #1271 (PR #1296) etablierte Invariante zur Laufzeit ein und
macht sie fuer Betreiber im ``/system/``-Compliance-Dashboard sichtbar: kein
Anwendungs-``User`` traegt Djangos ``is_superuser``-Flag. Seit #1271 setzt
kein Bootstrap-/Seed-Pfad das Flag mehr; Autorisierung laeuft ausschliesslich
ueber die Rolle (``is_super_admin``/``is_facility_admin``) plus Sudo-Mode, das
Django-Flag trifft keine Entscheidung mehr.

Bewusst eine ANDERE Ebene als die verwandten Checks — hier abgegrenzt:

- :mod:`core.services.compliance.db_roles` (Refs #902/#919) prueft die
  **PostgreSQL**-Rollen-Attribute (``rolsuper``/``rolbypassrls`` der DB-User
  ``anlaufstelle``/``anlaufstelle_admin``) — die Datenbank-Rollenebene, nicht
  das Django-Model.
- #793 (Health-Endpoint ``NOSUPERUSER``) betrifft ebenfalls die
  **Postgres**-Rolle.

Dieser Check betrachtet ausschliesslich das Django-``User.is_superuser``-Feld
(Anwendungsebene).
"""

from __future__ import annotations

from django.utils.translation import gettext as _

from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus


def _app_superuser_checks() -> list[ComplianceCheck]:
    """Kein App-``User`` traegt Djangos ``is_superuser``-Flag (Refs #1297)."""
    from core.models.user import User

    offenders = list(User.objects.filter(is_superuser=True).order_by("username").values_list("username", flat=True))
    if not offenders:
        return [
            ComplianceCheck(
                key="app_user_no_django_superuser",
                label=_("App-User kein Django-Superuser"),  # pragma: no mutate
                category=ComplianceCategory.PERMISSIONS,  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=_("Kein App-User trägt Djangos is_superuser-Flag."),  # pragma: no mutate
            )
        ]
    listing = ", ".join(offenders[:5])
    if len(offenders) > 5:
        listing += _(" (+ %(len_offenders_5)s weitere)") % {"len_offenders_5": len(offenders) - 5}
    return [
        ComplianceCheck(
            key="app_user_no_django_superuser",
            label=_("App-User kein Django-Superuser"),  # pragma: no mutate
            category=ComplianceCategory.PERMISSIONS,  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=_("%(len_offenders)s App-User mit is_superuser=True.")
            % {"len_offenders": len(offenders)},  # pragma: no mutate
            detail=listing,
            action_hint=(
                _(
                    "is_superuser dieser User auf False setzen — Autorisierung läuft über die Rolle, "
                    "nie über das Django-Flag (Refs #1271)."
                )
            ),  # pragma: no mutate
        )
    ]
