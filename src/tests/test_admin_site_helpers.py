"""Helper-Methoden auf AnlaufstelleAdminSite (Refs #785, #958).

M-2 zentralisiert die Rollen-/Facility-Scope-Logik aus den ModelAdmin-Mixins
in die Custom-AdminSite. Diese Tests pruefen die neuen Helper-Methoden direkt:

- ``has_role_permission(request)``: True fuer super_admin/facility_admin, sonst False
- ``scope_to_facility(qs, request)``: filtert nicht fuer super_admin, sonst auf Facility
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from core.admin_site import anlaufstelle_admin_site
from core.models import Client, Facility


@pytest.fixture
def rf():
    return RequestFactory()


def _request_with_user(rf, user):
    request = rf.get("/admin-mgmt/")
    request.user = user
    return request


@pytest.mark.django_db
class TestHasRolePermission:
    def test_super_admin_has_permission(self, rf, super_admin_user):
        request = _request_with_user(rf, super_admin_user)
        assert anlaufstelle_admin_site.has_role_permission(request) is True

    def test_facility_admin_has_permission(self, rf, admin_user):
        request = _request_with_user(rf, admin_user)
        assert anlaufstelle_admin_site.has_role_permission(request) is True

    def test_lead_has_no_permission(self, rf, lead_user):
        request = _request_with_user(rf, lead_user)
        assert anlaufstelle_admin_site.has_role_permission(request) is False

    def test_staff_has_no_permission(self, rf, staff_user):
        request = _request_with_user(rf, staff_user)
        assert anlaufstelle_admin_site.has_role_permission(request) is False

    def test_assistant_has_no_permission(self, rf, assistant_user):
        request = _request_with_user(rf, assistant_user)
        assert anlaufstelle_admin_site.has_role_permission(request) is False

    def test_anonymous_has_no_permission(self, rf):
        request = _request_with_user(rf, AnonymousUser())
        assert anlaufstelle_admin_site.has_role_permission(request) is False


@pytest.mark.django_db
class TestScopeToFacility:
    @pytest.fixture
    def other_facility(self, organization):
        return Facility.objects.create(organization=organization, name="Andere Stelle")

    @pytest.fixture
    def own_client(self, facility, staff_user):
        return Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            pseudonym="OWN-QU-01",
            created_by=staff_user,
        )

    @pytest.fixture
    def other_client(self, other_facility, staff_user):
        return Client.objects.create(
            facility=other_facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            pseudonym="OTHER-QU-02",
            created_by=staff_user,
        )

    def test_super_admin_sees_all(self, rf, super_admin_user, own_client, other_client):
        request = _request_with_user(rf, super_admin_user)
        qs = Client.objects.all()
        scoped = anlaufstelle_admin_site.scope_to_facility(qs, request)
        pks = set(scoped.values_list("pk", flat=True))
        assert own_client.pk in pks
        assert other_client.pk in pks

    def test_facility_admin_sees_only_own_facility(self, rf, admin_user, facility, own_client, other_client):
        request = _request_with_user(rf, admin_user)
        request.current_facility = facility
        qs = Client.objects.all()
        scoped = anlaufstelle_admin_site.scope_to_facility(qs, request)
        pks = set(scoped.values_list("pk", flat=True))
        assert own_client.pk in pks
        assert other_client.pk not in pks
