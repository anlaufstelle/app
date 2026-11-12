"""Shared demo accounts retain their login, activity and role (Refs #2717)."""

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from core.models import AuditLog, User
from tests.test_demo_guard import DEMO_REFUSAL, _demo_guard_settings, _messages
from tests.test_user_access_hub import _create_data, _update_data

pytestmark = pytest.mark.django_db

ACCOUNT_CHANGES = (
    pytest.param(False, "is_active", True, "", False, id="deactivate"),
    pytest.param(False, "is_active", False, "on", True, id="reactivate"),
    pytest.param(False, "role", User.Role.STAFF, User.Role.ASSISTANT, User.Role.ASSISTANT, id="demote"),
    pytest.param(False, "role", User.Role.STAFF, User.Role.LEAD, User.Role.LEAD, id="promote"),
    pytest.param(False, "username", "shared_login", "changed_login", "changed_login", id="rename-other"),
    pytest.param(True, "username", "shared_admin", "changed_admin", "changed_admin", id="rename-own"),
)


def _access_values(user):
    """Compare editable values without the freshly timestamped form token."""
    return {field: value for field, value in _update_data(user).items() if field != "basis_token"}


@pytest.mark.parametrize(("own_account", "field", "stored", "submitted", "expected"), ACCOUNT_CHANGES)
@pytest.mark.parametrize("demo_mode", [True, False], ids=["demo", "normal"])
def test_account_changes_are_refused_only_in_demo(
    client, admin_user, staff_user, own_account, field, stored, submitted, expected, demo_mode
):
    target = admin_user if own_account else staff_user
    setattr(target, field, stored)
    target.save(update_fields=[field])
    original = _access_values(target)
    target_session = Client()
    if target.is_active:
        target_session.force_login(target)
    client.force_login(admin_user)
    audit_ids = set(AuditLog.objects.values_list("pk", flat=True))
    url = reverse("core:user_access_update", kwargs={"pk": target.pk})

    with override_settings(DEMO_MODE=demo_mode, **_demo_guard_settings()):
        response = client.post(
            url,
            _update_data(target, **{field: submitted, "display_name": "Changed name", "email": "changed@example.org"}),
        )
        target.refresh_from_db()
        assert response.status_code == 302
        assert response.url == url
        if demo_mode:
            assert _messages(response) == [DEMO_REFUSAL]
            assert _access_values(target) == original
            assert set(AuditLog.objects.values_list("pk", flat=True)) == audit_ids
            if target.is_active:
                assert target_session.get("/").status_code == 200
        else:
            assert _messages(response) == ["Zugang wurde gespeichert."]
            assert getattr(target, field) == expected
            assert target.display_name == "Changed name"
            assert target.email == "changed@example.org"
            assert set(AuditLog.objects.values_list("pk", flat=True)) > audit_ids


@pytest.mark.parametrize("own_account", [False, True], ids=["other", "own"])
@pytest.mark.parametrize("change_other_fields", [False, True], ids=["unchanged", "other-fields"])
def test_demo_accepts_unchanged_protected_fields(client, admin_user, staff_user, own_account, change_other_fields):
    target = admin_user if own_account else staff_user
    original = _access_values(target)
    changes = {"display_name": "Changed name", "email": "changed@example.org"} if change_other_fields else {}
    client.force_login(admin_user)
    audit_ids = set(AuditLog.objects.values_list("pk", flat=True))

    with override_settings(DEMO_MODE=True, **_demo_guard_settings()):
        response = client.post(
            reverse("core:user_access_update", kwargs={"pk": target.pk}), _update_data(target, **changes)
        )

    target.refresh_from_db()
    assert response.status_code == 302
    assert _messages(response) == ["Zugang wurde gespeichert."]
    for field in ("username", "role", "is_active", "mfa_required"):
        assert _access_values(target)[field] == original[field]
    if change_other_fields:
        assert target.display_name == changes["display_name"]
        assert target.email == changes["email"]
        assert set(AuditLog.objects.values_list("pk", flat=True)) > audit_ids
    else:
        assert _access_values(target) == original
        assert set(AuditLog.objects.values_list("pk", flat=True)) == audit_ids


@pytest.mark.parametrize("demo_mode", [True, False], ids=["demo", "normal"])
@pytest.mark.parametrize(
    ("field", "submitted"), [("is_active", ""), ("role", User.Role.ASSISTANT)], ids=["deactivate", "demote"]
)
def test_own_activity_and_role_already_reject_changes(client, admin_user, demo_mode, field, submitted):
    original = _access_values(admin_user)
    client.force_login(admin_user)
    audit_ids = set(AuditLog.objects.values_list("pk", flat=True))

    with override_settings(DEMO_MODE=demo_mode, **_demo_guard_settings()):
        response = client.post(
            reverse("core:user_access_update", kwargs={"pk": admin_user.pk}),
            _update_data(admin_user, **{field: submitted}),
        )

    admin_user.refresh_from_db()
    assert response.status_code == 200
    assert field in response.context["form"].errors
    assert _access_values(admin_user) == original
    assert set(AuditLog.objects.values_list("pk", flat=True)) == audit_ids


def test_demo_invitation_cannot_change_an_existing_shared_account(client, admin_user, staff_user):
    original = _access_values(staff_user)
    password = staff_user.password
    client.force_login(admin_user)
    audit_ids = set(AuditLog.objects.values_list("pk", flat=True))

    with override_settings(DEMO_MODE=True, **_demo_guard_settings()):
        response = client.post(
            reverse("core:user_access_invite", kwargs={"pk": staff_user.pk}),
            {"username": "changed_login", "role": User.Role.ASSISTANT, "is_active": ""},
        )

    staff_user.refresh_from_db()
    assert response.status_code == 302
    assert _access_values(staff_user) == original
    assert staff_user.password == password
    assert set(AuditLog.objects.values_list("pk", flat=True)) == audit_ids


def test_demo_creation_cannot_overwrite_an_existing_shared_account(client, admin_user, staff_user):
    original = _access_values(staff_user)
    client.force_login(admin_user)
    audit_ids = set(AuditLog.objects.values_list("pk", flat=True))

    with override_settings(DEMO_MODE=True, **_demo_guard_settings()):
        response = client.post(
            reverse("core:user_access_create"),
            _create_data(username=staff_user.username, role=User.Role.ASSISTANT),
        )

    staff_user.refresh_from_db()
    assert response.status_code == 200
    assert "username" in response.context["form"].errors
    assert _access_values(staff_user) == original
    assert set(AuditLog.objects.values_list("pk", flat=True)) == audit_ids
