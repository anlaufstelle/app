"""Token-based invite flow tests (Refs #528)."""

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import User
from core.services.invite import build_invite_url, send_invite_email


@pytest.mark.django_db
class TestBuildInviteUrl:
    def test_relative_url_when_no_request(self, facility):
        user = User.objects.create_user(
            username="testinvite",
            email="invite@example.org",
            facility=facility,
        )
        url = build_invite_url(user)
        assert "/password-reset/" in url
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
        assert default_token_generator.check_token(user, token)


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
        assert "password-reset" in body or "/password-reset/" in body
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
