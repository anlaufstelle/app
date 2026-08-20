"""Admin fuer Organization + Facility (Refs #785, #958)."""

from django.contrib import admin
from unfold.admin import ModelAdmin

from core.admin.mixins import SuperAdminOnlyAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import Facility, Organization
from core.services.facility import ensure_settings_for_facility


@admin.register(Organization, site=anlaufstelle_admin_site)
class OrganizationAdmin(SuperAdminOnlyAdminMixin, ModelAdmin):
    """Organization liegt ueber der Facility-Ebene -> nur super_admin (A2.3, Refs #1021)."""

    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(Facility, site=anlaufstelle_admin_site)
class FacilityAdmin(SuperAdminOnlyAdminMixin, ModelAdmin):
    """Facility liegt oberhalb der facility_admin-Zustaendigkeit -> nur super_admin (L1, Refs #1375).

    Wie ``Organization`` (A2.3) ist die Facility ein installationsweites
    Struktur-Objekt; ein facility_admin verwaltet Daten INNERHALB seiner
    Facility, nicht die Facility-Datensaetze selbst (Anlegen/Umbenennen/
    Loeschen). Vorher delegierten die Permissions an ``RoleBasedPermissionMixin``
    und liessen facility_admin vollen Zugriff. Da jetzt nur super_admin Zugriff
    hat, ist kein Facility-Scoping im Queryset mehr noetig (super_admin sieht
    ohnehin alle)."""

    list_display = ("name", "organization", "is_active", "created_at")
    list_filter = ("is_active", "organization")
    search_fields = ("name",)

    def save_model(self, request, obj, form, change):
        """Sichert die ``Settings``-Zeile der Einrichtung (Refs #1884).

        Der Admin ist der einzige Laufzeit-Anlagepfad fuer eine Facility
        (``setup_facility`` legt die Zeile selbst an, ``core/seed/`` ist
        Fixture-Territorium). Der ``post_migrate``-Seed
        (``core/signals/settings_seed.py``) greift erst beim naechsten Deploy —
        bis dahin haette ``enforce_retention`` die neue Einrichtung
        uebersprungen und keine Aufbewahrungsfrist durchgesetzt.

        Bewusst hier statt in einem ``post_save``-Receiver auf ``Facility``:
        ein Signal wuerde jedes nachgelagerte ``Settings.objects.create``
        gegen den OneToOne-Primaerschluessel in einen ``IntegrityError``
        laufen lassen (die Testsuite tut das an gut fuenfzig Stellen), und die
        Tests, die das Verhalten OHNE ``Settings``-Zeile pruefen, waeren nicht
        mehr formulierbar. Ein Architektur-Guard
        (``test_architecture_guards_models.py``) pinnt stattdessen, dass kein
        zweiter Anlagepfad die Luecke still wieder aufreisst.
        """
        super().save_model(request, obj, form, change)
        ensure_settings_for_facility(obj)
