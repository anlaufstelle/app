"""Views for event recording and management."""

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.forms.events import DynamicEventDataForm, EventMetaForm
from core.models import Client, DocumentType
from core.services.client import get_client_or_none
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
    apply_attachment_changes,
    attach_files_to_new_event,
    build_attachment_context,
    build_event_detail_context,
    create_event,
    decrypt_event_text_data,
    filtered_server_data_json,
    get_idempotent_result,
    merge_update_payload,
    remember_idempotent_result,
    remove_restricted_fields,
    request_deletion,
    resolve_default_document_type,
    soft_delete_event,
    split_file_and_text_data,
    update_event,
)
from core.views.mixins import AssistantOrAboveRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


def _wants_json_response(request) -> bool:
    """Return True if the caller prefers a JSON response (HTMX/fetch).

    Used by :class:`EventUpdateView` to decide whether an optimistic-concurrency
    conflict should emit a 409 JSON body (Stage 3, Refs #575) or the classic
    HTML redirect+flash fallback for normal form submissions.
    """
    accept = (request.headers.get("Accept") or "").lower()
    if "application/json" in accept:
        return True
    # HTMX requests implicitly want a partial/data response, not a full redirect.
    return bool(request.headers.get("HX-Request"))


def _conflict_response(user, event, client_expected):
    """Build the 409-Conflict JSON payload for a stale optimistic-concurrency edit.

    The payload carries enough context for :file:`conflict-resolver.js` to show
    a side-by-side diff: the current server ``data_json`` (sensitivity-filtered,
    so an offline edit never surfaces fields the user is not allowed to see),
    the freshly read ``updated_at`` (which becomes the new
    ``expected_updated_at`` after the user resolves the conflict), the
    document-type display name, and the value the client sent — so the UI can
    label the two sides unambiguously.
    """
    return JsonResponse(
        {
            "error": "conflict",
            "server_state": {
                "data_json": filtered_server_data_json(user, event),
                "updated_at": event.updated_at.isoformat() if event.updated_at else None,
                "document_type_name": event.document_type.name,
            },
            "client_expected": client_expected,
        },
        status=409,
    )


class EventCreateView(AssistantOrAboveRequiredMixin, View):
    """Create an event (quick entry)."""

    def get(self, request):
        facility = request.current_facility

        default_doc_type, initial = resolve_default_document_type(facility)
        meta_form = EventMetaForm(facility=facility, user=request.user, initial=initial)

        # Pre-select client
        client_id = request.GET.get("client")
        if client_id:
            meta_form.fields["client"].initial = client_id

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
                if client_id:
                    meta_form.fields["client"].initial = client_id
                prefill_data = apply_template(applied_template)

        # Pre-render dynamic fields when default document type is set
        if default_doc_type:
            data_form = DynamicEventDataForm(
                document_type=default_doc_type,
                initial_data=prefill_data,
                facility=facility,
            )
            remove_restricted_fields(request.user, default_doc_type, data_form)
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
            "quick_templates": quick_templates,
            "current_doc_type_templates": current_doc_type_templates,
            "applied_template": applied_template,
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
        idem_key = request.headers.get("X-Idempotency-Key")
        if idem_key:
            existing_pk = get_idempotent_result("event_create", request.user.pk, idem_key)
            if existing_pk:
                return redirect("core:event_detail", pk=existing_pk)

        meta_form = EventMetaForm(request.POST, facility=facility, user=request.user)

        if not meta_form.is_valid():
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
                    doc_type = DocumentType.objects.get(pk=doc_type_id, facility=facility, is_active=True)
                except (DocumentType.DoesNotExist, ValueError):
                    doc_type = None
                if doc_type is not None and user_can_see_document_type(request.user, doc_type):
                    data_form = DynamicEventDataForm(
                        request.POST, request.FILES, document_type=doc_type, facility=facility
                    )
                    remove_restricted_fields(request.user, doc_type, data_form)

            return render(
                request,
                "core/events/create.html",
                {
                    "meta_form": meta_form,
                    "data_form": data_form,
                    "client_id": client_id,
                    "client_pseudonym": client_pseudonym,
                },
            )

        doc_type = meta_form.cleaned_data["document_type"]
        data_form = DynamicEventDataForm(request.POST, request.FILES, document_type=doc_type, facility=facility)
        remove_restricted_fields(request.user, doc_type, data_form)

        if not data_form.is_valid():
            return render(
                request,
                "core/events/create.html",
                {"meta_form": meta_form, "data_form": data_form},
            )

        client = None
        client_id = meta_form.cleaned_data.get("client")
        if client_id:
            client = Client.objects.for_facility(facility).filter(pk=client_id).first()

        case = meta_form.cleaned_data.get("case")

        file_fields, text_data = split_file_and_text_data(data_form.cleaned_data)

        # Event + Dateianhänge atomar: scheitert die File-Verschlüsselung
        # (Virus, Disk, Fernet), rollt die Transaktion auch das Event zurück
        # — sonst verweist die DB auf einen Anhang, der nie persistiert
        # wurde (Refs #584).
        try:
            with transaction.atomic():
                event = create_event(
                    facility=facility,
                    user=request.user,
                    document_type=doc_type,
                    occurred_at=meta_form.cleaned_data["occurred_at"],
                    data_json=text_data,
                    client=client,
                    case=case,
                )
                attach_files_to_new_event(event, request.user, file_fields, doc_type)
        except ValidationError as e:
            meta_form.add_error(None, e.message)
            return render(
                request,
                "core/events/create.html",
                {"meta_form": meta_form, "data_form": data_form},
            )

        # Erfolgreich angelegt → Ergebnis unter dem Idempotenz-Schlüssel merken,
        # damit ein späterer Replay (F-09) hier oben kurzschließt statt ein
        # zweites Event zu erzeugen. No-op ohne Schlüssel.
        remember_idempotent_result("event_create", request.user.pk, idem_key, event.pk)

        messages.success(request, _("Kontakt wurde dokumentiert."))
        return redirect("core:event_detail", pk=event.pk)


class EventFieldsPartialView(AssistantOrAboveRequiredMixin, View):
    """HTMX partial: dynamic fields for a DocumentType."""

    def get(self, request):
        doc_type_id = request.GET.get("document_type")
        if not doc_type_id:
            return render(request, "core/events/partials/dynamic_fields.html", {"data_form": None})

        doc_type = get_object_or_404(
            DocumentType,
            pk=doc_type_id,
            facility=request.current_facility,
            is_active=True,
        )
        if not user_can_see_document_type(request.user, doc_type):
            raise PermissionDenied
        data_form = DynamicEventDataForm(document_type=doc_type, facility=request.current_facility)
        remove_restricted_fields(request.user, doc_type, data_form)
        return render(request, "core/events/partials/dynamic_fields.html", {"data_form": data_form})


class EventDetailView(AssistantOrAboveRequiredMixin, View):
    """Event detail view."""

    def get(self, request, pk):
        event = get_visible_event_or_404(
            request.user,
            request.current_facility,
            pk,
            select_related=("document_type", "client", "created_by"),
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
        )

        # Remove sensitive fields from the form
        remove_restricted_fields(request.user, event.document_type, data_form)

        context = {
            "event": event,
            "data_form": data_form,
            "existing_attachments": build_attachment_context(event),
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
        )

        # Remove sensitive fields and preserve existing values
        restricted_keys = remove_restricted_fields(request.user, event.document_type, data_form)

        if data_form.is_valid():
            file_fields, merged = split_file_and_text_data(data_form.cleaned_data)

            # Refs #1160: Restricted-Felder + bestehende FILE-Marker re-injizieren.
            merged = merge_update_payload(event, merged, restricted_keys, event.document_type)

            expected_updated_at = request.POST.get("expected_updated_at")
            # Event-Update + Attachment-Versionierung atomar (Refs #584/#587/#622).
            try:
                with transaction.atomic():
                    update_event(event, request.user, merged, expected_updated_at=expected_updated_at)
                    apply_attachment_changes(
                        event,
                        request.user,
                        request.POST,
                        request.FILES,
                        file_fields,
                        event.document_type,
                    )
            except ValidationError as e:
                # Stage 3 (#575): JSON/HTMX clients receive a 409 with the
                # current server state so the client-side conflict resolver
                # can show a diff. Normal browser requests fall back to the
                # previous redirect + flash message behaviour.
                if _wants_json_response(request):
                    # Refresh the event so we emit the committed server state,
                    # not the in-memory copy this view started from.
                    event.refresh_from_db()
                    return _conflict_response(request.user, event, expected_updated_at)
                messages.error(request, str(e.message))
                return redirect("core:event_update", pk=event.pk)

            messages.success(request, _("Ereignis wurde aktualisiert."))
            return redirect("core:event_detail", pk=event.pk)

        context = {
            "event": event,
            "data_form": data_form,
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
        return render(request, "core/events/delete_confirm.html", {"event": self.event})

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
