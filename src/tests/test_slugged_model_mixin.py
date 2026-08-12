"""Vertragstests für das ``SluggedModel``-Mixin (Refs #1834).

Die Slug-Save-Mechanik — Auto-Slug beim Anlegen, Format-Guard für explizite
Slugs, Retry-Schleife bei ``IntegrityError`` und der Immutabilitäts-Guard —
lag dreimal nahezu wortgleich in ``DocumentType.save``, ``FieldTemplate.save``
und ``FieldGroup.save``. Diese Datei pinnt den Vertrag der gemeinsamen Basis:

* ``slug_sibling_queryset()`` ist ein Pflicht-Hook (kein stiller Default),
* alle drei Modelle liefern ihren Kollisions-Scope über genau diesen Hook,
* die Mechanik selbst existiert nur noch EINMAL (keine eigene ``save()``-
  Kopie mehr in ``DocumentType``/``FieldGroup``; ``FieldTemplate`` behält nur
  seine Verschlüsselungs-Normalisierung als Delta).

Das Verhalten selbst bleibt von ``test_document_type_slug.py``,
``test_fieldtemplate_slug.py`` und ``test_slug_immutable_trigger.py``
abgedeckt — hier steht nur der Vertrag.
"""

import pytest

from core.models import DocumentType, FieldGroup, FieldTemplate
from core.models.mixins import SluggedModel


class TestHookContract:
    def test_slug_sibling_queryset_is_mandatory(self):
        """Ohne eigenen Hook gibt es keinen stillen Fallback-Scope."""
        with pytest.raises(NotImplementedError):
            SluggedModel.slug_sibling_queryset(object())

    @pytest.mark.parametrize("model", [DocumentType, FieldTemplate, FieldGroup])
    def test_models_use_the_shared_base(self, model):
        assert issubclass(model, SluggedModel)
        assert "slug_sibling_queryset" in model.__dict__

    @pytest.mark.parametrize("model", [DocumentType, FieldGroup])
    def test_no_local_save_copy(self, model):
        """DocumentType/FieldGroup speichern ausschließlich über die Basis."""
        assert "save" not in model.__dict__
        assert model.save is SluggedModel.save

    def test_retry_limit_lives_on_the_base_only(self):
        assert SluggedModel._SLUG_RETRY_LIMIT == 3
        for model in (DocumentType, FieldTemplate, FieldGroup):
            assert "_SLUG_RETRY_LIMIT" not in model.__dict__


@pytest.mark.django_db
class TestSiblingScopes:
    def test_document_type_scope_is_the_facility(self, facility, other_facility):
        mine = DocumentType.objects.create(facility=facility, name="Beratung A")
        sibling = DocumentType.objects.create(facility=facility, name="Beratung B")
        foreign = DocumentType.objects.create(facility=other_facility, name="Beratung C")

        siblings = set(mine.slug_sibling_queryset())
        assert sibling in siblings
        assert mine not in siblings
        assert foreign not in siblings

    def test_field_template_scope_is_the_facility(self, facility, other_facility):
        mine = FieldTemplate.objects.create(facility=facility, name="Dauer A", field_type="text")
        sibling = FieldTemplate.objects.create(facility=facility, name="Dauer B", field_type="text")
        foreign = FieldTemplate.objects.create(facility=other_facility, name="Dauer C", field_type="text")

        siblings = set(mine.slug_sibling_queryset())
        assert sibling in siblings
        assert mine not in siblings
        assert foreign not in siblings

    def test_field_group_scope_is_the_document_type(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Gruppen-Typ")
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ")
        mine = FieldGroup.objects.create(document_type=dt, name="Block A")
        sibling = FieldGroup.objects.create(document_type=dt, name="Block B")
        foreign = FieldGroup.objects.create(document_type=other_dt, name="Block C")

        siblings = set(mine.slug_sibling_queryset())
        assert sibling in siblings
        assert mine not in siblings
        assert foreign not in siblings
