"""Pin-Tests zum Meilenstein release-0.94 — Modul-Batch ``main_1``.

Quelle: Survivor-Triage des Moduls ``src/core/services/client/main.py``
(Mutantenbaum ``mutants/src/core/services/client/main.py``). Vorgabe ist die
Survivor-Triage release-0.94 (Ledger des Meilensteins) (60 Eintraege, davon
43 verschiedene Mutanten — die Liste fuehrt die Ergebnisse des Cut-v0.91.0-Laufs
und des Nachlaufs nebeneinander, 17 Mutanten stehen doppelt drin).

Der Batch deckt die Art.-17-Kaskade der Personen-Anonymisierung ab. Vier
Muster ziehen sich durch:

* **Zieltyp-Klammern.** ``DeletionRequest``/``LegalHold``/``RetentionProposal``
  tragen ``target_type`` + ``target_id`` als *lose* generische Referenz; die
  ``target_id`` allein ist NICHT eindeutig. Faellt der ``target_type``-Filter,
  redigiert die Kaskade fremde Zeilen mit kollidierender UUID mit — bzw.
  greift bei der Merge-Inventar-Zeile zur falschen Audit-Zeile. Jeder solche
  Pin legt eine echte Koeder-Zeile mit derselben ``target_id`` und einem
  anderen ``target_type`` an und sichert zu, dass sie unberuehrt bleibt.
* **``update_fields``-Beschraenkung.** Die Teil-Saves der Kaskade duerfen nur
  die redigierten Spalten schreiben; ``update_fields=None`` macht aus jedem
  Save ein Lost-Update ueber alle Spalten. Die Pins weisen das fachlich nach
  (nebenlaeufiger Schreiber per ``pre_save``-Empfaenger bzw. unveraenderter
  ``updated_at``-Stempel), nicht ueber die erzeugte SQL.
* **Ops-Vertragstexte.** Log-Zeilen (``eventhistory_redaction_deferred``,
  ``client_merge_inventory_missing``) sind der einzige Beleg fuer die beiden
  degradierenden Pfade. Sie werden per ``caplog`` EXAKT verglichen.
* **Fehlermeldungen** des Dokumentationstyp-Guards — exakt, nicht per Regex.

Konventionen wie ``src/tests/test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt zu jedem Literal die Variante
``"XX<text>XX"``, die ein ``in`` durchliesse); der Sprachkontext wird bei
Texten per ``translation.override("de")`` festgenagelt; KEINE Imports aus
``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py`` bzw.
werden als echte Model-Objekte angelegt (keine Fakes). Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
"""

import logging
import uuid
from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save
from django.utils import timezone, translation

from core.models import (
    Activity,
    AuditLog,
    Client,
    DeletionRequest,
    DocumentType,
    Event,
    EventHistory,
    LegalHold,
    RetentionProposal,
)
from core.services.client import main as client_main
from core.services.client.main import (
    anonymize_eligible_soft_deleted_clients,
    get_pending_deletion_request,
    pending_deletion_client_ids,
    pending_merge_client_ids,
)
from core.services.dashboard.activity import ACTIVITY_CONTEXT_SCHEMA

pytestmark = pytest.mark.django_db

LOGGER_NAME = "core.services.client.main"

#: Der Marker, den alle Freitext-Redaktionen der Kaskade schreiben.
REDACTED = "[Anonymisiert]"

#: Der Kontext, den beide Activity-Redaktionen schreiben.
REDACTED_CONTEXT = {"schema": ACTIVITY_CONTEXT_SCHEMA, "action": Activity.Action.REDACTED.value}


# ===========================================================================
# _normalize_document_type_kwarg — die Meldungen des Typ-Guards
# ===========================================================================


class TestDocumentTypeGuardMessages:
    """Der Guard sagt fachlich, WAS falsch ist — Wort fuer Wort.

    ``_normalize_document_type_kwarg`` ist die einzige Stelle, die einem
    Bedienenden erklaert, warum eine Neuanlage abgelehnt wurde. Die
    Bestandstests pruefen die beiden Meldungen per Teilstring/Regex
    (``match="nur einmal"``); die ``XX...XX``- und die Kleinschreib-Variante
    von mutmut enthalten den gesuchten Teilstring weiterhin bzw. lesen sich
    als beilaeufiger Stilbruch und ueberlebten damit beide.
    """

    def test_both_spellings_at_once_are_rejected_with_the_exact_message(self, facility):
        """Pin: ``x__normalize_document_type_kwarg__mutmut_17`` — Meldung wird
        zu ``"XXDokumentationstyp bitte nur einmal angeben (Objekt oder ID,
        nicht beides).XX"``; ``x__normalize_document_type_kwarg__mutmut_18`` —
        Meldung komplett kleingeschrieben (``"dokumentationstyp bitte nur
        einmal angeben (objekt oder id, nicht beides)."``).

        Beide Mutanten liefern eine von der Zusicherung abweichende
        ``exc.value.messages``-Liste; der Regex-Match der Bestandstests
        haette beide passieren lassen.
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Kontakt",
            entity_type=DocumentType.EntityType.EVENT,
        )
        data = {"document_type": doc_type, "document_type_id": doc_type.pk}

        with translation.override("de"), pytest.raises(ValidationError) as exc:
            client_main._normalize_document_type_kwarg(data)

        assert exc.value.messages == ["Dokumentationstyp bitte nur einmal angeben (Objekt oder ID, nicht beides)."]

    def test_an_unknown_document_type_id_is_rejected_with_the_exact_message(self, facility):
        """Pin: ``x__normalize_document_type_kwarg__mutmut_30`` — Meldung wird
        zu ``"XXDer Dokumentationstyp existiert nicht.XX"``;
        ``x__normalize_document_type_kwarg__mutmut_31`` — Meldung
        kleingeschrieben (``"der dokumentationstyp existiert nicht."``).

        Beide Mutanten weichen im Wert von ``exc.value.messages`` ab. Der
        zweite Aufruf deckt zugleich den ``except``-Zweig ab (Django wirft bei
        syntaktisch ungueltigen UUIDs ``ValidationError``, nicht ``ValueError``)
        — dieselbe Meldung, derselbe exakte Vergleich.
        """
        with translation.override("de"), pytest.raises(ValidationError) as exc:
            client_main._normalize_document_type_kwarg({"document_type_id": uuid.uuid4()})

        assert exc.value.messages == ["Der Dokumentationstyp existiert nicht."]

        with translation.override("de"), pytest.raises(ValidationError) as exc_malformed:
            client_main._normalize_document_type_kwarg({"document_type_id": "keine-uuid"})

        assert exc_malformed.value.messages == ["Der Dokumentationstyp existiert nicht."]


# ===========================================================================
# _redact_client_identity — die Spaltenliste des Personen-Saves
# ===========================================================================


class TestClientRedactionWritesOnlyTheRedactedColumns:
    """Die Tilgung schreibt acht Spalten — und ``updated_at`` gehoert nicht dazu.

    ``update_fields`` ist hier zweierlei: Lost-Update-Schutz gegenueber
    nebenlaeufigen Schreibern auf derselben Personenzeile UND die Zusicherung,
    dass ``updated_at`` (``auto_now=True``) unberuehrt bleibt. Der Stempel
    datiert die letzte fachliche Bearbeitung der Person; die Tilgung ist
    bereits ueber ``anonymized_at`` datiert und darf die Bearbeitungsspur
    nicht ueberschreiben.
    """

    def test_the_update_timestamp_survives_the_redaction(self, client_identified):
        """Pin: ``x__redact_client_identity__mutmut_14`` — ``update_fields``
        faellt auf ``None``.

        Beim Mutanten schreibt Django ALLE Spalten, also auch das
        ``auto_now``-Feld ``updated_at``: der Stempel in der DB waere danach
        der Redaktionszeitpunkt statt des unveraenderten Anlage-Stempels
        (``updated_at`` nach dem Lauf > ``before``). Das Original laesst ihn
        bitgenau stehen.
        """
        before = Client.objects.values_list("updated_at", flat=True).get(pk=client_identified.pk)

        client_main._redact_client_identity(client_identified)

        client_identified.refresh_from_db()
        assert client_identified.updated_at == before
        # Beleg, dass die Redaktion tatsaechlich lief (sonst waere der Pin leer).
        assert client_identified.pseudonym == f"Gelöscht-{str(client_identified.pk)[:8]}"


# ===========================================================================
# _redact_live_events — die Spaltenliste des Ereignis-Saves
# ===========================================================================


class TestLiveEventRedactionWritesOnlyTheRedactedColumns:
    """Der Ereignis-Save darf nur drei Spalten anfassen.

    Die Instanz stammt aus einem ``.iterator()``-Lesevorgang; zwischen Lesen
    und Speichern kann ein anderer Vorgang dieselbe Zeile fortschreiben. Mit
    ``update_fields=["data_json", "search_text", "updated_at"]`` betrifft das
    ``UPDATE`` genau die redigierten Spalten, alles andere bleibt der Stand
    der DB. Ohne die Liste schreibt Django den kompletten (veralteten)
    In-Memory-Stand zurueck — ein Lost-Update mitten in der Tilgung.
    """

    def test_a_concurrent_write_to_another_column_is_not_overwritten(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Pin: ``x__redact_live_events__mutmut_7`` — ``update_fields`` faellt
        auf ``None``.

        Ein ``pre_save``-Empfaenger spielt den nebenlaeufigen Schreiber: er
        setzt ``occurred_at`` der gerade zu speichernden Zeile per
        ``queryset.update()`` auf ``moved_to``, waehrend die In-Memory-Instanz
        noch den alten Zeitpunkt traegt. Original: ``occurred_at`` steht nach
        dem Lauf auf ``moved_to`` (die Spalte ist nicht in ``update_fields``).
        Mutant: der veraltete In-Memory-Wert ``original_at`` gewinnt.
        """
        original_at = timezone.now() - timedelta(days=3)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=original_at,
            data_json={"notiz": "Klartext"},
            created_by=staff_user,
        )
        moved_to = original_at - timedelta(days=1)
        state = {"fired": False}

        def _concurrent_writer(sender, instance, **kwargs):
            if state["fired"] or instance.pk != event.pk:
                return
            state["fired"] = True
            Event.objects.filter(pk=event.pk).update(occurred_at=moved_to)

        pre_save.connect(_concurrent_writer, sender=Event, dispatch_uid="pins094_main_1_live_events")
        try:
            client_main._redact_live_events(client_identified)
        finally:
            pre_save.disconnect(sender=Event, dispatch_uid="pins094_main_1_live_events")

        assert state["fired"] is True
        event.refresh_from_db()
        assert event.data_json == {}
        assert event.occurred_at == moved_to


# ===========================================================================
# _generalize_event_timestamps — Ladeprofil und Chunk-Grenze
# ===========================================================================


class TestTimestampGeneralizationStaysOneSelectAndOneUpdate:
    """Der Generalisierungs-Lauf laedt genau die zwei Spalten, die er braucht.

    ``.only("pk", "occurred_at")`` ist kein Stilmittel: der Lauf iteriert ueber
    ALLE Ereignisse eines Tombstones. Faellt ``"occurred_at"`` aus der
    ``.only()``-Liste, ist die Spalte deferred und jeder einzelne Zugriff
    ``event.occurred_at`` loest eine eigene Nachlade-Query aus — aus einem
    ``SELECT`` werden N+1, mitten in der Transaktion, die ohnehin schon Locks
    haelt.
    """

    def test_no_deferred_reload_per_event(
        self, facility, client_identified, doc_type_contact, staff_user, django_assert_num_queries
    ):
        """Pin: ``x__generalize_event_timestamps__mutmut_5`` — ``.only("pk",
        "occurred_at")`` wird zu ``.only("pk")``.

        Drei Ereignisse, alle ausserhalb des Monats-Buckets. Original: ein
        ``SELECT`` plus ein ``bulk_update`` (die Transaktions-Klammer des
        ``bulk_update`` zaehlt mit). Mutant: zusaetzlich DREI Nachlade-Queries
        fuer die deferred Spalte ``occurred_at`` — die Zusicherung auf die
        Query-Zahl schlaegt fehl.
        """
        base = timezone.make_aware(timezone.datetime(2026, 5, 17, 14, 35, 12))
        events = [
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=base + timedelta(days=index),
                data_json={},
                created_by=staff_user,
            )
            for index in range(3)
        ]
        event_ids = [event.pk for event in events]

        with django_assert_num_queries(2):
            client_main._generalize_event_timestamps(event_ids)

        for event in events:
            event.refresh_from_db()
            local = timezone.localtime(event.occurred_at)
            assert (local.day, local.hour, local.minute, local.second, local.microsecond) == (1, 0, 0, 0, 0)


class TestTimestampGeneralizationRespectsItsChunkSize:
    """Die Chunk-Grenze ist eine Lock-Grenze, kein Richtwert.

    Der Kommentar ueber ``_TIMESTAMP_BUCKET_CHUNK`` nennt den Grund: der Lauf
    sitzt in derselben Transaktion wie die uebrige Kaskade, ein einzelnes
    ``bulk_update`` ueber zehntausende Zeilen haelt sonst unnoetig lange Locks.
    Die Bestandstests pruefen nur den Endzustand aller Ereignisse — der ist
    bei jeder Chunk-Aufteilung derselbe.
    """

    def test_batches_never_exceed_the_chunk_size(
        self, facility, client_identified, doc_type_contact, staff_user, monkeypatch
    ):
        """Pin: ``x__generalize_event_timestamps__mutmut_35`` — die
        Flush-Schwelle ``len(pending) >= _TIMESTAMP_BUCKET_CHUNK`` wird zu
        ``len(pending) > _TIMESTAMP_BUCKET_CHUNK``.

        Chunk-Groesse 2, fuenf zu bucketende Ereignisse. Original: drei
        ``bulk_update``-Aufrufe mit den Batch-Groessen ``[2, 2, 1]``. Mutant:
        zwei Aufrufe mit ``[3, 2]`` — die erste Charge ueberschreitet die
        Grenze um genau die eine Zeile, die der Off-by-one erlaubt.
        """
        base = timezone.make_aware(timezone.datetime(2026, 5, 17, 14, 35, 12))
        event_ids = [
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=base + timedelta(days=index),
                data_json={},
                created_by=staff_user,
            ).pk
            for index in range(5)
        ]
        monkeypatch.setattr(client_main, "_TIMESTAMP_BUCKET_CHUNK", 2)

        batch_sizes: list[int] = []
        original_bulk_update = Event.objects.bulk_update

        def _spy(objs, fields, **kwargs):
            objs = list(objs)
            batch_sizes.append(len(objs))
            return original_bulk_update(objs, fields, **kwargs)

        monkeypatch.setattr(Event.objects, "bulk_update", _spy)

        client_main._generalize_event_timestamps(event_ids)

        assert batch_sizes == [2, 2, 1]


# ===========================================================================
# _redact_event_history — die Ops-Zeile des Deferral-Pfades
# ===========================================================================


class TestEventHistoryDeferralIsLoggedVerbatim:
    """Der uebersprungene Schritt muss sich im Log wiederfinden.

    Laeuft die Anonymisierung unter der App-Rolle (Art.-17-Sofortaktion), darf
    sie den Owner-DDL-Bypass nicht nehmen und ueberspringt die
    ``EventHistory``-Redaktion; der owner-seitige Nachlauf holt sie. Diese
    ``logger.info``-Zeile ist der EINZIGE Beleg dafuer, dass etwas
    aufgeschoben wurde und wie viele Zeilen betroffen sind — ohne sie ist der
    Aufschub unsichtbar. Verglichen wird die fertig formatierte Meldung, damit
    Text UND eingesetzte Zeilenzahl gepinnt sind.
    """

    def test_the_deferral_line_carries_text_and_row_count(
        self, facility, client_identified, doc_type_contact, staff_user, monkeypatch, caplog
    ):
        """Pin: ``x__redact_event_history__mutmut_10`` — die Zeilenzahl wird zu
        ``None`` (``rows=None``); ``x__redact_event_history__mutmut_11`` — der
        Meldungstext faellt weg, ``logger.info(history_qs.count())`` erzeugt
        die Meldung ``"1"``; ``x__redact_event_history__mutmut_12`` — das
        Argument faellt weg, die Meldung bleibt die unformatierte Vorlage
        ``"eventhistory_redaction_deferred rows=%s reason=..."``;
        ``x__redact_event_history__mutmut_13`` — der Text wird zu
        ``"XXeventhistory_redaction_deferred rows=%s reason=...XX"``.

        Alle vier weichen im Wert von ``record.getMessage()`` ab.
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "Klartext"},
            created_by=staff_user,
        )
        EventHistory.objects.create(
            event=event,
            changed_by=staff_user,
            action=EventHistory.Action.CREATE,
            data_before=None,
            data_after={"notiz": "Klartext"},
        )
        # Die Test-Verbindung IST Eigentuemerin der Tabelle; der Deferral-Pfad
        # gehoert der App-Rolle. Gestubbt wird deshalb genau das Praedikat,
        # das den Pfad auswaehlt — nicht das ORM.
        monkeypatch.setattr(client_main, "current_connection_owns", lambda table: False)

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            client_main._redact_event_history([event.pk])

        messages = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
        assert messages == ["eventhistory_redaction_deferred rows=1 reason=connection_is_not_table_owner"]


# ===========================================================================
# _redact_deletion_requests / _redact_case_deletion_requests — Zieltyp-Klammer
# ===========================================================================


class TestDeletionRequestRedactionKeepsItsTargetType:
    """``target_id`` allein ist keine Identitaet.

    ``DeletionRequest`` verweist generisch: ``target_type`` + ``target_id``.
    Beide Helfer redigieren bewusst nur EINEN Zieltyp — den Ereignis- bzw. den
    Fall-Antrag. Faellt der Typ-Filter, trifft der Helfer jede Zeile mit
    passender UUID, unabhaengig davon, worauf sie zeigt: die Vier-Augen-
    Begruendung eines fremden, womoeglich noch offenen Antrags waere weg.
    """

    def test_only_event_targets_lose_their_reason(self, facility, client_identified, doc_type_contact, staff_user):
        """Pin: ``x__redact_deletion_requests__mutmut_5`` — der Filter
        ``target_type="Event"`` faellt weg.

        Der Koeder traegt ``target_type="Client"`` und ABSICHTLICH dieselbe
        ``target_id`` wie das Ereignis. Original: sein ``reason`` bleibt
        ``"Fremdtyp, ID-Kollision"``. Mutant: er steht auf
        ``"[Anonymisiert]"``.
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        own = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=event.pk,
            reason="Ereignis doppelt erfasst",
            requested_by=staff_user,
        )
        decoy = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=event.pk,
            reason="Fremdtyp, ID-Kollision",
            requested_by=staff_user,
        )

        client_main._redact_deletion_requests([event.pk])

        own.refresh_from_db()
        decoy.refresh_from_db()
        assert own.reason == REDACTED
        assert decoy.reason == "Fremdtyp, ID-Kollision"

    def test_only_case_targets_lose_their_reason(self, facility, case_open, staff_user):
        """Pin: ``x__redact_case_deletion_requests__mutmut_5`` — der Filter
        ``target_type=DeletionRequest.TargetType.CASE`` faellt weg.

        Der Koeder traegt ``target_type="Event"`` mit derselben ``target_id``
        wie der Fall. Original: sein ``reason`` bleibt ``"Fremdtyp,
        ID-Kollision"``. Mutant: er steht auf ``"[Anonymisiert]"``.
        """
        own = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=case_open.pk,
            reason="Fall doppelt angelegt",
            requested_by=staff_user,
        )
        decoy = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=case_open.pk,
            reason="Fremdtyp, ID-Kollision",
            requested_by=staff_user,
        )

        client_main._redact_case_deletion_requests([case_open.pk])

        own.refresh_from_db()
        decoy.refresh_from_db()
        assert own.reason == REDACTED
        assert decoy.reason == "Fremdtyp, ID-Kollision"


# ===========================================================================
# _redact_retention_proposals — Zieltyp-Klammer und Spaltenliste
# ===========================================================================


def _make_proposal(facility, target_type, target_id, *, pseudonym="Klartext-Meier", category="identified"):
    """Ein Loeschvorschlag mit Klartext-Pseudonym in ``details``."""
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        deletion_due_at=timezone.localdate(),
        retention_category=category,
        details={"pseudonym": pseudonym, "contact_stage": "identified"},
    )


class TestRetentionProposalRedactionKeepsItsTargetType:
    """Auch der Loeschvorschlag zeigt generisch — der Typ gehoert in den Filter.

    ``RetentionProposal.TargetType`` kennt fachlich nur ``EVENT``; das Feld ist
    aber ein ``CharField`` ohne DB-Zwang, und der eindeutige Index laesst
    dieselbe ``target_id`` mit anderem ``target_type`` ausdruecklich zu. Der
    Koeder ist deshalb keine Konstruktion, sondern genau der Zustand, gegen den
    der Filter schuetzt.
    """

    def test_a_foreign_target_type_keeps_its_pseudonym(self, facility, client_identified, doc_type_contact, staff_user):
        """Pin: ``x__redact_retention_proposals__mutmut_5`` — der Filter
        ``target_type=RetentionProposal.TargetType.EVENT`` faellt weg.

        Original: ``details["pseudonym"]`` des Koeders bleibt
        ``"Klartext-Meier"``. Mutant: ``"[Anonymisiert]"``.
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        own = _make_proposal(facility, RetentionProposal.TargetType.EVENT, event.pk)
        decoy = _make_proposal(facility, "Case", event.pk)

        client_main._redact_retention_proposals([event.pk])

        own.refresh_from_db()
        decoy.refresh_from_db()
        assert own.details["pseudonym"] == REDACTED
        assert decoy.details["pseudonym"] == "Klartext-Meier"


class TestRetentionProposalRedactionWritesOnlyDetails:
    """Der Vorschlags-Save fasst genau eine Spalte an.

    Der Nachweis-Trail des Vorschlags (Status, Kategorie, Frist,
    Zurueckstellungen) ist ausdruecklich erhaltenswert; die Redaktion
    entfernt nur den Freitext aus ``details``. ``update_fields=["details"]``
    ist das, was diese Trennung durchsetzt — sonst schreibt der Save den
    kompletten, moeglicherweise veralteten In-Memory-Stand zurueck.
    """

    def test_a_concurrent_write_to_another_column_survives(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Pin: ``x__redact_retention_proposals__mutmut_19`` —
        ``proposal.save(update_fields=["details"])`` wird zu
        ``update_fields=None``.

        Ein ``pre_save``-Empfaenger setzt ``retention_category`` der Zeile per
        ``queryset.update()`` auf ``"reviewed"``, waehrend die In-Memory-
        Instanz noch ``"identified"`` traegt. Original: die Kategorie steht
        danach auf ``"reviewed"``. Mutant: der veraltete Wert
        ``"identified"`` gewinnt.
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        proposal = _make_proposal(facility, RetentionProposal.TargetType.EVENT, event.pk)
        state = {"fired": False}

        def _concurrent_writer(sender, instance, **kwargs):
            if state["fired"] or instance.pk != proposal.pk:
                return
            state["fired"] = True
            RetentionProposal.objects.filter(pk=proposal.pk).update(retention_category="reviewed")

        pre_save.connect(_concurrent_writer, sender=RetentionProposal, dispatch_uid="pins094_main_1_proposal")
        try:
            client_main._redact_retention_proposals([event.pk])
        finally:
            pre_save.disconnect(sender=RetentionProposal, dispatch_uid="pins094_main_1_proposal")

        assert state["fired"] is True
        proposal.refresh_from_db()
        assert proposal.details["pseudonym"] == REDACTED
        assert proposal.retention_category == "reviewed"


# ===========================================================================
# _redact_activities — der Kontext-Vertrag der Zeitstrom-Redaktion
# ===========================================================================


class TestActivityRedactionWritesTheVersionedContext:
    """Der redigierte Zeitstrom-Eintrag traegt genau EIN Kontext-Format.

    Der Renderer (``core.services.dashboard.activity``) liest ``context`` ueber
    den Schluessel ``"schema"`` und entscheidet daran, wie er die Zeile
    darstellt. Ein anderer Schluesselname oder eine andere Schema-Version
    laesst die Zeile aus dem versionierten Pfad fallen — die Bestandstests
    pruefen nur den ``"action"``-Schluessel und liessen alle drei Varianten
    durch.
    """

    def test_the_redacted_context_is_exactly_the_versioned_marker(self, facility, client_identified, staff_user):
        """Pin: ``x__redact_activities__mutmut_9`` — Schluessel ``"schema"``
        wird zu ``"XXschemaXX"``; ``x__redact_activities__mutmut_10`` —
        Schluessel wird zu ``"SCHEMA"``; ``x__redact_activities__mutmut_11`` —
        Wert ``1`` wird zu ``2``.

        Alle drei weichen im Wert von ``activity.context`` ab; der exakte
        Dict-Vergleich gegen ``ACTIVITY_CONTEXT_SCHEMA`` trifft alle drei.
        """
        activity = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target_type=ContentType.objects.get_for_model(Client),
            target_id=client_identified.pk,
            context={"schema": ACTIVITY_CONTEXT_SCHEMA, "action": Activity.Action.CLIENT_CREATED.value},
        )

        client_main._redact_activities(client_identified, [])

        activity.refresh_from_db()
        assert activity.context == REDACTED_CONTEXT


# ===========================================================================
# _redact_merged_source_references — die Quell-Spuren des Merge-Tombstones
# ===========================================================================


@pytest.fixture
def merged_source(facility, client_identified, client_qualified, doc_type_contact, staff_user):
    """Ein Merge-Tombstone samt Inventar-Zeile und EINEM gewanderten Ereignis.

    Nachgebaut statt ``merge_clients`` gefahren, weil der Pin genau die
    Nachlese-Funktion prueft und nicht den Merge: die Inventar-Zeile
    (``CLIENT_MERGED`` mit ``moved_events``) und der ``merged_into``-Zeiger
    sind der vollstaendige Eingabezustand der Funktion. ``merged_at`` liegt
    bewusst in der Zukunft, damit alle im Test angelegten Zeilen die
    ``<= merged_at``-Schranke erfuellen.
    """
    moved_event = Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    Client.objects.filter(pk=client_identified.pk).update(merged_into=client_qualified)
    client_identified.refresh_from_db()
    merged_at = timezone.now() + timedelta(hours=1)
    AuditLog.objects.create(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.CLIENT_MERGED,
        target_type="Client",
        target_id=str(client_identified.pk),
        timestamp=merged_at,
        detail={"merged_into": str(client_qualified.pk), "moved_events": [str(moved_event.pk)]},
    )
    return {
        "source": client_identified,
        "target": client_qualified,
        "moved_event": moved_event,
        "merged_at": merged_at,
    }


class TestMergeInventoryRowIsFoundByItsTargetType:
    """Die Inventar-Zeile wird ueber ``target_type="Client"`` gefunden.

    ``AuditLog`` verweist wie die Antragszeilen generisch. Eine
    ``CLIENT_MERGED``-Zeile mit einem anderen ``target_type`` und derselben
    ``target_id`` ist kein Kunstprodukt: der Merge schreibt zu einem Vorgang
    mehrere Audit-Zeilen. Faellt der Typ-Filter, greift ``.order_by(
    "-timestamp").first()`` zur juengsten passenden Zeile — der
    ``merged_into``-Abgleich scheitert dann, die Funktion bricht in den
    Degradationszweig ab und redigiert GAR NICHTS.
    """

    def test_a_foreign_target_type_row_does_not_win(self, facility, staff_user, merged_source):
        """Pin: ``x__redact_merged_source_references__mutmut_10`` — der Filter
        ``target_type="Client"`` faellt weg.

        Der Koeder ist juenger und traegt ein fremdes ``merged_into``.
        Original: der Antrag des gewanderten Ereignisses steht danach auf
        ``"[Anonymisiert]"``. Mutant: der Koeder gewinnt das
        ``order_by("-timestamp")``, der ``merged_into``-Abgleich scheitert,
        der ``reason`` bleibt ``"Klartext-Begruendung"`` stehen.
        """
        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.CLIENT_MERGED,
            target_type="Event",
            target_id=str(merged_source["source"].pk),
            timestamp=merged_source["merged_at"] + timedelta(minutes=5),
            detail={"merged_into": str(uuid.uuid4()), "moved_events": []},
        )
        request = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=merged_source["moved_event"].pk,
            reason="Klartext-Begruendung",
            requested_by=staff_user,
        )

        client_main._redact_merged_source_references(merged_source["source"])

        request.refresh_from_db()
        assert request.reason == REDACTED


class TestMissingMergeInventoryIsLoggedVerbatim:
    """Der degradierende Zweig ist nur so viel wert wie seine Log-Zeile.

    Fehlt die Inventar-Zeile, faellt die Mit-Redaktion der gewanderten
    Ereignisse ersatzlos aus — der Retention-Lauf laeuft weiter, aber
    Klartext-Spuren der Quelle bleiben stehen. Sichtbar wird dieser Zustand
    ausschliesslich ueber die Warnung samt betroffener Personen-ID; ohne beide
    ist der Ausfall stumm.
    """

    def test_the_warning_names_the_event_and_the_client(self, client_identified, client_qualified, caplog):
        """Pin: ``x__redact_merged_source_references__mutmut_25`` — Text wird
        ``None`` (Meldung ``"None"``);
        ``x__redact_merged_source_references__mutmut_29`` — Text wird
        ``"XXclient_merge_inventory_missingXX"``;
        ``x__redact_merged_source_references__mutmut_30`` — Text wird
        ``"CLIENT_MERGE_INVENTORY_MISSING"``;
        ``x__redact_merged_source_references__mutmut_26`` — ``extra`` wird
        ``None``; ``x__redact_merged_source_references__mutmut_28`` — das
        ``extra``-Argument faellt ganz weg;
        ``x__redact_merged_source_references__mutmut_31`` — Schluessel wird
        ``"XXclient_idXX"``;
        ``x__redact_merged_source_references__mutmut_32`` — Schluessel wird
        ``"CLIENT_ID"``.

        Die drei Text-Mutanten weichen in ``record.getMessage()`` ab, die vier
        ``extra``-Mutanten lassen ``record.client_id`` fehlen (``getattr(...,
        None)`` liefert ``None`` statt der Personen-UUID).
        """
        Client.objects.filter(pk=client_identified.pk).update(merged_into=client_qualified)
        client_identified.refresh_from_db()

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            client_main._redact_merged_source_references(client_identified)

        records = [record for record in caplog.records if record.name == LOGGER_NAME]
        assert [record.getMessage() for record in records] == ["client_merge_inventory_missing"]
        assert [getattr(record, "client_id", None) for record in records] == [str(client_identified.pk)]


class TestMovedEventTraceRedactionKeepsEveryTargetType:
    """Jede der vier Nachlese-Queries traegt ihre eigene Zieltyp-Klammer.

    Die ``moved_events``-UUIDs sind Ereignis-PKs. Alle vier Modelle
    (``Activity``, ``DeletionRequest``, ``LegalHold``, ``RetentionProposal``)
    referenzieren generisch, und jedes kennt neben ``Event`` weitere
    Zieltypen. Ohne den Typ-Filter redigiert die Nachlese fremde Zeilen mit
    kollidierender UUID mit — Zeilen, die zur LEBENDEN Akte des Merge-Ziels
    gehoeren koennen und deren Freitext ausdruecklich erst mit dessen eigener
    Redaktion fallen darf.
    """

    def test_activities_of_a_foreign_target_type_stay_untouched(self, facility, staff_user, merged_source):
        """Pin: ``x__redact_merged_source_references__mutmut_45`` — der Filter
        ``target_type=ContentType.objects.get_for_model(Event)`` faellt weg.

        Original: der Koeder (Ziel-Typ ``Client``, dieselbe ``target_id`` wie
        das gewanderte Ereignis) behaelt seinen Kontext
        ``{"schema": 1, "action": "client_created"}``. Mutant: er traegt
        danach den Redaktionsmarker.
        """
        own = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target_type=ContentType.objects.get_for_model(Event),
            target_id=merged_source["moved_event"].pk,
            context={"schema": ACTIVITY_CONTEXT_SCHEMA, "action": Activity.Action.EVENT_CREATED.value},
        )
        decoy_context = {"schema": ACTIVITY_CONTEXT_SCHEMA, "action": Activity.Action.CLIENT_CREATED.value}
        decoy = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target_type=ContentType.objects.get_for_model(Client),
            target_id=merged_source["moved_event"].pk,
            context=dict(decoy_context),
        )

        client_main._redact_merged_source_references(merged_source["source"])

        own.refresh_from_db()
        decoy.refresh_from_db()
        assert own.context == REDACTED_CONTEXT
        assert decoy.context == decoy_context

    def test_the_redacted_activity_context_is_exactly_the_versioned_marker(self, facility, staff_user, merged_source):
        """Pin: ``x__redact_merged_source_references__mutmut_50`` — Schluessel
        ``"schema"`` wird zu ``"XXschemaXX"``;
        ``x__redact_merged_source_references__mutmut_51`` — Schluessel wird zu
        ``"SCHEMA"``; ``x__redact_merged_source_references__mutmut_52`` — Wert
        ``1`` wird zu ``2``.

        Alle drei weichen im Wert von ``activity.context`` ab.
        """
        activity = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target_type=ContentType.objects.get_for_model(Event),
            target_id=merged_source["moved_event"].pk,
            context={"schema": ACTIVITY_CONTEXT_SCHEMA, "action": Activity.Action.EVENT_CREATED.value},
        )

        client_main._redact_merged_source_references(merged_source["source"])

        activity.refresh_from_db()
        assert activity.context == REDACTED_CONTEXT

    def test_deletion_requests_of_a_foreign_target_type_stay_untouched(self, facility, staff_user, merged_source):
        """Pin: ``x__redact_merged_source_references__mutmut_59`` — der Filter
        ``target_type=DeletionRequest.TargetType.EVENT`` faellt weg.

        Original: der Koeder (``target_type="Case"``, dieselbe ``target_id``)
        behaelt ``"Fremdtyp, ID-Kollision"``. Mutant: ``"[Anonymisiert]"``.
        """
        own = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=merged_source["moved_event"].pk,
            reason="Klartext-Begruendung",
            requested_by=staff_user,
        )
        decoy = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=merged_source["moved_event"].pk,
            reason="Fremdtyp, ID-Kollision",
            requested_by=staff_user,
        )

        client_main._redact_merged_source_references(merged_source["source"])

        own.refresh_from_db()
        decoy.refresh_from_db()
        assert own.reason == REDACTED
        assert decoy.reason == "Fremdtyp, ID-Kollision"

    def test_legal_holds_of_a_foreign_target_type_stay_untouched(self, facility, lead_user, merged_source):
        """Pin: ``x__redact_merged_source_references__mutmut_69`` — der Filter
        ``target_type="Event"`` faellt weg.

        Original: der Koeder (``target_type="Case"``, dieselbe ``target_id``)
        behaelt ``"Fremdtyp, ID-Kollision"``. Mutant: ``"[Anonymisiert]"``.
        """
        own = LegalHold.objects.create(
            facility=merged_source["source"].facility,
            target_type="Event",
            target_id=merged_source["moved_event"].pk,
            reason="Klartext-Begruendung",
            created_by=lead_user,
        )
        decoy = LegalHold.objects.create(
            facility=merged_source["source"].facility,
            target_type="Case",
            target_id=merged_source["moved_event"].pk,
            reason="Fremdtyp, ID-Kollision",
            created_by=lead_user,
        )

        client_main._redact_merged_source_references(merged_source["source"])

        own.refresh_from_db()
        decoy.refresh_from_db()
        assert own.reason == REDACTED
        assert decoy.reason == "Fremdtyp, ID-Kollision"

    def test_retention_proposals_of_a_foreign_target_type_stay_untouched(self, facility, merged_source):
        """Pin: ``x__redact_merged_source_references__mutmut_83`` — der Filter
        ``target_type=RetentionProposal.TargetType.EVENT`` faellt weg.

        Original: ``details["pseudonym"]`` des Koeders bleibt
        ``"Klartext-Meier"``. Mutant: ``"[Anonymisiert]"``.
        """
        own = _make_proposal(facility, RetentionProposal.TargetType.EVENT, merged_source["moved_event"].pk)
        decoy = _make_proposal(facility, "Case", merged_source["moved_event"].pk)

        client_main._redact_merged_source_references(merged_source["source"])

        own.refresh_from_db()
        decoy.refresh_from_db()
        assert own.details["pseudonym"] == REDACTED
        assert decoy.details["pseudonym"] == "Klartext-Meier"


class TestMovedEventProposalRedactionWritesOnlyDetails:
    """Auch die Nachlese schreibt nur ``details`` zurueck.

    Dieselbe Zusicherung wie in :func:`_redact_retention_proposals` — hier auf
    dem Merge-Pfad, wo die Zeile zusaetzlich zur lebenden Akte des ZIELS
    gehoert und ein Lost-Update deshalb Daten trifft, die der Vorgang gar
    nicht anfassen darf.
    """

    def test_a_concurrent_write_to_another_column_survives(self, facility, merged_source):
        """Pin: ``x__redact_merged_source_references__mutmut_98`` —
        ``proposal.save(update_fields=["details"])`` wird zu
        ``update_fields=None``.

        Ein ``pre_save``-Empfaenger setzt ``retention_category`` per
        ``queryset.update()`` auf ``"reviewed"``, waehrend die
        In-Memory-Instanz noch ``"identified"`` traegt. Original:
        ``"reviewed"`` bleibt stehen. Mutant: der veraltete Wert
        ``"identified"`` gewinnt.
        """
        proposal = _make_proposal(facility, RetentionProposal.TargetType.EVENT, merged_source["moved_event"].pk)
        state = {"fired": False}

        def _concurrent_writer(sender, instance, **kwargs):
            if state["fired"] or instance.pk != proposal.pk:
                return
            state["fired"] = True
            RetentionProposal.objects.filter(pk=proposal.pk).update(retention_category="reviewed")

        pre_save.connect(_concurrent_writer, sender=RetentionProposal, dispatch_uid="pins094_main_1_merge_proposal")
        try:
            client_main._redact_merged_source_references(merged_source["source"])
        finally:
            pre_save.disconnect(sender=RetentionProposal, dispatch_uid="pins094_main_1_merge_proposal")

        assert state["fired"] is True
        proposal.refresh_from_db()
        assert proposal.details["pseudonym"] == REDACTED
        assert proposal.retention_category == "reviewed"


# ===========================================================================
# get_pending_deletion_request / pending_*_client_ids — Anzeige-Wahrheit
# ===========================================================================


class TestPendingDeletionRequestLookupIsScoped:
    """Der Antrags-Lookup ist tenant- UND typgebunden.

    Die Funktion ist EINE Quelle fuer Detail-Banner, Antragsseite und den
    Idempotenz-Zweig der Antrags-View: liefert sie einen fremden Antrag, wird
    eine Person faelschlich als „Loeschung beantragt" ausgewiesen und die
    Antragsstellung blockiert — mit der Begruendung und dem Namen der
    antragstellenden Person aus einer FREMDEN Einrichtung im Banner.
    """

    def test_a_request_from_another_facility_is_not_returned(
        self, facility, second_facility, second_facility_user, client_identified
    ):
        """Pin: ``x_get_pending_deletion_request__mutmut_6`` — der Filter
        ``facility_id=client.facility_id`` faellt weg.

        Der Antrag der zweiten Einrichtung traegt ABSICHTLICH die
        ``target_id`` der Person aus der ersten. Original: ``None``. Mutant:
        die fremde Antragszeile.
        """
        DeletionRequest.objects.create(
            facility=second_facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_identified.pk,
            reason="Fremde Einrichtung",
            requested_by=second_facility_user,
        )

        assert get_pending_deletion_request(client_identified) is None

    def test_a_request_with_another_target_type_is_not_returned(self, facility, client_identified, staff_user):
        """Pin: ``x_get_pending_deletion_request__mutmut_7`` — der Filter
        ``target_type=DeletionRequest.TargetType.CLIENT`` faellt weg.

        Der Ereignis-Antrag traegt ABSICHTLICH die ``target_id`` der Person.
        Original: ``None``. Mutant: die typfremde Antragszeile.
        """
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=client_identified.pk,
            reason="Fremdtyp, ID-Kollision",
            requested_by=staff_user,
        )

        assert get_pending_deletion_request(client_identified) is None

    def test_the_requesting_user_comes_with_the_row(
        self, facility, client_identified, staff_user, django_assert_num_queries
    ):
        """Pin: ``x_get_pending_deletion_request__mutmut_1`` —
        ``.select_related("requested_by")`` wird zu ``.select_related(None)``.

        Jede Anzeigeflaeche nennt die antragstellende Person; deshalb wird sie
        mitgeladen. Original: EINE Query fuer Antrag samt Nutzer. Mutant:
        ``select_related(None)`` verwirft die Vorgabe, der Zugriff auf
        ``requested_by`` loest eine zweite Query aus.
        """
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_identified.pk,
            reason="Loeschung gewuenscht",
            requested_by=staff_user,
        )

        with django_assert_num_queries(1):
            request = get_pending_deletion_request(client_identified)
            assert request.requested_by.username == staff_user.username


class TestPendingBadgeQueriesStayScoped:
    """Die Kennzeichen der Personenliste gelten nur fuer die abgefragten Zeilen.

    Beide Helfer bekommen die bereits paginierte Seite herein und liefern die
    Teilmenge mit laufendem Antrag. Zwei Klammern tragen das: der Zieltyp
    (``Client``) und die uebergebene ID-Menge. Faellt die erste, kennzeichnet
    ein Ereignis-Antrag eine Person; faellt die zweite, liefert der Helfer PKs
    von Personen, die gar nicht auf der Seite stehen.
    """

    def test_deletion_badge_ignores_foreign_target_types(self, facility, client_identified, staff_user):
        """Pin: ``x_pending_deletion_client_ids__mutmut_11`` — der Filter
        ``target_type=DeletionRequest.TargetType.CLIENT`` faellt weg.

        Original: ``set()``. Mutant: ``{client_identified.pk}`` — die Person
        traegt das Kennzeichen wegen eines EREIGNIS-Antrags.
        """
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=client_identified.pk,
            reason="Fremdtyp, ID-Kollision",
            requested_by=staff_user,
        )

        assert pending_deletion_client_ids(facility, [client_identified]) == set()

    def test_deletion_badge_ignores_clients_outside_the_page(
        self, facility, client_identified, client_qualified, staff_user
    ):
        """Pin: ``x_pending_deletion_client_ids__mutmut_12`` — der Filter
        ``target_id__in=ids`` faellt weg.

        Abgefragt wird nur ``client_identified``; ``client_qualified`` hat den
        einzigen laufenden Antrag. Original: ``set()``. Mutant:
        ``{client_qualified.pk}`` — eine PK, die auf der abgefragten Seite gar
        nicht vorkommt.
        """
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_qualified.pk,
            reason="Loeschung gewuenscht",
            requested_by=staff_user,
        )

        assert pending_deletion_client_ids(facility, [client_identified]) == set()

    def test_merge_badge_ignores_foreign_target_types(self, facility, client_identified, client_qualified, staff_user):
        """Pin: ``x_pending_merge_client_ids__mutmut_12`` — der Filter
        ``target_type=DeletionRequest.TargetType.CLIENT`` faellt weg.

        Original: ``set()``. Mutant: ``{client_identified.pk}``.
        """
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=client_identified.pk,
            merge_into=client_qualified,
            reason="Fremdtyp, ID-Kollision",
            requested_by=staff_user,
        )

        assert pending_merge_client_ids(facility, [client_identified]) == set()

    def test_merge_badge_ignores_clients_outside_the_page(
        self, facility, client_identified, client_qualified, staff_user
    ):
        """Pin: ``x_pending_merge_client_ids__mutmut_13`` — der Filter
        ``target_id__in=ids`` faellt weg.

        Abgefragt wird nur ``client_identified``; der Merge-Antrag gehoert
        einer dritten Person. Original: ``set()``. Mutant:
        ``{third.pk}``.
        """
        third = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Pin-094-Dritte",
            created_by=staff_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=third.pk,
            merge_into=client_qualified,
            reason="Doppelt angelegt",
            requested_by=staff_user,
        )

        assert pending_merge_client_ids(facility, [client_identified]) == set()


# ===========================================================================
# anonymize_eligible_soft_deleted_clients — der Sweep laeuft weiter
# ===========================================================================


class TestTrashSweepSkipsInsteadOfStopping:
    """Eine bereits erledigte Zeile ueberspringt der Sweep — er bricht nicht ab.

    Der Kommentar an der Stelle nennt den Fall beim Namen: ein paralleler Lauf
    war schneller (oder die Person wurde wiederhergestellt). Die PK-Liste wird
    vorab materialisiert; genau deshalb kann eine Zeile beim Sperren nicht mehr
    passen. ``continue`` ueberspringt sie, ``break`` liesse den Rest der
    faelligen Personen fuer diesen Lauf liegen — bei jedem naechtlichen Lauf
    aufs Neue, solange dieselbe Zeile vorne steht.
    """

    def test_a_row_already_handled_elsewhere_does_not_stop_the_run(
        self, facility, settings_obj, staff_user, monkeypatch
    ):
        """Pin: ``x_anonymize_eligible_soft_deleted_clients__mutmut_39`` — das
        ``continue`` im Zweig „Zeile bereits behandelt" wird zu ``break``.

        Die Kandidatenmenge wird deterministisch auf ``[lost, kept]`` gesetzt.
        ``lost`` ist eine ECHTE, bereits getilgte Papierkorb-Zeile
        (``anonymized_at`` gesetzt) und faellt deshalb beim erneuten Filtern
        unter der Sperre heraus — genau der Zustand nach einem parallelen
        Lauf. Original: ``lost`` wird uebersprungen, ``kept`` anonymisiert,
        Rueckgabe ``1``. Mutant: der Lauf bricht bei ``lost`` ab, Rueckgabe
        ``0`` und ``kept.anonymized_at`` bleibt ``None``.
        """
        expired = timezone.now() - timedelta(days=settings_obj.client_trash_days + 5)
        lost = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Pin-094-Bereits-getilgt",
            created_by=staff_user,
            is_deleted=True,
            deleted_at=expired,
            anonymized_at=timezone.now(),
        )
        kept = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Pin-094-Faellig",
            created_by=staff_user,
            is_deleted=True,
            deleted_at=expired,
        )

        def _candidates(queryset, facility_arg):
            # Reihenfolge festgenagelt: die bereits getilgte Zeile zuerst.
            return Client.objects.filter(pk__in=[lost.pk, kept.pk]).order_by("pseudonym")

        monkeypatch.setattr(client_main, "exclude_clients_with_active_holds", _candidates)

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        assert count == 1
        kept.refresh_from_db()
        assert kept.anonymized_at is not None
