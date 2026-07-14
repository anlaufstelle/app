#!/usr/bin/env python3
"""CI check: every translated file must declare a translation-version
header that matches the current minor version of `pyproject.toml`
(hard release gate since 2026-06-12, was: two minors tolerance).

Since #1552 each translated file must additionally carry a `source-hash`
marker whose value is the `git hash-object` blob hash of its DE source at the
last EN sync (introduced as a stamp in #1551). The guard recomputes the
source's current blob hash and fails **hard** on any drift (no warn mode) —
the stamp is refreshed in the same PR that updates the translation.
Refs #832, #1078, #1551, #1552.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_MINOR_BEHIND = 0
# git-Kurz-Hashes sind mind. 7 Hex-Zeichen; kuerzere Stempel gelten als ungueltig.
MIN_HASH_LEN = 7

VERSION_RE = re.compile(r"<!--\s*translation-version:\s*v(\d+)\.(\d+)\.(\d+)\s*-->")
# Capture-Gruppe = kompletter Quell-Wert (Pfad + optionale Annotation wie
# "(chapter 14)"); der Pfad ist das erste Whitespace-getrennte Token.
SOURCE_RE = re.compile(r"<!--\s*translation-source:\s*([^\n]+?)\s*-->")
SOURCE_HASH_RE = re.compile(r"<!--\s*source-hash:\s*([0-9a-fA-F]+)\s*-->")

TRANSLATED_FILES = [
    "README.en.md",
    "CONTRIBUTING.en.md",
    "docs/en/README.md",
    "docs/en/admin-guide.md",
    "docs/en/user-guide.md",
    "docs/en/domain-concept-summary.md",
    "docs/en/glossary.md",
    "docs/en/data-protection.md",
]


def current_version() -> tuple[int, int, int]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    raw = data["project"]["version"]
    parts = raw.split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def parse_marker(text: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def source_path(text: str) -> str | None:
    """Erster Whitespace-getrennter Token des translation-source-Werts (der
    DE-Quellpfad; Annotationen wie ``(chapter 14)`` werden abgeschnitten)."""
    match = SOURCE_RE.search(text)
    if match is None:
        return None
    value = match.group(1).strip()
    return value.split()[0] if value else None


def git_hash_object(path: Path) -> str:
    """Blob-Hash der DE-Quelle wie ``git hash-object`` — funktioniert auch
    ausserhalb eines Repos (reine SHA-1-Berechnung ueber den Datei-Inhalt)."""
    result = subprocess.run(
        ["git", "hash-object", str(path)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    return result.stdout.strip().lower()


def check_source_hash(rel: str, text: str) -> list[str]:
    """source-hash-Marker gegen ``git hash-object`` der DE-Quelle abgleichen.
    Harter Fail bei fehlendem/zu kurzem Marker, fehlender Quelle oder Drift."""
    errors: list[str] = []
    src = source_path(text)
    if src is None:
        # Fehlender translation-source-Header wird bereits separat gemeldet.
        return errors
    match = SOURCE_HASH_RE.search(text)
    if match is None:
        errors.append(f"{rel}: missing source-hash header")
        return errors
    stored = match.group(1).lower()
    if len(stored) < MIN_HASH_LEN:
        errors.append(f"{rel}: source-hash '{stored}' too short (min {MIN_HASH_LEN} hex chars)")
        return errors
    source_file = ROOT / src
    if not source_file.exists():
        errors.append(f"{rel}: translation source '{src}' missing")
        return errors
    actual = git_hash_object(source_file)
    if not actual.startswith(stored):
        errors.append(
            f"{rel}: DE-Quelle '{src}' geändert seit letztem EN-Sync "
            f"(source-hash {stored}, aktuell {actual}) — EN neu übersetzen und "
            f"source-hash aktualisieren"
        )
    return errors


def main() -> int:
    cur_major, cur_minor, _cur_patch = current_version()
    errors: list[str] = []
    for rel in TRANSLATED_FILES:
        path = ROOT / rel
        if not path.exists():
            errors.append(f"{rel}: file missing")
            continue
        text = path.read_text()
        if not SOURCE_RE.search(text):
            errors.append(f"{rel}: missing translation-source header")
        else:
            errors.extend(check_source_hash(rel, text))
        version = parse_marker(text)
        if version is None:
            errors.append(f"{rel}: missing translation-version header")
            continue
        major, minor, _patch = version
        if (major, minor) > (cur_major, cur_minor):
            errors.append(
                f"{rel}: translation-version v{major}.{minor} ahead of pyproject.toml v{cur_major}.{cur_minor}"
            )
            continue
        if major != cur_major:
            errors.append(f"{rel}: translation-version major v{major} != current v{cur_major}")
            continue
        behind = cur_minor - minor
        if behind > MAX_MINOR_BEHIND:
            errors.append(
                f"{rel}: translation-version v{major}.{minor} is "
                f"{behind} minor releases behind v{cur_major}.{cur_minor} "
                f"(max {MAX_MINOR_BEHIND})"
            )

    if errors:
        print("Translation-version drift detected:", file=sys.stderr)
        for line in errors:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(
        f"All {len(TRANSLATED_FILES)} translations within "
        f"{MAX_MINOR_BEHIND} minor releases of v{cur_major}.{cur_minor}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
