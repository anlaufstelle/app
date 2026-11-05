"""Inventory of the manually vendored JavaScript libraries.

SortableJS and axe-core are vendored by hand: the minified build lies under
``src/static/js/vendor/`` and there is no ``package.json`` entry, so neither
``npm audit`` nor the lock-file based SBOM sees them. ``vendor_js_inventory.json``
next to this module is the one machine-readable list of these libraries (npm
package name, version, shipped path). The license guard, the advisory gate and
the release SBOM all read it; nobody keeps a second version list.

The version in the inventory is only trusted after it was compared with the
banner of the shipped file: a replaced build without an inventory update is a
drift, not a silent mismatch between what is shipped and what is audited.

Stdlib only; the module is also loaded from a built release tree.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

INVENTORY_RELPATH = Path("scripts") / "vendor_js_inventory.json"
# The banner sits in the leading license comment; reading the head is enough.
BANNER_BYTES = 2000
REQUIRED_FIELDS = ("package", "table_name", "version", "path", "version_pattern", "license_marker", "scope")
# CycloneDX scope in the release SBOM: ``required`` = loaded by production pages,
# ``optional`` = shipped with the static files but only used by the test suite.
SCOPES = ("required", "optional")
EXACT_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


class InventoryError(Exception):
    """The inventory is missing, unreadable or does not match the shipped files."""


@dataclass(frozen=True)
class VendoredLibrary:
    package: str
    table_name: str
    version: str
    path: str
    version_pattern: str
    license_marker: str
    scope: str

    @property
    def purl(self) -> str:
        return f"pkg:npm/{quote(self.package, safe='/')}@{self.version}"


def load_inventory(root: Path) -> list[VendoredLibrary]:
    """Parse the inventory below ``root``; raise ``InventoryError`` on any defect."""
    path = root / INVENTORY_RELPATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InventoryError(f"{INVENTORY_RELPATH}: file missing") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"{INVENTORY_RELPATH}: unreadable ({exc})") from exc
    entries = data.get("libraries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise InventoryError(f"{INVENTORY_RELPATH}: no libraries listed")

    libraries: list[VendoredLibrary] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise InventoryError(f"{INVENTORY_RELPATH}: entry {index} is not an object")
        missing = [f for f in REQUIRED_FIELDS if not isinstance(entry.get(f), str)]
        missing += [f for f in REQUIRED_FIELDS if f not in missing and f != "license_marker" and not entry[f].strip()]
        if missing:
            raise InventoryError(f"{INVENTORY_RELPATH}: entry {index} lacks {', '.join(missing)}")
        if entry["scope"] not in SCOPES:
            raise InventoryError(f"{entry['package']}: scope {entry['scope']!r} is not one of {', '.join(SCOPES)}")
        if not EXACT_VERSION_RE.fullmatch(entry["version"]):
            raise InventoryError(f"{entry['package']}: version {entry['version']!r} is not an exact x.y.z version")
        try:
            pattern = re.compile(entry["version_pattern"])
        except re.error as exc:
            raise InventoryError(f"{entry['package']}: invalid version_pattern ({exc})") from exc
        if pattern.groups != 1:
            raise InventoryError(f"{entry['package']}: version_pattern needs exactly one capture group")
        libraries.append(VendoredLibrary(**{f: entry[f] for f in REQUIRED_FIELDS}))

    names = [lib.package for lib in libraries]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise InventoryError(f"{INVENTORY_RELPATH}: duplicate packages {', '.join(duplicates)}")
    return libraries


def shipped_version(root: Path, library: VendoredLibrary) -> str | None:
    """Version from the banner of the shipped file, ``None`` if file or banner is missing.

    Raises ``InventoryError`` if the file cannot be read as UTF-8.
    """
    path = root / library.path
    if not path.is_file():
        return None
    try:
        head = path.read_text(encoding="utf-8")[:BANNER_BYTES]
    except (OSError, UnicodeDecodeError) as exc:
        raise InventoryError(f"{library.package}: {library.path} is not readable as UTF-8 ({exc})") from exc
    match = re.search(library.version_pattern, head)
    return match.group(1) if match else None


def drift_errors(root: Path, libraries: list[VendoredLibrary]) -> list[str]:
    """One message per library whose shipped banner does not carry the inventory version."""
    errors = []
    for lib in libraries:
        try:
            found = shipped_version(root, lib)
        except InventoryError as exc:
            errors.append(str(exc))
            continue
        if not (root / lib.path).is_file():
            errors.append(f"{lib.package}: shipped file missing: {lib.path}")
        elif found is None:
            errors.append(f"{lib.package}: no version banner in {lib.path}")
        elif found != lib.version:
            errors.append(
                f"{lib.package}: DRIFT — {INVENTORY_RELPATH} lists v{lib.version}, but {lib.path} ships v{found}"
            )
    return errors


def load_verified_inventory(root: Path) -> list[VendoredLibrary]:
    """Inventory whose versions match the shipped files; ``InventoryError`` otherwise."""
    libraries = load_inventory(root)
    errors = drift_errors(root, libraries)
    if errors:
        raise InventoryError("; ".join(errors))
    return libraries
