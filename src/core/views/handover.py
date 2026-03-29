"""Views for the shift handover page."""

from datetime import date, timedelta

from django.utils import timezone
from django.views.generic import TemplateView

from core.models import TimeFilter
from core.services.handover import build_handover_summary
from core.views.mixins import AssistantOrAboveRequiredMixin


class HandoverView(AssistantOrAboveRequiredMixin, TemplateView):
    """Shift handover summary page."""

    template_name = "core/handover/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        facility = self.request.current_facility

        target_date = self._get_target_date()

        time_filters = TimeFilter.objects.for_facility(facility).filter(is_active=True)

        # Determine selected shift filter
        selected_filter = None
        time_filter_id = self.request.GET.get("time_filter")
        if time_filter_id:
            selected_filter = time_filters.filter(pk=time_filter_id).first()

        # Auto-select previous shift when viewing today without explicit filter
        if not selected_filter and not time_filter_id and target_date == timezone.localdate():
            now = timezone.localtime()
            prev_filter = None
            for tf in time_filters:
                if tf.covers_time(now):
                    break
                prev_filter = tf
            if prev_filter:
                selected_filter = prev_filter

        summary = build_handover_summary(facility, target_date, selected_filter, self.request.user)

        today = timezone.localdate()
        context.update(
            {
                "summary": summary,
                "target_date": target_date,
                "prev_date": target_date - timedelta(days=1),
                "next_date": target_date + timedelta(days=1),
                "today": today,
                "time_filters": time_filters,
                "selected_filter": selected_filter,
            }
        )
        return context

    def _get_target_date(self):
        date_str = self.request.GET.get("date")
        if date_str:
            try:
                return date.fromisoformat(date_str)
            except ValueError:
                pass
        return timezone.localdate()
