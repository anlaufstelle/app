"""Lesezugriff auf den mitgelieferten Standardkatalog (Refs #1883).

``core/packages/default.yaml`` ist der Katalog, mit dem eine frisch angelegte
Einrichtung ueberhaupt erst dokumentierbar wird (mindestens ein
Dokumentationstyp mit ``entity_type: event``; ohne ihn bleibt die Typ-Auswahl
in ``EventCreateView`` leer). Der Reader lag bis #1883 in
``core/seed/doc_types.py`` — und damit in dem Paket, das die Architektur-Guards
ausdruecklich als Test-/Fixture-Territorium behandeln
(``test_architecture_guards_models.py``, ``test_architecture_guards_services.py``).
Der Produktionspfad (``setup_facility``, Laufzeit-Anlage im Admin) darf da
nicht hineingreifen, also wohnt der Reader jetzt neben dem Paket, das er liest.

``core/seed/doc_types.py`` importiert die Funktion von hier zurueck — es gibt
weiterhin genau EINE Implementierung und genau EINEN Cache.

Bewusst nur Lesen und Parsen: das Anwenden auf eine Einrichtung liegt in
:mod:`core.services.facility_bootstrap` (Produktionspfad) bzw. im
``load_package``-Command (Operator-CLI).
"""

from __future__ import annotations

import copy
import importlib.resources
from typing import Any

from core.packages.schema import PackageV1, parse_package
from core.packages.yaml_io import safe_load_unique

# Paket-Ressource neben dem Loader (``core/packages/default.yaml``) — ueber
# ``importlib.resources`` adressiert, damit der Pfad unabhaengig vom
# Arbeitsverzeichnis und vom Deploy-Layout (Container: ``COPY src/ ./``)
# aufloesbar bleibt.
_PACKAGE_ANCHOR = "core.packages"
_PACKAGE_RESOURCE = "default.yaml"

#: Repo-relativer Pfad des Katalogs — fuer Meldungen/Doku, nicht zum Oeffnen.
DEFAULT_PACKAGE_PATH = "core/packages/default.yaml"

# Modulweiter Cache: die Datei wird einmal je Prozess gelesen und geparst,
# auch wenn der Aufrufer ueber mehrere Einrichtungen laeuft. Bewusst LAZY (kein
# Import-Zeit-Dateizugriff — sonst braeche der Import in Umgebungen ohne den
# Paketbaum, siehe Import-Zeit-Guard in den Architektur-Tests).
_package_data_cache: dict[str, Any] | None = None


def load_default_package_data() -> dict[str, Any]:
    """Rohdaten des Standardpakets aus ``core/packages/default.yaml``.

    Liefert eine **Kopie** des modulweiten Caches: Aufrufer (insbesondere
    Tests, die Katalog-Varianten bauen) duerfen das Ergebnis gefahrlos
    veraendern, ohne den Cache zu vergiften.
    """
    global _package_data_cache
    if _package_data_cache is None:
        text = importlib.resources.files(_PACKAGE_ANCHOR).joinpath(_PACKAGE_RESOURCE).read_text(encoding="utf-8")
        # Haertung Refs #1727: auch der Standardkatalog laeuft ueber den Duplikat-
        # Key-strikten Loader — ein Copy-Paste-Fehler im Repo faellt so auf.
        data = safe_load_unique(text)
        if not isinstance(data, dict):
            raise ValueError(f"{_PACKAGE_ANCHOR}/{_PACKAGE_RESOURCE} enthaelt kein Mapping auf oberster Ebene.")
        _package_data_cache = data
    return copy.deepcopy(_package_data_cache)


def parse_default_package() -> PackageV1:
    """Standardkatalog gelesen **und** schema-validiert.

    Eine :class:`~core.packages.schema.PackageValidationError` bedeutet hier
    immer einen Fehler im ausgelieferten Repo-Katalog, nie eine Operator-
    Eingabe — sie schlaegt deshalb ungefiltert bis zum Aufrufer durch.
    """
    return parse_package(load_default_package_data())
