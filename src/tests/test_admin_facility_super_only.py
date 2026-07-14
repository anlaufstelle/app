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
