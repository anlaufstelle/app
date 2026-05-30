"""Tests fuer Lockout-Recovery-Flows (Refs #869).

Drei Pfade jenseits CLI/Admin-Action:
  - B1: Password-Reset-Erfolg hebt Lockout automatisch auf.
  - B2: Dedizierter Recovery-Token-Flow per E-Mail (nur LOGIN_UNLOCK).
  - C:  MFA-Backup-Code als Recovery (setzt aktive MFA voraus).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import AuditLog
from core.services.security import generate_backup_codes, is_locked


def _lock_account(user, count: int = 10):
    """Hilfsfunktion: legt LOCKOUT_THRESHOLD LOGIN_FAILED-Eintraege an."""
    for _ in range(count):
        AuditLog.objects.create(
            user=user,
            facility=user.facility,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"username": user.username},
        )


@pytest.mark.django_db
class TestPasswordResetUnlocksAccount:
    """B1: Erfolgreicher Password-Reset schreibt LOGIN_UNLOCK + entsperrt Account."""

    def test_password_reset_confirm_writes_login_unlock(self, client, staff_user):
        _lock_account(staff_user)
        assert is_locked(staff_user) is True

        staff_user.email = "staff@example.org"
        staff_user.save(update_fields=["email"])

        uid = urlsafe_base64_encode(force_bytes(staff_user.pk))
        token = default_token_generator.make_token(staff_user)

        # Django's PasswordResetConfirmView leitet GET auf set-password/ um (token in session)
        url = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})
        response = client.get(url, follow=True)
        assert response.status_code == 200

        # POST das neue Passwort auf die set-password/-URL (kommt aus der Redirect-Kette)
        new_password = "NewSecur3Pass!2026"
        response = client.post(
            response.redirect_chain[-1][0] if response.redirect_chain else url,
            {"new_password1": new_password, "new_password2": new_password},
            follow=True,
        )
        assert response.status_code == 200

        unlock_entry = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).first()
        assert unlock_entry is not None, "Password-Reset muss LOGIN_UNLOCK schreiben"
        assert unlock_entry.detail.get("trigger") == "password_reset"
        assert is_locked(staff_user) is False

    def test_password_reset_on_unlocked_account_still_logs_unlock(self, client, staff_user):
        """Auch ohne aktiven Lockout schreibt der Reset einen LOGIN_UNLOCK-Eintrag.

        Vereinfacht die Logik: ein erfolgreicher Reset ist immer auch ein
        Vertrauensanker — ein vorheriger Lockout im naechsten Fenster soll
        nicht hinterhereilen koennen.
        """
        staff_user.email = "staff@example.org"
        staff_user.save(update_fields=["email"])
        uid = urlsafe_base64_encode(force_bytes(staff_user.pk))
        token = default_token_generator.make_token(staff_user)
        url = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})
        response = client.get(url, follow=True)
        new_password = "AnotherStr0ng!Pwd"
        client.post(
            response.redirect_chain[-1][0] if response.redirect_chain else url,
            {"new_password1": new_password, "new_password2": new_password},
        )
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).exists()


@pytest.mark.django_db
class TestRecoveryTokenFlow:
    """B2: Dedizierter Lockout-Recovery-Token via /account/recovery/."""

    def test_get_recovery_request_form(self, client):
        response = client.get(reverse("core:lockout_recovery_request"))
        assert response.status_code == 200
        assert b'name="email"' in response.content

    def test_post_known_email_sends_recovery_link(self, client, staff_user):
        staff_user.email = "lockedout@example.org"
        staff_user.save(update_fields=["email"])
        _lock_account(staff_user)

        response = client.post(
            reverse("core:lockout_recovery_request"),
            {"email": "lockedout@example.org"},
        )
        # Anti-Enumeration: gleiche Response wie bei unbekannter E-Mail.
        assert response.status_code == 302
        assert response.url.endswith("/account/recovery/sent/")

        # E-Mail wurde verschickt, AuditLog steht
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert "lockedout@example.org" in message.to
        assert "/account/recovery/confirm/" in message.body

    def test_post_unknown_email_renders_same_response_no_mail(self, client, db):
        response = client.post(
            reverse("core:lockout_recovery_request"),
            {"email": "ghost@example.org"},
        )
        assert response.status_code == 302
        assert response.url.endswith("/account/recovery/sent/")
        # Kein Mail-Versand fuer unbekannte Adresse — Anti-Enumeration.
        assert len(mail.outbox) == 0

    def test_recovery_confirm_unlocks_account(self, client, staff_user):
        from core.services.security import build_recovery_token

        staff_user.email = "locked@example.org"
        staff_user.save(update_fields=["email"])
        _lock_account(staff_user)
        token = build_recovery_token(staff_user)

        response = client.get(
            reverse("core:lockout_recovery_confirm", kwargs={"token": token}),
            follow=True,
        )
        assert response.status_code == 200
        assert is_locked(staff_user) is False
        unlock_entry = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).first()
        assert unlock_entry is not None
        assert unlock_entry.detail.get("trigger") == "recovery_token"

    def test_recovery_confirm_with_invalid_token_404s(self, client, staff_user):
        response = client.get(
            reverse(
                "core:lockout_recovery_confirm",
                kwargs={"token": "obviously-invalid-token-xxxx"},
            )
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestBackupCodeRecovery:
    """C: MFA-Backup-Code entsperrt den Account."""

    def test_get_backup_code_form(self, client):
        response = client.get(reverse("core:lockout_recovery_backup_code"))
        assert response.status_code == 200
        assert b'name="username"' in response.content
        assert b'name="backup_code"' in response.content

    def test_valid_backup_code_unlocks_and_consumes(self, client, staff_user):
        codes = generate_backup_codes(staff_user)
        _lock_account(staff_user)
        assert is_locked(staff_user) is True

        response = client.post(
            reverse("core:lockout_recovery_backup_code"),
            {"username": staff_user.username, "backup_code": codes[0]},
        )
        assert response.status_code == 302
        assert response.url.endswith("/login/?recovered=1")
        assert is_locked(staff_user) is False

        # Audit: backup_codes_used + login_unlock
        actions = list(AuditLog.objects.filter(user=staff_user).values_list("action", flat=True))
        assert AuditLog.Action.LOGIN_UNLOCK in actions
        assert AuditLog.Action.BACKUP_CODES_USED in actions

    def test_invalid_backup_code_keeps_lockout(self, client, staff_user):
        generate_backup_codes(staff_user)
        _lock_account(staff_user)

        response = client.post(
            reverse("core:lockout_recovery_backup_code"),
            {"username": staff_user.username, "backup_code": "definitely-wrong"},
        )
        assert response.status_code == 200
        assert b"Code ung" in response.content  # "Code ungültig" (UTF-8)
        assert is_locked(staff_user) is True

    def test_unknown_user_renders_same_invalid_response(self, client, db):
        # Anti-Enumeration: gleiche Antwort wie falscher Code.
        response = client.post(
            reverse("core:lockout_recovery_backup_code"),
            {"username": "ghost", "backup_code": "anycode"},
        )
        assert response.status_code == 200
        assert b"Code ung" in response.content
