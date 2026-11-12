"""The Zeitstrom page builds each handover summary at most once per request.

The feed's shift summary, the handover summary and the current-shift box can
all ask for the same facility, user, date and shift. The page must compute
such a summary once and reuse it; different shifts still get their own.
"""

from collections import Counter
from datetime import UTC, date, datetime, time
from unittest.mock import patch

import pytest
from django.urls import reverse

from core.models import TimeFilter
from core.services import case as case_services

#: 12:00 in Berlin (CEST): the early shift is running.
INSTANT = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
TODAY = date(2026, 6, 22)
YESTERDAY = date(2026, 6, 21)


@pytest.fixture
def shifts(facility):
    return {
        "early": TimeFilter.objects.create(
            facility=facility, label="Frühdienst", start_time=time(8, 0), end_time=time(16, 0), sort_order=0
        ),
        "late": TimeFilter.objects.create(
            facility=facility, label="Spätdienst", start_time=time(16, 0), end_time=time(22, 0), sort_order=1
        ),
        "night": TimeFilter.objects.create(
            facility=facility, label="Nachtdienst", start_time=time(22, 0), end_time=time(8, 0), sort_order=2
        ),
    }


def _summary_calls(client, user, params):
    """Run one page request and count summary builds per (date, shift)."""
    original = case_services.build_handover_summary
    calls = Counter()

    def spy(facility, target_date, time_filter, request_user):
        calls[(target_date, time_filter.label if time_filter else None)] += 1
        return original(facility, target_date, time_filter, request_user)

    client.force_login(user)
    with (
        patch("django.utils.timezone.now", return_value=INSTANT),
        patch("core.views.zeitstrom.build_handover_summary", side_effect=spy),
        patch("core.services.case.build_handover_summary", side_effect=spy),
    ):
        response = client.get(reverse("core:zeitstrom"), params)
    assert response.status_code == 200
    return calls, response.context


@pytest.mark.django_db
class TestHandoverSummaryOncePerRequest:
    def test_running_shift_shares_one_summary_with_the_current_shift_box(self, client, staff_user, shifts):
        calls, context = _summary_calls(client, staff_user, {})

        assert calls == Counter({(TODAY, "Frühdienst"): 1})
        assert context["handover_summary"] is context["current_shift_summary"]

    def test_handover_mode_builds_the_displayed_shift_once(self, client, staff_user, shifts):
        calls, context = _summary_calls(client, staff_user, {"view": "uebergabe"})

        assert calls == Counter({(YESTERDAY, "Nachtdienst"): 1, (TODAY, "Frühdienst"): 1})
        assert context["uebergabe_summary"] is context["handover_summary"]
        assert context["current_shift_summary"]["shift_label"] == "Frühdienst"

    def test_handover_for_the_running_shift_builds_it_once(self, client, staff_user, shifts):
        params = {"view": "uebergabe", "date": TODAY.isoformat(), "time_filter": str(shifts["early"].pk)}

        calls, _context = _summary_calls(client, staff_user, params)

        assert calls == Counter({(TODAY, "Frühdienst"): 1})

    def test_historical_shift_keeps_its_own_summary(self, client, staff_user, shifts):
        params = {"date": YESTERDAY.isoformat(), "time_filter": str(shifts["late"].pk)}

        calls, context = _summary_calls(client, staff_user, params)

        assert calls == Counter({(YESTERDAY, "Spätdienst"): 1, (TODAY, "Frühdienst"): 1})
        assert context["handover_summary"]["shift_label"] == "Spätdienst"
        assert context["current_shift_summary"]["shift_label"] == "Frühdienst"

    def test_handover_mode_builds_no_tally_summary(self, client, staff_user, shifts):
        client.force_login(staff_user)
        with (
            patch("django.utils.timezone.now", return_value=INSTANT),
            patch("core.views.zeitstrom.build_tally_summary", side_effect=AssertionError("tally summary built")),
            patch(
                "core.services.zeitstrom_watch.build_tally_summary",
                side_effect=AssertionError("tally summary built"),
            ),
        ):
            response = client.get(reverse("core:zeitstrom"), {"view": "uebergabe"})

        assert response.status_code == 200
        assert response.context["tally_summary"] is None
