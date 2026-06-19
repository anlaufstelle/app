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

from core.constants import WORKITEM_INBOX_CAP
from core.models import WorkItem
from core.models.user import User
from core.views.mixins import AssistantOrAboveRequiredMixin, HTMXPartialMixin

logger = logging.getLogger(__name__)


def can_user_mutate_workitem(user, workitem):
    """True if ``user`` darf ``workitem`` mutieren (Status/Priorität/Assignee).

    Mutierbar sind: Leads/Admins (innerhalb ihrer Facility), Ersteller:innen,
    Zugewiesene — sowie **nicht zugewiesene Teamaufgaben** (Refs #1125).

    Zur Begründung der Teamaufgaben-Regel: Die Inbox blendet jeder Fachkraft/
    Assistenz offene + nicht zugewiesene Items mit "Übernehmen"-Buttons und
    Bulk-Auswahl ein, und der Zeitstrom zeigt sie ebenfalls. Eine nicht
    zugewiesene Aufgabe ist damit fachlich eine vom Team aufzunehmende
    Aufgabe. Ohne diese Regel sähe eine Fachkraft die Aufgabe samt Buttons,
    erhielte beim Anwenden aber 403 "Keine Berechtigung für ausgewählte
    Aufgaben." — die Sichtbarkeit (Inbox/Zeitstrom/Übergabe) und die
    Mutierbarkeit liefen auseinander. Items, die einer *anderen* Person
    zugewiesen sind, bleiben geschützt (und sind für Fachkräfte auch in der
    Inbox ausgeblendet). Zentrale Hilfsfunktion, damit Single- und Bulk-Routen
    dieselbe Policy anwenden (Refs #583).
    """
    return (
        user.is_lead_or_admin
        or workitem.created_by == user
        or workitem.assigned_to == user
        or workitem.assigned_to_id is None
    )


class WorkItemInboxView(AssistantOrAboveRequiredMixin, HTMXPartialMixin, View):
    """Personal WorkItem inbox with filtering by type, priority, assignment and due date."""

    template_name = "core/workitems/inbox.html"
    partial_template_name = "core/workitems/partials/inbox_content.html"

    # Refs #1145: Sentinel-Wert für den ``assigned_to``-Filter, der die
    # Default-Sicht ("Mir zugewiesen + nicht zugewiesene Teamaufgaben")
    # *explizit* benennt. Nötig, weil diese Default-Sicht eine eigene, von
    # "Mir zugewiesen" (strikt ``me``) und "Alle" (alle Personen) verschiedene
    # Liste liefert. Ohne eigenen Wert zeigte das Dropdown beim parameterlosen
    # Aufruf mangels gesetztem ``selected`` die erste Option ("Mir zugewiesen")
    # an, während die Query die breitere Default-Liste lieferte — Anzeige und
    # Filterwirkung liefen auseinander. Der Sentinel sorgt dafür, dass die
    # sichtbare Auswahl der Query entspricht und über Filter-Persistenz
    # (filter-persistence.js) sowie Bulk-Redirect (Refs #1132) korrekt
    # round-trippt.
    DEFAULT_ASSIGNED_SCOPE = "mine_team"

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
        # Refs #1145: Der Default-Sentinel ist kein Personen-/me-Filter, sondern
        # benennt nur die breitere Default-Sicht — die eigentliche Eingrenzung
        # passiert in ``_apply_default_scope`` (get). Hier wie "kein Filter"
        # behandeln, damit ``mine_team`` nicht als (ungültiger) Personen-Wert in
        # die Query gerät.
        if assigned_to == self.DEFAULT_ASSIGNED_SCOPE:
            assigned_to = None
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
        # Refs #639 #640, #803.
        cap = WORKITEM_INBOX_CAP

        # Default-Sicht = "Mir zugewiesen + nicht zugewiesene Teamaufgaben".
        # Sie greift, wenn kein ``assigned_to``-Parameter gesetzt ist *oder* der
        # Default-Sentinel ``mine_team`` explizit gewählt wurde (Refs #1145).
        # Sobald die Nutzer:in dagegen "Alle" (``assigned_to=``) oder eine Person
        # wählt, greift nur noch der Filter aus ``_apply_filters`` — normale
        # Aufgaben sollen innerhalb der Facility auffindbar sein, auch wenn sie
        # einer anderen Person zugewiesen sind (private Aufgaben aus #607
        # existieren noch nicht). Vorher schnitt die Inbox jede Liste hart mit
        # ``Q(assigned_to=user) | isnull`` — dadurch lieferte "Alle" weiterhin
        # nur eigene+unassigned, und ein Personenfilter auf jemand anderen eine
        # leere Liste. Eine selbst erstellte, fremd-zugewiesene Aufgabe
        # "verschwand" so aus der Sicht der Erstellerin (Refs #1125).
        #
        # Refs #1134: Die Eingrenzung gilt im Default für *alle drei* Listen —
        # auch "Kürzlich erledigt". War diese als einzige unscoped, zeigte die
        # Default-Sicht fremd-zugewiesene erledigte Aufgaben an und machte sie
        # per Bulk auswählbar; eine Statusänderung (z.B. Erledigt → In
        # Bearbeitung) verschob das Item dann in eine scoped Liste, wo es nicht
        # mehr auftauchte — Liste und tatsächlicher Status liefen auseinander.
        #
        # Refs #1145: ``selected_assigned_to`` für die Anzeige unterscheidet
        # "Parameter fehlt" (Default → Sentinel anzeigen) von "Parameter
        # vorhanden, aber leer" (explizit "Alle"). ``request.GET.get(..., "")``
        # allein kann das nicht, weil beide Fälle ``""`` liefern.
        raw_assigned_to = request.GET.get("assigned_to")
        param_absent = raw_assigned_to is None
        default_scope = param_absent or raw_assigned_to == self.DEFAULT_ASSIGNED_SCOPE
        selected_assigned_to = self.DEFAULT_ASSIGNED_SCOPE if param_absent else raw_assigned_to

        def _apply_default_scope(qs):
            if default_scope:
                return qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
            return qs

        open_qs = _apply_default_scope(base_qs.filter(status=WorkItem.Status.OPEN))
        open_items = list(open_qs[: cap + 1])
        open_has_more = len(open_items) > cap
        if open_has_more:
            open_items = open_items[:cap]

        in_progress_qs = _apply_default_scope(base_qs.filter(status=WorkItem.Status.IN_PROGRESS))
        in_progress_items = list(in_progress_qs[: cap + 1])
        in_progress_has_more = len(in_progress_items) > cap
        if in_progress_has_more:
            in_progress_items = in_progress_items[:cap]

        seven_days_ago = timezone.now() - timedelta(days=7)
        done_qs = _apply_default_scope(
            base_qs.filter(
                status__in=[WorkItem.Status.DONE, WorkItem.Status.DISMISSED],
                updated_at__gte=seven_days_ago,
            )
        )
        done_items = list(done_qs[: cap + 1])
        done_has_more = len(done_items) > cap
        if done_has_more:
            done_items = done_items[:cap]

        facility_users = User.objects.filter(facility=facility).order_by("last_name", "first_name", "username")

        context = {
            "open_items": open_items,
            "open_has_more": open_has_more,
            "in_progress_items": in_progress_items,
            "in_progress_has_more": in_progress_has_more,
            "done_items": done_items,
            "done_has_more": done_has_more,
            "inbox_list_limit": cap,
            "item_type_choices": WorkItem.ItemType.choices,
            "priority_choices": WorkItem.Priority.choices,
            "status_choices": WorkItem.Status.choices,
            "due_filter_choices": self.DUE_FILTER_CHOICES,
            "facility_users": facility_users,
            "selected_item_type": request.GET.get("item_type", ""),
            "selected_priority": request.GET.get("priority", ""),
            "selected_assigned_to": selected_assigned_to,
            "default_assigned_scope": self.DEFAULT_ASSIGNED_SCOPE,
            "selected_due": request.GET.get("due", ""),
        }

        return self.render_htmx_or_full(context)


class WorkItemDetailView(AssistantOrAboveRequiredMixin, View):
    """WorkItem detail view."""

    def get(self, request, pk):
        workitem = get_object_or_404(
            WorkItem.objects.select_related("client", "created_by", "assigned_to"),
            pk=pk,
            facility=request.current_facility,
        )
        # Edit-Button-Sichtbarkeit folgt derselben Policy wie WorkItemUpdateView
        # (StaffRequiredMixin + can_user_mutate_workitem). Refs #753.
        can_edit = request.user.is_staff_or_above and can_user_mutate_workitem(request.user, workitem)
        return render(
            request,
            "core/workitems/detail.html",
            {"workitem": workitem, "can_edit": can_edit},
        )
