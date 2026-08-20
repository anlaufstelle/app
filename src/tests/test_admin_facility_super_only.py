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
from django.test import RequestFactory
from django.utils import timezone

from core.admin.organization import FacilityAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import Facility, LegalHold

pytestmark = pytest.mark.django_db


@pytest.fixture
def rf():
    return RequestFactory()


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
