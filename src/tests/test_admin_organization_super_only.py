"""A2.3 — OrganizationAdmin auf super_admin beschraenken.

Refs #1021 (Tracker #1016, Workstream A2.3).

Organization liegt ueber der Facility-Ebene; ein facility_admin darf sie nicht
verwalten. Bisher erlaubte ``OrganizationAdmin`` (``RoleBasedPermissionMixin``)
auch facility_admin vollen Zugriff. A2.3 beschraenkt alle Permissions auf
super_admin (delegiert an ``admin_site.has_super_admin_permission`` als SSoT).
"""

from __future__ import annotations

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from core.admin.organization import OrganizationAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import Organization

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
    return OrganizationAdmin(Organization, anlaufstelle_admin_site)


class TestOrganizationAdminSuperOnly:
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
