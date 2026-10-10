"""Pins fuer die FK-Vorsperre des Anhangs-Batches (``core.services.attachments.crud``).

Quelle: Mutations-Delta des Checkpoints release-0.99.3, 6 Mutanten.
Gruppen:

* ``apply_attachment_changes`` — die Vorsperre bekommt die anlegende Person
  oder die Feldvorlagen der neuen Zeilen nicht mehr mit.
* ``lock_batch_attachment_rows`` — ``creates_rows`` folgt nicht mehr den
  Feldvorlagen (reines Ersetzen) bzw. verliert den Parameter.
* ``_new_row_fk_values`` — ohne anlegende Person bricht die Ableitung ab.
* ``_lock_current_owner_person_then_owner`` (Refs #2379) — Owner mit Person:
  Bindung einmal lesen, diese Person zuerst sperren, dann die Owner-Zeile mit
  ``pk``, Einrichtung UND ``client_id``; Ablehnung mit der Meldung des
  Owner-Typs bei Bindungswechsel, Instanz nachziehen. Abfragen per
  ``CaptureQueriesContext`` mitgeschnitten (Bedingungen exakt).

Eine neue Anhangszeile traegt Einrichtung, Owner, Feldvorlage und anlegende
Person als Fremdschluessel. Diese Pins sind eine Zielmengen- und
Default-Pruefung: sie schneiden die Zielmenge am Aufruf von
``lock_fk_targets`` mit (Tabelle und Wert exakt) und halten fest, WELCHE
Ziele die Vorsperre bekommt und dass sie ueberhaupt aufgerufen wird. Sie
laufen in einer offenen Transaktion, weil die Vorsperre nur dort greift.
Den zeitlichen Vertrag — die Vorsperre faellt vor den Ketten-Lock der
Einrichtung — belegen sie nicht; den haelt der Reihenfolgenachweis in
``test_attachment_lock_order.py`` fest.
"""

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from core.models import DocumentType, DocumentTypeField, FieldTemplate
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


def _pdf(name="beleg.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


def _attach(owner, ft, user):
    staged = prepare_encrypted_upload(owner.facility, _pdf(), user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user)


def _dokumenttyp(facility, name):
    ft = FieldTemplate.objects.create(facility=facility, name=name, field_type=FieldTemplate.FieldType.FILE)
    dt = DocumentType.objects.create(facility=facility, name=f"Typ {name}")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    return dt, ft


def _mit_bestand(owner, ft, user):
    att = _attach(owner, ft, user)
    owner.data_json = {ft.slug: {"__files__": True, "entries": [{"id": str(att.pk), "sort": 0}]}}
    owner.save(update_fields=["data_json"])
    return att


@pytest.fixture
def fk_spy(monkeypatch):
    """Schneidet die Zielmenge jedes Aufrufs von ``lock_fk_targets`` mit (ohne Spalte und Reihenfolge)."""
    aufrufe = []
    original = crud.lock_fk_targets

    def _spy(targets):
        targets = list(targets)
        aufrufe.append({(tabelle, str(wert)) for tabelle, _spalte, wert in targets})
        return original(targets)

    monkeypatch.setattr(crud, "lock_fk_targets", _spy)
    return aufrufe


class TestApplyAttachmentChanges:
    """``apply_attachment_changes``: die Vorsperre traegt Person und Feldvorlage."""

    def test_zugang_sperrt_anlegende_person_und_feldvorlage_vor(self, sample_event, staff_user, fk_spy):
        """x_apply_attachment_changes__mutmut_28 (``user=None``), _33 (``user``
        fehlt -> Default ``None``): das ``core_user``-Ziel fehlt; _34
        (``field_templates`` fehlt): die neue Zeile wird mit leerer Vorlage
        abgeleitet, das ``core_fieldtemplate``-Ziel fehlt."""
        dt, ft = _dokumenttyp(sample_event.facility, "Zugang")
        sample_event.data_json = {}
        sample_event.save(update_fields=["data_json"])
        with transaction.atomic():
            crud.apply_attachment_changes(sample_event, staff_user, {}, {}, {ft.slug: [_pdf()]}, dt)
        assert fk_spy == [
            {
                ("core_event", str(sample_event.pk)),
                ("core_facility", str(sample_event.facility_id)),
                ("core_fieldtemplate", str(ft.pk)),
                ("core_user", str(staff_user.pk)),
            }
        ]

    def test_reines_ersetzen_nimmt_die_fk_vorsperre(self, sample_event, staff_user, fk_spy):
        """x_apply_attachment_changes__mutmut_34: beim reinen Ersetzen haengt
        ``creates_rows`` allein an den Feldvorlagen; ohne sie entfiele die
        FK-Vorsperre ganz."""
        dt, ft = _dokumenttyp(sample_event.facility, "Ersetzen")
        bestand = _mit_bestand(sample_event, ft, staff_user)
        fk_spy.clear()
        with transaction.atomic():
            crud.apply_attachment_changes(
                sample_event, staff_user, {}, {crud.replace_upload_key(ft.slug, bestand.entry_id): _pdf()}, {}, dt
            )
        assert sample_event.attachments.filter(superseded_by__isnull=False).count() == 1
        assert fk_spy == [
            {
                ("core_event", str(sample_event.pk)),
                ("core_facility", str(sample_event.facility_id)),
                ("core_fieldtemplate", str(ft.pk)),
                ("core_user", str(staff_user.pk)),
            }
        ]


class TestLockBatchAttachmentRows:
    """``lock_batch_attachment_rows``: ``creates_rows`` folgt Parameter ODER Feldvorlagen."""

    def test_ersetzen_ohne_creates_rows_sperrt_die_fk_ziele(self, sample_event, staff_user, fk_spy):
        """x_lock_batch_attachment_rows__mutmut_5 (``and`` statt ``or``), _6
        (Vorlagen fliessen nicht ein): ein Ersetzen-Batch mit
        ``creates_rows=False`` und einer Feldvorlage legt eine Zeile an und
        braucht die FK-Vorsperre."""
        _dt, ft = _dokumenttyp(sample_event.facility, "Batch")
        bestand = _mit_bestand(sample_event, ft, staff_user)
        fk_spy.clear()
        with transaction.atomic():
            assert connection.in_atomic_block
            crud.lock_batch_attachment_rows(
                sample_event, {bestand.entry_id}, creates_rows=False, user=staff_user, field_templates=[ft]
            )
        assert fk_spy == [
            {
                ("core_event", str(sample_event.pk)),
                ("core_facility", str(sample_event.facility_id)),
                ("core_fieldtemplate", str(ft.pk)),
                ("core_user", str(staff_user.pk)),
            }
        ]

    def test_zugang_ohne_vorlagen_und_ohne_ketten_sperrt_trotzdem(self, sample_event, staff_user, fk_spy):
        """x_lock_batch_attachment_rows__mutmut_5: mit ``and`` wuerde
        ``creates_rows=True`` ohne Vorlagen zu ``False``, und der Batch mit
        leerer Kettenmenge kehrte ohne jede Sperre zurueck."""
        with transaction.atomic():
            crud.lock_batch_attachment_rows(sample_event, set(), creates_rows=True, user=staff_user)
        assert fk_spy == [
            {
                ("core_event", str(sample_event.pk)),
                ("core_facility", str(sample_event.facility_id)),
                ("core_user", str(staff_user.pk)),
            }
        ]


class TestNewRowFkValues:
    """``_new_row_fk_values``: ohne anlegende Person bleibt ``created_by`` leer."""

    def test_ohne_person_ist_created_by_leer(self, sample_event):
        """x__new_row_fk_values__mutmut_19: ``getattr`` ohne Default wirft
        ``AttributeError`` fuer ``user=None``."""
        werte = crud._new_row_fk_values(sample_event, None, None)
        assert werte["created_by_id"] is None
        assert werte["facility_id"] == sample_event.facility_id

    def test_batch_ohne_person_sperrt_kein_personenziel(self, sample_event, fk_spy):
        """x__new_row_fk_values__mutmut_19: der Default ``user=None`` von
        ``lock_batch_attachment_rows`` muss durchlaufen, ohne ein
        ``core_user``-Ziel zu sperren."""
        with transaction.atomic():
            crud.lock_batch_attachment_rows(sample_event, set(), creates_rows=True)
        assert fk_spy == [
            {
                ("core_event", str(sample_event.pk)),
                ("core_facility", str(sample_event.facility_id)),
            }
        ]


# ---------------------------------------------------------------------------
# _lock_current_owner_person_then_owner — Vorsperre bei Owner mit Person
# ---------------------------------------------------------------------------

_BEDINGUNG = re.compile(r'"(\w+)"\."(\w+)" = \'([0-9a-f]{32})\'::uuid')


def _bedingungen(sql):
    """UUID-Gleichheiten der WHERE-Klausel als Menge ``(tabelle.spalte, hex)``."""
    where = sql.split(" WHERE ", 1)[1]
    return {(f"{t}.{c}", v) for t, c, v in _BEDINGUNG.findall(where)}


def _abfragen(ctx):
    """``(tabelle, sperrt, sql)`` je Abfrage in Reihenfolge, ohne Savepoints."""
    zeilen = []
    for q in ctx.captured_queries:
        sql = q["sql"]
        if "SAVEPOINT" in sql or " FROM " not in sql:
            continue
        tabelle = sql.split(" FROM ", 1)[1].split(" ", 1)[0].strip('"')
        zeilen.append((tabelle, sql.upper().endswith("FOR UPDATE"), sql))
    return zeilen


def _person(facility, user, pseudonym):
    from core.models import Client

    return Client.objects.create(
        facility=facility, contact_stage=Client.ContactStage.IDENTIFIED, pseudonym=pseudonym, created_by=user
    )


class _WechselVorDerPersonensperre:
    """Haengt den Owner unmittelbar vor der ersten Personensperre um."""

    def __init__(self, owner, andere):
        self.owner = owner
        self.andere = andere
        self.ausgeloest = False

    def __call__(self, execute, sql, params, many, context):
        if not self.ausgeloest and 'FROM "core_client"' in sql and "FOR UPDATE" in sql.upper():
            self.ausgeloest = True
            type(self.owner).objects.filter(pk=self.owner.pk).update(client=self.andere)
        return execute(sql, params, many, context)


class TestVorsperreOwnerMitPerson:
    """Refs #2379: Owner mit Person — Bindung einmal lesen, Person, dann Owner mit Bindung sperren."""

    def test_protection_then_binding_client_and_owner_locks_are_exact(self, sample_event):
        """Pin the shared protection phase before the unchanged entity conditions."""
        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            crud.lock_owner_rows_in_facility_order(sample_event)
        queries = _abfragen(ctx)
        assert [(table, locks) for table, locks, _sql in queries] == [
            ("core_facility", False),
            ("core_documenttype", False),
            ("core_fieldtemplate", False),
            ("core_event", False),
            ("core_client", True),
            ("core_event", True),
        ]
        assert all(sql.endswith("FOR SHARE") for _table, _locks, sql in queries[1:3])
        gate = [query["sql"] for query in ctx.captured_queries if "pg_advisory_xact_lock_shared(2525," in query["sql"]]
        assert len(gate) == 1
        sqls = [query["sql"] for query in ctx.captured_queries]
        assert sqls.index(gate[0]) < sqls.index(queries[0][2])
        binding, client, owner = (sql for _table, _locks, sql in queries[3:])
        assert binding.startswith('SELECT "core_event"."client_id"')
        assert _bedingungen(binding) == {
            ("core_event.id", sample_event.pk.hex),
            ("core_event.facility_id", sample_event.facility_id.hex),
        }
        assert _bedingungen(client) == {
            ("core_client.id", sample_event.client_id.hex),
            ("core_client.facility_id", sample_event.facility_id.hex),
        }
        assert _bedingungen(owner) == {
            ("core_event.id", sample_event.pk.hex),
            ("core_event.facility_id", sample_event.facility_id.hex),
            ("core_event.client_id", sample_event.client_id.hex),
        }

    def test_frische_bindung_statt_instanz_und_nachziehen(self, sample_event, staff_user):
        """Die Instanz zeigt noch auf P, die Zeile schon auf Q: gesperrt wird Q,
        danach traegt die Instanz Q. Toetet: Person aus der Instanz statt aus
        der Lesung, Wegfall des Nachziehens."""
        neu = _person(sample_event.facility, staff_user, "Pin-Q")
        alt_id = sample_event.client_id
        type(sample_event).objects.filter(pk=sample_event.pk).update(client=neu)
        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            crud.lock_owner_rows_in_facility_order(sample_event)
        sperren = [sql for tabelle, sperrt, sql in _abfragen(ctx) if sperrt]
        assert ("core_client.id", neu.pk.hex) in _bedingungen(sperren[0])
        assert ("core_event.client_id", neu.pk.hex) in _bedingungen(sperren[1])
        assert all(alt_id.hex not in sql for sql in sperren)
        assert sample_event.client_id == neu.pk

    @pytest.mark.parametrize("owner_art", ["fall", "ereignis"])
    def test_bindungswechsel_wird_mit_meldung_des_owner_typs_abgelehnt(
        self, owner_art, sample_event, case_open, staff_user
    ):
        """Wechsel zwischen Lesung und Personensperre: Owner-Sperre trifft
        nichts -> ``ValidationError`` mit der Meldung des Owner-Typs; die
        Instanz bleibt auf der gelesenen Person. Toetet: Wegfall der
        Ablehnung, vertauschte/umgekehrte Ablehnungsbedingung, falsche
        Meldungswahl."""
        from django.core.exceptions import ValidationError

        from core.services.case.state_contract import CASE_WRITE_GONE_MESSAGE, EVENT_WRITE_GONE_MESSAGE

        owner = case_open if owner_art == "fall" else sample_event
        meldung = CASE_WRITE_GONE_MESSAGE if owner_art == "fall" else EVENT_WRITE_GONE_MESSAGE
        gelesen = owner.client_id
        wechsel = _WechselVorDerPersonensperre(owner, _person(owner.facility, staff_user, "Pin-R"))
        with transaction.atomic(), connection.execute_wrapper(wechsel), pytest.raises(ValidationError) as abgelehnt:
            crud.lock_owner_rows_in_facility_order(owner)
        assert wechsel.ausgeloest
        assert abgelehnt.value.messages == [str(meldung)]
        assert owner.client_id == gelesen

    def test_unsichtbare_owner_zeile_wird_nicht_abgelehnt(self, sample_event):
        """Zeile schon bei der Lesung weg: keine Ablehnung, Sperre auf der
        Person der Instanz. Toetet: Ablehnung ohne die Bedingung „Zeile war
        sichtbar“ (``and`` -> ``or``, Bedingung entfaellt)."""
        person_id = sample_event.client_id
        type(sample_event).objects.filter(pk=sample_event.pk).delete()
        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            assert crud.lock_owner_rows_in_facility_order(sample_event) is None
        sperren = [sql for tabelle, sperrt, sql in _abfragen(ctx) if sperrt]
        assert ("core_client.id", person_id.hex) in _bedingungen(sperren[0])
        assert sample_event.client_id == person_id
