"""Guard fuer die vendorte ``axe.min.js`` (Refs #1059).

Warum ein eigener Guard und nicht ``test_vendor_js_sync_guard.py``: Jener Guard
prueft ausschliesslich die vier npm-gepinnten Libraries aus
``scripts/verify_vendor_js_sync.py`` (``VENDOR_LIBS``) gegen ``package.json``.
axe-core folgt — wie SortableJS — der ANDEREN, manuellen Vendoring-Konvention:
Datei unter ``src/static/js/vendor/``, dokumentiert in ``THIRDPARTY-LICENSES.md``,
**kein** ``package.json``-Eintrag (Ticket-Vorgabe „keine Node-Pakete"). Ohne
diesen Guard gaebe es fuer die Datei gar keine automatische Kontrolle.

Geprueft wird genau das, was bei einem Vendoring schiefgehen kann:
Versionsdrift zwischen Datei und Lizenztabelle, ein versehentlich getauschter
oder abgeschnittener Datei-Inhalt (Integritaet gegen den von Deque
veroeffentlichten SRI-Wert) und ein Upgrade, das neue CSP-kritische
Konstrukte einschleppt.

``pytest.mark.architecture``: reiner Source-Scan ohne Datenbank, und der Test
liest Dateien ausserhalb von ``src/`` (``THIRDPARTY-LICENSES.md`` im
Repo-Wurzelverzeichnis) — im Mutmut-Lauf gibt es nur den ``mutants/``-Baum,
der Marker deselektiert ihn dort (Welle-E-Lehre 29).
"""

from __future__ import annotations

import base64
import hashlib
import re
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
AXE_PATH = _REPO_ROOT / "src/static/js/vendor/axe.min.js"
LICENSES_PATH = _REPO_ROOT / "THIRDPARTY-LICENSES.md"

# Von Deque selbst veroeffentlichter SRI-Wert fuer axe-core 4.13.0
# (``axe-core-4.13.0.tgz`` -> ``package/sri-history.json`` -> ``"4.13.0"`` ->
# ``"axe.min.js"``). Beim Upgrade wird dieser Wert aus derselben Quelle der
# NEUEN Version uebernommen — nicht aus der heruntergeladenen Datei selbst
# nachgerechnet, sonst prueft der Guard nur sich selbst.
EXPECTED_SRI = "sha256-wk8Je9L0UdT5M+i8fY1Tn4ZyouvLXMn58+7IypRwoME="

# Kleinster plausibler Umfang. Der echte Build liegt bei ~580 kB; 100 kB
# schlaegt zuverlaessig bei einem abgeschnittenen Download oder einer
# versehentlich vendorten Stub-/Locale-Datei an.
MIN_SIZE_BYTES = 100 * 1024

# Stand der manuellen CSP-Durchsicht von 4.13.0: sechs ``new Function``-
# Aufrufstellen, alle in Pfaden, die ein reiner ``axe.run()`` nicht betritt
# (doT-Kompilierung nur bei ``axe.configure({locale})``,
# Metadata-Function-Deserialisierung nur bei Custom-Checks, ein
# ``es5-ext``-Arity-Fallback fuer Engines ohne konfigurierbares
# ``Function.length``). Der Guard laesst diese Zahl zu und schlaegt an, wenn
# ein Upgrade MEHR mitbringt — dann ist die Durchsicht zu wiederholen, bevor
# ``script-src 'self'`` weiter als ausreichend gilt.
MAX_NEW_FUNCTION_OCCURRENCES = 6

_HEADER_VERSION = re.compile(r"^/\*!\s*axe\s+v(?P<version>\d+\.\d+\.\d+)")
_TABLE_ROW = re.compile(
    r"^\|\s*axe-core\s*\|\s*(?P<version>\S+)\s*\|\s*(?P<license>[^|]+?)\s*\|[^|]*\|\s*`(?P<path>[^`]+)`\s*\|",
    re.MULTILINE,
)


@lru_cache(maxsize=1)
def _axe_source() -> str:
    """Dateiinhalt einmal je Prozess lesen (580 kB, drei Leser)."""
    return AXE_PATH.read_text(encoding="utf-8", errors="ignore")


def header_version(text: str) -> str | None:
    """Versionsnummer aus dem Lizenz-Header der Minfile-Datei."""
    match = _HEADER_VERSION.match(text)
    return match.group("version") if match else None


def licence_table_rows(text: str) -> list[dict[str, str]]:
    """Alle ``axe-core``-Zeilen der Tabelle „Vendored JavaScript"."""
    return [m.groupdict() for m in _TABLE_ROW.finditer(text)]


class TestVendoredAxeFile:
    """Die eingecheckte Datei selbst."""

    def test_file_exists_and_is_not_truncated(self):
        assert AXE_PATH.exists(), f"{AXE_PATH} fehlt — axe-Vendoring unvollstaendig (Refs #1059)."
        size = AXE_PATH.stat().st_size
        assert size > MIN_SIZE_BYTES, (
            f"{AXE_PATH} ist nur {size} Bytes gross (erwartet > {MIN_SIZE_BYTES}). "
            "Abgeschnittener Download oder falsche Datei aus dem Tarball?"
        )

    def test_integrity_matches_upstream_sri(self):
        """Byte-Identitaet mit dem von Deque veroeffentlichten Build."""
        digest = hashlib.sha256(AXE_PATH.read_bytes()).digest()
        actual = "sha256-" + base64.b64encode(digest).decode()
        assert actual == EXPECTED_SRI, (
            f"{AXE_PATH} weicht vom veroeffentlichten Upstream-Build ab.\n"
            f"  erwartet: {EXPECTED_SRI}\n  gefunden: {actual}\n"
            "Bei einem bewussten Upgrade den Wert aus sri-history.json der NEUEN "
            "Version uebernehmen — nicht aus der heruntergeladenen Datei nachrechnen."
        )

    def test_version_in_header_matches_licence_table(self):
        """Kein Drift zwischen Datei und ``THIRDPARTY-LICENSES.md``."""
        in_file = header_version(AXE_PATH.read_text(encoding="utf-8", errors="ignore")[:512])
        rows = licence_table_rows(LICENSES_PATH.read_text(encoding="utf-8"))

        assert in_file is not None, "Lizenz-Header von axe.min.js nennt keine Version (`/*! axe vX.Y.Z`)."
        assert len(rows) == 1, f"THIRDPARTY-LICENSES.md fuehrt {len(rows)} axe-core-Zeilen, erwartet genau eine."
        row = rows[0]
        assert row["version"] == in_file, (
            f"Versionsdrift: axe.min.js sagt {in_file}, THIRDPARTY-LICENSES.md sagt {row['version']}."
        )
        assert row["license"] == "MPL-2.0", f"Lizenzangabe fuer axe-core ist '{row['license']}', erwartet 'MPL-2.0'."
        assert row["path"] == "src/static/js/vendor/axe.min.js", (
            f"Vendored Path in der Tabelle ist '{row['path']}' und zeigt nicht auf die eingecheckte Datei."
        )


class TestVendoredAxeIsCspCompatible:
    """CSP-Vorbedingung: ``script-src 'self'`` ohne ``'unsafe-eval'``.

    Der Scan ist eine Heuristik ueber den minifizierten Text — er kann eine
    Zeichenkette nicht sicher von einem Aufruf unterscheiden. Fuer 4.13.0 wurde
    jede Fundstelle einmal von Hand am unminifizierten ``axe.js`` verifiziert;
    der Guard haelt diesen Stand fest, statt ihn beim naechsten Upgrade
    stillschweigend zu verlieren.
    """

    def test_no_eval_call(self):
        source = _axe_source()
        assert source.count("eval(") == 0, (
            "axe.min.js enthaelt `eval(` — unter `script-src 'self'` (ohne 'unsafe-eval') "
            "bricht der Aufruf zur Laufzeit ab. Fundstellen pruefen, bevor die Version bleibt."
        )

    def test_no_document_write(self):
        source = _axe_source()
        assert source.count("document.write") == 0, (
            "axe.min.js enthaelt `document.write` — das schreibt in das Dokument, das gerade "
            "auditiert wird, und verfaelscht damit jeden Befund."
        )

    def test_new_function_occurrences_do_not_grow(self):
        found = _axe_source().count("new Function")
        assert found <= MAX_NEW_FUNCTION_OCCURRENCES, (
            f"axe.min.js enthaelt {found}x `new Function`, durchgesehen sind "
            f"{MAX_NEW_FUNCTION_OCCURRENCES}. Jede zusaetzliche Stelle muss am unminifizierten "
            "axe.js daraufhin geprueft werden, ob `axe.run()` sie erreicht — sonst waere "
            "'unsafe-eval' noetig, und das ist keine Option."
        )


class TestGuardHelpers:
    """Negativtests der Parser gegen ``tmp_path``.

    Die Helfer bekommen ihren Text als Argument — es wird nie eine Sonde in den
    echten Baum gelegt.
    """

    def test_header_version_reads_the_axe_banner(self):
        assert header_version("/*! axe v4.13.0\n * Copyright") == "4.13.0"

    def test_header_version_is_none_without_banner(self):
        assert header_version("!function(){}();") is None

    def test_header_version_ignores_a_banner_of_another_library(self):
        assert header_version("/*! Sortable 1.15.7 -- MIT */") is None

    def test_table_rows_ignore_other_packages(self):
        text = (
            "| Package | Version | License | Upstream | Vendored Path |\n"
            "|---|---|---|---|---|\n"
            "| SortableJS | 1.15.7 | MIT | <https://x> | `src/static/js/vendor/sortable.min.js` |\n"
            "| axe-core | 4.13.0 | MPL-2.0 | <https://y> | `src/static/js/vendor/axe.min.js` |\n"
        )
        rows = licence_table_rows(text)
        assert rows == [
            {
                "version": "4.13.0",
                "license": "MPL-2.0",
                "path": "src/static/js/vendor/axe.min.js",
            }
        ]

    def test_table_rows_detect_a_duplicated_entry(self):
        text = (
            "| axe-core | 4.13.0 | MPL-2.0 | <https://y> | `src/static/js/vendor/axe.min.js` |\n"
            "| axe-core | 4.12.0 | MPL-2.0 | <https://y> | `src/static/js/vendor/axe.min.js` |\n"
        )
        assert len(licence_table_rows(text)) == 2

    def test_table_rows_are_empty_without_the_package(self, tmp_path: Path):
        doc = tmp_path / "THIRDPARTY-LICENSES.md"
        doc.write_text("| SortableJS | 1.15.7 | MIT | <https://x> | `a.js` |\n", encoding="utf-8")
        assert licence_table_rows(doc.read_text(encoding="utf-8")) == []
