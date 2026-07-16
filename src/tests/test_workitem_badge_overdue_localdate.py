"""Near-midnight TZ edge case for the navigation "overdue" WorkItem badge (COD-02).

``core.context_processors.workitem_counts`` derived the overdue anchor from the
naive, server-local ``datetime.date.today()`` although the project runs with
``USE_TZ=True`` / ``TIME_ZONE="Europe/Berlin"``. Every other overdue/expiry site
(dashboard SQL, ``LegalHold.is_active``, #1191) uses ``timezone.localdate()``.
Near midnight the UTC date and the Berlin local date differ, so the nav badge
could disagree with the dashboard/focus box by a full day.

The test pins an instant where the UTC calendar date and the Berlin calendar
date diverge. The naive ``date.today()`` (container runs in UTC) resolves to the
UTC date; ``timezone.localdate()`` must resolve to the Berlin date. A WorkItem
whose ``due_date`` sits exactly on the UTC date is overdue by the Berlin date but
not by the UTC date — so the count flips depending on which anchor wins.
Refs #1478 (COD-02).
"""

from datetime import UTC, date, datetime
from unittest import mock

import pytest
from django.test import RequestFactory
from django.utils import timezone

from core.context_processors import workitem_counts
from core.models import WorkItem

# 2026-06-21 22:30 UTC == 2026-06-22 00:30 Europe/Berlin (CEST, +02:00)
#   => UTC date    = 2026-06-21  (the naive/server-local value the bug used)
#      Berlin date = 2026-06-22  (what timezone.localdate() must return)
BOUNDARY_INSTANT = datetime(2026, 6, 21, 22, 30, tzinfo=UTC)
UTC_DATE = date(2026, 6, 21)
BERLIN_DATE = date(2026, 6, 22)


def _make_request(user, facility):
    request = RequestFactory().get("/")
    request.user = user
    request.current_facility = facility
    return request


@pytest.mark.django_db
class TestOverdueBadgeLocaldateBoundary:
    def test_overdue_uses_berlin_local_date_near_midnight(self, facility, staff_user):
        """A task due on the UTC date is already overdue by the Berlin date."""
        # Guard: the chosen instant truly diverges between UTC and Berlin.
        assert BOUNDARY_INSTANT.date() == UTC_DATE
        assert timezone.localtime(BOUNDARY_INSTANT).date() == BERLIN_DATE
        assert UTC_DATE != BERLIN_DATE

        WorkItem.objects.create(
            facility=facility,
            title="Frist gestern (Berlin)",
            status=WorkItem.Status.OPEN,
            assigned_to=staff_user,
            due_date=UTC_DATE,
        )
        request = _make_request(staff_user, facility)

        # Freeze "now" (drives timezone.localdate) to the boundary instant and
        # pin the naive date.today() to the container-UTC date, so the two
        # anchors genuinely disagree.
        with (
            mock.patch("django.utils.timezone.now", return_value=BOUNDARY_INSTANT),
            mock.patch("core.context_processors.datetime") as mock_dt,
        ):
            mock_dt.date.today.return_value = UTC_DATE
            ctx = workitem_counts(request)

        # Berlin "today" is 2026-06-22; due_date 2026-06-21 is in the past =>
        # the task must count as overdue. The naive UTC anchor would miss it.
        assert ctx["overdue_workitems_count"] == 1
