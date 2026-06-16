"""Views for the Zeitstrom (unified activity stream) start page."""

import logging
from datetime import date, timedelta

from django.utils import timezone
from django.views.generic import TemplateView

from core.models import DocumentType, TimeFilter
from core.services.case import build_handover_summary
from core.services.dashboard import build_focus_box, staff_dashboard_context
from core.services.events import build_feed_items, enrich_events_with_preview
from core.services.system import get_active_bans
from core.views.mixins import AssistantOrAboveRequiredMixin

logger = logging.getLogger(__name__)


class ZeitstromView(AssistantOrAboveRequiredMixin, TemplateView):
    """Unified activity stream combining Dashboard + Aktivitätslog + Timeline."""

    template_name = "core/zeitstrom/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        facility = self.request.current_facility

        target_date = self._get_target_date()
        feed_type = self.request.GET.get("type", "")

        # TimeFilter tabs (from Timeline)
        time_filters = TimeFilter.objects.for_facility(facility).filter(is_active=True)
        selected_filter_id = self.request.GET.get("time_filter")

        selected_filter = None
        if selected_filter_id and selected_filter_id != "all":
            selected_filter = time_filters.filter(pk=selected_filter_id).first()
        if not selected_filter and selected_filter_id != "all" and target_date == timezone.localdate():
            # Auto-select: choose the time filter that covers the current time (today only)
            now = timezone.localtime()
            for tf in time_filters:
                if tf.covers_time(now):
                    selected_filter = tf
                    # Midnight overlap: early morning hours belong to previous day's night shift
                    if tf.start_time > tf.end_time and now.time() <= tf.end_time:
                        target_date = target_date - timedelta(days=1)
                    break

        selected_filter_id = str(selected_filter.pk) if selected_filter else "all"

        # Build feed items
        feed_items = build_feed_items(
            facility,
            target_date,
            feed_type,
            time_filter=selected_filter,
            user=self.request.user,
        )
        enrich_events_with_preview(feed_items, self.request.user)

        # Document type filter (from Timeline)
        doc_type_id = self.request.GET.get("doc_type")
        if doc_type_id:
            feed_items = [
                item
                for item in feed_items
                if item["type"] != "event" or str(item["object"].document_type_id) == doc_type_id
            ]

        from core.services.compliance import allowed_sensitivities_for_user

        document_types = (
            DocumentType.objects.for_facility(facility)
            .filter(
                is_active=True,
                sensitivity__in=allowed_sensitivities_for_user(self.request.user),
            )
            .order_by("name")
        )

        # Sidebar: Team-Fokusbox für Handlungsbedarf (Refs #1128).
        # Gruppiert offene/laufende Aufgaben der Einrichtung nach Druckstufe
        # (überfällig, heute, dringend/wichtig, in Bearbeitung) statt einer
        # flachen Top-5-Liste; select_related deckt client/assigned_to für die
        # Zeilen-Partials ab.
        focus_box = build_focus_box(facility)

        # Handover summary (when a shift filter is active)
        handover_summary = None
        if selected_filter:
            handover_summary = build_handover_summary(facility, target_date, selected_filter, self.request.user)

        # Active bans (from Dashboard)
        active_bans = get_active_bans(facility, user=self.request.user)

        today = timezone.localdate()
        # Pills für die Type-Filter im Zeitstrom (Design: als-screens.jsx Filter-Pills)
        from django.utils.translation import gettext as _

        filter_options = [
            ("", _("Alle")),
            ("events", _("Kontakte")),
            ("activities", _("Aktivitäten")),
            ("workitems", _("Aufgaben")),
            ("bans", _("Hausverbote")),
        ]
        context.update(
            {
                "feed_items": feed_items,
                "handover_summary": handover_summary,
                "time_filters": time_filters,
                "selected_filter": selected_filter,
                "selected_filter_id": selected_filter_id,
                "target_date": target_date,
                "prev_date": target_date - timedelta(days=1),
                "next_date": target_date + timedelta(days=1),
                "today": today,
                "selected_type": feed_type,
                "filter_options": filter_options,
                "document_types": document_types,
                "selected_doc_type": doc_type_id or "",
                "events_partial_url": "core:zeitstrom_feed_partial",
                "focus_box": focus_box,
                "active_bans": active_bans,
            }
        )

        # Rollen-Cockpit als Kopf der Start-Seite — nur Fachkraft/Assistenz
        # (Leitung/Admin behalten /start/). Refs #1124.
        if not self.request.user.is_lead_or_admin and not self.request.user.is_super_admin:
            context["show_cockpit"] = True
            context["cockpit"] = staff_dashboard_context(self.request.user, facility)

        # Übergabe-Modus: Schicht-Zusammenfassung als Primärinhalt (Refs #1124).
        if self.request.GET.get("view") == "uebergabe":
            uebergabe_filter = self._select_handover_shift(time_filters, target_date)
            context["uebergabe_mode"] = True
            context["uebergabe_summary"] = build_handover_summary(
                facility, target_date, uebergabe_filter, self.request.user
            )
            context["selected_filter"] = uebergabe_filter
            context["selected_filter_id"] = str(uebergabe_filter.pk) if uebergabe_filter else "all"

        return context

    def _get_target_date(self):
        date_str = self.request.GET.get("date")
        if date_str:
            try:
                return date.fromisoformat(date_str)
            except ValueError:
                pass
        return timezone.localdate()

    def _select_handover_shift(self, time_filters, target_date):
        """Vorschicht-Auto-Wahl fuer den Uebergabe-Modus (Port aus HandoverView)."""
        time_filter_id = self.request.GET.get("time_filter")
        if time_filter_id and time_filter_id != "all":
            return time_filters.filter(pk=time_filter_id).first()
        if target_date == timezone.localdate():
            now = timezone.localtime()
            prev_filter = None
            for tf in time_filters:
                if tf.covers_time(now):
                    break
                prev_filter = tf
            return prev_filter
        return None


class ZeitstromFeedPartialView(AssistantOrAboveRequiredMixin, TemplateView):
    """HTMX partial: feed list for the Zeitstrom."""

    template_name = "core/zeitstrom/partials/feed_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        facility = self.request.current_facility

        target_date_str = self.request.GET.get("date")
        try:
            target_date = date.fromisoformat(target_date_str) if target_date_str else timezone.localdate()
        except ValueError:
            target_date = timezone.localdate()

        feed_type = self.request.GET.get("type", "")

        # TimeFilter support for HTMX partial
        time_filter = None
        time_filter_id = self.request.GET.get("time_filter")
        if time_filter_id and time_filter_id != "all":
            time_filter = TimeFilter.objects.for_facility(facility).filter(pk=time_filter_id).first()

        feed_items = build_feed_items(
            facility,
            target_date,
            feed_type,
            time_filter=time_filter,
            user=self.request.user,
        )
        enrich_events_with_preview(feed_items, self.request.user)

        # Document type filter
        doc_type_id = self.request.GET.get("doc_type")
        if doc_type_id:
            feed_items = [
                item
                for item in feed_items
                if item["type"] != "event" or str(item["object"].document_type_id) == doc_type_id
            ]

        context["feed_items"] = feed_items
        return context
