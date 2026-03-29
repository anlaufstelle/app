"""Tests for the encryption service and event field encryption."""

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings
from django.utils import timezone

from core.services.encryption import (
    EncryptionError,
    EncryptionKeyMissing,
    decrypt_field,
    encrypt_field,
    generate_key,
    is_encrypted_value,
    safe_decrypt,
)

# ---------------------------------------------------------------------------
# MultiFernet / Key-Rotation Tests
# ---------------------------------------------------------------------------


def test_multifernet_encrypt_decrypt_with_multiple_keys():
    """Encrypting with new key and decrypting with multiple keys works."""
    new_key = generate_key()
    old_key = generate_key()
    keys = f"{new_key},{old_key}"
    with override_settings(ENCRYPTION_KEYS=keys, ENCRYPTION_KEY=""):
        encrypted = encrypt_field("Geheim")
        result = decrypt_field(encrypted)
    assert result == "Geheim"


def test_multifernet_decrypt_old_key_data():
    """Data encrypted with the old key can still be decrypted when old key is in the list."""
    old_key = generate_key()
    new_key = generate_key()

    # Encrypt with old key only
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
        encrypted = encrypt_field("Alter Inhalt")

    # Decrypt with new key first, old key second
    keys = f"{new_key},{old_key}"
    with override_settings(ENCRYPTION_KEYS=keys, ENCRYPTION_KEY=""):
        result = decrypt_field(encrypted)
    assert result == "Alter Inhalt"


def test_multifernet_removed_key_makes_data_unreadable():
    """Removing a key from the list makes data encrypted with it unreadable."""
    old_key = generate_key()
    new_key = generate_key()

    # Encrypt with old key
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
        encrypted = encrypt_field("Geheimer Text")

    # Try to decrypt with only the new key (old key removed)
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=new_key):
        result = safe_decrypt(encrypted)
    assert result == "[verschlüsselt]"


def test_encryption_keys_takes_precedence_over_encryption_key():
    """ENCRYPTION_KEYS setting takes precedence over ENCRYPTION_KEY."""
    key_a = generate_key()
    key_b = generate_key()

    # Encrypt with key_a via ENCRYPTION_KEYS
    with override_settings(ENCRYPTION_KEYS=key_a, ENCRYPTION_KEY=key_b):
        encrypted = encrypt_field("Test")

    # Decrypt with key_a only — proves ENCRYPTION_KEYS was used, not ENCRYPTION_KEY
    with override_settings(ENCRYPTION_KEYS=key_a, ENCRYPTION_KEY=""):
        result = decrypt_field(encrypted)
    assert result == "Test"


def test_single_encryption_key_fallback_still_works():
    """Backward compatibility: single ENCRYPTION_KEY still works when ENCRYPTION_KEYS is empty."""
    key = generate_key()
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
        encrypted = encrypt_field("Fallback-Test")
        result = decrypt_field(encrypted)
    assert result == "Fallback-Test"


# ---------------------------------------------------------------------------
# Step 1 — Encryption Service Tests
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip():
    """Encrypt then decrypt returns the original plaintext."""
    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        plaintext = "Geheime Notiz"
        encrypted = encrypt_field(plaintext)
        result = decrypt_field(encrypted)
    assert result == plaintext


def test_encrypt_produces_marker():
    """Encrypted value is a dict with __encrypted__: True and a value key."""
    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        encrypted = encrypt_field("Testinhalt")
    assert isinstance(encrypted, dict)
    assert encrypted.get("__encrypted__") is True
    assert "value" in encrypted
    assert isinstance(encrypted["value"], str)


def test_is_encrypted_value_true():
    """is_encrypted_value correctly identifies encrypted values."""
    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        encrypted = encrypt_field("Hallo")
    assert is_encrypted_value(encrypted) is True


def test_is_encrypted_value_false_plain_string():
    """is_encrypted_value returns False for plain strings."""
    assert is_encrypted_value("plain text") is False


def test_is_encrypted_value_false_plain_dict():
    """is_encrypted_value returns False for dicts without the marker."""
    assert is_encrypted_value({"foo": "bar"}) is False


def test_is_encrypted_value_false_missing_value_key():
    """is_encrypted_value returns False if 'value' key is missing."""
    assert is_encrypted_value({"__encrypted__": True}) is False


def test_decrypt_invalid_token_raises():
    """decrypt_field raises EncryptionError when given an invalid token."""
    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        bad_value = {"__encrypted__": True, "value": "not-a-valid-fernet-token"}
        with pytest.raises(EncryptionError):
            decrypt_field(bad_value)


def test_safe_decrypt_returns_fallback():
    """safe_decrypt returns the fallback string instead of raising on error."""
    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        bad_value = {"__encrypted__": True, "value": "invalid-token"}
        result = safe_decrypt(bad_value, default="[Fehler]")
    assert result == "[Fehler]"


def test_safe_decrypt_returns_default_fallback():
    """safe_decrypt uses '[verschlüsselt]' as default fallback."""
    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        bad_value = {"__encrypted__": True, "value": "invalid-token"}
        result = safe_decrypt(bad_value)
    assert result == "[verschlüsselt]"


def test_safe_decrypt_passes_through_plain_value():
    """safe_decrypt returns plain (non-encrypted) values unchanged."""
    assert safe_decrypt("plain") == "plain"
    assert safe_decrypt(42) == 42


def test_missing_key_raises():
    """Empty ENCRYPTION_KEY raises EncryptionKeyMissing."""
    with override_settings(ENCRYPTION_KEY=""):
        with pytest.raises(EncryptionKeyMissing):
            from core.services.encryption import get_fernet

            get_fernet()


def test_encrypt_field_without_key_raises_error():
    """encrypt_field raises EncryptionError when ENCRYPTION_KEY is empty (fail-closed)."""
    with override_settings(ENCRYPTION_KEY=""):
        with pytest.raises(EncryptionError, match="no encryption key configured"):
            encrypt_field("Klartext")


def test_generate_key_format():
    """generate_key returns a valid Fernet key (decodable by Fernet)."""
    key = generate_key()
    assert isinstance(key, str)
    # Fernet will raise ValueError if key is not valid
    fernet = Fernet(key.encode())
    assert fernet is not None


# ---------------------------------------------------------------------------
# Step 2 — Event Field Encryption Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_event_save_encrypts_sensitive_fields(facility, client_identified, doc_type_crisis, staff_user):
    """Event.save() encrypts fields where is_encrypted=True on the FieldTemplate."""
    from core.models import Event

    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Sehr geheime Information"},
            created_by=staff_user,
        )

    stored_value = event.data_json["notiz-krise"]
    assert is_encrypted_value(stored_value), "Sensitive field should be stored encrypted"
    # Verify we can decrypt it back
    with override_settings(ENCRYPTION_KEY=key):
        plaintext = decrypt_field(stored_value)
    assert plaintext == "Sehr geheime Information"


@pytest.mark.django_db
def test_event_save_skips_unencrypted_fields(facility, client_identified, doc_type_contact, staff_user):
    """Non-encrypted fields remain as plain values after save."""
    from core.models import Event

    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 30, "notiz": "Gewöhnliche Notiz"},
            created_by=staff_user,
        )

    assert event.data_json["dauer"] == 30
    assert event.data_json["notiz"] == "Gewöhnliche Notiz"
    assert not is_encrypted_value(event.data_json["dauer"])
    assert not is_encrypted_value(event.data_json["notiz"])


@pytest.mark.django_db
def test_already_encrypted_not_double_encrypted(facility, client_identified, doc_type_crisis, staff_user):
    """Already encrypted values are not re-encrypted on a second save."""
    from core.models import Event

    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Original"},
            created_by=staff_user,
        )

        first_encrypted_value = event.data_json["notiz-krise"]["value"]

        # Save again — should not re-encrypt
        event.save()

    second_encrypted_value = event.data_json["notiz-krise"]["value"]
    assert first_encrypted_value == second_encrypted_value, (
        "Already encrypted value should not be re-encrypted on subsequent save"
    )


@pytest.mark.django_db
def test_event_history_contains_encrypted_data(facility, client_identified, doc_type_crisis, staff_user):
    """EventHistory.data_after contains encrypted values after create_event()."""
    from core.models import EventHistory
    from core.services.event import create_event

    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Vertraulicher Inhalt"},
            client=client_identified,
        )

    history = EventHistory.objects.get(event=event, action=EventHistory.Action.CREATE)
    stored_value = history.data_after["notiz-krise"]
    assert is_encrypted_value(stored_value), "EventHistory.data_after should contain encrypted values"
    with override_settings(ENCRYPTION_KEY=key):
        plaintext = decrypt_field(stored_value)
    assert plaintext == "Vertraulicher Inhalt"


# ---------------------------------------------------------------------------
# Step 3 — Reencrypt Command Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reencrypt_command_roundtrip(facility, client_identified, doc_type_crisis, staff_user):
    """reencrypt_fields command decrypts and re-encrypts without double encryption."""
    from io import StringIO

    from django.core.management import call_command

    from core.models import Event

    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Vertrauliche Notiz"},
            created_by=staff_user,
        )

    # Sanity check: field is encrypted after save
    assert is_encrypted_value(event.data_json["notiz-krise"])

    # Run the reencrypt command
    with override_settings(ENCRYPTION_KEY=key):
        out = StringIO()
        call_command("reencrypt_fields", stdout=out)

    # Reload from DB
    event.refresh_from_db()
    stored = event.data_json["notiz-krise"]

    # Must still be a valid encrypted value (not double-encrypted)
    assert is_encrypted_value(stored), "Field should still be encrypted after reencrypt"

    # Must decrypt back to the original plaintext
    with override_settings(ENCRYPTION_KEY=key):
        plaintext = decrypt_field(stored)
    assert plaintext == "Vertrauliche Notiz", (
        "Reencrypted field should decrypt to original value without double encryption"
    )


@pytest.mark.django_db
def test_reencrypt_command_key_rotation(facility, client_identified, doc_type_crisis, staff_user):
    """reencrypt_fields rotates encrypted data from old key to new key."""
    from io import StringIO

    from django.core.management import call_command

    from core.models import Event

    old_key = generate_key()
    new_key = generate_key()

    # Encrypt with old key
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Rotationstest"},
            created_by=staff_user,
        )

    # Run reencrypt with both keys (new primary, old for decryption)
    keys = f"{new_key},{old_key}"
    with override_settings(ENCRYPTION_KEYS=keys, ENCRYPTION_KEY=""):
        out = StringIO()
        call_command("reencrypt_fields", stdout=out)

    event.refresh_from_db()
    stored = event.data_json["notiz-krise"]

    # Now should be decryptable with new key only
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=new_key):
        plaintext = decrypt_field(stored)
    assert plaintext == "Rotationstest"
