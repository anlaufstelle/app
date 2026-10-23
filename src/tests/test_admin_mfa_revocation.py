"""MFA completion survives stale admin writes and session or authentication faults."""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from core.admin_site import anlaufstelle_admin_site
from core.models import EncryptedTOTPDevice, User
from core.services.user_access import reset_user_mfa
from tests.test_mfa_session_revocation import _audits, _browser, _prepare

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["db", "cache", "cached_db"])
def session_backend(request, settings):
    settings.SESSION_ENGINE = f"django.contrib.sessions.backends.{request.param}"
    return import_module(settings.SESSION_ENGINE).SessionStore


@pytest.mark.parametrize("action", ["change", "disable", "admin_reset"])
def test_concurrent_admin_edit_does_not_restore_revoked_sessions(action, session_backend, staff_user, admin_user):
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    current, other = _browser(staff_user), _browser(staff_user)
    # The admin change form reads an instance before the MFA commit, then
    # saves it after that commit. Use the actual registered ModelAdmin path.
    stale = User.objects.get(pk=staff_user.pk)
    stale.display_name = "Concurrent display edit"
    before = stale.password
    if action == "admin_reset":
        reset_user_mfa(staff_user, actor=admin_user)
    else:
        url, data, _pending = _prepare(current, staff_user, action)
        assert current.post(url, data).status_code == 302
    assert User.objects.get(pk=staff_user.pk).password != before

    request = RequestFactory().post("/admin-mgmt/core/user/change/")
    request.user = admin_user
    request.current_facility = admin_user.facility
    anlaufstelle_admin_site._registry[User].save_model(
        request, stale, SimpleNamespace(changed_data=["display_name"]), change=True
    )
    if action != "admin_reset":
        assert _audits(staff_user, action).get().detail["sessions_revoked"] is True

    response = other.get("/mfa/settings/")
    assert response.status_code == 302 and response.url.startswith("/login/"), (
        action,
        User.objects.get(pk=staff_user.pk).password == before,
        response.status_code,
        getattr(response, "url", None),
    )
    stored = User.objects.get(pk=stale.pk)
    assert stored.display_name == "Concurrent display edit"
    assert stored.password != before
    if action != "admin_reset":
        assert current.get("/mfa/settings/").status_code == 200
