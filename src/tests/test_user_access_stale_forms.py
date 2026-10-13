"""Access updates require the signed state the administrator reviewed."""

import copy

import pytest
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.test import Client as HttpClient
from django.urls import reverse

from core.models import AuditLog, User
from core.services.user_access import update_user_access

pytestmark = pytest.mark.django_db


def access_data(user):
    return {
        name: getattr(user, name)
        for name in ("username", "email", "display_name", "role", "is_active", "mfa_required", "can_confirm_deletion")
    }


def access_token(client, user):
    response = client.get(reverse("core:user_access_update", args=[user.pk]))
    return response.context["form"].initial.get("basis_token", "")


@pytest.mark.parametrize(
    "changed",
    [
        {"is_active": False},
        {"mfa_required": True},
        {"can_confirm_deletion": True},
        {"role": User.Role.ASSISTANT},
        {"username": "new_login"},
        {"email": "new@example.org"},
        {"display_name": "New name"},
    ],
)
def test_stale_access_preserves_current_state_and_submitted_inputs(client, admin_user, staff_user, changed):
    client.force_login(admin_user)
    url = reverse("core:user_access_update", args=[staff_user.pk])
    data = access_data(staff_user) | {
        "basis_token": access_token(client, staff_user),
        "display_name": "My draft",
        "role": User.Role.LEAD,
    }
    User.objects.filter(pk=staff_user.pk).update(**changed)
    before = AuditLog.objects.count()
    response = client.post(url, data)
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert response.context["form"]["display_name"].value() == "My draft"
    assert "Aktueller Sicherheitsstand" in response.content.decode()
    staff_user.refresh_from_db()
    assert all(getattr(staff_user, key) == value for key, value in changed.items())
    assert AuditLog.objects.count() == before
    refreshed = response.context["form"]["basis_token"].value()
    assert refreshed and refreshed != data["basis_token"]
    # Explicit resubmission against the displayed current state is allowed.
    data["basis_token"] = refreshed
    assert client.post(url, data).status_code == 302
    staff_user.refresh_from_db()
    assert staff_user.display_name == "My draft"


@pytest.mark.parametrize("kind", ["missing", "tampered", "other_user", "other_facility"])
def test_untrusted_access_state_is_rejected(client, admin_user, staff_user, lead_user, foreign_staff, kind):
    client.force_login(admin_user)
    token = access_token(client, staff_user)
    if kind == "missing":
        token = ""
    elif kind == "tampered":
        token += "broken"
    else:
        from core.forms.user_access import UserAccessUpdateForm

        other = lead_user if kind == "other_user" else copy.copy(staff_user)
        if kind == "other_facility":
            other.facility = foreign_staff.facility
        token = UserAccessUpdateForm(instance=other, facility=other.facility, actor=admin_user).initial.get(
            "basis_token", ""
        )
    old_name = staff_user.display_name
    response = client.post(
        reverse("core:user_access_update", args=[staff_user.pk]),
        access_data(staff_user) | {"basis_token": token, "display_name": "Rejected"},
    )
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    staff_user.refresh_from_db()
    assert staff_user.display_name == old_name


def test_access_service_requires_basis_state(facility, admin_user, staff_user):
    old_name = staff_user.display_name
    with pytest.raises(ValidationError):
        update_user_access(
            staff_user, facility=facility, actor=admin_user, **(access_data(staff_user) | {"display_name": "Rejected"})
        )
    staff_user.refresh_from_db()
    assert staff_user.display_name == old_name


def test_access_service_checks_under_lock(client, facility, admin_user, staff_user):
    staff_user.ensure_offline_key_salt()
    client.force_login(admin_user)
    data = access_data(staff_user) | {"basis_token": access_token(client, staff_user)}
    target_client = HttpClient()
    target_client.force_login(staff_user)
    session_key = target_client.session.session_key
    old_salt = staff_user.offline_key_salt
    # Tab A legitimately saves; tab B still holds the previous active state.
    response = client.post(
        reverse("core:user_access_update", args=[staff_user.pk]),
        data | {"is_active": False, "mfa_required": True, "can_confirm_deletion": True},
    )
    assert response.status_code == 302
    assert not Session.objects.filter(session_key=session_key).exists()
    staff_user.refresh_from_db()
    assert staff_user.offline_key_salt != old_salt
    assert AuditLog.objects.get(user=staff_user, action=AuditLog.Action.USER_DEACTIVATED).actor == admin_user
    data["role"] = User.Role.LEAD
    with pytest.raises(ValidationError):
        update_user_access(staff_user, facility=facility, actor=admin_user, **data)
    staff_user.refresh_from_db()
    assert not staff_user.is_active and staff_user.mfa_required and staff_user.can_confirm_deletion
