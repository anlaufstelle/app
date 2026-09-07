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

from typing import Any

from core.packages.catalog import load_package_data, parse_catalog_package
from core.packages.schema import PackageV1

#: Repo-relativer Pfad des Katalogs — fuer Meldungen/Doku, nicht zum Oeffnen.
DEFAULT_PACKAGE_PATH = "core/packages/default.yaml"


def load_default_package_data() -> dict[str, Any]:
    """Rohdaten des Standardpakets aus ``core/packages/default.yaml``.

    Liefert eine **Kopie** des modulweiten Caches: Aufrufer (insbesondere
    Tests, die Katalog-Varianten bauen) duerfen das Ergebnis gefahrlos
    veraendern, ohne den Cache zu vergiften.
    """
    return load_package_data("default")


def parse_default_package() -> PackageV1:
    """Standardkatalog gelesen **und** schema-validiert.

    Eine :class:`~core.packages.schema.PackageValidationError` bedeutet hier
    immer einen Fehler im ausgelieferten Repo-Katalog, nie eine Operator-
    Eingabe — sie schlaegt deshalb ungefiltert bis zum Aufrufer durch.
    """
    return parse_catalog_package("default")
