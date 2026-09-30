"""Pins for the conflict messages of saving a shift handover note (``core.services.case.handover_note``).

Source: mutation delta of the checkpoint release-0.99.5, 2 mutants.
Group:

* ``save_note`` — the ``HandoverNoteConflictError`` of the stale comparison
  (``base_revision`` no longer matches the stored version) and of the
  creation race (a parallel request stored a different text first) loses its
  message.

Both conflicts are shown to the person who lost; the stored version and her
own input travel with the exception, the message explains what happened.
The creation race is replayed deterministically: right after the first
observed row lock on the note table (``tests._row_lock_helpers``,
``nach_sperre``) — the lock finds no row yet — a parallel save stores the
winning text, so the insert runs into the unique constraint. The shift is the
whole current day of a facility without time filters, taken from
``timezone.localtime()``.
"""

import pytest
from django.utils import timezone

from core.models import ShiftHandoverNote
from core.services.case.handover_note import HandoverNoteConflictError, Shift, decrypt_note, save_note
from tests._row_lock_helpers import mitschnitt

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

NOTE_TABLE = "core_shifthandovernote"
STALE_COMPARISON_TEXT = (
    "Diese Schichtnotiz wurde geändert, während Sie verglichen haben. "
    "Bitte die jetzt gespeicherte Fassung lesen und erneut entscheiden — "
    "die eigene Eingabe ist im Feld erhalten."
)
CREATION_RACE_TEXT = (
    "Für diese Schicht wurde soeben eine andere Notiz gespeichert. "
    "Bitte beide Fassungen vergleichen: Der gespeicherte Text steht oben, "
    "die eigene Eingabe ist im Feld erhalten."
)


@pytest.fixture
def now():
    return timezone.localtime()


@pytest.fixture
def shift(now):
    return Shift(now.date(), None)


class TestSaveNoteConflictMessages:
    """``save_note``: both conflict refusals keep their message."""

    def test_stale_comparison_is_refused_with_its_message(self, facility, staff_user, shift, now):
        """x_save_note__mutmut_42: the conflict of an outdated
        ``base_revision`` lost its message; the stored text stays."""
        save_note(facility, shift, staff_user, "Stored", now=now)
        with pytest.raises(HandoverNoteConflictError) as excinfo:
            save_note(facility, shift, staff_user, "Own input", now=now, base_revision="outdated")
        assert excinfo.value.messages == [STALE_COMPARISON_TEXT]
        assert excinfo.value.eingabe == "Own input"
        assert decrypt_note(ShiftHandoverNote.objects.get(facility=facility)) == "Stored"

    def test_creation_race_with_a_different_text_is_refused_with_its_message(
        self, facility, staff_user, lead_user, shift, now
    ):
        """x_save_note__mutmut_147: the conflict of a lost creation race with
        a different text lost its message."""
        with mitschnitt() as queries, pytest.raises(HandoverNoteConflictError) as excinfo:
            queries.nach_sperre(NOTE_TABLE, lambda: save_note(facility, shift, lead_user, "Winner", now=now))
            save_note(facility, shift, staff_user, "Loser", now=now)
        assert queries.offene_schranken() == []
        assert excinfo.value.messages == [CREATION_RACE_TEXT]
        assert decrypt_note(excinfo.value.bestand) == "Winner"
        assert excinfo.value.eingabe == "Loser"
