"""docs/threat-model.md claims no more protection than the guards and views provide.

Two rows of the STRIDE tables are coupled to code here (Refs #2445):

* **D Login-Flood / Bot-Spam** — the mutation guard resolves registered URL
  callbacks recursively and requires method-specific blocking evidence.
  The row names each narrowly exempt URL and view.
* **D ClamAV-Outage** — ``/health/`` answers 503 on a database failure *and*
  on a failed encryption-key roundtrip (behaviour pinned in
  ``test_health.py``); the row must name both causes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import test_architecture_guards_views as views_guards

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
THREAT_MODEL = REPO_ROOT / "docs" / "threat-model.md"
VIEWS_DIR = REPO_ROOT / "src" / "core" / "views"

RATE_LIMIT_ROW = "| **D** Login-Flood / Bot-Spam |"
CLAMAV_ROW = "| **D** ClamAV-Outage blockt Uploads |"
UNIVERSAL_POST_CLAIMS = ("alle POST-Handler", "jeden POST-Handler", "alle POST-Endpunkte")


def table_row(text: str, prefix: str) -> str:
    rows = [line for line in text.splitlines() if line.startswith(prefix)]
    assert len(rows) == 1, f"expected exactly one row starting with {prefix!r}, found {len(rows)}"
    return rows[0]


class TestHelpers:
    def test_table_row_requires_exactly_one_match(self) -> None:
        with pytest.raises(AssertionError):
            table_row("| a |\n| a |", "| a |")


class TestRateLimitRow:
    def test_row_makes_no_universal_post_promise(self) -> None:
        row = table_row(THREAT_MODEL.read_text(encoding="utf-8"), RATE_LIMIT_ROW)
        found = [claim for claim in UNIVERSAL_POST_CLAIMS if claim in row]
        assert found == [], f"threat-model.md promises a rate limit on every POST handler: {found}"

    def test_row_names_every_allowlisted_view(self) -> None:
        row = table_row(THREAT_MODEL.read_text(encoding="utf-8"), RATE_LIMIT_ROW)
        missing = []
        for name, (target, methods, _reason) in views_guards.TestRateLimitOnAllMutations._ALLOWLIST.items():
            owner, _, attribute = target.partition(":")
            class_name = owner.rsplit(".", 1)[-1]
            qualified_handlers = [f"{class_name}.{attribute or method.lower()}" for method in methods]
            if f"`{name}`" not in row or any(handler not in row for handler in qualified_handlers):
                missing.append(name)
        assert missing == [], f"threat-model.md does not name POST views exempt from the rate limit: {missing}"

    def test_row_describes_recursive_blocking_guard(self) -> None:
        row = table_row(THREAT_MODEL.read_text(encoding="utf-8"), RATE_LIMIT_ROW)
        assert "rekursiv" in row
        assert "block=True" in row
        assert "require_POST" in row
        assert "geerbten Mutationshandlern" in row
        assert "Formularviews" in row
        assert "nicht rekursiv" not in row
        assert "fail-closed" in row
        assert "URL-Resolver" in row
        assert "Laufzeit" in row
        assert "Markierung" in row
        assert "Ausnahmeliste" in row


class TestClamavRow:
    def test_row_names_every_503_cause(self) -> None:
        row = table_row(THREAT_MODEL.read_text(encoding="utf-8"), CLAMAV_ROW)
        assert "503 nur bei DB-Ausfall" not in row
        assert "Encryption-Key" in row, "threat-model.md does not name the encryption-key check as 503 cause"
