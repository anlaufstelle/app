"""Mutations-Pins Welle L (0.99), Vollmutation #2174, Batch deletion-b.

Quelle: ``core.services.case.deletion`` (Loeschworkflow fuer Faelle, Refs
#1818/ADR-044, Sperrkette Refs #2123). Gepinnt wird das Prio-1/2/3-``test``-Set
der Batch-deletion-b-Triage (16 Survivor, sieben Funktionen): eine
Audit-Detail-Verfaelschung bei der endgueltigen Tilgung (Art. 5 Abs. 2), ein
Fallliste-Badge, das ueber die uebergebene Seite hinaus und ueber den
PENDING-Status hinweg meldet, ein Idempotenz-Flag, das bei einem
Vier-Augen-Antrag "neu" und "lief schon" vertauscht, eine geschwaechte
Verteidigungslinie der Sperrkette, eine fehlende Query-Vorladung und eine
fallfremde Aktivitaets-Redaktion bei ID-Kollision.

Konventionen wie ``src/tests/test_mutation_pins_094_case_deletion.py``:
Zusicherungen vergleichen EXAKT, nie per Teilstring; ``pytest.mark.django_db``
ueberall (reine ORM-Schreibwege); keine gefakten Fixtures — jeder Test legt
echte Model-Zeilen an; keine Imports aus ``test_rls_functional``.

Nicht gepinnt (siehe Bericht/Triage-JSON fuer Details):

* ``x__move_case_to_trash__mutmut_1``..``_5`` — reine Fehlertext-Varianten des
  Doppel-Loesch-Guards (``skip``, kein Fluss in Hash/Audit-Detail).
* ``x__lock_case_chain__mutmut_25`` — ``equivalent`` (facility_id-Filter der
  dritten Sperr-Query ist redundant, siehe Triage-Begruendung).
* ``x_redact_case_goals__mutmut_6`` — ``equivalent`` (``values_list(flat=True)``
  faellt bei ``OutcomeGoal`` auf das erste concrete field ``id`` == ``pk``
  zurueck, empirisch per ``.query``-Vergleich verifiziert).

Eine Testklasse je Funktion:

* :class:`TestPendingDeletionCaseIdsBleibtBeimUmfang` — mutmut 12/13
* :class:`TestCreateOrGetCaseDeletionRequestIdempotenzflag` — mutmut 4/35
* :class:`TestAnonymizeCaseAuditDetail` — mutmut 1/22
* :class:`TestLockCaseChainQueryAnzahl` — mutmut 9
* :class:`TestGetPendingCaseDeletionRequestPreload` — mutmut 1
* :class:`TestRedactCaseActivitiesZieltyp` — mutmut 4
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Activity, AuditLog, Case, Client
from core.models.workitem import DeletionRequest
from core.services.case.deletion import (
    _lock_case_chain,
    _redact_case_activities,
    anonymize_case,
    create_or_get_case_deletion_request,
    get_pending_case_deletion_request,
    pending_deletion_case_ids,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _person(facility, staff_user, pseudonym):
    """Echte Personenzeile — kein Fixture-Fake."""
    return Client.objects.create(
        facility=facility,
        pseudonym=pseudonym,
        contact_stage=Client.ContactStage.IDENTIFIED,
        created_by=staff_user,
    )


def _fall(facility, person, staff_user, titel):
    return Case.objects.create(
        facility=facility,
        client=person,
        title=titel,
        status=Case.Status.OPEN,
        created_by=staff_user,
    )


def _im_papierkorb(fall, *, deleted_at):
    """Setzt den Fall direkt in den Papierkorb mit einem festen Zeitstempel."""
    Case.objects.filter(pk=fall.pk).update(is_deleted=True, deleted_at=deleted_at)
    fall.refresh_from_db()
    return fall


# ============================================================================
# core.services.case.deletion.pending_deletion_case_ids
# ============================================================================


class TestPendingDeletionCaseIdsBleibtBeimUmfang:
    """Das Fallliste-Badge darf weder ueber die Seite noch ueber PENDING hinausmelden."""

    def test_fall_ausserhalb_der_seite_setzt_kein_badge(self, facility, client_identified, staff_user):
        """Pin: ``x_pending_deletion_case_ids__mutmut_12`` — ``target_id__in=ids`` entfaellt komplett.

        Ohne den Seitenfilter meldete jeder PENDING-Fall-Antrag der
        Einrichtung ein Badge, auch fuer Faelle, die gar nicht auf der
        uebergebenen (paginierten) Seite stehen.

        Original: ``set()`` (nur ``fall_a`` wird uebergeben, hat aber keinen
        eigenen Antrag).
        Mutant: ``{fall_b.pk}`` (``fall_b``s Antrag zaehlt trotz Ausschluss
        aus der uebergebenen Liste).
        """
        fall_a = _fall(facility, client_identified, staff_user, "Fall A (auf der Seite)")
        fall_b = _fall(facility, client_identified, staff_user, "Fall B (nicht auf der Seite)")
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=fall_b.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Antrag fuer Fall B",
            requested_by=staff_user,
        )

        assert pending_deletion_case_ids(facility, [fall_a]) == set()

    def test_entschiedener_antrag_setzt_kein_badge(self, facility, client_identified, staff_user, admin_user):
        """Pin: ``x_pending_deletion_case_ids__mutmut_13`` — ``status=PENDING`` entfaellt komplett.

        Ohne den Statusfilter zeigte das Badge "Loeschantrag laeuft" auch fuer
        Faelle, deren Antrag laengst abgelehnt oder genehmigt wurde —
        irrefuehrender Fallstatus fuer die Fachkraft.

        Original: ``set()``.
        Mutant: ``{fall.pk}`` (der REJECTED-Antrag zaehlt weiter mit).
        """
        fall = _fall(facility, client_identified, staff_user, "Fall mit entschiedenem Antrag")
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=fall.pk,
            status=DeletionRequest.Status.REJECTED,
            reason="Bereits abgelehnt",
            requested_by=staff_user,
            reviewed_by=admin_user,
            reviewed_at=timezone.now(),
        )

        assert pending_deletion_case_ids(facility, [fall]) == set()


# ============================================================================
# core.services.case.deletion.create_or_get_case_deletion_request
# ============================================================================


class TestCreateOrGetCaseDeletionRequestIdempotenzflag:
    """Das zweite Tupel-Element unterscheidet "neu angelegt" von "lief schon"."""

    def test_zweiter_aufruf_meldet_keine_neuanlage(self, facility, client_identified, staff_user):
        """Pin: ``x_create_or_get_case_deletion_request__mutmut_4`` — Idempotenz-Zweig: ``False`` -> ``True``.

        Ein zweiter Aufruf auf denselben Fall faellt in den
        Idempotenz-Zweig (der erste Antrag laeuft bereits); der Aufrufer
        wuerde bei ``created=True`` faelschlich eine Erfolgsmeldung fuer eine
        Neuanlage zeigen, die nie stattgefunden hat.

        Original: ``created`` ist beim zweiten Aufruf ``False``.
        Mutant: ``True``.
        """
        fall = _fall(facility, client_identified, staff_user, "Fall mit Antrag")

        antrag_1, erstellt_1 = create_or_get_case_deletion_request(fall, staff_user, "Erster Antrag")
        antrag_2, erstellt_2 = create_or_get_case_deletion_request(fall, staff_user, "Zweiter Antrag")

        assert erstellt_1 is True
        assert erstellt_2 is False
        assert antrag_2.pk == antrag_1.pk
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.DELETION_REQUESTED,
                target_type="DeletionRequest",
                target_id=str(antrag_1.pk),
            ).count()
            == 1
        )

    def test_erster_aufruf_meldet_die_neuanlage(self, facility, client_identified, staff_user):
        """Pin: ``x_create_or_get_case_deletion_request__mutmut_35`` — Neuanlage-Zweig: ``True`` -> ``False``.

        Spiegelbug zu mutmut_4: eine ECHTE Neuanlage wuerde als
        Idempotenz-Treffer gemeldet, die Erfolgsmeldung faellt fuer den
        Erstantrag aus.

        Original: ``created`` ist beim ersten Aufruf ``True``.
        Mutant: ``False``.
        """
        fall = _fall(facility, client_identified, staff_user, "Fall ohne Antrag")

        antrag, erstellt = create_or_get_case_deletion_request(fall, staff_user, "Erstantrag")

        assert erstellt is True
        assert antrag.target_id == fall.pk


# ============================================================================
# core.services.case.deletion.anonymize_case
# ============================================================================


class TestAnonymizeCaseAuditDetail:
    """Die ``CASE_ANONYMIZED``-Zeile ist der Art.-5-Abs.-2-Nachweis der Tilgung."""

    def test_audit_detail_traegt_den_echten_papierkorb_zeitpunkt(self, facility, client_identified, staff_user):
        """Pin: ``x_anonymize_case__mutmut_1`` und ``x_anonymize_case__mutmut_22``.

        mutmut_1 setzt die lokale Variable ``deleted_at = None`` statt
        ``case.deleted_at``; mutmut_22 laesst die Variable korrekt, schreibt
        aber ``str(None)`` direkt in den ``audit_event``-Aufruf. Beide
        erzeugen dieselbe beobachtbare Verfaelschung: das ``detail["deleted_at"]``
        der ``CASE_ANONYMIZED``-Zeile wird IMMER die Zeichenkette ``"None"``
        statt des tatsaechlichen Papierkorb-Zeitpunkts — der
        Rechenschaftsnachweis verliert den Bezug zur Fristberechnung.

        Original: ``detail["deleted_at"] == str(<echter Zeitpunkt>)``.
        Beide Mutanten: ``detail["deleted_at"] == "None"``.
        """
        fall = _fall(facility, client_identified, staff_user, "Fall zur Tilgung")
        zeitpunkt = timezone.now() - timedelta(days=40)
        fall = _im_papierkorb(fall, deleted_at=zeitpunkt)
        erwartet = str(fall.deleted_at)

        anonymize_case(fall, user=staff_user, trigger="manual")

        audit = AuditLog.objects.get(
            action=AuditLog.Action.CASE_ANONYMIZED,
            target_type="Case",
            target_id=str(fall.pk),
        )
        assert audit.detail["deleted_at"] == erwartet
        assert audit.detail["deleted_at"] != "None"


# ============================================================================
# core.services.case.deletion._lock_case_chain
# ============================================================================


class TestLockCaseChainQueryAnzahl:
    """Der Anker der Fall-Tilgung (Refs #2123, ADR-050 D5.5) — Verteidigungslinien einzeln pruefen."""

    def test_fremde_einrichtung_bricht_nach_einer_query_ab(self, facility, second_facility, second_facility_user):
        """Pin: ``x__lock_case_chain__mutmut_9`` — ``facility_id``-Filter der ERSTEN Lesequery entfaellt.

        Der Rueckgabewert bleibt in beiden Faellen ``None`` (die DRITTE Query
        bleibt facility-scoped und faengt den Fall trotzdem ab) — beobachtbar
        ist die QUERY-ANZAHL: das Original bricht nach der ersten Query ab
        (``client_id is None``), der Mutant haengt zwei wirkungslose
        Sperrversuche (Client, dann Case) an einen Fall einer FREMDEN
        Einrichtung — eine Verteidigungslinie der Sperrkette wird
        geschwaecht, auch wenn die zweite sie noch auffaengt.

        Original: genau 1 Query.
        Mutant: 3 Queries.
        """
        person_fremd = _person(second_facility, second_facility_user, "Person der anderen Einrichtung")
        fall_fremd = _fall(second_facility, person_fremd, second_facility_user, "Fall der anderen Einrichtung")

        with CaptureQueriesContext(connection) as mitschnitt:
            ergebnis = _lock_case_chain(facility.pk, fall_fremd.pk)

        assert ergebnis is None
        assert len(mitschnitt.captured_queries) == 1, mitschnitt.captured_queries


# ============================================================================
# core.services.case.deletion.get_pending_case_deletion_request
# ============================================================================


class TestGetPendingCaseDeletionRequestPreload:
    """``requested_by`` wird vorgeladen, weil jede Anzeigeflaeche es liest."""

    def test_select_related_verhindert_lazy_nachladung(self, facility, client_identified, staff_user):
        """Pin: ``x_get_pending_case_deletion_request__mutmut_1``.

        ``select_related("requested_by")`` -> ``select_related(None)``.

        Echte Leser: ``src/templates/core/cases/detail.html`` und
        ``delete_request_confirm.html`` lesen
        ``pending_deletion_request.requested_by.display_name`` direkt aus dem
        Rueckgabewert dieser Funktion — ``select_related(None)`` loescht die
        JOIN-Vorladung ersatzlos (kein Nachweis "kein
        Leser" moeglich, also Query-Zaehl-Pin statt ``equivalent``).

        Original: genau 1 Query (JOIN preloaded ``requested_by``).
        Mutant: 2 Queries (Grundquery + Lazy-Nachladung beim ``.requested_by``-Zugriff).
        """
        fall = _fall(facility, client_identified, staff_user, "Fall mit Antrag")
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=fall.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Testantrag",
            requested_by=staff_user,
        )

        with CaptureQueriesContext(connection) as mitschnitt:
            antrag = get_pending_case_deletion_request(fall)
            _ = antrag.requested_by.username

        assert len(mitschnitt.captured_queries) == 1, mitschnitt.captured_queries


# ============================================================================
# core.services.case.deletion._redact_case_activities
# ============================================================================


class TestRedactCaseActivitiesZieltyp:
    """Die Aktivitaets-Redaktion (#1972) darf nur Zeilen DESSELBEN Zieltyps treffen."""

    def test_fremdtyp_mit_gleicher_ziel_id_bleibt_unredigiert(self, facility, client_identified, staff_user):
        """Pin: ``x__redact_case_activities__mutmut_4`` — ``target_type=ContentType(Case)`` entfaellt.

        ``Activity.target_id`` ist ein blankes ``UUIDField`` ohne FK
        (``GenericForeignKey``), von mehreren Zieltypen geteilt. Ohne den
        Typfilter wuerde die Tilgung eines Falls jede Activity-Zeile EINES
        ANDEREN Entitaetstyps mit zufaellig identischer ``target_id``
        (ID-Kollision) mitredigieren — konsistent mit der bereits gepinnten
        Schwester-Mutante ``x__redact_case_activities__mutmut_5``
        (``target_id`` entfaellt) aus ``test_mutation_pins_094_case_deletion.py``.

        Original: ``koeder.context`` bleibt der urspruengliche Kontext.
        Mutant: ``koeder.context`` wird auf den REDACTED-Kontext ueberschrieben.
        """
        fall = _fall(facility, client_identified, staff_user, "Fall zur Tilgung")
        koeder_kontext = {"schema": 1, "action": Activity.Action.CLIENT_UPDATED.value}
        koeder = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target_type=ContentType.objects.get_for_model(Client),
            target_id=fall.pk,
            context=koeder_kontext,
        )

        _redact_case_activities(fall)

        koeder.refresh_from_db()
        assert koeder.context == koeder_kontext
