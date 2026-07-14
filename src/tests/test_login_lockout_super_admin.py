"""L14 (Refs #1375) — super_admin ist vom Login-Lockout ausgenommen (im Service).

Bisher prüfte nur die View (``views/system/lockouts.py``) die Rolle und schloss
super_admin aus Liste + Unlock-Aktion aus; der Service ``login_lockout`` selbst
kannte keine Rolle. Damit war der Zustand inkonsistent: ``is_locked`` im
Login-Pfad (``views/auth.py``) konnte einen super_admin sehr wohl sperren —
und die UI-Unlock-Aktion schloss genau diese Rolle aus (nur CLI-Recovery übrig).
Ein Angreifer konnte so das Break-Glass-Konto gezielt aussperren (DoS auf die
Recovery-Rolle).

Die Ausnahme wird jetzt zentral im Service durchgesetzt: super_admin ist NICHT
lockbar (``is_locked`` -> False) und ``unlock`` ist für super_admin ein No-Op
(nichts zu entsperren). Der Username-Ratelimit (10/h) in ``views/auth.py`` deckt
Brute-Force weiterhin ab.
"""

from __future__ import annotations

import pytest

from core.models import AuditLog
from core.services.security import LOCKOUT_THRESHOLD, is_locked, unlock

pytestmark = pytest.mark.django_db


def _seed_failed_logins(facility, user, count):
    for _ in range(count):
        AuditLog.objects.create(
            facility=facility,
            user=user,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"username": user.username},
        )


class TestSuperAdminLockoutExemption:
    def test_super_admin_never_locked(self, facility, super_admin_user):
        _seed_failed_logins(facility, super_admin_user, LOCKOUT_THRESHOLD + 5)
        assert is_locked(super_admin_user) is False

    def test_super_admin_never_locked_with_ip(self, facility, super_admin_user):
        _seed_failed_logins(facility, super_admin_user, LOCKOUT_THRESHOLD + 5)
        assert is_locked(super_admin_user, ip_address="203.0.113.7") is False

    def test_unlock_super_admin_is_noop(self, facility, super_admin_user, staff_user):
        result = unlock(super_admin_user, unlocked_by=staff_user)
        assert result is None
        assert not AuditLog.objects.filter(user=super_admin_user, action=AuditLog.Action.LOGIN_UNLOCK).exists()

    def test_regular_user_still_lockable(self, facility, staff_user):
        _seed_failed_logins(facility, staff_user, LOCKOUT_THRESHOLD)
        assert is_locked(staff_user) is True

    def test_unlock_regular_user_still_writes_audit(self, facility, staff_user, admin_user):
        entry = unlock(staff_user, unlocked_by=admin_user)
        assert entry is not None
        assert entry.action == AuditLog.Action.LOGIN_UNLOCK
