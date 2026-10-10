"""Installed admin assets take part in the escaping and CSRF inventories."""

import sys
from types import SimpleNamespace

import pytest

from tests import test_architecture_guards_csrf_exempt as csrf_guard
from tests import test_architecture_guards_safe_markup as markup_guard

pytestmark = pytest.mark.architecture


@pytest.fixture
def vendor_package(tmp_path, monkeypatch):
    package = tmp_path / "guard_probe_vendor"
    package.mkdir()
    (package / "__init__.py").write_text("")
    templates = package / "templates" / "admin"
    templates.mkdir(parents=True)
    (templates / "probe.html").write_text("{{ user_input|safe }}")
    (package / "views.py").write_text(
        "from django.views.decorators.csrf import csrf_exempt\n@csrf_exempt\ndef probe(request):\n    pass\n"
    )
    monkeypatch.setitem(sys.modules, "guard_probe_vendor", SimpleNamespace(__file__=str(package / "__init__.py")))
    monkeypatch.setattr(markup_guard, "VENDOR_PACKAGES", ("guard_probe_vendor",), raising=False)
    return package


def test_vendor_template_bypass_is_not_silently_excluded(vendor_package):
    found = markup_guard.violations(markup_guard._repo_template_findings(), markup_guard.TEMPLATE_ALLOWLIST)
    assert "vendor/guard_probe_vendor/templates/admin/probe.html:1 (filter:safe)" in found


def test_vendor_python_csrf_bypass_is_not_silently_excluded(vendor_package):
    found = markup_guard.violations(csrf_guard._repo_findings(), csrf_guard.CSRF_EXEMPT_ALLOWLIST)
    assert "vendor/guard_probe_vendor/views.py:2 (probe)" in found


def test_real_admin_template_inventory_is_nonempty():
    found = markup_guard._repo_template_findings()
    assert any(f.relpath.startswith("vendor/unfold/templates/") for f in found)
    assert any(f.relpath.startswith("vendor/django.contrib.admin/templates/") for f in found)
