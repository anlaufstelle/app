"""Tests für rollenbasierte Feldsensitivität in Event-Views (Issue #113)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate


@pytest.fixture
def doc_type_high(facility):
    """DocumentType mit HIGH-Sensibilität und einem verschlüsselten Feld."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.HIGH,
        name="Hochsensibel",
    )
    ft_secret = FieldTemplate.objects.create(
        facility=facility,
        name="Geheimfeld",
        field_type=FieldTemplate.FieldType.TEXT,
        is_encrypted=True,
    )
    ft_normal = FieldTemplate.objects.create(
        facility=facility,
        name="Normalfeld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_secret, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_normal, sort_order=1)
    return doc_type


@pytest.fixture
def doc_type_elevated(facility):
    """DocumentType mit ELEVATED-Sensibilität."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        name="ErhoehtSensibel",
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="ErhoehtFeld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
    return doc_type


@pytest.fixture
def event_high(facility, doc_type_high, staff_user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_high,
        occurred_at=timezone.now(),
        data_json={"geheimfeld": "secret-value", "normalfeld": "normal-value"},
        created_by=staff_user,
    )


@pytest.fixture
def event_elevated(facility, doc_type_elevated, staff_user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_elevated,
        occurred_at=timezone.now(),
        data_json={"erhoehtfeld": "elevated-value"},
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestEventDetailFieldSensitivity:
    """EventDetailView zeigt Felder rollenabhängig an."""

    def test_admin_sees_all_fields(self, client, admin_user, event_high):
        client.force_login(admin_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "secret-value" in content
        assert "normal-value" in content
        assert "[Eingeschränkt]" not in content

    def test_lead_sees_all_fields(self, client, lead_user, event_high):
        client.force_login(lead_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "secret-value" in content
        assert "normal-value" in content

    def test_staff_cannot_see_high_sensitivity(self, client, staff_user, event_high):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "secret-value" not in content
        assert "normal-value" not in content
        assert "[Eingeschränkt]" in content

    def test_staff_can_see_elevated(self, client, staff_user, event_elevated):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_elevated.pk}))
        content = response.content.decode()
        assert "elevated-value" in content
        assert "[Eingeschränkt]" not in content

    def test_assistant_cannot_see_elevated(self, client, assistant_user, event_elevated):
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_elevated.pk}))
        content = response.content.decode()
        assert "elevated-value" not in content
        assert "[Eingeschränkt]" in content

    def test_assistant_can_see_normal(self, client, assistant_user, sample_event):
        """sample_event has NORMAL sensitivity doc type."""
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk}))
        content = response.content.decode()
        assert "Testnotiz" in content
        assert "[Eingeschränkt]" not in content

    def test_encrypted_field_restricted_for_staff(self, client, staff_user, facility):
        """Even on NORMAL doc type, an encrypted field is HIGH → hidden for staff."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="NormalMitEncrypted",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft_enc = FieldTemplate.objects.create(
            facility=facility,
            name="EncField",
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=True,
        )
        ft_plain = FieldTemplate.objects.create(
            facility=facility,
            name="PlainField",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_enc, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_plain, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"encfield": "enc-val", "plainfield": "plain-val"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        content = response.content.decode()
        assert "plain-val" in content
        assert "enc-val" not in content
        assert "[Eingeschränkt]" in content


@pytest.mark.django_db
class TestEventUpdateFieldSensitivity:
    """EventUpdateView blendet sensitive Felder im Formular aus."""

    def test_admin_sees_all_form_fields(self, client, admin_user, event_high):
        client.force_login(admin_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "Geheimfeld" in content
        assert "Normalfeld" in content

    def test_staff_form_excludes_high_fields(self, client, staff_user, event_high):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "Geheimfeld" not in content
        assert "Normalfeld" not in content

    def test_assistant_form_excludes_elevated_fields(self, client, assistant_user, event_elevated):
        client.force_login(assistant_user)
        # assistant_user created_by is staff_user, but EventUpdateView checks
        # created_by for assistant — need to set it
        event_elevated.created_by = assistant_user
        event_elevated.save()
        response = client.get(reverse("core:event_update", kwargs={"pk": event_elevated.pk}))
        content = response.content.decode()
        assert "ErhoehtFeld" not in content

    def test_staff_post_preserves_restricted_values(self, client, staff_user, event_high):
        """POST von Staff darf eingeschränkte Felder nicht überschreiben."""
        original_data = event_high.data_json.copy()
        client.force_login(staff_user)
        client.post(
            reverse("core:event_update", kwargs={"pk": event_high.pk}),
            {},  # no fields submitted (all restricted)
        )
        event_high.refresh_from_db()
        # Restricted fields must still have their original values (may be encrypted dicts)
        assert event_high.data_json.get("geheimfeld") is not None
        assert event_high.data_json.get("normalfeld") == original_data.get("normalfeld")
