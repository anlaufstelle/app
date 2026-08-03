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
class TestSlugTruncation:
    """Fix-Runde 1 (Refs #1058, B-3): der modulweite ``generate_unique_slug``-
    Helfer (extrahiert aus dem vormaligen ``FieldTemplate._generate_unique_slug``
    im Rahmen von M2/DocumentType.slug) truncatiert jetzt auf 120 Zeichen VOR
    dem Counter-Suffix. Vorher hatte ``_generate_unique_slug`` KEINE
    Truncation — ein Name, dessen ``slugify()``-Ergebnis > 120 Zeichen lang
    ist, ließ ``FieldTemplate.save()`` mit einem ``DataError`` am
    DB-Spaltenlimit (``SlugField(max_length=120)``) crashen (Zeile passte
    nicht in die Spalte). Das ist eine bewusste Verhaltens-Heilung, nicht nur
    eine Extraktion — hier explizit gepinnt."""

    def test_long_name_slug_truncated_to_120(self, facility):
        # name-Feld selbst ist auf 200 Zeichen begrenzt (CharField(max_length=200))
        # — 150 reicht, um die 120er-Slug-Truncation zu ueberschreiten.
        long_name = "A" * 150
        ft = FieldTemplate.objects.create(facility=facility, name=long_name, field_type="text")
        assert len(ft.slug) == 120
        assert ft.slug == "a" * 120

    def test_long_name_collision_suffix_stays_within_120(self, facility):
        # Zwei verschiedene Rohnamen (Unique-Constraint auf (facility, name)
        # bleibt erfuellt — die Namen unterscheiden sich an Position 150,
        # WEIT hinter der 120er-Truncation-Grenze), aber identisches
        # slugify()-Ergebnis NACH Truncation -> Kollision, Suffix darf die
        # 120 Zeichen nicht sprengen.
        name1 = "A" * 150
        name2 = "A" * 149 + "Z"
        ft1 = FieldTemplate.objects.create(facility=facility, name=name1, field_type="text")
        ft2 = FieldTemplate.objects.create(facility=facility, name=name2, field_type="number")
        assert len(ft2.slug) <= 120
        assert ft2.slug != ft1.slug
        assert ft2.slug.endswith("-2")


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
    def _make_event(self, facility, doc_type_contact, staff_user):
        return Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at="2026-01-01T12:00:00Z",
            data_json={"unknown-old-key": "some value", "dauer": 15},
            is_anonymous=True,
            created_by=staff_user,
        )

    def test_unknown_key_does_not_crash_detail_view(self, client, staff_user, facility, doc_type_contact):
        """Event mit unbekanntem Key crasht die Detail-Ansicht nicht.

        Seit #1725 gilt fuer Waisen-Keys OHNE zugehoerige FieldTemplate der
        konservative HIGH-Fallback: staff sieht den Wert nicht mehr im
        Klartext, sondern restringiert.
        """
        event = self._make_event(facility, doc_type_contact, staff_user)
        client.force_login(staff_user)
        from django.urls import reverse

        response = client.get(reverse("core:event_detail", args=[event.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "some value" not in content
        assert "[Eingeschränkt]" in content

    def test_unknown_key_visible_for_lead(self, client, lead_user, staff_user, facility, doc_type_contact):
        """HIGH-Fallback (#1725): Leitung darf den Waisen-Wert weiterhin sehen."""
        event = self._make_event(facility, doc_type_contact, staff_user)
        client.force_login(lead_user)
        from django.urls import reverse

        response = client.get(reverse("core:event_detail", args=[event.pk]))
        assert response.status_code == 200
        assert "some value" in response.content.decode()
