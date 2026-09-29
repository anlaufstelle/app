"""Refs #1904: the delivered policy forbids ``<base>`` rewrites and foreign form targets.

``base-uri 'none'``: an injected ``<base href="https://attacker.example/">``
would silently re-point every relative URL of the page (scripts, form
targets, links) to a foreign origin. No template needs ``<base>``, so the
directive allows none at all.

``form-action 'self'``: a form (or a button's ``formaction``) may only submit
to the application's own origin; Chromium applies the directive to redirects
after the submit as well. An injected form cannot send typed-in data (for
example a password) to a foreign host.

Two promises, both checked against the *delivered* header:

1. **Every kind of response** carries both directives with exactly these
   values: regular pages (anonymous and signed in), the error pages 403, CSRF
   403, 404 and 500, the maintenance 503, the admin login (with its CSP
   relaxations active) and a demo page with analytics enabled. The header is
   split into directives and the tokens are compared exactly — a substring
   match over the whole header would also accept ``form-action 'self' *``.
2. **Exactly one source:** the policy comes from
   ``CONTENT_SECURITY_POLICY`` in ``settings/base.py`` via django-csp. No
   Caddyfile sets a CSP header, no template declares one via ``<meta>``, and
   no production code writes the header itself (the only writer is the
   rewrite helper in ``core/middleware/_csp.py``) or overrides the policy per
   view via ``csp.decorators``.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import logging
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import reverse

from tests.test_security_headers_single_source import caddy_header_names

REPO_ROOT = Path(__file__).resolve().parents[2]

ENFORCED_HEADER = "Content-Security-Policy"
REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"
CSP_HEADER_NAMES = frozenset({ENFORCED_HEADER.lower(), REPORT_ONLY_HEADER.lower()})

#: The two directives this guard pins, with their exact token lists.
EXPECTED_DIRECTIVES = {
    "base-uri": ["'none'"],
    "form-action": ["'self'"],
}

#: The one module allowed to write the header: the shared rewrite helper of
#: the per-request relax middlewares (it only rewrites what django-csp set).
CSP_REWRITE_HELPER = "src/core/middleware/_csp.py"

_UMAMI_SCRIPT_SRC = "https://umami.example.org/main.js"
_UMAMI_ORIGIN = "https://umami.example.org"


# --- Header parsing and the shared assertion ---------------------------------


def parse_policy(header: str) -> list[tuple[str, list[str]]]:
    """Split a CSP header into ``(directive, tokens)`` pairs, in header order.

    Duplicates are kept on purpose: browsers honour only the first
    occurrence, so a second ``form-action`` would point to a second source.
    """
    directives = []
    for part in header.split(";"):
        tokens = part.split()
        if tokens:
            directives.append((tokens[0].lower(), tokens[1:]))
    return directives


def directive_tokens(header: str, name: str) -> list[str]:
    """Tokens of the first occurrence of ``name`` (empty if absent)."""
    for directive, tokens in parse_policy(header):
        if directive == name:
            return tokens
    return []


def assert_base_and_form_locked(response, label: str) -> str:
    """Both directives are present exactly once with exactly the pinned tokens.

    A report-only header, where present, must not contradict them. Returns
    the enforced header for further, test-specific checks.
    """
    enforced = response.headers.get(ENFORCED_HEADER)
    assert enforced, f"{label}: response carries no {ENFORCED_HEADER} header"
    directives = parse_policy(enforced)
    for name, tokens in EXPECTED_DIRECTIVES.items():
        occurrences = [pair for pair in directives if pair[0] == name]
        assert occurrences == [(name, tokens)], (
            f"{label}: expected exactly one '{name} {' '.join(tokens)}', got {occurrences!r} in {enforced!r}"
        )
    report_only = response.headers.get(REPORT_ONLY_HEADER)
    if report_only:
        for name, tokens in parse_policy(report_only):
            if name in EXPECTED_DIRECTIVES:
                assert tokens == EXPECTED_DIRECTIVES[name], (
                    f"{label}: report-only '{name}' {tokens!r} contradicts the enforced policy"
                )
    return enforced


@contextmanager
def silenced_logger(name: str):
    """Mute an expected error log for the duration of a block (500 probe)."""
    logger = logging.getLogger(name)
    previous = logger.disabled
    logger.disabled = True
    try:
        yield
    finally:
        logger.disabled = previous


def _response_with(**headers) -> HttpResponse:
    response = HttpResponse("probe")
    for name, value in headers.items():
        response.headers[name.replace("_", "-")] = value
    return response


class TestAssertionRejectsWeakPolicies:
    """The shared assertion itself must fail on every weakened variant —
    otherwise the delivered-header tests below would prove nothing."""

    GOOD = "default-src 'self'; base-uri 'none'; form-action 'self'"

    def test_accepts_the_pinned_policy(self):
        assert_base_and_form_locked(_response_with(Content_Security_Policy=self.GOOD), "probe")

    @pytest.mark.parametrize(
        "policy",
        [
            "default-src 'self'",
            "default-src 'self'; base-uri 'none'",
            "default-src 'self'; form-action 'self'",
            "default-src 'self'; base-uri 'self'; form-action 'self'",
            "default-src 'self'; base-uri 'none'; form-action 'self' https://pay.example.org",
            "default-src 'self'; base-uri 'none'; form-action *",
            "default-src 'self'; base-uri 'none'; form-action 'self'; form-action *",
        ],
        ids=["none", "no-form-action", "no-base-uri", "base-self", "extra-origin", "wildcard", "duplicate"],
    )
    def test_rejects_weakened_policy(self, policy):
        with pytest.raises(AssertionError):
            assert_base_and_form_locked(_response_with(Content_Security_Policy=policy), "probe")

    def test_rejects_missing_header(self):
        with pytest.raises(AssertionError):
            assert_base_and_form_locked(HttpResponse("probe"), "probe")

    def test_rejects_contradicting_report_only_header(self):
        response = _response_with(
            Content_Security_Policy=self.GOOD,
            Content_Security_Policy_Report_Only="default-src 'self'; form-action *",
        )
        with pytest.raises(AssertionError):
            assert_base_and_form_locked(response, "probe")

    def test_accepts_report_only_header_without_the_directives(self):
        response = _response_with(
            Content_Security_Policy=self.GOOD,
            Content_Security_Policy_Report_Only="default-src 'self'",
        )
        assert_base_and_form_locked(response, "probe")


# --- Promise 1: every kind of response carries both directives ---------------


@pytest.mark.django_db
class TestDeliveredPolicy:
    def test_anonymous_login_page(self, client):
        response = client.get(reverse("login"))
        assert response.status_code == 200
        assert_base_and_form_locked(response, "login page")

    def test_signed_in_page(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        assert_base_and_form_locked(response, "timeline")

    def test_not_found_page(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get("/no-such-page-for-the-csp-guard/")
        assert response.status_code == 404
        assert_base_and_form_locked(response, "404")

    def test_permission_denied_page(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:audit_log"))
        assert response.status_code == 403
        assert "403.html" in [template.name for template in response.templates]
        assert_base_and_form_locked(response, "403")

    def test_csrf_failure_page(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(reverse("login"), {"username": "x", "password": "y"})
        assert response.status_code == 403
        assert "403_csrf.html" in [template.name for template in response.templates]
        assert_base_and_form_locked(response, "CSRF 403")

    def test_server_error_page(self, monkeypatch):
        """Independent of which handler renders the 500 page — only the
        delivered header counts."""
        from core.views.auth import CustomLoginView

        def broken_get(self, request, *args, **kwargs):
            raise RuntimeError("probe failure for the CSP guard")

        monkeypatch.setattr(CustomLoginView, "get", broken_get)
        with silenced_logger("django.request"):
            response = Client(raise_request_exception=False).get(reverse("login"))
        assert response.status_code == 500
        assert_base_and_form_locked(response, "500")

    def test_maintenance_page(self, tmp_path):
        from core.middleware.maintenance import MaintenanceModeMiddleware

        flag_file = tmp_path / "maintenance.flag"
        flag_file.write_text("", encoding="utf-8")
        MaintenanceModeMiddleware._cache = None
        try:
            with override_settings(MAINTENANCE_FLAG_FILE=str(flag_file)):
                response = Client().get("/clients/")
            assert response.status_code == 503, "maintenance mode was not active"
            assert_base_and_form_locked(response, "maintenance 503")
        finally:
            MaintenanceModeMiddleware._cache = None

    def test_admin_login_keeps_both_directives_and_its_relaxations(self, client):
        response = client.get("/admin-mgmt/login/")
        assert response.status_code == 200
        enforced = assert_base_and_form_locked(response, "admin login")
        # The admin relaxations are still active — the directives above were
        # passed through the rewrite, not dropped by it.
        assert "'unsafe-eval'" in directive_tokens(enforced, "script-src")
        assert "'unsafe-inline'" in directive_tokens(enforced, "style-src")

    @override_settings(DEMO_MODE=True, UMAMI_SCRIPT_SRC=_UMAMI_SCRIPT_SRC)
    def test_demo_page_with_analytics_keeps_both_directives(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        enforced = assert_base_and_form_locked(response, "demo page with analytics")
        assert _UMAMI_ORIGIN in directive_tokens(enforced, "script-src")
        assert _UMAMI_ORIGIN in directive_tokens(enforced, "connect-src")


@pytest.mark.django_db
@pytest.mark.parametrize("directive", ["base-uri", "form-action"])
def test_report_endpoint_accepts_violations_of_the_new_directives(client, caplog, directive):
    """A browser report about either directive is logged like any other."""
    report = {
        "csp-report": {
            "document-uri": "https://anlaufstelle.example/clients/",
            "violated-directive": directive,
            "effective-directive": directive,
            "original-policy": "base-uri 'none'; form-action 'self'; report-uri /csp-report/",
            "blocked-uri": "https://attacker.example/collect",
        }
    }
    with caplog.at_level(logging.WARNING, logger="security.csp"):
        response = client.post(reverse("csp_report"), data=json.dumps(report), content_type="application/csp-report")
    assert response.status_code == 204
    violations = [record for record in caplog.records if record.message == "csp_violation"]
    assert len(violations) == 1
    assert violations[0].csp_violation["effective-directive"] == directive


_DEPLOY_SETTINGS_ENV = {
    "PYTHONPATH": str(REPO_ROOT / "src"),
    "DJANGO_LOAD_DOTENV": "false",
    "POSTGRES_DB": "x",
    "POSTGRES_USER": "u",
    "POSTGRES_PASSWORD": "p" * 40,
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "DJANGO_SECRET_KEY": "x" * 50,
    "ALLOWED_HOSTS": "example.org",
    "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
    "ENCRYPTION_KEYS": "",
    "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    "UMAMI_SCRIPT_SRC": _UMAMI_SCRIPT_SRC,
    "UMAMI_WEBSITE_ID": "22222222-3333-4444-5555-666666666666",
}


@pytest.mark.parametrize("module", ["prod", "devlive", "demo"])
def test_deployed_settings_modules_inherit_both_directives(module):
    """The deployed settings modules copy or extend the base policy; none of
    them may drop or rewrite the two directives."""
    code = (
        "import importlib, json;"
        f"d = importlib.import_module('anlaufstelle.settings.{module}').CONTENT_SECURITY_POLICY['DIRECTIVES'];"
        "print(json.dumps({k: d.get(k) for k in ('base-uri', 'form-action', 'script-src')}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={"PATH": os.environ.get("PATH", ""), "DJANGO_SETTINGS_MODULE": f"anlaufstelle.settings.{module}"}
        | _DEPLOY_SETTINGS_ENV,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    directives = json.loads(result.stdout.strip().splitlines()[-1])
    assert directives["base-uri"] == EXPECTED_DIRECTIVES["base-uri"], module
    assert directives["form-action"] == EXPECTED_DIRECTIVES["form-action"], module
    # devlive widens script-src globally for analytics — proof that the
    # overlay was loaded, not just the base policy.
    assert (_UMAMI_ORIGIN in directives["script-src"]) is (module == "devlive")


# --- Promise 2: exactly one source -------------------------------------------


def tracked_files(root: Path, *pathspecs: str) -> list[Path]:
    """Files under version control (``git ls-files``), glob fallback without git.

    Untracked local files must not trip the guard; they never reach a commit.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--", *pathspecs],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0:
        files = [root / rel for rel in proc.stdout.decode("utf-8").split("\0") if rel]
    else:
        # git-style pathspecs: ``*`` also matches ``/``, a plain path is a prefix.
        files = [
            path
            for path in root.rglob("*")
            if any(
                fnmatch.fnmatchcase(rel := path.relative_to(root).as_posix(), spec) or rel.startswith(f"{spec}/")
                for spec in pathspecs
            )
        ]
    return sorted(path for path in files if path.is_file())


def caddyfiles_setting_a_policy(paths: list[Path]) -> list[str]:
    """Caddyfiles whose ``header {}`` blocks set a CSP header."""
    return [path.name for path in paths if caddy_header_names(path.read_text(encoding="utf-8")) & CSP_HEADER_NAMES]


_META_POLICY_RE = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*[\"']?content-security-policy", re.IGNORECASE)


def templates_declaring_a_policy(paths: list[Path]) -> list[str]:
    """Templates with a ``<meta http-equiv="Content-Security-Policy">`` tag."""
    return [str(path) for path in paths if _META_POLICY_RE.search(path.read_text(encoding="utf-8"))]


def _is_csp_header_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() in CSP_HEADER_NAMES


def policy_writes(source: str) -> list[int]:
    """Line numbers where Python code writes a CSP header or overrides the policy.

    Covered: ``obj["Content-Security-Policy"] = …`` (also on ``.headers``),
    ``.setdefault(…)``/``.__setitem__(…)`` with the header name, dict literals
    keyed by it (``headers={…}``, ``.update({…})``), imports of
    ``csp.decorators`` and assignments to django-csp's per-response
    ``_csp_*`` attributes. Comments and docstrings are ignored by design.
    """
    return sorted(node.lineno for node in ast.walk(ast.parse(source)) if _is_policy_write(node))


def _is_policy_write(node: ast.AST) -> bool:
    if isinstance(node, ast.Subscript):
        return isinstance(node.ctx, ast.Store) and _is_csp_header_name(node.slice)
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"setdefault", "__setitem__"}
            and bool(node.args)
            and _is_csp_header_name(node.args[0])
        )
    if isinstance(node, ast.Dict):
        return any(key is not None and _is_csp_header_name(key) for key in node.keys)
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").startswith("csp.decorators")
    if isinstance(node, ast.Import):
        return any(alias.name.startswith("csp.decorators") for alias in node.names)
    if isinstance(node, ast.Attribute):
        return isinstance(node.ctx, ast.Store) and node.attr.startswith("_csp_")
    return False


def production_modules_writing_the_policy(root: Path, paths: list[Path]) -> list[str]:
    offenders = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        if rel == CSP_REWRITE_HELPER:
            continue
        for line in policy_writes(path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}:{line}")
    return offenders


def _production_python_files(root: Path) -> list[Path]:
    return [path for path in tracked_files(root, "src/*.py") if "tests" not in path.relative_to(root / "src").parts]


@pytest.mark.architecture
class TestSingleSource:
    def test_no_caddyfile_sets_a_policy(self):
        caddyfiles = tracked_files(REPO_ROOT, "Caddyfile*", "*/Caddyfile*")
        assert any(path.name == "Caddyfile" for path in caddyfiles), "production Caddyfile not found"
        assert caddyfiles_setting_a_policy(caddyfiles) == [], (
            "The CSP comes from the application (settings/base.py); a proxy header would "
            "duplicate or silently override it."
        )

    def test_no_template_declares_a_policy_via_meta(self):
        templates = [path for path in tracked_files(REPO_ROOT, "src/templates") if path.suffix == ".html"]
        assert templates, "no templates found"
        assert templates_declaring_a_policy(templates) == []

    def test_no_production_code_writes_the_policy_header(self):
        modules = _production_python_files(REPO_ROOT)
        assert any(path.relative_to(REPO_ROOT).as_posix() == CSP_REWRITE_HELPER for path in modules)
        assert production_modules_writing_the_policy(REPO_ROOT, modules) == [], (
            "Only django-csp (from CONTENT_SECURITY_POLICY) and the rewrite helper in "
            f"{CSP_REWRITE_HELPER} may set the header."
        )


class TestSingleSourceScannersCatchProbes:
    """The scanners report a planted probe — probes live in ``tmp_path`` only."""

    def test_caddyfile_probe(self, tmp_path):
        probe = tmp_path / "Caddyfile"
        probe.write_text(
            "example.org {\n\theader {\n\t\tX-Robots-Tag noindex\n"
            '\t\t+Content-Security-Policy "form-action *"\n\t}\n}\n',
            encoding="utf-8",
        )
        clean = tmp_path / "Caddyfile.clean"
        clean.write_text("example.org {\n\theader {\n\t\tX-Robots-Tag noindex\n\t}\n}\n", encoding="utf-8")
        assert caddyfiles_setting_a_policy([probe, clean]) == ["Caddyfile"]

    def test_template_probe(self, tmp_path):
        probe = tmp_path / "probe.html"
        probe.write_text('<meta http-equiv="Content-Security-Policy" content="form-action *">', encoding="utf-8")
        clean = tmp_path / "clean.html"
        clean.write_text('<meta charset="utf-8">', encoding="utf-8")
        assert templates_declaring_a_policy([probe, clean]) == [str(probe)]

    @pytest.mark.parametrize(
        "source",
        [
            'response["Content-Security-Policy"] = "x"',
            'response.headers["content-security-policy-report-only"] = "x"',
            'response.headers.setdefault("Content-Security-Policy", "x")',
            'HttpResponse(headers={"Content-Security-Policy": "x"})',
            "from csp.decorators import csp_exempt",
            "import csp.decorators",
            "response._csp_replace = {}",
        ],
    )
    def test_python_probe(self, source):
        assert policy_writes(source) == [1]

    def test_reads_and_prose_are_not_writes(self):
        source = (
            '"""Sets the Content-Security-Policy header."""\n'
            "# response['Content-Security-Policy'] = 'x'\n"
            'value = response.headers.get("Content-Security-Policy")\n'
        )
        assert policy_writes(source) == []

    def test_rewrite_helper_is_the_only_exemption(self, tmp_path):
        helper = tmp_path / CSP_REWRITE_HELPER
        other = tmp_path / "src/core/other.py"
        for path in (helper, other):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('response["Content-Security-Policy"] = "x"\n', encoding="utf-8")
        assert production_modules_writing_the_policy(tmp_path, [helper, other]) == ["src/core/other.py:1"]
