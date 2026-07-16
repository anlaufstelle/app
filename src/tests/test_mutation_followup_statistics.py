"""Follow-Up-Tests für Mutation-Survivors in
``core.services.dashboard.statistics`` (Refs #1388).

Fokus auf die in diesem Milestone geänderte DoS-/Limit-Logik (Refs #1589) und
die Aggregations-Filter. Der Voll-Lauf 2026-07-16 liess echte Survivors an den
Query-Filtern (``facility``, ``is_deleted``, ``occurred_at__year``), am
Flat-View-Gate und an den Fehlermeldungen der Bereichsvalidierung stehen.

Die Boundary-Operatoren selbst (``date_from > date_to``,
``.days > STATISTICS_MAX_CUSTOM_RANGE_DAYS``) waren bereits durch Bestandstests
gekillt — hier fehlten die Meldungs- und Filter-Survivors.

Jeder Test benennt die konkret gekillte Mutation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from django.db import connection
from django.utils import timezone

from core.models import Event
from core.services.dashboard.statistics import (
    STATISTICS_MAX_CUSTOM_RANGE_DAYS,
    InvalidStatisticsPeriodError,
    _flat_view_enabled,
    get_event_counts_by_month,
    validate_statistics_range,
)


def _requires_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("Flat-View-Gate/Aggregat erfordert PostgreSQL-Semantik")


class TestValidateRangeMessages:
    """``validate_statistics_range`` — Fehlermeldungen tragen den Grund."""

    def test_inverted_range_message_names_the_dates(self):
        """Killt ``validate_statistics_range__mutmut_2``:
        ``raise InvalidStatisticsPeriodError(f"date_from …")`` → ``…(None)``.

        Die 400-Antwort/das Log muss den Grund benennen — eine ``None``-Message
        (``str(exc) == "None"``) verschluckt ihn.
        """
        with pytest.raises(InvalidStatisticsPeriodError) as exc:
            validate_statistics_range(date(2026, 5, 1), date(2026, 4, 1))
        assert "date_from" in str(exc.value)

    def test_too_large_range_message_names_the_maximum(self):
        """Killt ``validate_statistics_range__mutmut_5``:
        ``raise InvalidStatisticsPeriodError(f"… Maximum von … Tagen.")`` →
        ``…(None)``.
        """
        d0 = date(2000, 1, 1)
        d1 = d0 + timedelta(days=STATISTICS_MAX_CUSTOM_RANGE_DAYS + 5)
        with pytest.raises(InvalidStatisticsPeriodError) as exc:
            validate_statistics_range(d0, d1)
        assert "Maximum" in str(exc.value)


@pytest.mark.django_db
class TestEventCountsByMonthFilters:
    """``get_event_counts_by_month`` — die drei Aggregations-Filter."""

    def setup_method(self):
        _requires_postgres()

    def _total(self, facility, year):
        return sum(row["count"] for row in get_event_counts_by_month(facility, year))

    def test_excludes_deleted_events(self, facility, doc_type_contact):
        """Killt ``get_event_counts_by_month__mutmut_24``: entfernter
        ``is_deleted=False``-Filter. Redigierte/gelöschte Events dürfen nicht in
        die Träger-/Förderstatistik einfliessen.
        """
        when = timezone.make_aware(datetime(2026, 3, 15, 12, 0))
        Event.objects.create(facility=facility, document_type=doc_type_contact, occurred_at=when, data_json={})
        Event.objects.create(
            facility=facility, document_type=doc_type_contact, occurred_at=when, data_json={}, is_deleted=True
        )
        assert self._total(facility, 2026) == 1

    def test_excludes_other_facilities(self, facility, second_facility, doc_type_contact):
        """Killt ``get_event_counts_by_month__mutmut_23``: entfernter
        ``facility=facility``-Filter → Cross-Tenant-Leck in der Statistik.
        """
        when = timezone.make_aware(datetime(2026, 3, 15, 12, 0))
        Event.objects.create(facility=facility, document_type=doc_type_contact, occurred_at=when, data_json={})
        Event.objects.create(facility=second_facility, document_type=doc_type_contact, occurred_at=when, data_json={})
        assert self._total(facility, 2026) == 1

    def test_excludes_other_years(self, facility, doc_type_contact):
        """Killt ``get_event_counts_by_month__mutmut_25``: entfernter
        ``occurred_at__year=year``-Filter → Vermischung über Jahresgrenzen.
        """
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.make_aware(datetime(2026, 3, 15, 12, 0)),
            data_json={},
        )
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.make_aware(datetime(2025, 3, 15, 12, 0)),
            data_json={},
        )
        assert self._total(facility, 2026) == 1


class TestFlatViewGate:
    """``_flat_view_enabled`` — Feature-Flag UND Postgres-Backend."""

    def test_disabled_without_setting(self, settings):
        """Killt ``_flat_view_enabled__mutmut_11``:
        ``getattr(settings, "STATISTICS_USE_FLAT_VIEW", False)`` → ``…, True``.

        Ohne gesetztes Flag (Default) darf der MV-Pfad NICHT aktiv sein — sonst
        liefe eine Installation ohne materialisierte View ins Leere.
        """
        _requires_postgres()
        # STATISTICS_USE_FLAT_VIEW ist in settings nicht definiert → Default greift.
        assert not hasattr(settings, "STATISTICS_USE_FLAT_VIEW") or settings.STATISTICS_USE_FLAT_VIEW is False
        assert _flat_view_enabled() is False

    def test_requires_postgres_backend(self, settings):
        """Killt ``_flat_view_enabled__mutmut_12``:
        ``connection.vendor == "postgresql"`` → ``!= "postgresql"``.

        Mit gesetztem Flag entscheidet allein das Backend. Auf Postgres MUSS
        das Gate ``True`` liefern; die Mutation kehrt die Backend-Bedingung um.
        """
        _requires_postgres()
        settings.STATISTICS_USE_FLAT_VIEW = True
        assert _flat_view_enabled() is True
