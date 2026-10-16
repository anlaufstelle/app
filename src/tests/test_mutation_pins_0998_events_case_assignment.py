"""Pins for the public case assignment boundary.

Source: mutation delta of the release-0.99.8 checkpoint, 1 mutant.
Groups: anonymized open cases (1).
"""

import pytest
from django.utils import timezone

from core.models import Case
from core.services.events.case_assignment import assignable_cases

pytestmark = pytest.mark.django_db


def test_anonymized_open_case_is_not_assignable(facility, staff_user, client_identified):
    """An anonymization stamp excludes an otherwise eligible open case.

    x_assignable_cases__mutmut_5.
    """
    hidden = Case.objects.create(facility=facility, client=client_identified, title="A anonymized")
    Case.objects.filter(pk=hidden.pk).update(anonymized_at=timezone.now())
    live = Case.objects.create(facility=facility, client=client_identified, title="Z available")
    assert [case.pk for case in assignable_cases(facility, staff_user)] == [live.pk]
