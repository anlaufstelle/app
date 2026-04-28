"""Bulk-Aktionen für WorkItems (Refs #605).

Abgeteilt von :file:`views/workitems.py`, damit die Single-Item-Views und
die Inbox nicht mit Bulk-Semantik vermischt sind. Der gemeinsame
Ownership-Check (pro-Item!) ist aus [`fd140d0`](https://github.com/tobiasnix/anlaufstelle/commit/fd140d0)
und bleibt hier zentral — ein Bulk-Endpoint darf nicht feiner erlauben
als die Single-Route.
"""

from django.contrib import messages
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_BULK_ACTION
from core.models import WorkItem
from core.models.user import User
from core.services.workitems import (
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    bulk_update_workitem_status,
)
from core.views.mixins import AssistantOrAboveRequiredMixin
from core.views.workitems import can_user_mutate_workitem


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="POST", block=True),
    name="post",
)
class _BulkActionMixin(AssistantOrAboveRequiredMixin):
    """Shared helper for bulk WorkItem actions (Refs #267).

    Subclasses implement ``perform_action(request, workitems)`` which applies the
    mutation via a service function and returns the processed count. The mixin
    takes care of scoping to ``request.current_facility`` and rendering the
    inbox-partial for HTMX responses.
    """

    def _get_workitem_ids(self, request):
        ids = request.POST.getlist("workitem_ids") or request.POST.getlist("workitem_ids[]")
        return [i for i in ids if i]

    def _load_workitems(self, request, ids):
        return list(
            WorkItem.objects.filter(
                pk__in=ids,
                facility=request.current_facility,
            )
        )

    def perform_action(self, request, workitems):  # pragma: no cover - overridden
        raise NotImplementedError

    def post(self, request):
        ids = self._get_workitem_ids(request)
        if not ids:
            return HttpResponseBadRequest(_("Keine Aufgaben ausgewählt."))

        workitems = self._load_workitems(request, ids)
        if not workitems:
            return HttpResponseBadRequest(_("Keine gültigen Aufgaben gefunden."))

        # Ownership-Check pro Item — Bulk-Route darf nicht feiner erlauben als
        # die Single-Route (Refs #583). Sobald ein Item nicht mutierbar ist,
        # brechen wir ab, um keine Teil-Mutation mit irreführender
        # "5 aktualisiert"-Erfolgsmeldung zu erzeugen.
        forbidden = [wi for wi in workitems if not can_user_mutate_workitem(request.user, wi)]
        if forbidden:
            return HttpResponseForbidden(_("Keine Berechtigung für ausgewählte Aufgaben."))

        try:
            count = self.perform_action(request, workitems)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        messages.success(request, _("%(count)d Aufgaben aktualisiert.") % {"count": count})

        if request.headers.get("HX-Request"):
            response = redirect("core:workitem_inbox")
            response["HX-Redirect"] = response["Location"]
            return response

        return redirect("core:workitem_inbox")


class WorkItemBulkStatusView(_BulkActionMixin, View):
    """Bulk-update status for selected WorkItems."""

    def perform_action(self, request, workitems):
        status = request.POST.get("status", "").strip()
        if status not in {s.value for s in WorkItem.Status}:
            raise ValueError(_("Ungültiger Status"))
        return bulk_update_workitem_status(workitems, request.user, status)


class WorkItemBulkPriorityView(_BulkActionMixin, View):
    """Bulk-update priority for selected WorkItems."""

    def perform_action(self, request, workitems):
        priority = request.POST.get("priority", "").strip()
        if priority not in {p.value for p in WorkItem.Priority}:
            raise ValueError(_("Ungültige Priorität"))
        return bulk_update_workitem_priority(workitems, request.user, priority)


class WorkItemBulkAssignView(_BulkActionMixin, View):
    """Bulk-assign selected WorkItems (or clear the assignment)."""

    def perform_action(self, request, workitems):
        assignee_id = request.POST.get("assigned_to", "").strip()
        assignee = None
        if assignee_id:
            try:
                assignee = User.objects.get(
                    pk=assignee_id,
                    facility=request.current_facility,
                )
            except (User.DoesNotExist, ValueError, TypeError) as exc:
                raise ValueError(_("Unbekannte Benutzerin/Benutzer")) from exc
        return bulk_assign_workitems(workitems, request.user, assignee)
