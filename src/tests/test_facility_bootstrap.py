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


@pytest.mark.django_db
class TestForeignCatalogSlugs:
    def test_reports_inactive_document_types_and_field_templates_outside_package(self, facility):
        from core.packages.default_catalog import parse_default_package
        from core.services.facility_bootstrap import foreign_catalog_slugs

        DocumentType.objects.create(
            facility=facility,
            slug="fremder-typ",
            name="Fremder Typ",
            is_active=False,
        )
        FieldTemplate.objects.create(
            facility=facility,
            slug="fremdes-feld",
            name="Fremdes Feld",
            is_active=False,
        )

        assert foreign_catalog_slugs(facility, parse_default_package()) == {"fremder-typ", "fremdes-feld"}


@pytest.mark.django_db
class TestEnsureDefaultTimeFilters:
    """Refs #1970: Schichtfenster gehoeren zur Grundausstattung.

    Ohne Zeitfenster **bricht nichts** — ``build_zeitstrom_state`` liest die
    aktiven Filter als moeglicherweise leeres Tupel und
    ``core.services.events.feed.get_time_range`` faellt ohne Filter auf den
    vollen Tag zurueck. Es fehlt die Schichtsicht: Uebergabe, Zeitstrom-Filter
    und der Live-Zaehler haben keinen Zeitraum, auf den sie sich beziehen.
    """

    def test_creates_the_standard_shifts_once(self, facility):
        from core.models import TimeFilter
        from core.services.facility_bootstrap import DEFAULT_TIME_FILTERS, ensure_default_time_filters

        created = ensure_default_time_filters(facility)

        assert created is not None
        labels = list(TimeFilter.objects.for_facility(facility).order_by("sort_order").values_list("label", flat=True))
        assert labels == [item.label for item in DEFAULT_TIME_FILTERS]

    def test_sort_order_follows_the_declaration(self, facility):
        from core.models import TimeFilter
        from core.services.facility_bootstrap import DEFAULT_TIME_FILTERS, ensure_default_time_filters

        ensure_default_time_filters(facility)

        shifts = TimeFilter.objects.for_facility(facility).order_by("sort_order")
        orders = list(shifts.values_list("sort_order", flat=True))
        assert orders == list(range(len(DEFAULT_TIME_FILTERS)))

    def test_facility_with_own_shifts_stays_untouched(self, facility):
        """Ausstatten, kein Nachziehen — wie beim Katalog (Refs #1883)."""
        from datetime import time

        from core.models import TimeFilter
        from core.services.facility_bootstrap import ensure_default_time_filters

        TimeFilter.objects.create(
            facility=facility,
            label="Tagdienst 10-18",
            start_time=time(10, 0),
            end_time=time(18, 0),
        )

        assert ensure_default_time_filters(facility) is None

        assert list(TimeFilter.objects.for_facility(facility).values_list("label", flat=True)) == ["Tagdienst 10-18"]

    def test_a_second_facility_gets_its_own_shifts(self, facility, organization):
        from core.models import Facility, TimeFilter
        from core.services.facility_bootstrap import ensure_default_time_filters

        ensure_default_time_filters(facility)
        other = Facility.objects.create(organization=organization, name="Zweitstelle")

        assert ensure_default_time_filters(other) is not None
        assert TimeFilter.objects.for_facility(other).exists()


@pytest.mark.django_db
class TestEnsureDefaultQuickTemplates:
    """Refs #1970: knapper Produktions-Startbestand statt der Demo-Vorlagen.

    ``core/seed/quick_templates.py`` liefert Demo-Inhalte und sucht seine
    Dokumentationstypen ueber den **Namen** — ein im Admin umbenannter Typ
    faellt dort still durch. Der Produktionspfad matcht ueber den ``slug``.
    """

    def test_templates_hang_on_the_catalog_by_slug(self, facility):
        from core.models import QuickTemplate
        from core.services.facility_bootstrap import (
            DEFAULT_QUICK_TEMPLATES,
            ensure_default_package,
            ensure_default_quick_templates,
        )

        ensure_default_package(facility)
        doc_type = DocumentType.objects.get(facility=facility, slug="kontakt")
        doc_type.name = "Erstkontakt"  # Umbenennung im Admin darf den Bootstrap nicht abhaengen
        doc_type.save()

        created = ensure_default_quick_templates(facility)

        assert created is not None
        assert len(created) == len(DEFAULT_QUICK_TEMPLATES)
        assert QuickTemplate.objects.for_facility(facility).count() == len(DEFAULT_QUICK_TEMPLATES)
        assert all(tpl.document_type_id == doc_type.pk for tpl in created)

    def test_declared_values_survive_the_whitelist_on_the_shipped_catalog(self, facility):
        """Ein Startbestand, den ``filter_prefilled_data`` leerraeumt, waere wertlos.

        Der Schreibpfad filtert (siehe unten) — deshalb reicht ein
        Filter-Aufruf auf dem Ergebnis als Zusicherung **nicht** mehr aus, der
        waere tautologisch gruen. Verglichen wird gegen die **Deklaration**:
        haette der ausgelieferte Katalog ``dauer`` auf ELEVATED gehoben oder
        den Options-Slug ``beratung`` deaktiviert, entstuenden hier leere
        Vorlagen statt der erwarteten Werte.
        """
        from core.services.facility_bootstrap import (
            DEFAULT_QUICK_TEMPLATES,
            ensure_default_package,
            ensure_default_quick_templates,
        )

        ensure_default_package(facility)
        created = ensure_default_quick_templates(facility)

        assert created
        assert {tpl.name: tpl.prefilled_data for tpl in created} == {
            item.name: dict(item.prefilled_data) for item in DEFAULT_QUICK_TEMPLATES
        }

    def test_prefilled_data_is_written_through_the_whitelist_filter(self, facility):
        """Der Startbestand trifft auf einen **fremden** Katalog — der Filter entscheidet.

        Der Bootstrap legt Schnellvorlagen auch dann an, wenn der Katalog nicht
        aus der Auslieferung stammt (Einrichtung mit eigenen Typen, aber ohne
        eine einzige Vorlage). Traegt deren ``kontakt``-Typ ein ``dauer``-Feld
        mit erhoehter Sensitivitaet, waere ``{"dauer": 5}`` ein Wert, den
        ``QuickTemplateAdmin.save_model`` (``core/admin/documents.py``) nie in
        die Datenbank gelassen haette: gespeichert, aber beim Anwenden still
        verworfen. Der Schreibpfad des Bootstraps laeuft deshalb durch
        denselben Filter.
        """
        from core.models import DocumentType, DocumentTypeField, FieldTemplate, QuickTemplate
        from core.services.facility_bootstrap import ensure_default_quick_templates

        doc_type = DocumentType.objects.create(
            facility=facility,
            slug="kontakt",
            name="Kontakt",
            entity_type=DocumentType.EntityType.EVENT,
        )
        dauer = FieldTemplate.objects.create(
            facility=facility,
            slug="dauer",
            name="Dauer",
            field_type=FieldTemplate.FieldType.NUMBER,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=dauer, sort_order=0)

        created = ensure_default_quick_templates(facility)

        assert created, "Die Vorlagen selbst entstehen — nur ihre Werte muessen den Filter passieren"
        for template in created:
            assert template.prefilled_data == {}, "gesperrter Slug darf nicht roh in der DB landen"
        stored = QuickTemplate.objects.for_facility(facility).values_list("prefilled_data", flat=True)
        assert list(stored) == [{} for _ in created]

    def test_without_a_matching_catalog_nothing_is_created(self, facility):
        """Kein Dokumentationstyp mit passendem Slug -> leere Liste, kein Fehler."""
        from core.models import QuickTemplate
        from core.services.facility_bootstrap import ensure_default_quick_templates

        assert ensure_default_quick_templates(facility) == []
        assert not QuickTemplate.objects.for_facility(facility).exists()

    def test_facility_with_own_templates_stays_untouched(self, facility):
        from core.models import QuickTemplate
        from core.services.facility_bootstrap import ensure_default_package, ensure_default_quick_templates

        ensure_default_package(facility)
        doc_type = DocumentType.objects.get(facility=facility, slug="kontakt")
        QuickTemplate.objects.create(facility=facility, document_type=doc_type, name="Eigene Vorlage")

        assert ensure_default_quick_templates(facility) is None

        assert list(QuickTemplate.objects.for_facility(facility).values_list("name", flat=True)) == ["Eigene Vorlage"]


@pytest.mark.django_db
class TestEnsureFacilityBaseline:
    """Refs #1970: EIN Einstieg fuer alle Anlagepfade — Admin wie ``setup_facility``."""

    def test_provisions_catalog_shifts_and_templates(self, facility):
        from core.models import QuickTemplate, TimeFilter
        from core.services.facility_bootstrap import ensure_facility_baseline

        result = ensure_facility_baseline(facility)

        assert result.package == "default"
        assert result.package_report is not None
        assert result.time_filters
        assert result.quick_templates
        assert DocumentType.objects.filter(facility=facility, entity_type="event").exists()
        assert TimeFilter.objects.for_facility(facility).exists()
        assert QuickTemplate.objects.for_facility(facility).exists()

    def test_without_package_the_shifts_still_come(self, facility):
        """``--no-default-package`` laesst den Katalog weg, nicht die Schichtfenster."""
        from core.models import QuickTemplate, TimeFilter
        from core.services.facility_bootstrap import ensure_facility_baseline

        result = ensure_facility_baseline(facility, package=None)

        assert result.package is None
        assert result.package_report is None
        assert not DocumentType.objects.filter(facility=facility).exists()
        assert TimeFilter.objects.for_facility(facility).exists()
        # Ohne Katalog gibt es keinen Typ, an dem eine Vorlage haengen koennte.
        assert result.quick_templates == []
        assert not QuickTemplate.objects.for_facility(facility).exists()

    def test_second_call_reports_an_untouched_facility(self, facility):
        from core.services.facility_bootstrap import ensure_facility_baseline

        ensure_facility_baseline(facility)

        result = ensure_facility_baseline(facility)

        assert result.package == "default"
        assert result.package_report is None
        assert result.quick_templates is None
        assert result.time_filters is None

    def test_unknown_package_fails_before_creating_comfort_objects(self, facility):
        from core.models import TimeFilter
        from core.packages.catalog import UnknownPackageError
        from core.services.facility_bootstrap import ensure_facility_baseline

        with pytest.raises(UnknownPackageError, match="missing"):
            ensure_facility_baseline(facility, package="missing")

        assert not TimeFilter.objects.for_facility(facility).exists()

    def test_missing_comfort_is_not_reported_as_available(self, facility, monkeypatch):
        from core.packages.catalog import UnknownPackageError
        from core.services import facility_bootstrap

        monkeypatch.setattr(facility_bootstrap, "list_packages", lambda: ["default", "orphan"])

        with pytest.raises(UnknownPackageError) as excinfo:
            facility_bootstrap.ensure_facility_baseline(facility, package="orphan")

        assert excinfo.value.available == ["default"]
        assert "Verfügbare Pakete: default." in str(excinfo.value)

    def test_every_shipped_package_has_comfort_definitions(self):
        from core.packages.catalog import list_packages
        from core.services.facility_bootstrap import PACKAGE_COMFORT

        assert set(list_packages()) <= set(PACKAGE_COMFORT)

    def test_package_comfort_definitions_are_kept_separate(self):
        from datetime import time

        from core.services.facility_bootstrap import (
            DEFAULT_QUICK_TEMPLATES,
            DEFAULT_TIME_FILTERS,
            PACKAGE_COMFORT,
            QuickTemplateDef,
        )

        assert [(item.label, item.start_time, item.end_time) for item in PACKAGE_COMFORT["awareness"].time_filters] == [
            ("Abend", time(18, 0), time(0, 0)),
            ("Nacht", time(0, 0), time(6, 0)),
        ]
        assert [
            (item.label, item.start_time, item.end_time) for item in PACKAGE_COMFORT["beratungsstelle"].time_filters
        ] == [
            ("Sprechstunde Vormittag", time(8, 0), time(12, 0)),
            ("Sprechstunde Nachmittag", time(13, 0), time(17, 0)),
            ("Termine", time(17, 0), time(20, 0)),
        ]
        assert PACKAGE_COMFORT["default"].time_filters is DEFAULT_TIME_FILTERS
        assert PACKAGE_COMFORT["default"].quick_templates is DEFAULT_QUICK_TEMPLATES
        assert PACKAGE_COMFORT["frauenhaus"].time_filters is DEFAULT_TIME_FILTERS
        assert [
            (item.label, item.start_time, item.end_time) for item in PACKAGE_COMFORT["jugendarbeit"].time_filters
        ] == [
            ("Nachmittag", time(14, 0), time(18, 0)),
            ("Abend", time(18, 0), time(22, 0)),
        ]
        assert [
            (item.document_type_slug, item.name, item.prefilled_data)
            for item in PACKAGE_COMFORT["awareness"].quick_templates
        ] == [
            ("versorgung", "Wasser gegeben", {"bedarf": ["wasser"]}),
            ("versorgung", "Ruheraum begleitet", {"bedarf": ["ruheraum"]}),
        ]
        assert PACKAGE_COMFORT["beratungsstelle"].quick_templates == (
            QuickTemplateDef("kurzberatung", "Kurzinfo Sprechstunde", {"dauer": 10, "weiterverweis": "keine"}),
            QuickTemplateDef("kontakt", "Formularhilfe", {"leistungen": ["formularhilfe"]}),
            QuickTemplateDef("kurzberatung", "Telefonische Kurzberatung", {"dauer": 15}),
        )
        assert PACKAGE_COMFORT["frauenhaus"].quick_templates == ()
        assert [
            (item.document_type_slug, item.name, item.prefilled_data)
            for item in PACKAGE_COMFORT["jugendarbeit"].quick_templates
        ] == [
            ("kontakt", "Kontakt JZ-Raum", {"leistungen": ["aufenthaltsraum"]}),
            ("veranstaltungsteilnahme", "Veranstaltung (Personenzahl)", {"teilnehmende": 10}),
        ]

    def test_package_quick_templates_skip_unknown_type_slugs(self, facility):
        from core.services.facility_bootstrap import QuickTemplateDef, ensure_quick_templates

        created = ensure_quick_templates(
            facility,
            (QuickTemplateDef("nicht-im-katalog", "Nicht anlegen", {}),),
        )

        assert created == []

    def test_a_broken_catalog_takes_the_whole_baseline_with_it(self, facility, monkeypatch):
        """Fail-loud: keine halb ausgestattete Einrichtung (Muster aus Refs #1883)."""
        from core.models import TimeFilter
        from core.packages.schema import PackageValidationError
        from core.services import facility_bootstrap

        def _boom(_facility, _package):
            raise PackageValidationError([("document_types[0].slug", "kaputt")])

        monkeypatch.setattr(facility_bootstrap, "ensure_catalog_package", _boom)

        with pytest.raises(PackageValidationError):
            facility_bootstrap.ensure_facility_baseline(facility)

        assert not TimeFilter.objects.for_facility(facility).exists()
