#!/usr/bin/env python3
"""Vendored JS-Libs aus ``node_modules/`` nach ``src/static/js/`` synchronisieren.

Vier Frontend-Libs (htmx, @alpinejs/csp, dexie, chart.js) sind als gepinnte
``devDependencies`` in ``package.json`` gefuehrt und werden als vorgebaute
``*.min.js`` unter ``src/static/js/`` eingecheckt (vendored). Templates laden
sie via ``{% static %}`` — es gibt KEINEN Frontend-Bundler.

Dieses Skript kopiert je Lib den passenden dist-Build aus ``node_modules/``
in die vendored Zieldatei. Nach einem Dependabot-Bump genuegt::

    npm ci                              # node_modules/ auf package-lock-Stand
    python scripts/sync_vendor_js.py    # dist-Builds neu vendoren
    git add src/static/js package.json package-lock.json

Der Drift-Guard ``scripts/verify_vendor_js_sync.py`` (Make-Target
``verify-vendor-js-sync``, in ``make ci``) verifiziert anschliessend, dass die
eingebettete Version mit ``package.json`` uebereinstimmt.

dist-Pfad-Mapping (Stand chart.js 4.4.8 / dexie 4.2.0 / htmx 2.0.4 / alpine 3.14.8):

    htmx.org        node_modules/htmx.org/dist/htmx.min.js        -> htmx.min.js
    @alpinejs/csp   node_modules/@alpinejs/csp/dist/cdn.min.js    -> alpine-csp.min.js
    dexie           node_modules/dexie/dist/dexie.min.js          -> dexie.min.js
    chart.js        node_modules/chart.js/dist/chart.umd.js       -> chart.min.js

Hinweis chart.js: das npm-``dist`` liefert KEINE ``chart.min.js`` — der UMD-Build
``chart.umd.js`` ist bereits minifiziert (so baut auch jsdelivr die CDN-Datei).
Wir vendoren ihn daher als ``chart.min.js``. Fuer @alpinejs/csp ist ``cdn.min.js``
der browser-globale Build (``module.esm.min.js`` waere ein ES-Modul — nicht das,
was die Templates per klassischem ``<script>`` laden).

Stdlib-only (ausser dem zuvor noetigen ``npm ci``). Refs #1076.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE_MODULES = ROOT / "node_modules"
VENDOR_DIR = ROOT / "src" / "static" / "js"


@dataclass(frozen=True)
class VendorSync:
    """Mapping: npm-Paket + relativer dist-Pfad -> vendored Zieldatei."""

    package: str
    dist_relpath: str  # relativ zu node_modules/<package>/
    vendored_filename: str  # Zieldatei unter src/static/js/


# Reihenfolge entspricht der Tabelle im Modul-Docstring.
SYNC_MAP: tuple[VendorSync, ...] = (
    VendorSync("htmx.org", "dist/htmx.min.js", "htmx.min.js"),
    VendorSync("@alpinejs/csp", "dist/cdn.min.js", "alpine-csp.min.js"),
    VendorSync("dexie", "dist/dexie.min.js", "dexie.min.js"),
    VendorSync("chart.js", "dist/chart.umd.js", "chart.min.js"),
)


# Abschliessender ``//# sourceMappingURL=...``-Kommentar minifizierter Builds.
# chart.js 4.5.1 / dexie 4.4.4 haengen ihn an, ohne die ``.map`` mitzuliefern —
# unter CompressedManifestStaticFilesStorage (prod) bricht ``collectstatic`` sonst
# mit MissingFileError ab. Rein devtools-relevant; wir vendoren ohne ihn (analog
# htmx/alpine, deren Builds keine Map-Referenz tragen).
_SOURCE_MAP_COMMENT_RE = re.compile(rb"\n//# sourceMappingURL=[^\n]*\n?$")


def _strip_source_map_comment(data: bytes) -> bytes:
    """Entfernt eine abschliessende ``//# sourceMappingURL=...``-Referenz."""
    return _SOURCE_MAP_COMMENT_RE.sub(b"\n", data)


def sync_one(entry: VendorSync, *, check_only: bool) -> tuple[bool, str]:
    """Kopiert (oder prueft) eine Lib. Rueckgabe ``(ok, meldung)``."""
    source = NODE_MODULES / entry.package / entry.dist_relpath
    target = VENDOR_DIR / entry.vendored_filename

    if not source.is_file():
        return (
            False,
            f"{entry.package}: dist-Build fehlt: {source.relative_to(ROOT)} (zuerst 'npm ci' ausfuehren)",
        )

    src_bytes = _strip_source_map_comment(source.read_bytes())
    if check_only:
        if not target.is_file():
            return (False, f"{entry.package}: vendored Datei fehlt: {target.relative_to(ROOT)}")
        if target.read_bytes() != src_bytes:
            return (
                False,
                f"{entry.package}: {target.relative_to(ROOT)} weicht vom dist-Build ab "
                f"('python scripts/sync_vendor_js.py' ausfuehren)",
            )
        return (True, f"{entry.package}: aktuell ({target.relative_to(ROOT)})")

    target.write_bytes(src_bytes)
    return (
        True,
        f"{entry.package}: {source.relative_to(ROOT)} -> {target.relative_to(ROOT)} ({len(src_bytes)} Bytes)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Nur pruefen, ob vendored Dateien byte-genau dem dist-Build entsprechen (kein Schreiben).",
    )
    args = parser.parse_args(argv)

    if not NODE_MODULES.is_dir():
        print(
            "FEHLER: node_modules/ fehlt. Zuerst 'npm ci' (oder 'npm install') ausfuehren.",
            file=sys.stderr,
        )
        return 2

    results = [sync_one(entry, check_only=args.check) for entry in SYNC_MAP]
    failures = [msg for ok, msg in results if not ok]

    for ok, msg in results:
        prefix = "OK " if ok else "!! "
        print(f"{prefix}{msg}", file=sys.stderr if not ok else sys.stdout)

    if failures:
        verb = "Pruefung" if args.check else "Sync"
        print(f"\nFEHLER: Vendored-JS-{verb} fehlgeschlagen ({len(failures)} Lib(s)).", file=sys.stderr)
        return 1

    action = "geprueft" if args.check else "synchronisiert"
    print(f"\nOK: {len(SYNC_MAP)} vendored JS-Libs {action}.")
    if not args.check:
        print("Bitte 'src/static/js/' committen (node_modules/ ist gitignored).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
