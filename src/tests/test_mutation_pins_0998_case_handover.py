"""Pins for new-client counts in handover summaries.

Source: mutation delta of the release-0.99.8 checkpoint, 2 mutants.
Groups: deleted clients (1), inactive clients (1).
Follows test_mutation_pins_0997_case_cases.py.
"""

from datetime import date, datetime, time

import pytest
from django.utils import timezone

from core.models import Client, TimeFilter
from core.services.case.handover import build_handover_summary
from tests._factories import make_client

pytestmark = pytest.mark.django_db


def test_new_clients_exclude_deleted_and_inactive_rows(facility, staff_user):
    """Only active, undeleted clients created within the shift count as new.

    Kills x__collect_stats__mutmut_38 and x__collect_stats__mutmut_39
    through build_handover_summary.
    """
    shift_date = date(2026, 6, 15)
    time_filter = TimeFilter.objects.create(facility=facility, label="Morning", start_time=time(6), end_time=time(14))
    clients = [
        make_client(facility=facility, created_by=staff_user, pseudonym=name, **flags)
        for name, flags in [
            ("Deleted", {"is_deleted": True}),
            ("Inactive", {"is_active": False}),
            ("Eligible", {}),
        ]
    ]
    Client.objects.filter(pk__in=[client.pk for client in clients]).update(
        created_at=timezone.make_aware(datetime(2026, 6, 15, 10))
    )
    summary = build_handover_summary(facility, shift_date, time_filter, staff_user)
    assert summary["stats"]["clients_new"] == 1
