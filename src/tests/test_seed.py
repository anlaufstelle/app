"""Tests for the seed management command."""

import pytest
from django.core.management import call_command

from core.models import Facility, FieldTemplate


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
