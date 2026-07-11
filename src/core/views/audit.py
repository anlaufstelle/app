"""Views for audit log."""

import logging

from django.shortcuts import get_object_or_404, render
from django.views import View

from core.constants import AUDIT_PAGE_SIZE
from core.models import AuditLog
from core.models.user import User
from core.utils.formatting import parse_date
from core.views.mixins import FacilityAdminRequiredMixin, FilteredPaginatedListMixin, HTMXPartialMixin

logger = logging.getLogger(__name__)


class AuditLogListView(FacilityAdminRequiredMixin, FilteredPaginatedListMixin, HTMXPartialMixin, View):
    """Audit log list for admins with filters and pagination.

    Refs #1164: Equality-Filter (action, user) und ``pagination_params``
    laufen ueber ``FilteredPaginatedListMixin``. Das Datums-Parsing
    (``date_from``/``date_to`` via ``parse_date``) bleibt bewusst custom
    und wird ueber ``extra_params`` an den Querystring-Rebuild gereicht.
    """

    template_name = "core/audit/list.html"
    partial_template_name = "core/audit/partials/table.html"
    page_size = AUDIT_PAGE_SIZE
    filter_fields = {"action": "action", "user": "user_id"}

    def get(self, request):
        facility = request.current_facility
        queryset = AuditLog.objects.for_facility(facility).select_related("user")

        queryset = self.apply_filters(queryset, request)

        # Filter: date_from / date_to — custom (Datums-Parsing, Refs #1164).
        date_from_str = request.GET.get("date_from", "")
        date_from = parse_date(date_from_str)
        if date_from:
            queryset = queryset.filter(timestamp__date__gte=date_from)

        date_to_str = request.GET.get("date_to", "")
        date_to = parse_date(date_to_str)
        if date_to:
            queryset = queryset.filter(timestamp__date__lte=date_to)

        page = self.paginate(queryset, request)

        # Users of the facility for dropdown
        facility_users = User.objects.filter(facility=facility).order_by("last_name", "first_name", "username")

        pagination_params = self.pagination_params(
            request,
            extra_params={"date_from": date_from_str, "date_to": date_to_str},
        )

        context = {
            "page_obj": page,
            "action_choices": AuditLog.Action.choices,
            "facility_users": facility_users,
            "filter_action": request.GET.get("action", ""),
            "filter_user": request.GET.get("user", ""),
            "filter_date_from": date_from_str,
            "filter_date_to": date_to_str,
            "pagination_params": pagination_params,
        }

        return self.render_htmx_or_full(context)


class AuditLogDetailView(FacilityAdminRequiredMixin, View):
    """Detail view for a single audit log entry."""

    def get(self, request, pk):
        facility = request.current_facility
        entry = get_object_or_404(AuditLog.objects.select_related("user", "actor"), pk=pk, facility=facility)
        return render(request, "core/audit/detail.html", {"entry": entry})
