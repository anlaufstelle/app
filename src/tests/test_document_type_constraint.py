"""UniqueConstraint-Tests für DocumentType (Refs #434, Refs #733, Refs #1749).

Migration ``0072_documenttype_unique_constraint_helptext`` hatte
``UniqueConstraint(facility, name, category)`` eingeführt; Migration
``0134_remove_documenttype_category`` (ADR-040) engt sie auf
``(facility, name)`` ein, weil ``category`` als Zweitschlüssel entfallen ist.
Ohne die Regel könnten zwei gleichnamige DocumentTypes den Snapshot-Lookup in
``services/dashboard/snapshot.py`` mit ``MultipleObjectsReturned`` auf einen
500er fallen lassen.

**Verhaltensänderung gegenüber dem Bestand:** Zwei gleichnamige Typen einer
Einrichtung waren bisher erlaubt, solange ihre Kategorie sich unterschied. Das
ist jetzt verboten — für Nutzerinnen waren die beiden Zeilen ohnehin nicht
unterscheidbar (kein Dropdown gruppiert nach Kategorie).
"""

import pytest
from django.db import IntegrityError, transaction

from core.models import DocumentType


@pytest.mark.django_db
class TestDocumentTypeUniqueConstraint:
    def test_duplicate_facility_name_raises(self, facility):
        DocumentType.objects.create(facility=facility, name="Beratungsgespräch")
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentType.objects.create(facility=facility, name="Beratungsgespräch")

    def test_same_name_in_one_facility_is_rejected(self, facility):
        """Refs #1749: frueher erlaubt, solange die Kategorie abwich."""
        DocumentType.objects.create(facility=facility, name="Notiz")
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentType.objects.create(facility=facility, name="Notiz")

    def test_same_name_different_facility_allowed(self, facility, organization):
        # Zwei Einrichtungen duerfen unabhaengig denselben DocumentType
        # anlegen — die Constraint ist facility-scoped.
        from core.models import Facility

        other_facility = Facility.objects.create(organization=organization, name="Andere Einrichtung")
        DocumentType.objects.create(facility=facility, name="Krisengespräch")
        DocumentType.objects.create(facility=other_facility, name="Krisengespräch")

    def test_constraint_name_ist_das_paar(self):
        """Der Constraint-Name ist tragend — Migration 0134 und Modell teilen ihn."""
        names = {constraint.name for constraint in DocumentType._meta.constraints}
        assert "unique_facility_documenttype_name" in names
        assert "unique_facility_documenttype_name_category" not in names
