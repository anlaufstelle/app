"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.case.handover_note`` (Batch ``handover_note-c``).

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Vollmutations-Lauf auf main@27a4f481 (frischer Mutantenbaum), Batch
``handover_note-c`` (8 Survivor-Mutanten ueber vier Funktionen). Das Modul
ist auf drei Batches a/b/c verteilt; diese Datei pinnt ausschliesslich die
Mutanten des ``c``-Batches:

* ``compute_shift_window`` (mutmut_11/mutmut_14) — der ``time_filters``-
  Parameter wird beim Weiterreichen an ``editable_until`` verworfen
  (``None`` bzw. das Schluesselwortargument entfaellt ganz).
* ``assert_shift_date_supported`` (mutmut_4/mutmut_5) — die Datumsgrenzen
  (E-06) kippen von ``<``/``>`` auf ``<=``/``>=`` und lehnen die jeweils
  fachlich noch gueltigen Randtage faelschlich ab.
* ``_locked_existing_note`` (mutmut_4/mutmut_6) — der Zeilensperr-Lookup
  verliert entweder die ``facility``- oder die ``time_filter``-Klausel und
  faende damit die Notiz einer fremden Einrichtung bzw. einer fremden
  Schicht desselben Tages (Refs #2042, E-05).
* ``HandoverNoteConflictError.__init__`` (mutmut_1) — die uebergebene
  Fehlermeldung wird beim ``super().__init__`` durch ``None`` ersetzt.

``x__range_label_from__mutmut_2`` (reines Anzeige-Literal, s.
``reports/m-pins-handover_note-c-report.md``) ist ``skip`` und bleibt ohne
Pin.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import ShiftHandoverNote, TimeFilter
from core.services.case.handover_note import (
    HandoverNoteConflictError,
    Shift,
    _locked_existing_note,
    assert_shift_date_supported,
    compute_shift_window,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def tf_spaet(facility):
    return TimeFilter.objects.create(
        facility=facility, label="Spätdienst", start_time=time(14, 0), end_time=time(22, 0), sort_order=1
    )


class TestComputeShiftWindowReichtTimeFiltersUnveraendertDurch:
    """core.services.case.handover_note.x_compute_shift_window__mutmut_11/_14."""

    def test_editable_until_bekommt_die_uebergebene_time_filters_liste_nicht_none(self, facility):
        """Toetet ``x_compute_shift_window__mutmut_11`` (uebergibt
        ``time_filters=None`` an ``editable_until`` statt des Parameters) und
        ``x_compute_shift_window__mutmut_14`` (laesst das Schluesselwort-
        argument ganz weg, faellt intern ebenso auf ``None`` zurueck):
        ``_build_block`` reicht die EINMAL ermittelte Zeitfilterliste an
        beide Uebergabe-Bloecke weiter — ein Spy auf ``editable_until``
        belegt, dass ``compute_shift_window`` genau diese Liste (nicht
        ``None``) durchreicht.
        """
        schicht = Shift(date(2026, 6, 15), None)
        time_filters = [object()]
        sentinel = object()
        with patch("core.services.case.handover_note.editable_until", return_value=sentinel) as spy:
            ergebnis = compute_shift_window(facility, schicht, time_filters=time_filters)
        spy.assert_called_once_with(facility, schicht, time_filters=time_filters)
        assert ergebnis.editable_until is sentinel


class TestAssertShiftDateSupportedGrenzen:
    """core.services.case.handover_note.x_assert_shift_date_supported__mutmut_4/_5."""

    def test_min_datum_selbst_ist_noch_erlaubt(self):
        """Toetet ``x_assert_shift_date_supported__mutmut_4`` (``<`` wird zu
        ``<=`` an der Untergrenze): ``_MIN_SHIFT_DATE`` (2000-01-01) ist der
        fachlich fruehest unterstuetzte Tag (E-06) und darf keine
        ``ValidationError`` ausloesen.
        """
        schicht = Shift(date(2000, 1, 1), None)
        assert_shift_date_supported(schicht, today=date(2026, 1, 1))

    def test_max_zukunftstag_ist_noch_erlaubt(self):
        """Toetet ``x_assert_shift_date_supported__mutmut_5`` (``>`` wird zu
        ``>=`` an der Obergrenze): ``heute + 730 Tage`` (``_MAX_SHIFT_FUTURE``)
        ist der fachlich letzte erlaubte Zukunftstag (E-06) und darf keine
        ``ValidationError`` ausloesen.
        """
        heute = date(2026, 1, 1)
        schicht = Shift(heute + timedelta(days=730), None)
        assert_shift_date_supported(schicht, today=heute)


class TestLockedExistingNoteMandantenUndSchichtgrenze:
    """core.services.case.handover_note.x__locked_existing_note__mutmut_4/_6."""

    def test_findet_niemals_die_notiz_einer_fremden_einrichtung(self, facility, second_facility):
        """Toetet ``x__locked_existing_note__mutmut_4`` (die Klausel
        ``facility=facility`` entfaellt komplett): eine Notiz derselben
        Schicht in einer FREMDEN Einrichtung darf ``_locked_existing_note``
        fuer die eigene Einrichtung nie liefern — sonst ueberschriebe/
        loeschte ``save_note`` eine Notiz der falschen Einrichtung
        (Mandantengrenze, Refs #2042, E-05).
        """
        schicht = Shift(date(2026, 6, 15), None)
        jetzt = timezone.now()
        ShiftHandoverNote.objects.create(
            facility=second_facility,
            shift_date=schicht.shift_date,
            time_filter=None,
            note=None,
            window_start=jetzt,
            window_end=jetzt,
            editable_until=jetzt,
        )
        assert _locked_existing_note(facility, schicht) is None

    def test_findet_niemals_die_notiz_einer_anderen_schicht_desselben_tages(self, facility, tf_spaet):
        """Toetet ``x__locked_existing_note__mutmut_6`` (die Klausel
        ``time_filter=shift.time_filter`` entfaellt komplett): eine Notiz
        desselben Tages, aber einer ANDEREN Schicht (Spaetdienst statt
        Ganztag) darf ``_locked_existing_note`` fuer die Ganztags-Schicht
        nicht liefern — sonst ueberschriebe/loeschte ``save_note`` die
        Notiz der falschen Schicht (Datenverlust, Refs #2042, E-05). Es gibt
        bewusst NUR eine Distraktor-Zeile: die pk ist eine UUID (keine
        verlaessliche Sortierung), ein Zwei-Zeilen-Tiebreak waere nicht
        deterministisch.
        """
        tag = date(2026, 6, 15)
        jetzt = timezone.now()
        ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=tag,
            time_filter=tf_spaet,
            note=None,
            window_start=jetzt,
            window_end=jetzt,
            editable_until=jetzt,
        )
        assert _locked_existing_note(facility, Shift(tag, None)) is None


class TestHandoverNoteConflictErrorMessage:
    """core.services.case.handover_note.xǁHandoverNoteConflictErrorǁ__init____mutmut_1."""

    def test_traegt_die_originale_fehlermeldung_weiter(self):
        """Toetet ``xǁHandoverNoteConflictErrorǁ__init____mutmut_1``
        (``super().__init__(None)`` statt der uebergebenen ``message``):
        Django haengt bei ``message=None`` den literalen String ``"None"``
        an ``.messages`` — die Oberflaeche zeigt der unterlegenen Person dann
        keine verstaendliche Fehlermeldung, sondern das Wort ``None``.
        """
        text = "Diese Schichtnotiz wurde geändert, während Sie verglichen haben."
        fehler = HandoverNoteConflictError(text, bestand=None, eingabe="Entwurf")
        assert fehler.messages == [text]
