"""DST-Übergänge Europe/Berlin 2026 + Aware/Naive-DateTime-Mix.

Refs #927, Master #922.

Dokumentiert das System-Verhalten an den DST-Grenzen:
- Spring-Forward 2026-03-29 02:00 → 03:00 (nicht-existente Stunde).
- Fall-Back  2026-10-25 03:00 → 02:00 (ambige Stunde, ``fold``).
- Aware/Naive-Mix im Event-Service.

Die Tests sind Verhaltens-Snapshots — sie schützen gegen Regressionen, wenn
Python/zoneinfo, Django oder unser Code die Auflösung ändern. Sie sagen
nichts darüber aus, ob das Verhalten *fachlich richtig* ist (das wäre eine
Produkt-Entscheidung).
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone, translation

from core.forms.events import EventMetaForm
from core.forms.fields import AmbiguousWallClock, DstAwareDateTimeField
from core.models import Event, TimeFilter
from core.services.events import build_feed_items, get_time_range
from core.services.events.tally import current_tally_context
from core.services.zeitstrom_filters import resolve_zeitstrom_state

BERLIN = ZoneInfo("Europe/Berlin")
UTC = dt.UTC


class TestZoneInfoSpringForward:
    """2026-03-29 02:00 → 03:00 Europe/Berlin — die Stunde 02:00–03:00 existiert nicht.

    Python ``zoneinfo`` interpretiert "nicht-existent" deterministisch: die
    angegebene lokale Zeit wird als gültig akzeptiert; der UTC-Offset wird vor
    der DST-Schaltung (CET, +01:00) verwendet, was effektiv "wir bleiben in
    Winterzeit" bedeutet (02:30 CET = 01:30 UTC). Das deckt sich mit dem
    Default ``fold=0``.
    """

    def test_spring_forward_resolves_with_prefold_offset(self):
        nonexistent = dt.datetime(2026, 3, 29, 2, 30, tzinfo=BERLIN)
        utc = nonexistent.astimezone(UTC)
        # Erwartung: zoneinfo wählt den Pre-Fold-Offset (+01:00) → 01:30 UTC.
        # Falls Python/zoneinfo in einer zukünftigen Version den Post-Fold-Offset
        # (+02:00) bevorzugt, würde 00:30 UTC entstehen — beides ist valides
        # Library-Verhalten, der Test deckt die heute beobachtete Variante.
        assert utc == dt.datetime(2026, 3, 29, 1, 30, tzinfo=UTC), (
            f"Unerwartete UTC-Auflösung der nicht-existenten lokalen Zeit: {utc}"
        )

    def test_spring_forward_fold_variants_differ_by_one_hour(self):
        """Auch bei nicht-existenten Zeiten interpretiert zoneinfo ``fold`` —
        fold=0 wählt den Pre-Fold-Offset (+01:00), fold=1 den Post-Fold-Offset
        (+02:00). Der UTC-Instant unterscheidet sich um eine Stunde.

        Das ist überraschend — in der PEP-495-Semantik ist ``fold`` für
        ambige Zeiten definiert. Python/zoneinfo wendet es aber auch auf
        Lücken an. Test dokumentiert dieses Verhalten als Regressions-Schutz.
        """
        f0 = dt.datetime(2026, 3, 29, 2, 30, tzinfo=BERLIN, fold=0).astimezone(UTC)
        f1 = dt.datetime(2026, 3, 29, 2, 30, tzinfo=BERLIN, fold=1).astimezone(UTC)
        assert (f0 - f1) == dt.timedelta(hours=1)


class TestZoneInfoFallBack:
    """2026-10-25 03:00 → 02:00 Europe/Berlin — die Stunde 02:00–03:00 tritt zweimal auf.

    Bei ambiger Zeit wählt zoneinfo per Default ``fold=0`` (= das erste Auftreten,
    noch in Sommerzeit CEST +02:00). ``fold=1`` selektiert das zweite Auftreten
    in Winterzeit CET +01:00.
    """

    def test_fall_back_fold_zero_is_summer_time(self):
        ambiguous = dt.datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0)
        utc = ambiguous.astimezone(UTC)
        # fold=0 → erstes Auftreten von 02:30, noch CEST +02:00 → 00:30 UTC
        assert utc == dt.datetime(2026, 10, 25, 0, 30, tzinfo=UTC)

    def test_fall_back_fold_one_is_winter_time(self):
        ambiguous = dt.datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=1)
        utc = ambiguous.astimezone(UTC)
        # fold=1 → zweites Auftreten von 02:30, schon CET +01:00 → 01:30 UTC
        assert utc == dt.datetime(2026, 10, 25, 1, 30, tzinfo=UTC)

    def test_fall_back_fold_variants_differ_by_one_hour(self):
        f0 = dt.datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0).astimezone(UTC)
        f1 = dt.datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=1).astimezone(UTC)
        assert (f1 - f0) == dt.timedelta(hours=1)


@pytest.mark.django_db
class TestEventDstRoundTrip:
    """Roundtrip: Event mit DST-Grenz-Timestamp speichern und neu laden.

    PostgreSQL speichert ``timestamptz`` als UTC-Instant — die Berliner
    Wallclock-Zeit ist nur eine Darstellungssicht. Beim Reload muss die
    UTC-Instanz identisch sein.
    """

    def test_spring_forward_event_persists_as_instant(self, facility, staff_user, doc_type_contact):
        occurred = dt.datetime(2026, 3, 29, 2, 30, tzinfo=BERLIN)
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=occurred,
            data_json={"dauer": 5},
            created_by=staff_user,
        )
        reloaded = Event.objects.get(pk=event.pk)
        assert reloaded.occurred_at.astimezone(UTC) == occurred.astimezone(UTC)

    def test_fall_back_fold_zero_event_persists(self, facility, staff_user, doc_type_contact):
        occurred = dt.datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0)
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=occurred,
            data_json={"dauer": 5},
            created_by=staff_user,
        )
        reloaded = Event.objects.get(pk=event.pk)
        assert reloaded.occurred_at.astimezone(UTC) == occurred.astimezone(UTC)

    def test_fall_back_fold_one_event_persists_distinct(self, facility, staff_user, doc_type_contact):
        first = dt.datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0)
        second = dt.datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=1)
        e1 = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=first,
            data_json={"dauer": 5},
            created_by=staff_user,
        )
        e2 = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=second,
            data_json={"dauer": 5},
            created_by=staff_user,
        )
        e1.refresh_from_db()
        e2.refresh_from_db()
        # Trotz identischer Wallclock-Repräsentation sind die UTC-Instants
        # unterschiedlich — fold=0 vs fold=1.
        assert e2.occurred_at - e1.occurred_at == dt.timedelta(hours=1)


@pytest.mark.django_db
class TestAwareNaiveMix:
    """USE_TZ=True erlaubt nur aware datetimes in Querys/Models.

    Naive datetimes werden von Django mit ``RuntimeWarning`` quittiert und
    implizit als ``TIME_ZONE`` (Europe/Berlin) interpretiert. Wir
    dokumentieren dieses Verhalten — der Anwendungscode sollte aber immer
    aware datetimes verwenden (siehe ``timezone.now()``).
    """

    def test_make_aware_attaches_default_tz(self):
        naive = dt.datetime(2026, 3, 29, 2, 30)
        aware = timezone.make_aware(naive)
        assert aware.tzinfo is not None
        # Default-TZ ist Europe/Berlin.
        assert aware.utcoffset() in {dt.timedelta(hours=1), dt.timedelta(hours=2)}

    def test_naive_datetime_in_event_raises_or_warns(self, facility, staff_user, doc_type_contact):
        """Bei ``USE_TZ=True`` darf ein naive datetime in DateTimeField nicht
        stillschweigend akzeptiert werden — Django warnt mit ``RuntimeWarning``
        oder wirft ``ValueError``."""
        naive = dt.datetime(2026, 3, 29, 2, 30)
        # Django warnt mit RuntimeWarning; der Save geht durch, die Datenbank
        # interpretiert den Wert dann als UTC-naive → was üblicherweise nicht
        # gewünscht ist. Dieser Test schützt vor stillem Verhalten ohne Warnung.
        with pytest.warns(RuntimeWarning):
            Event.objects.create(
                facility=facility,
                document_type=doc_type_contact,
                occurred_at=naive,
                data_json={"dauer": 5},
                created_by=staff_user,
            )


# ---------------------------------------------------------------------------
# Schichtfenster ueber DST-Grenzen (Refs #2057)
#
# Semantik-Entscheid (Maintainer, Welle E / release-0.92):
#   * Rueckfalltag (Herbst): Fensterende = ZWEITE Okkurrenz der Wanduhrzeit
#     (``fold=1``). Eine Nachtschicht 20:00-02:45 laeuft real 7 h 45 und
#     enthaelt BEIDE 02:xx-Stunden.
#   * Vorstelltag (Fruehjahr): existiert die Wanduhrzeit nicht, gilt
#     Dauer-ab-Start (nominale Dauer aus ``start_time``/``end_time``).
#   * Ganztags-Fenster bleiben unveraendert (00:00 und 23:59:59.999999 sind
#     an keiner EU-Transition mehrdeutig) — der Rueckfalltag umfasst 25 h.
#
# Alle Instants sind gegen die EU-Regeln 2026 gepinnt (kein freezegun, wie im
# Rest dieser Datei).
# ---------------------------------------------------------------------------

NIGHT_SHIFT = (dt.time(20, 0), dt.time(2, 45))  # Mitternachtsueberlauf, nominal 6 h 45
EARLY_SHIFT = (dt.time(1, 0), dt.time(2, 45))  # Normalzweig, Ende in der Doppelstunde


def _time_filter(start_end):
    """Unsaved ``TimeFilter`` — ``get_time_range`` liest nur start_time/end_time."""
    start_time, end_time = start_end
    return TimeFilter(label="Schicht", start_time=start_time, end_time=end_time)


def _real_duration(start_dt, end_dt):
    """Echte verstrichene Zeit zwischen zwei aware Datetimes.

    ``end_dt - start_dt`` allein genuegt NICHT: tragen beide dasselbe
    ``tzinfo``-Objekt, ignoriert Python die UTC-Offsets und rechnet wie auf
    naiven Wanduhrzeiten (CPython-Doku zu ``datetime``: "the common tzinfo
    attribute is ignored"). Genau die DST-Stunde, um die es hier geht, faellt
    damit unter den Tisch — der Vergleich muss ueber UTC laufen.
    """
    return end_dt.astimezone(UTC) - start_dt.astimezone(UTC)


class TestShiftWindowDstTimezoneGuard:
    """Die gepinnten Instants gelten nur unter Europe/Berlin."""

    def test_current_timezone_is_berlin(self):
        assert timezone.get_current_timezone_name() == "Europe/Berlin"


class TestShiftWindowDstFallBack:
    """2026-10-25 — Fensterende faellt in die doppelte Stunde (Option A)."""

    def test_night_shift_end_is_second_occurrence(self):
        start_dt, end_dt = get_time_range(dt.date(2026, 10, 24), _time_filter(NIGHT_SHIFT))
        assert start_dt.astimezone(UTC) == dt.datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
        # 02:45 tritt zweimal auf; das Fenster endet beim ZWEITEN Mal (CET, +01:00).
        assert end_dt.astimezone(UTC) == dt.datetime(2026, 10, 25, 1, 45, tzinfo=UTC), (
            f"Fensterende steht auf der ersten Okkurrenz (#2057): {end_dt.astimezone(UTC)}"
        )

    def test_night_shift_really_lasts_seven_hours_forty_five(self):
        start_dt, end_dt = get_time_range(dt.date(2026, 10, 24), _time_filter(NIGHT_SHIFT))
        assert _real_duration(start_dt, end_dt) == dt.timedelta(hours=7, minutes=45)

    def test_normal_branch_end_in_doubled_hour_is_second_occurrence(self):
        """Auch ohne Mitternachtsueberlauf: 01:00-02:45 am Rueckfalltag."""
        start_dt, end_dt = get_time_range(dt.date(2026, 10, 25), _time_filter(EARLY_SHIFT))
        assert start_dt.astimezone(UTC) == dt.datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
        assert end_dt.astimezone(UTC) == dt.datetime(2026, 10, 25, 1, 45, tzinfo=UTC)
        assert _real_duration(start_dt, end_dt) == dt.timedelta(hours=2, minutes=45)

    def test_full_day_still_spans_twenty_five_hours(self):
        """Ganztag unveraendert: 00:00 und 23:59:59.999999 sind eindeutig."""
        start_dt, end_dt = get_time_range(dt.date(2026, 10, 25))
        assert start_dt.astimezone(UTC) == dt.datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
        assert end_dt.astimezone(UTC) == dt.datetime(2026, 10, 25, 22, 59, 59, 999999, tzinfo=UTC)
        assert _real_duration(start_dt, end_dt) == dt.timedelta(hours=25) - dt.timedelta(microseconds=1)


class TestShiftWindowDstSpringForward:
    """2026-03-29 — Fensterende faellt in die Luecke (Dauer-ab-Start, Option B).

    Auf Europe/Berlin faellt Dauer-ab-Start mit der bisherigen
    ``fold=0``-Aufloesung zusammen (der Pre-Fold-Offset ist derselbe wie am
    Schichtbeginn) — diese Tests pinnen also bestehendes Verhalten. Sie sind
    trotzdem noetig: sie halten fest, dass der neue Rueckfall-Zweig den
    Vorstelltag NICHT mitverschiebt (``fold=1`` waere hier 5 h 45).
    """

    def test_night_shift_end_uses_nominal_duration(self):
        start_dt, end_dt = get_time_range(dt.date(2026, 3, 28), _time_filter(NIGHT_SHIFT))
        assert start_dt.astimezone(UTC) == dt.datetime(2026, 3, 28, 19, 0, tzinfo=UTC)
        assert end_dt.astimezone(UTC) == dt.datetime(2026, 3, 29, 1, 45, tzinfo=UTC)

    def test_night_shift_keeps_nominal_six_hours_forty_five(self):
        start_dt, end_dt = get_time_range(dt.date(2026, 3, 28), _time_filter(NIGHT_SHIFT))
        assert _real_duration(start_dt, end_dt) == dt.timedelta(hours=6, minutes=45)

    def test_end_is_normalised_to_an_existing_wall_clock_time(self):
        """Der Rueckgabewert selbst darf keine Uhrzeit tragen, die es nie gab.

        Dauer-ab-Start auf der Wanduhr-Achse ergaebe ``02:45+01:00`` — den
        richtigen Instant unter einer lokalen Zeit, die am 29.03. nicht
        existiert. ``case.handover`` rendert ``end_dt`` direkt per
        ``strftime``, die Fachkraft saehe also "02:45".
        """
        _start_dt, end_dt = get_time_range(dt.date(2026, 3, 28), _time_filter(NIGHT_SHIFT))
        assert end_dt.isoformat() == "2026-03-29T03:45:00+02:00"

    def test_end_is_not_the_post_gap_offset(self):
        """Gegenprobe: ``fold=1`` waere in der Luecke 00:45 UTC — das darf nicht passieren."""
        _start_dt, end_dt = get_time_range(dt.date(2026, 3, 28), _time_filter(NIGHT_SHIFT))
        assert end_dt.astimezone(UTC) != dt.datetime(2026, 3, 29, 0, 45, tzinfo=UTC)

    def test_normal_branch_end_in_gap_uses_nominal_duration(self):
        start_dt, end_dt = get_time_range(dt.date(2026, 3, 29), _time_filter(EARLY_SHIFT))
        assert start_dt.astimezone(UTC) == dt.datetime(2026, 3, 29, 0, 0, tzinfo=UTC)
        assert _real_duration(start_dt, end_dt) == dt.timedelta(hours=1, minutes=45)

    def test_full_day_still_spans_twenty_three_hours(self):
        start_dt, end_dt = get_time_range(dt.date(2026, 3, 29))
        assert start_dt.astimezone(UTC) == dt.datetime(2026, 3, 28, 23, 0, tzinfo=UTC)
        assert end_dt.astimezone(UTC) == dt.datetime(2026, 3, 29, 21, 59, 59, 999999, tzinfo=UTC)
        assert _real_duration(start_dt, end_dt) == dt.timedelta(hours=23) - dt.timedelta(microseconds=1)


class TestShiftWindowDstNoTransition:
    """Tage ohne Transition bleiben unangetastet — die Regel darf nicht ueberall greifen."""

    def test_night_shift_on_ordinary_summer_day(self):
        start_dt, end_dt = get_time_range(dt.date(2026, 6, 14), _time_filter(NIGHT_SHIFT))
        assert start_dt.astimezone(UTC) == dt.datetime(2026, 6, 14, 18, 0, tzinfo=UTC)
        assert end_dt.astimezone(UTC) == dt.datetime(2026, 6, 15, 0, 45, tzinfo=UTC)
        assert _real_duration(start_dt, end_dt) == dt.timedelta(hours=6, minutes=45)

    def test_day_shift_on_ordinary_winter_day(self):
        start_dt, end_dt = get_time_range(dt.date(2026, 1, 15), _time_filter((dt.time(8, 0), dt.time(16, 0))))
        assert _real_duration(start_dt, end_dt) == dt.timedelta(hours=8)

    def test_unambiguous_end_keeps_fold_zero(self):
        """``fold=1`` gehoert nur an ein tatsaechlich mehrdeutiges Ende.

        Sonst aendert sich an jedem normalen Tag die Objektdarstellung
        (Pickle, Vergleich per ``==`` mit einem frisch gebauten Wert), ohne
        dass sich der Instant unterscheidet.
        """
        _start_dt, end_dt = get_time_range(dt.date(2026, 6, 14), _time_filter(NIGHT_SHIFT))
        assert end_dt.fold == 0

    def test_unambiguous_full_day_end_keeps_fold_zero(self):
        _start_dt, end_dt = get_time_range(dt.date(2026, 6, 14))
        assert end_dt.fold == 0

    def test_ambiguous_end_uses_fold_one(self):
        """Gegenprobe: nur am Rueckfalltag darf ``fold`` umschlagen."""
        _start_dt, end_dt = get_time_range(dt.date(2026, 10, 24), _time_filter(NIGHT_SHIFT))
        assert end_dt.fold == 1

    def test_full_day_on_ordinary_day_spans_twenty_four_hours(self):
        start_dt, end_dt = get_time_range(dt.date(2026, 6, 14))
        assert _real_duration(start_dt, end_dt) == dt.timedelta(hours=24) - dt.timedelta(microseconds=1)


@pytest.mark.django_db
class TestShiftWindowDstFeedResult:
    """Die verlorene zweite Stunde erscheint wieder im Zeitstrom (#2057)."""

    def test_event_in_second_occurrence_appears_in_night_shift_feed(self, facility, staff_user, doc_type_contact):
        night = TimeFilter.objects.create(
            facility=facility, label="Nachtschicht", start_time=dt.time(20, 0), end_time=dt.time(2, 45)
        )

        def _event(occurred, note):
            return Event.objects.create(
                facility=facility,
                document_type=doc_type_contact,
                occurred_at=occurred,
                data_json={"notiz": note},
                created_by=staff_user,
            )

        first_hour = _event(dt.datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0), "erste 02:30")
        second_hour = _event(dt.datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=1), "zweite 02:30")
        after_shift = _event(dt.datetime(2026, 10, 25, 3, 30, tzinfo=BERLIN), "nach Schichtende")

        items = build_feed_items(facility, dt.date(2026, 10, 24), "events", time_filter=night, user=staff_user)
        pks = {i["object"].pk for i in items}

        assert first_hour.pk in pks
        assert second_hour.pk in pks, "Die zweite (duplizierte) 02:xx-Stunde faellt aus dem Schichtfenster (#2057)"
        assert after_shift.pk not in pks, "Das Fenster darf nicht ueber das Schichtende hinaus aufgehen"


@pytest.mark.django_db
class TestShiftWindowDstLiveSelection:
    """Die automatischen Schicht-Auswahlen folgen der Fold-Semantik (Refs #2057).

    ``get_time_range`` liefert das richtige Fenster — aber wer die *laufende*
    bzw. *zuletzt abgeschlossene* Schicht ueber Wanduhrzeiten bestimmt,
    verlaesst dieses Fenster wieder. In der ersten 02:xx-Stunde des
    Rueckfalltags galt die Nachtschicht 20:00-02:45 dadurch ab 02:45
    Wanduhrzeit als beendet, obwohl sie real noch eine Stunde laeuft.
    """

    @pytest.fixture
    def night_shift(self, facility):
        return TimeFilter.objects.create(
            facility=facility, label="Nachtschicht", start_time=dt.time(20, 0), end_time=dt.time(2, 45)
        )

    # 02:50 der ERSTEN Okkurrenz (CEST, +02:00) — die Schicht laeuft noch.
    FIRST_OCCURRENCE = dt.datetime(2026, 10, 25, 2, 50, tzinfo=BERLIN, fold=0)
    # 02:50 der ZWEITEN Okkurrenz (CET, +01:00) — die Schicht ist vorbei.
    SECOND_OCCURRENCE = dt.datetime(2026, 10, 25, 2, 50, tzinfo=BERLIN, fold=1)

    def test_live_context_still_in_shift_during_first_occurrence(self, facility, night_shift):
        context = current_tally_context(facility, now=self.FIRST_OCCURRENCE)
        assert context.time_filter == night_shift, (
            "Die Nachtschicht gilt in der ersten 02:xx-Stunde faelschlich als beendet (#2057)"
        )
        assert context.shift_date == dt.date(2026, 10, 24)
        assert context.window_end.astimezone(UTC) == dt.datetime(2026, 10, 25, 1, 45, tzinfo=UTC)

    def test_live_context_shift_over_during_second_occurrence(self, facility, night_shift):
        """Gegenprobe: eine Stunde spaeter (CET) ist die Schicht wirklich vorbei."""
        context = current_tally_context(facility, now=self.SECOND_OCCURRENCE)
        assert context.time_filter is None
        assert context.shift_date == dt.date(2026, 10, 25)

    def test_auto_selection_keeps_the_shift_during_first_occurrence(self, facility, night_shift):
        state = resolve_zeitstrom_state({}, facility, today=dt.date(2026, 10, 25), now=self.FIRST_OCCURRENCE)
        assert state.time_filter == night_shift
        assert state.target_date == dt.date(2026, 10, 24)

    def test_auto_selection_releases_the_shift_during_second_occurrence(self, facility, night_shift):
        state = resolve_zeitstrom_state({}, facility, today=dt.date(2026, 10, 25), now=self.SECOND_OCCURRENCE)
        assert state.time_filter is None
        assert state.target_date == dt.date(2026, 10, 25)

    def test_handover_does_not_close_the_running_shift(self, facility, night_shift):
        """Die laufende Schicht ist nicht die "zuletzt abgeschlossene"."""
        state = resolve_zeitstrom_state(
            {"view": "uebergabe"}, facility, today=dt.date(2026, 10, 25), now=self.FIRST_OCCURRENCE
        )
        assert state.time_filter == night_shift
        assert state.target_date == dt.date(2026, 10, 23), (
            "Die noch laufende Schicht vom 24.10. wird als abgeschlossen ausgewiesen (#2057)"
        )

    def test_handover_closes_the_shift_after_the_second_occurrence(self, facility, night_shift):
        state = resolve_zeitstrom_state(
            {"view": "uebergabe"}, facility, today=dt.date(2026, 10, 25), now=self.SECOND_OCCURRENCE
        )
        assert state.time_filter == night_shift
        assert state.target_date == dt.date(2026, 10, 24)

    def test_ordinary_day_shift_still_running_before_the_end(self, facility, night_shift):
        context = current_tally_context(facility, now=dt.datetime(2026, 6, 15, 2, 30, tzinfo=BERLIN))
        assert context.time_filter == night_shift
        assert context.shift_date == dt.date(2026, 6, 14)

    def test_ordinary_day_shift_over_after_the_end(self, facility, night_shift):
        context = current_tally_context(facility, now=dt.datetime(2026, 6, 15, 2, 50, tzinfo=BERLIN))
        assert context.time_filter is None
        assert context.shift_date == dt.date(2026, 6, 15)

    def test_ordinary_day_shift_running_in_the_evening(self, facility, night_shift):
        context = current_tally_context(facility, now=dt.datetime(2026, 6, 14, 22, 0, tzinfo=BERLIN))
        assert context.time_filter == night_shift
        assert context.shift_date == dt.date(2026, 6, 14)

    def test_shift_end_instant_still_belongs_to_the_ending_shift(self, facility, night_shift):
        """Das Fenster ist an beiden Enden geschlossen (Refs #1910).

        Genau auf dem End-Instant laeuft die Schicht noch. Ein halboffenes
        ``[start, end)`` haette den Grenzfall aus
        ``test_tally_live_context.py`` gekippt, in dem ein endender
        Nachtdienst gegen einen beginnenden Fruehdienst gewinnt.
        """
        context = current_tally_context(facility, now=dt.datetime(2026, 6, 15, 2, 45, tzinfo=BERLIN))
        assert context.time_filter == night_shift
        assert context.shift_date == dt.date(2026, 6, 14)

    def test_shift_start_instant_already_belongs_to_the_shift(self, facility, night_shift):
        context = current_tally_context(facility, now=dt.datetime(2026, 6, 14, 20, 0, tzinfo=BERLIN))
        assert context.time_filter == night_shift
        assert context.shift_date == dt.date(2026, 6, 14)

    def test_fall_back_shift_end_instant_is_the_second_occurrence(self, facility, night_shift):
        """Der End-Instant am Rueckfalltag ist 02:45 CET, nicht 02:45 CEST."""
        still_running = current_tally_context(facility, now=dt.datetime(2026, 10, 25, 2, 45, tzinfo=BERLIN, fold=0))
        assert still_running.time_filter == night_shift

        on_the_end = current_tally_context(facility, now=dt.datetime(2026, 10, 25, 2, 45, tzinfo=BERLIN, fold=1))
        assert on_the_end.time_filter == night_shift
        assert on_the_end.shift_date == dt.date(2026, 10, 24)

        just_after = current_tally_context(facility, now=dt.datetime(2026, 10, 25, 2, 46, tzinfo=BERLIN, fold=1))
        assert just_after.time_filter is None


@pytest.mark.django_db
class TestEventFormDstInput:
    """Das Ereignis-Formular erklärt die beiden DST-Grenzfälle (Refs #2117).

    Django lehnt mehrdeutige (Rückfalltag) und nicht existierende
    (Vorstelltag) Wanduhrzeiten in ``forms.DateTimeField`` ab — mit **einer**
    generischen Meldung für beide Fälle. Eine Fachkraft um 02:30 in der
    Nachtschicht erfährt daraus weder, welcher der beiden Fälle vorliegt, noch
    was sie stattdessen eintragen soll. Diese Tests pinnen den Vorstelltag-Text
    mit Handlungsanweisung — und dass die regulären Nachbarstunden beider
    Grenztage unverändert gültig bleiben.

    Refs #2127: Der Rückfalltag ist seit der Okkurrenz-Auswahl **kein**
    Formularfehler an ``occurred_at`` mehr, sondern eine Rückfrage an
    ``occurred_at_fold`` — seine Zusicherungen stehen in
    :class:`TestEventFormOccurrenceFold` (Formular) bzw.
    :class:`TestDstAwareDateTimeFieldKeepsTheAmbiguousMessageByDefault` (Feld
    ohne Opt-in).
    """

    @staticmethod
    def _form(facility, staff_user, doc_type_contact, occurred_at, fold=None):
        data = {
            "document_type": str(doc_type_contact.pk),
            "occurred_at": occurred_at,
            "client": "",
        }
        if fold is not None:
            data["occurred_at_fold"] = fold
        return EventMetaForm(data=data, facility=facility, user=staff_user)

    @staticmethod
    def _occurred_at_errors(form):
        assert not form.is_valid()
        assert "occurred_at" in form.errors
        codes = [error.code for error in form.errors.as_data()["occurred_at"]]
        return codes, " ".join(form.errors["occurred_at"])

    # ---------------------------------------------------------------
    # Rückfalltag 2026-10-25 — die Nachbarstunden bleiben eindeutig
    # ---------------------------------------------------------------

    def test_hour_before_fall_back_stays_valid_in_summer_time(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T01:30")
        assert form.is_valid(), form.errors
        value = form.cleaned_data["occurred_at"]
        assert timezone.is_aware(value)
        assert value.utcoffset() == dt.timedelta(hours=2)

    def test_hour_after_fall_back_stays_valid_in_winter_time(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T03:30")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["occurred_at"].utcoffset() == dt.timedelta(hours=1)

    # ---------------------------------------------------------------
    # Vorstelltag 2026-03-29 — 02:00–02:59 gibt es nicht
    # ---------------------------------------------------------------

    def test_nonexistent_time_gets_own_message_with_instruction(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-03-29T02:30")
        codes, text = self._occurred_at_errors(form)
        assert codes == ["dst_nonexistent"], f"Erwartet der eigene Code, bekommen: {codes}"
        assert "springt von 02:00 direkt auf 03:00" in text, text
        assert "zweimal" not in text, text

    def test_hour_after_spring_forward_stays_valid(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-03-29T03:00")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["occurred_at"].utcoffset() == dt.timedelta(hours=2)

    def test_hour_before_spring_forward_stays_valid(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-03-29T01:30")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["occurred_at"].utcoffset() == dt.timedelta(hours=1)

    # ---------------------------------------------------------------
    # Normaltag + unveränderte Fehlerklassen
    # ---------------------------------------------------------------

    def test_ordinary_day_stays_valid(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-05-16T10:00")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["occurred_at"].utcoffset() == dt.timedelta(hours=2)

    def test_unparsable_input_keeps_the_invalid_code(self, facility, staff_user, doc_type_contact):
        """Alle anderen Fehlercodes bleiben unverändert — kein Fang zu viel."""
        form = self._form(facility, staff_user, doc_type_contact, "kein Datum")
        codes, _text = self._occurred_at_errors(form)
        assert codes == ["invalid"], codes

    # ---------------------------------------------------------------
    # Katalog: der Vorstelltag-Text ist übersetzt
    # ---------------------------------------------------------------

    def test_messages_are_translated_to_english(self, facility, staff_user, doc_type_contact):
        with translation.override("en"):
            nonexistent = self._form(facility, staff_user, doc_type_contact, "2026-03-29T02:30")
            _codes, nonexistent_text = self._occurred_at_errors(nonexistent)
        assert "does not exist" in nonexistent_text, nonexistent_text


class TestDstAwareDateTimeFieldKeepsTheAmbiguousMessageByDefault:
    """Ohne Opt-in bleibt der #2117-Vertrag des Feldes unverändert (Refs #2127).

    Die Auflösung der doppelten Stunde ist eine Formular-Fähigkeit, keine
    Feld-Fähigkeit: sie braucht ein zweites Eingabefeld, das nur das Formular
    hat. Ein Verbraucher ohne dieses zweite Feld (und
    ``src/tests/test_mutation_pins_093_fields.py`` prüft das Feld genau so:
    ``DstAwareDateTimeField()`` ohne Argumente) muss die mehrdeutige Uhrzeit
    weiterhin ablehnen — sonst käme bei ihm ein Zwischenwert statt eines
    ``datetime`` an.

    Die drei Zusicherungen standen bis #2127 wörtlich in
    ``TestEventFormDstInput`` und sind nur vom Formular auf das Feld gewandert
    (Verbraucher gewechselt, Zusage unverändert).
    """

    @staticmethod
    def _errors(value):
        field = DstAwareDateTimeField()
        with pytest.raises(ValidationError) as excinfo:
            field.clean(value)
        codes = [error.code for error in excinfo.value.error_list]
        return codes, " ".join(excinfo.value.messages)

    def test_ambiguous_time_gets_own_message_with_instruction(self):
        codes, text = self._errors("2026-10-25T02:30")
        assert codes == ["dst_ambiguous"], f"Erwartet der eigene Code, bekommen: {codes}"
        assert "zweimal" in text, text
        assert "vor 02:00 oder ab 03:00" in text, text

    def test_ambiguous_message_is_not_the_generic_django_text(self):
        """Gegenprobe: der Django-Standardtext nennt die Zeitzone technisch."""
        _codes, text = self._errors("2026-10-25T02:30")
        assert "Europe/Berlin" not in text, text
        assert "Zeitzone" not in text, text

    def test_ambiguous_message_is_translated_to_english(self):
        with translation.override("en"):
            _codes, text = self._errors("2026-10-25T02:30")
        assert "twice" in text, text

    def test_opt_in_field_returns_the_unresolved_wall_clock_instead(self):
        """Mit Opt-in wirft das Feld nicht mehr — es reicht den naiven Wert weiter."""
        field = DstAwareDateTimeField(resolve_ambiguous=True)
        value = field.clean("2026-10-25T02:30")
        assert isinstance(value, AmbiguousWallClock)
        assert value.naive == dt.datetime(2026, 10, 25, 2, 30)

    def test_opt_in_field_still_rejects_the_nonexistent_hour(self):
        """Die Lücke am Vorstelltag ist auch mit Opt-in nicht auflösbar."""
        field = DstAwareDateTimeField(resolve_ambiguous=True)
        with pytest.raises(ValidationError) as excinfo:
            field.clean("2026-03-29T02:30")
        assert [error.code for error in excinfo.value.error_list] == ["dst_nonexistent"]

    def test_opt_in_field_leaves_ordinary_times_untouched(self):
        field = DstAwareDateTimeField(resolve_ambiguous=True)
        value = field.clean("2026-05-16T10:00")
        assert timezone.is_aware(value)
        assert value.utcoffset() == dt.timedelta(hours=2)


@pytest.mark.django_db
class TestEventFormOccurrenceFold:
    """Die doppelte Stunde am Rückfalltag wird erfassbar (Refs #2127).

    Bis #2127 kostete der Rückfalltag die reale zweite Stunde: die Fachkraft
    wich auf 01:59/03:00 aus und vermerkte die Wahrheit im Freitext. Der
    Ablauf ist jetzt zweistufig und ohne JavaScript: der erste POST mit einer
    Uhrzeit im Doppelfenster stellt die Rückfrage (Fehler am neuen Feld
    ``occurred_at_fold``), der zweite POST mit der Auswahl speichert den
    gewählten Zeitpunkt. Die beiden Lesarten unterscheiden sich in UTC um
    genau eine Stunde — das ist der ganze fachliche Gehalt des Tickets.
    """

    @staticmethod
    def _form(facility, staff_user, doc_type_contact, occurred_at, fold=None):
        data = {
            "document_type": str(doc_type_contact.pk),
            "occurred_at": occurred_at,
            "client": "",
        }
        if fold is not None:
            data["occurred_at_fold"] = fold
        return EventMetaForm(data=data, facility=facility, user=staff_user)

    @staticmethod
    def _fold_errors(form):
        assert not form.is_valid()
        assert "occurred_at_fold" in form.errors, form.errors
        codes = [error.code for error in form.errors.as_data()["occurred_at_fold"]]
        return codes, " ".join(form.errors["occurred_at_fold"])

    # ---------------------------------------------------------------
    # Erster POST: Rückfrage statt Ablehnung
    # ---------------------------------------------------------------

    def test_ambiguous_hour_without_choice_asks_at_the_fold_field(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30")
        codes, text = self._fold_errors(form)
        assert codes == ["fold_required"], codes
        assert "zweimal" in text, text
        assert "Sommer- oder Winterzeit" in text, text

    def test_ambiguous_hour_without_choice_carries_no_error_on_the_timestamp(
        self, facility, staff_user, doc_type_contact
    ):
        """Die Uhrzeit selbst ist nicht falsch — sie ist nur noch nicht entschieden."""
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30")
        assert not form.is_valid()
        assert "occurred_at" not in form.errors, form.errors

    def test_unresolved_timestamp_never_reaches_cleaned_data(self, facility, staff_user, doc_type_contact):
        """Kein Zwischenwert im ``cleaned_data`` — der View liest ``occurred_at`` direkt."""
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30")
        assert not form.is_valid()
        assert "occurred_at" not in form.cleaned_data, form.cleaned_data

    def test_empty_choice_is_treated_like_no_choice(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30", fold="")
        codes, _text = self._fold_errors(form)
        assert codes == ["fold_required"], codes

    # ---------------------------------------------------------------
    # Zweiter POST: beide Lesarten speichern verschiedene Zeitpunkte
    # ---------------------------------------------------------------

    def test_summer_time_choice_stores_the_first_hour(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30", fold="0")
        assert form.is_valid(), form.errors
        value = form.cleaned_data["occurred_at"]
        assert timezone.is_aware(value)
        assert value.fold == 0
        assert value.utcoffset() == dt.timedelta(hours=2)
        assert value.astimezone(UTC) == dt.datetime(2026, 10, 25, 0, 30, tzinfo=UTC)

    def test_winter_time_choice_stores_the_second_hour(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30", fold="1")
        assert form.is_valid(), form.errors
        value = form.cleaned_data["occurred_at"]
        assert timezone.is_aware(value)
        assert value.fold == 1
        assert value.utcoffset() == dt.timedelta(hours=1)
        assert value.astimezone(UTC) == dt.datetime(2026, 10, 25, 1, 30, tzinfo=UTC)

    def test_both_choices_differ_by_exactly_one_hour(self, facility, staff_user, doc_type_contact):
        """Der eigentliche Zweck: zwei unterscheidbare Zeitpunkte, nicht zwei Etiketten.

        Gerechnet wird in UTC — und das ist keine Bequemlichkeit: PEP 495
        schreibt fuer Arithmetik INNERHALB derselben Zeitzone ausdruecklich
        vor, dass ``fold`` ignoriert wird ("naive" Semantik). Die Differenz
        der beiden Wanduhrzeiten ist deshalb null; erst der Wechsel in eine
        andere Zeitzone (hier UTC) macht die Stunde sichtbar. So speichert die
        Datenbank die Werte ebenfalls.
        """
        summer = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30", fold="0")
        winter = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30", fold="1")
        assert summer.is_valid(), summer.errors
        assert winter.is_valid(), winter.errors
        delta = winter.cleaned_data["occurred_at"].astimezone(UTC) - summer.cleaned_data["occurred_at"].astimezone(UTC)
        assert delta == dt.timedelta(hours=1), delta

    # ---------------------------------------------------------------
    # Außerhalb des Doppelfensters: die Auswahl ist bedeutungslos
    # ---------------------------------------------------------------

    def test_choice_on_an_ordinary_day_is_ignored(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-05-16T10:00", fold="1")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["occurred_at"].utcoffset() == dt.timedelta(hours=2)
        assert form.cleaned_data["occurred_at_fold"] == ""

    def test_choice_on_the_neighbouring_hour_is_ignored(self, facility, staff_user, doc_type_contact):
        """01:30 am Rückfalltag ist eindeutig Sommerzeit — die Auswahl darf nichts verschieben."""
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T01:30", fold="1")
        assert form.is_valid(), form.errors
        assert form.cleaned_data["occurred_at"].utcoffset() == dt.timedelta(hours=2)
        assert form.cleaned_data["occurred_at_fold"] == ""

    # ---------------------------------------------------------------
    # Vorstelltag und Fehlerklassen bleiben unberührt
    # ---------------------------------------------------------------

    def test_nonexistent_hour_keeps_its_own_message_even_with_a_choice(self, facility, staff_user, doc_type_contact):
        """Die Lücke ist nicht wählbar — eine Auswahl darf sie nicht auflösen."""
        form = self._form(facility, staff_user, doc_type_contact, "2026-03-29T02:30", fold="1")
        assert not form.is_valid()
        codes = [error.code for error in form.errors.as_data()["occurred_at"]]
        assert codes == ["dst_nonexistent"], codes
        assert "occurred_at_fold" not in form.errors, form.errors

    def test_unknown_choice_value_is_rejected_once(self, facility, staff_user, doc_type_contact):
        """Ein manipulierter Wert ergibt genau EINEN Fehler, nicht zwei."""
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30", fold="7")
        codes, _text = self._fold_errors(form)
        assert codes == ["invalid_choice"], codes
        assert "occurred_at" not in form.cleaned_data, form.cleaned_data

    def test_choice_without_a_timestamp_does_not_crash(self, facility, staff_user, doc_type_contact):
        """Leere Pflichtuhrzeit + Auswahl: nur der Pflichtfeld-Fehler, kein Folgefehler."""
        form = self._form(facility, staff_user, doc_type_contact, "", fold="1")
        assert not form.is_valid()
        assert [error.code for error in form.errors.as_data()["occurred_at"]] == ["required"]
        assert "occurred_at_fold" not in form.errors, form.errors

    def test_the_field_is_optional_and_offers_exactly_three_values(self, facility, staff_user, doc_type_contact):
        form = EventMetaForm(facility=facility, user=staff_user)
        field = form.fields["occurred_at_fold"]
        assert field.required is False
        assert [value for value, _label in field.choices] == ["", "0", "1"]

    # ---------------------------------------------------------------
    # Sichtbarkeits-Zustand — die EINE Quelle für das Template
    # ---------------------------------------------------------------

    def test_an_unbound_form_never_shows_the_choice(self, facility, staff_user, doc_type_contact):
        assert EventMetaForm(facility=facility, user=staff_user).show_occurred_at_fold is False

    def test_the_choice_shows_in_the_double_hour_without_an_answer(self, facility, staff_user, doc_type_contact):
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30")
        assert form.show_occurred_at_fold is True

    def test_the_choice_keeps_showing_after_it_was_answered(self, facility, staff_user, doc_type_contact):
        """Sonst verschwände die Auswahl bei einem Fehler an einem anderen Feld."""
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30", fold="1")
        assert form.show_occurred_at_fold is True

    def test_a_stray_choice_outside_the_double_hour_does_not_show(self, facility, staff_user, doc_type_contact):
        """Der rohe POST-Wert darf das Feld NICHT sichtbar machen (Refs #2127, R-L8-1)."""
        for occurred_at in ("2026-05-16T10:00", "2026-10-25T01:30", "2026-03-29T02:30"):
            form = self._form(facility, staff_user, doc_type_contact, occurred_at, fold="1")
            assert form.show_occurred_at_fold is False, occurred_at

    def test_the_state_is_computed_even_without_an_is_valid_call(self, facility, staff_user, doc_type_contact):
        """Das Template darf das Formular rendern, ohne dass der View vorher geprüft hat."""
        form = self._form(facility, staff_user, doc_type_contact, "2026-10-25T02:30")
        assert form.show_occurred_at_fold is True
        assert form._errors is not None, "Der Zugriff muss full_clean() ausgelöst haben"


@pytest.mark.django_db
class TestEventCreateTemplateOccurrenceFold:
    """Das Select steht nur im Grenzfall im DOM (Refs #2127, R-L8-1/R-L8-3).

    Der Sichtbarkeitswechsel passiert serverseitig — kein JavaScript, kein
    ``hidden``-Attribut, das eine Fachkraft mit Tastatur trotzdem erreicht.
    Außerhalb des Doppelfensters existiert das Feld im gerenderten Formular
    schlicht nicht.
    """

    @staticmethod
    def _post(client, doc_type_contact, occurred_at, fold=None):
        payload = {
            "document_type": str(doc_type_contact.pk),
            "occurred_at": occurred_at,
            "client": "",
        }
        if fold is not None:
            payload["occurred_at_fold"] = fold
        return client.post(reverse("core:event_create"), payload)

    def test_empty_form_does_not_render_the_select(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        assert 'id="id_occurred_at_fold"' not in response.content.decode()

    def test_ambiguous_hour_renders_the_select_with_its_error(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = self._post(client, doc_type_contact, "2026-10-25T02:30")
        assert response.status_code == 200
        html = response.content.decode()
        assert 'id="id_occurred_at_fold"' in html
        assert 'id="id_occurred_at_fold-error"' in html
        assert 'for="id_occurred_at_fold"' in html
        assert "Sommer- oder Winterzeit" in html

    def test_the_error_list_keeps_its_list_role_inside_the_alert(self, client, staff_user, doc_type_contact):
        """``role="alert"`` auf dem ``<ul>`` würde dessen Listenrolle entwerten (Refs #2154)."""
        client.force_login(staff_user)
        response = self._post(client, doc_type_contact, "2026-10-25T02:30")
        html = response.content.decode()
        assert '<div role="alert">' in html
        assert '<ul id="id_occurred_at_fold-error" role="list"' in html

    def test_the_select_is_described_by_help_and_error(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = self._post(client, doc_type_contact, "2026-10-25T02:30")
        html = response.content.decode()
        assert 'aria-describedby="id_occurred_at_fold-help id_occurred_at_fold-error"' in html
        assert 'aria-invalid="true"' in html
        assert 'id="id_occurred_at_fold-help"' in html

    def test_the_select_takes_the_focus_after_the_redisplay(self, client, staff_user, doc_type_contact):
        """Ohne HTMX greift das globale Fokus-Management nicht — ``autofocus`` schon."""
        client.force_login(staff_user)
        response = self._post(client, doc_type_contact, "2026-10-25T02:30")
        html = response.content.decode()
        marker = html.index('id="id_occurred_at_fold"')
        assert "autofocus" in html[marker : marker + 600], html[marker : marker + 600]

    def test_a_made_choice_stays_rendered_when_another_field_fails(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {"document_type": "", "occurred_at": "2026-10-25T02:30", "client": "", "occurred_at_fold": "1"},
        )
        assert response.status_code == 200
        html = response.content.decode()
        assert 'id="id_occurred_at_fold"' in html
        assert '<option value="1" selected' in html

    def test_an_ordinary_timestamp_error_does_not_pull_the_select_in(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {"document_type": str(doc_type_contact.pk), "occurred_at": "", "client": ""},
        )
        assert response.status_code == 200
        assert 'id="id_occurred_at_fold"' not in response.content.decode()

    def test_a_stray_choice_on_an_ordinary_day_is_not_rendered(self, client, staff_user, doc_type_contact):
        """Refs #2127 (R-L8-1): außerhalb des Doppelfensters bleibt das Feld unsichtbar.

        Der Fall ist real: ein POST, der ``occurred_at_fold`` mitschickt (weil
        die Rückfrage im vorigen Anlauf schon beantwortet wurde) und danach an
        einem ANDEREN Feld scheitert. Wer die Sichtbarkeit am rohen POST-Wert
        festmacht (``BoundField.value()``), zeigt das Feld hier fälschlich —
        obwohl ``clean()`` die Auswahl längst auf ``""`` normalisiert hat.
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {"document_type": "", "occurred_at": "2026-05-16T10:00", "client": "", "occurred_at_fold": "1"},
        )
        assert response.status_code == 200
        assert 'id="id_occurred_at_fold"' not in response.content.decode()

    def test_a_stray_choice_on_the_spring_forward_day_is_not_rendered(self, client, staff_user, doc_type_contact):
        """Die Lückenstunde ist nicht wählbar — dort darf keine Auswahl erscheinen."""
        client.force_login(staff_user)
        response = self._post(client, doc_type_contact, "2026-03-29T02:30", fold="1")
        assert response.status_code == 200
        html = response.content.decode()
        assert 'id="id_occurred_at_fold"' not in html
        assert "springt von 02:00 direkt auf 03:00" in html

    def test_a_stray_choice_on_the_neighbouring_hour_is_not_rendered(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {"document_type": "", "occurred_at": "2026-10-25T01:30", "client": "", "occurred_at_fold": "1"},
        )
        assert response.status_code == 200
        assert 'id="id_occurred_at_fold"' not in response.content.decode()

    def test_the_saved_event_keeps_the_chosen_hour(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = self._post(client, doc_type_contact, "2026-10-25T02:30", fold="1")
        assert response.status_code == 302
        event = Event.objects.filter(created_by=staff_user).order_by("-created_at").first()
        assert event is not None
        assert event.occurred_at.astimezone(UTC) == dt.datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
