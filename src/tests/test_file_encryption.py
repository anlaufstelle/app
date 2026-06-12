"""Tests for chunk-based binary file encryption."""

import io
import struct

import pytest
from cryptography.fernet import Fernet

from core.services.file_vault import (
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


def _split_v2(path):
    """Parse a v2 file into (version, chunk_count, [token_bytes])."""
    out = []
    with open(path, "rb") as f:
        (version,) = struct.unpack(">B", f.read(1))
        (count,) = struct.unpack(">I", f.read(4))
        for _ in range(count):
            (tlen,) = struct.unpack(">I", f.read(4))
            out.append(f.read(tlen))
    return version, count, out


def _write_v2(path, version, count, tokens):
    with open(path, "wb") as f:
        f.write(struct.pack(">B", version))
        f.write(struct.pack(">I", count))
        for tok in tokens:
            f.write(struct.pack(">I", len(tok)))
            f.write(tok)


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFileEncryptionV2Binding:
    """A4.5 (Refs #1016): Chunks an file_id + index + total binden.

    Fernet kennt kein AEAD-AAD; der Kontext wird daher in den Klartext jedes
    Chunks gepackt und beim Entschlüsseln gegen die erwartete Position +
    Datei-ID geprüft. Schützt Bestandsdateien gegen Reorder, Truncation und
    Cross-File-Splicing durch einen Angreifer mit Schreibzugriff auf die Disk.
    """

    def test_v2_roundtrip(self, tmp_path):
        out = tmp_path / "v2.enc"
        data = b"Y" * (CHUNK_SIZE * 2 + 7)
        encrypt_file(io.BytesIO(data), out, file_id="file-A")
        assert b"".join(decrypt_file_stream(out, file_id="file-A")) == data

    def test_v2_uses_version_byte_2(self, tmp_path):
        out = tmp_path / "v2ver.enc"
        encrypt_file(io.BytesIO(b"x"), out, file_id="file-A")
        version, _, _ = _split_v2(out)
        assert version == 2

    def test_v2_wrong_file_id_rejected(self, tmp_path):
        out = tmp_path / "v2wrong.enc"
        encrypt_file(io.BytesIO(b"secret"), out, file_id="file-A")
        with pytest.raises(EncryptionError, match="binding"):
            list(decrypt_file_stream(out, file_id="file-B"))

    def test_v2_chunk_reorder_rejected(self, tmp_path):
        out = tmp_path / "v2reorder.enc"
        data = b"A" * CHUNK_SIZE + b"B" * CHUNK_SIZE + b"C" * 10
        encrypt_file(io.BytesIO(data), out, file_id="file-A")
        version, count, tokens = _split_v2(out)
        tokens[0], tokens[1] = tokens[1], tokens[0]  # swap first two chunks
        _write_v2(out, version, count, tokens)
        with pytest.raises(EncryptionError, match="binding"):
            list(decrypt_file_stream(out, file_id="file-A"))

    def test_v2_truncation_rejected(self, tmp_path):
        out = tmp_path / "v2trunc.enc"
        data = b"A" * CHUNK_SIZE + b"B" * CHUNK_SIZE + b"C" * CHUNK_SIZE
        encrypt_file(io.BytesIO(data), out, file_id="file-A")
        version, count, tokens = _split_v2(out)
        _write_v2(out, version, count - 1, tokens[:-1])  # drop last chunk + adjust count
        with pytest.raises(EncryptionError, match="binding"):
            list(decrypt_file_stream(out, file_id="file-A"))

    def test_v2_requires_file_id_to_decrypt(self, tmp_path):
        out = tmp_path / "v2noid.enc"
        encrypt_file(io.BytesIO(b"x"), out, file_id="file-A")
        with pytest.raises(EncryptionError, match="file_id"):
            list(decrypt_file_stream(out))

    def test_v1_still_roundtrips_without_file_id(self, tmp_path):
        """Bestandsformat (kein file_id) bleibt les-/schreibbar (Abwärtskompat)."""
        out = tmp_path / "v1.enc"
        encrypt_file(io.BytesIO(b"legacy"), out)  # no file_id → v1
        version, _, _ = _split_v2(out)
        assert version == 1
        assert b"".join(decrypt_file_stream(out)) == b"legacy"

    def test_v2_zero_chunk_count_rejected(self, tmp_path):
        """[v2][count=0]-Header (Blanking durch 5-Byte-Overwrite) wird abgewiesen
        statt still einen leeren Stream zu liefern. PR-Review bug_002."""
        out = tmp_path / "v2zero.enc"
        encrypt_file(io.BytesIO(b"x"), out, file_id="file-A")
        _write_v2(out, 2, 0, [])  # version 2, count 0, keine Chunks
        with pytest.raises(EncryptionError, match="binding"):
            list(decrypt_file_stream(out, file_id="file-A"))

    def test_v1_zero_chunk_count_on_bound_file_rejected(self, tmp_path):
        """[v1][count=0]-Header auf einer v2-gebundenen Datei wird als Blanking
        abgewiesen statt still einen leeren Stream zu liefern — der gefälschte
        v1-Header darf den v2-only count=0-Guard nicht umgehen (die per-Chunk-
        Downgrade-Erkennung läuft bei 0 Chunks nie). Refs #1069."""
        out = tmp_path / "v1zero.enc"
        encrypt_file(io.BytesIO(b"x"), out, file_id="file-A")
        _write_v2(out, 1, 0, [])  # Header auf [v1][count=0] gefälscht
        with pytest.raises(EncryptionError, match="binding"):
            list(decrypt_file_stream(out, file_id="file-A"))

    def test_v2_header_downgraded_to_v1_rejected(self, tmp_path):
        """v2-Datei mit auf v1 gefälschtem Header-Byte wird erkannt (kein stiller
        Garbage-Stream mit 24-Byte-Kontext-Präfix). PR-Review bug_002."""
        out = tmp_path / "v2down.enc"
        encrypt_file(io.BytesIO(b"X" * 100), out, file_id="file-A")
        version, count, tokens = _split_v2(out)
        assert version == 2
        _write_v2(out, 1, count, tokens)  # Header-Byte 0x02 → 0x01
        with pytest.raises(EncryptionError, match="binding"):
            list(decrypt_file_stream(out, file_id="file-A"))

    def test_legit_v1_with_file_id_not_flagged(self, tmp_path):
        """Echtes v1 (ungebunden) wird auch mit file_id NICHT fälschlich als
        Downgrade markiert — kein False-Positive auf dem Storage-Lesepfad."""
        content = b"echte v1-Nutzdaten, definitiv kein Bindungs-Kontext-Praefix" * 3
        out = tmp_path / "v1fid.enc"
        encrypt_file(io.BytesIO(content), out)  # v1
        assert b"".join(decrypt_file_stream(out, file_id="file-A")) == content
