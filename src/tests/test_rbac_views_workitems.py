"""RBAC-Matrix-Tests, TestWorkItem- und Event-Views.

Refs Welle 6 (#929) — gesplittet aus test_rbac_matrix.py.
Refs #867: SUPER_ADMIN ist installations-weite Top-Rolle (Persona Jonas);
hat *keinen* Zugriff auf facility-scoped Views.
"""

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog

from tests._rbac_helpers import login_user_fixture as _login


@pytest.mark.django_db
class TestWorkItemCreateViewRBAC:
    """WorkItemCreateView — GET, StaffRequiredMixin."""

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
    def test_workitem_create_get(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:workitem_create"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestWorkItemUpdateViewRBAC:
    """WorkItemUpdateView — GET, StaffRequiredMixin."""

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
    def test_workitem_update_get(self, client, sample_workitem, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk})
        response = client.get(url)
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestEventDeleteViewRBAC:
    """EventDeleteView — GET, StaffRequiredMixin (plus owner-or-lead check).

    Refs #867: ``EventDeleteView.dispatch`` ruft ``get_visible_event_or_404``
    *vor* ``super().dispatch()`` (also vor dem StaffRequiredMixin-Test).
    Fuer super_admin (``facility=None``) liefert der Event-Lookup
    ``Http404``, *bevor* der 403-Branch greift. Ergebnis ist eine Form von
    Denial — 404 statt 403 — weil die Existenz des Events nicht
    durchsickern darf. Wichtig fuers Test-Mapping: super_admin ist denied,
    der genaue Status (404/403) haengt am Dispatch-Order der jeweiligen
    View. Fuer reine Mixin-only-Views ist es 403; fuer Views, die Daten vor
    dem Mixin-Check laden, kann es 404 sein.
    """

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            # staff_user is the creator of sample_event, so they pass the owner check
            ("staff_user", 200),
            ("assistant_user", 403),
            # super_admin: 404 (Event-Lookup vor Mixin-Check). Trotzdem
            # eindeutig "Denial" — Datenexistenz wird nicht offenbart.
            ("super_admin_user", 404),
        ],
    )
    def test_event_delete_get(self, client, sample_event, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:event_delete", kwargs={"pk": sample_event.pk})
        response = client.get(url)
        assert response.status_code == expected_status


# ---------------------------------------------------------------------------
# AssistantOrAboveRequiredMixin views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEventCreateViewRBAC:
    """EventCreateView — GET, AssistantOrAboveRequiredMixin."""

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
    def test_event_create_get(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestWorkItemInboxViewRBAC:
    """WorkItemInboxView — GET, AssistantOrAboveRequiredMixin."""

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
    def test_workitem_inbox(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestWorkItemDetailViewRBAC:
    """WorkItemDetailView — GET, AssistantOrAboveRequiredMixin."""

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
    def test_workitem_detail(self, client, sample_workitem, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk})
        response = client.get(url)
        assert response.status_code == expected_status
