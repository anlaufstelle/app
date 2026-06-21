"""Near-midnight TZ edge case for LegalHold active/expired classification (#1191).

``LegalHold.is_active`` (model property) and the Dashboard-SQL filter from #1171
classified holds by the naive, server-local ``date.today()`` although the
project runs with ``USE_TZ=True`` / ``TIME_ZONE="Europe/Berlin"``. Near
midnight the UTC date and the Berlin local date differ, so the naive code could
shift the active/expired boundary by a full day. Both sites must instead use
``django.utils.timezone.localdate()`` and agree on the SAME boundary date.

The test pins an instant where the UTC calendar date and the Berlin calendar
date diverge and asserts the Berlin local date decides — for BOTH the model
property and the dashboard SQL. ``freezegun`` is not installed in this venv, so
the boundary is built by patching ``timezone.now`` (which ``localdate()``
derives from). The genuine TZ boundary — not a tautology — is that the naive
``date.today()`` would return the UTC date here (the container runs in UTC),
which classifies the boundary hold as still active, whereas the Berlin local
date classifies it as expired.
"""

from datetime import UTC, date, datetime
from unittest import mock

import pytest
from django.utils import timezone

from core.models import LegalHold
from core.services.dashboard.main import lead_dashboard_context

# Instant where the UTC calendar date and the Europe/Berlin calendar date differ:
#   2026-06-21 22:30 UTC  ==  2026-06-22 00:30 Europe/Berlin (CEST, +02:00)
# => UTC date    = 2026-06-21  (the naive/server-local value the bug used)
#    Berlin date = 2026-06-22  (what timezone.localdate() must return)
BOUNDARY_INSTANT = datetime(2026, 6, 21, 22, 30, tzinfo=UTC)
UTC_DATE = date(2026, 6, 21)
BERLIN_DATE = date(2026, 6, 22)

# A hold expiring on the UTC date sits exactly between the two candidate "today"
# values, so the verdict flips depending on which date wins:
#   naive/UTC  today=2026-06-21 -> expires_at < today is False -> ACTIVE   (bug)
#   Berlin     today=2026-06-22 -> expires_at < today is True  -> EXPIRED  (fix)
EXPIRES_ON_UTC_DATE = UTC_DATE


def _assert_boundary_really_diverges():
    """Pin the chosen instant: UTC date and Berlin local date truly differ."""
    assert BOUNDARY_INSTANT.date() == UTC_DATE
    assert timezone.localtime(BOUNDARY_INSTANT).date() == BERLIN_DATE
    assert UTC_DATE != BERLIN_DATE


@pytest.mark.django_db
class TestLegalHoldLocaldateBoundary:
    def test_is_active_uses_berlin_local_date_near_midnight(self, facility, lead_user):
        """The model property must expire the boundary hold by the Berlin date."""
        _assert_boundary_really_diverges()
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=lead_user.pk,  # any UUID-shaped value; not dereferenced here
            reason="Boundary hold",
            expires_at=EXPIRES_ON_UTC_DATE,
            created_by=lead_user,
        )

        # Freeze "now" to the boundary instant; localdate() reads now and must
        # resolve to the Berlin date 2026-06-22 (> expires_at) => expired.
        with mock.patch("django.utils.timezone.now", return_value=BOUNDARY_INSTANT):
            assert timezone.localdate() == BERLIN_DATE  # guard: lever is wired
            assert hold.is_active is False

    def test_dashboard_sql_uses_berlin_local_date_near_midnight(self, facility, lead_user):
        """The dashboard SQL filter must agree: boundary hold is NOT active."""
        _assert_boundary_really_diverges()
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=lead_user.pk,
            reason="Boundary hold",
            expires_at=EXPIRES_ON_UTC_DATE,
            created_by=lead_user,
        )

        with mock.patch("django.utils.timezone.now", return_value=BOUNDARY_INSTANT):
            ctx = lead_dashboard_context(lead_user, facility)

        # Berlin "today" 2026-06-22; expires_at 2026-06-21 is in the past =>
        # the hold is expired and must not be counted as active.
        assert ctx["active_legal_holds"] == 0

    def test_both_sites_agree_on_the_same_boundary_date(self, facility, lead_user):
        """Lockstep: property and SQL classify the SAME hold identically."""
        _assert_boundary_really_diverges()
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=lead_user.pk,
            reason="Boundary hold",
            expires_at=EXPIRES_ON_UTC_DATE,
            created_by=lead_user,
        )

        with mock.patch("django.utils.timezone.now", return_value=BOUNDARY_INSTANT):
            property_active = hold.is_active
            ctx = lead_dashboard_context(lead_user, facility)
            sql_active = ctx["active_legal_holds"] == 1

        # Both must say "not active" (Berlin date), i.e. they stay in lockstep.
        assert property_active is False
        assert sql_active is False
        assert property_active == sql_active
