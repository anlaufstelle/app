"""Tests fuer das Beispiel-Backend ``monthly-trend`` (Refs #1754).

Zweites Referenz-Backend der Doku: es zeigt, dass ein Bericht ueber
**Modellfelder** (``Event.occurred_at`` + Dokumenttyp-Mapping) ohne Migration,
View- oder URL-Aenderung entsteht. Die Faelle folgen der Checkliste aus
``docs/reporting-plugins.md`` § 10 (Discovery, Zeitraumgrenzen,
``is_deleted``-Filter, Fremd-Facility, ``is_available``, Suppression,
Rueckprojektion auf die FieldSpec-Keys).
"""

from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone
from django.utils.functional import Promise

from core.models import DocumentType, Event, Facility, Organization, Settings
from core.reporting import registry
from core.reporting.base import CompletenessIssue, DatasetResult
from core.reporting.data_source import MAPPED_EVENTS
from tests.conftest import isolate_reporting_registry

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends bleiben verfuegbar.

    Muster aus ``test_reporting_example.py`` (``docs/reporting-plugins.md``
    § 10): erst die echte Discovery ausloesen (``@register_backend`` ist ein
    Import-Seiteneffekt und feuert je Modul nur beim *ersten* Import), dann mit
    einer Kopie isolieren.
    """
    isolate_reporting_registry(monkeypatch)


@pytest.fixture
def backend():
    return registry.get_backend("monthly-trend")


# Fester Zeitraum statt "heute +/- 1 Tag": die Monatsbucket-Faelle brauchen
# reproduzierbare Kalendergrenzen, unabhaengig vom Ausfuehrungsdatum.
JANUAR = date(2026, 1, 1)
FEBRUAR_ENDE = date(2026, 2, 28)


def _berlin(jahr, monat, tag, stunde=12, minute=0):
    """Aware Zeitpunkt in der Projektzeitzone (``TIME_ZONE = Europe/Berlin``).

    Die Monatsbuckets entstehen ueber ``TruncMonth`` und damit in der aktiven
    Zeitzone — genau wie die ``occurred_at__date``-Zeitraumgrenzen. Die Tests
    setzen deshalb **lokale** Zeitpunkte, sonst prueften sie eine andere
    Semantik als die Produktion.
    """
    return timezone.make_aware(datetime(jahr, monat, tag, stunde, minute))


@pytest.fixture
def doc_type_kontakt(facility):
    """Erstschluessel-Fall: gesetzter ``report_key`` (Erststufe seit E3, #1849).

    Einen begleitenden ``system_type`` gibt es dazu nicht mehr — ``contact`` ist
    mit E4 (Refs #1850) aus dem Enum entfernt.
    """
    return DocumentType.objects.create(
        facility=facility,
        name="Ambulanz",
        report_key="contact",
    )


@pytest.fixture
def doc_type_beratung_mit_paket_slug(facility):
    """Schluessel gesetzt, Slug aus dem Paket — der Slug traegt nichts bei (E-E)."""
    return DocumentType.objects.create(
        facility=facility,
        name="Beratungsgespräch",
        slug="beratungsgespraech",
        report_key="counseling",
    )


@pytest.fixture
def doc_type_nur_paket_slug(facility):
    """Entscheid E-E: der Paket-Slug allein bindet seit E3 nicht mehr."""
    return DocumentType.objects.create(
        facility=facility,
        name="Eigene Beratung",
        slug="beratungsgespraech-2",
    )


@pytest.fixture
def doc_type_hausverbot(facility):
    """Bewusst ausgeschlossen: der Sentinel ``excluded`` (Backfill-Ergebnis)."""
    return DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        system_type=DocumentType.SystemType.BAN,
        report_key="excluded",
    )


def _event(facility, doc_type, user, when):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type,
        occurred_at=when,
        data_json={},
        created_by=user,
    )


class TestDiscovery:
    def test_backend_wird_automatisch_gefunden(self):
        assert "monthly-trend" in [b.slug for b in registry.get_backends()]


class TestBackendMetadaten:
    def test_slug_und_snapshot_flag(self, backend):
        assert backend.slug == "monthly-trend"
        assert backend.snapshot_enabled is False

    def test_population_ist_mapped_events(self, backend):
        """Nur Ereignisse **mit** Berichtszuordnung — der Ausweis muss das sagen."""
        assert backend.population == MAPPED_EVENTS

    def test_name_und_labels_sind_lazy(self, backend):
        assert isinstance(backend.name, Promise)
        for spec in backend.get_field_specs():
            assert isinstance(spec.label, Promise), f"FieldSpec {spec.key!r} traegt Klartext statt gettext_lazy"

    def test_field_spec_keys(self, backend):
        assert [spec.key for spec in backend.get_field_specs()] == ["monat", "kategorie", "count"]


class TestBuildDataset:
    def test_leere_facility_liefert_wohlgeformtes_leeres_ergebnis(self, backend, facility):
        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.mode == "rows"
        assert result.records == []
        assert result.summary == {"total": 0}
        assert result.completeness == []

    def test_monatsbucket_ueber_die_monatsgrenze(self, backend, facility, staff_user, doc_type_kontakt):
        """Ereignis am Monatsletzten spaetabends und am Monatsersten frueh:
        zwei Buckets — die Truncation laeuft in lokaler Zeit, nicht in UTC.

        Beide Zeitpunkte liegen in UTC am **selben** Kalendertag (31.01.),
        eine UTC-Truncation legte sie also in denselben Januar-Bucket.
        """
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 1, 31, 23, 30))
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 2, 1, 0, 30))

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.records == [
            {"monat": "2026-01", "kategorie": "Kontakte", "count": 1},
            {"monat": "2026-02", "kategorie": "Kontakte", "count": 1},
        ]
        assert result.summary == {"total": 2}

    def test_kategorien_je_monat_sortiert(
        self, backend, facility, staff_user, doc_type_kontakt, doc_type_beratung_mit_paket_slug
    ):
        """Zwei Kategorien, und die Zeilenordnung ist (Monat, Kategorie) —
        wertunabhaengig, damit ``ORDER_INPUT`` traegt."""
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 1, 10))
        _event(facility, doc_type_beratung_mit_paket_slug, staff_user, _berlin(2026, 1, 11))
        _event(facility, doc_type_beratung_mit_paket_slug, staff_user, _berlin(2026, 1, 12))
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 2, 5))

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.records == [
            {"monat": "2026-01", "kategorie": "Beratung", "count": 2},
            {"monat": "2026-01", "kategorie": "Kontakte", "count": 1},
            {"monat": "2026-02", "kategorie": "Kontakte", "count": 1},
        ]
        assert result.summary == {"total": 4}

    def test_ausdruecklich_ausgeschlossener_typ_erscheint_nicht_und_meldet_nichts(
        self, backend, facility, staff_user, doc_type_kontakt, doc_type_hausverbot
    ):
        """Hausverbote gehoeren nicht in diesen Bericht — und sind seit Refs #1819
        auch keine Luecke: der Sentinel ``excluded`` ist eine vollstaendige
        Aussage. Der Slug-Zweitschluessel darf sie weiterhin nicht hereinholen
        (Disclosure-Lehre #1785).
        """
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 1, 10))
        _event(facility, doc_type_hausverbot, staff_user, _berlin(2026, 1, 11))

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.records == [{"monat": "2026-01", "kategorie": "Kontakte", "count": 1}]
        assert result.summary == {"total": 1}
        assert result.completeness == []

    def test_typ_ohne_jede_zuordnung_wird_als_luecke_gemeldet(self, backend, facility, staff_user, doc_type_kontakt):
        """Der dritte Zustand: keine Aussage getroffen (Refs #1819)."""
        ohne = DocumentType.objects.create(facility=facility, name="Freitext", report_key=None)
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 1, 10))
        _event(facility, ohne, staff_user, _berlin(2026, 1, 11))

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert [(issue.code, issue.count) for issue in result.completeness] == [("unmapped_system_types", 1)]

    def test_unbekannter_schluessel_bekommt_einen_eigenen_code(self, backend, facility, staff_user, doc_type_kontakt):
        """Entscheid E-H: „ich habe zugeordnet, es passiert nur nichts"."""
        fremd = DocumentType.objects.create(facility=facility, name="Fremdpaket-Typ", report_key="fantasie")
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 1, 10))
        _event(facility, fremd, staff_user, _berlin(2026, 1, 11))

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert [(issue.code, issue.count) for issue in result.completeness] == [("unknown_report_keys", 1)]

    def test_gesetzter_erstschluessel_schlaegt_den_slug(self, backend, facility, staff_user):
        """Ein ausdruecklich ausgeschlossener Typ mit dem Slug ``kontakt`` bleibt draussen."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Hausverbot",
            slug="kontakt",
            system_type=DocumentType.SystemType.BAN,
            report_key="excluded",
        )
        _event(facility, doc_type, staff_user, _berlin(2026, 1, 10))

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.records == []
        assert result.summary == {"total": 0}

    def test_soft_geloeschte_ereignisse_zaehlen_nicht(self, backend, facility, staff_user, doc_type_kontakt):
        event = _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 1, 10))
        event.is_deleted = True
        event.save(update_fields=["is_deleted"])

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.records == []
        assert result.summary == {"total": 0}

    def test_zeitraumgrenzen_sind_inklusiv(self, backend, facility, staff_user, doc_type_kontakt):
        _event(facility, doc_type_kontakt, staff_user, _berlin(2025, 12, 31, 12))
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 1, 1, 0, 5))
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 2, 28, 23, 55))
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 3, 1, 0, 5))

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.records == [
            {"monat": "2026-01", "kategorie": "Kontakte", "count": 1},
            {"monat": "2026-02", "kategorie": "Kontakte", "count": 1},
        ]

    def test_fremde_facility_ist_unsichtbar(self, backend, facility, staff_user):
        fremd = Facility.objects.create(organization=Organization.objects.create(name="Fremd"), name="Fremdstelle")
        fremd_typ = DocumentType.objects.create(facility=fremd, name="Ambulanz", report_key="contact")
        _event(fremd, fremd_typ, staff_user, _berlin(2026, 1, 10))

        assert backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE).records == []

    def test_query_anzahl_ist_eingefroren(
        self, backend, facility, staff_user, doc_type_kontakt, django_assert_num_queries
    ):
        """Eine Aggregation fuer die Zeilen, eine fuer den Vollstaendigkeitscheck."""
        _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 1, 10))

        with django_assert_num_queries(2):
            backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)


class TestIsAvailable:
    def test_positiv_ueber_den_report_key(self, backend, facility, doc_type_kontakt):
        assert backend.is_available(facility) is True

    def test_positiv_mit_paket_slug(self, backend, facility, doc_type_beratung_mit_paket_slug):
        assert backend.is_available(facility) is True

    def test_negativ_wenn_nur_der_paket_slug_passt(self, backend, facility, doc_type_nur_paket_slug):
        """Entscheid E-E (Refs #1849): der Slug macht den Bericht nicht mehr waehlbar.

        Das ist die Kehrseite des Wegfalls, auf die der Inventar-Command vor der
        Abschaltung geschaut hat — ein Bestandstyp OHNE Schluessel laesst das
        Backend aus dem Dropdown fallen.
        """
        assert backend.is_available(facility) is False

    def test_negativ_ohne_passenden_dokumenttyp(self, backend, facility, doc_type_hausverbot):
        assert backend.is_available(facility) is False

    def test_negativ_bei_leerer_facility(self, backend, facility):
        assert backend.is_available(facility) is False

    def test_negativ_wenn_typ_deaktiviert(self, backend, facility, doc_type_kontakt):
        doc_type_kontakt.is_active = False
        doc_type_kontakt.save()

        assert backend.is_available(facility) is False

    def test_negativ_wenn_nur_nicht_ereignis_typen_zugeordnet_sind(self, backend, facility):
        """Refs #1754: Der Bericht zaehlt ``Event``-Zeilen. Ein Personen- oder
        Fall-Formular mit passendem Berichtsschluessel erzeugt keine Ereignisse —
        der Bericht stuende im Dropdown und zeigte dauerhaft Nullen (dieselbe
        Regel wie in ``client-metrics``)."""
        DocumentType.objects.create(
            facility=facility,
            name="Kontaktprofil",
            report_key="contact",
            entity_type=DocumentType.EntityType.CLIENT,
        )

        assert backend.is_available(facility) is False

    def test_fremde_facility_macht_nicht_verfuegbar(self, backend, facility, doc_type_kontakt):
        fremd = Facility.objects.create(organization=Organization.objects.create(name="Fremd"), name="Fremdstelle")

        assert backend.is_available(fremd) is False


def _rohergebnis(records, total, completeness=()):
    return DatasetResult(mode="rows", records=list(records), summary={"total": total}, completeness=list(completeness))


class TestPrepareExport:
    def test_zelle_unter_schwelle_faellt_mit_der_randsumme(self, backend, facility):
        """Einzige Zeile < k: die Zelle faellt, und ``total`` ist hier die Zelle
        selbst — Randsummen-Regel R14."""
        prepared = backend.prepare_export(
            facility, _rohergebnis([{"monat": "2026-01", "kategorie": "Kontakte", "count": 2}], 2)
        )

        assert prepared.records == [{"monat": "2026-01", "kategorie": "Kontakte", "count": None}]
        assert prepared.summary["total"] is None
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_sekundaere_suppression_gegen_rueckrechnung(self, backend, facility):
        """Eine Zelle < k, eine >= k: die verbliebene unterdrueckte Zelle waere
        aus ``total`` exakt rueckrechenbar — das Machbarkeits-Audit laesst
        deshalb die kleinste sichtbare Zelle mitfallen."""
        prepared = backend.prepare_export(
            facility,
            _rohergebnis(
                [
                    {"monat": "2026-01", "kategorie": "Beratung", "count": 2},
                    {"monat": "2026-01", "kategorie": "Kontakte", "count": 9},
                ],
                11,
            ),
        )

        assert [row["count"] for row in prepared.records] == [None, None]
        assert prepared.summary["total"] == 11

    def test_zellen_ab_schwelle_bleiben_sichtbar(self, backend, facility):
        prepared = backend.prepare_export(
            facility,
            _rohergebnis(
                [
                    {"monat": "2026-01", "kategorie": "Kontakte", "count": 7},
                    {"monat": "2026-02", "kategorie": "Kontakte", "count": 6},
                ],
                13,
            ),
        )

        assert prepared.records == [
            {"monat": "2026-01", "kategorie": "Kontakte", "count": 7},
            {"monat": "2026-02", "kategorie": "Kontakte", "count": 6},
        ]
        assert prepared.summary["total"] == 13

    def test_records_tragen_exakt_die_field_spec_keys(self, backend, facility):
        """Das interne ``suppressed``-Flag aus ``_suppress_small`` darf nicht
        ueberleben — JSON serialisiert die Records unveraendert."""
        prepared = backend.prepare_export(
            facility, _rohergebnis([{"monat": "2026-01", "kategorie": "Kontakte", "count": 2}], 2)
        )

        field_keys = {spec.key for spec in backend.get_field_specs()}
        for row in prepared.records:
            assert set(row) == field_keys

    def test_schwelle_kommt_aus_den_facility_settings(self, backend, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})

        prepared = backend.prepare_export(
            facility, _rohergebnis([{"monat": "2026-01", "kategorie": "Kontakte", "count": 2}], 2)
        )

        assert prepared.records[0]["count"] == 2
        assert prepared.summary["total"] == 2
        assert prepared.summary["k_anonymity_threshold"] == 1

    def test_vollstaendigkeitsmeldung_ist_datenunabhaengig_und_unbeziffert(self, backend, facility):
        """K6 (Refs #1769): Der Export zeigt die Meldung **immer** und **nie**
        mit einer Zahl — sonst publizierte sie selbst eine Kleinstfallzahl."""
        ohne_luecke = backend.prepare_export(
            facility, _rohergebnis([{"monat": "2026-01", "kategorie": "Kontakte", "count": 7}], 7)
        )
        mit_luecke = backend.prepare_export(
            facility,
            _rohergebnis(
                [{"monat": "2026-01", "kategorie": "Kontakte", "count": 7}],
                7,
                completeness=[CompletenessIssue("unmapped_system_types", "3 Ereignisse ...", "warning", count=3)],
            ),
        )

        erwartet = ["unmapped_system_types", "unknown_report_keys"]
        assert [issue.code for issue in ohne_luecke.completeness] == erwartet
        assert [issue.code for issue in mit_luecke.completeness] == erwartet
        for prepared in (ohne_luecke, mit_luecke):
            for issue in prepared.completeness:
                assert issue.count is None
                assert not any(zeichen.isdigit() for zeichen in str(issue.message))


class TestEndeZuEndeUeberBuildDataset:
    def test_panel_sicht_zeigt_suppressierte_zahlen(self, backend, facility, staff_user, doc_type_kontakt):
        """Panel und Download rufen ``prepare_export(facility, build_dataset(...))``
        — die Rohzahlen duerfen dort nie erscheinen."""
        for tag in range(1, 4):
            _event(facility, doc_type_kontakt, staff_user, _berlin(2026, 1, 10) + timedelta(days=tag))

        prepared = backend.prepare_export(facility, backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE))

        assert prepared.records == [{"monat": "2026-01", "kategorie": "Kontakte", "count": None}]
        assert prepared.summary["total"] is None
