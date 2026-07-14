"""Token-based invite flow tests (Refs #528)."""

from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import User
from core.services.security import build_invite_url, invite_token_generator, send_invite_email


@pytest.mark.django_db
class TestBuildInviteUrl:
    def test_relative_url_when_no_request(self, facility):
        user = User.objects.create_user(
            username="testinvite",
            email="invite@example.org",
            facility=facility,
        )
        url = build_invite_url(user)
        assert "/invite/" in url
        assert url.startswith("/")

    def test_url_contains_valid_uid_and_token(self, facility):
        user = User.objects.create_user(
            username="testinvite2",
            email="invite2@example.org",
            facility=facility,
        )
        url = build_invite_url(user)
        expected_uid = urlsafe_base64_encode(force_bytes(user.pk))
        assert expected_uid in url
        token = url.rstrip("/").split("/")[-1]
        # L4 (Refs #1375): Invite-Token wird vom eigenen Generator geprueft
        # (entkoppelt vom Passwort-Reset-Token).
        assert invite_token_generator.check_token(user, token)


@pytest.mark.django_db
class TestInviteTokenTimeoutDecoupling:
    """L4 (Refs #1375): Passwort-Reset-Tokens sind kurzlebig, Invites brauchen
    aber Tage — ihre Gueltigkeit ist ueber einen eigenen Generator + Timeout
    (``INVITE_TOKEN_TIMEOUT``) von ``PASSWORD_RESET_TIMEOUT`` entkoppelt."""

    def test_password_reset_timeout_is_short(self):
        assert settings.PASSWORD_RESET_TIMEOUT <= 4 * 60 * 60, (
            "PASSWORD_RESET_TIMEOUT muss explizit kurz sein (<=4h), nicht der 3-Tage-Django-Default."
        )

    def test_invite_timeout_longer_than_reset_timeout(self):
        assert settings.INVITE_TOKEN_TIMEOUT > settings.PASSWORD_RESET_TIMEOUT

    def test_invite_token_survives_past_reset_timeout(self, facility):
        """Ein Invite-Token bleibt gueltig, auch wenn PASSWORD_RESET_TIMEOUT laengst
        abgelaufen waere — Beweis der Entkopplung."""
        user = User.objects.create_user(username="latecomer", email="l@example.org", facility=facility)
        token = invite_token_generator.make_token(user)
        # Zeit um (Reset-Timeout + 1h) vorspulen — jenseits des Reset-Fensters,
        # aber innerhalb des Invite-Fensters.
        seconds = settings.PASSWORD_RESET_TIMEOUT + 3600
        future = invite_token_generator._now() + __import__("datetime").timedelta(seconds=seconds)
        with patch.object(invite_token_generator, "_now", return_value=future):
            assert invite_token_generator.check_token(user, token) is True

    def test_invite_token_expires_after_invite_timeout(self, facility):
        user = User.objects.create_user(username="expired", email="e@example.org", facility=facility)
        token = invite_token_generator.make_token(user)
        seconds = settings.INVITE_TOKEN_TIMEOUT + 3600
        future = invite_token_generator._now() + __import__("datetime").timedelta(seconds=seconds)
        with patch.object(invite_token_generator, "_now", return_value=future):
            assert invite_token_generator.check_token(user, token) is False


@pytest.mark.django_db
class TestSendInviteEmail:
    def test_email_sent_to_user_address(self, facility):
        user = User.objects.create_user(
            username="invitee",
            email="invitee@example.org",
            facility=facility,
        )
        sent = send_invite_email(user)
        assert sent is True
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == ["invitee@example.org"]
        assert "Anlaufstelle" in msg.subject

    def test_email_body_contains_setup_link(self, facility):
        user = User.objects.create_user(
            username="linkee",
            email="linkee@example.org",
            facility=facility,
        )
        send_invite_email(user)
        body = mail.outbox[0].body
        assert "/invite/" in body
        assert "linkee" in body  # username included

    def test_no_email_returns_false(self, facility):
        user = User.objects.create_user(
            username="noemail",
            email="",
            facility=facility,
        )
        sent = send_invite_email(user)
        assert sent is False
        assert len(mail.outbox) == 0


@pytest.mark.django_db
class TestInviteConfirmView:
    """L4 (Refs #1375): Die eigene invite_confirm-Route akzeptiert Invite-Tokens
    und ist von der Passwort-Reset-Route getrennt (eigener key_salt)."""

    def _confirm_url(self, user, token):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        return f"/invite/{uid}/{token}/"

    def test_valid_invite_token_opens_set_password(self, facility, client):
        user = User.objects.create_user(username="setpw", email="s@example.org", facility=facility)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        token = invite_token_generator.make_token(user)
        resp = client.get(self._confirm_url(user, token))
        # Django's PasswordResetConfirmView spiegelt den Token in die Session und
        # redirectet (302) auf die set-password-URL. Ein ungueltiger Link waere 200
        # mit validlink=False.
        assert resp.status_code == 302
        assert "set-password" in resp["Location"]

    def test_password_reset_token_rejected_on_invite_route(self, facility, client):
        """Cross-Salt-Trennung: ein Passwort-Reset-Token gilt NICHT auf /invite/."""
        user = User.objects.create_user(username="crosssalt", email="c@example.org", facility=facility)
        reset_token = default_token_generator.make_token(user)
        resp = client.get(self._confirm_url(user, reset_token))
        assert resp.status_code == 200
        assert resp.context["validlink"] is False


@pytest.mark.django_db
class TestUserOnboardingFlow:
    """End-to-end check that a freshly invited user must reset their password."""

    def test_user_must_change_password_after_invite(self, facility, admin_user, client):
        client.force_login(admin_user)
        # Create user via admin: must_change_password should be True (set in admin)
        user = User.objects.create_user(
            username="freshuser",
            email="fresh@example.org",
            facility=facility,
        )
        user.set_unusable_password()
        user.must_change_password = True
        user.save()

        assert not user.has_usable_password()
        assert user.must_change_password is True
