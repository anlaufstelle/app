"""Pin-Tests des Mutations-Delta-Checkpoints der Welle M (release-0.99.1) zu
``core.services.zeitstrom_watch``.

Quelle: Ticket #2322 (https://github.com/anlaufstelle/app/issues/2322),
Survivor-Triage des Checkpoint-Deltas ``6435c18e`` (BASE v0.99.0). Gepinnt
werden die fuenf Survivor dieses Moduls in zwei Funktionen:

* ``build_zeitstrom_watch_snapshot`` — der aufgeloeste ``state.live_context``
  muss unveraendert an ``build_tally_summary`` durchgereicht werden; ein
  Wegfall (``context=None``) laesst die Strichlisten-Aggregation auf das
  AKTUELLE statt das angeforderte Zeitfenster zurueckfallen.
* ``_handover_note_projection`` — die angezeigte Schicht
  (``Shift(state.target_date, state.time_filter)``) und die projizierte
  ``time_filter_id`` je Block muessen den REALEN Zeitfilter-Bezug tragen;
  sonst meldet der HMAC-Validator Aenderungen zwischen Zeitfiltern nicht
  mehr zuverlaessig.

Beide Funktionen werden ueber einen Spion auf ihre modul-lokal importierte
Abhaengigkeit gepinnt (Muster
``src/tests/test_mutation_pins_099_neu_1.py::TestCommitStagedUploadQuotaBackstopAttribution``)
statt ueber echte Uebergabenotiz-/Strichlisten-Datensaetze — das haelt die
Tests schnell und isoliert auf die hier zu pinnenden Weitergabe-Fehler, ohne
die referenzierten Service-Funktionen selbst erneut zu pruefen (die sind
anderswo gepinnt).
"""

from types import SimpleNamespace

import pytest

from core.models import TimeFilter
from core.services import zeitstrom_watch
from core.services.case import handover_note as handover_note_module
from core.services.case.handover_note import NoteBlock, Shift
from core.services.zeitstrom_filters import ZeitstromState
from core.services.zeitstrom_watch import _handover_note_projection, build_zeitstrom_watch_snapshot

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _state(*, target_date, time_filter, view=""):
    return ZeitstromState(
        target_date=target_date,
        time_filter=time_filter,
        time_filters=(time_filter,) if time_filter is not None else (),
        live_context=SimpleNamespace(time_filter=None, shift_date=target_date, window_start=None, window_end=None),
        feed_type="",
        doc_type_id="",
        view=view,
        local_date=target_date,
    )


class TestBuildZeitstromWatchSnapshotLiveContext:
    def test_live_context_fliesst_in_die_tally_summary(self, monkeypatch, facility, staff_user):
        """Pin: ``x_build_zeitstrom_watch_snapshot__mutmut_18`` und
        ``…__mutmut_21`` — der an ``build_tally_summary`` weitergereichte
        ``context``-Wert (``state.live_context``) faellt auf ``None`` zurueck
        (bzw. das Argument entfaellt, derselbe Effekt).

        Im standardmaessigen Live-Tally-Kontext (kein Zeitfilter, heutiges
        Datum) MUSS ``build_zeitstrom_watch_snapshot`` genau
        ``state.live_context`` weiterreichen — nicht ``None``, das intern auf
        das AKTUELLE statt das angeforderte Fenster zurueckfiele.
        """
        import datetime

        today = datetime.date(2026, 1, 15)
        eigener_live_context = SimpleNamespace(time_filter=None, shift_date=today, window_start=None, window_end=None)
        state = ZeitstromState(
            target_date=today,
            time_filter=None,
            time_filters=(),
            live_context=eigener_live_context,
            feed_type="",
            doc_type_id="",
            view="",
            local_date=today,
        )
        assert state.is_live_tally_context

        calls = []

        def _spy(facility_arg, user_arg, *, context=None):
            calls.append(context)
            return {
                "time_filter": None,
                "shift_date": today,
                "window_start": None,
                "window_end": None,
                "rows": [],
            }

        monkeypatch.setattr(zeitstrom_watch, "build_tally_summary", _spy)

        build_zeitstrom_watch_snapshot(staff_user, facility, state)

        assert calls == [eigener_live_context]


class TestHandoverNoteProjectionShift:
    def test_angezeigte_schicht_traegt_den_angeforderten_zeitfilter(self, monkeypatch, facility, staff_user):
        """Pin: ``x__handover_note_projection__mutmut_3`` — ``Shift(...)``
        erhaelt ``None`` statt ``state.time_filter``.

        Original: die angezeigte Schicht traegt den in ``state`` gewaehlten
        Zeitfilter. Mutant: die Watch-Projektion laedt die Uebergabenotiz fuer
        den UNGEFILTERTEN (Ganztag-)Kontext, unabhaengig davon, welchen
        Zeitfilter der Request tatsaechlich gewaehlt hat.
        """
        import datetime

        zeitfilter = TimeFilter.objects.create(
            facility=facility,
            label="Pins-0991-Fruehschicht",
            start_time=datetime.time(6, 0),
            end_time=datetime.time(14, 0),
        )
        heute = datetime.date(2026, 1, 15)
        state = _state(target_date=heute, time_filter=zeitfilter)

        calls = []

        def _spy(facility_arg, user_arg, displayed_shift, *, now=None):
            calls.append(displayed_shift)
            return None

        monkeypatch.setattr(handover_note_module, "build_handover_note_context", _spy)

        _handover_note_projection(facility, staff_user, state)

        assert len(calls) == 1
        assert calls[0].shift_date == heute
        assert calls[0].time_filter == zeitfilter
        assert calls[0].time_filter is not None


class TestHandoverNoteProjectionTimeFilterId:
    def test_time_filter_id_projektion_bleibt_exakt(self, facility, staff_user, monkeypatch):
        """Pin: ``x__handover_note_projection__mutmut_21`` (die projizierte
        ``time_filter_id`` wird konstant zu ``str(None)``) und ``…__mutmut_22``
        (``time_filter_id or ""`` wird zu ``time_filter_id and ""``).

        Zwei Bloecke — einer MIT, einer OHNE Zeitfilter. Original: die
        Projektion traegt je Block den exakten UUID-Text bzw. den leeren
        Text. Mutant 21: beide werden zum LITERALEN Text ``"None"``. Mutant
        22: beide werden zum leeren Text (die Boolesche Operator-Umkehrung
        macht den Fallback bedingungslos).
        """
        import datetime

        zeitfilter = TimeFilter.objects.create(
            facility=facility,
            label="Pins-0991-Spaetschicht",
            start_time=datetime.time(14, 0),
            end_time=datetime.time(22, 0),
        )
        heute = datetime.date(2026, 1, 15)

        block_mit_filter = NoteBlock(
            kind="current",
            shift=Shift(heute, zeitfilter),
            label="Mit Zeitfilter",
            range_label="",
            text="",
            author=None,
            updated_at=None,
            editable=False,
            editable_until=None,
        )
        block_ohne_filter = NoteBlock(
            kind="previous",
            shift=Shift(heute, None),
            label="Ohne Zeitfilter",
            range_label="",
            text="",
            author=None,
            updated_at=None,
            editable=False,
            editable_until=None,
        )

        monkeypatch.setattr(
            handover_note_module,
            "build_handover_note_context",
            lambda *args, **kwargs: {"can_write": False, "blocks": (block_mit_filter, block_ohne_filter)},
        )

        state = _state(target_date=heute, time_filter=zeitfilter, view="uebergabe")
        ergebnis = _handover_note_projection(facility, staff_user, state)

        assert ergebnis is not None
        projizierte_ids = [eintrag[2] for eintrag in ergebnis["blocks"]]
        assert projizierte_ids == [str(zeitfilter.pk), ""]
        assert "None" not in projizierte_ids
