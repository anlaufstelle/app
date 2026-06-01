"""Django admin configuration — Subpackage nach Domaene gesplittet (Refs #958).

Refs #785 — Custom AdminSite mit Sudo + Rollen-Gate.
Refs #958 — vorher 559 LoC in einer einzigen ``core/admin.py``; jetzt thematisch
gesplittet (mixins, users, organization, clients, documents, events, workflow,
system) analog zum bereits erfolgten ``views/system/``-Split.

Imports der ModelAdmin-Module triggern die ``@admin.register``-Decorator-
Registrierungen an der Custom-AdminSite. Re-Exports erhalten den
``from core.admin import X``-Import-Pfad fuer Tests und Drittstellen.
"""

from core.admin.clients import CaseAdmin, ClientAdmin
from core.admin.documents import (
    DocumentTypeAdmin,
    DocumentTypeFieldInline,
    FieldTemplateAdmin,
    QuickTemplateAdmin,
    TimeFilterAdmin,
)
from core.admin.events import EventAdmin, EventAttachmentAdmin, EventHistoryAdmin
from core.admin.mixins import FacilityScopedAdminMixin, RoleBasedPermissionMixin
from core.admin.organization import FacilityAdmin, OrganizationAdmin
from core.admin.system import AuditLogAdmin, SettingsAdmin, StatisticsSnapshotAdmin
from core.admin.users import UserAdmin, unlock_selected_users
from core.admin.workflow import DeletionRequestAdmin, WorkItemAdmin

__all__ = [
    "AuditLogAdmin",
    "CaseAdmin",
    "ClientAdmin",
    "DeletionRequestAdmin",
    "DocumentTypeAdmin",
    "DocumentTypeFieldInline",
    "EventAdmin",
    "EventAttachmentAdmin",
    "EventHistoryAdmin",
    "FacilityAdmin",
    "FacilityScopedAdminMixin",
    "FieldTemplateAdmin",
    "OrganizationAdmin",
    "QuickTemplateAdmin",
    "RoleBasedPermissionMixin",
    "SettingsAdmin",
    "StatisticsSnapshotAdmin",
    "TimeFilterAdmin",
    "UserAdmin",
    "WorkItemAdmin",
    "unlock_selected_users",
]
