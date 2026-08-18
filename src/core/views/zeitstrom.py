"""Views for the Zeitstrom (unified activity stream) start page."""

import json
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import connection, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.crypto import constant_time_compare
from django.utils.http import parse_etags
from django.views import View
from django.views.generic import TemplateView

from core.models import DocumentType
from core.services.case import build_handover_summary
from core.services.dashboard import build_focus_box, staff_dashboard_context
from core.services.events import (
    build_feed_items,
    build_tally_summary,
    current_tally_context,
    enrich_events_with_preview,
)
from core.services.system import get_active_bans
from core.services.zeitstrom_filters import ZEITSTROM_FILTER_KEYS, resolve_zeitstrom_state
from core.services.zeitstrom_watch import (
    WATCH_COMPONENTS,
    build_current_shift_summary,
    build_zeitstrom_component_validator,
    build_zeitstrom_page_scope,
    build_zeitstrom_tally_topology_validator,
    build_zeitstrom_watch_snapshot,
    zeitstrom_page_scope_matches,
)
from core.views.mixins import AssistantOrAboveRequiredMixin

ZEITSTROM_FILTER_TRANSITION_HEADER = "X-Zeitstrom-Filter-Transition"
ZEITSTROM_VALIDATORS_HEADER = "X-Zeitstrom-Validators"
ZEITSTROM_PREVIOUS_STATE_HEADER = "X-Zeitstrom-Previous-State"


class ZeitstromView(AssistantOrAboveRequiredMixin, TemplateView):
    """Unified activity stream combining Dashboard + Aktivitätslog + Timeline."""

    template_name = "core/zeitstrom/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        facility = self.request.current_facility
        today = timezone.localdate()
        now = timezone.localtime()
        live_context = current_tally_context(facility)
        state = resolve_zeitstrom_state(
            self.request.GET,
            facility,
            today=today,
            now=now,
            live_context=live_context,
        )
        target_date = state.target_date
        feed_type = state.feed_type
        live_context = state.live_context
        time_filters = state.time_filters
        selected_filter = state.time_filter
        selected_filter_id = state.time_filter_id

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
        doc_type_id = state.doc_type_id
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
                # M2 (Refs #1058): nur event-Typen — client-/case-Typen gehoeren
                # nicht in den Event-Filter-Dropdown (Entscheid E21).
                entity_type=DocumentType.EntityType.EVENT,
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

        # Dienst-Übersicht der aktuell *laufenden* Schicht (Refs #1138).
        # Bewusst unabhängig von der Datums-/Schicht-Auswahl im Zeitstrom:
        # Die Box bezieht sich immer auf den gerade laufenden Dienst, damit sie
        # beim Durchklicken anderer Tage/Schichten nicht mitwechselt.
        current_shift_summary = build_current_shift_summary(facility, self.request.user, state)

        # Die teure Eignungs-/Count-Summary wird erst gebaut, wenn das Panel
        # im finalen Kontext tatsächlich gerendert wird.
        tally_summary = None
        show_tally_panel = False

        # Active bans (from Dashboard)
        active_bans = get_active_bans(facility, user=self.request.user)

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
                "current_shift_summary": current_shift_summary,
                "tally_summary": tally_summary,
                "show_tally_panel": show_tally_panel,
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
                "zeitstrom_filter_state": state.params,
            }
        )

        # Rollen-Cockpit als Kopf der Start-Seite — nur Fachkraft/Assistenz
        # (Leitung/Admin behalten /start/). Refs #1124.
        if not self.request.user.is_lead_or_admin and not self.request.user.is_super_admin:
            context["show_cockpit"] = True
            context["cockpit"] = staff_dashboard_context(self.request.user, facility)

        # Übergabe-Modus: Schicht-Zusammenfassung als Primärinhalt (Refs #1124).
        if state.is_handover:
            uebergabe_filter = selected_filter
            context["uebergabe_mode"] = True
            context["uebergabe_summary"] = build_handover_summary(
                facility, target_date, uebergabe_filter, self.request.user
            )
            context["selected_filter"] = uebergabe_filter
            context["selected_filter_id"] = str(uebergabe_filter.pk) if uebergabe_filter else "all"
            context["zeitstrom_filter_state"] = state.params
        else:
            show_tally_panel = state.is_live_tally_context
            if show_tally_panel:
                tally_summary = build_tally_summary(facility, self.request.user, context=live_context)
            context["show_tally_panel"] = show_tally_panel
            context["tally_summary"] = tally_summary

        context["tally_next"] = f"{self.request.path}?{urlencode(context['zeitstrom_filter_state'])}"
        if context["tally_summary"] is not None:
            self._add_tally_action_urls(
                context["tally_summary"], context["zeitstrom_filter_state"], context["tally_next"]
            )

        context["zeitstrom_page_scope"] = build_zeitstrom_page_scope(
            self.request.user,
            facility,
        )
        context["zeitstrom_tally_topology"] = build_zeitstrom_tally_topology_validator(
            self.request.user,
            facility,
            state,
            tally_summary=context["tally_summary"],
        )
        return context

    @staticmethod
    def _add_tally_action_urls(tally_summary, filter_state, tally_next):
        tally_context = {
            "tally_window_start": tally_summary["window_start"].isoformat(),
            "tally_window_end": tally_summary["window_end"].isoformat(),
        }
        for row in tally_summary["rows"]:
            row["tally_increment_url"] = "{}?{}".format(
                reverse("core:tally_increment"),
                urlencode(
                    {
                        "document_type": row["document_type"].pk,
                        "idempotency_key": row["idempotency_key"],
                        **filter_state,
                        **tally_context,
                        "next": tally_next,
                    }
                ),
            )


def _reload_required(*, json_response):
    if json_response:
        return _watch_cache_headers(JsonResponse({"detail": "reload_required"}, status=409))
    return _watch_cache_headers(HttpResponse(status=409))


def _scope_matches_request(request):
    facility = request.current_facility
    return facility is not None and zeitstrom_page_scope_matches(
        request.headers.get("X-Zeitstrom-Page-Scope", ""),
        request.user,
        facility,
    )


def _parse_filter_state_header(raw):
    if not raw or len(raw) > 2048:
        return None
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(state, dict) or set(state) != set(ZEITSTROM_FILTER_KEYS):
        return None
    if any(not isinstance(state[key], str) or len(state[key]) > 256 for key in ZEITSTROM_FILTER_KEYS):
        return None
    return {key: state[key] for key in ZEITSTROM_FILTER_KEYS}


def _parse_transition_topology(raw):
    if not raw or len(raw) > 1024:
        return None
    try:
        validators = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(validators, dict) or set(validators) != {"tally_topology"}:
        return None
    topology = validators["tally_topology"]
    if not isinstance(topology, str) or len(topology) > 256:
        return None
    return topology


def _requested_filter_state(request):
    if any(key not in request.GET for key in ZEITSTROM_FILTER_KEYS):
        return None
    return {key: request.GET.get(key, "") for key in ZEITSTROM_FILTER_KEYS}


def _transition_baseline_matches(request, state, *, today, now):
    requested = _requested_filter_state(request)
    previous = _parse_filter_state_header(request.headers.get(ZEITSTROM_PREVIOUS_STATE_HEADER, ""))
    previous_topology = _parse_transition_topology(request.headers.get(ZEITSTROM_VALIDATORS_HEADER, ""))
    if requested is None or previous is None or previous_topology is None:
        return False
    if state.params != requested:
        return False
    if any(previous[key] != requested[key] for key in ZEITSTROM_FILTER_KEYS if key != "time_filter"):
        return False

    previous_state = resolve_zeitstrom_state(
        previous,
        request.current_facility,
        today=today,
        now=now,
    )
    if previous_state.params != previous:
        return False
    current_topology = build_zeitstrom_tally_topology_validator(
        request.user,
        request.current_facility,
        previous_state,
    )
    return constant_time_compare(previous_topology, current_topology)


@contextmanager
def _consistent_watch_render():
    """Use one DB snapshot for a Watch follow-up body and its ETag."""

    nested = connection.in_atomic_block
    with transaction.atomic():
        if not nested:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        yield


class ZeitstromWatchFollowupMixin:
    """Bind Watch-triggered HTML to page scope and the advertised snapshot."""

    watch_component: str

    def dispatch(self, request, *args, **kwargs):
        if request.headers.get("X-Zeitstrom-Watch") == "1" and not _scope_matches_request(request):
            return _reload_required(json_response=False)
        return super().dispatch(request, *args, **kwargs)

    def render_watch_followup(
        self,
        request,
        template_name,
        context,
        state,
        *,
        tally_summary=None,
        status=200,
    ):
        response = render(request, template_name, context, status=status) if status != 204 else HttpResponse(status=204)
        validator = build_zeitstrom_component_validator(
            request.user,
            request.current_facility,
            state,
            self.watch_component,
            tally_summary=tally_summary,
        )
        response["ETag"] = validator
        if request.headers.get("X-Zeitstrom-Watch") == "1":
            candidates = parse_etags(request.headers.get("If-Match", ""))
            if validator not in candidates:
                response = HttpResponse(status=412)
                response["ETag"] = validator
        return _watch_cache_headers(response)


class ZeitstromFeedPartialView(AssistantOrAboveRequiredMixin, ZeitstromWatchFollowupMixin, TemplateView):
    """HTMX partial: feed list for the Zeitstrom."""

    template_name = "core/zeitstrom/partials/feed_list.html"
    watch_component = "feed"

    @staticmethod
    def _is_time_filter_transition(request):
        return (
            request.headers.get("HX-Request") == "true"
            and request.headers.get(ZEITSTROM_FILTER_TRANSITION_HEADER) == "time_filter"
            and request.GET.get("feed_only") != "1"
        )

    def get(self, request, *args, **kwargs):
        if request.headers.get("X-Zeitstrom-Watch") == "1":
            with _consistent_watch_render():
                self._watch_state = resolve_zeitstrom_state(request.GET, request.current_facility)
                context = self.get_context_data(**kwargs)
                return self.render_watch_followup(request, self.template_name, context, self._watch_state)
        if not self._is_time_filter_transition(request):
            return super().get(request, *args, **kwargs)
        if not _scope_matches_request(request):
            return _reload_required(json_response=False)
        with _consistent_watch_render():
            today = timezone.localdate()
            now = timezone.localtime()
            self._watch_state = resolve_zeitstrom_state(
                request.GET,
                request.current_facility,
                today=today,
                now=now,
            )
            if not _transition_baseline_matches(
                request,
                self._watch_state,
                today=today,
                now=now,
            ):
                return _reload_required(json_response=False)
            context = self.get_context_data(**kwargs)
            response = render(request, self.template_name, context)
            validators = {
                "feed": build_zeitstrom_component_validator(
                    request.user,
                    request.current_facility,
                    self._watch_state,
                    "feed",
                ),
                "tally_topology": build_zeitstrom_tally_topology_validator(
                    request.user,
                    request.current_facility,
                    self._watch_state,
                    tally_summary=context["tally_summary"],
                ),
            }
            response["ETag"] = validators["feed"]
            response[ZEITSTROM_VALIDATORS_HEADER] = json.dumps(validators, separators=(",", ":"))
            return _watch_cache_headers(response)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        facility = self.request.current_facility
        state = getattr(self, "_watch_state", None) or resolve_zeitstrom_state(self.request.GET, facility)
        live_context = state.live_context
        target_date = state.target_date
        feed_type = state.feed_type
        time_filter = state.time_filter

        feed_items = build_feed_items(
            facility,
            target_date,
            feed_type,
            time_filter=time_filter,
            user=self.request.user,
        )
        enrich_events_with_preview(feed_items, self.request.user)

        # Document type filter
        doc_type_id = state.doc_type_id
        if doc_type_id:
            feed_items = [
                item
                for item in feed_items
                if item["type"] != "event" or str(item["object"].document_type_id) == doc_type_id
            ]

        context["feed_items"] = feed_items
        show_tally_panel = state.is_live_tally_context
        tally_summary = (
            build_tally_summary(facility, self.request.user, context=live_context) if show_tally_panel else None
        )
        filter_state = state.params
        tally_next = f"{reverse('core:zeitstrom')}?{urlencode(filter_state)}"
        if tally_summary is not None:
            ZeitstromView._add_tally_action_urls(tally_summary, filter_state, tally_next)
        context["tally_summary"] = tally_summary
        context["show_tally_panel"] = show_tally_panel
        context["tally_filter_state"] = filter_state
        context["tally_next"] = tally_next
        # Beim durch eine Tally-Aktion ausgelösten Feed-Refresh darf #1910
        # nicht das komplette Panel OOB ersetzen: dort können andere Zeilen
        # gerade ein offenes Formular enthalten. Normale Filterwechsel
        # behalten den OOB-Mechanismus.
        context["tally_panel_oob"] = self.request.GET.get("feed_only") != "1"
        context["zeitstrom_filter_state"] = filter_state
        return context


def _watch_cache_headers(response):
    response["Cache-Control"] = "no-store, private"
    patch_vary_headers(response, ("Cookie",))
    return response


class ZeitstromWatchView(View):
    """PII-free conditional JSON endpoint for multi-user Zeitstrom updates."""

    http_method_names = ["get"]

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return _watch_cache_headers(
                JsonResponse(
                    {"detail": "authentication_required", "login_url": settings.LOGIN_URL},
                    status=401,
                )
            )
        if not user.is_assistant_or_above or request.current_facility is None:
            return _watch_cache_headers(JsonResponse({"detail": "forbidden"}, status=403))
        if not _scope_matches_request(request):
            return _reload_required(json_response=True)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        state = resolve_zeitstrom_state(request.GET, request.current_facility)
        previous = {}
        raw_validators = request.headers.get("X-Zeitstrom-Validators", "")
        if raw_validators and len(raw_validators) <= 4096:
            try:
                parsed_validators = json.loads(raw_validators)
            except (TypeError, ValueError):
                parsed_validators = {}
            if isinstance(parsed_validators, dict):
                previous = {
                    component: value
                    for component, value in parsed_validators.items()
                    if component in WATCH_COMPONENTS and isinstance(value, str) and len(value) <= 256
                }
        snapshot = build_zeitstrom_watch_snapshot(
            request.user,
            request.current_facility,
            state,
            previous_validators=previous,
        )
        if_none_match = request.headers.get("If-None-Match")
        if if_none_match:
            candidates = parse_etags(if_none_match)
            if "*" in candidates or snapshot.etag in candidates:
                response = HttpResponse(status=304)
                response["ETag"] = snapshot.etag
                return _watch_cache_headers(response)

        response = JsonResponse(
            {
                "components": list(snapshot.changed_components),
                "validators": snapshot.changed_validators,
            }
        )
        response["ETag"] = snapshot.etag
        return _watch_cache_headers(response)


class ZeitstromTallyCountsPartialView(AssistantOrAboveRequiredMixin, ZeitstromWatchFollowupMixin, View):
    """Authoritative live counts only; never rows, actions, or forms."""

    http_method_names = ["get"]
    watch_component = "tally_counts"

    def get(self, request):
        with _consistent_watch_render():
            state = resolve_zeitstrom_state(request.GET, request.current_facility)
            if not state.is_live_tally_context:
                return self.render_watch_followup(request, "", {}, state, status=204)
            summary = build_tally_summary(request.current_facility, request.user, context=state.live_context)
            return self.render_watch_followup(
                request,
                "core/zeitstrom/partials/tally_counts.html",
                {"rows": summary["rows"]},
                state,
                tally_summary=summary,
            )


class ZeitstromCurrentShiftPartialView(AssistantOrAboveRequiredMixin, ZeitstromWatchFollowupMixin, View):
    template_name = "core/zeitstrom/partials/current_shift_slot.html"
    watch_component = "current_shift"

    def get(self, request):
        with _consistent_watch_render():
            state = resolve_zeitstrom_state(request.GET, request.current_facility)
            context = {
                "current_shift_summary": build_current_shift_summary(
                    request.current_facility,
                    request.user,
                    state,
                )
            }
            return self.render_watch_followup(request, self.template_name, context, state)


class ZeitstromFocusBoxPartialView(AssistantOrAboveRequiredMixin, ZeitstromWatchFollowupMixin, View):
    template_name = "core/zeitstrom/partials/focus_box_slot.html"
    watch_component = "focus_box"

    def get(self, request):
        with _consistent_watch_render():
            state = resolve_zeitstrom_state(request.GET, request.current_facility)
            context = {"focus_box": build_focus_box(request.current_facility)}
            return self.render_watch_followup(request, self.template_name, context, state)


class ZeitstromBansPartialView(AssistantOrAboveRequiredMixin, ZeitstromWatchFollowupMixin, View):
    """Autoritatives Hausverbots-Banner als Watch-Follow-up (Refs #1917).

    Nur HIER wird die Hausverbots-Historie geladen und entschlüsselt — exakt
    wie beim Voll-Seiten-Render (``get_active_bans`` inkl. rollenbasierter
    Grund-Redaktion, ungekürzt). Der Poll selbst liest ausschließlich die
    opake ``BanRevision``-Zeile. REPEATABLE READ bindet Body und ETag an
    denselben DB-Snapshot.
    """

    template_name = "core/zeitstrom/partials/bans_slot.html"
    watch_component = "bans"

    def get(self, request):
        with _consistent_watch_render():
            state = resolve_zeitstrom_state(request.GET, request.current_facility)
            context = {"active_bans": get_active_bans(request.current_facility, user=request.user)}
            return self.render_watch_followup(request, self.template_name, context, state)
