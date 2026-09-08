"""Pin-Tests der Welle I (0.96) — Prio-1-Survivors in ``core.services.events.crud``.

Quelle: Survivor-Triage des Mutations-Deltas v0.96.0
(``welle-i-0.96/reports/pin-task-events-import.json``). Gepinnt werden 13
bestaetigte Testluecken des Event-Schreib- und Loeschpfads in vier Familien:

* **Art.-18-Gate im Loeschpfad** — ``assert_processing_allowed(None, ...)``
  schaltet das Gate in :func:`soft_delete_event` vollstaendig ab
  (``_restricted_flag(None)`` ist immer ``False``).
  ``test_processing_restriction_write_paths.py`` pinnt Create und Update, den
  Loeschpfad bislang nicht.
* **Die Zeilensperre des Gates** — ``lock=True`` -> ``lock=False``/``None``/
  weggelassen. Die Abweisung einer BEREITS eingeschraenkten Person bleibt
  erhalten (die Bestandstests bleiben gruen), nur das ``FOR UPDATE`` faellt
  weg. Genau darin liegt aber der Unterschied zwischen „war beim Pruefen
  nicht eingeschraenkt" und „kann bis zum Commit nicht eingeschraenkt
  werden" (Docstring ``assert_processing_allowed``). Beobachtbar nur am
  ausgefuehrten SQL.
* **Die gesperrte ZEILE** — ``filter(pk=client_id)`` -> ``filter(pk=None)`` in
  :func:`_lock_client_row`/:func:`_lock_event_row`. Das SQL traegt weiterhin
  Tabellenname und ``FOR UPDATE`` (``WHERE ... IS NULL ... FOR UPDATE``), die
  Ordnungs-Pins in ``test_eventhistory_retention.py`` bleiben deshalb gruen —
  gesperrt wird trotzdem nichts. Gepinnt wird darum die gesperrte PK im SQL,
  nicht nur der Tabellenname.
* **Anonymitaets-Grenze der Fall-Zuordnung** — ``or`` -> ``and`` in
  :func:`_validate_case_assignment` bzw. das auf ``None`` gesetzte
  ``is_anonymous``-Argument im Aufruf aus :func:`create_event`.

Dazu der DELETE-Audit-Zeitbezug (``occurred_at``) und die Akteurs-Weitergabe
des duennen Alias :func:`core.services.events.crud.finalize_staged_files`.

Konventionen wie ``test_mutation_pins_094_attachments_crud.py``: eine
Testklasse je Funktion; jeder Test nennt seine Mutanten in der Docstring samt
dem Wert, der beim Mutanten abweicht; Zusicherungen vergleichen EXAKT
(``==``/``is``), nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-
Varianten); keine Fakes — es laufen echte Model-Objekte durch die echten
Services; KEIN Import aus ``test_rls_functional`` (dessen modulweiter Skip
unter ``MUTANT_UNDER_TEST`` wuerde jeden Pin entwerten).
"""

import re

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AuditLog, Client, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE, restrict_processing
from core.services.events.crud import (
    _lock_client_row,
    _lock_event_row,
    create_event,
    finalize_staged_files,
    soft_delete_event,
    update_event,
)
from core.services.file_vault import prepare_encrypted_upload

#: ``FOR UPDATE``-Statements verraten die Sperre; der Tabellenname steht im
#: ``FROM``. Gleiche Grob-Erkennung wie ``test_restriction_lock_order.py``,
#: damit sie nicht an Django-Versionsdetails der SQL-Formatierung haengt.
_LOCK_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)


def _locking_statements(captured, table):
    """Alle ``FOR UPDATE``-Anweisungen eines Mitschnitts auf *table*."""
    hits = []
    for query in captured.captured_queries:
        match = _LOCK_RE.search(query["sql"])
        if match and match.group("table") == table:
            hits.append(query["sql"])
    return hits


def _pk_literal(obj_or_pk):
    """Die PK so, wie Postgres sie im ausgefuehrten SQL zeigt: Hex ohne Bindestriche."""
    pk = getattr(obj_or_pk, "pk", obj_or_pk)
    return str(pk).replace("-", "")


def _occurred():
    return timezone.now()


# ---------------------------------------------------------------------------
# soft_delete_event — Art.-18-Gate und DELETE-Audit (2 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSoftDeleteEventArt18Gate:
    """Die Einzelloeschung an einer eingeschraenkten Person ist blockiert.

    Der Docstring von ``soft_delete_event`` zieht die Linie ausdruecklich:
    „Blockiert bleibt die Einzelentscheidung ohne zweite Person" — der
    GEPRUEFTE Vier-Augen-Weg (``approve_deletion``) bleibt offen, weil Art. 17
    waehrend einer Art.-18-Einschraenkung weitergilt.
    """

    def test_einzelloeschung_an_eingeschraenkter_person_ist_blockiert(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Pin: ``x_soft_delete_event__mutmut_1`` — ``assert_processing_allowed(event.client_id, lock=True)``
        -> ``assert_processing_allowed(None, lock=True)``.

        ``_restricted_flag(None)`` liefert IMMER ``False``; das Gate im
        Loeschpfad waere damit vollstaendig abgeschaltet.

        Abweichung beim Mutanten: kein ``ValidationError``, das Ereignis wird
        geloescht (``is_deleted is True``, ``data_json == {}``).
        """
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=_occurred(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)

        with pytest.raises(ValidationError) as excinfo:
            soft_delete_event(event, staff_user)

        assert excinfo.value.messages == [str(PROCESSING_RESTRICTED_MESSAGE)]
        event.refresh_from_db()
        assert event.is_deleted is False
        assert event.data_json == {"dauer": 15}

    def test_geprueter_vier_augen_weg_bleibt_offen(
        self, facility, staff_user, lead_user, client_identified, doc_type_contact
    ):
        """Gegenprobe zur Trennlinie: Art. 17 gilt waehrend der Einschraenkung
        weiter — der Vier-Augen-Weg oeffnet den benannten Loeschworkflow-Kontext
        und laeuft durch. Ohne diese Gegenprobe waere der Pin oben auch mit
        einem pauschalen Loeschverbot gruen."""
        from core.services.events import approve_deletion, request_deletion

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=_occurred(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        deletion_request = request_deletion(event, staff_user, "Antrag der betroffenen Person")
        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)

        approve_deletion(deletion_request, lead_user)

        event.refresh_from_db()
        assert event.is_deleted is True


@pytest.mark.django_db
class TestSoftDeleteEventAuditDetail:
    def test_delete_audit_traegt_den_ereigniszeitpunkt(self, facility, staff_user, client_identified, doc_type_contact):
        """Pin: ``x_soft_delete_event__mutmut_58`` — ``"occurred_at": str(event.occurred_at)``
        -> ``"occurred_at": str(None)``.

        Nach dem Wipe von ``data_json`` ist der DELETE-Eintrag der
        Rechenschaftsnachweis der Loeschung; ohne den Zeitbezug ist nicht mehr
        belegbar, WELCHER Vorgang geloescht wurde. Der Bestandstest prueft nur
        ``client_id`` und die Abwesenheit des Pseudonyms.

        Abweichung beim Mutanten: ``detail["occurred_at"] == "None"``.
        """
        occurred_at = _occurred()
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=occurred_at,
            data_json={"dauer": 15},
            client=client_identified,
        )

        soft_delete_event(event, staff_user)

        entry = AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_id=str(event.pk)).latest("timestamp")
        assert entry.detail["occurred_at"] == str(occurred_at)
        assert entry.detail["occurred_at"] != "None"


# ---------------------------------------------------------------------------
# create_event / update_event — die Zeilensperre des Art.-18-Gates (6 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestArt18GateTakesTheClientRowLock:
    """``lock=True`` ist der Unterschied zwischen Momentaufnahme und Zusage.

    Beide Services nehmen sonst KEINE Client-Sperre; faellt sie weg, kann ein
    paralleles ``restrict_processing`` zwischen Pruefung und Write committen.
    Die Bestandstests (``test_create_event_blocked``/``test_update_event_blocked``)
    bleiben gruen, weil eine bereits gesetzte Einschraenkung auch ungesperrt
    gelesen wird.
    """

    def test_create_event_sperrt_die_personenzeile(self, facility, staff_user, client_identified, doc_type_contact):
        """Pin: ``x_create_event__mutmut_3`` (``lock=None``), ``…__mutmut_5``
        (Argument weggelassen, Default ``False``) und ``…__mutmut_6``
        (``lock=False``).

        Abweichung beim Mutanten: der ``core_client``-SELECT des Gates traegt
        kein ``FOR UPDATE`` mehr — die Liste der Sperr-Anweisungen ist leer.
        """
        with CaptureQueriesContext(connection) as captured:
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=_occurred(),
                data_json={"dauer": 15},
                client=client_identified,
            )

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_update_event_sperrt_die_personenzeile(self, facility, staff_user, client_identified, doc_type_contact):
        """Pin: ``x_update_event__mutmut_3`` (``lock=None``), ``…__mutmut_5``
        (Argument weggelassen) und ``…__mutmut_6`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt; die ``core_event``-Sperre des Slug-Diffs bleibt bestehen
        und deckt den Ausfall zu, wenn man nur „irgendeine Sperre" pruefte.
        """
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=_occurred(),
            data_json={"dauer": 15},
            client=client_identified,
        )

        with CaptureQueriesContext(connection) as captured:
            update_event(event, staff_user, {"dauer": 30})

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]


# ---------------------------------------------------------------------------
# _lock_client_row / _lock_event_row — die gesperrte ZEILE (2 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeletePathLocksTheRealRows:
    """``filter(pk=None)`` erzeugt ``WHERE id IS NULL … FOR UPDATE``.

    Das ist syntaktisch eine Sperranweisung und semantisch keine: die
    Ergebnismenge ist leer, es wird keine Zeile gesperrt. Die Ordnungs-Pins in
    ``test_eventhistory_retention.py`` suchen Tabellenname + ``FOR UPDATE`` und
    koennen den Unterschied nicht sehen — hier wird deshalb die gesperrte PK
    gepinnt.
    """

    def test_lock_client_row_sperrt_genau_diese_person(self, facility, staff_user, client_identified):
        """Pin: ``x__lock_client_row__mutmut_3`` — ``filter(pk=client_id)``
        -> ``filter(pk=None)``.

        Abweichung beim Mutanten: das SQL enthaelt ``IS NULL`` statt der PK.
        """
        with CaptureQueriesContext(connection) as captured:
            _lock_client_row(client_identified.pk)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]
        assert "IS NULL" not in locks[0].upper(), locks[0]

    def test_lock_event_row_sperrt_genau_dieses_ereignis(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Pin: ``x__lock_event_row__mutmut_2`` — ``filter(pk=event.pk)``
        -> ``filter(pk=None)``.

        Abweichung beim Mutanten: das SQL enthaelt ``IS NULL`` statt der PK.
        """
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=_occurred(),
            data_json={"dauer": 15},
            client=client_identified,
        )

        with CaptureQueriesContext(connection) as captured:
            _lock_event_row(event)

        locks = _locking_statements(captured, "core_event")
        assert len(locks) == 1, f"erwartet GENAU eine Ereignis-Sperre, war: {locks}"
        assert _pk_literal(event) in locks[0], locks[0]
        assert "IS NULL" not in locks[0].upper(), locks[0]

    def test_geprueter_loeschweg_sperrt_beide_zeilen_wirksam(
        self, facility, staff_user, lead_user, client_identified, doc_type_contact
    ):
        """Pin: ``x__lock_client_row__mutmut_3`` und ``x__lock_event_row__mutmut_2``
        auf dem Weg, fuer den die Sperren eigenstaendig stehen.

        Im Vier-Augen-Weg kehrt das Art.-18-Gate im ``deletion_request``-Kontext
        sofort zurueck und steuert KEINE Sperre bei — die einzigen
        ``FOR UPDATE``-Anweisungen auf ``core_client``/``core_event`` stammen
        hier aus ``_lock_client_row``/``_lock_event_row``. Genau das ist der
        Koordinationspunkt gegen eine parallele Hold-Anlage (ADR-050 D2).

        Abweichung beim Mutanten: die jeweilige Anweisung sperrt ``IS NULL``
        statt der Zeile.
        """
        from core.services.events import approve_deletion, request_deletion

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=_occurred(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        deletion_request = request_deletion(event, staff_user, "Antrag der betroffenen Person")

        with CaptureQueriesContext(connection) as captured:
            approve_deletion(deletion_request, lead_user)

        client_locks = _locking_statements(captured, "core_client")
        event_locks = _locking_statements(captured, "core_event")
        assert len(client_locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {client_locks}"
        assert len(event_locks) == 1, f"erwartet GENAU eine Ereignis-Sperre, war: {event_locks}"
        assert _pk_literal(client_identified) in client_locks[0], client_locks[0]
        assert _pk_literal(event) in event_locks[0], event_locks[0]


# ---------------------------------------------------------------------------
# _validate_case_assignment — Anonymitaets-Grenze der Fall-Zuordnung (2 Mutanten)
# ---------------------------------------------------------------------------


ANONYMITAETS_MELDUNG = "Anonyme Ereignisse dürfen nicht an Fälle mit zugeordneter Person gehängt werden."


@pytest.mark.django_db
class TestValidateCaseAssignmentAnonymity:
    """Ein Fall mit zugeordneter Person nimmt kein anonymes Ereignis auf.

    Die Bedingung ist eine ODER-Verknuepfung: WEDER darf die Person fehlen
    (``create_event`` setzt direkt danach ``is_anonymous = True``) NOCH darf
    ``is_anonymous`` gesetzt sein. Der Bestandstest deckt nur eine der beiden
    Haelften ab, das ``and`` faellt deshalb nicht auf.
    """

    def test_ereignis_ohne_person_an_personenfall_wird_abgewiesen(
        self, facility, staff_user, doc_type_contact, case_open
    ):
        """Pin: ``x__validate_case_assignment__mutmut_20`` — ``(client is None or
        is_anonymous)`` -> ``(client is None and is_anonymous)``.

        ``client=None``/``is_anonymous=False``: die Vorlage weist ab, der Mutant
        laesst durch — und ``create_event`` setzt unmittelbar danach
        ``is_anonymous = True`` (der Typ hat keine Mindest-Kontaktstufe). Das
        anonyme Ereignis haengt dann am Fall einer identifizierten Person.

        Abweichung beim Mutanten: kein ``ValidationError``, ein Ereignis mit
        ``is_anonymous is True`` haengt an ``case_open``.
        """
        with pytest.raises(ValidationError) as excinfo:
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=_occurred(),
                data_json={"dauer": 15},
                client=None,
                is_anonymous=False,
                case=case_open,
            )

        assert excinfo.value.messages == [ANONYMITAETS_MELDUNG]
        assert not Event.objects.filter(case=case_open).exists()

    def test_anonymes_ereignis_mit_person_an_personenfall_wird_abgewiesen(
        self, facility, staff_user, doc_type_contact, case_open, client_identified
    ):
        """Pin: ``x__validate_case_assignment__mutmut_20`` (``or`` -> ``and``)
        UND ``x_create_event__mutmut_35`` (``_validate_case_assignment(facility,
        case, client, None)`` statt ``is_anonymous``).

        Person gesetzt und ``is_anonymous=True``: die zweite Haelfte der
        ODER-Bedingung traegt allein. Beim Argument-Mutanten sieht der Guard
        ``is_anonymous`` immer als falsy, beim ``and``-Mutanten scheitert die
        erste Haelfte — beide lassen durch.

        Abweichung beim Mutanten: kein ``ValidationError``, das Ereignis
        entsteht mit ``is_anonymous is True`` am Fall der Person.
        """
        with pytest.raises(ValidationError) as excinfo:
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=_occurred(),
                data_json={"dauer": 15},
                client=client_identified,
                is_anonymous=True,
                case=case_open,
            )

        assert excinfo.value.messages == [ANONYMITAETS_MELDUNG]
        assert not Event.objects.filter(case=case_open).exists()


# ---------------------------------------------------------------------------
# finalize_staged_files — Akteurs-Weitergabe des duennen Alias (1 Mutant)
# ---------------------------------------------------------------------------


#: Minimales, gueltiges PDF — ``prepare_encrypted_upload`` prueft Magic-Bytes.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


@pytest.fixture
def medienordner(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


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


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestEventAliasFinalizeStagedFiles:
    """Der Event-Alias reicht den Akteur durch — nicht nur die Implementierung.

    Die 0.94-Pins in ``test_mutation_pins_094_attachments_crud.py`` treffen
    ``attachments.crud.finalize_staged_files``; der duenne Event-Alias in
    ``events/crud.py`` (Bestands-Importpfad der Event-Views) war ungepinnt.
    """

    def test_alias_reicht_den_handelnden_user_durch(self, staff_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x_finalize_staged_files__mutmut_2`` — ``attachments_crud.
        finalize_staged_files(event, user, …)`` -> ``(event, None, …)``.

        ``user`` landet dort als ``Attachment.created_by`` und als Akteur des
        ATTACHMENT_UPLOAD-Audits. Beide FK sind nullable, der Mutant laeuft
        also fehlerfrei durch.

        Abweichung beim Mutanten: ``created_by is None`` statt ``staff_user``,
        die Audit-Zeile hat ``user is None`` — der Upload waere niemandem mehr
        zuzuordnen.
        """
        staged = prepare_encrypted_upload(
            sample_event.facility,
            SimpleUploadedFile("beleg.pdf", PDF_HEADER, content_type="application/pdf"),
            None,
            owner=sample_event,
        )

        finalize_staged_files(sample_event, staff_user, {ft_file.slug: [staged]}, dt_file)

        attachment = sample_event.attachments.get()
        assert attachment.created_by == staff_user
        entry = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_UPLOAD)
        assert entry.user == staff_user
