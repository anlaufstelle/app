"""Tests für FieldGroup (M0, Refs #1056).

Deckt Slug-Autogenerierung (inkl. Umlaute), Slug-Zähler je Dokumentationstyp,
Slug-Immutabilität, Name-Unique je Dokumentationstyp, Ordering, den
``SET_NULL``-Rückweg auf ``DocumentTypeField.field_group`` sowie den
``clean()``-Guard gegen fremde Dokumentationstypen ab.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core.models import DocumentTypeField, FieldGroup, FieldTemplate


@pytest.mark.django_db
class TestFieldGroupSlugGeneration:
    def test_auto_slug_from_name(self, doc_type_contact):
        fg = FieldGroup.objects.create(document_type=doc_type_contact, name="Stammdaten")
        assert fg.slug == "stammdaten"

    def test_umlaut_slug(self, doc_type_contact):
        fg = FieldGroup.objects.create(document_type=doc_type_contact, name="Persönliche Angaben")
        assert fg.slug == "persoenliche-angaben"

    def test_ampersand_and_spaces_slug(self, doc_type_contact):
        fg = FieldGroup.objects.create(document_type=doc_type_contact, name="Leistungen & Notizen")
        assert fg.slug == "leistungen-notizen"

    def test_strasse_slug(self, doc_type_contact):
        fg = FieldGroup.objects.create(document_type=doc_type_contact, name="Straßenkontakt")
        assert fg.slug == "strassenkontakt"

    def test_empty_slug_raises(self, doc_type_contact):
        with pytest.raises(ValidationError, match="gültigen Slug"):
            FieldGroup.objects.create(document_type=doc_type_contact, name="!!!")


@pytest.mark.django_db
class TestFieldGroupSlugCollision:
    def test_different_names_same_slug_gets_suffix(self, doc_type_contact):
        """Zwei verschiedene Namen, die denselben Slug erzeugen → Counter-Suffix."""
        fg1 = FieldGroup.objects.create(document_type=doc_type_contact, name="Test Gruppe")
        fg2 = FieldGroup.objects.create(document_type=doc_type_contact, name="Test-Gruppe")
        assert fg1.slug == "test-gruppe"
        assert fg2.slug == "test-gruppe-2"

    def test_same_slug_allowed_in_other_document_type(self, doc_type_contact, foreign_doc_type):
        """Der gleiche Slug ist in einem ANDEREN Dokumentationstyp erlaubt."""
        fg1 = FieldGroup.objects.create(document_type=doc_type_contact, name="Stammdaten")
        fg2 = FieldGroup.objects.create(document_type=foreign_doc_type, name="Stammdaten")
        assert fg1.slug == fg2.slug == "stammdaten"


@pytest.mark.django_db
class TestFieldGroupSlugImmutability:
    def test_slug_immutable_after_creation(self, doc_type_contact):
        fg = FieldGroup.objects.create(document_type=doc_type_contact, name="Stammdaten")
        fg.slug = "geaendert"
        with pytest.raises(ValidationError, match="nach Erstellung nicht geändert"):
            fg.save()

    def test_same_slug_no_error(self, doc_type_contact):
        fg = FieldGroup.objects.create(document_type=doc_type_contact, name="Stammdaten")
        fg.slug = "stammdaten"
        fg.sort_order = 5
        fg.save()  # kein Fehler
        fg.refresh_from_db()
        assert fg.sort_order == 5


@pytest.mark.django_db
class TestFieldGroupNameUnique:
    def test_duplicate_name_same_document_type_rejected(self, doc_type_contact):
        FieldGroup.objects.create(document_type=doc_type_contact, name="Stammdaten")
        with pytest.raises(IntegrityError), transaction.atomic():
            FieldGroup.objects.create(document_type=doc_type_contact, name="Stammdaten")

    def test_same_name_other_document_type_allowed(self, doc_type_contact, foreign_doc_type):
        FieldGroup.objects.create(document_type=doc_type_contact, name="Stammdaten")
        fg2 = FieldGroup.objects.create(document_type=foreign_doc_type, name="Stammdaten")
        assert fg2.pk is not None


@pytest.mark.django_db
class TestFieldGroupOrdering:
    def test_ordering_by_sort_order_then_name(self, doc_type_contact):
        FieldGroup.objects.create(document_type=doc_type_contact, name="Zebra", sort_order=1)
        FieldGroup.objects.create(document_type=doc_type_contact, name="Alpha", sort_order=2)
        FieldGroup.objects.create(document_type=doc_type_contact, name="Beta", sort_order=1)
        names = list(FieldGroup.objects.filter(document_type=doc_type_contact).values_list("name", flat=True))
        # sort_order zuerst (1 vor 2), dann Name alphabetisch innerhalb sort_order.
        assert names == ["Beta", "Zebra", "Alpha"]


@pytest.mark.django_db
class TestDocumentTypeFieldGroupRelation:
    def test_set_null_on_group_deletion(self, doc_type_contact):
        """Löschen der Gruppe lässt das DocumentTypeField bestehen (field_group=None)."""
        fg = FieldGroup.objects.create(document_type=doc_type_contact, name="Stammdaten")
        ft = FieldTemplate.objects.create(
            facility=doc_type_contact.facility,
            name="Neues Feld",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        dtf = DocumentTypeField.objects.create(
            document_type=doc_type_contact,
            field_template=ft,
            field_group=fg,
            sort_order=0,
        )
        fg.delete()
        dtf.refresh_from_db()
        assert dtf.field_group is None
        assert DocumentTypeField.objects.filter(pk=dtf.pk).exists()

    def test_reverse_relation_name(self, doc_type_contact):
        """FieldGroup.fields liefert die zugeordneten DocumentTypeFields."""
        fg = FieldGroup.objects.create(document_type=doc_type_contact, name="Stammdaten")
        ft = FieldTemplate.objects.create(
            facility=doc_type_contact.facility,
            name="Gruppenfeld",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        dtf = DocumentTypeField.objects.create(
            document_type=doc_type_contact,
            field_template=ft,
            field_group=fg,
            sort_order=0,
        )
        assert list(fg.fields.all()) == [dtf]

    def test_clean_rejects_foreign_document_type_group(self, doc_type_contact, foreign_doc_type):
        """Eine Gruppe aus einem fremden Dokumentationstyp ist unzulässig."""
        foreign_group = FieldGroup.objects.create(document_type=foreign_doc_type, name="Fremd")
        ft = FieldTemplate.objects.create(
            facility=doc_type_contact.facility,
            name="Feld A",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        dtf = DocumentTypeField(
            document_type=doc_type_contact,
            field_template=ft,
            field_group=foreign_group,
            sort_order=0,
        )
        with pytest.raises(ValidationError, match="selben Dokumentationstyp"):
            dtf.clean()

    def test_clean_accepts_matching_document_type_group(self, doc_type_contact):
        """Eine Gruppe aus demselben Dokumentationstyp passiert clean()."""
        fg = FieldGroup.objects.create(document_type=doc_type_contact, name="Passend")
        ft = FieldTemplate.objects.create(
            facility=doc_type_contact.facility,
            name="Feld B",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        dtf = DocumentTypeField(
            document_type=doc_type_contact,
            field_template=ft,
            field_group=fg,
            sort_order=0,
        )
        dtf.clean()  # kein Fehler

    def test_clean_allows_null_group(self, doc_type_contact):
        """Ohne Gruppe (field_group=None) greift der Guard nicht."""
        ft = FieldTemplate.objects.create(
            facility=doc_type_contact.facility,
            name="Feld C",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        dtf = DocumentTypeField(document_type=doc_type_contact, field_template=ft, sort_order=0)
        dtf.clean()  # kein Fehler
