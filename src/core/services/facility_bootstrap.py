"""Grundausstattung einer Einrichtung im Produktionspfad (Refs #1883).

Eine frisch angelegte Einrichtung hat **null Dokumentationstypen**. Ohne
mindestens einen Typ mit ``entity_type: event`` bleibt die Typ-Auswahl in
``EventCreateView`` leer — es laesst sich kein einziger Kontakt dokumentieren.
Dieses Modul ist der eine Einstieg, der das behebt; ``setup_facility`` ruft
ihn auf, und der Laufzeit-Anlagepfad im Django-Admin (Refs #1970) soll
denselben Aufruf benutzen statt einen zweiten Weg mit abweichendem Ergebnis
zu bauen (genau die Falle aus Refs #1884).

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
``core_fieldgroup``, ``core_fieldcondition``). Aus einem HTTP-Request heraus
setzt die ``FacilityScopeMiddleware`` den noetigen Einrichtungs-Kontext; ein
CLI-Aufrufer hat ihn nicht und braucht die BYPASSRLS-Admin-Rolle. Die
Fail-Loud-Pruefung (:func:`core.services.system.has_rls_bypass_context`) liegt
bewusst beim CLI-Aufrufer und nicht hier — im Request-Pfad waere sie falsch.
"""

from __future__ import annotations

from django.db import transaction

from core.models.document_type import DocumentType
from core.models.field_template import FieldTemplate
from core.models.organization import Facility
from core.packages.default_catalog import parse_default_package
from core.packages.loader import PackageReport, load_package


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
