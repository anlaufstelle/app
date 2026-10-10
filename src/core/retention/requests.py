"""Retention of completed deletion, merge and stage requests (Refs #2524)."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import DeletionRequest


@transaction.atomic
def prune_deletion_requests(facility, settings_obj, now=None, dry_run=False):
    """Delete terminal requests after their completion period; keep the audit."""
    now = now or timezone.now()
    candidates = DeletionRequest.objects.for_facility(facility).filter(
        status__in=(DeletionRequest.Status.APPROVED, DeletionRequest.Status.REJECTED, DeletionRequest.Status.WITHDRAWN),
        reviewed_at__lt=now - timedelta(days=settings_obj.deletion_request_retention_days),
    )
    if dry_run:
        return {"count": candidates.count()}
    ids = list(candidates.order_by("pk").select_for_update().values_list("pk", flat=True))
    DeletionRequest.objects.for_facility(facility).filter(pk__in=ids).delete()
    return {"count": len(ids)}
