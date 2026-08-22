"""LegalHold-Lifecycle (#744).

Aus ``services/retention.py`` ausgekoppelt — eigenes Thema (Sperre
gegen Loeschung; orthogonal zu Vorschlaegen und Enforcement).
"""

from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, Case, Client, LegalHold, RetentionProposal
from core.services.audit import audit_retention_decision


def create_hold_for_target(facility, *, target_type, target_id, user, reason, expires_at=None):
    """Legt einen Legal Hold OHNE Retention-Vorschlag an (Refs #1818, ADR-044 §5).

    ``LegalHold.target_type`` ist ein freies ``CharField`` ohne ``choices`` —
    ``"Case"`` war also immer speicherbar. Was fehlte, war der **Erzeugungsweg**:
    :func:`create_legal_hold` setzt einen ``RetentionProposal`` voraus, und
    ``RetentionProposal.TargetType`` kennt nur ``EVENT``. Ein Fall-Hold war damit
    ausschliesslich per ORM-Konsole anlegbar.

    Bewusst NICHT geloest, indem ``RetentionProposal`` einen ``CASE``-Zweig
    bekommt: ein Vorschlag traegt ``deletion_due_at`` und
    ``retention_category`` und entsteht aus den Fristen des
    Ereignis-Enforcements (:mod:`core.retention.enforcement`). Fuer Faelle gibt
    es keine solche Frist — ihr Loeschweg ist der Vier-Augen-Antrag plus die
    Papierkorb-Frist. Ein Pseudo-Vorschlag nur als Traeger des Holds haette ein
    zweites, leerlaufendes Vorschlagsobjekt in die Retention-Ansicht gespuelt.

    Der Vorschlags-gebundene Weg bleibt :func:`create_legal_hold` und setzt
    zusaetzlich den Vorschlag auf ``HELD``; beide schreiben dieselbe
    ``LEGAL_HOLD``-Audit-Zeile.
    """
    hold = LegalHold.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        expires_at=expires_at,
        created_by=user,
    )
    audit_retention_decision(
        facility,
        target_type=target_type,
        target_id=target_id,
        action=AuditLog.Action.LEGAL_HOLD,
        category="legal_hold_created",
        user=user,
        reason=reason,
        expires_at=str(expires_at) if expires_at else None,
        hold_id=str(hold.pk),
    )
    return hold


def create_legal_hold(proposal, user, reason, expires_at=None):
    """Create a legal hold and set the proposal to held status."""
    hold = create_hold_for_target(
        proposal.facility,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        user=user,
        reason=reason,
        expires_at=expires_at,
    )
    proposal.status = RetentionProposal.Status.HELD
    proposal.save(update_fields=["status"])
    return hold


@transaction.atomic
def dismiss_legal_hold(hold, user):
    """Dismiss a legal hold and revert the proposal to pending."""
    hold.dismissed_at = timezone.now()
    hold.dismissed_by = user
    hold.save(update_fields=["dismissed_at", "dismissed_by"])

    # Revert associated proposal to pending
    RetentionProposal.objects.filter(
        facility=hold.facility,
        target_type=hold.target_type,
        target_id=hold.target_id,
        status=RetentionProposal.Status.HELD,
    ).update(status=RetentionProposal.Status.PENDING)

    audit_retention_decision(
        hold.facility,
        target_type=hold.target_type,
        target_id=hold.target_id,
        action=AuditLog.Action.LEGAL_HOLD,
        category="legal_hold_dismissed",
        user=user,
        hold_id=str(hold.pk),
        reason=hold.reason,
    )


def has_active_hold(facility, target_type, target_id):
    """Check if a target has an active (not dismissed, not expired) legal hold.

    Uses ``timezone.localdate()`` (Europe/Berlin) rather than the naive,
    server-local ``date.today()`` so the active/expired boundary stays in
    lockstep with ``LegalHold.is_active`` and the dashboard SQL filter near
    midnight (UTC vs. Berlin); see #1191 (Refs #1192).
    """
    return (
        LegalHold.objects.filter(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            dismissed_at__isnull=True,
        )
        .exclude(
            expires_at__lt=timezone.localdate(),
        )
        .exists()
    )


def get_active_hold_target_ids(facility, target_type="Event"):
    """Return set of target_ids with active legal holds for a facility.

    Uses ``timezone.localdate()`` (Europe/Berlin) for the active/expired
    boundary, in lockstep with ``has_active_hold`` and the dashboard filter;
    see #1191 (Refs #1192).
    """
    qs = LegalHold.objects.filter(
        facility=facility,
        target_type=target_type,
        dismissed_at__isnull=True,
    ).exclude(
        expires_at__lt=timezone.localdate(),
    )
    return set(qs.values_list("target_id", flat=True))


def exclude_clients_with_active_holds(queryset, facility):
    """Nimmt Klienten mit aktivem Legal Hold aus *queryset* (Refs #1066, #1756).

    Ausgeschlossen wird, wer selbst unter einem Hold steht
    (``target_type="Client"``) **oder** mindestens ein Event unter Hold hat
    (``target_type="Event"``) — sonst risse die Loeschung des Eltern-Klienten
    den gehaltenen Event mit (Spoliation). Nicht aufgehobene, nicht
    abgelaufene Holds zaehlen; die Aktiv-Grenze zieht
    :func:`get_active_hold_target_ids` (``timezone.localdate``).

    Die Anonymisierung wird damit nur **aufgeschoben**: ein spaeterer
    Retention-Lauf holt sie nach, sobald der Hold aufgehoben oder abgelaufen
    ist. Die Zeile bleibt bis dahin unveraendert — sie zaehlt insbesondere
    weiter zu ihrer k-Anonymitaets-Aequivalenzklasse, weil sie mit echten
    Quasi-Identifikatoren real existiert (siehe ``count_clients_in_bucket``).

    Gemeinsamer Helfer statt Kopie: der Trash-Expiry-Pfad
    (:func:`core.services.client.anonymize_eligible_soft_deleted_clients`) und
    die K-Anon-Bridge (:func:`core.retention.anonymization.anonymize_clients`)
    muessen exakt dieselbe Menge ausnehmen — #1756 war genau die Drift
    zwischen beiden.
    """
    return queryset.exclude(pk__in=get_active_hold_target_ids(facility, "Client")).exclude(
        events__pk__in=get_active_hold_target_ids(facility, "Event")
    )


def exclude_cases_with_active_holds(queryset, facility):
    """Nimmt Faelle mit aktivem Legal Hold aus *queryset* (Refs #1818, P4).

    Gegenstueck zu :func:`exclude_clients_with_active_holds`, mit **drei**
    Ausschluss-Zweigen statt zwei:

    * der Fall selbst steht unter Hold (``target_type="Case"`` — seit ADR-044
      zulaessig),
    * mindestens ein **Ereignis des Falls** steht unter Hold. Die Tilgung
      loest die Zuordnung (P5); welcher Fall ein gehaltenes Ereignis
      einrahmte, ist selbst Beweismittel — der Hold darf sich nicht dadurch
      aushebeln lassen, dass man den Rahmen entfernt (Spoliation, Muster
      #1066),
    * die **Person** des Falls steht unter Hold. Ohne diesen Zweig waere der
      Fall-Loeschweg die Hintertuer um genau die Sperre, die
      ``exclude_clients_with_active_holds`` zieht: die Personen-Anonymisierung
      redigiert dieselben Fall-Inhalte (``_redact_cases_and_episodes``) und ist
      bei einem Personen-Hold blockiert. ADR-039 §6 sagt dasselbe von der
      Datei-Seite her („der Client-Legal-Hold schuetzt transitiv").

    Bewusst NICHT ueber :func:`client_has_active_hold` gebaut: der Helfer
    schliesst zusaetzlich Personen aus, von denen **irgendein** Ereignis unter
    Hold steht — auch eines, das mit diesem Fall nichts zu tun hat. Fuer die
    Personen-Anonymisierung ist das richtig (sie fasst alle Ereignisse an), fuer
    die Fall-Redaktion waere es Ueberdeckung: sie laesst Ereignisse unberuehrt.

    Wie beim Personen-Pfad wird nur **aufgeschoben** — ein spaeterer
    Retention-Lauf holt die Tilgung nach, sobald der Hold aufgehoben oder
    abgelaufen ist.
    """
    return (
        queryset.exclude(pk__in=get_active_hold_target_ids(facility, "Case"))
        .exclude(events__pk__in=get_active_hold_target_ids(facility, "Event"))
        .exclude(client_id__in=get_active_hold_target_ids(facility, "Client"))
    )


def case_has_active_hold(case):
    """Einzel-Pruefung derselben Regel wie :func:`exclude_cases_with_active_holds`.

    Fuer die manuelle Sofort-Tilgung
    (:func:`core.services.case.deletion.anonymize_case_now`), die den Ausschluss
    als Meldung statt als Mengen-Filter braucht. Bewusst **auf** dem
    Mengen-Filter aufgesetzt statt daneben nachgebaut — dieselbe Lehre wie
    #1756 beim Personen-Pfad.
    """
    return not exclude_cases_with_active_holds(
        Case.objects.filter(pk=case.pk),
        case.facility,
    ).exists()


def owner_has_active_hold(owner):
    """Aktiver Legal Hold, der die Anhaenge eines Owners schuetzt (Refs #1874).

    Gebraucht vom **physischen** Anhang-Loeschpfad
    (:func:`core.services.attachments.crud.purge_attachment_chain`). Der
    bisherige UI-Entfernen-Pfad fragte gar nicht nach Holds — er brauchte es
    auch nicht: ``soft_delete_attachment_chain`` setzt nur ``deleted_at``, die
    verschluesselte Datei und die Zeile bleiben bitgleich erhalten. Ein Legal
    Hold sichert Beweismittel; genau das leistet ein Soft-Delete noch. Ein
    Purge dagegen macht die Datei unwiederbringlich — er ist der erste
    Anhang-Pfad, an dem ein Hold ueberhaupt etwas zu verhindern hat.

    ``LegalHold.target_type`` kennt nur ``Event``/``Client``/``Activity``; ein
    Anhang ist nie selbst Hold-Ziel. Die Sperre wird deshalb ueber den Owner
    aufgeloest:

    * **Ereignis** — eigener Event-Hold ODER (ueber die Person) der
      Personen-Zweig unten,
    * **Person** — der Personen-Zweig,
    * **Fall** — eigener Case-Hold ODER der Personen-Zweig ueber ``case.client``.
      Bis #1818 gab es nur die zweite Haelfte, weil ``Case`` kein Hold-Ziel sein
      konnte; seit ADR-044 kann er es (P4), und ein Hold auf genau diesem Fall
      muss dann auch seine Anhaenge schuetzen — sonst waere der Purge-Weg die
      Luecke im frisch eingefuehrten Hold. Der Personen-Zweig bleibt bewusst
      **zusaetzlich** stehen (rein additive Erweiterung): er sperrt schon heute
      breiter, naemlich auch bei einem gehaltenen Ereignis derselben Person.

    Der Personen-Zweig ist bewusst :func:`client_has_active_hold` und kein
    Eigenbau: dieselbe Menge, die schon Anonymisierung und Trash-Ablauf
    zurueckhaelt (Refs #1756) — inklusive „mindestens ein Ereignis der Person
    steht unter Hold". Ein personen-eigener Anhang mit gehaltenem Ereignis
    waere sonst loeschbar, waehrend die Anonymisierung derselben Person
    blockiert ist.

    Ein personenloses Ereignis (``client`` ist ``NULL``) hat keinen
    Personen-Zweig — dort zaehlt nur der eigene Event-Hold.
    """
    model_name = owner._meta.model_name
    if model_name == "event":
        if has_active_hold(owner.facility, "Event", owner.pk):
            return True
        client = owner.client
    elif model_name == "client":
        client = owner
    elif model_name == "case":
        if has_active_hold(owner.facility, "Case", owner.pk):
            return True
        client = owner.client
    else:
        raise ValueError(f"Kein zulaessiger Attachment-Owner: {owner._meta.label}")
    return client is not None and client_has_active_hold(client)


def client_has_active_hold(client):
    """Einzel-Pruefung derselben Regel wie :func:`exclude_clients_with_active_holds`.

    Fuer die manuelle Sofort-Anonymisierung
    (:func:`core.services.client.anonymize_client_now`), die den Ausschluss als
    Meldung statt als Mengen-Filter braucht. Bewusst **auf** dem Mengen-Filter
    aufgesetzt statt daneben nachgebaut: die drei Anonymisierungs-Einstiege
    (Trash-Ablauf, Retention-Bridge, manuelle Aktion) duerfen nicht erneut
    auseinanderlaufen (Refs #1756).
    """
    return not exclude_clients_with_active_holds(
        Client.objects.filter(pk=client.pk),
        client.facility,
    ).exists()
