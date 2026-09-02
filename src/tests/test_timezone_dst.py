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
from django.utils import timezone

from core.models import Event, TimeFilter
from core.services.events import build_feed_items, get_time_range

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
