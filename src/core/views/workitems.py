"""WorkItem-Inbox und Detail (Refs #605).

Einzel-Aktionen (Create/Update/Status) liegen in
:file:`views/workitem_actions.py`, Bulk-Endpoints in
:file:`views/workitem_bulk.py`.
"""

import logging

from django.shortcuts import render
from django.views import View

from core.services.case import (
    build_inbox_sections,
    can_user_mutate_workitem,  # Übergangs-Reexport, Abbau nach #1891 (Refs #1837).
)
from core.services.scoping import get_scoped_object
from core.services.workitem_visibility import workitems_for_read
from core.views.mixins import AssistantOrAboveRequiredMixin, HTMXPartialMixin

logger = logging.getLogger(__name__)


class WorkItemInboxView(AssistantOrAboveRequiredMixin, HTMXPartialMixin, View):
    """Personal WorkItem inbox with filtering by type, priority, assignment and due date."""

    template_name = "core/workitems/inbox.html"
    partial_template_name = "core/workitems/partials/inbox_content.html"

    def get(self, request):
        context = build_inbox_sections(request.current_facility, request.user, request.GET)
        return self.render_htmx_or_full(context)


class WorkItemDetailView(AssistantOrAboveRequiredMixin, View):
    """WorkItem detail view."""

    def get(self, request, pk):
        workitem = get_scoped_object(
            workitems_for_read(request.current_facility).select_related("client", "created_by", "assigned_to"),
            request,
            pk=pk,
        )
        # Edit-Button-Sichtbarkeit folgt derselben Policy wie WorkItemUpdateView
        # (StaffRequiredMixin + can_user_mutate_workitem). Refs #753.
        can_edit = request.user.is_staff_or_above and can_user_mutate_workitem(request.user, workitem)
        return render(
            request,
            "core/workitems/detail.html",
            {"workitem": workitem, "can_edit": can_edit},
        )
