"""Pins fuer den Fingerabdruck im Fallformular (``core.forms.cases``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 2 Mutanten in
``CaseForm.__init__``: der Leerwert von ``form_state_token`` wird zum
Platzhalter ``"XXXX"`` — beim Anlegen (Anfangswert) und beim gebundenen
Bearbeitungsformular ohne mitgeschickten Fingerabdruck (``data.get``-
Default). Beobachtbar ist das am Formularattribut; das Template gibt das
versteckte Feld ``expected_form_state`` nur im Bearbeitungszweig und nur bei
gesetztem Token aus.
"""

import pytest

from core.forms.cases import CaseForm

pytestmark = pytest.mark.django_db


class TestCaseFormInit:
    """``CaseForm.__init__``: kein Fingerabdruck ohne Bearbeitungsstand."""

    def test_anlageformular_traegt_keinen_fingerabdruck(self, facility, staff_user):
        """xǁCaseFormǁ__init____mutmut_35: das ungebundene Anlageformular
        trug ``"XXXX"`` als Token."""
        form = CaseForm(facility=facility, user=staff_user)
        assert form.form_state_token == ""

    def test_gebundenes_bearbeitungsformular_ohne_mitgeschickten_fingerabdruck(self, case_open, facility, staff_user):
        """xǁCaseFormǁ__init____mutmut_44: ohne ``expected_form_state`` in den
        Formulardaten wurde ``"XXXX"`` zum Token der Neu-Anzeige."""
        form = CaseForm(data={"title": ""}, instance=case_open, facility=facility, user=staff_user)
        assert form.form_state_token == ""
