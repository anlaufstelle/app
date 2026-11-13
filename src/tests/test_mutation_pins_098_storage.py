"""Mutation regression tests for ``core.services.file_vault.storage``.

Quelle: Füllauftrag #2193,
Batch ``merge-crud`` (``triage-096-vault-rot-storage.json``). Vier Mutanten in
zwei Funktionen, inhaltlich deckungsgleich mit dem Batch-Text (keine
Numerierungsdrift in diesem Modul):

* **``_commit_staged_upload`` — Schreibumfang des Replace-Pfads** (1 Mutant):
  ``supersedes.save(update_fields=[...])`` verliert das Argument (Default
  ``None`` = Vollschreiben). Genau die Art unbedingten Vollschreibens, die
  #2131/#2177 in der Rotation beseitigt haben — ein Rotationslauf oder
  ``soft_delete_attachment_chain``, der die Vorgaengerzeile im selben Fenster
  aendert, wuerde stillschweigend ueberschrieben.
* **``build_attachment_context`` — Sortierung und Feld-Scope** (3
  Mutanten): der Tiebreaker ``created_at`` bzw. der Primaerschluessel
  ``sort_order`` faellt aus der Sortierung, oder der ``field_template``-Filter
  entfaellt komplett (Dateien aus Feld B erscheinen unter Feld A).

Konventionen wie ``test_mutation_pins_096_storage.py``: eine Testklasse je
Funktion, echte Model-Objekte durch den echten File-Vault (kein Mock der
Pipeline selbst — nur die zu pinnende Schreib-/Sortier-Operation wird
beobachtet), Zusicherungen EXAKT.
"""

from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

# ``store_encrypted_file`` prueft Magic-Bytes per libmagic; ohne die
# Shared-Library (Host ohne ``libmagic1``) ist die Pipeline nicht lauffaehig.
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffaehig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from core.models import FieldTemplate  # noqa: E402
from core.models.attachment import Attachment  # noqa: E402
from core.services.file_vault import store_encrypted_file  # noqa: E402

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: Minimales, gueltiges PDF — die Pipeline prueft Magic-Bytes gegen den
#: deklarierten ``content_type``.
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


@pytest.fixture
def ft_file(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Beleg (Pin098)",
        field_type=FieldTemplate.FieldType.FILE,
    )


@pytest.fixture
def medienordner(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.mark.usefixtures("medienordner")
class TestCommitStagedUploadReplaceScope:
    """Der Replace-Pfad schreibt an der Vorgaengerzeile NUR die drei
    Lifecycle-Felder — nie die restliche Zeile aus dem In-Memory-Snapshot."""

    def test_replace_writes_only_the_three_lifecycle_fields(self, facility, staff_user, sample_event, ft_file):
        """Pin: ``x__commit_staged_upload__mutmut_51`` —
        ``supersedes.save(update_fields=["is_current", "superseded_by",
        "superseded_at"])`` -> ``update_fields=None``.

        Abweichung beim Mutanten: der Aufruf schreibt ALLE Felder der
        Vorgaengerzeile zurueck (Vollschreiben) statt der drei genannten.
        """
        original = store_encrypted_file(facility, _upload(), ft_file, sample_event, staff_user)
        calls = []
        original_save = Attachment.save

        def spy_save(self, *args, **kwargs):
            if self.pk == original.pk:
                calls.append(kwargs.get("update_fields"))
            return original_save(self, *args, **kwargs)

        with mock.patch.object(Attachment, "save", spy_save):
            store_encrypted_file(
                facility, _upload(name="beleg-ersatz.pdf"), ft_file, sample_event, staff_user, supersedes=original
            )

        assert len(calls) == 1, f"erwartet genau einen Save der Vorgaengerzeile, war: {calls}"
        assert calls[0] == ["is_current", "superseded_by", "superseded_at"]


@pytest.mark.usefixtures("medienordner")
class TestGetCurrentEntriesForFieldScope:
    """Nur Anhaenge des ANGEFRAGTEN FILE-Feldes — nie die eines anderen."""

    def test_file_context_keeps_fields_separate(self, facility, staff_user, sample_event, ft_file):
        from core.services.attachments import build_attachment_context

        other_field = FieldTemplate.objects.create(
            facility=facility, name="Other field", field_type=FieldTemplate.FieldType.FILE
        )
        wanted = store_encrypted_file(facility, _upload(), ft_file, sample_event, staff_user)
        other = store_encrypted_file(facility, _upload(name="other.pdf"), other_field, sample_event, staff_user)
        sample_event.data_json = {
            ft_file.slug: {"__file__": True, "attachment_id": str(wanted.pk)},
            other_field.slug: {"__file__": True, "attachment_id": str(other.pk)},
        }
        context = build_attachment_context(sample_event)
        assert [entry["attachment_id"] for entry in context[ft_file.slug]] == [str(wanted.pk)]
        assert [entry["attachment_id"] for entry in context[other_field.slug]] == [str(other.pk)]
