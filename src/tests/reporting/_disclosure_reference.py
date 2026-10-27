"""Frozen exact solver from 69bba8f17, before the performance fix (Refs #2639).

Keep this independent reference unchanged when optimizing the production solver.
Only the unchanged cell types and constants are shared with production.
"""

import dataclasses
from collections.abc import Sequence
from fractions import Fraction
from math import lcm

from core.services.dashboard.disclosure import MAX_GUARD_ITERATIONS, MIN_FEASIBLE_SPAN, Cell, Partition


def _shared_basis(
    cells: dict[str, Cell], partitions: Sequence[Partition]
) -> dict[str, tuple[dict[str, Fraction], Fraction]]:
    """Reduce exact equations after substituting all published values."""
    basis: dict[str, tuple[dict[str, Fraction], Fraction]] = {}
    for partition in partitions:
        if partition.margin_key is None:
            continue
        coefficients = {partition.margin_key: Fraction(1)}
        for cell in partition.cells:
            coefficients[cell.key] = coefficients.get(cell.key, Fraction(0)) - 1
        constant = Fraction(0)
        for key in list(coefficients):
            published = cells[key].published
            if published is not None:
                constant -= coefficients.pop(key) * published
        for pivot, (row, value) in sorted(basis.items()):
            factor = coefficients.get(pivot, Fraction(0))
            if factor:
                constant -= factor * value
                for key, coefficient in row.items():
                    coefficients[key] = coefficients.get(key, Fraction(0)) - factor * coefficient
                coefficients = {key: coefficient for key, coefficient in coefficients.items() if coefficient}
        if not coefficients:
            if constant:
                raise ValueError("Inconsistent shared partition equations")
            continue
        pivot = min(coefficients)
        scale = coefficients[pivot]
        coefficients = {key: coefficient / scale for key, coefficient in coefficients.items()}
        constant /= scale
        for previous, (row, value) in list(basis.items()):
            factor = row.get(pivot, Fraction(0))
            if factor:
                for key, coefficient in coefficients.items():
                    row[key] = row.get(key, Fraction(0)) - factor * coefficient
                basis[previous] = (
                    {key: coefficient for key, coefficient in row.items() if coefficient},
                    value - factor * constant,
                )
        basis[pivot] = coefficients, constant
    return basis


def _exact_shared_pins(cells: dict[str, Cell], partitions: Sequence[Partition]) -> dict[str, int]:
    """Find exact linear deductions, including cycles of overlapping margins."""
    pins = {}
    basis = _shared_basis(cells, partitions)
    for key, (row, value) in basis.items():
        if len(row) == 1:
            if value.denominator != 1:
                raise ValueError("Non-integer shared partition solution")
            pins[key] = int(value)
    return pins


def shared_alternatives(
    cells: dict[str, Cell], partitions: Sequence[Partition], raw_values: dict[str, int]
) -> dict[str, set[int]]:
    """Construct actual integer worlds witnessing ambiguity of hidden cells.

    A nullspace direction preserves every published equation. Integer scaling
    and a check against every box bound make each accepted alternative a
    concrete feasible world. Failure to find a direction proves nothing:
    callers must withhold more, since a combination of basis directions may
    still exist. This deliberately trades utility for a sound certificate
    without introducing a general integer optimization dependency.
    """
    basis = _shared_basis(cells, partitions)
    hidden = {key for key, cell in cells.items() if cell.published is None}
    alternatives = {key: {raw_values[key]} for key in hidden}
    directions = []

    def witness(step: dict[str, int]) -> None:
        candidate = {key: raw_values[key] + value for key, value in step.items()}
        if all(
            value >= lower and (upper is None or value <= upper)
            for lower, upper, value in ((cells[key].lo, cells[key].hi, value) for key, value in candidate.items())
        ):
            for key, value in candidate.items():
                alternatives[key].add(value)

    for free in sorted(hidden - basis.keys()):
        direction = {free: Fraction(1)}
        direction.update({key: -row[free] for key, (row, _) in basis.items() if free in row})
        scale = lcm(*(coefficient.denominator for coefficient in direction.values()))
        step = {key: int(coefficient * scale) for key, coefficient in direction.items() if coefficient}
        directions.append(step)
        for sign in (-1, 1):
            witness({key: sign * value for key, value in step.items()})
    # Coupled population aliases can require two basis directions at once
    # (Refs #2639). Every accepted combination is still an actual integer
    # world, never an estimate of feasibility. Bound the additional search;
    # missing a witness remains a reason to suppress more.
    remaining = {key for key in hidden if raw_values[key] > 0 and len(alternatives[key]) < MIN_FEASIBLE_SPAN}
    attempts = 0
    for index, first in enumerate(directions):
        for second in directions[index + 1 :]:
            if not remaining or attempts >= 4096:
                return alternatives
            keys = first.keys() | second.keys()
            if not (first.keys() & second.keys()) or not (keys & remaining):
                continue
            for left in (-1, 1):
                for right in (-1, 1):
                    attempts += 1
                    witness({key: left * first.get(key, 0) + right * second.get(key, 0) for key in keys})
            remaining = {key for key in remaining if len(alternatives[key]) < MIN_FEASIBLE_SPAN}
    return alternatives


def shared_bounds(cells: dict[str, Cell], partitions: Sequence[Partition]) -> dict[str, tuple[int, int | None]]:
    """Propagate integer bounds through a network of exact partitions.

    Unlike the artifact-local audit, a margin is itself a cell and may be a
    member of another partition. Repeated propagation therefore includes
    reconstructed margins and differences of overlapping populations. This is
    interval consistency plus exact linear deductions, not a general integer
    linear programming solver.
    Callers must fail closed if propagation does not converge.
    """
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

    for _ in range(MAX_GUARD_ITERATIONS):
        effective = {
            key: dataclasses.replace(cell, published=bounds[key][0]) if bounds[key][1] == bounds[key][0] else cell
            for key, cell in cells.items()
        }
        changed = False
        for key, value in _exact_shared_pins(effective, partitions).items():
            changed |= tighten(key, value, value)
        for partition in partitions:
            if partition.margin_key is None:
                continue
            keys = [cell.key for cell in partition.cells]
            lower_sum = sum(bounds[key][0] for key in keys)
            upper_values = [bounds[key][1] for key in keys]
            upper_sum = None if None in upper_values else sum(value for value in upper_values if value is not None)
            changed |= tighten(partition.margin_key, lower_sum, upper_sum)
            margin_lower, margin_upper = bounds[partition.margin_key]
            for key in keys:
                others = [bounds[other] for other in keys if other != key]
                other_upper = [upper for _, upper in others]
                lower = 0 if None in other_upper else margin_lower - sum(v for v in other_upper if v is not None)
                upper = None if margin_upper is None else margin_upper - sum(v for v, _ in others)
                changed |= tighten(key, lower, upper)
        if not changed:
            return bounds
    raise RuntimeError("Shared partition bounds did not converge")
