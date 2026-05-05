"""Template context processors for Anlaufstelle."""

import datetime

from django.conf import settings
from django.db.models import Q

from core.models import WorkItem


def source_code(request):
    """Refs #835 (C-68): exponiere SOURCE_CODE_URL/SOURCE_CODE_VERSION
    fuer den AGPL-§13-Footer. Wird in jedem Template gerendert.
    """
    return {
        "SOURCE_CODE_URL": settings.SOURCE_CODE_URL,
        "SOURCE_CODE_VERSION": settings.SOURCE_CODE_VERSION,
    }


def workitem_counts(request):
    """Open and overdue WorkItem counts for navigation badges."""
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {}

    # HTMX partials never render the navigation — badge count unnecessary.
    if request.headers.get("HX-Request"):
        return {}

    facility = getattr(request, "current_facility", None)
    if not facility:
        return {}

    active_filter = Q(status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS])
    user_filter = Q(assigned_to=request.user) | Q(assigned_to__isnull=True)

    base_qs = WorkItem.objects.for_facility(facility).filter(active_filter).filter(user_filter)

    count = base_qs.count()
    overdue_count = base_qs.filter(due_date__lt=datetime.date.today()).count()

    return {
        "open_workitems_count": count,
        "overdue_workitems_count": overdue_count,
        "current_facility": facility,
    }
