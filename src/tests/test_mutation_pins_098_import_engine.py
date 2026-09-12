import pytest

"""Pin-Tests der Welle K (0.98) — Prio-2-Survivors in ``core.services.import_engine``.

Quelle: Füllauftrag #2193,
Batch ``merge-crud`` (``triage-096-events-import.json``). Gepinnt wird der
zweite Erkennungsweg (RIFF-Container) von
:func:`~core.services.import_engine._binary_kind`
(#2128):

Reale RIFF-Dateien (WebP/WAV/AVI) enthalten fast immer NUL-Bytes und werden
schon vom DRITTEN Erkennungsweg abgewiesen — dann nur mit der unspezifischen
Bezeichnung ``"Binaerdatei"`` statt des konkreten Formats. Eine NUL-FREIE
RIFF-Datei (Groessenfeld ohne Nullbyte, Textrumpf) rutscht beim toten Zweig
dagegen als Textdatei durch den Guard. Kein Bestandstest faehrt den
RIFF-Zweig an (Grep ueber ``src/tests`` leer, wie schon fuer den NUL-Weg in
``test_mutation_pins_096_import_engine.py`` festgestellt).

Konventionen wie ``test_mutation_pins_096_import_engine.py``: Zusicherungen
vergleichen EXAKT (``==``); der Funktionskoerper ist bei allen drei Mutanten
deckungsgleich mit dem Batch-Text (keine Numerierungsdrift in diesem Modul).
"""

from core.services import import_engine

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: RIFF-Header, bewusst NUL-frei: 4 Bytes Signatur + 4 Bytes Groessenfeld ohne
#: Nullbyte + 4 Bytes Formatkennung ("WEBP") + Textrumpf. Damit greift beim
#: Original garantiert der zweite Weg, nicht der dritte (NUL-Byte).
RIFF_WEBP = b"RIFF" + b"\x01\x01\x01\x01" + b"WEBP" + b"text"


class TestBinaryKindRiffContainer:
    """Der RIFF-Zweig ist der einzige, der ein konkretes Bildformat benennt."""

    def test_nul_freies_riff_wird_als_webp_erkannt(self):
        """Pin: ``x__binary_kind__mutmut_3`` (``raw[:4]`` -> ``raw[:5]``, kann
        nie zutreffen), ``_5`` (Vergleichswert ``b"XXRIFFXX"``) und ``_6``
        (Vergleichswert ``b"riff"``) — killt alle drei in einem Zug.

        Abweichung beim Mutanten: die ``if``-Bedingung ist bei JEDER Eingabe
        falsch (Grossschreibung/Laenge stimmen nie), der RIFF-Zweig ist tot —
        die NUL-freie Eingabe faellt durch alle drei Wege und liefert ``None``
        (gilt als Text) statt ``"WEBP"``.
        """
        assert import_engine._binary_kind(RIFF_WEBP) == "WEBP"

    def test_kein_nul_byte_im_riff_kopf_der_gegenprobe(self):
        """Gegenprobe: ohne die bewusst NUL-freie Konstruktion wuerde der
        dritte Erkennungsweg (NUL-Byte) den Pin oben unbemerkt mittragen."""
        assert b"\x00" not in RIFF_WEBP
