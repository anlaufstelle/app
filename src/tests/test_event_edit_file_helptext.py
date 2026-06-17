"""Refs #1143: Der Datei-Upload-Hilfetext im Event-Edit-Formular darf nur an
echten Datei-Feldern erscheinen, nicht an Mehrfach-Auswahl-Feldern.

Ursache: ``edit.html`` gated den File-Upload-Block (inkl. „Weitere Dateien
hinzufügen (mehrere möglich):") an ``widget.allow_multiple_selected``. Dieses
Attribut ist bei Djangos ``CheckboxSelectMultiple`` (Mehrfach-Auswahl, z.B.
das Feld „Leistungen" im Kontakt) ebenso ``True`` wie beim ``MultipleFileInput``
— deshalb erschien der Datei-Hinweis fälschlich an Checkbox-Gruppen.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate

FILE_HELP = "Weitere Dateien hinzufügen"


def _doc_type_with_field(facility, field_type, *, name, slug, options=None):
    doc_type = DocumentType.objects.create(
        facility=facility,
        name=name,
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name=f"{name}-Feld",
        slug=slug,
        field_type=field_type,
        options_json=options or [],
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
    return doc_type


def _event(facility, doc_type, user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=user,
    )


@pytest.mark.django_db
class TestEventEditFileHelptext:
    def test_multiselect_field_has_no_file_helptext(self, client, staff_user, facility):
        """Ein Mehrfach-Auswahl-Feld (CheckboxSelectMultiple) zeigt KEINEN
        Datei-Upload-Hinweis."""
        doc_type = _doc_type_with_field(
            facility,
            FieldTemplate.FieldType.MULTI_SELECT,
            name="MitMehrfachauswahl",
            slug="mehrfachauswahl-test",
            options=[
                {"slug": "a", "label": "A", "is_active": True},
                {"slug": "b", "label": "B", "is_active": True},
            ],
        )
        event = _event(facility, doc_type, staff_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event.pk}))
        assert response.status_code == 200
        assert FILE_HELP not in response.content.decode()

    def test_file_field_keeps_file_helptext(self, client, staff_user, facility):
        """Kontrolle: an einem echten Datei-Feld bleibt der Hinweis erhalten."""
        doc_type = _doc_type_with_field(
            facility,
            FieldTemplate.FieldType.FILE,
            name="MitDatei",
            slug="datei-test",
        )
        event = _event(facility, doc_type, staff_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event.pk}))
        assert response.status_code == 200
        assert FILE_HELP in response.content.decode()
