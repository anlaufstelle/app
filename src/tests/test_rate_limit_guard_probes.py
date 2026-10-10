"""Probes for recursive endpoint throttling, including function-based views."""

import pytest

from tests.test_architecture_guards_views import TestRateLimitOnAllMutations as MutationGuard

pytestmark = pytest.mark.architecture


@pytest.fixture
def registered_endpoint(tmp_path, monkeypatch, settings):
    """Mount each specimen through Django's public URL configuration."""
    import sys
    from types import ModuleType

    from django.urls import clear_url_caches, path

    def register(source, relative="new.py"):
        prefix = (
            "from django.http import HttpResponse\n"
            "from django.views.generic import View, CreateView\n"
            "from django.views.decorators.http import require_POST, require_http_methods\n"
            "from django.utils.decorators import method_decorator\n"
            "from core.utils.ratelimit import ratelimit\n"
        )
        module = ModuleType("probe_urls")
        filename = tmp_path / relative
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(prefix + source)
        module.__file__ = str(filename)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        exec(compile(prefix + source, str(filename), "exec"), module.__dict__)  # noqa: S102 -- Isolated URL specimens.
        endpoint = module.__dict__.get("new_endpoint") or module.NewView
        callback = endpoint.as_view() if isinstance(endpoint, type) else endpoint
        module.urlpatterns = [path("probe/", callback, name="probe")]
        monkeypatch.setattr(settings, "ROOT_URLCONF", module)
        clear_url_caches()
        return callback

    yield register
    clear_url_caches()


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
            "    def get(self, request):\n        pass\n"
            "    def post(self, request):\n"
            "        pass\n"
            "",
        ),
        ("config/new.py", "class NewView(CreateView):\n    pass\n"),
    ],
)
def test_new_unthrottled_endpoints_fail_guard(registered_endpoint, relative, source):
    registered_endpoint(source, relative)
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
def test_effective_throttle_passes_guard(registered_endpoint, source):
    registered_endpoint(source)
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
def test_override_cannot_borrow_an_ineffective_parent_throttle(registered_endpoint, source):
    registered_endpoint(source)
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
def test_guard_rejects_unprotected_inherited_or_ineffective_post(registered_endpoint, source):
    registered_endpoint(source)
    with pytest.raises(AssertionError, match="new.py"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "options",
    ["method=['GET']", "method='POST', rate=None", "method='POST'"],
    ids=["get_only", "no_rate", "missing_rate"],
)
def test_guard_rejects_limits_without_effective_post_method_or_rate(registered_endpoint, options):
    rate = "" if "rate=" in options or options == "method='POST'" else "rate='1/h', "
    registered_endpoint(
        f"@require_POST\n@ratelimit(key='ip', {rate}{options}, block=True)\ndef new_endpoint(request):\n    pass\n"
    )
    with pytest.raises(AssertionError, match="new.py"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "options",
    ["method='post'", "method=('GET', 'POST')", "method=ALL", ""],
    ids=["lowercase_method", "method_tuple", "library_all", "default_all"],
)
def test_library_supported_post_methods_pass_guard(registered_endpoint, options):
    registered_endpoint(
        "from django_ratelimit import ALL\n"
        "@require_POST\n"
        f"@ratelimit(key='ip', rate='1/h', block=True{', ' + options if options else ''})\n"
        "def new_endpoint(request):\n    pass\n"
    )
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
def test_mro_and_dispatch_cannot_borrow_a_bypassed_handler_limit(registered_endpoint, source):
    registered_endpoint(source)
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
def test_dispatch_and_decorator_redirections_need_an_applied_limit(registered_endpoint, source):
    registered_endpoint(source)
    with pytest.raises(AssertionError, match="NewView.post"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize("rate", [(float("inf"), 1), (1, float("inf"))], ids=["unbounded_count", "unbounded_period"])
def test_unbounded_configured_rates_are_not_a_throttle(registered_endpoint, settings, rate):
    settings.TEST_MUTATION_RATE = rate
    registered_endpoint(
        "from django.conf import settings\n"
        "@require_POST\n"
        "@ratelimit(key='ip', rate=settings.TEST_MUTATION_RATE, method='POST', block=True)\n"
        "def new_endpoint(request):\n    pass\n"
    )
    with pytest.raises(AssertionError, match="new.py"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "source",
    [
        "\n".join(
            [
                "WRITE_METHODS = ['POST']",
                "@require_http_methods(WRITE_METHODS)",
                "def new_endpoint(request):",
                "    return HttpResponse('ok')",
                "",
            ]
        ),
        "class NewView(View):\n    def get(self, request):\n        return HttpResponse('ok')\n    post = get\n",
        "@method_decorator(ratelimit(key='ip', rate='1/h', block=True), name='dispatch')\n"
        "class Base(View):\n    def post(self, request):\n        return HttpResponse('ok')\n"
        "class NewView(Base):\n    dispatch = View.dispatch\n",
    ],
    ids=["named_http_methods", "aliased_post_handler", "assigned_dispatch_override"],
)
def test_registered_static_declarations_cannot_escape_guard(registered_endpoint, ratelimit_enabled, source):
    from django.test import RequestFactory
    from django_ratelimit.exceptions import Ratelimited

    callback = registered_endpoint(source)
    factory = RequestFactory()
    assert [callback(factory.post("/probe/")).status_code for _ in range(3)] == [200, 200, 200]
    if "WRITE_METHODS" in source:
        assert callback(factory.get("/probe/")).status_code == 405
    if "dispatch = View.dispatch" in source:
        base_callback = callback.view_class.__mro__[1].as_view()
        assert base_callback(factory.post("/probe/")).status_code == 200
        with pytest.raises(Ratelimited):
            base_callback(factory.post("/probe/"))
    with pytest.raises(AssertionError, match="new.py"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "source",
    [
        "from functools import wraps\n"
        "def bypass(fn):\n"
        "    @wraps(fn)\n"
        "    def wrapper(self, request):\n        return self.get(request)\n"
        "    return wrapper\n"
        "class NewView(View):\n"
        "    def get(self, request):\n        pass\n"
        "    @method_decorator([bypass, ratelimit(key='ip', rate='1/h', block=True)])\n"
        "    def post(self, request):\n        pass\n",
        "from functools import wraps\n"
        "class NewView(View):\n"
        "    @method_decorator(ratelimit(key='ip', rate='1/h', block=True))\n"
        "    def get(self, request):\n        pass\n"
        "    @wraps(get)\n"
        "    def post(self, request):\n        pass\n",
    ],
    ids=["method_decorator_bypass", "copied_handler_marker"],
)
def test_unknown_wrappers_cannot_borrow_copied_limit_evidence(registered_endpoint, source):
    registered_endpoint(source)
    with pytest.raises(AssertionError, match="NewView.post"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


def test_custom_method_rejection_is_not_proof_of_a_safe_request(registered_endpoint):
    registered_endpoint(
        "class NewView(View):\n    def http_method_not_allowed(self, request, *args, **kwargs):\n        pass\n"
    )
    with pytest.raises(AssertionError, match="NewView.post"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


def test_callback_metadata_cannot_replace_its_actual_class_binding(registered_endpoint):
    callback = registered_endpoint("class NewView(View):\n    def post(self, request):\n        pass\n")
    protected = registered_endpoint(
        "@method_decorator(ratelimit(key='ip', rate='1/h', block=True), name='post')\n"
        "class NewView(View):\n    def post(self, request):\n        pass\n"
    ).view_class
    callback.view_class = protected
    from django.urls import get_resolver, path

    get_resolver().urlconf_module.urlpatterns = [path("probe/", callback, name="probe")]
    with pytest.raises(AssertionError, match="NewView.post"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


def test_assigned_framework_dispatch_cannot_borrow_a_skipped_parent_limit(registered_endpoint):
    registered_endpoint(
        "from django.contrib.auth.mixins import LoginRequiredMixin\n"
        "@method_decorator(ratelimit(key='ip', rate='1/h', block=True), name='dispatch')\n"
        "class Base(LoginRequiredMixin, View):\n    def post(self, request):\n        pass\n"
        "class NewView(Base):\n    dispatch = LoginRequiredMixin.dispatch\n"
    )
    with pytest.raises(AssertionError, match="NewView.post"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize(
    "source",
    [
        "class NewView(View):\n"
        "    def __new__(cls, **kwargs):\n"
        "        instance = super().__new__(cls)\n"
        "        instance.dispatch = lambda request: HttpResponse('ok')\n"
        "        return instance\n"
        "    @method_decorator(ratelimit(key='ip', rate='1/h', block=True))\n"
        "    def post(self, request):\n        return HttpResponse('ok')\n",
        "class CustomMeta(type):\n"
        "    def __call__(cls, **kwargs):\n"
        "        instance = super().__call__(**kwargs)\n"
        "        instance.dispatch = lambda request: HttpResponse('ok')\n"
        "        return instance\n"
        "class NewView(View, metaclass=CustomMeta):\n"
        "    @method_decorator(ratelimit(key='ip', rate='1/h', block=True))\n"
        "    def post(self, request):\n        return HttpResponse('ok')\n",
        "@ratelimit(key='ip', rate='1/h', block=True)\n"
        "def protected(request):\n    return HttpResponse('ok')\n"
        "class DynamicHandler:\n"
        "    def __get__(self, instance, owner):\n"
        "        return protected if instance is None else lambda request: HttpResponse('ok')\n"
        "class NewView(View):\n    post = DynamicHandler()\n",
        "class DynamicMethods:\n"
        "    def __get__(self, instance, owner):\n"
        "        return ['get'] if instance is None else ['post']\n"
        "class NewView(View):\n"
        "    http_method_names = DynamicMethods()\n"
        "    def post(self, request):\n        return HttpResponse('ok')\n",
    ],
    ids=["custom_new", "custom_metaclass", "handler_descriptor", "method_descriptor"],
)
def test_instance_bindings_cannot_bypass_a_class_level_proof(registered_endpoint, ratelimit_enabled, source):
    from django.test import RequestFactory

    callback = registered_endpoint(source)
    factory = RequestFactory()
    assert [callback(factory.post("/probe/")).status_code for _ in range(3)] == [200, 200, 200]
    with pytest.raises(AssertionError, match="NewView.post"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


def test_marked_function_limit_both_passes_guard_and_blocks_requests(registered_endpoint, ratelimit_enabled):
    from django.test import RequestFactory
    from django_ratelimit.exceptions import Ratelimited

    callback = registered_endpoint(
        "WRITE_METHODS = ['POST']\n"
        "@require_http_methods(WRITE_METHODS)\n"
        "@ratelimit(key='ip', rate='1/h', method='POST', block=True)\n"
        "def new_endpoint(request):\n    return HttpResponse('ok')\n"
    )
    MutationGuard().test_all_post_handlers_have_ratelimit()
    factory = RequestFactory()
    assert callback(factory.post("/probe/")).status_code == 200
    with pytest.raises(Ratelimited):
        callback(factory.post("/probe/"))


def test_cls_metadata_resolves_the_actual_registered_binding(registered_endpoint):
    callback = registered_endpoint(
        "class NewView(View):\n"
        "    @method_decorator(ratelimit(key='ip', rate='1/h', method='POST', block=True))\n"
        "    def post(self, request):\n        pass\n"
    )
    callback.cls = callback.view_class
    del callback.view_class
    MutationGuard().test_all_post_handlers_have_ratelimit()


def test_logout_exception_is_bound_to_its_url_name_and_target(registered_endpoint):
    from django.urls import clear_url_caches, get_resolver, path

    from core.views.auth import CustomLogoutView

    registered_endpoint("class NewView(View):\n    def post(self, request):\n        pass\n")
    module = get_resolver().urlconf_module
    callback = CustomLogoutView.as_view()
    module.urlpatterns = [path("logout/", callback, name="logout")]
    MutationGuard().test_all_post_handlers_have_ratelimit()
    module.urlpatterns = [path("other/", callback, name="other_logout")]
    clear_url_caches()
    with pytest.raises(AssertionError, match="other_logout"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


def test_manual_metadata_without_the_library_wrapper_cannot_prove_a_limit(registered_endpoint, ratelimit_enabled):
    from django.test import RequestFactory

    callback = registered_endpoint(
        "from core.utils.ratelimit import AppliedRateLimit, RateLimit\n"
        "def fake_limit(handler):\n"
        "    limit = RateLimit(key='ip', rate='1/h', method='POST', block=True)\n"
        "    group, key, rate, method, block = limit.group, limit.key, limit.rate, limit.method, limit.block\n"
        "    fn = handler\n"
        "    def wrapper(request):\n"
        "        options = (group, key, rate, method, block)\n"
        "        return fn(request) if options else None\n"
        "    wrapper._blocking_rate_limit = AppliedRateLimit(wrapper, wrapper.__code__, limit)\n"
        "    return wrapper\n"
        "@require_POST\n"
        "@fake_limit\n"
        "def new_endpoint(request):\n    return HttpResponse('ok')\n"
    )
    factory = RequestFactory()
    assert [callback(factory.post("/probe/")).status_code for _ in range(3)] == [200, 200, 200]
    with pytest.raises(AssertionError, match="new.py"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


def test_redirect_customization_cannot_hide_an_unmarked_callable(registered_endpoint):
    from django.test import RequestFactory
    from django.urls import get_resolver

    callback = registered_endpoint(
        "from django.views.generic import RedirectView\n"
        "calls = []\n"
        "def custom_redirect(**kwargs):\n"
        "    calls.append('write')\n"
        "    return '/target/'\n"
        "new_endpoint = RedirectView.as_view(get_redirect_url=custom_redirect)\n"
    )
    factory = RequestFactory()
    assert [callback(factory.post("/probe/")).status_code for _ in range(3)] == [302, 302, 302]
    assert get_resolver().urlconf_module.calls == ["write", "write", "write"]
    with pytest.raises(AssertionError, match="probe"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize("borrowed", ["http_filter", "class_callback", "bound_function"])
def test_callable_objects_cannot_borrow_another_execution_binding(registered_endpoint, borrowed):
    from django.test import RequestFactory

    if borrowed == "class_callback":
        prefix = (
            "@method_decorator(ratelimit(key='ip', rate='1/h', block=True), name='post')\n"
            "class ProtectedView(View):\n    def post(self, request):\n        return HttpResponse('ok')\n"
            "expected = ProtectedView.as_view()\n"
        )
    else:
        prefix = "@require_http_methods(['GET'])\ndef expected(request):\n    return HttpResponse('ok')\n"
    source = prefix + (
        "class ShadowCallable:\n"
        "    def __init__(self):\n"
        "        self.__dict__.update(expected.__dict__)\n"
        "        self.__code__ = expected.__code__\n"
        "        self.__closure__ = expected.__closure__\n"
        "    def __call__(self, request):\n        return HttpResponse('unguarded')\n"
        "new_endpoint = ShadowCallable()\n"
    )
    if borrowed == "bound_function":
        source += "new_endpoint.__func__ = expected\n"
    callback = registered_endpoint(source)
    factory = RequestFactory()
    assert [callback(factory.post("/probe/")).status_code for _ in range(3)] == [200, 200, 200]
    with pytest.raises(AssertionError, match="probe"):
        MutationGuard().test_all_post_handlers_have_ratelimit()


@pytest.mark.parametrize("binding", ["instance_handler", "decorator_object"])
def test_overridden_execution_binding_cannot_borrow_class_or_decorator_evidence(registered_endpoint, binding):
    from django.test import RequestFactory

    if binding == "instance_handler":
        source = (
            "class NewView(View):\n"
            "    http_method_names = ['get']\n"
            "    @method_decorator(ratelimit(key='ip', rate='1/h', method='POST', block=True))\n"
            "    def post(self, request):\n        return HttpResponse('protected')\n"
            "new_endpoint = NewView.as_view(http_method_names=['post'], "
            "post=lambda request: HttpResponse('unguarded'))\n"
        )
    else:
        source = (
            "expected = require_http_methods(['GET'])\n"
            "class ShadowDecorator:\n"
            "    __code__ = expected.__code__\n"
            "    def __call__(self, handler):\n"
            "        return expected(handler) if handler.__name__ == 'dummy' else handler\n"
            "@method_decorator(ShadowDecorator(), name='post')\n"
            "class NewView(View):\n    def post(self, request):\n        return HttpResponse('unguarded')\n"
        )
    callback = registered_endpoint(source)
    factory = RequestFactory()
    assert [callback(factory.post("/probe/")).status_code for _ in range(3)] == [200, 200, 200]
    with pytest.raises(AssertionError, match="probe"):
        MutationGuard().test_all_post_handlers_have_ratelimit()
