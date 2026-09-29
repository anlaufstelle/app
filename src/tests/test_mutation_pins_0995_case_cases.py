"""Pins fuer Fingerabdruck und Ereignis-Zuordnung der Fallakte (``core.services.case.cases``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 3 Mutanten.
Gruppen:

* ``_case_form_state_unchanged`` — ein leerer oder nicht-textueller
  Fingerabdruck steigt ohne Abfrage aus (``and`` statt ``or`` liesse die
  gesperrte Fingerabdruck-Lesung laufen; das Ergebnis bliebe ``False``).
* ``assign_event_to_case`` — ein Akteur ohne ``facility_id``-Attribut wird
  fail-closed mit der Einrichtungsmeldung abgewiesen, nicht mit
  ``AttributeError``, und nichts wird gelesen oder gesperrt.
* ``remove_event_from_case`` — haengt das Ereignis unter der Sperre an einem
  anderen als dem erwarteten Fall, nennt die Absage die Schreibmeldung
  (kein leerer Meldungstext).
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import Case, Event
from core.services.case.cases import (
    EventNotOnExpectedCaseError,
    _case_form_state_unchanged,
    assign_event_to_case,
    remove_event_from_case,
)
from core.services.case.state_contract import EVENT_WRITE_GONE_MESSAGE

pytestmark = pytest.mark.django_db

EINRICHTUNGSMELDUNG = "Fall gehört zu einer anderen Einrichtung."


class TestCaseFormStateUnchanged:
    """``_case_form_state_unchanged``: Frueh-Ausstieg ohne Abfrage."""

    @pytest.mark.parametrize("fingerabdruck", ["", 123, ["x"]], ids=["leer", "zahl", "liste"])
    def test_leerer_oder_nicht_textueller_fingerabdruck_fragt_nicht_ab(
        self, case_open, fingerabdruck, django_assert_num_queries
    ):
        """x__case_form_state_unchanged__mutmut_1: ``and`` statt ``or`` — ohne
        Frueh-Ausstieg liefe die gesperrte Fingerabdruck-Lesung."""
        with django_assert_num_queries(0):
            assert _case_form_state_unchanged(case_open, fingerabdruck) is False


class TestAssignEventToCase:
    """``assign_event_to_case``: Akteur ohne Einrichtung."""

    def test_akteur_ohne_einrichtung_wird_ohne_abfrage_abgewiesen(self, case_open, sample_event):
        """x_assign_event_to_case__mutmut_11: ohne ``getattr``-Default warf
        ``None`` als Akteur ``AttributeError`` statt der Einrichtungsmeldung.
        Vor der Absage laeuft nur die Savepoint-Klammer der Transaktion."""
        with CaptureQueriesContext(connection) as abfragen:
            with pytest.raises(ValidationError) as exc:
                assign_event_to_case(case_open, sample_event, None)
        assert exc.value.messages == [EINRICHTUNGSMELDUNG]
        assert {q["sql"].split()[0].upper() for q in abfragen.captured_queries} <= {"SAVEPOINT", "RELEASE", "ROLLBACK"}
        assert Event._base_manager.filter(pk=sample_event.pk, case__isnull=True).exists()


class TestRemoveEventFromCase:
    """``remove_event_from_case``: Absage bei abweichendem Fall unter der Sperre."""

    def test_ereignis_haengt_an_einem_anderen_als_dem_erwarteten_fall(
        self, facility, client_identified, staff_user, case_open, case_event
    ):
        """x_remove_event_from_case__mutmut_19: ohne Meldungstext verlor die
        Absage den Hinweis zum Neuladen."""
        anderer_fall = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Anderer Fall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        Event._base_manager.filter(pk=case_event.pk).update(case=anderer_fall)
        with pytest.raises(EventNotOnExpectedCaseError) as exc:
            remove_event_from_case(case_event, staff_user, expected_case=case_open)
        assert exc.value.messages == [str(EVENT_WRITE_GONE_MESSAGE)]
        assert Event._base_manager.filter(pk=case_event.pk, case=anderer_fall).exists()
