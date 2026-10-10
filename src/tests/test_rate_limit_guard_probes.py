"""Probes for recursive endpoint throttling, including function-based views."""

import pytest

from tests.test_architecture_guards_views import TestRateLimitOnAllMutations as MutationGuard

pytestmark = pytest.mark.architecture


@pytest.mark.parametrize(
    "relative,source",
    [
        ("config/new.py", "class NewView(View):\n    def post(self, request):\n        pass\n"),
        ("auth/nested/new.py", "@require_POST\ndef new_endpoint(request):\n    pass\n"),
        (
            "auth/new.py",
            "from django.views.decorators.http import require_POST as post_only\n"
            "@post_only\n"
            "def new_endpoint(request):\n"
            "    pass\n"
            "",
        ),
        ("auth/new.py", "@require_http_methods(['POST'])\ndef new_endpoint(request):\n    pass\n"),
        (
            "new.py",
            "@method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=False), name='post')\n"
            "class NewView(View):\n"
            "    def post(self, request):\n"
            "        pass\n"
            "",
        ),
        (
            "new.py",
            "@method_decorator(ratelimit(key='user', rate='60/h', method='GET', block=True), name='get')\n"
            "class NewView(View):\n"
            "    def post(self, request):\n"
            "        pass\n"
            "",
        ),
        ("config/new.py", "class NewView(CreateView):\n    pass\n"),
    ],
)
def test_new_unthrottled_endpoints_fail_guard(tmp_path, monkeypatch, relative, source):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    monkeypatch.setattr(MutationGuard, "_VIEWS_DIR", tmp_path)
    with pytest.raises(AssertionError, match="new.py"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "source",
    [
        "class Base(View):\n"
        "    @method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=True))\n"
        "    def post(self, request):\n"
        "        pass\n"
        "class NewView(Base):\n"
        "    pass\n"
        "",
        "@method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=True), name='dispatch')\n"
        "class NewView(CreateView):\n"
        "    http_method_names = ['post']\n"
        "",
        "@require_POST\n"
        "@ratelimit(key='ip', rate='5/m', method='POST', block=True)\n"
        "def new_endpoint(request):\n"
        "    pass\n"
        "",
    ],
)
def test_effective_throttle_passes_guard(tmp_path, monkeypatch, source):
    (tmp_path / "new.py").write_text(source)
    monkeypatch.setattr(MutationGuard, "_VIEWS_DIR", tmp_path)
    MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "source",
    [
        "@method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=True), name='post')\n"
        "class Base(View):\n    def post(self, request):\n        pass\n"
        "class NewView(Base):\n    def post(self, request):\n        pass\n",
        "class Base(View):\n"
        "    @method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=True))\n"
        "    def dispatch(self, request):\n        pass\n"
        "    def post(self, request):\n        pass\n"
        "class NewView(Base):\n    def dispatch(self, request):\n        pass\n",
    ],
)
def test_override_cannot_borrow_an_ineffective_parent_throttle(tmp_path, monkeypatch, source):
    (tmp_path / "new.py").write_text(source)
    monkeypatch.setattr(MutationGuard, "_VIEWS_DIR", tmp_path)
    with pytest.raises(AssertionError, match="NewView"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "source",
    [
        "from django_otp_webauthn.views import CompleteCredentialAuthenticationView\n"
        "class NewView(CompleteCredentialAuthenticationView):\n    pass\n",
        "from django.contrib.auth.views import LogoutView\nclass NewView(LogoutView):\n    pass\n",
        "@require_POST\n"
        "@ratelimit(key='ip', rate='1/h', method='ALL', block=True)\n"
        "def new_endpoint(request):\n    pass\n",
        "class DispatchMixin:\n"
        "    def dispatch(self, request, *args, **kwargs):\n"
        "        return self.post(request, *args, **kwargs)\n"
        "@method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=True), name='dispatch')\n"
        "class Base(View):\n    def post(self, request):\n        pass\n"
        "class NewView(DispatchMixin, Base):\n    pass\n",
    ],
    ids=["external_passkey", "external_logout", "literal_all", "shadowed_dispatch"],
)
def test_guard_rejects_unprotected_inherited_or_ineffective_post(tmp_path, monkeypatch, source):
    (tmp_path / "new.py").write_text(source)
    monkeypatch.setattr(MutationGuard, "_VIEWS_DIR", tmp_path)
    with pytest.raises(AssertionError, match="new.py"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "options",
    ["method=['GET']", "method='POST', rate=None", "method='POST'"],
    ids=["get_only", "no_rate", "missing_rate"],
)
def test_guard_rejects_limits_without_effective_post_method_or_rate(tmp_path, monkeypatch, options):
    rate = "" if "rate=" in options or options == "method='POST'" else "rate='1/h', "
    (tmp_path / "new.py").write_text(
        f"@require_POST\n@ratelimit(key='ip', {rate}{options}, block=True)\ndef new_endpoint(request):\n    pass\n"
    )
    monkeypatch.setattr(MutationGuard, "_VIEWS_DIR", tmp_path)
    with pytest.raises(AssertionError, match="new.py"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "options",
    ["method='post'", "method=('GET', 'POST')", "method=ALL", ""],
    ids=["lowercase_method", "method_tuple", "library_all", "default_all"],
)
def test_library_supported_post_methods_pass_guard(tmp_path, monkeypatch, options):
    (tmp_path / "new.py").write_text(
        "from django_ratelimit import ALL\n"
        "@require_POST\n"
        f"@ratelimit(key='ip', rate='1/h', block=True{', ' + options if options else ''})\n"
        "def new_endpoint(request):\n    pass\n"
    )
    monkeypatch.setattr(MutationGuard, "_VIEWS_DIR", tmp_path)
    MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "source",
    [
        "class Common(View):\n"
        "    @method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=True))\n"
        "    def post(self, request):\n        pass\n"
        "class Left(Common):\n    pass\n"
        "class Right(Common):\n    def post(self, request):\n        pass\n"
        "class NewView(Left, Right):\n    pass\n",
        "class NewView(View):\n"
        "    @method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=True))\n"
        "    def post(self, request):\n        pass\n"
        "    def get(self, request):\n        pass\n"
        "    def dispatch(self, request):\n        return self.get(request)\n",
    ],
    ids=["diamond_mro", "dispatch_to_get"],
)
def test_mro_and_dispatch_cannot_borrow_a_bypassed_handler_limit(tmp_path, monkeypatch, source):
    (tmp_path / "new.py").write_text(source)
    monkeypatch.setattr(MutationGuard, "_VIEWS_DIR", tmp_path)
    with pytest.raises(AssertionError, match="NewView.post"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "source",
    [
        "class Base(View):\n    def post(self, request):\n        pass\n"
        "class NewView(Base):\n"
        "    @method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=True))\n"
        "    def post(self, request):\n        pass\n"
        "    def dispatch(self, request):\n        return super().post(request)\n",
        "class NewView(View):\n"
        "    @method_decorator(ratelimit(key='user', rate='60/h', method='POST', block=True))\n"
        "    def post(self, request):\n        pass\n"
        "    def get(self, request):\n        pass\n"
        "    def dispatch(self, request):\n        handler = self.get\n        return handler(request)\n",
        "class NewView(View):\n"
        "    http_method_names = ['get']\n"
        "    def post(self, request):\n        pass\n"
        "    def dispatch(self, request):\n        return self.post(request)\n",
        "class NewView(View):\n"
        "    def get(self, request):\n        pass\n"
        "    def dispatch(self, request):\n        return self.get(request)\n",
        "@require_POST\n"
        "@(lambda ignored: lambda fn: fn)(ratelimit(key='ip', rate='1/h', method='POST', block=True))\n"
        "def NewView(request):\n    pass\n",
    ],
    ids=["parent_post", "aliased_get", "disabled_post_redirect", "dispatch_without_post", "ignored_decorator"],
)
def test_dispatch_and_decorator_redirections_need_an_applied_limit(tmp_path, monkeypatch, source):
    (tmp_path / "new.py").write_text(source)
    monkeypatch.setattr(MutationGuard, "_VIEWS_DIR", tmp_path)
    with pytest.raises(AssertionError, match="NewView.post"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize("rate", [(float("inf"), 1), (1, float("inf"))], ids=["unbounded_count", "unbounded_period"])
def test_unbounded_configured_rates_are_not_a_throttle(tmp_path, monkeypatch, settings, rate):
    settings.TEST_MUTATION_RATE = rate
    (tmp_path / "new.py").write_text(
        "from django.conf import settings\n"
        "@require_POST\n"
        "@ratelimit(key='ip', rate=settings.TEST_MUTATION_RATE, method='POST', block=True)\n"
        "def new_endpoint(request):\n    pass\n"
    )
    monkeypatch.setattr(MutationGuard, "_VIEWS_DIR", tmp_path)
    with pytest.raises(AssertionError, match="new.py"):
        MutationGuard().test_all_post_handlers_have_ratelimit()
