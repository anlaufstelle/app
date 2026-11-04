"""Key rotation must not confuse valid plaintext with the decryption fallback (Refs #2624).

``safe_decrypt`` returns the display placeholder ``"[verschlüsselt]"`` for a
corrupt token. A user may legitimately store exactly that text. Rotation has to
re-encrypt such a value under the new primary key like any other value; after
the old key is removed it must still decrypt to the original text. Corrupt
tokens stay untouched and a missing key configuration still fails loudly.

All checks run through ``rotate_all`` with real old/new Fernet keys.
"""

import pytest
from django.test import override_settings
from django.utils import timezone

from core.models import Client, Event, EventHistory, RectificationRequest, ShiftHandoverNote
from core.models.attachment import Attachment
from core.services.file_vault import EncryptionKeyMissing, decrypt_field, encrypt_field, generate_key
from core.services.file_vault.rotation import rotate_all
from tests.test_reencrypt_fields import (
    _handover_window,
    raw_client_data_json,
    raw_event_data_json,
    raw_history_payloads,
)

pytestmark = pytest.mark.django_db

PLACEHOLDER_TEXT = "[verschlüsselt]"
CORRUPT_TOKEN = {"__encrypted__": True, "value": "corrupt"}


@pytest.fixture
def keys():
    return {"old": generate_key(), "new": generate_key()}


@pytest.fixture
def placeholder_rows(keys, facility, client_identified, doc_type_crisis, doc_type_client_profile, staff_user):
    """One row per rotation path whose encrypted value is exactly the placeholder text."""
    with override_settings(ENCRYPTION_KEYS=keys["old"]):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": PLACEHOLDER_TEXT},
            created_by=staff_user,
        )
        history = EventHistory.objects.create(
            event=event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
            data_before={
                "notiz-krise": encrypt_field(PLACEHOLDER_TEXT),
                "plain-control": encrypt_field("regular text"),
                "list-control": encrypt_field(["a", "b"]),
                "scalar-control": encrypt_field(7),
            },
            data_after={"notiz-krise": encrypt_field(PLACEHOLDER_TEXT)},
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Rotation-Placeholder-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"diagnose": PLACEHOLDER_TEXT},
        )
        rectification = RectificationRequest.objects.create(
            facility=facility,
            target_client=client_identified,
            field_path="pseudonym",
            old_value=encrypt_field(PLACEHOLDER_TEXT),
            requested_by=staff_user,
        )
        note = ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=timezone.localdate(),
            time_filter=None,
            note=encrypt_field(PLACEHOLDER_TEXT),
            author=staff_user,
            updated_by=staff_user,
            **_handover_window(),
        )
        attachment = Attachment.objects.create(
            facility=facility,
            event=event,
            field_template=doc_type_crisis.fields.first().field_template,
            storage_filename="placeholder-name.enc",
            original_filename_encrypted=encrypt_field(PLACEHOLDER_TEXT),
            file_size=1,
            mime_type="application/pdf",
            created_by=staff_user,
        )
    return {
        "event": event,
        "history": history,
        "client": person,
        "rectification": rectification,
        "note": note,
        "attachment": attachment,
    }


def _stored_tokens(rows):
    rows["rectification"].refresh_from_db()
    rows["note"].refresh_from_db()
    rows["attachment"].refresh_from_db()
    before, after = raw_history_payloads(rows["history"])
    return {
        "event": raw_event_data_json(rows["event"])["notiz-krise"],
        "history_before": before["notiz-krise"],
        "history_after": after["notiz-krise"],
        "client": raw_client_data_json(rows["client"])["diagnose"],
        "rectification": rows["rectification"].old_value,
        "note": rows["note"].note,
        "attachment": rows["attachment"].original_filename_encrypted,
    }


ROTATED_COUNTERS = {
    "events": 1,
    "event_history": 1,
    "clients": 1,
    "rectification_requests": 1,
    "shift_handover_notes": 1,
    "attachments": 1,
}


def test_placeholder_text_is_rotated_and_readable_with_new_key_only(keys, placeholder_rows):
    tokens_before = _stored_tokens(placeholder_rows)

    with override_settings(ENCRYPTION_KEYS=f"{keys['new']},{keys['old']}"):
        counts = rotate_all(dry_run=False)

    assert {name: counts[name] for name in ROTATED_COUNTERS} == ROTATED_COUNTERS
    assert counts["contended"] == 0
    tokens_after = _stored_tokens(placeholder_rows)
    with override_settings(ENCRYPTION_KEYS=keys["new"]):
        for path, token in tokens_after.items():
            assert token["value"] != tokens_before[path]["value"], path
            assert decrypt_field(token) == PLACEHOLDER_TEXT, path
        history_before, _ = raw_history_payloads(placeholder_rows["history"])
        assert decrypt_field(history_before["plain-control"]) == "regular text"
        assert decrypt_field(history_before["list-control"]) == ["a", "b"]
        assert decrypt_field(history_before["scalar-control"]) == 7


def test_dry_run_counts_placeholder_text_as_rotatable_without_writing(keys, placeholder_rows):
    tokens_before = _stored_tokens(placeholder_rows)

    with override_settings(ENCRYPTION_KEYS=f"{keys['new']},{keys['old']}"):
        counts = rotate_all(dry_run=True)

    assert {name: counts[name] for name in ROTATED_COUNTERS} == ROTATED_COUNTERS
    assert _stored_tokens(placeholder_rows) == tokens_before


def test_corrupt_tokens_are_skipped_without_placeholder_writes(
    keys, facility, client_identified, doc_type_crisis, staff_user
):
    with override_settings(ENCRYPTION_KEYS=keys["old"]):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        history = EventHistory.objects.create(
            event=event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
            data_before={"notiz-krise": CORRUPT_TOKEN},
            data_after={"notiz-krise": CORRUPT_TOKEN},
        )
        note = ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=timezone.localdate(),
            time_filter=None,
            note=CORRUPT_TOKEN,
            author=staff_user,
            updated_by=staff_user,
            **_handover_window(),
        )

    with override_settings(ENCRYPTION_KEYS=f"{keys['new']},{keys['old']}"):
        counts = rotate_all(dry_run=False)

    assert counts["event_history"] == 0
    assert counts["shift_handover_notes"] == 0
    assert raw_history_payloads(history) == ({"notiz-krise": CORRUPT_TOKEN}, {"notiz-krise": CORRUPT_TOKEN})
    note.refresh_from_db()
    assert note.note == CORRUPT_TOKEN


def test_missing_key_configuration_still_fails_loudly(keys, facility, staff_user):
    with override_settings(ENCRYPTION_KEYS=keys["old"]):
        ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=timezone.localdate(),
            time_filter=None,
            note=encrypt_field(PLACEHOLDER_TEXT),
            author=staff_user,
            updated_by=staff_user,
            **_handover_window(),
        )

    with override_settings(ENCRYPTION_KEYS=""), pytest.raises(EncryptionKeyMissing):
        rotate_all(dry_run=False)
