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
from core.services.client.restriction import _deletion_workflow_write
from core.services.events.crud import soft_delete_event


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
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")
    # Refs #1053: Genehmiger-Pool wird über das Recht kuratiert (SSoT hier,
    # nicht nur in der View).
    if not reviewer.can_confirm_deletion:
        raise ValidationError("Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip).")

    # Refs #1376 (I9): facility-scoped Lookup wahrt die RLS-Scopung, der Lock
    # haelt die Zeile bis zum Commit dieser Transaktion.
    deletion_request = DeletionRequest.objects.select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility
    )
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend).")

    event = Event.objects.get(pk=deletion_request.target_id, facility=deletion_request.facility)
    # Refs #2045: Der gepruefte Art.-17-Weg bleibt waehrend einer
    # Art.-18-Einschraenkung offen — die Loeschung ist ein Betroffenenrecht.
    # Der Ausstieg ist ein benannter, modul-privater Kontext (kein Argument von
    # ``soft_delete_event``), damit er nicht zum Generalschluessel wird.
    with _deletion_workflow_write():
        soft_delete_event(event, reviewer)
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
    """
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
