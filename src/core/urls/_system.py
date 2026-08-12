"""System-Bereich-Routen (``/system/…``, super_admin/installations-weit).

Ausgelagert aus ``core/urls.py`` (Refs #1835): eigenes Untermodul statt
Namespace — ``include("core.urls._system")`` ohne eigenes ``app_name`` haengt
diese Patterns namensraum-frei unter den einen ``core:``-Namespace aus
``core/urls/__init__.py`` ein, damit jedes ``{% url "core:…" %}`` unveraendert
bleibt.
"""

from django.urls import path

from core.views.system import (
    SystemAuditLogDetailView,
    SystemAuditLogExportView,
    SystemAuditLogListView,
    SystemComplianceView,
    SystemDashboardView,
    SystemLegalHoldListView,
    SystemLockoutListView,
    SystemMaintenanceView,
    SystemOrganizationView,
    SystemRetentionView,
    SystemUnlockView,
    SystemVVTView,
)

urlpatterns = [
    # System (super_admin / installations-weit, Refs #867)
    path("system/", SystemDashboardView.as_view(), name="system_dashboard"),
    path("system/audit/", SystemAuditLogListView.as_view(), name="system_audit_list"),
    path("system/audit/<uuid:pk>/", SystemAuditLogDetailView.as_view(), name="system_audit_detail"),
    # Refs #873: AuditLog-Export (CSV/JSON via ?format=...).
    path("system/audit/export/", SystemAuditLogExportView.as_view(), name="system_audit_export"),
    path("system/organization/", SystemOrganizationView.as_view(), name="system_organization"),
    # Refs #872: Sperrkonten-Uebersicht + Unlock-Button.
    path("system/lockouts/", SystemLockoutListView.as_view(), name="system_lockout_list"),
    path("system/lockouts/unlock/", SystemUnlockView.as_view(), name="system_unlock"),
    # Refs #874: Wartungsmodus-Toggle.
    path("system/maintenance/", SystemMaintenanceView.as_view(), name="system_maintenance"),
    # Refs #875: Cross-Facility-Retention.
    path("system/retention/", SystemRetentionView.as_view(), name="system_retention"),
    # Refs #876: Verzeichnis Verarbeitungstaetigkeiten (Art. 30).
    path("system/vvt/", SystemVVTView.as_view(), name="system_vvt"),
    # Refs #877: Cross-Facility-Legal-Holds.
    path("system/legal-holds/", SystemLegalHoldListView.as_view(), name="system_legal_hold_list"),
    # Refs #919: Compliance-Dashboard (DB-Rollen, Backup, ClamAV, Retention, MFA, Audit-Events).
    path("system/compliance/", SystemComplianceView.as_view(), name="system_compliance"),
]
