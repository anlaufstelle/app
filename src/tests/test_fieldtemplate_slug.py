"""Tests für FieldTemplate.slug — Generierung, Immutabilität, Validierung."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from core.models import Event, FieldTemplate


@pytest.mark.django_db
class TestSlugGeneration:
    def test_auto_slug_from_name(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Dauer", field_type="number")
        assert ft.slug == "dauer"

    def test_umlaut_slug(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Krisengespräch", field_type="text")
        assert ft.slug == "krisengespraech"

    def test_slug_with_spaces_and_parens(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Notiz (Krise)", field_type="textarea")
        assert ft.slug == "notiz-krise"

    def test_slug_strasse(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Straßenkontakt", field_type="boolean")
        assert ft.slug == "strassenkontakt"

    def test_empty_slug_raises(self, facility):
        with pytest.raises(ValidationError, match="gültigen Slug"):
            FieldTemplate.objects.create(facility=facility, name="!!!", field_type="text")


@pytest.mark.django_db
class TestSlugImmutability:
    def test_slug_immutable_after_creation(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Dauer", field_type="number")
        ft.slug = "changed"
        with pytest.raises(ValidationError, match="nach Erstellung nicht geändert"):
            ft.save()

    def test_same_slug_no_error(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Dauer", field_type="number")
        ft.slug = "dauer"
        ft.save()  # No error


@pytest.mark.django_db
class TestSlugCollision:
    def test_different_names_same_slug_gets_suffix(self, facility):
        """Zwei verschiedene Namen, die denselben Slug erzeugen → Counter-Suffix."""
        ft1 = FieldTemplate.objects.create(facility=facility, name="Test Feld", field_type="text")
        ft2 = FieldTemplate.objects.create(facility=facility, name="Test-Feld", field_type="text")
        assert ft1.slug == "test-feld"
        assert ft2.slug == "test-feld-2"

    def test_cross_facility_same_slug_allowed(self, facility, other_facility):
        ft1 = FieldTemplate.objects.create(facility=facility, name="Dauer", field_type="number")
        ft2 = FieldTemplate.objects.create(facility=other_facility, name="Dauer", field_type="number")
        assert ft1.slug == ft2.slug == "dauer"


@pytest.mark.django_db
class TestSlugRetryOnRaceCondition:
    """Race-Condition: IntegrityError bei paralleler Slug-Vergabe → Retry."""

    def test_retries_on_integrity_error(self, facility):
        """Bei IntegrityError wird der Slug neu generiert und erneut gespeichert."""
        from django.db.models import Model

        original_save = Model.save
        call_count = 0

        def fake_save(self_inner, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise IntegrityError("unique_facility_fieldtemplate_slug")
            return original_save(self_inner, *args, **kwargs)

        with patch.object(Model, "save", fake_save):
            ft = FieldTemplate(facility=facility, name="Retry Test", field_type="text")
            ft.save()

        assert ft.slug == "retry-test"
        assert ft.pk is not None
        assert call_count == 2

    def test_raises_after_max_retries(self, facility):
        """Nach Ausschöpfung aller Versuche wird IntegrityError weitergereicht."""
        from django.db.models import Model

        def always_fail(self_inner, *args, **kwargs):
            raise IntegrityError("unique_facility_fieldtemplate_slug")

        with patch.object(Model, "save", always_fail):
            ft = FieldTemplate(facility=facility, name="Always Fail", field_type="text")
            with pytest.raises(IntegrityError):
                ft.save()

    def test_no_retry_on_update(self, facility):
        """Update-Pfad hat keine Retry-Logik — IntegrityError wird direkt geworfen."""
        from django.db.models import Model

        ft = FieldTemplate.objects.create(facility=facility, name="Existing", field_type="text")

        def fail_save(self_inner, *args, **kwargs):
            raise IntegrityError("some constraint")

        with patch.object(Model, "save", fail_save), pytest.raises(IntegrityError):
            ft.name = "Updated"
            ft.save()


@pytest.mark.django_db
class TestExplicitSlug:
    def test_explicit_valid_slug_accepted(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Foo", slug="custom-slug", field_type="text")
        assert ft.slug == "custom-slug"

    def test_explicit_invalid_slug_rejected(self, facility):
        with pytest.raises(ValidationError, match="nicht gültig"):
            FieldTemplate.objects.create(facility=facility, name="Foo", slug="Invalid Slug!", field_type="text")

    def test_explicit_duplicate_slug_rejected(self, facility):
        FieldTemplate.objects.create(facility=facility, name="Foo", slug="same-slug", field_type="text")
        with pytest.raises(IntegrityError):
            FieldTemplate.objects.create(facility=facility, name="Bar", slug="same-slug", field_type="text")


@pytest.mark.django_db
class TestUnknownKeyFallback:
    def test_unknown_key_does_not_crash_detail_view(self, client, staff_user, facility, doc_type_contact):
        """Event with unknown key in data_json should not crash the detail view."""
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at="2026-01-01T12:00:00Z",
            data_json={"unknown-old-key": "some value", "dauer": 15},
            is_anonymous=True,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        from django.urls import reverse

        response = client.get(reverse("core:event_detail", args=[event.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "some value" in content
