"""``report_key`` im Paketformat — Schema, Toleranz, E13 (E3, Refs #1849).

Das Paketformat ist die Stelle, an der Fremdbestaende auf den neuen Schluessel
treffen. Drei Zusagen aus Konzept
#1847 §E3.8 haengen
daran, und alle drei sind so gebaut, dass sie **nicht** blockieren:

1. **Werteraum-Toleranz** — ein Schluessel, den kein Backend kennt, ist beim
   Laden kein Fehler. Der Werteraum kommt aus den Deklarationen, ein Fremdpaket
   darf ein anderes Vokabular fuehren; sichtbar wird die Luecke ueber den
   Vollstaendigkeitshinweis, nicht ueber einen Parserfehler. Ein harter Fehler
   waere hier besonders teuer: ``parse_package`` wirft, **bevor** ein
   ``PackageV1`` existiert — ein einziger Altwert machte das ganze Paket
   unladbar.
2. **Werte-Normalisierung** — ehemals gueltige Berichtswerte (``note``/``ban``)
   werden uebersetzt, mit Warnung im Report statt still.
3. **E13** — leer→gesetzt erlaubt, gesetzt→anders Fehler; ein **fehlender**
   Schluessel laesst den Bestand unangetastet.

Dazu der Doppellauf ueber ``default.yaml``: seine Werte muessen exakt dem
Ergebnis des Backfills entsprechen, sonst waere der naechste Seed-Lauf gegen
eine aktualisierte Einrichtung ein Immutabilitaets-Konflikt.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.db import connection

from core.models import DocumentType, FieldTemplate
from core.packages.loader import PackageLoadError, load_package
from core.packages.schema import PackageValidationError, parse_package
from core.seed.doc_types import load_default_package_data, seed_document_types

pytestmark = pytest.mark.django_db

_BACKFILL = importlib.import_module("core.migrations.0145_backfill_report_key")


def _raw(typ_extra: dict[str, Any] | None = None, vorlage_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "field_templates": [
            {"slug": "dauer", "name": "Dauer", "field_type": "number", **(vorlage_extra or {})},
        ],
        "document_types": [
            {
                "slug": "kontakt",
                "name": "Kontakt",
                "entity_type": "event",
                "field_refs": [{"template_slug": "dauer", "sort_order": 0}],
                **(typ_extra or {}),
            }
        ],
    }


def _load(facility, raw):
    return load_package(facility, parse_package(raw))


class TestSchema:
    def test_report_key_wird_an_beiden_traegern_angenommen(self):
        package = parse_package(_raw({"report_key": "contact"}, {"report_key": "dauer_minuten"}))

        assert package.document_types[0].report_key == "contact"
        assert package.field_templates[0].report_key == "dauer_minuten"

    def test_unbekannter_schluessel_ist_kein_schema_fehler(self):
        """Der Werteraum kommt aus den Backends — ein Fremdvokabular ist zulaessig."""
        package = parse_package(_raw({"report_key": "fantasie"}))

        assert package.document_types[0].report_key == "fantasie"
        assert package.document_types[0].normalized_report_keys == ()

    @pytest.mark.parametrize("wert", ["Kontakt", "mit leerzeichen", "mit-bindestrich", 42, ["contact"]])
    def test_formatverstoss_ist_ein_schema_fehler(self, wert):
        """Ein Tippfehler ist keine fremde Konvention — das Format wird geprueft."""
        with pytest.raises(PackageValidationError) as exc:
            parse_package(_raw({"report_key": wert}))

        assert any(pfad.endswith("report_key") for pfad, _meldung in exc.value.errors)

    def test_fehlender_und_leerer_schluessel_sind_gleichbedeutend(self):
        assert parse_package(_raw()).document_types[0].report_key is None
        assert parse_package(_raw({"report_key": None})).document_types[0].report_key is None

    @pytest.mark.parametrize("alt", ["note", "ban"])
    def test_ehemals_gueltige_werte_werden_normalisiert(self, alt):
        package = parse_package(_raw({"report_key": alt}))

        assert package.document_types[0].report_key == "excluded"
        assert package.document_types[0].normalized_report_keys == ((alt, "excluded"),)

    def test_die_sieben_zaehlenden_werte_bleiben_gleichnamig(self):
        for key in ("contact", "crisis", "medical", "needle_exchange", "accompaniment", "counseling", "referral"):
            definition = parse_package(_raw({"report_key": key})).document_types[0]
            assert definition.report_key == key
            assert definition.normalized_report_keys == ()


class TestLaden:
    def test_schluessel_wird_geschrieben(self, facility):
        _load(facility, _raw({"report_key": "contact"}, {"report_key": "dauer_minuten"}))

        assert DocumentType.objects.get(facility=facility, slug="kontakt").report_key == "contact"
        assert FieldTemplate.objects.get(facility=facility, slug="dauer").report_key == "dauer_minuten"

    def test_unbekannter_schluessel_laedt_durch(self, facility):
        report = _load(facility, _raw({"report_key": "fantasie"}))

        assert report.errors == []
        assert DocumentType.objects.get(facility=facility, slug="kontakt").report_key == "fantasie"

    def test_normalisierung_erzeugt_eine_warnung(self, facility):
        report = _load(facility, _raw({"report_key": "note"}, {"report_key": "ban"}))

        assert report.errors == []
        assert [identifier for identifier, _text in report.warnings] == ["dauer", "kontakt"]
        assert all("normalisiert" in text for _identifier, text in report.warnings)
        assert DocumentType.objects.get(facility=facility, slug="kontakt").report_key == "excluded"
        assert FieldTemplate.objects.get(facility=facility, slug="dauer").report_key == "excluded"

    def test_leer_nach_gesetzt_ist_erlaubt(self, facility):
        _load(facility, _raw())
        assert DocumentType.objects.get(facility=facility, slug="kontakt").report_key is None

        report = _load(facility, _raw({"report_key": "contact"}, {"report_key": "dauer_minuten"}))

        assert report.errors == []
        assert DocumentType.objects.get(facility=facility, slug="kontakt").report_key == "contact"
        assert FieldTemplate.objects.get(facility=facility, slug="dauer").report_key == "dauer_minuten"

    @pytest.mark.parametrize(
        ("typ_extra", "vorlage_extra", "identifier"),
        [
            ({"report_key": "counseling"}, {}, "kontakt"),
            ({}, {"report_key": "ausgabe_stueck"}, "dauer"),
        ],
    )
    def test_gesetzt_nach_anders_ist_ein_fehler(self, facility, typ_extra, vorlage_extra, identifier):
        """E13 an BEIDEN Traegern — die Feldvorlage hatte bisher gar keinen."""
        _load(facility, _raw({"report_key": "contact"}, {"report_key": "dauer_minuten"}))

        with pytest.raises(PackageLoadError) as exc:
            _load(facility, _raw(typ_extra, vorlage_extra))

        # Ein Konflikt an der Feldvorlage zieht ihre Zuordnung als Folgefehler
        # nach (Bestandsmuster: der Typ landet nicht in der Erfolgs-Map).
        assert exc.value.report.errors[0][0] == identifier
        assert "unveränderlich" in exc.value.report.errors[0][1]

    def test_fehlender_schluessel_laesst_den_bestand_unangetastet(self, facility):
        """Bewusst anders als ``system_type``: ein Paket ohne ``report_key`` ist
        der Normalfall JEDES vor E3 veroeffentlichten Fremdpakets. Waere das
        Weglassen ein Fehler, machte diese Etappe sie gegen eine aktualisierte
        Instanz unladbar, ohne dass das Paket etwas falsch macht.
        """
        _load(facility, _raw({"report_key": "contact"}, {"report_key": "dauer_minuten"}))

        report = _load(facility, _raw())

        assert report.errors == []
        assert DocumentType.objects.get(facility=facility, slug="kontakt").report_key == "contact"
        assert FieldTemplate.objects.get(facility=facility, slug="dauer").report_key == "dauer_minuten"


class TestStandardpaket:
    def test_doppellauf_bleibt_unveraendert(self, facility):
        """Der Kern-Regressionsschutz: die ``default.yaml``-Werte muessen exakt
        dem Backfill-Ergebnis entsprechen, sonst ist der zweite Lauf gegen eine
        aktualisierte Einrichtung ein E13-Konflikt.
        """
        seed_document_types(facility)

        report = seed_document_types(facility)

        assert report.errors == []
        assert report.document_types.updated == []
        assert report.field_templates.updated == []
        assert len(report.document_types.unchanged) == 9

    @staticmethod
    def _schluessel(facility):
        return {
            "typen": dict(DocumentType.objects.filter(facility=facility).values_list("slug", "report_key")),
            "vorlagen": dict(FieldTemplate.objects.filter(facility=facility).values_list("slug", "report_key")),
        }

    #: Systemtypen des Katalogs VOR der E4-Reduktion (Refs #1850). Migration
    #: ``0145`` laeuft auf einer echten Instanz vor ``0147`` — sie sieht die
    #: Werte also noch. Ohne diese Rekonstruktion pruefte der Vergleich unten
    #: einen Zustand, den es nie gab: ``notiz`` bekaeme seinen Sentinel
    #: ``excluded`` nicht (der Slug-Zweitweg des Backfills kennt ihn bewusst
    #: nicht), und der Test schluege an einer Stelle an, an der nichts kaputt
    #: ist.
    _VOR_E4_SYSTEM_TYPES = {
        "kontakt": "contact",
        "krisengespraech": "crisis",
        "medizinische-versorgung": "medical",
        "spritzentausch": "needle_exchange",
        "begleitung": "accompaniment",
        "beratungsgespraech": "counseling",
        "vermittlung": "referral",
        "notiz": "note",
        "hausverbot": "ban",
    }

    @classmethod
    def _backfill_ueber_geleerten_bestand(cls, facility):
        """Den Vor-Migrations-Zustand herstellen und die Migration laufen lassen.

        **Das Leeren ist der Kern.** Ohne es waere jeder Vergleich tautologisch:
        der Seed schreibt die Werte aus der YAML, der Backfill filtert auf leere
        Schluessel und taete dann gar nichts. Zwei vertauschte Zuordnungen in der
        YAML passierten so jede Zusicherung und broechen erst auf einer echten
        Bestandsinstanz am E13-Konflikt.

        Zum Vor-Zustand gehoeren seit E4 auch die Systemtypen: ``.update()``
        umgeht bewusst ``save()`` (dessen Immutabilitaets-Guard genau das
        verhindert) — hier wird kein Bestand veraendert, sondern ein
        historischer nachgestellt.
        """
        DocumentType.objects.filter(facility=facility).update(report_key=None)
        FieldTemplate.objects.filter(facility=facility).update(report_key=None)
        for slug, system_type in cls._VOR_E4_SYSTEM_TYPES.items():
            DocumentType.objects.filter(facility=facility, slug=slug).update(system_type=system_type)
        with connection.schema_editor() as schema_editor:
            _BACKFILL.backfill_report_key(django_apps, schema_editor)

    def test_backfill_und_paket_liefern_dasselbe_ergebnis(self, facility):
        """Beide Wege in dieselbe Instanz — das Paket darf nichts anderes sagen
        als der Bestands-Backfill (sonst waere der naechste Seed ein Konflikt).
        """
        seed_document_types(facility)
        aus_dem_paket = self._schluessel(facility)

        self._backfill_ueber_geleerten_bestand(facility)

        assert self._schluessel(facility) == aus_dem_paket
        assert aus_dem_paket["typen"]["hausverbot"] == "excluded"
        assert aus_dem_paket["vorlagen"]["dauer"] == "dauer_minuten"

    def test_vertauschte_yaml_zuordnung_wuerde_auffallen(self, facility):
        """Positiv-Kontrolle: derselbe Vergleich muss an einer manipulierten YAML reissen.

        Ohne sie bliebe offen, ob der Vergleich oben ueberhaupt etwas misst —
        genau die Luecke, die seinen Vorgaenger tautologisch machte. Vertauscht
        werden ``begleitung`` und ``vermittlung``: beide sind zaehlende
        Schluessel, die Verwechslung waere in keiner anderen Zusicherung sichtbar.
        """
        rohdaten = load_default_package_data()
        vertauscht = {"begleitung": "referral", "vermittlung": "accompaniment"}
        for eintrag in rohdaten["document_types"]:
            if eintrag["slug"] in vertauscht:
                eintrag["report_key"] = vertauscht[eintrag["slug"]]
        load_package(facility, parse_package(rohdaten))
        aus_dem_paket = self._schluessel(facility)
        assert aus_dem_paket["typen"]["begleitung"] == "referral"

        self._backfill_ueber_geleerten_bestand(facility)

        assert self._schluessel(facility) != aus_dem_paket

    def test_katalog_fuehrt_den_schluessel_an_jedem_typ_mit(self):
        """Kein Typ ohne Aussage — sonst faellt er nach der Umstellung in den
        Vollstaendigkeitshinweis, obwohl der Katalog ihn kennt.
        """
        package = parse_package(load_default_package_data())

        ohne = [definition.slug for definition in package.document_types if definition.report_key is None]
        assert ohne == []
