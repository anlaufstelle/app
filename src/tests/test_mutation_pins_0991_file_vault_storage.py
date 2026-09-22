"""Pin-Tests des Mutations-Delta-Checkpoints der Welle M (release-0.99.1) zu
``core.services.file_vault.storage``.

Quelle: Ticket #2322 (https://github.com/anlaufstelle/app/issues/2322),
Survivor-Triage des Checkpoint-Deltas ``6435c18e`` (BASE v0.99.0). Gepinnt
werden die vier Prio-1-Survivor dieses Moduls: ``soft_delete_attachment_chain``
ruft die kanonische Sperr-Vorlaufordnung
(``lock_attachment_rows_in_facility_order``) mit ``[entry_id]`` (gezielte
Kette, nicht alle Owner-Anhaenge) UND ``lock_owner_rows=True`` (Stufe 1 der
Ordnung, Refs #2260) auf. Kein Bestandstest legt eine ZWEITE, unbeteiligte
Kette desselben Owners an oder prueft die SQL-Reihenfolge der Vorsperre —
beide Argumente koennten deshalb unbemerkt kippen.

Konvention wie ``src/tests/test_mutation_pins_094_attachments_crud.py``:
echte Model-Objekte, Zusicherungen exakt, Sperr-Eigenschaft ueber
mitgeschnittene ``FOR UPDATE``-Queries (``connection.execute_wrapper``).
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from core.models import FieldTemplate
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload
from core.services.file_vault.storage import soft_delete_attachment_chain

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

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


def _attach(owner, ft, user, *, name="beleg.pdf"):
    staged = prepare_encrypted_upload(owner.facility, _upload(name), user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user)


class _Mitschnitt(list):
    def wrapper(self, execute, sql, params, many, context):
        self.append((sql, params))
        return execute(sql, params, many, context)


def _for_update_queries(mitschnitt, *, table=None):
    treffer = [(sql, params) for sql, params in mitschnitt if "FOR UPDATE" in sql.upper()]
    if table is not None:
        treffer = [(sql, params) for sql, params in treffer if table in sql]
    return treffer


@pytest.fixture
def ft_file(facility):
    return FieldTemplate.objects.create(
        facility=facility, name="Beleg (Pins 0991 storage)", field_type=FieldTemplate.FieldType.FILE
    )


@pytest.fixture(autouse=True)
def medienordner(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


class TestSoftDeleteAttachmentChainOwnerStage:
    def test_owner_stufe_wird_vor_der_kette_gesperrt(self, sample_event, ft_file, staff_user):
        """Pin: ``x_soft_delete_attachment_chain__mutmut_4``
        (``lock_owner_rows=None``), ``…__mutmut_7`` (Schluesselargument
        entfaellt, Default ``False``) und ``…__mutmut_8``
        (``lock_owner_rows=False`` explizit).

        ``sample_event`` traegt einen Personenbezug — Stufe 1 sperrt sowohl
        ``core_client`` als auch ``core_event``. Original: drei
        ``FOR UPDATE``-Treffer (Client, Event, Attachment). Mutant: die
        Owner-Stufe entfaellt komplett — nur noch die Attachment-Zeile wird
        gesperrt.
        """
        att = _attach(sample_event, ft_file, staff_user)

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper):
            entfernt = soft_delete_attachment_chain(sample_event, att.entry_id, staff_user)

        assert entfernt == 1
        sperrend = _for_update_queries(mitschnitt)
        assert len(sperrend) == 3
        assert len(_for_update_queries(mitschnitt, table="core_client")) == 1
        assert len(_for_update_queries(mitschnitt, table="core_event")) == 1
        assert len(_for_update_queries(mitschnitt, table="core_attachment")) == 1

    def test_sperrmenge_bleibt_auf_die_zielkette_beschraenkt(self, sample_event, ft_file, staff_user):
        """Pin: ``x_soft_delete_attachment_chain__mutmut_3`` — die gezielte
        Sperrmenge ``[entry_id]`` wird durch ``None`` (alle Owner-Anhaenge)
        ersetzt.

        ``att_andere`` ist eine ZWEITE, unabhaengige Kette desselben Owners.
        Original: nur ``att_ziel`` wird gesperrt. Mutant: ``entry_ids=None``
        sperrt ALLE Attachment-Zeilen des Owners — auch ``att_andere``.
        """
        att_ziel = _attach(sample_event, ft_file, staff_user, name="ziel.pdf")
        att_andere = _attach(sample_event, ft_file, staff_user, name="andere.pdf")

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper):
            entfernt = soft_delete_attachment_chain(sample_event, att_ziel.entry_id, staff_user)

        assert entfernt == 1
        attachment_sperren = _for_update_queries(mitschnitt, table="core_attachment")
        werte = {str(p) for _sql, params in attachment_sperren for p in params}
        assert str(att_ziel.entry_id) in werte
        assert str(att_andere.entry_id) not in werte
