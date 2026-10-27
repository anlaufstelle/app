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
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from fractions import Fraction
from math import gcd, lcm

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


def _bounds(
    cells: Sequence[Cell], residual: int, index: int, *, totals: tuple[int, int, int] | None = None
) -> tuple[int, int]:
    """Exact interval projection, optionally reusing the partition totals."""
    if totals is None:
        totals = (
            sum(cell.lo for cell in cells),
            sum(cell.hi or 0 for cell in cells),
            sum(cell.hi is None for cell in cells),
        )
    sum_lo, sum_hi, unbounded = totals
    cell = cells[index]
    lower = cell.lo if unbounded - (cell.hi is None) else max(cell.lo, residual - (sum_hi - (cell.hi or 0)))
    ceiling = residual - (sum_lo - cell.lo)
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
    totals = (
        sum(cell.lo for cell in hidden),
        sum(cell.hi or 0 for cell in hidden),
        sum(cell.hi is None for cell in hidden),
    )
    for index, cell in enumerate(hidden):
        lower, upper = _bounds(hidden, residual, index, totals=totals)
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


type _IntegerBasis = dict[str, tuple[dict[str, int], int]]
type _RationalBasis = dict[str, tuple[dict[str, int | Fraction], int | Fraction]]


def _primitive_row(row: dict[str, int], value: int) -> tuple[dict[str, int], int]:
    """Keep exact integer rows small without changing their solution set."""
    row = {key: coefficient for key, coefficient in row.items() if coefficient}
    divisor = gcd(value, *row.values())
    if divisor > 1:
        row = {key: coefficient // divisor for key, coefficient in row.items()}
        value //= divisor
    return row, value


def _eliminate_integer_rows(
    rows: Iterable[tuple[dict[str, int], int]], *, hidden: frozenset[str] | None = None
) -> _IntegerBasis:
    """Reduce rational equations using integer cross-products and gcd only.

    Pivot coefficients stay integral and need not equal one. A column index
    restricts back elimination to affected rows; fractions are created only
    when exporting the reduced basis to the unchanged inference consumers.
    """

    def order(key: str) -> tuple[bool, str]:
        return hidden is not None and key not in hidden, key

    basis: _IntegerBasis = {}
    columns: dict[str, set[str]] = {}
    for coefficients, constant in rows:
        coefficients, constant = _primitive_row(coefficients, constant)
        for pivot in sorted(coefficients.keys() & basis.keys(), key=order):
            row, value = basis[pivot]
            factor, scale = coefficients[pivot], row[pivot]
            reduced = {key: coefficient * scale for key, coefficient in coefficients.items()}
            for key, coefficient in row.items():
                reduced[key] = reduced.get(key, 0) - factor * coefficient
            coefficients, constant = _primitive_row(reduced, constant * scale - factor * value)
        if not coefficients:
            if constant:
                raise ValueError("Inconsistent shared partition equations")
            continue
        pivot = min(coefficients, key=order)
        scale = coefficients[pivot]
        for previous in tuple(columns.get(pivot, ())):
            row, value = basis[previous]
            factor = row[pivot]
            reduced = {key: coefficient * scale for key, coefficient in row.items()}
            for key, coefficient in coefficients.items():
                reduced[key] = reduced.get(key, 0) - factor * coefficient
            reduced, value = _primitive_row(reduced, value * scale - factor * constant)
            for key in row.keys() - reduced.keys():
                columns[key].remove(previous)
            for key in reduced.keys() - row.keys():
                columns.setdefault(key, set()).add(previous)
            basis[previous] = reduced, value
        basis[pivot] = coefficients, constant
        for key in coefficients:
            columns.setdefault(key, set()).add(pivot)
    return basis


def _substitute_rows(rows: Iterable[tuple[dict[str, int], int]], published: dict[str, int]) -> _IntegerBasis:
    """Specialize an equivalent basis by adding newly published equalities."""

    def substituted():
        for row, value in rows:
            remaining = {}
            for key, coefficient in row.items():
                if key in published:
                    value -= coefficient * published[key]
                else:
                    remaining[key] = coefficient
            yield remaining, value

    return _eliminate_integer_rows(substituted())


def _shared_basis(cells: dict[str, Cell], partitions: Sequence[Partition]) -> _IntegerBasis:
    """Reduce the complete graph, ordering hidden columns before known ones.

    Retain known coefficients symbolically so hiding another value can change
    the column order without rebuilding or losing currently inactive rows.
    """

    def equations():
        seen = set()
        for partition in partitions:
            if partition.margin_key is None:
                continue
            equation = (partition.margin_key, *[cell.key for cell in partition.cells])
            if equation in seen:
                continue
            seen.add(equation)
            row = {partition.margin_key: 1}
            for cell in partition.cells:
                row[cell.key] = row.get(cell.key, 0) - 1
            yield row, 0

    return _eliminate_integer_rows(
        equations(), hidden=frozenset(key for key, cell in cells.items() if cell.published is None)
    )


def _extend_hidden_column(basis: _IntegerBasis, key: str, hidden: frozenset[str]) -> None:
    """Move one column earlier in the ordering with at most one pivot swap.

    All other columns keep their relative order. If the entering column is
    free, its last nonzero row whose pivot now follows it becomes the new
    pivot row; eliminating that column restores the reduced column order.
    Known-only rows remain available when a later suppression activates them.
    """
    if key in basis:
        return

    def order(column: str) -> tuple[bool, str]:
        return column not in hidden, column

    candidates = [pivot for pivot, (row, _) in basis.items() if key in row and order(pivot) > order(key)]
    if not candidates:
        return
    previous = max(candidates, key=order)
    coefficients, constant = basis.pop(previous)
    scale = coefficients[key]
    for pivot, (row, value) in list(basis.items()):
        if key not in row:
            continue
        factor = row[key]
        reduced = {column: coefficient * scale for column, coefficient in row.items()}
        for column, coefficient in coefficients.items():
            reduced[column] = reduced.get(column, 0) - factor * coefficient
        basis[pivot] = _primitive_row(reduced, value * scale - factor * constant)
    basis[key] = coefficients, constant


class SharedBasisCache:
    """Exact symbolic matrix and publication views for one mask computation.

    The graph reduction retains all columns. Monotone suppression moves each
    newly hidden column ahead of known columns, with one pivot swap at most.
    Additional published pins specialize an existing view. Changed values and
    utility trials substitute their complete publication signature afresh.
    Bounds and raw values never enter the symbolic matrix. Graph changes need
    a new cache. Consumers must only read cached publication views.
    """

    def __init__(self, partitions: Sequence[Partition]) -> None:
        self._partitions = tuple(partitions)
        self._graph = self._graph_signature(partitions)
        self._bases: dict[tuple[tuple[str, int | None], ...], _RationalBasis] = {}
        self._previous: tuple[dict[str, int], _IntegerBasis] | None = None
        self._keys: frozenset[str] | None = None
        self._symbolic: _IntegerBasis | None = None
        self._hidden: frozenset[str] = frozenset()

    @staticmethod
    def _graph_signature(partitions: Sequence[Partition]) -> tuple:
        return tuple(
            (
                partition.name,
                partition.margin_key,
                tuple(cell.key for cell in partition.cells),
                partition.reveals_person_gap,
            )
            for partition in partitions
        )

    def get(self, cells: dict[str, Cell], partitions: Sequence[Partition]) -> _RationalBasis:
        if partitions is not self._partitions:
            if self._graph_signature(partitions) != self._graph:
                raise ValueError("A shared basis cache requires the same partition graph")
            self._partitions = tuple(partitions)
        signature = tuple(sorted((key, cell.published) for key, cell in cells.items()))
        if signature not in self._bases:
            published = {key: cell.published for key, cell in cells.items() if cell.published is not None}
            keys = frozenset(cells)
            hidden = keys - published.keys()
            if self._symbolic is None or keys != self._keys:
                self._symbolic = _shared_basis(cells, self._partitions)
                self._hidden = hidden
            elif self._hidden <= hidden:
                for key in sorted(hidden - self._hidden):
                    self._hidden = self._hidden | {key}
                    _extend_hidden_column(self._symbolic, key, self._hidden)
            if self._previous is not None and keys == self._keys and self._previous[0].items() <= published.items():
                previous_values, previous_basis = self._previous
                additions = {key: value for key, value in published.items() if key not in previous_values}
                basis = _substitute_rows(previous_basis.values(), additions)
            else:
                basis = _substitute_rows(self._symbolic.values(), published)
            self._previous = published, basis
            self._keys = keys
            exported: _RationalBasis = {}
            for pivot, (row, value) in basis.items():
                scale = row[pivot]
                if scale in (-1, 1):
                    exported[pivot] = {key: coefficient // scale for key, coefficient in row.items()}, value // scale
                else:
                    exported[pivot] = (
                        {key: Fraction(coefficient, scale) for key, coefficient in row.items()},
                        Fraction(value, scale),
                    )
            self._bases[signature] = exported
        return self._bases[signature]


def _exact_shared_pins(
    cells: dict[str, Cell], partitions: Sequence[Partition], basis_cache: SharedBasisCache
) -> dict[str, int]:
    """Find exact linear deductions, including cycles of overlapping margins."""
    pins = {}
    basis = basis_cache.get(cells, partitions)
    for key, (row, value) in basis.items():
        if len(row) == 1:
            if value.denominator != 1:
                raise ValueError("Non-integer shared partition solution")
            pins[key] = int(value)
    return pins


def shared_alternatives(
    cells: dict[str, Cell],
    partitions: Sequence[Partition],
    raw_values: dict[str, int],
    *,
    basis_cache: SharedBasisCache | None = None,
) -> dict[str, set[int]]:
    """Construct actual integer worlds witnessing ambiguity of hidden cells.

    A nullspace direction preserves every published equation. Integer scaling
    and a check against every box bound make each accepted alternative a
    concrete feasible world. Failure to find a direction proves nothing:
    callers must withhold more, since a combination of basis directions may
    still exist. This deliberately trades utility for a sound certificate
    without introducing a general integer optimization dependency.
    """
    basis_cache = basis_cache or SharedBasisCache(partitions)
    basis = basis_cache.get(cells, partitions)
    hidden = {key for key, cell in cells.items() if cell.published is None}
    alternatives = {key: {raw_values[key]} for key in hidden}
    directions = []

    limits = {
        key: (cells[key].lo - raw_values[key], None if (upper := cells[key].hi) is None else upper - raw_values[key])
        for key in hidden
    }
    remaining: set[str] = set()

    def witness(step: dict[str, int]) -> None:
        for key, value in step.items():
            lower, upper = limits[key]
            if value < lower or (upper is not None and value > upper):
                return
        for key, value in step.items():
            values = alternatives[key]
            values.add(raw_values[key] + value)
            if len(values) >= MIN_FEASIBLE_SPAN:
                remaining.discard(key)

    coefficients_by_free: dict[str, dict[str, int | Fraction]] = defaultdict(dict)
    for pivot, (row, _) in basis.items():
        for free, coefficient in row.items():
            if free != pivot:
                coefficients_by_free[free][pivot] = coefficient
    for free in sorted(hidden - basis.keys()):
        coefficients = coefficients_by_free.get(free, {})
        scale = lcm(*(coefficient.denominator for coefficient in coefficients.values()))
        step = {free: scale}
        step.update(
            {
                key: -coefficient.numerator * (scale // coefficient.denominator)
                for key, coefficient in coefficients.items()
            }
        )
        directions.append(step)
        for sign in (-1, 1):
            witness({key: sign * value for key, value in step.items()})
    # Coupled population aliases can require two basis directions at once
    # (Refs #2639). Every accepted combination is still an actual integer
    # world, never an estimate of feasibility. Bound the additional search;
    # missing a witness remains a reason to suppress more.
    remaining = {key for key in hidden if raw_values[key] > 0 and len(alternatives[key]) < MIN_FEASIBLE_SPAN}
    if not remaining:
        return alternatives
    attempts = 0
    directions_by_key: dict[str, set[int]] = defaultdict(set)
    for index, direction in enumerate(directions):
        for key in direction:
            directions_by_key[key].add(index)
    relevant = {index for key in remaining for index in directions_by_key[key]}
    for index, first in enumerate(directions):
        # Preserve the original pair order and attempt budget, visiting only
        # pairs sharing a nonzero coordinate. Disjoint pairs cannot witness
        # anything beyond the individual directions already checked.
        neighbours: set[int] = set()
        for key in first:
            neighbours.update(directions_by_key[key] if index in relevant else directions_by_key[key] & relevant)
        for other in sorted(other for other in neighbours if other > index):
            second = directions[other]
            if not remaining or attempts >= 4096:
                return alternatives
            keys = first.keys() | second.keys()
            if not (keys & remaining):
                continue
            for left in (-1, 1):
                left_step = {key: left * value for key, value in first.items()}
                for right in (-1, 1):
                    attempts += 1
                    step = dict(left_step)
                    for key, value in second.items():
                        step[key] = step.get(key, 0) + right * value
                    witness(step)
            # All four signs run before remaining is consulted again, matching
            # the original pair order and attempt budget exactly.
    return alternatives


def shared_bounds(
    cells: dict[str, Cell], partitions: Sequence[Partition], *, basis_cache: SharedBasisCache | None = None
) -> dict[str, tuple[int, int | None]]:
    """Propagate integer bounds through a network of exact partitions.

    Unlike the artifact-local audit, a margin is itself a cell and may be a
    member of another partition. Repeated propagation therefore includes
    reconstructed margins and differences of overlapping populations. This is
    interval consistency plus exact linear deductions, not a general integer
    linear programming solver.
    Callers must fail closed if propagation does not converge.
    """
    basis_cache = basis_cache or SharedBasisCache(partitions)
    bounds = {
        key: (cell.published, cell.published) if cell.published is not None else (cell.lo, cell.hi)
        for key, cell in cells.items()
    }

    def tighten(key: str, lower: int, upper: int | None) -> bool:
        old_lower, old_upper = bounds[key]
        lower = max(old_lower, lower)
        upper = old_upper if upper is None else upper if old_upper is None else min(old_upper, upper)
        if upper is not None and lower > upper:
            raise ValueError("Inconsistent shared partition bounds")
        bounds[key] = lower, upper
        return bounds[key] != (old_lower, old_upper)

    equations = [
        (partition.margin_key, tuple(cell.key for cell in partition.cells))
        for partition in partitions
        if partition.margin_key is not None
    ]
    evaluated: dict[tuple[str, tuple[str, ...]], list[tuple[int, int | None]]] = {}
    effective = dict(cells)
    for _ in range(MAX_GUARD_ITERATIONS):
        effective = {
            key: dataclasses.replace(cell, published=bounds[key][0])
            if bounds[key][1] == bounds[key][0] and cell.published != bounds[key][0]
            else cell
            for key, cell in effective.items()
        }
        changed = False
        for key, value in _exact_shared_pins(effective, partitions, basis_cache).items():
            changed |= tighten(key, value, value)
        for margin_key, keys in equations:
            state = [bounds[key] for key in (margin_key, *keys)]
            equation = margin_key, keys
            if evaluated.get(equation) == state:
                # Repeating an unchanged input would produce the same bounds
                # and a false changed flag, preserving the iteration schedule.
                continue
            evaluated[equation] = state
            lower_sum = sum(bounds[key][0] for key in keys)
            finite_upper_sum = sum(upper for key in keys if (upper := bounds[key][1]) is not None)
            unbounded = sum(bounds[key][1] is None for key in keys)
            changed |= tighten(margin_key, lower_sum, None if unbounded else finite_upper_sum)
            margin_lower, margin_upper = bounds[margin_key]
            for key in keys:
                old_lower, old_upper = bounds[key]
                other_unbounded = unbounded - (old_upper is None)
                lower = 0 if other_unbounded else margin_lower - (finite_upper_sum - (old_upper or 0))
                upper = None if margin_upper is None else margin_upper - (lower_sum - old_lower)
                changed |= tighten(key, lower, upper)
                # Keep totals current: later cells in this partition must see
                # earlier tightenings, exactly as in sequential propagation.
                new_lower, new_upper = bounds[key]
                lower_sum += new_lower - old_lower
                finite_upper_sum += (new_upper or 0) - (old_upper or 0)
                unbounded += (new_upper is None) - (old_upper is None)
        if not changed:
            return bounds
    raise RuntimeError("Shared partition bounds did not converge")
