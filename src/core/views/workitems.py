"""WorkItem-Inbox und Detail (Refs #605).

Einzel-Aktionen (Create/Update/Status) liegen in
:file:`views/workitem_actions.py`, Bulk-Endpoints in
:file:`views/workitem_bulk.py`. Die Shared-Ownership-Policy
``can_user_mutate_workitem`` bleibt hier zentral, damit sie von beiden
Modulen importiert werden kann, ohne Zirkular-Import-Risiko.
"""

import logging
from datetime import timedelta

from django.db.models import Case, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.models import WorkItem
from core.models.user import User
from core.views.mixins import AssistantOrAboveRequiredMixin

logger = logging.getLogger(__name__)


def can_user_mutate_workitem(user, workitem):
    """True if ``user`` darf ``workitem`` mutieren (Status/Priorität/Assignee).

    Identisch zur Einzel-Update-Regel: Leads/Admins, Ersteller:innen und
    Zugewiesene. Zentrale Hilfsfunktion, damit Single- und Bulk-Routen
    dieselbe Policy anwenden (Refs #583).
    """
    return user.is_lead_or_admin or workitem.created_by == user or workitem.assigned_to == user


class WorkItemInboxView(AssistantOrAboveRequiredMixin, View):
    """Personal WorkItem inbox with filtering by type, priority, assignment and due date."""

    DUE_FILTER_CHOICES = [
        ("overdue", _("Überfällig")),
        ("today", _("Heute")),
        ("week", _("Diese Woche")),
        ("none", _("Ohne Frist")),
    ]

    def _apply_filters(self, qs, request):
        """Evaluate GET parameters and filter the queryset."""
        item_type = request.GET.get("item_type")
        if item_type and item_type in dict(WorkItem.ItemType.choices):
            qs = qs.filter(item_type=item_type)

        priority = request.GET.get("priority")
        if priority and priority in dict(WorkItem.Priority.choices):
            qs = qs.filter(priority=priority)

        assigned_to = request.GET.get("assigned_to")
        if assigned_to == "me":
            assigned_to = str(request.user.id)
        if assigned_to:
            qs = qs.filter(assigned_to_id=assigned_to)

        due = request.GET.get("due")
        if due:
            today = timezone.localdate()
            valid_due_values = {c[0] for c in self.DUE_FILTER_CHOICES}
            if due in valid_due_values:
                if due == "overdue":
                    qs = qs.filter(
                        due_date__lt=today,
                        status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
                    )
                elif due == "today":
                    qs = qs.filter(due_date=today)
                elif due == "week":
                    qs = qs.filter(due_date__gte=today, due_date__lte=today + timedelta(days=7))
                elif due == "none":
                    qs = qs.filter(due_date__isnull=True)

        return qs

    def get(self, request):
        facility = request.current_facility
        user = request.user

        today = timezone.localdate()
        base_qs = (
            WorkItem.objects.for_facility(facility)
            .select_related("client", "created_by", "assigned_to")
            .annotate(
                priority_order=Case(
                    When(priority=WorkItem.Priority.URGENT, then=Value(0)),
                    When(priority=WorkItem.Priority.IMPORTANT, then=Value(1)),
                    When(priority=WorkItem.Priority.NORMAL, then=Value(2)),
                    output_field=IntegerField(),
                ),
                due_date_bucket=Case(
                    When(
                        due_date__lt=today,
                        status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
                        then=Value(0),
                    ),
                    When(due_date=today, then=Value(1)),
                    When(due_date__gt=today, then=Value(2)),
                    When(due_date__isnull=True, then=Value(9)),
                    default=Value(5),
                    output_field=IntegerField(),
                ),
            )
            .order_by("due_date_bucket", "due_date", "priority_order", "-created_at")
        )

        base_qs = self._apply_filters(base_qs, request)

        # Jede der drei Listen wird auf INBOX_LIST_LIMIT begrenzt, damit
        # Facilities mit Hunderten offener Aufgaben die Inbox nicht langsam
        # machen. Das *_has_more-Flag signalisiert dem Template, dass
        # weitere Einträge über Filter oder die Detail-Suche erreichbar sind.
        # Listen werden evaluiert (list(...)), damit {{ list|length }} im
        # Template keine zusätzliche COUNT-Query auslöst.
        # Refs #639 #640.
        INBOX_LIST_LIMIT = 50

        open_qs = base_qs.filter(
            status=WorkItem.Status.OPEN,
        ).filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
        open_items = list(open_qs[:INBOX_LIST_LIMIT + 1])
        open_has_more = len(open_items) > INBOX_LIST_LIMIT
        if open_has_more:
            open_items = open_items[:INBOX_LIST_LIMIT]

        in_progress_qs = base_qs.filter(
            status=WorkItem.Status.IN_PROGRESS,
        ).filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
        in_progress_items = list(in_progress_qs[:INBOX_LIST_LIMIT + 1])
        in_progress_has_more = len(in_progress_items) > INBOX_LIST_LIMIT
        if in_progress_has_more:
            in_progress_items = in_progress_items[:INBOX_LIST_LIMIT]

        seven_days_ago = timezone.now() - timedelta(days=7)
        done_qs = base_qs.filter(
            status__in=[WorkItem.Status.DONE, WorkItem.Status.DISMISSED],
            updated_at__gte=seven_days_ago,
        )
        done_items = list(done_qs[:INBOX_LIST_LIMIT + 1])
        done_has_more = len(done_items) > INBOX_LIST_LIMIT
        if done_has_more:
            done_items = done_items[:INBOX_LIST_LIMIT]

        facility_users = User.objects.filter(facility=facility).order_by("last_name", "first_name", "username")

        context = {
            "open_items": open_items,
            "open_has_more": open_has_more,
            "in_progress_items": in_progress_items,
            "in_progress_has_more": in_progress_has_more,
            "done_items": done_items,
            "done_has_more": done_has_more,
            "inbox_list_limit": INBOX_LIST_LIMIT,
            "item_type_choices": WorkItem.ItemType.choices,
            "priority_choices": WorkItem.Priority.choices,
            "status_choices": WorkItem.Status.choices,
            "due_filter_choices": self.DUE_FILTER_CHOICES,
            "facility_users": facility_users,
            "selected_item_type": request.GET.get("item_type", ""),
            "selected_priority": request.GET.get("priority", ""),
            "selected_assigned_to": request.GET.get("assigned_to", ""),
            "selected_due": request.GET.get("due", ""),
        }

        if request.headers.get("HX-Request"):
            return render(request, "core/workitems/partials/inbox_content.html", context)

        return render(request, "core/workitems/inbox.html", context)


class WorkItemDetailView(AssistantOrAboveRequiredMixin, View):
    """WorkItem detail view."""

    def get(self, request, pk):
        workitem = get_object_or_404(
            WorkItem.objects.select_related("client", "created_by", "assigned_to"),
            pk=pk,
            facility=request.current_facility,
        )
        return render(request, "core/workitems/detail.html", {"workitem": workitem})
