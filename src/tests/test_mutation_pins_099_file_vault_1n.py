"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — ``core.services.file_vault.encryption``
(Nacharbeit Batch ``file_vault-1n``, Familienreview-Widerlegung von ``encryption-a``).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-file_vault-1n.json`` (7 Survivor-Mutanten,
Triage ``welle-l-0.99/reports/m-scratch/pins-file_vault-1n/triage-099-file_vault-1n.json``).
Alle sieben Mutanten waren im Batch ``encryption-a`` faelschlich als ``skip`` eingestuft
(Meldungstext per Teilstring/``match=`` bereits „erreicht") — die Familienreview hat das
widerlegt: die betroffenen ``EncryptionError``-Texte werden real vom Import-Wizard
ausgegeben bzw. von Health-Checks protokolliert, und die ``safe_decrypt``-Logmeldungen
sind der einzige Diagnosetext bei einer T4-Schluesselfehlkonfiguration (Refs #1269) bzw.
einem kaputten Chiffrat. Alle sieben sind hier Klasse ``test``, Prio 3, mit exaktem
(``==``) statt Teilstring-Vergleich gepinnt:

- ``x_safe_decrypt__mutmut_6``: ``logger.error(<zweizeilige Meldung>)`` -> ``None``
  (fehlender/fehlkonfigurierter Schluessel, T4-Fall).
- ``x_safe_decrypt__mutmut_13``: ``logger.warning("Decryption failed …")`` -> ``None``
  (kaputtes Chiffrat, Fallback auf den Platzhalter).
- ``x_decrypt_file_stream__mutmut_13``: ``EncryptionError("Empty encrypted file")`` per
  XX-Marker gewrapt.
- ``x_decrypt_file_stream__mutmut_67``: ``EncryptionError("Chunk binding mismatch —
  empty bound file …")`` per XX-Marker gewrapt (leeres, gebundenes v2-File).
- ``x_decrypt_file_stream__mutmut_93``: ``EncryptionError("Truncated encrypted file")``
  per XX-Marker gewrapt (Token-Koerper-Truncation je Chunk, NICHT die
  Laengenfeld-Truncation aus ``test_mutation_pins_099_file_vault_1.py``).
- ``x_decrypt_file_stream__mutmut_117``: ``EncryptionError("Chunk binding mismatch —
  possible reorder/truncation/splicing")`` per XX-Marker gewrapt (v2-Bindungspruefung).
- ``x_encrypt_field__mutmut_18``: ``EncryptionError("Cannot encrypt field: …")`` per
  XX-Marker gewrapt (fehlender Schluessel beim Verschluesseln).

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen ``ENCRYPTION_KEY``;
ohne die Fixture waeren die Pins dort aus dem FALSCHEN Grund rot.

Zusicherungen EXAKT (``==``), nie per Teilstring/``match=`` (mutmut erzeugt
``"XX<text>XX"``-Varianten, die eine Teilstring-Pruefung uebersehen wuerde).
"""

from __future__ import annotations

import io
import logging
import struct

import pytest

from core.services.file_vault import (
    CHUNK_SIZE,
    EncryptionError,
    EncryptionKeyMissing,
    decrypt_file_stream,
    encrypt_field,
    encrypt_file,
    safe_decrypt,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_LOGGER_NAME = "core.services.file_vault.encryption"


# ---------------------------------------------------------------------------
# safe_decrypt — Log-Meldungen (Refs #1269 T4)
# ---------------------------------------------------------------------------


class TestSafeDecryptLogsExactErrorWhenKeyIsMissing:
    """Pin ``x_safe_decrypt__mutmut_6``.

    Refs #1269 (T4): ein fehlender/fehlkonfigurierter Schluessel wird NICHT
    als Platzhalter maskiert, sondern laut gemeldet (Error-Log) und
    durchgereicht. Die Mutante ersetzt das Argument von ``logger.error(...)``
    durch ``None`` — der einzige Diagnosetext fuer diesen Betriebsfall ginge
    verloren.
    """

    def test_safe_decrypt_meldet_fehlenden_schluessel_exakt(self, settings, caplog):
        settings.ENCRYPTION_KEYS = ""
        settings.ENCRYPTION_KEY = ""

        with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
            with pytest.raises(EncryptionKeyMissing):
                safe_decrypt({"__encrypted__": True, "value": "irrelevant-weil-key-fehlt"})

        records = [r for r in caplog.records if r.name == _LOGGER_NAME]
        assert len(records) == 1
        record = records[0]
        assert record.levelno == logging.ERROR
        assert record.getMessage() == (
            "safe_decrypt: Entschlüsselung unmöglich — ENCRYPTION_KEY/ENCRYPTION_KEYS "
            "fehlt oder ist fehlkonfiguriert. Fehler wird durchgereicht statt maskiert."
        )


class TestSafeDecryptLogsExactWarningOnCorruptToken:
    """Pin ``x_safe_decrypt__mutmut_13``.

    Echte Token-Korruption (``InvalidToken`` -> generischer ``EncryptionError``)
    faellt graceful auf den ``default``-Platzhalter zurueck — aber NUR mit
    einer WARNING-Logzeile, die den Grund nennt. Die Mutante ersetzt das
    Argument von ``logger.warning(...)`` durch ``None``.
    """

    def test_safe_decrypt_meldet_kaputtes_chiffrat_exakt(self, caplog):
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            result = safe_decrypt({"__encrypted__": True, "value": "nicht-valides-fernet-token"})

        assert result == "[verschlüsselt]"
        records = [r for r in caplog.records if r.name == _LOGGER_NAME]
        assert len(records) == 1
        record = records[0]
        assert record.levelno == logging.WARNING
        assert record.getMessage() == "Decryption failed (corrupt token) — using fallback placeholder."


# ---------------------------------------------------------------------------
# decrypt_file_stream — EncryptionError-Texte (reale Aufrufer: Import-Wizard,
# Health-Check)
# ---------------------------------------------------------------------------


class TestDecryptFileStreamReportsEmptyFileExactly:
    """Pin ``x_decrypt_file_stream__mutmut_13``.

    Nachstellung von ``test_file_encryption.py::test_empty_encrypted_file_raises_error``
    (dort nur ``match="Empty"``-Teilstring) mit exaktem Textvergleich.
    """

    def test_completely_empty_file_raises_exact_error(self, tmp_path):
        out = tmp_path / "zero.enc"
        out.write_bytes(b"")

        with pytest.raises(EncryptionError) as exc_info:
            list(decrypt_file_stream(out))

        assert str(exc_info.value) == "Empty encrypted file"
        assert exc_info.value.args == ("Empty encrypted file",)


class TestDecryptFileStreamReportsEmptyBoundFileExactly:
    """Pin ``x_decrypt_file_stream__mutmut_67``.

    Nachstellung von ``test_file_encryption.py::test_v2_zero_chunk_count_rejected``
    (dort nur ``match="binding"``-Teilstring) mit exaktem Textvergleich.
    """

    def test_v2_zero_chunk_count_header_raises_exact_error(self, tmp_path):
        out = tmp_path / "v2zero.enc"
        encrypt_file(io.BytesIO(b"x"), out, file_id="file-A")
        with open(out, "wb") as f:
            f.write(struct.pack(">B", 2))  # version 2
            f.write(struct.pack(">I", 0))  # count 0, keine Chunks

        with pytest.raises(EncryptionError) as exc_info:
            list(decrypt_file_stream(out, file_id="file-A"))

        assert str(exc_info.value) == "Chunk binding mismatch — empty bound file (possible truncation/blanking)"


class TestDecryptFileStreamReportsTruncatedTokenBodyExactly:
    """Pin ``x_decrypt_file_stream__mutmut_93``.

    Nachstellung von ``test_file_encryption.py::test_truncated_file_raises_error``
    (dort nur ``match="Truncated"``-Teilstring) mit exaktem Textvergleich — dieser
    Fall trifft gezielt die Token-KOERPER-Truncation (``len(token) < token_len``),
    NICHT die bereits in ``test_mutation_pins_099_file_vault_1.py`` gepinnte
    Laengenfeld-Truncation.
    """

    def test_truncated_token_body_raises_exact_error(self, tmp_path):
        out = tmp_path / "truncated.enc"
        with open(out, "wb") as f:
            f.write(struct.pack(">B", 1))  # version 1
            f.write(struct.pack(">I", 1))  # behauptet 1 Chunk
            f.write(struct.pack(">I", 999))  # behauptet 999 Token-Bytes
            f.write(b"short")  # nur 5 Bytes vorhanden

        with pytest.raises(EncryptionError) as exc_info:
            list(decrypt_file_stream(out))

        assert str(exc_info.value) == "Truncated encrypted file"


class TestDecryptFileStreamReportsChunkReorderExactly:
    """Pin ``x_decrypt_file_stream__mutmut_117``.

    Nachstellung von ``test_file_encryption.py::test_v2_chunk_reorder_rejected``
    (dort nur ``match="binding"``-Teilstring) mit exaktem Textvergleich.
    """

    def test_v2_chunk_reorder_raises_exact_error(self, tmp_path):
        out = tmp_path / "v2reorder.enc"
        data = b"A" * CHUNK_SIZE + b"B" * CHUNK_SIZE + b"C" * 10
        encrypt_file(io.BytesIO(data), out, file_id="file-A")

        with open(out, "rb") as f:
            (version,) = struct.unpack(">B", f.read(1))
            (count,) = struct.unpack(">I", f.read(4))
            tokens = []
            for _ in range(count):
                (tlen,) = struct.unpack(">I", f.read(4))
                tokens.append(f.read(tlen))
        tokens[0], tokens[1] = tokens[1], tokens[0]  # erste beiden Chunks vertauschen
        with open(out, "wb") as f:
            f.write(struct.pack(">B", version))
            f.write(struct.pack(">I", count))
            for tok in tokens:
                f.write(struct.pack(">I", len(tok)))
                f.write(tok)

        with pytest.raises(EncryptionError) as exc_info:
            list(decrypt_file_stream(out, file_id="file-A"))

        assert str(exc_info.value) == "Chunk binding mismatch — possible reorder/truncation/splicing"


# ---------------------------------------------------------------------------
# encrypt_field — EncryptionError-Text bei fehlendem Schluessel
# ---------------------------------------------------------------------------


class TestEncryptFieldReportsMissingKeyExactly:
    """Pin ``x_encrypt_field__mutmut_18``.

    Die Bestandssuite prueft nur per ``pytest.raises(match="no encryption key
    configured")`` (Teilstring — uebersieht die XX-Marker-Variante, da die
    Wortfolge in der Mitte des Satzes unveraendert bleibt); hier exakter
    Vergleich.
    """

    def test_encrypt_field_without_key_raises_exact_error(self, settings):
        settings.ENCRYPTION_KEYS = ""
        settings.ENCRYPTION_KEY = ""

        with pytest.raises(EncryptionError) as exc_info:
            encrypt_field("Klartext, der nie verschluesselt wird")

        assert str(exc_info.value) == (
            "Cannot encrypt field: no encryption key configured. Set ENCRYPTION_KEY in environment."
        )
