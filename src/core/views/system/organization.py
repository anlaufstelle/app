"""SystemOrganizationView — Read-Only-Ansicht der Organisation."""

from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from core.models import Facility, Organization
from core.views.system.mixins import SystemAuditMixin


class SystemOrganizationView(SystemAuditMixin, TemplateView):
    """Read-Only-Ansicht der Organisation und ihrer Einrichtungen.

    Aktuell ist die Organization-Verwaltung Sache des Django-Admin
    (``/admin-mgmt/``). Diese View dient super_admin als kompakte
    Uebersicht ohne Admin-UI-Overhead. Refs #867.
    """

    template_name = "core/system/organization.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = Organization.objects.first()
        if organization is not None:
            facilities = organization.facilities.all().order_by("name")
        else:
            facilities = Facility.objects.none()

        context.update(
            {
                "organization": organization,
                "facilities": facilities,
                "no_organization_hint": _(
                    "Es ist noch keine Organisation angelegt. Bitte ueber die Administration einrichten."
                ),
            }
        )
        return context
