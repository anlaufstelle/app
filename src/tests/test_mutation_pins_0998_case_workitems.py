"""Pins for assignment locks, recipient ordering and inbox annotations.

Source: mutation delta of the release-0.99.8 checkpoint, 20 mutants.
Groups: recipient lock mode (2), recipient name order (2), priority (6),
due-date buckets (10). Follows test_mutation_pins_0997_case_cases.py.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.http import QueryDict
from django.utils import timezone

from core.models import User, WorkItem
from core.services.case.workitems import assignable_workitem_users, build_inbox_sections, bulk_assign_workitems

pytestmark = pytest.mark.django_db


@pytest.mark.postgres
def test_assignment_locks_recipient_without_blocking_foreign_keys(sample_workitem, staff_user, lead_user):
    """Assignment uses a scoped no-key recipient lock that permits FK readers.

    Kills x__locked_workitem_recipient__mutmut_3 and
    x__locked_workitem_recipient__mutmut_5 through bulk_assign_workitems.
    """
    statements = []

    def capture(execute, sql, params, many, context):
        if 'FROM "core_user"' in sql and "FOR " in sql:
            statements.append((sql, params))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        bulk_assign_workitems([sample_workitem], staff_user, lead_user)

    sample_workitem.refresh_from_db()
    assert sample_workitem.assigned_to_id == lead_user.pk
    assert statements
    for sql, params in statements:
        assert "FOR NO KEY UPDATE" in sql
        assert "WHERE" in sql
        assert '"core_user"."id" = %s' in sql
        assert '"core_user"."facility_id" = %s' in sql
        assert lead_user.pk in params
        assert lead_user.facility_id in params


def test_recipients_sort_by_surname_then_first_name_then_username(facility):
    """Surname and first name precede username even when their orders disagree.

    Kills x_assignable_workitem_users__mutmut_4 and
    x_assignable_workitem_users__mutmut_5.
    """
    users = [
        User.objects.create(
            facility=facility, role=User.Role.STAFF, username=username, first_name=first, last_name=last
        )
        for username, first, last in [
            ("a-second-surname", "Aaron", "Zulu"),
            ("b-second-first-name", "Zoe", "Alpha"),
            ("z-first-name", "Beth", "Alpha"),
            ("y-first-name", "Beth", "Alpha"),
        ]
    ]
    assert list(assignable_workitem_users(facility)) == [users[3], users[2], users[1], users[0]]


def test_inbox_returns_priority_and_due_date_annotations(facility, staff_user):
    """Returned tasks expose normal priority 2 and the complete due-date buckets.

    Kills x_build_inbox_sections__mutmut_34, x_build_inbox_sections__mutmut_48,
    x_build_inbox_sections__mutmut_49, x_build_inbox_sections__mutmut_51,
    x_build_inbox_sections__mutmut_52, x_build_inbox_sections__mutmut_53,
    x_build_inbox_sections__mutmut_63, x_build_inbox_sections__mutmut_69,
    x_build_inbox_sections__mutmut_73, x_build_inbox_sections__mutmut_79,
    x_build_inbox_sections__mutmut_85, x_build_inbox_sections__mutmut_87,
    x_build_inbox_sections__mutmut_89, x_build_inbox_sections__mutmut_91,
    x_build_inbox_sections__mutmut_92 and x_build_inbox_sections__mutmut_94.
    """
    today = timezone.localdate()
    expected = {}
    for status, due_date, bucket in [
        (WorkItem.Status.OPEN, None, 9),
        (WorkItem.Status.OPEN, today + timedelta(days=2), 2),
        (WorkItem.Status.OPEN, today, 1),
        (WorkItem.Status.OPEN, today - timedelta(days=2), 0),
        (WorkItem.Status.DONE, today - timedelta(days=2), 5),
    ]:
        item = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Annotation contract",
            priority=WorkItem.Priority.NORMAL,
            status=status,
            due_date=due_date,
            completed_at=timezone.now() if status == WorkItem.Status.DONE else None,
        )
        expected[item.pk] = (2, bucket)
    result = build_inbox_sections(facility, staff_user, QueryDict(""))
    returned = [*result["open_items"], *result["done_items"]]
    assert {item.pk: (item.priority_order, item.due_date_bucket) for item in returned} == expected
