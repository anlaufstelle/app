"""Approvers for deletion requests: first admin, compliance card, defaults when inviting.

A deletion request needs a second person with ``can_confirm_deletion``; nobody can approve
their own request. A facility with no approver cannot complete any request, one with a single
approver cannot complete that person's own requests.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from core.forms.user_access import UserAccessCreateForm
from core.models import AuditLog, Facility, User
from core.services import compliance
from core.services.compliance import ComplianceStatus

pytestmark = pytest.mark.django_db


def _approver(facility, username, *, is_active=True, can_confirm_deletion=True, role=User.Role.LEAD):
    return User.objects.create_user(
        username=username,
        role=role,
        facility=facility,
        is_active=is_active,
        can_confirm_deletion=can_confirm_deletion,
    )


class TestSetupFacilityGrantsTheRight:
    def test_created_admin_can_confirm_deletions(self):
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "firstadmin"]),
            patch("getpass.getpass", side_effect=["secret123", "secret123"]),
        ):
            call_command("setup_facility")

        user = User.objects.get(username="firstadmin")
        assert user.role == User.Role.FACILITY_ADMIN
        assert user.can_confirm_deletion is True


class TestComplianceApproverCard:
    def _card(self):
        checks = [check for check in compliance.aggregate_checks() if check.key == "deletion_approvers"]
        assert len(checks) == 1
        return checks[0]

    def test_no_approver_is_critical_and_names_the_facility(self, facility):
        _approver(facility, "inactive", is_active=False)
        _approver(facility, "without_right", can_confirm_deletion=False)

        card = self._card()

        assert card.status == ComplianceStatus.CRITICAL
        assert facility.name in card.message

    def test_single_approver_warns_that_own_requests_stay_open(self, facility):
        _approver(facility, "only_one")

        card = self._card()

        assert card.status == ComplianceStatus.WARNING
        assert "eigene Anträge" in card.message
        assert facility.name in card.message

    def test_two_approvers_are_ok(self, facility):
        _approver(facility, "first")
        _approver(facility, "second", role=User.Role.ASSISTANT)

        assert self._card().status == ComplianceStatus.OK

    def test_worst_facility_decides_and_each_facility_is_counted(self, facility, organization):
        other = Facility.objects.create(organization=organization, name="Zweite Stelle")
        _approver(facility, "first")
        _approver(facility, "second")
        _approver(other, "alone")

        card = self._card()

        assert card.status == ComplianceStatus.WARNING
        assert "Zweite Stelle" in card.message
        assert f"{facility.name}: 2" in card.detail
        assert "Zweite Stelle: 1" in card.detail

    @override_settings(DEMO_MODE=True)
    def test_demo_is_evaluated_like_any_installation(self, facility):
        _approver(facility, "first")
        _approver(facility, "second")

        assert self._card().status == ComplianceStatus.OK

    def test_dashboard_shows_the_card(self, client, super_admin_user, facility):
        client.force_login(super_admin_user)

        content = client.get(reverse("core:system_compliance")).content.decode()

        assert "Genehmiger für Löschanträge" in content

    def test_label_and_category_are_translated(self, facility):
        with patch("core.services.compliance.deletion_approvers._", side_effect=lambda text: f"[{text}]"):
            card = self._card()

        assert card.label == "[Genehmiger für Löschanträge]"
        assert card.category == "[Berechtigungen]"

    def test_dashboard_groups_the_card_under_the_translated_category(self, client, super_admin_user, facility):
        client.force_login(super_admin_user)

        with patch("core.services.compliance.deletion_approvers._", side_effect=lambda text: f"[{text}]"):
            response = client.get(reverse("core:system_compliance"))

        group = response.context["groups"]["[Berechtigungen]"]
        assert [check.key for check in group] == ["deletion_approvers"]
        assert "[Genehmiger für Löschanträge]" in response.content.decode()


def _create_form(facility, actor, **overrides):
    data = {
        "username": "invited",
        "email": "invited@example.org",
        "display_name": "",
        "role": User.Role.STAFF,
        "mfa_required": "",
    }
    data.update(overrides)
    return UserAccessCreateForm(data, facility=facility, actor=actor)


class TestInviteDefaultsTheRightByRole:
    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (User.Role.FACILITY_ADMIN, True),
            (User.Role.LEAD, True),
            (User.Role.STAFF, False),
            (User.Role.ASSISTANT, False),
        ],
    )
    def test_default_follows_the_role(self, facility, admin_user, role, expected):
        form = _create_form(facility, admin_user, role=role, can_confirm_deletion="role")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["can_confirm_deletion"] is expected

    def test_default_can_be_deselected_for_a_lead(self, facility, admin_user):
        form = _create_form(facility, admin_user, role=User.Role.LEAD, can_confirm_deletion="no")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["can_confirm_deletion"] is False

    def test_right_can_be_granted_to_other_roles(self, facility, admin_user):
        form = _create_form(facility, admin_user, role=User.Role.ASSISTANT, can_confirm_deletion="yes")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["can_confirm_deletion"] is True

    def test_unrendered_field_falls_back_to_the_role(self, facility, admin_user):
        form = _create_form(facility, admin_user, role=User.Role.LEAD)

        assert form.is_valid(), form.errors
        assert form.cleaned_data["can_confirm_deletion"] is True

    def test_create_page_preselects_the_role_default(self, client, admin_user):
        client.force_login(admin_user)

        content = client.get(reverse("core:user_access_create")).content.decode()

        assert 'value="role" selected' in content

    def test_created_lead_carries_the_right(self, client, admin_user, facility):
        client.force_login(admin_user)
        data = {
            "username": "newlead",
            "email": "newlead@example.org",
            "display_name": "",
            "role": User.Role.LEAD,
            "can_confirm_deletion": "role",
        }

        response = client.post(reverse("core:user_access_create"), data)

        assert response.status_code == 302
        assert User.objects.get(username="newlead").can_confirm_deletion is True


class TestDjangoAdminAddDefaultsTheRightByRole:
    def _form_class(self, rf, super_admin_user):
        from core.admin.users import UserAdmin
        from core.admin_site import anlaufstelle_admin_site

        request = rf.get("/admin-mgmt/core/user/add/")
        request.user = super_admin_user
        return UserAdmin(User, anlaufstelle_admin_site).get_form(request, None)

    @pytest.mark.parametrize(("role", "expected"), [(User.Role.LEAD, True), (User.Role.STAFF, False)])
    def test_default_follows_the_role(self, rf, super_admin_user, facility, role, expected):
        form_class = self._form_class(rf, super_admin_user)
        form = form_class(
            {
                "username": "adminadded",
                "email": "adminadded@example.org",
                "role": role,
                "facility": str(facility.pk),
                "display_name": "",
                "can_confirm_deletion": "role",
            }
        )

        assert form.is_valid(), form.errors
        assert form.cleaned_data["can_confirm_deletion"] is expected


def _creation_entries(user):
    return list(
        AuditLog.objects.filter(action=AuditLog.Action.USER_CREATED, target_type="User", target_id=str(user.pk))
    )


class TestInitialGrantIsAudited:
    """The creation entry records the initial value of the right, so every grant is traceable."""

    @pytest.mark.parametrize(
        ("role", "choice", "expected"),
        [(User.Role.LEAD, "role", True), (User.Role.STAFF, "role", False), (User.Role.LEAD, "no", False)],
    )
    def test_invite_records_the_initial_value(self, client, admin_user, facility, role, choice, expected):
        client.force_login(admin_user)
        data = {
            "username": "audited",
            "email": "audited@example.org",
            "display_name": "",
            "role": role,
            "can_confirm_deletion": choice,
        }

        response = client.post(reverse("core:user_access_create"), data)

        assert response.status_code == 302
        user = User.objects.get(username="audited")
        assert user.can_confirm_deletion is expected
        (entry,) = _creation_entries(user)
        assert entry.detail["can_confirm_deletion"] is expected
        assert entry.actor == admin_user

    def test_setup_facility_records_the_grant_without_an_actor(self):
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "firstadmin"]),
            patch("getpass.getpass", side_effect=["secret123", "secret123"]),
        ):
            call_command("setup_facility")

        user = User.objects.get(username="firstadmin")
        (entry,) = _creation_entries(user)
        assert entry.actor is None
        assert entry.facility == user.facility
        assert entry.detail["can_confirm_deletion"] is True
        assert entry.detail["system"] is True
        assert entry.detail["command"] == "setup_facility"
        assert entry.detail["new_role"] == User.Role.FACILITY_ADMIN

    def test_setup_facility_for_an_existing_account_writes_no_creation_entry(self, facility):
        existing = User.objects.create(username="firstadmin", facility=facility, role=User.Role.LEAD)

        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "firstadmin"]),
            patch("getpass.getpass", side_effect=["secret123", "secret123"]),
        ):
            call_command("setup_facility")

        assert _creation_entries(existing) == []

    def test_creation_without_an_actor_stays_unaudited(self, facility):
        user = User.objects.create(username="seeded", facility=facility, role=User.Role.LEAD, can_confirm_deletion=True)

        assert _creation_entries(user) == []
