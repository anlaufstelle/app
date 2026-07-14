"""Einzel-Aktionen für WorkItems — Create, Update, StatusUpdate (Refs #605).

Abgeteilt von :file:`views/workitems.py`. Die Inbox- und Detail-Views
bleiben dort, weil sie reine Read-Pfade sind.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_FREQUENT, RATELIMIT_MUTATION
from core.forms.workitems import WorkItemForm
from core.models import WorkItem
from core.services.case import (
    create_workitem,
    update_workitem,
    update_workitem_status,
)
from core.services.client import get_client_or_none
from core.services.events import (
    get_idempotent_result,
    normalize_idempotency_key,
    payload_fingerprint,
    remember_idempotent_result,
)
from core.services.scoping import get_scoped_object
from core.views._json_contracts import (
    _conflict_response,
    _idempotency_mismatch_response,
    _invalid_form_response,
    _wants_json_response,
    _wants_raw_json_response,
)
from core.views.mixins import AssistantOrAboveRequiredMixin, StaffRequiredMixin
from core.views.utils import safe_redirect_path
from core.views.workitems import can_user_mutate_workitem


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
        "updated_at": workitem.updated_at.isoformat() if workitem.updated_at else None,
    }
    return _conflict_response(server_state, client_expected, error=error)


class WorkItemStatusUpdateView(AssistantOrAboveRequiredMixin, View):
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

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="POST", block=True))
    def post(self, request, pk):
        new_status = request.POST.get("status")
        # Refs #819: Django bietet Status.values als Liste an.
        if new_status not in WorkItem.Status.values:
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
        # Kein DB-Backstop wie bei Create (R5): Transitionen haben keine
        # ``idempotency_key``-Spalte; nach Cache-Verlust fängt der
        # Versions-Token den Doppel-Replay als sichtbaren 409 ab (kein
        # stilles Überschreiben).
        idem_key = normalize_idempotency_key(request.headers.get("X-Idempotency-Key"))
        idem_payload_hash = payload_fingerprint(request) if idem_key else None
        if idem_key:
            hit = get_idempotent_result("workitem_status", request.user.pk, idem_key)
            if hit is not None:
                # N14 (#1424): derselbe Key mit ABWEICHENDEM Body ist eine
                # Key-Kollision — kein gecachter Erfolg, sondern 422.
                if hit.payload_hash is not None and hit.payload_hash != idem_payload_hash:
                    return _idempotency_mismatch_response()
                workitem = get_scoped_object(WorkItem, request, pk=pk)
                return self._success_response(request, workitem)

        # Permission-Check + Service-Call innerhalb derselben Transaktion,
        # damit das gelockte Objekt aus dem Service-Layer zurueckgegeben
        # wird und kein parallel laufender Request den Stand zwischen
        # Check und Update veraendern kann (Refs #129 Teil A, Refs #733).
        try:
            with transaction.atomic():
                workitem = get_scoped_object(
                    WorkItem.objects.select_for_update(),
                    request,
                    pk=pk,
                )
                if not can_user_mutate_workitem(request.user, workitem):
                    return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))

                workitem = update_workitem_status(
                    workitem,
                    new_status,
                    request.user,
                    expected_updated_at=expected_updated_at,
                    require_version_token=wants_raw_json,
                )
        except ValidationError as e:
            # Refs #1419 (analog WorkItemUpdateView/#1338): der Offline-Replay
            # braucht den 409 mit Server-Stand, damit der Konflikt in der
            # M8-Liste sichtbar und auflösbar bleibt, statt von der
            # generischen Queue still als Erfolg oder Dead-Letter
            # klassifiziert zu werden.
            workitem.refresh_from_db()
            if _wants_json_response(request):
                if getattr(e, "code", None) == "missing_token":
                    return _workitem_conflict_response(workitem, None, error="missing-token")
                return _workitem_conflict_response(workitem, expected_updated_at)
            # Defensiver HTML-Fallback (praktisch unerreichbar, weil Token/
            # Pflicht nur für Raw-JSON-Clients gesetzt werden).
            messages.error(request, e.message if hasattr(e, "message") else str(e))
            return redirect("core:workitem_inbox")

        remember_idempotent_result(
            "workitem_status", request.user.pk, idem_key, workitem.pk, payload_hash=idem_payload_hash
        )
        return self._success_response(request, workitem)

    def _success_response(self, request, workitem):
        """Erfolgsform des Status-POSTs — geteilt zwischen Erst-Request und
        Idempotenz-Cache-Hit, damit der Replay exakt die Antwort bekommt, der
        die generische Queue als Erfolg folgt (HX: 200-Partial bzw. leere
        Antwort bei ``hide``; sonst Redirect)."""
        if request.headers.get("HX-Request") == "true":
            if request.POST.get("hide"):
                return HttpResponse("")
            return render(request, "core/workitems/partials/item_card.html", {"wi": workitem})

        messages.success(request, _("Status aktualisiert."))
        next_url = safe_redirect_path(request.POST.get("next"))
        if next_url != "/":
            return redirect(next_url)
        return redirect("core:workitem_inbox")


class WorkItemCreateView(StaffRequiredMixin, View):
    """Create a WorkItem."""

    def get(self, request):
        facility = request.current_facility
        form = WorkItemForm(facility=facility)

        client_id = request.GET.get("client")
        client_pseudonym = ""
        if client_id:
            client = get_client_or_none(facility, client_id)
            if client:
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
        # Duplikat zu erzeugen.
        idem_key = normalize_idempotency_key(request.headers.get("X-Idempotency-Key"))
        idem_payload_hash = payload_fingerprint(request) if idem_key else None
        if idem_key:
            hit = get_idempotent_result("workitem_create", request.user.pk, idem_key)
            if hit is not None:
                # N14 (#1424): derselbe Key mit ABWEICHENDEM Body ist eine
                # Key-Kollision — kein gecachter Erfolg, sondern 422 (Dead-
                # Letter-UI; Rationale 422 vs. 409 im Helper-Docstring).
                # Legacy-Eintraege (payload_hash is None, Cache-Wert aus der
                # Zeit vor N14) passieren ohne Hash-Pruefung.
                if hit.payload_hash is not None and hit.payload_hash != idem_payload_hash:
                    return _idempotency_mismatch_response()
                return redirect("core:workitem_inbox")
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
                # R6: zwei parallele Replays desselben Keys passieren beide den
                # Cache-Check (get-then-set-Fenster). NUR der Idempotenz-Unique-
                # Constraint (workitem_idem_key_per_user_uniq) darf als Duplikat
                # gelten — ein fachfremder IntegrityError im selben Replay-Request
                # wuerde sonst faelschlich als Duplikat maskiert, sobald zufaellig
                # eine passende Key-Zeile existiert (Refs #1443). Dann auf das
                # Original umleiten statt 500; jeden anderen IntegrityError re-raisen.
                if idem_key and "idem_key_per_user_uniq" in str(exc):
                    existing = (
                        WorkItem.objects.for_facility(facility)
                        .filter(created_by=request.user, idempotency_key=idem_key)
                        .first()
                    )
                    if existing:
                        return redirect("core:workitem_inbox")
                raise
            # Erfolgreich angelegt → Ergebnis unter dem Idempotenz-Schlüssel
            # merken, damit ein späterer Replay (#1329) hier oben kurzschließt
            # statt ein zweites WorkItem zu erzeugen. Der Payload-Hash (N14)
            # macht eine Key-Kollision mit anderem Body erkennbar. No-op ohne
            # Schlüssel.
            remember_idempotent_result(
                "workitem_create", request.user.pk, idem_key, workitem.pk, payload_hash=idem_payload_hash
            )
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
            WorkItem.objects.select_related("client"),
            request,
            pk=pk,
        )
        # Refs #735: Edit-Pfad richtet sich nach derselben Owner/Assignee-
        # Policy wie der Status-Pfad. Staff-User sehen das Form nur fuer
        # eigene oder zugewiesene WorkItems; Lead/Admin uneingeschraenkt
        # innerhalb ihrer Facility.
        if not can_user_mutate_workitem(request.user, workitem):
            return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))
        form = WorkItemForm(instance=workitem, facility=request.current_facility)
        context = {
            "form": form,
            "workitem": workitem,
            "client_id": str(workitem.client.pk) if workitem.client else "",
            "client_pseudonym": workitem.client.pseudonym if workitem.client else "",
        }
        return render(request, "core/workitems/form.html", context)

    def post(self, request, pk):
        workitem = get_scoped_object(
            WorkItem,
            request,
            pk=pk,
        )
        if not can_user_mutate_workitem(request.user, workitem):
            return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))
        form = WorkItemForm(request.POST, instance=workitem, facility=request.current_facility)

        if form.is_valid():
            expected_updated_at = request.POST.get("expected_updated_at") or None
            wants_json = _wants_json_response(request)
            try:
                update_workitem(
                    workitem,
                    request.user,
                    expected_updated_at=expected_updated_at,
                    # Refs #1351 Task 7 (analog #1338 bei Events): JSON-/
                    # Offline-Replay-Clients müssen den Versions-Token
                    # mitschicken (kein stilles Last-Write-Wins mehr). Der
                    # klassische HTML-Formular-Pfad bleibt unverändert
                    # (kein require).
                    require_version_token=wants_json,
                    client=form.cleaned_data.get("client"),
                    item_type=form.cleaned_data["item_type"],
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    priority=form.cleaned_data["priority"],
                    due_date=form.cleaned_data.get("due_date"),
                    remind_at=form.cleaned_data.get("remind_at"),
                    recurrence=form.cleaned_data.get("recurrence") or WorkItem.Recurrence.NONE,
                    assigned_to=form.cleaned_data.get("assigned_to"),
                )
            except ValidationError as e:
                # Refs #1351 Task 7: JSON/HTMX-Clients erhalten einen 409 mit
                # dem aktuellen Server-Stand, damit der Offline-Konflikt
                # sichtbar bleibt statt von der generischen Queue (die jedem
                # 200/redirect als Erfolg folgt) stillschweigend als
                # "synchronisiert" verworfen zu werden. Normale
                # Browser-Requests behalten das bisherige
                # redirect+Flash-Verhalten.
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
            "client_id": request.POST.get("client", ""),
            "client_pseudonym": "",
        }
        return render(request, "core/workitems/form.html", context)
