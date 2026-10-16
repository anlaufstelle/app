"""Pins for prepared client relations in the staff dashboard.

Source: mutation delta of the release-0.99.8 checkpoint, 1 mutant.
Groups: reading a returned task's client needs no additional database access (1).
"""

import pytest

from core.models import WorkItem
from core.services.dashboard import staff_dashboard_context

pytestmark = pytest.mark.django_db


def test_returned_tasks_have_their_client_ready(client_identified, staff_user, django_assert_num_queries):
    """Rendering returned client links needs no lazy reads: x_staff_dashboard_context__mutmut_27."""
    task = WorkItem.objects.create(
        facility=client_identified.facility,
        client=client_identified,
        assigned_to=staff_user,
        created_by=staff_user,
        title="Follow up",
        status=WorkItem.Status.OPEN,
    )

    context = staff_dashboard_context(staff_user, client_identified.facility)

    assert [item.pk for item in context["my_open_workitems"]] == [task.pk]
    with django_assert_num_queries(0):
        assert [item.client.pseudonym for item in context["my_open_workitems"]] == [client_identified.pseudonym]
