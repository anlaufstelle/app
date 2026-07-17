"""MFA-Compliance-Quote koppelt den Nenner an die erzwungenen Rollen (COD-12).

Die MFA-Quote (``compliance._mfa_checks``) zaehlte ueber ein separates
``_PRIVILEGED_ROLES``-Tupel (super_admin/facility_admin/LEAD). Der Nenner ist
jetzt an die gemeinsame Quelle ``mfa_enforced_roles()`` (``core.models.user``)
gebunden: gezaehlt wird genau die Menge, die auch erzwungen wird — kein
Duplikat-Tupel, keine dauerhaft unschliessbare Luecke. Welche Rollen erzwungen
werden, steuern die konfigurierbaren Sets ``MFA_ENFORCED_ROLES_IMMEDIATE`` /
``MFA_ENFORCED_ROLES_GRACE`` (Refs #1639); LEAD ist per Default enthalten.
Refs #1478 (COD-12).
"""

from __future__ import annotations

import pytest

from core.models.user import User, mfa_enforced_roles
from core.services import compliance


def _make_user(facility, role, username):
    return User.objects.create_user(
        username=username,
        password="anlaufstelle2026",
        facility=facility,
        role=role,
    )


@pytest.mark.django_db
class TestMfaQuotaDenominatorCoupling:
    def test_unenforced_lead_not_counted_when_removed_from_set(self, settings, facility):
        """LEAD aus dem Grace-Set genommen: nicht rollen-erzwungen, darf die
        Quote nicht belasten — ohne weitere erzwungene User bleibt der Check
        UNKNOWN."""
        settings.MFA_ENFORCED_ROLES_GRACE = ("staff", "assistant")
        _make_user(facility, User.Role.LEAD, "lead_no_mfa")

        checks = compliance._mfa_checks()
        assert checks[0].status == compliance.ComplianceStatus.UNKNOWN

    def test_lead_counted_by_default(self, settings, facility):
        """Default (LEAD im Grace-Set): LEAD ist rollen-erzwungen und zaehlt in
        die Quote — ein LEAD ohne MFA drueckt sie konsistent (0 % => CRITICAL)."""
        _make_user(facility, User.Role.LEAD, "lead_no_mfa")

        checks = compliance._mfa_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL

    def test_grace_enforced_staff_is_counted(self, settings, facility):
        """staff/assistant sind grace-erzwungen und gehoeren in den Nenner."""
        _make_user(facility, User.Role.STAFF, "staff_no_mfa")

        checks = compliance._mfa_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL

    def test_denominator_tracks_grace_set(self, settings):
        """Die gemeinsame Rollenquelle enthaelt LEAD genau dann, wenn LEAD im
        konfigurierten Grace-Set steht — der Nenner haengt an keiner
        Duplikat-Liste."""
        settings.MFA_ENFORCED_ROLES_GRACE = ("staff", "assistant")
        assert User.Role.LEAD not in mfa_enforced_roles()
        settings.MFA_ENFORCED_ROLES_GRACE = ("lead", "staff", "assistant")
        assert User.Role.LEAD in mfa_enforced_roles()
