"""Pins for note relation loading and role-restriction feedback.

Source: mutation delta of the release-0.99.8 checkpoint, 2 mutants.
Groups: eager shift loading (1), role error message (1).
Follows test_mutation_pins_0997_case_cases.py.
"""

from datetime import date, datetime, time

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import ShiftHandoverNote, TimeFilter
from core.services.case.handover_note import Shift, get_note, save_note

pytestmark = pytest.mark.django_db


@pytest.mark.usefixtures("_encryption_key")
def test_get_note_preloads_its_time_filter(facility, staff_user, django_assert_num_queries):
    """A returned note's shift label is available without another database read.

    Kills x_get_note__mutmut_6 through get_note.
    """
    time_filter = TimeFilter.objects.create(facility=facility, label="Morning", start_time=time(6), end_time=time(14))
    shift = Shift(date(2026, 6, 15), time_filter)
    original = save_note(facility, shift, staff_user, "Shift note", now=timezone.make_aware(datetime(2026, 6, 15, 10)))
    note = get_note(facility, shift)
    assert note.pk == original.pk
    with django_assert_num_queries(0):
        assert note.time_filter.label == time_filter.label


def test_assistant_receives_the_complete_role_restriction(facility, assistant_user):
    """An assistant cannot write a note and receives the full role explanation.

    Kills x_save_note__mutmut_5 through save_note.
    """
    with pytest.raises(PermissionDenied) as error:
        save_note(facility, Shift(date(2026, 6, 15), None), assistant_user, "Shift note")
    assert str(error.value) == _("Übergabenotizen sind Fachkräften, Leitung und Anwendungsbetreuung vorbehalten.")
    assert not ShiftHandoverNote.objects.for_facility(facility).exists()
