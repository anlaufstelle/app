"""Service for creating activity feed entries."""

from django.contrib.contenttypes.models import ContentType

from core.models.activity import Activity


def log_activity(*, facility, actor, verb, target, summary=""):
    """Create an Activity entry for a system action.

    Called from views and services after successful CRUD operations.
    """
    return Activity.objects.create(
        facility=facility,
        actor=actor,
        verb=verb,
        target_type=ContentType.objects.get_for_model(target),
        target_id=target.pk,
        summary=summary,
    )
