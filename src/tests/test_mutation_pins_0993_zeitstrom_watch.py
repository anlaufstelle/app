"""Pins fuer die Projektion der Uebergabenotiz im Zeitstrom-Watch (``core.services.zeitstrom_watch``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 3 Mutanten in
``_handover_note_projection``. Gruppen:

* Die angezeigte Schicht traegt das gewaehlte Zeitfenster
  (``Shift(target_date, time_filter)``) — sonst vergliche die Notiz den
  zeitfensterlosen Ganztag.
* Die Zeitfenster-ID jedes Blocks geht in den Validator ein (``""`` fuer den
  Ganztag), damit ein Wechsel des Zeitfensters die Ansicht erneuert.

``build_handover_note_context`` wird ersetzt: der Pin haelt fest, mit welcher
Schicht die Projektion ihn ruft und was sie aus den Bloecken macht.
"""

from datetime import date
from types import SimpleNamespace

from core.services import zeitstrom_watch
from core.services.case import handover_note
from core.services.case.handover_note import Shift

TAG = date(2026, 9, 23)


def _block(time_filter_id):
    return SimpleNamespace(
        kind="abgebend",
        shift=SimpleNamespace(shift_date=TAG, time_filter_id=time_filter_id),
        label="Frueh",
        range_label="06–14",
        text="Notiz",
        author="teststaff",
        updated_at=None,
        editable=True,
        editable_until=None,
    )


class TestHandoverNoteProjection:
    def test_schicht_traegt_das_zeitfenster_und_bloecke_ihre_id(self, monkeypatch):
        """x__handover_note_projection__mutmut_3 (``time_filter`` faellt aus
        der Schicht), _21 und _22 (Zeitfenster-ID fehlt im Validator bzw.
        ``or``/``and`` vertauscht: ``"7"`` wuerde ``""``)."""
        zeitfenster = SimpleNamespace(pk=7)
        aufrufe = []

        def _kontext(facility, user, shift):
            aufrufe.append(shift)
            return {"can_write": True, "blocks": [_block("7"), _block("")]}

        monkeypatch.setattr(handover_note, "build_handover_note_context", _kontext)
        state = SimpleNamespace(target_date=TAG, time_filter=zeitfenster)
        projektion = zeitstrom_watch._handover_note_projection("einrichtung", "person", state)
        assert aufrufe == [Shift(TAG, zeitfenster)]
        assert projektion == {
            "can_write": True,
            "blocks": (
                ("abgebend", TAG, "7", "Frueh", "06–14", "Notiz", "teststaff", None, True, None),
                ("abgebend", TAG, "", "Frueh", "06–14", "Notiz", "teststaff", None, True, None),
            ),
        }
