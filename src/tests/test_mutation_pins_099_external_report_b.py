"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — ``core.services.dashboard.external_report``,
Batch ``external_report-b`` (Vollmutation-Survivor-Triage, 70 Survivors ueber
``build_external_report``, ``suppress_sample_report_1_stats``, ``suppress_report_stats``,
``_suppress_stage_dict``, ``_age_cluster_key``, ``_suppress_small``,
``_suppress_smallest_visible``, ``apply_residual_guard``, ``suppress_single_margin``,
``with_no_person_row``).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-external_report-b.json`` (Triage
``reports/m-scratch/pins-external_report-b/triage-099-external_report-b.json``).

Kernthema dieser Datei ist die K-Anonymitaets-Suppression externer Berichte
(Refs #921/#1770): ein K2-Machbarkeits-Audit innerhalb jeder einzelnen
Aufschluesselung (``_audit_feasible_span``, ueber ``_suppress_small``/
``_suppress_stage_dict``) UND ein tabellenuebergreifender K1-Fixpunkt-Guard
(``apply_residual_guard``) danach. Viele Survivors betreffen genau die
Randsummen-Weitergabe (``margin=...``), die Personen-Gating-Flags
(``person_gated``/``persons=...``) oder die Default-Werte fehlender
Snapshot-Schluessel — an all diesen Stellen ist die publizierte K-Anonymitaet
unmittelbar betroffen.

**Wichtige empirische Lehre beim Schreiben dieser Pins:** ``apply_residual_guard``
laeuft nach JEDEM der drei Wrapper (``build_external_report``,
``suppress_report_stats``, ``suppress_sample_report_1_stats``) unbedingt und
kann ein deaktiviertes INNERES K2-Audit (``margin=None``) teilweise kompensieren,
SOLANGE die betroffene Aufschluesselung nur eine einzelne primaer unterdrueckte
Zeile neben sichtbaren Zeilen enthaelt — das aeussere K1-Modell ist fuer genau
EINE versteckte Zelle ebenso stark wie das innere K2-Modell. Ein beobachtbarer
Unterschied entsteht erst, wenn eine Aufschluesselung MEHRERE gleichzeitig
verdeckte Zellen enthaelt, deren Schranken sich gegenseitig beeinflussen (K1
nutzt dafuer nur ``hi=None`` fuer ALLE unterdrueckten Zellen, K2 kennt die
schaerfere Schranke ``hi=threshold-1`` fuer primaer unterdrueckte Zeilen). Jedes
Margin-Pin dieser Datei nutzt deshalb bewusst vier (bzw. drei bei
``by_contact_stage``, das strukturell nur drei feste Stufen kennt) gleichzeitig
betroffene Zellen statt nur zwei — mit zwei Zeilen bliebe der Survivor unentdeckt
(empirisch verifiziert, Lehre 137).

Ebenso empirisch verifiziert: ein reiner ``difflib``-Zeilenabgleich kann bei
MEHREREN identischen Codezeilen im selben Funktionskoerper (z. B. zwei
``margin=published_total,``-Zeilen in ``suppress_report_stats``) die FALSCHE
Zeile als veraendert ausweisen. Alle Diffs dieser Datei wurden deshalb
zusaetzlich per striktem Positionsvergleich (Original- vs. Mutanten-Koerper
Zeile fuer Zeile) verifiziert, bevor ein Pin geschrieben wurde — ``mutmut_66``
in ``suppress_report_stats`` betrifft dadurch nachweislich die
``by_contact_stage``-Randsumme, nicht ``by_age_cluster``.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat keine
``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, Event, Settings
from core.reporting.base import DatasetResult, FieldSpec
from core.services.dashboard import (
    build_external_report,
    suppress_report_stats,
    suppress_sample_report_1_stats,
)
from core.services.dashboard.external_report import (
    ALL_EVENTS,
    PERSONS,
    _age_cluster_key,
    _Breakdown,
    _suppress_small,
    _suppress_smallest_visible,
    _suppress_stage_dict,
    apply_residual_guard,
    suppress_single_margin,
    with_no_person_row,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def report_period():
    return date(2026, 1, 1), date(2026, 12, 31)


def _make_events(facility, client, doc_type, user, count: int, *, is_anonymous: bool = False):
    """Hilfsmethode: erzeugt ``count`` Events (analog test_external_report_service.py)."""
    return [
        Event.objects.create(
            facility=facility,
            client=None if is_anonymous else client,
            is_anonymous=is_anonymous,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"dauer": 10},
            created_by=user,
        )
        for _ in range(count)
    ]


def _make_doc_type(facility, name):
    return DocumentType.objects.create(facility=facility, name=name, report_key=name.lower(), tally_enabled=True)


def _make_client(
    facility, user, *, age_cluster=Client.AgeCluster.UNKNOWN, contact_stage=Client.ContactStage.QUALIFIED, pseudonym
):
    return Client.objects.create(
        facility=facility,
        contact_stage=contact_stage,
        age_cluster=age_cluster,
        pseudonym=pseudonym,
        created_by=user,
    )


class TestBuildExternalReportMarginAuditMultiCell:
    """Margin-Verlust in ``build_external_report`` (K2-Audit-Abschaltung).

    Jedes Szenario nutzt vier (bzw. drei bei Kontaktstufen) gleichzeitig
    verdeckte Zellen, weil das aeussere K1-Audit (``apply_residual_guard``,
    laeuft in jedem Fall danach) eine EINZELNE verdeckte Zelle neben
    sichtbaren Zeilen genauso gut aufloest wie das innere K2-Audit — der
    Survivor wird erst mit mehreren gleichzeitig verdeckten Zellen sichtbar
    (siehe Modul-Docstring).
    """

    def test_dokumenttypen_bleiben_ohne_randsumme_teilweise_sichtbar(
        self, facility, staff_user, client_qualified, report_period
    ):
        """Killt ``x_build_external_report__mutmut_20`` (``margin=None`` statt
        ``published_total`` beim ``by_document_type``-Aufruf).

        Vier Dokumenttypen mit Ereigniszahlen [1, 1, 5, 5] (Schwelle 5,
        Randsumme 12): das Original unterdrueckt ALLE vier (die beiden
        Fuenfer-Zellen sind ueber die Randsumme exakt aus den beiden
        Einser-Zellen rueckrechenbar), die Mutante (kein inneres K2-Audit)
        laesst eine der beiden Fuenfer-Zellen sichtbar, weil das aeussere
        K1-Audit bei VIER gleichzeitig verdeckten Zellen nicht dieselbe
        Schaerfe erreicht.
        """
        d0, d1, d2, d3 = (_make_doc_type(facility, name) for name in ["D0", "D1", "D2", "D3"])
        _make_events(facility, client_qualified, d0, staff_user, 1)
        _make_events(facility, client_qualified, d1, staff_user, 1)
        _make_events(facility, client_qualified, d2, staff_user, 5)
        _make_events(facility, client_qualified, d3, staff_user, 5)

        report = build_external_report(facility, *report_period)

        assert all(row["suppressed"] for row in report["by_document_type"])

    def test_kontaktstufen_bleiben_ohne_randsumme_falsch_zugeordnet(self, facility, staff_user, report_period):
        """Killt ``x_build_external_report__mutmut_51`` (``margin=None`` statt
        ``published_total`` beim ``by_contact_stage``-Aufruf).

        Drei Stufen identified=6/qualified=6/anonym=3 (Schwelle 5, Randsumme
        15): das Original haelt ``identified`` sichtbar (6) und opfert
        ``qualified`` als Sekundaer-Opfer (wertunabhaengiger Zweitschluessel:
        bei Gleichstand faellt die LETZTE Stufe der festen Reihenfolge). Die
        Mutante (kein inneres K2-Audit) opfert stattdessen faelschlich
        ``identified`` und laesst ``qualified`` sichtbar.
        """
        for index in range(6):
            client = _make_client(
                facility, staff_user, contact_stage=Client.ContactStage.IDENTIFIED, pseudonym=f"ID-{index:02d}"
            )
            _make_events(facility, client, self._doc_type(facility), staff_user, 1)
        for index in range(6):
            client = _make_client(
                facility, staff_user, contact_stage=Client.ContactStage.QUALIFIED, pseudonym=f"QU-{index:02d}"
            )
            _make_events(facility, client, self._doc_type(facility), staff_user, 1)
        _make_events(facility, None, self._doc_type(facility), staff_user, 3, is_anonymous=True)

        report = build_external_report(facility, *report_period)

        assert report["by_contact_stage"]["identifiziert"] == 6
        assert report["by_contact_stage"]["qualifiziert"] is None

    def _doc_type(self, facility):
        doc_type, _created = DocumentType.objects.get_or_create(
            facility=facility, name="Kontakt", defaults={"report_key": "contact", "tally_enabled": True}
        )
        return doc_type

    def test_altersgruppen_bleiben_ohne_randsumme_teilweise_sichtbar(self, facility, staff_user, report_period):
        """Killt ``x_build_external_report__mutmut_35`` (``margin=None`` statt
        ``published_total`` beim ``by_age_cluster``-Aufruf).

        Vier Altersgruppen mit Personenzahlen [1, 1, 5, 5] (Schwelle 5,
        Randsumme 12, W=0): analoges Muster zu den Dokumenttypen — das
        Original unterdrueckt alle vier Altersgruppen, die Mutante laesst
        ``unknown`` (5) sichtbar.
        """
        doc_type = self._doc_type(facility)
        cluster_counts = [
            (Client.AgeCluster.U18, 1),
            (Client.AgeCluster.AGE_18_26, 1),
            (Client.AgeCluster.AGE_27_PLUS, 5),
            (Client.AgeCluster.UNKNOWN, 5),
        ]
        for cluster, count in cluster_counts:
            for index in range(count):
                client = _make_client(facility, staff_user, age_cluster=cluster, pseudonym=f"{cluster}-{index:02d}")
                _make_events(facility, client, doc_type, staff_user, 1)

        report = build_external_report(facility, *report_period)

        assert all(row["suppressed"] for row in report["by_age_cluster"])


class TestBuildExternalReportContactStagePersonsCollapse:
    """``persons=...`` in ``build_external_report``'s ``_suppress_stage_dict``-Aufruf.

    Killt ``x_build_external_report__mutmut_50`` (``persons=None``), ``_58``
    (``raw.get(None)``), ``_59`` (``raw.get("XXpersons_by_contact_stageXX")``)
    und ``_60`` (``raw.get("PERSONS_BY_CONTACT_STAGE")``) — alle vier
    Varianten liefern denselben Effekt: ``persons`` wird effektiv ``None``,
    ``_suppress_stage_dict`` behandelt dann JEDE Stufe als personenbedingt
    unbekannt und unterdrueckt sie fail-safe, auch die eigentlich sichere.
    """

    def test_identified_bleibt_sichtbar_wenn_persons_korrekt_gelesen_werden(self, facility, staff_user, report_period):
        doc_type = DocumentType.objects.create(
            facility=facility, name="Kontakt", report_key="contact", tally_enabled=True
        )
        for index in range(5):
            client = _make_client(
                facility, staff_user, contact_stage=Client.ContactStage.IDENTIFIED, pseudonym=f"ID-{index:02d}"
            )
            _make_events(facility, client, doc_type, staff_user, 1)
        # Eine einzige qualifizierte Person mit 6 Ereignissen: Ereigniszahl (6)
        # liegt ueber der Schwelle, die Personenzahl (1) klar darunter.
        lone_qualified = _make_client(
            facility, staff_user, contact_stage=Client.ContactStage.QUALIFIED, pseudonym="QU-lone"
        )
        _make_events(facility, lone_qualified, doc_type, staff_user, 6)

        report = build_external_report(facility, *report_period)

        assert report["by_contact_stage"]["identifiziert"] == 5
        assert report["by_contact_stage"]["qualifiziert"] is None


class TestBuildExternalReportPopulationsMetadata:
    """``metadata.populations``-Schluessel (stabiler Code fuer JSON-Konsumenten).

    Killt ``x_build_external_report__mutmut_101``/``_102`` (``total_contacts``),
    ``_103``/``_104`` (``unique_clients``) und ``_105``/``_106``
    (``by_contact_stage``) — je zwei XX-Marker-/Grossschreibungs-Varianten
    desselben verlorenen Schluessels.
    """

    def test_populations_traegt_die_dokumentierten_stabilen_codes(
        self, facility, staff_user, client_qualified, report_period
    ):
        doc_type = DocumentType.objects.create(
            facility=facility, name="Kontakt", report_key="contact", tally_enabled=True
        )
        _make_events(facility, client_qualified, doc_type, staff_user, 1)

        report = build_external_report(facility, *report_period)

        populations = report["metadata"]["populations"]
        assert populations["total_contacts"] == ALL_EVENTS
        assert populations["unique_clients"] == PERSONS
        assert populations["by_contact_stage"] == ALL_EVENTS


class TestSuppressSampleReport1StatsThresholdEdgeCase:
    """``unique``/``total``-Default (0) bei fehlendem Snapshot-Schluessel.

    Killt ``x_suppress_sample_report_1_stats__mutmut_10`` (Default 0 -> 1 fuer
    ``unique_clients``) und ``_18`` (Default 0 -> 1 fuer ``total``): bei
    Schwelle 1 (zulaessiger Facility-Wert, vgl. Bestandstest
    ``test_schwelle_1_deaktiviert_auch_den_fail_safe`` in
    ``test_external_report_service.py``) macht der falsche Default 1 aus
    „unbekannt, also unterdruecken" ein „unbekannt, aber >= 1, also
    publizieren" — K0-b-Fail-safe gebrochen.
    """

    def test_fehlende_schluessel_bleiben_bei_schwelle_1_unterdrueckt(self, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        stats = {
            "by_category": [],
            "by_age_cluster": [],
            # "unique_clients" UND "total" bewusst weggelassen (Alt-Snapshot).
        }

        out = suppress_sample_report_1_stats(facility, stats)

        assert out["unique_clients"] is None
        assert out["total"] is None


class TestSuppressSampleReport1StatsMissingKeysGraceful:
    """``by_category``/``by_age_cluster``-Default (``[]``) bei Alt-Snapshots.

    Killt ``x_suppress_sample_report_1_stats__mutmut_30``/``_32`` (Default
    ``[]`` -> ``None`` fuer ``by_category``) und ``_48``/``_50`` (dasselbe fuer
    ``by_age_cluster``): mit ``None`` statt ``[]`` wirft die anschliessende
    Iteration ``TypeError`` — das Original toleriert einen Snapshot ohne
    diese Schluessel defensiv.
    """

    def test_fehlende_listen_schluessel_werfen_keine_exception(self, facility):
        stats = {"unique_clients": 3, "total": 3}

        out = suppress_sample_report_1_stats(facility, stats)

        assert out["by_category"] == []
        assert out.get("by_age_cluster") is None or out["by_age_cluster"] == []


class TestSuppressReportStatsThresholdEdgeCase:
    """``total_contacts``/``unique_clients``-Default (0) bei Schwelle 1.

    Killt ``x_suppress_report_stats__mutmut_12`` (Default 0 -> 1 fuer
    ``total_contacts``, wirkt sich zusaetzlich auf die K3-A1-Restzeile in
    ``with_no_person_row`` aus) und ``_93`` (Default 0 -> 1 fuer
    ``unique_clients``).
    """

    def test_fehlendes_total_contacts_bleibt_bei_schwelle_1_unterdrueckt(self, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        stats = {
            "unique_clients": 5,
            "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
            "persons_by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
            "by_document_type": [],
            "by_age_cluster": [],
            # "total_contacts" bewusst weggelassen.
        }

        out = suppress_report_stats(facility, stats)

        assert out["total_contacts"] is None
        # K3-A1-Restzeile: bei korrektem Default (0) ist W=0 und faellt unter
        # Schwelle 1 -> unterdrueckt; beim falschen Default (1) waere W=1 und
        # damit >= Schwelle 1 -> faelschlich sichtbar.
        rest = next(row for row in out["by_age_cluster"] if row["cluster"] == "__no_person__")
        assert rest["count"] is None
        assert rest["suppressed"] is True

    def test_fehlendes_unique_clients_bleibt_bei_schwelle_1_unterdrueckt(self, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        stats = {
            "total_contacts": 20,
            "by_contact_stage": {"anonym": 20, "identifiziert": 0, "qualifiziert": 0},
            "persons_by_contact_stage": {"anonym": 20, "identifiziert": 0, "qualifiziert": 0},
            "by_document_type": [{"name": "Kontakt", "count": 20}],
            "by_age_cluster": [{"cluster": "27_plus", "count": 20, "persons": 20}],
            # "unique_clients" bewusst weggelassen.
        }

        out = suppress_report_stats(facility, stats)

        assert out["unique_clients"] is None


class TestSuppressReportStatsContactStageMarginAudit:
    """``margin=None`` beim ``by_contact_stage``-Aufruf in ``suppress_report_stats``.

    Killt ``x_suppress_report_stats__mutmut_66`` — Positionsvergleich der
    Mutantenkoerper bestaetigt, dass diese Mutante die ``by_contact_stage``-
    Randsumme betrifft (Quellzeile 1160), NICHT ``by_age_cluster`` (1153),
    trotz zweier identischer ``margin=published_total,``-Zeilen im
    Funktionskoerper.
    """

    def test_kontaktstufen_bleiben_ohne_randsumme_falsch_zugeordnet(self, facility):
        stats = {
            "total_contacts": 15,
            "unique_clients": 12,
            "by_contact_stage": {"anonym": 3, "identifiziert": 6, "qualifiziert": 6},
            "persons_by_contact_stage": {"anonym": 3, "identifiziert": 6, "qualifiziert": 6},
            "by_document_type": [{"name": "Kontakt", "count": 15}],
            "by_age_cluster": [{"cluster": "27_plus", "count": 15, "persons": 12}],
        }

        out = suppress_report_stats(facility, stats)

        assert out["by_contact_stage"]["identifiziert"] == 6
        assert out["by_contact_stage"]["qualifiziert"] is None


class TestSuppressReportStatsUniqueClientsOverride:
    """``out["unique_clients"]``-Hardcodierung in ``suppress_report_stats``.

    Killt ``x_suppress_report_stats__mutmut_94`` (hardcoded ``None`` statt
    ``_published_if_above(unique, threshold)``) und ``_97``
    (``_published_if_above(None, threshold)`` statt des echten Werts) —
    beide setzen ``unique_clients`` IMMER auf ``None``, auch wenn die echte
    Personenzahl klar ueber der Schwelle liegt (Utility-Regression).
    """

    def test_unique_clients_weit_ueber_schwelle_bleibt_sichtbar(self, facility):
        stats = {
            "total_contacts": 40,
            "unique_clients": 20,
            "by_contact_stage": {"anonym": 40, "identifiziert": 0, "qualifiziert": 0},
            "persons_by_contact_stage": {"anonym": 20, "identifiziert": 0, "qualifiziert": 0},
            "by_document_type": [{"name": "Kontakt", "count": 40}],
            "by_age_cluster": [{"cluster": "27_plus", "count": 40, "persons": 20}],
        }

        out = suppress_report_stats(facility, stats)

        assert out["unique_clients"] == 20


class TestSuppressStageDictContactStagePersonGatedZeroCellAtThreshold1:
    """``_suppress_stage_dict``'s ``values``-Liste steuert in ``_local_partition``
    NICHT nur die Opferwahl unter sichtbaren Kandidaten, sondern AUCH die
    Intervall-Branchwahl fuer eine bereits unterdrueckte Zelle
    (``value < threshold`` -> enges Intervall ``[lo, threshold-1]`` vs. sonst
    unbeschraenktes ``[threshold, ∞)``).

    NACHTRAG (Familienreview, Reviewer-Befund): killt
    ``x__suppress_stage_dict__mutmut_7`` (``stage_dict[key] or 0`` ->
    ``or 1``) — urspruenglich als ``equivalent`` eingestuft, weil die
    Brute-Force-Pruefung nur bei Schwelle 5 lief, wo der Effekt nicht
    sichtbar wird. Bei Schwelle 1 kippt der mutierte Wert einer echten
    Nullzelle (``0 or 1`` = 1) die Bedingung ``value < threshold``
    (``1 < 1`` ist False, waehrend ``0 < 1`` True ist): die Nullzelle
    erscheint der Mutante als unbeschraenkt-hoch statt exakt ``[0, 0]``,
    wodurch BEIDE verdeckten Zellen ein LEERES statt eines EXAKTEN
    Intervalls liefern und der Fund fuer ``qualifiziert`` verlorengeht — der
    nachgeschaltete K1-Guard (``apply_residual_guard``, laeuft in
    ``suppress_report_stats`` in jedem Fall danach) holt das nicht nach.
    Getestet ueber den vollstaendigen Suppressions-/Report-Pfad
    (``suppress_report_stats``), nicht nur den isolierten Helfer.
    """

    def test_kontaktstufe_mit_nullzelle_bleibt_bei_schwelle_1_vollstaendig_unterdrueckt(self, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        stats = {
            "total_contacts": 2,
            "unique_clients": 1,
            "by_contact_stage": {"anonym": 0, "identifiziert": 1, "qualifiziert": 1},
            "persons_by_contact_stage": {"anonym": 0, "identifiziert": 1, "qualifiziert": None},
            "by_document_type": [{"name": "Beratung", "count": 2}],
            "by_age_cluster": [{"cluster": "27_plus", "label": "27+", "count": 2, "persons": None}],
        }

        out = suppress_report_stats(facility, stats)

        assert out["by_contact_stage"] == {"anonym": None, "identifiziert": None, "qualifiziert": None}


class TestSuppressSmallAgeClusterPersonGatedZeroCellAtThreshold1:
    """``_suppress_small``'s ``values``-Liste — dieselbe Bugklasse wie
    ``_suppress_stage_dict``'s ``values`` (siehe
    ``TestSuppressStageDictContactStagePersonGatedZeroCellAtThreshold1``),
    hier ueber die K3-A1-Restzeile (``with_no_person_row``), deren Wert bei
    W=0 ebenfalls eine echte Nullzelle ist.

    NACHTRAG (eigener Nachzug im selben Durchgang wie der Reviewer-Befund,
    gleiche Ursache): killt ``x__suppress_small__mutmut_8``
    (``row.get(count_key) or 0`` -> ``or 1``) — ebenfalls urspruenglich als
    ``equivalent`` eingestuft (Brute-Force nur bei Schwelle 5). Bei Schwelle 1
    kippt die mutierte Restzeile (``0 or 1`` = 1) von der engen ``[0, 0]``-
    in die unbeschraenkte ``[1, ∞)``-Branche und macht dadurch das Intervall
    einer zweiten, gleichzeitig personenbedingt unterdrueckten Zelle leer
    statt exakt. Getestet ueber den vollstaendigen Suppressions-/Report-Pfad
    (``suppress_report_stats``), nicht nur den isolierten Helfer.
    """

    def test_altersgruppe_mit_nullzeile_bleibt_bei_schwelle_1_vollstaendig_unterdrueckt(self, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        stats = {
            "total_contacts": 7,
            "unique_clients": 2,
            "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 7},
            "persons_by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 2},
            "by_document_type": [{"name": "Beratung", "count": 7}],
            "by_age_cluster": [
                {"cluster": "27_plus", "count": 5, "persons": 0},
                {"cluster": "u18", "count": 2, "persons": 2},
            ],
        }

        out = suppress_report_stats(facility, stats)

        assert all(row["suppressed"] for row in out["by_age_cluster"])


class TestSuppressStageDictAuditDetails:
    """Direkte Unit-Tests fuer ``_suppress_stage_dict`` (K2-Audit-Feinheiten).

    Killt ``x__suppress_stage_dict__mutmut_34`` (``min_values`` 0 -> 1),
    ``_36`` (Zweitschluessel wird ``None``) und ``_37`` (Zweitschluessel-
    Vorzeichen umgekehrt).
    """

    def test_falsche_zeilen_existenz_untergrenze_ueberunterdrueckt(self):
        """``_34``: bei ``min_values=[0,...]`` (korrekt) bleibt ``anonym``
        sichtbar; bei ``min_values=[1,...]`` (Mutante) wird ``anonym``
        faelschlich sekundaer unterdrueckt (Brute-Force-verifiziertes
        Szenario: identified=0, pseudonym=2, anonym=5, Schwelle 5, margin=7).
        """
        stage_dict = {"identified": 0, "pseudonym": 2, "anonym": 5}
        persons = {"identified": 0, "pseudonym": 2, "anonym": 5}

        result = _suppress_stage_dict(stage_dict, 5, persons=persons, margin=7)

        assert result["anonym"] == 5

    def test_zweitschluessel_bestimmt_bei_gleichstand_die_letzte_stufe(self):
        """``_36``/``_37``: identified=6, qualified=6 (Gleichstand), anonym=3,
        Schwelle 5, margin=15 -> der dokumentierte Zweitschluessel opfert bei
        Gleichstand die LETZTE Stufe der festen Reihenfolge (``qualified``,
        nicht ``identified``).
        """
        stage_dict = {"identified": 6, "qualified": 6, "anonym": 3}
        persons = {"identified": 6, "qualified": 6, "anonym": 3}

        result = _suppress_stage_dict(stage_dict, 5, persons=persons, margin=15)

        assert result["identified"] == 6
        assert result["qualified"] is None


class TestAgeClusterKeyFallbackAndFormatting:
    """Direkte Unit-Tests fuer ``_age_cluster_key`` (K5-Sortierschluessel).

    Killt ``x__age_cluster_key__mutmut_5``/``_8``/``_10`` (Fallback-Index wird
    ``None`` statt der stabilen Randposition) und ``_11``/``_12``/``_13``
    (Zweitschluessel-String-Formatierung).
    """

    def test_unbekannter_cluster_bekommt_einen_sortierbaren_fallback_index(self):
        """Ein ``cluster``-Wert ausserhalb von ``Client.AgeCluster`` UND
        ausserhalb von ``NO_PERSON_CLUSTER`` muss einen INT-Index bekommen,
        sonst wirft ``sorted(..., key=_age_cluster_key)`` beim Mischen mit
        bekannten Clustern ``TypeError`` (int vs. None nicht vergleichbar).
        """
        index, _label = _age_cluster_key({"cluster": "voellig_unbekannt"})

        assert isinstance(index, int)

        # Gegenprobe: Sortierung mit einem bekannten Cluster darf nicht crashen.
        rows = [{"cluster": "voellig_unbekannt"}, {"cluster": "u18"}]
        sorted_rows = sorted(rows, key=_age_cluster_key)
        assert [row["cluster"] for row in sorted_rows] == ["u18", "voellig_unbekannt"]

    def test_zweitschluessel_traegt_den_echten_cluster_string(self):
        """``_11``/``_12``: bei einem bekannten, nicht-leeren ``cluster``-Wert
        muss der Zweitschluessel exakt dieser Wert sein (nicht ``"None"``
        oder ``""``).
        """
        _index, label = _age_cluster_key({"cluster": "u18"})

        assert label == "u18"

    def test_zweitschluessel_bei_fehlendem_cluster_ist_leerstring(self):
        """``_13``: bei ``cluster=None`` muss der Zweitschluessel ``""`` sein,
        nicht der fabrizierte Platzhalter ``"XXXX"``.
        """
        _index, label = _age_cluster_key({"cluster": None})

        assert label == ""


class TestSuppressSmallPersonGating:
    """``person_gated`` in ``_suppress_small`` (K1-Personenluecken-Ausnahme).

    Killt ``x__suppress_small__mutmut_21`` (``person_gated=None`` statt der
    echten Berechnung) und ``_32`` (invertierte Bedingung ``not in`` statt
    ``in``) — beide deaktivieren die Ausnahme „Pin auf >= k MIT
    Personenkriterium" in ``disclosure.findings`` und lassen eine exakt
    rekonstruierbare, personenbedingt gefallene Zeile faelschlich sichtbar.
    """

    def test_personenbezogene_exakte_rueckrechnung_wird_erkannt(self):
        """Brute-Force-verifiziertes Szenario: drei Zeilen mit (count,
        persons) = (5,0), (5,0), (5,5), Schwelle 5, margin=15. Die ersten
        beiden sind personenbedingt unterdrueckt; die dritte (Wert 5) ist ueber
        die Randsumme exakt rekonstruierbar UND traegt ein Personenkriterium
        -> muss ebenfalls unterdrueckt werden.
        """
        rows = [
            {"name": "r0", "count": 5, "persons": 0},
            {"name": "r1", "count": 5, "persons": 0},
            {"name": "r2", "count": 5, "persons": 5},
        ]

        result = _suppress_small(rows, 5, margin=15, order="input")

        assert all(row["suppressed"] for row in result)


class TestSuppressSmallestVisibleVictimSelection:
    """Direkte Unit-Tests fuer ``_suppress_smallest_visible`` (Eskalationsleiter).

    Killt ``x__suppress_smallest_visible__mutmut_35``/``_37``/``_38``
    (falsche/abstuerzende Opferwahl bei ``kind="rows"``), ``_40`` (``""``
    statt ``None`` als unterdrueckter Wert), ``_11`` (falscher Rueckgabewert
    bei ``kind="stages"`` ohne sichtbare Stufe) und ``_19`` (falscher
    Rueckgabewert nach erfolgreicher Stufen-Unterdrueckung).
    """

    def test_rows_opfert_die_kleinste_sichtbare_zelle_nicht_die_erste(self):
        spec = _Breakdown("by_document_type", "total_contacts", "input")
        report = {
            "by_document_type": [
                {"name": "a", "count": 9, "suppressed": False},
                {"name": "b", "count": 1, "suppressed": False},
                {"name": "c", "count": 5, "suppressed": False},
            ]
        }

        changed = _suppress_smallest_visible(report, spec)

        assert changed is True
        victim = next(row for row in report["by_document_type"] if row["suppressed"])
        assert victim["name"] == "b"
        assert victim["count"] is None

    def test_stages_ohne_sichtbare_stufe_meldet_keinen_erfolg(self):
        spec = _Breakdown("by_contact_stage", "total_contacts", "input", kind="stages")
        report = {"by_contact_stage": {"a": None, "b": None}}

        changed = _suppress_smallest_visible(report, spec)

        assert changed is False
        assert report["by_contact_stage"] == {"a": None, "b": None}

    def test_stages_mit_sichtbarer_stufe_meldet_erfolg(self):
        spec = _Breakdown("by_contact_stage", "total_contacts", "input", kind="stages")
        report = {"by_contact_stage": {"a": 5, "b": None}}

        changed = _suppress_smallest_visible(report, spec)

        assert changed is True
        assert report["by_contact_stage"]["a"] is None


class TestApplyResidualGuardPersonPartition:
    """Direkte Unit-Tests fuer ``apply_residual_guard`` (Personen-Partition).

    Killt ``x_apply_residual_guard__mutmut_1``/``_6``/``_7``/``_8``/``_9``/
    ``_15`` — alle sechs Varianten verhindern, dass die Personen-Partition
    (Σ ``persons`` = ``unique_clients``) ueberhaupt berechnet bzw. an
    ``_published_partitions`` weitergegeben wird. Ohne sie prueft das
    K1-Audit die Randsumme ``unique_clients`` nie gegen die Alterstabelle —
    ein exakt rekonstruierbarer Wert bleibt faelschlich sichtbar.
    """

    def test_unique_clients_wird_bei_exakter_rueckrechnung_fallengelassen(self):
        """Brute-Force-verifiziertes Szenario: zwei Altersgruppen mit
        (count, persons) = (0, 0) und (0, 2), total_contacts=0,
        unique_clients=2 (noch nicht fallengelassen). Ueber die a-priori-
        Schranken der Personen-Partition (lo=1, hi=Schwelle-1 je Zelle) ist
        die publizierte Randsumme unique_clients=2 exakt als (1, 1)
        rekonstruierbar -> das Original loescht sie, jede der sechs Mutanten
        (die Personen-Partition entfaellt komplett) laesst sie stehen.
        """
        breakdowns = (_Breakdown("by_age_cluster", "total_contacts", "age_cluster", reveals_person_gap=True),)
        report = {
            "total_contacts": 0,
            "by_age_cluster": [
                {"cluster": "a0", "count": None, "suppressed": True},
                {"cluster": "a1", "count": None, "suppressed": True},
            ],
            "unique_clients": 2,
        }
        age_rows = [
            {"cluster": "a0", "count": 0, "persons": 0},
            {"cluster": "a1", "count": 0, "persons": 2},
        ]

        apply_residual_guard(report, 5, breakdowns, age_rows=age_rows)

        assert report["unique_clients"] is None


class TestSuppressSingleMarginAudit:
    """Direkte Unit-Tests fuer ``suppress_single_margin`` (K-Anon fuer
    Backends mit genau einer Randsumme, Refs #1835).

    Killt ``x_suppress_single_margin__mutmut_18`` (``margin=None`` beim
    inneren ``_suppress_small``-Aufruf: das aeussere K1-Audit kompensiert bei
    VIER gleichzeitig verdeckten Zellen nicht vollstaendig) und ``_25``
    (``threshold=None`` beim ``apply_residual_guard``-Aufruf: sobald das
    K1-Audit eine Zelle mit ``lower >= threshold`` pruefen muss, wirft der
    Vergleich ``int >= None`` einen ``TypeError``).
    """

    def _make_result(self, counts):
        records = [{"name": f"r{i}", "count": c} for i, c in enumerate(counts)]
        return DatasetResult(mode="rows", records=records, summary={"total": sum(counts)}, completeness=[])

    def _breakdown(self):
        from core.services.dashboard.external_report import ORDER_INPUT

        return _Breakdown(key="records", margin_key="total", order=ORDER_INPUT)

    def _field_specs(self):
        return [FieldSpec("name", "Name"), FieldSpec("count", "Anzahl")]

    def test_vier_zellen_bleiben_ohne_inneres_audit_teilweise_sichtbar(self, facility):
        result = self._make_result([0, 2, 5, 5])

        records, _summary = suppress_single_margin(
            facility, result, breakdown=self._breakdown(), field_specs=self._field_specs()
        )

        assert all(row["count"] is None for row in records)

    def test_kaputte_schwelle_im_aeusseren_audit_wirft_keine_exception(self, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 2})
        result = self._make_result([1, 1])

        records, _summary = suppress_single_margin(
            facility, result, breakdown=self._breakdown(), field_specs=self._field_specs()
        )

        assert all(row["count"] is None for row in records)


class TestWithNoPersonRowArithmetic:
    """Direkte Unit-Tests fuer ``with_no_person_row`` (K3-A1-Restzeile).

    Killt ``x_with_no_person_row__mutmut_13`` (``or 0`` -> ``or 1`` beim
    Aufsummieren legitimer Nullzeilen), ``_19`` (``max(0,...)`` ->
    ``max(1,...)`` bei negativem Restwert) und ``_22`` (``total_events or 0``
    -> ``or 1`` bei leerem Zeitraum).
    """

    def test_legitime_nullzeile_wird_nicht_als_eins_gezaehlt(self):
        rows = [{"cluster": "u18", "count": 0}, {"cluster": "18_26", "count": 3}]

        result = with_no_person_row(rows, 10)

        rest = next(row for row in result if row["cluster"] == "__no_person__")
        assert rest["count"] == 7

    def test_negativer_restwert_wird_auf_null_geklemmt(self):
        rows = [{"cluster": "u18", "count": 5}, {"cluster": "18_26", "count": 3}]

        result = with_no_person_row(rows, 5)

        rest = next(row for row in result if row["cluster"] == "__no_person__")
        assert rest["count"] == 0

    def test_leerer_zeitraum_erfindet_keine_restperson(self):
        result = with_no_person_row([], 0)

        rest = next(row for row in result if row["cluster"] == "__no_person__")
        assert rest["count"] == 0
