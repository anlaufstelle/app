"""Pins for safe sudo return paths and optional view metadata.

Source: mutation delta of the release-0.99.9 checkpoint, 15 mutants.
Groups: safe methods (6), fallback routes (4), absent metadata (2), public API (3).
"""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from django.http import HttpResponse
from django.urls import path
from django.views import View

from core.services.security.sudo_mode import RequireSudoModeMixin, sudo_redirect_if_required


class PostOnlyView(View):
    """Provide a resolvable action that cannot be reached with GET."""

    http_method_names = ["post"]

    def post(self, request):
        return HttpResponse()


class ReturnView(View):
    """Provide a declared return page with a GET handler."""

    def get(self, request):
        return HttpResponse()


def function_view(request):
    """Provide a route without class-based view metadata."""
    return HttpResponse()


urlpatterns = [
    path("action/", PostOnlyView.as_view()),
    path("return/", ReturnView.as_view()),
    path("function/", function_view),
    path("sudo/", ReturnView.as_view(), name="sudo_mode"),
]


@pytest.fixture
def sudo_request(rf, settings):
    settings.SUDO_MODE_ENABLED = True
    settings.ROOT_URLCONF = __name__
    request = rf.post("/action/?q=a%26b&empty=")
    request.user = SimpleNamespace(is_authenticated=True)
    request.session = {}
    return request


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "TRACE"])
def test_safe_method_retains_full_return_target(sudo_request, method):
    """Safe methods retain path and query even for a view without GET.

    x__sudo_return_path__mutmut_5, x__sudo_return_path__mutmut_6,
    x__sudo_return_path__mutmut_7, x__sudo_return_path__mutmut_8,
    x__sudo_return_path__mutmut_9, x__sudo_return_path__mutmut_10.
    """
    sudo_request.method = method
    response = sudo_redirect_if_required(sudo_request, sudo_return_url="/return/", view=PostOnlyView())
    assert parse_qs(urlsplit(response.url).query)["next"] == [sudo_request.get_full_path()]


@pytest.mark.parametrize("fallback", ["/missing/", "/action/", "/function/"])
def test_unusable_fallback_returns_home(sudo_request, fallback):
    """Unknown, POST-only and function routes safely fall back to the root.

    x__sudo_return_path__mutmut_17, x__sudo_return_path__mutmut_19,
    x__sudo_return_path__mutmut_25, x__sudo_return_path__mutmut_28.
    """
    response = sudo_redirect_if_required(sudo_request, sudo_return_url=fallback, view=PostOnlyView())
    assert parse_qs(urlsplit(response.url).query)["next"] == ["/"]


@pytest.mark.parametrize("view", [None, object()])
def test_missing_view_metadata_still_redirects(sudo_request, view):
    """Absent view metadata permits a safe fallback instead of raising.

    x__supports_get__mutmut_7, x__supports_get__mutmut_10.
    """
    response = sudo_redirect_if_required(sudo_request, sudo_return_url="/return/", view=view)
    assert parse_qs(urlsplit(response.url).query)["next"] == ["/return/"]


def test_declared_return_page_keeps_query(sudo_request):
    """Conditional sudo forwards a declared GET page and its complete query.

    x_sudo_redirect_if_required__mutmut_6, x_sudo_redirect_if_required__mutmut_9.
    """
    target = "/return/?tab=keys&q=a%26b&empty="
    response = sudo_redirect_if_required(sudo_request, sudo_return_url=target, view=PostOnlyView())
    assert parse_qs(urlsplit(response.url).query)["next"] == [target]


def test_unspecified_return_url_is_absent():
    """An undeclared return URL stays absent: xǁRequireSudoModeMixinǁget_sudo_return_url__mutmut_2."""
    assert RequireSudoModeMixin().get_sudo_return_url() is None
