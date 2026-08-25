"""Zugaenge-Reiter im Verwaltungs-Hub (Refs #1871)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.urls import reverse

from core.forms.user_access import UserAccessCreateForm, UserAccessUpdateForm
from core.models import AuditLog, User
from core.services.security import LOCKOUT_THRESHOLD
from core.services.user_access import (
    _assert_actor,
    create_invited_user,
    resend_user_invitation,
    unlock_managed_user,
    update_user_access,
)

pytestmark = pytest.mark.django_db


def _create_data(**overrides):
    data = {
        "username": "neue_fachkraft",
        "email": "neu@example.org",
        "display_name": "Neue Fachkraft",
        "role": User.Role.STAFF,
        "mfa_required": "on",
        "can_confirm_deletion": "on",
    }
    data.update(overrides)
    return data


def _update_data(user, **overrides):
    data = {
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": "on" if user.is_active else "",
        "mfa_required": "on" if user.mfa_required else "",
        "can_confirm_deletion": "on" if user.can_confirm_deletion else "",
    }
    data.update(overrides)
    return data


def _service_values(user, **overrides):
    values = {
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "mfa_required": user.mfa_required,
        "can_confirm_deletion": user.can_confirm_deletion,
    }
    values.update(overrides)
    return values


def _pending_invite(facility, username="pending_invite"):
    user = User(
        username=username,
        email=f"{username}@example.org",
        role=User.Role.STAFF,
        facility=facility,
        must_change_password=True,
    )
    user.set_unusable_password()
    user.save()
    return user


class TestUserAccessForms:
    def test_create_form_exposes_only_facility_roles_and_requires_email(self, facility, admin_user):
        form = UserAccessCreateForm(facility=facility, actor=admin_user)

        assert set(form.fields) == {
            "username",
            "email",
            "display_name",
            "role",
            "mfa_required",
            "can_confirm_deletion",
        }
        assert form.fields["email"].required is True
        assert User.Role.SUPER_ADMIN not in {value for value, _label in form.fields["role"].choices}

    def test_update_form_never_exposes_facility_or_django_permission_fields(self, facility, admin_user, staff_user):
        form = UserAccessUpdateForm(instance=staff_user, facility=facility, actor=admin_user)

        assert set(form.fields) == {
            "username",
            "email",
            "display_name",
            "role",
            "is_active",
            "mfa_required",
            "can_confirm_deletion",
        }
        assert set(form.fields).isdisjoint(
            {"facility", "password", "is_staff", "is_superuser", "groups", "user_permissions"}
        )

    def test_admin_cannot_deactivate_or_demote_own_account(self, facility, admin_user):
        deactivate = UserAccessUpdateForm(
            _update_data(admin_user, is_active=""),
            instance=admin_user,
            facility=facility,
            actor=admin_user,
        )
        demote = UserAccessUpdateForm(
            _update_data(admin_user, role=User.Role.STAFF),
            instance=admin_user,
            facility=facility,
            actor=admin_user,
        )

        assert not deactivate.is_valid()
        assert "is_active" in deactivate.errors
        assert not demote.is_valid()
        assert "role" in demote.errors

    def test_other_active_account_remains_a_valid_update(self, facility, admin_user, staff_user):
        form = UserAccessUpdateForm(
            _update_data(staff_user),
            instance=staff_user,
            facility=facility,
            actor=admin_user,
        )

        assert form.is_valid(), form.errors

    def test_other_inactive_account_remains_a_valid_update(self, facility, admin_user, staff_user):
        form = UserAccessUpdateForm(
            _update_data(staff_user, is_active=""),
            instance=staff_user,
            facility=facility,
            actor=admin_user,
        )

        assert form.is_valid(), form.errors


class TestUserAccessList:
    def test_list_is_explicitly_facility_scoped_and_hides_super_admin(
        self, client, admin_user, staff_user, foreign_staff, super_admin_user
    ):
        client.force_login(admin_user)

        response = client.get(reverse("core:user_access_list"))

        assert response.status_code == 200
        content = response.content.decode()
        assert staff_user.username in content
        assert foreign_staff.username not in content
        assert super_admin_user.username not in content
        assert response.headers["Cache-Control"] == "no-store"

    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    def test_every_other_role_is_forbidden(self, client, request, user_fixture):
        client.force_login(request.getfixturevalue(user_fixture))

        assert client.get(reverse("core:user_access_list")).status_code == 403

    def test_locked_account_is_marked(self, client, facility, admin_user, staff_user):
        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"username": staff_user.username},
            )
        client.force_login(admin_user)

        content = client.get(reverse("core:user_access_list")).content.decode()

        assert f'data-user-id="{staff_user.pk}"' in content
        assert "Gesperrt" in content


class TestUserAccessSudoPolicy:
    @override_settings(SUDO_MODE_ENABLED=True)
    def test_list_is_readable_but_every_management_entry_requires_sudo(self, client, admin_user, staff_user):
        client.force_login(admin_user)

        assert client.get(reverse("core:user_access_list")).status_code == 200
        probes = (
            ("get", reverse("core:user_access_create"), None),
            ("get", reverse("core:user_access_update", args=[staff_user.pk]), None),
            ("post", reverse("core:user_access_invite", args=[staff_user.pk]), {}),
            ("post", reverse("core:user_access_unlock", args=[staff_user.pk]), {}),
        )
        for method, url, data in probes:
            response = getattr(client, method)(url, data)
            assert response.status_code == 302
            assert response.url.startswith(reverse("sudo_mode"))


class TestCreateInvitedUser:
    def test_create_sends_invite_and_audits_target_and_actor(self, client, facility, admin_user):
        client.force_login(admin_user)

        with patch("core.services.user_access.send_invite_email", return_value=True) as send:
            response = client.post(
                reverse("core:user_access_create"),
                _create_data(),
                REMOTE_ADDR="203.0.113.84",
            )

        assert response.status_code == 302
        created = User.objects.get(username="neue_fachkraft")
        assert response.url == reverse("core:user_access_update", args=[created.pk])
        assert created.facility == facility
        assert created.email == "neu@example.org"
        assert created.display_name == "Neue Fachkraft"
        assert created.role == User.Role.STAFF
        assert created.mfa_required is True
        assert created.can_confirm_deletion is True
        assert created.must_change_password is True
        assert not created.has_usable_password()
        send.assert_called_once_with(created)

        created_row = AuditLog.objects.get(action=AuditLog.Action.USER_CREATED, user=created)
        invite_row = AuditLog.objects.get(action=AuditLog.Action.USER_INVITE_SENT, user=created)
        for row in (created_row, invite_row):
            assert row.facility == facility
            assert row.actor == admin_user
            assert row.target_type == "User"
            assert row.target_id == str(created.pk)
        assert created_row.detail["new_role"] == User.Role.STAFF
        assert invite_row.detail["trigger"] == "invite_create"
        assert invite_row.detail["username"] == created.username
        assert invite_row.detail["actor_id"] == admin_user.pk
        assert invite_row.detail["actor_username"] == admin_user.username
        assert invite_row.ip_address == "203.0.113.84"

    def test_smuggled_super_admin_role_is_rejected(self, client, admin_user):
        client.force_login(admin_user)

        response = client.post(
            reverse("core:user_access_create"),
            _create_data(username="escalated", role=User.Role.SUPER_ADMIN),
        )

        assert response.status_code == 200
        assert not User.objects.filter(username="escalated").exists()

    def test_service_repeats_the_privilege_guard(self, facility, admin_user):
        with pytest.raises(PermissionDenied):
            create_invited_user(
                facility=facility,
                actor=admin_user,
                username="service_escalation",
                email="service@example.org",
                display_name="",
                role=User.Role.SUPER_ADMIN,
                mfa_required=False,
                can_confirm_deletion=False,
            )

    def test_service_persists_a_non_default_assignable_role(self, facility, admin_user):
        with patch("core.services.user_access.send_invite_email", return_value=False):
            result = create_invited_user(
                facility=facility,
                actor=admin_user,
                username="service_assistant",
                email="assistant@example.org",
                display_name="Service Assistant",
                role=User.Role.ASSISTANT,
                mfa_required=False,
                can_confirm_deletion=False,
            )

        assert result.user.role == User.Role.ASSISTANT


class TestUpdateUserAccess:
    def test_update_persists_access_fields_and_writes_attributed_audit_rows(self, client, admin_user, staff_user):
        client.force_login(admin_user)

        response = client.post(
            reverse("core:user_access_update", args=[staff_user.pk]),
            _update_data(
                staff_user,
                role=User.Role.ASSISTANT,
                mfa_required="on",
                can_confirm_deletion="on",
            ),
        )

        assert response.status_code == 302
        staff_user.refresh_from_db()
        assert staff_user.role == User.Role.ASSISTANT
        assert staff_user.mfa_required is True
        assert staff_user.can_confirm_deletion is True
        expected = {
            AuditLog.Action.USER_ROLE_CHANGED,
            AuditLog.Action.USER_ACCESS_UPDATED,
            AuditLog.Action.DELETION_CONFIRMER_CHANGED,
        }
        rows = AuditLog.objects.filter(user=staff_user, action__in=expected)
        assert set(rows.values_list("action", flat=True)) == expected
        assert all(row.actor == admin_user for row in rows)
        access_rows = rows.filter(action=AuditLog.Action.USER_ACCESS_UPDATED)
        assert access_rows.count() == 1
        assert access_rows.get().detail["changed_fields"] == ["mfa_required"]

    def test_service_guards_actor_role_facility_and_own_admin_access(
        self, facility, second_facility, admin_user, staff_user
    ):
        with pytest.raises(PermissionDenied):
            update_user_access(
                staff_user,
                facility=facility,
                actor=staff_user,
                **_service_values(staff_user),
            )

        foreign_admin = User.objects.create_user(
            username="foreign_facility_admin",
            role=User.Role.FACILITY_ADMIN,
            facility=second_facility,
        )
        with pytest.raises(PermissionDenied):
            update_user_access(
                staff_user,
                facility=facility,
                actor=foreign_admin,
                **_service_values(staff_user),
            )

        for overrides in (
            {"role": User.Role.STAFF},
            {"is_active": False},
        ):
            with pytest.raises(PermissionDenied):
                update_user_access(
                    admin_user,
                    facility=facility,
                    actor=admin_user,
                    **_service_values(admin_user, **overrides),
                )

    def test_actor_guard_rejects_same_facility_non_admin_directly(self, facility, staff_user):
        with pytest.raises(PermissionDenied):
            _assert_actor(facility, staff_user)

    def test_service_accepts_unchanged_own_admin_access(self, facility, admin_user):
        updated = update_user_access(
            admin_user,
            facility=facility,
            actor=admin_user,
            **_service_values(admin_user),
        )

        assert updated.pk == admin_user.pk

    def test_service_update_pins_fields_and_audit_payload(self, facility, admin_user, staff_user):
        updated = update_user_access(
            staff_user,
            facility=facility,
            actor=admin_user,
            ip_address="203.0.113.85",
            **_service_values(
                staff_user,
                username="mutation_checked",
                email="mutation@example.org",
                display_name="Mutation Check",
                mfa_required=True,
            ),
        )

        assert updated.username == "mutation_checked"
        assert updated.email == "mutation@example.org"
        assert updated.display_name == "Mutation Check"
        assert updated.mfa_required is True
        row = AuditLog.objects.get(action=AuditLog.Action.USER_ACCESS_UPDATED, user=staff_user)
        assert row.facility == facility
        assert row.actor == admin_user
        assert row.target_type == "User"
        assert row.target_id == str(staff_user.pk)
        assert row.detail == {
            "changed_fields": ["display_name", "email", "mfa_required", "username"],
            "username": "mutation_checked",
            "actor_id": admin_user.pk,
            "actor_username": admin_user.username,
        }
        assert row.ip_address == "203.0.113.85"

    def test_service_deactivation_uses_only_the_dedicated_audit_action(self, facility, admin_user, staff_user):
        update_user_access(
            staff_user,
            facility=facility,
            actor=admin_user,
            **_service_values(staff_user, is_active=False),
        )

        assert AuditLog.objects.filter(action=AuditLog.Action.USER_DEACTIVATED, user=staff_user).exists()
        assert not AuditLog.objects.filter(action=AuditLog.Action.USER_ACCESS_UPDATED, user=staff_user).exists()

    def test_service_updates_an_already_inactive_account_without_key_errors(self, facility, admin_user, staff_user):
        User.objects.filter(pk=staff_user.pk).update(is_active=False)
        staff_user.refresh_from_db()

        updated = update_user_access(
            staff_user,
            facility=facility,
            actor=admin_user,
            **_service_values(staff_user, display_name="Inaktiv bearbeitet"),
        )

        assert updated.display_name == "Inaktiv bearbeitet"
        assert AuditLog.objects.get(action=AuditLog.Action.USER_ACCESS_UPDATED, user=staff_user).detail[
            "changed_fields"
        ] == ["display_name"]

    def test_service_reactivation_is_recorded_as_access_update(self, facility, admin_user, staff_user):
        User.objects.filter(pk=staff_user.pk).update(is_active=False)
        staff_user.refresh_from_db()

        update_user_access(
            staff_user,
            facility=facility,
            actor=admin_user,
            **_service_values(staff_user, is_active=True),
        )

        assert AuditLog.objects.get(action=AuditLog.Action.USER_ACCESS_UPDATED, user=staff_user).detail[
            "changed_fields"
        ] == ["is_active"]

    def test_foreign_user_is_404_for_read_update_invite_and_unlock(self, client, admin_user, foreign_staff):
        client.force_login(admin_user)
        urls = (
            reverse("core:user_access_update", args=[foreign_staff.pk]),
            reverse("core:user_access_invite", args=[foreign_staff.pk]),
            reverse("core:user_access_unlock", args=[foreign_staff.pk]),
        )

        assert client.get(urls[0]).status_code == 404
        assert client.post(urls[0], _update_data(foreign_staff)).status_code == 404
        assert client.post(urls[1]).status_code == 404
        assert client.post(urls[2]).status_code == 404

    def test_super_admin_object_is_not_manageable_even_if_assigned_to_facility(
        self, client, facility, admin_user, super_admin_user
    ):
        User.objects.filter(pk=super_admin_user.pk).update(facility=facility)
        client.force_login(admin_user)

        assert client.get(reverse("core:user_access_update", args=[super_admin_user.pk])).status_code == 404


class TestInviteAndUnlockActions:
    def test_pending_invite_can_be_resent_and_is_audited(self, client, facility, admin_user):
        invited = _pending_invite(facility)
        client.force_login(admin_user)

        with patch("core.services.user_access.send_invite_email", return_value=True) as send:
            response = client.post(reverse("core:user_access_invite", args=[invited.pk]))

        assert response.status_code == 302
        assert response.url == reverse("core:user_access_update", args=[invited.pk])
        send.assert_called_once_with(invited)
        row = AuditLog.objects.get(action=AuditLog.Action.USER_INVITE_SENT, user=invited)
        assert row.actor == admin_user
        assert row.detail["trigger"] == "invite_resend"

    def test_established_account_cannot_receive_an_invite(self, client, admin_user, staff_user):
        client.force_login(admin_user)

        with patch("core.services.user_access.send_invite_email") as send:
            response = client.post(reverse("core:user_access_invite", args=[staff_user.pk]))

        assert response.status_code == 302
        send.assert_not_called()
        assert not AuditLog.objects.filter(action=AuditLog.Action.USER_INVITE_SENT, user=staff_user).exists()

    def test_service_rejects_pending_invite_without_email(self, facility, admin_user):
        invited = _pending_invite(facility, username="pending_without_email")
        invited.email = ""
        invited.save(update_fields=["email"])

        with pytest.raises(ValidationError):
            resend_user_invitation(invited, facility=facility, actor=admin_user)

    def test_service_rejects_established_account_even_with_email(self, facility, admin_user, staff_user):
        staff_user.email = "staff@example.org"
        staff_user.must_change_password = False
        staff_user.save(update_fields=["email", "must_change_password"])

        with pytest.raises(ValidationError):
            resend_user_invitation(staff_user, facility=facility, actor=admin_user)

    def test_locked_account_can_be_unlocked(self, client, facility, admin_user, staff_user):
        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"username": staff_user.username},
            )
        client.force_login(admin_user)

        response = client.post(reverse("core:user_access_unlock", args=[staff_user.pk]))

        assert response.status_code == 302
        row = AuditLog.objects.get(action=AuditLog.Action.LOGIN_UNLOCK, user=staff_user)
        assert row.actor == admin_user
        assert row.detail["unlocked_by"] == str(admin_user.pk)

    def test_service_does_not_write_unlock_for_unlocked_account(self, facility, admin_user, staff_user):
        with (
            patch("core.services.user_access.is_locked", return_value=False),
            patch("core.services.user_access.unlock") as unlock,
        ):
            assert unlock_managed_user(staff_user, facility=facility, actor=admin_user) is False

        unlock.assert_not_called()
