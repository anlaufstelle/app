"""Tests for the ``unlock`` management command (Refs #867).

Der Command hebt die Account-Sperre eines Users auf und schreibt einen
``LOGIN_UNLOCK``-AuditLog mit ``detail.unlocked_by=None`` (CLI-Kontext —
es gibt keinen anderen User, der den Unlock vornimmt).
"""

import pytest
from django.core.management import CommandError, call_command

from core.models import AuditLog, User
from core.services import login_lockout


@pytest.mark.django_db
class TestUnlockCommand:
    """Refs #867: ``manage.py unlock <username>`` schreibt LOGIN_UNLOCK-Audit.

    Der Command ist die CLI-Variante zur Django-Admin-Action ``unlock``.
    Das Detail-Feld unterscheidet beide Kontexte — bei CLI ist
    ``unlocked_by=None``, bei der Admin-Action steht dort die UUID des
    handelnden Admins.
    """

    def test_unlock_user_with_active_lock(self, facility):
        """Happy-Path: User mit Fehlversuchen wird entsperrt; AuditLog
        ``LOGIN_UNLOCK`` mit ``unlocked_by=None`` ist geschrieben.
        """
        user = User.objects.create_user(
            username="locked_user",
            facility=facility,
            password="anything",
            role=User.Role.STAFF,
        )

        # Vor dem Unlock: ein paar Login-Failed-Audits anlegen, damit
        # ``is_locked`` ohne den Unlock True liefern wuerde.
        for _ in range(login_lockout.LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"message": "Failed-Login-Sim", "username": user.username},
            )
        assert login_lockout.is_locked(user) is True, "Vorbedingung des Tests: User muss vor dem Unlock gesperrt sein."

        # Command ausfuehren.
        call_command("unlock", "locked_user")

        # Audit-Eintrag pruefen.
        unlocks = AuditLog.objects.filter(user=user, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlocks.count() == 1, f"Erwartet genau 1 LOGIN_UNLOCK-Audit, gefunden: {unlocks.count()}."
        unlock_entry = unlocks.first()
        # CLI-Kontext -> ``unlocked_by`` ist None (vs. Django-Admin-Action,
        # die die UUID des admins liefert).
        assert unlock_entry.detail.get("unlocked_by") is None, (
            f"detail.unlocked_by sollte None sein im CLI-Kontext, erhalten: {unlock_entry.detail!r}"
        )
        # Facility wird vom Service automatisch aus user.facility gezogen.
        assert unlock_entry.facility == facility
        assert unlock_entry.target_id == str(user.pk)

        # Effekt: ``is_locked`` ist nach dem Unlock False.
        assert login_lockout.is_locked(user) is False, (
            "Nach dem Unlock muss is_locked False liefern (Audit-Cutoff greift)."
        )

    def test_unknown_username_raises_command_error(self):
        """Refs #867: Unbekannter Username -> CommandError mit hilfreicher
        Fehlermeldung. Der Test darf KEINEN AuditLog hinterlassen.
        """
        before = AuditLog.objects.count()

        with pytest.raises(CommandError) as exc:
            call_command("unlock", "ghost_user")

        # Fehlermeldung referenziert den (unbekannten) Username.
        assert "ghost_user" in str(exc.value)

        # Keine Seiteneffekte (kein AuditLog).
        assert AuditLog.objects.count() == before, "CommandError-Zweig darf keinen LOGIN_UNLOCK schreiben."

    def test_unlock_super_admin_writes_null_facility_audit(self, super_admin_user):
        """Refs #867: Super-Admin hat ``facility=None`` — der LOGIN_UNLOCK-
        Audit wird mit ``facility=NULL`` geschrieben (Migration 0083 + 0085
        erlauben das ueber WITH-CHECK).
        """
        call_command("unlock", super_admin_user.username)

        unlocks = AuditLog.objects.filter(user=super_admin_user, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlocks.count() == 1
        entry = unlocks.first()
        assert entry.facility is None, "Super-Admin-Unlock muss facility=NULL liefern (Persona ohne Facility-Bindung)."
        assert entry.detail.get("unlocked_by") is None
