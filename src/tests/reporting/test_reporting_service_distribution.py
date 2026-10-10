"""Tests fuer das Beispiel-Backend ``service-distribution`` (Refs #1754).

Drittes Feldwert-Backend und der Fall, an dem die Randsummen-Frage kippt: eine
``multi_select``-Verteilung partitioniert ihre Ereignis-Randsumme **nicht**
(ein Ereignis kann mehrere Optionen tragen), eine ``select``-Verteilung schon.
Die Tests pinnen deshalb vor allem, dass beide Gruppen unabhaengig voneinander
suppressiert werden und nur die Einfachauswahl eine Randsummen-Partition
deklariert.
"""

import csv
import json
import xml.etree.ElementTree as ET
from datetime import timedelta

import pytest
from django.utils import timezone
from django.utils.functional import Promise

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, Settings
from core.reporting import registry
from core.reporting.backends.service_distribution import (
    KRISE_SLUG,
    LEISTUNGEN_SLUG,
    ServiceDistributionBackend,
)
from core.reporting.base import EXCLUDED_KEY, DatasetResult
from core.reporting.data_source import TYPED_EVENTS
from core.reporting.export import export_dataset
from tests._reporting_helpers import feld_report_key_fuer

pytestmark = pytest.mark.django_db


@pytest.fixture
def backend():
    return registry.get_backend("service-distribution")


def _type_with_field(facility, name, *, report_key, field_name, field_type, attach=True):
    """Bind the backend by report key and configure valid selection options."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        name=name,
        report_key=report_key,
    )
    if attach:
        field, _created = FieldTemplate.objects.get_or_create(
            facility=facility,
            name=field_name,
            defaults={
                "field_type": field_type,
                "report_key": feld_report_key_fuer(field_name),
                "options_json": [
                    {"slug": slug, "label": slug}
                    for slug in (
                        ("essen", "duschen", "beratung", "sachspenden")
                        if field_type == FieldTemplate.FieldType.MULTI_SELECT
                        else ("gewalt", "suizidal", "sucht")
                    )
                ],
            },
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    return doc_type


@pytest.fixture
def kontakt_typ(facility):
    return _type_with_field(
        facility,
        "Kontakt",
        report_key="contact",
        field_name="Leistungen",
        field_type=FieldTemplate.FieldType.MULTI_SELECT,
    )


@pytest.fixture
def krise_typ(facility):
    return _type_with_field(
        facility,
        "Krisengespräch",
        report_key="crisis",
        field_name="Art der Krise",
        field_type=FieldTemplate.FieldType.SELECT,
    )


def _events(facility, doc_type, user, count, data, *, legacy=False):
    for _index in range(count):
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={} if legacy else dict(data),
            created_by=user,
        )
        if legacy:
            # Historical malformed or removed selections predate write validation.
            Event.objects.filter(pk=event.pk).update(data_json=dict(data))


class TestDiscoveryUndMetadaten:
    def test_discovery_findet_das_backend(self):
        assert "service-distribution" in [b.slug for b in registry.get_backends()]

    def test_registriert_unter_slug(self, backend):
        assert isinstance(backend, ServiceDistributionBackend)
        assert backend.slug == "service-distribution"

    def test_snapshot_bleibt_aus(self, backend):
        assert backend.snapshot_enabled is False

    def test_grundgesamtheit_ist_enger_als_der_default(self, backend):
        """I3: das Backend zaehlt NICHT alle Ereignisse des Zeitraums, sondern
        nur die seiner Dokumentationstypen — ``base.py`` verlangt dann eine
        explizite, engere Deklaration.
        """
        assert backend.population == TYPED_EVENTS

    def test_field_specs(self, backend):
        assert [spec.key for spec in backend.get_field_specs()] == ["feld", "option", "count"]

    def test_labels_sind_lazy(self, backend):
        for spec in backend.get_field_specs():
            assert isinstance(spec.label, Promise), f"FieldSpec {spec.key!r} traegt Klartext statt gettext_lazy"

    def test_name_ist_lazy(self, backend):
        assert isinstance(ServiceDistributionBackend.name, Promise)


class TestBuildDataset:
    def test_mehrfachnennungen_zaehlen_je_option(
        self, backend, facility, admin_user, kontakt_typ, krise_typ, date_range
    ):
        _events(facility, kontakt_typ, admin_user, 2, {LEISTUNGEN_SLUG: ["essen", "duschen"]})
        _events(facility, kontakt_typ, admin_user, 1, {LEISTUNGEN_SLUG: ["essen"]})
        _events(facility, krise_typ, admin_user, 3, {KRISE_SLUG: "gewalt"})

        result = backend.build_dataset(facility, *date_range)

        assert result.mode == "rows"
        assert result.records == [
            {"feld": LEISTUNGEN_SLUG, "option": "duschen", "count": 2},
            {"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 3},
            {"feld": KRISE_SLUG, "option": "gewalt", "count": 3},
        ]
        # Die Options-Counts summieren sich BEWUSST nicht auf die Randsumme:
        # 2 + 3 > 3 Ereignisse mit Leistungen.
        assert result.summary["events_leistungen"] == 3
        assert result.summary["events_art_der_krise"] == 3

    def test_doppelte_option_zaehlt_je_ereignis_einmal(self, backend, facility, admin_user, kontakt_typ, date_range):
        """Die Zelle ist eine Ereigniszahl („Ereignisse mit dieser Option") —
        sonst waere sie keine k-anonymisierbare Groesse.
        """
        _events(facility, kontakt_typ, admin_user, 1, {LEISTUNGEN_SLUG: ["essen", "essen"]})

        result = backend.build_dataset(facility, *date_range)

        assert result.records == [{"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 1}]

    def test_string_statt_liste_wird_als_ein_wert_gelesen(self, backend, facility, admin_user, kontakt_typ, date_range):
        """Altwert-Robustheit: aeltere/fremde Schreibpfade legen einen blanken
        String ab, wo heute eine Liste steht.
        """
        _events(facility, kontakt_typ, admin_user, 2, {LEISTUNGEN_SLUG: "beratung"}, legacy=True)

        result = backend.build_dataset(facility, *date_range)

        assert result.records == [{"feld": LEISTUNGEN_SLUG, "option": "beratung", "count": 2}]
        assert result.summary["events_leistungen"] == 2

    def test_muellwerte_sind_kein_absturz(self, backend, facility, admin_user, kontakt_typ, date_range):
        _events(facility, kontakt_typ, admin_user, 1, {LEISTUNGEN_SLUG: 42}, legacy=True)
        _events(facility, kontakt_typ, admin_user, 1, {LEISTUNGEN_SLUG: ["", "   ", None, 7]}, legacy=True)
        _events(facility, kontakt_typ, admin_user, 1, {LEISTUNGEN_SLUG: ["essen"]})

        result = backend.build_dataset(facility, *date_range)

        assert result.records == [{"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 1}]
        assert result.summary["events_leistungen"] == 1
        assert [issue.code for issue in result.completeness] == ["missing_field_values"]
        assert result.completeness[0].count == 2

    def test_unbekannte_optionsschluessel_erscheinen_wie_gespeichert(
        self, backend, facility, admin_user, kontakt_typ, date_range
    ):
        """Options-Keys kommen roh aus ``data_json`` — auch inaktive oder
        geloeschte Optionen bleiben so sichtbar, wie sie dokumentiert wurden.
        """
        _events(facility, kontakt_typ, admin_user, 1, {LEISTUNGEN_SLUG: ["sachspenden", "gibt-es-nicht"]}, legacy=True)

        result = backend.build_dataset(facility, *date_range)

        assert [row["option"] for row in result.records] == ["gibt-es-nicht", "sachspenden"]

    def test_verschluesselter_feldwert_roundtrip(self, backend, facility, admin_user, krise_typ, date_range):
        FieldTemplate.objects.filter(facility=facility, slug=KRISE_SLUG).update(is_encrypted=True)
        _events(facility, krise_typ, admin_user, 1, {KRISE_SLUG: "suizidal"})
        assert Event.objects.get().data_json[KRISE_SLUG]["__encrypted__"] is True

        result = backend.build_dataset(facility, *date_range)

        assert result.records == [{"feld": KRISE_SLUG, "option": "suizidal", "count": 1}]

    def test_nur_konfigurierte_gruppen_erscheinen(self, backend, facility, admin_user, kontakt_typ, date_range):
        _events(facility, kontakt_typ, admin_user, 1, {LEISTUNGEN_SLUG: ["essen"]})

        result = backend.build_dataset(facility, *date_range)

        assert set(result.summary) == {"events_leistungen"}
        assert [row["feld"] for row in result.records] == [LEISTUNGEN_SLUG]

    def test_leerer_zeitraum(self, backend, facility, admin_user, kontakt_typ, krise_typ):
        _events(facility, kontakt_typ, admin_user, 1, {LEISTUNGEN_SLUG: ["essen"]})
        vorher = timezone.localdate() - timedelta(days=100)

        result = backend.build_dataset(facility, vorher, vorher + timedelta(days=1))

        assert result.records == []
        assert result.summary == {"events_leistungen": 0, "events_art_der_krise": 0}

    def test_soft_geloeschte_events_zaehlen_nicht(self, backend, facility, admin_user, kontakt_typ, date_range):
        _events(facility, kontakt_typ, admin_user, 1, {LEISTUNGEN_SLUG: ["essen"]})
        Event.objects.update(is_deleted=True)

        result = backend.build_dataset(facility, *date_range)

        assert result.records == []

    def test_fremde_einrichtung_zaehlt_nicht(
        self, backend, facility, kontakt_typ, second_facility, second_facility_user, date_range
    ):
        fremd = _type_with_field(
            second_facility,
            "Kontakt",
            report_key="contact",
            field_name="Leistungen",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
        )
        _events(second_facility, fremd, second_facility_user, 3, {LEISTUNGEN_SLUG: ["essen"]})

        result = backend.build_dataset(facility, *date_range)

        assert result.records == []


class TestTypaufloesung:
    def test_typ_zaehlt_ueber_den_report_key(self, backend, facility, admin_user, date_range):
        """Die Zuordnung kommt allein aus dem Schluessel (Entscheid E-D)."""
        typ = _type_with_field(
            facility,
            "Kontakt",
            report_key="contact",
            field_name="Leistungen",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
        )
        assert typ.slug == "kontakt"
        _events(facility, typ, admin_user, 1, {LEISTUNGEN_SLUG: ["essen"]})

        result = backend.build_dataset(facility, *date_range)

        assert result.records == [{"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 1}]

    def test_der_slug_allein_zaehlt_nicht_mehr(self, backend, facility, admin_user, date_range):
        """Entscheid E-E (Refs #1849): der Paket-Slug ordnet nichts mehr zu."""
        typ = _type_with_field(
            facility,
            "Kontakt",
            report_key=None,
            field_name="Leistungen",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
        )
        assert typ.slug == "kontakt"
        _events(facility, typ, admin_user, 1, {LEISTUNGEN_SLUG: ["essen"]})

        result = backend.build_dataset(facility, *date_range)

        assert result.records == []

    def test_ausgeschlossener_schluessel_sperrt_den_slug(self, backend, facility, admin_user, date_range):
        """Refs #1785: ein ausdruecklich ausgeschlossener Typ wird vom Slug NICHT gerettet.

        Der Notiz-Typ traegt den Paket-Slug ``kontakt``; die Gruppe ist ueber
        einen zweiten, korrekt geschluesselten Dokumentationstyp konfiguriert.
        Wuerde der Slug greifen, zoege der Notiz-Typ seine Ereignisse in die
        Verteilung — genau das Disclosure-Leck aus #1785. Bis E4 stand der
        Ausschluss im ``system_type=note``; heute steht er da, wo die
        Zuordnung selbst steht.
        """
        echter_kontakt = _type_with_field(
            facility,
            "Kontaktdokumentation",
            report_key="contact",
            field_name="Leistungen",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
        )
        notiz = DocumentType.objects.create(
            facility=facility,
            name="Kontaktnotiz",
            slug="kontakt",
            # „Gehoert ausdruecklich in keinen Bericht."
            report_key=EXCLUDED_KEY,
        )
        feld = FieldTemplate.objects.get(facility=facility, slug=LEISTUNGEN_SLUG)
        DocumentTypeField.objects.create(document_type=notiz, field_template=feld)
        _events(facility, notiz, admin_user, 3, {LEISTUNGEN_SLUG: ["essen"]})
        _events(facility, echter_kontakt, admin_user, 1, {LEISTUNGEN_SLUG: ["duschen"]})

        result = backend.build_dataset(facility, *date_range)

        assert result.records == [{"feld": LEISTUNGEN_SLUG, "option": "duschen", "count": 1}]
        assert result.summary["events_leistungen"] == 1


def _result(zeilen, summary):
    return DatasetResult(mode="rows", records=zeilen, summary=summary, completeness=[])


class TestPrepareExport:
    def test_beide_gruppen_ueber_der_schwelle(self, backend, facility):
        prepared = backend.prepare_export(
            facility,
            _result(
                [
                    {"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 8},
                    {"feld": KRISE_SLUG, "option": "gewalt", "count": 6},
                ],
                {"events_leistungen": 8, "events_art_der_krise": 6},
            ),
        )

        assert prepared.records == [
            {"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 8},
            {"feld": KRISE_SLUG, "option": "gewalt", "count": 6},
        ]
        assert prepared.summary["events_leistungen"] == 8
        assert prepared.summary["events_art_der_krise"] == 6
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_kleine_zelle_faellt_je_gruppe_unabhaengig(self, backend, facility):
        """Die Gruppen teilen keine Randsumme — eine Suppression in der einen
        darf die andere nicht anfassen.
        """
        prepared = backend.prepare_export(
            facility,
            _result(
                [
                    {"feld": LEISTUNGEN_SLUG, "option": "duschen", "count": 2},
                    {"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 9},
                    {"feld": KRISE_SLUG, "option": "gewalt", "count": 7},
                ],
                {"events_leistungen": 10, "events_art_der_krise": 7},
            ),
        )

        zellen = {(row["feld"], row["option"]): row["count"] for row in prepared.records}
        assert zellen[(LEISTUNGEN_SLUG, "duschen")] is None
        # Keine Sekundaer-Suppression bei Mehrfachnennung: die Nachbarzelle
        # bleibt stehen. Geschuetzt wird stattdessen ueber die Randsumme.
        assert zellen[(LEISTUNGEN_SLUG, "essen")] == 9
        # Total-Doktrin (C1): die Randsumme der Mehrfachnennungs-Gruppe faellt
        # mit der ersten unterdrueckten Zelle — sonst gaebe die Ungleichung
        # ``Σ Counts >= events_leistungen`` eine untere Schranke her.
        assert prepared.summary["events_leistungen"] is None
        # Der Punkt dieses Tests: die Einfachauswahl bleibt davon voellig
        # unberuehrt — die Gruppen teilen keine Randsumme.
        assert zellen[(KRISE_SLUG, "gewalt")] == 7
        assert prepared.summary["events_art_der_krise"] == 7

    def test_suppression_in_der_einfachauswahl_laesst_die_mehrfachauswahl_stehen(self, backend, facility):
        """Gegenrichtung zu ``…_je_gruppe_unabhaengig`` (Refs #1754).

        Dort faellt eine Zelle der **Mehrfach**auswahl und die Einfachauswahl
        bleibt unberuehrt. Hier faellt eine Zelle der **Einfach**auswahl — dem
        Zweig mit deklarierter Randsumme, Sekundaer-Suppression und
        Residual-Guard, also der Maschinerie, die im Extremfall eine ganze
        Aufschluesselung fallen lassen kann. Sie darf ``events_leistungen``
        trotzdem nicht mitnehmen: die Gruppen teilen keine Randsumme, und die
        Total-Doktrin der Mehrfachauswahl haengt ausschliesslich an deren
        **eigenen** Zeilen.
        """
        prepared = backend.prepare_export(
            facility,
            _result(
                [
                    {"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 8},
                    {"feld": KRISE_SLUG, "option": "gewalt", "count": 2},
                    {"feld": KRISE_SLUG, "option": "sucht", "count": 9},
                ],
                {"events_leistungen": 8, "events_art_der_krise": 11},
            ),
        )

        zellen = {(row["feld"], row["option"]): row["count"] for row in prepared.records}
        assert zellen[(KRISE_SLUG, "gewalt")] is None
        assert zellen[(LEISTUNGEN_SLUG, "essen")] == 8
        assert prepared.summary["events_leistungen"] == 8

    def test_mehrfachnennung_randsumme_faellt_bei_jeder_unterdrueckten_zelle(self, backend, facility):
        """C1: die Ungleichung ist auch eine Gleichung, sobald sie eng genug wird.

        ``Σ Options-Counts >= events_leistungen`` (jedes Ereignis mit Wert traegt
        nach Dedup mindestens eine Option bei). Mit ``beratung = 6`` sichtbar und
        ``events_leistungen = 10`` publiziert folgt ``essen >= 10 − 6 = 4``, und
        aus der Primaer-Suppression folgt ``essen <= k − 1 = 4`` — die Zelle ist
        exakt gepinnt, obwohl sie unterdrueckt ist.

        ``margin=None`` allein schliesst diesen Kanal NICHT: es schaltet nur das
        Machbarkeits-Audit ab. Es braucht die Total-Doktrin — ohne publizierte
        Randsumme faellt die untere Schranke, und uebrig bleibt nur noch
        ``1 <= essen <= 4``.
        """
        prepared = backend.prepare_export(
            facility,
            _result(
                [
                    {"feld": LEISTUNGEN_SLUG, "option": "beratung", "count": 6},
                    {"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 4},
                ],
                {"events_leistungen": 10},
            ),
        )

        zellen = {row["option"]: row["count"] for row in prepared.records}
        assert zellen == {"beratung": 6, "essen": None}
        assert prepared.summary["events_leistungen"] is None

    def test_mehrfachnennung_randsumme_bleibt_ohne_suppression(self, backend, facility):
        """Gegenprobe: ohne unterdrueckte Zelle gibt es keine Schranke zu setzen."""
        prepared = backend.prepare_export(
            facility,
            _result(
                [
                    {"feld": LEISTUNGEN_SLUG, "option": "beratung", "count": 6},
                    {"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 7},
                ],
                {"events_leistungen": 10},
            ),
        )

        assert prepared.summary["events_leistungen"] == 10

    def test_einfachauswahl_kennt_die_sekundaer_suppression(self, backend, facility):
        """``art-der-krise`` IST eine exakte Partition seiner Randsumme — die
        verbliebene unterdrueckte Zelle waere sonst die Differenz.
        """
        prepared = backend.prepare_export(
            facility,
            _result(
                [
                    {"feld": KRISE_SLUG, "option": "gewalt", "count": 9},
                    {"feld": KRISE_SLUG, "option": "suizidal", "count": 2},
                ],
                {"events_art_der_krise": 11},
            ),
        )

        assert [row["count"] for row in prepared.records] == [None, None]
        assert prepared.summary["events_art_der_krise"] == 11

    def test_randsumme_unter_schwelle_faellt(self, backend, facility):
        prepared = backend.prepare_export(
            facility,
            _result(
                [{"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 3}],
                {"events_leistungen": 3},
            ),
        )

        assert prepared.records == [{"feld": LEISTUNGEN_SLUG, "option": "essen", "count": None}]
        assert prepared.summary["events_leistungen"] is None

    def test_nicht_konfigurierte_gruppe_bleibt_draussen(self, backend, facility):
        prepared = backend.prepare_export(
            facility,
            _result([{"feld": KRISE_SLUG, "option": "gewalt", "count": 6}], {"events_art_der_krise": 6}),
        )

        assert "events_leistungen" not in prepared.summary
        assert [row["feld"] for row in prepared.records] == [KRISE_SLUG]

    def test_records_tragen_nur_die_field_spec_keys(self, backend, facility):
        prepared = backend.prepare_export(
            facility,
            _result([{"feld": KRISE_SLUG, "option": "gewalt", "count": 6}], {"events_art_der_krise": 6}),
        )

        assert set(prepared.records[0]) == {"feld", "option", "count"}

    def test_completeness_ist_datenunabhaengig_und_unbeziffert(self, backend, facility):
        prepared = backend.prepare_export(
            facility,
            _result([{"feld": KRISE_SLUG, "option": "gewalt", "count": 6}], {"events_art_der_krise": 6}),
        )

        assert [issue.code for issue in prepared.completeness] == ["missing_field_values"]
        assert prepared.completeness[0].count is None

    def test_schwelle_aus_den_facility_settings(self, backend, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 2})

        prepared = backend.prepare_export(
            facility,
            _result(
                [{"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 3}],
                {"events_leistungen": 3},
            ),
        )

        assert prepared.records[0]["count"] == 3
        assert prepared.summary["events_leistungen"] == 3


class TestExportPipeline:
    """Doku § 10: der Datensatz muss durch alle drei Renderer laufen.

    Der Wert liegt nicht im Format-Dispatch (den pinnt ``test_reporting_export.py``
    generisch), sondern in DIESEM Datensatz: variable Summary-Schluessel je
    konfigurierter Gruppe und ``None``-Zellen aus der Suppression sind genau die
    Formen, an denen ein Serialisierer stolpert.
    """

    @pytest.mark.parametrize("fmt", ["csv", "json", "xml"])
    def test_export_laeuft_und_ist_wohlgeformt(
        self, backend, facility, admin_user, kontakt_typ, krise_typ, date_range, fmt
    ):
        _events(facility, kontakt_typ, admin_user, 6, {LEISTUNGEN_SLUG: ["essen"]})
        _events(facility, kontakt_typ, admin_user, 2, {LEISTUNGEN_SLUG: ["duschen"]})
        _events(facility, krise_typ, admin_user, 3, {KRISE_SLUG: "gewalt"})

        response = export_dataset(backend, facility, *date_range, format=fmt)
        body = b"".join(response.streaming_content) if fmt == "csv" else response.content

        assert response.status_code == 200
        assert f"service-distribution_{date_range[0]}_{date_range[1]}.{fmt}" in response["Content-Disposition"]
        if fmt == "json":
            payload = json.loads(body)
            # Die Kleinstzelle „duschen" (2 < 5) darf im Artefakt nur als
            # ``None`` stehen — Panel/Export-Paritaet der Suppression.
            zellen = {row["option"]: row["count"] for row in payload["records"]}
            assert zellen["duschen"] is None
            assert zellen["essen"] == 6
        if fmt == "xml":
            # Begruendung fuers noqa (Muster ``test_reporting_export.py``):
            # eigener, gerade erst generierter Output, keine fremde Eingabe.
            ET.fromstring(body)  # noqa: S314
        if fmt == "csv":
            zeilen = body.decode().splitlines()
            assert next(csv.reader(zeilen[1:])) == ["Feld", "Option", "Anzahl"]


class TestIsAvailable:
    def test_nur_leistungen_konfiguriert(self, backend, facility, kontakt_typ):
        assert backend.is_available(facility) is True

    def test_nur_krisenart_konfiguriert(self, backend, facility, krise_typ):
        assert backend.is_available(facility) is True

    def test_keine_gruppe_konfiguriert(self, backend, facility, doc_type_contact):
        assert backend.is_available(facility) is False

    def test_feld_am_falschen_typ(self, backend, facility):
        _type_with_field(
            facility,
            "Spritzentausch",
            report_key="needle_exchange",
            field_name="Leistungen",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
        )

        assert backend.is_available(facility) is False

    def test_inaktiver_typ_zaehlt_nicht(self, backend, facility, kontakt_typ):
        DocumentType.objects.filter(pk=kontakt_typ.pk).update(is_active=False)

        assert backend.is_available(facility) is False

    def test_fremde_einrichtung_macht_nicht_verfuegbar(self, backend, facility, second_facility):
        _type_with_field(
            second_facility,
            "Kontakt",
            report_key="contact",
            field_name="Leistungen",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
        )

        assert backend.is_available(facility) is False


def test_encrypted_multi_selection_counts_individual_slugs_once(backend, facility, admin_user, kontakt_typ, date_range):
    from django.test import override_settings

    from core.services.file_vault import generate_key
    from tests._factories import make_event

    FieldTemplate.objects.filter(facility=facility, slug=LEISTUNGEN_SLUG).update(is_encrypted=True)
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=generate_key()):
        event = make_event(
            facility=facility,
            client=None,
            document_type=kontakt_typ,
            occurred_at=timezone.now(),
            data_json={LEISTUNGEN_SLUG: ["essen", "beratung", "essen"]},
            created_by=admin_user,
        )
        event.refresh_from_db()
        assert event.data_json[LEISTUNGEN_SLUG]["__encrypted__"] is True
        result = backend.build_dataset(facility, *date_range)
    assert result.records == [
        {"feld": LEISTUNGEN_SLUG, "option": "beratung", "count": 1},
        {"feld": LEISTUNGEN_SLUG, "option": "essen", "count": 1},
    ]
    assert result.summary["events_leistungen"] == 1
    assert result.completeness == []
