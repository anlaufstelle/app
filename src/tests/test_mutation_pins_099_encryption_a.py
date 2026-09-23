"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.file_vault.encryption`` (Batch a).

Quelle: Vollmutation #2174, Batch ``encryption-a``
(``welle-l-0.99/reports/m-scratch/batch-encryption-a.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-encryption-a/triage-099-encryption-a.json``).
69 Survivor-Mutanten in fünf Funktionen: ``encrypt_file`` (29),
``decrypt_file_stream`` (18), ``safe_decrypt`` (11), ``_get_keys`` (6),
``encrypt_field`` (5). Die restlichen Funktionen des Moduls (``decrypt_field``,
``is_encrypted_value``, ``get_fernet``, ``_reset_fernet_cache``,
``encrypt_dynamic_data``, ``generate_key``, ``_file_binding``,
``_chunk_context``) liegen im Batch ``encryption-b`` und werden hier NICHT
gepinnt.

**Nicht gepinnt (``skip``, 23 Mutanten) — reine Text-/Log-Varianten ohne
Leser:**

- ``safe_decrypt`` (alle 11 Survivor: ``__mutmut_6``–``__mutmut_16``): jede
  Mutante ändert ausschließlich das Argument von ``logger.error(...)``/
  ``logger.warning(...)`` (``XX…XX``-Marker, Groß-/Kleinschreibung oder
  ``None``). Kein Aufrufer und kein Test liest den Log-TEXT (die
  Bestandssuite prüft nur ``levelno >= ERROR``); der ``raise``- bzw.
  ``return default``-Kontrollfluss bleibt in jeder Variante identisch.
- ``encrypt_field.__mutmut_18``/``__mutmut_19``: die Meldung von
  ``EncryptionError("Cannot encrypt field: …")`` wird per ``XX…XX``-Marker
  bzw. Groß-/Kleinschreibung verändert. Die Bestandssuite prüft nur per
  ``pytest.raises(match="no encryption key configured")`` (Teilstring —
  übersieht beide Varianten, da die Wortfolge in der Mitte des Satzes
  unverändert bleibt); die Fehlerklasse und der Kontrollfluss (fail-closed)
  bleiben unverändert, kein Aufrufer vergleicht den Text exakt.
- ``decrypt_file_stream.__mutmut_13``/``_31``/``_60``/``_67``/``_68``/``_93``/
  ``_104``/``_105``/``_117``/``_118``: reine ``XX…XX``-/Case-Varianten der
  ``EncryptionError``-Meldungen an Stellen, die die Bestandssuite bereits
  über ``pytest.raises(match=…)`` abdeckt (``test_empty_encrypted_file_raises_error``,
  ``test_truncated_chunk_count_raises_encryption_error``,
  ``test_v2_requires_file_id_to_decrypt``, ``test_v2_zero_chunk_count_rejected``,
  ``test_v1_zero_chunk_count_on_bound_file_rejected``,
  ``test_truncated_file_raises_error``, ``test_v2_header_downgraded_to_v1_rejected``,
  ``test_v2_wrong_file_id_rejected``, ``test_v2_chunk_reorder_rejected``,
  ``test_v2_truncation_rejected``, ``test_v2_downgrade_with_rewritten_chunk_count_rejected``).
  Der ``match=``-Teilstring übersteht jede dieser Varianten (das gesuchte
  Wort steht innerhalb der ``XX…XX``-Klammer bzw. ist case-insensitiv nicht
  geprüft); es handelt sich um dieselbe ``EncryptionError``-Klasse mit
  identischem Kontrollfluss — kein Hash/Schlüssel/AAD/Validator liest den
  Text.

**Äquivalent (18 Mutanten, je mit Diff-Beleg UND Testlauf unter der Mutante
verifiziert):**

- ``_get_keys.__mutmut_4``/``_7``/``_10``/``_16``/``_19``/``_22``: alle sechs
  verändern nur den ``default``-Parameter von
  ``getattr(settings, "ENCRYPTION_KEYS"/"ENCRYPTION_KEY", …)``. Beide Settings
  werden in ``anlaufstelle/settings/base.py`` UNBEDINGT gesetzt
  (``os.environ.get(…, "")``, Zeilen 505/506) und jede vererbte Settings-Datei
  (``dev``/``test``/``prod``/``devlive``/``e2e``) importiert ``base`` — der
  ``getattr``-Default wird demnach in KEINEM real existierenden
  Settings-Modul je verwendet (das Attribut existiert immer), ob der Default
  ``""``, ``None``, ``"XXXX"`` oder ganz entfernt ist, ändert den
  Rückgabewert von ``_get_keys()`` nie.
- ``encrypt_field.__mutmut_9``/``_11``: ``.encode("utf-8")``/``.decode("utf-8")``
  → ``"UTF-8"``. Python-Codec-Namen sind case-insensitiv
  (``codecs.lookup("UTF-8") == codecs.lookup("utf-8")``) — die Klassifikationsregel
  „Codec-Namen-Case" ist explizit als äquivalent benannt.
- ``encrypt_file.__mutmut_18``/``_109``: dieselbe Codec-Case-Äquivalenz für
  die beiden ``str``→``bytes``-Konvertierungen in ``encrypt_file``.
- ``encrypt_file.__mutmut_30``/``_31``/``_32``: ``_write_token``s
  ``else``-Zweig (``token.encode(...)``) ist toter Code — ``token`` stammt
  IMMER aus ``_encrypt_chunk()`` bzw. direkt aus ``f.encrypt(...)``
  (``MultiFernet``/``Fernet.encrypt()`` liefert laut ``cryptography``-Bibliothek
  garantiert ``bytes``), der ``isinstance(token, bytes)``-Zweig trifft daher
  in jedem tatsächlichen Aufruf zu — der ``else``-Zweig (und jede Mutation
  darin) ist für keinen Aufrufer erreichbar.
- ``encrypt_file.__mutmut_39``/``_79``/``_86``/``_132``/``_139``: ``struct.pack``
  mit vorzeichenlosem (``">I"``/``">B"``) statt vorzeichenbehaftetem Format
  (``">i"``/``">b"``). Für jeden nichtnegativen Wert < 2³¹ (Format-Version
  1/2, Chunk-Anzahl, Token-Länge — bei ``CHUNK_SIZE`` = 64 KiB astronomisch
  weit unterhalb dieser Grenze) erzeugen beide Formate byteidentische Ausgabe
  (dasselbe Zweierkomplement-Bitmuster ohne gesetztes Vorzeichenbit); ein
  Unterschied wäre nur bei Werten ≥ 2³¹ beobachtbar (~137 TB Einzeldatei bzw.
  Versionsbyte ≥ 128), die kein realer Aufrufer je erzeugt.

**Korrigiert (waren fälschlich ``equivalent``, jetzt ``test``/Prio 2):**
``decrypt_file_stream.__mutmut_22`` (Versionsbyte-Parsing) und
``__mutmut_49`` (``max_chunks``-Division) — in BEIDEN Fällen bleibt die
Annahme-/Ablehnungsentscheidung selbst zwar gleich, aber der jeweils in die
Fehlermeldung eingebettete WERT unterscheidet sich beobachtbar (negative
Zahl bzw. nicht-ganzzahliger Float statt der ursprünglichen Ganzzahl) —
siehe die jeweiligen Pin-Klassen oben.

Zusicherungen EXAKT (``==``/``is``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die eine ``in``-Prüfung bzw. ``pytest.raises(match=…)``
übersehen würde).
"""

from __future__ import annotations

import io
import struct

import pytest

from core.services.file_vault import (
    CHUNK_SIZE,
    EncryptionError,
    decrypt_file_stream,
    encrypt_field,
    encrypt_file,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# encrypt_field
# ---------------------------------------------------------------------------


class TestEncryptFieldWrapsUnexpectedErrorsWithMessage:
    """Pin ``xǁencrypt_fieldǁ__mutmut_21``.

    Der zweite ``except``-Zweig von ``encrypt_field`` verpackt JEDE
    unerwartete Exception in eine ``EncryptionError`` MIT dem Original-Text
    (``f"Encryption failed: {exc}"``). Die Mutante ersetzt das
    formatierte Argument durch ``None`` — die Meldung würde diagnoselos
    ``"Encryption failed: None"`` statt des tatsächlichen Fehlers.
    """

    def test_generic_encryption_failure_message_contains_original_error(self, monkeypatch):
        class _BoomFernet:
            def encrypt(self, *args, **kwargs):
                raise ValueError("boom-vom-key-backend")

        def _boom_get_fernet():
            return _BoomFernet()

        # ``get_fernet`` ist real ``lru_cache``-dekoriert; ``_reset_fernet_cache``
        # (Signal-Receiver auf ``setting_changed``) ruft beim Fixture-Teardown
        # der ``_encryption_key``-Fixture ``get_fernet.cache_clear()`` auf —
        # der Ersatz braucht dieselbe Schnittstelle, sonst schlaegt die
        # Teardown-Reihenfolge unabhaengig vom eigentlichen Testinhalt fehl.
        _boom_get_fernet.cache_clear = lambda: None
        monkeypatch.setattr("core.services.file_vault.encryption.get_fernet", _boom_get_fernet)

        with pytest.raises(EncryptionError) as excinfo:
            encrypt_field("Klartext")

        assert str(excinfo.value) == "Encryption failed: boom-vom-key-backend"


# ---------------------------------------------------------------------------
# decrypt_file_stream — Chunk-Grenzen-Plausibilisierung (Refs #1968)
# ---------------------------------------------------------------------------


class TestImplausibleChunkCountOnTinyHeaderOnlyFile:
    """Pins ``xǁdecrypt_file_streamǁ__mutmut_48``/``_50``.

    ``max_chunks = max(0, (file_size - _HEADER_LEN) // _CHUNK_RECORD_MIN_BYTES)``
    ist die Plausibilisierungsgrenze gegen einen angreifer-kontrollierten
    ``chunk_count``-Header (Refs #1968). Für eine Datei, die NUR aus dem
    5-Byte-Header besteht (keine Chunk-Daten), muss ``max_chunks`` exakt
    ``0`` sein — jeder behauptete Chunk ist per Definition unplausibel.

    - ``__mutmut_48`` erzwingt eine Untergrenze von ``1`` (``max(1, …)``
      statt ``max(0, …)``) — ein einzelner behaupteter Chunk würde die
      Plausibilisierung fälschlich passieren.
    - ``__mutmut_50`` vertauscht ``-`` gegen ``+`` (``file_size + _HEADER_LEN``)
      und bläht ``max_chunks`` massiv auf — dieselbe Datei würde sogar
      ``chunk_count=2`` als plausibel durchlassen.

    Beide Mutanten weichen die Integritätsprüfung gegen manipulierte Header
    auf (Refs #1968-Zusage: Header-Längenfelder werden VOR jeder
    Leseanforderung plausibilisiert) — in beiden Fällen wird die exakte
    Fehlermeldung ``Implausible chunk count in header: …`` durch eine
    andere Fehlerklasse ersetzt (Truncated statt Implausible), weil die
    Prüfung an dieser Stelle nicht mehr greift.
    """

    def test_single_claimed_chunk_on_header_only_file_is_implausible(self, tmp_path):
        out = tmp_path / "tiny_header_only.enc"
        with open(out, "wb") as f:
            f.write(struct.pack(">B", 1))  # Version 1
            f.write(struct.pack(">I", 1))  # behauptet 1 Chunk
            # KEINE weiteren Bytes — Datei ist exakt der 5-Byte-Header.

        with pytest.raises(EncryptionError) as excinfo:
            list(decrypt_file_stream(out))

        assert str(excinfo.value) == "Implausible chunk count in header: 1 exceeds 0 possible chunks in 5 bytes"


class TestMaxChunksBoundInErrorMessageStaysIntegerValued:
    """Pin ``xǁdecrypt_file_streamǁ__mutmut_49``.

    ``max_chunks`` fließt direkt in die Fehlermeldung ein
    (``f"... exceeds {max_chunks} possible chunks..."``). Die Mutante
    ersetzt die Floor-Division (``//``) durch echte Division (``/``): die
    Annahme-/Ablehnungsentscheidung selbst bleibt für jeden ganzzahligen
    ``chunk_count`` identisch (mathematisch: ``c > floor(x) ⟺ c > x``), aber
    der in der Meldung AUSGEGEBENE Grenzwert wird zu einem
    nicht-ganzzahligen Float (``1.5`` statt ``1``), sobald
    ``file_size - _HEADER_LEN`` nicht durch ``_CHUNK_RECORD_MIN_BYTES``
    teilbar ist — ein Zahlformat-Unterschied im beobachtbaren Fehlertext,
    exakt vergleichbar.
    """

    def test_bound_in_implausible_chunk_count_message_is_an_integer(self, tmp_path):
        out = tmp_path / "eleven_bytes.enc"
        with open(out, "wb") as f:
            f.write(struct.pack(">B", 1))  # Version 1
            f.write(struct.pack(">I", 2))  # behauptet 2 Chunks
            f.write(b"\x00" * 6)  # 6 Extra-Bytes: (11-5)/4 = 1,5 -- nicht ganzzahlig

        with pytest.raises(EncryptionError) as excinfo:
            list(decrypt_file_stream(out))

        assert str(excinfo.value) == "Implausible chunk count in header: 2 exceeds 1 possible chunks in 11 bytes"


# ---------------------------------------------------------------------------
# decrypt_file_stream — per-Chunk-Längenfeld truncated (bisher ungetestet)
# ---------------------------------------------------------------------------


class TestPerChunkLengthFieldTruncationIsReportedExactly:
    """Pins ``xǁdecrypt_file_streamǁ__mutmut_76``/``_77``/``_78``/``_79``.

    Die Bestandssuite testet Truncation nur auf Ebene des
    ``chunk_count``-Headerfelds (``test_truncated_chunk_count_raises_encryption_error``)
    und auf Ebene des Token-KÖRPERS nach einem VOLLSTÄNDIGEN 4-Byte-Längenfeld
    (``test_truncated_file_raises_error``). Der Zweig, der eine abgeschnittene
    per-Chunk-LÄNGE selbst (``len(len_bytes) < 4`` INNERHALB der
    Chunk-Schleife) behandelt, hat KEINE eigene Testabdeckung — dieser Test
    schließt die Lücke UND prüft die Meldung exakt (nicht per Teilstring),
    damit ``None``/``XX…XX``/Case-Varianten an dieser Stelle nicht mehr
    unbemerkt durchrutschen.
    """

    def test_length_field_cut_short_inside_chunk_loop_raises_exact_error(self, tmp_path):
        # Echte 2-Chunk-Datei erzeugen (erster Chunk bleibt gueltig und
        # entschluesselbar), dann direkt hinter dem ersten Chunk-Body
        # abschneiden — nur 2 der 4 Laengenfeld-Bytes des ZWEITEN Chunks
        # bleiben uebrig. Die Schleife muss den ersten Chunk also
        # erfolgreich durchlaufen, bevor sie auf die Truncation trifft.
        data = b"A" * CHUNK_SIZE + b"B" * 5
        src = tmp_path / "src.enc"
        encrypt_file(io.BytesIO(data), src)
        content = src.read_bytes()

        version_and_count = content[:5]
        (first_token_len,) = struct.unpack(">I", content[5:9])
        first_chunk_record = content[5 : 9 + first_token_len]
        truncated = version_and_count + first_chunk_record + b"\x00\x00"

        out = tmp_path / "chunk_length_truncated.enc"
        out.write_bytes(truncated)

        with pytest.raises(EncryptionError) as excinfo:
            list(decrypt_file_stream(out))

        assert str(excinfo.value) == "Truncated encrypted file"


class TestUnsupportedVersionByteIsParsedAsUnsigned:
    """Pin ``xǁdecrypt_file_streamǁ__mutmut_22``.

    Das Versionsbyte wird vorzeichenlos gelesen
    (``struct.unpack(">B", version_bytes)``). Für Bytes ``0x80``–``0xff``
    (≥ 128) liefert eine vorzeichenbehaftete Lesart (``">b"``) einen
    ANDEREN, negativen Wert — die Annahme-/Ablehnungsentscheidung ändert
    sich zwar nicht (beide Werte sind ``not in (1, 2)``), aber die in der
    Fehlermeldung eingebettete Zahl unterscheidet sich beobachtbar
    (``"128"`` vs. ``"-128"``). Exakter Vergleich (kein ``match=``-Teilstring
    bzw. ``in``-Prüfung): ``"128"`` ist als Teilstring in ``"-128"``
    enthalten und würde einen laxeren Vergleich nicht unterscheiden.
    """

    def test_high_bit_version_byte_is_reported_as_unsigned_128(self, tmp_path):
        out = tmp_path / "version_0x80.enc"
        out.write_bytes(b"\x80")

        with pytest.raises(EncryptionError) as excinfo:
            list(decrypt_file_stream(out))

        assert str(excinfo.value) == "Unsupported file format version: 128"
        assert excinfo.value.args == ("Unsupported file format version: 128",)


# ---------------------------------------------------------------------------
# encrypt_file — str-Chunks werden vor der Verschluesselung UTF-8-kodiert
# ---------------------------------------------------------------------------

#: Bewusst kurz gehalten (weit unter ``CHUNK_SIZE`` Zeichen): ``StringIO``/
#: der Text-Fallback-Stream lesen ``CHUNK_SIZE`` ZEICHEN pro ``read()``, nicht
#: Bytes — bei mehrbytigen UTF-8-Zeichen (Umlaute) wuerde ein CHUNK_SIZE-Zeichen-
#: Chunk nach der Kodierung ``MAX_CHUNK_TOKEN_BYTES`` sprengen. Das waere ein
#: Artefakt dieses Tests, kein Produktionsfehler — deshalb absichtlich kurz.
_TEXT_PAYLOAD = "Ünïcödé Text, mehrfach wiederholt. " * 50


class TestSeekablePathEncodesStrChunksToUtf8:
    """Pins ``xǁencrypt_fileǁ__mutmut_15``/``_16``/``_17``.

    Der seekbare (Zwei-Pass-)Pfad akzeptiert laut
    ``if isinstance(data, str): data = data.encode("utf-8")`` auch
    ``str``-liefernde Streams (z. B. ``io.StringIO``, real genutzt für
    Text-Uploads). Die Mutanten ersetzen die Kodierung durch ``None``,
    ``.encode(None)`` bzw. eine ungültige Codec-Zeichenkette — alle drei
    lassen ``encrypt_file`` mit einer Exception abstürzen statt den Text
    korrekt zu verschlüsseln.
    """

    def test_stringio_input_roundtrips_as_utf8_bytes(self, tmp_path):
        out = tmp_path / "seekable_text.enc"
        encrypt_file(io.StringIO(_TEXT_PAYLOAD), out)
        assert b"".join(decrypt_file_stream(out)) == _TEXT_PAYLOAD.encode("utf-8")


class TestFallbackPathEncodesStrChunksToUtf8:
    """Pins ``xǁencrypt_fileǁ__mutmut_106``/``_107``/``_108``.

    Dieselbe ``str``→``bytes``-Kodierung existiert ein zweites Mal im
    Fallback-Pfad (nicht-seekbarer Stream, volle Pufferung). Ein
    eigenständiger nicht-seekbarer Text-Stream (Muster
    ``test_file_encryption.py::TestFileEncryption::test_encrypt_non_seekable_stream_falls_back_and_roundtrips``,
    hier mit ``str`` statt ``bytes``) erreicht gezielt diesen zweiten
    Kodierungsort.
    """

    class _NonSeekableText:
        def __init__(self, text: str) -> None:
            self._buf = io.StringIO(text)

        def read(self, n: int = -1) -> str:
            return self._buf.read(n)

        # Absichtlich KEIN seek/seekable → erzwingt den Fallback-Pfad.

    def test_non_seekable_text_stream_roundtrips_as_utf8_bytes(self, tmp_path):
        out = tmp_path / "fallback_text.enc"
        encrypt_file(self._NonSeekableText(_TEXT_PAYLOAD), out)
        assert b"".join(decrypt_file_stream(out)) == _TEXT_PAYLOAD.encode("utf-8")


# ---------------------------------------------------------------------------
# encrypt_file — Seekbarkeits-Erkennung (Refs #1268 Peak-RAM-Invariante)
# ---------------------------------------------------------------------------


class TestSeekableStreamUsesTwoPassStreamingAndCallsSeek:
    """Pins ``xǁencrypt_fileǁ__mutmut_41``/``_43``/``_47``/``_48``/``_49``.

    ``encrypt_file`` erkennt einen seekbaren Stream und nutzt dafür den
    Zwei-Pass-Pfad (Refs #1268: Peak-RAM O(CHUNK) statt O(Dateigröße)) —
    erkennbar daran, dass nach Pass 1 (Zählen) ``input_stream.seek(start)``
    aufgerufen wird, um für Pass 2 zurückzuspulen. Alle fünf Mutanten
    verfälschen die ``seekable``-Erkennung so, dass ein GENUIN seekbarer
    Stream (``io.BytesIO``) fälschlich als NICHT seekbar behandelt wird
    (``seekable = None`` bzw. ``hasattr`` auf ``None``/einen falschen
    Attributnamen) — der Zwei-Pass-Pfad wird dann NIE betreten und
    ``.seek()`` NIE aufgerufen, obwohl der Stream es unterstützt.
    """

    class _SeekSpy(io.BytesIO):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.seek_calls = 0

        def seek(self, *args, **kwargs):
            self.seek_calls += 1
            return super().seek(*args, **kwargs)

    def test_genuinely_seekable_stream_is_seeked_back_for_pass_two(self, tmp_path):
        data = b"Q" * (CHUNK_SIZE + 17)
        spy = self._SeekSpy(data)
        out = tmp_path / "seekspy.enc"

        encrypt_file(spy, out)

        assert spy.seek_calls >= 1
        assert b"".join(decrypt_file_stream(out)) == data


class TestStreamReportingNotSeekableIsNeverSeeked:
    """Pins ``xǁencrypt_fileǁ__mutmut_50``/``_51``/``_55``/``_56``.

    Umgekehrter Fall zur Klasse oben: ein Stream, der ``.seek``/``.tell``
    BESITZT, dessen ``seekable()`` aber ausdrücklich ``False`` liefert (Muster
    ``sys.stdin.buffer`` in einer Pipe). Die Original-Bedingung
    ``not hasattr(input_stream, "seekable") or input_stream.seekable()``
    ruft in diesem Fall TATSÄCHLICH ``input_stream.seekable()`` auf und
    respektiert dessen ``False`` — der Fallback-Pfad wird genutzt, ``.seek()``
    bleibt ungerufen. Alle vier Mutanten ersetzen entweder das ``hasattr``-Objekt
    durch ``None`` oder den geprüften Attributnamen durch eine nicht
    existierende Zeichenkette — in jedem Fall kürzt das den ``or``-Ausdruck
    per Kurzschlussauswertung auf ``True``, OHNE ``seekable()`` je
    aufzurufen, und behandelt den Stream fälschlich als seekbar. Das
    ``encrypt_file`` versucht dann ``.seek()``, was hier bewusst mit
    ``OSError`` abbricht — unter dem Original bleibt dieser Aufruf aus.
    """

    class _ClaimsNotSeekable:
        def __init__(self, data: bytes) -> None:
            self._buf = io.BytesIO(data)

        def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

        def tell(self) -> int:
            return self._buf.tell()

        def seek(self, *args, **kwargs):
            raise OSError("seek() darf hier nicht aufgerufen werden — seekable() ist False")

        def seekable(self) -> bool:
            return False

    def test_stream_with_seekable_false_falls_back_without_ever_seeking(self, tmp_path):
        data = b"R" * 200
        out = tmp_path / "claims_not_seekable.enc"

        encrypt_file(self._ClaimsNotSeekable(data), out)

        assert b"".join(decrypt_file_stream(out)) == data


class TestStartPositionDefaultsToZeroWhenTellIsUnavailable:
    """Pins ``xǁencrypt_fileǁ__mutmut_58``/``_59``.

    Wenn ``input_stream.tell()`` mit ``OSError``/``ValueError`` scheitert
    (seekbarer, aber positionsloser Stream), fängt ``encrypt_file`` das ab
    und nimmt ``start = 0`` an, BEVOR Pass 2 mit ``input_stream.seek(start)``
    zurückspult. Die Mutanten ersetzen ``0`` durch ``None`` (Absturz beim
    ``seek(None)``) bzw. ``1`` (Pass 2 spult NICHT an den wahren Anfang
    zurück, sondern überspringt das erste Byte — stiller Datenverlust).
    """

    class _SeekableButTellFails:
        def __init__(self, data: bytes) -> None:
            self._buf = io.BytesIO(data)

        def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

        def seek(self, *args, **kwargs):
            return self._buf.seek(*args, **kwargs)

        def seekable(self) -> bool:
            return True

        def tell(self) -> int:
            raise OSError("tell() wird von diesem Stream-Typ nicht unterstuetzt")

    def test_tell_failure_still_rewinds_to_the_true_start_of_the_stream(self, tmp_path):
        data = b"S" * (CHUNK_SIZE + 5)
        out = tmp_path / "tellfails.enc"

        encrypt_file(self._SeekableButTellFails(data), out)

        assert b"".join(decrypt_file_stream(out)) == data


# ---------------------------------------------------------------------------
# encrypt_file — Fallback-Pfad bindet Chunk-Index/-Anzahl fuer v2 (Refs #1016)
# ---------------------------------------------------------------------------


class TestFallbackPathBindsChunkIndexAndTotalForV2:
    """Pins ``xǁencrypt_fileǁ__mutmut_114``/``_115``.

    Die Bestandssuite kombiniert den nicht-seekbaren Fallback-Pfad
    (``test_encrypt_non_seekable_stream_falls_back_and_roundtrips``) nur mit
    v1 (kein ``file_id``) — die v2-Bindung
    (``_chunk_context(fid16, index, total)``) wird im Fallback-Pfad NIE
    geprüft. Beide Mutanten ersetzen ``index`` bzw. ``total`` im
    ``_encrypt_chunk(i, total, raw_chunks[i])``-Aufruf durch ``None`` —
    ``struct.pack(">II", …)`` verlangt Ganzzahlen und bricht sofort mit
    ``struct.error`` ab, statt den Chunk korrekt zu binden.
    """

    class _NonSeekable:
        def __init__(self, data: bytes) -> None:
            self._buf = io.BytesIO(data)

        def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

        # Absichtlich KEIN seek/seekable → erzwingt den Fallback-Pfad.

    def test_non_seekable_v2_stream_binds_every_chunk_correctly(self, tmp_path):
        data = b"A" * CHUNK_SIZE + b"B" * CHUNK_SIZE + b"C" * 10
        out = tmp_path / "fallback_v2.enc"

        encrypt_file(self._NonSeekable(data), out, file_id="file-A")

        assert b"".join(decrypt_file_stream(out, file_id="file-A")) == data
