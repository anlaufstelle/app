"""Grundausstattung einer Einrichtung im Produktionspfad (Refs #1883, #1970).

Eine frisch angelegte Einrichtung hat **null Dokumentationstypen**. Ohne
mindestens einen Typ mit ``entity_type: event`` bleibt die Typ-Auswahl in
``EventCreateView`` leer — es laesst sich kein einziger Kontakt dokumentieren.
Dieses Modul ist der eine Einstieg, der das behebt; ``setup_facility`` und der
Laufzeit-Anlagepfad im Django-Admin (``core/admin/organization.py``) rufen
beide :func:`ensure_facility_baseline` auf, statt einen zweiten Weg mit
abweichendem Ergebnis zu bauen (genau die Falle aus Refs #1884). Ein
Architektur-Guard (``test_architecture_guards_models.py``) pinnt das.

**Drei Bestaende, zwei Mechanismen (Refs #1970).** Dokumentationstypen und
Feldvorlagen kommen aus dem mitgelieferten Paket ``core/packages/default.yaml``
— das ist der einzige Bestand, dessen Fehlen die Einrichtung **blockiert**.
Schichtfenster (``TimeFilter``) und Schnellvorlagen (``QuickTemplate``) sind
Komfort und stehen als schmale Deklaration in diesem Modul: das Paketformat v1
kennt ausschliesslich ``schema_version``, ``field_templates`` und
``document_types`` (``_PACKAGE_KEYS`` in ``core/packages/schema.py``, jeder
unbekannte Schluessel ist ein harter Validierungsfehler) — die beiden sind dort
gar nicht ausdrueckbar. Eine Schema-Erweiterung dafuer haette Parser, Loader,
Report, ``--dry-run`` und die Paket-Doku mitgezogen; fuer zwei Komfort-Objekte
ohne Fremdpaket-Bedarf ist das zu viel Apparat.

Der Startbestand ist bewusst **neu gefasst** und nicht aus ``core/seed/``
gehoben: die Seed-Schnellvorlagen sind Demo-Inhalte einer fiktiven Einrichtung
und suchen ihre Dokumentationstypen ueber den **Namen** statt den Slug — ein im
Admin umbenannter Typ haengt sie still ab. Hier laeuft der Lookup ueber den
Slug (je Einrichtung eindeutig, im Admin nicht editierbar).

**Nur beim Anlegen.** Der Django-Admin zieht den Einstieg ausschliesslich bei
``change=False`` (``FacilityAdmin.save_model``). Die Leer-Pruefungen unten
arbeiten je Gruppe und koennen „noch nie ausgestattet" nicht von „bewusst leer
gehalten" unterscheiden — an einer bestehenden Einrichtung waere jedes
Speichern deshalb ein stilles Nachziehen: eine per ``--no-default-package``
katalogfrei angelegte Einrichtung bekaeme beim Umbenennen den Katalog,
geloeschte Standardschichten kaemen zurueck. ``setup_facility`` ruft den
Einstieg dagegen bei jedem Lauf — ein Wiederholungslauf ist eine ausdrueckliche
Operator-Handlung mit einer Ausgabe je Bestand.

**Ausstatten, nicht nachziehen.** Der Einstieg heisst
:func:`ensure_default_package` und nicht ``apply_…``, weil er genau eine Frage
beantwortet: „hat diese Einrichtung schon einen Katalog?" — wenn ja, fasst er
sie nicht an. Der Loader ist deklarativ und schreibt bei jedem Lauf die
Paket-Werte auf Bestandszeilen zurueck (``name``, ``sort_order``, ``color``,
``icon``, ``sensitivity``, ``min_contact_stage``; unveraenderlich sind nur
``slug``/``entity_type``/``system_type``, siehe ``core/packages/loader.py``).
Fuer ``manage.py load_package`` ist das die dokumentierte Ruecksync-Semantik,
die der Betreiber bewusst ausloest und mit ``--dry-run`` vorher sehen kann.
Ein Bootstrap-Aufrufer loest sie **nicht** bewusst aus: ``setup_facility``
laesst sich wiederholen (alle Anlagen sind ``get_or_create``), und ein
Wiederholungslauf wuerde sonst im Admin gepflegte Katalog-Eigenschaften still
auf den Auslieferungsstand zuruecksetzen. Wer den Auslieferungsstand
tatsaechlich nachziehen will, nimmt ``manage.py load_package --default``.

**Rollen-Voraussetzung.** Der Loader schreibt in fuenf FORCE-RLS-Tabellen
(``core_documenttype``, ``core_fieldtemplate``, ``core_documenttypefield``,
``core_fieldgroup``, ``core_fieldcondition``), der Rest der Grundausstattung in
zwei weitere (``core_quicktemplate``, ``core_timefilter``). Aus einem
HTTP-Request heraus setzt die ``FacilityScopeMiddleware`` den noetigen
Kontext; ein CLI-Aufrufer hat ihn nicht und braucht die BYPASSRLS-Admin-Rolle.
Die Fail-Loud-Pruefung (:func:`core.services.system.has_rls_bypass_context`)
liegt bewusst beim CLI-Aufrufer und nicht hier — im Request-Pfad waere sie
falsch. Der Admin-Pfad schreibt in eine **andere** Einrichtung als die des
Requests; gedeckt ist das ueber ``app.is_super_admin`` (Bypass-Zweig aller
``facility_isolation``-Policies seit Migration 0085/0091), und ``FacilityAdmin``
ist super_admin-only (Refs #1375).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any

from django.db import transaction

from core.models.document_type import DocumentType
from core.models.field_template import FieldTemplate
from core.models.organization import Facility
from core.models.quick_template import QuickTemplate
from core.models.time_filter import TimeFilter
from core.packages.default_catalog import parse_default_package
from core.packages.loader import PackageReport, load_package
from core.services.dashboard import filter_prefilled_data


@dataclass(frozen=True)
class TimeFilterDef:
    """Ein Schichtfenster des Startbestands. ``sort_order`` folgt der Deklaration."""

    label: str
    start_time: time
    end_time: time
    is_default: bool = False


@dataclass(frozen=True)
class QuickTemplateDef:
    """Eine Schnellvorlage des Startbestands, an einem Katalog-**Slug** haengend."""

    document_type_slug: str
    name: str
    prefilled_data: dict[str, Any] = field(default_factory=dict)


# Drei Standardschichten mit Mitternachts-Ueberlappung im Nachtdienst
# (``TimeFilter.covers_time`` traegt den Fall). Genau EINE Vorauswahl:
# ``current_tally_context`` faellt ausserhalb jeder Schicht auf den vollen Tag
# zurueck, die ``is_default``-Zeile ist die Vorauswahl der Uebergabe-Ansicht.
DEFAULT_TIME_FILTERS: tuple[TimeFilterDef, ...] = (
    TimeFilterDef("Frühdienst", time(8, 0), time(16, 0), is_default=True),
    TimeFilterDef("Spätdienst", time(16, 0), time(22, 0)),
    TimeFilterDef("Nachtdienst", time(22, 0), time(8, 0)),
)

# Knapper Startbestand an Schnellvorlagen — bewusst NICHT die Demo-Vorlagen aus
# ``core/seed/quick_templates.py`` ("Fruehstueck & Beratung", "Behoerdengang
# Jobcenter", "Team-Info"): das sind Screenshot-Inhalte einer fiktiven
# Einrichtung, kein Produktions-Startbestand. Zwei Vorlagen am meistgenutzten
# Typ zeigen die Mechanik; alles Weitere pflegt die Anwendungsbetreuung selbst.
#
# Beide Werte sind gegen den ausgelieferten Katalog so gewaehlt, dass der
# Sensitivitaets-Whitelist-Filter nichts verwirft: ``dauer`` und ``leistungen``
# erben vom Typ ``kontakt`` die Sensitivitaet NORMAL und sind unverschluesselt,
# ``beratung`` ist ein aktiver Options-Slug von ``leistungen``. Verlassen tut
# sich der Schreibpfad darauf nicht — er filtert (siehe
# :func:`ensure_default_quick_templates`), weil der Katalog auch ein fremder
# sein kann.
DEFAULT_QUICK_TEMPLATES: tuple[QuickTemplateDef, ...] = (
    QuickTemplateDef("kontakt", "Kurzkontakt", {"dauer": 5}),
    QuickTemplateDef("kontakt", "Beratungskontakt", {"dauer": 30, "leistungen": ["beratung"]}),
)


@dataclass(frozen=True)
class FacilityBaseline:
    """Was :func:`ensure_facility_baseline` tatsaechlich angelegt hat.

    Jedes Feld ist ``None``, wenn der jeweilige Bestand schon da war und
    deshalb **unangetastet** blieb — die Unterscheidung, an der die Ausgabe
    von ``setup_facility`` haengt.
    """

    package: PackageReport | None
    quick_templates: list[QuickTemplate] | None
    time_filters: list[TimeFilter] | None


def has_catalog_objects(facility: Facility) -> bool:
    """Traegt die Einrichtung schon Katalog-Objekte?

    Geprueft werden die beiden **Wurzel**-Objekte des Paketformats:
    Dokumentationstypen und Feldvorlagen. Alles Weitere (Feldgruppen,
    Feldzuordnungen, Bedingungen) haengt an ihnen und kann ohne sie nicht
    existieren. Beide tragen im Admin gepflegte Eigenschaften, die ein
    erneuter Paket-Lauf zuruecksetzen wuerde — deshalb genuegt schon eines von
    beiden, um die Einrichtung als ausgestattet zu behandeln.
    """
    # Hausform ``for_facility`` statt ``objects.filter(facility=…)`` (Refs #1834,
    # gepinnt in ``test_architecture_guards_models.py``). Soft-geloeschte Zeilen
    # zaehlen bewusst mit: der Loader wuerde sie ueber den Slug wiederfinden und
    # ueberschreiben, sie sind also genauso schuetzenswerter Bestand.
    return DocumentType.objects.for_facility(facility).exists() or FieldTemplate.objects.for_facility(facility).exists()


def ensure_default_package(facility: Facility) -> PackageReport | None:
    """Stattet ``facility`` mit dem mitgelieferten Standardkatalog aus.

    Gibt den Loader-Report zurueck — oder ``None``, wenn die Einrichtung
    bereits Katalog-Objekte hat und deshalb **unangetastet** bleibt (siehe
    Modul-Docstring: Grundausstattung, kein Katalog-Update). Aufrufer nutzen
    genau diese Unterscheidung fuer ihre Ausgabe.

    Der Erstlauf gegen eine leere Einrichtung legt nur an; ein Loeschen
    verwaister Objekte wie im Seed (``core.seed.doc_types.seed_document_types``
    raeumt Feldgruppen weg, Refs #1710) gibt es hier bewusst nicht — der
    Produktionspfad entfernt nie etwas.

    Fehler werden **nicht** geschluckt: eine
    :class:`~core.packages.schema.PackageValidationError` (kaputter Katalog im
    Repo) bzw. :class:`~core.packages.loader.PackageLoadError` (Konflikt mit
    dem Bestand) schlaegt bis zum Aufrufer durch — eine halb ausgestattete
    Einrichtung waere schlimmer als ein abgebrochener Lauf. Der CLI-Aufrufer
    uebersetzt sie in eine ``CommandError``-Meldung.
    """
    # Der Loader klammert sich selbst atomar; die Klammer hier haelt zusaetzlich
    # Bestandspruefung und Anwenden zusammen, wenn der Aufrufer noch keine
    # Transaktion geoeffnet hat.
    with transaction.atomic():
        if has_catalog_objects(facility):
            return None
        return load_package(facility, parse_default_package())


def ensure_default_time_filters(facility: Facility) -> list[TimeFilter] | None:
    """Legt die drei Standardschichten an — oder ``None``, wenn schon welche da sind.

    Fehlende Zeitfenster **blockieren nichts**: ``build_zeitstrom_state`` liest
    die aktiven Filter als moeglicherweise leeres Tupel,
    ``current_tally_context`` faellt ohne passende Schicht auf den heutigen Tag
    zurueck und ``get_time_range`` ohne Filter auf 00:00–23:59. Was fehlt, ist
    die **Schichtsicht**: Uebergabe-Ansicht, Zeitstrom-Filter und Live-Zaehler
    haben ohne Zeitfenster keinen Zeitraum, auf den sie sich beziehen koennen.

    „Ausstatten, kein Nachziehen" wie beim Katalog (siehe Modul-Docstring):
    schon EIN vorhandenes Zeitfenster heisst, dass die Einrichtung ihre
    Schichten selbst pflegt — dann bleibt sie unangetastet. Sonst wuerde ein
    Wiederholungslauf geloeschte Standardschichten stumm wieder hereinholen.
    """
    if TimeFilter.objects.for_facility(facility).exists():
        return None
    return [
        TimeFilter.objects.create(
            facility=facility,
            label=item.label,
            start_time=item.start_time,
            end_time=item.end_time,
            is_default=item.is_default,
            sort_order=index,
        )
        for index, item in enumerate(DEFAULT_TIME_FILTERS)
    ]


def ensure_default_quick_templates(facility: Facility) -> list[QuickTemplate] | None:
    """Legt den Startbestand an Schnellvorlagen an.

    Gibt ``None`` zurueck, wenn die Einrichtung schon Schnellvorlagen traegt
    (unangetastet), sonst die Liste der angelegten Vorlagen — die **leer** sein
    darf: gibt es keinen Dokumentationstyp mit dem erwarteten Slug, entsteht
    einfach nichts. Das ist kein Fehler, sondern der Normalfall fuer eine
    Einrichtung mit eigenem Katalog.

    Der Lookup laeuft ueber den **Slug**, nicht ueber den Namen. Der Slug ist je
    Einrichtung eindeutig (UniqueConstraint) und im Admin nicht editierbar; der
    Name ist es. Ein ueber den Namen suchender Bootstrap faende nach der ersten
    Umbenennung nichts mehr — genau der Lookup-Fehler, den der Seed noch traegt
    (``core/seed/quick_templates.py``) und den ``core/seed/doc_types.py``
    laengst behoben hat.

    ``prefilled_data`` geht durch denselben Whitelist-Filter wie der etablierte
    Schreibpfad (``QuickTemplateAdmin.save_model`` in
    ``core/admin/documents.py``): der gefundene Typ muss nicht aus der
    Auslieferung stammen — eine Einrichtung mit **eigenem** ``kontakt``-Typ
    ohne eine einzige Vorlage bekommt den Startbestand ebenfalls. Traegt deren
    ``dauer``-Feld eine erhoehte Sensitivitaet oder fehlt der Options-Slug
    ``beratung``, entstuende sonst ein Wert, den ``apply_template`` beim
    Anwenden ohnehin still verwirft — gespeichert, aber wirkungslos.
    """
    if QuickTemplate.objects.for_facility(facility).exists():
        return None
    doc_types = {
        doc_type.slug: doc_type
        for doc_type in DocumentType.objects.for_facility(facility).filter(
            slug__in={item.document_type_slug for item in DEFAULT_QUICK_TEMPLATES}
        )
    }
    created: list[QuickTemplate] = []
    for item in DEFAULT_QUICK_TEMPLATES:
        doc_type = doc_types.get(item.document_type_slug)
        if doc_type is None:
            continue
        created.append(
            QuickTemplate.objects.create(
                facility=facility,
                document_type=doc_type,
                name=item.name,
                prefilled_data=filter_prefilled_data(doc_type, dict(item.prefilled_data)),
                sort_order=len(created),
            )
        )
    return created


def ensure_facility_baseline(facility: Facility, *, with_package: bool = True) -> FacilityBaseline:
    """Der EINE Einstieg fuer die Grundausstattung einer Einrichtung (Refs #1970).

    Beide Laufzeit-Anlagepfade rufen ihn auf — der Django-Admin
    (``core/admin/organization.py``, einziger Anlagepfad an der Oberflaeche;
    dort nur beim Anlegen, siehe Modul-Docstring) und ``manage.py
    setup_facility``. Ein zweiter Weg mit abweichendem Ergebnis war genau die
    Falle aus Refs #1884; ein Architektur-Guard
    (``test_architecture_guards_models.py``) pinnt deshalb, dass jede
    Facility-Anlage hier vorbeikommt.

    ``with_package=False`` ist das Opt-out von ``setup_facility``
    (``--no-default-package``) und laesst **nur** den Dokumentationstyp-Katalog
    weg. Die Schichtfenster haengen an keinem Katalog und entstehen trotzdem;
    Schnellvorlagen haengen an Katalog-Slugs und bleiben ohne Katalog
    zwangslaeufig leer.

    Warum Katalog und Rest zwei Mechanismen sind: ``QuickTemplate`` und
    ``TimeFilter`` sind im Paketformat v1 gar nicht ausdrueckbar — es kennt nur
    ``schema_version``, ``field_templates`` und ``document_types``
    (``core/packages/schema.py``), und jeder unbekannte Schluessel ist dort ein
    harter Validierungsfehler. Ein mitgeliefertes Konfigurationspaket kann die
    beiden also nicht transportieren. Eine Schema-Erweiterung dafuer haette
    Parser, Loader, Report, ``--dry-run`` und die Paket-Doku mitgezogen — fuer
    zwei Komfort-Objekte ohne Fremdpaket-Bedarf zu viel; sie stehen deshalb als
    schmale Deklaration in diesem Modul.

    Fehler werden nicht geschluckt (siehe :func:`ensure_default_package`): die
    Transaktionsklammer nimmt bei einem kaputten Katalog auch Schichtfenster und
    Schnellvorlagen wieder mit, statt eine halb ausgestattete Einrichtung
    zurueckzulassen.
    """
    # Reihenfolge ist bedeutsam: der Katalog zuerst, weil die Schnellvorlagen an
    # seinen Slugs haengen — und weil ein Katalog-Fehler dann abbricht, bevor
    # ueberhaupt etwas anderes entstanden ist.
    with transaction.atomic():
        package = ensure_default_package(facility) if with_package else None
        quick_templates = ensure_default_quick_templates(facility)
        time_filters = ensure_default_time_filters(facility)
    return FacilityBaseline(package=package, quick_templates=quick_templates, time_filters=time_filters)
