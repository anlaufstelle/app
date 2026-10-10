"""Persist business case activity without using rotation timestamps (Refs #2524)."""

from django.db.models import F, Q
from django.db.models.functions import Coalesce, Greatest
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from core.models import Activity, Case, Event, EventHistory, WorkItem

_BUSINESS_FIELDS = {
    Case: ("title", "description", "status", "closed_at", "lead_user_id", "document_type_id", "data_json"),
    Event: (
        "client_id",
        "case_id",
        "episode_id",
        "document_type_id",
        "occurred_at",
        "data_json",
        "is_anonymous",
        "is_deleted",
    ),
    WorkItem: (
        "client_id",
        "title",
        "description",
        "status",
        "priority",
        "assigned_to_id",
        "due_date",
        "remind_at",
        "recurrence",
        "is_deleted",
    ),
}


def _touch_cases(facility_id, at, *, case_ids=(), client_ids=()):
    """Advance the anchor monotonically; person tasks apply to their open cases."""
    scope = Q(pk__in=[pk for pk in case_ids if pk is not None])
    scope |= Q(client_id__in=[pk for pk in client_ids if pk is not None], status=Case.Status.OPEN)
    Case.objects.filter(facility=facility_id, is_deleted=False, anonymized_at__isnull=True).filter(scope).update(
        last_activity_at=Greatest(Coalesce(F("last_activity_at"), F("created_at")), at)
    )


@receiver(pre_save, sender=Case)
@receiver(pre_save, sender=Event)
@receiver(pre_save, sender=WorkItem)
def remember_business_change(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    fields = _BUSINESS_FIELDS[sender]
    if update_fields is not None:
        fields = tuple(
            field for field in fields if field in update_fields or field.removesuffix("_id") in update_fields
        )
    previous = (
        sender.objects.filter(facility=instance.facility_id, pk=instance.pk).values(*_BUSINESS_FIELDS[sender]).first()
    )
    instance._retention_previous = previous or {}
    instance._retention_business_change = previous is None or any(
        previous[field] != getattr(instance, field) for field in fields
    )


@receiver(post_save, sender=Case)
@receiver(post_save, sender=Event)
@receiver(post_save, sender=WorkItem)
def record_business_change(sender, instance, created, raw=False, **kwargs):
    if raw or not getattr(instance, "_retention_business_change", False):
        return
    if sender is Case:
        if not created:
            _touch_cases(instance.facility_id, timezone.now(), case_ids=(instance.pk,))
        return
    # The system's retention/redaction writes do not renew the business clock.
    if instance.is_deleted and instance.deleted_by_id is None:
        return
    previous = getattr(instance, "_retention_previous", {})
    if sender is Event:
        _touch_cases(instance.facility_id, timezone.now(), case_ids=(instance.case_id, previous.get("case_id")))
    else:
        _touch_cases(instance.facility_id, timezone.now(), client_ids=(instance.client_id, previous.get("client_id")))


@receiver(post_save, sender=Activity)
def record_feed_activity(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return
    model = instance.target_type.model
    case_ids, client_ids = [], []
    if model == "case":
        case_ids = [instance.target_id]
    elif model == "event":
        case_ids = list(
            Event.objects.filter(facility=instance.facility_id, pk=instance.target_id).values_list("case_id", flat=True)
        )
    elif model == "workitem":
        client_ids = list(
            WorkItem.objects.filter(facility=instance.facility_id, pk=instance.target_id).values_list(
                "client_id", flat=True
            )
        )
    elif model == "client":
        client_ids = [instance.target_id]
    _touch_cases(instance.facility_id, instance.occurred_at, case_ids=case_ids, client_ids=client_ids)


@receiver(post_save, sender=EventHistory)
def record_history_activity(sender, instance, created, raw=False, **kwargs):
    if raw or not created or (instance.action == EventHistory.Action.DELETE and instance.changed_by_id is None):
        return
    event = instance.event
    if event.case_id:
        _touch_cases(event.facility_id, instance.changed_at, case_ids=(event.case_id,))
