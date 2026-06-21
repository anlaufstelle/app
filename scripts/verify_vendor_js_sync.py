#!/usr/bin/env python3
"""CI drift-guard: vendored JS libs must match the version pinned in ``package.json``.

Vier Frontend-Libs werden als gepinnte ``devDependencies`` in
``package.json`` gefuehrt, aber als vorgebaute ``*.min.js`` unter
``src/static/js/`` eingecheckt (vendored). Dependabot oeffnet Update-PRs
auf der Manifest-Version; dieser Guard erzwingt, dass der Vendor-Sync
(``scripts/sync_vendor_js.py``) nicht vergessen wird: er vergleicht die in
``package.json`` gepinnte Version mit dem Versions-String, der im
vendored ``*.min.js`` eingebettet ist, und failt (Exit-Code != 0) bei Drift.

Bewusst **reiner String-Vergleich** — KEIN node/npm noetig, kein
``node_modules/``. Ein byte-genauer Vergleich waere zu sproede: die
eingecheckten Builds stammen teils vom CDN (jsdelivr) und unterscheiden
sich vom npm-``dist`` nur in Kommentaren (``sourceMappingURL``-Zeile,
Header-Banner) — der eingebettete Versions-String ist der stabile Anker.

Stdlib-only. Aufruf::

    python scripts/verify_vendor_js_sync.py

Refs #1076.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_JSON = ROOT / "package.json"
VENDOR_DIR = ROOT / "src" / "static" / "js"


@dataclass(frozen=True)
class VendorLib:
    """Eine vendored JS-Lib: npm-Paketname, eingecheckte Datei, Versions-Regex.

    ``version_re`` muss die Version in genau einer Capture-Gruppe liefern.
    Mehrere Patterns werden der Reihe nach probiert (Builds variieren leicht).
    """

    package: str
    vendored_filename: str
    version_patterns: tuple[str, ...]


# Die vier vendored Libs (Refs #1076). Die Patterns matchen den Versions-String,
# wie ihn der jeweilige Build einbettet:
#   htmx        ... version:"2.0.4"        (config-Objekt)
#   @alpinejs/csp ... version:"3.14.8"     (Alpine.version)
#   dexie       ... version:"4.2.0"        (Dexie.version)
#   chart.js    ... Chart.js v4.4.8        (Banner-Kommentar)
VENDOR_LIBS: tuple[VendorLib, ...] = (
    VendorLib(
        package="htmx.org",
        vendored_filename="htmx.min.js",
        version_patterns=(r'version\s*:\s*"(\d+\.\d+\.\d+)"',),
    ),
    VendorLib(
        package="@alpinejs/csp",
        vendored_filename="alpine-csp.min.js",
        version_patterns=(r'version\s*:\s*"(\d+\.\d+\.\d+)"',),
    ),
    VendorLib(
        package="dexie",
        vendored_filename="dexie.min.js",
        version_patterns=(r'version\s*:\s*"(\d+\.\d+\.\d+)"',),
    ),
    VendorLib(
        package="chart.js",
        vendored_filename="chart.min.js",
        version_patterns=(
            r"Chart\.js\s+v(\d+\.\d+\.\d+)",
            r"chart\.js@(\d+\.\d+\.\d+)",
        ),
    ),
)


def load_pinned_versions(package_json: Path) -> dict[str, str]:
    """``devDependencies`` (+ ``dependencies``) aus ``package.json`` als dict.

    Liefert die **rohen** Specs (inkl. ``^``/``~``/Whitespace), damit
    ``check_lib`` Range-Pins als Fehler erkennen kann — vendored Libs muessen
    exakt gepinnt sein.
    """
    data = json.loads(package_json.read_text(encoding="utf-8"))
    pinned: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        for name, spec in data.get(section, {}).items():
            pinned[name] = spec.strip()
    return pinned


class AmbiguousVersionError(Exception):
    """Ein Pattern matcht mehr als einmal -> die Version ist nicht eindeutig.

    Die generischen ``version:"x.y.z"``-Patterns (htmx/alpine/dexie) verlassen
    sich darauf, dass GENAU EIN solches Literal pro Min-File existiert. Bettet ein
    kuenftiger Build ein zweites ein, lieferte ``re.findall`` mehrere Treffer; den
    ersten blind zu nehmen waere ein stiller Fehler — moeglicherweise die FALSCHE
    Version. Statt zu raten, brechen wir hier laut ab; der Aufrufer reichert die
    Meldung um Lib/Datei an.
    """


def extract_vendored_version(text: str, patterns: tuple[str, ...]) -> str | None:
    """Eindeutige Version aus dem Datei-Inhalt extrahieren, sonst ``None``.

    Die ``patterns`` werden der Reihe nach probiert (Builds variieren leicht). Das
    ERSTE Pattern, das ueberhaupt greift, entscheidet — muss dann aber GENAU EINEN
    Treffer liefern. Geprueft wird per ``re.findall``: mehrere Treffer desselben
    Patterns sind mehrdeutig und loesen ``AmbiguousVersionError`` aus (statt still
    den ersten Treffer zu nehmen). Kein Pattern greift -> ``None``.
    """
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if not matches:
            continue
        if len(matches) > 1:
            raise AmbiguousVersionError(
                f"Pattern {pattern!r} matcht {len(matches)}x ({', '.join(sorted(set(matches)))}) — Version mehrdeutig"
            )
        return matches[0]
    return None


def check_lib(lib: VendorLib, pinned: dict[str, str]) -> str | None:
    """Prueft eine Lib. Gibt eine Fehlermeldung zurueck oder ``None`` (OK)."""
    if lib.package not in pinned:
        return f"{lib.package}: nicht als (dev)Dependency in package.json gepinnt"

    expected = pinned[lib.package]
    if not _is_exact_version(expected):
        return (
            f"{lib.package}: package.json pinnt '{expected}' — vendored Libs "
            f"muessen EXAKT gepinnt sein (ohne ^/~/Range), damit der Sync-Guard "
            f"eindeutig vergleichen kann"
        )

    vendored_path = VENDOR_DIR / lib.vendored_filename
    if not vendored_path.is_file():
        return f"{lib.package}: vendored Datei fehlt: {vendored_path.relative_to(ROOT)}"

    try:
        found = extract_vendored_version(vendored_path.read_text(encoding="utf-8"), lib.version_patterns)
    except AmbiguousVersionError as exc:
        return (
            f"{lib.package}: Versions-String in {vendored_path.relative_to(ROOT)} "
            f"ist MEHRDEUTIG ({exc}). Erwartet wird genau ein Treffer pro Min-File; "
            f"den Pattern fuer {lib.package} praezisieren (statt eines Zufalls-Treffers)."
        )
    if found is None:
        return (
            f"{lib.package}: kein Versions-String in "
            f"{vendored_path.relative_to(ROOT)} gefunden "
            f"(Patterns: {', '.join(lib.version_patterns)})"
        )

    if found != expected:
        return (
            f"{lib.package}: DRIFT — package.json pinnt v{expected}, "
            f"aber {vendored_path.relative_to(ROOT)} enthaelt v{found}. "
            f"'make sync-vendor-js' ausfuehren (oder package.json korrigieren)."
        )
    return None


def _is_exact_version(spec: str) -> bool:
    return re.fullmatch(r"\d+\.\d+\.\d+", spec) is not None


def main(argv: list[str] | None = None) -> int:
    if not PACKAGE_JSON.is_file():
        print(f"FEHLER: package.json nicht gefunden: {PACKAGE_JSON}", file=sys.stderr)
        return 2

    pinned = load_pinned_versions(PACKAGE_JSON)

    errors = [msg for lib in VENDOR_LIBS if (msg := check_lib(lib, pinned)) is not None]

    if errors:
        print("FEHLER: Vendored-JS-Sync-Drift erkannt:", file=sys.stderr)
        for msg in errors:
            print(f"  - {msg}", file=sys.stderr)
        print(
            "\nTipp: Nach einem Dependabot-Bump die dist-Builds neu vendoren via\n"
            "  npm ci && python scripts/sync_vendor_js.py\n"
            "und das Ergebnis committen. Details: CONTRIBUTING.md "
            "(Abschnitt 'Vendored JS aktualisieren').",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(VENDOR_LIBS)} vendored JS-Libs stimmen mit package.json ueberein.")
    for lib in VENDOR_LIBS:
        print(f"  - {lib.package}: v{pinned[lib.package]} ({lib.vendored_filename})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
