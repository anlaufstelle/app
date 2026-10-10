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
        "    pass\n"
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
