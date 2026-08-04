"""Empfaenger-Inferenz ueber die publizierte Sicht (K1/K2, Refs #1770).

Reiner Rechenkern ohne Datenbank: :mod:`core.services.dashboard.disclosure`
rechnet genau das nach, was ein Empfaenger eines Berichts-Artefakts aus den
publizierten Groessen ableiten kann — publizierte Randsumme, sichtbare Zellen,
Zeilen-Existenz (jede gelistete Zeile traegt mindestens einen Fall) und die
a-priori-Schranken unterdrueckter Zellen.

Die Tests pinnen die geschlossene Intervallformel: fuer eine eindimensionale
Tabelle mit exakt publizierter Randsumme ist die Machbarkeitsspanne je
unterdrueckter Zelle geschlossen berechenbar (keine NP-harte Optimierung), und
jeder ganzzahlige Wert im berechneten Intervall ist auch erreichbar.
"""

from __future__ import annotations

import pytest

from core.services.dashboard.disclosure import (
    MIN_FEASIBLE_SPAN,
    Cell,
    Partition,
    feasible_spans,
    findings,
)


def _rows(*specs: tuple[str, int | None, int, int | None]) -> tuple[Cell, ...]:
    return tuple(Cell(key=key, published=published, lo=lo, hi=hi) for key, published, lo, hi in specs)


class TestMachbarkeitsspanne:
    """Die Spanne ist die Anzahl der Werte, die einer unterdrueckten Zelle
    ueber alle mit der publizierten Sicht konsistenten Welten offenstehen."""

    def test_einzige_unterdrueckte_zelle_ist_exakt_bestimmt(self):
        """s = 1: die Zelle IST das Residuum — Spanne 1, egal welche Schranken."""
        partition = Partition(
            name="by_document_type",
            margin_key="total_contacts",
            margin=21,
            cells=_rows(("A", 10, 1, None), ("B", 8, 1, None), ("C", None, 1, 4)),
        )
        assert feasible_spans([partition]) == {("by_document_type", "C"): 1}

    def test_zwei_unterdrueckte_zellen_lassen_beide_werte_offen(self):
        partition = Partition(
            name="t",
            margin_key="total",
            margin=20,
            cells=_rows(("A", 14, 1, None), ("B", None, 1, 4), ("C", None, 1, 4)),
        )
        # Residuum 6 auf zwei Zellen aus [1, 4]: B in [2, 4], C in [2, 4].
        assert feasible_spans([partition]) == {("t", "B"): 3, ("t", "C"): 3}

    def test_alle_unterdrueckten_zellen_am_minimum_sind_gepinnt(self):
        """Residuum = Summe der Untergrenzen ⇒ jede Zelle liegt fest."""
        partition = Partition(
            name="t",
            margin_key="total",
            margin=12,
            cells=_rows(("A", 10, 1, None), ("B", None, 1, 4), ("C", None, 1, 4)),
        )
        assert feasible_spans([partition]) == {("t", "B"): 1, ("t", "C"): 1}

    def test_unbeschraenkte_gegenzelle_oeffnet_die_untergrenze(self):
        """Eine Zelle ohne Obergrenze (Sekundaer-Opfer/personenbedingt
        unterdrueckt) laesst der Gegenzelle ihre volle a-priori-Spanne."""
        partition = Partition(
            name="t",
            margin_key="total",
            margin=40,
            cells=_rows(("gross", None, 5, None), ("klein", None, 1, 4)),
        )
        spans = feasible_spans([partition])
        assert spans[("t", "klein")] == 4  # [1, 4] bleibt voll offen
        assert spans[("t", "gross")] == 4  # [36, 39]

    def test_zeilen_existenz_ist_die_untergrenze(self):
        """Jede gelistete Zeile traegt >= 1 Fall — ohne diese Untergrenze waere
        die Spanne der Gegenzelle groesser, als sie tatsaechlich ist."""
        eng = Partition(name="t", margin_key="total", margin=4, cells=_rows(("A", None, 1, 4), ("B", None, 1, 4)))
        weit = Partition(name="t", margin_key="total", margin=4, cells=_rows(("A", None, 0, 4), ("B", None, 0, 4)))
        assert feasible_spans([eng])[("t", "A")] == 3  # [1, 3], weil B >= 1
        assert feasible_spans([weit])[("t", "A")] == 5  # [0, 4], wenn B auch 0 sein darf

    def test_ohne_publizierte_randsumme_ist_nichts_ableitbar(self):
        partition = Partition(
            name="by_age_cluster",
            margin_key=None,
            margin=None,
            cells=_rows(("u18", None, 1, 4), ("27_plus", 30, 1, None)),
        )
        assert feasible_spans([partition]) == {("by_age_cluster", "u18"): None}

    def test_unterdrueckte_randsumme_ist_aus_vollstaendig_sichtbaren_zellen_ableitbar(self):
        """Cross-Partition-Inferenz: eine zweite Aufschluesselung derselben
        Randsumme, die vollstaendig sichtbar ist, stellt die Randsumme wieder
        her — das Unterdruecken der Randsumme allein schuetzt dann nicht."""
        offen = Partition(
            name="by_document_type",
            margin_key="total_contacts",
            margin=None,
            cells=_rows(("A", 12, 1, None), ("B", 9, 1, None)),
        )
        verdeckt = Partition(
            name="by_contact_stage",
            margin_key="total_contacts",
            margin=None,
            cells=_rows(("anonym", 20, 0, None), ("identifiziert", None, 0, 4), ("qualifiziert", None, 0, 4)),
        )
        spans = feasible_spans([offen, verdeckt])
        # 21 - 20 = 1 auf zwei Zellen aus [0, 4] ⇒ beide auf {0, 1} eingeengt.
        assert spans[("by_contact_stage", "identifiziert")] == 2
        assert spans[("by_contact_stage", "qualifiziert")] == 2


class TestBefundKriterium:
    """Nicht jeder exakte Pin ist ein Datenschutz-Befund — aber jeder Pin auf
    eine Kleinstfallzahl ist einer."""

    def test_pin_unter_der_schwelle_ist_ein_befund(self):
        partition = Partition(
            name="t", margin_key="total", margin=13, cells=_rows(("A", 10, 1, None), ("C", None, 1, 4))
        )
        assert findings([partition], threshold=5) == [("t", "C")]

    def test_pin_oberhalb_der_schwelle_ist_kein_befund(self):
        """Ein Sekundaer-Opfer, dessen Wert sich exakt zurueckrechnen laesst, ist
        keine Kleinstfallzahl: k-Anonymitaet schuetzt Merkmalskombinationen mit
        weniger als k Faellen, nicht jede Zahl, die je unterdrueckt wurde."""
        partition = Partition(
            name="t", margin_key="total", margin=30, cells=_rows(("A", 10, 1, None), ("B", None, 5, None))
        )
        assert findings([partition], threshold=5) == []

    def test_person_gegateter_pin_ist_immer_ein_befund(self):
        """Traegt die Aufschluesselung ein Personenkriterium, verraet schon der
        exakte Ereigniswert die Personenluecke: der Empfaenger sieht, dass die
        Zelle trotz >= k Ereignissen unterdrueckt wurde, also personenbedingt —
        und damit, dass hinter ihr weniger als k Personen stehen."""
        partition = Partition(
            name="by_contact_stage",
            margin_key="total_contacts",
            margin=30,
            cells=_rows(("anonym", 10, 0, None), ("qualifiziert", None, 5, None)),
            reveals_person_gap=True,
        )
        assert findings([partition], threshold=5) == [("by_contact_stage", "qualifiziert")]

    def test_pin_auf_null_ist_kein_befund(self):
        """Ein leerer Bucket schuetzt niemanden — „keine anonymen Kontakte im
        Zeitraum" ist die Abwesenheit von Faellen, keine Kleinstfallzahl."""
        partition = Partition(
            name="by_contact_stage",
            margin_key="total_contacts",
            margin=31,
            cells=_rows(("anonym", None, 0, 4), ("identifiziert", None, 0, 4), ("qualifiziert", 31, 0, None)),
            reveals_person_gap=True,
        )
        assert feasible_spans([partition])[("by_contact_stage", "anonym")] == 1
        assert findings([partition], threshold=5) == []

    def test_unerfuellbares_modell_ist_kein_befund(self):
        """Leeres Intervall ⇒ die Aufschluesselung partitioniert ihre Randsumme
        nicht exakt (driftender Snapshot-Merge). Bei konsistenten Daten ist der
        Fall unmoeglich, weil die wahre Welt immer alle Schranken erfuellt — und
        wo er auftritt, rechnet auch der Empfaenger ins Leere."""
        partition = Partition(
            name="by_document_type",
            margin_key="total_contacts",
            margin=40,  # Zeilen summieren sich nur auf 37
            cells=_rows(("Notiz", 30, 1, None), ("Beratung", None, 1, 4), ("Aufnahme", None, 1, 4)),
        )
        assert feasible_spans([partition])[("by_document_type", "Beratung")] == 0
        assert findings([partition], threshold=5) == []

    def test_ohne_randsumme_gibt_es_keine_befunde(self):
        partition = Partition(name="by_age_cluster", margin_key=None, margin=None, cells=_rows(("u18", None, 1, 4)))
        assert findings([partition], threshold=5) == []


class TestSpannenSchwelle:
    def test_die_geforderte_mindestspanne_ist_zwei(self):
        """Produktentscheid zu K2 (#1770): „Spanne >= 2" statt „Spanne >= k-1" —
        mindestens zwei offene Werte, also kein exakter Pin."""
        assert MIN_FEASIBLE_SPAN == 2


class TestIntervallformelIstVollstaendig:
    """Gegenprobe per Brute-Force: die geschlossene Formel darf keinen Wert
    ausschliessen, den eine konsistente Welt erreicht, und keinen zulassen, den
    keine erreicht."""

    @pytest.mark.parametrize("margin", [5, 8, 11, 14])
    def test_formel_deckt_sich_mit_der_aufzaehlung(self, margin):
        bounds = [(1, 4), (1, 4), (5, 9)]
        cells = _rows(*[(f"c{i}", None, lo, hi) for i, (lo, hi) in enumerate(bounds)])
        partition = Partition(name="t", margin_key="total", margin=margin, cells=cells)

        erreichbar: list[set[int]] = [set() for _ in bounds]
        for a in range(bounds[0][0], bounds[0][1] + 1):
            for b in range(bounds[1][0], bounds[1][1] + 1):
                c = margin - a - b
                if bounds[2][0] <= c <= bounds[2][1]:
                    for index, value in enumerate((a, b, c)):
                        erreichbar[index].add(value)

        spans = feasible_spans([partition])
        for index in range(len(bounds)):
            assert spans[("t", f"c{index}")] == len(erreichbar[index]), (
                f"Zelle c{index}: Formel {spans[('t', f'c{index}')]} vs. Aufzaehlung {sorted(erreichbar[index])}"
            )
