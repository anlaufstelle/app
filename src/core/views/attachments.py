"""Views for the central file attachment overview."""

import logging

from django.db.models import Q
from django.shortcuts import render
from django.views import View

from core.models import DocumentType
from core.models.attachment import EventAttachment
from core.services.file_vault import get_original_filename
from core.services.sensitivity import allowed_sensitivities_for_user
from core.utils.formatting import format_file_size
from core.views.mixins import AssistantOrAboveRequiredMixin

logger = logging.getLogger(__name__)


class AttachmentListView(AssistantOrAboveRequiredMixin, View):
    """Central file attachment overview for a facility."""

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
                    "client_pseudonym": att.event.client.pseudonym if att.event.client else "\u2014",
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

        # HTMX partial support
        if request.headers.get("HX-Request"):
            return render(request, "core/attachments/partials/attachment_table.html", context)
        return render(request, "core/attachments/list.html", context)
