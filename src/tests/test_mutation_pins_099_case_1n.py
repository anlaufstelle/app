"""Mutations-Pins Welle L (0.99), Vollmutation #2174, Nacharbeit Batch case-1n.

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Batch ``case-1n`` sammelt zwei Survivor-Mutanten, die in den Batches
``deletion-b`` bzw. ``handover_note-c`` zunaechst als Fehlertext-/
Anzeige-Varianten eingestuft waren und deren Urteil die Familienreview
widerlegt hat:

* ``core.services.case.deletion.x__move_case_to_trash__mutmut_1`` —
  ``raise ValidationError(_("Der Fall liegt bereits im Papierkorb."))`` ->
  ``ValidationError(None)``: der Doppel-Loesch-Guard verliert seine
  Fehlermeldung vollstaendig, statt nur den Wortlaut zu variieren.
* ``core.services.case.handover_note.x__range_label_from__mutmut_2`` —
  ``return "00:00 – 23:59"`` -> ``return "XX00:00 – 23:59XX"``: der
  Rueckgabewert des Ganztags-Zweigs (gerendert als ``NoteBlock.range_label``
  im Uebergabekopf) ist ein funktionaler String, kein Logging-Text.

``pytestmark`` bindet ``_encryption_key`` modulweit (Lehre 112): der
Mutantenbaum hat keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Case
from core.services.case.deletion import _move_case_to_trash
from core.services.case.handover_note import Shift, ShiftWindow, _range_label_from

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _fall(facility, person, staff_user, titel):
    """Echte Fall-Zeile — kein Fixture-Fake."""
    return Case.objects.create(
        facility=facility,
        client=person,
        title=titel,
        status=Case.Status.OPEN,
        created_by=staff_user,
    )


# ============================================================================
# core.services.case.deletion._move_case_to_trash
# ============================================================================


class TestMoveCaseToTrashDoppelLoeschMeldung:
    """core.services.case.deletion.x__move_case_to_trash__mutmut_1."""

    def test_papierkorb_guard_traegt_die_originale_fehlermeldung(self, facility, client_identified, staff_user):
        """Toetet ``x__move_case_to_trash__mutmut_1`` (``ValidationError(_("Der
        Fall liegt bereits im Papierkorb."))`` -> ``ValidationError(None)``):
        ein zweiter Loeschversuch auf einen Fall, der bereits im Papierkorb
        liegt, muss der unterlegenen Person den ORIGINALEN Wortlaut zeigen,
        nicht das Wort ``None``.

        Original: ``exc_info.value.messages == ["Der Fall liegt bereits im
        Papierkorb."]``.
        Mutant: ``exc_info.value.messages == ["None"]``.
        """
        fall = _fall(facility, client_identified, staff_user, "Fall bereits im Papierkorb")
        Case.objects.filter(pk=fall.pk).update(is_deleted=True, deleted_at=timezone.now())
        fall.refresh_from_db()

        with pytest.raises(ValidationError) as exc_info:
            _move_case_to_trash(fall, staff_user)

        assert exc_info.value.messages == ["Der Fall liegt bereits im Papierkorb."]


# ============================================================================
# core.services.case.handover_note._range_label_from
# ============================================================================


class TestRangeLabelFromGanztag:
    """core.services.case.handover_note.x__range_label_from__mutmut_2."""

    def test_ganztag_liefert_den_exakten_labeltext(self):
        """Toetet ``x__range_label_from__mutmut_2`` (``"00:00 – 23:59"`` ->
        ``"XX00:00 – 23:59XX"``): der Ganztags-Zweig (``shift.time_filter is
        None``) liest ``fenster`` gar nicht — der Rueckgabewert selbst ist
        der einzige beobachtbare Effekt, gerendert als
        ``NoteBlock.range_label`` im Uebergabekopf.

        Original: ``"00:00 – 23:59"``.
        Mutant: ``"XX00:00 – 23:59XX"``.
        """
        schicht = Shift(date(2026, 6, 15), None)
        fenster = ShiftWindow(
            window_start=timezone.now(),
            window_end=timezone.now(),
            editable_until=timezone.now(),
        )

        assert _range_label_from(fenster, schicht) == "00:00 – 23:59"
