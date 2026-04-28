"""Service layer for client-related business logic."""

import logging

from django.db import transaction

from core.models import AuditLog, Client
from core.models.activity import Activity
from core.models.recent_client_visit import RecentClientVisit
from core.services.activity import log_activity

logger = logging.getLogger(__name__)


@transaction.atomic
def create_client(facility, user, **data):
    """Create a client with activity logging."""
    client = Client(facility=facility, created_by=user, **data)
    client.save()
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=client,
        summary=f"Klientel {client.pseudonym} angelegt",
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
def update_client(client, user, *, old_stage=None, **fields):
    """Update a client with activity logging and stage-change auditing.

    Accepts a dict of allowed fields (pseudonym, contact_stage, age_cluster, notes).
    Handles AuditLog for stage changes and Activity for qualification transitions.

    old_stage: If provided, used as the previous contact_stage for stage-change
    detection.  When called from a ModelForm view, the form's _post_clean may
    already have mutated the instance, so the caller should capture the stage
    before form.is_valid().
    """
    if old_stage is None:
        old_stage = client.contact_stage

    # Diff erfassen — nur Feldnamen, keine PII-Werte selbst
    changed_fields = []
    for key, value in fields.items():
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


def track_client_visit(user, client, facility):
    """Record that a user visited a client detail page.

    Upserts a RecentClientVisit row and prunes old entries (max 20 per user).
    """
    RecentClientVisit.objects.update_or_create(
        user=user,
        client=client,
        defaults={"facility": facility},
    )
    # Prune: keep only the 20 most recent visits
    visits = RecentClientVisit.objects.filter(user=user).order_by("-visited_at")
    ids_to_keep = list(visits[:20].values_list("pk", flat=True))
    if ids_to_keep:
        RecentClientVisit.objects.filter(user=user).exclude(pk__in=ids_to_keep).delete()
