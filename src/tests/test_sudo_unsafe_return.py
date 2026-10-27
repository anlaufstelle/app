"""Unsafe requests return to a GET page after sudo without replay (Refs #2640)."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.views import View
from django_otp_webauthn.models import WebAuthnCredential

from core.models import AuditLog, EncryptedTOTPDevice
from core.services.audit import audit_event
from core.services.security import LOCKOUT_THRESHOLD, RequireSudoModeMixin, is_locked

pytestmark = pytest.mark.django_db
PASSWORD = "testpass123"


@pytest.fixture(autouse=True)
def enable_sudo():
    with override_settings(SUDO_MODE_ENABLED=True):
        yield


@pytest.fixture
def passkey(staff_user):
    WebAuthnCredential.objects.create(
        user=staff_user,
        name="Retained key",
        confirmed=True,
        credential_id=b"sudo-retained-key",
        public_key=b"retained-public-key",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )
    return WebAuthnCredential.objects.create(
        user=staff_user,
        name="Existing key",
        confirmed=True,
        credential_id=b"sudo-return-key",
        public_key=b"public-key",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


def login_verified(client, user):
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


def confirm_sudo(client, response, *, secure=False):
    assert response.status_code == 302
    assert urlsplit(response.url).path == reverse("sudo_mode")
    page = client.get(response.url, secure=secure)
    assert page.status_code == 200
    confirmed = client.post(reverse("sudo_mode"), {"password": PASSWORD, "next": page.context["next"]}, secure=secure)
    assert confirmed.status_code == 302
    landing = client.get(confirmed.url, secure=secure)
    assert landing.status_code == 200
    return confirmed.url


@pytest.mark.parametrize("language", ["de", "en"])
def test_passkey_post_returns_to_settings_without_replay(client, staff_user, passkey, language):
    login_verified(client, staff_user)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
    response = client.post(reverse("mfa_passkey_delete", args=[passkey.pk]))
    assert confirm_sudo(client, response) == reverse("mfa_settings")
    assert WebAuthnCredential.objects.filter(pk=passkey.pk).exists()
    repeated = client.post(reverse("mfa_passkey_delete", args=[passkey.pk]))
    assert repeated.status_code == 302
    assert repeated.url == reverse("mfa_settings")
    assert not WebAuthnCredential.objects.filter(pk=passkey.pk).exists()


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("action", ["user_access_invite", "user_access_unlock"])
def test_access_post_returns_to_account_without_replay(client, admin_user, staff_user, mailoutbox, action, language):
    staff_user.email = "pending@example.com"
    staff_user.must_change_password = True
    staff_user.save(update_fields=["email", "must_change_password"])
    for _ in range(LOCKOUT_THRESHOLD):
        audit_event(AuditLog.Action.LOGIN_FAILED, user=staff_user, facility=staff_user.facility)
    assert is_locked(staff_user)
    original_password = staff_user.password
    effects = AuditLog.objects.filter(
        user=staff_user, action__in=[AuditLog.Action.USER_INVITE_SENT, AuditLog.Action.LOGIN_UNLOCK]
    )
    assert not effects.exists()

    login_verified(client, admin_user)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
    action_url = reverse(f"core:{action}", args=[staff_user.pk])
    response = client.post(action_url)
    assert not mail.outbox
    assert is_locked(staff_user)
    assert not effects.exists()
    assert confirm_sudo(client, response) == reverse("core:user_access_update", args=[staff_user.pk])
    staff_user.refresh_from_db()
    assert staff_user.password == original_password
    assert not mail.outbox
    assert is_locked(staff_user)
    assert not effects.exists()

    repeated = client.post(action_url)
    assert repeated.status_code == 302
    assert repeated.url == reverse("core:user_access_update", args=[staff_user.pk])
    if action == "user_access_invite":
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [staff_user.email]
        assert effects.get().action == AuditLog.Action.USER_INVITE_SENT
        staff_user.refresh_from_db()
        assert not staff_user.has_usable_password()
        assert is_locked(staff_user)
    else:
        assert not mail.outbox
        assert not is_locked(staff_user)
        assert effects.get().action == AuditLog.Action.LOGIN_UNLOCK


@pytest.mark.parametrize(
    "referer",
    [
        "https://evil.example/mfa/settings/",
        "//evil.example/mfa/settings/",
        "/\\evil.example/mfa/settings/",
        "https://testserver.evil.example/mfa/settings/",
        "http://testserver/mfa/settings/",
        "https://testserver/mfa/passkey/999/delete/",
    ],
)
def test_unsafe_referer_uses_fixed_fallback(client, staff_user, passkey, referer):
    login_verified(client, staff_user)
    response = client.post(reverse("mfa_passkey_delete", args=[passkey.pk]), HTTP_REFERER=referer, secure=True)
    assert confirm_sudo(client, response, secure=True) == reverse("mfa_settings")
    assert WebAuthnCredential.objects.filter(pk=passkey.pk).exists()


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_unsafe_methods_ignore_same_origin_referer(client, staff_user, passkey, method):
    login_verified(client, staff_user)
    target = reverse("mfa_settings") + "?tab=keys&search=a%26b+key"
    response = getattr(client, method)(
        reverse("mfa_passkey_delete", args=[passkey.pk]),
        HTTP_REFERER="https://testserver" + target + "#keys",
        secure=True,
    )
    assert confirm_sudo(client, response, secure=True) == reverse("mfa_settings")
    assert WebAuthnCredential.objects.filter(pk=passkey.pk).exists()


@pytest.mark.parametrize("view_name", ["mfa_setup", "mfa_device_change"])
def test_conditional_setup_post_returns_to_own_page_without_replaying_token(client, staff_user, passkey, view_name):
    login_verified(client, staff_user)
    target = reverse(view_name) + "?tab=totp&return=1"
    response = client.post(target, {"token": "unused"}, HTTP_REFERER="http://testserver/mfa/settings/?source=referer")
    assert not EncryptedTOTPDevice.objects.filter(user=staff_user).exists()
    assert confirm_sudo(client, response) == target
    # GET may prepare an unconfirmed device; it must not complete the token POST.
    assert not EncryptedTOTPDevice.objects.filter(user=staff_user, confirmed=True).exists()
    assert "mfa_setup_pending" not in client.session
    assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.BACKUP_CODES_GENERATED).exists()
    assert WebAuthnCredential.objects.filter(pk=passkey.pk).exists()


def test_get_preserves_full_path_and_query_even_with_referer(client, admin_user):
    login_verified(client, admin_user)
    target = reverse("core:facility_settings") + "?tab=a%26b&q=x+y&empty="
    response = client.get(target, HTTP_REFERER="http://testserver/")
    assert parse_qs(urlsplit(response.url).query)["next"] == [target]
    assert confirm_sudo(client, response) == target


@pytest.mark.parametrize(
    ("action", "kwargs", "fallback"),
    [
        ("system_unlock", {}, "system_lockout_list"),
        (
            "system_legal_hold_dismiss",
            {"pk": "00000000-0000-0000-0000-000000000001"},
            "system_legal_hold_list",
        ),
    ],
)
def test_system_post_only_actions_return_to_their_list(client, super_admin_user, action, kwargs, fallback):
    login_verified(client, super_admin_user)
    response = client.post(reverse(f"core:{action}", kwargs=kwargs))
    assert confirm_sudo(client, response) == reverse(f"core:{fallback}")


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize(
    "referer",
    [
        "http://testserver/admin-mgmt/logout/",
        "//evil.example/mfa/settings/?source=foreign-relative",
        "//testserver/mfa/settings/?source=same-host-relative",
        "https://testserver/mfa/settings/?source=cross-scheme",
        "http://evil.example/mfa/settings/?source=foreign-host",
        "http://testserver/mfa/settings/?source=same-origin",
    ],
)
def test_system_post_ignores_every_referer(client, super_admin_user, referer, language):
    login_verified(client, super_admin_user)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
    response = client.post(reverse("core:system_unlock"), HTTP_REFERER=referer)
    assert confirm_sudo(client, response) == reverse("core:system_lockout_list")


def test_get_capable_action_returns_to_own_path_and_query(client, admin_user):
    login_verified(client, admin_user)
    target = reverse("core:facility_settings") + "?tab=a%26b&q=x+y&empty="
    response = client.post(target, HTTP_REFERER="http://testserver/mfa/settings/?source=referer")
    assert confirm_sudo(client, response) == target


@pytest.mark.parametrize("has_get_handler", [False, True])
def test_action_without_allowed_get_or_return_route_uses_root(rf, admin_user, has_get_handler):
    class ActionView(RequireSudoModeMixin, View):
        http_method_names = ["post"]

    if has_get_handler:
        ActionView.get = lambda self, request: None
    request = rf.post("/unregistered-action/?unused=1", HTTP_REFERER="http://testserver/mfa/settings/")
    request.user = admin_user
    request.session = {}
    response = ActionView.as_view()(request)
    assert parse_qs(urlsplit(response.url).query)["next"] == ["/"]
