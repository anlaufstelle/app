"""Pins fuer die Vorsperren des Anhangs-Batches (``core.services.attachments.crud``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 15 von 17 Mutanten
(``x_apply_attachment_changes__mutmut_27/30`` sind seit der Ableitung von
``creates_rows`` aus den Feldvorlagen der neuen Zeilen nicht mehr
beobachtbar). Gruppen:

* ``lock_attachment_batch_before_audit`` — Sperrmenge beim Text-Update, beim
  Typwechsel, aus der kuenftigen Markerlage und hinter dem Sensitivity-Gate.
* ``lock_batch_attachment_rows`` — leere Menge ohne neue Zeile sperrt nichts.
* ``lock_owner_rows_in_facility_order`` — Owner ohne Personenbezug und die
  Bedingungen der Owner-Zeilensperre.
* ``purge_attachment_chain`` — Vorsperre nur der eigenen Kette, mit den
  referenzierenden Fremdzeilen.

Die Sperrmengen werden am Aufruf des jeweils naechsten Sperrhelfers
mitgeschnitten (Werte exakt), die Sperrabfragen selbst ueber
``CaptureQueriesContext``.
"""

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.attachments import crud
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)
_BEDINGUNG = re.compile(r'"(\w+)"\."(\w+)" = \'([0-9a-f]{32})\'::uuid')


def _bedingungen(sql):
    where = sql.split(" WHERE ", 1)[1]
    return {(f"{t}.{c}", v) for t, c, v in _BEDINGUNG.findall(where)}


def _attach(owner, ft, user):
    upload = SimpleUploadedFile("beleg.pdf", PDF, content_type="application/pdf")
    staged = prepare_encrypted_upload(owner.facility, upload, user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user)


def _marker(*attachments):
    return {"__files__": True, "entries": [{"id": str(a.pk), "sort": i} for i, a in enumerate(attachments)]}


def _dokumenttyp(facility, name, *, sensitivity=None):
    extra = {} if sensitivity is None else {"sensitivity": sensitivity}
    ft = FieldTemplate.objects.create(facility=facility, name=name, field_type=FieldTemplate.FieldType.FILE, **extra)
    dt = DocumentType.objects.create(facility=facility, name=f"Typ {name}")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    return dt, ft


@pytest.fixture
def batch_spy(monkeypatch):
    """Schneidet jede Sperrmenge mit, die an ``lock_batch_attachment_rows`` geht."""
    aufrufe = []
    original = crud.lock_batch_attachment_rows

    def _spy(owner, entry_ids, **kwargs):
        aufrufe.append(set(entry_ids))
        return original(owner, entry_ids, **kwargs)

    monkeypatch.setattr(crud, "lock_batch_attachment_rows", _spy)
    return aufrufe


@pytest.fixture
def reihenfolge_spy(monkeypatch):
    """Schneidet die Argumente von ``lock_attachment_rows_in_facility_order`` mit."""
    aufrufe = []
    original = crud.lock_attachment_rows_in_facility_order

    def _spy(owner, entry_ids, **kwargs):
        aufrufe.append((None if entry_ids is None else list(entry_ids), kwargs))
        return original(owner, entry_ids, **kwargs)

    monkeypatch.setattr(crud, "lock_attachment_rows_in_facility_order", _spy)
    return aufrufe


class TestLockAttachmentBatchBeforeAudit:
    def test_text_update_ohne_typ_setzt_keine_abfrage_ab(self, sample_event, staff_user):
        """x_lock_attachment_batch_before_audit__mutmut_1 (Default
        ``discards_existing_chains=True``: liest und sperrt die offenen Ketten)
        und _4 (``creates_rows=True``: sperrt die Owner-Zeile) — beide setzen
        Abfragen ab, das reine Text-Update keine."""
        ft = FieldTemplate.objects.create(
            facility=sample_event.facility, name="Bestand", field_type=FieldTemplate.FieldType.FILE
        )
        _attach(sample_event, ft, staff_user)
        with CaptureQueriesContext(connection) as ctx:
            crud.lock_attachment_batch_before_audit(sample_event, staff_user, {}, {}, None)
        assert ctx.captured_queries == []

    def test_typwechsel_sperrt_die_offenen_bestandsketten(self, sample_event, staff_user, batch_spy):
        """x_lock_attachment_batch_before_audit__mutmut_28 (Menge wird ``None``)
        und _29 (Schnitt statt Vereinigung -> leere Menge): beim Typwechsel
        ohne neuen Batch muss genau die offene Bestandskette in die Menge."""
        ft = FieldTemplate.objects.create(
            facility=sample_event.facility, name="Bestand", field_type=FieldTemplate.FieldType.FILE
        )
        bestand = _attach(sample_event, ft, staff_user)
        crud.lock_attachment_batch_before_audit(sample_event, staff_user, {}, {}, None, discards_existing_chains=True)
        assert batch_spy == [{bestand.entry_id}]

    def test_sperrmenge_folgt_der_uebergebenen_markerlage(self, sample_event, staff_user, batch_spy):
        """x_lock_attachment_batch_before_audit__mutmut_11 (``data_json=None``)
        und _15 (``data_json`` fehlt): die Menge kaeme aus der gespeicherten
        Markerlage des Owners, die den zu entfernenden Eintrag nicht kennt."""
        dt, ft = _dokumenttyp(sample_event.facility, "Kuenftig")
        att = _attach(sample_event, ft, staff_user)
        assert ft.slug not in (sample_event.data_json or {})
        crud.lock_attachment_batch_before_audit(
            sample_event,
            staff_user,
            {f"{ft.slug}__remove": str(att.entry_id)},
            {},
            dt,
            data_json={ft.slug: _marker(att)},
        )
        assert batch_spy == [{att.entry_id}]

    def test_sensitivity_gate_gilt_fuer_die_sperrmenge(self, sample_event, staff_user, batch_spy):
        """x_lock_attachment_batch_before_audit__mutmut_9: mit ``user=None``
        entfaellt das Sensitivity-Gate, und die Kette eines fuer STAFF
        unsichtbaren Feldes landete in der Sperrmenge."""
        dt, ft = _dokumenttyp(sample_event.facility, "Verschlusssache", sensitivity=DocumentType.Sensitivity.HIGH)
        att = _attach(sample_event, ft, staff_user)
        crud.lock_attachment_batch_before_audit(
            sample_event,
            staff_user,
            {f"{ft.slug}__remove": str(att.entry_id)},
            {},
            dt,
            data_json={ft.slug: _marker(att)},
        )
        assert batch_spy == [set()]


class TestLockBatchAttachmentRows:
    def test_leere_menge_ohne_neue_zeile_setzt_keine_abfrage_ab(self, sample_event):
        """x_lock_batch_attachment_rows__mutmut_1: Default ``creates_rows=True``
        sperrte bei leerer Menge ungefragt Personen- und Owner-Zeile."""
        with CaptureQueriesContext(connection) as ctx:
            crud.lock_batch_attachment_rows(sample_event, [])
        assert ctx.captured_queries == []


class _OwnerOhnePersonenbezug:
    """Owner-Objekt ohne ``client_id``-Attribut, das auf eine Ereigniszeile zeigt."""

    objects = Event.objects

    def __init__(self, event):
        self.pk = event.pk
        self.facility_id = event.facility_id


class TestLockOwnerRowsInFacilityOrder:
    def test_owner_zeile_mit_pk_und_einrichtung(self, sample_event):
        """x_lock_owner_rows_in_facility_order__mutmut_28 (``pk`` faellt weg ->
        beliebige Zeile der Einrichtung) und _29 (Einrichtung faellt weg)."""
        with CaptureQueriesContext(connection) as ctx:
            crud.lock_owner_rows_in_facility_order(sample_event)
        sperren = [q["sql"] for q in ctx.captured_queries if q["sql"].startswith('SELECT "core_event"."id" FROM')]
        assert len(sperren) == 1
        assert sperren[0].endswith("FOR UPDATE")
        assert _bedingungen(sperren[0]) == {
            ("core_event.id", sample_event.pk.hex),
            ("core_event.facility_id", sample_event.facility_id.hex),
            # Refs #2379: Bindungsbedingung auf die frisch gelesene Person.
            ("core_event.client_id", sample_event.client_id.hex),
        }

    def test_owner_ohne_client_attribut_bricht_nicht_ab(self, sample_event):
        """x_lock_owner_rows_in_facility_order__mutmut_17: ``getattr`` ohne
        Default wirft ``AttributeError`` fuer einen Owner ohne ``client_id``;
        erwartet ist nur die Owner-Zeilensperre, keine Personenzeile."""
        with CaptureQueriesContext(connection) as ctx:
            assert crud.lock_owner_rows_in_facility_order(_OwnerOhnePersonenbezug(sample_event)) is None
        tabellen = [q["sql"].split(" FROM ", 1)[1].split(" ", 1)[0] for q in ctx.captured_queries]
        assert tabellen == ['"core_event"']


class TestPurgeAttachmentChain:
    def test_vorsperre_nur_eigene_kette_mit_fremdzeilen(self, sample_event, staff_user, reihenfolge_spy):
        """x_purge_attachment_chain__mutmut_39 (``None`` = alle Owner-Zeilen),
        _40 (``deletes_rows=None``), _43 (``deletes_rows=False``), _44
        (``deletes_rows`` fehlt): die Vorsperre muss genau die eigene Kette
        mit ``deletes_rows=True`` nehmen."""
        ft = FieldTemplate.objects.create(
            facility=sample_event.facility, name="Purge", field_type=FieldTemplate.FieldType.FILE
        )
        att = _attach(sample_event, ft, staff_user)
        assert crud.purge_attachment_chain(sample_event, att.entry_id) == 1
        assert reihenfolge_spy == [([att.entry_id], {"deletes_rows": True})]
