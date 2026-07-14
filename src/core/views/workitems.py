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
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.constants import WORKITEM_INBOX_CAP, WORKITEM_RECENT_DONE_PREVIEW
from core.models import WorkItem
from core.models.user import User
from core.services.scoping import get_scoped_object
from core.views.mixins import AssistantOrAboveRequiredMixin, HTMXPartialMixin

logger = logging.getLogger(__name__)


def apply_workitem_filters(qs, *, item_type="", priority="", assigned_to="", due="", user=None):
    """Wendet die Inbox-Filter (Typ/Priorität/Zuweisung/Fälligkeit) auf ``qs`` an.

    Refs #1568: aus ``WorkItemInboxView._apply_filters`` extrahiert, damit
    ``WorkItemStatusUpdateView`` beim OOB-Insert in "Kürzlich erledigt"
    (nach dem Markieren als erledigt/verworfen) dieselbe Filterlogik
    wiederverwenden kann statt sie zu duplizieren — die Issue-Vorgabe
    verlangt ausdrücklich "keine neue Filterlogik erfinden".

    ``assigned_to`` ist hier bereits aufgelöst: der Default-Sentinel
    (``WorkItemInboxView.DEFAULT_ASSIGNED_SCOPE``) muss der Aufrufer vorher
    auf ``""`` normalisieren (siehe ``apply_workitem_default_scope``); "me"
    wird hier noch in die User-ID übersetzt.
    """
    if item_type and item_type in dict(WorkItem.ItemType.choices):
        qs = qs.filter(item_type=item_type)

    if priority and priority in dict(WorkItem.Priority.choices):
        qs = qs.filter(priority=priority)

    if assigned_to == "me" and user is not None:
        assigned_to = str(user.id)
    if assigned_to:
        qs = qs.filter(assigned_to_id=assigned_to)

    if due:
        today = timezone.localdate()
        valid_due_values = {c[0] for c in WorkItemInboxView.DUE_FILTER_CHOICES}
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


def apply_workitem_default_scope(qs, user, *, default_scope):
    """Grenzt ``qs`` auf "Mir & unzugewiesene" ein, wenn ``default_scope`` True ist.

    Refs #1145 (Ursprung), #1568 (extrahiert für Wiederverwendung durch den
    OOB-Insert von ``WorkItemStatusUpdateView``).
    """
    if default_scope:
        return qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
    return qs


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

    # Refs #1570: Gezielter Statusfilter — ergänzt die drei immer sichtbaren
    # Sektionen (Offen/In Bearbeitung/passive "Kürzlich erledigt"-Vorschau) um
    # die Möglichkeit, genau eine Sektion anzuzeigen. "" (leer) = alle drei
    # Sektionen wie bisher ("Alle Status"). DISMISSED läuft — wie in der
    # bestehenden "Kürzlich erledigt"-Sektion auch — unter "Erledigt" mit,
    # keine eigene Option (kein zusätzlicher UI-Zustand ohne Bedarf).
    STATUS_FILTER_CHOICES = [
        ("open", _("Offen")),
        ("in_progress", _("In Bearbeitung")),
        ("done", _("Erledigt")),
    ]

    # Refs #1570: Zeitraum für die gezielte Erledigt-Ansicht (``status=done``).
    # Wirkt NUR dort — die passive "Kürzlich erledigt"-Vorschau (#1149) bleibt
    # unabhängig davon fest auf die letzten 7 Tage begrenzt, damit die normale
    # Aufgabenübersicht nicht dauerhaft mit allen erledigten Aufgaben
    # überladen wird.
    DONE_PERIOD_CHOICES = [
        ("7", _("Letzte 7 Tage")),
        ("30", _("Letzte 30 Tage")),
        ("all", _("Alle")),
    ]
    DEFAULT_DONE_PERIOD = "7"

    def _apply_filters(self, qs, request):
        """Evaluate GET parameters and filter the queryset.

        Refs #1568: delegiert an das modulweite ``apply_workitem_filters`` —
        die eigentliche Filterlogik lebt dort, damit der OOB-Insert von
        ``WorkItemStatusUpdateView`` sie unverändert wiederverwenden kann.
        """
        assigned_to = request.GET.get("assigned_to")
        # Refs #1145: Der Default-Sentinel ist kein Personen-/me-Filter, sondern
        # benennt nur die breitere Default-Sicht — die eigentliche Eingrenzung
        # passiert in ``_apply_default_scope`` (get). Hier wie "kein Filter"
        # behandeln, damit ``mine_team`` nicht als (ungültiger) Personen-Wert in
        # die Query gerät.
        if assigned_to == self.DEFAULT_ASSIGNED_SCOPE:
            assigned_to = ""

        return apply_workitem_filters(
            qs,
            item_type=request.GET.get("item_type", ""),
            priority=request.GET.get("priority", ""),
            assigned_to=assigned_to or "",
            due=request.GET.get("due", ""),
            user=request.user,
        )

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

        # Refs #1570: Gezielter Statusfilter — steuert, welche der Sektionen
        # das Template rendert (nicht welche Query läuft: alle drei Basis-
        # Sektionen bleiben wie bisher berechnet, siehe unten). Ungültige
        # Werte fallen auf "" (alle Sektionen) zurück.
        raw_status_filter = request.GET.get("status", "")
        valid_status_values = {c[0] for c in self.STATUS_FILTER_CHOICES}
        status_filter = raw_status_filter if raw_status_filter in valid_status_values else ""

        raw_done_period = request.GET.get("done_period", self.DEFAULT_DONE_PERIOD)
        valid_done_period_values = {c[0] for c in self.DONE_PERIOD_CHOICES}
        done_period = raw_done_period if raw_done_period in valid_done_period_values else self.DEFAULT_DONE_PERIOD

        show_open = status_filter in ("", "open")
        show_in_progress = status_filter in ("", "in_progress")
        # Die passive "Kürzlich erledigt"-Vorschau (7 Tage, Preview/Extra-Split)
        # bleibt der Default-Zustand; sobald "Erledigt" gezielt gewählt ist,
        # tritt die volle, zeitraum-parametrisierte Sektion an ihre Stelle.
        show_done_widget = status_filter == ""
        show_done_full = status_filter == "done"

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
            return apply_workitem_default_scope(qs, user, default_scope=default_scope)

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

        # Refs #1149: "Kürzlich erledigt" ist Rückblick, kein Arbeitsbereich.
        # Standardmäßig nur die letzten N (innerhalb der 7 Tage) zeigen; der
        # Rest wird im Template über eine eigene, eindeutig auf "letzte 7 Tage"
        # beschriftete Aufklapp-Aktion eingeblendet. Die Aufteilung passiert
        # hier (statt per Template-|slice), damit sie testbar bleibt und das
        # Template dumm bleibt.
        done_items_preview = done_items[:WORKITEM_RECENT_DONE_PREVIEW]
        done_items_extra = done_items[WORKITEM_RECENT_DONE_PREVIEW:]

        # Refs #1570: Gezielte Erledigt-Ansicht (``status=done``) — löst die
        # feste 7-Tage-Grenze der passiven Vorschau über ``done_period`` auf
        # (7/30 Tage/alle) und zeigt die volle (weiterhin gecappte) Liste ohne
        # Preview/Extra-Split, da die Nutzer:in hier bewusst "Erledigt"
        # gewählt hat. Nur berechnet, wenn die Sektion auch gerendert wird —
        # unnötige Zusatzabfrage bei den übrigen Statusfiltern vermeiden.
        done_full_items = []
        done_full_has_more = False
        if show_done_full:
            done_full_qs = base_qs.filter(status__in=[WorkItem.Status.DONE, WorkItem.Status.DISMISSED])
            if done_period != "all":
                period_start = timezone.now() - timedelta(days=int(done_period))
                done_full_qs = done_full_qs.filter(updated_at__gte=period_start)
            done_full_qs = _apply_default_scope(done_full_qs)
            done_full_items = list(done_full_qs[: cap + 1])
            done_full_has_more = len(done_full_items) > cap
            if done_full_has_more:
                done_full_items = done_full_items[:cap]

        done_period_label = dict(self.DONE_PERIOD_CHOICES).get(done_period, "")

        facility_users = User.objects.filter(facility=facility).order_by("last_name", "first_name", "username")

        # Refs #1148 (Folge-Feedback): Nach einer wegen fehlender Berechtigung
        # abgelehnten Bulk-Aktion reicht der Bulk-Endpoint die PKs der
        # blockierenden Items als wiederholten ``forbidden``-Parameter zurueck.
        # Diese Items markiert/hebt das Template wieder hervor, damit die zuvor
        # getroffene Auswahl nicht verloren geht und die Warnmeldung mit der
        # konkreten Aufgabe verbunden bleibt. Strikt auf die *tatsaechlich
        # gerenderten* PKs beschraenken: so wird nichts Unsichtbares vorab
        # ausgewaehlt und kein beliebiger fremder Wert in den DOM gespiegelt.
        visible_pks = {str(wi.pk) for wi in (*open_items, *in_progress_items, *done_items, *done_full_items)}
        forbidden_ids = {pk for pk in request.GET.getlist("forbidden") if pk in visible_pks}

        context = {
            "open_items": open_items,
            "open_has_more": open_has_more,
            "in_progress_items": in_progress_items,
            "in_progress_has_more": in_progress_has_more,
            "done_items": done_items,
            "done_has_more": done_has_more,
            "done_items_preview": done_items_preview,
            "done_items_extra": done_items_extra,
            "recent_done_preview": WORKITEM_RECENT_DONE_PREVIEW,
            "done_full_items": done_full_items,
            "done_full_has_more": done_full_has_more,
            "done_period_label": done_period_label,
            "show_open": show_open,
            "show_in_progress": show_in_progress,
            "show_done_widget": show_done_widget,
            "show_done_full": show_done_full,
            "inbox_list_limit": cap,
            "item_type_choices": WorkItem.ItemType.choices,
            "priority_choices": WorkItem.Priority.choices,
            "status_choices": WorkItem.Status.choices,
            "due_filter_choices": self.DUE_FILTER_CHOICES,
            "status_filter_choices": self.STATUS_FILTER_CHOICES,
            "done_period_choices": self.DONE_PERIOD_CHOICES,
            "facility_users": facility_users,
            "selected_item_type": request.GET.get("item_type", ""),
            "selected_priority": request.GET.get("priority", ""),
            "selected_assigned_to": selected_assigned_to,
            "default_assigned_scope": self.DEFAULT_ASSIGNED_SCOPE,
            "selected_due": request.GET.get("due", ""),
            "selected_status": status_filter,
            "selected_done_period": done_period,
            "forbidden_ids": forbidden_ids,
        }

        return self.render_htmx_or_full(context)


class WorkItemDetailView(AssistantOrAboveRequiredMixin, View):
    """WorkItem detail view."""

    def get(self, request, pk):
        workitem = get_scoped_object(
            WorkItem.objects.select_related("client", "created_by", "assigned_to"),
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
