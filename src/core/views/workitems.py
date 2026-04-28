"""Views for WorkItem management."""

import logging
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.forms.workitems import WorkItemForm
from core.models import Client, WorkItem
from core.models.user import User
from core.services.workitems import (
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    bulk_update_workitem_status,
    create_workitem,
    update_workitem,
    update_workitem_status,
)
from core.views.mixins import AssistantOrAboveRequiredMixin, StaffRequiredMixin

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

        open_items = base_qs.filter(
            status=WorkItem.Status.OPEN,
        ).filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))

        in_progress_items = base_qs.filter(
            status=WorkItem.Status.IN_PROGRESS,
        ).filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))

        seven_days_ago = timezone.now() - timedelta(days=7)
        done_items = base_qs.filter(
            status__in=[WorkItem.Status.DONE, WorkItem.Status.DISMISSED],
            updated_at__gte=seven_days_ago,
        )

        facility_users = User.objects.filter(facility=facility).order_by("last_name", "first_name", "username")

        context = {
            "open_items": open_items,
            "in_progress_items": in_progress_items,
            "done_items": done_items,
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


class WorkItemStatusUpdateView(AssistantOrAboveRequiredMixin, View):
    """HTMX: update WorkItem status."""

    @method_decorator(ratelimit(key="user", rate="120/h", method="POST", block=True))
    def post(self, request, pk):
        workitem = get_object_or_404(
            WorkItem,
            pk=pk,
            facility=request.current_facility,
        )

        if not can_user_mutate_workitem(request.user, workitem):
            return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))

        new_status = request.POST.get("status")
        valid_statuses = [s.value for s in WorkItem.Status]
        if new_status not in valid_statuses:
            return HttpResponseBadRequest(_("Ungültiger Status"))

        update_workitem_status(workitem, new_status, request.user)

        if request.htmx:
            if request.POST.get("hide"):
                return HttpResponse("")
            return render(request, "core/workitems/partials/item_card.html", {"wi": workitem})

        messages.success(request, _("Status aktualisiert."))
        next_url = request.POST.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect("core:workitem_inbox")


class WorkItemCreateView(StaffRequiredMixin, View):
    """Create a WorkItem."""

    def get(self, request):
        facility = request.current_facility
        form = WorkItemForm(facility=facility)

        client_id = request.GET.get("client")
        client_pseudonym = ""
        if client_id:
            try:
                client = Client.objects.get(pk=client_id, facility=facility)
                client_pseudonym = client.pseudonym
            except (Client.DoesNotExist, ValueError):
                client_id = ""

        context = {
            "form": form,
            "client_id": client_id or "",
            "client_pseudonym": client_pseudonym,
        }
        return render(request, "core/workitems/form.html", context)

    @method_decorator(ratelimit(key="user", rate="60/h", method="POST", block=True))
    def post(self, request):
        facility = request.current_facility
        form = WorkItemForm(request.POST, facility=facility)

        if form.is_valid():
            create_workitem(
                facility=facility,
                user=request.user,
                client=form.cleaned_data.get("client"),
                item_type=form.cleaned_data["item_type"],
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description", ""),
                priority=form.cleaned_data["priority"],
                due_date=form.cleaned_data.get("due_date"),
                remind_at=form.cleaned_data.get("remind_at"),
                recurrence=form.cleaned_data.get("recurrence") or WorkItem.Recurrence.NONE,
                assigned_to=form.cleaned_data.get("assigned_to"),
            )
            messages.success(request, _("Aufgabe wurde erstellt."))
            return redirect("core:workitem_inbox")

        context = {
            "form": form,
            "client_id": request.POST.get("client", ""),
            "client_pseudonym": "",
        }
        return render(request, "core/workitems/form.html", context)


class WorkItemUpdateView(StaffRequiredMixin, View):
    """Edit a WorkItem."""

    def get(self, request, pk):
        workitem = get_object_or_404(
            WorkItem.objects.select_related("client"),
            pk=pk,
            facility=request.current_facility,
        )
        form = WorkItemForm(instance=workitem, facility=request.current_facility)
        context = {
            "form": form,
            "workitem": workitem,
            "client_id": str(workitem.client.pk) if workitem.client else "",
            "client_pseudonym": workitem.client.pseudonym if workitem.client else "",
        }
        return render(request, "core/workitems/form.html", context)

    def post(self, request, pk):
        workitem = get_object_or_404(
            WorkItem,
            pk=pk,
            facility=request.current_facility,
        )
        form = WorkItemForm(request.POST, instance=workitem, facility=request.current_facility)

        if form.is_valid():
            expected_updated_at = request.POST.get("expected_updated_at") or None
            try:
                update_workitem(
                    workitem,
                    request.user,
                    expected_updated_at=expected_updated_at,
                    client=form.cleaned_data.get("client"),
                    item_type=form.cleaned_data["item_type"],
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    priority=form.cleaned_data["priority"],
                    due_date=form.cleaned_data.get("due_date"),
                    remind_at=form.cleaned_data.get("remind_at"),
                    recurrence=form.cleaned_data.get("recurrence") or WorkItem.Recurrence.NONE,
                    assigned_to=form.cleaned_data.get("assigned_to"),
                )
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, "message") else str(e))
                return redirect("core:workitem_update", pk=workitem.pk)
            messages.success(request, _("Aufgabe wurde aktualisiert."))
            return redirect("core:workitem_inbox")

        context = {
            "form": form,
            "workitem": workitem,
            "client_id": request.POST.get("client", ""),
            "client_pseudonym": "",
        }
        return render(request, "core/workitems/form.html", context)


class WorkItemDetailView(AssistantOrAboveRequiredMixin, View):
    """WorkItem detail view."""

    def get(self, request, pk):
        workitem = get_object_or_404(
            WorkItem.objects.select_related("client", "created_by", "assigned_to"),
            pk=pk,
            facility=request.current_facility,
        )
        return render(request, "core/workitems/detail.html", {"workitem": workitem})


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
