"""Service layer for client-related business logic."""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, Client
from core.models.activity import Activity
from core.models.recent_client_visit import RecentClientVisit
from core.models.workitem import DeletionRequest
from core.services._db_admin import bypass_replication_triggers
from core.services.activity import log_activity
from core.services.locking import check_version_conflict

logger = logging.getLogger(__name__)


_REDACTED_HISTORY_MARKER = {"_redacted": True, "anonymized": True}


@transaction.atomic
def anonymize_client(client, user=None):
    """DSGVO-konforme Aggregat-Anonymisierung eines Klienten (Refs #715, #743).

    Klartext-Felder werden entfernt, der Datensatz bleibt für Statistik-
    Aggregate erhalten (Events behalten ``client_id`` per ``SET_NULL``-FK).
    Anonymisierung erfasst alle Aggregat-Bestandteile, die sonst eine
    Re-Identifikation ermöglichen würden:

    - ``Client``-Felder (pseudonym, notes, age_cluster, is_active)
    - ``Case.title`` / ``Case.description`` aller verknüpften Fälle
    - ``Episode.title`` / ``Episode.description`` aller Episoden dieser Fälle
    - alle Workitems (auch DONE/DISMISSED): title, description
    - ``EventHistory.data_before``/``data_after`` für alle Events des
      Klienten — append-only-Trigger (Migration 0012) wird transaktional
      umgangen, sonst blockt das ``UPDATE``.
    - ``EventAttachment``: Disk-Files + DB-Zeilen via
      :func:`core.services.file_vault.delete_event_attachments`.
    - ``DeletionRequest.reason`` für Event-Targets dieses Klienten —
      Antrags-Meta (status/requested_by/...) bleibt für den 4-Augen-
      Audit-Trail erhalten.
    """
    from core.models.case import Case
    from core.models.episode import Episode
    from core.models.event import Event
    from core.models.event_history import EventHistory
    from core.models.workitem import DeletionRequest
    from core.services.file_vault import delete_event_attachments

    client.pseudonym = f"Gelöscht-{str(client.pk)[:8]}"
    client.notes = ""
    client.age_cluster = client.AgeCluster.UNKNOWN
    client.is_active = False
    client.save(update_fields=["pseudonym", "notes", "age_cluster", "is_active"])

    cases = Case.objects.filter(client=client)
    case_ids = list(cases.values_list("pk", flat=True))
    for case in cases:
        case.title = f"[Anonymisiert {case.created_at:%Y-%m-%d}]"
        case.description = ""
        case.save(update_fields=["title", "description"])

    Episode.objects.filter(case_id__in=case_ids).update(
        title="Episode (anonymisiert)",
        description="",
    )

    client.work_items.all().update(
        title="Aufgabe (anonymisiert)",
        description="",
    )

    event_ids = list(Event.objects.filter(client=client).values_list("pk", flat=True))
    if event_ids:
        for event in Event.objects.filter(pk__in=event_ids).iterator():
            delete_event_attachments(event)

        history_qs = EventHistory.objects.filter(event_id__in=event_ids)
        if history_qs.exists():
            with bypass_replication_triggers():
                history_qs.update(
                    data_before=_REDACTED_HISTORY_MARKER,
                    data_after=_REDACTED_HISTORY_MARKER,
                )

        DeletionRequest.objects.filter(
            target_type="Event",
            target_id__in=event_ids,
        ).update(reason="[Anonymisiert]")

    logger.info(
        "client_anonymized",
        extra={"client_id": str(client.pk), "user_id": getattr(user, "pk", None)},
    )


@transaction.atomic
def create_client(facility, user, **data):
    """Create a client with activity and audit logging."""
    client = Client(facility=facility, created_by=user, **data)
    client.save()
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=client,
        summary=f"Klientel {client.pseudonym} angelegt",
    )
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.CLIENT_CREATE,
        target_type="Client",
        target_id=str(client.pk),
    )
    return client


def update_client_stage(client, old_stage, new_stage, facility, user):
    """Log a stage change if the contact stage has changed."""
    if old_stage == new_stage:
        return
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.STAGE_CHANGE,
        target_type="Client",
        target_id=str(client.pk),
        detail={
            "old_stage": old_stage,
            "new_stage": new_stage,
            "client_pseudonym": client.pseudonym,
        },
    )


@transaction.atomic
def update_client(client, user, *, old_stage=None, expected_updated_at=None, **fields):
    """Update a client with activity logging and stage-change auditing.

    Accepts a dict of allowed fields (pseudonym, contact_stage, age_cluster, notes).
    Handles AuditLog for stage changes and Activity for qualification transitions.

    old_stage: If provided, used as the previous contact_stage for stage-change
    detection.  When called from a ModelForm view, the form's _post_clean may
    already have mutated the instance, so the caller should capture the stage
    before form.is_valid().

    expected_updated_at: Optional optimistic-locking guard (Refs #531). When set,
    the DB-side ``updated_at`` must match — otherwise a ``ValidationError`` is
    raised so the caller can surface a conflict message.
    """
    check_version_conflict(client, expected_updated_at)
    if old_stage is None:
        old_stage = client.contact_stage

    # Allowlist (Refs #734): Verhindert Mass-Assignment durch versehentlich
    # durchgereichte Felder (z.B. ``facility``, ``id``, ``created_at``). Liste
    # spiegelt die Felder von ``ClientForm`` wider.
    allowed = {"pseudonym", "contact_stage", "age_cluster", "notes"}
    # Diff erfassen — nur Feldnamen, keine PII-Werte selbst
    changed_fields = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")
        if getattr(client, key) != value:
            changed_fields.append(key)
        setattr(client, key, value)
    client.save()

    new_stage = client.contact_stage
    update_client_stage(client, old_stage, new_stage, client.facility, user)

    if old_stage != new_stage and new_stage == Client.ContactStage.QUALIFIED:
        log_activity(
            facility=client.facility,
            actor=user,
            verb=Activity.Verb.QUALIFIED,
            target=client,
            summary=f"{client.pseudonym} qualifiziert",
        )

    if changed_fields:
        AuditLog.objects.create(
            facility=client.facility,
            user=user,
            action=AuditLog.Action.CLIENT_UPDATE,
            target_type="Client",
            target_id=str(client.pk),
            detail={"changed_fields": changed_fields},
        )

    log_activity(
        facility=client.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=client,
        summary=f"Klientel {client.pseudonym} aktualisiert",
    )

    return client


def get_client_or_none(facility, client_id):
    """Lookup a client by PK scoped to *facility*.

    Returns None if the client doesn't exist, the ID isn't a valid UUID, or
    the client belongs to a different facility. Gedacht für View-GET-Handler,
    die einen vorselektierten Client aus der URL/Query laden wollen, ohne bei
    fehlendem/ungültigem Parameter eine 404 zu werfen — stattdessen wird das
    Formular unverändert angezeigt.

    Refs #598 Finding R-5.
    """
    if not client_id:
        return None
    try:
        return Client.objects.get(pk=client_id, facility=facility)
    except (Client.DoesNotExist, ValueError, ValidationError):
        # Django wirft bei ungültigen UUID-Strings ValidationError (nicht
        # ValueError) — beide Fälle werden als "not found" behandelt.
        return None


def track_client_visit(user, client, facility):
    """Record that a user visited a client detail page.

    Upserts a RecentClientVisit row and prunes old entries (max 20 per user).
    """
    RecentClientVisit.objects.update_or_create(
        user=user,
        client=client,
        defaults={"facility": facility},
    )
    # Prune: keep only the 20 most recent visits. Subquery-Variante spart
    # einen Roundtrip gegenüber "erst SELECT LIMIT 20 holen, dann DELETE"
    # — Refs #642.
    keep_qs = RecentClientVisit.objects.filter(user=user).order_by("-visited_at").values("pk")[:20]
    RecentClientVisit.objects.filter(user=user).exclude(pk__in=keep_qs).delete()


# --- Vier-Augen-Lösch-Workflow für Personen (Refs #626) ---


def request_client_deletion(client, user, reason):
    """Stellt einen Vier-Augen-Löschantrag für eine Person.

    Idempotent: wenn bereits ein PENDING-Antrag existiert, wird dieser
    zurückgegeben (analog zu Event-Pfad in services/event.request_deletion).

    Aufruf: Fachkraft+ aus dem UI. Genehmigung erfolgt durch Leitung
    via :func:`approve_client_deletion`.
    """
    existing = DeletionRequest.objects.filter(
        facility=client.facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=client.pk,
        status=DeletionRequest.Status.PENDING,
    ).first()
    if existing is not None:
        return existing
    return DeletionRequest.objects.create(
        facility=client.facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=client.pk,
        reason=reason,
        requested_by=user,
    )


@transaction.atomic
def approve_client_deletion(deletion_request, reviewer):
    """Genehmigt einen Client-Löschantrag und führt den Soft-Delete aus.

    Setzt ``is_deleted=True`` (Mixin-Methode), schreibt AuditLog und
    schließt den DeletionRequest. Nach Ablauf der Papierkorb-Frist
    (``Settings.client_trash_days``) anonymisiert ``enforce_retention``
    den Datensatz automatisch.
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")

    client = Client.objects.get(pk=deletion_request.target_id, facility=deletion_request.facility)
    client.soft_delete(user=reviewer)

    deletion_request.status = DeletionRequest.Status.APPROVED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()

    AuditLog.objects.create(
        facility=client.facility,
        user=reviewer,
        action=AuditLog.Action.CLIENT_SOFT_DELETED,
        target_type="Client",
        target_id=str(client.pk),
        detail={
            "pseudonym": client.pseudonym,
            "requested_by": deletion_request.requested_by.username,
            "reason": deletion_request.reason,
        },
    )
    log_activity(
        facility=client.facility,
        actor=reviewer,
        verb=Activity.Verb.DELETED,
        target=client,
        summary=f"Person {client.pseudonym} in den Papierkorb verschoben",
    )
    return client


@transaction.atomic
def reject_client_deletion(deletion_request, reviewer):
    """Lehnt einen Client-Löschantrag ab — Status→REJECTED, kein Soft-Delete."""
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")
    deletion_request.status = DeletionRequest.Status.REJECTED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()


@transaction.atomic
def restore_client(client, user):
    """Holt eine soft-deletete Person aus dem Papierkorb zurück."""
    if not client.is_deleted:
        raise ValidationError("Person ist nicht im Papierkorb.")
    client.restore()
    AuditLog.objects.create(
        facility=client.facility,
        user=user,
        action=AuditLog.Action.CLIENT_RESTORED,
        target_type="Client",
        target_id=str(client.pk),
        detail={"pseudonym": client.pseudonym},
    )


def anonymize_eligible_soft_deleted_clients(facility, settings_obj, *, dry_run=False):
    """Anonymisiert soft-deletete Personen, deren Papierkorb-Frist abgelaufen ist.

    Aufruf aus enforce_retention. Findet alle Clients in dieser Facility
    mit ``is_deleted=True`` und ``deleted_at`` älter als
    ``settings_obj.client_trash_days``, ruft :func:`anonymize_client` auf.

    Returns: Anzahl anonymisierter Datensätze.
    """
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(days=settings_obj.client_trash_days)
    qs = Client.objects.filter(
        facility=facility,
        is_deleted=True,
        deleted_at__lt=cutoff,
    )
    if dry_run:
        return qs.count()

    count = 0
    for client in qs:
        anonymize_client(client, user=None)
        AuditLog.objects.create(
            facility=facility,
            action=AuditLog.Action.CLIENT_ANONYMIZED,
            target_type="Client",
            target_id=str(client.pk),
            detail={
                "trigger": "trash_days_expired",
                "deleted_at": str(client.deleted_at),
            },
        )
        count += 1
    return count
