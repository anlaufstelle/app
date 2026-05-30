"""Installation-wide system area for super_admin (Refs #867 / #904).

Operates outside the per-Facility scope: ``request.current_facility`` is
``None`` for super_admin sessions and MUST NOT be used as filter. The
underlying :class:`~core.models.AuditLog` queries cross all facilities —
RLS-bypass runs through the ``app.is_super_admin`` Postgres-Setting set
by :class:`core.middleware.facility_scope.FacilityScopeMiddleware` (see
Migration 0085).

Every dispatch into one of these views writes a
``AuditLog.Action.SYSTEM_VIEW`` entry via the typed helper
:func:`core.services.audit.audit_system_view`
(DSGVO-Rechenschaftspflicht).

Refs #904: dieser Modul war fruher eine 877-LOC-Datei
``views/system.py``. Aufgeteilt in thematische Submodule; das Package
re-exportiert alle View-Klassen, damit ``core.urls`` und Tests den
Import-Pfad nicht aendern muessen.
"""

from core.views.system.audit import (
    SystemAuditLogDetailView,
    SystemAuditLogExportView,
    SystemAuditLogListView,
)
from core.views.system.compliance import SystemComplianceView
from core.views.system.dashboard import SystemDashboardView
from core.views.system.legal_holds import SystemLegalHoldListView
from core.views.system.lockouts import SystemLockoutListView, SystemUnlockView
from core.views.system.maintenance import SystemMaintenanceView
from core.views.system.mixins import SystemAuditMixin
from core.views.system.organization import SystemOrganizationView
from core.views.system.retention import SystemRetentionView
from core.views.system.vvt import SystemVVTView

__all__ = [
    "SystemAuditLogDetailView",
    "SystemAuditLogExportView",
    "SystemAuditLogListView",
    "SystemAuditMixin",
    "SystemComplianceView",
    "SystemDashboardView",
    "SystemLegalHoldListView",
    "SystemLockoutListView",
    "SystemMaintenanceView",
    "SystemOrganizationView",
    "SystemRetentionView",
    "SystemUnlockView",
    "SystemVVTView",
]
