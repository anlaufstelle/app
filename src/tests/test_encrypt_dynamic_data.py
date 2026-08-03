"""Tests fuer ``encrypt_dynamic_data`` (P4a, Refs #1058).

Deckt den Bulk-Pfad ab, der bei ``bulk_create``/Heal-Laeufen
``Event.save()`` (und damit ``DynamicDataEncryptionMixin._encrypt_sensitive_fields``)
umgeht. Kernbefund: FILE-Felder sind immer ``is_encrypted=True``
(``DocumentType.save()``), daher landet ihr Marker-Dict
(``__file__``/``__files__``) in der ``encrypted_slugs``-Menge — ohne
File-Marker-Skip wuerde der Marker faelschlich als Klartext verschluesselt
(kaputte Anhangsreferenz). Der Save-Pfad (``Event._encrypt_sensitive_fields``,
jetzt im Mixin) uebersprang Marker schon immer; der Bulk-Pfad bisher nicht.
"""

import pytest
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.file_vault import decrypt_field, encrypt_dynamic_data, encrypt_field, is_encrypted_value


@pytest.fixture
def doc_type_with_file_field(facility):
    """DocumentType mit einem FILE-Feld (immer is_encrypted=True) plus einem
    regulaeren verschluesselten Textfeld — deckt Koexistenz beider Faelle ab.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        name="Mit Anhang",
    )
    ft_anhang = FieldTemplate.objects.create(
        facility=facility,
        name="Anhang",
        field_type=FieldTemplate.FieldType.FILE,
    )
    ft_notiz = FieldTemplate.objects.create(
        facility=facility,
        name="Vertrauliche Notiz",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_encrypted=True,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_anhang, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_notiz, sort_order=1)
    return doc_type


@pytest.mark.django_db
class TestEncryptDynamicDataFileMarkerSkip:
    def test_singleton_file_marker_untouched(self, doc_type_with_file_field, settings):
        settings.ENCRYPTION_KEY = "dummy-key-not-needed-for-this-path"
        from cryptography.fernet import Fernet

        settings.ENCRYPTION_KEY = Fernet.generate_key().decode()
        marker = {"__file__": True, "attachment_id": "11111111-1111-1111-1111-111111111111"}
        data_json = {"anhang": marker}

        result = encrypt_dynamic_data(doc_type_with_file_field, data_json)

        assert result["anhang"] == marker, "Stufe-A-Marker (__file__) darf nicht verschluesselt werden"

    def test_multi_file_marker_untouched(self, doc_type_with_file_field, settings):
        from cryptography.fernet import Fernet

        settings.ENCRYPTION_KEY = Fernet.generate_key().decode()
        marker = {"__files__": True, "entries": [{"id": "a", "sort": 0}, {"id": "b", "sort": 1}]}
        data_json = {"anhang": marker}

        result = encrypt_dynamic_data(doc_type_with_file_field, data_json)

        assert result["anhang"] == marker, "Stufe-B-Marker (__files__) darf nicht verschluesselt werden"

    def test_normal_encrypted_slug_still_encrypted(self, doc_type_with_file_field, settings):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        settings.ENCRYPTION_KEY = key
        data_json = {"vertrauliche-notiz": "Klartext-PII"}

        result = encrypt_dynamic_data(doc_type_with_file_field, data_json)

        assert is_encrypted_value(result["vertrauliche-notiz"])
        assert decrypt_field(result["vertrauliche-notiz"]) == "Klartext-PII"

    def test_file_marker_and_normal_field_coexist(self, doc_type_with_file_field, settings):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        settings.ENCRYPTION_KEY = key
        marker = {"__files__": True, "entries": [{"id": "a", "sort": 0}]}
        data_json = {"anhang": marker, "vertrauliche-notiz": "Geheim"}

        result = encrypt_dynamic_data(doc_type_with_file_field, data_json)

        assert result["anhang"] == marker
        assert is_encrypted_value(result["vertrauliche-notiz"])
        assert decrypt_field(result["vertrauliche-notiz"]) == "Geheim"

    def test_already_encrypted_value_stays_stable(self, doc_type_with_file_field, settings):
        """Idempotenz: ein bereits verschluesselter Wert wird nicht doppelt verschluesselt."""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        settings.ENCRYPTION_KEY = key
        already_encrypted = encrypt_field("Original")
        data_json = {"vertrauliche-notiz": already_encrypted}

        result = encrypt_dynamic_data(doc_type_with_file_field, data_json)

        assert result["vertrauliche-notiz"]["value"] == already_encrypted["value"]


@pytest.mark.django_db
def test_encrypt_dynamic_data_returns_falsy_data_json_unchanged(doc_type_with_file_field):
    """Leeres/None ``data_json`` bleibt unveraendert (Bestandsverhalten)."""
    assert encrypt_dynamic_data(doc_type_with_file_field, {}) == {}
    assert encrypt_dynamic_data(doc_type_with_file_field, None) is None


@pytest.mark.django_db
def test_encrypt_dynamic_data_used_by_bulk_create_path(facility, client_identified, staff_user):
    """Charakterisierung des realen Bulk-Pfads (analog ``seed/events.py``):
    ``bulk_create`` umgeht ``Event.save()`` komplett — ``encrypt_dynamic_data``
    muss VOR dem ``bulk_create``-Aufruf angewandt werden, inkl. Marker-Skip.
    """
    from cryptography.fernet import Fernet
    from django.test import override_settings

    from core.models import Event

    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        name="Bulk mit Anhang",
    )
    ft_anhang = FieldTemplate.objects.create(
        facility=facility,
        name="Anhang Bulk",
        field_type=FieldTemplate.FieldType.FILE,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_anhang, sort_order=0)

    marker = {"__files__": True, "entries": [{"id": "a", "sort": 0}]}
    with override_settings(ENCRYPTION_KEY=Fernet.generate_key().decode()):
        event = Event(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"anhang-bulk": marker},
            created_by=staff_user,
        )
        event.data_json = encrypt_dynamic_data(event.document_type, event.data_json)
        Event.objects.bulk_create([event])

    event.refresh_from_db()
    assert event.data_json["anhang-bulk"] == marker
