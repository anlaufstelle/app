"""Regression tests for active statistics range validation and event filters."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from django.db import connection
from django.utils import timezone

from core.models import DocumentType, Event
from core.services.dashboard.statistics import (
    STATISTICS_MAX_CUSTOM_RANGE_DAYS,
    InvalidStatisticsPeriodError,
    get_statistics,
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
class TestLiveStatisticsFilters:
    """The active statistics path keeps facility, deletion and year filters."""

    def setup_method(self):
        _requires_postgres()

    def _total(self, facility, year):
        return get_statistics(facility, date(year, 1, 1), date(year, 12, 31))["total_contacts"]

    def test_excludes_deleted_events(self, facility, doc_type_contact):
        """Deleted events must not contribute to published contact totals."""
        when = timezone.make_aware(datetime(2026, 3, 15, 12, 0))
        Event.objects.create(facility=facility, document_type=doc_type_contact, occurred_at=when, data_json={})
        Event.objects.create(
            facility=facility, document_type=doc_type_contact, occurred_at=when, data_json={}, is_deleted=True
        )
        assert self._total(facility, 2026) == 1

    def test_excludes_other_facilities(self, facility, second_facility, doc_type_contact):
        """Contact totals must never include another facility."""
        when = timezone.make_aware(datetime(2026, 3, 15, 12, 0))
        # Refs #2066: Der Composite-FK pinnt document_type an die Facility des
        # Events — das Fremd-Event braucht seither einen eigenen Doctype seiner
        # Facility (der alte Cross-Facility-Link ist genau das, was die DB
        # jetzt abweist).
        foreign_doc_type = DocumentType.objects.create(
            facility=second_facility, name="Kontakt (fremd)", report_key="contact"
        )
        Event.objects.create(facility=facility, document_type=doc_type_contact, occurred_at=when, data_json={})
        Event.objects.create(facility=second_facility, document_type=foreign_doc_type, occurred_at=when, data_json={})
        assert self._total(facility, 2026) == 1

    def test_excludes_other_years(self, facility, doc_type_contact):
        """Contact totals must stay inside the requested calendar year."""
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
