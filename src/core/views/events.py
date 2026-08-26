"""Views for event recording and management."""

import logging
from contextlib import suppress
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.forms.events import DynamicEventDataForm, EventMetaForm
from core.forms.tally import TallyNumberDataForm
from core.models import Client, DocumentType, Event
from core.services.attachments import stage_attachment_uploads, stage_uploads
from core.services.case import get_case_or_none
from core.services.client import CLIENT_WRITE_REVOKED_MESSAGE, get_client_or_none
from core.services.compliance import (
    get_visible_event_or_404,
    user_can_see_document_type,
)
from core.services.dashboard import (
    apply_template,
    get_template_for_user,
    get_templates_for_document_type,
    list_templates_for_user,
)
from core.services.events import (
    TALLY_IDEMPOTENCY_SCOPE,
    TALLY_UNDO_SECONDS,
    TallyUndoDenied,
    apply_attachment_changes,
    build_attachment_context,
    build_event_detail_context,
    build_tally_summary,
    create_event,
    current_tally_context,
    decrypt_event_text_data,
    evaluate_tally_eligibility,
    filtered_server_data_json,
    finalize_staged_files,
    get_idempotent_result,
    has_idempotency_payload_mismatch,
    merge_update_payload,
    mint_tally_undo_token,
    new_tally_idempotency_key,
    normalize_idempotency_key,
    payload_fingerprint,
    remember_idempotent_result,
    remove_restricted_fields,
    request_deletion,
    resolve_default_document_type,
    resolve_idempotency_key,
    soft_delete_event,
    split_file_and_text_data,
    tally_request_matches_live_context,
    undo_tally_event,
    update_event,
)
from core.services.events.fields import file_marker_slugs
from core.services.scoping import get_scoped_object
from core.services.zeitstrom_filters import zeitstrom_filter_state
from core.utils.formatting import isoformat_or_none
from core.views._dynamic_errors import add_dynamic_service_errors
from core.views._json_contracts import (
    _conflict_response,
    _idempotency_mismatch_response,
    _invalid_form_response,
    _wants_json_response,
    _wants_raw_json_response,
)
from core.views.mixins import (
    AssistantOrAboveRequiredMixin,
    IdempotentCreateMixin,
    StaffRequiredMixin,
    is_idempotency_conflict,
)
from core.views.utils import object_back_link, safe_redirect_path

logger = logging.getLogger(__name__)

_VERSION_ERROR_CODES = frozenset({"missing_token", "invalid_token", "version_conflict"})

# Django rendert die Vorlage mit einem einfachen Prozentzeichen, waehrend der
# Template-Extractor den Platzhalter als ``%%(stage)s`` katalogisiert. Der
# No-op-Marker haelt deshalb die tatsaechliche Runtime-ID im Katalog; ohne ihn
# faellt die englische UI auf den deutschen Quelltext zurueck (Refs #1915).
MIN_CONTACT_STAGE_HINT_MSGID = gettext_noop(
    'Für diesen Dokumentationstyp sind nur Personen ab der Kontaktstufe „%(stage)s" auswählbar.'
)


def _event_back_link(event) -> dict[str, str]:
    """Ruecklink der Ereignis-Unterseiten (Refs #1802, Konzept §3.4).

    Bearbeiten und Loesch-Bestaetigung sind Unterseiten DES Ereignisses
    (``events/<pk>/edit/``): ihre Heimat ist das Ereignis, nicht der Zeitstrom.
    Beschriftet wird mit derselben Zeichenkette wie die H1 der Detailseite.
    """
    return object_back_link("core:event_detail", event.document_type.name, pk=event.pk)


def _prefill_owner(meta_form, facility, client_id, case_id):
    """``?client=`` und ``?case=`` in ``meta_form`` vorbelegen (Refs #1813 T4).

    Ersetzt die vormals dreifach wiederholte ``?client=``-Vorbelegung in
    :class:`EventCreateView` — jetzt ergänzt um ``?case=`` (Konzept #1801
    §6.5): ist ein Fall gesetzt, wird zusätzlich die zugehörige Person
    abgeleitet vorbelegt, aber nur, solange kein expliziter ``?client=``
    vorliegt (der gewinnt immer).

    Sicherheit: ``EventMetaForm.case`` führt bereits ein facility-gescoptes
    Queryset als Validierungs-Whitelist — ein manipulierter ``?case=`` kann
    beim Absenden nichts vorbelegen, was der Nutzer nicht ohnehin wählen
    dürfte. Für die abgeleitete Person braucht es trotzdem einen eigenen
    facility-gescopten, fail-closed Lookup (:func:`get_case_or_none`, Muster
    ``get_client_or_none``) — sonst wäre die Ableitung ein Existenz-Orakel
    über Fremd-/widerrufene Fälle.

    Gibt die (ggf. aus dem Fall abgeleitete) ``client_id`` zurück, damit der
    Aufrufer sie für die restliche Kontext-Konstruktion (Pseudonym-Anzeige,
    ``context["client_id"]``) weiterverwenden kann.
    """
    if case_id:
        meta_form.fields["case"].initial = case_id
        case = get_case_or_none(facility, case_id)
        if case is not None and case.client_id and not client_id:
            client_id = str(case.client_id)
    if client_id:
        meta_form.fields["client"].initial = client_id
    return client_id


def _event_conflict_response(user, event, client_expected, *, error="conflict"):
    """Build the 409-Conflict JSON payload for a stale optimistic-concurrency Event edit.

    Assembles the Event-specific ``server_state`` (Refs #1351 Task 7: dieser
    Teil ist NICHT nach ``_json_contracts.py`` mitgezogen, weil er
    Event-spezifisch ist) and delegates the shared envelope shape to
    :func:`core.views._json_contracts._conflict_response`.

    The payload carries enough context for :file:`conflict-resolver.js` to show
    a side-by-side diff: the current server ``data_json`` (sensitivity-filtered,
    so an offline edit never surfaces fields the user is not allowed to see),
    the freshly read ``updated_at`` (which becomes the new
    ``expected_updated_at`` after the user resolves the conflict), the
    document-type display name, and the value the client sent — so the UI can
    label the two sides unambiguously.

    ``error`` (Refs #1338) distinguishes the two JSON failure shapes the
    offline-sync contract defines: the default ``"conflict"`` for a genuine
    version mismatch (also used defensively for a corrupt/unparseable token —
    the server state is shown for review instead of raising a 500) and
    ``"missing-token"`` when the caller omitted ``expected_updated_at``
    entirely on a JSON/HTMX edit. Both share the same body shape, so
    :file:`conflict-resolver.js` needs only one parser.
    """
    server_state = {
        "data_json": filtered_server_data_json(user, event),
        "updated_at": isoformat_or_none(event.updated_at),
        "document_type_name": event.document_type.name,
    }
    return _conflict_response(server_state, client_expected, error=error)


class EventCreateView(AssistantOrAboveRequiredMixin, IdempotentCreateMixin, View):
    """Kontakt-Dokumentation (Create) — online und als Offline-Replay (F-09)."""

    idempotency_scope = "event_create"

    def _invalid_response(self, request, form, context):
        """Ungueltiges Formular beantworten — die EINE Stelle des Raw-JSON-
        Contracts (Refs #1351 Task 8, #1387).

        Der Offline-Replay (roher ``Accept: application/json``) darf ein
        ungueltiges Formular NICHT als Erfolg (200) deuten — sonst verwirft
        die generische Queue die Anlage still als "synchronisiert", obwohl
        nie ein Event entstand (Datenverlust). Bewusst NUR an
        ``_wants_raw_json_response`` gebunden (nicht HX-Request) — Muster wie
        im Update-Pfad (``EventUpdateView.post``). HTML rendert das Formular
        mit dem uebergebenen Kontext neu.
        """
        if _wants_raw_json_response(request):
            return _invalid_form_response(form)
        return render(request, "core/events/create.html", context)

    def on_idempotent_hit(self, request, hit):
        # Replay trifft bereits angelegtes Event → Redirect auf das Original
        # (die generische Queue folgt exakt dem Original-Erfolg).
        return redirect("core:event_detail", pk=hit.pk)

    def get(self, request):
        facility = request.current_facility

        default_doc_type, initial = resolve_default_document_type(facility)
        meta_form = EventMetaForm(facility=facility, user=request.user, initial=initial)

        # Pre-select client/case (?client= und ?case=, Refs #1813 T4 — bei
        # gesetztem ?case= wird die Person daraus abgeleitet, sofern kein
        # expliziter ?client= vorliegt).
        client_id = request.GET.get("client")
        case_id = request.GET.get("case")
        client_id = _prefill_owner(meta_form, facility, client_id, case_id)

        # Quick-Templates (Refs #494): Applied template prefills dynamic fields
        # and selects the associated DocumentType. Template-Auswahl über
        # ``?template=<uuid>`` – der Service liefert nur Templates, deren
        # DocumentType der User sehen darf.
        applied_template = None
        template_id = request.GET.get("template")
        prefill_data = None
        if template_id:
            applied_template = get_template_for_user(request.user, facility, template_id)
            if applied_template is not None:
                default_doc_type = applied_template.document_type
                initial["document_type"] = default_doc_type.pk
                meta_form = EventMetaForm(facility=facility, user=request.user, initial=initial)
                client_id = _prefill_owner(meta_form, facility, client_id, case_id)
                prefill_data = apply_template(applied_template)

        # Serienerfassung (Refs #1349): der „Speichern & nächster Kontakt"-
        # Redirect trägt ``?document_type=<pk>`` — den Typ des gerade
        # gespeicherten Kontakts. Er wird vorgewählt, damit der nächste
        # (bewusst anonyme) Kontakt ohne erneute Typwahl erfasst werden kann.
        # ZWINGEND facility-scoped + Sensitivity-Guard (Muster
        # EventFieldsPartialView): sonst könnte ein Assistant per manipuliertem
        # Query die Feldlabels/Help-Texte eines HIGH-Typs sichtbar machen (IDOR).
        # Fremd-Facility/unbekannt → 404; sichtbarer-aber-unberechtigter Typ →
        # still ignoriert (kein Leak), Formular rendert wie ohne Prefill.
        serienerfassung = False
        doc_type_param = request.GET.get("document_type")
        if doc_type_param and applied_template is None:
            try:
                prefill_doc_type = DocumentType.objects.get(
                    pk=doc_type_param,
                    facility=facility,
                    is_active=True,
                    entity_type=DocumentType.EntityType.EVENT,
                )
            except (DocumentType.DoesNotExist, ValidationError, ValueError):
                raise Http404(_("Dokumentationstyp nicht gefunden.")) from None
            if user_can_see_document_type(request.user, prefill_doc_type):
                default_doc_type = prefill_doc_type
                initial["document_type"] = prefill_doc_type.pk
                meta_form = EventMetaForm(facility=facility, user=request.user, initial=initial)
                client_id = _prefill_owner(meta_form, facility, client_id, case_id)
                serienerfassung = True

        # Pre-render dynamic fields when default document type is set
        if default_doc_type:
            data_form = DynamicEventDataForm(
                document_type=default_doc_type,
                initial_data=prefill_data,
                facility=facility,
            )
            remove_restricted_fields(request.user, default_doc_type, data_form)
            # Refs #1349: Bei der Serienerfassung Fokus auf das erste dynamische
            # Feld setzen, damit direkt getippt werden kann (CSP-konform via
            # HTML-``autofocus``, kein Inline-Script). App-weites Fokus-
            # Management ist #1339 — hier bewusst minimal.
            if serienerfassung and data_form.fields:
                first_field = next(iter(data_form.fields.values()))
                first_field.widget.attrs["autofocus"] = True
        else:
            data_form = DynamicEventDataForm()

        quick_templates = list_templates_for_user(request.user, facility)
        current_doc_type_templates = (
            get_templates_for_document_type(request.user, facility, default_doc_type) if default_doc_type else []
        )

        context = {
            "meta_form": meta_form,
            "data_form": data_form,
            "client_id": client_id or "",
            "client_pseudonym": "",
            # Refs #1813 (T4, §6.5): roher ?case=-Query-Wert, analog client_id —
            # das Case-<select> ist Alpine-/JS-getrieben (eventClientAutocomplete),
            # data-initial-case-id ist der einzige Übergabeweg dorthin.
            "case_id": case_id or "",
            "quick_templates": quick_templates,
            "current_doc_type_templates": current_doc_type_templates,
            "applied_template": applied_template,
            # Refs #1349: Serienerfassungs-Prefill zählt (wie ein Quick-Template)
            # als bewusster Server-Prefill — der autosave-Draft desselben Pfads
            # darf das vorbelegte Formular beim Laden NICHT überschreiben (#625).
            "serienerfassung": serienerfassung,
        }

        client = get_client_or_none(facility, client_id)
        if client:
            context["client_pseudonym"] = client.pseudonym

        return render(request, "core/events/create.html", context)

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request):
        facility = request.current_facility

        # Idempotenz-Guard (F-09, Refs #1109): Bricht beim Offline-Replay die
        # Verbindung nach erfolgreichem Server-Write, aber vor Empfang der
        # Response ab, spielt der Client dieselbe Queue-Zeile erneut. Trägt sie
        # den ``X-Idempotency-Key`` eines bereits angelegten Events, leiten wir
        # auf dieses Event um, statt einen Duplikat-Datensatz zu erzeugen.
        # Sequenz + Mismatch-422: IdempotentCreateMixin (Refs #1831).
        idem_key, idem_payload_hash = self.read_idempotency(request)
        replay_response = self.check_replay(request, idem_key, idem_payload_hash)
        if replay_response is not None:
            return replay_response
        if idem_key:
            # R5/#1909: Cache-Eviction/Neustart ueberlebt der DB-Backstop;
            # neue Events tragen dort auch den Payload-Hash. Legacy-NULL bleibt
            # ein kompatibler Treffer ohne Hash-Prüfung.
            existing = (
                Event.objects.for_facility(facility).filter(created_by=request.user, idempotency_key=idem_key).first()
            )
            if existing:
                if has_idempotency_payload_mismatch(existing.idempotency_payload_hash, idem_payload_hash):
                    return _idempotency_mismatch_response()
                return redirect("core:event_detail", pk=existing.pk)

        meta_form = EventMetaForm(request.POST, facility=facility, user=request.user)

        if not meta_form.is_valid():
            # Läuft wie die drei anderen Invalid-Zweige über
            # ``_invalid_response`` (DoD-Audit Refs #1831, Fold Refs #1996).
            # Bis #1996 schloss der Raw-JSON-Contract hier bewusst VOR der
            # HTML-only-Rekonstruktion kurz: die Rekonstruktion löst
            # ``document_type`` erneut gegen die DB auf und fing dabei nur
            # ``DoesNotExist``/``ValueError`` — bei einem syntaktisch kaputten
            # UUID-Wert wirft Django aber eine ``ValidationError``, also 500
            # statt der erwarteten 422 für den Replay-Client. Seit #1996 fängt
            # die Rekonstruktion (unten) auch ``ValidationError`` mit, die
            # Reihenfolge ist also nicht mehr verhaltensrelevant und der
            # separate Kurzschluss entfällt. Festgenagelt von
            # ``test_event_create_invalid_document_type_uuid_stays_422_json``
            # (JSON) und
            # ``test_event_create_invalid_document_type_uuid_html_stays_422_not_500``
            # (HTML). Änderungen am 422-Contract folglich HIER und in
            # ``_invalid_response`` nachziehen.

            # Preserve client selection on validation error
            client_id = request.POST.get("client", "")
            client_obj = get_client_or_none(facility, client_id)
            client_pseudonym = client_obj.pseudonym if client_obj else ""

            # Re-render dynamic fields for selected document type
            #
            # Refs #774 — Sensitivity-Guard: ohne diesen Check konnte ein
            # Assistant durch invaliden POST mit ``document_type=<HIGH-id>``
            # die Feldlabels/Help-Texte des HIGH-Typs in der Re-Render-Antwort
            # sichtbar machen. Wir spiegeln deshalb die Pruefung aus
            # ``EventFieldsPartialView``: ist ``user_can_see_document_type``
            # False, rendern wir ein leeres ``DynamicEventDataForm``.
            doc_type_id = request.POST.get("document_type")
            data_form = DynamicEventDataForm()
            if doc_type_id:
                try:
                    doc_type = DocumentType.objects.get(
                        pk=doc_type_id,
                        facility=facility,
                        is_active=True,
                        entity_type=DocumentType.EntityType.EVENT,
                    )
                except (DocumentType.DoesNotExist, ValueError, ValidationError):
                    # Django wirft bei syntaktisch kaputten UUID-Strings
                    # ValidationError (nicht ValueError) — Refs #1996, analog
                    # zu ``get_client_or_none``.
                    doc_type = None
                if doc_type is not None and user_can_see_document_type(request.user, doc_type):
                    data_form = DynamicEventDataForm(
                        request.POST, request.FILES, document_type=doc_type, facility=facility
                    )
                    remove_restricted_fields(request.user, doc_type, data_form)

            return self._invalid_response(
                request,
                meta_form,
                {
                    "meta_form": meta_form,
                    "data_form": data_form,
                    "client_id": client_id,
                    "client_pseudonym": client_pseudonym,
                },
            )

        doc_type = meta_form.cleaned_data["document_type"]
        data_form = DynamicEventDataForm(request.POST, request.FILES, document_type=doc_type, facility=facility)
        restricted_keys = remove_restricted_fields(request.user, doc_type, data_form)

        if not data_form.is_valid():
            return self._invalid_response(request, data_form, {"meta_form": meta_form, "data_form": data_form})

        # Refs #1423 (N11): ``EventMetaForm.clean_client`` loest die UUID
        # bereits facility-scoped gegen die DB auf und liefert eine
        # ``Client``-Instanz oder ``None`` — der manuelle Lookup (der eine
        # unbekannte/fremde UUID vorher still verwarf) entfaellt hier.
        client = meta_form.cleaned_data.get("client")

        case = meta_form.cleaned_data.get("case")

        file_fields, text_data = split_file_and_text_data(data_form.cleaned_data)

        # Staging VOR der Transaktion + Cleanup-Garantie bei jedem
        # Nicht-Commit-Ausgang: stage_uploads (Refs #1345/#1831; die ausführliche
        # Begründung — AuditLog-Hashketten-Lock vs. ClamAV-Lauf — steht am CM).
        # Policy-/Virus-Reject (ValidationError aus dem Staging) gehört ans
        # meta_form; Service-ValidationError aus create_event/finalize (die
        # data_json validiert ein zweites Mal, #1938) gehört ans data_form.
        try:
            with stage_uploads(facility, request.user, file_fields) as staged:
                try:
                    # Event + Dateianhänge atomar: scheitert der (jetzt schnelle)
                    # DB-Write, rollt die Transaktion auch das Event zurück —
                    # sonst verweist die DB auf einen Anhang, der nie persistiert
                    # wurde (Refs #584).
                    with transaction.atomic():
                        event = create_event(
                            facility=facility,
                            user=request.user,
                            document_type=doc_type,
                            occurred_at=meta_form.cleaned_data["occurred_at"],
                            data_json=text_data,
                            client=client,
                            case=case,
                            idempotency_key=idem_key,
                            idempotency_payload_hash=idem_payload_hash,
                        )
                        finalize_staged_files(event, request.user, staged.by_slug, doc_type)
                    staged.commit()
                except ValidationError as e:
                    # #1938: ``create_event`` validiert ``data_json`` ein zweites
                    # Mal und liefert den Fehler feldbezogen; er gehört deshalb ans
                    # dynamische Formular, damit ein sichtbares Feld markiert wird
                    # und ein unsichtbares nur die generische Meldung ergibt.
                    # Raw-JSON-Contract: _invalid_response (Refs #1831).
                    add_dynamic_service_errors(data_form, e, restricted_keys)
                    return self._invalid_response(request, data_form, {"meta_form": meta_form, "data_form": data_form})
                except IntegrityError as exc:
                    # R6 (Refs #1443, Prädikat in views/mixins.py): NUR der
                    # Idempotenz-Unique-Constraint darf als Duplikat gelten; dann
                    # auf das Original umleiten statt 500, jeden anderen
                    # IntegrityError re-raisen. Der Duplikat-Replay verweist auf
                    # das Original-Event; die frisch verschluesselten ``.enc``
                    # raeumt stage_uploads auf (#1345).
                    if idem_key and is_idempotency_conflict(exc):
                        existing = (
                            Event.objects.for_facility(facility)
                            .filter(created_by=request.user, idempotency_key=idem_key)
                            .first()
                        )
                        if existing:
                            if has_idempotency_payload_mismatch(existing.idempotency_payload_hash, idem_payload_hash):
                                return _idempotency_mismatch_response()
                            return redirect("core:event_detail", pk=existing.pk)
                    raise
        except ValidationError as e:
            # Policy-/Virus-Reject VOR der Transaktion. Der zugehoerige
            # SECURITY_VIOLATION-Audit wurde bereits (durable) geschrieben und
            # ueberlebt jetzt bewusst (Refs #1345) — er rollt nicht mehr mit dem
            # Event zurueck. Bereits gestagte ``.enc`` hat stage_uploads entfernt.
            # #1938: ``e`` kann feldbezogen sein (dict) — dann traegt es kein
            # ``.message``. Der geteilte Helfer bildet sichtbare Slugs ab und
            # ersetzt alles andere generisch. Raw-JSON-Contract: _invalid_response
            # (Refs #1831).
            add_dynamic_service_errors(meta_form, e, restricted_keys)
            return self._invalid_response(request, meta_form, {"meta_form": meta_form, "data_form": data_form})

        # Erfolgreich angelegt → Ergebnis unter dem Idempotenz-Schlüssel merken,
        # damit ein späterer Replay (F-09) hier oben kurzschließt statt ein
        # zweites Event zu erzeugen. Der Payload-Hash (N14) macht dabei eine
        # Key-Kollision mit anderem Body erkennbar. No-op ohne Schlüssel.
        self.remember_idempotency(request, idem_key, idem_payload_hash, event.pk)

        # Serienerfassung (Refs #1349): „Speichern & nächster Kontakt" leitet
        # zurück auf das leere Formular mit vorgewähltem Dokumentationstyp,
        # damit anonyme Massenkontakte zügig hintereinander erfasst werden
        # können. Bewusst NUR für den klassischen Formular-Submit — NICHT für
        # den Offline-Replay (roher ``Accept: application/json``): dessen
        # generische Queue erwartet unverändert den normalen Erfolgs-Redirect
        # auf die Detailseite, ein abweichender Ziel-Pfad würde den
        # HTTP-Replay-Contract brechen. Der Redirect trägt den Typ, aber
        # bewusst KEINEN client → der nächste Kontakt startet anonym; das
        # ``occurred_at`` wird beim GET neu gesetzt.
        if request.POST.get("_save_and_new") and not _wants_raw_json_response(request):
            messages.success(request, _("Kontakt gespeichert – bereit für den nächsten."))
            return redirect(f"{reverse('core:event_create')}?document_type={doc_type.pk}")

        messages.success(request, _("Kontakt wurde dokumentiert."))
        return redirect("core:event_detail", pk=event.pk)


class EventFieldsPartialView(AssistantOrAboveRequiredMixin, View):
    """HTMX partial: dynamic fields for a DocumentType."""

    def get(self, request):
        doc_type_id = request.GET.get("document_type")
        if not doc_type_id:
            return render(request, "core/events/partials/dynamic_fields.html", {"data_form": None})

        doc_type = get_scoped_object(
            DocumentType,
            request,
            pk=doc_type_id,
            is_active=True,
            entity_type=DocumentType.EntityType.EVENT,
        )
        if not user_can_see_document_type(request.user, doc_type):
            raise PermissionDenied
        data_form = DynamicEventDataForm(document_type=doc_type, facility=request.current_facility)
        remove_restricted_fields(request.user, doc_type, data_form)
        return render(request, "core/events/partials/dynamic_fields.html", {"data_form": data_form})


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True), name="post")
class TallyIncrementView(AssistantOrAboveRequiredMixin, View):
    """Strichlisten-Erfassung anonymer Massenkontakte (Refs #1349/#1906).

    Typen ohne Pflichtfelder bleiben ein direkter ``+1``-POST. Bestehen alle
    Pflichtfelder aus Zahlen, liefert GET das kompakte Inline-Formular und POST
    legt mit dessen bereinigten Werten genau ein Event über ``create_event`` an.
    Audit, EventHistory und Activity-Log greifen damit unverändert pro Kontakt.
    """

    def _resolve_document_type(self, request, params):
        # Facility-Scope: unbekannter/fremder Typ → 404 (verrät keine Existenz
        # in anderer Einrichtung). Alle fachlichen Regeln einschließlich
        # Entitätstyp, Aktivstatus und Feld-Sensitivität liefert die Policy.
        doc_type = get_scoped_object(DocumentType, request, pk=params.get("document_type"))
        eligibility = evaluate_tally_eligibility(doc_type, request.user)
        if eligibility.conceal_from_request:
            raise Http404
        if eligibility.permission_denied:
            raise PermissionDenied
        if not eligibility.eligible:
            message = " ".join(str(reason) for reason in eligibility.reason_messages)
            return eligibility, HttpResponseBadRequest(message)
        return eligibility, None

    @staticmethod
    def _filter_state(params, live_context, *, request_is_live):
        """Keep Tally requests on the server-rendered Zeitstrom contract."""
        target_date = live_context.shift_date
        if request_is_live and (raw_date := params.get("date")):
            with suppress(ValueError):
                target_date = date.fromisoformat(raw_date)
        time_filter_id = (params.get("time_filter") if request_is_live else None) or (
            str(live_context.time_filter.pk) if live_context.time_filter else "all"
        )
        return zeitstrom_filter_state(
            params,
            target_date=target_date,
            time_filter_id=time_filter_id,
        )

    @staticmethod
    def _tally_next(params, filter_state, *, request_is_live):
        """Keep a boundary-crossing form on the current server state."""
        if request_is_live:
            return safe_redirect_path(params.get("next"))
        return f"{reverse('core:zeitstrom')}?{urlencode(filter_state)}"

    @staticmethod
    def _tally_increment_url(
        document_type,
        filter_state,
        tally_next,
        idempotency_key=None,
        *,
        tally_context=None,
    ):
        params = {
            "document_type": document_type.pk,
            **filter_state,
            **(tally_context or {}),
            "next": tally_next,
        }
        if idempotency_key:
            params["idempotency_key"] = idempotency_key
        return "{}?{}".format(
            reverse("core:tally_increment"),
            urlencode(params),
        )

    @classmethod
    def _row_context(cls, request, facility, event, params, live_context, *, request_is_live, tally_undo_token=None):
        summary = build_tally_summary(facility, request.user, context=live_context)
        row = next((entry for entry in summary["rows"] if entry["document_type"].pk == event.document_type_id), None)
        if row is None:
            return None
        filter_state = cls._filter_state(params, live_context, request_is_live=request_is_live)
        tally_next = cls._tally_next(params, filter_state, request_is_live=request_is_live)
        tally_context = cls._tally_context_values(live_context)
        row["tally_increment_url"] = cls._tally_increment_url(
            event.document_type,
            filter_state,
            tally_next,
            row["idempotency_key"],
            tally_context=tally_context,
        )
        return {
            "row": row,
            "tally_summary": summary,
            "tally_filter_state": filter_state,
            "tally_context": tally_context,
            "tally_next": tally_next,
            # Eine Capability wird ausschliesslich dem Response des wirklich
            # frischen ``create_event``-Erfolgs mitgegeben, nie einem Replay.
            "tally_undo_token": tally_undo_token,
            "tally_undo_expires_at": (
                event.created_at + timedelta(seconds=TALLY_UNDO_SECONDS) if tally_undo_token else None
            ),
        }

    @staticmethod
    def _fresh_undo_token(event):
        """Mint nur für den unmittelbar erfolgreichen ``create_event``-Pfad."""
        try:
            return mint_tally_undo_token(event)
        except TallyUndoDenied:
            return None

    @classmethod
    def _success_response(cls, request, facility, event, live_context, *, request_is_live, fresh_event=False):
        """Return the changed row, or a normal navigation without HTMX."""
        filter_state = cls._filter_state(request.POST, live_context, request_is_live=request_is_live)
        tally_next = cls._tally_next(request.POST, filter_state, request_is_live=request_is_live)
        if not request.headers.get("HX-Request"):
            return redirect(tally_next)
        tally_undo_token = cls._fresh_undo_token(event) if fresh_event else None
        context = cls._row_context(
            request,
            facility,
            event,
            request.POST,
            live_context,
            request_is_live=request_is_live,
            tally_undo_token=tally_undo_token,
        )
        if context is None:
            # Das Schreiben war erfolgreich, aber eine parallele Policy- oder
            # Sichtbarkeitsänderung hat die Zeile inzwischen aus der Summary
            # entfernt. Die alte Zeile verschwindet autoritativ; der Feed
            # erhält trotzdem sein Erfolgssignal.
            response = HttpResponse("")
            response["HX-Retarget"] = f"#tally-row-{event.document_type_id}"
            response["HX-Reswap"] = "outerHTML"
            response["HX-Trigger-After-Swap"] = "zeitstrom-changed"
            return response
        response = render(
            request,
            "core/zeitstrom/partials/tally_row.html",
            context,
        )
        response["HX-Retarget"] = f"#tally-row-{event.document_type_id}"
        response["HX-Reswap"] = "outerHTML"
        response["HX-Trigger-After-Swap"] = "zeitstrom-changed"
        return response

    @staticmethod
    def _full_form_response(request, context):
        return render(request, "core/zeitstrom/tally_form.html", context)

    @staticmethod
    def _request_matches_live_context(request, params, live_context):
        return tally_request_matches_live_context(request.current_facility, params, context=live_context)

    @classmethod
    def _live_context_error(cls, request, params, live_context):
        if cls._request_matches_live_context(request, params, live_context):
            return None
        return HttpResponseBadRequest(_("Anonyme Kontakte im aktuellen Zeitfenster"))

    @staticmethod
    def _tally_context_values(live_context):
        """Serialisiere das einmal erfasste Live-Fenster für Folgeaktionen."""
        return {
            "tally_window_start": live_context.window_start.isoformat(),
            "tally_window_end": live_context.window_end.isoformat(),
        }

    def get(self, request):
        facility = request.current_facility
        live_context = current_tally_context(facility)

        if request.GET.get("cancel"):
            eligibility, error_response = self._resolve_document_type(request, request.GET)
            if error_response is not None:
                return error_response
            doc_type = eligibility.document_type
            if not eligibility.required_number_fields:
                return HttpResponseBadRequest(_("Dieser Dokumentationstyp benötigt keine Wert-Eingabe."))
            request_is_live = self._request_matches_live_context(request, request.GET, live_context)
            summary = build_tally_summary(facility, request.user, context=live_context)
            row = next(
                (entry for entry in summary["rows"] if entry["document_type"].pk == doc_type.pk),
                None,
            )
            if row is None:
                return HttpResponseBadRequest(_("Dieser Dokumentationstyp kann nicht per Strichliste erfasst werden."))
            filter_state = self._filter_state(request.GET, live_context, request_is_live=request_is_live)
            context = {
                "row": row,
                "tally_summary": summary,
                "tally_filter_state": filter_state,
                "tally_context": self._tally_context_values(live_context),
                "tally_next": self._tally_next(request.GET, filter_state, request_is_live=request_is_live),
            }
            context["row"]["tally_increment_url"] = self._tally_increment_url(
                doc_type,
                context["tally_filter_state"],
                context["tally_next"],
                context["row"]["idempotency_key"],
                tally_context=context["tally_context"],
            )
            if not request.headers.get("HX-Request"):
                return redirect(context["tally_next"])
            return render(
                request,
                "core/zeitstrom/partials/tally_action.html",
                context,
            )

        if error_response := self._live_context_error(request, request.GET, live_context):
            return error_response
        eligibility, error_response = self._resolve_document_type(request, request.GET)
        if error_response is not None:
            return error_response
        doc_type = eligibility.document_type
        required_fields = eligibility.required_number_fields
        if not required_fields:
            return HttpResponseBadRequest(_("Dieser Dokumentationstyp benötigt keine Wert-Eingabe."))

        idempotency_key = normalize_idempotency_key(request.GET.get("idempotency_key"))

        context = {
            "document_type": doc_type,
            "tally_form": TallyNumberDataForm(eligibility=eligibility),
            "idempotency_key": idempotency_key or new_tally_idempotency_key(),
            "tally_filter_state": self._filter_state(request.GET, live_context, request_is_live=True),
            "tally_context": self._tally_context_values(live_context),
            "tally_next": self._tally_next(request.GET, {}, request_is_live=True),
        }
        context["tally_increment_url"] = self._tally_increment_url(
            doc_type,
            context["tally_filter_state"],
            context["tally_next"],
            context["idempotency_key"],
            tally_context=context["tally_context"],
        )
        if request.headers.get("HX-Request"):
            return render(request, "core/zeitstrom/partials/tally_inline_form.html", context)
        return self._full_form_response(request, context)

    def post(self, request):
        facility = request.current_facility
        live_context = current_tally_context(facility)
        request_is_live = self._request_matches_live_context(request, request.POST, live_context)
        try:
            idempotency_key = resolve_idempotency_key(
                request.headers.get("X-Idempotency-Key"),
                request.POST.get("idempotency_key"),
                required=True,
            )
        except ValueError:
            if not request_is_live:
                return HttpResponseBadRequest(_("Anonyme Kontakte im aktuellen Zeitfenster"))
            return HttpResponseBadRequest(_("Idempotenz-Schlüssel fehlt, ist ungültig oder widersprüchlich."))

        payload_hash = payload_fingerprint(request)
        hit = get_idempotent_result(TALLY_IDEMPOTENCY_SCOPE, request.user.pk, idempotency_key)
        if hit is not None:
            if has_idempotency_payload_mismatch(hit.payload_hash, payload_hash):
                return _idempotency_mismatch_response()
            try:
                event = (
                    Event.objects.for_facility(facility)
                    .filter(pk=hit.pk, created_by=request.user, idempotency_key=idempotency_key)
                    .first()
                )
            except ValidationError:
                event = None
            if event is None:
                return HttpResponseBadRequest(_("Idempotenz-Schlüssel fehlt, ist ungültig oder widersprüchlich."))
            if has_idempotency_payload_mismatch(event.idempotency_payload_hash, payload_hash):
                return _idempotency_mismatch_response()
            return self._success_response(request, facility, event, live_context, request_is_live=request_is_live)
        existing = (
            Event.objects.for_facility(facility)
            .filter(created_by=request.user, idempotency_key=idempotency_key)
            .first()
        )
        if existing is not None:
            if has_idempotency_payload_mismatch(existing.idempotency_payload_hash, payload_hash):
                return _idempotency_mismatch_response()
            return self._success_response(request, facility, existing, live_context, request_is_live=request_is_live)
        if not request_is_live:
            return HttpResponseBadRequest(_("Anonyme Kontakte im aktuellen Zeitfenster"))
        eligibility, error_response = self._resolve_document_type(request, request.POST)
        if error_response is not None:
            return error_response
        doc_type = eligibility.document_type
        required_fields = eligibility.required_number_fields

        data_json = {}
        if required_fields:
            tally_form = TallyNumberDataForm(request.POST, eligibility=eligibility)
            if not tally_form.is_valid():
                if _wants_raw_json_response(request):
                    return _invalid_form_response(tally_form)
                context = {
                    "document_type": doc_type,
                    "tally_form": tally_form,
                    "idempotency_key": idempotency_key,
                    "tally_filter_state": self._filter_state(request.POST, live_context, request_is_live=True),
                    "tally_context": self._tally_context_values(live_context),
                    "tally_next": self._tally_next(request.POST, {}, request_is_live=True),
                }
                context["tally_increment_url"] = self._tally_increment_url(
                    doc_type,
                    context["tally_filter_state"],
                    context["tally_next"],
                    context["idempotency_key"],
                    tally_context=context["tally_context"],
                )
                if request.headers.get("HX-Request"):
                    return render(request, "core/zeitstrom/partials/tally_inline_form.html", context)
                return self._full_form_response(request, context)
            data_json = tally_form.cleaned_data

        try:
            event = create_event(
                facility=facility,
                user=request.user,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json=data_json,
                is_anonymous=True,
                idempotency_key=idempotency_key,
                idempotency_payload_hash=payload_hash,
                created_via=Event.CreatedVia.TALLY,
            )
        except IntegrityError as exc:
            # R6 (Refs #1443, Prädikat in views/mixins.py): NUR der
            # Idempotenz-Unique-Constraint darf als Duplikat gelten.
            if is_idempotency_conflict(exc):
                existing = (
                    Event.objects.for_facility(facility)
                    .filter(created_by=request.user, idempotency_key=idempotency_key)
                    .first()
                )
                if existing is not None:
                    if has_idempotency_payload_mismatch(existing.idempotency_payload_hash, payload_hash):
                        return _idempotency_mismatch_response()
                    return self._success_response(
                        request, facility, existing, live_context, request_is_live=request_is_live
                    )
            raise

        remember_idempotent_result(
            TALLY_IDEMPOTENCY_SCOPE,
            request.user.pk,
            idempotency_key,
            event.pk,
            payload_hash=payload_hash,
        )
        return self._success_response(
            request,
            facility,
            event,
            live_context,
            request_is_live=True,
            fresh_event=True,
        )


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True), name="post")
class TallyUndoView(AssistantOrAboveRequiredMixin, View):
    """POST-only Rücknahme einer signierten Tally-Capability (Refs #1913)."""

    def post(self, request):
        try:
            event = undo_tally_event(request.current_facility, request.user, request.POST.get("token"))
        except TallyUndoDenied as exc:
            # Wie ``get_visible_event_or_404``: keine Unterscheidung zwischen
            # gefälschtem, fremdem, abgelaufenem oder inzwischen geändertem Event.
            raise Http404 from exc

        next_url = safe_redirect_path(request.POST.get("next"))
        if not request.headers.get("HX-Request"):
            return redirect(next_url)

        live_context = current_tally_context(request.current_facility)
        context = TallyIncrementView._row_context(
            request,
            request.current_facility,
            event,
            request.POST,
            live_context,
            request_is_live=TallyIncrementView._request_matches_live_context(request, request.POST, live_context),
        )
        if context is None:
            response = HttpResponse("")
        else:
            response = render(request, "core/zeitstrom/partials/tally_row.html", context)
        response["HX-Retarget"] = f"#tally-row-{event.document_type_id}"
        response["HX-Reswap"] = "outerHTML"
        response["HX-Trigger-After-Swap"] = "zeitstrom-changed"
        return response


class EventDetailView(AssistantOrAboveRequiredMixin, View):
    """Event detail view."""

    def get(self, request, pk):
        event = get_visible_event_or_404(
            request.user,
            request.current_facility,
            pk,
            # ``case`` mitgeladen (Refs #1818): ``_build_secondary_action_flags``
            # prueft dessen Papierkorb-Zustand, sonst eine Extra-Query je Aufruf.
            select_related=("document_type", "client", "created_by", "case"),
        )
        context = build_event_detail_context(event, request.user)
        return render(request, "core/events/detail.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class EventUpdateView(AssistantOrAboveRequiredMixin, View):
    """Edit an event."""

    def dispatch(self, request, *args, **kwargs):
        """Load event and check permissions (assistants may only edit their own events)."""
        if not request.user.is_authenticated:
            # Refs #1072: Kein ORM-Lookup für anonyme Requests — die Mixin-
            # Kette (LoginRequiredMixin) liefert den Login-Redirect mit
            # ``next``-Parameter wie alle anderen geschützten Endpoints.
            return super().dispatch(request, *args, **kwargs)
        self.event = get_visible_event_or_404(
            request.user,
            request.current_facility,
            kwargs["pk"],
            select_related=("document_type", "client"),
        )
        if not request.user.is_staff_or_above and self.event.created_by != request.user:
            raise PermissionDenied
        # Refs #1771/#1774: Schreibgate gegen widerrufene Personen. Anders als die
        # Create-Pfade loest diese View den Personenbezug nicht ueber
        # ``EventMetaForm.clean_client`` auf (der Client ist am Ereignis fixiert),
        # deshalb greift ``resolve_writable_client`` hier nie — die Grenze muss
        # explizit gezogen werden. Ein Offline-Replay oder ein staler Deep-Link
        # auf ein Ereignis einer anonymisierten (``is_active=False``) oder
        # papierkorb-geloeschten (``is_deleted=True``) Person darf keinen frischen
        # Klartext (``data_json``/``search_text``) und keine neuen
        # Attachment-``.enc``-Dateien schreiben; ``anonymize_clients`` erfasst
        # diesen Bestand nie wieder. Auf ``dispatch`` (nicht nur ``post``), damit
        # auch das GET-Bearbeiten-Formular fuer eine fort-Person 404 liefert.
        client = self.event.client
        if client is not None and (client.is_deleted or not client.is_active):
            if _wants_raw_json_response(request):
                # Offline-Replay: 422, damit ``offline-queue.js`` den Eintrag als
                # endgueltigen Konflikt (dead-letter) einsortiert statt zu retryen.
                return JsonResponse(
                    {"error": "invalid", "errors": {"client": [{"message": str(CLIENT_WRITE_REVOKED_MESSAGE)}]}},
                    status=422,
                )
            # HTML/Deep-Link: die Person ist fort — 404 wie an den uebrigen
            # Widerrufs-Flaechen (Personen-/Fall-Detail).
            raise Http404(CLIENT_WRITE_REVOKED_MESSAGE)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        event = self.event

        # Decrypted data as initial_data (skip file markers, both legacy __file__
        # and new __files__ format). Refs #1160: gemeinsame Service-Helfer.
        initial_data = decrypt_event_text_data(event)

        data_form = DynamicEventDataForm(
            document_type=event.document_type,
            initial_data=initial_data,
            facility=request.current_facility,
            # E17 (Refs #1057): Marker werden von decrypt_event_text_data gestrippt —
            # die FILE-Slugs mit bestehendem Anhang separat durchreichen, damit ein
            # vorhandener Anhang beim Bearbeiten als „gefüllt" zählt.
            existing_file_slugs=file_marker_slugs(event.data_json),
        )

        # Remove sensitive fields from the form
        remove_restricted_fields(request.user, event.document_type, data_form)

        context = {
            "event": event,
            "data_form": data_form,
            "existing_attachments": build_attachment_context(event),
            "back_link": _event_back_link(event),
        }
        return render(request, "core/events/edit.html", context)

    def post(self, request, pk):
        event = self.event
        facility = request.current_facility

        # Pass existing data so inactive options stay in choices for validation.
        # Refs #1160: gemeinsame Service-Helfer (vorher dupliziert mit get()).
        existing_data = decrypt_event_text_data(event)

        data_form = DynamicEventDataForm(
            request.POST,
            request.FILES,
            document_type=event.document_type,
            initial_data=existing_data,
            facility=facility,
            # E17 (Refs #1057): bestehende FILE-Anhänge zählen bei der
            # Hidden-Required-Prüfung als „gefüllt" (Marker aus data_json).
            existing_file_slugs=file_marker_slugs(event.data_json),
        )

        # Remove sensitive fields and preserve existing values
        restricted_keys = remove_restricted_fields(request.user, event.document_type, data_form)

        if data_form.is_valid():
            file_fields, merged = split_file_and_text_data(data_form.cleaned_data)

            # Refs #1160: Restricted-Felder + bestehende FILE-Marker re-injizieren.
            merged = merge_update_payload(event, merged, restricted_keys, event.document_type)

            expected_updated_at = request.POST.get("expected_updated_at")
            wants_json = _wants_json_response(request)
            # Event-Update + Attachment-Versionierung atomar (Refs #584/#587/#622).
            # Refs #1956: Scan + Fernet-Verschluesselung liegen als Staging VOR
            # dem atomic — im Block faellt nur noch der schnelle DB-Write an.
            # Refs #1958: ``update_event`` schreibt in diesem Block jetzt
            # tatsaechlich eine AuditLog-Zeile — ihr per-Einrichtung-Advisory-
            # Lock faellt erst mit dem Commit, hier also erst nach dem
            # ``apply_attachment_changes`` darunter, und laege ohne das Staging
            # zusaetzlich ueber dem gesamten ClamAV-Scan. ``merged`` traegt die
            # kuenftige Markerlage (``merge_update_payload``), damit die
            # REPLACE-Aufloesung beider Seiten identisch ist.
            committed = False
            staged_all: list = []
            try:
                staged_by_key, staged_all = stage_attachment_uploads(
                    event,
                    request.user,
                    request.FILES,
                    file_fields,
                    event.document_type,
                    data_json=merged,
                )
                with transaction.atomic():
                    update_event(
                        event,
                        request.user,
                        merged,
                        expected_updated_at=expected_updated_at,
                        # Refs #1338: JSON-/Offline-Replay-Clients müssen den
                        # Versions-Token mitschicken (kein stilles Last-Write-
                        # Wins mehr). Der klassische HTML-Formular-Pfad bleibt
                        # unverändert (kein require).
                        require_version_token=wants_json,
                    )
                    apply_attachment_changes(
                        event,
                        request.user,
                        request.POST,
                        request.FILES,
                        file_fields,
                        event.document_type,
                        staged_by_key=staged_by_key,
                    )
                committed = True
            except ValidationError as e:
                # #1936: Nur die drei expliziten Optimistic-Locking-Codes
                # duerfen in den 409-Konfliktresolver. Fachliche/service-seitige
                # ValidationErrors sind kein Versionskonflikt.
                error_code = getattr(e, "code", None)
                if wants_json and error_code in _VERSION_ERROR_CODES:
                    # Refresh the event so we emit the committed server state,
                    # not the in-memory copy this view started from.
                    event.refresh_from_db()
                    if error_code == "missing_token":
                        # Refs #1338: fehlender Token ist kein Merge-Konflikt
                        # im eigentlichen Sinn (es wurde nichts verglichen) —
                        # eigene Fehlerkennung, damit der Client zwischen
                        # "bitte Token nachreichen" und "echter Konflikt"
                        # unterscheiden kann. client_expected ist null, weil
                        # kein sinnvoller roher Client-Wert vorliegt.
                        return _event_conflict_response(request.user, event, None, error="missing-token")
                    return _event_conflict_response(request.user, event, expected_updated_at)
                safe_messages = add_dynamic_service_errors(data_form, e, restricted_keys)
                if _wants_raw_json_response(request):
                    return _invalid_form_response(data_form)
                messages.error(request, "; ".join(safe_messages))
                return redirect("core:event_update", pk=event.pk)
            finally:
                # Refs #1345/#1956: Jeder Nicht-Commit-Ausgang laesst die
                # vorab verschluesselten ``.enc`` verwaisen — restlos entfernen
                # (Cleanup-Garantie aus #662).
                if not committed:
                    for staged in staged_all:
                        staged.output_path.unlink(missing_ok=True)

            messages.success(request, _("Ereignis wurde aktualisiert."))
            return redirect("core:event_detail", pk=event.pk)

        # Formular ungueltig. Refs #1111: der Offline-Replay (Accept: application/json)
        # darf ein ungueltiges Formular NICHT als Erfolg (HTTP 200) deuten — sonst
        # verwirft er den Edit still als "synchronisiert" (Datenverlust von Art.9-/
        # §203-Dokumentation). Daher 422 mit Feldfehlern. Bewusst NUR an
        # _wants_raw_json_response gebunden (nicht _wants_json_response, das auch
        # HX-Request erfasst): ein normaler HTML-/HTMX-Submit behaelt das
        # 200-Re-Render mit inline-Formularfehlern. (Refs #1351 Task 8: Helper
        # nach _json_contracts.py gezogen, sobald die Create-Views denselben
        # Zweig brauchten — verhaltensneutral.)
        if _wants_raw_json_response(request):
            return _invalid_form_response(data_form)
        context = {
            "event": event,
            "data_form": data_form,
            # Refs #1056 (P4): wie im GET-Pfad — sonst verschwinden bestehende
            # Anhang-Zeilen beim invalid-POST-Re-Render aus dem Formular.
            "existing_attachments": build_attachment_context(event),
            "back_link": _event_back_link(event),
        }
        return render(request, "core/events/edit.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class EventDeleteView(StaffRequiredMixin, View):
    """Delete an event (with four-eyes principle for qualified data)."""

    def dispatch(self, request, *args, **kwargs):
        """Load event and check permissions (staff may only delete their own events)."""
        if not request.user.is_authenticated:
            # Refs #1072: Kein ORM-Lookup für anonyme Requests — die Mixin-
            # Kette (LoginRequiredMixin) liefert den Login-Redirect mit
            # ``next``-Parameter wie alle anderen geschützten Endpoints.
            return super().dispatch(request, *args, **kwargs)
        self.event = get_visible_event_or_404(
            request.user,
            request.current_facility,
            kwargs["pk"],
            select_related=("document_type", "client"),
        )
        if not request.user.is_lead_or_admin and self.event.created_by != request.user:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return render(
            request,
            "core/events/delete_confirm.html",
            {"event": self.event, "back_link": _event_back_link(self.event)},
        )

    def post(self, request, pk):
        event = self.event

        reason = request.POST.get("reason", "")

        # Four-eyes principle: qualified clients require a deletion request
        if event.client and event.client.contact_stage == Client.ContactStage.QUALIFIED:
            request_deletion(event, request.user, reason)
            messages.info(request, _("Löschantrag wurde gestellt und muss von einer Leitung genehmigt werden."))
        else:
            soft_delete_event(event, request.user)
            messages.success(request, _("Ereignis wurde gelöscht."))

        return redirect("core:zeitstrom")
