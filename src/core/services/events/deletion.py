"""4-Augen-Loeschungs-Workflow fuer Events (Refs #777, #932).

``request_deletion`` legt einen ``DeletionRequest`` an (idempotent fuer
PENDING-Antraege), ``approve_deletion`` und ``reject_deletion`` schliessen
ihn.

DSGVO Art. 5 (2) Rechenschaftspflicht (Refs #932): jeder Workflow-Schritt
schreibt einen dedizierten ``AuditLog``-Eintrag mit ``DELETION_*``-Action
und ``target_type="DeletionRequest"``. Bei Approve kommt zusaetzlich der
``Action.DELETE``-Eintrag aus :func:`soft_delete_event` (target=Event).

Aufgeteilt aus dem alten ``services/event.py`` (#777).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event
from core.services.audit import audit_event
from core.services.client.restriction import _deletion_workflow_write, assert_processing_allowed
from core.services.events.crud import (
    CLIENT_BINDING_CHANGED_MESSAGE,
    _assert_actor_in_facility,
    _lock_client_row,
    _lock_event_row,
    soft_delete_event,
)


def get_pending_event_deletion_request(event):
    """Der laufende (PENDING) Loeschantrag dieses Ereignisses — sonst ``None``.

    Refs #2015: Muster ``get_pending_deletion_request`` (Personen-Pfad) und
    ``get_pending_case_deletion_request`` (Fall-Pfad). Der Ereignis-Pfad hatte
    als einziger keinen benannten Helfer, sondern eine Inline-Query in
    :func:`request_deletion` — die View konnte den Gewinner eines
    Antrags-Rennens deshalb gar nicht nachladen. Eine Quelle fuer beide
    Aufrufer.
    """
    return (
        DeletionRequest.objects.for_facility(event.facility)
        .filter(target_type="Event", target_id=event.pk, status=DeletionRequest.Status.PENDING)
        .first()
    )


def request_deletion(event, user, reason):
    """Create a deletion request for qualified data — nur der Antrag.

    Duenne Huelle um :func:`create_or_get_event_deletion_request` fuer alle
    Aufrufer, die den Unterschied „neu angelegt" vs. „lief schon" nicht
    brauchen.
    """
    return create_or_get_event_deletion_request(event, user, reason)[0]


def create_or_get_event_deletion_request(event, user, reason):
    """Wie :func:`request_deletion`, aber ``(antrag, created)``.

    Refs #2015 (Re-Review): Muster ``create_or_get_client_deletion_request``.
    Der Ereignis-Pfad hat keinen Vorab-Check in der View — die Idempotenz
    sitzt allein hier. Ein paralleler Zweit-POST landet deshalb regelmaessig
    im Zweig unten und muss als solcher erkennbar sein.

    Idempotent: if a PENDING DeletionRequest already exists for the same
    event, the existing record is returned instead of creating a duplicate
    (#530). Im Idempotenz-Fall wird KEIN neues Audit-Event geschrieben
    (Refs #932) — der DELETION_REQUESTED-Eintrag existiert bereits vom
    ersten Aufruf.
    """
    # The facility of the requesting person comes first — before the lookup of
    # a running request and before anything is written (fail-closed without a
    # facility). The same check guards the event services.
    _assert_actor_in_facility(user, event.facility_id)
    existing = get_pending_event_deletion_request(event)
    if existing is not None:
        return existing, False
    dr = DeletionRequest.objects.create(
        facility=event.facility,
        target_type="Event",
        target_id=event.pk,
        reason=reason,
        requested_by=user,
    )
    audit_event(
        action=AuditLog.Action.DELETION_REQUESTED,
        user=user,
        facility=event.facility,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
        # Refs #1093: reason nicht ins detail duplizieren — er lebt in
        # DeletionRequest.reason (via target_id=dr.pk erreichbar).
        detail={"target_event": str(event.pk)},
    )
    return dr, True


def _lock_person_and_event_then_request(deletion_request):
    """Lock the person row of the target event, the event row, then the request row.

    The anonymisation cascades (``anonymize_client`` and the k-anonymisation
    bridge of the retention run) lock the person first, then the person's
    cases and events, and write the deletion requests of these events
    afterwards. The bridge also reaches persons outside the trash, so a pending
    event request can meet it. Taking the request first here, and the person
    only inside :func:`soft_delete_event`, could deadlock against them.

    The target and its person are read without a lock. The facility-scoped read
    of the request keeps the answer for an unknown request (``DoesNotExist``);
    a target outside the facility locks nothing, and the caller answers as
    before. An event without a person has no person row to lock. The target of
    an event request is never written after filing, but the person of the
    event can change (a merge moves the events of its source). The caller
    therefore compares the person on the locked event row and refuses instead
    of locking the new person while it holds the event and the request.

    The two row locks are the ones :func:`soft_delete_event` takes on every
    other path; the caller tells it that both rows are already held.

    Returns the locked request and the person id the lock was taken for.
    """
    target_id = (
        DeletionRequest.objects.for_facility(deletion_request.facility)
        .filter(pk=deletion_request.pk)
        .values_list("target_id", flat=True)
        .get()
    )
    event = (
        Event.objects.for_facility(deletion_request.facility_id).filter(pk=target_id).only("pk", "client_id").first()
    )
    person_id = event.client_id if event is not None else None
    if event is not None:
        _lock_client_row(person_id)
        _lock_event_row(event, facility_id=deletion_request.facility_id)
    locked_request = DeletionRequest.objects.select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility
    )
    return locked_request, person_id


@transaction.atomic
def approve_deletion(deletion_request, reviewer):
    """Approve a deletion request and soft-delete the event.

    Refs #932: schreibt zusaetzlich zum DELETE-Audit (via
    :func:`soft_delete_event`) einen DELETION_APPROVED-Eintrag mit Bezug
    zum DeletionRequest. So sind die drei Workflow-Schritte (Request,
    Approve, Reject) in der Auditspur klar unterscheidbar.

    Vier-Augen-Prinzip (A3.4, Refs #1021 #1016): der Reviewer darf nicht der
    Antragsteller sein. Der Guard sitzt hier im Service (Single Source of
    Truth) — analog ``approve_client_deletion`` — und greift *vor* jedem
    DB-Schreibzugriff, statt sich auf die DB-CHECK-Constraint (IntegrityError)
    oder die View zu verlassen.

    TOCTOU-Fix (Refs #1376, I9): Zwei zeitgleiche Approve-Aufrufe fuer
    denselben (noch PENDING gelesenen) Antrag koennten sonst das Event
    doppelt verarbeiten und zwei DELETION_APPROVED-Audit-Eintraege schreiben.
    Der Antrag wird deshalb innerhalb der Transaktion per
    ``select_for_update()`` neu geladen und der Status erneut geprueft —
    analog ``core.services.case.workitems.update_workitem_status``. Der
    zweite (wartende) Aufruf sieht danach den bereits fortgeschrittenen
    Status und bricht sauber mit ``ValidationError`` ab.

    Lock order: the person of the event first, then the event, then the
    request (:func:`_lock_person_and_event_then_request`), the same order the
    anonymisation takes. An event that moved to another person while the rows
    were locked is refused with the existing binding-changed answer.
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")
    # Refs #1053: Genehmiger-Pool wird über das Recht kuratiert (SSoT hier,
    # nicht nur in der View).
    if not reviewer.can_confirm_deletion:
        raise ValidationError("Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip).")
    # Refs #2338: same facility boundary as on the reject side (Refs #2321) —
    # even more urgent here, because an approval destroys data. The guards
    # above (four-eyes principle, permission) keep their order and wording.
    #
    # Checked before the request row is locked and before any write, so
    # nothing is locked across the tenant boundary; a reviewer without a
    # facility never matches. The locking lookup below is bound to this very
    # facility, so the locked row belongs to the reviewer's facility by
    # construction.
    if reviewer.facility_id != deletion_request.facility_id:
        raise ValidationError("Antrag gehört zu einer anderen Einrichtung.")

    # Refs #1376 (I9): facility-scoped lookups keep the RLS scoping. Person,
    # event, then the request (see the helper); all locks hold until this
    # transaction commits.
    deletion_request, locked_person_id = _lock_person_and_event_then_request(deletion_request)
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend).")

    event = Event.objects.get(pk=deletion_request.target_id, facility=deletion_request.facility)
    if event.client_id != locked_person_id:
        # The event moved to another person between the unlocked read and the
        # event lock. That person is not locked, and locking it now, behind the
        # event and the request, would reverse the order.
        raise ValidationError(CLIENT_BINDING_CHANGED_MESSAGE)
    # The person row is already locked above; check the restriction before
    # entering the deletion-workflow exemption used by other write services.
    assert_processing_allowed(event.client_id, lock=False)
    # Refs #2045: Der gepruefte Art.-17-Weg bleibt waehrend einer
    # Art.-18-Einschraenkung offen — die Loeschung ist ein Betroffenenrecht.
    # Der Ausstieg ist ein benannter, modul-privater Kontext (kein Argument von
    # ``soft_delete_event``), damit er nicht zum Generalschluessel wird.
    with _deletion_workflow_write():
        soft_delete_event(event, reviewer, rows_locked=True)
    deletion_request.status = DeletionRequest.Status.APPROVED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()
    audit_event(
        action=AuditLog.Action.DELETION_APPROVED,
        user=reviewer,
        facility=deletion_request.facility,
        target_type="DeletionRequest",
        target_id=str(deletion_request.pk),
        detail={"target_event": str(deletion_request.target_id)},
    )


@transaction.atomic
def reject_deletion(deletion_request, reviewer):
    """Reject a deletion request.

    Refs #932: schreibt einen DELETION_REJECTED-Eintrag (DSGVO Art. 5(2)
    Rechenschaftspflicht ueber abgelehnte Loesch-Versuche). KEIN
    ``Action.DELETE``-Eintrag — nur Approve loescht das Event.

    Refs #2012: derselbe Lock + PENDING-Re-Check wie in
    :func:`approve_deletion` (und in ``reject_case_deletion`` /
    ``reject_client_merge``). Ohne ihn dreht ein zeitgleicher Reject einen
    bereits genehmigten Antrag auf REJECTED zurueck, waehrend das Event
    geloescht bleibt — und weil beide Schritte auditiert werden, versiegelt
    das append-only AuditLog den Widerspruch dauerhaft (DELETE, danach
    DELETION_REJECTED).

    Vier-Augen-Prinzip (A3.4, Refs #2082): der Reviewer darf nicht der
    Antragsteller sein — hier im Service als Single Source of Truth, wie in
    :func:`approve_deletion` und in den drei Reject-Pendants
    (``reject_client_deletion``, ``reject_case_deletion``,
    ``reject_client_merge``). Dieser Weg war der einzige der vier, der sich
    dafuer allein auf ``DeletionRequestReviewView`` und dahinter auf die
    CHECK-Constraint ``deletion_request_different_reviewer`` verliess: an der
    View vorbei aufgerufen brach er mitten in der Transaktion mit
    ``IntegrityError`` ab statt mit der deutschen Meldung, die jeder andere
    Reject-Weg liefert. Der Guard steht deshalb VOR der Sperre — kein
    Statuswechsel, kein Reviewer-Stempel, keine DELETION_REJECTED-Zeile.

    Wie bei :func:`approve_deletion` entscheidet außerdem ausschließlich das
    einzeln vergebbare Recht ``can_confirm_deletion`` — nicht die Rolle und
    nicht nur das View-Gate. So bleibt der Service auch für direkte Aufrufer
    die Quelle der Wahrheit.
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")
    if not reviewer.can_confirm_deletion:
        raise ValidationError("Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip).")
    # Refs #2321 (H-3): Einrichtungsgrenze auch fuer den REVIEWER. Bisher
    # scopete der Service nur auf die Einrichtung des ANTRAGS — wer das Recht
    # 'Loeschbestaetigung' hat, konnte einen fremden Antrag entscheiden.
    # Ueber HTTP unerreichbar (die View scopet), in Produktion von RLS
    # getragen; der Service ist hier wie beim Vier-Augen-Prinzip die Single
    # Source of Truth.
    #
    # Checked before the request row is locked and before any write, so
    # nothing is locked across the tenant boundary; a reviewer without a
    # facility never matches. The locking lookup below is bound to this very
    # facility, so the locked row belongs to the reviewer's facility by
    # construction.
    if reviewer.facility_id != deletion_request.facility_id:
        raise ValidationError("Antrag gehört zu einer anderen Einrichtung.")
    # Refs #2012: facility-scoped Lookup wahrt die RLS-Scopung, der Lock haelt
    # die Zeile bis zum Commit dieser Transaktion.
    deletion_request = DeletionRequest.objects.select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility
    )
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend).")
    deletion_request.status = DeletionRequest.Status.REJECTED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()
    audit_event(
        action=AuditLog.Action.DELETION_REJECTED,
        user=reviewer,
        facility=deletion_request.facility,
        target_type="DeletionRequest",
        target_id=str(deletion_request.pk),
        detail={"target_event": str(deletion_request.target_id)},
    )
