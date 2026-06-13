"""Views for DSGVO documentation package."""

import logging

from django.http import Http404
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_BULK_ACTION
from core.models import AuditLog
from core.services.audit import log_audit_event
from core.services.client import DOCUMENTS, get_document_list, render_document
from core.services.security import RequireSudoModeMixin
from core.utils.downloads import safe_download_response
from core.views.mixins import FacilityAdminRequiredMixin

logger = logging.getLogger(__name__)


class DSGVOPackageView(FacilityAdminRequiredMixin, RequireSudoModeMixin, View):
    """Overview page listing all DSGVO document templates.

    S3 (Refs #1084): Rate-limited (30/h/User) — Dokument-Rendering ist teuer;
    AuthZ-Gates (FacilityAdmin + Sudo) ersetzen keine Drosselung.
    """

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="GET", block=True))
    def get(self, request):
        documents = get_document_list()
        return render(request, "core/dsgvo/package.html", {"documents": documents})


class DSGVODocumentDownloadView(FacilityAdminRequiredMixin, RequireSudoModeMixin, View):
    """Download a single DSGVO document template, filled with facility data.

    S3 (Refs #1084): Rate-limited (30/h/User), siehe ``DSGVOPackageView``.
    """

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="GET", block=True))
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
