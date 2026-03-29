"""Unit tests for the AuditLog list view."""

from datetime import timedelta

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog


def _make_audit_log(facility, user, action=AuditLog.Action.LOGIN, delta_days=0):
    """Helper: create an AuditLog entry, optionally with a backdated timestamp.

    Uses raw SQL INSERT to set the timestamp directly, bypassing the
    immutable trigger (INSERT is allowed, only UPDATE/DELETE are blocked).
    """
    import uuid

    entry_id = uuid.uuid4()
    ts = timezone.now() - timedelta(days=delta_days)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core_auditlog"
            " (id, facility_id, user_id, action, target_type,"
            " target_id, detail, ip_address, timestamp)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [entry_id, facility.pk, user.pk, action, "", "", "{}", None, ts],
        )
    return AuditLog.objects.get(pk=entry_id)


@pytest.mark.django_db
def test_audit_log_list_renders(client, admin_user, facility):
    """Admin can access the audit log and receives HTTP 200."""
    client.force_login(admin_user)
    response = client.get(reverse("core:audit_log"))
    assert response.status_code == 200
    assert "page_obj" in response.context
    assert "action_choices" in response.context
    assert "facility_users" in response.context


@pytest.mark.django_db
def test_audit_log_pagination(client, admin_user, facility):
    """Creating >50 entries results in a paginated response."""
    for _ in range(55):
        AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.LOGIN)

    client.force_login(admin_user)
    response = client.get(reverse("core:audit_log"))
    assert response.status_code == 200

    page_obj = response.context["page_obj"]
    assert page_obj.paginator.num_pages > 1
    assert len(page_obj.object_list) == 50


@pytest.mark.django_db
def test_audit_log_filter_by_action(client, admin_user, facility):
    """Filter ?action=login returns only login entries."""
    AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.LOGIN)
    AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.LOGOUT)
    AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.EXPORT)

    client.force_login(admin_user)
    response = client.get(reverse("core:audit_log") + "?action=login")
    assert response.status_code == 200

    page_obj = response.context["page_obj"]
    actions = [entry.action for entry in page_obj.object_list]
    assert all(a == AuditLog.Action.LOGIN for a in actions)
    assert response.context["filter_action"] == "login"


@pytest.mark.django_db
def test_audit_log_filter_by_date(client, admin_user, facility):
    """Filter by date_from/date_to restricts the result set."""
    # Entry 10 days ago
    old_entry = _make_audit_log(facility, admin_user, delta_days=10)
    # Entry today
    new_entry = _make_audit_log(facility, admin_user, delta_days=0)

    client.force_login(admin_user)

    # Filter: only entries from 5 days ago onwards
    date_from = (timezone.now() - timedelta(days=5)).date().isoformat()
    response = client.get(reverse("core:audit_log") + f"?date_from={date_from}")
    assert response.status_code == 200

    ids = [str(entry.pk) for entry in response.context["page_obj"].object_list]
    assert str(new_entry.pk) in ids
    assert str(old_entry.pk) not in ids


@pytest.mark.django_db
def test_audit_log_htmx_returns_partial(client, admin_user, facility):
    """An HX-Request header causes the view to return the partial table template."""
    AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.LOGIN)

    client.force_login(admin_user)
    response = client.get(
        reverse("core:audit_log"),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    # The partial template is used, not the full list template
    templates_used = [t.name for t in response.templates]
    assert "core/audit/partials/table.html" in templates_used
    assert "core/audit/list.html" not in templates_used
