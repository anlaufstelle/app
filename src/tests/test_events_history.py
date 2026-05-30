"""Tests für Events — Event-Erstellungs-Defaults + Felder-Partial (HTMX) (Refs Welle 6 #929)."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestEventCreateDefaultDocType:
    """Tests fuer Standard-Dokumentationstyp aus Einstellungen (#156)."""

    def test_default_document_type_preselected(self, client, staff_user, facility, doc_type_contact):
        """Wenn ein Standard-Dokumentationstyp gesetzt ist, wird er vorausgewaehlt."""
        from core.models import Settings

        Settings.objects.create(facility=facility, default_document_type=doc_type_contact)
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        content = response.content.decode()
        # Der Default-Typ sollte selected sein
        assert f'value="{doc_type_contact.pk}"' in content
        # selected-Attribut in derselben <option>
        import re

        assert re.search(rf'value="{doc_type_contact.pk}"[^>]*selected', content)
        # Dynamische Felder sollten vorgerendert sein
        assert "Dauer" in content

    def test_no_default_document_type(self, client, staff_user, facility):
        """Ohne Standard-Dokumentationstyp wird kein Typ vorausgewaehlt."""
        from core.models import Settings

        Settings.objects.create(facility=facility)
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "selected" not in content or 'value="" selected' not in content

    def test_no_settings_object(self, client, staff_user, facility):
        """Ohne Settings-Objekt soll die Seite trotzdem fehlerfrei laden."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200

    def test_inactive_default_document_type_ignored(self, client, staff_user, facility):
        """Inaktiver Standard-Dokumentationstyp wird nicht vorausgewaehlt."""
        from core.models import DocumentType, Settings

        inactive_dt = DocumentType.objects.create(
            facility=facility,
            name="Inaktiv",
            category=DocumentType.Category.CONTACT,
            is_active=False,
        )
        Settings.objects.create(facility=facility, default_document_type=inactive_dt)
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        content = response.content.decode()
        # Inaktiver Typ sollte nicht selected sein
        assert f'value="{inactive_dt.pk}" selected' not in content


@pytest.mark.django_db
class TestEventFieldsPartial:
    def test_event_fields_partial(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:event_fields_partial"),
            {"document_type": str(doc_type_contact.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Dauer" in content
        assert "Notiz" in content

    def test_event_fields_partial_empty(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_fields_partial"))
        assert response.status_code == 200
