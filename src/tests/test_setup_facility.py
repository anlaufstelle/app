"""Tests for the setup_facility management command."""

from unittest.mock import patch

import pytest
from django.core.management import call_command

from core.models import Facility, Organization, Settings, User


@pytest.mark.django_db
class TestSetupFacilitySuccess:
    """Happy-path tests for interactive facility setup."""

    def _run_with_inputs(self, inputs, passwords):
        """Helper: call setup_facility with mocked input() and getpass()."""
        with (
            patch("builtins.input", side_effect=inputs),
            patch("getpass.getpass", side_effect=passwords),
        ):
            call_command("setup_facility")

    def test_creates_org_facility_and_admin(self):
        """Successful run creates Organization, Facility, Settings, and admin User."""
        self._run_with_inputs(
            inputs=["Testorg", "Teststelle", "myadmin"],
            passwords=["secret123", "secret123"],
        )

        assert Organization.objects.filter(name="Testorg").exists()
        assert Facility.objects.filter(name="Teststelle").exists()

        facility = Facility.objects.get(name="Teststelle")
        assert facility.organization.name == "Testorg"

        assert Settings.objects.filter(facility=facility).exists()
        settings = Settings.objects.get(facility=facility)
        assert settings.facility_full_name == "Testorg – Teststelle"

        user = User.objects.get(username="myadmin")
        assert user.role == User.Role.FACILITY_ADMIN
        assert user.is_superuser is True
        assert user.is_staff is True
        assert user.facility == facility
        assert user.check_password("secret123")

    def test_default_username_is_admin(self):
        """Pressing Enter at the username prompt defaults to 'admin'."""
        self._run_with_inputs(
            inputs=["Org", "Stelle", ""],  # empty -> default "admin"
            passwords=["pw1234", "pw1234"],
        )

        assert User.objects.filter(username="admin").exists()

    def test_get_or_create_is_idempotent_for_org_and_facility(self):
        """Running setup twice with the same org/facility names reuses existing records."""
        Organization.objects.create(name="Existing Org")
        self._run_with_inputs(
            inputs=["Existing Org", "New Facility", "admin1"],
            passwords=["pass1234", "pass1234"],
        )

        assert Organization.objects.filter(name="Existing Org").count() == 1
        assert Facility.objects.filter(name="New Facility").count() == 1

    def test_display_name_is_titlecased_username(self):
        """The admin's display_name is the titlecased version of the username."""
        self._run_with_inputs(
            inputs=["Org", "Stelle", "johndoe"],
            passwords=["secret", "secret"],
        )

        user = User.objects.get(username="johndoe")
        assert user.display_name == "Johndoe"


@pytest.mark.django_db
class TestSetupFacilityValidation:
    """Validation / error-path tests."""

    def test_empty_org_name_aborts(self):
        """Empty organization name aborts without creating anything."""
        with (
            patch("builtins.input", side_effect=["", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["pw", "pw"]),
        ):
            call_command("setup_facility")

        assert Organization.objects.count() == 0
        assert Facility.objects.count() == 0
        assert User.objects.count() == 0

    def test_empty_facility_name_aborts(self):
        """Empty facility name aborts after org prompt but before creating records."""
        with (
            patch("builtins.input", side_effect=["Org", "", "admin"]),
            patch("getpass.getpass", side_effect=["pw", "pw"]),
        ):
            call_command("setup_facility")

        assert Organization.objects.count() == 0
        assert Facility.objects.count() == 0

    def test_empty_password_aborts(self):
        """Empty password aborts before creating anything."""
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["", ""]),
        ):
            call_command("setup_facility")

        # Password check happens before get_or_create — nothing is created
        assert not Organization.objects.filter(name="Org").exists()
        assert not Facility.objects.filter(name="Stelle").exists()
        assert User.objects.count() == 0

    def test_password_mismatch_retries(self):
        """Mismatched passwords trigger a retry; matching on second attempt succeeds."""
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
            patch(
                "getpass.getpass",
                side_effect=[
                    "first_pw",
                    "different_pw",  # first attempt: mismatch
                    "correct_pw",
                    "correct_pw",  # second attempt: match
                ],
            ),
        ):
            call_command("setup_facility")

        user = User.objects.get(username="admin")
        assert user.check_password("correct_pw")

    def test_existing_username_warns_and_skips_creation(self):
        """If the username already exists, setup warns but still completes."""
        # Pre-create org, facility, and user
        org = Organization.objects.create(name="Org")
        facility = Facility.objects.create(organization=org, name="Stelle")
        User.objects.create_user(
            username="admin",
            role=User.Role.STAFF,
            facility=facility,
        )

        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["newpass", "newpass"]),
        ):
            call_command("setup_facility")

        # User was not recreated, role unchanged
        user = User.objects.get(username="admin")
        assert user.role == User.Role.STAFF
        # Password was NOT updated (get_or_create returned existing)
        assert not user.check_password("newpass")

    def test_whitespace_only_org_name_treated_as_empty(self):
        """Whitespace-only org name is stripped and treated as empty."""
        with (
            patch("builtins.input", side_effect=["   ", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["pw", "pw"]),
        ):
            call_command("setup_facility")

        assert Organization.objects.count() == 0
