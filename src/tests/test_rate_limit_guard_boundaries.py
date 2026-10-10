"""Known boundaries of the mutation guard, reproduced through registered URLs."""

from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.core.cache import cache
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.test import RequestFactory
from django.urls import clear_url_caches, path, resolve
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django_ratelimit.exceptions import Ratelimited

from core.utils.ratelimit import ratelimit
from core.views.auth import CustomLogoutView
from tests.test_architecture_guards_views import TestRateLimitOnAllMutations as MutationGuard

pytestmark = pytest.mark.architecture


@pytest.fixture
def isolated_limits(settings, monkeypatch):
    settings.RATELIMIT_ENABLE = True
    cache.clear()
    monkeypatch.setattr("django_ratelimit.core.time", SimpleNamespace(time=lambda: 1_800_000_000))
    yield
    cache.clear()
    clear_url_caches()


def mount(settings, callback, name="probe"):
    module = ModuleType("guard_boundary_urls")
    module.__file__ = __file__
    module.urlpatterns = [path("probe/", callback, name=name)]
    settings.ROOT_URLCONF = module
    clear_url_caches()


def request():
    result = RequestFactory().post("/probe/", {"submission": "example"})
    result.user = SimpleNamespace(is_authenticated=False)
    result.META["HTTP_X_CSRFTOKEN"] = get_token(result)
    result.COOKIES["csrftoken"] = result.META["CSRF_COOKIE"]
    return result


def test_current_registered_endpoints_pass_the_guard():
    MutationGuard().test_all_post_handlers_have_ratelimit()


def test_outer_limit_really_precedes_the_permission_hook(settings, isolated_limits):
    calls = []

    @method_decorator(ratelimit(key="ip", rate="1/h", method="POST"), name="dispatch")
    class ProtectedView(LoginRequiredMixin, View):
        def handle_no_permission(self):
            calls.append(self.request.method)
            return HttpResponse("denied submission recorded")

    callback = ProtectedView.as_view()
    mount(settings, callback)
    MutationGuard().test_all_post_handlers_have_ratelimit()
    assert callback(request()).status_code == 200
    for _ in range(2):
        with pytest.raises(Ratelimited):
            callback(request())
    assert calls == ["POST"]


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="The guard trusts auth-mixin dispatch forwarding without checking custom hooks before throttling (#2529).",
)
@pytest.mark.parametrize("hook", ["login_fallback", "permission_hook", "user_test_hook"])
def test_guard_rejects_auth_hooks_before_handler_limit(settings, isolated_limits, hook):
    calls = []

    class LimitedHandler(View):
        @method_decorator(ratelimit(key="ip", rate="1/h", method="POST"))
        def post(self, request):
            return HttpResponse("ordinary submission")

    class LoginFallback(LoginRequiredMixin, LimitedHandler):
        def handle_no_permission(self):
            calls.append(self.request.method)
            return HttpResponse("denied submission recorded")

    class PermissionHook(PermissionRequiredMixin, LimitedHandler):
        def has_permission(self):
            calls.append(self.request.method)
            return True

    class UserTestHook(UserPassesTestMixin, LimitedHandler):
        def test_func(self):
            calls.append(self.request.method)
            return True

    specimens = {
        "login_fallback": (LoginFallback, [200, 200, 200]),
        "permission_hook": (PermissionHook, [200, 429, 429]),
        "user_test_hook": (UserTestHook, [200, 429, 429]),
    }
    view, expected_statuses = specimens[hook]
    mount(settings, view.as_view())
    callback = resolve("/probe/").func
    statuses = []
    for _ in range(3):
        try:
            statuses.append(callback(request()).status_code)
        except Ratelimited:
            statuses.append(429)
    assert statuses == expected_statuses
    assert calls == ["POST", "POST", "POST"]
    with pytest.raises(AssertionError, match="probe"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="The logout exception trusts the view class despite a dispatch replacement via as_view (#2529).",
)
def test_guard_rejects_logout_dispatch_replacement(settings, isolated_limits):
    calls = []

    @require_POST
    def replacement_dispatch(request, *args, **kwargs):
        calls.append(request.method)
        return HttpResponse("custom submission")

    mount(settings, CustomLogoutView.as_view(dispatch=replacement_dispatch), name="logout")
    callback = resolve("/probe/").func
    assert [callback(request()).status_code for _ in range(3)] == [200, 200, 200]
    assert calls == ["POST", "POST", "POST"]
    with pytest.raises(AssertionError, match="logout"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "path,hook_boundary,dispatch_boundary",
    [
        ("docs/security-notes.md", "eigene Prüf-Hooks", "per `as_view(...)` ersetzte Dispatches"),
        ("docs/threat-model.md", "eigene Prüf-Hooks", "per `as_view(...)` ersetzte Dispatches"),
        ("docs/en/security-notes.md", "custom auth-mixin check hooks", "dispatches replaced via `as_view(...)`"),
        ("docs/en/threat-model.md", "custom auth-mixin check hooks", "dispatches replaced via `as_view(...)`"),
    ],
)
def test_docs_name_known_guard_boundaries(path, hook_boundary, dispatch_boundary):
    text = (Path(__file__).resolve().parents[2] / path).read_text(encoding="utf-8")
    assert hook_boundary in text
    assert dispatch_boundary in text
