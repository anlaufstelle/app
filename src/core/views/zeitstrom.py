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
from django.views.generic import TemplateView
from django_ratelimit.decorators import ratelimit

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
from core.views.mixins import AssistantOrAboveRequiredMixin, StaffRequiredMixin

ZEITSTROM_FILTER_TRANSITION_HEADER = "X-Zeitstrom-Filter-Transition"
ZEITSTROM_VALIDATORS_HEADER = "X-Zeitstrom-Validators"
ZEITSTROM_PREVIOUS_STATE_HEADER = "X-Zeitstrom-Previous-State"

#: Eine Meldung fuer jede Art unbrauchbarer Schichtkoordinaten — sie nennt
#: bewusst keinen Feldnamen und keinen eingegebenen Wert (Refs #2042, E-06).
_SHIFT_UNRESOLVED = _("Die Schicht konnte nicht bestimmt werden. Bitte die Seite neu laden.")


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
            # Refs #2042: die manuelle Schichtnotiz sitzt oben in der
            # Uebergabe — zur angezeigten (zuletzt abgeschlossenen) Schicht
            # und zur laufenden. ``None`` fuer Rollen ohne Leserecht.
            angezeigte_schicht = Shift(target_date, uebergabe_filter)
            context["handover_notes"] = build_handover_note_context(
                facility,
                self.request.user,
                angezeigte_schicht,
                now=now,
            )
            # Refs #2042 (E-04): das Formular fuehrt die ANZEIGE-Koordinaten
            # als Hidden-Fields mit, damit der HTMX-Austausch nach dem
            # Speichern dieselbe Uebergabe zeigt wie der vorherige GET.
            context["handover_note_display"] = angezeigte_schicht
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


class HandoverNoteUpdateView(StaffRequiredMixin, View):
    """HTMX-Endpunkt der manuellen Schichtnotiz (Refs #2042).

    Bewusst eine eigene, kleine POST-Route statt eines Zweigs in
    ``ZeitstromView``: die Uebergabe selbst ist eine GET-Ansicht mit
    Watch-/ETag-Vertrag, in den ein Schreibpfad nicht gehoert. Die Rolle wird
    doppelt geprueft — hier ueber ``StaffRequiredMixin`` (die Uebergabe selbst
    steht der Assistenz offen) und noch einmal im Service, damit kein zweiter
    Aufrufer am Guard vorbeikommt.

    **Zwei Schicht-Koordinatenpaare** (Refs #2042, Befund E-04): das
    Formular sagt mit ``shift_date``/``time_filter``, WELCHE Notiz geschrieben
    wird, und mit ``display_date``/``display_time_filter``, WELCHE Uebergabe
    gerade auf dem Bildschirm steht. Vorher war beides dasselbe — nach dem
    Speichern der laufenden Notiz verengte sich der Austausch auf genau diesen
    Block, und die Notiz der abgebenden Schicht verschwand aus der Seite, bis
    jemand neu lud. Fehlen oder taugen die Anzeigekoordinaten nicht, wird die
    automatische Uebergabe neu aufgeloest — dieselbe Quelle wie im GET.
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
        dieser Lane; der Fall ist im Bericht als bewusste Grenze vermerkt. Fuer
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
