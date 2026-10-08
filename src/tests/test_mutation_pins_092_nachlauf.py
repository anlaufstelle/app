"""Pin-Tests der Survivor-Triage des 0.92-Nachlaufs (Delta BASE v0.91.0).

Der Nachlauf-``mutation-delta`` zum 0.92-Checkpoint deckte drei weitere
Prio-1-Testluecken-Module ab, die der Checkpoint-Pin
(``test_mutation_pins_092_checkpoint.py``) noch offen liess. Diese Datei
buendelt die drei zugehoerigen Staging-Pin-Dateien der Nachlauf-Triage zu
EINER Datei — 29 Tests gegen 30 bestaetigte Survivor-Mutanten in drei
Modulen (Refs #2080, #2107, #2108, #2109, #2110, #2111):

* ``core.services.client.main`` (16 Tests, 16 Mutanten) — Ziel-Eingrenzung
  und Redaktions-Vollstaendigkeit der Vier-Augen-/Merge-Loeschpfade sowie
  Papierkorb-Gate und Hold-Recheck der Nachtsweeps;
* ``core.services.security.lockout_recovery`` (8 Tests, 11 Mutanten) — die
  vier Verteidigungslinien des Recovery-Tokens: Anker-Skopierung, TTL,
  Identitaets-/Aktiv-Bindung, RLS-Sitzungskontext und Einmal-Nutzung;
* ``core.services.security.mfa`` (5 Tests, 3 Mutanten) — Eigentuemer-Bindung
  und Drosselung der Backup-Code-Recovery.

Konventionen wie ``test_mutation_pins_091_cut.py``/``..._092_checkpoint.py``:
Zusicherungen vergleichen EXAKT (``==``/``is``), nie per Teilstring (mutmut
erzeugt ``"XX<text>XX"``-Varianten); KEINE Imports aus ``test_rls_functional``
(its fixture ``rls_test_role`` skips itself under ``MUTANT_UNDER_TEST``; a pin
requesting it never runs in a mutation run);
keine Fixture-Faken — alle Objekte sind echte Model-Zeilen aus
``src/tests/conftest.py``; jeder Test nennt seine Mutanten samt abweichendem
Wert in der Docstring. Der Kill-Nachweis ist der Delta-RERUN des Nachlaufs
(Delta-Mutanten werden immer neu getestet). Da alle drei Module DB-Zeilen
anlegen, gilt EIN modulweites ``pytestmark`` fuer die ganze Datei.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest import mock

import pytest
from django.core.signing import TimestampSigner
from django.db import connection
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice

from core.models import AuditLog, Client, Event, LegalHold, User
from core.models.retention import RetentionProposal
from core.models.workitem import DeletionRequest
from core.retention.legal_holds import create_hold_for_target
from core.services.client.main import (
    _generalize_event_timestamps,
    _redact_deletion_requests,
    _redact_merged_source_references,
    _redact_retention_proposals,
    anonymize_eligible_soft_deleted_clients,
    get_pending_deletion_request,
)
from core.services.client.merge import merge_clients
from core.services.security.lockout_recovery import (
    _SALT,
    _latest_failed_anchor,
    build_recovery_token,
    consume_recovery_token,
    verify_recovery_token,
)
from core.services.security.mfa import (
    BACKUP_CODES_COUNT,
    BACKUP_DEVICE_NAME,
    generate_backup_codes,
    remaining_backup_codes,
    verify_backup_code,
)

pytestmark = pytest.mark.django_db


# ── core.services.client.main ───────────────────────────────────────────────
#
# Pin-Tests der Prio-1-Survivor aus ``core.services.client.main`` (Welle-E-Nachlauf).
#
# Quelle: ``src/core/services/client/main.py`` (Modul
# ``core.services.client.main``), Mutantenbaum
# ``mutants/src/core/services/client/main.py``. Gepinnt werden die 16
# bestaetigten Prio-1-Testluecken der Nachlauf-Triage zu 0.92 — sechs Funktionen,
# eine Testklasse je Funktion:
#
# * :func:`_generalize_event_timestamps` — der Idempotenz-Zweig (Refs #2047);
# * :func:`_redact_deletion_requests` — die Ziel-Eingrenzung der
#   Vier-Augen-Begruendungen (Refs #1091);
# * :func:`_redact_retention_proposals` — Ziel-Eingrenzung und Skip-Zweig der
#   Vorschlags-Redaktion (Refs #1751, Punkt 3);
# * :func:`_redact_merged_source_references` — die Inventar-Auswahl und alle
#   vier Redaktions-Achsen des Merge-Tombstones (Refs #1876, ADR-045);
# * :func:`anonymize_eligible_soft_deleted_clients` — Papierkorb-Gate der
#   Vorschau und der Hold-Recheck unter der Zeilensperre (Refs #2013, #2108);
# * :func:`get_pending_deletion_request` — der Personenbezug des laufenden
#   Loeschantrags (Refs #626, #1736).
#
# Konventionen wie ``test_mutation_pins_089/090/091*.py``: jeder Test nennt
# seine Mutanten samt Diff in der Docstring und benennt dort, welcher Wert beim
# Mutanten abweicht; Zusicherungen vergleichen EXAKT (``==``), nie per
# Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); es werden echte
# Model-Zeilen gebaut, keine Fixture-Fakes; KEINE Imports aus
# ``test_rls_functional`` (its fixture ``rls_test_role`` skips itself under
# ``MUTANT_UNDER_TEST``; a pin requesting it never runs in a mutation run). Ein
# Sprachkontext ist hier nirgends noetig: alle
# verglichenen Texte sind DB-Werte bzw. der nicht uebersetzte Marker
# ``"[Anonymisiert]"``.
#
# Die VIER unscoped Manager-Aufrufe der Datei — in ``_redact_deletion_requests``,
# in ``_redact_retention_proposals`` sowie im DeletionRequest- UND im
# RetentionProposal-Zweig von ``_redact_merged_source_references`` (jeweils
# ``DeletionRequest.objects``/``RetentionProposal.objects`` ohne
# ``for_facility``) — werden bewusst gegen eine ZWEITE Einrichtung
# gegengeprueft — dort ist der Mandantenbruch beobachtbar. Wo die Quelle
# ``for_facility`` fuehrt (Activity, LegalHold), liegt die Negativkontrolle
# folgerichtig bei einer anderen PERSON derselben Einrichtung.


# ---------------------------------------------------------------------------
# Bau-Helfer (echte Zeilen, keine Fakes)
# ---------------------------------------------------------------------------


def _make_event(facility, client, doc_type, user, *, occurred_at=None):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=occurred_at if occurred_at is not None else timezone.now(),
        data_json={},
        created_by=user,
    )


def _make_person(facility, user, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=user,
    )


def _make_event_request(facility, event, user, reason):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.EVENT,
        target_id=event.pk,
        reason=reason,
        requested_by=user,
    )


def _make_event_hold(facility, event, user, reason, *, expires_at):
    return LegalHold.objects.create(
        facility=facility,
        target_type="Event",
        target_id=event.pk,
        reason=reason,
        created_by=user,
        expires_at=expires_at,
    )


def _make_event_proposal(facility, event, details, *, due_offset_days=0):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=event.pk,
        deletion_due_at=timezone.localdate() + timedelta(days=due_offset_days),
        details=details,
        retention_category="identified",
    )


# ===========================================================================
# core.services.client.main._generalize_event_timestamps
# ===========================================================================


class TestTimestampGeneralizationSkipsInsteadOfStopping:
    """Ein bereits gebucketetes Ereignis ueberspringt — es beendet nichts.

    ``_generalize_event_timestamps`` generalisiert ``Event.occurred_at`` auf
    den Monatsersten 00:00 im lokalen Kalender (Refs #2047) und ist
    ausdruecklich idempotent: ein Wert, der schon auf dem Bucket liegt, faellt
    aus der Aenderungsmenge.
    """

    def test_an_already_bucketed_event_does_not_stop_the_run(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Der Bucket-Treffer wird uebersprungen, er beendet den Lauf nicht.

        Pin: ``x__generalize_event_timestamps__mutmut_32`` —
        ``if bucket == local: continue`` → ``break``.

        Die Schleife iteriert ueber ``Event.objects.filter(pk__in=...)`` und
        erbt damit ``Event.Meta.ordering = ["-occurred_at"]``: das spaetere
        Ereignis kommt zuerst. Genau so ist es hier gebaut — das schon
        gebucketete Ereignis (Monatserster 00:00 des laufenden Monats) liegt
        zeitlich VOR dem exakt gestempelten Ereignis aus dem Vormonat und wird
        deshalb als erstes gezogen.

        Original: der Bucket-Treffer wird uebersprungen, das Vormonats-Ereignis
        landet auf dem 1. des Vormonats 00:00:00.000000.
        Mutant: ``break`` beim ersten Treffer — das Vormonats-Ereignis behaelt
        seinen exakten Zeitstempel. Abweichender Wert:
        ``timezone.localtime(exakt.occurred_at)`` ist beim Mutanten
        ``<Vormonat>-<Tag> 13:37:42.123456`` statt ``<Vormonat>-01 00:00:00``.
        """
        tz = timezone.get_current_timezone()
        now_local = timezone.localtime(timezone.now())
        naive_bucket = datetime(now_local.year, now_local.month, 1, 0, 0, 0, 0)
        naive_previous = naive_bucket - timedelta(days=5)
        naive_exact = naive_previous.replace(hour=13, minute=37, second=42, microsecond=123456)
        naive_expected = datetime(naive_previous.year, naive_previous.month, 1, 0, 0, 0, 0)

        already_bucketed = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=timezone.make_aware(naive_bucket, tz),
        )
        exact = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=timezone.make_aware(naive_exact, tz),
        )

        _generalize_event_timestamps([already_bucketed.pk, exact.pk])

        exact.refresh_from_db()
        already_bucketed.refresh_from_db()
        assert timezone.localtime(exact.occurred_at) == timezone.make_aware(naive_expected, tz)
        # Der Idempotenz-Treffer bleibt unangetastet (kein zweiter Schreibweg).
        assert timezone.localtime(already_bucketed.occurred_at) == timezone.make_aware(naive_bucket, tz)


# ===========================================================================
# core.services.client.main._redact_deletion_requests
# ===========================================================================


class TestEventDeletionRequestRedactionStaysOnItsTargets:
    """Die Redaktion der Vier-Augen-Begruendung trifft nur die eigenen Ziele.

    ``DeletionRequest.objects`` ist ein ``FacilityScopedManager``, dessen
    ``.filter()`` NICHT automatisch scopet — die Eingrenzung haengt allein an
    ``target_id__in``. Die Wartungspfade (``enforce_retention``, K-Anon-Bridge)
    laufen als Owner ohne RLS, also einrichtungsuebergreifend.
    """

    def test_only_the_given_event_ids_lose_their_reason(
        self,
        facility,
        client_identified,
        doc_type_contact,
        staff_user,
        second_facility,
        foreign_client,
        foreign_doc_type,
        foreign_staff,
    ):
        """Nur die uebergebenen Ereignis-IDs verlieren ihren Freitext.

        Pin: ``x__redact_deletion_requests__mutmut_6`` —
        ``filter(target_type="Event", target_id__in=event_ids)``
        → ``filter(target_type="Event")``.

        Original: nur der Antrag zum uebergebenen Ereignis verliert seinen
        Freitext.
        Mutant: die Mengenbegrenzung faellt weg, das ``UPDATE`` trifft JEDEN
        Ereignis-Loeschantrag der Datenbank. Abweichende Werte: sowohl
        ``other.reason`` als auch ``foreign.reason`` sind beim Mutanten
        ``"[Anonymisiert]"`` statt ``"Grund der anderen Person"`` bzw.
        ``"Grund der fremden Einrichtung"``.
        """
        own_event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        other_person = _make_person(facility, staff_user, "Unbeteiligt-01")
        other_event = _make_event(facility, other_person, doc_type_contact, staff_user)
        foreign_event = _make_event(second_facility, foreign_client, foreign_doc_type, foreign_staff)

        own = _make_event_request(facility, own_event, staff_user, "Grund der eigenen Person")
        other = _make_event_request(facility, other_event, staff_user, "Grund der anderen Person")
        foreign = _make_event_request(second_facility, foreign_event, foreign_staff, "Grund der fremden Einrichtung")

        _redact_deletion_requests([own_event.pk])

        own.refresh_from_db()
        other.refresh_from_db()
        foreign.refresh_from_db()
        assert own.reason == "[Anonymisiert]"
        assert other.reason == "Grund der anderen Person"
        assert foreign.reason == "Grund der fremden Einrichtung"
        # Antrags-Meta bleibt fuer den Vier-Augen-Trail stehen.
        assert own.status == DeletionRequest.Status.PENDING


# ===========================================================================
# core.services.client.main._redact_retention_proposals
# ===========================================================================


class TestRetentionProposalRedactionIsBoundAndComplete:
    """Zielbindung und Vollstaendigkeit der Vorschlags-Redaktion (Refs #1751).

    Der Helfer laeuft im Haupt-Kaskadenpfad jeder Anonymisierung
    (``anonymize_client`` → ``_redact_retention_proposals``). Er darf weder zu
    viel treffen (fremde Vorschlaege) noch zu frueh aufhoeren (Vorschlag ohne
    ``pseudonym``-Schluessel).
    """

    def test_only_the_given_event_ids_lose_their_pseudonym(
        self,
        facility,
        client_identified,
        doc_type_contact,
        staff_user,
        second_facility,
        foreign_client,
        foreign_doc_type,
        foreign_staff,
    ):
        """Nur die uebergebenen Ereignis-IDs verlieren ihr Pseudonym.

        Pin: ``x__redact_retention_proposals__mutmut_6`` —
        ``filter(target_type=EVENT, target_id__in=event_ids)``
        → ``filter(target_type=EVENT)``.

        Original: nur der Vorschlag zum uebergebenen Ereignis verliert das
        Klartext-Pseudonym.
        Mutant: ohne ``target_id__in`` wird das Pseudonym in JEDEM
        Ereignis-Loeschvorschlag der gesamten Datenbank ersetzt. Abweichende
        Werte: ``other.details["pseudonym"]`` ist beim Mutanten
        ``"[Anonymisiert]"`` statt ``"Unbeteiligt-02"`` und
        ``foreign.details["pseudonym"]`` ``"[Anonymisiert]"`` statt
        ``"Fremd-PII"``.
        """
        own_event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        other_person = _make_person(facility, staff_user, "Unbeteiligt-02")
        other_event = _make_event(facility, other_person, doc_type_contact, staff_user)
        foreign_event = _make_event(second_facility, foreign_client, foreign_doc_type, foreign_staff)

        own = _make_event_proposal(facility, own_event, {"pseudonym": client_identified.pseudonym})
        other = _make_event_proposal(facility, other_event, {"pseudonym": other_person.pseudonym})
        foreign = _make_event_proposal(second_facility, foreign_event, {"pseudonym": "Fremd-PII"})

        _redact_retention_proposals([own_event.pk])

        own.refresh_from_db()
        other.refresh_from_db()
        foreign.refresh_from_db()
        assert own.details == {"pseudonym": "[Anonymisiert]"}
        assert other.details == {"pseudonym": "Unbeteiligt-02"}
        assert foreign.details == {"pseudonym": "Fremd-PII"}

    def test_a_proposal_without_pseudonym_is_skipped_not_final(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Ein Vorschlag ohne ``pseudonym`` wird uebersprungen, nicht zum Abbruch.

        Pin: ``x__redact_retention_proposals__mutmut_12`` —
        ``if "pseudonym" not in details: continue`` → ``break``.

        ``RetentionProposal.Meta.ordering = ["deletion_due_at"]``: der Vorschlag
        OHNE ``pseudonym`` bekommt hier die fruehere Frist (heute) und wird
        deshalb zuerst iteriert, der Vorschlag MIT Pseudonym die spaetere
        (heute + 3 Tage).

        Original: die schluessellose Zeile wird uebersprungen, die folgende
        wird redigiert.
        Mutant: ``break`` beendet die Schleife an der schluessellosen Zeile.
        Abweichender Wert: ``with_pseudonym.details["pseudonym"]`` ist beim
        Mutanten ``"Test-ID-01"`` (das Klartext-Pseudonym) statt
        ``"[Anonymisiert]"``.
        """
        first_event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        second_event = _make_event(facility, client_identified, doc_type_contact, staff_user)

        without_pseudonym = _make_event_proposal(
            facility,
            first_event,
            {"occurred_at": str(first_event.occurred_at)},
            due_offset_days=0,
        )
        with_pseudonym = _make_event_proposal(
            facility,
            second_event,
            {"pseudonym": client_identified.pseudonym, "contact_stage": "identified"},
            due_offset_days=3,
        )

        _redact_retention_proposals([first_event.pk, second_event.pk])

        without_pseudonym.refresh_from_db()
        with_pseudonym.refresh_from_db()
        assert with_pseudonym.details == {"pseudonym": "[Anonymisiert]", "contact_stage": "identified"}
        # Die schluessellose Zeile wird nicht angefasst — nur uebersprungen.
        assert without_pseudonym.details == {"occurred_at": str(first_event.occurred_at)}


# ===========================================================================
# core.services.client.main._redact_merged_source_references
# ===========================================================================


class _MergeScenario:
    """Ergebnis von :func:`_build_merge_scenario` (nur Datentraeger)."""

    def __init__(self, moved_event, request, hold, proposal, spare_event=None):
        self.moved_event = moved_event
        self.request = request
        self.hold = hold
        self.proposal = proposal
        #: Zweites gewandertes Ereignis OHNE Spuren. Die Nach-Merge-Zeilen der
        #: Zeitgrenzen-Tests haengen daran, weil ``unique_pending_deletion_request``
        #: bzw. ``unique_active_legal_hold`` je Ziel nur EINE offene Zeile
        #: zulassen — es bleibt ein ``moved_event`` desselben Inventars.
        self.spare_event = spare_event


def _build_merge_scenario(facility, source, target, doc_type, staff_user, lead_user):
    """Ein Merge mit je EINER quell-bezogenen Spur auf allen vier Achsen.

    Alle Spuren entstehen VOR ``merge_clients`` und liegen damit innerhalb der
    Zeitgrenze ``created_at/proposed_at <= merged_at``.
    """
    moved_event = _make_event(facility, source, doc_type, staff_user)
    spare_event = _make_event(facility, source, doc_type, staff_user)
    request = _make_event_request(facility, moved_event, staff_user, f"Doppelt dokumentiert bei {source.pseudonym}")
    hold = _make_event_hold(
        facility,
        moved_event,
        lead_user,
        f"Anfrage zum Kontakt mit {source.pseudonym}",
        expires_at=timezone.localdate() - timedelta(days=1),
    )
    proposal = _make_event_proposal(facility, moved_event, {"pseudonym": source.pseudonym})
    merge_clients(source, target, lead_user)
    source.refresh_from_db()
    return _MergeScenario(moved_event, request, hold, proposal, spare_event=spare_event)


def _assert_source_traces_redacted(scenario):
    scenario.request.refresh_from_db()
    scenario.hold.refresh_from_db()
    scenario.proposal.refresh_from_db()
    assert scenario.request.reason == "[Anonymisiert]"
    assert scenario.hold.reason == "[Anonymisiert]"
    assert scenario.proposal.details == {"pseudonym": "[Anonymisiert]"}


class TestMergeInventoryLookupStaysBoundToThisMerge:
    """Die ``CLIENT_MERGED``-Inventarzeile muss DIESE Zusammenfuehrung sein.

    ``_redact_merged_source_references`` zieht die Ereignis-IDs der gewanderten
    Ereignisse aus dem ``moved_events``-Inventar der ``CLIENT_MERGED``-Zeile.
    ``AuditLog.objects`` ist ein ``FacilityScopedManager`` ohne automatisches
    Scoping; die Auswahl haengt an allen drei Filtern (``facility``, ``action``,
    ``target_id``) plus ``order_by("-timestamp").first()``.
    """

    def test_a_foreign_facility_merge_row_is_not_the_inventory(
        self, facility, second_facility, doc_type_contact, staff_user, lead_user
    ):
        """Das Inventar kommt aus DIESER Einrichtung, nie aus einer fremden.

        Pin: ``x__redact_merged_source_references__mutmut_8`` —
        ``AuditLog.objects.filter(facility=client.facility, …)``
        → der ``facility``-Filter entfaellt.

        Die zweite Einrichtung bekommt eine JUENGERE ``CLIENT_MERGED``-Zeile
        mit demselben ``target_id`` (``AuditLog.target_id`` ist ein
        ``CharField``, der Wert ist konstruierbar) und einem fremden
        ``merged_into``.

        Original: die Fremdzeile wird weggefiltert, das echte Inventar greift,
        die Quell-Spuren werden redigiert.
        Mutant: ``order_by("-timestamp").first()`` liefert die Fremdzeile, ihr
        ``detail["merged_into"]`` passt nicht → Warn-Log und ``return``.
        Abweichende Werte: ``request.reason`` bleibt beim Mutanten
        ``"Doppelt dokumentiert bei Merge-Quelle-08"`` statt
        ``"[Anonymisiert]"`` (ebenso ``hold.reason`` und
        ``proposal.details["pseudonym"]``).
        """
        source = _make_person(facility, staff_user, "Merge-Quelle-08")
        target = _make_person(facility, staff_user, "Merge-Ziel-08")
        scenario = _build_merge_scenario(facility, source, target, doc_type_contact, staff_user, lead_user)

        AuditLog.objects.create(
            facility=second_facility,
            user=None,
            action=AuditLog.Action.CLIENT_MERGED,
            target_type="Client",
            target_id=str(source.pk),
            detail={"merged_into": str(uuid.uuid4()), "moved_events": []},
            timestamp=timezone.now() + timedelta(hours=1),
        )

        _redact_merged_source_references(source)

        _assert_source_traces_redacted(scenario)

    def test_a_later_non_merge_audit_row_is_not_the_inventory(self, facility, doc_type_contact, staff_user, lead_user):
        """Das Inventar ist die ``CLIENT_MERGED``-Zeile, nicht die juengste Client-Zeile.

        Pin: ``x__redact_merged_source_references__mutmut_9`` —
        ``filter(action=AuditLog.Action.CLIENT_MERGED, …)``
        → der ``action``-Filter entfaellt.

        Produktiv folgen der Merge-Zeile reichlich spaetere Client-Zeilen
        (Art.-15-``EXPORT`` auf den Tombstone, ``CLIENT_UPDATE``,
        ``CLIENT_RESTORED``, ``CLIENT_ANONYMIZED``). Hier steht dafuer eine
        juengere ``EXPORT``-Zeile derselben Person und derselben Einrichtung.

        Original: nur ``CLIENT_MERGED`` zaehlt, das Inventar wird gefunden, die
        Quell-Spuren werden redigiert.
        Mutant: die juengste Client-Zeile ist die ``EXPORT``-Zeile, deren
        ``detail`` kein ``merged_into`` traegt → Warn-Log und ``return``.
        Abweichende Werte: ``request.reason`` bleibt beim Mutanten
        ``"Doppelt dokumentiert bei Merge-Quelle-09"`` statt
        ``"[Anonymisiert]"`` (ebenso ``hold.reason`` und
        ``proposal.details["pseudonym"]``).
        """
        source = _make_person(facility, staff_user, "Merge-Quelle-09")
        target = _make_person(facility, staff_user, "Merge-Ziel-09")
        scenario = _build_merge_scenario(facility, source, target, doc_type_contact, staff_user, lead_user)

        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.EXPORT,
            target_type="Client",
            target_id=str(source.pk),
            detail={},
            timestamp=timezone.now() + timedelta(hours=1),
        )

        _redact_merged_source_references(source)

        _assert_source_traces_redacted(scenario)

    def test_a_second_merge_into_the_same_target_is_not_this_inventory(
        self, facility, doc_type_contact, staff_user, lead_user
    ):
        """Das Inventar gehoert zu DIESER Quelle, nicht zu einer zweiten.

        Pin: ``x__redact_merged_source_references__mutmut_11`` —
        ``filter(…, target_id=str(client.pk))``
        → der ``target_id``-Filter entfaellt.

        Zwei Zusammenfuehrungen in DERSELBEN Einrichtung auf DASSELBE Ziel:
        erst ``A → Z``, danach ``B → Z``. Die Zeile von B ist die juengere.

        Original: das Inventar wird ueber ``target_id`` an A gebunden — A's
        Spuren werden redigiert, B's bleiben stehen.
        Mutant: gezogen wird B's Inventarzeile; ihr ``merged_into`` ist
        ebenfalls ``str(Z.pk)``, der Konsistenz-Check besteht also, und
        redigiert werden B's ``moved_events``. Abweichende Werte:
        ``a.request.reason`` bleibt beim Mutanten
        ``"Doppelt dokumentiert bei Merge-Quelle-11a"`` statt
        ``"[Anonymisiert]"``, und ``b.request.reason`` wird beim Mutanten
        ``"[Anonymisiert]"`` statt ``"Doppelt dokumentiert bei
        Merge-Quelle-11b"``.
        """
        target = _make_person(facility, staff_user, "Merge-Ziel-11")
        source_a = _make_person(facility, staff_user, "Merge-Quelle-11a")
        source_b = _make_person(facility, staff_user, "Merge-Quelle-11b")

        scenario_a = _build_merge_scenario(facility, source_a, target, doc_type_contact, staff_user, lead_user)
        scenario_b = _build_merge_scenario(facility, source_b, target, doc_type_contact, staff_user, lead_user)

        _redact_merged_source_references(source_a)

        _assert_source_traces_redacted(scenario_a)
        scenario_b.request.refresh_from_db()
        scenario_b.hold.refresh_from_db()
        scenario_b.proposal.refresh_from_db()
        assert scenario_b.request.reason == "Doppelt dokumentiert bei Merge-Quelle-11b"
        assert scenario_b.hold.reason == "Anfrage zum Kontakt mit Merge-Quelle-11b"
        assert scenario_b.proposal.details == {"pseudonym": "Merge-Quelle-11b"}


class TestMergeTombstoneRedactionKeepsBothItsAxes:
    """Die Mit-Redaktion bleibt auf den gewanderten Ereignissen UND vor dem Merge.

    Der Docstring der Quelle zieht zwei Grenzen: nur die ``moved_events`` des
    Inventars, und nur Zeilen bis zum ``timestamp`` der Audit-Zeile. Vier
    Filter-Mutanten schneiden je eine dieser Grenzen heraus.
    """

    def test_deletion_requests_of_other_targets_survive(
        self,
        facility,
        doc_type_contact,
        staff_user,
        lead_user,
        second_facility,
        foreign_client,
        foreign_doc_type,
        foreign_staff,
    ):
        """Fremde Ereignis-Loeschantraege behalten ihre Begruendung.

        Pin: ``x__redact_merged_source_references__mutmut_60`` —
        im DeletionRequest-Filter entfaellt ``target_id__in=moved_event_ids``.

        ``DeletionRequest.objects`` ist ein ``FacilityScopedManager`` ohne
        automatisches Scoping — uebrig bliebe
        ``filter(target_type=EVENT, created_at__lte=merged_at)``, also JEDER
        Ereignis-Loeschantrag ALLER Einrichtungen von vor dem Merge.

        Original: nur der Antrag zum gewanderten Ereignis verliert seinen
        Freitext.
        Mutant: auch die Fremdzeilen. Abweichende Werte:
        ``other.reason`` ist beim Mutanten ``"[Anonymisiert]"`` statt
        ``"Grund der anderen Person"``, ``foreign.reason``
        ``"[Anonymisiert]"`` statt ``"Grund der fremden Einrichtung"``.
        """
        source = _make_person(facility, staff_user, "Merge-Quelle-60")
        target = _make_person(facility, staff_user, "Merge-Ziel-60")
        other_person = _make_person(facility, staff_user, "Unbeteiligt-60")
        other_event = _make_event(facility, other_person, doc_type_contact, staff_user)
        other = _make_event_request(facility, other_event, staff_user, "Grund der anderen Person")
        foreign_event = _make_event(second_facility, foreign_client, foreign_doc_type, foreign_staff)
        foreign = _make_event_request(second_facility, foreign_event, foreign_staff, "Grund der fremden Einrichtung")

        scenario = _build_merge_scenario(facility, source, target, doc_type_contact, staff_user, lead_user)

        _redact_merged_source_references(source)

        scenario.request.refresh_from_db()
        other.refresh_from_db()
        foreign.refresh_from_db()
        assert scenario.request.reason == "[Anonymisiert]"
        assert other.reason == "Grund der anderen Person"
        assert foreign.reason == "Grund der fremden Einrichtung"

    def test_a_deletion_request_filed_after_the_merge_survives(self, facility, doc_type_contact, staff_user, lead_user):
        """Ein Antrag von NACH dem Merge behaelt seine Begruendung.

        Pin: ``x__redact_merged_source_references__mutmut_61`` —
        im DeletionRequest-Filter entfaellt ``created_at__lte=merged_at``.

        Ein Antrag, der NACH dem Merge zu einem gewanderten Ereignis gestellt
        wird, begruendet sich gegen die Akte des ZIELS — er darf seine
        Vier-Augen-Begruendung nicht verlieren (Quell-Docstring, ADR-045). Er
        haengt am zweiten gewanderten Ereignis (``spare_event``), weil
        ``unique_pending_deletion_request`` je Ziel nur EINEN offenen Antrag
        zulaesst; im ``moved_events``-Inventar steht es genauso.

        Original: die Zeitgrenze schliesst ihn aus.
        Mutant: ohne ``created_at__lte`` wird er mit-redigiert. Abweichender
        Wert: ``post_merge.reason`` ist beim Mutanten ``"[Anonymisiert]"`` statt
        ``"Antrag gegen die Akte von Merge-Ziel-61"``.
        """
        source = _make_person(facility, staff_user, "Merge-Quelle-61")
        target = _make_person(facility, staff_user, "Merge-Ziel-61")
        scenario = _build_merge_scenario(facility, source, target, doc_type_contact, staff_user, lead_user)

        post_merge = _make_event_request(
            facility,
            scenario.spare_event,
            staff_user,
            f"Antrag gegen die Akte von {target.pseudonym}",
        )

        _redact_merged_source_references(source)

        scenario.request.refresh_from_db()
        post_merge.refresh_from_db()
        assert scenario.request.reason == "[Anonymisiert]"
        assert post_merge.reason == "Antrag gegen die Akte von Merge-Ziel-61"

    def test_legal_holds_of_other_targets_survive(self, facility, doc_type_contact, staff_user, lead_user):
        """Fremde Ereignis-Holds behalten ihre Begruendung.

        Pin: ``x__redact_merged_source_references__mutmut_70`` —
        im LegalHold-Filter entfaellt ``target_id__in=moved_event_ids``.

        Der LegalHold-Zweig laeuft ueber ``for_facility(client.facility)`` —
        die Negativkontrolle ist deshalb folgerichtig eine andere PERSON
        derselben Einrichtung, und zwar mit einem AKTIVEN Hold (Ablauf in der
        Zukunft): uebrig bliebe
        ``for_facility(...).filter(target_type="Event", created_at__lte=merged_at)``,
        also saemtliche Ereignis-Holds der Einrichtung von vor dem Merge.

        Original: nur der Hold zum gewanderten Ereignis verliert seine
        Begruendung.
        Mutant: auch der fremde, aktive Hold. Abweichender Wert:
        ``other.reason`` ist beim Mutanten ``"[Anonymisiert]"`` statt
        ``"Laufende Aufbewahrungspflicht einer anderen Person"``.
        """
        source = _make_person(facility, staff_user, "Merge-Quelle-70")
        target = _make_person(facility, staff_user, "Merge-Ziel-70")
        other_person = _make_person(facility, staff_user, "Unbeteiligt-70")
        other_event = _make_event(facility, other_person, doc_type_contact, staff_user)
        other = _make_event_hold(
            facility,
            other_event,
            lead_user,
            "Laufende Aufbewahrungspflicht einer anderen Person",
            expires_at=timezone.localdate() + timedelta(days=90),
        )

        scenario = _build_merge_scenario(facility, source, target, doc_type_contact, staff_user, lead_user)

        _redact_merged_source_references(source)

        scenario.hold.refresh_from_db()
        other.refresh_from_db()
        assert scenario.hold.reason == "[Anonymisiert]"
        assert other.reason == "Laufende Aufbewahrungspflicht einer anderen Person"

    def test_a_legal_hold_ordered_after_the_merge_survives(self, facility, doc_type_contact, staff_user, lead_user):
        """Ein Hold von NACH dem Merge behaelt seine Begruendung.

        Pin: ``x__redact_merged_source_references__mutmut_71`` —
        im LegalHold-Filter entfaellt ``created_at__lte=merged_at``.

        Dieselbe Zeitgrenze wie beim Loeschantrag, nur auf der Hold-Achse: ein
        nach dem Merge angeordneter Hold auf ein gewandertes Ereignis gehoert
        zur Akte des ZIELS und begruendet dort eine laufende
        Aufbewahrungspflicht. Er haengt am zweiten gewanderten Ereignis
        (``spare_event``), weil ``unique_active_legal_hold`` je Ziel nur EINEN
        nicht aufgehobenen Hold zulaesst; im ``moved_events``-Inventar steht es
        genauso.

        Original: die Zeitgrenze schliesst ihn aus.
        Mutant: ohne ``created_at__lte`` verliert er seine Begruendung.
        Abweichender Wert: ``post_merge.reason`` ist beim Mutanten
        ``"[Anonymisiert]"`` statt ``"Behoerdenanordnung zur Akte von
        Merge-Ziel-71"``.
        """
        source = _make_person(facility, staff_user, "Merge-Quelle-71")
        target = _make_person(facility, staff_user, "Merge-Ziel-71")
        scenario = _build_merge_scenario(facility, source, target, doc_type_contact, staff_user, lead_user)

        post_merge = _make_event_hold(
            facility,
            scenario.spare_event,
            lead_user,
            f"Behoerdenanordnung zur Akte von {target.pseudonym}",
            expires_at=timezone.localdate() + timedelta(days=90),
        )

        _redact_merged_source_references(source)

        scenario.hold.refresh_from_db()
        post_merge.refresh_from_db()
        assert scenario.hold.reason == "[Anonymisiert]"
        assert post_merge.reason == "Behoerdenanordnung zur Akte von Merge-Ziel-71"

    def test_retention_proposals_of_other_targets_survive(
        self,
        facility,
        doc_type_contact,
        staff_user,
        lead_user,
        second_facility,
        foreign_client,
        foreign_doc_type,
        foreign_staff,
    ):
        """Fremde Loeschvorschlaege behalten ihr Pseudonym.

        Pin: ``x__redact_merged_source_references__mutmut_84`` —
        im RetentionProposal-Filter entfaellt ``target_id__in=moved_event_ids``.

        ``RetentionProposal.objects`` laeuft hier ebenfalls ungescopt — uebrig
        bliebe ``filter(target_type=EVENT, proposed_at__lte=merged_at)``, also
        ALLE Ereignis-Vorschlaege ALLER Einrichtungen vor dem Merge.

        Original: nur der Vorschlag zum gewanderten Ereignis verliert das
        Klartext-Pseudonym.
        Mutant: auch die Fremdzeilen. Abweichende Werte:
        ``other.details["pseudonym"]`` ist beim Mutanten ``"[Anonymisiert]"``
        statt ``"Unbeteiligt-84"``, ``foreign.details["pseudonym"]``
        ``"[Anonymisiert]"`` statt ``"Fremd-PII"``.
        """
        source = _make_person(facility, staff_user, "Merge-Quelle-84")
        target = _make_person(facility, staff_user, "Merge-Ziel-84")
        other_person = _make_person(facility, staff_user, "Unbeteiligt-84")
        other_event = _make_event(facility, other_person, doc_type_contact, staff_user)
        other = _make_event_proposal(facility, other_event, {"pseudonym": other_person.pseudonym})
        foreign_event = _make_event(second_facility, foreign_client, foreign_doc_type, foreign_staff)
        foreign = _make_event_proposal(second_facility, foreign_event, {"pseudonym": "Fremd-PII"})

        scenario = _build_merge_scenario(facility, source, target, doc_type_contact, staff_user, lead_user)

        _redact_merged_source_references(source)

        scenario.proposal.refresh_from_db()
        other.refresh_from_db()
        foreign.refresh_from_db()
        assert scenario.proposal.details == {"pseudonym": "[Anonymisiert]"}
        assert other.details == {"pseudonym": "Unbeteiligt-84"}
        assert foreign.details == {"pseudonym": "Fremd-PII"}

    def test_a_proposal_without_pseudonym_is_skipped_not_final(self, facility, doc_type_contact, staff_user, lead_user):
        """Ein Vorschlag ohne ``pseudonym`` wird uebersprungen, nicht zum Abbruch.

        Pin: ``x__redact_merged_source_references__mutmut_91`` —
        ``if "pseudonym" not in details: continue`` → ``break``.

        Zwei gewanderte Ereignisse; ``RetentionProposal.Meta.ordering =
        ["deletion_due_at"]`` legt die Reihenfolge fest: der Vorschlag OHNE
        ``pseudonym`` traegt die frueheste Frist (heute) und wird zuerst
        iteriert, der Vorschlag der Quelle die spaetere (heute + 3 Tage).

        Original: die schluessellose Zeile wird uebersprungen, die folgende
        wird redigiert.
        Mutant: ``break`` beendet die Schleife sofort. Abweichender Wert:
        ``scenario.proposal.details["pseudonym"]`` ist beim Mutanten
        ``"Merge-Quelle-91"`` statt ``"[Anonymisiert]"``.
        """
        source = _make_person(facility, staff_user, "Merge-Quelle-91")
        target = _make_person(facility, staff_user, "Merge-Ziel-91")
        keyless_event = _make_event(facility, source, doc_type_contact, staff_user)
        keyless = _make_event_proposal(
            facility,
            keyless_event,
            {"occurred_at": str(keyless_event.occurred_at)},
            due_offset_days=0,
        )

        moved_event = _make_event(facility, source, doc_type_contact, staff_user)
        request = _make_event_request(facility, moved_event, staff_user, f"Doppelt bei {source.pseudonym}")
        hold = _make_event_hold(
            facility,
            moved_event,
            lead_user,
            f"Anfrage zu {source.pseudonym}",
            expires_at=timezone.localdate() - timedelta(days=1),
        )
        proposal = _make_event_proposal(
            facility,
            moved_event,
            {"pseudonym": source.pseudonym},
            due_offset_days=3,
        )
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        scenario = _MergeScenario(moved_event, request, hold, proposal)

        _redact_merged_source_references(source)

        _assert_source_traces_redacted(scenario)
        keyless.refresh_from_db()
        assert keyless.details == {"occurred_at": str(keyless_event.occurred_at)}


# ===========================================================================
# core.services.client.main.anonymize_eligible_soft_deleted_clients
# ===========================================================================


class TestTrashSweepGatesAndDefers:
    """Papierkorb-Gate der Vorschau und Hold-Recheck unter der Zeilensperre."""

    def test_the_dry_run_preview_also_honours_the_trash_gate(self, facility, staff_user, settings_obj):
        """Auch die Ops-Vorschau haelt das Papierkorb-Gate.

        Pin: ``x_anonymize_eligible_soft_deleted_clients__mutmut_13`` —
        ``filter(is_deleted=True, deleted_at__lt=cutoff, anonymized_at__isnull=True)``
        → das ``is_deleted``-Gate der AUSWAHL-Query entfaellt.

        Der Aequivalenz-Nachweis in
        ``test_mutation_pins_090.py::test_a_restored_client_is_never_anonymized``
        stuetzt sich auf das ZWEITE ``is_deleted``-Gate der gesperrten Zeile —
        das der ``dry_run``-Zweig nie erreicht: er gibt ``qs.count()`` zurueck,
        bevor die Schleife beginnt. Der Mutant ist damit killbar (die
        Aequivalenz-Notiz dort gehoert korrigiert).

        Eine wiederhergestellte Person (``is_deleted=False``) mit noch stehendem,
        abgelaufenem ``deleted_at`` ist der Zustand, den ``restore_client``
        hinterlaesst.

        Original: die Vorschau zaehlt 0.
        Mutant: sie zaehlt 1 — die Ops-Vorschau kuendigt die irreversible
        Tilgung einer LEBENDEN Akte an. Abweichender Wert: der Rueckgabewert
        von ``anonymize_eligible_soft_deleted_clients(..., dry_run=True)`` ist
        beim Mutanten ``1`` statt ``0``.
        """
        person = _make_person(facility, staff_user, "Wiederhergestellt-13")
        expired = timezone.now() - timedelta(days=settings_obj.client_trash_days + 5)
        Client.objects.filter(pk=person.pk).update(is_deleted=False, deleted_at=expired)

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj, dry_run=True) == 0

        # Gegenprobe auf derselben Zeile: auch der Echt-Lauf ruehrt sie nicht an.
        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 0
        person.refresh_from_db()
        assert person.anonymized_at is None
        assert person.pseudonym == "Wiederhergestellt-13"

    def test_a_held_client_only_defers_itself_not_the_whole_sweep(
        self, facility, settings_obj, admin_user, doc_type_contact
    ):
        """Eine gehaltene Person stellt sich zurueck, nicht den ganzen Lauf.

        Pin: ``x_anonymize_eligible_soft_deleted_clients__mutmut_42`` —
        ``if client_has_active_hold(client): continue`` → ``break``.

        Der Hold-Recheck unter der Zeilensperre (Refs #2108) soll GENAU die
        gehaltene Person zurueckstellen. Zwei faellige Personen, Hold nur auf
        der ersten; ``exclude_clients_with_active_holds`` wird durch ein
        nach ``pseudonym`` sortierendes Passthrough ersetzt — das ist zugleich
        der stale Sammelstand eines Laufs, dessen Sammlung VOR der Hold-Anlage
        lief, und macht die Reihenfolge deterministisch
        (``"A-Gehalten"`` < ``"B-Frei"``).

        Original: die gehaltene Person wird uebersprungen, die freie
        anonymisiert — Rueckgabewert 1.
        Mutant: ``break`` bricht den gesamten Nachtlauf an der ersten
        gehaltenen Person ab. Abweichende Werte: der Rueckgabewert ist beim
        Mutanten ``0`` statt ``1``, und ``frei.anonymized_at`` bleibt ``None``
        statt gesetzt zu sein — alle danach faelligen Personen blieben
        unbegrenzt mit Klartext-PII im Papierkorb.
        """
        held = self._expired_person(facility, admin_user, doc_type_contact, "A-Gehalten")
        free = self._expired_person(facility, admin_user, doc_type_contact, "B-Frei")
        create_hold_for_target(
            facility,
            target_type="Client",
            target_id=held.pk,
            user=admin_user,
            reason="Behoerdenanordnung nach der Sammlung",
        )

        with mock.patch(
            "core.services.client.main.exclude_clients_with_active_holds",
            side_effect=lambda qs, fac: qs.order_by("pseudonym"),
        ):
            count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        held.refresh_from_db()
        free.refresh_from_db()
        assert count == 1
        assert held.anonymized_at is None
        assert held.pseudonym == "A-Gehalten"
        assert free.anonymized_at is not None

    @staticmethod
    def _expired_person(facility, user, doc_type, pseudonym):
        person = Client.objects.create(
            facility=facility,
            pseudonym=pseudonym,
            contact_stage=Client.ContactStage.IDENTIFIED,
            notes="Interne Notiz im Klartext",
        )
        _make_event(facility, person, doc_type, user)
        person.soft_delete(user=user)
        Client.objects.filter(pk=person.pk).update(deleted_at=timezone.now() - timedelta(days=400))
        person.refresh_from_db()
        return person


# ===========================================================================
# core.services.client.main.get_pending_deletion_request
# ===========================================================================


class TestPendingDeletionRequestLookupIsPersonBound:
    """Der laufende Loeschantrag gehoert zu GENAU dieser Person.

    Die Funktion ist seit Refs #1736 (A) die eine Quelle fuer Detail-Banner,
    Antragsseite, Idempotenz-Zweig von ``request_client_deletion`` und den
    Merge-Blocker.
    """

    def test_a_request_of_another_person_is_not_reported(self, facility, staff_user):
        """Der gefundene Antrag gehoert zu GENAU dieser Person.

        Pin: ``x_get_pending_deletion_request__mutmut_8`` —
        ``filter(…, target_id=client.pk, …)``
        → der ``target_id``-Filter entfaellt.

        Original: fuer die Person OHNE Antrag ist das Ergebnis ``None``, fuer
        die Person MIT Antrag genau dieser Antrag.
        Mutant: ohne ``target_id`` liefert die Funktion IRGENDEINEN offenen
        Personen-Loeschantrag der Einrichtung. Abweichender Wert:
        ``get_pending_deletion_request(ohne_antrag)`` ist beim Mutanten der
        Antrag von ``mit_antrag`` (dieselbe ``pk``) statt ``None`` — das
        Detail-Banner wuerde fremden Antragsteller und fremden Grund an der
        falschen Person nennen, und der Merge-/Loesch-Blocker wiese einen
        berechtigten Antrag ab.
        """
        with_request = _make_person(facility, staff_user, "Antragsteller-Ziel")
        without_request = _make_person(facility, staff_user, "Ohne-Antrag")
        request = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=with_request.pk,
            reason="Betroffener verlangt Loeschung",
            requested_by=staff_user,
        )

        assert get_pending_deletion_request(without_request) is None
        found = get_pending_deletion_request(with_request)
        assert found is not None
        assert found.pk == request.pk


# ── core.services.security.lockout_recovery ─────────────────────────────────
#
# Mutations-Pins fuer ``core.services.security.lockout_recovery`` (Welle E / 0.92-Nachlauf).
#
# Quelle der Vorgaben: Survivor-Triage des 0.92-Nachlaufs, Prio-1-Set zu
# ``src/core/services/security/lockout_recovery.py`` (11 bestaetigte Luecken).
# Gepinnt werden die vier Verteidigungslinien des Recovery-Tokens (Refs #869,
# #1273, #2111):
#
# * **Anker-Skopierung** — ``_latest_failed_anchor`` liest nur die Fehlversuche
#   des ANGEFRAGTEN Users (Mutant 12 nimmt sonst den juengsten Fehlversuch
#   eines BELIEBIGEN Users; die Pin-Fixtures ``staff_user``/``assistant_user``
#   teilen sich dieselbe ``facility``-Fixture — geprueft wird hier also ein
#   ANDERER Nutzer DERSELBEN Einrichtung, keine fremde Einrichtung);
# * **TTL** — ``TimestampSigner.unsign`` wird mit ``_MAX_AGE_SECONDS``
#   aufgerufen (Mutanten 6/8 lassen abgelaufene Links unbegrenzt gelten);
# * **Identitaets- und Aktiv-Bindung** — ``User.objects.get(pk=..., is_active=True)``
#   (Mutanten 17/18);
# * **RLS-Sitzungskontext** — ``_set_session_vars(user.facility, is_super_admin=False)``
#   vor dem AuditLog-Anker-Read (Mutanten 21/25/30/31/32, Regression #2111);
# * **Einmal-Nutzung** — Bindung des Ankers an genau die eine ``pk``
#   (Mutant 47).
#
# Konventionen wie ``src/tests/test_mutation_pins_091_cut.py``: Zusicherungen
# vergleichen EXAKT (``==``/``is None``), nie per Teilstring (mutmut erzeugt
# ``"XX<text>XX"``-Varianten); keine Fixture-Faken (echte Model-Objekte aus
# ``src/tests/conftest.py``); KEIN Import aus ``test_rls_functional`` (its fixture
# ``rls_test_role`` skips itself under ``MUTANT_UNDER_TEST``; a pin requesting it never
# runs in a mutation run); jeder
# Test nennt seine Mutanten in der Docstring samt abweichendem Wert.


def _failed(user) -> AuditLog:
    """Legt einen echten LOGIN_FAILED-Eintrag fuer ``user`` an."""
    return AuditLog.objects.create(
        user=user,
        facility=user.facility,
        action=AuditLog.Action.LOGIN_FAILED,
        detail={"username": user.username},
    )


# ============================================================================
# _latest_failed_anchor — Anker ist auf den anfragenden User skopiert
# ============================================================================


class TestLatestFailedAnchorIsScopedToUser:
    """Der Anker gehoert dem anfragenden User — nie einem fremden."""

    def test_anchor_ignores_newer_failure_of_another_user(self, staff_user, assistant_user):
        """Pin: x__latest_failed_anchor__mutmut_12 — filter(user=user, action=...) -> filter(action=...).

        Aufbau: erst zwei Fehlversuche von ``staff_user``, DANACH ein juengerer
        von ``assistant_user``. Das Original liefert die UUID des juengsten
        Fehlversuchs VON staff_user; der Mutant liefert (per explizitem
        ``.order_by("-timestamp", "-id")`` in der Quelle, nicht ueber
        ``Meta.ordering``) die UUID des assistant-Eintrags — also einen
        fremden AuditLog-Schluessel im signierten, per Mail versendeten Token.

        Abweichender Wert beim Mutanten: ``_latest_failed_anchor(staff_user)``
        == ``str(fremd.id)`` statt ``str(eigen_neu.id)``; in der Folge findet
        ``verify_recovery_token`` unter ``pk=anchor, user=staff_user`` nichts
        mehr und liefert ``None`` statt ``staff_user``.
        """
        _failed(staff_user)
        eigen_neu = _failed(staff_user)
        fremd = _failed(assistant_user)

        assert _latest_failed_anchor(staff_user) == str(eigen_neu.id)
        assert _latest_failed_anchor(staff_user) != str(fremd.id)

        token = build_recovery_token(staff_user)
        assert verify_recovery_token(token) == staff_user


# ============================================================================
# verify_recovery_token — TTL, Identitaet, RLS-Kontext, Einmal-Nutzung
# ============================================================================


class TestVerifyRecoveryTokenTTL:
    """Die 30-Minuten-Lebensdauer ist Teil der Pruefung, nicht Doku."""

    def test_expired_token_is_rejected(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_6 — max_age=_MAX_AGE_SECONDS -> max_age=None.
        Pin: x_verify_recovery_token__mutmut_8 — max_age-Argument entfaellt (Django-Default None).

        ``_MAX_AGE_SECONDS`` wird auf ``-1`` gepatcht: jeder frisch erzeugte
        Token ist damit sofort ueberaltert. Das Original faengt die daraus
        folgende ``SignatureExpired`` und liefert ``None``.

        Abweichender Wert bei beiden Mutanten: die TTL-Konstante wird gar nicht
        mehr an ``unsign`` gereicht, die Signatur bleibt gueltig, und
        ``verify_recovery_token`` liefert ``staff_user`` statt ``None`` — ein
        beliebig alter Recovery-Link bliebe unbegrenzt einloesbar.
        """
        _failed(staff_user)
        token = build_recovery_token(staff_user)

        # Gegenprobe: ungepatcht ist derselbe Token gueltig.
        assert verify_recovery_token(token) == staff_user

        with mock.patch("core.services.security.lockout_recovery._MAX_AGE_SECONDS", -1):
            assert verify_recovery_token(token) is None


class TestVerifyRecoveryTokenIdentityBinding:
    """Der Token ist an die signierte User-PK und an einen AKTIVEN Account gebunden."""

    def test_token_resolves_exactly_the_signed_user(self, staff_user, assistant_user):
        """Pin: x_verify_recovery_token__mutmut_17 — get(pk=int(pk_str), is_active=True) -> get(is_active=True).

        Es existieren zwei aktive User. Das Original loest jeden Token auf die
        im Payload signierte PK auf.

        Abweichendes Verhalten beim Mutanten: die PK-Bedingung faellt weg,
        ``User.objects.get(is_active=True)`` trifft zwei Zeilen und wirft ein
        ungefangenes ``User.MultipleObjectsReturned`` (weder ``DoesNotExist``
        noch ``ValueError``) — der Aufruf endet im Fehler statt mit dem User.
        """
        token_staff = build_recovery_token(staff_user)
        token_assistant = build_recovery_token(assistant_user)

        assert verify_recovery_token(token_staff) == staff_user
        assert verify_recovery_token(token_assistant) == assistant_user

    def test_deactivated_user_cannot_redeem_token(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_18 — get(pk=..., is_active=True) -> get(pk=...).

        Der Token wird gebaut, waehrend der Account aktiv ist; danach wird der
        User deaktiviert (Offboarding zwischen Versand und Klick). Das Original
        findet unter ``is_active=True`` nichts (``DoesNotExist``) und liefert
        ``None``; ``consume_recovery_token`` schreibt entsprechend keinen
        LOGIN_UNLOCK.

        Abweichender Wert beim Mutanten: ``verify_recovery_token`` liefert
        ``staff_user`` statt ``None`` und ``consume_recovery_token`` legt einen
        LOGIN_UNLOCK an (Zaehler 1 statt 0).
        """
        _failed(staff_user)
        token = build_recovery_token(staff_user)

        staff_user.is_active = False
        staff_user.save(update_fields=["is_active"])

        assert verify_recovery_token(token) is None
        assert consume_recovery_token(token, ip_address=None) is None
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).count() == 0


class TestVerifyRecoveryTokenSetsRlsSessionContext:
    """Der anonyme Recovery-Request setzt den Ziel-Facility-GUC — und NICHT Super-Admin."""

    def test_session_vars_called_with_target_facility_and_no_super_admin(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_21 — _set_session_vars(getattr(user, "facility", None), ...)
        -> _set_session_vars(None, ...).
        Pin: x_verify_recovery_token__mutmut_25 — getattr(user, "facility", None) -> getattr(None, "facility", None).
        Pin: x_verify_recovery_token__mutmut_30 — getattr(user, "facility", None)
        -> getattr(user, "XXfacilityXX", None).
        Pin: x_verify_recovery_token__mutmut_31 — getattr(user, "facility", None) -> getattr(user, "FACILITY", None).
        Pin: x_verify_recovery_token__mutmut_32 — is_super_admin=False -> is_super_admin=True.

        Spy auf ``_set_session_vars`` im Modul-Namensraum: das Original ruft
        genau einmal mit der Einrichtung des Ziel-Users und ``is_super_admin=False``.

        Abweichende Werte: 21/25/30/31 uebergeben ``None`` als erstes Argument
        (der Facility-GUC bliebe leer ⇒ der RLS-gebundene Anker-Read sieht unter
        der Prod-Rolle nichts, Regression #2111); 32 uebergibt
        ``is_super_admin=True`` (ein unauthentifizierter Request wuerde den
        Super-Admin-Zweig der RLS-Policies aktivieren).
        """
        _failed(staff_user)
        token = build_recovery_token(staff_user)

        with mock.patch("core.services.security.lockout_recovery._set_session_vars") as spy:
            assert verify_recovery_token(token) == staff_user

        assert spy.call_args_list == [mock.call(staff_user.facility, is_super_admin=False)]

    def test_session_gucs_hold_target_facility_and_empty_super_admin(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_21, __mutmut_25, __mutmut_30, __mutmut_31, __mutmut_32.

        Wirkungsnachweis ohne Patch, direkt an der Postgres-Session: nach
        ``verify_recovery_token`` traegt ``app.current_facility_id`` die UUID der
        Ziel-Einrichtung und ``app.is_super_admin`` ist leer.

        Abweichende Werte: bei 21/25/30/31 ist ``app.current_facility_id`` ``""``
        (``_set_session_vars`` bekommt ``None``), bei 32 ist
        ``app.is_super_admin`` ``"true"``.
        """
        if connection.vendor != "postgresql":
            pytest.skip("GUC-Nachweis nur unter PostgreSQL aussagekraeftig")

        _failed(staff_user)
        token = build_recovery_token(staff_user)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_facility_id', '', false), "
                "       set_config('app.is_super_admin', '', false)"
            )

        assert verify_recovery_token(token) == staff_user

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('app.current_facility_id', true), "
                "       current_setting('app.is_super_admin', true)"
            )
            facility_guc, super_admin_guc = cursor.fetchone()

        assert facility_guc == str(staff_user.facility.pk)
        assert super_admin_guc == ""


class TestVerifyRecoveryTokenAnchorIsPkBound:
    """Der Anker ist an GENAU eine AuditLog-Zeile gebunden (Einmal-Nutzung, Refs #1273)."""

    def test_foreign_anchor_row_invalidates_token(self, staff_user, assistant_user):
        """Pin: x_verify_recovery_token__mutmut_47 — filter(pk=anchor_pk, user=user, action=...)
        -> filter(user=user, action=...).

        Der Token wird von Hand mit demselben Salt signiert, traegt aber als
        Anker die UUID eines LOGIN_FAILED-Eintrags eines ANDEREN Users
        (AuditLog-Zeilen sind unveraenderlich, ein "geloeschter" Anker laesst
        sich nicht herstellen — der fremde Anker ist derselbe Guard-Pfad
        "Anker verschwunden/fremd"). Das Original findet unter
        ``pk=anchor_pk, user=staff_user`` nichts und liefert ``None``.

        Abweichender Wert beim Mutanten: ohne PK-Bindung liefert der Read den
        Zeitstempel des juengsten eigenen Fehlversuchs, der Guard greift nicht,
        und ``verify_recovery_token`` gibt ``staff_user`` statt ``None`` zurueck.
        """
        _failed(staff_user)
        _failed(staff_user)
        fremd = _failed(assistant_user)

        signer = TimestampSigner(salt=_SALT)
        fremd_token = signer.sign(f"{staff_user.pk}:{fremd.id}")
        eigen_token = signer.sign(f"{staff_user.pk}:{_latest_failed_anchor(staff_user)}")

        # Gegenprobe: mit eigenem Anker ist derselbe Aufbau gueltig.
        assert verify_recovery_token(eigen_token) == staff_user
        assert verify_recovery_token(fremd_token) is None

    def test_token_stays_consumed_after_new_failures(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_47 — Anker-Bindung an die eine pk.

        Replay-Szenario: Token einloesen (LOGIN_UNLOCK), danach entstehen neue
        LOGIN_FAILED-Eintraege. Das Original vergleicht den LOGIN_UNLOCK weiter
        gegen den GEBUNDENEN Fehlversuch — der Link bleibt verbraucht.

        Abweichender Wert beim Mutanten: der Anker rutscht auf den neuen,
        juengeren Fehlversuch; ``last_unlock_ts > anchor_ts`` ist falsch, der
        zweite ``consume_recovery_token`` liefert ``staff_user`` statt ``None``
        und der LOGIN_UNLOCK-Zaehler steht auf 2 statt 1.
        """
        for _ in range(3):
            _failed(staff_user)
        token = build_recovery_token(staff_user)

        assert consume_recovery_token(token, ip_address=None) == staff_user
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).count() == 1

        for _ in range(3):
            _failed(staff_user)

        assert consume_recovery_token(token, ip_address=None) is None
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).count() == 1


# ── core.services.security.mfa ───────────────────────────────────────────────
#
# Pin-Tests des 0.92-Nachlaufs — Prio-1-Survivors in ``core.services.security.mfa``.
#
# Quelle: Survivor-Triage des ``mutation-delta``-Nachlaufs zur Welle E (0.92),
# Vorgabe ``welle-e-0.92/nachlauf/pin-mfa.json``. Gepinntes Modul:
# ``src/core/services/security/mfa.py`` (Backup-Code-Recovery, Refs #588/#790).
#
# Drei bestaetigte Testluecken, alle in derselben Wurzel — die Zeilen der Datei
# binden Geraet und Verifikation an den **Eigentuemer** bzw. an den
# **Drosselungszustand**, und genau diese beiden Zusicherungen waren von keinem
# Test beobachtet, weil ``test_mfa_backup_codes.py`` durchgaengig mit einem
# einzigen Nutzer (``staff_user``) und nie im gesperrten Zustand faehrt:
#
# * ``x_remaining_backup_codes__mutmut_4`` (mfa.py:90) — Wegfall des
#   ``user=user``-Filters: die MFA-Einstellungen zeigten die Restcode-Zahl eines
#   FREMDEN Kontos (Cross-User-Informationsabfluss).
# * ``x_verify_backup_code__mutmut_7`` (mfa.py:107) — Wegfall des
#   ``user=user``-Filters: ein fremder Backup-Code wuerde den 2FA-Login
#   (``views/mfa``, ``mode=backup``) und den Sudo-Mode-Re-Auth
#   (``views/sudo_mode.py``) oeffnen — Authentifizierungs-Bypass.
# * ``x_verify_backup_code__mutmut_15`` (mfa.py:113) — ``if not verify_allowed:
#   return False`` -> ``return True``: im gedrosselten Zustand verifiziert die
#   Funktion JEDE Eingabe, ohne einen Code zu pruefen. Der Angreifer muesste nur
#   genug Fehlversuche erzeugen.
#
# Konventionen wie ``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen
# EXAKT (``==`` / ``is``), nie per Teilstring (mutmut erzeugt
# ``"XX<text>XX"``-Varianten); KEINE Imports aus ``test_rls_functional`` (its fixture
# ``rls_test_role`` skips itself under ``MUTANT_UNDER_TEST``; a pin requesting it never
# runs in a mutation run); keine
# Fixture-Faken, sondern echte ``User``-/``StaticDevice``-Zeilen; jeder Test nennt
# seine Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
# ``translation.override`` ist hier nicht noetig — die gepinnten Zeilen liefern
# ausschliesslich ``bool``/``int``, keinen uebersetzten Text.


@pytest.fixture
def mfa_pin_user_a(facility):
    return User.objects.create_user(
        username="pin092n-mfa-a", password="pin092npass!A", role=User.Role.STAFF, facility=facility
    )


@pytest.fixture
def mfa_pin_user_b(facility):
    return User.objects.create_user(
        username="pin092n-mfa-b", password="pin092npass!B", role=User.Role.STAFF, facility=facility
    )


class TestRemainingBackupCodesIsOwnerBound:
    """``remaining_backup_codes`` zaehlt nur die Codes des uebergebenen Nutzers.

    Gepinnter Mutant an ``mfa.py:90``
    (``StaticDevice.objects.filter(user=user, name=BACKUP_DEVICE_NAME).first()``).
    """

    def test_foreign_backup_device_is_not_counted(self, mfa_pin_user_a, mfa_pin_user_b):
        """Pin: ``x_remaining_backup_codes__mutmut_4`` — Wegfall des Eigentuemer-Filters:
        ``filter(user=user, name=BACKUP_DEVICE_NAME)`` -> ``filter(name=BACKUP_DEVICE_NAME)``.

        ``mfa_pin_user_a`` hat einen frischen Satz Backup-Codes,
        ``mfa_pin_user_b`` gar keinen. Original: ``filter(user=b, …)`` findet
        nichts -> ``device is None`` -> **0**. Ohne Eigentuemer-Filter liefert
        ``.first()`` zwangslaeufig A's Geraet — die EINZIGE Zeile der Tabelle,
        deshalb ohne Reihenfolgerisiko — und der Mutant meldet **10**
        (``BACKUP_CODES_COUNT``) fremde Restcodes.
        """
        generate_backup_codes(mfa_pin_user_a)
        assert StaticDevice.objects.count() == 1

        assert remaining_backup_codes(mfa_pin_user_b) == 0

    def test_own_backup_codes_are_counted(self, mfa_pin_user_a):
        """Kontrolle (kein Pin): der Positivfall bleibt positiv.

        Ohne ihn koennte der Pin oben auch dadurch gruen sein, dass
        ``remaining_backup_codes`` grundsaetzlich 0 liefert.
        """
        generate_backup_codes(mfa_pin_user_a)

        assert remaining_backup_codes(mfa_pin_user_a) == BACKUP_CODES_COUNT


class TestVerifyBackupCodeOwnerBoundAndThrottled:
    """``verify_backup_code`` bleibt eigentuemergebunden und respektiert die Drosselung.

    Gepinnte Mutanten an ``mfa.py:107`` (Geraete-Query) und ``mfa.py:113``
    (Throttle-Blockpfad).
    """

    def test_foreign_backup_code_does_not_verify_when_owner_has_none(self, mfa_pin_user_a, mfa_pin_user_b):
        """Pin: ``x_verify_backup_code__mutmut_7`` — Wegfall des Eigentuemer-Filters:
        ``filter(user=user, name=…, confirmed=True)`` -> ``filter(name=…, confirmed=True)``.

        ``mfa_pin_user_b`` besitzt Backup-Codes, ``mfa_pin_user_a`` kein
        Geraet. Original: ``filter(user=a, …)`` -> ``None`` -> Rueckgabe
        **False**, B's Codes bleiben unangetastet (**10**). Ohne
        Eigentuemer-Filter findet ``.first()`` B's bestaetigtes Geraet (die
        einzige Zeile der Tabelle), der Drosselungszustand ist frisch, der
        Hash-Treffer wird geloescht — der Mutant liefert **True** und laesst
        nur noch **9** Restcodes zurueck. Beide Zusicherungen weichen also ab.
        """
        codes = generate_backup_codes(mfa_pin_user_b)
        assert StaticDevice.objects.count() == 1

        assert verify_backup_code(mfa_pin_user_a, codes[0]) is False
        assert remaining_backup_codes(mfa_pin_user_b) == BACKUP_CODES_COUNT

    def test_throttled_device_rejects_even_a_valid_code(self, mfa_pin_user_a):
        """Pin: ``x_verify_backup_code__mutmut_15`` — Throttle-Blockpfad kippt:
        ``if not verify_allowed: return False`` -> ``return True``.

        Das Geraet wird in den realen Sperrzustand von django-otps
        ``ThrottlingMixin`` versetzt: ``throttling_failure_count`` > 0 und ein
        frischer ``throttling_failure_timestamp``. ``StaticDevice.get_throttle_factor``
        liefert 1 (kein ``OTP_STATIC_THROTTLE_FACTOR`` in den Settings), die
        geforderte Sperrfrist betraegt also ``2 ** (6 - 1) = 32`` Sekunden —
        komfortabel mehr als die Laufzeit des folgenden Aufrufs, damit der Pin
        auch unter Last nicht kippt. Fragilitaets-Hinweis: der Pin haengt an
        dieser django-otp-Default-Throttle-Konfiguration
        (``OTP_STATIC_THROTTLE_FACTOR``, Default 1) — wird dieses Setting
        projektweit gesetzt, aendert sich die Sperrfrist mit und die 32-Sekunden-
        Marge muss neu bewertet werden.

        Original: ``verify_is_allowed()`` -> ``(False, …)`` -> Rueckgabe
        **False**, und zwar OHNE den Code zu verbrauchen (**10** Restcodes).
        Der Mutant liefert an derselben Stelle **True** — Verifikation ohne
        jede Codepruefung. Der abweichende Wert ist der Rueckgabewert; die
        Restcode-Zusicherung haelt den Nicht-Verbrauch zusaetzlich fest.
        """
        codes = generate_backup_codes(mfa_pin_user_a)
        device = StaticDevice.objects.get(user=mfa_pin_user_a, name=BACKUP_DEVICE_NAME)
        device.throttling_failure_count = 6
        device.throttling_failure_timestamp = timezone.now()
        device.save(update_fields=["throttling_failure_count", "throttling_failure_timestamp"])
        assert device.verify_is_allowed()[0] is False

        assert verify_backup_code(mfa_pin_user_a, codes[0]) is False
        assert remaining_backup_codes(mfa_pin_user_a) == BACKUP_CODES_COUNT

    def test_own_code_verifies_on_an_unthrottled_device(self, mfa_pin_user_a):
        """Kontrolle (kein Pin): der Positivfall bleibt positiv.

        Ohne ihn koennten beide Pins oben auch dadurch gruen sein, dass
        ``verify_backup_code`` grundsaetzlich ``False`` liefert.
        """
        codes = generate_backup_codes(mfa_pin_user_a)

        assert verify_backup_code(mfa_pin_user_a, codes[0]) is True
        assert remaining_backup_codes(mfa_pin_user_a) == BACKUP_CODES_COUNT - 1
