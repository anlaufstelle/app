"""Standard-Dokumentationstypen einer Einrichtung einspielen.

M2 P12 (Refs #1058): Der Katalog steht nicht mehr als Python-Dict in dieser
Datei, sondern deklarativ in :file:`src/core/packages/default.yaml` und wird
über den Paket-Loader (:func:`core.packages.loader.load_package`) angewendet.
Der Seed ist damit der erste echte Nutzer des M2-Paketformats.

Was sich dadurch **verhält**:

* **Properties sind update-sicher.** Der alte Katalog lief über
  ``DocumentType.objects.get_or_create(facility, name)`` — Defaults griffen
  nur beim Anlegen, ein per Admin verstelltes Icon/``sort_order``/``category``
  blieb für immer stehen. Der Loader diffed und schreibt zurück.
* **Lookup-Key ist der Slug, nicht der Name.** Eine Bestands-Einrichtung
  findet ihre Typen über den (in Migration 0117 backgefüllten) Slug wieder;
  ein per UI umbenannter Typ wird aktualisiert statt dupliziert — sein Name
  wird dabei auf den Katalogwert zurückgesetzt.
* **Feldvorlagen sind dedupliziert.** ``dauer``/``notiz`` hingen früher
  mehrfach inline im Katalog und wurden per ``update_or_create``
  übereinandergeschrieben (Last-Write-Wins); jetzt stehen sie einmal unter
  ``field_templates`` und werden mehrfach referenziert.

Was der Loader **nicht** kann und deshalb hier bleibt: das Aufräumen
verwaister Feldgruppen (#1710). Der Loader ist bewusst additiv — er entfernt
nie etwas, das ein Paket nicht mehr erwähnt. Siehe
:func:`_delete_orphan_field_groups`.
"""

from __future__ import annotations

import copy
import importlib.resources
from typing import Any

import yaml
from django.db import transaction

from core.models import DocumentType, Facility, FieldGroup
from core.packages.loader import PackageReport, load_package
from core.packages.schema import PackageV1, parse_package

# Paket-Ressource neben dem Loader (``core/packages/default.yaml``) — über
# ``importlib.resources`` adressiert, damit der Pfad unabhängig vom
# Arbeitsverzeichnis und vom Deploy-Layout (Container: ``COPY src/ ./``)
# auflösbar bleibt.
_PACKAGE_ANCHOR = "core.packages"
_PACKAGE_RESOURCE = "default.yaml"

# Modulweiter Cache: die Datei wird einmal je Prozess gelesen und geparst,
# auch wenn der Seed über mehrere Einrichtungen läuft. Bewusst LAZY (kein
# Import-Zeit-Dateizugriff — sonst bräche der Import in Umgebungen ohne den
# Paketbaum, siehe Import-Zeit-Guard in den Architektur-Tests).
_package_data_cache: dict[str, Any] | None = None


def load_default_package_data() -> dict[str, Any]:
    """Rohdaten des Standardpakets aus ``core/packages/default.yaml``.

    Liefert eine **Kopie** des modulweiten Caches: Aufrufer (insbesondere
    Tests, die Katalog-Varianten bauen) dürfen das Ergebnis gefahrlos
    verändern, ohne den Cache zu vergiften.
    """
    global _package_data_cache
    if _package_data_cache is None:
        text = importlib.resources.files(_PACKAGE_ANCHOR).joinpath(_PACKAGE_RESOURCE).read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f"{_PACKAGE_ANCHOR}/{_PACKAGE_RESOURCE} enthält kein Mapping auf oberster Ebene.")
        _package_data_cache = data
    return copy.deepcopy(_package_data_cache)


def seed_document_types(facility: Facility) -> PackageReport:
    """Spielt das Standardpaket in ``facility`` ein (idempotent).

    Fehler werden **nicht** geschluckt: eine
    :class:`~core.packages.schema.PackageValidationError` (kaputtes YAML im
    Repo) bzw. :class:`~core.packages.loader.PackageLoadError` (Konflikt mit
    dem Bestand) schlägt bis zum Aufrufer durch — ein halb eingespielter
    Katalog wäre schlimmer als ein abgebrochener Seed-Lauf.
    """
    package = parse_package(load_default_package_data())
    # Cleanup + Anwendung in EINER Transaktion: ohne die Klammer bliebe eine
    # gelöschte Gruppe gelöscht, wenn der Loader danach zurückrollt.
    with transaction.atomic():
        _delete_orphan_field_groups(facility, package)
        return load_package(facility, package)


def _delete_orphan_field_groups(facility: Facility, package: PackageV1) -> None:
    """Feldgruppen entfernen, die der Katalog nicht mehr kennt (Refs #1710).

    Der Paket-Loader ist additiv und lässt Gruppen stehen, die im Paket
    fehlen. Für den Seed ist das zu wenig: erst dieser Pass macht den
    Übergang von einem Alt-Slug auf den Katalog-Slug in EINEM Lauf möglich
    (Bestandszeile mit abweichendem Auto-Slug wird gelöscht, die
    Katalog-Slug-Zeile im selben Lauf angelegt) und lässt eine aus dem
    Katalog entfernte Gruppe wirklich verschwinden.

    **Opt-in je Dokumentationstyp:** aufgeräumt wird nur bei Typen, für die
    das Paket mindestens eine Gruppe deklariert — dieser Typ „gehört" dem
    Paket. Typen ohne ``groups`` (z. B. Spritzentausch) bleiben unangetastet,
    inklusive der Gruppen, die eine Einrichtung dort per Admin/Config-UI
    angelegt hat. Das entspricht der #1710-Semantik („nur Typen mit
    ``groups``-Schlüssel nehmen am Cleanup teil"); einzige Verengung: ein
    Paket kann nicht mehr ausdrücken „lösche ALLE Gruppen dieses Typs" (das
    Schema kennt für ``groups`` kein Tri-State wie für ``conditions``).

    Felder der gelöschten Gruppe fallen per ``SET_NULL`` auf „ohne Gruppe"
    zurück; die im Paket zugeordneten bekommen ihre Zielgruppe unmittelbar
    danach vom Loader gesetzt.
    """
    for doc_def in package.document_types:
        if not doc_def.groups:
            continue
        doc_type = DocumentType.objects.filter(facility=facility, slug=doc_def.slug).first()
        if doc_type is None:
            continue
        catalog_slugs = [group.slug for group in doc_def.groups]
        FieldGroup.objects.filter(document_type=doc_type).exclude(slug__in=catalog_slugs).delete()
