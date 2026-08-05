"""Tests fuer das Beispiel-Backend ``client-metrics`` (Refs #1754).

Referenz fuer **Personen**-Kennzahlen: jede publizierte Zahl ist eine
Personenzaehlung und damit vollstaendig k-anon-pflichtig. Die Faelle folgen der
Checkliste aus ``docs/reporting-plugins.md`` § 10 und pruefen zusaetzlich die
beiden Fallen dieses Backends: Personenbezug (anonyme Ereignisse zaehlen nicht)
und die Kaskade zwischen Altersverteilung und Randsumme.
"""

from datetime import date, datetime

import pytest
from django.utils import timezone
from django.utils.functional import Promise

from core.models import Client, DocumentType, Event, Facility, Organization, Settings
from core.reporting import registry
from core.reporting.base import DatasetResult
from core.reporting.data_source import PERSONS

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends bleiben verfuegbar (§ 10)."""
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


@pytest.fixture
def backend():
    return registry.get_backend("client-metrics")


JANUAR = date(2026, 1, 1)
FEBRUAR_ENDE = date(2026, 2, 28)


def _berlin(jahr, monat, tag, stunde=12):
    return timezone.make_aware(datetime(jahr, monat, tag, stunde))


@pytest.fixture
def doc_type(facility):
    return DocumentType.objects.create(facility=facility, name="Kontakt")


def _client(facility, pseudonym, age_cluster=Client.AgeCluster.UNKNOWN, *, created=None):
    obj = Client.objects.create(facility=facility, pseudonym=pseudonym, age_cluster=age_cluster)
    if created is not None:
        # ``created_at`` ist ``auto_now_add`` — nur ein Queryset-``update``
        # kommt daran vorbei.
        Client.objects.filter(pk=obj.pk).update(created_at=created)
        obj.refresh_from_db()
    return obj


def _event(facility, doc_type, user, when, client=None):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=when,
        data_json={},
        created_by=user,
    )


class TestDiscovery:
    def test_backend_wird_automatisch_gefunden(self):
        assert "client-metrics" in [b.slug for b in registry.get_backends()]


class TestBackendMetadaten:
    def test_slug_und_snapshot_flag(self, backend):
        assert backend.slug == "client-metrics"
        assert backend.snapshot_enabled is False

    def test_population_ist_personen(self, backend):
        assert backend.population == PERSONS

    def test_name_und_labels_sind_lazy(self, backend):
        assert isinstance(backend.name, Promise)
        for spec in backend.get_field_specs():
            assert isinstance(spec.label, Promise), f"FieldSpec {spec.key!r} traegt Klartext statt gettext_lazy"

    def test_field_spec_keys(self, backend):
        assert [spec.key for spec in backend.get_field_specs()] == ["alterscluster", "personen"]


class TestBuildDataset:
    def test_leere_facility_liefert_wohlgeformtes_leeres_ergebnis(self, backend, facility):
        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.mode == "aggregate"
        assert result.records == []
        assert result.summary == {"aktive_personen": 0, "neuaufnahmen": 0}
        assert result.completeness == []

    def test_person_mit_mehreren_ereignissen_zaehlt_einmal(self, backend, facility, staff_user, doc_type):
        person = _client(facility, "P1", Client.AgeCluster.U18)
        _event(facility, doc_type, staff_user, _berlin(2026, 1, 10), person)
        _event(facility, doc_type, staff_user, _berlin(2026, 1, 11), person)

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.summary["aktive_personen"] == 1
        assert result.records == [{"alterscluster": "u18", "personen": 1}]

    def test_ereignisse_ausserhalb_des_zeitraums_zaehlen_nicht(self, backend, facility, staff_user, doc_type):
        person = _client(facility, "P1", Client.AgeCluster.U18)
        _event(facility, doc_type, staff_user, _berlin(2025, 12, 31), person)

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.summary["aktive_personen"] == 0
        assert result.records == []

    def test_anonymes_ereignis_zaehlt_nicht(self, backend, facility, staff_user, doc_type):
        _event(facility, doc_type, staff_user, _berlin(2026, 1, 10), client=None)

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.summary["aktive_personen"] == 0
        assert result.records == []

    def test_soft_geloeschtes_ereignis_zaehlt_nicht(self, backend, facility, staff_user, doc_type):
        person = _client(facility, "P1")
        event = _event(facility, doc_type, staff_user, _berlin(2026, 1, 10), person)
        event.is_deleted = True
        event.save(update_fields=["is_deleted"])

        assert backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE).summary["aktive_personen"] == 0

    def test_alterscluster_verteilung_in_wertunabhaengiger_ordnung(self, backend, facility, staff_user, doc_type):
        """Die Zeilen kommen nach Cluster-Schluessel sortiert, nicht nach Anzahl
        — Voraussetzung fuer ``ORDER_INPUT`` (K5)."""
        for index in range(3):
            person = _client(facility, f"U{index}", Client.AgeCluster.U18)
            _event(facility, doc_type, staff_user, _berlin(2026, 1, 10), person)
        person = _client(facility, "A1", Client.AgeCluster.AGE_18_26)
        _event(facility, doc_type, staff_user, _berlin(2026, 1, 10), person)

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.records == [
            {"alterscluster": "18_26", "personen": 1},
            {"alterscluster": "u18", "personen": 3},
        ]
        assert result.summary["aktive_personen"] == 4

    def test_alterscluster_summieren_sich_auf_die_randsumme(self, backend, facility, staff_user, doc_type):
        """Die Partitions-Invariante, an der die K-Anon-Maschinerie rechnet."""
        for index, cluster in enumerate(
            [Client.AgeCluster.U18, Client.AgeCluster.AGE_27_PLUS, Client.AgeCluster.UNKNOWN]
        ):
            person = _client(facility, f"P{index}", cluster)
            _event(facility, doc_type, staff_user, _berlin(2026, 1, 10), person)

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert sum(row["personen"] for row in result.records) == result.summary["aktive_personen"]

    def test_neuaufnahmen_zaehlt_anlagen_im_zeitraum(self, backend, facility):
        _client(facility, "Neu", created=_berlin(2026, 1, 15))
        _client(facility, "Alt", created=_berlin(2025, 12, 15))

        assert backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE).summary["neuaufnahmen"] == 1

    def test_neuaufnahmen_ignoriert_geloeschte_personen(self, backend, facility):
        geloescht = _client(facility, "Weg", created=_berlin(2026, 1, 15))
        geloescht.soft_delete()

        assert backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE).summary["neuaufnahmen"] == 0

    def test_fremde_facility_ist_unsichtbar(self, backend, facility, staff_user):
        fremd = Facility.objects.create(organization=Organization.objects.create(name="Fremd"), name="Fremdstelle")
        fremd_typ = DocumentType.objects.create(facility=fremd, name="Kontakt")
        fremd_person = _client(fremd, "F1", created=_berlin(2026, 1, 15))
        _event(fremd, fremd_typ, staff_user, _berlin(2026, 1, 10), fremd_person)

        result = backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE)

        assert result.records == []
        assert result.summary == {"aktive_personen": 0, "neuaufnahmen": 0}


class TestIsAvailable:
    def test_positiv_mit_ereignis_dokumenttyp(self, backend, facility, doc_type):
        assert backend.is_available(facility) is True

    def test_negativ_bei_leerer_facility(self, backend, facility):
        assert backend.is_available(facility) is False

    def test_negativ_wenn_nur_nicht_ereignis_typen_konfiguriert_sind(self, backend, facility):
        DocumentType.objects.create(
            facility=facility, name="Personenprofil", entity_type=DocumentType.EntityType.CLIENT
        )

        assert backend.is_available(facility) is False

    def test_negativ_wenn_typ_deaktiviert(self, backend, facility, doc_type):
        doc_type.is_active = False
        doc_type.save()

        assert backend.is_available(facility) is False


def _rohergebnis(records, aktive, neuaufnahmen=0):
    return DatasetResult(
        mode="aggregate",
        records=list(records),
        summary={"aktive_personen": aktive, "neuaufnahmen": neuaufnahmen},
        completeness=[],
    )


class TestPrepareExport:
    def test_alles_unter_der_schwelle_faellt(self, backend, facility):
        """Anforderungsaenderung (finale Fix-Welle, Refs #1754): frueher pinnte
        dieser Test eine **stehenbleibende** Zeile ``{"u18": None}``. Genau die
        war das Leck — die Zeilenexistenz publiziert „alle < k aktiven Personen
        sind u18". Liegt die Personenzahl unter der Schwelle, faellt die
        gesamte personenabgeleitete Tabelle (K0-Fail-safe wie im Bestand)."""
        prepared = backend.prepare_export(
            facility, _rohergebnis([{"alterscluster": "u18", "personen": 2}], aktive=2, neuaufnahmen=1)
        )

        assert prepared.records == []
        assert prepared.summary["aktive_personen"] is None
        assert prepared.summary["neuaufnahmen"] is None
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_k0_fail_safe_wirft_die_ganze_altersverteilung_weg(self, backend, facility):
        """K0-Fail-safe (Refs #1768/#1754): ``aktive_personen`` < k ⇒ keine Zeile.

        Der Angriff sitzt in der blossen **Zeilenexistenz**, nicht im Wert: eine
        Tabelle mit zwei genullten Zeilen sagt „die < k aktiven Personen
        verteilen sich auf genau diese beiden Altersgruppen". Artefakt-
        uebergreifend ist das ein Pin bis hinunter zu k = 1, denn der Bestand
        (``_drop_person_derived_breakdowns``) laesst in derselben Lage die ganze
        Tabelle fallen — die Genauigkeit dieses Berichts darf die des Bestands
        nicht ueberschreiten.
        """
        prepared = backend.prepare_export(
            facility,
            _rohergebnis(
                [{"alterscluster": "u18", "personen": 3}, {"alterscluster": "unknown", "personen": 1}],
                aktive=4,
                neuaufnahmen=8,
            ),
        )

        assert prepared.records == []
        assert prepared.summary["aktive_personen"] is None
        # ``neuaufnahmen`` ist keine Zelle der Partition — der Fail-safe fasst
        # sie nicht an, sie steht allein an der Schwelle.
        assert prepared.summary["neuaufnahmen"] == 8

    def test_zeilen_bleiben_ab_der_schwelle_trotz_suppressierter_zellen(self, backend, facility):
        """Gegenprobe zum Fail-safe: er haengt an der **Personen**zahl, nicht an
        der Suppression einzelner Zellen. Ist ``aktive_personen`` >= k, bleiben
        die Zeilen stehen (genullt) — die Doktrin-Kaskade bleibt unveraendert."""
        prepared = backend.prepare_export(
            facility,
            _rohergebnis(
                [{"alterscluster": "u18", "personen": 5}, {"alterscluster": "unknown", "personen": 1}],
                aktive=6,
            ),
        )

        assert [row["alterscluster"] for row in prepared.records] == ["u18", "unknown"]
        assert [row["personen"] for row in prepared.records] == [None, None]
        assert prepared.summary["aktive_personen"] is None

    def test_zahlen_ab_der_schwelle_bleiben_sichtbar(self, backend, facility):
        prepared = backend.prepare_export(
            facility,
            _rohergebnis(
                [{"alterscluster": "u18", "personen": 6}, {"alterscluster": "unknown", "personen": 5}],
                aktive=11,
                neuaufnahmen=7,
            ),
        )

        assert prepared.records == [
            {"alterscluster": "u18", "personen": 6},
            {"alterscluster": "unknown", "personen": 5},
        ]
        assert prepared.summary["aktive_personen"] == 11
        assert prepared.summary["neuaufnahmen"] == 7

    def test_suppressionskaskade_alterscluster_gegen_randsumme(self, backend, facility):
        """Eine Zelle < k neben einer sichtbaren: das Machbarkeits-Audit laesst
        die kleinste sichtbare Zelle mitfallen — **und** die Personen-Randsumme
        faellt, weil eine Altersgruppe personenbedingt unterdrueckt ist
        (``drop_person_margin_on_person_suppression``, Auflage 1 zu K0-b).

        Anforderungsaenderung (Fix-Runde 1, Refs #1754): frueher pinnte dieser
        Fall ``aktive_personen == 6``. Das war die Divergenz zur
        Bestands-Doktrin, die fuer genau diese Partition (Σ Personen je
        Altersgruppe = aktive Personen) in allen drei Bestandsartefakten laeuft.
        """
        prepared = backend.prepare_export(
            facility,
            _rohergebnis(
                [{"alterscluster": "u18", "personen": 5}, {"alterscluster": "unknown", "personen": 1}],
                aktive=6,
                neuaufnahmen=9,
            ),
        )

        assert [row["personen"] for row in prepared.records] == [None, None]
        assert prepared.summary["aktive_personen"] is None
        # ``neuaufnahmen`` ist keine Zelle der Partition und faellt allein an
        # der Schwelle — die Kaskade darf sie nicht mitnehmen.
        assert prepared.summary["neuaufnahmen"] == 9

    def test_randsumme_faellt_bei_personenbedingter_suppression(self, backend, facility):
        """Der konkrete Angriff aus dem Review (k = 5): vier Altersgruppen
        (2, 1, 1, 1) auf eine Randsumme von 5.

        Alle vier Zellen fallen primaer; der Fixpunkt-Guard findet die Spanne
        genau bei ``MIN_FEASIBLE_SPAN`` und greift **nicht**. Bliebe die
        Randsumme stehen, wuesste die Empfaengerin: jede Gruppe hat ein oder
        zwei Personen, drei davon genau eine — die einzige mit Σ = 5
        vertraegliche Partition. Genau dagegen ist die Personen-Randsummen-
        Doktrin gebaut: eine personenbedingt unterdrueckte Zelle laesst die
        Randsumme fallen, noch bevor ein exakter Pin entsteht.
        """
        prepared = backend.prepare_export(
            facility,
            _rohergebnis(
                [
                    {"alterscluster": "18_26", "personen": 1},
                    {"alterscluster": "27_plus", "personen": 1},
                    {"alterscluster": "u18", "personen": 2},
                    {"alterscluster": "unknown", "personen": 1},
                ],
                aktive=5,
            ),
        )

        assert [row["personen"] for row in prepared.records] == [None, None, None, None]
        assert prepared.summary["aktive_personen"] is None

    def test_randsumme_bleibt_wenn_keine_gruppe_unterdrueckt_ist(self, backend, facility):
        """Gegenprobe: die Doktrin ist an die **personenbedingte** Suppression
        gebunden, nicht an ihre blosse Moeglichkeit. Sind alle Gruppen >= k,
        bleibt die Randsumme stehen — sonst waere der Bericht ohne Not leer."""
        prepared = backend.prepare_export(
            facility,
            _rohergebnis(
                [{"alterscluster": "u18", "personen": 7}, {"alterscluster": "unknown", "personen": 5}],
                aktive=12,
            ),
        )

        assert [row["personen"] for row in prepared.records] == [7, 5]
        assert prepared.summary["aktive_personen"] == 12

    def test_neuaufnahmen_faellt_unabhaengig_von_der_verteilung(self, backend, facility):
        prepared = backend.prepare_export(
            facility, _rohergebnis([{"alterscluster": "u18", "personen": 9}], aktive=9, neuaufnahmen=2)
        )

        assert prepared.records == [{"alterscluster": "u18", "personen": 9}]
        assert prepared.summary["aktive_personen"] == 9
        assert prepared.summary["neuaufnahmen"] is None

    def test_records_tragen_exakt_die_field_spec_keys(self, backend, facility):
        """Kein internes ``suppressed``-Flag im Export (JSON serialisiert roh)."""
        prepared = backend.prepare_export(facility, _rohergebnis([{"alterscluster": "u18", "personen": 2}], aktive=2))

        field_keys = {spec.key for spec in backend.get_field_specs()}
        for row in prepared.records:
            assert set(row) == field_keys

    def test_schwelle_kommt_aus_den_facility_settings(self, backend, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})

        prepared = backend.prepare_export(
            facility, _rohergebnis([{"alterscluster": "u18", "personen": 2}], aktive=2, neuaufnahmen=1)
        )

        assert prepared.records[0]["personen"] == 2
        assert prepared.summary["aktive_personen"] == 2
        assert prepared.summary["neuaufnahmen"] == 1
        assert prepared.summary["k_anonymity_threshold"] == 1


class TestEndeZuEndeUeberBuildDataset:
    def test_panel_sicht_zeigt_suppressierte_zahlen(self, backend, facility, staff_user, doc_type):
        """Anforderungsaenderung (finale Fix-Welle, Refs #1754): drei aktive
        Personen liegen unter der Schwelle, also faellt die Altersverteilung
        **ganz** — vorher blieb die genullte ``u18``-Zeile stehen und verriet,
        dass alle drei Personen unter 18 sind (K0-Fail-safe)."""
        for index in range(3):
            person = _client(facility, f"P{index}", Client.AgeCluster.U18, created=_berlin(2026, 1, 5))
            _event(facility, doc_type, staff_user, _berlin(2026, 1, 10), person)

        prepared = backend.prepare_export(facility, backend.build_dataset(facility, JANUAR, FEBRUAR_ENDE))

        assert prepared.records == []
        assert prepared.summary["aktive_personen"] is None
        assert prepared.summary["neuaufnahmen"] is None
