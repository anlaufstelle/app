"""Tests fuer ``scripts/verify_vendor_js_sync.py`` (Refs #1076).

Der Guard vergleicht die in ``package.json`` gepinnte Version jeder vendored
JS-Lib mit dem Versions-String im eingecheckten ``src/static/js/*.min.js`` und
faillt (Exit != 0) bei Drift. Er ist reiner String-Vergleich — kein node/npm.

Die Tests rufen das Script per ``subprocess.run`` auf (End-to-End: Exit-Code +
Stderr). Der Negativ-Fall baut ein **manipuliertes Repo-Abbild** in ``tmp_path``
(package.json, das Script und die vendored ``*.min.js`` werden hineinkopiert,
die jeweils zu pruefende Datei verbogen) und ruft die dorthin gespiegelte
Script-Kopie auf — so triggert der Test NIE den echten ``make ci``-Guard.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Meta-Test auf Repo-Filesystem (package.json + src/static/js). Im Mutmut-
# Subprozess fehlen diese Pfade, daher wie test_matrix_drift.py markiert (#930).
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_vendor_js_sync.py"
VENDOR_REL = Path("src") / "static" / "js"
FONT_REL = Path("src") / "static" / "fonts"


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    """Guard mit ``root`` als Repo-Wurzel ausfuehren.

    Das Script leitet seine Pfade aus ``Path(__file__).parent.parent`` ab; wir
    legen es daher samt ``package.json`` + ``src/static/js`` in ``root`` ab und
    rufen die dorthin gespiegelte Script-Kopie auf.
    """
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "verify_vendor_js_sync.py")],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )


def _mirror_repo(dest: Path, *, mutate: dict[str, str] | None = None) -> Path:
    """Minimal-Spiegel des Repos unter ``dest``: package.json, das Script und
    die vendored ``*.min.js``. ``mutate`` ersetzt im Inhalt einzelner Dateien
    (relativer Pfad -> alt:neu, ``\\x00``-getrennt) — fuer Drift-Szenarien.
    """
    mutate = mutate or {}
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / VENDOR_REL).mkdir(parents=True, exist_ok=True)

    files = ["package.json", "scripts/verify_vendor_js_sync.py", "THIRDPARTY-LICENSES.md"]
    files += [p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / VENDOR_REL).glob("*.min.js")]
    files += [p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / VENDOR_REL / "vendor").glob("*.min.js")]
    files += [p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / VENDOR_REL / "licenses").glob("*.txt")]
    files += [p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / FONT_REL).glob("*.txt")]
    files += [p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / FONT_REL).glob("*.woff2")]

    for rel in files:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        content = "" if rel.endswith(".woff2") else (REPO_ROOT / rel).read_text(encoding="utf-8")
        if rel in mutate:
            old, new = mutate[rel].split("\x00")
            assert old in content, f"Marker {old!r} nicht in {rel} gefunden"
            content = content.replace(old, new, 1)
        target = dest / rel
        if rel.endswith(".woff2"):
            target.write_bytes((REPO_ROOT / rel).read_bytes())
        else:
            target.write_text(content, encoding="utf-8")
    return dest


def test_real_tree_guard_is_green() -> None:
    """Smoke gegen den ECHTEN Baum — heute MUSS der Guard gruen sein."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, (
        f"Guard schlug auf dem echten Baum fehl:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "4 vendored JS-Libs" in result.stdout


def test_manipulated_vendored_version_is_detected(tmp_path: Path) -> None:
    """Verbiegt die htmx-Version im vendored File -> Drift -> Exit != 0."""
    real = (REPO_ROOT / VENDOR_REL / "htmx.min.js").read_text(encoding="utf-8")
    pinned = json.loads((REPO_ROOT / "package.json").read_text())["devDependencies"]["htmx.org"]
    assert 'version:"' + pinned + '"' in real, "Erwarteter htmx-Versions-String nicht gefunden"

    mirror = _mirror_repo(
        tmp_path / "repo",
        mutate={str(VENDOR_REL / "htmx.min.js"): f'version:"{pinned}"\x00version:"0.0.0"'},
    )
    result = _run(mirror)
    assert result.returncode == 1
    assert "DRIFT" in (result.stdout + result.stderr)
    assert "htmx.org" in (result.stdout + result.stderr)


def test_ambiguous_double_version_literal_is_flagged(tmp_path: Path) -> None:
    """Zwei ``version:"x.y.z"``-Literale im selben Min-File -> laute Ambiguität.

    Der generische Pattern (htmx/alpine/dexie) verlaesst sich darauf, dass GENAU
    EIN solches Literal pro Datei existiert. Schmuggelt ein Build ein zweites ein,
    darf der Guard NICHT still den ersten Treffer nehmen, sondern muss mit einer
    klaren Fehlermeldung (Lib + Datei genannt) failen statt potenziell die falsche
    Version zu lesen.
    """
    pinned = json.loads((REPO_ROOT / "package.json").read_text())["devDependencies"]["htmx.org"]
    # Aus dem EINEN echten Literal zwei machen (Pin bleibt unveraendert -> ohne
    # Härtung wuerde der erste Treffer == Pin den Guard faelschlich gruen lassen).
    mirror = _mirror_repo(
        tmp_path / "repo",
        mutate={
            str(VENDOR_REL / "htmx.min.js"): (f'version:"{pinned}"\x00version:"{pinned}";var __x={{version:"9.9.9"}}')
        },
    )
    result = _run(mirror)
    combined = result.stdout + result.stderr
    assert result.returncode == 1, f"Guard sollte bei Mehrdeutigkeit failen:\n{combined}"
    assert "htmx.org" in combined
    assert "htmx.min.js" in combined
    # 'mehrdeutig' belegt, dass NICHT der gewoehnliche DRIFT-Pfad griff.
    assert "mehrdeutig" in combined.lower()


def test_missing_version_string_is_reported(tmp_path: Path) -> None:
    """Kein ``version:"x.y.z"``-Literal im Min-File -> kein Treffer -> Exit != 0.

    Zerstoert den einzigen Versions-String im vendored htmx-File, sodass KEIN
    Pattern mehr greift (``extract_vendored_version`` liefert ``None``). Der Guard
    darf das nicht als „OK" durchwinken, sondern muss laut failen und Lib + Datei
    in der Meldung nennen — sonst wuerde ein Build mit unerwartetem Versions-Format
    still als synchron gelten.
    """
    pinned = json.loads((REPO_ROOT / "package.json").read_text())["devDependencies"]["htmx.org"]
    # Das Versions-Literal so verbiegen, dass weder der htmx-Pattern noch ein
    # anderer generischer Pattern es noch matcht (Pin in package.json bleibt gueltig).
    mirror = _mirror_repo(
        tmp_path / "repo",
        mutate={str(VENDOR_REL / "htmx.min.js"): f'version:"{pinned}"\x00version:"NICHT_GEFUNDEN"'},
    )
    result = _run(mirror)
    combined = result.stdout + result.stderr
    assert result.returncode == 1, f"Guard sollte ohne Versions-Treffer failen:\n{combined}"
    assert "htmx.org" in combined
    assert "htmx.min.js" in combined
    assert "kein Versions-String" in combined


def test_manipulated_package_version_is_detected(tmp_path: Path) -> None:
    """Verbiegt die gepinnte dexie-Version in package.json -> Drift -> Exit != 0."""
    pinned = json.loads((REPO_ROOT / "package.json").read_text())["devDependencies"]["dexie"]
    mirror = _mirror_repo(
        tmp_path / "repo",
        mutate={"package.json": f'"dexie": "{pinned}"\x00"dexie": "9.9.9"'},
    )
    result = _run(mirror)
    assert result.returncode == 1
    assert "dexie" in (result.stdout + result.stderr)


def test_non_exact_pin_is_rejected(tmp_path: Path) -> None:
    """Ein Range-Pin (``^``) auf einer vendored Lib wird als Fehler gemeldet."""
    pinned = json.loads((REPO_ROOT / "package.json").read_text())["devDependencies"]["chart.js"]
    mirror = _mirror_repo(
        tmp_path / "repo",
        mutate={"package.json": f'"chart.js": "{pinned}"\x00"chart.js": "^{pinned}"'},
    )
    result = _run(mirror)
    assert result.returncode == 1
    assert "EXAKT" in (result.stdout + result.stderr)


def test_license_text_missing_is_detected(tmp_path: Path) -> None:
    """Removing a shipped library's license text turns the guard red (probe in tmp_path)."""
    mirror = _mirror_repo(tmp_path / "repo")
    assert _run(mirror).returncode == 0
    (mirror / VENDOR_REL / "licenses" / "NOTICE-dexie.txt").unlink()
    result = _run(mirror)
    assert result.returncode == 1
    assert "NOTICE-dexie.txt" in result.stderr


def test_font_license_missing_is_detected(tmp_path: Path) -> None:
    mirror = _mirror_repo(tmp_path / "repo")
    (mirror / FONT_REL / "LICENSE-DM-fonts.txt").unlink()
    result = _run(mirror)
    assert result.returncode == 1
    assert "DM Sans Project Authors" in result.stderr


def test_unregistered_vendor_script_is_detected(tmp_path: Path) -> None:
    """A new shipped *.min.js without a license entry cannot pass silently."""
    mirror = _mirror_repo(tmp_path / "repo")
    (mirror / VENDOR_REL / "vendor" / "newlib.min.js").write_text("/*! newlib 1.0.0 */", encoding="utf-8")
    result = _run(mirror)
    assert result.returncode == 1
    assert "vendor/newlib.min.js" in result.stderr


def test_unknown_font_family_is_detected(tmp_path: Path) -> None:
    mirror = _mirror_repo(tmp_path / "repo")
    (mirror / FONT_REL / "otherfont-latin.woff2").write_bytes(b"wOF2")
    result = _run(mirror)
    assert result.returncode == 1
    assert "otherfont" in result.stderr


def test_missing_thirdparty_row_is_detected(tmp_path: Path) -> None:
    mirror = _mirror_repo(tmp_path / "repo")
    table = mirror / "THIRDPARTY-LICENSES.md"
    table.write_text(
        "\n".join(line for line in table.read_text(encoding="utf-8").splitlines() if "Chart.js" not in line),
        encoding="utf-8",
    )
    result = _run(mirror)
    assert result.returncode == 1
    assert "row for Chart.js" in result.stderr


@pytest.mark.parametrize("name", ["LICENSE-alpine.txt", "LICENSE-sortablejs.txt"])
def test_mit_license_text_missing_is_detected(tmp_path: Path, name: str) -> None:
    """Alpine.js and SortableJS have no exception: removing their license text fails."""
    mirror = _mirror_repo(tmp_path / "repo")
    (mirror / VENDOR_REL / "licenses" / name).unlink()
    result = _run(mirror)
    assert result.returncode == 1
    assert name in result.stderr


def test_runs_without_node_modules(tmp_path: Path) -> None:
    """Der Guard ist reiner String-Vergleich: kein node_modules/ im Abbild noetig."""
    mirror = _mirror_repo(tmp_path / "repo")
    assert not (mirror / "node_modules").exists()
    result = _run(mirror)
    assert result.returncode == 0, (
        f"Guard sollte ohne node_modules/ gruen sein:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert os.path.exists(mirror / "package.json")


if __name__ == "__main__":
    # Eigenstaendiger Selbsttest OHNE pytest (Test-DB ist seriell/--reuse-db).
    # Baut die Szenarien manuell und prueft die Exit-Codes; gibt am Ende eine
    # Zusammenfassung aus. Aufruf: python src/tests/test_vendor_js_sync_guard.py
    import tempfile

    failures: list[str] = []

    def _check(name: str, cond: bool) -> None:
        status = "OK  " if cond else "FAIL"
        print(f"  [{status}] {name}")
        if not cond:
            failures.append(name)

    print("Selbsttest scripts/verify_vendor_js_sync.py (ohne pytest):")

    real = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=REPO_ROOT, check=False)
    _check("echter Baum gruen (Exit 0)", real.returncode == 0)
    _check("Meldung '4 vendored JS-Libs'", "4 vendored JS-Libs" in real.stdout)

    with tempfile.TemporaryDirectory() as td:
        htmx_pin = json.loads((REPO_ROOT / "package.json").read_text())["devDependencies"]["htmx.org"]
        m = _mirror_repo(
            Path(td) / "drift",
            mutate={str(VENDOR_REL / "htmx.min.js"): f'version:"{htmx_pin}"\x00version:"0.0.0"'},
        )
        r = _run(m)
        _check("manipulierte htmx-Version -> Exit 1", r.returncode == 1)
        _check("Meldung enthaelt 'DRIFT'", "DRIFT" in (r.stdout + r.stderr))

    with tempfile.TemporaryDirectory() as td:
        chart_pin = json.loads((REPO_ROOT / "package.json").read_text())["devDependencies"]["chart.js"]
        m = _mirror_repo(
            Path(td) / "range",
            mutate={"package.json": f'"chart.js": "{chart_pin}"\x00"chart.js": "^{chart_pin}"'},
        )
        r = _run(m)
        _check("Range-Pin '^' -> Exit 1", r.returncode == 1)

    if failures:
        print(f"\n{len(failures)} Selbsttest(s) fehlgeschlagen: {failures}")
        sys.exit(1)
    print("\nAlle Selbsttests gruen.")
