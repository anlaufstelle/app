"""Shared read boundary for tasks belonging to live people or no person."""

from django.db.models import Q, QuerySet

from core.models import WorkItem


def workitems_for_read(facility) -> QuerySet[WorkItem]:
    """Keep personless tasks and tasks whose person is active and outside the trash."""
    return WorkItem.objects.for_facility(facility).filter(
        Q(client__isnull=True) | Q(client__is_deleted=False, client__is_active=True)
    )
