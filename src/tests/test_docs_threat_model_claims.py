"""docs/threat-model.md claims no more protection than the guards and views provide.

Two rows of the STRIDE tables are coupled to code here (Refs #2445):

* **D Login-Flood / Bot-Spam** — the POST rate-limit guard
  (``TestRateLimitOnAllMutations``) scans only class-based ``post()`` methods
  in ``src/core/views/*.py`` (not recursive) and has an allowlist. The row must
  not promise a rate limit on *all* POST handlers, must name every allowlisted
  view and every views subpackage with ``post()`` handlers that the guard does
  not scan.
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


def unscanned_view_packages(views_dir: Path) -> list[str]:
    """Subpackages of ``views_dir`` that define ``post()`` handlers; the POST
    rate-limit guard globs ``*.py`` non-recursively and never reads them."""
    found: list[str] = []
    for sub in sorted(p for p in views_dir.iterdir() if p.is_dir() and (p / "__init__.py").is_file()):
        if any("def post(" in f.read_text(encoding="utf-8") for f in sub.rglob("*.py")):
            found.append(f"src/core/views/{sub.name}/")
    return found


class TestHelpers:
    def test_unscanned_packages_only_lists_packages_with_post(self, tmp_path: Path) -> None:
        for name, body in (("with_post", "class V:\n    def post(self):\n        pass\n"), ("read_only", "x = 1\n")):
            pkg = tmp_path / name
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "views.py").write_text(body, encoding="utf-8")
        (tmp_path / "top.py").write_text("def post(self):\n    pass\n", encoding="utf-8")
        assert unscanned_view_packages(tmp_path) == ["src/core/views/with_post/"]

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
        missing = sorted(
            entry.split(":", 1)[1]
            for entry in views_guards.TestRateLimitOnAllMutations._ALLOWLIST
            if entry.split(":", 1)[1] not in row
        )
        assert missing == [], f"threat-model.md does not name POST views exempt from the rate limit: {missing}"

    def test_row_names_view_packages_the_guard_does_not_scan(self) -> None:
        row = table_row(THREAT_MODEL.read_text(encoding="utf-8"), RATE_LIMIT_ROW)
        packages = unscanned_view_packages(VIEWS_DIR)
        assert packages, "expected views subpackages with post() handlers"
        missing = [pkg for pkg in packages if pkg not in row]
        assert missing == [], f"threat-model.md does not name views packages outside the POST guard: {missing}"


class TestClamavRow:
    def test_row_names_every_503_cause(self) -> None:
        row = table_row(THREAT_MODEL.read_text(encoding="utf-8"), CLAMAV_ROW)
        assert "503 nur bei DB-Ausfall" not in row
        assert "Encryption-Key" in row, "threat-model.md does not name the encryption-key check as 503 cause"
