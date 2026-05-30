"""RBAC-Matrix-Tests, Fall- und Klient-Views.

Refs Welle 6 (#929) — gesplittet aus test_rbac_matrix.py.
Refs #867: SUPER_ADMIN ist installations-weite Top-Rolle (Persona Jonas);
hat *keinen* Zugriff auf facility-scoped Views.
"""

import pytest
from django.urls import reverse

from tests._rbac_helpers import login_user_fixture as _login


@pytest.mark.django_db
class TestCaseCloseViewRBAC:
    """CaseCloseView — POST only, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 302),  # redirect to case_detail on success
            ("lead_user", 302),
            ("staff_user", 403),
            ("assistant_user", 403),
            # Refs #867: super_admin lebt nur im /system/-Bereich — kein
            # Zutritt zu Facility-scoped Views (is_lead_or_admin=False).
            ("super_admin_user", 403),
        ],
    )
    def test_case_close(self, client, case_open, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:case_close", kwargs={"pk": case_open.pk})
        response = client.post(url)
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestCaseReopenViewRBAC:
    """CaseReopenView — POST only, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 302),  # redirect to case_detail on success
            ("lead_user", 302),
            ("staff_user", 403),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_case_reopen(self, client, case_closed, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:case_reopen", kwargs={"pk": case_closed.pk})
        response = client.post(url)
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestCaseListViewRBAC:
    """CaseListView — GET, StaffRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_case_list(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:case_list"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestCaseCreateViewRBAC:
    """CaseCreateView — GET/POST, StaffRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_case_create_get(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:case_create"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestCaseDetailViewRBAC:
    """CaseDetailView — GET, StaffRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_case_detail(self, client, case_open, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:case_detail", kwargs={"pk": case_open.pk})
        response = client.get(url)
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestCaseUpdateViewRBAC:
    """CaseUpdateView — GET, StaffRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_case_update_get(self, client, case_open, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:case_update", kwargs={"pk": case_open.pk})
        response = client.get(url)
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestClientCreateViewRBAC:
    """ClientCreateView — GET, StaffRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_client_create_get(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:client_create"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestClientUpdateViewRBAC:
    """ClientUpdateView — GET, StaffRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 403),
            ("super_admin_user", 403),
        ],
    )
    def test_client_update_get(self, client, client_identified, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:client_update", kwargs={"pk": client_identified.pk})
        response = client.get(url)
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestClientListViewRBAC:
    """ClientListView — GET, AssistantOrAboveRequiredMixin."""

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
    def test_client_list(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:client_list"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestClientDetailViewRBAC:
    """ClientDetailView — GET, AssistantOrAboveRequiredMixin."""

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
    def test_client_detail(self, client, client_identified, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:client_detail", kwargs={"pk": client_identified.pk})
        response = client.get(url)
        assert response.status_code == expected_status
