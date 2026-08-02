"""Regressionstests für den Slug-Immutable-Trigger auf FieldGroup/FieldTemplate
(Refs #1058, #1709).

Migration ``0116_slug_immutable_triggers.py`` installiert je einen Postgres-
Trigger ``BEFORE UPDATE ON core_fieldgroup``/``core_fieldtemplate``, der jede
Änderung des ``slug``-Werts mit einem ``RAISE EXCEPTION`` blockt (M2-Paket-
format-Identifier — muss über die Feldvorlage/-gruppe hinweg stabil bleiben,
da er als Key referenziert wird). Die Modelle blocken Slug-Änderungen bereits
Python-seitig in ``save()`` (siehe ``document_type.py``) — dieser Trigger ist
die DB-seitige Verteidigungslinie gegen ORM-Bypasses (``QuerySet.update()``,
``bulk_update()``, Raw SQL, Admin-Bulk-Actions, künftige Migrationen).

Analog ``test_audit_trigger.py`` (Refs #733).
"""

import pytest
from django.db import DatabaseError, connection, transaction

from core.models import FieldGroup, FieldTemplate


@pytest.mark.django_db(transaction=True)
class TestFieldGroupSlugImmutableTrigger:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Slug-Immutable-Trigger existiert nur auf PostgreSQL")

    def _create_group(self, doc_type_contact, **kwargs):
        return FieldGroup.objects.create(document_type=doc_type_contact, name="Gruppe", **kwargs)

    def test_raw_update_of_slug_is_blocked(self, doc_type_contact):
        group = self._create_group(doc_type_contact)
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE core_fieldgroup SET slug = %s WHERE id = %s",
                ["anderer-slug", str(group.pk)],
            )
        assert "immutable" in str(excinfo.value).lower(), (
            f"Erwartet 'immutable' im Trigger-Fehler, erhalten: {excinfo.value!r}"
        )

    def test_queryset_update_of_slug_is_blocked(self, doc_type_contact):
        group = self._create_group(doc_type_contact)
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic():
            FieldGroup.objects.filter(pk=group.pk).update(slug="anderer-slug")
        assert "immutable" in str(excinfo.value).lower()

    def test_bulk_update_of_slug_is_blocked(self, doc_type_contact):
        group = self._create_group(doc_type_contact)
        group.slug = "anderer-slug"
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic():
            FieldGroup.objects.bulk_update([group], ["slug"])
        assert "immutable" in str(excinfo.value).lower()

    def test_update_of_name_and_sort_order_is_allowed(self, doc_type_contact):
        group = self._create_group(doc_type_contact, sort_order=0)
        FieldGroup.objects.filter(pk=group.pk).update(name="Neuer Name", sort_order=3)
        group.refresh_from_db()
        assert group.name == "Neuer Name"
        assert group.sort_order == 3

    def test_insert_is_allowed(self, doc_type_contact):
        before = FieldGroup.objects.filter(document_type=doc_type_contact).count()
        self._create_group(doc_type_contact)
        after = FieldGroup.objects.filter(document_type=doc_type_contact).count()
        assert after == before + 1

    def test_raw_update_with_identical_slug_is_allowed(self, doc_type_contact):
        # Seed-Pfad (``update_or_create(document_type=…, slug=…)``): eine
        # UPDATE-Anweisung, die ``slug`` auf ihren BISHERIGEN Wert setzt, darf
        # nicht am Trigger scheitern — ``IS DISTINCT FROM`` statt ``!=``
        # (letzteres wäre bei NULL ohnehin falsch, hier aber vor allem: die
        # Werte SIND gleich, keine Aenderung liegt vor).
        group = self._create_group(doc_type_contact)
        original_slug = group.slug
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE core_fieldgroup SET slug = %s WHERE id = %s",
                [group.slug, str(group.pk)],
            )
        group.refresh_from_db()
        assert group.slug == original_slug

    def test_update_or_create_with_identical_slug_is_allowed(self, doc_type_contact):
        # Direkte Seed-Regression: ``update_or_create`` mit unveraendertem
        # Slug im Lookup darf nicht am Trigger scheitern.
        group = self._create_group(doc_type_contact, sort_order=0)
        updated, created = FieldGroup.objects.update_or_create(
            document_type=doc_type_contact,
            slug=group.slug,
            defaults={"name": "Umbenannt", "sort_order": 1},
        )
        assert not created
        assert updated.pk == group.pk
        assert updated.name == "Umbenannt"


@pytest.mark.django_db(transaction=True)
class TestFieldTemplateSlugImmutableTrigger:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Slug-Immutable-Trigger existiert nur auf PostgreSQL")

    def _create_template(self, facility, **kwargs):
        return FieldTemplate.objects.create(facility=facility, name="Vorlage", **kwargs)

    def test_raw_update_of_slug_is_blocked(self, facility):
        template = self._create_template(facility)
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE core_fieldtemplate SET slug = %s WHERE id = %s",
                ["anderer-slug", str(template.pk)],
            )
        assert "immutable" in str(excinfo.value).lower(), (
            f"Erwartet 'immutable' im Trigger-Fehler, erhalten: {excinfo.value!r}"
        )

    def test_queryset_update_of_slug_is_blocked(self, facility):
        template = self._create_template(facility)
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic():
            FieldTemplate.objects.filter(pk=template.pk).update(slug="anderer-slug")
        assert "immutable" in str(excinfo.value).lower()

    def test_bulk_update_of_slug_is_blocked(self, facility):
        template = self._create_template(facility)
        template.slug = "anderer-slug"
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic():
            FieldTemplate.objects.bulk_update([template], ["slug"])
        assert "immutable" in str(excinfo.value).lower()

    def test_update_of_name_is_allowed(self, facility):
        template = self._create_template(facility)
        FieldTemplate.objects.filter(pk=template.pk).update(name="Neuer Name")
        template.refresh_from_db()
        assert template.name == "Neuer Name"

    def test_insert_is_allowed(self, facility):
        before = FieldTemplate.objects.filter(facility=facility).count()
        self._create_template(facility)
        after = FieldTemplate.objects.filter(facility=facility).count()
        assert after == before + 1

    def test_raw_update_with_identical_slug_is_allowed(self, facility):
        template = self._create_template(facility)
        original_slug = template.slug
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE core_fieldtemplate SET slug = %s WHERE id = %s",
                [template.slug, str(template.pk)],
            )
        template.refresh_from_db()
        assert template.slug == original_slug

    def test_update_or_create_with_identical_slug_is_allowed(self, facility):
        template = self._create_template(facility)
        updated, created = FieldTemplate.objects.update_or_create(
            facility=facility,
            slug=template.slug,
            defaults={"name": "Umbenannt"},
        )
        assert not created
        assert updated.pk == template.pk
        assert updated.name == "Umbenannt"
