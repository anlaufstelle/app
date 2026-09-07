"""Katalog der mitgelieferten Dokumentationspakete (Refs #2134).

Die technisch verfuegbaren Pakete sind die ``*.yaml``-Ressourcen direkt in
``core.packages``. Anzeige-Metadaten bleiben bewusst ausserhalb des strikten
Schemas v1: ein Paket beschreibt weiterhin ausschliesslich Feldvorlagen und
Dokumentationstypen.
"""

from __future__ import annotations

import copy
import importlib.resources
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from typing import Any

from core.packages.schema import PackageV1, parse_package
from core.packages.yaml_io import safe_load_unique

_PACKAGE_ANCHOR = "core.packages"
_package_data_cache: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class PackageInfo:
    """Anzeige-Metadaten eines kuratierten Pakets."""

    display_name: str
    domain: str


PACKAGE_INFO: dict[str, PackageInfo] = {
    "default": PackageInfo("Standardkatalog", "niedrigschwellige Hilfe"),
    "awareness": PackageInfo("Awareness", "Awareness-Teams bei Veranstaltungen"),
    "beratungsstelle": PackageInfo("Beratungsstelle", "offene Sprechstunde / Sozialberatung"),
    "frauenhaus": PackageInfo("Frauenhaus", "Frauenhaus / Gewalt-Fachberatung"),
    "jugendarbeit": PackageInfo("Jugendarbeit", "mobile und offene Jugendarbeit"),
}


class UnknownPackageError(LookupError):
    """Ein Paketname gehoert zu keiner mitgelieferten YAML-Ressource."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = list(available)
        rendered = ", ".join(self.available) if self.available else "–"
        # Meldung vorab bauen: der Guard gegen f-Strings in gettext-Aufrufen (#662)
        # trifft auch einen f-String direkt im Konstruktor-Aufruf; die Meldung ist
        # bewusst unuebersetzt (CLI-Fehlertext).
        message = f"Unbekanntes Paket {name!r}. Verfügbare Pakete: {rendered}."
        super().__init__(message)


def _package_root() -> Traversable:
    """Ressourcen-Anker als schmale, in Tests ersetzbare Naht."""
    return importlib.resources.files(_PACKAGE_ANCHOR)


def list_packages() -> list[str]:
    """Mitgelieferte ``*.yaml``-Pakete, ``default`` immer zuerst."""
    names = [
        item.name.removesuffix(".yaml")
        for item in _package_root().iterdir()
        if item.is_file() and item.name.endswith(".yaml")
    ]
    return sorted(names, key=lambda name: (name != "default", name))


def load_package_data(name: str) -> dict[str, Any]:
    """Rohdaten eines Katalogpakets als tiefe Kopie des Name-Caches."""
    available = list_packages()
    if name not in available:
        raise UnknownPackageError(name, available)
    if name not in _package_data_cache:
        resource = _package_root().joinpath(f"{name}.yaml")
        data = safe_load_unique(resource.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{_PACKAGE_ANCHOR}/{name}.yaml enthaelt kein Mapping auf oberster Ebene.")
        _package_data_cache[name] = data
    return copy.deepcopy(_package_data_cache[name])


def parse_catalog_package(name: str) -> PackageV1:
    """Mitgeliefertes Paket lesen und strikt gegen Schema v1 validieren."""
    return parse_package(load_package_data(name))
