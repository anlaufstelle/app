"""Tests for EventHistory diff template tag (compute_diff)."""

import pytest
from django.utils import timezone

from core.models import EventHistory
from core.services.event import create_event, soft_delete_event, update_event
from core.templatetags.history_tags import ENCRYPTED_PLACEHOLDER, RESTRICTED_PLACEHOLDER, compute_diff


@pytest.mark.django_db
class TestComputeDiffUpdate:
    """Test compute_diff for UPDATE actions."""

    def test_update_shows_changed_fields(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Erstgespräch"},
        )
        update_event(event, staff_user, {"dauer": 30, "notiz": "Erstgespräch"})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        result = compute_diff(entry)

        assert result["action"] == "update"
        assert len(result["fields"]) == 1
        field = result["fields"][0]
        assert field["label"] == "Dauer"
        assert field["old_value"] == "15"
        assert field["new_value"] == "30"
        assert field["changed"] is True

    def test_update_all_fields_changed(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Alt"},
        )
        update_event(event, staff_user, {"dauer": 30, "notiz": "Neu"})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        result = compute_diff(entry)

        assert result["action"] == "update"
        assert len(result["fields"]) == 2
        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Notiz" in labels

    def test_update_no_changes_yields_empty_fields(self, facility, staff_user, doc_type_contact):
        """If data_before == data_after, no changed fields are returned."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        # Force an update with same data
        update_event(event, staff_user, {"dauer": 15})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        result = compute_diff(entry)

        assert result["action"] == "update"
        assert len(result["fields"]) == 0


@pytest.mark.django_db
class TestComputeDiffCreate:
    """Test compute_diff for CREATE actions."""

    def test_create_shows_all_fields(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Erstgespräch"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        result = compute_diff(entry)

        assert result["action"] == "create"
        assert len(result["fields"]) == 2
        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Notiz" in labels
        for f in result["fields"]:
            assert "value" in f

    def test_create_resolves_labels(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        result = compute_diff(entry)

        assert result["fields"][0]["label"] == "Dauer"


@pytest.mark.django_db
class TestComputeDiffDelete:
    """Test compute_diff for DELETE actions."""

    def test_delete_shows_redacted_fields(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Abschluss"},
        )
        soft_delete_event(event, staff_user)

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).first()
        result = compute_diff(entry)

        assert result["action"] == "delete"
        assert len(result["fields"]) == 2
        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Notiz" in labels
        # Values are redacted, not the original data
        for f in result["fields"]:
            assert f["value"] == "\u2013 (gelöscht)"


@pytest.mark.django_db
class TestComputeDiffEncrypted:
    """Test that encrypted fields are masked in diffs."""

    def test_encrypted_field_masked_on_create(self, facility, staff_user, doc_type_crisis):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Vertrauliche Info"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        result = compute_diff(entry)

        assert result["action"] == "create"
        assert len(result["fields"]) == 1
        assert result["fields"][0]["value"] == ENCRYPTED_PLACEHOLDER

    def test_encrypted_field_masked_on_update(self, facility, staff_user, doc_type_crisis):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Alt"},
        )
        update_event(event, staff_user, {"notiz-krise": "Neu"})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        result = compute_diff(entry)

        assert result["action"] == "update"
        assert len(result["fields"]) == 1
        assert result["fields"][0]["old_value"] == ENCRYPTED_PLACEHOLDER
        assert result["fields"][0]["new_value"] == ENCRYPTED_PLACEHOLDER

    def test_encrypted_field_masked_on_delete(self, facility, staff_user, doc_type_crisis):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Geheim"},
        )
        soft_delete_event(event, staff_user)

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).first()
        # data_before is now redacted (only field names, no values)
        assert entry.data_before == {"_redacted": True, "fields": ["notiz-krise"]}
        result = compute_diff(entry)

        assert result["action"] == "delete"
        assert len(result["fields"]) == 1
        # Encrypted fields still show the encrypted placeholder even in redacted mode
        assert result["fields"][0]["value"] == ENCRYPTED_PLACEHOLDER


@pytest.mark.django_db
class TestComputeDiffSensitivity:
    """Test that sensitive fields are masked based on user role."""

    def test_compute_diff_masks_sensitive_fields_for_staff(self, facility, staff_user, doc_type_crisis):
        """Staff user cannot see HIGH-sensitivity fields — they must be masked."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Vertrauliche Info"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()

        # Without user: encrypted placeholder (is_encrypted=True takes precedence)
        result_no_user = compute_diff(entry)
        assert result_no_user["fields"][0]["value"] == ENCRYPTED_PLACEHOLDER

        # With staff user: HIGH sensitivity field → restricted placeholder
        # (restricted check runs before encrypted, so restricted wins)
        result_with_user = compute_diff(entry, user=staff_user)
        assert result_with_user["fields"][0]["value"] == RESTRICTED_PLACEHOLDER

    def test_compute_diff_masks_elevated_field_for_assistant(self, facility, assistant_user):
        """Assistant user cannot see ELEVATED-sensitivity fields — they must be masked."""
        from core.models import DocumentType, DocumentTypeField, FieldTemplate

        # NORMAL doc type so assistant can create events, but with an ELEVATED field
        doc_type = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.CONTACT,
            name="Kontakt (Elevated-Feld)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Vertrauliche Notiz",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)

        event = create_event(
            facility=facility,
            user=assistant_user,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"vertrauliche-notiz": "Geheim"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()

        result = compute_diff(entry, user=assistant_user)
        assert result["fields"][0]["value"] == RESTRICTED_PLACEHOLDER

    def test_compute_diff_shows_sensitive_fields_for_lead(self, facility, doc_type_crisis):
        """Lead user can see all sensitivity levels — values should not be masked by sensitivity."""
        from core.models.user import User

        lead_user = User.objects.create_user(
            username="testlead_hist",
            role=User.Role.LEAD,
            facility=facility,
        )
        event = create_event(
            facility=facility,
            user=lead_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Vertraulich"},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()

        # Lead can see HIGH fields, but field is still encrypted → encrypted placeholder
        result = compute_diff(entry, user=lead_user)
        assert result["fields"][0]["value"] == ENCRYPTED_PLACEHOLDER

    def test_compute_diff_masks_sensitive_update_for_staff(self, facility, staff_user, doc_type_crisis):
        """Update diffs must also mask sensitive fields for staff."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Alt"},
        )
        update_event(event, staff_user, {"notiz-krise": "Neu"})

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()

        result = compute_diff(entry, user=staff_user)
        assert len(result["fields"]) == 1
        assert result["fields"][0]["old_value"] == RESTRICTED_PLACEHOLDER
        assert result["fields"][0]["new_value"] == RESTRICTED_PLACEHOLDER

    def test_compute_diff_masks_sensitive_delete_for_staff(self, facility, staff_user, doc_type_crisis):
        """Delete diffs must also mask sensitive fields for staff."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Geheim"},
        )
        soft_delete_event(event, staff_user)

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).first()

        result = compute_diff(entry, user=staff_user)
        assert len(result["fields"]) == 1
        assert result["fields"][0]["value"] == RESTRICTED_PLACEHOLDER


@pytest.mark.django_db
class TestComputeDiffFieldMetadata:
    """Test that frozen field_metadata is used instead of live FieldTemplates."""

    def test_field_rename_does_not_affect_old_history(self, facility, staff_user, doc_type_contact):
        """After renaming a FieldTemplate, old history entries still show the original name."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        assert entry.field_metadata["dauer"]["name"] == "Dauer"

        from core.models import FieldTemplate

        ft = FieldTemplate.objects.get(facility=facility, slug="dauer")
        ft.name = "Dauer (Minuten)"
        ft.save()

        result = compute_diff(entry)
        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Dauer (Minuten)" not in labels

    def test_legacy_entry_without_metadata_falls_back(self, facility, staff_user, doc_type_contact):
        """Legacy entries without field_metadata fall back to live FieldTemplates."""
        from core.models import Event

        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            created_by=staff_user,
        )
        legacy_entry = EventHistory.objects.create(
            event=event,
            changed_by=staff_user,
            action=EventHistory.Action.CREATE,
            data_after={"dauer": 15},
        )

        result = compute_diff(legacy_entry)
        assert result["fields"][0]["label"] == "Dauer"


@pytest.mark.django_db
class TestComputeDiffFallbackLabel:
    """Test that unknown slugs use fallback labels."""

    def test_unknown_slug_stripped_by_validation(self, facility, staff_user, doc_type_contact):
        """Unknown fields are stripped by _validate_data_json before saving."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "unknown-field": "value"},
        )

        # Unknown field was stripped during creation
        assert "unknown-field" not in event.data_json
        assert event.data_json == {"dauer": 15}

        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        result = compute_diff(entry)

        labels = {f["label"] for f in result["fields"]}
        assert "Dauer" in labels
        assert "Unknown Field" not in labels
