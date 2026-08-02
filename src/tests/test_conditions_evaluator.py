"""Unit-Tests fuer den gemeinsamen Bedingungs-Evaluator (M1, Refs #1057).

Der Kern (``src/core/services/conditions.py``) ist bewusst DB-frei — reine
Funktionen. Die parametrisierten ``test_vectors``-Faelle stammen aus der
ausfuehrbaren Spezifikation ``src/tests/data/condition_vectors.json``, die
zusaetzlich der Playwright-Paritaetstest (P7) gegen die JS-Implementierung
laufen laesst. Jede Semantikaenderung wird zuerst dort als Vektor ergaenzt
(Prozessregel ADR-035).
"""

import datetime
import json
from pathlib import Path

from core.services.conditions import (
    OPERATORS_BY_FIELD_TYPE,
    evaluate_condition,
    evaluate_conditions,
    find_cycle,
    is_empty,
    normalize_value,
)

_VECTORS_PATH = Path(__file__).parent / "data" / "condition_vectors.json"


def _load_vectors() -> list[dict]:
    """Liest die Vektoren erst zur Testlaufzeit (Collection), nicht beim Import.

    Guard-konform (``test_architecture_guards_mutation_isolation.py``): der
    Dateizugriff steckt in einer Funktion, die nur von ``pytest_generate_tests``
    (einem Collection-Hook, nie einem Modul-Level-Aufruf) ausgefuehrt wird.
    """
    return json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))["cases"]


def pytest_generate_tests(metafunc):
    if "case" in metafunc.fixturenames:
        vectors = _load_vectors()
        metafunc.parametrize("case", vectors, ids=[c["name"] for c in vectors])


def test_vectors(case):
    """Ausfuehrbare Spezifikation: jeder JSON-Vektor gegen den Evaluator."""
    result = evaluate_conditions(case["fields"], case["conditions"], case["values"])
    assert result == set(case["expected_visible"]), case["name"]


def test_is_empty_false_and_zero_not_empty():
    """E10: ``False`` und ``0`` sind gesetzte Werte, nicht leer."""
    assert is_empty(None) is True
    assert is_empty("") is True
    assert is_empty([]) is True
    assert is_empty(()) is True
    assert is_empty(False) is False
    assert is_empty(0) is False
    assert is_empty(0.0) is False
    assert is_empty("abc") is False
    assert is_empty(["a"]) is False


def test_normalize_time_isoformat():
    """E11: time -> ``"HH:MM:SS"`` (Python ``time.isoformat``)."""
    assert normalize_value("time", datetime.time(14, 30)) == "14:30:00"
    assert normalize_value("time", datetime.time(9, 5, 15)) == "09:05:15"
    # Bereits als String vorliegende Werte werden unveraendert durchgereicht.
    assert normalize_value("time", "14:30:00") == "14:30:00"
    assert normalize_value("time", "") is None
    assert normalize_value("time", None) is None


def test_normalize_date_isoformat():
    """E11: date -> ``"YYYY-MM-DD"`` (Python ``date.isoformat``)."""
    assert normalize_value("date", datetime.date(2026, 1, 5)) == "2026-01-05"
    assert normalize_value("date", "2026-01-05") == "2026-01-05"
    assert normalize_value("date", "") is None
    assert normalize_value("date", None) is None


def test_normalize_select_empty_string_none():
    """E11: leerer Select-String wird zu ``None`` (=> leer)."""
    assert normalize_value("select", "") is None
    assert normalize_value("select", None) is None
    assert normalize_value("select", "opt_a") == "opt_a"
    # text/textarea gleiche Regel.
    assert normalize_value("text", "") is None
    assert normalize_value("textarea", "hallo") == "hallo"


def test_normalize_number_boolean_and_multiselect():
    """E11: number -> Zahl|None, boolean -> bool, multi_select -> Liste."""
    assert normalize_value("number", "") is None
    assert normalize_value("number", None) is None
    assert normalize_value("number", 0) == 0
    assert normalize_value("number", 3.5) == 3.5
    assert normalize_value("number", "7") == 7.0
    assert normalize_value("number", "keine-zahl") is None
    assert normalize_value("boolean", True) is True
    assert normalize_value("boolean", None) is False
    assert normalize_value("multi_select", None) == []
    assert normalize_value("multi_select", ["a", "b"]) == ["a", "b"]
    assert normalize_value("multi_select", "a") == ["a"]
    # Unbekannter Typ (Defensive): "" -> None, sonst unveraendert.
    assert normalize_value("unbekannt", "") is None
    assert normalize_value("unbekannt", "x") == "x"


def test_cascade_terminates_within_target_count():
    """E7: Fixpunkt-Iteration konvergiert; verstecktes Quellfeld = leer.

    Kette a -> b -> c -> d (jeweils ``is_not_empty``). Wird ``a`` geleert,
    verschwinden b, c und d in Kaskade (Konvergenz innerhalb der
    Ziel-Anzahl + 1, keine Endlosschleife).
    """
    fields = {"a": "text", "b": "text", "c": "text", "d": "text"}
    conditions = [
        {"target": "b", "source": "a", "operator": "is_not_empty", "value": None, "group": 0},
        {"target": "c", "source": "b", "operator": "is_not_empty", "value": None, "group": 0},
        {"target": "d", "source": "c", "operator": "is_not_empty", "value": None, "group": 0},
    ]
    all_filled = evaluate_conditions(fields, conditions, {"a": "x", "b": "x", "c": "x"})
    assert all_filled == {"a", "b", "c", "d"}

    # ``a`` leer -> b versteckt -> als leer gewertet -> c versteckt -> d versteckt.
    a_empty = evaluate_conditions(fields, conditions, {"a": "", "b": "x", "c": "x"})
    assert a_empty == {"a"}


def test_dropped_condition_source_not_in_form():
    """E8/#774: Bedingung mit Quelle ausserhalb des Formulars wird verworfen."""
    fields = {"notiz": "textarea"}
    conditions = [{"target": "notiz", "source": "geheim", "operator": "equals", "value": "x", "group": 0}]
    # ``geheim`` ist nicht im Formular -> Bedingung faellt weg -> Ziel sichtbar.
    assert evaluate_conditions(fields, conditions, {}) == {"notiz"}


def test_find_cycle_detects_direct_and_transitive():
    """Direkter (a<->b) und transitiver (a->b->c->a) Zyklus werden erkannt."""
    direct = find_cycle([("a", "b"), ("b", "a")])
    assert direct is not None
    assert direct[0] == direct[-1]
    assert set(direct) == {"a", "b"}

    transitive = find_cycle([("a", "b"), ("b", "c"), ("c", "a")])
    assert transitive is not None
    assert transitive[0] == transitive[-1]
    assert {"a", "b", "c"} <= set(transitive)


def test_find_cycle_none_on_dag():
    """Ein azyklischer Graph liefert ``None``."""
    assert find_cycle([("a", "b"), ("b", "c"), ("a", "c")]) is None
    assert find_cycle([]) is None


def test_operator_matrix_rejects_unknown_combo():
    """E9: Die Matrix erlaubt nur die spezifizierten Operator x Typ-Paare."""
    # boolean kennt weder contains noch is_empty.
    assert "contains" not in OPERATORS_BY_FIELD_TYPE["boolean"]
    assert "is_empty" not in OPERATORS_BY_FIELD_TYPE["boolean"]
    # select/multi_select kennen keine Ordnungsvergleiche.
    assert "greater_than" not in OPERATORS_BY_FIELD_TYPE["select"]
    assert "greater_than" not in OPERATORS_BY_FIELD_TYPE["multi_select"]
    # select kennt kein contains (nur Gleichheit/Leere).
    assert "contains" not in OPERATORS_BY_FIELD_TYPE["select"]
    # Gueltige Paare sind vorhanden.
    assert "contains" in OPERATORS_BY_FIELD_TYPE["multi_select"]
    assert "greater_than" in OPERATORS_BY_FIELD_TYPE["number"]
    assert "greater_than" in OPERATORS_BY_FIELD_TYPE["time"]
    assert OPERATORS_BY_FIELD_TYPE["boolean"] == {"equals", "not_equals"}


def test_evaluate_condition_empty_actual_fails_value_operators():
    """E9: Ein leerer Ist-Wert erfuellt keinen Wert-Operator (auch not_equals)."""
    assert evaluate_condition("equals", "text", "", "abc") is False
    assert evaluate_condition("not_equals", "text", "", "abc") is False
    assert evaluate_condition("contains", "text", None, "abc") is False
    assert evaluate_condition("greater_than", "number", None, 5) is False
    # is_empty/is_not_empty gelten weiterhin.
    assert evaluate_condition("is_empty", "text", "", None) is True
    assert evaluate_condition("is_not_empty", "text", "", None) is False
    # Unbekannter Operator (Defensive): schaltet nie sichtbar.
    assert evaluate_condition("bogus", "text", "x", "y") is False
