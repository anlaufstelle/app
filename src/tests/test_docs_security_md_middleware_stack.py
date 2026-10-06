"""SECURITY.md lists the middleware stack exactly as the settings define it.

The security policy describes, in both languages, which middleware sets the
security headers and in which order the stack runs (Refs #2445). A hand-kept
copy of the list drifts silently as soon as a middleware is added, removed or
moved; this guard derives the expected list from ``MIDDLEWARE`` instead of
pinning a count.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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


class TestSecurityMdMatchesSettings:
    @pytest.mark.parametrize("heading", SECTION_HEADINGS)
    def test_section_lists_middleware_in_settings_order(self, heading: str) -> None:
        text = SECURITY_MD.read_text(encoding="utf-8")
        assert documented_middleware(text, heading) == list(base_settings.MIDDLEWARE), (
            f"SECURITY.md section {heading!r} does not match MIDDLEWARE in settings/base.py "
            "(order and entries must be identical)"
        )

    def test_prod_settings_do_not_change_the_stack(self) -> None:
        """SECURITY.md states that production uses the base stack unchanged."""
        source = PROD_SETTINGS.read_text(encoding="utf-8")
        assert not re.search(r"^\s*MIDDLEWARE\b", source, re.MULTILINE), (
            "settings/prod.py touches MIDDLEWARE — SECURITY.md would no longer describe production"
        )
