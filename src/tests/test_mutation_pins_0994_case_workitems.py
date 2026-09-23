"""Pins fuer den Formular-Fingerabdruck der Aufgaben (``core.services.case.workitems``).

Quelle: Mutations-Delta des Checkpoints release-0.99.3, 1 Mutant.
Gruppe:

* ``_form_state_unchanged`` — ein Fingerabdruck, der kein ``str`` ist, wird
  nicht mehr sofort abgelehnt.

Der einzige Aufrufer liefert einen POST-String oder ``None``; der Vertrag ist
trotzdem, dass nur ein nichtleerer ``str`` ueberhaupt verglichen wird. Die
Bytes des ECHTEN Fingerabdrucks sind der Fall, in dem das auffaellt:
``constant_time_compare`` wandelt beide Seiten in Bytes und haelt sie fuer
gleich.
"""

import pytest

from core.services.case.workitems import _form_state_unchanged, workitem_form_state_token

pytestmark = pytest.mark.django_db


class TestFormStateUnchanged:
    """``_form_state_unchanged``: nur ein nichtleerer ``str`` wird verglichen."""

    def test_bytes_des_echten_fingerabdrucks_gelten_nicht(self, sample_workitem, django_assert_num_queries):
        """x__form_state_unchanged__mutmut_1 (``and`` statt ``or``): die Bytes
        des gueltigen Fingerabdrucks liefen in die Sperrabfrage und galten
        als unveraendert."""
        token = workitem_form_state_token(sample_workitem)
        with django_assert_num_queries(0):
            assert _form_state_unchanged(sample_workitem, token.encode()) is False

    def test_gegenprobe_str_des_echten_fingerabdrucks_gilt(self, sample_workitem):
        assert _form_state_unchanged(sample_workitem, workitem_form_state_token(sample_workitem)) is True
