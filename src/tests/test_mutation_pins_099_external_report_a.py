"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — ``core.services.dashboard.external_report``,
Batch ``external_report-a`` (Vollmutation-Survivor-Triage, 58 Survivors in
``_person_partition_cells``, ``_local_partition``, ``_published_partitions``).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-external_report-a.json`` (Triage
``reports/m-scratch/pins-external_report-a/triage-099-external_report-a.json``).

Alle drei Funktionen bilden den **Empfaenger-Blick** der K-Anonymitaets-Guards
(K1-Fixpunkt-Guard, K2-Machbarkeits-Audit, Refs #1770): sie bauen die
``Cell``/``Partition``-Objekte, gegen die ``disclosure.findings()`` rechnet.
Alle Survivors dieser Datei mutieren entweder ``Cell.key``/``Cell.lo``/
``Cell.hi`` (die a-priori-Schranken, mit denen der Guard eine unterdrueckte
Kleinstfallzahl zurueckrechnet) oder ``Partition.name``/``Partition.margin_key``/
``Partition.margin``/``Partition.reveals_person_gap``. Je nach Zweig ist die
Wirkung eine echte Datenschutz-Luecke (verlorene Schranke, verlorene
Randsummen-Aufloesung, verlorenes Personenluecken-Flag) oder ein pinnbarer,
aber fachlich folgenloser Bezeichner (``_local_partition`` baut ihre
``Partition`` ausschliesslich fuer den EINEN, selbstreferenziellen Aufruf
``findings([partition])`` in ``_audit_feasible_span`` — dort zaehlt nur die
Nichtleere des Ergebnisses, ``name``/``margin_key`` werden nirgends
ausgewertet; trotzdem sind sie Teil des Rueckgabewerts einer Funktion mit
Bestandsdirekttests und werden im Zweifel gepinnt statt als aequivalent
gewertet, siehe Triage-Begruendung).

Empirisch verifiziert (nicht nur aus dem Diff geraten, Lehre 137): jede
Pin-Idee wurde gegen eine aus dem Mutantenbaum extrahierte Kopie der
jeweiligen ``x__<funktion>__mutmut_<n>``-Funktion nachgerechnet (Scratch-Diff,
nicht Teil dieses Commits) und die Erwartungswerte von Hand gegen den
Original-Quelltext nachvollzogen.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``. Alle drei getesteten
Funktionen sind reine Python-Funktionen ohne DB-Zugriff — ``django_db`` ist
hier ausschliesslich wegen der einheitlichen Fixture-Vorgabe fuer Pin-Dateien gesetzt, nicht weil ein Test ihn
braucht.
"""

from __future__ import annotations

import pytest

from core.services.dashboard.disclosure import Cell
from core.services.dashboard.external_report import (
    _PERSON_PARTITION,
    NO_PERSON_CLUSTER,
    ORDER_DOCUMENT_TYPE,
    ORDER_INPUT,
    PERSON_MARGIN_KEY,
    PERSONS_KEY,
    _Breakdown,
    _local_partition,
    _person_partition_cells,
    _published_partitions,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestPersonPartitionCells:
    """``_person_partition_cells()`` — Zellen der Personen-Partition Σ persons = unique_clients (K0-b)."""

    def test_unknown_below_and_at_threshold_rows_get_the_right_bounds(self):
        """Killt folgende Survivors ueber drei Zeilen (unbekannt / unter Schwelle / genau an der
        Schwelle):

        ``x__person_partition_cells__mutmut_3``/``_4`` (``persons`` wird fest auf ``None``
        gesetzt bzw. per ``row.get(None)`` gelesen — eine Zeile mit bekannter Personenzahl fiele
        faelschlich in den Unbekannt-Zweig), ``_7``/``_8``/``_13``/``_14`` (Unbekannt-Zweig:
        ``key``/``lo`` falsch), ``_15`` (``<=`` statt ``<`` an der Schwelle — falscher Zweig bei
        Gleichstand), ``_17``/``_18``/``_19``/``_24``/``_25``/``_26``/``_27`` (Unter-Schwelle-Zweig:
        ``key``/``lo``/``hi`` falsch), ``_23`` (Unter-Schwelle-Zweig verliert ``hi`` ganz — der
        Cell-Default ``None`` deckt sich NICHT mit dem Originalwert ``threshold - 1``),
        ``_29``/``_33``/``_35`` (Ab-Schwelle-Zweig: ``key``/``lo`` falsch; ``_33`` verliert ``lo``
        ganz — der Cell-Default ``1`` deckt sich NICHT mit dem Originalwert ``threshold``).
        """
        threshold = 5
        rows = [
            {},  # Index 0: keine Personenzahl bekannt (Alt-Snapshot) -> Unbekannt-Zweig
            {PERSONS_KEY: 4},  # Index 1: unter der Schwelle -> [1, threshold-1]
            {PERSONS_KEY: 5},  # Index 2: GENAU an der Schwelle -> Ab-Schwelle-Zweig, nicht "<"
        ]

        cells = _person_partition_cells(rows, threshold)

        assert cells[0] == Cell(key="0", published=None, lo=1, hi=None)
        assert cells[1] == Cell(key="1", published=None, lo=1, hi=4)
        assert cells[2] == Cell(key="2", published=None, lo=5, hi=None)


class TestLocalPartitionCellFields:
    """``_local_partition()`` — Empfaenger-Sicht einer Tabelle mit echter Opfer-Herkunft (K2)."""

    def test_visible_and_primary_cell_carry_their_own_key_and_lo(self):
        """Killt: ``x__local_partition__mutmut_13``/``_15``/``_18``/``_20`` (sichtbare Zelle:
        ``key``/``lo`` falsch bzw. ``lo`` faellt auf den Cell-Default ``1`` statt des uebergebenen
        ``min_value``), ``_23``/``_28``/``_30`` (primaer unterdrueckte Zelle: ``key``/``lo``
        falsch bzw. ``lo`` faellt auf den Default ``1`` statt ``min_value`` — die Bestandstests in
        ``test_external_report_service.py`` nutzen ausnahmslos ``min_values=[1, 1, 1]`` und
        uebersehen genau diesen Default-Treffer, hier deshalb bewusst ``min_values=[3, 2]``), sowie
        ``_50``/``_60``/``_61``/``_62``/``_63`` (Partition-Bezeichner ``name``/``margin_key`` —
        Rueckgabewert, im aktuellen Aufrufer ohne Fachwirkung, aber direkt pinnbar).
        """
        partition = _local_partition(
            [7, 2],
            flags=[False, True],
            victims=[False, False],
            threshold=5,
            margin=9,
            min_values=[3, 2],
            person_gated=False,
        )

        assert partition.cells[0] == Cell(key="0", published=7, lo=3, hi=None)
        assert partition.cells[1] == Cell(key="1", published=None, lo=2, hi=4)
        assert partition.name == "local"
        assert partition.margin_key == "local"

    def test_secondary_victim_cell_key_matches_its_index(self):
        """Killt ``x__local_partition__mutmut_34``/``_41`` (Sekundaer-Opfer-Zweig: ``key`` falsch —
        die Bestandstests pruefen dort nur ``lo``/``hi``, nie ``key``)."""
        partition = _local_partition(
            [12, 6, 2],
            flags=[False, True, True],
            victims=[False, True, False],
            threshold=5,
            margin=20,
            min_values=[1, 1, 1],
            person_gated=False,
        )

        opfer = partition.cells[1]
        assert opfer.key == "1"
        assert (opfer.lo, opfer.hi) == (5, 12)

    def test_person_suppressed_non_victim_cell_key_and_lo(self):
        """Killt ``x__local_partition__mutmut_43``/``_49`` (else-Zweig: ``key`` falsch) und ``_47``
        (``lo`` faellt auf den Cell-Default ``1`` statt ``threshold`` — die K0-b-Untergrenze
        ``[threshold, unendlich)`` geht verloren)."""
        partition = _local_partition(
            [9],
            flags=[True],
            victims=[False],
            threshold=5,
            margin=9,
            min_values=[1],
            person_gated=True,
        )

        assert partition.cells[0] == Cell(key="0", published=None, lo=5, hi=None)


class TestPublishedPartitionsBreakdownCells:
    """``_published_partitions()`` — Empfaenger-Sicht ueber alle Tabellen eines Artefakts (K1)."""

    def test_stages_breakdown_cells_and_person_gap_flag(self):
        """Killt: ``x__published_partitions__mutmut_13``/``_20`` (stages-Zweig: ``key`` falsch),
        ``_18``/``_21`` (``lo`` falsch bzw. faellt auf den Cell-Default ``1`` statt ``0`` — Stufen
        mit festen Schluesseln duerfen mit ``0`` existieren), sowie ``_43``/``_48``
        (``reveals_person_gap`` wird ``None`` bzw. faellt auf den Partition-Default ``False`` —
        der K1-Guard uebersaehe dann eine Personenluecke bei ``by_contact_stage``/
        ``by_age_cluster``, siehe ``disclosure.findings()``)."""
        breakdown = _Breakdown(
            key="by_contact_stage",
            margin_key="total_contacts",
            order=ORDER_INPUT,
            kind="stages",
            reveals_person_gap=True,
        )
        report = {"by_contact_stage": {"identifiziert": 7, "anonym": 3}, "total_contacts": 10}

        partitions = _published_partitions(report, (breakdown,), person_cells=())

        assert len(partitions) == 1
        assert partitions[0].cells[0] == Cell(key="anonym", published=3, lo=0, hi=None)
        assert partitions[0].cells[1] == Cell(key="identifiziert", published=7, lo=0, hi=None)
        assert partitions[0].reveals_person_gap is True

    def test_rows_breakdown_cells_key_lo_and_suppressed_lookup(self):
        """Killt: ``x__published_partitions__mutmut_25``/``_31`` (rows-Zweig: ``key`` falsch),
        ``_30`` (``lo`` faellt auf den Cell-Default ``1`` statt ``_row_min_value(row)`` — fuer die
        K3-A1-Restzeile ist das ``0``), sowie ``_32``/``_33``/``_34`` (die
        ``"suppressed"``-Markierung wird nie gefunden — eine tatsaechlich unterdrueckte Zeile
        publizierte ihren Rohwert statt ``None``, ein Datenschutz-Leck)."""
        breakdown = _Breakdown(key="by_document_type", margin_key="total_contacts", order=ORDER_DOCUMENT_TYPE)
        report = {
            "by_document_type": [
                {"count": 9, "suppressed": False},
                {"count": 4, "suppressed": True, "cluster": NO_PERSON_CLUSTER},
            ],
            "total_contacts": 9,
        }

        partitions = _published_partitions(report, (breakdown,), person_cells=())

        assert partitions[0].cells[0] == Cell(key="0", published=9, lo=1, hi=None)
        assert partitions[0].cells[1] == Cell(key="1", published=None, lo=0, hi=None)


class TestPublishedPartitionsPersonPartition:
    """``_published_partitions()`` — die zusaetzliche Personen-Partition Σ persons = unique_clients."""

    def test_person_partition_name_margin_key_and_margin(self):
        """Killt ``x__published_partitions__mutmut_50``/``_51``/``_52``/``_58``: ``name``/
        ``margin_key``/``margin`` der Personen-Partition. ``_51`` ist der schaerfste Fund —
        ``margin_key=None`` laesst ``disclosure._resolved_margins()`` die Personen-Randsumme NIE
        aufloesen (``if partition.margin_key is None: continue``), der K1-Guard prueft die
        Personen-Partition dann ueberhaupt nicht mehr."""
        person_cells = (Cell(key="0", published=None, lo=1, hi=4),)

        partitions = _published_partitions({"unique_clients": 12}, (), person_cells=person_cells)

        assert len(partitions) == 1
        assert partitions[0].name == _PERSON_PARTITION
        assert partitions[0].margin_key == PERSON_MARGIN_KEY
        assert partitions[0].margin == 12
