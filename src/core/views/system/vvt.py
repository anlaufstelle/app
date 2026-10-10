"""Verzeichnis Verarbeitungstaetigkeiten (Art. 30 DSGVO) fuer super_admin (Refs #876)."""

from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods
from django.views.generic import TemplateView

from core.services.compliance import get_processing_activities
from core.views.system.mixins import SystemAuditMixin


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
class SystemVVTView(SystemAuditMixin, TemplateView):
    """Read-Only Verzeichnis aller Verarbeitungstaetigkeiten der Installation.

    Quelle ist die statische Konstante in
    :mod:`core.services.compliance.vvt`. MVP ohne PDF-Export — der Browser-Druck
    (mit Print-CSS-Klassen) reicht aus, um eine PDF zu erzeugen.
    Refs #876.
    """

    template_name = "core/system/vvt.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["activities"] = get_processing_activities()
        return context
