"""Tests for EventHistory diff template tag (compute_diff)."""

import pytest
from django.utils import timezone

from core.models import EventHistory
from core.services.event import create_event, soft_delete_event, update_event
from core.templatetags.history_tags import ENCRYPTED_PLACEHOLDER, compute_diff


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
