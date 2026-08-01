"""Architecture-Guard — Statistik-Matview nur über den Zugriffs-Helper (Refs #1659).

Die Statistik-Materialized-View ``core_statistics_event_flat`` (Migration
0049) unterliegt **keiner RLS** — Zugriffsschutz ist reine Service-Schicht:
``core/services/dashboard/statistics.py:_query_statistics_mv`` schreibt den
``WHERE facility_id = %s``-Filter fest verdrahtet in jede Query. Ein
künftiger zweiter Zugriffspfad könnte diesen Filter vergessen, wenn er die
Matview direkt anspricht statt über den Helper zu gehen.

Dieser Guard verhindert genau das: Er scannt ``src/core`` nach Vorkommen des
Matview-Namens (Konstante ``STATISTICS_MV_NAME`` oder das String-Literal
``"core_statistics_event_flat"``) außerhalb einer kleinen Allowlist:

- ``core/services/dashboard/statistics.py`` — der Helper + die Konstante selbst
- ``core/services/dashboard/__init__.py`` — reiner Re-Export der Konstante
  (kein Query-Bau)
- ``core/migrations/`` — Schema: die View wird hier erzeugt/verändert
- ``core/management/commands/refresh_statistics_view.py`` — ``REFRESH
  MATERIALIZED VIEW`` ist eine Wartungsoperation auf der gesamten View, kein
  facility-skopierter Lesezugriff, daher ohne Filterbedarf

Analog zu ``test_architecture_guards_services.py`` (Encryption-Bypass-Guard)
und ``test_architecture_guards_mutation_isolation.py`` (Detektor-Kontrakt mit
synthetischen Fixtures).
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

_CORE_DIR = Path("src/core")
_ALLOWLIST = (
    Path("src/core/services/dashboard/statistics.py"),
    Path("src/core/services/dashboard/__init__.py"),
    Path("src/core/migrations"),
    Path("src/core/management/commands/refresh_statistics_view.py"),
)

# Erfasst sowohl die Konstante (``STATISTICS_MV_NAME``) als auch den nackten
# Matview-Namen als Text — auch eingebettet in einen größeren SQL-String wie
# ``"SELECT * FROM core_statistics_event_flat"``. Ein Bypass könnte den Namen
# direkt hinschreiben statt die Konstante zu importieren.
_MV_REFERENCE = re.compile(r"STATISTICS_MV_NAME\b|core_statistics_event_flat")


def _is_allowlisted(path: Path, allowlist: tuple[Path, ...]) -> bool:
    return any(path == allow or allow in path.parents for allow in allowlist)


def find_direct_matview_references(base_dir: Path, allowlist: tuple[Path, ...]) -> list[str]:
    """Liefert ``"<pfad>:<zeile> — <treffer>"`` je Matview-Referenz außerhalb
    der Allowlist unter ``base_dir``."""
    violations: list[str] = []
    if not base_dir.exists():
        return violations
    for py_file in sorted(base_dir.rglob("*.py")):
        if _is_allowlisted(py_file, allowlist):
            continue
        source = py_file.read_text(errors="ignore")
        for match in _MV_REFERENCE.finditer(source):
            line = source[: match.start()].count("\n") + 1
            violations.append(f"{py_file}:{line} — {match.group(0)}")
    return violations


class TestStatisticsMatviewAccessGuard:
    def test_no_direct_matview_access_outside_helper(self):
        if not _CORE_DIR.exists():
            pytest.skip(f"{_CORE_DIR} nicht vorhanden")
        violations = find_direct_matview_references(_CORE_DIR, _ALLOWLIST)
        assert not violations, (
            "Folgende Stellen referenzieren die Statistik-Matview "
            "(core_statistics_event_flat) außerhalb des Zugriffs-Helpers. "
            "Jeder Lesezugriff muss über "
            "core.services.dashboard.statistics._query_statistics_mv laufen, "
            "der den facility_id-Filter strukturell erzwingt.\n"
            "Refs #1659.\n"
            f"Verstöße: {violations}"
        )


class TestDetectorContract:
    """Pinnt die Erkennungs-/Allowlist-Semantik des Guards mit synthetischen
    Fixtures — unabhängig vom echten ``src/core``-Baum (Refs #1659)."""

    def test_string_literal_outside_allowlist_is_flagged(self, tmp_path):
        services_dir = tmp_path / "services"
        services_dir.mkdir()
        bypass = services_dir / "shadow_report.py"
        bypass.write_text(
            "from django.db import connection\n\n"
            "def unsafe_all_facilities():\n"
            "    with connection.cursor() as cursor:\n"
            '        cursor.execute("SELECT * FROM core_statistics_event_flat")\n'
        )

        violations = find_direct_matview_references(tmp_path, allowlist=())
        assert any("shadow_report.py" in v for v in violations)

    def test_constant_usage_outside_allowlist_is_flagged(self, tmp_path):
        services_dir = tmp_path / "services"
        services_dir.mkdir()
        bypass = services_dir / "shadow_report.py"
        bypass.write_text(
            "from core.services.dashboard.statistics import STATISTICS_MV_NAME\n\n"
            "def unsafe_all_facilities():\n"
            '    return f"SELECT * FROM {STATISTICS_MV_NAME}"\n'
        )

        violations = find_direct_matview_references(tmp_path, allowlist=())
        assert any("shadow_report.py" in v for v in violations)

    def test_allowlisted_helper_module_is_not_flagged(self, tmp_path):
        services_dir = tmp_path / "services"
        services_dir.mkdir()
        helper = services_dir / "statistics.py"
        helper.write_text('STATISTICS_MV_NAME = "core_statistics_event_flat"\n')

        violations = find_direct_matview_references(tmp_path, allowlist=(helper,))
        assert violations == []

    def test_allowlisted_migrations_dir_is_not_flagged(self, tmp_path):
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        migration = migrations_dir / "0049_statistics_event_flat_mv.py"
        migration.write_text('MV_NAME = "core_statistics_event_flat"\n')

        violations = find_direct_matview_references(tmp_path, allowlist=(migrations_dir,))
        assert violations == []

    def test_files_without_any_reference_are_untouched(self, tmp_path):
        harmless = tmp_path / "harmless.py"
        harmless.write_text("def add(a, b):\n    return a + b\n")

        violations = find_direct_matview_references(tmp_path, allowlist=())
        assert violations == []
