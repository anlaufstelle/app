"""Standard-Dokumentationstypen einer Einrichtung einspielen.

M2 P12 (Refs #1058): Der Katalog steht nicht mehr als Python-Dict in dieser
Datei, sondern deklarativ in :file:`src/core/packages/default.yaml` und wird
über den Paket-Loader (:func:`core.packages.loader.load_package`) angewendet.
Der Seed ist damit der erste echte Nutzer des M2-Paketformats.

Was sich dadurch **verhält**:

* **Properties sind update-sicher.** Der alte Katalog lief über
  ``DocumentType.objects.get_or_create(facility, name)`` — Defaults griffen
  nur beim Anlegen, ein per Admin verstelltes Icon/``sort_order``/``sensitivity``
  blieb für immer stehen. Der Loader diffed und schreibt zurück.
* **Lookup-Key ist der Slug, nicht der Name.** Eine Bestands-Einrichtung
  findet ihre Typen über den (in Migration 0117 backgefüllten) Slug wieder;
  ein per UI umbenannter Typ wird aktualisiert statt dupliziert — sein Name
  wird dabei auf den Katalogwert zurückgesetzt.
* **Feldvorlagen sind dedupliziert.** ``dauer``/``notiz`` hingen früher
  mehrfach inline im Katalog und wurden per ``update_or_create``
  übereinandergeschrieben (Last-Write-Wins); jetzt stehen sie einmal unter
  ``field_templates`` und werden mehrfach referenziert.

Seit Refs #1883 liegt das reine Lesen/Parsen des Katalogs in
:mod:`core.packages.default_catalog` (Produktionspfad); hier bleibt der
Seed-spezifische Teil.

Was der Loader **nicht** kann und deshalb hier bleibt: das Aufräumen
verwaister Feldgruppen (#1710). Der Loader ist bewusst additiv — er entfernt
nie etwas, das ein Paket nicht mehr erwähnt. Siehe
:func:`_delete_orphan_field_groups`.
"""

from __future__ import annotations

from django.core.management.base import CommandError
from django.db import transaction

from core.models import DocumentType, Facility, FieldGroup

# Refs #1883: Der Katalog-Reader ist Produktionscode und lebt deshalb neben dem
# Paket, das er liest (``core/packages/default_catalog.py``) — ``core/seed/``
# ist Fixture-Territorium, aus dem der Produktionspfad nicht importieren darf.
# Hier bleibt der Bestands-Name als Re-Export stehen: Seed und Tests behalten
# ihren Import, und es gibt weiterhin genau EINE Implementierung samt Cache.
from core.packages.catalog import parse_catalog_package
from core.packages.default_catalog import load_default_package_data
from core.packages.loader import PackageReport, load_package
from core.packages.schema import PackageV1
from core.services.facility_bootstrap import foreign_catalog_slugs

__all__ = ["load_default_package_data", "seed_document_types"]


def seed_document_types(facility: Facility, *, package: str = "default") -> PackageReport:
    """Spielt ``package`` in ``facility`` ein (idempotent).

    Refs #2148 (R-L5c-1): generalisiert vom reinen Standardkatalog auf jedes
    mitgelieferte Paket — läuft für JEDEN Paketnamen (inkl. ``"default"``)
    ueber denselben Katalog-Reader (:func:`~core.packages.catalog.parse_catalog_package`),
    den auch ``setup_facility``/``load_package`` nutzen (MITTEL A:
    kein Sonderpfad mehr ueber :mod:`core.packages.default_catalog`). Ein
    unbekannter Paketname (z. B. ein Domaenen-Paket, das noch nicht im Katalog
    liegt) schlaegt **fail-loud** als
    :class:`~core.packages.catalog.UnknownPackageError` durch — kein stiller
    Fallback auf den Standardkatalog.

    Refs #2148 (R-L5c-6, HOCH A): „Ersatz, kein Zusatz" gilt auch
    im Seed. Ein Domaenen-Paket (``package != "default"``) darf nur gegen eine
    Einrichtung OHNE fremden Katalog laufen — sonst blieben Default-Reste
    (Typen/Felder, die das neue Paket nicht kennt) neben den neuen Paket-Typen
    stehen. Die Pruefung spiegelt ``load_package.py`` 1:1 (``foreign_catalog_slugs``);
    ein Treffer bricht **vor** ``load_package()`` mit ``CommandError`` ab —
    nichts wird halb geladen. ``package="default"`` bleibt bewusst die
    dokumentierte Rücksync-Ausnahme (wie beim CLI-Pfad): wer den
    Auslieferungsstand zurückholen will, darf das weiterhin auf einem
    beliebigen Bestand tun.

    Fehler werden **nicht** geschluckt: eine
    :class:`~core.packages.schema.PackageValidationError` (kaputtes YAML im
    Repo) bzw. :class:`~core.packages.loader.PackageLoadError` (Konflikt mit
    dem Bestand) schlägt bis zum Aufrufer durch — ein halb eingespielter
    Katalog wäre schlimmer als ein abgebrochener Seed-Lauf.
    """
    parsed_package = parse_catalog_package(package)
    if package != "default":
        foreign_slugs = foreign_catalog_slugs(facility, parsed_package)
        if foreign_slugs:
            rendered = ", ".join(sorted(foreign_slugs))
            raise CommandError(
                f"Einrichtung „{facility.name}“ trägt bereits einen anderen Katalog "
                f"(fremde Slugs: {rendered}) — Domänen-Pakete sind Ersatz, kein Zusatz. "
                "`make seed --flush` verwenden."
            )
    # Cleanup + Anwendung in EINER Transaktion: ohne die Klammer bliebe eine
    # gelöschte Gruppe gelöscht, wenn der Loader danach zurückrollt.
    with transaction.atomic():
        _delete_orphan_field_groups(facility, parsed_package)
        return load_package(facility, parsed_package)


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
