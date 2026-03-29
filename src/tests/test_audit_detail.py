"""Unit tests for the AuditLog detail view."""

import pytest
from django.urls import reverse

from core.models import AuditLog


@pytest.mark.django_db
def test_audit_detail_as_admin(client, admin_user, facility):
    """Admin can access the audit log detail view."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
        detail={"message": "Test login"},
        ip_address="127.0.0.1",
    )

    client.force_login(admin_user)
    response = client.get(reverse("core:audit_detail", kwargs={"pk": entry.pk}))
    assert response.status_code == 200
    assert "entry" in response.context
    assert response.context["entry"].pk == entry.pk


@pytest.mark.django_db
def test_audit_detail_forbidden_for_non_admin(client, staff_user, facility):
    """Non-admin users cannot access the audit log detail view (403)."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.LOGIN,
    )

    client.force_login(staff_user)
    response = client.get(reverse("core:audit_detail", kwargs={"pk": entry.pk}))
    assert response.status_code == 403


@pytest.mark.django_db
def test_audit_detail_shows_all_fields(client, admin_user, facility):
    """Detail view renders all audit log fields correctly."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.EXPORT,
        target_type="CSV",
        target_id="some-uuid",
        detail={"format": "CSV", "date_from": "2026-01-01", "date_to": "2026-03-01"},
        ip_address="10.0.0.1",
    )

    client.force_login(admin_user)
    response = client.get(reverse("core:audit_detail", kwargs={"pk": entry.pk}))
    assert response.status_code == 200

    content = response.content.decode()
    assert "CSV" in content
    assert "some-uuid" in content
    assert "10.0.0.1" in content
    assert "2026-01-01" in content
    assert "2026-03-01" in content


@pytest.mark.django_db
def test_audit_detail_empty_detail(client, admin_user, facility):
    """Detail view handles empty detail dict gracefully."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )

    client.force_login(admin_user)
    response = client.get(reverse("core:audit_detail", kwargs={"pk": entry.pk}))
    assert response.status_code == 200
