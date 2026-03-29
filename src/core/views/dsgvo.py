"""Views for DSGVO documentation package."""

import logging

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views import View

from core.models import AuditLog
from core.services.dsgvo_package import DOCUMENTS, get_document_list, render_document
from core.signals.audit import get_client_ip
from core.views.mixins import AdminRequiredMixin

logger = logging.getLogger(__name__)


class DSGVOPackageView(AdminRequiredMixin, View):
    """Overview page listing all DSGVO document templates."""

    def get(self, request):
        documents = get_document_list()
        return render(request, "core/dsgvo/package.html", {"documents": documents})


class DSGVODocumentDownloadView(AdminRequiredMixin, View):
    """Download a single DSGVO document template, filled with facility data."""

    def get(self, request, document):
        if document not in DOCUMENTS:
            raise Http404

        facility = request.current_facility
        content, filename = render_document(document, facility)

        AuditLog.objects.create(
            facility=facility,
            user=request.user,
            action=AuditLog.Action.EXPORT,
            target_type="DSGVO-Dokument",
            target_id=document,
            detail={"document": document, "name": DOCUMENTS[document]["name"]},
            ip_address=get_client_ip(request),
        )

        response = HttpResponse(content, content_type="text/markdown; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
