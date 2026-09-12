"""Pin-Tests der Welle K (0.98) — Prio-2-Survivors in ``core.services.file_vault.storage``.

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
* **``get_current_entries_for_field`` — Sortierung und Feld-Scope** (3
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
from django.db.models.query import QuerySet

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
from core.services.file_vault.storage import get_current_entries_for_field  # noqa: E402

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
class TestGetCurrentEntriesForFieldOrdering:
    """Die dokumentierte Sortierung ist ``sort_order`` MIT ``created_at`` als
    Tiebreaker — beide Schluessel, in dieser Reihenfolge, in einem Aufruf."""

    def test_query_wird_nach_sort_order_und_created_at_sortiert(self, facility, staff_user, sample_event, ft_file):
        """Pin: ``x_get_current_entries_for_field__mutmut_4`` (nur
        ``created_at``, ``sort_order`` entfaellt) und ``_5`` (nur
        ``sort_order``, ``created_at``-Tiebreaker entfaellt).

        Beobachtet die tatsaechlichen ``order_by``-Argumente statt sich auf
        DB-Ausfuehrungsreihenfolge bei Gleichstand zu verlassen (nicht
        garantiert ohne Tiebreaker). Abweichung bei BEIDEN Mutanten: das Tupel
        ``("sort_order", "created_at")`` taucht unter den Aufrufen nicht auf.
        """
        store_encrypted_file(facility, _upload(), ft_file, sample_event, staff_user)
        calls = []
        original_order_by = QuerySet.order_by

        def spy(self, *args, **kwargs):
            calls.append(args)
            return original_order_by(self, *args, **kwargs)

        with mock.patch.object(QuerySet, "order_by", spy):
            get_current_entries_for_field(sample_event, ft_file)

        assert ("sort_order", "created_at") in calls, calls


@pytest.mark.usefixtures("medienordner")
class TestGetCurrentEntriesForFieldScope:
    """Nur Anhaenge des ANGEFRAGTEN FILE-Feldes — nie die eines anderen."""

    def test_nur_eintraege_des_angefragten_feldes_kommen_zurueck(self, facility, staff_user, sample_event, ft_file):
        """Pin: ``x_get_current_entries_for_field__mutmut_9`` — der
        ``field_template``-Filter entfaellt komplett.

        Abweichung beim Mutanten: die Funktion liefert ALLE aktuellen Anhaenge
        des Owners, nicht nur die von ``ft_file`` — der Anhang aus
        ``ft_other`` erscheint mit im Ergebnis.
        """
        ft_other = FieldTemplate.objects.create(
            facility=facility, name="Anderes Feld (Pin098)", field_type=FieldTemplate.FieldType.FILE
        )
        wanted = store_encrypted_file(facility, _upload(), ft_file, sample_event, staff_user)
        store_encrypted_file(facility, _upload(name="anderes-feld.pdf"), ft_other, sample_event, staff_user)

        entries = get_current_entries_for_field(sample_event, ft_file)

        assert [entry.pk for entry in entries] == [wanted.pk]
