"""E2E-Paritätstest: JS-Evaluator gegen die gemeinsame Spezifikation (M1 P7, Refs #1057).

Die Frontend-Implementierung (``src/static/js/alpine/conditions.js``) und die
Python-Seite (``src/core/services/conditions.py``) MÜSSEN semantisch identisch
sein. ``src/tests/test_conditions_evaluator.py`` prüft die Python-Seite gegen die
ausführbare Spezifikation ``src/tests/data/condition_vectors.json`` (83 Vektoren);
dieser Test prüft die **JS-Seite** gegen DIESELBEN Vektoren im echten Browser —
die einzige Stelle, an der FE/BE-Drift mechanisch auffällt (E13/ADR-035).

``conditions.js`` lädt global über ``base.html``, daher exponiert jede eingeloggte
Seite (das Dashboard reicht) ``window.AnlaufstelleConditions``. Der ``page.evaluate``-
Aufruf läuft via CDP und ist von der Seiten-CSP nicht betroffen (kein separater
``bypass_csp``-Kontext nötig — manuell-first bestätigt).

Bewusste Auslassung (plan-mandatiert, E11): KEIN Cross-Assert für ``parseFloat("5abc")``
— hier divergiert JS (``NaN``→null) bewusst von der Python-Seite; das ist kein Drift.
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

# ``data/`` liegt unter ``src/tests/`` — parents[1] von ``src/tests/e2e/<datei>``.
_VECTORS_PATH = Path(__file__).parents[1] / "data" / "condition_vectors.json"

# JS, das ``window.AnlaufstelleConditions.evaluate`` für JEDEN Vektor in einem
# einzigen CDP-Roundtrip aufruft (statt 83 Einzel-Roundtrips) und die sortierte
# Sichtbarkeitsmenge je Vektorname zurückgibt.
_EVAL_ALL_JS = """
(cases) => cases.map((c) => ({
    name: c.name,
    got: Array.from(window.AnlaufstelleConditions.evaluate(c.fields, c.conditions, c.values)).sort(),
}))
"""

_READY_JS = "() => !!(window.AnlaufstelleConditions && typeof window.AnlaufstelleConditions.evaluate === 'function')"


def _load_cases() -> list[dict]:
    return json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))["cases"]


class TestEvaluatorParity:
    """Der JS-Evaluator liefert für jeden Vektor exakt die erwartete Sichtbarkeitsmenge."""

    def test_js_evaluate_matches_all_vectors(self, authenticated_page):
        page = authenticated_page
        # ``conditions.js`` lädt via ``base.html`` mit ``defer`` — kurz auf das
        # Namespace-Objekt warten (``authenticated_page`` startet auf ``/``).
        page.wait_for_function(_READY_JS, timeout=10000)

        cases = _load_cases()
        # Härtungs-Commit f10c316a hob die Vektoren auf 83 — der JS-Selbsttest aus
        # P5 kannte nur 60; hier gegen ALLE prüfen (der Kern der Paritäts-Sicherung).
        assert len(cases) >= 83, f"Erwartet >= 83 Vektoren, geladen {len(cases)}"

        results = page.evaluate(_EVAL_ALL_JS, cases)
        got_by_name = {row["name"]: row["got"] for row in results}

        mismatches = []
        for case in cases:
            expected = sorted(case["expected_visible"])
            got = got_by_name.get(case["name"])
            if got != expected:
                mismatches.append(f"{case['name']}: js={got} != python-spec={expected}")

        assert not mismatches, "JS/Python-Evaluator-Drift in {}/{} Vektoren:\n{}".format(
            len(mismatches), len(cases), "\n".join(mismatches)
        )

    def test_js_normalize_matches_python_conventions(self, authenticated_page):
        """Die JS-``normalize``-Schicht (E11) — Fälle, die die (bereits normalisierten,
        E14) Vektoren nicht abdecken. Insbesondere die time-Falle ``"HH:MM"`` →
        ``"HH:MM:SS"`` und ``""`` → ``null`` bei number/select sowie checked → true.
        """
        page = authenticated_page
        page.wait_for_function(_READY_JS, timeout=10000)

        def norm(field_type, value):
            return page.evaluate(
                "([t, v]) => window.AnlaufstelleConditions.normalize(t, v)",
                [field_type, value],
            )

        # time: DOM liefert ``input[type=time].value`` als ``"HH:MM"`` → ``":00"``
        # anhängen, sonst schlägt der equals-Vergleich gegen Pythons ``"HH:MM:SS"`` fehl.
        assert norm("time", "14:30") == "14:30:00"
        assert norm("time", "09:05:15") == "09:05:15"
        assert norm("time", "") is None
        # number: ``""`` → null (nicht 0); gültige Zahl → Number.
        assert norm("number", "") is None
        assert norm("number", "5") == 5
        # select/text: ``""`` → null (leerer Wert, nicht Leerstring).
        assert norm("select", "") is None
        assert norm("select", "option_a") == "option_a"
        # boolean: checkbox.checked → true/false.
        assert norm("boolean", True) is True
        assert norm("boolean", False) is False
        # date: ``""`` → null (DOM liefert sonst bereits ``"YYYY-MM-DD"``).
        assert norm("date", "") is None
