"""Standardkatalog-Bootstrap fuer den Produktionspfad (Refs #1883).

Der Katalog-Reader lebt seit diesem Ticket in
:mod:`core.packages.default_catalog` (Produktionscode) statt in
``core/seed/doc_types.py`` (Fixture-Territorium), der einspielende Service in
:mod:`core.services.facility_bootstrap`. Diese Datei pinnt beide Seams — den
Reader und den Service —, weil der Admin-Anlagepfad (#1970) und der
``setup_facility``-Command denselben Einstieg benutzen muessen statt ihn
zweimal zu implementieren.
"""

from __future__ import annotations

import pytest

from core.models import DocumentType, FieldTemplate
from core.packages.default_catalog import load_default_package_data, parse_default_package
from core.services.facility_bootstrap import ensure_default_package


class TestDefaultCatalogReader:
    def test_reader_lives_in_packages_not_in_seed(self):
        """Der Produktionspfad darf nicht aus ``core/seed/`` importieren."""
        assert load_default_package_data.__module__ == "core.packages.default_catalog"

    def test_returns_a_copy_so_callers_cannot_poison_the_cache(self):
        first = load_default_package_data()
        first["document_types"] = []
        assert load_default_package_data()["document_types"]

    def test_parsed_package_offers_at_least_one_event_type(self):
        """Ohne ``entity_type: event`` bliebe die Typ-Auswahl beim Dokumentieren leer."""
        package = parse_default_package()
        assert any(doc.entity_type == "event" for doc in package.document_types)

    def test_seed_module_reexports_the_same_callable(self):
        """Bestands-Importe aus ``core.seed.doc_types`` bleiben gueltig (ein Objekt, kein Klon)."""
        from core.seed import doc_types

        assert doc_types.load_default_package_data is load_default_package_data


@pytest.mark.django_db
class TestEnsureDefaultPackage:
    def test_creates_document_types_and_field_templates(self, facility):
        report = ensure_default_package(facility)

        assert report is not None
        assert DocumentType.objects.filter(facility=facility, entity_type="event").exists()
        assert FieldTemplate.objects.filter(facility=facility).exists()
        assert report.document_types.created, "Erstlauf muss Dokumentationstypen anlegen"
        assert not report.errors

    def test_second_call_is_a_no_op_and_says_so(self, facility):
        """Grundausstattung heisst „ausstatten, falls leer" — nicht „Katalog nachziehen".

        Der Rueckgabewert ``None`` ist die Unterscheidung, an der der Aufrufer
        haengt: ``setup_facility`` meldet damit „Katalog unangetastet" statt
        „eingespielt". Ein blosser Zeilen-Zaehler-Vergleich waere auch ohne
        diese Zusicherung gruen gewesen (der Loader-Upsert legt beim zweiten
        Lauf ebenfalls nichts Neues an) — er haette den stillen Rueckschreiber
        auf ``name``/``sort_order`` genau nicht gefunden.
        """
        ensure_default_package(facility)
        doc_type = DocumentType.objects.get(facility=facility, slug="kontakt")
        doc_type.name = "Beratungskontakt"
        doc_type.sort_order = 99
        doc_type.save()
        template = FieldTemplate.objects.filter(facility=facility).first()
        assert template is not None
        template.name = "Eigene Beschriftung"
        template.save()

        assert ensure_default_package(facility) is None

        doc_type.refresh_from_db()
        template.refresh_from_db()
        assert doc_type.name == "Beratungskontakt"
        assert doc_type.sort_order == 99
        assert template.name == "Eigene Beschriftung"

    def test_field_templates_alone_already_count_as_provisioned(self, facility):
        """Auch ohne Dokumentationstyp gilt eine Einrichtung mit Feldvorlagen als ausgestattet.

        Feldvorlagen tragen ebenfalls im Admin gepflegte Werte (Name, Hilfetext,
        Auswahloptionen), die der Loader zurueckschreiben wuerde.
        """
        FieldTemplate.objects.create(
            facility=facility,
            slug="dauer",
            name="Eigene Beschriftung",
            field_type=FieldTemplate.FieldType.NUMBER,
        )

        assert ensure_default_package(facility) is None

        assert FieldTemplate.objects.get(facility=facility, slug="dauer").name == "Eigene Beschriftung"
        assert not DocumentType.objects.filter(facility=facility).exists()

    def test_a_second_facility_gets_its_own_catalog(self, facility, organization):
        """Die Bestands-Pruefung ist einrichtungs-, nicht installationsweit."""
        from core.models import Facility

        ensure_default_package(facility)
        other = Facility.objects.create(organization=organization, name="Zweitstelle")

        report = ensure_default_package(other)

        assert report is not None
        assert DocumentType.objects.filter(facility=other, entity_type="event").exists()
