"""Tests fuer den E2E-Scheiben-Splitter ``scripts/e2e_slice.py`` (Refs #1976).

Der Splitter schneidet die E2E-Suite in ``n`` Scheiben, die in der CI als
Matrix je fuer sich **seriell** laufen. Weil kein Job die Suite mehr komplett
sieht, ist die einzige harte Zusicherung die **Partition**: die Vereinigung
aller Scheiben muss exakt der Menge entsprechen, die ``pytest -m e2e``
einsammelt — keine Datei doppelt, keine fehlend. Faellt eine Datei still aus
dem Schnitt, laeuft die CI gruen, ohne sie je ausgefuehrt zu haben; genau das
pruefen die Tests hier (synthetisch **und** gegen die echte Suite).

Zweite Zusicherung: der Schnitt ist **deterministisch** — dieselbe
Verzeichnisstruktur ergibt immer denselben Schnitt, unabhaengig von der
Reihenfolge, in der die Dateien hereinkommen. Sonst laeuft eine Datei je nach
Job-Laune mal in Scheibe 1, mal in Scheibe 3, und ein roter Lauf ist nicht
reproduzierbar.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# Meta-Test, der das Repo-Filesystem scannt (``scripts/`` + ``src/tests/e2e/``).
# Im Mutmut-Subprozess laeuft pytest aus ``mutants/`` und diese Pfade fehlen,
# daher als ``architecture`` markiert und im Mutmut-Run deselektiert (#930).
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "e2e_slice.py"
E2E_DIR = REPO_ROOT / "src" / "tests" / "e2e"

# Der ``architecture``-Marker deselektiert diesen Meta-Test im Mutmut-Run erst
# NACH dem Sammeln — der Splitter unten wird aber schon beim Import geladen.
# Die mutants/-Kopie enthaelt kein ``scripts/``; der Fehler braeche dort das
# Test-Listing und damit den kompletten Mutation-Run ab. Refs #930, #1600.
if not SCRIPT.exists():  # pragma: no cover - nur im mutants/-Tree
    pytest.skip(
        "scripts/e2e_slice.py nicht erreichbar — Meta-Test ist nur im echten Repo-Tree sinnvoll",
        allow_module_level=True,
    )


def _load_splitter():
    spec = importlib.util.spec_from_file_location("e2e_slice_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None, f"Splitter nicht ladbar: {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e2e_slice = _load_splitter()


def _write(base: Path, name: str, tests: int) -> Path:
    """Legt eine synthetische Testdatei mit genau ``tests`` Testfunktionen an."""
    body = "\n\n".join(f"def test_{name.rstrip('.py')}_{i}():\n    assert True" for i in range(tests))
    path = base / name
    path.write_text(body + "\n", encoding="utf-8")
    return path


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


class TestTestCounting:
    """Der Aufteilungsschluessel ist die Testzahl je Datei, nicht der Dateiname."""

    def test_counts_module_level_and_test_class_methods(self, tmp_path: Path) -> None:
        path = tmp_path / "test_sample.py"
        path.write_text(
            "def test_one():\n    pass\n\n\n"
            "class TestGroup:\n"
            "    def test_two(self):\n        pass\n\n"
            "    def test_three(self):\n        pass\n",
            encoding="utf-8",
        )
        assert e2e_slice.count_tests(path) == 3

    def test_ignores_helpers_non_test_classes_and_nested_defs(self, tmp_path: Path) -> None:
        """Nur was pytest auch einsammelt, zaehlt — Helfer verzerren sonst das Gewicht."""
        path = tmp_path / "test_sample.py"
        path.write_text(
            "def helper_setup():\n    pass\n\n\n"
            "def test_real():\n"
            "    def test_inner():\n        pass\n"
            "    test_inner()\n\n\n"
            "class HelperBag:\n"
            "    def test_not_collected(self):\n        pass\n",
            encoding="utf-8",
        )
        assert e2e_slice.count_tests(path) == 1

    def test_counts_async_tests(self, tmp_path: Path) -> None:
        path = tmp_path / "test_async.py"
        path.write_text("async def test_a():\n    pass\n\n\nasync def test_b():\n    pass\n", encoding="utf-8")
        assert e2e_slice.count_tests(path) == 2


class TestSliceInvariants:
    """Partition + Determinismus auf einem synthetischen Baum."""

    def test_every_file_lands_in_exactly_one_slice(self, tmp_path: Path) -> None:
        for i, n in enumerate([7, 5, 4, 3, 3, 2, 1]):
            _write(tmp_path, f"test_f{i}.py", n)
        slices = e2e_slice.build_slices(tmp_path, 3)
        flat = [p for chunk in slices for p in chunk]
        assert sorted(flat) == sorted(tmp_path.glob("test_*.py"))
        assert len(flat) == len(set(flat)), "Datei doppelt vergeben"

    def test_no_slice_is_empty(self, tmp_path: Path) -> None:
        for i, n in enumerate([9, 1, 1, 1]):
            _write(tmp_path, f"test_f{i}.py", n)
        assert all(chunk for chunk in e2e_slice.build_slices(tmp_path, 4))

    def test_files_without_tests_do_not_leave_a_slice_empty(self, tmp_path: Path) -> None:
        """Gewicht 0 darf keine Scheibe leer lassen — sonst laeuft sie als Voll-Lauf.

        Eine ``test_*.py`` ohne eigene Testfunktion (nur Fixtures/Helfer, oder
        alle Tests auskommentiert) wiegt 0. Wuerde die Zuteilung allein auf die
        Last schauen, landeten alle Nuller in derselben Scheibe — die uebrigen
        blieben leer, und der Splitter meldete das nicht einmal, weil er ohne
        Fehler durchlaeuft. Genau dieser Weg umgeht die Zusicherung "bei jedem
        Fehler stdout leer UND Exit != 0": eine leere Scheibe erzeugt keinen
        Fehler, aber ``pytest`` ohne Dateiargumente faehrt die ganze Suite.
        """
        _write(tmp_path, "test_with_tests.py", 1)
        for name in ("test_fixtures_only_a.py", "test_fixtures_only_b.py", "test_fixtures_only_c.py"):
            _write(tmp_path, name, 0)
        buckets = e2e_slice.build_slices(tmp_path, 4)
        assert all(chunk for chunk in buckets), f"leere Scheibe: {buckets}"

    def test_heaviest_file_is_placed_first(self, tmp_path: Path) -> None:
        """Der Greedy laeuft nach Gewicht, nicht nach Dateiname.

        Die Zuteilung "in die aktuell leichteste Scheibe" allein genuegt nicht:
        erst die absteigende Gewichtssortierung macht sie zu LPT und damit
        ausgewogen. Die Gewichte hier sind so gewaehlt, dass nur die schwerste
        Datei zuerst aufgeht (6 | 3+2+1); in Namensreihenfolge (1, 2, 3, 6)
        endet dieselbe Verteilung bei 4 zu 8. Ohne diesen Test bliebe die
        Sortierung ungeprueft — die uebrigen Faelle fangen nur grobe
        Verschlechterungen ab.
        """
        for name, count in (("test_a.py", 1), ("test_b.py", 2), ("test_c.py", 3), ("test_d.py", 6)):
            _write(tmp_path, name, count)
        buckets = e2e_slice.build_slices(tmp_path, 2)
        loads = [sum(e2e_slice.count_tests(path) for path in chunk) for chunk in buckets]
        assert loads == [6, 6], f"nicht nach Gewicht verteilt: {loads}"
        assert buckets[0] == [tmp_path / "test_d.py"], "die schwerste Datei gehoert zuerst vergeben"

    def test_spread_never_exceeds_largest_file(self, tmp_path: Path) -> None:
        """Invariante des Greedy-Schnitts: die Streuung bleibt unter der groessten Datei."""
        weights = [11, 9, 8, 6, 5, 5, 4, 3, 2, 2, 1]
        for i, n in enumerate(weights):
            _write(tmp_path, f"test_f{i}.py", n)
        loads = [sum(e2e_slice.count_tests(p) for p in chunk) for chunk in e2e_slice.build_slices(tmp_path, 3)]
        assert max(loads) - min(loads) <= max(weights)

    def test_split_is_deterministic(self, tmp_path: Path) -> None:
        for i, n in enumerate([6, 6, 5, 4, 4, 2, 1]):
            _write(tmp_path, f"test_f{i}.py", n)
        assert e2e_slice.build_slices(tmp_path, 2) == e2e_slice.build_slices(tmp_path, 2)

    def test_input_order_does_not_change_the_split(self, tmp_path: Path) -> None:
        """Gleiche Dateien, andere Reihenfolge — identischer Schnitt."""
        weights = {Path(f"test_f{i}.py"): n for i, n in enumerate([6, 6, 5, 4, 4, 2, 1])}
        forward = e2e_slice.distribute(weights, 3)
        backward = e2e_slice.distribute(dict(reversed(list(weights.items()))), 3)
        assert forward == backward

    def test_equal_weights_are_broken_by_name(self, tmp_path: Path) -> None:
        """Gleich schwere Dateien duerfen nicht per Zufall (Dict-/FS-Reihenfolge) landen."""
        weights = {Path(f"test_{c}.py"): 2 for c in "abcd"}
        assert e2e_slice.distribute(weights, 2) == [
            [Path("test_a.py"), Path("test_c.py")],
            [Path("test_b.py"), Path("test_d.py")],
        ]

    def test_more_slices_than_files_is_an_error(self, tmp_path: Path) -> None:
        _write(tmp_path, "test_only.py", 3)
        with pytest.raises(ValueError):
            e2e_slice.build_slices(tmp_path, 2)

    def test_non_positive_slice_count_is_an_error(self, tmp_path: Path) -> None:
        _write(tmp_path, "test_a.py", 1)
        _write(tmp_path, "test_b.py", 1)
        for bad in (0, -1):
            with pytest.raises(ValueError):
                e2e_slice.build_slices(tmp_path, bad)

    def test_empty_directory_is_an_error(self, tmp_path: Path) -> None:
        """Leerlauf ist gefaehrlicher als ein Fehler: pytest ohne Dateiargumente nimmt ALLES."""
        with pytest.raises(ValueError):
            e2e_slice.build_slices(tmp_path, 2)


class TestRealSuite:
    """Der Schnitt gegen die echte E2E-Suite."""

    @pytest.mark.parametrize("count", [2, 3, 4, 8])
    def test_slices_partition_the_real_suite(self, count: int) -> None:
        slices = e2e_slice.build_slices(E2E_DIR, count)
        flat = [p for chunk in slices for p in chunk]
        assert len(slices) == count
        assert all(chunk for chunk in slices), "leere Scheibe wuerde einen Voll-Lauf ausloesen"
        assert len(flat) == len(set(flat)), "Datei in mehreren Scheiben"
        assert set(flat) == set(E2E_DIR.glob("test_*.py"))

    @pytest.mark.parametrize("count", [2, 4, 8])
    def test_real_slices_are_balanced(self, count: int) -> None:
        """Der Datei-Halbierungs-Schluessel lag bei 2 Scheiben 47 % daneben — der hier nicht.

        Die Schranke ist bewusst eng: real liegt die schwerste Scheibe unter
        einem Promille ueber dem Mittel (207 bei Mittel 206,75), und genau
        diese Zusage steht in ``docs/e2e-architecture.md``. Faellt die
        Gewichtssortierung im Splitter weg, driftet der Schnitt auf gut sieben
        Prozent Streuung — mit den frueheren 10 % Toleranz waere das
        unbemerkt durchgegangen. Zwei Prozent lassen Luft fuer eine einzelne
        schwere neue Testdatei (LPT haelt die Streuung unter deren Gewicht),
        ohne die Aussage zu verwaessern.
        """
        loads = [sum(e2e_slice.count_tests(p) for p in chunk) for chunk in e2e_slice.build_slices(E2E_DIR, count)]
        mean = sum(loads) / count
        assert max(loads) <= mean * 1.02, f"Scheiben unausgewogen: {loads}"

    def test_union_of_slices_equals_the_pytest_marker_selection(self) -> None:
        """Haerteste Zusicherung: der Schnitt deckt exakt das ab, was ``-m e2e`` einsammelt."""
        pytest.importorskip("playwright")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-m", "e2e", "--collect-only", "-q", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        assert result.returncode == 0, f"Collect fehlgeschlagen:\n{result.stdout}\n{result.stderr}"
        collected = {
            (REPO_ROOT / line.split("::", 1)[0]).resolve() for line in result.stdout.splitlines() if "::" in line
        }
        assert collected, "pytest hat keine E2E-Tests eingesammelt — Test waere wertlos"
        sliced = {p.resolve() for chunk in e2e_slice.build_slices(E2E_DIR, 4) for p in chunk}
        assert sliced == collected, (
            f"Nur im Schnitt: {sorted(p.name for p in sliced - collected)}; "
            f"nur in der Suite: {sorted(p.name for p in collected - sliced)}"
        )


class TestCommandLine:
    """Die CI ruft das Script als Kommando — Exit-Codes und Ausgabeform zaehlen."""

    def test_prints_one_repo_relative_path_per_line(self) -> None:
        result = _run_cli("--slices", "4", "--index", "1")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.split()
        assert lines
        assert all(line.startswith("src/tests/e2e/test_") for line in lines), result.stdout
        assert all((REPO_ROOT / line).exists() for line in lines)

    def test_indexes_are_one_based_and_cover_all_slices(self) -> None:
        seen: set[str] = set()
        for index in (1, 2):
            result = _run_cli("--slices", "2", "--index", str(index))
            assert result.returncode == 0, result.stderr
            seen |= set(result.stdout.split())
        assert len(seen) == len(list(E2E_DIR.glob("test_*.py")))

    def test_index_out_of_range_exits_non_zero(self) -> None:
        for bad in ("0", "5"):
            result = _run_cli("--slices", "4", "--index", bad)
            assert result.returncode != 0, f"--index {bad} haette scheitern muessen"
            assert result.stdout.strip() == "", "Kein Dateiargument bei Fehler — sonst laeuft pytest auf ALLES"

    def test_summary_reports_every_slice_with_its_load(self) -> None:
        result = _run_cli("--slices", "4", "--summary")
        assert result.returncode == 0, result.stderr
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        assert len(lines) == 4
        for index, line in enumerate(lines, start=1):
            assert f"{index}/4" in line


def test_workflow_runs_eight_slices_with_a_forty_minute_limit() -> None:
    """The matrix and ``E2E_SLICES`` must agree, otherwise slices go unrun."""
    import yaml

    job = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "e2e.yml").read_text())["jobs"]["test-e2e"]
    assert job["env"]["E2E_SLICES"] == 8
    assert job["strategy"]["matrix"]["slice"] == list(range(1, 9))
    assert job["strategy"]["fail-fast"] is False
    assert job["timeout-minutes"] == 40
    # ``make test-e2e-slice SLICES=<n>`` defaults to the CI cut, so a failed job number fits locally.
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "\nSLICES ?= 8\n" in makefile
