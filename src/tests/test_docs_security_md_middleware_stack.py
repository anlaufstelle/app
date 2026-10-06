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
# Per heading: the word a table row must use to name the HTTPS redirect as an
# exception when its header is missing there.
REDIRECT_WORD = {
    "## Sicherheits-Header und Middleware-Stack": "HTTPS-Umleitung",
    "## Security headers and middleware stack": "HTTPS redirect",
}
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
        missing_on_redirect = sorted(name for name in rows if name not in response)
        assert "Strict-Transport-Security" in missing_on_redirect
        undocumented = [name for name in missing_on_redirect if REDIRECT_WORD[heading] not in rows[name]]
        assert undocumented == [], (
            f"SECURITY.md section {heading!r}: headers absent on the HTTPS redirect without "
            f"naming it ({REDIRECT_WORD[heading]!r}): {undocumented}"
        )

    def test_prod_settings_do_not_change_the_stack(self) -> None:
        """SECURITY.md states that production uses the base stack unchanged."""
        source = PROD_SETTINGS.read_text(encoding="utf-8")
        assert not re.search(r"^\s*MIDDLEWARE\b", source, re.MULTILINE), (
            "settings/prod.py touches MIDDLEWARE — SECURITY.md would no longer describe production"
        )
