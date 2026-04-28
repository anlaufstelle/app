"""Views for event recording and management."""

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.forms.events import DynamicEventDataForm, EventMetaForm
from core.models import AuditLog, Client, DeletionRequest, DocumentType, Event, FieldTemplate
from core.models.attachment import EventAttachment
from core.services.encryption import safe_decrypt
from core.services.event import (
    approve_deletion,
    create_event,
    reject_deletion,
    request_deletion,
    soft_delete_event,
    update_event,
)
from core.services.file_vault import (
    delete_attachment_file,
    get_decrypted_file_stream,
    get_original_filename,
    store_encrypted_file,
)
from core.services.sensitivity import (
    get_visible_attachment_or_404,
    get_visible_event_or_404,
    user_can_see_document_type,
    user_can_see_field,
)
from core.views.mixins import AssistantOrAboveRequiredMixin, LeadOrAdminRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


def _remove_restricted_fields(user, document_type, data_form):
    """Remove fields from *data_form* that *user* may not see.

    Returns a list of removed field names.
    """
    doc_sensitivity = document_type.sensitivity
    field_templates = {}
    for dtf in document_type.fields.select_related("field_template"):
        field_templates[dtf.field_template.slug] = dtf.field_template

    restricted = []
    for name in list(data_form.fields.keys()):
        ft = field_templates.get(name)
        field_sensitivity = ft.sensitivity if ft else ""
        if not user_can_see_field(user, doc_sensitivity, field_sensitivity):
            del data_form.fields[name]
            restricted.append(name)
    return restricted


def _format_file_size(size_bytes):
    """Format file size for display."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


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

        # Pre-render dynamic fields when default document type is set
        data_form = (
            DynamicEventDataForm(document_type=default_doc_type, facility=facility)
            if default_doc_type
            else DynamicEventDataForm()
        )
        if default_doc_type:
            _remove_restricted_fields(request.user, default_doc_type, data_form)

        context = {
            "meta_form": meta_form,
            "data_form": data_form,
            "client_id": client_id or "",
            "client_pseudonym": "",
        }

        if client_id:
            try:
                client = Client.objects.get(pk=client_id, facility=facility)
                context["client_pseudonym"] = client.pseudonym
            except Client.DoesNotExist:
                pass

        return render(request, "core/events/create.html", context)

    @method_decorator(ratelimit(key="user", rate="60/h", method="POST", block=True))
    def post(self, request):
        facility = request.current_facility
        meta_form = EventMetaForm(request.POST, facility=facility, user=request.user)

        if not meta_form.is_valid():
            # Preserve client selection on validation error
            client_id = request.POST.get("client", "")
            client_pseudonym = ""
            if client_id:
                try:
                    client_obj = Client.objects.get(pk=client_id, facility=facility)
                    client_pseudonym = client_obj.pseudonym
                except (Client.DoesNotExist, ValueError):
                    pass

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
        _remove_restricted_fields(request.user, doc_type, data_form)

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

        # Separate file uploads from text data
        from django.core.files.uploadedfile import UploadedFile

        file_fields = {}
        text_data = {}
        for key, value in data_form.cleaned_data.items():
            if isinstance(value, UploadedFile):
                file_fields[key] = value
            else:
                text_data[key] = value

        try:
            event = create_event(
                facility=facility,
                user=request.user,
                document_type=doc_type,
                occurred_at=meta_form.cleaned_data["occurred_at"],
                data_json=text_data,
                client=client,
                case=case,
            )
        except ValidationError as e:
            meta_form.add_error(None, e.message)
            return render(
                request,
                "core/events/create.html",
                {"meta_form": meta_form, "data_form": data_form},
            )

        # Store encrypted file attachments
        if file_fields:
            field_templates = {
                dtf.field_template.slug: dtf.field_template for dtf in doc_type.fields.select_related("field_template")
            }
            for slug, uploaded_file in file_fields.items():
                ft = field_templates.get(slug)
                if ft:
                    attachment = store_encrypted_file(facility, uploaded_file, ft, event, request.user)
                    event.data_json[slug] = {"__file__": True, "attachment_id": str(attachment.pk)}
            event.save(update_fields=["data_json"])

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
        _remove_restricted_fields(request.user, doc_type, data_form)
        return render(request, "core/events/partials/dynamic_fields.html", {"data_form": data_form})


# MIME types that may be rendered inline in the browser without XSS risk
# (text/html and image/svg+xml are deliberately excluded — they can contain
# active script content). Issue #508.
INLINE_MIME_WHITELIST = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "application/pdf",
        "text/plain",
    }
)


def _attachment_disposition(mime_type, force_download):
    """Return the Content-Disposition disposition token for an attachment.

    Inline display is only allowed for whitelisted MIME types and only when
    the caller did not explicitly request a download (``?download=1``).
    """
    if force_download:
        return "attachment"
    if (mime_type or "").lower() in INLINE_MIME_WHITELIST:
        return "inline"
    return "attachment"


class AttachmentDownloadView(AssistantOrAboveRequiredMixin, View):
    """Auth-checked streaming view for an encrypted file attachment.

    By default, displays the file inline if its MIME type is on the safe
    whitelist (images, PDF, plain text). Other types and requests with
    ``?download=1`` are served with ``Content-Disposition: attachment``.
    """

    def get(self, request, pk, attachment_pk):
        event, attachment = get_visible_attachment_or_404(
            request.user, request.current_facility, pk, attachment_pk
        )

        # Field-level sensitivity check (PermissionDenied keeps the UX hint
        # that the event exists but a specific attachment field is restricted).
        ft = attachment.field_template
        doc_sensitivity = event.document_type.sensitivity
        if not user_can_see_field(request.user, doc_sensitivity, ft.sensitivity):
            raise PermissionDenied

        # Audit log
        AuditLog.objects.create(
            facility=event.facility,
            user=request.user,
            action=AuditLog.Action.DOWNLOAD,
            target_type="EventAttachment",
            target_id=str(attachment.pk),
            detail={"event_id": str(event.pk), "field": ft.slug},
        )

        force_download = request.GET.get("download") in ("1", "true")
        disposition = _attachment_disposition(attachment.mime_type, force_download)

        original_filename = get_original_filename(attachment)
        response = StreamingHttpResponse(
            get_decrypted_file_stream(attachment),
            content_type=attachment.mime_type,
        )
        response["Content-Disposition"] = f'{disposition}; filename="{original_filename}"'
        response["Content-Length"] = attachment.file_size
        response["X-Content-Type-Options"] = "nosniff"
        return response


class EventDetailView(AssistantOrAboveRequiredMixin, View):
    """Event detail view."""

    def get(self, request, pk):
        event = get_visible_event_or_404(
            request.user,
            request.current_facility,
            pk,
            select_related=("document_type", "client", "created_by"),
        )

        # Prepare fields with labels and decrypted values
        field_templates = {}
        for dtf in event.document_type.fields.select_related("field_template").order_by("sort_order"):
            field_templates[dtf.field_template.slug] = dtf.field_template

        doc_sensitivity = event.document_type.sensitivity

        fields_display = []
        for key, value in (event.data_json or {}).items():
            ft = field_templates.get(key)
            field_sensitivity = ft.sensitivity if ft else ""
            is_encrypted = ft.is_encrypted if ft else False

            if not user_can_see_field(request.user, doc_sensitivity, field_sensitivity):
                fields_display.append(
                    {
                        "label": ft.name if ft else key.replace("-", " ").title(),
                        "value": _("[Eingeschränkt]"),
                        "is_sensitive": bool(field_sensitivity),
                        "restricted": True,
                    }
                )
                continue

            # File attachment marker
            if isinstance(value, dict) and value.get("__file__"):
                attachment = EventAttachment.objects.filter(pk=value.get("attachment_id"), event=event).first()
                if attachment:
                    fields_display.append(
                        {
                            "label": ft.name if ft else key,
                            "is_file": True,
                            "attachment_id": str(attachment.pk),
                            "original_filename": get_original_filename(attachment),
                            "file_size_display": _format_file_size(attachment.file_size),
                            "is_sensitive": bool(field_sensitivity),
                        }
                    )
                    continue

            fields_display.append(
                {
                    "label": ft.name if ft else key.replace("-", " ").title(),
                    "value": safe_decrypt(value, default=_("[verschlüsselt]")),
                    "is_encrypted": is_encrypted,
                    "is_sensitive": bool(field_sensitivity),
                }
            )

        history = event.history.select_related("changed_by").order_by("-changed_at")

        context = {
            "event": event,
            "fields_display": fields_display,
            "history": history,
        }
        return render(request, "core/events/detail.html", context)


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

    @staticmethod
    def _remove_restricted_fields(user, event, data_form):
        """Remove restricted fields from the form.

        Returns a list of field names that were removed.
        """
        return _remove_restricted_fields(user, event.document_type, data_form)

    def get(self, request, pk):
        event = self.event

        # Decrypted data as initial_data (skip file markers)
        initial_data = {}
        for key, value in (event.data_json or {}).items():
            if isinstance(value, dict) and value.get("__file__"):
                continue  # File fields don't use initial_data
            initial_data[key] = safe_decrypt(value, default="")

        data_form = DynamicEventDataForm(
            document_type=event.document_type,
            initial_data=initial_data,
            facility=request.current_facility,
        )

        # Remove sensitive fields from the form
        self._remove_restricted_fields(request.user, event, data_form)

        # Build attachment info for file fields
        existing_attachments = {}
        for attachment in event.attachments.select_related("field_template"):
            existing_attachments[attachment.field_template.slug] = {
                "filename": get_original_filename(attachment),
                "size": _format_file_size(attachment.file_size),
            }

        context = {
            "event": event,
            "data_form": data_form,
            "existing_attachments": existing_attachments,
        }
        return render(request, "core/events/edit.html", context)

    def post(self, request, pk):
        event = self.event
        facility = request.current_facility

        # Pass existing data so inactive options stay in choices for validation
        existing_data = {}
        for key, value in (event.data_json or {}).items():
            if isinstance(value, dict) and value.get("__file__"):
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
        restricted_keys = self._remove_restricted_fields(request.user, event, data_form)

        if data_form.is_valid():
            from django.core.files.uploadedfile import UploadedFile

            # Separate file uploads from text data
            file_fields = {}
            merged = {}
            for key, value in data_form.cleaned_data.items():
                if isinstance(value, UploadedFile):
                    file_fields[key] = value
                else:
                    merged[key] = value

            # Re-insert restricted fields with original values
            for key in restricted_keys:
                if key in (event.data_json or {}):
                    merged[key] = event.data_json[key]

            # Preserve existing file markers for FILE fields without new upload
            field_templates = {
                dtf.field_template.slug: dtf.field_template
                for dtf in event.document_type.fields.select_related("field_template")
            }
            for slug, ft in field_templates.items():
                if ft.field_type == FieldTemplate.FieldType.FILE and slug not in file_fields:
                    existing_marker = (event.data_json or {}).get(slug)
                    if isinstance(existing_marker, dict) and existing_marker.get("__file__"):
                        merged[slug] = existing_marker

            expected_updated_at = request.POST.get("expected_updated_at")
            try:
                update_event(event, request.user, merged, expected_updated_at=expected_updated_at)
            except ValidationError as e:
                messages.error(request, str(e.message))
                return redirect("core:event_update", pk=event.pk)

            # Store new file attachments (replace old ones)
            if file_fields:
                for slug, uploaded_file in file_fields.items():
                    ft = field_templates.get(slug)
                    if not ft:
                        continue
                    # Delete old attachment if exists
                    old = event.attachments.filter(field_template=ft).first()
                    if old:
                        delete_attachment_file(old)
                        old.delete()
                    attachment = store_encrypted_file(facility, uploaded_file, ft, event, request.user)
                    event.data_json[slug] = {"__file__": True, "attachment_id": str(attachment.pk)}
                event.save(update_fields=["data_json"])

            messages.success(request, _("Ereignis wurde aktualisiert."))
            return redirect("core:event_detail", pk=event.pk)

        context = {
            "event": event,
            "data_form": data_form,
        }
        return render(request, "core/events/edit.html", context)


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


class DeletionRequestListView(LeadOrAdminRequiredMixin, View):
    """List of all deletion requests."""

    def get(self, request):
        facility = request.current_facility
        all_requests = DeletionRequest.objects.for_facility(facility).select_related("requested_by", "reviewed_by")

        pending = all_requests.filter(status=DeletionRequest.Status.PENDING)
        approved = all_requests.filter(status=DeletionRequest.Status.APPROVED)
        rejected = all_requests.filter(status=DeletionRequest.Status.REJECTED)

        context = {
            "pending_requests": pending,
            "approved_requests": approved,
            "rejected_requests": rejected,
        }
        return render(request, "core/deletion_requests/list.html", context)


class DeletionRequestReviewView(LeadOrAdminRequiredMixin, View):
    """Review a deletion request (four-eyes principle)."""

    def get(self, request, pk):
        dr = get_object_or_404(
            DeletionRequest,
            pk=pk,
            facility=request.current_facility,
            status=DeletionRequest.Status.PENDING,
        )

        try:
            event = Event.objects.select_related("document_type", "client").get(
                pk=dr.target_id, facility=request.current_facility
            )
        except Event.DoesNotExist:
            raise Http404

        context = {
            "deletion_request": dr,
            "event": event,
        }
        return render(request, "core/events/deletion_review.html", context)

    def post(self, request, pk):
        dr = get_object_or_404(
            DeletionRequest,
            pk=pk,
            facility=request.current_facility,
            status=DeletionRequest.Status.PENDING,
        )

        # Reviewer must not be the requester
        if dr.requested_by == request.user:
            messages.error(request, _("Sie können Ihren eigenen Löschantrag nicht genehmigen."))
            return redirect("core:deletion_review", pk=pk)

        action = request.POST.get("action")
        if action == "approve":
            approve_deletion(dr, request.user)
            messages.success(request, _("Löschantrag wurde genehmigt."))
        elif action == "reject":
            reject_deletion(dr, request.user)
            messages.info(request, _("Löschantrag wurde abgelehnt."))

        return redirect("core:zeitstrom")
