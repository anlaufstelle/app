"""Views for the central file attachment overview and download."""

import logging

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.views import View

from core.models import AuditLog, DocumentType
from core.models.attachment import EventAttachment
from core.services.file_vault import get_attachment_path, get_decrypted_file_stream, get_original_filename
from core.services.sensitivity import allowed_sensitivities_for_user, get_visible_attachment_or_404, user_can_see_field
from core.utils.downloads import safe_download_response
from core.utils.formatting import format_file_size
from core.views.mixins import AssistantOrAboveRequiredMixin, HTMXPartialMixin

logger = logging.getLogger(__name__)


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


class AttachmentListView(AssistantOrAboveRequiredMixin, HTMXPartialMixin, View):
    """Central file attachment overview for a facility."""

    template_name = "core/attachments/list.html"
    partial_template_name = "core/attachments/partials/attachment_table.html"

    def get(self, request):
        facility = request.current_facility
        allowed = allowed_sensitivities_for_user(request.user)

        # Get all attachments for this facility via event__facility
        attachments = (
            EventAttachment.objects.filter(event__facility=facility, event__is_deleted=False)
            .select_related(
                "event",
                "event__document_type",
                "event__client",
                "field_template",
                "created_by",
            )
            # Sensitivity filter BEFORE slicing: effective sensitivity is
            # max(doc_type, field_template). Both must be within the user's
            # allowed range for the attachment to be visible.
            .filter(
                event__document_type__sensitivity__in=allowed,
            )
            .filter(
                Q(field_template__sensitivity="") | Q(field_template__sensitivity__in=allowed),
            )
            .order_by("-created_at")
        )

        # Apply filters from query params
        doc_type_id = request.GET.get("document_type")
        if doc_type_id:
            attachments = attachments.filter(event__document_type_id=doc_type_id)

        client_id = request.GET.get("client")
        if client_id:
            attachments = attachments.filter(event__client_id=client_id)

        # Build display list from pre-filtered queryset
        visible = []
        for att in attachments[:200]:
            visible.append(
                {
                    "attachment": att,
                    "event": att.event,
                    "original_filename": get_original_filename(att),
                    "file_size_display": format_file_size(att.file_size),
                    "doc_type_name": att.event.document_type.name,
                    "client_pseudonym": att.event.client.pseudonym if att.event.client else "—",
                }
            )

        # Document types for filter dropdown
        doc_types = DocumentType.objects.for_facility(facility).filter(is_active=True)

        context = {
            "attachments": visible,
            "doc_types": doc_types,
            "selected_doc_type": doc_type_id or "",
            "selected_client": client_id or "",
        }

        return self.render_htmx_or_full(context)


class AttachmentDownloadView(AssistantOrAboveRequiredMixin, View):
    """Auth-checked streaming view for an encrypted file attachment.

    By default, displays the file inline if its MIME type is on the safe
    whitelist (images, PDF, plain text). Other types and requests with
    ``?download=1`` are served with ``Content-Disposition: attachment``.
    """

    def get(self, request, pk, attachment_pk):
        event, attachment = get_visible_attachment_or_404(request.user, request.current_facility, pk, attachment_pk)

        # Field-level sensitivity check (PermissionDenied keeps the UX hint
        # that the event exists but a specific attachment field is restricted).
        ft = attachment.field_template
        doc_sensitivity = event.document_type.sensitivity
        if not user_can_see_field(request.user, doc_sensitivity, ft.sensitivity):
            raise PermissionDenied

        # Verify the encrypted file still exists on disk before streaming.
        # Without this check, a missing file raises FileNotFoundError inside
        # the streaming generator after response headers are already sent —
        # the browser then sees the connection being reset mid-stream
        # ("Secure Connection Failed" in Firefox) instead of a proper 404.
        file_path = get_attachment_path(attachment)
        if not file_path.exists():
            logger.error(
                "Attachment file missing on disk: attachment_id=%s event_id=%s path=%s",
                attachment.pk,
                event.pk,
                file_path,
            )
            raise Http404("Attachment file not found")

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
        as_attachment = disposition == "attachment"
        response = safe_download_response(
            original_filename,
            attachment.mime_type,
            get_decrypted_file_stream(attachment),
            as_attachment=as_attachment,
        )
        response["Content-Length"] = attachment.file_size
        return response
