"""Service layer for WorkItem-related business logic.

**Kanonische Sperr-Reihenfolge: Client VOR WorkItem** (Refs #2045,
Cross-Review-Runde 2). Jeder Pfad, der beide Zeilenarten sperrt, nimmt sie in
dieser Reihenfolge — der Merge-Pfad (``services/client/merge.py``) tat das
bereits (``_locked_clients`` -> WorkItem-Umschreibung), die Aufgaben-Pfade
sperrten umgekehrt. Aus beidem zusammen entstand ein realer Deadlock-Zyklus:
Bulk hielt ein WorkItem und wartete auf dessen Client, waehrend der Merge den
Client hielt und beim WorkItem wartete.

Praktische Folge fuer diese Datei: Wo der Personenbezug erst aus der
WorkItem-Zeile gelesen werden muss, geschieht das **ungesperrt** (nur zur
Bestimmung der Lock-Reihenfolge); danach wird der Client gesperrt, danach das
WorkItem, und ein Auseinanderlaufen beider Lesestaende wird als
Nebenlaeufigkeits-Konflikt abgewiesen statt durch Umdrehen der Reihenfolge
aufgeloest.
"""

import calendar
import json
import logging
import uuid
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, IntegerField, Q, QuerySet, Value, When
from django.http import QueryDict
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext_lazy as _

from core.constants import WORKITEM_INBOX_CAP, WORKITEM_RECENT_DONE_PREVIEW
from core.models import Activity, AuditLog, WorkItem
from core.models.user import User
from core.services.audit import apply_allowlisted_update, audit_event, check_update_allowlist
from core.services.case.state_contract import _raise_if_clients_revoked
from core.services.dashboard import log_activity

logger = logging.getLogger(__name__)

#: Die Aufgabe liegt im Papierkorb. Eine Aenderung waere eine Aenderung an
#: einem geloeschten Datensatz; der Weg zurueck ist das Wiederherstellen.
WORKITEM_DELETED_MESSAGE = _("Diese Aufgabe wurde gelöscht — sie kann nicht mehr geändert werden.")


class WorkItemBatchForbiddenError(PermissionDenied):
    """At the locked rows, the actor may not change some work items of a batch.

    Carries the refused primary keys, so the caller can answer exactly like its
    own ownership check before the service (same message, same marked
    selection).
    """

    def __init__(self, workitem_ids):
        super().__init__()
        self.workitem_ids = list(workitem_ids)


def _assert_actor_in_facility(user, facility_id) -> None:
    """Die handelnde Person muss der Einrichtung der Aufgabe angehoeren.

    Ueber HTTP scopen die Views bereits, in Produktion traegt zusaetzlich die
    Zeilensicherheit — der Dienst zieht die Grenze trotzdem selbst, damit ein
    direkter Aufruf sie nicht umgeht. Fail-closed fuer eine Person ohne
    Einrichtung. Der Vergleich kostet keine Abfrage und steht deshalb vor der
    ersten Lesung und Sperre.
    """
    actor_facility_id = getattr(user, "facility_id", None)
    if actor_facility_id is None or actor_facility_id != facility_id:
        raise ValidationError(_("Aufgabe gehört zu einer anderen Einrichtung."))


# Refs #1804 (Konzept §3.6, Z1/Z2/Z4): „Diese Woche" war ein Kalenderwort ueber
# einem rollierenden Fenster — und das Fenster war acht Tage lang
# (``heute … heute+7``). Im selben Screen trug das Faelligkeits-Badge einer
# Aufgabe „Naechste Woche (Mo)" (ISO-kalendarisch, ``utils/dates.py``), waehrend
# der Filter sie unter „Diese Woche" zeigte. Der Widerspruch war nicht
# kosmetisch, sondern arithmetisch.
#
# Kanonisch heisst der Filter jetzt ``next7`` („Naechste 7 Tage",
# ``heute … heute+6``). ``week`` bleibt DAUERHAFT ein stiller Alias (Entscheid
# F5): ``filter-persistence.js`` legt Select-Werte im ``sessionStorage`` ab, und
# ein reiner Rename liesse gespeicherte ``week``-Werte in ein Select ohne
# passende Option zurueckfliessen — die Auswahl fiele stumm auf „Alle" zurueck.
DUE_NEXT7 = "next7"
DUE_FILTER_ALIASES = {"week": DUE_NEXT7}
NEXT7_WINDOW_DAYS = 7
DEFAULT_ASSIGNED_SCOPE = "mine_team"

DUE_FILTER_CHOICES = [
    ("overdue", _("Überfällig")),
    ("today", _("Heute")),
    (DUE_NEXT7, _("Nächste 7 Tage")),
    ("none", _("Ohne Frist")),
]

STATUS_FILTER_CHOICES = [
    ("open", _("Offen")),
    ("in_progress", _("In Bearbeitung")),
    ("done", _("Erledigt")),
]

DONE_PERIOD_CHOICES = [
    ("7", _("Letzte 7 Tage")),
    ("30", _("Letzte 30 Tage")),
    ("all", _("Alle")),
]
DEFAULT_DONE_PERIOD = "7"


def normalize_due_filter(value: str) -> str:
    """Kanonischer Faelligkeits-Schluessel; Unbekanntes bleibt unveraendert.

    Bewusst kein Fallback auf ``""``: die Gueltigkeitspruefung gegen
    ``DUE_FILTER_CHOICES`` liegt in ``apply_workitem_filters`` und soll dort
    bleiben (ein ungueltiger Wert wird ignoriert, nicht uebersetzt).
    """
    return DUE_FILTER_ALIASES.get(value, value)


def apply_workitem_filters(qs, *, item_type="", priority="", assigned_to="", due="", user=None):
    """Wendet die Inbox-Filter (Typ/Priorität/Zuweisung/Fälligkeit) auf ``qs`` an.

    Refs #1568: aus ``WorkItemInboxView._apply_filters`` extrahiert, damit
    ``WorkItemStatusUpdateView`` beim OOB-Insert in "Kürzlich erledigt"
    (nach dem Markieren als erledigt/verworfen) dieselbe Filterlogik
    wiederverwenden kann statt sie zu duplizieren — die Issue-Vorgabe
    verlangt ausdrücklich "keine neue Filterlogik erfinden".

    ``assigned_to`` ist hier bereits aufgelöst: der Default-Sentinel
    ``DEFAULT_ASSIGNED_SCOPE`` muss der Aufrufer vorher auf ``""``
    normalisieren (siehe ``apply_workitem_default_scope``); "me" wird hier
    noch in die User-ID übersetzt.
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
        due = normalize_due_filter(due)
        valid_due_values = {choice[0] for choice in DUE_FILTER_CHOICES}
        if due in valid_due_values:
            if due == "overdue":
                qs = qs.filter(
                    due_date__lt=today,
                    status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
                )
            elif due == "today":
                qs = qs.filter(due_date=today)
            elif due == DUE_NEXT7:
                # Z4: beide Enden inklusiv — sieben Tage ab heute enden am
                # sechsten Folgetag, nicht am siebten.
                qs = qs.filter(
                    due_date__gte=today,
                    due_date__lte=today + timedelta(days=NEXT7_WINDOW_DAYS - 1),
                )
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


def _capped(qs: QuerySet[WorkItem], cap: int) -> tuple[list[WorkItem], bool]:
    """Evaluiert höchstens ``cap + 1`` Einträge und markiert den Überlauf."""
    items = list(qs[: cap + 1])
    has_more = len(items) > cap
    return items[:cap] if has_more else items, has_more


def build_inbox_sections(facility, user, params: QueryDict) -> dict[str, object]:
    """Baut Querysets, Sektionen und Filterkontext der WorkItem-Inbox."""
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

    raw_assigned_to = params.get("assigned_to")
    assigned_to = "" if raw_assigned_to == DEFAULT_ASSIGNED_SCOPE else raw_assigned_to or ""
    base_qs = apply_workitem_filters(
        base_qs,
        item_type=params.get("item_type", ""),
        priority=params.get("priority", ""),
        assigned_to=assigned_to,
        due=normalize_due_filter(params.get("due", "")),
        user=user,
    )

    raw_status_filter = params.get("status", "")
    valid_status_values = {choice[0] for choice in STATUS_FILTER_CHOICES}
    status_filter = raw_status_filter if raw_status_filter in valid_status_values else ""

    raw_done_period = params.get("done_period", DEFAULT_DONE_PERIOD)
    valid_done_period_values = {choice[0] for choice in DONE_PERIOD_CHOICES}
    done_period = raw_done_period if raw_done_period in valid_done_period_values else DEFAULT_DONE_PERIOD

    show_open = status_filter in ("", "open")
    show_in_progress = status_filter in ("", "in_progress")
    show_done_widget = status_filter == ""
    show_done_full = status_filter == "done"
    cap = WORKITEM_INBOX_CAP

    param_absent = raw_assigned_to is None
    default_scope = param_absent or raw_assigned_to == DEFAULT_ASSIGNED_SCOPE
    selected_assigned_to = DEFAULT_ASSIGNED_SCOPE if param_absent else raw_assigned_to

    def _apply_default_scope(qs: QuerySet[WorkItem]) -> QuerySet[WorkItem]:
        return apply_workitem_default_scope(qs, user, default_scope=default_scope)

    open_items, open_has_more = _capped(
        _apply_default_scope(base_qs.filter(status=WorkItem.Status.OPEN)),
        cap,
    )
    in_progress_items, in_progress_has_more = _capped(
        _apply_default_scope(base_qs.filter(status=WorkItem.Status.IN_PROGRESS)),
        cap,
    )

    seven_days_ago = timezone.now() - timedelta(days=7)
    done_items, done_has_more = _capped(
        _apply_default_scope(
            base_qs.filter(
                status__in=[WorkItem.Status.DONE, WorkItem.Status.DISMISSED],
                updated_at__gte=seven_days_ago,
            )
        ),
        cap,
    )
    done_items_preview = done_items[:WORKITEM_RECENT_DONE_PREVIEW]
    done_items_extra = done_items[WORKITEM_RECENT_DONE_PREVIEW:]

    done_full_items: list[WorkItem] = []
    done_full_has_more = False
    if show_done_full:
        done_full_qs = base_qs.filter(status__in=[WorkItem.Status.DONE, WorkItem.Status.DISMISSED])
        if done_period != "all":
            period_start = timezone.now() - timedelta(days=int(done_period))
            done_full_qs = done_full_qs.filter(updated_at__gte=period_start)
        done_full_items, done_full_has_more = _capped(_apply_default_scope(done_full_qs), cap)

    done_period_label = dict(DONE_PERIOD_CHOICES).get(done_period, "")
    has_active_filters = bool(
        params.get("item_type", "")
        or params.get("priority", "")
        or params.get("due", "")
        or status_filter
        or not default_scope
    )
    facility_users = User.objects.filter(facility=facility).order_by("last_name", "first_name", "username")

    visible_pks = {str(wi.pk) for wi in (*open_items, *in_progress_items, *done_items, *done_full_items)}
    forbidden_ids = {pk for pk in params.getlist("forbidden") if pk in visible_pks}

    return {
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
        "due_filter_choices": DUE_FILTER_CHOICES,
        "status_filter_choices": STATUS_FILTER_CHOICES,
        "done_period_choices": DONE_PERIOD_CHOICES,
        "facility_users": facility_users,
        "selected_item_type": params.get("item_type", ""),
        "selected_priority": params.get("priority", ""),
        "selected_assigned_to": selected_assigned_to,
        "default_assigned_scope": DEFAULT_ASSIGNED_SCOPE,
        "selected_due": normalize_due_filter(params.get("due", "")),
        "selected_status": status_filter,
        "selected_done_period": done_period,
        "has_active_filters": has_active_filters,
        "forbidden_ids": forbidden_ids,
    }


def _add_months(source: date, months: int) -> date:
    """Add ``months`` to ``source`` using calendar-aware month arithmetic.

    Behaviour matches ``dateutil.relativedelta(months=+n)``:
    the day is clamped to the last valid day of the target month
    (e.g. 31.01. + 1 month = 28./29.02.).
    """
    month_index = source.month - 1 + months
    target_year = source.year + month_index // 12
    target_month = month_index % 12 + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    target_day = min(source.day, last_day)
    return date(target_year, target_month, target_day)


def _next_due_date(current: date, recurrence: str) -> date | None:
    """Compute the next due date for a recurrence pattern.

    Returns ``None`` when no duplicate should be scheduled.
    """
    if recurrence == WorkItem.Recurrence.WEEKLY:
        return current + timedelta(days=7)
    if recurrence == WorkItem.Recurrence.MONTHLY:
        return _add_months(current, 1)
    if recurrence == WorkItem.Recurrence.QUARTERLY:
        return _add_months(current, 3)
    if recurrence == WorkItem.Recurrence.YEARLY:
        return _add_months(current, 12)
    return None


#: Rueckgabe von :func:`_locked_client_id`, wenn die Aufgabenzeile in der
#: Einrichtung der Aufgabe fehlt. Bewusst verschieden von ``None`` (= Aufgabe
#: ohne Person): eine fremde personenlose Aufgabe darf nicht als „unveraendert
#: personenlos" weiterlaufen (Refs #2377).
WORKITEM_ROW_MISSING = object()


def _client_pk(value):
    """Primary key of a ``client`` argument in the form the database returns.

    Accepts an instance, a UUID or its text form (offline replay, JSON);
    ``None`` stays ``None``. Without this, a text key compares unequal to the
    stored UUID and cannot be sorted together with it.
    """
    pk = getattr(value, "pk", value)
    if pk is None or isinstance(pk, uuid.UUID):
        return pk
    return uuid.UUID(str(pk))


def _locked_client_id(workitem):
    """Frischer ``client_id``-Stand der Zeile **unter Row-Lock** (Refs #1786).

    Muster von ``changed_fields_vs_db``: ``_base_manager`` (kein Custom-Manager
    verfaelscht den Low-Level-Lookup), ``values_list`` (kein Nachladen des
    verwandten Objekts) und ``select_for_update`` in der Transaktionsklammer des
    Callers — der Lock haelt bis zum Commit, der spaetere Diff-Read greift also
    auf dieselbe gesperrte Zeile.

    ``None`` heisst „keine Person zugeordnet".

    Refs #2377: gesperrt wird nur in der Einrichtung der Aufgabe. Fehlt die
    Zeile dort — nicht (mehr) vorhanden oder einer anderen Einrichtung —,
    kommt :data:`WORKITEM_ROW_MISSING` zurueck, nie ``None``: sonst liefe eine
    fremde personenlose Aufgabe als „personenlos, unveraendert" weiter bis zur
    Schreibung.
    """
    row = (
        type(workitem)
        ._base_manager.select_for_update()
        .filter(pk=workitem.pk, facility_id=workitem.facility_id)
        .values_list("pk", "client_id")
        .first()
    )
    if row is None:
        return WORKITEM_ROW_MISSING
    return row[1]


# Spalten, die das Bearbeiten-Formular zeigt oder mitschickt (Refs #2331) —
# ``attname``-Form, damit Instanz und ``values()`` dieselben Werte liefern.
# ``status``/``completed_at`` stehen bewusst NICHT darin: sie aendert nur der
# Status-Pfad, und ein paralleler Statuswechsel ist kein Konflikt mit dem
# Formular.
WORKITEM_FORM_STATE_FIELDS = (
    "item_type",
    "title",
    "description",
    "priority",
    "due_date",
    "remind_at",
    "recurrence",
    "assigned_to_id",
    "client_id",
)


def _form_state_token_from_values(pk, values):
    payload = json.dumps(
        {field: values.get(field) for field in WORKITEM_FORM_STATE_FIELDS},
        sort_keys=True,
        default=str,
    )
    return salted_hmac("core.workitem.form_state", f"{pk}|{payload}", algorithm="sha256").hexdigest()


def workitem_form_state_token(workitem):
    """Fingerabdruck der Formularfelder zum Renderzeitpunkt (Refs #2331).

    Das Bearbeiten-Formular traegt ihn versteckt mit. Ein HMAC statt der
    Klartextwerte: das Formular soll keinen zweiten, manipulierbaren Satz
    Feldwerte mitschicken, und der Wert verraet nichts ueber den Inhalt.
    Wer ihn faelscht, erreicht hoechstens, was ein Neuladen mit anschliessendem
    Speichern ohnehin erlaubt — Status, Sperren und Personenbezug laufen
    unabhaengig davon durch ihre eigenen Pruefungen.
    """
    values = {field: getattr(workitem, field) for field in WORKITEM_FORM_STATE_FIELDS}
    return _form_state_token_from_values(workitem.pk, values)


def _form_state_unchanged(workitem, expected_form_state):
    """``True``, wenn seit dem Rendern kein Formularfeld geaendert wurde.

    Liest die Zeile unter Row-Lock (``update_workitem`` haelt ihn bereits;
    ``select_for_update`` ist innerhalb derselben Transaktion ein No-op).
    Wie :func:`_locked_client_id` nur in der Einrichtung der Aufgabe (Refs #2377).
    """
    if not expected_form_state or not isinstance(expected_form_state, str):
        return False
    current = (
        type(workitem)
        ._base_manager.select_for_update()
        .filter(pk=workitem.pk, facility_id=workitem.facility_id)
        .values(*WORKITEM_FORM_STATE_FIELDS)
        .first()
    )
    if current is None:
        return False
    return constant_time_compare(_form_state_token_from_values(workitem.pk, current), expected_form_state)


def _log_workitem_update(workitem, user, changed_fields):
    """Write a single AuditLog entry for a WorkItem bulk field update."""
    audit_event(
        AuditLog.Action.WORKITEM_UPDATE,
        user=user,
        facility=workitem.facility,
        target_obj=workitem,
        detail={"changed_fields": list(changed_fields), "bulk": True},
    )


def _apply_status_transition(workitem, new_status, user, *, auto_assign: bool) -> bool:
    """Mutate ``workitem`` fields for a status transition.

    Refs #906: Gemeinsame Kern-Logik fuer Single- (``update_workitem_status``)
    und Bulk-Pfad (``bulk_update_workitem_status``). Verantwortlich nur fuer:
    Statuswechsel, optionales Auto-Assign, ``completed_at``-Pflege. **Kein**
    ``save()`` — der Caller entscheidet ueber ``update_fields`` und
    ``select_for_update``-Kontext.

    - ``auto_assign=True`` (Single-Pfad): IN_PROGRESS ohne Assignee → User
      wird zugewiesen. ``auto_assign=False`` (Bulk-Pfad): keine Zuweisung.
    - DONE/DISMISSED setzt ``completed_at = now``.
    - Jede andere Transition setzt ``completed_at = None`` (Reopen).

    Returns ``True``, wenn der Status sich tatsaechlich aendert. ``False``
    fuer den Idempotenz-Guard (gleicher Status; Caller kann frueh
    zurueckkehren ohne ``save()``).
    """
    if workitem.status == new_status:
        return False
    workitem.status = new_status
    if auto_assign and new_status == WorkItem.Status.IN_PROGRESS and not workitem.assigned_to:
        workitem.assigned_to = user
    if new_status in (WorkItem.Status.DONE, WorkItem.Status.DISMISSED):
        workitem.completed_at = timezone.now()
    elif workitem.completed_at:
        workitem.completed_at = None
    return True


def _maybe_duplicate_recurring(workitem, user, new_status) -> None:
    """Falls Statuswechsel auf DONE + Recurrence != NONE: Folgeaufgabe anlegen.

    Refs #906: Beide Pfade (Single + Bulk) brauchen dieselbe Bedingung —
    Idempotenz wird in ``duplicate_recurring_workitem`` selbst durchgesetzt
    (``recurrence_duplicated_at``-Marker, Refs #596).
    """
    if new_status != WorkItem.Status.DONE:
        return
    if not workitem.recurrence or workitem.recurrence == WorkItem.Recurrence.NONE:
        return
    duplicate_recurring_workitem(workitem, user)


def _locked_workitems(workitems, user):
    """Refs #1022 (B2): Items innerhalb der Transaktion per
    ``select_for_update`` neu laden+locken — deterministisch nach ``pk``
    geordnet zur Deadlock-Vermeidung —, damit die Bulk-Pfade auf frischem
    DB-Stand operieren statt auf den (potenziell veralteten) aus der View
    geladenen Instanzen. Spiegelt den Single-Pfad ``update_workitem_status``
    (Refs #129/#733). Facility-/Tenant-Isolation greift ueber RLS (GUC im
    Request-Kontext) und die View-Vorfilterung.

    Einrichtungsgrenze: jede Aufgabe des Stapels muss in der Einrichtung der
    handelnden Person liegen. Geprueft wird vor der ersten Lesung und Sperre,
    fail-closed fuer den ganzen Stapel; beide Lesungen tragen die Einrichtung
    zusaetzlich, damit eine verfaelschte Instanz nichts Fremdes erreicht.

    Zustandsvertrag: nach den Personensperren der Widerrufszustand der
    gesperrten Personen (Papierkorb, deaktiviert, zusammengefuehrt), nach der
    Aufgabensperre der Loeschzustand der gesperrten Aufgaben. Beides weist wie
    das Art.-18-Gate den ganzen Stapel ab statt ihn teilweise auszufuehren.

    Permission: after the deletion state, ``can_user_mutate_workitem`` is
    decided at every locked row; any refused item raises
    :class:`WorkItemBatchForbiddenError` for the whole batch.
    """
    from core.services.client.restriction import assert_processing_allowed

    for workitem in workitems:
        _assert_actor_in_facility(user, workitem.facility_id)
    facility_id = getattr(user, "facility_id", None)
    pks = [w.pk for w in workitems]
    # KANONISCHE SPERR-REIHENFOLGE: Client VOR WorkItem (Modul-Docstring).
    # Die Personenbezuege werden dafuer UNGESPERRT vorgelesen — sie bestimmen
    # nur, welche Client-Zeilen zu sperren sind. Dedupliziert (eine Sperre je
    # Person statt einer je Aufgabe) und nach PK sortiert, damit zwei
    # gleichzeitige Stapel mit ueberlappender Personenmenge dieselbe
    # Reihenfolge nehmen und einander nicht ueber Kreuz blockieren.
    client_ids = sorted(
        {
            cid
            for cid in WorkItem.objects.filter(pk__in=pks, facility_id=facility_id).values_list("client_id", flat=True)
            if cid is not None
        }
    )
    # Art.-18-Gate (Refs #2045) am gemeinsamen Chokepoint der drei Bulk-Pfade
    # (Status/Prioritaet/Zuweisung) — einmal hier statt dreimal kopiert.
    # Fail-closed fuer den ganzen Stapel: ist auch nur eine Aufgabe einer
    # eingeschraenkten Person dabei, wird die Sammelaktion abgewiesen statt
    # teilweise ausgefuehrt — ein Teil-Erfolg waere fuer die ausloesende Person
    # nicht nachvollziehbar. Die Sperre haelt bis zum Commit; die spaeter
    # gelesenen ``client_id`` koennen sich also nicht mehr aendern.
    for client_id in client_ids:
        assert_processing_allowed(client_id, lock=True)
    # Widerrufszustand auf den eben gesperrten Personenzeilen (ohne zweite
    # Sperre); ohne Personenbezug im Stapel keine Abfrage.
    _raise_if_clients_revoked(client_ids, facility_id)

    # Creator and assignee are joined into the same statement, so the
    # permission check below costs no query per item; only the work item rows
    # are locked (``of=("self",)``), never the user rows.
    locked = list(
        WorkItem.objects.select_for_update(of=("self",))
        .select_related("created_by", "assigned_to")
        .filter(pk__in=pks, facility_id=facility_id)
        .order_by("pk")
    )
    # Eine inzwischen geloeschte Aufgabe wird nicht mehr geaendert — gelesen
    # an der gesperrten Zeile, der Manager blendet geloeschte nicht aus.
    if any(item.is_deleted for item in locked):
        raise ValidationError(WORKITEM_DELETED_MESSAGE)
    # Permission at the locked rows: the view checks ownership beforehand on
    # rows read without a lock. A work item reassigned or re-owned in between
    # refuses the whole batch before the first write or audit row.
    forbidden_ids = [item.pk for item in locked if not can_user_mutate_workitem(user, item)]
    if forbidden_ids:
        raise WorkItemBatchForbiddenError(forbidden_ids)

    # REVALIDIERUNG (Re-Review 2): Zwischen dem ungesperrten Vorab-Lesen und
    # dem Erhalt der WorkItem-Sperre kann eine Zuordnung gewechselt haben — die
    # Sperre kann Wartezeit bedeuten. Eine Aufgabe, die jetzt auf eine Person
    # zeigt, die NICHT in der gesperrten und gegateten Menge liegt, ist
    # ungeprueft; sie wird uebersprungen statt bearbeitet. Fail-closed in die
    # richtige Richtung: lieber eine Aufgabe zu wenig im Stapel als eine
    # ungeprueft geaenderte an einer eingeschraenkten Person.
    #
    # ``client_id is None`` bleibt zulaessig: ohne Personenbezug gibt es nichts
    # einzuschraenken.
    checked = [item for item in locked if item.client_id is None or item.client_id in set(client_ids)]
    skipped = len(locked) - len(checked)
    if skipped:
        logger.warning(
            "bulk_workitem_reassigned_during_lock",
            extra={"skipped": skipped, "requested": len(locked)},
        )
    return checked


@transaction.atomic
def create_workitem(facility, user, *, client=None, **data):
    """Create a work item with activity and audit logging.

    Art.-18-Gate (Refs #2045): eine neue Aufgabe zu einer eingeschraenkten
    Person ist neue inhaltliche Verarbeitung.

    Eine Person im Papierkorb, deaktiviert oder zusammengefuehrt nimmt keine
    neue Aufgabe an — entschieden an der eben gesperrten Personenzeile, nicht
    am Stand der Formularpruefung.
    """
    from core.services.client.restriction import assert_processing_allowed

    facility_id = getattr(facility, "pk", facility)
    _assert_actor_in_facility(user, facility_id)
    assert_processing_allowed(client, lock=True)
    if client is not None:
        _raise_if_clients_revoked([getattr(client, "pk", client)], facility_id)
    workitem = WorkItem(facility=facility, created_by=user, client=client, **data)
    workitem.save()
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=workitem,
        action=Activity.Action.WORKITEM_CREATED,
    )
    audit_event(
        AuditLog.Action.WORKITEM_CREATE,
        user=user,
        facility=facility,
        target_obj=workitem,
    )
    return workitem


@transaction.atomic
def update_workitem(
    workitem,
    user,
    *,
    expected_updated_at=None,
    require_version_token=False,
    expected_form_state=None,
    **fields,
):
    """Update a work item with activity logging and audit logging.

    Accepts allowed fields (item_type, title, description, priority, due_date,
    remind_at, recurrence, assigned_to, client) and persists them — die Liste im
    Code ist die Wahrheit, dieser Satz ihr Spiegel. Writes an AuditLog entry with
    the names of changed fields (no PII values).

    ``expected_updated_at`` enables optimistic locking (Refs #531) — when the
    DB-side ``updated_at`` differs, a ``ValidationError`` is raised.

    ``require_version_token`` (Refs #1338, #1351): wird 1:1 an
    ``check_version_conflict`` durchgereicht. ``WorkItemUpdateView`` setzt es
    auf das Ergebnis von ``_wants_json_response`` — JSON-/Offline-Replay-
    Clients müssen dadurch einen Token mitschicken, während der klassische
    HTML-Formular-Pfad beim Default ``False`` bleibt (kein Verhaltensbruch).

    ``expected_form_state`` (Refs #2331): Fingerabdruck der Formularfelder zum
    Renderzeitpunkt (:func:`workitem_form_state_token`). Weicht nur
    ``updated_at`` ab, die Formularfelder in der Datenbank aber nicht — etwa
    weil parallel nur der Status gewechselt wurde —, ist das kein Konflikt mit
    dem Formular: die Eingabe wird gespeichert, der parallele Status bleibt.
    Jede parallele Aenderung eines Formularfelds bleibt ein Konflikt. Ohne
    Fingerabdruck (Default, JSON-/Offline-Replay) gilt der Versionsvertrag
    unveraendert.

    Ursprungs-Pinning (Refs #1786, Maintainer-Entscheid 2026-08-05): Anders
    als beim Fall (dort strikt) bleibt bei der Aufgabe das nachträgliche
    Zuordnen erlaubt — eine personenlose Aufgabe darf einer Person zugeordnet
    werden. Verboten sind Wechsel und Entfernen: ``title``/``description``
    sind Freitext und es gibt keinen nachgelagerten Prune, ein Umhängen
    schriebe also fremden Klartext an eine andere Person fest.
    """
    from core.services.client.restriction import assert_processing_allowed
    from core.services.security import check_version_conflict

    # Art.-18-Gate (Refs #2045) VOR dem Versions-Guard und unabhaengig davon,
    # WELCHE Felder das Update traegt.
    #
    # KANONISCHE SPERR-REIHENFOLGE: Client VOR WorkItem (siehe Modul-Docstring).
    # Deshalb erst ein UNGESPERRTER Lesestand des Personenbezugs — er dient nur
    # der Lock-Reihenfolge, nicht der Entscheidung —, dann die Client-Sperre,
    # dann die WorkItem-Sperre. Der Instanz-Wert taugt dafuer NICHT: bei einem
    # ``ModelForm`` hat ``_post_clean()`` ``workitem.client_id`` bereits auf den
    # geposteten Wert mutiert (Muster #1613).
    # Einrichtungsgrenze der handelnden Person vor jeder Lesung und Sperre.
    _assert_actor_in_facility(user, workitem.facility_id)
    # Refs #2377: auch diese Lesung nur in der Einrichtung der Aufgabe — sonst
    # sperrte eine Instanz mit verfaelschter Einrichtung die Person einer
    # fremden Aufgabe, bevor die gescopte Aufgabensperre unten abweist.
    current_client_id = (
        type(workitem)
        ._base_manager.filter(pk=workitem.pk, facility_id=workitem.facility_id)
        .values_list("client_id", flat=True)
        .first()
    )
    # BEIDE Personenbezuege gehoeren VOR die WorkItem-Sperre (Re-Review 2):
    # der bisherige und — bei einer Zuordnung ``None -> Person`` — der neue.
    # Die erste Fassung sperrte den neuen Client erst im ``client``-Zweig
    # weiter unten, also NACH dem WorkItem, und riss damit genau die
    # Reihenfolge auf, die dieser Umbau schliessen sollte. Der Wunsch-Client
    # steht schon hier in ``fields`` — es gibt keinen Grund, ihn spaeter zu
    # sperren.
    #
    # Sortiert nach PK: liegen zwei Client-Zeilen an, muessen zwei
    # gleichzeitige Updates sie in derselben Ordnung nehmen (dieselbe Regel wie
    # in ``_locked_clients``).
    pending_client_ids = {current_client_id}
    if "client" in fields:
        pending_client_ids.add(_client_pk(fields["client"]))
    locked_client_ids = sorted(cid for cid in pending_client_ids if cid is not None)
    for client_id in locked_client_ids:
        assert_processing_allowed(client_id, lock=True)
    # Widerrufszustand des bisherigen UND des neuen Personenbezugs auf den eben
    # gesperrten Zeilen: eine Person im Papierkorb, deaktiviert oder
    # zusammengefuehrt nimmt keine geaenderten Aufgabendaten mehr an.
    # A raw text key is already in database form here (``_client_pk``).
    _raise_if_clients_revoked(locked_client_ids, workitem.facility_id)
    # Erst jetzt die WorkItem-Zeile sperren. Ein Auseinanderlaufen zwischen dem
    # ungesperrten Vorab-Lesen und dem gesperrten Stand ist ein echter
    # Nebenlaeufigkeits-Konflikt: ihn hier abzuweisen ist billiger und
    # ehrlicher, als die Sperr-Reihenfolge fuer den Randfall umzudrehen und ein
    # Deadlock-Fenster zu oeffnen.
    db_client_id = _locked_client_id(workitem)
    if db_client_id is WORKITEM_ROW_MISSING:
        # Refs #2377: keine Zeile im Einrichtungs-Scope — abbrechen, bevor ein
        # nachgelagerter Zugriff (Diff-Lesung, Schreibung) sie per PK erreicht.
        raise ValidationError(_("Aufgabe gehört zu einer anderen Einrichtung."))
    if db_client_id != current_client_id:
        raise ValidationError(_("Die Personenzuordnung der Aufgabe wurde zwischenzeitlich geändert."))
    # Loeschzustand der eben gesperrten Aufgabenzeile — ein gewoehnliches Lesen
    # unter der Sperre; der Manager blendet geloeschte Aufgaben nicht aus.
    # The same read carries creator and assignee (joined, no extra query): the
    # instance cannot serve here, ``_post_clean()`` has already put the posted
    # assignee on it.
    locked_state = (
        type(workitem)
        ._base_manager.select_related("created_by", "assigned_to")
        .only("pk", "is_deleted", "created_by", "assigned_to")
        .get(pk=workitem.pk, facility_id=workitem.facility_id)
    )
    if locked_state.is_deleted:
        raise ValidationError(WORKITEM_DELETED_MESSAGE)
    # Permission at the locked state: the view checks it beforehand without a
    # lock. A work item reassigned or re-owned in between is refused here,
    # before any write or audit row.
    if not can_user_mutate_workitem(user, locked_state):
        raise PermissionDenied(_("Keine Berechtigung für diese Aufgabe."))
    try:
        check_version_conflict(workitem, expected_updated_at, require_token=require_version_token)
    except ValidationError as exc:
        # Nur der echte Versions-Mismatch darf per Fingerabdruck aufgeloest
        # werden — fehlender oder ungueltiger Token bleiben Fehler.
        if exc.code != "version_conflict" or not _form_state_unchanged(workitem, expected_form_state):
            raise
    # Allowlist (Refs #734-Muster, Refs #1786): bis hierher war
    # ``update_workitem`` eine von ZWEI der vier Update-Services ohne Allowlist —
    # jedes durchgereichte Feld landete blind im ``setattr``. Die verbliebene
    # Luecke ist ``update_event`` (dokumentierter Restpunkt in
    # ``docs/security-notes.md``, bewusst nicht in #1786 mitbehoben: fremder
    # Pfad, eigener Testbedarf). Menge = die Kwargs von
    # ``WorkItemUpdateView.post``. Läuft ZUERST, unabhängig vom Diff.
    allowed = {
        "item_type",
        "title",
        "description",
        "priority",
        "due_date",
        "assigned_to",
        "client",
        "remind_at",
        "recurrence",
    }
    check_update_allowlist(fields, allowed)

    if "client" in fields:
        # Refs #1786 (TOCTOU): Der Pin-Zustand wird gegen den **DB-Stand unter
        # Row-Lock** entschieden, nicht gegen ``workitem.client_id``. Die
        # Instanz kann in zwei Richtungen falsch liegen: veraltet (ein
        # parallelter Attach-POST hat die Person inzwischen gesetzt — beide
        # Requests lesen sonst „noch personenlos" und der zweite ueberschreibt
        # den ersten) oder bereits von ``_post_clean()`` mutiert (Muster #1613).
        # Der Lock haelt bis zum Commit dieser Transaktion, also auch ueber den
        # Diff-Read und das ``save()`` unten. ``db_client_id`` stammt aus dem
        # gesperrten Lesestand oben — kein zweiter Lookup (Re-Review:
        # Doppel-Query-Redundanz).
        new_pk = _client_pk(fields["client"])
        # Der neue Personenbezug ist bereits oben gesperrt und gegatet — eine
        # personenlose Aufgabe einer eingeschraenkten Person ZUZUORDNEN ist
        # neue inhaltliche Verarbeitung und genau der Fall, den das
        # Ursprungs-Pinning offen laesst (``None -> X`` ist erlaubt).
        if db_client_id is not None:
            if new_pk != db_client_id:
                raise ValueError("Personenzuordnung einer Aufgabe kann nicht geändert oder entfernt werden.")
            # No-op: identischer Wert. Raus aus ``fields``, damit weder ein
            # leerer Audit-Diff noch ein unnötiger Schreibzugriff entsteht —
            # und der DB-Stand auf die Instanz, weil das volle ``save()`` unten
            # sonst die Zuordnung einer stale (personenlosen) Instanz
            # zurueckschriebe. Das Wegwerfen des Kwargs darf nie ein Nullen
            # bedeuten.
            fields.pop("client")
            workitem.client_id = db_client_id
    # Refs #1613: Diff gegen den DB-Stand, NICHT gegen die übergebene Instanz.
    # ``WorkItemForm(instance=workitem)`` hat die Instanz in ``_post_clean()``
    # bereits mutiert — ein ``getattr(workitem, key) != value`` sähe hier
    # ``neu == neu`` und schriebe nie ``WORKITEM_UPDATE`` (TB6). Der Helper
    # liest die noch unveränderte DB-Zeile (FK-Vergleich über ``attname``).
    # Refs #1789: NUR die genannten Spalten schreiben. Ein volles ``save()``
    # schriebe alle Felder der uebergebenen Instanz zurueck — und die stammt
    # aus einem frueheren Read. Alles ausserhalb dieser Allowlist (``status``/
    # ``completed_at``, der Recurrence-Marker, die Soft-Delete-Felder und vor
    # allem per ``_redact_workitems`` redigierte ``title``/``description``)
    # waere damit auf den stalen Stand zurueckgesetzt. ``updated_at`` muss
    # explizit mitgenannt werden: ``auto_now`` bumpt bei ``update_fields``
    # nicht von allein (gleiches Muster wie ``bulk_update_workitem_status``).
    # ``fields`` enthaelt nach dem ``client``-Pop exakt die zu schreibenden
    # Spalten; Django akzeptiert dabei den Feldnamen (``assigned_to``) ebenso
    # wie den ``attname``.
    changed_fields = apply_allowlisted_update(workitem, fields, allowed_fields=allowed)

    if changed_fields:
        audit_event(
            AuditLog.Action.WORKITEM_UPDATE,
            user=user,
            facility=workitem.facility,
            target_obj=workitem,
            detail={"changed_fields": changed_fields},
        )

    log_activity(
        facility=workitem.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=workitem,
        action=Activity.Action.WORKITEM_UPDATED,
    )

    return workitem


@transaction.atomic
def update_workitem_status(workitem, new_status, user, *, expected_updated_at=None, require_version_token=False):
    """Perform a status transition including auto-assign and completed_at management.

    Art.-18-Gate (Refs #2045): ein Statuswechsel ist fachliche Arbeit an der
    Aufgabe einer Person, nicht blosse Speicherung.

    - On IN_PROGRESS: auto-assign to the user if not yet assigned.
    - On DONE/DISMISSED: set completed_at.
    - On back-transition: reset completed_at.
    - On DONE with ``recurrence != NONE``: auto-duplicate the work item
      with the next due date (Refs #266).

    Concurrency (Refs #129 Teil A, Refs #733): Das WorkItem wird unter
    ``select_for_update()`` neu geladen, damit zwei zeitgleiche Klicks
    nicht denselben ``old_status`` lesen und beide einen Activity-/
    Recurrence-Folgeeintrag erzeugen. Wenn ``new_status == aktueller
    Status`` ist (Idempotenz-Guard), kehrt die Funktion sofort ohne
    ``save()`` und ohne Activity-Log zurueck.

    ``expected_updated_at``/``require_version_token`` (Refs #1419, analog
    ``update_workitem``): Optimistic-Lock-Token fuer Offline-Replay-Clients.
    Der Check laeuft auf der frisch GELOCKTEN Instanz innerhalb derselben
    Transaktion (ADR-030-Invariante) — die View setzt die Pflicht nur fuer
    Raw-JSON-Clients (``_wants_raw_json_response``), der normale HTMX-/
    HTML-Pfad bleibt beim bisherigen Status-gegen-Status-LWW (Default
    ``None``/``False``, kein Verhaltensbruch).

    Refs #906: Status-/completed_at-/auto-assign-/Recurrence-Logik teilt
    sich mit dem Bulk-Pfad ueber ``_apply_status_transition`` und
    ``_maybe_duplicate_recurring``.

    ``can_user_mutate_workitem`` wird an der gesperrten Zeile entschieden
    (``PermissionDenied``), damit die View die Aufgabe nicht vor der Person
    sperren muss.
    """
    from core.services.client.restriction import assert_processing_allowed
    from core.services.security import check_version_conflict

    # Einrichtungsgrenze der handelnden Person vor jeder Lesung und Sperre.
    _assert_actor_in_facility(user, workitem.facility_id)
    # Innerhalb der Transaktion neu laden + locken — Facility-Filter
    # explizit, damit RLS- und Tenant-Isolation greifen.
    # KANONISCHE SPERR-REIHENFOLGE: Client VOR WorkItem (Modul-Docstring).
    # Ungesperrtes Vorab-Lesen nur zur Bestimmung der Lock-Reihenfolge; die
    # Entscheidung faellt gegen den gesperrten Stand darunter.
    current_client_id = (
        WorkItem.objects.filter(pk=workitem.pk, facility_id=workitem.facility_id)
        .values_list("client_id", flat=True)
        .first()
    )
    assert_processing_allowed(current_client_id, lock=True)
    # Widerrufszustand der eben gesperrten Person (Papierkorb, deaktiviert,
    # zusammengefuehrt); ohne Personenbezug keine Abfrage.
    _raise_if_clients_revoked([current_client_id] if current_client_id is not None else [], workitem.facility_id)
    locked = WorkItem.objects.select_for_update().get(pk=workitem.pk, facility_id=workitem.facility_id)
    if locked.client_id != current_client_id:
        raise ValidationError(_("Die Personenzuordnung der Aufgabe wurde zwischenzeitlich geändert."))
    # Eine inzwischen geloeschte Aufgabe wechselt den Status nicht mehr — und
    # erzeugt damit auch keine Folgeaufgabe.
    if locked.is_deleted:
        raise ValidationError(WORKITEM_DELETED_MESSAGE)
    # Berechtigung am gesperrten Stand: die View prueft vorab ohne Sperre (eine
    # Aufgabensperre dort stuende VOR der Personensperre). Eine inzwischen
    # einer anderen Person zugewiesene Aufgabe wird hier abgewiesen.
    if not can_user_mutate_workitem(user, locked):
        raise PermissionDenied(_("Keine Berechtigung für diese Aufgabe."))
    check_version_conflict(locked, expected_updated_at, require_token=require_version_token)
    old_status = locked.status

    if not _apply_status_transition(locked, new_status, user, auto_assign=True):
        return locked

    locked.save()

    # #1467: Unveränderliche AuditLog-Spur für JEDEN Statuswechsel — auf dem
    # gemeinsamen Post-Save-Pfad (nicht in den status-spezifischen
    # log_activity-Zweigen), damit auch IN_PROGRESS und DISMISSED erfasst
    # werden. Analog Bulk-Pfad (_log_workitem_update); erfüllt Threat-Model TB6
    # (threat-model.md:150). Die Activity bleibt zusätzlich als mutabler Feed.
    audit_event(
        AuditLog.Action.WORKITEM_UPDATE,
        user=user,
        facility=locked.facility,
        target_obj=locked,
        detail={"changed_fields": ["status"], "from": old_status, "to": new_status},
    )

    if new_status == WorkItem.Status.DONE:
        log_activity(
            facility=locked.facility,
            actor=user,
            verb=Activity.Verb.COMPLETED,
            target=locked,
            action=Activity.Action.WORKITEM_COMPLETED,
        )
    elif new_status == WorkItem.Status.OPEN and old_status == WorkItem.Status.DONE:
        log_activity(
            facility=locked.facility,
            actor=user,
            verb=Activity.Verb.REOPENED,
            target=locked,
            action=Activity.Action.WORKITEM_REOPENED,
        )

    _maybe_duplicate_recurring(locked, user, new_status)
    return locked


@transaction.atomic
def duplicate_recurring_workitem(workitem, user):
    """Create a follow-up WorkItem for a recurring task (Refs #266, #596).

    Copies relevant fields (title, description, priority, assigned_to, client,
    item_type, facility, recurrence, remind_at) and bumps ``due_date`` by the
    recurrence interval. If the source has no ``due_date`` the interval is
    applied to today so the new item still has a valid deadline.

    Idempotency (Refs #596): If the source WorkItem already carries a
    ``recurrence_duplicated_at`` marker, the follow-up has already been
    generated during an earlier DONE transition — the call is a no-op and
    returns ``None``. This prevents duplicate follow-ups on Done→Open→Done
    toggles and on bulk DONE paths that re-process items.

    Returns the newly created WorkItem, or ``None`` if ``recurrence`` is NONE
    or unknown, or if the source is already marked as duplicated.
    """
    from core.services.client.restriction import assert_processing_allowed

    # Einrichtungsgrenze der handelnden Person vor jeder Lesung und Sperre.
    _assert_actor_in_facility(user, workitem.facility_id)
    # Art.-18-Gate (Refs #2045): Die Folgeaufgabe wird mit DEMSELBEN
    # Personenbezug neu angelegt — eine Neuanlage, kein Fortschreiben. Ohne
    # Guard entstand an einer eingeschraenkten Person auf dem DONE-Uebergang
    # (einzeln wie im Stapel) eine frische Aufgabe.
    assert_processing_allowed(workitem.client_id, lock=True)
    # Eine widerrufene Person (Papierkorb, deaktiviert, zusammengefuehrt)
    # bekommt keine neue Aufgabe — gelesen an der eben gesperrten Zeile.
    _raise_if_clients_revoked([workitem.client_id] if workitem.client_id is not None else [], workitem.facility_id)
    # Quellaufgabe gesperrt und einrichtungsgebunden neu lesen (Person vor
    # Aufgabe). Wiederholung und Marker entscheidet die frische Zeile, nicht die
    # Instanz des Aufrufers: eine zweite, veraltete Instanz saehe den Marker
    # sonst leer und legte eine zweite Folgeaufgabe an. In der Kette aus dem
    # Statuswechsel haelt der Aufrufer die Sperre bereits.
    source = WorkItem.objects.select_for_update().filter(pk=workitem.pk, facility_id=workitem.facility_id).first()
    if source is None:
        raise ValidationError(_("Aufgabe gehört zu einer anderen Einrichtung."))
    if source.client_id != workitem.client_id:
        raise ValidationError(_("Die Personenzuordnung der Aufgabe wurde zwischenzeitlich geändert."))
    if source.is_deleted:
        raise ValidationError(WORKITEM_DELETED_MESSAGE)
    if not source.recurrence or source.recurrence == WorkItem.Recurrence.NONE:
        return None

    # Refs #596: Idempotency guard — skip if a follow-up was already produced.
    if source.recurrence_duplicated_at is not None:
        return None

    reference_date = source.due_date or timezone.localdate()
    new_due_date = _next_due_date(reference_date, source.recurrence)
    if new_due_date is None:
        return None

    new_remind_at = None
    if source.remind_at and source.due_date:
        # Preserve the reminder offset relative to the due date.
        offset = source.due_date - source.remind_at
        new_remind_at = new_due_date - offset

    new_workitem = WorkItem.objects.create(
        facility_id=source.facility_id,
        created_by=user,
        client_id=source.client_id,
        assigned_to_id=source.assigned_to_id,
        item_type=source.item_type,
        title=source.title,
        description=source.description,
        priority=source.priority,
        due_date=new_due_date,
        remind_at=new_remind_at,
        recurrence=source.recurrence,
        status=WorkItem.Status.OPEN,
    )
    log_activity(
        facility=workitem.facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=new_workitem,
        action=Activity.Action.WORKITEM_RECURRENCE_CREATED,
    )
    # Refs #596: Mark the source so a subsequent DONE transition does not
    # duplicate again. Persist within the same atomic block — an der
    # gesperrten Zeile; die Instanz des Aufrufers erhaelt denselben Stand.
    source.recurrence_duplicated_at = timezone.now()
    source.save(update_fields=["recurrence_duplicated_at"])
    workitem.recurrence_duplicated_at = source.recurrence_duplicated_at
    return new_workitem


@transaction.atomic
def bulk_update_workitem_status(workitems, user, status):
    """Bulk-update the status of multiple WorkItems (Refs #267, #593).

    Writes one AuditLog entry per changed WorkItem and respects completed_at
    semantics (set on DONE/DISMISSED, cleared otherwise).

    On bulk DONE transitions (Refs #593): for items with a non-NONE recurrence,
    a follow-up WorkItem is generated via ``duplicate_recurring_workitem`` —
    analogous to the single-update path in ``update_workitem_status``. The
    duplicate call is guarded by the ``recurrence_duplicated_at`` marker
    (Refs #596), so re-processing the same batch stays idempotent.

    Returns the number of processed items.
    """
    valid_statuses = {s.value for s in WorkItem.Status}
    if status not in valid_statuses:
        raise ValueError(f"Invalid status: {status}")

    count = 0
    for workitem in _locked_workitems(workitems, user):
        # Refs #906: gemeinsame Transition-Logik mit dem Single-Pfad. Bulk
        # uebernimmt kein Auto-Assign (Designentscheidung, Refs #593).
        if not _apply_status_transition(workitem, status, user, auto_assign=False):
            continue
        workitem.save(update_fields=["status", "completed_at", "updated_at"])
        _log_workitem_update(workitem, user, ["status"])
        # Refs #593 / #596: Recurrence-Duplikation auch im Bulk-Pfad,
        # Idempotenz via ``recurrence_duplicated_at``-Marker in
        # ``duplicate_recurring_workitem``.
        _maybe_duplicate_recurring(workitem, user, status)
        count += 1
    return count


@transaction.atomic
def bulk_update_workitem_priority(workitems, user, priority):
    """Bulk-update the priority of multiple WorkItems (Refs #267).

    Returns the number of processed items.
    """
    valid_priorities = {p.value for p in WorkItem.Priority}
    if priority not in valid_priorities:
        raise ValueError(f"Invalid priority: {priority}")

    count = 0
    for workitem in _locked_workitems(workitems, user):
        if workitem.priority == priority:
            continue
        workitem.priority = priority
        workitem.save(update_fields=["priority", "updated_at"])
        _log_workitem_update(workitem, user, ["priority"])
        count += 1
    return count


@transaction.atomic
def bulk_assign_workitems(workitems, user, assignee_or_none):
    """Bulk-assign WorkItems to a user (or clear the assignment).

    ``assignee_or_none=None`` removes the assignment. Returns the processed count.
    """
    count = 0
    for workitem in _locked_workitems(workitems, user):
        if workitem.assigned_to_id == (assignee_or_none.pk if assignee_or_none else None):
            continue
        workitem.assigned_to = assignee_or_none
        workitem.save(update_fields=["assigned_to", "updated_at"])
        _log_workitem_update(workitem, user, ["assigned_to"])
        count += 1
    return count
