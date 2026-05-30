"""DST-Übergänge Europe/Berlin 2026 + Aware/Naive-DateTime-Mix.

Refs Welle 4 (#927), Master #922.

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

from core.models import Event

BERLIN = ZoneInfo("Europe/Berlin")
UTC = dt.timezone.utc


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
