"""Architecture guard — CSRF protection is switched off only where reviewed (Refs #2468).

Every state-changing request passes ``CsrfViewMiddleware``. ``csrf_exempt``
(as decorator, via ``method_decorator`` or as call around a view) and the
request flag ``_dont_enforce_csrf_checks`` remove that check for a view.
This guard allows both only with an allowlist entry ``(path, qualname)`` that
states why the endpoint is safe without a token. The qualname is the class or
function the reference belongs to; a decorator counts for the decorated
object, a module-level ``csrf_exempt(view)`` call is keyed ``"<module>"``.

The AST scanner is shared with ``test_architecture_guards_safe_markup.py``;
probes run against ``tmp_path``, the repository-wide check lists files via
``git ls-files``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings

from tests.test_architecture_guards_safe_markup import (
    REPO_ROOT,
    production_python_files,
    scan_python,
    stale_entries,
    violations,
)

pytestmark = pytest.mark.architecture

CSRF_BYPASS_NAMES = frozenset({"csrf_exempt", "_dont_enforce_csrf_checks"})

CSRF_EXEMPT_ALLOWLIST: dict[tuple[str, str], str] = {
    ("src/core/views/csp_report.py", "CSPReportView"): (
        "Browser-generated CSP violation reports carry no CSRF token. The view "
        "only accepts POST, writes a sanitised log line and changes no "
        "application state; it is rate-limited per IP (10/m) and caps the "
        "violations per report."
    ),
}


def _repo_findings():
    return scan_python(REPO_ROOT, CSRF_BYPASS_NAMES, production_python_files(REPO_ROOT))


class TestCsrfExemptAllowlist:
    def test_no_csrf_bypass_outside_allowlist(self):
        found = violations(_repo_findings(), CSRF_EXEMPT_ALLOWLIST)
        assert not found, (
            "CSRF protection is switched off (csrf_exempt / _dont_enforce_csrf_checks) "
            "without an allowlist entry. Keep the token check, or add an entry to "
            "CSRF_EXEMPT_ALLOWLIST with the reason the endpoint is safe without it "
            f"(Refs #2468).\nFound: {found}"
        )

    def test_allowlist_has_no_stale_entries(self):
        assert not stale_entries(_repo_findings(), CSRF_EXEMPT_ALLOWLIST)

    def test_scanner_sees_the_real_tree(self):
        """Anti-vacuity: without its allowlist the real tree must be red."""
        assert violations(_repo_findings(), {}), "csrf scan found nothing — file list empty?"

    def test_csrf_middleware_is_active(self):
        assert "django.middleware.csrf.CsrfViewMiddleware" in settings.MIDDLEWARE


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestCsrfScannerProbes:
    def test_method_decorator_on_class_is_a_violation(self, tmp_path):
        _write(
            tmp_path,
            "src/core/views/hook.py",
            "from django.utils.decorators import method_decorator\n"
            "from django.views.decorators.csrf import csrf_exempt\n"
            "\n"
            '@method_decorator(csrf_exempt, name="dispatch")\n'
            "class HookView:\n"
            "    pass\n",
        )
        found = violations(scan_python(tmp_path, CSRF_BYPASS_NAMES), CSRF_EXEMPT_ALLOWLIST)
        assert found == ["src/core/views/hook.py:4 (HookView)"]

    def test_function_decorator_alias_and_module_level_call_are_found(self, tmp_path):
        _write(
            tmp_path,
            "urls.py",
            "from django.views.decorators.csrf import csrf_exempt as no_csrf\n"
            "@no_csrf\n"
            "def view(request):\n"
            "    request._dont_enforce_csrf_checks = True\n"
            "urlpatterns = [no_csrf(view)]\n",
        )
        keys = [(f.lineno, f.key) for f in scan_python(tmp_path, CSRF_BYPASS_NAMES)]
        assert keys == [(2, "view"), (4, "view"), (5, "<module>")]

    def test_allowlisted_view_passes(self, tmp_path):
        _write(
            tmp_path,
            "src/core/views/csp_report.py",
            "from django.views.decorators.csrf import csrf_exempt\n"
            "@method_decorator(csrf_exempt, name='dispatch')\n"
            "class CSPReportView:\n"
            "    pass\n",
        )
        assert violations(scan_python(tmp_path, CSRF_BYPASS_NAMES), CSRF_EXEMPT_ALLOWLIST) == []

    def test_other_csrf_decorators_are_not_findings(self, tmp_path):
        _write(
            tmp_path,
            "v.py",
            "from django.views.decorators.csrf import ensure_csrf_cookie\n@ensure_csrf_cookie\ndef v(r):\n    pass\n",
        )
        assert scan_python(tmp_path, CSRF_BYPASS_NAMES) == []
