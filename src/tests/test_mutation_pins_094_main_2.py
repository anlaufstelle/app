"""Pin-Tests zum Meilenstein release-0.94 — triagierte Survivors in ``core.services.client.main``.

Quelle: Survivor-Triage des Moduls ``src/core/services/client/main.py``
(Modul-Batch ``main_2``, Mutantenbaum ``mutants/src/core/services/client/main.py``).
Gepinnt wird die Vorgabe aus der Survivor-Triage release-0.94 (Ledger des
Meilensteins) (20 Mutanten, Herkunft „nachlauf"). Schwerpunkte:

* die **Zieltyp-Klammern** der vier Massen-Redaktionen des Merge-Tombstones
  (``Activity``/``DeletionRequest``/``LegalHold``/``RetentionProposal``) und
  dieselbe Klammer im Nicht-Merge-Pfad ``_redact_retention_proposals`` —
  bislang existierte in keinem Test eine Zeile mit abweichendem ``target_type``,
  die Filter waren damit unbelegt;
* der **Kontext-Vertrag** der redigierten ``Activity``-Zeilen
  (``{"schema": 1, "action": "redacted"}``) — die Merge-Tests prueften nur
  ``context["action"]``, der Schema-Schluessel selbst war frei mutierbar;
* die ``update_fields``-Beschraenkung der beiden ``RetentionProposal``-Saves
  (Lost-Update-Schutz; gepinnt ueber das ``update_fields``-Argument des
  ``post_save``-Signals, deterministisch ohne Threads);
* die **Mandanten- und Zieltyp-Grenze** der drei Antrags-Abfragen
  (``get_pending_deletion_request``, ``pending_deletion_client_ids``,
  ``pending_merge_client_ids``) samt der dokumentierten Seiten-Eingrenzung
  („bewusst nur fuer die bereits paginierte Seite") und dem ``select_related``;
* das **Ops-Signal** des Papierkorb-Sweeps: ``count += 1`` (Rueckgabewert > 1)
  und der ``continue``-Zweig des Sperr-Rennens, der den Sweep NICHT abbrechen
  darf.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten) — Dicts werden
als GANZES verglichen, Log-Felder als voller String; KEINE Imports aus
``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py`` bzw.
werden als echte Model-Objekte angelegt (keine Fakes). Uebersetzte Texte
werden hier bewusst nicht zugesichert (alle verglichenen Werte sind stabile
Schluessel bzw. selbst gesetzte Freitexte), ein ``translation.override`` ist
daher nicht noetig. Jeder Test nennt seine Mutanten in der Docstring samt dem
Wert, der beim Mutanten abweicht.
"""

import logging
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from django.utils import timezone

from core.models import Activity, Client, Event, LegalHold
from core.models.retention import RetentionProposal
from core.models.workitem import DeletionRequest
from core.services.client import (
    anonymize_eligible_soft_deleted_clients,
    get_pending_deletion_request,
    pending_deletion_client_ids,
    pending_merge_client_ids,
)
from core.services.client import main as client_main
from core.services.client.main import _redact_merged_source_references, _redact_retention_proposals
from core.services.client.merge import merge_clients
from core.services.dashboard import log_activity

pytestmark = pytest.mark.django_db

MODULE_LOGGER = "core.services.client.main"


@pytest.fixture
def source(facility, staff_user):
    """Merge-Quelle — wird zum Tombstone und traegt die Mit-Redaktion."""
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin-Quelle",
        created_by=staff_user,
    )


@pytest.fixture
def merge_target(facility, staff_user):
    """Merge-Ziel — seine Zeilen bleiben unberuehrt."""
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin-Ziel",
        created_by=staff_user,
    )


def _event(facility, client, doc_type, user):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=user,
    )


@contextmanager
def _captured_update_fields(model):
    """Sammelt das ``update_fields``-Argument jedes ``save()`` von ``model``.

    Django normalisiert ``save(update_fields=[...])`` zu einem ``frozenset``
    und reicht es an ``post_save`` weiter; ``save()`` ohne Einschraenkung
    sendet ``None``. Das macht die Schreib-Beschraenkung deterministisch und
    ohne Nebenlaeufigkeit beobachtbar — genau die Achse, auf der die
    ``update_fields=None``-Mutanten (Lost Update) liegen.
    """
    seen = []

    def _receiver(sender, instance, update_fields=None, **kwargs):  # noqa: ARG001
        seen.append(update_fields)

    post_save.connect(_receiver, sender=model, weak=False)
    try:
        yield seen
    finally:
        post_save.disconnect(_receiver, sender=model)


def _capture_warnings(caplog, call):
    """Fuehrt ``call()`` aus und liefert die WARNING-Records des Modul-Loggers.

    Der ``core``-Logger hat ``propagate=False`` (``settings/base.py``) — der
    ``caplog``-Handler muss deshalb explizit an den Modul-Logger gehaengt
    werden (gleiches Muster wie ``test_health.py``).
    """
    module_logger = logging.getLogger(MODULE_LOGGER)
    caplog.set_level(logging.WARNING)
    module_logger.addHandler(caplog.handler)
    try:
        call()
    finally:
        module_logger.removeHandler(caplog.handler)
    return list(caplog.records)


# ============================================================================
# core.services.client.main._redact_merged_source_references
# ============================================================================


class TestMergeInventoryDegradationNamesTheClient:
    """Die degradierende Warnung muss sagen, WELCHE Person betroffen ist."""

    def test_missing_inventory_warning_carries_the_client_id(self, source, merge_target, caplog):
        """Pin: ``x__redact_merged_source_references__mutmut_33`` —
        ``extra={"client_id": str(client.pk)}`` -> ``str(None)``.

        Fehlt die ``CLIENT_MERGED``-Inventar-Zeile (Alt-/Reparaturzustand),
        degradiert die Mit-Redaktion und loggt — das ist der EINZIGE Hinweis
        darauf, dass Quell-Spuren stehen bleiben. Der Mutant schreibt konstant
        ``client_id == "None"`` und macht die Warnung fuer die Ops-Auswertung
        wertlos (welche Akte?). Der Test setzt ``merged_into`` ohne Merge-Lauf,
        damit keine Inventar-Zeile existiert.
        """
        Client.objects.filter(pk=source.pk).update(merged_into=merge_target)
        source.refresh_from_db()

        records = _capture_warnings(caplog, lambda: _redact_merged_source_references(source))

        assert records != []
        # Als Menge, weil derselbe Record je nach Handler-Kette mehrfach
        # eingesammelt wird — die Zusicherung bleibt eine EXAKTE Gleichheit.
        # Original: die UUID der Quelle; Mutant _33: der String "None".
        assert {(record.getMessage(), record.client_id) for record in records} == {
            ("client_merge_inventory_missing", str(source.pk))
        }


class TestMergeRedactionKeepsTheActivityContextContract:
    """Redigierte Activity-Zeilen tragen den vollstaendigen Kontext-Vertrag."""

    def test_redacted_activity_context_is_the_full_versioned_dict(
        self, facility, source, merge_target, doc_type_contact, staff_user, lead_user
    ):
        """Pin: ``x__redact_merged_source_references__mutmut_50`` (``"XXschemaXX"``),
        ``__mutmut_51`` (``"SCHEMA"``) und ``__mutmut_52`` (``"schema": 2``).

        Die Merge-Tests pruefen nur ``context["action"]`` — der
        Versions-Schluessel des Kontext-Vertrags (``ACTIVITY_CONTEXT_SCHEMA``,
        ``services/dashboard/activity.py``; Migration 0163) war damit frei
        mutierbar. Diese Zusicherung vergleicht das GANZE dict:
        Original ``{"schema": 1, "action": "redacted"}``; ``_50`` liefert
        ``{"XXschemaXX": 1, ...}``, ``_51`` ``{"SCHEMA": 1, ...}``, ``_52``
        ``{"schema": 2, ...}``.
        """
        event = _event(facility, source, doc_type_contact, staff_user)
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=event,
            action=Activity.Action.EVENT_CREATED,
        )

        merge_clients(source, merge_target, lead_user)
        source.refresh_from_db()
        _redact_merged_source_references(source)

        activity.refresh_from_db()
        assert activity.context == {"schema": 1, "action": Activity.Action.REDACTED.value}


class TestMergeRedactionStaysWithinTheEventTargetType:
    """Die vier Massen-Updates duerfen nur EREIGNIS-Ziele treffen.

    ``target_id`` ist in allen vier Modellen ein freies ``UUIDField``; der
    ``target_type``-Filter ist die einzige Typ-Sicherung dieser ``.update()``-
    Schreibpfade. In den bestehenden Tests existieren ausschliesslich
    Event-Zeilen — ein Negativfall fehlte komplett.
    """

    def test_rows_of_other_target_types_survive_the_tombstone_redaction(
        self, facility, source, merge_target, doc_type_contact, staff_user, lead_user
    ):
        """Pin: ``x__redact_merged_source_references__mutmut_45`` (Activity ohne
        ``target_type``), ``__mutmut_59`` (DeletionRequest ohne ``target_type``),
        ``__mutmut_69`` (LegalHold ohne ``target_type``) und ``__mutmut_83``
        (RetentionProposal ohne ``target_type``).

        Alle vier Fremdtyp-Zeilen tragen dieselbe ``target_id`` wie das
        gewanderte Ereignis (natuerlich kollisionsfrei, hier von Hand gebaut).
        Original: sie bleiben unveraendert. Mutanten: die jeweilige Zeile wird
        mit-redigiert — ``context`` wird zu
        ``{"schema": 1, "action": "redacted"}`` (``_45``), ``reason`` zu
        ``"[Anonymisiert]"`` (``_59``/``_69``) bzw. ``details["pseudonym"]`` zu
        ``"[Anonymisiert]"`` (``_83``). Der Event-Hold ist die Positiv-Kontrolle:
        er MUSS redigiert werden, sonst waere der Test leer gruen.
        """
        event = _event(facility, source, doc_type_contact, staff_user)
        client_ct = ContentType.objects.get_for_model(Client)

        foreign_activity = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target_type=client_ct,
            target_id=event.pk,
            context={"schema": 1, "action": Activity.Action.CLIENT_UPDATED.value},
        )
        foreign_request = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=event.pk,
            reason="Doppelt dokumentiert im Fall",
            requested_by=staff_user,
        )
        foreign_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Case",
            target_id=event.pk,
            reason="Anfrage zum Fallverlauf",
            created_by=lead_user,
            expires_at=timezone.localdate() - timedelta(days=1),
        )
        foreign_proposal = RetentionProposal.objects.create(
            facility=facility,
            target_type="Case",
            target_id=event.pk,
            deletion_due_at=timezone.localdate(),
            details={"pseudonym": "Fremd-Pseudonym"},
            retention_category="identified",
        )
        event_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Anfrage zum Kontakt",
            created_by=lead_user,
            expires_at=timezone.localdate() - timedelta(days=1),
        )

        merge_clients(source, merge_target, lead_user)
        source.refresh_from_db()
        _redact_merged_source_references(source)

        foreign_activity.refresh_from_db()
        foreign_request.refresh_from_db()
        foreign_hold.refresh_from_db()
        foreign_proposal.refresh_from_db()
        event_hold.refresh_from_db()

        assert foreign_activity.context == {"schema": 1, "action": Activity.Action.CLIENT_UPDATED.value}
        assert foreign_request.reason == "Doppelt dokumentiert im Fall"
        assert foreign_hold.reason == "Anfrage zum Fallverlauf"
        assert foreign_proposal.details == {"pseudonym": "Fremd-Pseudonym"}
        # Positiv-Kontrolle: der Event-Zweig hat wirklich gearbeitet.
        assert event_hold.reason == "[Anonymisiert]"


class TestMergeRedactionWritesOnlyTheDetailsColumn:
    """Der Vorschlags-Save des Merge-Pfads bleibt auf ``details`` beschraenkt."""

    def test_proposal_save_is_limited_to_details(
        self, facility, source, merge_target, doc_type_contact, staff_user, lead_user
    ):
        """Pin: ``x__redact_merged_source_references__mutmut_98`` —
        ``save(update_fields=["details"])`` -> ``save(update_fields=None)``.

        Die Zeilen werden per ``.iterator()`` gelesen; ``update_fields=None``
        schreibt ALLE Spalten der zuvor geladenen Instanz zurueck (``status``,
        ``deletion_due_at``, ``deferred_until``, ``defer_count``,
        ``retention_category``) und macht aus dem Redaktions-Save einen Lost
        Update auf einem Nachweis-Objekt. Original: ``post_save`` sieht
        ``frozenset({"details"})``; Mutant: ``None``.
        """
        event = _event(facility, source, doc_type_contact, staff_user)
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=event.pk,
            deletion_due_at=timezone.localdate(),
            details={"pseudonym": source.pseudonym},
            retention_category="identified",
        )

        merge_clients(source, merge_target, lead_user)
        source.refresh_from_db()

        with _captured_update_fields(RetentionProposal) as seen:
            _redact_merged_source_references(source)

        assert seen == [frozenset({"details"})]


# ============================================================================
# core.services.client.main._redact_retention_proposals
# ============================================================================


class TestRetentionProposalRedactionStaysWithinTheEventTargetType:
    """Auch der Nicht-Merge-Pfad redigiert nur EREIGNIS-Vorschlaege."""

    def test_a_proposal_of_another_target_type_is_untouched(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Pin: ``x__redact_retention_proposals__mutmut_5`` — der Filter
        ``target_type=RetentionProposal.TargetType.EVENT`` entfaellt.

        ``TargetType`` kennt heute nur ``EVENT``, ``choices`` sind aber keine
        DB-Constraint: eine Zeile mit abweichendem Typ ist konstruierbar
        (Reparaturzustand, kuenftiger Zieltyp) und wuerde vom Mutanten
        mit-redigiert. Original: ``details == {"pseudonym": "Fall-PII"}``;
        Mutant: ``details["pseudonym"] == "[Anonymisiert]"``. Der
        Event-Vorschlag ist die Positiv-Kontrolle.
        """
        event = _event(facility, client_identified, doc_type_contact, staff_user)
        event_proposal = RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=event.pk,
            deletion_due_at=timezone.localdate(),
            details={"pseudonym": "Quelle-PII", "contact_stage": "identified"},
            retention_category="identified",
        )
        foreign_proposal = RetentionProposal.objects.create(
            facility=facility,
            target_type="Case",
            target_id=event.pk,
            deletion_due_at=timezone.localdate(),
            details={"pseudonym": "Fall-PII"},
            retention_category="identified",
        )

        _redact_retention_proposals([event.pk])

        event_proposal.refresh_from_db()
        foreign_proposal.refresh_from_db()
        assert foreign_proposal.details == {"pseudonym": "Fall-PII"}
        # Positiv-Kontrolle: der Event-Vorschlag wurde redigiert, der
        # Nachweis-Trail (contact_stage) bleibt stehen.
        assert event_proposal.details == {"pseudonym": "[Anonymisiert]", "contact_stage": "identified"}


class TestRetentionProposalRedactionWritesOnlyTheDetailsColumn:
    """Der Redaktions-Save fasst genau eine Spalte an."""

    def test_proposal_save_is_limited_to_details(self, facility, client_identified, doc_type_contact, staff_user):
        """Pin: ``x__redact_retention_proposals__mutmut_19`` —
        ``save(update_fields=["details"])`` -> ``save(update_fields=None)``.

        Mit ``update_fields=None`` schreibt Django alle Spalten der per
        ``.iterator()`` gelesenen Zeile zurueck — ``status``,
        ``deletion_due_at``, ``deferred_until``, ``defer_count`` und
        ``retention_category`` inklusive. Eine parallele Freigabe/Ablehnung
        waehrend des Anonymisierungslaufs wuerde auf den alten Stand
        zurueckgesetzt. Original: ``post_save`` sieht
        ``frozenset({"details"})``; Mutant: ``None``.
        """
        event = _event(facility, client_identified, doc_type_contact, staff_user)
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=event.pk,
            deletion_due_at=timezone.localdate(),
            details={"pseudonym": "Quelle-PII"},
            retention_category="identified",
        )

        with _captured_update_fields(RetentionProposal) as seen:
            _redact_retention_proposals([event.pk])

        assert seen == [frozenset({"details"})]


# ============================================================================
# core.services.client.main — Antrags-Abfragen der Personenflaechen
# ============================================================================


class TestPendingDeletionRequestLookupIsScoped:
    """Der Einzelabruf antwortet nur ueber Personen-Antraege der EIGENEN Stelle."""

    def test_foreign_facility_and_foreign_target_type_are_ignored(
        self, facility, client_identified, second_facility, second_facility_user, staff_user
    ):
        """Pin: ``x_get_pending_deletion_request__mutmut_6`` (``facility_id``-Filter
        entfaellt) und ``__mutmut_7`` (``target_type``-Filter entfaellt).

        ``get_pending_deletion_request`` benutzt bewusst NICHT den
        ``FacilityScopedManager`` — ``facility_id`` ist der einzige
        Mandanten-Guard dieser Funktion, ``target_type`` die einzige
        Zusicherung „nur Personen-Antraege". Beide sind bislang durch keinen
        Test belegt. Original: ``None``; ``_6`` liefert den Antrag der fremden
        Einrichtung, ``_7`` den Fall-Antrag derselben Einrichtung (beide also
        ein ``DeletionRequest``-Objekt statt ``None``).
        """
        DeletionRequest.objects.create(
            facility=second_facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_identified.pk,
            reason="Fremde Einrichtung",
            requested_by=second_facility_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=client_identified.pk,
            reason="Fall-Antrag",
            requested_by=staff_user,
        )

        assert get_pending_deletion_request(client_identified) is None

    def test_the_requester_comes_with_the_row(self, facility, client_identified, staff_user, django_assert_num_queries):
        """Pin: ``x_get_pending_deletion_request__mutmut_1`` —
        ``.select_related("requested_by")`` -> ``.select_related(None)``.

        Der Docstring nennt das Vorladen ausdruecklich als Grund: jede
        Anzeigeflaeche (Detail-Banner, Antragsseite, Merge-Blocker) nennt die
        antragstellende Person. ``select_related(None)`` loescht die Join-Liste
        — das Ergebnisobjekt ist identisch, aber ``requested_by`` wird
        nachgeladen. Original: 1 Query fuer Abruf + Zugriff; Mutant: 2.
        """
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_identified.pk,
            reason="Personen-Antrag",
            requested_by=staff_user,
        )

        with django_assert_num_queries(1):
            request = get_pending_deletion_request(client_identified)
            assert request.requested_by.username == "teststaff"


class TestPendingDeletionClientIdsStayOnThePage:
    """Der Listen-Badge fragt genau die uebergebene Seite, nur Personen-Antraege."""

    def test_result_is_exactly_the_client_requests_of_the_given_page(
        self, facility, client_identified, client_qualified, staff_user
    ):
        """Pin: ``x_pending_deletion_client_ids__mutmut_11`` (``target_type``-Filter
        entfaellt) und ``__mutmut_12`` (``target_id__in``-Filter entfaellt).

        Der bestehende Test uebergibt genau die Personen, die auch Antraege
        haben, und legt nur ``CLIENT``-Antraege an — beide Filter waren damit
        unbelegt. Aufbau: ``client_identified`` traegt nur einen FALL-Antrag,
        ``client_qualified`` den Personen-Antrag, und eine dritte Person
        ausserhalb der Seite hat einen eigenen Personen-Antrag.
        Original: ``{client_qualified.pk}``. ``_11`` liefert zusaetzlich
        ``client_identified.pk``, ``_12`` zusaetzlich die PK der dritten Person
        (Gleichheit statt Membership macht beides sichtbar).
        """
        off_page = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Nicht-auf-der-Seite",
            created_by=staff_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=client_identified.pk,
            reason="Fall-Antrag",
            requested_by=staff_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_qualified.pk,
            reason="Personen-Antrag",
            requested_by=staff_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=off_page.pk,
            reason="Antrag ausserhalb der Seite",
            requested_by=staff_user,
        )

        assert pending_deletion_client_ids(facility, [client_identified, client_qualified]) == {client_qualified.pk}


class TestPendingMergeClientIdsStayOnThePage:
    """Der Merge-Badge fragt ebenfalls genau die uebergebene Seite."""

    def test_result_is_exactly_the_client_merge_requests_of_the_given_page(
        self, facility, client_identified, client_qualified, staff_user
    ):
        """Pin: ``x_pending_merge_client_ids__mutmut_12`` (``target_type``-Filter
        entfaellt) und ``__mutmut_13`` (``target_id__in``-Filter entfaellt).

        Der bestehende Pin (``test_mutation_pins_090.py``) legt nur
        ``CLIENT``-Antraege an und uebergibt genau die Person, die auch den
        Antrag hat. Aufbau hier: ``client_identified`` traegt einen FALL-
        Merge-Antrag, ``client_qualified`` den Personen-Merge-Antrag, eine
        dritte Person ausserhalb der Seite einen eigenen.
        Original: ``{client_qualified.pk}``. ``_12`` liefert zusaetzlich
        ``client_identified.pk``, ``_13`` zusaetzlich die PK der dritten Person.
        """
        off_page = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Merge-ausserhalb",
            created_by=staff_user,
        )
        merge_into = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Merge-Ziel-Badge",
            created_by=staff_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=client_identified.pk,
            reason="Fall-Merge",
            merge_into=merge_into,
            requested_by=staff_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_qualified.pk,
            reason="Personen-Merge",
            merge_into=merge_into,
            requested_by=staff_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=off_page.pk,
            reason="Merge ausserhalb der Seite",
            merge_into=merge_into,
            requested_by=staff_user,
        )

        assert pending_merge_client_ids(facility, [client_identified, client_qualified]) == {client_qualified.pk}


# ============================================================================
# core.services.client.main.anonymize_eligible_soft_deleted_clients
# ============================================================================


class TestTrashSweepCountsEveryRecord:
    """Das Ops-Signal „Anonymisiert N" muss die echte Zahl nennen."""

    def test_the_sweep_returns_the_number_of_anonymized_records(self, facility, staff_user, settings_obj):
        """Pin: ``x_anonymize_eligible_soft_deleted_clients__mutmut_58`` —
        ``count += 1`` -> ``count = 1``.

        Kein Test im Repo assertet bisher ein Ergebnis > 1 (alle Aufrufstellen
        pruefen 0, 1 oder ``>= 1``). Mit ``count = 1`` meldet jeder Lauf
        hoechstens 1 — ein Rueckstau faelliger Personen bleibt unsichtbar und
        die Retention-Berichterstattung ist dauerhaft falsch.
        Original: ``3``; Mutant: ``1``. Die ``dry_run``-Vorschau (eigener
        ``qs.count()``-Zweig) haelt dieselbe Zahl fest.
        """
        expired = timezone.now() - timedelta(days=settings_obj.client_trash_days + 5)
        for index in range(3):
            client = Client.objects.create(
                facility=facility,
                contact_stage=Client.ContactStage.IDENTIFIED,
                pseudonym=f"Sweep-{index}",
                created_by=staff_user,
            )
            Client.objects.filter(pk=client.pk).update(is_deleted=True, deleted_at=expired)

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj, dry_run=True) == 3
        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 3


class TestTrashSweepSkipsRacedRowsWithoutAborting:
    """Eine verlorene Zeile ueberspringt der Sweep — er bricht NICHT ab."""

    def test_a_raced_row_does_not_stop_the_remaining_records(self, facility, staff_user, settings_obj, monkeypatch):
        """Pin: ``x_anonymize_eligible_soft_deleted_clients__mutmut_39`` —
        ``continue`` -> ``break`` im Zweig ``if client is None``.

        Der Zweig faengt den Verlierer eines Rennens ab (paralleler Lauf war
        schneller oder die Person wurde zwischenzeitlich wiederhergestellt).
        Mit ``break`` beendet der erste solche Treffer den GESAMTEN Sweep —
        bei ueberlappenden ``enforce_retention``-Laeufen (Refs #2013) bleiben
        faellige Personen mit Klartext liegen. Bestehende Tests benutzen je
        genau EINE Person; dort sind ``continue`` und ``break`` nicht
        unterscheidbar.

        Aufbau: ``A-Rennen`` steht dank ersetztem Hold-Ausschluss in der
        Auswahlmenge, faellt aber auf der gesperrten Zeile durch das
        ``is_deleted``-Gate; ``B-Faellig`` folgt (Sortierung ueber das aeltere
        ``deleted_at`` von A). Original: ``1`` und B ist anonymisiert;
        Mutant: ``0`` und B bleibt unberuehrt.
        """
        raced = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="A-Rennen",
            created_by=staff_user,
        )
        due = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="B-Faellig",
            created_by=staff_user,
        )
        now = timezone.now()
        Client.objects.filter(pk=raced.pk).update(
            is_deleted=False, deleted_at=now - timedelta(days=settings_obj.client_trash_days + 10)
        )
        Client.objects.filter(pk=due.pk).update(
            is_deleted=True, deleted_at=now - timedelta(days=settings_obj.client_trash_days + 5)
        )

        monkeypatch.setattr(
            client_main,
            "exclude_clients_with_active_holds",
            lambda qs, facility: (
                Client.objects.for_facility(facility)
                .filter(deleted_at__isnull=False, anonymized_at__isnull=True)
                .order_by("deleted_at")
            ),
        )

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 1

        raced.refresh_from_db()
        due.refresh_from_db()
        assert raced.anonymized_at is None
        assert raced.pseudonym == "A-Rennen"
        assert due.anonymized_at is not None
