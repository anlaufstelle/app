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

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_JSON = ROOT / "package.json"
VENDOR_DIR = ROOT / "src" / "static" / "js"
LICENSE_DIR = VENDOR_DIR / "licenses"
FONT_DIR = ROOT / "src" / "static" / "fonts"
FONT_LICENSE = FONT_DIR / "LICENSE-DM-fonts.txt"
THIRDPARTY = ROOT / "THIRDPARTY-LICENSES.md"


@dataclass(frozen=True)
class VendorLib:
    """Eine vendored JS-Lib: npm-Paketname, eingecheckte Datei, Versions-Regex.

    ``version_re`` muss die Version in genau einer Capture-Gruppe liefern.
    Mehrere Patterns werden der Reihe nach probiert (Builds variieren leicht).
    """

    package: str
    vendored_filename: str
    version_patterns: tuple[str, ...]
    table_name: str = ""


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
        table_name="htmx",
    ),
    VendorLib(
        package="@alpinejs/csp",
        vendored_filename="alpine-csp.min.js",
        version_patterns=(r'version\s*:\s*"(\d+\.\d+\.\d+)"',),
        table_name="Alpine.js CSP",
    ),
    VendorLib(
        package="dexie",
        vendored_filename="dexie.min.js",
        version_patterns=(r'version\s*:\s*"(\d+\.\d+\.\d+)"',),
        table_name="Dexie.js",
    ),
    VendorLib(
        package="chart.js",
        vendored_filename="chart.min.js",
        version_patterns=(
            r"Chart\.js\s+v(\d+\.\d+\.\d+)",
            r"chart\.js@(\d+\.\d+\.\d+)",
        ),
        table_name="Chart.js",
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


# Vendored builds outside package.json: (file, version regex, required header marker).
# An empty marker skips the header check.
UNMANAGED_BUILDS: dict[str, tuple[str, str, str]] = {
    "SortableJS": ("vendor/sortable.min.js", r"Sortable (\d+\.\d+\.\d+)", ""),
    "axe-core": ("vendor/axe.min.js", r"axe v(\d+\.\d+\.\d+)", "Mozilla Public"),
}

# Self-hosted fonts: sha256 per file. A replaced font changes the hash, which forces
# a review of the version and license rows below.
FONT_HASHES: dict[str, str] = {
    "dmmono-400-latin-ext.woff2": "a52e19ebe0398c9c0f8fa28a0c5e9a6bc324f35d9f0881c9aa407d10af447175",
    "dmmono-400-latin.woff2": "e1896b13b2b1bb112fac2f9571bd6c40e118746e77a4511edbf43fbb41bf3e1e",
    "dmmono-500-latin-ext.woff2": "8711f938c3f04f91e35ae64ebc7f2eecd800d2ae1c3542f6c837c758a50422f5",
    "dmmono-500-latin.woff2": "9964608a849396bd00c4bfd7034afe03486469dcf20b4f6b8cbdfdd310369951",
    "dmsans-latin-ext.woff2": "a5d38fe99f930275684999b462c7123faa063d9e44e73b4b241723d884aa0f49",
    "dmsans-latin.woff2": "9fea608a947e67020c33cad9a6fe3d60c54119dfb8cff87768a8117a15ed7543",
}
FONT_VERSIONS = {"DM Sans": "4.004", "DM Mono": "1.000"}
ICON_DIR = ROOT / "src" / "static" / "icons"
ICON_FILES = ("favicon.svg", "icon-192.svg", "icon-512.svg")
ICON_LICENSE = ICON_DIR / "LICENSE-heroicons.txt"
HEROICONS_VERSION = "2.0.18"

TABLE_HEADER = ["Package", "Version", "License", "Upstream", "Vendored Path", "License text"]
JS_LICENSES = "src/static/js/licenses/"
# Per work: (license id, upstream URL, path-cell tokens, license-text-cell tokens). A cell must name
# exactly these backticked tokens; a work without text tokens must say "file header" instead.
TABLE_EXPECTED: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    "htmx": (
        "0BSD",
        "https://github.com/bigskysoftware/htmx",
        ("src/static/js/htmx.min.js",),
        (JS_LICENSES + "LICENSE-htmx.txt",),
    ),
    "Alpine.js CSP": (
        "MIT",
        "https://github.com/alpinejs/alpine",
        ("src/static/js/alpine-csp.min.js",),
        (JS_LICENSES + "LICENSE-alpine.txt",),
    ),
    "Chart.js": (
        "MIT",
        "https://github.com/chartjs/Chart.js",
        ("src/static/js/chart.min.js",),
        (JS_LICENSES + "LICENSE-chartjs.txt",),
    ),
    "Dexie.js": (
        "Apache-2.0",
        "https://github.com/dexie/Dexie.js",
        ("src/static/js/dexie.min.js",),
        (JS_LICENSES + "LICENSE-dexie.txt", JS_LICENSES + "NOTICE-dexie.txt"),
    ),
    "SortableJS": (
        "MIT",
        "https://github.com/SortableJS/Sortable",
        ("src/static/js/vendor/sortable.min.js",),
        (JS_LICENSES + "LICENSE-sortablejs.txt",),
    ),
    "axe-core": ("MPL-2.0", "https://github.com/dequelabs/axe-core", ("src/static/js/vendor/axe.min.js",), ()),
    "DM Sans": (
        "SIL OFL 1.1",
        "https://github.com/googlefonts/dm-fonts",
        ("src/static/fonts/dmsans-*.woff2",),
        ("src/static/fonts/LICENSE-DM-fonts.txt",),
    ),
    "DM Mono": (
        "SIL OFL 1.1",
        "https://github.com/googlefonts/dm-mono",
        ("src/static/fonts/dmmono-*.woff2",),
        ("src/static/fonts/LICENSE-DM-fonts.txt",),
    ),
    "Heroicons": (
        "MIT",
        "https://github.com/tailwindlabs/heroicons",
        ("src/static/icons/*.svg", "src/templates/auth/login.html"),
        ("src/static/icons/LICENSE-heroicons.txt",),
    ),
}
# License texts must carry their obligatory parts: a placeholder or a truncated
# text fails even though the file is non-empty. (markers, minimum size in bytes)
LICENSE_TEXT_RULES: dict[str, tuple[tuple[str, ...], int]] = {
    "src/static/js/licenses/LICENSE-htmx.txt": (("Zero-Clause BSD", "Permission to use, copy, modify"), 400),
    "src/static/js/licenses/LICENSE-alpine.txt": (
        ("Caleb Porzio", "Permission is hereby granted", "THE SOFTWARE IS PROVIDED"),
        800,
    ),
    "src/static/js/licenses/LICENSE-chartjs.txt": (
        ("Chart.js Contributors", "Permission is hereby granted", "THE SOFTWARE IS PROVIDED"),
        800,
    ),
    "src/static/js/licenses/LICENSE-sortablejs.txt": (
        ("All contributors to Sortable", "Permission is hereby granted", "THE SOFTWARE IS PROVIDED"),
        800,
    ),
    "src/static/js/licenses/LICENSE-dexie.txt": (
        ("Apache License", "Version 2.0", "TERMS AND CONDITIONS", "END OF TERMS AND CONDITIONS"),
        8000,
    ),
    "src/static/js/licenses/NOTICE-dexie.txt": (
        ("David Fahlander", "Apache License, Version 2.0", "limitations under the License"),
        400,
    ),
    "src/static/fonts/LICENSE-DM-fonts.txt": (
        (
            "SIL OPEN FONT LICENSE Version 1.1",
            "DM Sans Project Authors",
            "DM Mono Project Authors",
            "PREAMBLE",
            "DEFINITIONS",
            "OTHER DEALINGS IN THE FONT SOFTWARE",
        ),
        3000,
    ),
    "src/static/icons/LICENSE-heroicons.txt": (
        ("Refactoring UI Inc", "Permission is hereby granted", "THE SOFTWARE IS PROVIDED"),
        800,
    ),
}
# SHA-256 of each license text. Files with a source line (Alpine.js, SortableJS, Heroicons)
# are hashed after that line and the blank line below it; all others as a whole. Every text
# was compared byte for byte with the upstream file at the vendored tag or the installed
# package, so removing a clause (e.g. the MIT condition to keep the notice) changes the hash.
SOURCE_LINE_TEXTS = ("alpine.txt", "sortablejs.txt", "heroicons.txt")
LICENSE_TEXT_SHA256: dict[str, str] = {
    "src/static/js/licenses/LICENSE-htmx.txt": "d3d2456f76414f2456104660ebd65aff1c04cd7966b942bdabd63f3cdb316a38",
    "src/static/js/licenses/LICENSE-alpine.txt": "08b7502da6e7aa1d0bbdc97d220fbf669b9366c61bd0f072238283c89bc4773a",
    "src/static/js/licenses/LICENSE-chartjs.txt": "41a84aa2caba645f966a18d9c2056b73e6d3a81d80bc0046bc0011a2634d4cce",
    "src/static/js/licenses/LICENSE-sortablejs.txt": "199071e94a4d6ba6f634acd6020842efc55161b9fb639a432c50da687781219d",
    "src/static/js/licenses/LICENSE-dexie.txt": "cb5e8e7e5f4a3988e1063c142c60dc2df75605f4c46515e776e3aca6df976e14",
    "src/static/js/licenses/NOTICE-dexie.txt": "6176d2e8a3fd8a881828a1a27bbcc5a9e03a75f437ce3c4ddbedd94fdac475c1",
    "src/static/fonts/LICENSE-DM-fonts.txt": "51a024caa839b97cc0f85f4578f69356d043e897da8a1e3690e380a913c19f02",
    "src/static/icons/LICENSE-heroicons.txt": "75523ddd65d9620bea09f84e89d0c373b4205a3708b8a1e9f9598a5438a3e641",
}


def parse_table(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Header cells and rows (first cell -> all cells) of the vendored-works table."""
    header: list[str] = []
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] == "Package":
            header = cells
        elif header and not set(cells[0]) <= {"-"}:
            rows[cells[0]] = cells
    return header, rows


def _compare_tokens(name: str, label: str, expected: tuple[str, ...], cell: str) -> list[str]:
    """Backticked tokens of ``cell`` must equal ``expected`` exactly (no substring match)."""
    found = re.findall(r"`([^`]+)`", cell)
    errors = [f"THIRDPARTY-LICENSES.md: {name} {label} {t!r} missing" for t in expected if t not in found]
    errors += [f"THIRDPARTY-LICENSES.md: {name} {label} {t!r} unexpected" for t in found if t not in expected]
    return errors


def _nonempty(path: Path) -> bool:
    return path.is_file() and bool(path.read_text(encoding="utf-8").strip())


def _check_license_texts() -> list[str]:
    errors: list[str] = []
    for rel, (markers, min_size) in LICENSE_TEXT_RULES.items():
        path = ROOT / rel
        if not _nonempty(path):
            errors.append(f"license text missing or empty: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) < min_size:
            errors.append(f"{rel}: license text truncated or a placeholder (< {min_size} bytes)")
        errors += [f"{rel}: license text incomplete ({m!r} missing)" for m in markers if m not in text]
        if "Source: " not in text.splitlines()[0] and rel.endswith(("alpine.txt", "sortablejs.txt", "heroicons.txt")):
            errors.append(f"{rel}: source line missing in the first line")
        body = text.split("\n", 2)[2] if rel.endswith(SOURCE_LINE_TEXTS) and text.count("\n") >= 2 else text
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != LICENSE_TEXT_SHA256[rel]:
            errors.append(f"{rel}: license text differs from the verified upstream text")
    return errors


def _discover_shipped() -> list[str]:
    """Shipped third-party files that are not registered."""
    errors: list[str] = []
    registered = {lib.vendored_filename for lib in VENDOR_LIBS} | {entry[0] for entry in UNMANAGED_BUILDS.values()}
    shipped = {p.relative_to(VENDOR_DIR).as_posix() for p in VENDOR_DIR.glob("*.min.js")}
    vendor_subdir = VENDOR_DIR / "vendor"
    if vendor_subdir.is_dir():
        shipped |= {p.relative_to(VENDOR_DIR).as_posix() for p in vendor_subdir.rglob("*") if p.is_file()}
    errors += [f"{n}: shipped vendor file is not registered in the license guard" for n in sorted(shipped - registered)]

    if FONT_DIR.is_dir():
        fonts = {
            p.relative_to(FONT_DIR).as_posix(): p for p in FONT_DIR.rglob("*") if p.is_file() and p != FONT_LICENSE
        }
        for name in sorted(set(fonts) - set(FONT_HASHES)):
            errors.append(f"font without license mapping: {name}")
        for name, digest in FONT_HASHES.items():
            if name not in fonts:
                errors.append(f"registered font missing: {name}")
            elif hashlib.sha256(fonts[name].read_bytes()).hexdigest() != digest:
                errors.append(
                    f"font changed: {name} — review version and license, then update FONT_HASHES/FONT_VERSIONS"
                )
    if ICON_DIR.is_dir():
        icons = {p.relative_to(ICON_DIR).as_posix() for p in ICON_DIR.rglob("*") if p.is_file()}
        known = set(ICON_FILES) | {ICON_LICENSE.name} | {n.replace(".svg", ".png") for n in ICON_FILES}
        errors += [f"icon without license mapping: {n}" for n in sorted(icons - known)]
        for name in ICON_FILES:
            path = ICON_DIR / name
            if not path.is_file() or "Heroicons" not in path.read_text(encoding="utf-8"):
                errors.append(f"icon {name}: Heroicons attribution missing")
    return errors


def check_licenses(pinned: dict[str, str]) -> list[str]:
    """Every shipped third-party work has its license text in the repo and a complete row in THIRDPARTY-LICENSES.md."""
    errors = _discover_shipped() + _check_license_texts()

    versions: dict[str, str] = {lib.table_name: pinned.get(lib.package, "") for lib in VENDOR_LIBS}
    for name, (fname, pattern, marker) in UNMANAGED_BUILDS.items():
        path = VENDOR_DIR / fname
        head = path.read_text(encoding="utf-8")[:2000] if path.is_file() else ""
        match = re.search(pattern, head)
        versions[name] = match.group(1) if match else ""
        if not match:
            errors.append(f"{name}: no version header in {fname}")
        if marker and marker not in head:
            errors.append(f"{name}: license header ({marker}) missing in {fname}")
    versions |= FONT_VERSIONS | {"Heroicons": HEROICONS_VERSION}

    table = THIRDPARTY.read_text(encoding="utf-8") if THIRDPARTY.is_file() else ""
    header, rows = parse_table(table)
    if header != TABLE_HEADER:
        errors.append(f"THIRDPARTY-LICENSES.md: table header must be {' | '.join(TABLE_HEADER)}")
        return errors
    for name, (spdx, upstream, paths, texts) in TABLE_EXPECTED.items():
        row = rows.get(name)
        if row is None or len(row) != len(TABLE_HEADER):
            errors.append(f"THIRDPARTY-LICENSES.md: row for {name} missing or malformed")
            continue
        if row[1] != versions.get(name):
            errors.append(f"THIRDPARTY-LICENSES.md: {name} version is {row[1]!r}, expected {versions.get(name)!r}")
        if row[2] != spdx:
            errors.append(f"THIRDPARTY-LICENSES.md: {name} license is {row[2]!r}, expected {spdx!r}")
        if f"<{upstream}>" not in row[3]:
            errors.append(f"THIRDPARTY-LICENSES.md: {name} upstream <{upstream}> missing")
        errors += _compare_tokens(name, "path", paths, row[4])
        errors += _compare_tokens(name, "license text", texts, row[5])
        if not texts and "file header" not in row[5]:
            errors.append(f"THIRDPARTY-LICENSES.md: {name} license text must say 'file header'")
    errors += [
        f"THIRDPARTY-LICENSES.md: row for {n} is not registered in the guard"
        for n in sorted(set(rows) - set(TABLE_EXPECTED))
    ]
    return errors


def main(argv: list[str] | None = None) -> int:
    if not PACKAGE_JSON.is_file():
        print(f"FEHLER: package.json nicht gefunden: {PACKAGE_JSON}", file=sys.stderr)
        return 2

    pinned = load_pinned_versions(PACKAGE_JSON)

    errors = [msg for lib in VENDOR_LIBS if (msg := check_lib(lib, pinned)) is not None]
    errors += check_licenses(pinned)

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
