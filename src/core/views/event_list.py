"""Paginated contacts with role-visible timeline contents."""

from urllib.parse import urlencode

from django.urls import reverse
from django.views import View

from core.services.events.list_filters import (
    event_list_active_filters,
    event_list_day_groups,
    event_list_options,
    event_list_page_contents,
    event_list_period_segments,
    event_list_queryset,
    event_list_url_params,
    resolve_event_list_filters,
)
from core.views.mixins import AssistantOrAboveRequiredMixin, HTMXPartialMixin, PaginatedListMixin


class EventListView(AssistantOrAboveRequiredMixin, PaginatedListMixin, HTMXPartialMixin, View):
    """Show the same content visibility as the Zeitstrom; no access audit."""

    template_name = "core/events/list.html"
    partial_template_name = "core/events/partials/table.html"

    def is_htmx(self):
        """A history restore swaps the response into the body, so it needs the full page."""
        return super().is_htmx() and self.request.headers.get("HX-History-Restore-Request") != "true"

    def get(self, request):
        facility = request.current_facility
        options = {key: list(values) for key, values in event_list_options(facility, request.user).items()}
        filters = resolve_event_list_filters(request.GET, facility, request.user, options=options)
        queryset = event_list_queryset(facility, request.user, filters, options=options)
        events = self.paginate(queryset, request)
        events.object_list = event_list_page_contents(facility, request.user, events.object_list)
        base_url = reverse("core:event_list")
        active_filters = event_list_active_filters(filters, options, base_url=base_url)
        period_segments, period_extra = event_list_period_segments(filters, base_url=base_url)
        return self.render_htmx_or_full(
            {
                **options,
                "events": events,
                "day_groups": event_list_day_groups(queryset, events.object_list),
                "filters": filters,
                "show_cases": request.user.is_staff_or_above,
                "pagination_params": urlencode(event_list_url_params(filters)),
                "active_filters": active_filters,
                "active_filter_count": len(active_filters),
                "clear_filters_url": base_url,
                "period_segments": period_segments,
                "period_extra": period_extra,
                "filter_form_oob": self.is_htmx(),
            }
        )
