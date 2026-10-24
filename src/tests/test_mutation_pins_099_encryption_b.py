"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.file_vault.encryption``.

Quelle: Vollmutation #2174, Batch ``encryption-b``
(``welle-l-0.99/reports/m-scratch/batch-encryption-b.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-encryption-b/triage-099-encryption-b.json``). Die Batch hat
8 Survivor-Mutanten in ``_reset_fernet_cache`` (2), ``decrypt_field`` (3), ``generate_key`` (1),
``_file_binding`` (1) und ``_chunk_context`` (1). Vier davon sind reine Codec-Namen-Case-Varianten
(``"utf-8"`` -> ``"UTF-8"``) — Python-Codec-Lookup ist case-insensitiv, ``encode``/``decode``
liefern byteidentische Ergebnisse, daher ``equivalent`` (siehe Bericht, kein Pin). Ein weiterer
(``decrypt_field__mutmut_13``, Fehlermeldung -> ``None``) ist ``skip`` (Text fliesst nirgends in
Hash/Vergleich). Ein anderes Batch (``encryption-a``, parallel) behandelt die restlichen
Survivor-Mutanten desselben Moduls — diese Datei behandelt ausschliesslich die 3
``test``-klassifizierten Survivors der Batch ``encryption-b``.

Zwei Themen:

* **``_reset_fernet_cache`` — cache invalidation after key rotation.** ``get_fernet()`` is
  cached process-wide with ``lru_cache(maxsize=1)``; the ``setting_changed`` signal receiver
  ``_reset_fernet_cache`` must clear the cache on every change of ``ENCRYPTION_KEYS``, otherwise
  the OLD ``MultiFernet`` stays active after a key rotation and a key that was already removed
  keeps decrypting (key mix-up). ``mutmut_7``/``mutmut_8`` mangle the ``ENCRYPTION_KEYS`` name the
  receiver compares against. The pin changes ``ENCRYPTION_KEYS`` through plain ``settings``
  assignments (each assignment fires ``setting_changed`` exactly once for exactly this name, see
  ``pytest_django.fixtures.Settings.__setattr__``).
* **``_chunk_context`` — Header-Feldbreite bleibt UNSIGNED.** Der Datei-Header dokumentiert
  ``chunk_index``/``total`` explizit als 4-Byte-UNSIGNED-Felder (Kommentar Zeile 204-211,
  ``_HEADER_LEN``/``_CHUNK_RECORD_MIN_BYTES``). ``mutmut_9`` tauscht das Pack-Format ``">II"``
  gegen das SIGNIERTE ``">ii"``: fuer Werte unter ``2**31`` sind die gepackten Bytes identisch
  (gleiches Zweierkomplement-Bitmuster), ab ``2**31`` wirft das signierte Format ``struct.error``,
  waehrend das spezifizierte unsignierte Format weiterhin gueltig packt — ein an der Funktion
  direkt beobachtbarer Unterschied, kein ``equivalent``.

Alle Exakt-Vergleiche (nie Teilstring/``pytest.raises(match=…)``) — sonst uebersieht der Test den
mutmut-Marker ``XX…XX``, weil der Original-Wortlaut als Teilstring in der markierten Variante
weiterhin vorkommt (Lehre aus fruehen Batches dieser Testreihe).
"""

from __future__ import annotations

import pytest

from core.services.file_vault import EncryptionError, decrypt_field, encrypt_field, generate_key, get_fernet
from core.services.file_vault.encryption import _chunk_context

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# _reset_fernet_cache — ENCRYPTION_KEYS-only-Rotation muss den Cache leeren
# ---------------------------------------------------------------------------


def test_encryption_keys_only_rotation_invalidates_fernet_cache(settings):
    """Pins ``x__reset_fernet_cache__mutmut_7`` (``ENCRYPTION_KEYS`` -> ``XXENCRYPTION_KEYSXX``)
    and ``x__reset_fernet_cache__mutmut_8`` (``ENCRYPTION_KEYS`` -> ``encryption_keys``; setting
    names are case-sensitive): changing ``ENCRYPTION_KEYS`` must clear the ``get_fernet()``
    cache. Without this invalidation the OLD ``MultiFernet`` stays active after a rotation and a
    key that was removed from the ring keeps decrypting without complaint."""
    get_fernet.cache_clear()
    key1 = generate_key()
    key2 = generate_key()

    # Single assignment: fires setting_changed(setting="ENCRYPTION_KEYS") once and
    # populates the cache with key1 on the next call.
    settings.ENCRYPTION_KEYS = key1
    encrypted_with_key1 = encrypt_field("Testinhalt")

    # Rotation in its own single assignment: the signal alone must drop the cached key1 ring.
    settings.ENCRYPTION_KEYS = key2

    with pytest.raises(EncryptionError):
        decrypt_field(encrypted_with_key1)


# ---------------------------------------------------------------------------
# _chunk_context — chunk_index/total bleiben UNSIGNED-32-Bit-Felder
# ---------------------------------------------------------------------------


def test_chunk_context_packs_index_and_total_as_unsigned_32bit():
    """Pinnt ``x__chunk_context__mutmut_9`` (``struct.pack(">II", ...)`` -> ``">ii"``,
    unsigned -> signed): der Datei-Header spezifiziert ``chunk_index``/``total`` als
    UNSIGNED-32-Bit-Felder (Kommentar ueber ``_HEADER_LEN``). Ab ``total >= 2**31`` packt das
    unsignierte Format weiterhin gueltig, waehrend das signierte Format ``struct.error``
    wirft — direkt an der Funktion beobachtbar, ohne dass irgendein realer Chunk-Zaehler
    diese Groessenordnung je erreicht."""
    fid16 = b"\x11" * 16
    large_total = 2**31

    ctx = _chunk_context(fid16, 0, large_total)

    assert len(ctx) == 16 + 4 + 4
    assert ctx[:16] == fid16
    assert ctx[16:20] == (0).to_bytes(4, "big")
    assert ctx[20:24] == large_total.to_bytes(4, "big")
