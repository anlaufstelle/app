"""Tests for the seed management command."""

import pytest
from django.core.management import call_command

from core.models import (
    Case,
    Client,
    DocumentType,
    Facility,
    FieldTemplate,
    Organization,
    Settings,
    TimeFilter,
    User,
    WorkItem,
)

# ---------------------------------------------------------------------------
# Idempotency (existing tests)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_idempotent_no_duplicate_field_templates():
    """Running seed twice does not create duplicate FieldTemplates."""
    call_command("seed")
    count_after_first = FieldTemplate.objects.count()

    call_command("seed")
    count_after_second = FieldTemplate.objects.count()

    assert count_after_second == count_after_first


@pytest.mark.django_db
def test_seed_updates_field_template_properties():
    """Re-running seed updates FieldTemplate properties to match the seed definition."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    ft = FieldTemplate.objects.get(facility=facility, name="Ausgabe")

    # Verify initial state from seed
    assert ft.is_required is True

    # Manually change a property
    ft.is_required = False
    ft.save()
    ft.refresh_from_db()
    assert ft.is_required is False

    # Re-run seed — property should be restored
    call_command("seed")
    ft.refresh_from_db()
    assert ft.is_required is True


# ---------------------------------------------------------------------------
# Scale: small (default)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_small_creates_one_facility():
    """Default (small) scale creates exactly one facility named 'Hauptstelle'."""
    call_command("seed")

    assert Facility.objects.count() == 1
    assert Facility.objects.filter(name="Hauptstelle").exists()


@pytest.mark.django_db
def test_seed_small_creates_expected_clients():
    """Small scale creates 7 clients with the hard-coded pseudonyms."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    clients = Client.objects.filter(facility=facility)
    assert clients.count() == 7

    expected_pseudonyms = {"Stern-42", "Wolke-17", "Blitz-08", "Regen-55", "Wind-33", "Nebel-71", "Sonne-99"}
    actual_pseudonyms = set(clients.values_list("pseudonym", flat=True))
    assert actual_pseudonyms == expected_pseudonyms


@pytest.mark.django_db
def test_seed_small_creates_four_users():
    """Small scale creates 4 users (admin, lead, staff, assistant)."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    users = User.objects.filter(facility=facility)
    assert users.count() == 4

    roles = set(users.values_list("role", flat=True))
    assert roles == {User.Role.ADMIN, User.Role.LEAD, User.Role.STAFF, User.Role.ASSISTANT}


@pytest.mark.django_db
def test_seed_small_creates_settings_and_time_filters():
    """Small scale creates Settings and TimeFilters for the facility."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    assert Settings.objects.filter(facility=facility).exists()
    assert TimeFilter.objects.filter(facility=facility).count() == 3


@pytest.mark.django_db
def test_seed_small_creates_document_types():
    """Small scale creates at least one DocumentType per facility."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    assert DocumentType.objects.filter(facility=facility).count() > 0


@pytest.mark.django_db
def test_seed_small_creates_cases_and_work_items():
    """Small scale creates cases and work items as per config."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    assert Case.objects.filter(facility=facility).count() == 3
    assert WorkItem.objects.filter(facility=facility).count() == 5


# ---------------------------------------------------------------------------
# Scale: solo
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_solo_creates_one_facility():
    """Solo scale creates exactly one facility."""
    call_command("seed", scale="solo")

    assert Facility.objects.count() == 1


@pytest.mark.django_db
def test_seed_solo_creates_bulk_clients():
    """Solo scale uses bulk client creation with 30 clients."""
    call_command("seed", scale="solo")

    facility = Facility.objects.get(name="Hauptstelle")
    assert Client.objects.filter(facility=facility).count() == 30


# ---------------------------------------------------------------------------
# Scale: medium
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_medium_creates_two_facilities():
    """Medium scale creates two facilities."""
    call_command("seed", scale="medium")

    assert Facility.objects.count() == 2
    assert Facility.objects.filter(name="Hauptstelle").exists()
    assert Facility.objects.filter(name="Zweigstelle Nord").exists()


@pytest.mark.django_db
def test_seed_medium_creates_users_per_facility():
    """Medium scale creates 4 users per facility (8 total for 2 facilities)."""
    call_command("seed", scale="medium")

    assert User.objects.count() == 8


@pytest.mark.django_db
def test_seed_medium_second_facility_users_have_suffix():
    """Users in the second facility have a '_1' suffix to avoid name collision."""
    call_command("seed", scale="medium")

    assert User.objects.filter(username="admin").exists()
    assert User.objects.filter(username="admin_1").exists()


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_creates_single_organization():
    """Seed always creates exactly one organization named 'Anlaufstelle'."""
    call_command("seed")

    assert Organization.objects.count() == 1
    assert Organization.objects.filter(name="Anlaufstelle").exists()


@pytest.mark.django_db
def test_seed_medium_shares_single_organization():
    """Medium scale: all facilities belong to the same organization."""
    call_command("seed", scale="medium")

    org = Organization.objects.get(name="Anlaufstelle")
    assert Facility.objects.filter(organization=org).count() == 2


# ---------------------------------------------------------------------------
# Idempotency across scales
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_idempotent_user_count():
    """Running seed twice produces the same number of users."""
    call_command("seed")
    count_first = User.objects.count()

    call_command("seed")
    count_second = User.objects.count()

    assert count_second == count_first


@pytest.mark.django_db
def test_seed_idempotent_client_count():
    """Running seed twice produces the same number of clients."""
    call_command("seed")
    count_first = Client.objects.count()

    call_command("seed")
    count_second = Client.objects.count()

    assert count_second == count_first


@pytest.mark.django_db
def test_seed_idempotent_facility_count():
    """Running seed twice produces the same number of facilities."""
    call_command("seed")
    count_first = Facility.objects.count()

    call_command("seed")
    count_second = Facility.objects.count()

    assert count_second == count_first


# ---------------------------------------------------------------------------
# Flush option
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_flush_recreates_data():
    """--flush deletes existing data and recreates from scratch."""
    call_command("seed")
    first_user_ids = set(User.objects.values_list("id", flat=True))

    call_command("seed", flush=True)
    second_user_ids = set(User.objects.values_list("id", flat=True))

    # After flush+recreate, IDs differ (new objects)
    assert first_user_ids != second_user_ids
    # But counts are the same
    assert User.objects.count() == 4


# ---------------------------------------------------------------------------
# Invalid scale (argparse catches this)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_invalid_scale_raises():
    """Passing an invalid scale value raises a KeyError (not in SCALE_CONFIG)."""
    with pytest.raises(KeyError):
        call_command("seed", scale="nonexistent")


# ---------------------------------------------------------------------------
# Admin user properties
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_admin_has_superuser_flag():
    """The seeded admin user has is_superuser=True."""
    call_command("seed")

    admin = User.objects.get(username="admin")
    assert admin.is_superuser is True
    assert admin.is_staff is True
    assert admin.role == User.Role.ADMIN


@pytest.mark.django_db
def test_seed_non_admin_users_are_not_superusers():
    """Non-admin seeded users do not have is_superuser."""
    call_command("seed")

    for user in User.objects.exclude(role=User.Role.ADMIN):
        assert user.is_superuser is False
