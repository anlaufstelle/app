"""Admin fuer ``Attachment`` (Refs #785, #958, #1738).

Eigenes Modul seit M24/AP2: der Anhang ist keine Event-Beilage mehr, sondern
eine eigene Domaene mit genau EINEM Owner (Ereignis, Person oder Fall) und
eigener ``facility`` (ADR-039). Bis dahin lag die Klasse in ``admin/events.py``.

Append-only-Charakter (Audit-Trail): keine Add/Change/Delete-Permissions.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from core.admin.mixins import FacilityScopedAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models.attachment import OWNER_FIELDS, Attachment

#: Anzeigenamen der Owner-Typen fuer die Uebersichtsspalte.
_OWNER_LABELS = {
    "event": _("Ereignis"),
    "client": _("Person"),
    "case": _("Fall"),
}


@admin.register(Attachment, site=anlaufstelle_admin_site)
class AttachmentAdmin(FacilityScopedAdminMixin, ModelAdmin):
    """Read-only Sicht auf die verschluesselten Dateianhaenge.

    Facility-Scoping laeuft ueber die **eigene** ``facility``-Spalte
    (G2-Befund, Refs #1738): der frueher genutzte Join ``event__facility`` haette
    personen- und falleigene Zeilen komplett verborgen, weil deren ``event``
    ``NULL`` ist — ein facility_admin haette seine eigenen Anhaenge nicht mehr
    gesehen. Seit Migration 0130 traegt jede Zeile ihre Facility selbst
    (ADR-039 §2).
    """

    list_display = (
        "storage_filename",
        "owner_display",
        "field_template",
        "mime_type",
        "file_size",
        "created_by",
        "created_at",
    )
    list_filter = ("mime_type",)
    readonly_fields = (
        "id",
        "facility",
        *OWNER_FIELDS,
        "field_template",
        "storage_filename",
        "original_filename_encrypted",
        "file_size",
        "mime_type",
        "created_by",
        "created_at",
    )
    search_fields = ("storage_filename",)

    @admin.display(description=_("Zugeordnet zu"))
    def owner_display(self, obj):
        """``"Person · <uuid>"`` — Owner-Typ + -PK ohne Nachladen.

        ``owner_type``/``owner_id`` verzweigen ueber die ``*_id``-Spalten und
        loesen die Owner-Instanz bewusst NICHT auf (kein N+1 in der Changelist).
        """
        owner_type = obj.owner_type
        if owner_type is None:
            return "—"
        return f"{_OWNER_LABELS.get(owner_type, owner_type)} · {obj.owner_id}"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
