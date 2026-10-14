"""Einzel-Aktionen für WorkItems — Create, Update, StatusUpdate (Refs #605).

Abgeteilt von :file:`views/workitems.py`. Die Inbox- und Detail-Views
bleiben dort, weil sie reine Read-Pfade sind.
"""

from contextlib import suppress
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.constants import RATELIMIT_FREQUENT, RATELIMIT_MUTATION, WORKITEM_INBOX_CAP
from core.forms.workitems import WorkItemForm
from core.models import WorkItem
from core.services.case import (
    DEFAULT_ASSIGNED_SCOPE,
    apply_workitem_default_scope,
    apply_workitem_filters,
    can_user_mutate_workitem,
    create_workitem,
    update_workitem,
    update_workitem_status,
)
from core.services.case.workitems import workitem_form_state_token
from core.services.client import resolve_writable_client
from core.services.scoping import get_scoped_object
from core.services.workitem_visibility import workitems_for_read
from core.utils.formatting import isoformat_or_none
from core.utils.ratelimit import ratelimit
from core.views._json_contracts import (
    _conflict_response,
    _invalid_form_response,
    _wants_json_response,
    _wants_raw_json_response,
)
from core.views._offline_replay import saved_confirmation
from core.views.mixins import (
    AssistantOrAboveRequiredMixin,
    IdempotentCreateMixin,
    StaffRequiredMixin,
    htmx_absage,
    is_idempotency_conflict,
)
from core.views.utils import safe_redirect_path


def _workitem_conflict_response(workitem, client_expected, *, error="conflict"):
    """Build the 409-Conflict JSON payload for a stale optimistic-concurrency WorkItem edit.

    Refs #1351 Task 7: Analog zu ``core.views.events._event_conflict_response``,
    aber mit einer anderen ``server_state``-Form — WorkItems halten ihre
    Felder direkt als Modell-Spalten (kein dynamisches ``data_json`` wie bei
    Events), daher trägt der Payload die vier Felder, die
    :file:`conflict-resolver.js` für ein WorkItem zum Diff-Rendern braucht:

    - ``title`` — aktueller Titel
    - ``description`` — aktuelle Freitext-Beschreibung
    - ``status`` — aktueller Workflow-Status (``open``/``in_progress``/…)
    - ``updated_at`` — ISO-Timestamp, wird nach Konfliktauflösung zum neuen
      ``expected_updated_at``

    Keine Sensitivitäts-Filterung nötig (anders als bei Events): WorkItems
    haben keine dynamischen, pro-DocumentType klassifizierten Felder.

    ``error`` unterscheidet wie beim Event-Pendant ``"conflict"`` (echter
    Versions-Mismatch, auch defensiv für einen korrupten Token) von
    ``"missing-token"`` (JSON-/HTMX-Edit ohne ``expected_updated_at``).
    """
    server_state = {
        "title": workitem.title,
        "description": workitem.description,
        "status": workitem.status,
        "updated_at": isoformat_or_none(workitem.updated_at),
    }
    return _conflict_response(server_state, client_expected, error=error)


def _build_recent_done_oob(request, workitem):
    """Insert a just-completed task into the visible seven-day inbox widget.

    Respect the row's inbox status and type/priority/assignee/due filters.
    ``filter_status`` is separate from the action's ``status`` payload.
    Completion windows and the facility/person read scope match the inbox.
    Outside the default status view there is no recent-done OOB target.
    """
    if request.POST.get("recent_done_target") != "1":
        return ""
    if workitem.status not in (WorkItem.Status.DONE, WorkItem.Status.DISMISSED):
        return ""

    if request.POST.get("filter_status", ""):
        return ""

    due = request.POST.get("due", "")
    # Der "Überfällig"-Filter gilt nur für offene/laufende Aufgaben
    # (apply_workitem_filters) — ein soeben erledigtes/verworfenes Item passt
    # danach nie mehr; das entspricht dem Stand nach einem Reload.
    if due == "overdue":
        return ""

    raw_assigned_to = request.POST.get("assigned_to", DEFAULT_ASSIGNED_SCOPE)
    default_scope = raw_assigned_to == DEFAULT_ASSIGNED_SCOPE
    assigned_to = "" if default_scope else raw_assigned_to

    qs = apply_workitem_filters(
        workitems_for_read(request.current_facility),
        item_type=request.POST.get("item_type", ""),
        priority=request.POST.get("priority", ""),
        assigned_to=assigned_to,
        due=due,
        user=request.user,
    )
    qs = apply_workitem_default_scope(qs, request.user, default_scope=default_scope)

    # Das Item selbst muss durch den Filter passen — sonst war es (z.B. bei
    # einem Personen-Filter auf jemand anderen) nach dem Statuswechsel gar
    # nicht mehr im aktuellen Filterkontext sichtbar.
    if not qs.filter(pk=workitem.pk).exists():
        return ""

    seven_days_ago = timezone.now() - timedelta(days=7)
    done_qs = qs.filter(
        status__in=[WorkItem.Status.DONE, WorkItem.Status.DISMISSED],
        updated_at__gte=seven_days_ago,
    )
    total = done_qs.count()
    was_empty = total <= 1  # das soeben erledigte Item zaehlt hier schon mit
    cap = WORKITEM_INBOX_CAP
    has_more = total > cap
    shown = min(total, cap)

    empty_oob = '<div hx-swap-oob="delete:#recent-done-empty"></div>' if was_empty else ""
    count_html = render_to_string(
        "core/workitems/partials/_recent_done_count.html",
        {"done_count": shown, "done_has_more": has_more},
    )
    card_html = render_to_string(
        "core/workitems/partials/item_card.html",
        {"wi": workitem},
        request=request,
    )
    return empty_oob + count_html + f'<div hx-swap-oob="afterbegin:#recent-done-items">{card_html}</div>'


class WorkItemStatusUpdateView(AssistantOrAboveRequiredMixin, IdempotentCreateMixin, View):
    """HTMX: update WorkItem status.

    Refs #1419: Teil des HTTP-Replay-Contracts (ADR-030), damit
    Status-Übergänge offline queue- und nachspielbar sind. Anders als der
    Edit-Pfad (Token-Pflicht via ``_wants_json_response`` inkl. HTMX) gilt
    die Token-Pflicht hier NUR für Raw-JSON-Clients
    (``_wants_raw_json_response``): HTMX ist beim Status-Toggle der normale
    ONLINE-Pfad (Inbox-Buttons), dessen bewusst akzeptiertes
    Status-gegen-Status-LWW unverändert bleibt. Die Templates senden das
    Token trotzdem immer mit — es gehört in den Offline-Payload, den der
    Service Worker beim Queueing einfriert; ausgewertet wird es erst vom
    Replay (der ``Accept: application/json`` setzt, offline-queue.js).
    """

    idempotency_scope = "workitem_status"

    def on_idempotent_hit(self, request, hit):
        # Replay einer bereits angewendeten Transition: der gecachte Erfolg
        # wird als scoped Live-Lookup beantwortet, damit der Zweit-Replay
        # exakt die Erfolgsform des Originals bekommt (inkl. zwischen-
        # zeitlicher Folge-Transitionen).
        workitem = get_scoped_object(workitems_for_read(request.current_facility), request, pk=self.kwargs["pk"])
        return self._success_response(request, workitem)

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="POST", block=True))
    def post(self, request, pk):
        # Refs #2585: reject unreadable records before replay or error responses.
        workitem = get_scoped_object(workitems_for_read(request.current_facility), request, pk=pk)
        new_status = request.POST.get("status")
        # Refs #819: Django bietet Status.values als Liste an.
        if new_status not in WorkItem.Status.values:
            # Refs #2385: the inbox row buttons (`_workitem_row.html`,
            # `hx-post` + `hx-target="#workitem-<pk>"` +
            # `hx-swap="outerHTML"`) would otherwise swap this plain text
            # into the row itself (error class #2358). The second caller
            # (`_actions.html`, a plain form on the detail page) only ever
            # submits hard-coded, valid status values — that branch stays
            # defensive and practically unreachable, so it is left
            # unchanged.
            if request.headers.get("HX-Request") == "true":
                return htmx_absage(_("Ungültiger Status"), status=400)
            return HttpResponseBadRequest(_("Ungültiger Status"))

        wants_raw_json = _wants_raw_json_response(request)
        expected_updated_at = (request.POST.get("expected_updated_at") or None) if wants_raw_json else None

        # Idempotenz-Guard (Refs #1419, analog WorkItemCreateView/#1329):
        # bricht der Offline-Client die Verbindung nach erfolgreichem
        # Server-Write, aber vor Empfang der Response ab, spielt er dieselbe
        # Queue-Zeile erneut. Ohne Dedup würde der Zweit-Replay einen
        # ZWISCHENZEITLICHEN Statuswechsel (z.B. Reopen durch Kollegin) als
        # 409-Konflikt melden, obwohl die Aktion längst angewendet ist — der
        # Cache-Hit liefert stattdessen die Erfolgsform des Originals.
        # Sequenz + Mismatch-422: IdempotentCreateMixin (Refs #1831).
        # Kein DB-Backstop wie bei Create (R5): Transitionen haben keine
        # ``idempotency_key``-Spalte; nach Cache-Verlust fängt der
        # Versions-Token den Doppel-Replay als sichtbaren 409 ab (kein
        # stilles Überschreiben).
        idem_key, idem_payload_hash = self.read_idempotency(request)
        replay_response = self.check_replay(request, idem_key)
        if replay_response is not None:
            return replay_response

        # Permission-Check + Service-Call innerhalb derselben Transaktion; das
        # gelockte Objekt kommt aus dem Service-Layer zurueck (Refs #129
        # Teil A, Refs #733). Die Aufgabe wird hier NICHT gesperrt: der Dienst
        # sperrt Person vor Aufgabe, eine Aufgabensperre davor stuende gegen
        # diese Reihenfolge. Die Vorpruefung hier liefert die schnelle Absage;
        # entschieden wird die Berechtigung erneut an der gesperrten Zeile
        # (``PermissionDenied`` aus dem Dienst -> dieselbe 403-Antwort).
        try:
            with transaction.atomic():
                workitem = get_scoped_object(workitems_for_read(request.current_facility), request, pk=pk)
                if not can_user_mutate_workitem(request.user, workitem):
                    return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))

                workitem = update_workitem_status(
                    workitem,
                    new_status,
                    request.user,
                    expected_updated_at=expected_updated_at,
                    require_version_token=wants_raw_json,
                )
        except PermissionDenied:
            return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))
        except ValidationError as e:
            # Refs #1419 (analog WorkItemUpdateView/#1338): der Offline-Replay
            # braucht den 409 mit Server-Stand, damit der Konflikt in der
            # M8-Liste sichtbar und auflösbar bleibt, statt von der
            # generischen Queue still als Erfolg oder Dead-Letter
            # klassifiziert zu werden.
            workitem = get_scoped_object(workitems_for_read(request.current_facility), request, pk=pk)
            if _wants_raw_json_response(request):
                if getattr(e, "code", None) == "missing_token":
                    return _workitem_conflict_response(workitem, None, error="missing-token")
                return _workitem_conflict_response(workitem, expected_updated_at)
            # Refs #2385: the view only honours the version token for
            # raw-JSON clients (``wants_raw_json`` above, true only for
            # ``Accept: application/json``) — the HTMX row button DOES send
            # ``expected_updated_at`` (``_workitem_row.html``, ``hx-vals``,
            # Refs #1419), but the view discards it on this path, so an
            # optimistic-lock conflict can never be raised here: every other
            # ``ValidationError`` from the service (Art. 18 processing
            # restriction, a deleted WorkItem, a changed client assignment,
            # ...) is a business rejection, not a version conflict. Swapping
            # the 409-JSON body into the row (``_workitem_row.html``,
            # ``hx-swap="outerHTML"``) used to erase the row and show raw
            # JSON text (error class #2358/#2385) instead of the usual
            # plain-text toast.
            if request.headers.get("HX-Request") == "true":
                return htmx_absage("; ".join(e.messages), status=409)
            # Defensiver HTML-Fallback (praktisch unerreichbar, weil Token/
            # Pflicht nur für Raw-JSON-Clients gesetzt werden).
            messages.error(request, "; ".join(e.messages))
            return redirect("core:workitem_inbox")

        self.remember_idempotency(request, idem_key, idem_payload_hash, workitem.pk)
        return self._success_response(request, workitem)

    def _success_response(self, request, workitem):
        """Erfolgsform des Status-POSTs — geteilt zwischen Erst-Request und
        Idempotenz-Cache-Hit, damit der Replay exakt die Antwort bekommt, der
        die generische Queue als Erfolg folgt (HX: 200-Partial bzw. leere/OOB-
        Antwort bei ``hide``; sonst Redirect).

        Refs #1568: Bei ``hide`` haengt ``_build_recent_done_oob`` optional ein
        Out-of-Band-Fragment an, das die Karte oben in "Kürzlich erledigt"
        einfügt — ohne Reload sichtbar, konsistent mit dem primären
        Verschwinden aus Offen/In Bearbeitung (leere Primärantwort)."""
        if request.headers.get("HX-Request") == "true":
            if request.POST.get("hide"):
                return HttpResponse(_build_recent_done_oob(request, workitem))
            return render(request, "core/workitems/partials/item_card.html", {"wi": workitem})

        messages.success(request, _("Status aktualisiert."))
        next_url = safe_redirect_path(request.POST.get("next"))
        if next_url != "/":
            return redirect(next_url)
        return redirect("core:workitem_inbox")


class WorkItemCreateView(StaffRequiredMixin, IdempotentCreateMixin, View):
    """Create a WorkItem."""

    idempotency_scope = "workitem_create"

    def on_idempotent_hit(self, request, hit):
        # Replay trifft bereits angelegtes WorkItem → dem Offline-Replay den
        # Bestaetigungsmarker geben (Refs #2289: ein Idempotenz-Treffer belegt
        # einen bereits festgeschriebenen Schreibvorgang), sonst dasselbe Ziel
        # wie beim Original-Erfolg (Inbox), statt ein Duplikat zu erzeugen.
        confirmation = saved_confirmation(request, hit.pk)
        if confirmation is not None:
            return confirmation
        return redirect("core:workitem_inbox")

    def get(self, request):
        facility = request.current_facility
        form = WorkItemForm(facility=facility)

        client_id = request.GET.get("client")
        client_pseudonym = ""
        if client_id:
            client = None
            with suppress(ValidationError):
                client = resolve_writable_client(
                    facility,
                    client_id,
                    missing_error=_("Ungültige Person-ID"),  # pragma: no mutate
                )
            if client:
                client_id = str(client.pk)
                client_pseudonym = client.pseudonym
            else:
                client_id = ""

        context = {
            "form": form,
            "client_id": client_id or "",
            "client_pseudonym": client_pseudonym,
        }
        return render(request, "core/workitems/form.html", context)

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request):
        facility = request.current_facility

        # Idempotenz-Guard (Refs #1329): analog zu EventCreateView.post —
        # bricht der Offline-Client die Verbindung nach erfolgreichem
        # Server-Write, aber vor Empfang der Response ab, spielt er dieselbe
        # Queue-Zeile beim nächsten ``online``-Event erneut. Trägt sie den
        # ``X-Idempotency-Key`` eines bereits angelegten WorkItems, leiten
        # wir auf dasselbe Ziel um wie beim Original-Erfolg, statt ein
        # Duplikat zu erzeugen. Sequenz + Mismatch-422: IdempotentCreateMixin
        # (Refs #1831).
        idem_key, idem_payload_hash = self.read_idempotency(request)
        replay_response = self.check_replay(request, idem_key)
        if replay_response is not None:
            return replay_response
        if idem_key:
            # R5: Cache-Eviction/Neustart ueberlebt nur der DB-Backstop —
            # sonst legt der Replay nach Cache-Verlust ein Duplikat an. Die
            # DB-Spalte traegt bewusst KEINEN Payload-Hash (N14-Grenze, siehe
            # Modul-Docstring idempotency.py): Treffer ohne Payload-Pruefung.
            existing_pk = (
                WorkItem.objects.for_facility(facility)
                .filter(created_by=request.user, idempotency_key=idem_key)
                .values_list("pk", flat=True)
                .first()
            )
            if existing_pk:
                return redirect("core:workitem_inbox")

        form = WorkItemForm(request.POST, facility=facility)

        if form.is_valid():
            try:
                # Savepoint, damit ein IntegrityError am Unique-Constraint die
                # laufende Transaktion nicht kaputt zurücklässt — sonst würde
                # der Lookup im except-Zweig auf einer abgebrochenen
                # Transaktion scheitern (Postgres). Analog zu EventCreateView.
                with transaction.atomic():
                    workitem = create_workitem(
                        facility=facility,
                        user=request.user,
                        client=form.cleaned_data.get("client"),
                        item_type=form.cleaned_data["item_type"],
                        title=form.cleaned_data["title"],
                        description=form.cleaned_data.get("description", ""),
                        priority=form.cleaned_data["priority"],
                        due_date=form.cleaned_data.get("due_date"),
                        remind_at=form.cleaned_data.get("remind_at"),
                        recurrence=form.cleaned_data.get("recurrence") or WorkItem.Recurrence.NONE,
                        assigned_to=form.cleaned_data.get("assigned_to"),
                        idempotency_key=idem_key,
                    )
            except IntegrityError as exc:
                # R6 (Refs #1443, Prädikat in views/mixins.py): NUR der
                # Idempotenz-Unique-Constraint darf als Duplikat gelten —
                # dann auf das Original umleiten statt 500, jeden anderen
                # IntegrityError re-raisen.
                if idem_key and is_idempotency_conflict(exc):
                    existing = (
                        WorkItem.objects.for_facility(facility)
                        .filter(created_by=request.user, idempotency_key=idem_key)
                        .first()
                    )
                    if existing:
                        # Refs #2289: bereits festgeschrieben (Parallel-Replay).
                        confirmation = saved_confirmation(request, existing.pk)
                        if confirmation is not None:
                            return confirmation
                        return redirect("core:workitem_inbox")
                raise
            except ValidationError as exc:
                # Fachliche Absage des Dienstes am gesperrten Stand (etwa eine
                # Person, die seit der Formularpruefung geloescht wurde): als
                # Formularfehler zeigen — der Offline-Abgleich bekommt darueber
                # die 422-Antwort unten, statt dass die Anlage mit 500 endet.
                form.add_error(None, exc)
            else:
                # Erfolgreich angelegt → Ergebnis unter dem Idempotenz-Schlüssel
                # merken, damit ein späterer Replay (#1329) hier oben kurzschließt
                # statt ein zweites WorkItem zu erzeugen. Der Payload-Hash (N14)
                # macht eine Key-Kollision mit anderem Body erkennbar. No-op ohne
                # Schlüssel.
                self.remember_idempotency(request, idem_key, idem_payload_hash, workitem.pk)
                # Refs #2289: belastbare Erfolgsbestaetigung fuer den
                # Offline-Replay statt des Redirects (siehe EventCreateView) —
                # bewusst VOR ``messages.success``.
                confirmation = saved_confirmation(request, workitem.pk)
                if confirmation is not None:
                    return confirmation
                messages.success(request, _("Aufgabe wurde erstellt."))
                return redirect("core:workitem_inbox")

        # Formular ungueltig. Refs #1351 Task 8, #1387 (analog #1111/EventCreateView):
        # der Offline-Replay (roher Accept: application/json) darf ein
        # ungueltiges Formular NICHT als Erfolg (200) deuten — sonst verwirft
        # die generische Queue die Anlage still als "synchronisiert", obwohl
        # nie ein WorkItem entstand (Datenverlust). Bewusst NUR an
        # _wants_raw_json_response gebunden (nicht HX-Request): ein normaler
        # HTML-/HTMX-Submit behaelt das 200-Re-Render mit inline-Fehlern.
        if _wants_raw_json_response(request):
            return _invalid_form_response(form)

        context = {
            "form": form,
            "client_id": request.POST.get("client", ""),
            "client_pseudonym": "",
        }
        return render(request, "core/workitems/form.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class WorkItemUpdateView(StaffRequiredMixin, View):
    """Edit a WorkItem."""

    def get(self, request, pk):
        workitem = get_scoped_object(
            workitems_for_read(request.current_facility).select_related("client"),
            request,
            pk=pk,
        )
        # Refs #735: Edit-Pfad richtet sich nach derselben Owner/Assignee-
        # Policy wie der Status-Pfad. Staff-User sehen das Form nur fuer
        # eigene oder zugewiesene WorkItems; Lead/Admin uneingeschraenkt
        # innerhalb ihrer Facility.
        if not can_user_mutate_workitem(request.user, workitem):
            return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))
        form = WorkItemForm(instance=workitem, facility=request.current_facility, pinned_client=workitem.client)
        context = {
            "form": form,
            "workitem": workitem,
            # Refs #1786: Nur eine bereits zugeordnete Aufgabe wird gepinnt —
            # die personenlose behält ihr Autocomplete (Attach-Fall).
            "client_locked": bool(workitem.client),
            "client_id": str(workitem.client.pk) if workitem.client else "",
            "client_pseudonym": workitem.client.pseudonym if workitem.client else "",
            # Refs #2331: Stand der Formularfelder zum Renderzeitpunkt — damit
            # ein paralleler reiner Statuswechsel die Eingabe nicht verwirft.
            "form_state_token": workitem_form_state_token(workitem),
            # Refs #2361: Versions-Token des ersten Ladens. Nur HIER aus der
            # Instanz — die Neu-Anzeige nach ungueltiger Eingabe reicht den
            # mitgeschickten Wert durch und erzeugt nie einen neuen.
            "expected_updated_at": workitem.updated_at.isoformat(),
        }
        return render(request, "core/workitems/form.html", context)

    def post(self, request, pk):
        workitem = get_scoped_object(
            workitems_for_read(request.current_facility).select_related("client"),
            request,
            pk=pk,
        )
        if not can_user_mutate_workitem(request.user, workitem):
            return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))
        # Refs #1786: VOR ``is_valid()`` lesen — ``_post_clean()`` mutiert die
        # Instanz (Muster #1613). ``client`` steht zwar nicht in ``Meta.fields``,
        # der Pin-Zustand darf aber grundsätzlich nicht vom Formular abhängen.
        pinned_client = workitem.client
        form = WorkItemForm(
            request.POST,
            instance=workitem,
            facility=request.current_facility,
            pinned_client=pinned_client,
        )

        if form.is_valid():
            expected_updated_at = request.POST.get("expected_updated_at") or None
            wants_json = _wants_json_response(request)
            # Ursprungs-Pinning (Refs #1786): ``client`` geht nur im Attach-Fall
            # (bisher personenlose Aufgabe) an den Service. Bei bereits
            # gesetzter Person weist ``update_workitem`` jeden ``client``-Kwarg
            # ab — auch das stille ``None`` eines Formulars ohne Personenfeld.
            pin_kwargs = {} if pinned_client else {"client": form.cleaned_data.get("client")}
            # Refs #2331: der Fingerabdruck der Formularfelder gilt nur fuer den
            # klassischen Formular-Pfad. JSON-/HTMX-Clients (Offline-Replay)
            # behalten ihren Versionsvertrag unveraendert: jeder Mismatch = 409.
            expected_form_state = None if wants_json else (request.POST.get("expected_form_state") or None)
            try:
                update_workitem(
                    workitem,
                    request.user,
                    expected_updated_at=expected_updated_at,
                    expected_form_state=expected_form_state,
                    **pin_kwargs,
                    # Refs #1351 Task 7 (analog #1338 bei Events): JSON-/
                    # Offline-Replay-Clients müssen den Versions-Token
                    # mitschicken (kein stilles Last-Write-Wins mehr). Der
                    # klassische HTML-Formular-Pfad bleibt unverändert
                    # (kein require).
                    require_version_token=wants_json,
                    item_type=form.cleaned_data["item_type"],
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    priority=form.cleaned_data["priority"],
                    due_date=form.cleaned_data.get("due_date"),
                    remind_at=form.cleaned_data.get("remind_at"),
                    recurrence=form.cleaned_data.get("recurrence") or WorkItem.Recurrence.NONE,
                    assigned_to=form.cleaned_data.get("assigned_to"),
                )
            except PermissionDenied:
                # Permission decided again at the locked row (reassigned or
                # re-owned since the check above) -> the same 403 answer.
                return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))
            except ValidationError as e:
                # Refs #1351 Task 7: JSON/HTMX-Clients erhalten einen 409 mit
                # dem aktuellen Server-Stand, damit der Offline-Konflikt
                # sichtbar bleibt statt von der generischen Queue (die jedem
                # 200/redirect als Erfolg folgt) stillschweigend als
                # "synchronisiert" verworfen zu werden. Normale
                # Browser-Requests behalten das bisherige
                # redirect+Flash-Verhalten.
                # Refs #2385: unlike the inbox row button
                # (``WorkItemStatusUpdateView``), this branch stays on
                # ``_wants_json_response`` (HTMX included) — no template
                # calls ``core:workitem_update`` via ``hx-post``, so there is
                # no row-swap target that a 409-JSON body could erase, and
                # the Edit path's 422-invalid/409-conflict contract already
                # requires the version token for every JSON/HTMX caller
                # (unlike the status-toggle path, see the class docstring
                # above). No behavior change here.
                if wants_json:
                    # Aktuellen Server-Stand nachladen, nicht die
                    # In-Memory-Kopie, mit der diese View gestartet ist.
                    workitem.refresh_from_db()
                    if getattr(e, "code", None) == "missing_token":
                        # Refs #1338/#1351: fehlender Token ist kein
                        # Merge-Konflikt im eigentlichen Sinn (es wurde
                        # nichts verglichen) — eigene Fehlerkennung, damit
                        # der Client zwischen "bitte Token nachreichen" und
                        # "echter Konflikt" unterscheiden kann.
                        # client_expected ist null, weil kein sinnvoller
                        # roher Client-Wert vorliegt.
                        return _workitem_conflict_response(workitem, None, error="missing-token")
                    return _workitem_conflict_response(workitem, expected_updated_at)
                messages.error(request, e.message if hasattr(e, "message") else str(e))
                return redirect("core:workitem_update", pk=workitem.pk)
            # Refs #2289: belastbare Erfolgsbestaetigung fuer den
            # Offline-Replay statt des Redirects (siehe EventCreateView).
            confirmation = saved_confirmation(request, workitem.pk)
            if confirmation is not None:
                return confirmation
            messages.success(request, _("Aufgabe wurde aktualisiert."))
            return redirect("core:workitem_inbox")

        # Formular ungueltig. Refs #1351 Task 7 (analog #1111 bei Events):
        # der Offline-Replay (Accept: application/json) darf ein ungueltiges
        # Formular NICHT als Erfolg (HTTP 200) deuten — sonst verwirft er den
        # Edit still als "synchronisiert" (Datenverlust). Daher 422 mit
        # Feldfehlern. Bewusst NUR an _wants_raw_json_response gebunden (nicht
        # _wants_json_response, das auch HX-Request erfasst): ein normaler
        # HTML-/HTMX-Submit behaelt das 200-Re-Render mit
        # inline-Formularfehlern. (Refs #1351 Task 8: Helper nach
        # _json_contracts.py gezogen, sobald die Create-Views denselben Zweig
        # brauchten — verhaltensneutral.)
        if _wants_raw_json_response(request):
            return _invalid_form_response(form)
        context = {
            "form": form,
            "workitem": workitem,
            "client_locked": bool(pinned_client),  # Refs #1786
            "client_id": request.POST.get("client", ""),
            "client_pseudonym": "",
            # Refs #2331: den mitgeschickten Stand weiterreichen, nicht neu
            # berechnen — ``_post_clean()`` hat die Instanz bereits mutiert.
            "form_state_token": request.POST.get("expected_form_state", ""),
            # Refs #2361: ebenso den Versions-Token aus dem ersten Laden. Die
            # Instanz stammt aus DIESEM Request; ihr ``updated_at`` enthaelt
            # bereits jede zwischenzeitliche Aenderung eines anderen Vorgangs,
            # und der naechste Speicherversuch wuerde sie still ueberschreiben.
            # Leer bleibt leer: ein fehlender Token wird nicht nachgeliefert,
            # sonst entginge ein HTMX-Absenden ohne Token der Ablehnung.
            "expected_updated_at": request.POST.get("expected_updated_at", ""),
        }
        return render(request, "core/workitems/form.html", context)
