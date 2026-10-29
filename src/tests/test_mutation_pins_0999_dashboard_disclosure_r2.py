"""Pins for exact disclosure equations, bound propagation and feasible witnesses.

Source: mutation delta of the release-0.99.9 checkpoint, 37 mutants.
Groups: exact rational equations (9), interval propagation (14), bounded witnesses (14).
"""

import pytest

from core.services.dashboard import disclosure as service
from core.services.dashboard.disclosure import Cell, Partition, SharedBasisCache, shared_alternatives, shared_bounds


def partition(name, margin, *keys):
    """Describe a graph equation; authoritative values come from the cell map."""
    return Partition(name, margin, None, tuple(Cell(key, None, lo=0) for key in keys))


def odd_cycle():
    """Three pair totals determine a half-integer hidden solution."""
    cells = {key: Cell(key, None, lo=0) for key in ("a", "b", "c")}
    cells.update({key: Cell(key, 3) for key in ("ab", "bc", "ac")})
    graph = [partition("ab", "ab", "a", "b"), partition("bc", "bc", "b", "c"), partition("ac", "ac", "a", "c")]
    return cells, graph


def test_nonintegral_deduction_is_rejected_with_diagnosis():
    """Odd overlapping totals cannot become integer pins.

    x__primitive_row__mutmut_4, x__exact_shared_pins__mutmut_11,
    xǁSharedBasisCacheǁget__mutmut_76, xǁSharedBasisCacheǁget__mutmut_77,
    xǁSharedBasisCacheǁget__mutmut_78.
    """
    cells, graph = odd_cycle()
    cache = SharedBasisCache(graph)
    with pytest.raises(ValueError) as error:
        shared_bounds(cells, graph, basis_cache=cache)
    assert str(error.value) == "Non-integer shared partition solution"


def test_repeated_cell_keeps_its_equation_multiplicity():
    """A repeated cell contributes twice rather than once: x__shared_basis__mutmut_12."""
    cells = {"a": Cell("a", None, lo=0), "total": Cell("total", 6)}
    graph = [partition("double", "total", "a", "a")]
    assert shared_bounds(cells, graph)["a"] == (3, 3)


@pytest.mark.parametrize("prefix", ["without_margin", "duplicate"])
def test_skipped_equation_does_not_hide_later_deductions(prefix):
    """Skipped graph entries retain later exact deductions: x__shared_basis__mutmut_3, x__shared_basis__mutmut_6."""
    cells = {key: Cell(key, None, lo=0) for key in ("a", "b", "c")}
    cells.update({"ab": Cell("ab", 6), "bc": Cell("bc", 7), "ac": Cell("ac", 5)})
    graph = [partition("ab", "ab", "a", "b"), partition("bc", "bc", "b", "c"), partition("ac", "ac", "a", "c")]
    graph.insert(1, partition("skipped", None, "a") if prefix == "without_margin" else graph[0])
    assert shared_bounds(cells, graph)["a"] == (2, 2)


@pytest.mark.parametrize("include_zero", [False, True])
def test_unbounded_cell_uses_finite_siblings_complement(include_zero):
    """Finite siblings tighten the only unbounded cell, including zero upper bounds.

    x_shared_bounds__mutmut_101, x_shared_bounds__mutmut_133.
    """
    cells = {"m": Cell("m", None, lo=8), "b": Cell("b", None, lo=0, hi=2), "c": Cell("c", None, lo=0)}
    keys = ["b", "c"]
    if include_zero:
        cells["z"] = Cell("z", None, lo=0, hi=0)
        keys.insert(0, "z")
    assert shared_bounds(cells, [partition("sum", "m", *keys)])["c"][0] == 6


@pytest.mark.parametrize(
    "a_bounds,b_bounds,expected",
    [((2, None), (3, None), {"a": (2, 7), "b": (3, 8)}), ((3, 5), (2, 20), {"a": (3, 5), "b": (5, 7)})],
)
def test_published_total_subtracts_updated_sibling_bounds(a_bounds, b_bounds, expected):
    """A published total subtracts sibling lower bounds throughout propagation.

    x_shared_bounds__mutmut_115, x_shared_bounds__mutmut_126.
    """
    cells = {
        "m": Cell("m", 10),
        "a": Cell("a", None, lo=a_bounds[0], hi=a_bounds[1]),
        "b": Cell("b", None, lo=b_bounds[0], hi=b_bounds[1]),
    }
    bounds = shared_bounds(cells, [partition("sum", "m", "a", "b")])
    assert {key: bounds[key] for key in expected} == expected


@pytest.mark.parametrize("stable_prefix,extended", [(False, False), (True, False), (False, True)])
def test_reverse_dependency_chain_reaches_fixed_point(stable_prefix, extended):
    """Earlier equations are revisited until every dependent interval converges.

    x_shared_bounds__mutmut_117, x_shared_bounds__mutmut_72,
    x_shared_bounds__mutmut_75, x_shared_bounds__mutmut_88, x_shared_bounds__mutmut_89.
    """
    cells = {key: Cell(key, None, lo=0, hi=10) for key in ("x", "a", "b")}
    cells.update({"c": Cell("c", None, lo=2, hi=6), "m": Cell("m", 8)})
    graph = [partition("ab", "m", "a", "b"), partition("bc", "m", "b", "c")]
    if extended:
        graph.insert(0, partition("xa", "m", "x", "a"))
    if stable_prefix:
        cells.update({"stable": Cell("stable", 2), "one": Cell("one", 1)})
        graph.insert(0, partition("stable", "stable", "one", "one"))
    bounds = shared_bounds(cells, graph)
    assert bounds["a"] == (2, 6)
    if extended:
        assert bounds["x"] == (2, 6)


def test_stable_partition_returns_without_convergence_error(monkeypatch):
    """An already stable partition returns within the available budget: x_shared_bounds__mutmut_33."""
    monkeypatch.setattr(service, "MAX_GUARD_ITERATIONS", 1)
    cells = {"a": Cell("a", None, lo=0, hi=2), "b": Cell("b", None, lo=0, hi=2), "m": Cell("m", 2)}
    assert shared_bounds(cells, [partition("sum", "m", "a", "b")]) == {"a": (0, 2), "b": (0, 2), "m": (2, 2)}


def test_fixed_interval_enters_exact_linear_inference():
    """A hidden fixed interval participates in exact deductions: x_shared_bounds__mutmut_42."""
    cells = {
        "a": Cell("a", None, lo=0, hi=0),
        "b": Cell("b", None, lo=0),
        "c": Cell("c", None, lo=0),
        "m": Cell("m", 6),
    }
    graph = [partition("abc", "c", "a", "b"), partition("bc", "m", "b", "c")]
    bounds = shared_bounds(cells, graph)
    assert bounds["b"] == bounds["c"] == (3, 3)


def test_exact_deduction_tightens_both_interval_ends():
    """A linear equality closes both bounds even with unbounded neighbours: x_shared_bounds__mutmut_67."""
    cells = {key: Cell(key, None, lo=0) for key in ("a", "b", "c")}
    cells["d"] = Cell("d", 3)
    graph = [partition("ab", "c", "a", "b"), partition("bd", "c", "b", "d")]
    assert shared_bounds(cells, graph)["a"] == (3, 3)


def test_inconsistent_equations_keep_their_diagnosis():
    """Contradictory published equations retain their explanation: x__eliminate_integer_rows__mutmut_37."""
    cells = {"a": Cell("a", 1), "m": Cell("m", 2)}
    with pytest.raises(ValueError) as error:
        shared_bounds(cells, [partition("sum", "m", "a")])
    assert str(error.value) == "Inconsistent shared partition equations"


def test_inconsistent_bounds_keep_their_diagnosis():
    """A nonempty linear solution outside box bounds is diagnosed: x_shared_bounds__mutmut_28."""
    cells = {"a": Cell("a", None, lo=3), "b": Cell("b", None, lo=3), "m": Cell("m", 5)}
    with pytest.raises(ValueError) as error:
        shared_bounds(cells, [partition("sum", "m", "a", "b")])
    assert str(error.value) == "Inconsistent shared partition bounds"


def test_exhausted_iteration_budget_keeps_its_diagnosis(monkeypatch):
    """Incomplete propagation reports the convergence failure: x_shared_bounds__mutmut_142."""
    monkeypatch.setattr(service, "MAX_GUARD_ITERATIONS", 1)
    cells = {"a": Cell("a", None, lo=0), "b": Cell("b", None, lo=0), "m": Cell("m", 5)}
    with pytest.raises(RuntimeError) as error:
        shared_bounds(cells, [partition("sum", "m", "a", "b")])
    assert str(error.value) == "Shared partition bounds did not converge"


def test_asymmetric_bounds_return_only_feasible_values():
    """Alternative values follow the accepted signed witness: x_shared_alternatives__mutmut_28."""
    cells = {"a": Cell("a", None, lo=1, hi=2)}
    assert shared_alternatives(cells, [], {"a": 1}) == {"a": {1, 2}}


def test_large_integer_directions_never_round_to_float():
    """Integer direction scaling preserves coefficients beyond float precision: x_shared_alternatives__mutmut_47."""
    raw = {f"d{i:02}": 2 ** (54 - i) for i in range(55)}
    raw["a"] = 2**54 + 1
    cells = {key: Cell(key, None, lo=0, hi=2 * value) for key, value in raw.items()}
    graph = [partition(f"double{i}", f"d{i:02}", f"d{i + 1:02}", f"d{i + 1:02}") for i in range(54)]
    graph.append(partition("offset", "a", "d00", "d54"))
    alternatives = shared_alternatives(cells, graph, raw)
    assert alternatives["a"] == {0, 2**54 + 1, 2 * (2**54 + 1)}
    assert all(isinstance(value, int) for values in alternatives.values() for value in values)


def budget_world(last):
    """Only the first and last free cells can move while preserving their fixed sum."""
    raw = {f"f{i:04}": 1 for i in range(last + 1)}
    raw["a"] = last + 1
    cells = {key: Cell(key, None, lo=value, hi=value) for key, value in raw.items()}
    for key in ("f0000", f"f{last:04}"):
        cells[key] = Cell(key, None, lo=0, hi=2)
    return cells, [partition("sum", "a", *sorted(key for key in raw if key != "a"))], raw


@pytest.mark.parametrize("last,expected", [(700, {0, 1, 2}), (1024, {0, 1, 2}), (1025, {1})])
def test_coupled_witness_respects_the_search_budget(last, expected):
    """Late witnesses respect the configured budget without counting implementation calls.

    x_shared_alternatives__mutmut_75, x_shared_alternatives__mutmut_77,
    x_shared_alternatives__mutmut_79, x_shared_alternatives__mutmut_80,
    x_shared_alternatives__mutmut_94, x_shared_alternatives__mutmut_95,
    x_shared_alternatives__mutmut_96.
    """
    cells, graph, raw = budget_world(last)
    assert shared_alternatives(cells, graph, raw)["f0000"] == expected


@pytest.mark.parametrize("early", ["resolved_pairs", "resolved_singles", "zero_cells"])
def test_irrelevant_targets_preserve_budget_for_later_population(early):
    """Resolved or empty populations leave the witness budget for positive unresolved cells.

    x_shared_alternatives__mutmut_29, x_shared_alternatives__mutmut_30,
    x_shared_alternatives__mutmut_56, x_shared_alternatives__mutmut_58,
    x_shared_alternatives__mutmut_84.
    """
    raw = {f"f{i:02}": 0 if early == "zero_cells" else 1 for i in range(48)}
    cells = {}
    if early == "resolved_pairs":
        raw["a"] = 0
        cells["a"] = Cell("a", None, lo=0, hi=0)
        for key in raw:
            if key != "a":
                cells[key] = Cell(key, None, lo=0, hi=1) if key == "f00" else Cell(key, None, lo=1, hi=2)
        cells["m"] = Cell("m", 48)
        raw["m"] = 48
        graph = [partition("early", "m", "a", *sorted(key for key in raw if key.startswith("f")))]
    elif early == "resolved_singles":
        raw["a"] = 1
        cells["a"] = Cell("a", None, lo=0, hi=1)
        cells.update({key: Cell(key, None, lo=1, hi=2) for key in raw if key.startswith("f")})
        cells["m"] = Cell("m", 49)
        raw["m"] = 49
        graph = [partition("early", "m", "a", *sorted(key for key in raw if key.startswith("f")))]
    else:
        raw["a"] = 0
        cells = {key: Cell(key, None, lo=0, hi=0) for key in raw}
        graph = [partition("early", "a", *sorted(key for key in raw if key.startswith("f")))]
    raw.update({"b": 2, "z0": 1, "z1": 1})
    cells.update(
        {"b": Cell("b", None, lo=2, hi=2), "z0": Cell("z0", None, lo=0, hi=2), "z1": Cell("z1", None, lo=0, hi=2)}
    )
    graph.append(partition("late", "b", "z0", "z1"))

    alternatives = shared_alternatives(cells, graph, raw)

    assert alternatives["z0"] == alternatives["z1"] == {0, 1, 2}
