"""Tests fuer die DemoGuardMiddleware (Refs #1062)."""

import ast
from functools import cache

import pytest
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from core.middleware.demo_guard import DemoGuardMiddleware
from tests.test_demo_settings import _run_demo_import


def _request(method, path, **extra):
    rf = RequestFactory()
    req = getattr(rf, method.lower())(path, **extra)
    req.session = {}
    req._messages = FallbackStorage(req)
    return req


def _passthrough(request):
    return HttpResponse("passed")


class DemoGuardTests(SimpleTestCase):
    @override_settings(DEMO_MODE=True)
    def test_blocks_maintenance_toggle_post(self):
        resp = DemoGuardMiddleware(_passthrough)(_request("POST", "/system/maintenance/"))
        self.assertEqual(resp.status_code, 302)

    @override_settings(DEMO_MODE=True)
    def test_external_referer_is_not_used_as_redirect_target(self):
        """L6 (Refs #1375): Ein externer Referer darf nicht als Redirect-Ziel
        dienen (Open Redirect). Die Middleware laeuft vor CSRF/Auth."""
        resp = DemoGuardMiddleware(_passthrough)(
            _request("POST", "/system/maintenance/", HTTP_REFERER="https://evil.example/phish")
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")

    @override_settings(DEMO_MODE=True)
    def test_protocol_relative_referer_is_not_used(self):
        """``//evil`` (protokoll-relativ) wird vom Browser als externer Host
        gelesen -> muss auf ``/`` normalisiert werden."""
        resp = DemoGuardMiddleware(_passthrough)(
            _request("POST", "/system/maintenance/", HTTP_REFERER="//evil.example/phish")
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")

    @override_settings(DEMO_MODE=True)
    def test_same_origin_relative_referer_is_preserved(self):
        """Ein interner relativer Pfad bleibt als Redirect-Ziel erhalten."""
        resp = DemoGuardMiddleware(_passthrough)(_request("POST", "/system/maintenance/", HTTP_REFERER="/system/"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/system/")

    @override_settings(DEMO_MODE=True)
    def test_allows_get_on_blocked_path(self):
        resp = DemoGuardMiddleware(_passthrough)(_request("GET", "/system/maintenance/"))
        self.assertEqual(resp.content, b"passed")

    @override_settings(DEMO_MODE=True)
    def test_allows_other_post(self):
        resp = DemoGuardMiddleware(_passthrough)(_request("POST", "/event/create/"))
        self.assertEqual(resp.content, b"passed")

    @override_settings(DEMO_MODE=False)
    def test_noop_when_not_demo(self):
        resp = DemoGuardMiddleware(_passthrough)(_request("POST", "/system/maintenance/"))
        self.assertEqual(resp.content, b"passed")

    @override_settings(DEMO_MODE=True, DEMO_GUARD_BLOCKED_PREFIXES=("/foo/",))
    def test_denylist_configurable(self):
        blocked = DemoGuardMiddleware(_passthrough)(_request("POST", "/foo/bar/"))
        self.assertEqual(blocked.status_code, 302)
        allowed = DemoGuardMiddleware(_passthrough)(_request("POST", "/system/maintenance/"))
        self.assertEqual(allowed.content, b"passed")

    def test_blocks_full_demo_denylist(self):
        with override_settings(DEMO_MODE=True, DEMO_GUARD_BLOCKED_PREFIXES=_demo_blocked_prefixes()):
            for path in (
                "/system/maintenance/",
                "/mfa/setup/",
                "/password-change/",
                "/admin-mgmt/core/user/123/change/",
            ):
                resp = DemoGuardMiddleware(_passthrough)(_request("POST", path))
                self.assertEqual(resp.status_code, 302, path)
            # Core demo actions stay allowed.
            for path in ("/event/create/", "/client/5/edit/"):
                resp = DemoGuardMiddleware(_passthrough)(_request("POST", path))
                self.assertEqual(resp.content, b"passed", path)


@cache
def _demo_blocked_prefixes():
    """The denylist exactly as the demo settings module defines it."""
    result = _run_demo_import(
        "from anlaufstelle.settings.demo import DEMO_GUARD_BLOCKED_PREFIXES;print(repr(DEMO_GUARD_BLOCKED_PREFIXES))"
    )
    assert result.returncode == 0, result.stderr
    return ast.literal_eval(result.stdout.strip())


def _demo_post(path):
    request = _request("POST", path, HTTP_REFERER="/mfa/settings/")
    with override_settings(DEMO_MODE=True, DEMO_GUARD_BLOCKED_PREFIXES=_demo_blocked_prefixes()):
        response = DemoGuardMiddleware(_passthrough)(request)
    return request, response


# Every path that changes the second factor or the passkeys of a shared demo account.
FACTOR_CHANGING_ROUTES = (
    ("mfa_setup", {}),
    ("mfa_device_change", {}),
    ("otp_webauthn:credential-registration-begin", {}),
    ("otp_webauthn:credential-registration-complete", {}),
    ("mfa_backup_codes_regenerate", {}),
    ("mfa_disable", {}),
    ("mfa_passkey_delete", {"pk": 1}),
)

# Signing in with a second factor stays possible on the demo.
SIGN_IN_ROUTES = (
    ("login", {}),
    ("mfa_verify", {}),
    ("otp_webauthn:credential-authentication-begin", {}),
    ("otp_webauthn:credential-authentication-complete", {}),
)


@pytest.mark.parametrize(("name", "kwargs"), FACTOR_CHANGING_ROUTES)
def test_demo_blocks_factor_change(name, kwargs):
    request, response = _demo_post(reverse(name, kwargs=kwargs))

    assert response.status_code == 302
    assert response["Location"] == "/mfa/settings/"
    assert [str(message) for message in get_messages(request)] == ["Diese Aktion ist im Demo-Modus deaktiviert."]


@pytest.mark.parametrize(("name", "kwargs"), SIGN_IN_ROUTES)
def test_demo_keeps_sign_in_with_second_factor_open(name, kwargs):
    _request_obj, response = _demo_post(reverse(name, kwargs=kwargs))

    assert response.content == b"passed"


@pytest.mark.django_db
def test_demo_sign_in_with_totp_completes(client, staff_user):
    from django_otp.oath import totp

    from core.models import EncryptedTOTPDevice

    device = EncryptedTOTPDevice.objects.create(user=staff_user, name="demo", confirmed=True)
    token = f"{totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits):0{device.digits}d}"

    with override_settings(DEMO_MODE=True, DEMO_GUARD_BLOCKED_PREFIXES=_demo_blocked_prefixes()):
        login = client.post(reverse("login"), {"username": "teststaff", "password": "testpass123"})
        verify = client.post(reverse("mfa_verify"), {"token": token})

    assert login.status_code == 302
    assert verify.status_code == 302
    assert verify.url != reverse("mfa_verify")
    assert client.session.get("mfa_verified") is True


@cache
def _demo_guard_settings():
    """Every demo guard setting exactly as the demo settings module defines it."""
    result = _run_demo_import(
        "import anlaufstelle.settings.demo as demo;"
        "print(repr({name: getattr(demo, name) for name in dir(demo) if name.startswith('DEMO_GUARD_')}))"
    )
    assert result.returncode == 0, result.stderr
    return ast.literal_eval(result.stdout.strip())


@pytest.mark.django_db
def test_demo_refuses_admin_reset_of_another_accounts_second_factor(client, admin_user, staff_user):
    """Account management must not reset the second factor of a shared demo account."""
    from django.test import Client

    from core.models import AuditLog, EncryptedTOTPDevice

    target_session = Client()
    target_session.force_login(staff_user)
    device = EncryptedTOTPDevice.objects.create(user=staff_user, name="target", confirmed=True)
    original_password_hash = staff_user.password
    reset_url = reverse("core:user_access_mfa_reset", kwargs={"pk": staff_user.pk})

    with override_settings(DEMO_MODE=True, SUDO_MODE_ENABLED=True, **_demo_guard_settings()):
        client.force_login(admin_user)
        sudo = client.post(reverse("sudo_mode"), {"password": "testpass123", "next": reset_url})
        assert sudo.status_code == 302
        assert sudo.url == reset_url
        page = client.get(reset_url)
        response = client.post(reset_url, HTTP_REFERER=reset_url)
        target_page = target_session.get(reverse("mfa_verify"))

    staff_user.refresh_from_db()
    assert page.status_code == 200
    assert response.status_code == 302
    assert response.url == reset_url
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        "Diese Aktion ist im Demo-Modus deaktiviert."
    ]
    assert EncryptedTOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists()
    assert staff_user.password == original_password_hash
    assert target_page.status_code == 200
    assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_RESET_BY_ADMIN).exists()


@pytest.mark.parametrize(
    ("name", "kwargs"),
    (
        ("core:user_access_list", {}),
        ("core:user_access_create", {}),
        ("core:user_access_update", {"pk": 7}),
        ("core:user_access_invite", {"pk": 7}),
        ("core:user_access_unlock", {"pk": 7}),
    ),
)
def test_demo_keeps_the_rest_of_account_management_open(name, kwargs):
    request = _request("POST", reverse(name, kwargs=kwargs))
    with override_settings(DEMO_MODE=True, **_demo_guard_settings()):
        response = DemoGuardMiddleware(_passthrough)(request)

    assert response.content == b"passed"


def test_admin_reset_passes_the_guard_outside_the_demo():
    request = _request("POST", reverse("core:user_access_mfa_reset", kwargs={"pk": 7}))
    with override_settings(DEMO_MODE=False, **_demo_guard_settings()):
        response = DemoGuardMiddleware(_passthrough)(request)

    assert response.content == b"passed"


def test_unknown_path_passes_the_guard_in_the_demo():
    request = _request("POST", "/no-such-route/")
    with override_settings(DEMO_MODE=True, **_demo_guard_settings()):
        response = DemoGuardMiddleware(_passthrough)(request)

    assert response.content == b"passed"


DEMO_REFUSAL = "Diese Aktion ist im Demo-Modus deaktiviert."


def _messages(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


@pytest.mark.django_db
def test_demo_refuses_imposing_2fa_on_another_shared_account(client, admin_user, staff_user):
    """Requiring 2FA would send the shared account to a setup the demo refuses."""
    from django.test import Client

    from core.models import AuditLog
    from tests.test_user_access_hub import _update_data

    target_session = Client()
    target_session.force_login(staff_user)
    update_url = reverse("core:user_access_update", kwargs={"pk": staff_user.pk})

    with override_settings(DEMO_MODE=True, **_demo_guard_settings()):
        client.force_login(admin_user)
        response = client.post(
            update_url,
            _update_data(staff_user, mfa_required="on", display_name="Changed name"),
            HTTP_REFERER=update_url,
        )
        target_landing = target_session.get("/")

    staff_user.refresh_from_db()
    assert response.status_code == 302
    assert response.url == update_url
    assert _messages(response) == [DEMO_REFUSAL]
    assert staff_user.mfa_required is False
    assert staff_user.display_name != "Changed name"
    assert target_landing.status_code == 200
    assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.USER_ACCESS_UPDATED).exists()


@pytest.mark.django_db
def test_demo_keeps_editing_another_account_without_changing_its_2fa_requirement(client, admin_user, staff_user):
    from tests.test_user_access_hub import _update_data

    with override_settings(DEMO_MODE=True, **_demo_guard_settings()):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:user_access_update", kwargs={"pk": staff_user.pk}),
            _update_data(staff_user, display_name="Changed name"),
        )

    staff_user.refresh_from_db()
    assert response.status_code == 302
    assert _messages(response) == ["Zugang wurde gespeichert."]
    assert staff_user.display_name == "Changed name"
    assert staff_user.mfa_required is False


@pytest.mark.django_db
def test_requiring_2fa_for_another_account_still_works_outside_the_demo(client, admin_user, staff_user):
    from tests.test_user_access_hub import _update_data

    with override_settings(DEMO_MODE=False, **_demo_guard_settings()):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:user_access_update", kwargs={"pk": staff_user.pk}),
            _update_data(staff_user, mfa_required="on"),
        )

    staff_user.refresh_from_db()
    assert response.status_code == 302
    assert staff_user.mfa_required is True


@pytest.mark.django_db
def test_demo_refuses_facility_wide_2fa_requirement(client, admin_user, staff_user, settings_obj):
    """Facility-wide 2FA would send every shared account, the admin included, to a refused setup."""
    from django.test import Client

    from core.models import AuditLog
    from tests.test_facility_settings_hub import _post_data

    target_session = Client()
    target_session.force_login(staff_user)
    settings_url = reverse("core:facility_settings")
    data = _post_data(settings_obj)
    data["mfa_enforced_facility_wide"] = "on"
    data["facility_full_name"] = "Changed name"

    with override_settings(DEMO_MODE=True, **_demo_guard_settings()):
        client.force_login(admin_user)
        response = client.post(settings_url, data, HTTP_REFERER=settings_url)
        target_landing = target_session.get("/")
        admin_landing = client.get("/")

    settings_obj.refresh_from_db()
    assert response.status_code == 302
    assert response.url == settings_url
    assert _messages(response) == [DEMO_REFUSAL]
    assert settings_obj.mfa_enforced_facility_wide is False
    assert settings_obj.facility_full_name != "Changed name"
    assert target_landing.status_code == 200
    assert admin_landing.status_code == 200
    assert not AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).exists()


@pytest.mark.django_db
def test_demo_keeps_saving_other_facility_settings(client, admin_user, settings_obj):
    from tests.test_facility_settings_hub import _post_data

    data = _post_data(settings_obj)
    data["facility_full_name"] = "Changed name"

    with override_settings(DEMO_MODE=True, **_demo_guard_settings()):
        client.force_login(admin_user)
        response = client.post(reverse("core:facility_settings"), data)

    settings_obj.refresh_from_db()
    assert response.status_code == 302
    assert _messages(response) == ["Einstellungen wurden gespeichert."]
    assert settings_obj.facility_full_name == "Changed name"
    assert settings_obj.mfa_enforced_facility_wide is False


@pytest.mark.django_db
def test_facility_wide_2fa_requirement_still_works_outside_the_demo(client, admin_user, settings_obj):
    from tests.test_facility_settings_hub import _post_data

    data = _post_data(settings_obj)
    data["mfa_enforced_facility_wide"] = "on"

    with override_settings(DEMO_MODE=False, **_demo_guard_settings()):
        client.force_login(admin_user)
        response = client.post(reverse("core:facility_settings"), data)

    settings_obj.refresh_from_db()
    assert response.status_code == 302
    assert settings_obj.mfa_enforced_facility_wide is True


def _admin_settings_post_data(request, settings_obj):
    """Every field of the admin change form with its stored value."""
    from core.admin_site import anlaufstelle_admin_site
    from core.models import Settings

    form = anlaufstelle_admin_site._registry[Settings].get_form(request, settings_obj)(instance=settings_obj)
    data = {}
    for name in form.fields:
        value = form[name].value()
        if isinstance(value, bool):
            if value:
                data[name] = "on"
        elif value is None:
            data[name] = ""
        elif isinstance(value, (list, tuple)):
            data[name] = [str(item) for item in value]
        else:
            data[name] = str(value)
    return data


@pytest.mark.django_db
@pytest.mark.parametrize(("demo_mode", "stored"), ((True, False), (False, True)))
def test_admin_settings_2fa_requirement_is_refused_only_on_the_demo(
    client, admin_user, settings_obj, demo_mode, stored
):
    change_url = reverse("admin:core_settings_change", args=[settings_obj.pk])

    with override_settings(DEMO_MODE=demo_mode, SUDO_MODE_ENABLED=False, **_demo_guard_settings()):
        client.force_login(admin_user)
        page = client.get(change_url)
        data = _admin_settings_post_data(page.wsgi_request, settings_obj)
        data["mfa_enforced_facility_wide"] = "on"
        response = client.post(change_url, data, HTTP_REFERER=change_url)

    settings_obj.refresh_from_db()
    assert page.status_code == 200
    assert response.status_code == 302
    assert settings_obj.mfa_enforced_facility_wide is stored
    if demo_mode:
        assert response.url == change_url
        assert _messages(response) == [DEMO_REFUSAL]
