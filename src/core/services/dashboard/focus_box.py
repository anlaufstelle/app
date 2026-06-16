"""Team-Fokusbox für die Zeitstrom-Sidebar (Refs #1128).

Die Sidebar im Zeitstrom ist kein persönlicher Arbeitsbereich, sondern eine
kompakte Team-Fokusbox: Sie macht auf einrichtungsbezogene Aufgaben mit
aktuellem Handlungsbedarf aufmerksam — unabhängig davon, welcher Person eine
Aufgabe zugewiesen ist.

Aufgaben werden nach Handlungsdruck gruppiert. Jede Aufgabe erscheint nur in
*einer* Gruppe (die höchste zutreffende Druckstufe), damit nichts doppelt
gezählt wird:

1. ``overdue``      — offen/laufend, Frist liegt in der Vergangenheit.
2. ``today``        — Frist ist heute (und nicht bereits überfällig).
3. ``priority``     — dringend oder wichtig, ohne akuten Frist-Druck.
4. ``in_progress``  — bereits in Bearbeitung, ohne der obigen Stufen.

Offene Normal-Aufgaben ohne Frist erzeugen keinen Handlungsdruck und tauchen
deshalb nicht in der Fokusbox auf — sie bleiben über die vollständige
Aufgabenübersicht (mit Filtern wie ``Mir zugewiesen``) erreichbar. Sie zählen
aber zu ``total_open_count``, damit der Transparenz-Hinweis ehrlich bleibt.

Sortierung innerhalb der Gruppen: nach Fälligkeit, dann Priorität, dann
Erstellzeit — analog zur Inbox (:class:`core.views.workitems.WorkItemInboxView`).
"""

from __future__ import annotations

from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import WorkItem

# Maximal angezeigte Aufgaben in der kompakten Sidebar. Die Box soll auf
# Handlungsbedarf aufmerksam machen, nicht Vollständigkeit abbilden.
FOCUS_BOX_LIMIT = 8

_OPEN_STATUSES = [WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS]

# Reihenfolge + Beschriftung der Gruppen (absteigender Handlungsdruck).
_GROUP_LABELS = [
    ("overdue", _("Überfällig")),
    ("today", _("Heute fällig")),
    ("priority", _("Dringend · Wichtig")),
    ("in_progress", _("In Bearbeitung")),
]


def _bucket(workitem, today) -> str:
    """Höchste zutreffende Druckstufe für ein einzelnes WorkItem."""
    if workitem.due_date is not None and workitem.due_date < today:
        return "overdue"
    if workitem.due_date == today:
        return "today"
    if workitem.priority in (WorkItem.Priority.URGENT, WorkItem.Priority.IMPORTANT):
        return "priority"
    if workitem.status == WorkItem.Status.IN_PROGRESS:
        return "in_progress"
    return ""


def build_focus_box(facility) -> dict:
    """Gruppierte Team-Aufgaben mit Handlungsbedarf für die Sidebar.

    Liefert ein Dict:

    - ``groups``           — Liste nicht-leerer Gruppen ``{"key", "label", "items"}``
                             in Reihenfolge absteigenden Handlungsdrucks.
    - ``shown_count``      — Anzahl tatsächlich angezeigter Aufgaben (≤ Limit).
    - ``total_open_count`` — alle offenen/laufenden Aufgaben der Einrichtung.
    - ``has_more``         — True, wenn mehr offene Aufgaben existieren als gezeigt.
    """
    today = timezone.localdate()

    qs = (
        WorkItem.objects.filter(facility=facility, status__in=_OPEN_STATUSES)
        .select_related("client", "assigned_to")
        .annotate(
            priority_order=Case(
                When(priority=WorkItem.Priority.URGENT, then=Value(0)),
                When(priority=WorkItem.Priority.IMPORTANT, then=Value(1)),
                When(priority=WorkItem.Priority.NORMAL, then=Value(2)),
                output_field=IntegerField(),
            ),
            due_date_bucket=Case(
                When(due_date__lt=today, then=Value(0)),
                When(due_date=today, then=Value(1)),
                When(due_date__gt=today, then=Value(2)),
                When(due_date__isnull=True, then=Value(9)),
                output_field=IntegerField(),
            ),
        )
        # Fälligkeit zuerst (überfällig oben), dann Priorität, dann Erstellzeit.
        .order_by("due_date_bucket", "due_date", "priority_order", "-created_at")
    )

    total_open_count = qs.count()

    buckets: dict[str, list] = {key: [] for key, _label in _GROUP_LABELS}
    shown = 0
    for workitem in qs.iterator():
        if shown >= FOCUS_BOX_LIMIT:
            break
        key = _bucket(workitem, today)
        if not key:
            continue
        buckets[key].append(workitem)
        shown += 1

    groups = [{"key": key, "label": label, "items": buckets[key]} for key, label in _GROUP_LABELS if buckets[key]]

    return {
        "groups": groups,
        "shown_count": shown,
        "total_open_count": total_open_count,
        "has_more": total_open_count > shown,
    }
