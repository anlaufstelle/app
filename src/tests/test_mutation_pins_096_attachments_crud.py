"""Pin-Test der Prio-1-Survivor-Triage zu ``core.services.attachments.crud`` (v0.96.0).

Quelle: ``src/core/services/attachments/crud.py::stage_attachment_uploads``
(Schreibpfade der Anhang-Domaene, M24/AP2, Refs #1738/#1956/#1874/#2129).
Gepinnt wird das Prio-1-Set der Checkpoint-Survivor-Triage v0.96.0: die
laufende Kontingent-Summe (``pending_bytes``) im REPLACE-Zweig.

Refs #2129 (R-L7a-5): REPLACE- und ADD-Uploads EINES Submits zaehlen
gemeinsam gegen das Facility-Kontingent — die Summe ``pending_bytes`` muss
ueber ALLE Ersatz-Uploads eines Aufrufs kumulieren
(``pending_bytes += staged.file_size``), sonst rechnet die
Kontingentpruefung des naechsten Uploads gegen einen zu kleinen Wert und ein
einzelnes Formular kann das Kontingent unbemerkt ueberschreiten.

``test_mutation_pins_094_attachments_crud.py`` pinnt den REPLACE-Zweig nur
mit GENAU EINEM Ersatz-Upload (``pending_bytes=0`` beim einzigen Aufruf) —
das unterscheidet ``+=`` weder von ``=`` noch von ``-=``. Erst der DRITTE
Ersatz-Upload EINES Submits trennt alle drei Varianten:

* Original (``+=``): ``[0, 1000, 2000]``
* ``x_stage_attachment_uploads__mutmut_45`` (``=`` statt ``+=``): ``[0, 1000, 1000]``
  — der zweite Aufruf sieht ebenfalls 1000, weil ``pending_bytes`` nicht
  weiterlaeuft, sondern jedes Mal ueberschrieben wird.
* ``x_stage_attachment_uploads__mutmut_46`` (``-=`` statt ``+=``): ``[0, 1000, -1000]``
  — faellt schon beim zweiten Aufruf ins Negative.

Konventionen wie ``test_mutation_pins_094_attachments_crud.py``: eine
Testklasse je Funktion, Zusicherungen exakt (``==``), ``patch`` auf
``core.services.file_vault.prepare_encrypted_upload`` (Modulpfad, den
``stage_attachment_uploads`` intern importiert) statt Fixture-Faken fuer
Scan/Verschluesselung — das Muster ``_FakeStaged`` aus
``test_stage_uploads.py`` liefert die ``file_size``-tragende Attrappe.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.attachments.crud import replace_upload_key, stage_attachment_uploads
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload

pytestmark = pytest.mark.django_db

#: Minimales, gueltiges PDF — ``prepare_encrypted_upload`` prueft Magic-Bytes.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _upload(name="beleg.pdf"):
    return SimpleUploadedFile(name, PDF_HEADER, content_type="application/pdf")


def _attach(owner, ft, user, *, name="beleg.pdf", sort_order=None):
    staged = prepare_encrypted_upload(owner.facility, _upload(name), user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user, sort_order=sort_order)


def _marker(*attachments):
    return {
        "__files__": True,
        "entries": [{"id": str(a.pk), "sort": i} for i, a in enumerate(attachments)],
    }


class _FakeStaged:
    """``StagedUpload``-Double mit fester Groesse (Muster ``test_stage_uploads.py``).

    Der Aufrufer prueft im Erfolgsfall weder ``output_path`` noch
    ``storage_name`` — nur ``file_size`` fliesst in ``pending_bytes`` ein.
    """

    FILE_SIZE = 1000

    def __init__(self):
        self.file_size = self.FILE_SIZE
        self.output_path = Path("/nicht-verwendet.enc")


@pytest.fixture
def ft_file(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Beleg (Pins 0.96)",
        field_type=FieldTemplate.FieldType.FILE,
    )


@pytest.fixture
def dt_file(facility, ft_file):
    dt = DocumentType.objects.create(facility=facility, name="Pins 0.96 — ein Dateifeld")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt


@pytest.fixture
def medienordner(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.mark.usefixtures("_encryption_key")
class TestStageAttachmentUploadsReplacePendingBytes:
    def test_replace_zweig_summiert_pending_bytes_ueber_drei_dateien(
        self, staff_user, sample_event, ft_file, dt_file, medienordner
    ):
        """Pin: ``x_stage_attachment_uploads__mutmut_45`` (``pending_bytes += staged.file_size``
        -> ``pending_bytes = staged.file_size``) und ``…__mutmut_46`` (``-=``
        statt ``+=``) im REPLACE-Zweig.

        Aufbau: DREI Bestandsanhaenge an EINEM FILE-Feld, DREI
        ``replace_upload_key``-Eintraege in ``request_files`` — alle drei in
        einem einzigen Aufruf von ``stage_attachment_uploads``.
        ``prepare_encrypted_upload`` wird durch einen Recorder ersetzt, der je
        Aufruf ein frisches ``_FakeStaged`` (``file_size=1000``) liefert.

        Zwei Dateien reichen NICHT: bei ``=`` sieht auch der zweite Aufruf
        1000 (identisch zum Original). Erst der DRITTE Aufruf trennt alle drei
        Varianten (siehe Modul-Docstring).

        Abweichung bei den Mutanten: die mitgeschriebene Folge der
        ``pending_bytes``-Kwargs ist ``[0, 1000, 1000]`` (mutmut_45) bzw.
        ``[0, 1000, -1000]`` (mutmut_46) statt ``[0, 1000, 2000]``.
        """
        att1 = _attach(sample_event, ft_file, staff_user, name="erste.pdf", sort_order=0)
        att2 = _attach(sample_event, ft_file, staff_user, name="zweite.pdf", sort_order=1)
        att3 = _attach(sample_event, ft_file, staff_user, name="dritte.pdf", sort_order=2)
        sample_event.data_json[ft_file.slug] = _marker(att1, att2, att3)
        sample_event.save(update_fields=["data_json"])

        schluessel = [replace_upload_key(ft_file.slug, att.entry_id) for att in (att1, att2, att3)]
        request_files = {k: _upload(f"ersatz-{i}.pdf") for i, k in enumerate(schluessel)}

        with patch("core.services.file_vault.prepare_encrypted_upload") as vorbereiten:
            vorbereiten.side_effect = [_FakeStaged() for _ in schluessel]
            stage_attachment_uploads(sample_event, staff_user, request_files, {}, dt_file)

        gesehene_pending_bytes = [call.kwargs["pending_bytes"] for call in vorbereiten.call_args_list]
        assert gesehene_pending_bytes == [0, 1000, 2000]
