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
from core.models import Client, DocumentType, FieldTemplate
from core.services.clients import get_client_or_none
from core.services.encryption import safe_decrypt
from core.services.event import (
    apply_attachment_changes,
    attach_files_to_new_event,
    build_event_detail_context,
    build_field_template_lookup,
    create_event,
    filtered_server_data_json,
    remove_restricted_fields,
    request_deletion,
    soft_delete_event,
    split_file_and_text_data,
    update_event,
)
from core.services.file_vault import (
    get_original_filename,
)
from core.services.quick_templates import (
    apply_template,
    get_template_for_user,
    get_templates_for_document_type,
    list_templates_for_user,
)
from core.services.sensitivity import (
    get_visible_event_or_404,
    user_can_see_document_type,
)
from core.utils.formatting import format_file_size
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
    if request.headers.get("HX-Request"):
        return True
    return False


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

        # Load default document type from settings
        default_doc_type = None
        initial = {}
        try:
            settings = facility.settings
            if settings.default_document_type_id:
                default_doc_type = settings.default_document_type
                if default_doc_type.is_active and default_doc_type.facility == facility:
                    initial["document_type"] = default_doc_type.pk
                else:
                    default_doc_type = None
        except facility._meta.get_field("settings").related_model.DoesNotExist:
            pass

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
        meta_form = EventMetaForm(request.POST, facility=facility, user=request.user)

        if not meta_form.is_valid():
            # Preserve client selection on validation error
            client_id = request.POST.get("client", "")
            client_obj = get_client_or_none(facility, client_id)
            client_pseudonym = client_obj.pseudonym if client_obj else ""

            # Re-render dynamic fields for selected document type
            doc_type_id = request.POST.get("document_type")
            data_form = DynamicEventDataForm()
            if doc_type_id:
                try:
                    doc_type = DocumentType.objects.get(pk=doc_type_id, facility=facility, is_active=True)
                    data_form = DynamicEventDataForm(
                        request.POST, request.FILES, document_type=doc_type, facility=facility
                    )
                except (DocumentType.DoesNotExist, ValueError):
                    pass

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
        # and new __files__ format).
        initial_data = {}
        for key, value in (event.data_json or {}).items():
            if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
                continue
            initial_data[key] = safe_decrypt(value, default="")

        data_form = DynamicEventDataForm(
            document_type=event.document_type,
            initial_data=initial_data,
            facility=request.current_facility,
        )

        # Remove sensitive fields from the form
        remove_restricted_fields(request.user, event.document_type, data_form)

        # Build attachment info for file fields — jetzt pro Slug eine Liste
        # von Einträgen (Refs #622). Rückwärtskompatibel: legacy __file__
        # wird per normalize_file_marker auch als Liste mit einem Eintrag
        # gerendert.
        from core.services.event import normalize_file_marker

        existing_attachments_by_slug = {}
        for slug, value in (event.data_json or {}).items():
            entries_meta = normalize_file_marker(value)
            if not entries_meta:
                continue
            entries = []
            for entry in entries_meta:
                att = event.attachments.filter(pk=entry["id"]).first()
                if not att or att.deleted_at is not None:
                    continue
                entries.append(
                    {
                        "entry_id": str(att.entry_id),
                        "attachment_id": str(att.pk),
                        "filename": get_original_filename(att),
                        "size": format_file_size(att.file_size),
                        "sort_order": att.sort_order,
                    }
                )
            if entries:
                existing_attachments_by_slug[slug] = entries

        context = {
            "event": event,
            "data_form": data_form,
            "existing_attachments": existing_attachments_by_slug,
        }
        return render(request, "core/events/edit.html", context)

    def post(self, request, pk):
        event = self.event
        facility = request.current_facility

        # Pass existing data so inactive options stay in choices for validation
        existing_data = {}
        for key, value in (event.data_json or {}).items():
            if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
                continue  # File fields don't use initial_data
            existing_data[key] = safe_decrypt(value, default="")

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

            # Re-insert restricted fields with original values
            for key in restricted_keys:
                if key in (event.data_json or {}):
                    merged[key] = event.data_json[key]

            # Für FILE-Felder: bestehenden Marker (beide Formate) erstmal
            # beibehalten — apply_attachment_changes passt ihn anschliessend an.
            field_templates = build_field_template_lookup(event.document_type)
            for slug, ft in field_templates.items():
                if ft.field_type == FieldTemplate.FieldType.FILE:
                    existing_marker = (event.data_json or {}).get(slug)
                    if isinstance(existing_marker, dict) and (
                        existing_marker.get("__file__") or existing_marker.get("__files__")
                    ):
                        merged[slug] = existing_marker

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
