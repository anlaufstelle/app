"""RBAC-Matrix-Tests, Audit-, Löschanträge-, Handover-, Zeitstrom- und sonstige Views.

Refs #929 — gesplittet aus test_rbac_matrix.py.
Refs #867: SUPER_ADMIN ist installations-weite Top-Rolle (Persona Jonas);
hat *keinen* Zugriff auf facility-scoped Views.
"""

import uuid

import pytest
from django.urls import reverse

from core.models import AuditLog
from tests._rbac_helpers import login_user_fixture as _login


@pytest.mark.django_db
class TestDeletionRequestListViewRBAC:
    """DeletionRequestListView — GET, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 403),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_deletion_request_list(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestDeletionRequestReviewViewRBAC:
    """DeletionRequestReviewView — GET, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 404),  # passes permission check, but object doesn't exist
            ("lead_user", 404),
            ("staff_user", 403),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_deletion_review_nonexistent(self, client, user_fixture, expected_status, request):
        """With a non-existent pk, allowed roles get 404, denied roles get 403."""
        _login(client, user_fixture, request)
        fake_pk = uuid.uuid4()
        response = client.get(reverse("core:deletion_review", kwargs={"pk": fake_pk}))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestAuditLogListViewRBAC:
    """AuditLogListView — GET, FacilityAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 403),
            ("staff_user", 403),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_audit_log_list(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:audit_log"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestAuditLogDetailViewRBAC:
    """AuditLogDetailView — GET, FacilityAdminRequiredMixin."""

    @pytest.fixture()
    def audit_entry(self, facility, admin_user):
        return AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.LOGIN,
            ip_address="127.0.0.1",
        )

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 403),
            ("staff_user", 403),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_audit_log_detail(self, client, audit_entry, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:audit_detail", kwargs={"pk": audit_entry.pk})
        response = client.get(url)
        assert response.status_code == expected_status


# ---------------------------------------------------------------------------
# StaffRequiredMixin views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHandoverViewRBAC:
    """HandoverView — GET, AssistantOrAboveRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 200),
            ("super_admin_user", 403),
        ],
    )
    def test_handover(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:handover"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestZeitstromViewRBAC:
    """ZeitstromView — GET, AssistantOrAboveRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 200),
            ("super_admin_user", 403),
        ],
    )
    def test_zeitstrom(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestSearchViewRBAC:
    """SearchView — GET, AssistantOrAboveRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 200),
            ("super_admin_user", 403),
        ],
    )
    def test_search(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:search"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestAccountProfileViewRBAC:
    """AccountProfileView — GET, AssistantOrAboveRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 200),
            # Refs #975: super_admin darf das eigene Profil sehen (facility-lose
            # Widgets bleiben leer, kein Crash). Vorher 403.
            ("super_admin_user", 200),
        ],
    )
    def test_account_profile(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:account_profile"))
        assert response.status_code == expected_status


# ---------------------------------------------------------------------------
# Unauthenticated access — additional views not in test_permissions.py
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnauthenticatedRedirectsMatrix:
    """Unauthenticated requests to various protected views must redirect to login."""

    @pytest.mark.parametrize(
        "url_name,kwargs",
        [
            ("core:handover", None),
            ("core:case_list", None),
            ("core:case_create", None),
            ("core:workitem_inbox", None),
            ("core:workitem_create", None),
            ("core:search", None),
            ("core:dsgvo_package", None),
            ("core:account_profile", None),
        ],
    )
    def test_unauthenticated_redirect(self, client, url_name, kwargs):
        url = reverse(url_name) if kwargs is None else reverse(url_name, kwargs=kwargs)
        response = client.get(url)
        assert response.status_code == 302
        assert "/login/" in response.url
