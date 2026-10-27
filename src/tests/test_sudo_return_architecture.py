"""Every registered sudo action declares a GET return route (Refs #2640)."""

from uuid import UUID

import pytest
from django.urls import URLResolver, get_resolver, resolve, reverse
from django.urls.converters import UUIDConverter

from core.services.security import RequireSudoModeMixin

pytestmark = pytest.mark.architecture


def _sudo_routes(patterns, namespaces=(), converters=None):
    for pattern in patterns:
        route_converters = (converters or {}) | pattern.pattern.converters
        if isinstance(pattern, URLResolver):
            child_namespaces = namespaces + ((pattern.namespace,) if pattern.namespace else ())
            yield from _sudo_routes(pattern.url_patterns, child_namespaces, route_converters)
        else:
            view_class = getattr(pattern.callback, "view_class", None)
            if view_class is not None and issubclass(view_class, RequireSudoModeMixin):
                assert pattern.name, view_class.__name__
                yield ":".join((*namespaces, pattern.name)), view_class, route_converters


SUDO_ROUTES = list(_sudo_routes(get_resolver().url_patterns))
assert SUDO_ROUTES, "The sudo return guard must inspect registered views."


def _supports_get(view_class):
    return "get" in view_class.http_method_names and callable(getattr(view_class, "get", None))


@pytest.mark.parametrize(("url_name", "view_class", "converters"), SUDO_ROUTES, ids=[r[0] for r in SUDO_ROUTES])
def test_sudo_action_has_get_or_explicit_get_return(rf, url_name, view_class, converters):
    if _supports_get(view_class):
        return
    kwargs = {
        name: UUID(int=1) if isinstance(converter, UUIDConverter) else converter.to_python("1")
        for name, converter in converters.items()
    }
    view = view_class()
    view.setup(rf.get(reverse(url_name, kwargs=kwargs)), **kwargs)
    target = view.get_sudo_return_url()
    assert target, f"{view_class.__name__} must declare a sudo return route."
    assert target.startswith("/") and not target.startswith("//"), target
    target_class = getattr(resolve(target).func, "view_class", None)
    assert target_class is not None and _supports_get(target_class), (url_name, target)
