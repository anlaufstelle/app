"""Pin-Tests der Welle I (0.96) — Prio-1-Survivor in ``core.services.import_engine``.

Quelle: Survivor-Triage des Mutations-Deltas v0.96.0
(``welle-i-0.96/reports/pin-task-events-import.json``). Gepinnt wird der dritte
und breiteste Erkennungsweg des Binaer-Guards aus
#2128:
:func:`~core.services.import_engine._binary_kind` erkennt Formate OHNE feste
Signatur an einem ``NUL``-Byte irgendwo im Inhalt — laut Docstring
ausdruecklich BMP.

Warum das traegt: ``store_import_upload`` verschluesselt die Rohbytes direkt
(``encrypt_file``) und umgeht damit die File-Vault-Pipeline samt der
Metadaten-Bereinigung aus #2128. Faellt der NUL-Weg aus, wird ein BMP (dessen
2-Byte-Signatur bewusst NICHT in ``_BINARY_SIGNATURES`` steht) wieder als
Textdatei akzeptiert und roh verschluesselt abgelegt — der Bild-Bypass im
zweiten Speicherpfad, den der Guard schliessen soll (EXIF/GPS).

Bislang gab es zu ``_binary_kind``/``_reject_binary_upload`` ueberhaupt keinen
Test (Grep ueber ``src/tests`` leer).

Konventionen wie ``test_mutation_pins_093_import_engine.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten); der Sprachkontext wird bei Texten per
``translation.override("de")`` festgenagelt; Fixtures aus
``src/tests/conftest.py``; KEINE Imports aus ``test_rls_functional``. Jeder
Test nennt seine Mutanten in der Docstring samt dem Wert, der beim Mutanten
abweicht.
"""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import translation

from core.models import ImportJob
from core.services import import_engine

PSEUDONYM_HEADER = "Pseudonym;Kontaktstufe;Altersgruppe;Notizen\n"

#: BMP-Kopf ohne feste Signatur in ``_BINARY_SIGNATURES``: ``BM`` allein waere
#: zu unspezifisch (eine CSV mit der Spalte „BMI"). Erkannt wird er
#: ausschliesslich ueber das ``NUL``-Byte des Headers.
BMP_HEADER = b"BM" + b"\x00" * 40 + b"spalte;wert\n"

MSG_BINARY = "Die Datei ist keine Textdatei (erkannt: Binaerdatei). Bitte exportieren Sie die Daten als CSV."


def _upload(content, name="import.csv"):
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return SimpleUploadedFile(name, raw, content_type="text/csv")


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


class TestBinaryKindNulByte:
    """Der NUL-Weg ist der breiteste der drei Erkennungswege.

    ``b"XX\\x00XX"`` kommt in realen Dateien nicht vor — der Zweig waere damit
    praktisch tot, ohne dass irgendein Bestandstest es merkt.
    """

    def test_bmp_ohne_signatur_wird_am_nul_byte_erkannt(self):
        """Pin: ``x__binary_kind__mutmut_22`` — ``if b"\\x00" in raw`` ->
        ``if b"XX\\x00XX" in raw``.

        Abweichung beim Mutanten: ``_binary_kind`` liefert ``None`` statt
        ``"Binaerdatei"`` — die Datei gilt wieder als Text.
        """
        assert import_engine._binary_kind(BMP_HEADER) == "Binaerdatei"

    def test_reines_nul_byte_am_ende_reicht(self):
        """Pin: ``x__binary_kind__mutmut_22``.

        „Irgendwo im Inhalt" — auch ein einzelnes NUL hinter lauter Text
        (abgeschnittener Binaeranhang) ist kein Tabellenkalkulations-Export.

        Abweichung beim Mutanten: ``None`` statt ``"Binaerdatei"``.
        """
        assert import_engine._binary_kind(b"spalte;wert\nA;1\n\x00") == "Binaerdatei"

    def test_echte_csv_bleibt_text(self):
        """Gegenprobe: NUL-frei -> ``None``. Ohne sie waere der Pin oben auch
        mit einem pauschalen „alles ist binaer" gruen."""
        assert import_engine._binary_kind(PSEUDONYM_HEADER.encode("utf-8") + b"IMP-01;identified;u18;Notiz\n") is None


class TestRejectBinaryUploadMessage:
    def test_meldung_nennt_das_erkannte_format(self):
        """Pin: ``x__binary_kind__mutmut_22`` — ueber
        :func:`_reject_binary_upload`, die den Namen in die Meldung setzt.

        Abweichung beim Mutanten: kein ``ValidationError`` — der Aufruf kehrt
        still zurueck (``kind is None``).
        """
        with translation.override("de"):
            with pytest.raises(ValidationError) as excinfo:
                import_engine._reject_binary_upload(BMP_HEADER)
            assert excinfo.value.messages == [MSG_BINARY]


@pytest.mark.django_db
class TestStoreImportUploadRejectsBinary:
    def test_wizard_einstieg_weist_bmp_ab_und_legt_nichts_ab(self, facility, admin_user, media_root):
        """Pin: ``x__binary_kind__mutmut_22`` am oeffentlichen Einstieg.

        ``store_import_upload`` ruft ``_read_upload`` -> ``_reject_binary_upload``
        VOR jedem Dateischreiben. Der Guard ist genau deshalb hier: dieser Pfad
        verschluesselt die Rohbytes direkt und umgeht die
        Metadaten-Bereinigung aus #2128.

        Abweichung beim Mutanten: kein ``ValidationError``; das BMP laeuft
        durch, ein ``ImportJob`` entsteht und die Rohbytes liegen verschluesselt
        im Import-Ordner.
        """
        with translation.override("de"):
            with pytest.raises(ValidationError) as excinfo:
                import_engine.store_import_upload(
                    facility,
                    admin_user,
                    _upload(BMP_HEADER, name="bild.bmp"),
                    target_type=ImportJob.TargetType.CLIENT,
                )
            assert excinfo.value.messages == [MSG_BINARY]

        assert ImportJob.objects.count() == 0

    def test_echte_csv_laeuft_weiterhin_durch(self, facility, admin_user, media_root):
        """Gegenprobe zum Guard: eine NUL-freie CSV wird unveraendert
        angenommen — der Pin oben darf keinen Totalausfall des Uploads
        festschreiben."""
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER + "IMP-01;identified;u18;Notiz\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        assert job.pk is not None
        assert ImportJob.objects.count() == 1
