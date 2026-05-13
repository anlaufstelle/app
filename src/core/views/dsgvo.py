"""Views for DSGVO documentation package."""

import logging

from django.http import Http404
from django.shortcuts import render
from django.views import View

from core.models import AuditLog
from core.services.audit import log_audit_event
from core.services.dsgvo_package import DOCUMENTS, get_document_list, render_document
from core.services.sudo_mode import RequireSudoModeMixin
from core.utils.downloads import safe_download_response
from core.views.mixins import FacilityAdminRequiredMixin

logger = logging.getLogger(__name__)


class DSGVOPackageView(FacilityAdminRequiredMixin, RequireSudoModeMixin, View):
    """Overview page listing all DSGVO document templates."""

    def get(self, request):
        documents = get_document_list()
        return render(request, "core/dsgvo/package.html", {"documents": documents})


class DSGVODocumentDownloadView(FacilityAdminRequiredMixin, RequireSudoModeMixin, View):
    """Download a single DSGVO document template, filled with facility data."""

    def get(self, request, document):
        if document not in DOCUMENTS:
            raise Http404

        facility = request.current_facility
        content, filename = render_document(document, facility)

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_type="DSGVO-Dokument",
            target_id=document,
            detail={"document": document, "name": DOCUMENTS[document]["name"]},
        )

        return safe_download_response(
            filename,
            "text/markdown; charset=utf-8",
            content,
        )
