"""Bulk-Aktionen für WorkItems (Refs #605).

Abgeteilt von :file:`views/workitems.py`, damit die Single-Item-Views und
die Inbox nicht mit Bulk-Semantik vermischt sind. Der gemeinsame
Ownership-Check (pro-Item!) ist aus Commit fd140d0
und bleibt hier zentral — ein Bulk-Endpoint darf nicht feiner erlauben
als die Single-Route.
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_BULK_ACTION
from core.models import WorkItem
from core.models.user import User
from core.services.case import (
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    bulk_update_workitem_status,
)
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

    def _inbox_redirect_target(self, request):
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
        """
        params = {}
        for post_key, get_key in self.FILTER_PARAM_MAP.items():
            if post_key not in request.POST:
                continue
            value = request.POST.get(post_key, "").strip()
            if value or post_key in self.FILTER_KEYS_KEEP_EMPTY:
                params[get_key] = value
        url = reverse("core:workitem_inbox")
        if params:
            url = f"{url}?{urlencode(params)}"
        return url

    def _load_workitems(self, request, ids):
        return list(
            WorkItem.objects.filter(
                pk__in=ids,
                facility=request.current_facility,
            )
        )

    def perform_action(self, request, workitems):  # pragma: no cover - overridden
        raise NotImplementedError

    def post(self, request):
        ids = self._get_workitem_ids(request)
        if not ids:
            return HttpResponseBadRequest(_("Keine Aufgaben ausgewählt."))

        workitems = self._load_workitems(request, ids)
        if not workitems:
            return HttpResponseBadRequest(_("Keine gültigen Aufgaben gefunden."))

        # Ownership-Check pro Item — Bulk-Route darf nicht feiner erlauben als
        # die Single-Route (Refs #583). Sobald ein Item nicht mutierbar ist,
        # brechen wir ab, um keine Teil-Mutation mit irreführender
        # "5 aktualisiert"-Erfolgsmeldung zu erzeugen.
        forbidden = [wi for wi in workitems if not can_user_mutate_workitem(request.user, wi)]
        if forbidden:
            # Refs #1136: Konkrete statt pauschaler Meldung. Seit #1125 zeigt die
            # Inbox bei explizitem Filter ("Alle"/Person) auch fremd-zugewiesene
            # Aufgaben an und macht sie per "Alle sichtbaren auswählen"
            # auswählbar. Eine Fachkraft wählt sie dadurch unbeabsichtigt mit
            # aus; die alte Pauschalmeldung "Keine Berechtigung für ausgewählte
            # Aufgaben." erklärte nicht, *welche* Einschränkung greift. Wir
            # nennen die Anzahl der blockierenden (fremd-zugewiesenen) Items von
            # der Gesamtauswahl, damit gezielt abgewählt werden kann. Die
            # Alles-oder-nichts-Semantik bleibt: es wird nichts verändert.
            n = len(forbidden)
            message = ngettext(
                "%(forbidden)d der %(total)d ausgewählten Aufgaben ist einer "
                "anderen Person zugewiesen und kann nicht per Sammelaktion "
                "geändert werden. Bitte diese Aufgabe abwählen.",
                "%(forbidden)d der %(total)d ausgewählten Aufgaben sind anderen "
                "Personen zugewiesen und können nicht per Sammelaktion geändert "
                "werden. Bitte diese Aufgaben abwählen.",
                n,
            ) % {"forbidden": n, "total": len(workitems)}
            # Refs #1148: Diese fachliche Meldung NICHT mehr als nackte
            # ``HttpResponseForbidden``-Textseite ausliefern — das erschien als
            # leere weiße Seite mit Text in der Ecke und wirkte wie ein
            # technischer Abbruch. Stattdessen — wie der Erfolgsfall — als
            # Flash-Hinweis in die (gefilterte) Inbox zurückleiten, damit der
            # Hinweis als Alert oberhalb der Aufgabenliste erscheint und die
            # Nutzerin im Arbeitskontext bleibt. ``warning`` statt ``success``,
            # weil nichts geändert wurde (Alles-oder-nichts, Refs #583).
            messages.warning(request, message)
            return self._inbox_redirect(request)

        try:
            count = self.perform_action(request, workitems)
        except BulkValidationError:
            return HttpResponseBadRequest(self.invalid_input_message)

        messages.success(request, _("%(count)d Aufgaben aktualisiert.") % {"count": count})

        return self._inbox_redirect(request)

    def _inbox_redirect(self, request):
        """Redirect in die gefilterte Inbox-Sicht — HTMX-bewusst.

        Filter-State erhalten: zurueck in dieselbe gefilterte Inbox-Sicht
        (Refs #1132), statt auf den nackten ``/workitems/``-Pfad, der die Liste
        ungefiltert neu lud. Bei HX-Request zusaetzlich ``HX-Redirect``, damit
        HTMX einen echten Seitenwechsel ausloest und der hinterlegte Flash
        oberhalb der Liste gerendert wird, statt ein Fragment in den DOM zu
        swappen (Refs #1148).
        """
        target = self._inbox_redirect_target(request)
        response = redirect(target)
        if request.headers.get("HX-Request"):
            response["HX-Redirect"] = response["Location"]
        return response


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
