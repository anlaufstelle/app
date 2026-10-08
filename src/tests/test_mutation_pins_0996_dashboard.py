"""Pins for the report threshold, the snapshot carry-over and the lead dashboard.

Source: mutation delta of the release-0.99.6 checkpoint, 9 mutants. Groups:

* ``core.services.dashboard.external_report`` (5, Refs #2499) -- at an effective
  threshold of 2 the warning names groups of two, not the k = 1 text
  (``x_k_threshold_warning__mutmut_4``); a stored threshold below the minimum is
  logged with the stored value, the facility, the minimum and the threshold the
  reports compute with (``x__get_threshold__mutmut_8`` to ``_11``).
* ``core.services.dashboard.snapshot`` (3, Refs #2425) -- the row lock is scoped to
  the year as well as the month (``x__lock_snapshot_row__mutmut_16``); the stored
  carry-over keeps the earlier carried entries first on equal counts
  (``x__add_deleted_events__mutmut_8``); a failed carry-over names the event
  (``x_carry_deleted_event__mutmut_34``).
* ``core.services.dashboard.main`` (1, Refs #2425) -- the lead dashboard names the
  latest completed month, never a row of a later year
  (``x_lead_dashboard_context__mutmut_19``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest import mock

import pytest
from django.db import transaction
from django.utils.translation import gettext

from core.models import Event, Settings, StatisticsSnapshot
from core.models.settings import K_ANONYMITY_MIN
from core.services.dashboard import carry_deleted_event, create_or_update_snapshot, lead_dashboard_context
from core.services.dashboard.external_report import DEFAULT_K_THRESHOLD, _get_threshold, k_threshold_warning
from core.services.dashboard.snapshot import SnapshotCarryError
from tests._mutation_followup_snapshot_helpers import _make_event

pytestmark = pytest.mark.django_db

GROUPS_OF_TWO_WARNING = (
    "Die k-Anonymitäts-Schwelle liegt unter 3. Gruppen ab zwei Datensätzen werden veröffentlicht; "
    "Personen in kleinen Gruppen können in den Zahlen wiedererkennbar sein. Empfohlen ist k = 5."
)
NO_SUPPRESSION_WARNING = (
    "Die k-Anonymitäts-Schwelle liegt unter 3. Bei k = 1 wird keine Zahl unterdrückt; "
    "auch Einzelfälle werden veröffentlicht und können Personen erkennbar machen. Empfohlen ist k = 5."
)


def _delete_and_carry(facility, event):
    with transaction.atomic():
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        carry_deleted_event(facility, event)


class TestThresholdWarning:
    def test_threshold_two_warns_about_groups_of_two(self):
        """At the minimum 2 the warning names published groups of two, not the k = 1 text.

        ``x_k_threshold_warning__mutmut_4`` compares with ``<=`` and returns the k = 1 text.
        """
        assert K_ANONYMITY_MIN == 2
        assert str(k_threshold_warning(2)) == gettext(GROUPS_OF_TWO_WARNING)
        assert str(k_threshold_warning(1)) == gettext(NO_SUPPRESSION_WARNING)


class TestStoredThresholdBelowMinimumIsLogged:
    @pytest.mark.parametrize(("stored", "effective"), [(0, DEFAULT_K_THRESHOLD), (1, 1)])
    def test_log_names_value_facility_minimum_and_effective_threshold(
        self, facility, settings_obj, caplog, stored, effective
    ):
        """The warning names the stored value, the facility, the minimum and the threshold used.

        ``_get_threshold`` is the shared entry point of every report backend and the log
        record is its only trace, so the pin calls it directly.
        ``x__get_threshold__mutmut_8`` (stored value), ``__mutmut_9`` (facility),
        ``__mutmut_10`` (minimum) and ``__mutmut_11`` (effective threshold) log ``None``.
        """
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=stored)

        with caplog.at_level(logging.WARNING, logger="core.services.dashboard.external_report"):
            assert _get_threshold(facility) == effective

        messages = [r.getMessage() for r in caplog.records if r.name == "core.services.dashboard.external_report"]
        assert messages == [
            f"Stored k-anonymity threshold {stored} for facility {facility.pk} is below the minimum "
            f"{K_ANONYMITY_MIN}; computing with {effective}."
        ]


class TestSnapshotRowOfTheSameMonthInAnotherYear:
    def test_carry_over_reaches_the_row_of_the_event_year(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """With a June row of another year present, carrying a June event succeeds and grows only
        the row of the event's year.

        ``x__lock_snapshot_row__mutmut_16`` locks by facility and month only; the lookup finds
        two rows and the carry-over fails.
        """
        create_or_update_snapshot(facility, 2023, 6)
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))

        _delete_and_carry(facility, event)

        current = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=6)
        other = StatisticsSnapshot.objects.get(facility=facility, year=2023, month=6)
        assert current.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1
        assert "_deleted" not in other.backend_data

    def test_recompute_reaches_the_row_of_the_requested_year(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Recomputing June of two years writes one row per year with that year's events.

        ``x__lock_snapshot_row__mutmut_16`` fails on the second year.
        """
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))

        create_or_update_snapshot(facility, 2023, 6)
        create_or_update_snapshot(facility, 2024, 6)

        rows = dict(
            ((row.year, row.month), row.data["total_contacts"])
            for row in StatisticsSnapshot.objects.filter(facility=facility)
        )
        assert rows == {(2023, 6): 0, (2024, 6): 1}


class TestCarryOverKeepsTheEarlierEntriesFirst:
    def test_equal_counts_keep_the_earlier_carried_document_type_first(
        self, facility, staff_user, client_identified, doc_type_contact, doc_type_crisis
    ):
        """Two carried events of different document types with one count each: the stored
        carry-over lists the type carried first before the later one.

        ``x__add_deleted_events__mutmut_8`` appends the stored carry-over after the new counts
        and reverses the order on equal counts.
        """
        first = _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))
        second = _make_event(facility, client_identified, doc_type_crisis, staff_user, datetime(2024, 6, 16, 10, 0))

        _delete_and_carry(facility, first)
        _delete_and_carry(facility, second)

        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=6)
        carried = snapshot.backend_data["_deleted"]["_statistics"]["by_document_type"]
        assert [(entry["name"], entry["count"]) for entry in carried] == [
            (doc_type_contact.name, 1),
            (doc_type_crisis.name, 1),
        ]


class TestFailedCarryOverNamesTheEvent:
    def test_error_message_names_the_event(
        self, facility, staff_user, client_identified, doc_type_contact, monkeypatch
    ):
        """A failing carry-over raises ``SnapshotCarryError`` naming the event; the deletion run
        logs this message.

        ``x_carry_deleted_event__mutmut_34`` raises without a message.
        """
        import core.services.dashboard.snapshot as snapshots

        event = _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))

        def fail(events):
            raise RuntimeError("summary failed")

        monkeypatch.setattr(snapshots, "get_statistics_for_events", fail)

        with pytest.raises(SnapshotCarryError) as excinfo:
            carry_deleted_event(facility, event)

        assert str(excinfo.value) == f"monthly statistics carry-over failed for event {event.pk}"


class TestLeadDashboardNamesTheLatestCompletedMonth:
    def test_a_row_of_a_later_year_is_not_the_last_snapshot(self, facility, lead_user):
        """On 15 October 2026 the card names September 2026, not a March 2027 row (the carry-over
        of an event dated in the future), although that row sorts first.

        ``x_lead_dashboard_context__mutmut_19`` drops the year from the running-year condition.
        """
        completed = StatisticsSnapshot.objects.create(facility=facility, year=2026, month=9, data={})
        StatisticsSnapshot.objects.create(facility=facility, year=2027, month=3, data={})
        StatisticsSnapshot.objects.create(facility=facility, year=2026, month=10, data={})

        with mock.patch("django.utils.timezone.now", return_value=datetime(2026, 10, 15, 10, 0, tzinfo=UTC)):
            context = lead_dashboard_context(lead_user, facility)

        assert context["last_snapshot"] == completed
