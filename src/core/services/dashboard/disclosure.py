"""Empfaenger-Inferenz ueber die publizierte Sicht (K1/K2, Refs #1770).

Reiner Rechenkern der Disclosure-Analyse aus
[docs/security-notes.md](../../../../docs/security-notes.md) § Disclosure-Analyse
der Berichts-Artefakte: er rechnet nach, was ein Empfaenger aus einem
Berichts-Artefakt ableiten kann, und ist damit die gemeinsame Grundlage von

* **K2** — dem Machbarkeits-Audit der Sekundaer-Suppression („Spanne >= 2"), und
* **K1** — dem Fixpunkt-Guard gegen Randsummen-Rueckrechnung.

**Modell.** Eine :class:`Partition` ist eine publizierte Aufschluesselung, die
ihre Randsumme **exakt** partitioniert (``Event.document_type`` ist NOT NULL,
also ``total_contacts`` = Σ ``by_document_type``; ``Client.ContactStage`` hat
genau zwei Werte, also anonym + identifiziert + qualifiziert =
``total_contacts``; Σ ``persons``(``by_age_cluster``) = ``unique_clients``).
Jede :class:`Cell` traegt entweder ihren publizierten Wert oder — wenn sie
unterdrueckt ist — die a-priori-Schranken, die der Empfaenger ueber sie kennt:

* ``lo`` — die **Zeilen-Existenz**: eine gelistete Zeile entsteht aus einem
  GROUP BY und traegt daher mindestens einen Fall (``lo = 1``). Aufschluesselungen
  mit **festen** Schluesseln (``by_contact_stage``) emittieren dagegen auch
  Nullwerte, dort ist ``lo = 0``.
* ``hi`` — die Obergrenze, ``None`` = unbeschraenkt. Eine ereignisbedingt
  unterdrueckte Zelle liegt bei ``<= k-1``; eine personenbedingt unterdrueckte
  oder als Sekundaer-Opfer gefallene Zelle **nicht** (K0-b hat die alte Annahme
  „unterdrueckt ⇒ Wert <= k-1" aufgehoben) — sie ist nach unten durch *k*
  beschraenkt und nach oben offen.

**Angreifermodell.** Die Schranken werden aus der *tatsaechlichen* Herkunft der
Unterdrueckung gebildet, nicht aus dem, was ein realer Empfaenger sicher weiss.
Der reale Empfaenger kann Primaer- und Sekundaer-Unterdrueckung nicht
auseinanderhalten; sein Wissen ist die *Vereinigung* ueber beide Hypothesen und
damit **weiter** als das hier modellierte. Ein staerkerer Angreifer erzeugt
engere Intervalle und damit hoechstens *mehr* Unterdrueckung — die Richtung ist
bewusst konservativ.

**Warum geschlossen berechenbar:** fuer eine eindimensionale Tabelle mit einer
Summenbedingung und Box-Schranken ist die Projektion des zulaessigen Polytops
auf jede Koordinate wieder ein Intervall, und jeder ganzzahlige Wert darin ist
erreichbar. Es braucht also keine Aufzaehlung und erst recht keine Optimierung
(``TestIntervallformelIstVollstaendig`` in ``test_disclosure_inference.py``
prueft die Formel gegen eine Brute-Force-Aufzaehlung nach).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence

# K2-Produktentscheid (#1770): eine unterdrueckte Zelle gilt als geschuetzt,
# sobald ihr mindestens ZWEI Werte offenstehen. Die Analyse hatte auch die
# strengere Variante „Spanne >= k-1" vermessen; sie unterdrueckt in 13,5 % der
# Welten mehr Zellen, ohne im geprueften Raum weniger Leaks zu hinterlassen.
MIN_FEASIBLE_SPAN = 2

# Harte Obergrenze der Fixpunkt-Iteration. Die echte Schranke ist die Zahl der
# Zellen: jeder Schritt unterdrueckt mindestens eine weitere Zelle oder eine
# Randsumme und ist damit monoton und endlich. Diese Konstante ist die zweite
# Sicherung gegen eine kuenftige Erweiterung, die die Monotonie verliert —
# deshalb bewusst weit ueber jeder realistischen Tabellenbreite (eine
# Einrichtung mit 256 Dokumenttypen im Zeitraum ist keine, die noch
# k-anonymisierte Berichte braucht). Wird sie erreicht, faellt fail-safe die
# ganze Aufschluesselung.
MAX_GUARD_ITERATIONS = 256


@dataclasses.dataclass(frozen=True)
class Cell:
    """Eine Zelle der publizierten Sicht.

    ``published`` ist der Wert, den der Empfaenger liest — ``None`` heisst
    unterdrueckt. ``lo``/``hi`` sind die a-priori-Schranken, die fuer
    unterdrueckte Zellen gelten; ``hi=None`` bedeutet „nach oben offen".
    """

    key: str
    published: int | None
    lo: int = 1
    hi: int | None = None


@dataclasses.dataclass(frozen=True)
class Partition:
    """Eine Aufschluesselung samt der Randsumme, die sie exakt partitioniert.

    ``margin`` ist die **publizierte** Randsumme (``None`` = unterdrueckt);
    ``margin_key`` benennt sie, damit zwei Aufschluesselungen derselben
    Randsumme (``by_document_type`` und ``by_contact_stage`` teilen sich
    ``total_contacts``) sich gegenseitig informieren koennen.
    ``margin_key=None`` heisst: dieses Artefakt publiziert fuer diese
    Aufschluesselung ueberhaupt keine Randsumme.

    ``reveals_person_gap`` markiert Aufschluesselungen, deren Zellen **auch
    personenbedingt** unterdrueckt werden koennen (K0-b). Dort ist selbst ein
    exakter Pin oberhalb der Schwelle ein Befund: der Empfaenger sieht dann,
    dass eine Zelle mit >= k Ereignissen unterdrueckt wurde, und schliesst
    daraus auf < k Personen.
    """

    name: str
    margin_key: str | None
    margin: int | None
    cells: tuple[Cell, ...]
    reveals_person_gap: bool = False


def _resolved_margins(partitions: Sequence[Partition]) -> dict[str, int | None]:
    """Randsummen, die der Empfaenger kennt — publiziert ODER rekonstruierbar.

    Eine unterdrueckte Randsumme ist **nicht** verborgen, solange irgendeine
    Aufschluesselung derselben Randsumme vollstaendig sichtbar ist: ihre Summe
    ist die Randsumme. Ohne diesen Schritt wuerde der Guard das Fallenlassen der
    Randsumme fuer wirksam halten, obwohl der Empfaenger sie nebenan addieren
    kann.
    """
    resolved: dict[str, int | None] = {}
    for partition in partitions:
        if partition.margin_key is None:
            continue
        candidate = partition.margin
        if candidate is None and partition.cells and all(cell.published is not None for cell in partition.cells):
            candidate = sum(cell.published for cell in partition.cells if cell.published is not None)
        if candidate is None:
            resolved.setdefault(partition.margin_key, None)
        else:
            resolved[partition.margin_key] = candidate
    return resolved


def _bounds(cells: Sequence[Cell], residual: int, index: int) -> tuple[int, int]:
    """Geschlossene Intervallgrenzen der unterdrueckten Zelle ``index``."""
    others = [cell for position, cell in enumerate(cells) if position != index]
    sum_lo = sum(cell.lo for cell in others)
    unbounded = any(cell.hi is None for cell in others)
    cell = cells[index]
    lower = cell.lo if unbounded else max(cell.lo, residual - sum(cell.hi or 0 for cell in others))
    ceiling = residual - sum_lo
    upper = ceiling if cell.hi is None else min(cell.hi, ceiling)
    return lower, upper


def _hidden_bounds(partition: Partition, margin: int | None) -> Iterator[tuple[Cell, int | None, int | None]]:
    """Verdeckte Zellen einer Aufschluesselung samt Intervallgrenzen.

    Refs #1827: gemeinsamer Kern von :func:`feasible_spans`/:func:`findings`
    — beide iterierten dieselben verdeckten Zellen und berechneten bei
    bekannter Randsumme dieselben Grenzen ueber :func:`_bounds`; nur die
    Auswertung des Intervalls unterscheidet sich (Spannweite vs. die drei
    Ausnahme-Pruefungen in ``findings``). Ist ``margin`` unbekannt, liefert
    dieser Generator ``(cell, None, None)`` je verdeckter Zelle — „unbeschraenkt"
    — statt Grenzen zu berechnen.
    """
    hidden = [cell for cell in partition.cells if cell.published is None]
    if margin is None:
        for cell in hidden:
            yield cell, None, None
        return
    residual = margin - sum(cell.published for cell in partition.cells if cell.published is not None)
    for index, cell in enumerate(hidden):
        lower, upper = _bounds(hidden, residual, index)
        yield cell, lower, upper


def feasible_spans(partitions: Sequence[Partition]) -> dict[tuple[str, str], int | None]:
    """Anzahl der Werte, die jeder unterdrueckten Zelle noch offenstehen.

    ``None`` = unbeschraenkt, weil die Randsumme der Aufschluesselung weder
    publiziert noch rekonstruierbar ist; ``1`` = exakt zurueckrechenbar; ``0``
    = das Modell ist in sich unerfuellbar (kann nur durch falsche Schranken
    entstehen und wird wie ein Pin behandelt).
    """
    margins = _resolved_margins(partitions)
    spans: dict[tuple[str, str], int | None] = {}
    for partition in partitions:
        margin = margins.get(partition.margin_key) if partition.margin_key is not None else None
        for cell, lower, upper in _hidden_bounds(partition, margin):
            spans[(partition.name, cell.key)] = None if lower is None or upper is None else max(0, upper - lower + 1)
    return spans


def findings(partitions: Sequence[Partition], *, threshold: int) -> list[tuple[str, str]]:
    """Unterdrueckte Zellen, deren Rueckrechnung ein Datenschutz-Befund ist.

    Ausgangspunkt ist jede Zelle mit einer Spanne unter
    :data:`MIN_FEASIBLE_SPAN`, also jeder exakte Pin. Drei Faelle sind davon
    ausgenommen — jeder aus einem Grund, der in der Sache und nicht in der
    Bequemlichkeit liegt:

    1. **Leeres Intervall** (``upper < lower``): die Aufschluesselung
       partitioniert ihre Randsumme nicht exakt — bei konsistenten Daten
       unmoeglich (die wahre Welt erfuellt immer alle Schranken), bei
       driftenden Snapshot-Merges denkbar. Dann rechnet auch der Empfaenger ins
       Leere; es gibt nichts, was er korrekt ableiten koennte.
    2. **Pin auf 0**: ein leerer Bucket schuetzt niemanden. „In dieser
       Altersgruppe gab es keinen Kontakt" ist keine Kleinstfallzahl, sondern
       die Abwesenheit von Faellen.
    3. **Pin auf >= k ohne Personenkriterium**: der rueckrechenbare Wert ist
       selbst keine Kleinstfallzahl (k-Anonymitaet schuetzt
       Merkmalskombinationen mit weniger als *k* Faellen, nicht jede Zahl, die je
       durch eine Suppressionsregel gelaufen ist), und ohne Personenkriterium
       verraet er auch keine Personenluecke. Ohne diese Ausnahme wuerde der
       Guard ein Sekundaer-Opfer mit 20 Ereignissen endlos nachunterdruecken —
       Utility-Verlust ohne Schutzgewinn. **Mit** Personenkriterium
       (``reveals_person_gap``) gilt sie nicht: dort sagt der exakte Wert dem
       Empfaenger, dass die Zelle trotz >= *k* Ereignissen gefallen ist, also
       personenbedingt — und damit, dass < *k* Personen dahinterstehen.
    """
    margins = _resolved_margins(partitions)
    result: list[tuple[str, str]] = []
    for partition in partitions:
        margin = margins.get(partition.margin_key) if partition.margin_key is not None else None
        if margin is None:
            continue
        for cell, lower, upper in _hidden_bounds(partition, margin):
            if lower is None or upper is None:
                # Unerreichbar: ``margin`` ist hier bereits als nicht-None
                # geprueft, und _hidden_bounds liefert lower/upper nur dann
                # als None, wenn margin None ist. Typreinigung fuer mypy.
                continue
            if upper - lower + 1 >= MIN_FEASIBLE_SPAN or upper < lower:
                continue
            if upper == 0:
                continue
            if not partition.reveals_person_gap and lower >= threshold:
                continue
            result.append((partition.name, cell.key))
    return result
