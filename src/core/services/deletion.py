"""Gemeinsame Helfer des Vier-Augen-Löschworkflows (Refs #1053, #1736).

Der Genehmiger-Pool wird über das Recht ``can_confirm_deletion`` kuratiert und
nicht aus der Rolle abgeleitet. Er wird an zwei Stellen gebraucht — im
Setup-Guard der Löschanträge-Liste und auf der Antragsseite, die vor dem
Absenden zeigt, wer den Antrag überhaupt bestätigen könnte. Damit beide
Flächen dieselbe Menge meinen, liegt die Abfrage hier statt zweimal in den
Views.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, Case, Client, DeletionRequest, Event, User
from core.services.audit import audit_event
from core.services.targets import TARGET_CASE, TARGET_CLIENT, TARGET_EVENT

#: Meldung des Rennens „inzwischen entschieden" — die View macht daraus eine
#: ``messages.error``-Zeile, statt sie als 404 zu verschlucken.
WITHDRAW_NOT_PENDING_MESSAGE = _("Dieser Löschantrag ist bereits entschieden und kann nicht mehr zurückgezogen werden.")

#: Der Sonderfall „schon zurückgezogen" bekommt eine eigene Meldung: geht die
#: Erfolgsantwort verloren und der Browser wiederholt den POST, hat NIEMAND
#: entschieden — sie selbst hat zurückgezogen, und genau das ist eingetreten.
#: „bereits entschieden" wäre dort sachlich falsch und beunruhigend.
WITHDRAW_ALREADY_WITHDRAWN_MESSAGE = _("Dieser Löschantrag wurde bereits zurückgezogen.")


def withdraw_blocked_message(status):
    """Die passende Meldung für einen nicht (mehr) zurückziehbaren Antrag."""
    if status == DeletionRequest.Status.WITHDRAWN:
        return WITHDRAW_ALREADY_WITHDRAWN_MESSAGE
    return WITHDRAW_NOT_PENDING_MESSAGE


#: Der ``detail``-Schluessel je Zieltyp — exakt die Schluessel, die der
#: ``DELETION_REQUESTED``-Schreiber des jeweiligen Pfades benutzt
#: (``services/events/deletion.py``, ``services/client/main.py``,
#: ``services/case/deletion.py``). Nur die Ziel-UUID, nie Pseudonym, Titel oder
#: Begruendung — die stehen am ``DeletionRequest`` selbst (Refs #1093).
_WITHDRAW_DETAIL_KEY = {
    DeletionRequest.TargetType.EVENT: "target_event",
    DeletionRequest.TargetType.CLIENT: "target_client",
    DeletionRequest.TargetType.CASE: "target_case",
}


@transaction.atomic
def withdraw_deletion_request(deletion_request, user):
    """Zieht den EIGENEN, noch offenen Löschantrag zurück (Refs #2040).

    Vorher war ein versehentlich gestellter Antrag eine Sackgasse: die
    Antragstellerin sah ihn (Refs #1736 B), konnte ihn aber nicht selbst
    beenden — er lief zwingend über den Genehmiger-Pool.

    Zwei Invarianten, die den Zuschnitt erklären:

    * **``reviewed_by`` bleibt NULL.** Der CHECK-Constraint
      ``deletion_request_different_reviewer`` verbietet
      ``reviewed_by == requested_by``; ein Rückzug ist ohnehin keine
      Vier-Augen-Entscheidung. ``reviewed_at`` trägt den Zeitpunkt (im UI als
      „zurückgezogen am" gelabelt) — kein zusätzliches Feld.
    * **``WITHDRAWN`` ist ein Endstatus, kein Löschen der Zeile.** Der
      Vorgang bleibt nach Art. 5 Abs. 2 DSGVO nachweisbar; der partielle
      Unique-Index ``unique_pending_deletion_request`` greift bei WITHDRAWN
      nicht mehr, ein neuer Antrag auf dasselbe Ziel ist also wieder möglich.

    Sperr-Ordnung: es wird ausschließlich die ``DeletionRequest``-Zeile
    gesperrt (``select_for_update`` + Status-Recheck, Muster
    :func:`core.services.events.deletion.reject_deletion`) — keine neue
    Kombination gegenüber den Bestandspfaden, und der ``AuditLog``-Schreibzugriff
    steht als letzter Schritt der Transaktion (Schreibreihenfolge aus
    ``services/system/_db_admin.py``).

    Wirft ``ValidationError``, wenn der Antrag nicht (mehr) der eigene oder
    nicht mehr ``PENDING`` ist. In beiden Fällen entsteht **kein**
    Audit-Eintrag — zurückgezogen wurde ja nichts.

    A person outside the facility of the request, or without any facility
    (fail-closed), is refused before the request row is locked — even the
    requester herself after a move to another facility. The facility of a
    request never changes, and the locking lookup below is bound to that very
    facility, so the locked row belongs to the person's facility by
    construction.
    """
    actor_facility_id = getattr(user, "facility_id", None)
    if actor_facility_id is None or actor_facility_id != deletion_request.facility_id:
        raise ValidationError(_("Antrag gehört zu einer anderen Einrichtung."))
    # Facility-scoped Lookup wahrt die RLS-Scopung, der Lock haelt die Zeile
    # bis zum Commit dieser Transaktion.
    try:
        dr = DeletionRequest.objects.select_for_update().get(
            pk=deletion_request.pk, facility_id=deletion_request.facility_id
        )
    except DeletionRequest.DoesNotExist as exc:
        raise ValidationError(WITHDRAW_NOT_PENDING_MESSAGE) from exc
    if dr.requested_by_id is None or dr.requested_by_id != user.pk:
        raise ValidationError(_("Nur die antragstellende Person kann einen Löschantrag zurückziehen."))
    if dr.status != DeletionRequest.Status.PENDING:
        raise ValidationError(withdraw_blocked_message(dr.status))

    dr.status = DeletionRequest.Status.WITHDRAWN
    dr.reviewed_at = timezone.now()
    dr.save(update_fields=["status", "reviewed_at"])
    detail = {_WITHDRAW_DETAIL_KEY[dr.target_type]: str(dr.target_id)}
    if dr.merge_into_id is not None:
        # Merge-Antraege (Refs #1876) nennen im ``DELETION_REQUESTED``-Eintrag
        # beide Seiten (``services/client/merge.py``). Ohne ``merge_into``
        # sagte die Rueckzugszeile nicht, WELCHE Zusammenfuehrung beendet
        # wurde — beide Werte sind Client-UUIDs, also PII-frei.
        detail["merge_into"] = str(dr.merge_into_id)
    audit_event(
        action=AuditLog.Action.DELETION_WITHDRAWN,
        user=user,
        facility=dr.facility,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
        detail=detail,
    )
    return dr


def eligible_deletion_confirmers(facility, *, exclude_user=None):
    """Aktive Träger:innen des Rechts „Löschbestätigung" einer Einrichtung.

    ``exclude_user`` blendet die antragstellende Person aus — sie darf ihren
    eigenen Antrag nicht genehmigen (Vier-Augen-Prinzip, hart durchgesetzt in
    ``approve_client_deletion``/``approve_deletion`` und per DB-Constraint).
    Ist die zurückgegebene Menge leer, gibt es für einen Antrag dieser Person
    keinen möglichen Genehmiger: der Antrag würde still liegenbleiben.
    """
    qs = User.objects.filter(facility=facility, is_active=True, can_confirm_deletion=True)
    if exclude_user is not None and exclude_user.pk is not None:
        qs = qs.exclude(pk=exclude_user.pk)
    return qs.order_by("display_name", "username")


def annotate_deletion_targets(requests, facility, user) -> None:
    """Setzt ``target_label`` und ``target_url`` je Löschantrag (Refs #1736 E).

    Die Liste zeigte als Ziel nur ``target_type`` — „Client"/„Event" ohne
    Pseudonym oder Titel; wofür man da entscheidet, war nicht erkennbar. Die
    Auflösung passiert gebündelt (zwei Queries für die ganze Liste, nicht eine
    je Zeile) und **fail-closed**:

    * Ereignisse laufen durch ``Event.objects.visible_to(user)`` — die
      Sensibilitäts-Policy gilt hier wie überall sonst; ein für die Rolle
      unsichtbares Ereignis behält ein neutrales Label und bekommt keinen
      Link (sonst leckte der Dokumentationstyp-Name genau die Metadaten, die
      die Policy verbirgt).
    * Ein soft-gelöschtes oder nicht mehr auffindbares Ziel bekommt ebenfalls
      keinen Link — die Detailansicht antwortet dort mit 404.
    * Fall-Ziele (Refs #1818) tragen ihren Titel als Label — aber nur für
      Rollen, die Fälle überhaupt sehen dürfen. ``can_confirm_deletion`` ist
      rollenunabhängig (#1053): eine Assistenz kann es tragen, und für sie ist
      die Fall-Domäne sonst durchgängig geschlossen (``case_detail`` und der
      Fall-Anhang-Download sind STAFF_PLUS, ADR-039 §12). Ohne Gate bekäme sie
      hier den Freitext-Titel im Klartext und dazu einen
      ``core:case_detail``-Link, den ihre Rolle nicht öffnen darf. Für sie
      bleibt das Label deshalb generisch — dieselbe fail-closed-Regel wie beim
      unsichtbaren Ereignis. Ins ``AuditLog`` gehört der Titel weiterhin nicht
      (#1751).

    ``requests`` wird in-place annotiert (Listen aus der View).
    """
    requests = list(requests)
    if not requests:
        return

    client_ids = [r.target_id for r in requests if r.target_type == TARGET_CLIENT]
    # Merge-Antraege (Refs #1876): das Ziel der Zusammenfuehrung haengt an
    # derselben Bulk-Query — die Liste soll zeigen, WOHIN aufgeloest wuerde.
    client_ids += [r.merge_into_id for r in requests if r.merge_into_id is not None]
    event_ids = [r.target_id for r in requests if r.target_type == TARGET_EVENT]
    case_ids = [r.target_id for r in requests if r.target_type == TARGET_CASE]

    clients = (
        {c.pk: c for c in Client.objects.for_facility(facility).filter(pk__in=client_ids)}
        if client_ids and user is not None and user.is_assistant_or_above
        else {}
    )
    # Fail-closed: ohne Fall-Sichtbarkeit bleibt die Menge leer, der Zweig
    # unten faellt dann in denselben generischen Ast wie ein nicht mehr
    # auffindbarer Fall (Label ohne Link).
    cases = (
        {
            c.pk: c
            for c in Case.objects.for_facility(facility).filter(
                pk__in=case_ids, client__is_deleted=False, client__is_active=True
            )
        }
        if case_ids and user is not None and user.is_staff_or_above
        else {}
    )
    events = (
        {
            e.pk: e
            for e in Event.objects.visible_to(user)
            .filter(facility=facility, pk__in=event_ids)
            .select_related("document_type")
        }
        if event_ids and user is not None and user.is_assistant_or_above
        else {}
    )

    for dr in requests:
        # Merge-Ziel (Refs #1876): Label + Link fuer „→ <Ziel>" in der Liste;
        # gleiche fail-closed-Regeln wie das Antragsziel selbst.
        dr.merge_target_label = ""
        dr.merge_target_url = ""
        if dr.merge_into_id is not None:
            merge_target = clients.get(dr.merge_into_id)
            if merge_target is None:
                dr.merge_target_label = _("Person (nicht mehr auffindbar)")
            else:
                dr.merge_target_label = merge_target.pseudonym
                dr.merge_target_url = (
                    "" if merge_target.is_deleted else reverse("core:client_detail", args=[merge_target.pk])
                )
        if dr.target_type == TARGET_CLIENT:
            target = clients.get(dr.target_id)
            if target is None:
                dr.target_label = _("Person (nicht mehr auffindbar)")
                dr.target_url = ""
                continue
            dr.target_label = target.pseudonym
            dr.target_url = "" if target.is_deleted else reverse("core:client_detail", args=[target.pk])
            continue

        if dr.target_type == TARGET_CASE:
            target = cases.get(dr.target_id)
            if target is None:
                # Unsichtbar (Rollen-Gate) oder nicht mehr vorhanden — beides
                # bleibt bewusst ununterscheidbar generisch, Muster
                # Ereignis-Zweig.
                dr.target_label = _("Fall")
                dr.target_url = ""
                continue
            dr.target_label = target.title
            dr.target_url = "" if target.is_deleted else reverse("core:case_detail", args=[target.pk])
            continue

        target = events.get(dr.target_id)
        if target is None:
            # Unsichtbar (Sensibilität) oder nicht mehr vorhanden — beides
            # bleibt bewusst ununterscheidbar generisch.
            dr.target_label = _("Ereignis")
            dr.target_url = ""
            continue
        occurred = date_format(timezone.localtime(target.occurred_at), "DATE_FORMAT")
        dr.target_label = f"{target.document_type.name} — {occurred}"
        dr.target_url = "" if target.is_deleted else reverse("core:event_detail", args=[target.pk])
