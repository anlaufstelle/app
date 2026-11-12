"""Views for the Zeitstrom (unified activity stream) start page."""

import json
from contextlib import contextmanager
from datetime import date as date_cls
from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.utils.http import parse_etags
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.http import require_http_methods
from django.views.generic import TemplateView

from core import nav
from core.constants import RATELIMIT_MUTATION
from core.models import DocumentType, TimeFilter
from core.services.case import build_handover_summary
from core.services.case.handover_note import (
    HandoverNoteConflictError,
    Shift,
    assert_shift_date_supported,
    build_handover_note_context,
    decrypt_note,
    note_revision,
    save_note,
)
from core.services.dashboard import build_focus_box, staff_dashboard_context
from core.services.events import (
    build_feed_items,
    build_tally_summary,
    current_tally_context,
    enrich_events_with_preview,
)
from core.services.events.list_filters import event_list_params_from_zeitstrom
from core.services.system import get_active_bans
from core.services.zeitstrom_display import format_shift_window
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
from core.utils.ratelimit import ratelimit
from core.views.mixins import AssistantOrAboveRequiredMixin, StaffRequiredMixin

ZEITSTROM_FILTER_TRANSITION_HEADER = "X-Zeitstrom-Filter-Transition"
ZEITSTROM_VALIDATORS_HEADER = "X-Zeitstrom-Validators"
ZEITSTROM_PREVIOUS_STATE_HEADER = "X-Zeitstrom-Previous-State"

#: Eine Meldung fuer jede Art unbrauchbarer Schichtkoordinaten — sie nennt
#: bewusst keinen Feldnamen und keinen eingegebenen Wert (Refs #2042, E-06).
_SHIFT_UNRESOLVED = _("Die Schicht konnte nicht bestimmt werden. Bitte die Seite neu laden.")


def _contacts_navigation_context(user, state):
    """Render the same contacts destination on full pages and feed refreshes."""
    return {
        "contacts_navigation": nav.CONTACTS if nav.is_visible(nav.CONTACTS, user) else None,
        "contacts_list_params": urlencode(event_list_params_from_zeitstrom(state)),
    }


#: Feed type pills (design: als-screens.jsx filter pills).
_FEED_FILTER_OPTIONS = (
    ("", _("Alle")),
    ("events", _("Kontakte")),
    ("activities", _("Aktivitäten")),
    ("workitems", _("Aufgaben")),
    ("bans", _("Hausverbote")),
)


class HandoverSummaries:
    """Request-scoped reuse of ``build_handover_summary`` (Refs #1997).

    The feed's shift summary, the handover view and the current-shift box can
    ask for the same facility, user, date and shift within one request. Each
    distinct shift is built once; the key carries every shift attribute the
    summary reads, so two different shifts never share a result. One instance
    lives for one request only.
    """

    def __init__(self, facility, user):
        self._facility = facility
        self._user = user
        self._built = {}

    @staticmethod
    def _shift_key(time_filter):
        if time_filter is None:
            return None
        return (time_filter.pk, time_filter.label, time_filter.start_time, time_filter.end_time)

    def __call__(self, target_date, time_filter):
        key = (target_date, self._shift_key(time_filter))
        if key not in self._built:
            self._built[key] = build_handover_summary(self._facility, target_date, time_filter, self._user)
        return self._built[key]


def _feed_context(facility, user, state):
    """Feed cards with previews for the resolved date, shift and filters."""
    feed_items = build_feed_items(
        facility,
        state.target_date,
        state.feed_type,
        time_filter=state.time_filter,
        user=user,
        # The document-type filter (from Timeline) runs in the query, before
        # the feed cap.
        document_type_id=state.doc_type_id or None,
    )
    enrich_events_with_preview(feed_items, user)
    return {"feed_items": feed_items}


def _selection_url(state, day, *, shift=None, keep_shift=True):
    """Query string for the same view on *day* (and optionally another shift)."""
    params = {"date": day.isoformat()}
    if state.is_handover:
        params["view"] = "uebergabe"
    else:
        if state.feed_type:
            params["type"] = state.feed_type
        if state.doc_type_id:
            params["doc_type"] = state.doc_type_id
    if shift is not None:
        params["time_filter"] = shift
    elif keep_shift:
        params["time_filter"] = state.time_filter_id
    return "?" + urlencode(params)


def _selection_context(state, *, today):
    """Header facts shared by the full page and the feed refresh.

    The header is the only place that names the selected date and shift. When
    the selection is not the running shift, a hint line says so and links back.
    """
    live_filter = state.live_context.time_filter
    in_feed = not state.is_handover
    selection_is_live = in_feed and state.is_live_tally_context
    hint = None
    if in_feed and not selection_is_live:
        hint = {
            "shift_label": state.time_filter.label if state.time_filter is not None else "",
            "has_live_shift": live_filter is not None,
        }
    prev_day = state.target_date - timedelta(days=1)
    next_day = state.target_date + timedelta(days=1)
    return {
        "prev_url": _selection_url(state, prev_day),
        "next_url": _selection_url(state, next_day) if next_day <= today else None,
        "today_url": ("?view=uebergabe" if state.is_handover else reverse("core:zeitstrom")),
        "shift_urls": {
            "all": _selection_url(state, state.target_date, shift="all"),
            **{item.pk: _selection_url(state, state.target_date, shift=str(item.pk)) for item in state.time_filters},
        },
        "live_time_filter": live_filter,
        "live_shift_date": state.live_context.shift_date,
        "selection_is_live": selection_is_live,
        "selection_hint": hint,
    }


def _filter_context(facility, user, state, *, today):
    """Date navigation, shift and type filters, and the document-type choices."""
    from core.services.compliance import allowed_sensitivities_for_user

    document_types = (
        DocumentType.objects.for_facility(facility)
        .filter(
            is_active=True,
            # M2 (Refs #1058): event types only; client and case types do not
            # belong in the event filter dropdown (decision E21).
            entity_type=DocumentType.EntityType.EVENT,
            sensitivity__in=allowed_sensitivities_for_user(user),
        )
        .order_by("name")
    )
    return {
        **_selection_context(state, today=today),
        "time_filters": state.time_filters,
        "selected_filter": state.time_filter,
        "selected_filter_id": state.time_filter_id,
        "target_date": state.target_date,
        "prev_date": state.target_date - timedelta(days=1),
        "next_date": state.target_date + timedelta(days=1),
        "today": today,
        "selected_type": state.feed_type,
        "filter_options": list(_FEED_FILTER_OPTIONS),
        "document_types": document_types,
        "selected_doc_type": state.doc_type_id or "",
        "events_partial_url": "core:zeitstrom_feed_partial",
        "zeitstrom_filter_state": state.params,
    }


def _sidebar_context(facility, user, state, summaries):
    """Team focus box, current-shift box and active house bans."""
    return {
        # Team focus box (Refs #1128): open and running tasks grouped by
        # urgency instead of a flat top-5 list.
        "focus_box": build_focus_box(facility),
        # Refs #1138: always the shift running now, independent of the date
        # and shift selected in the Zeitstrom.
        "current_shift_summary": build_current_shift_summary(facility, user, state, summary_builder=summaries),
        "active_bans": get_active_bans(facility, user=user),
    }


def _cockpit_context(facility, user):
    """Role cockpit at the top of the start page for staff and assistants (Refs #1124)."""
    if user.is_lead_or_admin or user.is_super_admin:
        return {}
    return {"show_cockpit": True, "cockpit": staff_dashboard_context(user, facility)}


def _handover_context(facility, user, state, summaries, *, now):
    """Shift summary of the selected shift; in handover mode also the notes (Refs #1124)."""
    selected_filter = state.time_filter
    context = {
        "handover_summary": summaries(state.target_date, selected_filter) if selected_filter else None,
    }
    if not state.is_handover:
        return context
    # Refs #2042: the manual shift note sits at the top of the handover, for
    # the displayed (latest completed) shift and the running one; ``None``
    # for roles without read access. The form carries the displayed
    # coordinates as hidden fields so the HTMX swap after saving shows the
    # same handover as the previous GET (#2042).
    displayed_shift = Shift(state.target_date, selected_filter)
    context.update(
        {
            "uebergabe_mode": True,
            "uebergabe_summary": summaries(state.target_date, selected_filter),
            "handover_notes": build_handover_note_context(facility, user, displayed_shift, now=now),
            "handover_note_display": displayed_shift,
        }
    )
    return context


def _tally_context(facility, user, state):
    """Live tally panel; the costly summary is built only when the panel is shown."""
    show_tally_panel = state.is_live_tally_context
    tally_summary = build_tally_summary(facility, user, context=state.live_context) if show_tally_panel else None
    if tally_summary is not None:
        tally_summary["window_text"] = format_shift_window(tally_summary["window_start"], tally_summary["window_end"])
    return {"show_tally_panel": show_tally_panel, "tally_summary": tally_summary}


class ZeitstromView(AssistantOrAboveRequiredMixin, TemplateView):
    """Unified activity stream combining Dashboard + Aktivitätslog + Timeline."""

    template_name = "core/zeitstrom/index.html"

    def get_login_url(self):
        """Use the public scenario selection as the demo entry point."""
        if getattr(settings, "DEMO_MODE", False):
            return reverse("demo_scenario_select")
        return super().get_login_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        facility = self.request.current_facility
        user = self.request.user
        # One instant and one resolved state for every context part.
        today = timezone.localdate()
        now = timezone.localtime()
        state = resolve_zeitstrom_state(
            self.request.GET,
            facility,
            today=today,
            now=now,
            live_context=current_tally_context(facility),
        )
        summaries = HandoverSummaries(facility, user)

        context.update(_feed_context(facility, user, state))
        context.update(_filter_context(facility, user, state, today=today))
        context.update(_sidebar_context(facility, user, state, summaries))
        context.update(_handover_context(facility, user, state, summaries, now=now))
        context.update(_contacts_navigation_context(user, state))
        context.update(_cockpit_context(facility, user))
        context.update(_tally_context(facility, user, state))

        # URLs and watch validators stay at the HTTP boundary.
        context["tally_next"] = f"{self.request.path}?{urlencode(state.params)}"
        if context["tally_summary"] is not None:
            self._add_tally_action_urls(context["tally_summary"], state.params, context["tally_next"])
        context["zeitstrom_page_scope"] = build_zeitstrom_page_scope(user, facility)
        context["zeitstrom_tally_topology"] = build_zeitstrom_tally_topology_validator(
            user,
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


class HandoverNoteUpdateView(StaffRequiredMixin, View):
    """HTMX-Endpunkt der manuellen Schichtnotiz (Refs #2042).

    Bewusst eine eigene, kleine POST-Route statt eines Zweigs in
    ``ZeitstromView``: die Uebergabe selbst ist eine GET-Ansicht mit
    Watch-/ETag-Vertrag, in den ein Schreibpfad nicht gehoert. Die Rolle wird
    doppelt geprueft — hier ueber ``StaffRequiredMixin`` (die Uebergabe selbst
    steht der Assistenz offen) und noch einmal im Service, damit kein zweiter
    Aufrufer am Guard vorbeikommt.

    **Two pairs of shift coordinates** (Refs #2042, finding E-04): the form
    identifies the note to write with ``shift_date``/``time_filter`` and the
    handover on screen with ``display_date``/``display_time_filter``. Previously
    both were the same: saving the current note narrowed the replacement to
    that block and hid the outgoing shift's note until reload. Missing or
    invalid display coordinates trigger automatic handover resolution using
    the same source as GET.
    """

    http_method_names = ["get", "post"]
    template_name = "core/handover/partials/_note.html"

    def get(self, request):
        """Return the existing note partial as a validated Watch follow-up."""

        if request.headers.get("X-Zeitstrom-Watch") != "1":
            return HttpResponseNotAllowed(["POST"])
        if not _scope_matches_request(request):
            return _reload_required(json_response=False)
        with _consistent_watch_render():
            now = timezone.localtime()
            state = resolve_zeitstrom_state(
                request.GET,
                request.current_facility,
                today=timezone.localdate(now),
                now=now,
            )
            if not state.is_handover:
                return _reload_required(json_response=False)
            displayed_shift = Shift(state.target_date, state.time_filter)
            context = {
                "handover_notes": build_handover_note_context(
                    request.current_facility,
                    request.user,
                    displayed_shift,
                    now=now,
                ),
                "handover_note_display": displayed_shift,
            }
            response = render(request, self.template_name, context)
            validator = build_zeitstrom_component_validator(
                request.user,
                request.current_facility,
                state,
                "handover_note",
            )
            response["ETag"] = validator
            if validator not in parse_etags(request.headers.get("If-Match", "")):
                response = HttpResponse(status=412)
                response["ETag"] = validator
            return _watch_cache_headers(response)

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request):
        facility = request.current_facility
        if facility is None:
            # Einrichtungslose Konten (super_admin traegt die Bindung per Design
            # nicht) haben keine Schicht, in die sie schreiben koennten — 403 wie
            # jede andere facility-gebundene Schreibroute, nicht 400 (Refs #2042,
            # AuthZ-Guard ``test_authz_facility_less``).
            raise PermissionDenied
        now = timezone.localtime()
        today = timezone.localdate()

        schicht = self._parse_shift(request, facility, "shift_date", "time_filter", today=today)
        fehler: list[str] = []
        gespeicherte_schicht = None
        konflikt = None
        if schicht is None:
            # Malformte Koordinaten sind ein **Client**-Fehler: so etwas kann
            # die eigene Oberflaeche nicht erzeugen (Refs #2042, E-06).
            return self._render(
                request,
                facility,
                now=now,
                today=today,
                fehler=[str(_SHIFT_UNRESOLVED)],
                gespeicherte_schicht=None,
                konflikt=None,
                status=400,
            )
        if request.POST.get("discard") == "1":
            # „Verwerfen" nach einem Anlagekonflikt: nichts schreiben, nur den
            # gespeicherten Stand neu zeigen (Refs #2042, R-E-23).
            return self._render(
                request,
                facility,
                now=now,
                today=today,
                fehler=[],
                gespeicherte_schicht=None,
                konflikt=None,
            )
        try:
            save_note(
                facility,
                schicht,
                request.user,
                request.POST.get("note", ""),
                now=now,
                # Nur der Abgleich schickt dieses Feld mit. Fehlt es, ist der
                # POST eine gewoehnliche Bearbeitung und laeuft wie bisher
                # (Refs #2042, R-E-25: der Bestandsvergleich gilt dem
                # Konflikt-Abgleich, nicht jeder Bearbeitung).
                base_revision=request.POST.get("base_revision"),
            )
            gespeicherte_schicht = schicht
        except HandoverNoteConflictError as exc:
            fehler = list(exc.messages)
            traeger = (exc.bestand.updated_by or exc.bestand.author) if exc.bestand is not None else None
            konflikt = {
                "shift": schicht,
                "vorhanden": exc.bestand is not None,
                "bestand": decrypt_note(exc.bestand),
                "eingabe": exc.eingabe,
                "autor": str(traeger) if traeger else None,
                "zeit": exc.bestand.updated_at if exc.bestand is not None else None,
                # Frische Revision: der naechste Abgleich gilt genau DER
                # Fassung, die diese Antwort zeigt.
                "base_revision": note_revision(exc.bestand),
            }
        except ValidationError as exc:
            # Fachliche Ausgaenge (Fenster zu, Schicht-Identitaet) sind ein
            # ERGEBNIS, kein Protokollfehler — und muessen im HTMX-Pfad
            # sichtbar bleiben, der 4xx nicht einwechselt (s. ``_render``).
            fehler = list(exc.messages)

        return self._render(
            request,
            facility,
            now=now,
            today=today,
            fehler=fehler,
            gespeicherte_schicht=gespeicherte_schicht,
            konflikt=konflikt,
        )

    @staticmethod
    def _parse_shift(request, facility, date_key, filter_key, *, today):
        """Schichtkoordinaten aus dem POST — ``None``, wenn sie nicht taugen.

        Datum und UUID werden geprueft, **bevor** irgendein Fenster gerechnet
        wird (Refs #2042, E-06): ein syntaktisch gueltiges ``9999-12-31`` lief
        sonst beim Folgetag in einen ``OverflowError``, eine kaputte UUID in
        eine ungefangene ``ValidationError`` — beides HTTP 500.
        """
        raw_date = request.POST.get(date_key)
        if raw_date is None:
            return None
        try:
            shift_date = date_cls.fromisoformat(raw_date)
        except (TypeError, ValueError):
            return None
        raw_filter = request.POST.get(filter_key, "")
        if not raw_filter:
            shift = Shift(shift_date, None)
        else:
            try:
                canonical = UUID(raw_filter)
            except (TypeError, ValueError, AttributeError):
                return None
            time_filter = TimeFilter.objects.for_facility(facility).filter(pk=canonical).first()
            if time_filter is None:
                return None
            shift = Shift(shift_date, time_filter)
        try:
            assert_shift_date_supported(shift, today=today)
        except ValidationError:
            return None
        return shift

    def _display_shift(self, request, facility, *, today, now):
        """Die Schicht, deren Uebergabe angezeigt wird — nie die Schreibschicht."""
        schicht = self._parse_shift(request, facility, "display_date", "display_time_filter", today=today)
        if schicht is not None:
            return schicht
        state = resolve_zeitstrom_state({"view": "uebergabe"}, facility, today=today, now=now)
        return Shift(state.target_date, state.time_filter)

    def _render(self, request, facility, *, now, today, fehler, gespeicherte_schicht, konflikt, status=200):
        """Rendert den kanonischen Anzeigekontext.

        **Zum Statuscode** (Refs #2042, E-06): ``400`` gibt es ausschliesslich
        fuer malformte Schichtkoordinaten — eine Anfrage, die die eigene
        Oberflaeche nicht stellen kann. Fachliche Ausgaenge (Fenster
        geschlossen, Schicht-Identitaet, Anlagekonflikt) bleiben bei ``200``,
        und zwar bewusst: das vendorierte htmx 2.0.10 traegt die Vorgabe
        ``responseHandling: [{code:"[45]..", swap:false, error:true}]``, wechselt
        4xx-Antworten also **nicht** ein. Ein 400 auf einem Ergebnis, das die
        Fachkraft lesen muss, waere im HTMX-Pfad eine stumme Antwort. Die
        globale htmx-Konfiguration liegt in ``base.html`` und damit ausserhalb
        dieses Moduls; dieser Fall bleibt hier bewusst unbehandelt. Fuer
        den 400er bleibt der nicht-HTMX-Pfad (das Formular traegt ``action``)
        sichtbar, und der Client-Fehler ist im Netzwerk-Log eindeutig.
        """
        angezeigt = self._display_shift(request, facility, today=today, now=now)
        context = {
            "handover_notes": build_handover_note_context(facility, request.user, angezeigt, now=now),
            "handover_note_display": angezeigt,
            "handover_note_errors": fehler,
            "handover_note_saved": gespeicherte_schicht is not None,
            # Refs #2042: NUR der gerade gespeicherte Block bekommt den Fokus
            # zurueck. Ein ``autofocus`` an jedem Block schickte ihn nach dem
            # Speichern in das erste Textfeld der Seite — also in eine fremde
            # Schicht (WCAG 2.4.3: der Fokus gehoert an das Ausgangselement).
            "handover_note_saved_shift": gespeicherte_schicht,
            # Refs #2042 (R-E-23): beide Fassungen des Anlagekonflikts.
            "handover_note_conflict": konflikt,
        }
        return render(request, self.template_name, context, status=status)


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


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
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

        context.update(_feed_context(facility, self.request.user, state))
        context.update(_tally_context(facility, self.request.user, state))
        filter_state = state.params
        tally_next = f"{reverse('core:zeitstrom')}?{urlencode(filter_state)}"
        if context["tally_summary"] is not None:
            ZeitstromView._add_tally_action_urls(context["tally_summary"], filter_state, tally_next)
        context["tally_filter_state"] = filter_state
        context["tally_next"] = tally_next
        # Beim durch eine Tally-Aktion ausgelösten Feed-Refresh darf #1910
        # nicht das komplette Panel OOB ersetzen: dort können andere Zeilen
        # gerade ein offenes Formular enthalten. Normale Filterwechsel
        # behalten den OOB-Mechanismus.
        context["tally_panel_oob"] = self.request.GET.get("feed_only") != "1"
        context["zeitstrom_filter_state"] = filter_state
        context.update(_contacts_navigation_context(self.request.user, state))
        context["contacts_navigation_oob"] = True
        context.update(_selection_context(state, today=timezone.localdate()))
        context["selection_hint_oob"] = True
        return context


def _watch_cache_headers(response):
    response["Cache-Control"] = "no-store, private"
    patch_vary_headers(response, ("Cookie",))
    return response


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
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


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
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


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
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
                ),
                "selection_is_live": not state.is_handover and state.is_live_tally_context,
            }
            return self.render_watch_followup(request, self.template_name, context, state)


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
class ZeitstromFocusBoxPartialView(AssistantOrAboveRequiredMixin, ZeitstromWatchFollowupMixin, View):
    template_name = "core/zeitstrom/partials/focus_box_slot.html"
    watch_component = "focus_box"

    def get(self, request):
        with _consistent_watch_render():
            state = resolve_zeitstrom_state(request.GET, request.current_facility)
            context = {"focus_box": build_focus_box(request.current_facility)}
            return self.render_watch_followup(request, self.template_name, context, state)


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
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
