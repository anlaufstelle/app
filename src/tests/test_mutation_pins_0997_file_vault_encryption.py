"""Pins for field encryption and bounded binary stream contracts.

Source: mutation delta of the release-0.99.7 checkpoint, 13 mutants.
Groups: sensitive values and finite JSON (3), bounded trailing-data reads (2),
unsigned count serialization (2), typed payload diagnostics (6).
"""

import io
import struct
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.file_vault import encryption


@pytest.fixture
def cipher(settings):
    """Use a real, isolated Fernet key for the public encryption APIs."""
    key = Fernet.generate_key()
    settings.ENCRYPTION_KEYS = ""
    settings.ENCRYPTION_KEY = key.decode()
    return Fernet(key)


def test_nonempty_sentinel_is_encrypted_and_empty_text_is_preserved(cipher):
    """Encrypt literal XXXX and preserve empty storage: x_encrypt_field__mutmut_6."""
    stored = encryption.encrypt_field("XXXX")
    assert encryption.is_encrypted_value(stored)
    assert encryption.decrypt_field(stored) == "XXXX"
    assert encryption.encrypt_field("") == ""


@pytest.mark.django_db
def test_sensitive_dynamic_sentinel_is_encrypted(facility, cipher):
    """Encrypt literal XXXX through the public bulk API: x_encrypt_dynamic_data__mutmut_21."""
    document_type = DocumentType.objects.create(facility=facility, name="Sensitive fields")
    field = FieldTemplate.objects.create(
        facility=facility, name="Secret", field_type=FieldTemplate.FieldType.TEXT, is_encrypted=True
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    source = {field.slug: "XXXX"}
    result = encryption.encrypt_dynamic_data(document_type, source)
    assert encryption.is_encrypted_value(result[field.slug])
    assert encryption.decrypt_field(result[field.slug]) == "XXXX"
    assert source == {field.slug: "XXXX"}


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf")], ids=["nan", "infinity", "negative-infinity"]
)
def test_nonfinite_list_values_fail_closed(cipher, value):
    """Reject nonfinite JSON list numbers: x_encrypt_field__mutmut_18."""
    with pytest.raises(encryption.EncryptionError) as error:
        encryption.encrypt_field([value])
    assert isinstance(error.value.__cause__, ValueError)


def test_trailing_data_check_leaves_the_callers_stream_bounded_and_open(cipher, tmp_path):
    """Consume one trailing byte while rejecting extra data on an owned stream.

    x_decrypt_file_stream__mutmut_128, x_decrypt_file_stream__mutmut_129.
    """
    path = tmp_path / "encrypted.bin"
    encryption.encrypt_file(io.BytesIO(b"payload"), path)
    payload_size = path.stat().st_size
    with path.open("ab") as output:
        output.write(b"unexpected trailing bytes")
    with path.open("rb") as stream:
        with pytest.raises(encryption.EncryptionError) as error:
            list(encryption.decrypt_file_stream(stream))
        assert str(error.value) == "Trailing data after encrypted chunks"
        assert not stream.closed
        assert stream.tell() == payload_size + 1
        assert stream.read() == b"nexpected trailing bytes"


@pytest.mark.parametrize("seekable", [True, False], ids=["seekable", "buffered"])
def test_chunk_count_serialization_accepts_the_unsigned_boundary(cipher, tmp_path, monkeypatch, seekable):
    """Serialize a synthetic 2**31 count through both public writer paths.

    x_encrypt_file__mutmut_86, x_encrypt_file__mutmut_139.
    The codec seam injects the boundary count to avoid allocating billions of
    chunks; token lengths and encryption still use the real codec and cipher.
    """
    stream = io.BytesIO(b"payload")
    if not seekable:
        stream = SimpleNamespace(read=stream.read, seekable=lambda: False)
    boundary_injected = False

    def pack(format_string, *values):
        nonlocal boundary_injected
        if not boundary_injected and format_string in (">I", ">i"):
            assert values == (1,)
            values = (2**31,)
            boundary_injected = True
        return struct.pack(format_string, *values)

    monkeypatch.setattr(encryption, "struct", SimpleNamespace(pack=pack))
    path = tmp_path / "unsigned.bin"
    encryption.encrypt_file(stream, path)
    content = path.read_bytes()
    assert boundary_injected
    assert struct.unpack(">BI", content[:5]) == (1, 2**31)
    (token_size,) = struct.unpack(">I", content[5:9])
    assert cipher.decrypt(content[9 : 9 + token_size]) == b"payload"
    assert len(content) == 9 + token_size


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"1:scalar\x00{broken", "Invalid encrypted scalar payload"),
        (b'1:scalar\x00"text"', "Encrypted scalar payload must contain a boolean or integer"),
        (b"2:list\x00[]", "Unsupported encrypted field format"),
        (b"1:list\x00{broken", "Invalid encrypted list payload"),
        (b"1:list\x00{}", "Encrypted list payload must contain a list"),
    ],
    ids=["scalar-json", "scalar-type", "unknown-format", "list-json", "list-type"],
)
def test_invalid_authenticated_payload_retains_its_diagnostic(cipher, payload, message):
    """Retain precise error messages for malformed authenticated field payloads.

    x_decrypt_field__mutmut_22, x_decrypt_field__mutmut_28,
    x_decrypt_field__mutmut_34, x_decrypt_field__mutmut_43, x_decrypt_field__mutmut_48.
    """
    token = cipher.encrypt(b"\x89ANLAUFSTELLE-FIELD\x00" + payload).decode()
    with pytest.raises(encryption.EncryptionError) as error:
        encryption.decrypt_field({"__encrypted__": True, "value": token})
    assert str(error.value) == message


def test_invalid_token_retains_the_decryption_diagnostic(cipher):
    """Retain the public invalid-token diagnostic: x_decrypt_field__mutmut_52."""
    with pytest.raises(encryption.EncryptionError) as error:
        encryption.decrypt_field({"__encrypted__": True, "value": "invalid-token"})
    assert str(error.value) == "Decryption failed: "
