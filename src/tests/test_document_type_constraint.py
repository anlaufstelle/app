"""UniqueConstraint-Tests für DocumentType (Refs #434, Refs #733).

Migration ``0072_documenttype_unique_constraint_helptext`` ergaenzt
``UniqueConstraint(facility, name, category)``. Ohne diese Regel
konnten zwei DocumentTypes mit identischem Tripel den Snapshot-
Lookup in ``services/snapshot.py:33-42`` mit
``MultipleObjectsReturned`` auf einen 500er fallen lassen.
"""

import pytest
from django.db import IntegrityError, transaction

from core.models import DocumentType


@pytest.mark.django_db
class TestDocumentTypeUniqueConstraint:
    def test_duplicate_facility_name_category_raises(self, facility):
        DocumentType.objects.create(
            facility=facility,
            name="Beratungsgespräch",
            category=DocumentType.Category.SERVICE,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                DocumentType.objects.create(
                    facility=facility,
                    name="Beratungsgespräch",
                    category=DocumentType.Category.SERVICE,
                )

    def test_same_name_different_category_allowed(self, facility):
        # Zwei DocumentTypes mit gleichem Namen aber unterschiedlicher
        # Kategorie sind weiterhin erlaubt — die Constraint ist auf das
        # Tripel, nicht auf das Paar (facility, name).
        DocumentType.objects.create(
            facility=facility,
            name="Notiz",
            category=DocumentType.Category.NOTE,
        )
        DocumentType.objects.create(
            facility=facility,
            name="Notiz",
            category=DocumentType.Category.ADMIN,
        )

    def test_same_name_same_category_different_facility_allowed(self, facility, organization):
        # Zwei Einrichtungen duerfen unabhaengig denselben DocumentType
        # anlegen — die Constraint ist facility-scoped.
        from core.models import Facility

        other_facility = Facility.objects.create(organization=organization, name="Andere Einrichtung")
        DocumentType.objects.create(
            facility=facility,
            name="Krisengespräch",
            category=DocumentType.Category.SERVICE,
        )
        DocumentType.objects.create(
            facility=other_facility,
            name="Krisengespräch",
            category=DocumentType.Category.SERVICE,
        )
