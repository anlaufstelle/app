"""SECURITY.md lists the middleware stack exactly as the settings define it.

The security policy describes, in both languages, which middleware sets the
security headers and in which order the stack runs (Refs #2445). A hand-kept
copy of the list drifts silently as soon as a middleware is added, removed or
moved; this guard derives the expected list from ``MIDDLEWARE`` instead of
pinning a count.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from django.test import Client, override_settings

from anlaufstelle.settings import base as base_settings

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SECURITY_MD = REPO_ROOT / "SECURITY.md"
PROD_SETTINGS = REPO_ROOT / "src" / "anlaufstelle" / "settings" / "prod.py"

SECTION_HEADINGS = (
    "## Sicherheits-Header und Middleware-Stack",
    "## Security headers and middleware stack",
)

_ITEM_RE = re.compile(r"^\d+\.\s+`([\w.]+)`")
_ROW_RE = re.compile(r"^\|\s*`([^`:]+)[^`]*`\s*\|")

# Wording that promises a header on every response without condition.
UNIVERSAL_CLAIMS = ("auf jeder Antwort", "on every response")
# Responses that SecurityMiddleware produces or aborts itself, keyed by the
# word a table row uses for them. A header such a response lacks must be named
# as an exception for it in the "When" column (see ``exception_violations``).
REDIRECT_WORD = {
    "## Sicherheits-Header und Middleware-Stack": "HTTPS-Umleitung",
    "## Security headers and middleware stack": "HTTPS redirect",
}
HOST_REJECTION_WORD = {
    "## Sicherheits-Header und Middleware-Stack": "Host-Ablehnung",
    "## Security headers and middleware stack": "host rejection",
}
# A clause of the "When" column (split at ";") that starts with one of these
# words states where the header is NOT set.
NEGATIONS = ("nicht", "nie", "weder", "not", "never", "neither")
# Header settings of settings/prod.py that decide what the redirect carries.
PROD_HEADER_SETTINGS = (
    "SECURE_SSL_REDIRECT",
    "SECURE_REDIRECT_EXEMPT",
    "SECURE_PROXY_SSL_HEADER",
    "SECURE_HSTS_SECONDS",
    "SECURE_HSTS_INCLUDE_SUBDOMAINS",
    "SECURE_HSTS_PRELOAD",
    "SECURE_CONTENT_TYPE_NOSNIFF",
    "SECURE_REFERRER_POLICY",
    "SECURE_CROSS_ORIGIN_OPENER_POLICY",
    "X_FRAME_OPTIONS",
)


def documented_middleware(text: str, heading: str) -> list[str]:
    """Return the dotted paths of the numbered list in the section ``heading``."""
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        return []
    found: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## ") or line.startswith("# "):
            break
        match = _ITEM_RE.match(line)
        if match:
            found.append(match.group(1))
    return found


def section_text(text: str, heading: str) -> str:
    """Return the body of the section ``heading`` up to the next heading."""
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        return ""
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## ") or line.startswith("# "):
            break
        body.append(line)
    return "\n".join(body)


def documented_header_rows(text: str, heading: str) -> dict[str, str]:
    """Map header name -> table row for the header table of ``heading``."""
    rows: dict[str, str] = {}
    for line in section_text(text, heading).splitlines():
        match = _ROW_RE.match(line)
        if match:
            rows[match.group(1).strip()] = line
    return rows


def prod_header_settings() -> dict[str, object]:
    """Literal values of PROD_HEADER_SETTINGS, read from settings/prod.py
    without importing it (the module needs production secrets)."""
    tree = ast.parse(PROD_SETTINGS.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in PROD_HEADER_SETTINGS:
                    values[target.id] = ast.literal_eval(node.value)
    return values


def when_clauses(row: str) -> list[str]:
    """Split the last cell ("When") of a table row into its ``;`` clauses."""
    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    return [clause.strip() for clause in cells[-1].split(";") if clause.strip()]


def is_negative(clause: str) -> bool:
    first = clause.split(maxsplit=1)[0].lower() if clause else ""
    return first in NEGATIONS


def exception_violations(rows: dict[str, str], response, word: str) -> list[str]:
    """Headers whose row contradicts ``response`` for the response class ``word``.

    A header the response lacks must name ``word`` in a negative clause and in
    no other clause; a header the response carries must not name ``word`` in a
    negative clause. Merely mentioning the response class is not enough.
    """
    violations: list[str] = []
    for name, row in rows.items():
        clauses = [clause for clause in when_clauses(row) if word in clause]
        negative = [clause for clause in clauses if is_negative(clause)]
        positive = [clause for clause in clauses if not is_negative(clause)]
        if name not in response and (not negative or positive):
            violations.append(name)
        if name in response and negative:
            violations.append(name)
    return violations


def host_rejection_response(tmp_path: Path):
    """Plaintext request with a host outside ALLOWED_HOSTS under the
    production header settings: SecurityMiddleware raises DisallowedHost
    while building the redirect, Django answers 400 outside of it."""
    with override_settings(
        DEBUG=False, ALLOWED_HOSTS=["testserver"], STATIC_ROOT=str(tmp_path), **prod_header_settings()
    ):
        return Client().get("/login/", HTTP_HOST="unlisted.invalid")


class TestExtraction:
    def test_reads_only_the_numbered_list_of_the_section(self) -> None:
        text = "\n".join(
            [
                "## Other",
                "1. `not.this.Middleware` — elsewhere",
                "## Security headers and middleware stack",
                "| `X-Frame-Options` | `XFrameOptionsMiddleware` | x |",
                "1. `a.b.FirstMiddleware` — first",
                "2. `c.d.SecondMiddleware` — second",
                "## Out of Scope",
                "3. `e.f.AfterMiddleware` — after",
            ]
        )
        assert documented_middleware(text, "## Security headers and middleware stack") == [
            "a.b.FirstMiddleware",
            "c.d.SecondMiddleware",
        ]

    def test_missing_section_yields_empty_list(self) -> None:
        assert documented_middleware("## Something else\n1. `a.B`", "## Security headers and middleware stack") == []

    def test_reads_header_rows_of_the_section(self) -> None:
        text = "\n".join(
            [
                "## Security headers and middleware stack",
                "| Header | Source | When |",
                "|---|---|---|",
                "| `X-Frame-Options` | `XFrameOptionsMiddleware` | not on the HTTPS redirect |",
                "| `Cache-Control: no-store, private` | `NoStoreCacheMiddleware` | signed in |",
                "## Out of Scope",
                "| `Other` | x | y |",
            ]
        )
        rows = documented_header_rows(text, "## Security headers and middleware stack")
        assert sorted(rows) == ["Cache-Control", "X-Frame-Options"]
        assert "HTTPS redirect" in rows["X-Frame-Options"]

    def test_when_clauses_are_split_at_semicolons_of_the_last_cell(self) -> None:
        row = "| `X-Frame-Options` | `A; B` | every response; not the HTTPS redirect |"
        assert when_clauses(row) == ["every response", "not the HTTPS redirect"]


class TestExceptionCheck:
    """``exception_violations`` requires the exception, not a mere mention."""

    RESPONSE = {"Content-Security-Policy": "default-src 'self'"}

    @pytest.mark.parametrize(
        "when",
        [
            "every response that passes it; not the HTTPS redirect",
            "only over HTTPS; never on plaintext, so neither on the HTTPS redirect nor elsewhere",
        ],
    )
    def test_missing_header_named_as_exception_passes(self, when: str) -> None:
        rows = {"X-Frame-Options": f"| `X-Frame-Options` | x | y | {when} |"}
        assert exception_violations(rows, self.RESPONSE, "HTTPS redirect") == []

    @pytest.mark.parametrize(
        "when",
        [
            "every response, including the HTTPS redirect",
            "every response; also the HTTPS redirect",
            "every response",
            "every response, including the HTTPS redirect; not the HTTPS redirect",
            "jede Antwort, auch die Host-Ablehnung",
        ],
    )
    def test_missing_header_without_exception_fails(self, when: str) -> None:
        rows = {"X-Frame-Options": f"| `X-Frame-Options` | x | y | {when} |"}
        word = "Host-Ablehnung" if "Host" in when else "HTTPS redirect"
        assert exception_violations(rows, self.RESPONSE, word) == ["X-Frame-Options"]

    def test_present_header_named_as_exception_fails(self) -> None:
        row = "| `Content-Security-Policy` | x | y | every response; not the host rejection |"
        rows = {"Content-Security-Policy": row}
        assert exception_violations(rows, self.RESPONSE, "host rejection") == ["Content-Security-Policy"]

    def test_present_header_named_positively_passes(self) -> None:
        row = "| `Content-Security-Policy` | x | y | every response, including the host rejection |"
        rows = {"Content-Security-Policy": row}
        assert exception_violations(rows, self.RESPONSE, "host rejection") == []


class TestSecurityMdMatchesSettings:
    @pytest.mark.parametrize("heading", SECTION_HEADINGS)
    def test_section_lists_middleware_in_settings_order(self, heading: str) -> None:
        text = SECURITY_MD.read_text(encoding="utf-8")
        assert documented_middleware(text, heading) == list(base_settings.MIDDLEWARE), (
            f"SECURITY.md section {heading!r} does not match MIDDLEWARE in settings/base.py "
            "(order and entries must be identical)"
        )

    @pytest.mark.parametrize("heading", SECTION_HEADINGS)
    def test_section_makes_no_unconditional_header_promise(self, heading: str) -> None:
        body = section_text(SECURITY_MD.read_text(encoding="utf-8"), heading)
        found = [claim for claim in UNIVERSAL_CLAIMS if claim in body]
        assert found == [], (
            f"SECURITY.md section {heading!r} promises headers on every response ({found}); "
            "the plaintext HTTPS redirect carries neither HSTS nor X-Frame-Options"
        )

    @pytest.mark.parametrize("heading", SECTION_HEADINGS)
    def test_headers_missing_on_https_redirect_are_documented_as_such(self, heading: str, tmp_path: Path) -> None:
        """A plaintext request under the production header settings is
        answered by SecurityMiddleware with a redirect. Every documented
        header that this response lacks must name the redirect as exception."""
        values = prod_header_settings()
        assert sorted(values) == sorted(PROD_HEADER_SETTINGS)
        with override_settings(ALLOWED_HOSTS=["testserver"], STATIC_ROOT=str(tmp_path), **values):
            response = Client().get("/login/")
        assert response.status_code == 301
        assert response["Location"] == "https://testserver/login/"

        rows = documented_header_rows(SECURITY_MD.read_text(encoding="utf-8"), heading)
        assert rows, f"no header table in SECURITY.md section {heading!r}"
        assert "Strict-Transport-Security" in rows, f"SECURITY.md section {heading!r} no longer documents HSTS"
        assert "Strict-Transport-Security" not in response
        violations = exception_violations(rows, response, REDIRECT_WORD[heading])
        assert violations == [], (
            f"SECURITY.md section {heading!r}: rows contradict the HTTPS redirect "
            f"(missing headers must name {REDIRECT_WORD[heading]!r} as exception): {violations}"
        )

    @pytest.mark.parametrize("heading", SECTION_HEADINGS)
    def test_headers_missing_on_host_rejection_are_documented_as_such(self, heading: str, tmp_path: Path) -> None:
        """The 400 for an unlisted host on a plaintext request is produced
        outside SecurityMiddleware; every documented header it lacks must
        name the host rejection as exception."""
        response = host_rejection_response(tmp_path)
        assert response.status_code == 400
        assert response["Content-Security-Policy"]

        rows = documented_header_rows(SECURITY_MD.read_text(encoding="utf-8"), heading)
        assert rows, f"no header table in SECURITY.md section {heading!r}"
        assert "X-Content-Type-Options" not in response
        violations = exception_violations(rows, response, HOST_REJECTION_WORD[heading])
        assert violations == [], (
            f"SECURITY.md section {heading!r}: rows contradict the host rejection (HTTP 400) "
            f"(missing headers must name {HOST_REJECTION_WORD[heading]!r} as exception): {violations}"
        )

    def test_prod_settings_do_not_change_the_stack(self) -> None:
        """SECURITY.md states that production uses the base stack unchanged."""
        source = PROD_SETTINGS.read_text(encoding="utf-8")
        assert not re.search(r"^\s*MIDDLEWARE\b", source, re.MULTILINE), (
            "settings/prod.py touches MIDDLEWARE — SECURITY.md would no longer describe production"
        )
