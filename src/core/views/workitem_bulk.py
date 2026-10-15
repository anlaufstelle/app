"""Bulk-Aktionen für WorkItems (Refs #605).

Abgeteilt von :file:`views/workitems.py`, damit die Single-Item-Views und
die Inbox nicht mit Bulk-Semantik vermischt sind. Der gemeinsame
Ownership-Check (pro-Item!) ist aus Commit fd140d0
und bleibt hier zentral — ein Bulk-Endpoint darf nicht feiner erlauben
als die Single-Route.
"""

import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views import View

from core.constants import RATELIMIT_BULK_ACTION, WORKITEM_BULK_MAX_IDS
from core.models import WorkItem
from core.models.user import User
from core.services.case import (
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    bulk_update_workitem_status,
)
from core.services.case.workitems import WorkItemBatchForbiddenError, workitems_for_read
from core.utils.ratelimit import ratelimit
from core.views.mixins import AssistantOrAboveRequiredMixin
from core.views.workitems import can_user_mutate_workitem


class BulkValidationError(Exception):
    """Signalisiert ungueltige Bulk-Eingabe.

    Die user-sichtbare Meldung kommt aus ``invalid_input_message`` der
    jeweiligen View (kontrollierte, uebersetzte Literale) — **nicht** aus der
    Exception selbst. So fliesst kein Exception-/Trace-Inhalt in die HTTP-
    Antwort (Refs #1011, CodeQL py/stack-trace-exposure). Unerwartete
    Exceptions werden nicht gefangen und propagieren als 500.
    """


def _parse_workitem_ids(raw_values) -> list[uuid.UUID]:
    """POST-Liste von ID-Strings → Liste von ``uuid.UUID``.

    Ein kaputter Wert wirft ``ValueError`` (``post()`` macht daraus HTTP 400).
    Ohne diese Vorvalidierung landet der Rohstring in ``filter(pk__in=…)`` und
    ``UUIDField`` wirft dort eine ``ValidationError`` — die faengt niemand, sie
    schlaegt als 500 durch (``ValidationError`` ist **keine**
    ``ValueError``-Unterklasse). Dasselbe Muster wie
    ``core.views.config.reorder._parse_uuids`` (Refs #2035).
    """
    return [uuid.UUID(str(value)) for value in raw_values]


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="POST", block=True),
    name="post",
)
class _BulkActionMixin(AssistantOrAboveRequiredMixin):
    """Shared helper for bulk WorkItem actions (Refs #267).

    Subclasses implement ``perform_action(request, workitems)`` which applies the
    mutation via a service function and returns the processed count. The mixin
    takes care of scoping to ``request.current_facility`` and rendering the
    inbox-partial for HTMX responses.
    """

    # Kontrollierte Fehlermeldung pro View — fliesst statt ``str(exc)`` in die
    # 400-Antwort (Refs #1011). Subklassen ueberschreiben sie.
    invalid_input_message = _("Ungültige Eingabe.")

    # Filter-Parameter, die nach dem Bulk-Submit in das Redirect-Ziel
    # uebernommen werden, damit die Inbox in derselben gefilterten Sicht
    # zurueckkommt (Refs #1132). Schluessel sind die ``filter_<name>``-Felder
    # aus dem Bulk-Form; Werte die zugehoerigen Inbox-GET-Parameter. Bewusst
    # eine Allowlist: beliebige POST-Keys koennten sonst als Query in den
    # Redirect-Location wandern.
    FILTER_PARAM_MAP = {
        "filter_item_type": "item_type",
        "filter_priority": "priority",
        "filter_assigned_to": "assigned_to",
        "filter_due": "due",
        # Refs #1570: Status-/Zeitraumfilter (erledigte Aufgaben) ebenfalls
        # nach dem Bulk-Submit erhalten.
        "filter_status": "status",
        "filter_done_period": "done_period",
    }

    # Refs #1134: Filter-Felder, deren *leerer* Wert eine eigene Bedeutung hat
    # und daher (wenn explizit gesendet) erhalten bleiben muss. Beim
    # ``assigned_to``-Filter ist der Leerstring die "Alle"-Sicht — eine andere
    # Liste als die parameterlose Default-Sicht ("Mir & unzugewiesene",
    # Refs #1145). Das Bulk-Form sendet ``filter_assigned_to`` immer mit (auch
    # leer); ohne diese Ausnahme verwarf der Redirect den Leerstring wie "kein
    # Filter" und warf die Nutzerin aus der "Alle"-Sicht zurueck in die
    # Default-Eingrenzung. Eine fremd-zugewiesene, gerade Erledigt → In
    # Bearbeitung gesetzte Aufgabe verschwand dort, obwohl ihr Status korrekt
    # geaendert wurde — Liste und Status liefen auseinander. Die uebrigen Filter
    # (Typ/Prioritaet/Faelligkeit) haben keinen bedeutungstragenden Leerwert
    # (leer == "Alle" == Default), daher bleiben sie aus dem Redirect-Ziel
    # heraus, solange sie leer sind.
    FILTER_KEYS_KEEP_EMPTY = frozenset({"filter_assigned_to"})

    def _get_workitem_ids(self, request):
        ids = request.POST.getlist("workitem_ids") or request.POST.getlist("workitem_ids[]")
        return [i for i in ids if i]

    def _inbox_redirect_target(self, request, forbidden_ids=None):
        """Inbox-URL inkl. erhaltener Filter-Query (Refs #1132, #1134).

        Liest ausschliesslich die in ``FILTER_PARAM_MAP`` definierten
        ``filter_<name>``-Felder aus dem POST und haengt nicht-leere Werte als
        Query-String an die Inbox-URL. Unbekannte POST-Keys werden ignoriert,
        sodass kein fremder Parameter (z.B. ``next``) ins Redirect-Ziel
        gelangt.

        Refs #1134: Felder aus ``FILTER_KEYS_KEEP_EMPTY`` (z.B.
        ``filter_assigned_to`` = "Alle") werden auch mit leerem Wert erhalten,
        sofern der Schluessel im POST *vorhanden* ist — sonst wuerde die
        "Alle"-Sicht beim Redirect auf die parameterlose Default-Eingrenzung
        zurueckfallen. Fehlt der Schluessel ganz (No-JS-/filterloser POST),
        bleibt das Ziel der nackte Inbox-Pfad.

        Refs #1148 (Folge-Feedback): ``forbidden_ids`` (PKs der blockierenden,
        fremd-zugewiesenen Items) werden als wiederholter ``forbidden``-Parameter
        angehaengt, damit die Inbox genau diese Auswahl wieder markiert und
        hervorhebt — sonst ginge die zuvor getroffene Auswahl beim Redirect
        verloren und die Meldung verloere den Bezug zur konkreten Aufgabe.
        """
        params = {}
        for post_key, get_key in self.FILTER_PARAM_MAP.items():
            if post_key not in request.POST:
                continue
            value = request.POST.get(post_key, "").strip()
            if value or post_key in self.FILTER_KEYS_KEEP_EMPTY:
                params[get_key] = value
        url = reverse("core:workitem_inbox")
        # Je blockierende PK ein eigenes ``forbidden=<pk>``-Paar (Liste von
        # Tupeln statt dict, damit der Schluessel mehrfach vorkommen darf); die
        # Inbox liest sie per ``request.GET.getlist("forbidden")``.
        query = urlencode(params)
        if forbidden_ids:
            forbidden_query = urlencode([("forbidden", str(pk)) for pk in forbidden_ids])
            query = f"{query}&{forbidden_query}" if query else forbidden_query
        if query:
            url = f"{url}?{query}"
        return url

    def _load_workitems(self, request, ids):
        # Refs #2385: preserve the caller's original selection order (the
        # submitted `workitem_ids` order) instead of the DB's arbitrary
        # `pk__in` order, so a later forbidden-batch redirect can list the
        # refused items in the order they were picked. The lock order taken
        # further down the call chain (`_locked_workitems`, deadlock
        # avoidance) is independent of this list's order and stays
        # deterministic-by-pk, unaffected by this change.
        by_pk = {wi.pk: wi for wi in workitems_for_read(request.current_facility).filter(pk__in=ids)}
        seen = set()
        ordered_ids = [pk for pk in ids if pk not in seen and not seen.add(pk)]
        return [by_pk[pk] for pk in ordered_ids if pk in by_pk]

    def perform_action(self, request, workitems):  # pragma: no cover - overridden
        raise NotImplementedError

    def post(self, request):
        ids = self._get_workitem_ids(request)
        if not ids:
            # Refs #2385: inbox.html submits this via a plain form (no
            # `hx-*`) — a raw 400 text response would replace the whole
            # inbox page with that plain text instead of showing it as a
            # flash above the (unchanged) list.
            messages.error(request, _("Keine Aufgaben ausgewählt."))
            return self._inbox_redirect(request)

        # Refs #2035: Die Auswahl kam ungecappt aus dem POST und ging roh in
        # ``filter(pk__in=…)`` — die IN-Liste war allein durch Djangos
        # DATA_UPLOAD_MAX_NUMBER_FIELDS (Default 1000) begrenzt, also ~990 IDs
        # je Request. Das Ratelimit (30/h) deckelt die Frequenz, nicht die
        # Payload: 30 solcher Requests pro Stunde waren erlaubt. Der Cap sitzt
        # zentral hier im Mixin, weil alle drei Bulk-Endpunkte (Status,
        # Prioritaet, Zuweisung) dieses ``post()`` erben und nur
        # ``perform_action`` ueberschreiben — je Subklasse geprueft, waere die
        # erste vergessene Kopie das Loch, und die Pruefung liefe erst nach der
        # teuren Query. Gezaehlt wird die rohe Liste (nicht dedupliziert): die
        # Payload ist die Kostenstelle. 200 statt 150, weil ein legitimes "alle
        # sichtbaren auswaehlen" hoechstens drei Statuskoerbe à
        # WORKITEM_INBOX_CAP umfasst (Begruendung an der Konstante).
        if len(ids) > WORKITEM_BULK_MAX_IDS:
            messages.error(
                request, _("Zu viele Aufgaben ausgewählt (maximal %(max)d).") % {"max": WORKITEM_BULK_MAX_IDS}
            )
            return self._inbox_redirect(request)

        # Vorvalidierung VOR dem ORM-Zugriff (Muster aus
        # ``core.views.config.reorder``): ein kaputter Wert wuerde in
        # ``filter(pk__in=…)`` eine ``ValidationError`` ausloesen, die niemand
        # faengt und die als 500 durchschlaegt. Das ``except`` umschliesst
        # bewusst nur den Parse-Aufruf — unerwartete Fehler aus
        # ``perform_action`` muessen weiterhin als 500 propagieren (Refs #1011).
        try:
            ids = _parse_workitem_ids(ids)
        except ValueError:
            messages.error(request, _("Ungültige ID im Payload."))
            return self._inbox_redirect(request)

        workitems = self._load_workitems(request, ids)
        if not workitems:
            messages.error(request, _("Keine gültigen Aufgaben gefunden."))
            return self._inbox_redirect(request)

        # Ownership-Check pro Item — Bulk-Route darf nicht feiner erlauben als
        # die Single-Route (Refs #583). Sobald ein Item nicht mutierbar ist,
        # brechen wir ab, um keine Teil-Mutation mit irreführender
        # "5 aktualisiert"-Erfolgsmeldung zu erzeugen.
        forbidden = [wi for wi in workitems if not can_user_mutate_workitem(request.user, wi)]
        if forbidden:
            return self._forbidden_redirect(request, [wi.pk for wi in forbidden], total=len(workitems))

        try:
            count = self.perform_action(request, workitems)
        except BulkValidationError:
            messages.error(request, self.invalid_input_message)
            return self._inbox_redirect(request)
        except WorkItemBatchForbiddenError as exc:
            # The service decides ownership again at the locked rows; an item
            # reassigned or re-owned since the check above gets the same answer.
            return self._forbidden_redirect(request, exc.workitem_ids, total=len(workitems))
        except ValidationError as exc:
            # Fachliche Absage des Dienstes am gesperrten Stand (Art. 18,
            # geloeschte Person oder Aufgabe, fremde Einrichtung). Der Dienst
            # weist den ganzen Stapel ab — wie bei der Ownership-Absage oben
            # als Hinweis in die Inbox statt als 500.
            for message in exc.messages:
                messages.error(request, message)
            return self._inbox_redirect(request)

        messages.success(request, _("%(count)d Aufgaben aktualisiert.") % {"count": count})

        return self._inbox_redirect(request)

    def _forbidden_redirect(self, request, forbidden_ids, *, total):
        """Refuse the whole batch: warning plus the refused selection marked in the inbox."""
        # Refs #1136: Konkrete statt pauschaler Meldung. Seit #1125 zeigt die
        # Inbox bei explizitem Filter ("Alle"/Person) auch fremd-zugewiesene
        # Aufgaben an und macht sie per "Alle sichtbaren auswählen"
        # auswählbar. Eine Fachkraft wählt sie dadurch unbeabsichtigt mit
        # aus; die alte Pauschalmeldung "Keine Berechtigung für ausgewählte
        # Aufgaben." erklärte nicht, *welche* Einschränkung greift. Wir
        # nennen die Anzahl der blockierenden (fremd-zugewiesenen) Items von
        # der Gesamtauswahl, damit gezielt abgewählt werden kann. Die
        # Alles-oder-nichts-Semantik bleibt: es wird nichts verändert.
        n = len(forbidden_ids)
        message = ngettext(
            "%(forbidden)d der %(total)d ausgewählten Aufgaben ist einer "
            "anderen Person zugewiesen und kann nicht per Sammelaktion "
            "geändert werden. Bitte diese Aufgabe abwählen.",
            "%(forbidden)d der %(total)d ausgewählten Aufgaben sind anderen "
            "Personen zugewiesen und können nicht per Sammelaktion geändert "
            "werden. Bitte diese Aufgaben abwählen.",
            n,
        ) % {"forbidden": n, "total": total}
        # Refs #1148: Diese fachliche Meldung NICHT mehr als nackte
        # ``HttpResponseForbidden``-Textseite ausliefern — das erschien als
        # leere weiße Seite mit Text in der Ecke und wirkte wie ein
        # technischer Abbruch. Stattdessen — wie der Erfolgsfall — als
        # Flash-Hinweis in die (gefilterte) Inbox zurückleiten, damit der
        # Hinweis als Alert oberhalb der Aufgabenliste erscheint und die
        # Nutzerin im Arbeitskontext bleibt. ``warning`` statt ``success``,
        # weil nichts geändert wurde (Alles-oder-nichts, Refs #583).
        messages.warning(request, message)
        # Refs #1148 (Folge-Feedback): Die zuvor getroffene Auswahl der
        # blockierenden Items in das Redirect-Ziel durchreichen, damit die
        # Inbox sie wieder markiert/hervorhebt und die Nutzerin sie direkt
        # abwaehlen kann — statt die Auswahl kommentarlos zu verlieren.
        return self._inbox_redirect(request, forbidden_ids=forbidden_ids)

    def _inbox_redirect(self, request, forbidden_ids=None):
        """Redirect in die gefilterte Inbox-Sicht — HTMX-bewusst.

        Filter-State erhalten: zurueck in dieselbe gefilterte Inbox-Sicht
        (Refs #1132), statt auf den nackten ``/workitems/``-Pfad, der die Liste
        ungefiltert neu lud. Bei HX-Request zusaetzlich ``HX-Redirect``, damit
        HTMX einen echten Seitenwechsel ausloest und der hinterlegte Flash
        oberhalb der Liste gerendert wird, statt ein Fragment in den DOM zu
        swappen (Refs #1148).

        ``forbidden_ids`` (optional) reicht die PKs der blockierenden Items als
        ``forbidden``-Query mit, damit die Inbox die abgelehnte Auswahl wieder
        markiert/hervorhebt (Refs #1148 Folge-Feedback).
        """
        target = self._inbox_redirect_target(request, forbidden_ids=forbidden_ids)
        if request.headers.get("HX-Request"):
            # 200 + HX-Redirect, not 302: the XHR follows a 302 by itself and htmx
            # would only see the redirected page (no header) and swap it into the
            # clicked element, leaving the flash message unseen.
            response = HttpResponse(status=200)
            response["HX-Redirect"] = target
            return response
        return redirect(target)


class WorkItemBulkStatusView(_BulkActionMixin, View):
    """Bulk-update status for selected WorkItems."""

    invalid_input_message = _("Ungültiger Status")

    def perform_action(self, request, workitems):
        status = request.POST.get("status", "").strip()
        # Refs #819: Django bietet Status.values als Liste an.
        if status not in WorkItem.Status.values:
            raise BulkValidationError
        return bulk_update_workitem_status(workitems, request.user, status)


class WorkItemBulkPriorityView(_BulkActionMixin, View):
    """Bulk-update priority for selected WorkItems."""

    invalid_input_message = _("Ungültige Priorität")

    def perform_action(self, request, workitems):
        priority = request.POST.get("priority", "").strip()
        if priority not in WorkItem.Priority.values:
            raise BulkValidationError
        return bulk_update_workitem_priority(workitems, request.user, priority)


class WorkItemBulkAssignView(_BulkActionMixin, View):
    """Bulk-assign selected WorkItems (or clear the assignment)."""

    invalid_input_message = _("Unbekannte Benutzerin/Benutzer")

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
                raise BulkValidationError from exc
        return bulk_assign_workitems(workitems, request.user, assignee)
