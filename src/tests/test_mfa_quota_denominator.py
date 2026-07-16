"""MFA-Compliance-Quote koppelt den Nenner an die erzwungenen Rollen (COD-12).

Die MFA-Quote (``compliance._mfa_checks``) zaehlte ueber ein separates
``_PRIVILEGED_ROLES``-Tupel (super_admin/facility_admin/LEAD). LEAD stand damit
im Quoten-NENNER, wurde aber nur dann rollenbasiert MFA-erzwungen, wenn
``MFA_ENFORCE_INCLUDE_LEAD`` aktiv ist — bei ausgeschaltetem Flag ein
Datensatz, der die Quote dauerhaft druecken kann, ohne dass die App je MFA
verlangt (unschliessbare Luecke). Umgekehrt fehlten die grace-erzwungenen
Rollen staff/assistant im Nenner.

Der Nenner ist jetzt an die gemeinsame Quelle ``mfa_enforced_roles()``
(``core.models.user``) gebunden: gezaehlt wird genau die Menge, die auch
erzwungen wird. LEAD folgt damit exakt dem ``MFA_ENFORCE_INCLUDE_LEAD``-Flag.
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
    def test_unenforced_lead_not_counted_when_flag_off(self, settings, facility):
        """Flag AUS: LEAD ist nicht rollen-erzwungen und darf die Quote nicht
        belasten — ohne weitere erzwungene User bleibt der Check UNKNOWN."""
        settings.MFA_ENFORCE_INCLUDE_LEAD = False
        _make_user(facility, User.Role.LEAD, "lead_no_mfa")

        checks = compliance._mfa_checks()
        assert checks[0].status == compliance.ComplianceStatus.UNKNOWN

    def test_lead_counted_when_flag_on(self, settings, facility):
        """Flag AN: LEAD ist rollen-erzwungen und zaehlt in die Quote — ein
        LEAD ohne MFA drueckt sie damit konsistent (0 % => CRITICAL)."""
        settings.MFA_ENFORCE_INCLUDE_LEAD = True
        _make_user(facility, User.Role.LEAD, "lead_no_mfa")

        checks = compliance._mfa_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL

    def test_grace_enforced_staff_is_counted(self, settings, facility):
        """staff/assistant sind grace-erzwungen und gehoeren jetzt in den
        Nenner — vorher fehlten sie ganz."""
        settings.MFA_ENFORCE_INCLUDE_LEAD = False
        _make_user(facility, User.Role.STAFF, "staff_no_mfa")

        checks = compliance._mfa_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL

    def test_denominator_tracks_include_lead_flag(self, settings):
        """Die gemeinsame Rollenquelle enthaelt LEAD genau dann, wenn das Flag
        gesetzt ist — der Quoten-Nenner haengt an keiner Duplikat-Liste."""
        settings.MFA_ENFORCE_INCLUDE_LEAD = False
        assert User.Role.LEAD not in mfa_enforced_roles()
        settings.MFA_ENFORCE_INCLUDE_LEAD = True
        assert User.Role.LEAD in mfa_enforced_roles()
