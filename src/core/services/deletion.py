"""Gemeinsame Helfer des Vier-Augen-Löschworkflows (Refs #1053, #1736).

Der Genehmiger-Pool wird über das Recht ``can_confirm_deletion`` kuratiert und
nicht aus der Rolle abgeleitet. Er wird an zwei Stellen gebraucht — im
Setup-Guard der Löschanträge-Liste und auf der Antragsseite, die vor dem
Absenden zeigt, wer den Antrag überhaupt bestätigen könnte. Damit beide
Flächen dieselbe Menge meinen, liegt die Abfrage hier statt zweimal in den
Views.
"""

from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from core.models import Case, Client, DeletionRequest, Event, User


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

    client_ids = [r.target_id for r in requests if r.target_type == DeletionRequest.TargetType.CLIENT]
    # Merge-Antraege (Refs #1876): das Ziel der Zusammenfuehrung haengt an
    # derselben Bulk-Query — die Liste soll zeigen, WOHIN aufgeloest wuerde.
    client_ids += [r.merge_into_id for r in requests if r.merge_into_id is not None]
    event_ids = [r.target_id for r in requests if r.target_type == DeletionRequest.TargetType.EVENT]
    case_ids = [r.target_id for r in requests if r.target_type == DeletionRequest.TargetType.CASE]

    clients = {c.pk: c for c in Client.objects.for_facility(facility).filter(pk__in=client_ids)} if client_ids else {}
    # Fail-closed: ohne Fall-Sichtbarkeit bleibt die Menge leer, der Zweig
    # unten faellt dann in denselben generischen Ast wie ein nicht mehr
    # auffindbarer Fall (Label ohne Link).
    cases = (
        {c.pk: c for c in Case.objects.for_facility(facility).filter(pk__in=case_ids)}
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
        if event_ids
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
        if dr.target_type == DeletionRequest.TargetType.CLIENT:
            target = clients.get(dr.target_id)
            if target is None:
                dr.target_label = _("Person (nicht mehr auffindbar)")
                dr.target_url = ""
                continue
            dr.target_label = target.pseudonym
            dr.target_url = "" if target.is_deleted else reverse("core:client_detail", args=[target.pk])
            continue

        if dr.target_type == DeletionRequest.TargetType.CASE:
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
