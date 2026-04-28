"""Tests for chunk-based binary file encryption."""

import io
import struct

import pytest
from cryptography.fernet import Fernet

from core.services.encryption import (
    CHUNK_SIZE,
    FILE_FORMAT_VERSION,
    EncryptionError,
    decrypt_file_stream,
    encrypt_file,
)


@pytest.fixture
def _encryption_key(settings):
    settings.ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")
    settings.ENCRYPTION_KEYS = ""


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFileEncryption:
    def test_encrypt_decrypt_small_file(self, tmp_path):
        """Small file (< chunk size) round-trips correctly."""
        data = b"Hello, encrypted world!"
        output = tmp_path / "test.enc"

        encrypt_file(io.BytesIO(data), output)
        assert output.exists()

        result = b"".join(decrypt_file_stream(output))
        assert result == data

    def test_encrypt_decrypt_large_file(self, tmp_path):
        """File spanning multiple chunks round-trips correctly."""
        data = b"X" * (CHUNK_SIZE * 3 + 42)
        output = tmp_path / "large.enc"

        encrypt_file(io.BytesIO(data), output)
        result = b"".join(decrypt_file_stream(output))
        assert result == data

    def test_encrypt_decrypt_empty_file(self, tmp_path):
        """Empty file produces valid encrypted file with 0 chunks."""
        output = tmp_path / "empty.enc"
        encrypt_file(io.BytesIO(b""), output)
        result = b"".join(decrypt_file_stream(output))
        assert result == b""

    def test_file_format_header(self, tmp_path):
        """Encrypted file starts with version byte and chunk count."""
        data = b"test"
        output = tmp_path / "header.enc"
        encrypt_file(io.BytesIO(data), output)

        with open(output, "rb") as f:
            (version,) = struct.unpack(">B", f.read(1))
            (chunk_count,) = struct.unpack(">I", f.read(4))

        assert version == FILE_FORMAT_VERSION
        assert chunk_count == 1

    def test_multiple_chunks_header(self, tmp_path):
        """Multi-chunk file has correct chunk count."""
        data = b"A" * (CHUNK_SIZE * 5)
        output = tmp_path / "multi.enc"
        encrypt_file(io.BytesIO(data), output)

        with open(output, "rb") as f:
            f.read(1)  # version
            (chunk_count,) = struct.unpack(">I", f.read(4))

        assert chunk_count == 5

    def test_key_rotation(self, tmp_path, settings):
        """File encrypted with old key decrypts with new primary + old fallback."""
        old_key = settings.ENCRYPTION_KEY
        output = tmp_path / "rotated.enc"
        data = b"rotation test data"

        encrypt_file(io.BytesIO(data), output)

        # Rotate: new primary key, old key as fallback
        new_key = Fernet.generate_key().decode("utf-8")
        settings.ENCRYPTION_KEYS = f"{new_key},{old_key}"
        settings.ENCRYPTION_KEY = ""

        result = b"".join(decrypt_file_stream(output))
        assert result == data

    def test_corrupted_file_raises_error(self, tmp_path):
        """Corrupted encrypted file raises EncryptionError."""
        output = tmp_path / "corrupt.enc"
        # Write valid header but garbage token
        with open(output, "wb") as f:
            f.write(struct.pack(">B", FILE_FORMAT_VERSION))
            f.write(struct.pack(">I", 1))  # 1 chunk
            garbage = b"not-a-valid-fernet-token"
            f.write(struct.pack(">I", len(garbage)))
            f.write(garbage)

        with pytest.raises(EncryptionError, match="Chunk decryption failed"):
            list(decrypt_file_stream(output))

    def test_truncated_file_raises_error(self, tmp_path):
        """Truncated file raises EncryptionError."""
        output = tmp_path / "truncated.enc"
        with open(output, "wb") as f:
            f.write(struct.pack(">B", FILE_FORMAT_VERSION))
            f.write(struct.pack(">I", 1))  # claims 1 chunk
            f.write(struct.pack(">I", 999))  # claims 999 bytes
            f.write(b"short")  # only 5 bytes

        with pytest.raises(EncryptionError, match="Truncated"):
            list(decrypt_file_stream(output))

    def test_unsupported_version_raises_error(self, tmp_path):
        """Unknown format version raises EncryptionError."""
        output = tmp_path / "badversion.enc"
        with open(output, "wb") as f:
            f.write(struct.pack(">B", 99))  # bad version
            f.write(struct.pack(">I", 0))

        with pytest.raises(EncryptionError, match="Unsupported file format"):
            list(decrypt_file_stream(output))

    def test_empty_encrypted_file_raises_error(self, tmp_path):
        """Completely empty file raises EncryptionError."""
        output = tmp_path / "zero.enc"
        output.write_bytes(b"")

        with pytest.raises(EncryptionError, match="Empty"):
            list(decrypt_file_stream(output))

    def test_creates_parent_directories(self, tmp_path):
        """encrypt_file creates intermediate directories."""
        output = tmp_path / "a" / "b" / "c" / "test.enc"
        encrypt_file(io.BytesIO(b"nested"), output)
        assert output.exists()
        assert b"".join(decrypt_file_stream(output)) == b"nested"
