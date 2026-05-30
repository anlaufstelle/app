"""Unit-Tests fuer den Account-Lockout-Service nach Fehlanmeldungen.

Refs Matrix AUD-SEC-MFA-02 (Issue #926, Master #922).

Verifiziert den IST-Zustand des ``core.services.security.login_lockout``-Services:
Nach N fehlgeschlagenen Logins (LOCKOUT_THRESHOLD, default 10) innerhalb des
Zeitfensters (LOCKOUT_WINDOW) sperrt ``is_locked()`` den Account. Ein
``unlock()``-Eintrag (Action: ``LOGIN_UNLOCK``) hebt die Sperre auf, indem
nachfolgende ``is_locked()``-Aufrufe alle LOGIN_FAILED-Eintraege ignorieren,
deren Timestamp <= dem Unlock-Eintrag liegt. Sperren sind per User getrennt
(separate Zaehler, kein facility-weiter Lockout).

Der Service ist in ``src/tests/test_auth.py`` bereits durch
``TestLoginLockoutService`` und ``TestLoginLockoutIntegration`` abgedeckt;
diese Datei dokumentiert die identische Erwartungslage **eigenstaendig
unter der Matrix-TC-ID**, damit Audit-Tools den Test der TC zuordnen.
"""

import pytest

from core.models import AuditLog
from core.services.security import is_locked, unlock


@pytest.mark.django_db
class TestMfaLockout:
    """AUD-SEC-MFA-02: Account-Lockout nach N fehlgeschlagenen Logins."""

    @staticmethod
    def _failed_login(user):
        """Hilfsfunktion: legt einen LOGIN_FAILED-AuditLog-Eintrag fuer ``user`` an."""
        return AuditLog.objects.create(
            facility=user.facility,
            user=user,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"username": user.username},
        )

    def test_n_failed_logins_locks_account(self, staff_user):
        """10x LOGIN_FAILED erreicht den default-Threshold → ``is_locked() is True``."""
        # LOCKOUT_THRESHOLD = 10 (siehe ``core/services/login_lockout.py``).
        for _ in range(10):
            self._failed_login(staff_user)
        assert is_locked(staff_user) is True

    def test_lockout_below_threshold_stays_unlocked(self, staff_user):
        """9 Fehlversuche bleiben unter dem Threshold → keine Sperre."""
        for _ in range(9):
            self._failed_login(staff_user)
        assert is_locked(staff_user) is False

    def test_unlock_resets_counter(self, staff_user, admin_user):
        """Admin-Unlock setzt den Zaehler zurueck — frueher gesperrt, nun frei.

        Service-Doku in ``login_lockout.py``:
        "Subsequent is_locked(user) calls ignore LOGIN_FAILED entries
        with timestamp <= this_entry.timestamp."
        """
        for _ in range(10):
            self._failed_login(staff_user)
        assert is_locked(staff_user) is True
        unlock(staff_user, unlocked_by=admin_user)
        # Direkt nach unlock muss is_locked() False sein — der Service
        # filtert alle LOGIN_FAILED-Eintraege mit timestamp <= unlock-entry.
        assert is_locked(staff_user) is False

    def test_lockout_is_per_user(self, staff_user, lead_user):
        """Lockout-Zaehler sind pro User getrennt — staff locked, lead frei."""
        for _ in range(10):
            self._failed_login(staff_user)
        assert is_locked(staff_user) is True
        # lead_user hat keine eigenen LOGIN_FAILED-Eintraege → nicht gesperrt.
        assert is_locked(lead_user) is False
