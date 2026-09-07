"""L1 (Refs #1375) — FacilityAdmin auf super_admin beschraenken + Loeschschutz.

Zwei Haertungen aus dem Sicherheitsreview 2026-07-02 (Befund L1):

1. ``FacilityAdmin`` delegierte seine Permissions bislang an
   ``RoleBasedPermissionMixin`` — ein facility_admin konnte damit Facilities
   anlegen/aendern/loeschen. Die Facility-Ebene liegt jedoch (wie Organization,
   A2.3) oberhalb der Zustaendigkeit eines facility_admin: nur super_admin darf
   sie verwalten.

2. Kein Loeschschutz gegen aktive Legal Holds: ``Facility`` -> ``LegalHold``
   und ``Facility`` -> ``Event`` sind CASCADE. Ein Facility-Delete haette einen
   aktiven Legal Hold (Nachweis einer Aufbewahrungspflicht, Spoliationsschutz)
   samt der gehaltenen Events mitgerissen. Ein ``pre_delete``-Guard blockt das
   auf Modellebene fuer JEDEN Loeschpfad (Admin, Shell, ORM).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from core.admin.organization import FacilityAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import Facility, LegalHold

pytestmark = pytest.mark.django_db


def _request(rf, user):
    request = rf.get("/")
    request.user = user
    request.current_facility = getattr(user, "facility", None)
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _admin():
    return FacilityAdmin(Facility, anlaufstelle_admin_site)


class TestFacilityAdminSuperOnly:
    def test_facility_admin_denied_all_permissions(self, rf, admin_user):
        admin_cls = _admin()
        request = _request(rf, admin_user)
        assert admin_cls.has_view_permission(request) is False
        assert admin_cls.has_add_permission(request) is False
        assert admin_cls.has_change_permission(request) is False
        assert admin_cls.has_delete_permission(request) is False

    def test_super_admin_granted_all_permissions(self, rf, super_admin_user):
        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        assert admin_cls.has_view_permission(request) is True
        assert admin_cls.has_add_permission(request) is True
        assert admin_cls.has_change_permission(request) is True
        assert admin_cls.has_delete_permission(request) is True


class TestFacilityDeleteLegalHoldGuard:
    def _make_hold(self, facility, user, *, expires_at=None, dismissed=False):
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id="00000000-0000-0000-0000-000000000001",
            reason="Aufbewahrungspflicht",
            created_by=user,
            expires_at=expires_at,
        )
        if dismissed:
            hold.dismissed_at = timezone.now()
            hold.save(update_fields=["dismissed_at"])
        return hold

    def test_delete_blocked_with_active_hold(self, facility, super_admin_user):
        self._make_hold(facility, super_admin_user)
        # Eigener Savepoint: der ProtectedError aus dem pre_delete markiert die
        # umgebende (Test-)Transaktion sonst als gebrochen; ``atomic`` rollt nur
        # den Delete-Versuch sauber zurueck.
        with pytest.raises(ProtectedError), transaction.atomic():
            facility.delete()
        assert Facility.objects.filter(pk=facility.pk).exists()

    def test_delete_allowed_without_holds(self, facility):
        pk = facility.pk
        facility.delete()
        assert not Facility.objects.filter(pk=pk).exists()

    def test_delete_allowed_with_only_dismissed_hold(self, facility, super_admin_user):
        self._make_hold(facility, super_admin_user, dismissed=True)
        pk = facility.pk
        facility.delete()
        assert not Facility.objects.filter(pk=pk).exists()

    def test_delete_allowed_with_only_expired_hold(self, facility, super_admin_user):
        yesterday = timezone.localdate() - timedelta(days=1)
        self._make_hold(facility, super_admin_user, expires_at=yesterday)
        pk = facility.pk
        facility.delete()
        assert not Facility.objects.filter(pk=pk).exists()


class TestFacilityAdminSeedsSettings:
    """Refs #1884 — jede zur Laufzeit angelegte Einrichtung bekommt sofort eine ``Settings``-Zeile.

    ``ensure_facility_settings`` haengt am ``post_migrate``-Signal und greift
    damit erst beim naechsten Deploy. Bis dahin haette eine im Admin angelegte
    Einrichtung keine ``Settings``-Zeile — und ``enforce_retention`` /
    ``create_statistics_snapshots`` ueberspringen genau solche Einrichtungen.
    Aufbewahrungsfristen (Art. 5 Abs. 1 lit. e DSGVO) liefen fuer sie gar nicht.

    Der Admin ist der einzige Laufzeit-Anlagepfad (``setup_facility`` legt die
    Zeile selbst an, ``core/seed/`` ist Fixture-Territorium), deshalb sitzt der
    Hook in ``save_model`` statt in einem ``post_save``-Receiver.
    """

    def test_add_creates_settings_row_with_defaults(self, rf, super_admin_user, organization):
        from core.models import Settings
        from core.models.settings import (
            DEFAULT_RETENTION_ANONYMOUS_DAYS,
            DEFAULT_RETENTION_IDENTIFIED_DAYS,
            DEFAULT_RETENTION_QUALIFIED_DAYS,
        )

        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        new_facility = Facility(organization=organization, name="Frisch angelegt")

        admin_cls.save_model(request, new_facility, form=None, change=False)

        settings_obj = Settings.objects.get(facility=new_facility)
        assert settings_obj.retention_anonymous_days == DEFAULT_RETENTION_ANONYMOUS_DAYS
        assert settings_obj.retention_identified_days == DEFAULT_RETENTION_IDENTIFIED_DAYS
        assert settings_obj.retention_qualified_days == DEFAULT_RETENTION_QUALIFIED_DAYS

    def test_change_does_not_overwrite_existing_settings(self, rf, super_admin_user, facility, settings_obj):
        from core.models import Settings

        settings_obj.retention_anonymous_days = 7
        settings_obj.save(update_fields=["retention_anonymous_days"])

        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        facility.name = "Umbenannt"
        admin_cls.save_model(request, facility, form=None, change=True)

        assert Settings.objects.filter(facility=facility).count() == 1
        settings_obj.refresh_from_db()
        assert settings_obj.retention_anonymous_days == 7

    def test_change_heals_legacy_facility_without_settings(self, rf, super_admin_user, facility):
        from core.models import Settings

        assert not Settings.objects.filter(facility=facility).exists()

        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        admin_cls.save_model(request, facility, form=None, change=True)

        assert Settings.objects.filter(facility=facility).exists()


class TestFacilityAdminBootstrapsBaseline:
    """Refs #1970 — eine im Admin angelegte Einrichtung ist sofort dokumentierbar.

    ``ensure_settings_for_facility`` (Refs #1884) hat nur die ``Settings``-Zeile
    geschlossen. Ohne Dokumentationstyp bleibt die Typ-Auswahl in
    ``/events/new/`` leer und es laesst sich **kein einziger Kontakt**
    dokumentieren; Schichtfenster und Schnellvorlagen fehlten ebenfalls.

    Der Admin benutzt denselben Einstieg wie ``setup_facility``
    (``core.services.facility_bootstrap.ensure_facility_baseline``) statt einen
    zweiten Weg mit abweichendem Ergebnis — genau die Falle aus Refs #1884.
    """

    def test_add_form_offers_package_choice_with_accessible_label(self, rf, super_admin_user, monkeypatch):
        from core.admin import organization as admin_module

        monkeypatch.setattr(admin_module, "list_packages", lambda: ["default", "awareness"])
        form_class = _admin().get_form(_request(rf, super_admin_user), obj=None)
        form = form_class()

        assert list(form.fields["package"].choices) == [
            ("default", "Standardkatalog — niedrigschwellige Hilfe"),
            ("awareness", "Awareness — Awareness-Teams bei Veranstaltungen"),
        ]
        assert form.fields["package"].initial == "default"
        assert 'for="id_package"' in form["package"].label_tag()

    def test_add_form_falls_back_to_package_name_without_metadata(self, rf, super_admin_user, monkeypatch):
        from core.admin import organization as admin_module

        monkeypatch.setattr(admin_module, "list_packages", lambda: ["ohne-info"])

        form_class = _admin().get_form(_request(rf, super_admin_user), obj=None)
        form = form_class()

        assert list(form.fields["package"].choices) == [("ohne-info", "ohne-info")]

    def test_add_uses_selected_package_for_bootstrap(
        self,
        rf,
        super_admin_user,
        organization,
        monkeypatch,
    ):
        from core.admin import organization as admin_module

        selected = []
        monkeypatch.setattr(admin_module, "list_packages", lambda: ["default", "awareness"])
        monkeypatch.setattr(
            admin_module,
            "ensure_facility_baseline",
            lambda facility, *, package: selected.append((facility.name, package)),
        )
        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        form_class = admin_cls.get_form(request, obj=None)
        form = form_class(
            data={
                "organization": str(organization.pk),
                "name": "Awareness-Team",
                "package": "awareness",
                "is_active": "on",
            }
        )
        assert form.is_valid(), form.errors
        new_facility = form.save(commit=False)

        admin_cls.save_model(request, new_facility, form=form, change=False)

        assert selected == [("Awareness-Team", "awareness")]

    def test_add_without_form_defaults_to_default_package(
        self,
        rf,
        super_admin_user,
        organization,
        monkeypatch,
    ):
        from core.admin import organization as admin_module

        selected = []
        monkeypatch.setattr(
            admin_module,
            "ensure_facility_baseline",
            lambda facility, *, package: selected.append(package),
        )
        new_facility = Facility(organization=organization, name="Standardstelle")

        _admin().save_model(_request(rf, super_admin_user), new_facility, form=None, change=False)

        assert selected == ["default"]

    def test_change_form_has_no_package_field(self, rf, super_admin_user, facility):
        form_class = _admin().get_form(_request(rf, super_admin_user), obj=facility)

        assert "package" not in form_class.base_fields

    def test_add_provisions_catalog_shifts_and_templates(self, rf, super_admin_user, organization):
        from core.models import DocumentType, QuickTemplate, TimeFilter

        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        new_facility = Facility(organization=organization, name="Frisch angelegt")

        admin_cls.save_model(request, new_facility, form=None, change=False)

        assert DocumentType.objects.filter(
            facility=new_facility, entity_type=DocumentType.EntityType.EVENT, is_active=True
        ).exists(), "Ohne Ereignistyp bleibt die Typ-Auswahl leer — nichts dokumentierbar"
        assert TimeFilter.objects.filter(facility=new_facility).exists()
        assert QuickTemplate.objects.filter(facility=new_facility).exists()

    def test_change_keeps_a_maintained_catalog_untouched(self, rf, super_admin_user, facility):
        """Ausstatten, kein Nachziehen — im Admin gepflegte Werte bleiben stehen.

        Die Zusicherung an der Oberflaeche: ein Umbenennen der Einrichtung
        schreibt keinen Katalogwert zurueck. Sie haelt seit dem ``change``-Gate
        doppelt (der Bootstrap laeuft beim Aendern gar nicht mehr) — und hielte
        auch ohne, weil ein gepflegter Katalog die Leer-Pruefung nicht passiert.
        """
        from core.models import DocumentType
        from core.services.facility_bootstrap import ensure_facility_baseline

        ensure_facility_baseline(facility)
        doc_type = DocumentType.objects.get(facility=facility, slug="kontakt")
        doc_type.name = "Beratungskontakt"
        doc_type.sort_order = 99
        doc_type.save()

        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        facility.name = "Umbenannt"
        admin_cls.save_model(request, facility, form=None, change=True)

        doc_type.refresh_from_db()
        assert doc_type.name == "Beratungskontakt"
        assert doc_type.sort_order == 99

    def test_change_leaves_a_deliberately_empty_facility_empty(self, rf, super_admin_user, facility):
        """Leer IST ein gepflegter Zustand — ``--no-default-package`` ist eine Entscheidung.

        Der Bootstrap laeuft nur beim Anlegen (``change=False``). Liefe er auch
        beim Aendern, holte das erste beliebige Speichern (Umbenennen,
        ``is_active`` umschalten) den kompletten Standardkatalog samt
        Schnellvorlagen in eine bewusst leer gehaltene Einrichtung — gegen den
        Hilfetext des Flags und gegen die eigene Zusicherung „ausstatten, kein
        Nachziehen". Der Nachzieh-Weg ist ``load_package --default``.
        """
        from core.models import DocumentType, QuickTemplate

        assert not DocumentType.objects.filter(facility=facility).exists()

        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        facility.name = "Umbenannt"
        admin_cls.save_model(request, facility, form=None, change=True)

        assert not DocumentType.objects.filter(facility=facility).exists()
        assert not QuickTemplate.objects.for_facility(facility).exists()

    def test_change_does_not_resurrect_deleted_standard_shifts(self, rf, super_admin_user, facility):
        """Geloeschte Standardschichten kommen nicht von selbst zurueck (Refs #1970).

        Wer alle Zeitfenster loescht, will keine Schichtsicht — ein spaeteres
        Speichern der Einrichtung darf sie nicht stumm wieder hereinholen.
        """
        from core.models import TimeFilter
        from core.services.facility_bootstrap import ensure_facility_baseline

        ensure_facility_baseline(facility)
        TimeFilter.objects.for_facility(facility).delete()

        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        facility.name = "Umbenannt"
        admin_cls.save_model(request, facility, form=None, change=True)

        assert not TimeFilter.objects.for_facility(facility).exists()

    def test_type_selector_is_filled_for_a_user_of_the_new_facility(self, rf, client, super_admin_user, organization):
        """Der Beweis liegt an der Oberflaeche, nicht in der Zeilenzahl."""
        from django.urls import reverse

        from core.models import User

        admin_cls = _admin()
        request = _request(rf, super_admin_user)
        new_facility = Facility(organization=organization, name="Zweigstelle Süd")
        admin_cls.save_model(request, new_facility, form=None, change=False)

        user = User.objects.create_user(
            username="neue-fachkraft",
            role=User.Role.STAFF,
            facility=new_facility,
            is_staff=True,
        )
        user.set_password("testpass123")
        user.save()
        client.force_login(user)

        response = client.get(reverse("core:event_create"))

        assert response.status_code == 200
        assert response.context["meta_form"].fields["document_type"].queryset.exists()
