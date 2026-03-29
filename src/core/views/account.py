"""User profile page."""

import logging
from datetime import datetime, time

from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from core.models import Case as CaseModel
from core.models import DashboardPreference, Event, RecentClientVisit, WorkItem
from core.views.mixins import AssistantOrAboveRequiredMixin

logger = logging.getLogger(__name__)


class AccountProfileView(AssistantOrAboveRequiredMixin, TemplateView):
    """Profile page for the logged-in user with events and tasks."""

    template_name = "core/account/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        facility = self.request.current_facility
        today = timezone.localdate()

        # Stats widget (from dashboard)
        start_dt = timezone.make_aware(datetime.combine(today, time.min))
        end_dt = timezone.make_aware(datetime.combine(today, time.max))
        context["stats"] = {
            "events_today": Event.objects.filter(
                facility=facility,
                is_deleted=False,
                occurred_at__gte=start_dt,
                occurred_at__lte=end_dt,
            ).count(),
            "open_cases": CaseModel.objects.filter(
                facility=facility,
                status=CaseModel.Status.OPEN,
            ).count(),
            "my_open_tasks": WorkItem.objects.filter(
                facility=facility,
                assigned_to=user,
                status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
            ).count(),
            "total_open_tasks": WorkItem.objects.filter(
                facility=facility,
                status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
            ).count(),
        }

        # Recent clients widget (from dashboard)
        context["recent_clients"] = (
            RecentClientVisit.objects.filter(user=user, facility=facility)
            .select_related("client")
            .order_by("-is_favorite", "-visited_at")[:8]
        )

        # Last 10 created events (read-only)
        context["recent_events"] = (
            Event.objects.filter(created_by=user, is_deleted=False, facility=facility)
            .select_related("document_type", "client")
            .order_by("-occurred_at")[:10]
        )

        # Open tasks (assigned to the user)
        context["open_workitems"] = (
            WorkItem.objects.filter(
                assigned_to=user,
                status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
                facility=facility,
            )
            .select_related("client")
            .order_by("-created_at")[:10]
        )

        # Recently completed tasks
        context["done_workitems"] = (
            WorkItem.objects.filter(
                assigned_to=user,
                status__in=[WorkItem.Status.DONE, WorkItem.Status.DISMISSED],
                facility=facility,
            )
            .select_related("client")
            .order_by("-completed_at")[:5]
        )

        return context


class DashboardPreferenceUpdateView(AssistantOrAboveRequiredMixin, View):
    """HTMX endpoint to toggle dashboard widgets on/off."""

    def post(self, request):
        widget = request.POST.get("widget", "")
        enabled = request.POST.get("enabled", "true") == "true"

        valid_widgets = set(DashboardPreference.DEFAULT_WIDGETS.keys())
        if widget not in valid_widgets:
            return JsonResponse({"error": "Invalid widget"}, status=400)

        pref, _created = DashboardPreference.objects.get_or_create(
            user=request.user,
            defaults={"widgets": dict(DashboardPreference.DEFAULT_WIDGETS)},
        )
        pref.widgets[widget] = enabled
        pref.save(update_fields=["widgets", "updated_at"])

        return JsonResponse({"widget": widget, "enabled": enabled})
