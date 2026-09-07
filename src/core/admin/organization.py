"""Admin fuer Organization + Facility (Refs #785, #958)."""

from django import forms
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from core.admin.mixins import SuperAdminOnlyAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import Facility, Organization
from core.packages.catalog import PACKAGE_INFO, list_packages
from core.services.facility import ensure_settings_for_facility
from core.services.facility_bootstrap import ensure_facility_baseline


def _package_choices():
    for name in list_packages():
        info = PACKAGE_INFO[name]
        yield name, f"{info.display_name} — {info.domain}"


class FacilityAdminForm(forms.ModelForm):
    """Facility-Formular mit Katalogwahl ausserhalb des Datenmodells."""

    package = forms.ChoiceField(
        choices=_package_choices,
        initial="default",
        label=_("Paket"),
        help_text=_("Katalog für die Grundausstattung der neuen Einrichtung."),
    )

    class Meta:
        model = Facility
        fields = "__all__"


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
    form = FacilityAdminForm

    def get_form(self, request, obj=None, **kwargs):
        """Paketwahl nur beim Anlegen zeigen; Bestandskatalog nie implizit wechseln."""
        form = super().get_form(request, obj, **kwargs)
        if obj is not None:
            form.base_fields.pop("package", None)
        return form

    def save_model(self, request, obj, form, change):
        """Sichert Einstellungen und Grundausstattung der Einrichtung (Refs #1884, #1970).

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

        Refs #1970 — die ``Settings``-Zeile allein macht eine Einrichtung noch
        nicht benutzbar: ohne Dokumentationstyp mit ``entity_type: event``
        bleibt die Typ-Auswahl in ``/events/new/`` leer und es laesst sich
        **kein einziger Kontakt** dokumentieren. ``ensure_facility_baseline``
        ist derselbe Einstieg, den ``setup_facility`` benutzt (Standardkatalog,
        Schichtfenster, Schnellvorlagen) — ein zweiter Weg mit abweichendem
        Ergebnis war genau die Falle aus Refs #1884.

        Der Aufruf haengt bewusst an ``change=False`` — Ausstatten beim
        Anlegen, nie beim Aendern. Die Leer-Pruefungen im Bootstrap arbeiten je
        Gruppe (Katalog, Zeitfenster, Schnellvorlagen); an einer bestehenden
        Einrichtung waere „leer" damit unweigerlich als „noch nicht ausgestattet"
        gelesen worden — obwohl leer hier ein **gepflegter** Zustand sein kann:
        eine per ``setup_facility --no-default-package`` bewusst katalogfrei
        angelegte Einrichtung haette beim ersten beliebigen Speichern
        (Umbenennen, ``is_active`` umschalten) den kompletten Standardkatalog
        bekommen, geloeschte Standardschichten waeren zurueckgekehrt. Der
        Auslieferungsstand laesst sich nur ausdruecklich nachziehen:
        ``manage.py load_package --default --facility <uuid>`` fuer den Katalog,
        Core -> Zeitfenster / Quick-Templates fuer den Rest.

        Die ``Settings``-Zeile eine Zeile hoeher laeuft dagegen weiter
        unbedingt (Refs #1884): sie ist ein 1:1-Pflichtsatz ohne bewusst leeren
        Zustand, ihr Fehlen ein Datenfehler und kein Betreiber-Entscheid.

        Zugriffsrechte: ``FacilityAdmin`` ist super_admin-only (L1, Refs #1375),
        und die ``FacilityScopeMiddleware`` setzt fuer super_admin
        ``app.is_super_admin`` — der Bypass-Zweig der ``facility_isolation``-
        Policies. Die Schreibzugriffe des Bootstraps in die FORCE-RLS-Tabellen
        (Dokumentationstypen, Feldvorlagen, Feldgruppen, Feldzuordnungen,
        Bedingungen, Schnellvorlagen, Zeitfenster) sind damit gedeckt, obwohl
        der Einrichtungs-Kontext des Requests auf eine ANDERE Einrichtung zeigt.

        Fehler werden nicht geschluckt: ein kaputter Auslieferungskatalog
        schlaegt durch und Djangos ``_changeform_view``-Transaktionsklammer
        nimmt die halb angelegte Einrichtung wieder mit — besser als eine
        Einrichtung, die es gibt, in der aber nichts dokumentiert werden kann.
        """
        super().save_model(request, obj, form, change)
        ensure_settings_for_facility(obj)
        if not change:
            package = str(form.cleaned_data.get("package") or "default") if form is not None else "default"
            ensure_facility_baseline(obj, package=package)
