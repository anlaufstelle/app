"""Tests für FieldTemplate-Löschschutz (Issue #356).

Verhindert Datenverlust: Eine FieldTemplate darf nicht hart gelöscht werden,
solange Events im selben Facility-Scope Werte unter ihrem Slug enthalten.
Soft-Delete (is_active=False) bleibt als Alternative verfügbar.
"""

import pytest
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate


@pytest.mark.django_db
class TestFieldTemplateDeleteProtection:
    """Schutz vor Hard-Delete einer FieldTemplate, die in Events referenziert ist."""

    def test_delete_fieldtemplate_with_data_raises_protected_error(self, facility, staff_user, doc_type_contact):
        """Event.data_json enthält den Slug → ProtectedError beim delete()."""
        ft = FieldTemplate.objects.get(facility=facility, slug="dauer")
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            is_anonymous=True,
            created_by=staff_user,
        )

        with pytest.raises(ProtectedError) as excinfo:
            with transaction.atomic():
                ft.delete()

        assert "Dauer" in str(excinfo.value.args[0])
        assert "dauer" in str(excinfo.value.args[0])
        assert FieldTemplate.objects.filter(pk=ft.pk).exists()

    def test_delete_fieldtemplate_without_data_succeeds(self, facility):
        """Keine Events mit dem Slug → Hard-Delete funktioniert."""
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Ungenutzt",
            field_type=FieldTemplate.FieldType.TEXT,
        )

        ft.delete()

        assert not FieldTemplate.objects.filter(pk=ft.pk).exists()

    def test_delete_fieldtemplate_with_only_empty_events_succeeds(self, facility, staff_user):
        """Events ohne Wert unter dem Slug blockieren nicht."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.CONTACT,
            name="Andere Doku",
        )
        ft_unused = FieldTemplate.objects.create(
            facility=facility,
            name="Unbenutztes Feld",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_unused, sort_order=0)
        Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"andere-key": "wert"},
            is_anonymous=True,
            created_by=staff_user,
        )

        ft_unused.delete()

        assert not FieldTemplate.objects.filter(pk=ft_unused.pk).exists()

    def test_deactivate_fieldtemplate_succeeds_with_data(self, facility, staff_user, doc_type_contact):
        """Soft-Delete via is_active=False bleibt auch mit Event-Daten möglich."""
        ft = FieldTemplate.objects.get(facility=facility, slug="dauer")
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            is_anonymous=True,
            created_by=staff_user,
        )

        ft.is_active = False
        ft.save()

        ft.refresh_from_db()
        assert ft.is_active is False
        # Der Soft-Delete darf die Löschung NICHT auslösen.
        assert FieldTemplate.objects.filter(pk=ft.pk).exists()

    def test_queryset_delete_blocks_with_data(self, facility, staff_user, doc_type_contact):
        """Auch Bulk-Delete via QuerySet löst den Schutz aus."""
        ft = FieldTemplate.objects.get(facility=facility, slug="dauer")
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 42},
            is_anonymous=True,
            created_by=staff_user,
        )

        with pytest.raises(ProtectedError):
            with transaction.atomic():
                FieldTemplate.objects.filter(pk=ft.pk).delete()

        assert FieldTemplate.objects.filter(pk=ft.pk).exists()

    def test_cross_facility_data_does_not_block(self, facility, other_facility, staff_user):
        """Events in einer anderen Facility dürfen das Löschen nicht blockieren."""
        ft_a = FieldTemplate.objects.create(
            facility=facility,
            name="Dauer",
            field_type=FieldTemplate.FieldType.NUMBER,
        )
        other_doc = DocumentType.objects.create(
            facility=other_facility,
            category=DocumentType.Category.CONTACT,
            name="OtherKontakt",
        )
        other_ft = FieldTemplate.objects.create(
            facility=other_facility,
            name="Dauer",
            field_type=FieldTemplate.FieldType.NUMBER,
        )
        DocumentTypeField.objects.create(document_type=other_doc, field_template=other_ft, sort_order=0)

        other_user = staff_user
        Event.objects.create(
            facility=other_facility,
            document_type=other_doc,
            occurred_at=timezone.now(),
            data_json={"dauer": 5},
            is_anonymous=True,
            created_by=other_user,
        )

        # ft_a (im Hauptfacility) darf gelöscht werden — keine Events dort.
        ft_a.delete()
        assert not FieldTemplate.objects.filter(pk=ft_a.pk).exists()

    def test_facility_cascade_is_not_blocked_by_signal(self, facility, staff_user, doc_type_contact):
        """Facility-Cascade löst den FieldTemplate-Schutz nicht aus.

        Hintergrund: Beim Löschen einer Facility werden sowohl ihre FieldTemplates
        als auch ihre Events gelöscht. Unser Signal darf diesen Cascade nicht
        blockieren — Events werden in derselben Operation mitentfernt, es entsteht
        kein Datenverlust. (``origin`` ist die Facility, nicht die FieldTemplate.)
        """
        ft = FieldTemplate.objects.get(facility=facility, slug="dauer")
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 99},
            is_anonymous=True,
            created_by=staff_user,
        )

        # Events vorab entfernen (ungleicher on_delete-Typ: Event→DocumentType=PROTECT blockiert sonst
        # den Facility-Cascade — unabhängig von unserem FieldTemplate-Schutz).
        Event.objects.filter(facility=facility).delete()

        # Facility-Cascade darf jetzt NICHT durch unseren FieldTemplate-Schutz scheitern.
        # (Falls das Signal naiv prüfte, würde es den Slug zwar nicht mehr in Events finden —
        #  der wichtigere Test ist: Origin-Heuristik greift für nicht-direkte Cascades.)
        facility.delete()

        assert not FieldTemplate.objects.filter(pk=ft.pk).exists()

    def test_cascade_origin_heuristic_allows_non_fieldtemplate_delete(self, facility, staff_user, doc_type_contact):
        """Auch bei bestehenden Events im Facility-Scope blockiert ein nicht-direkter Delete nicht.

        Szenario: Ein Nicht-FieldTemplate-Origin (z.B. theoretischer Cascade oder manuelle
        Löschung eines Dokumenttyps) erreicht das FieldTemplate-pre_delete. Da ``origin``
        keine FieldTemplate ist, wird die Schutzprüfung übersprungen.
        """
        ft = FieldTemplate.objects.get(facility=facility, slug="dauer")
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 77},
            is_anonymous=True,
            created_by=staff_user,
        )

        # Signal manuell mit Origin=DocumentType feuern — Schutz darf NICHT greifen.
        from django.db.models.signals import pre_delete

        # Sollte nicht werfen (kein direkter FieldTemplate-Delete).
        pre_delete.send(sender=FieldTemplate, instance=ft, origin=doc_type_contact, using="default")
