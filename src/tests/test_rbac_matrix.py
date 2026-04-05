"""Systematic RBAC matrix tests across all 4 roles.

Tests every restricted view against all roles (Admin, Lead, Staff, Assistant)
to ensure correct access/denial. Complements test_permissions.py by covering
views not yet tested there and providing full 4-role matrix coverage.
"""

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login(client, user_fixture, request):
    """Resolve a fixture name to a user object and force-login."""
    user = request.getfixturevalue(user_fixture)
    client.force_login(user)
    return user


# ---------------------------------------------------------------------------
# LeadOrAdminRequiredMixin views
# ---------------------------------------------------------------------------


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
        ],
    )
    def test_case_reopen(self, client, case_closed, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:case_reopen", kwargs={"pk": case_closed.pk})
        response = client.post(url)
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestStatisticsViewRBAC:
    """StatisticsView — GET, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 403),
            ("assistant_user", 403),
        ],
    )
    def test_statistics_dashboard(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:statistics"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestCSVExportViewRBAC:
    """CSVExportView — GET, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,allowed",
        [
            ("admin_user", True),
            ("lead_user", True),
            ("staff_user", False),
            ("assistant_user", False),
        ],
    )
    def test_csv_export(self, client, user_fixture, allowed, request):
        _login(client, user_fixture, request)
        today = timezone.localdate()
        url = reverse("core:statistics_csv_export")
        response = client.get(url, {"date_from": str(today), "date_to": str(today)})
        if allowed:
            assert response.status_code == 200
        else:
            assert response.status_code == 403


@pytest.mark.django_db
class TestPDFExportViewRBAC:
    """PDFExportView — GET, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,allowed",
        [
            ("admin_user", True),
            ("lead_user", True),
            ("staff_user", False),
            ("assistant_user", False),
        ],
    )
    def test_pdf_export(self, client, user_fixture, allowed, request):
        _login(client, user_fixture, request)
        today = timezone.localdate()
        url = reverse("core:statistics_pdf_export")
        response = client.get(url, {"date_from": str(today), "date_to": str(today)})
        if allowed:
            assert response.status_code == 200
        else:
            assert response.status_code == 403


@pytest.mark.django_db
class TestJugendamtExportViewRBAC:
    """JugendamtExportView — GET, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,allowed",
        [
            ("admin_user", True),
            ("lead_user", True),
            ("staff_user", False),
            ("assistant_user", False),
        ],
    )
    def test_jugendamt_export(self, client, user_fixture, allowed, request):
        _login(client, user_fixture, request)
        today = timezone.localdate()
        url = reverse("core:statistics_jugendamt_export")
        response = client.get(url, {"date_from": str(today), "date_to": str(today)})
        if allowed:
            assert response.status_code == 200
        else:
            assert response.status_code == 403


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
        ],
    )
    def test_deletion_review_nonexistent(self, client, user_fixture, expected_status, request):
        """With a non-existent pk, allowed roles get 404, denied roles get 403."""
        _login(client, user_fixture, request)
        fake_pk = uuid.uuid4()
        response = client.get(reverse("core:deletion_review", kwargs={"pk": fake_pk}))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestClientDataExportJSONViewRBAC:
    """ClientDataExportJSONView — GET, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,allowed",
        [
            ("admin_user", True),
            ("lead_user", True),
            ("staff_user", False),
            ("assistant_user", False),
        ],
    )
    def test_client_export_json(self, client, client_identified, user_fixture, allowed, request):
        _login(client, user_fixture, request)
        url = reverse("core:client_export_json", kwargs={"pk": client_identified.pk})
        response = client.get(url)
        if allowed:
            assert response.status_code == 200
        else:
            assert response.status_code == 403


@pytest.mark.django_db
class TestClientDataExportPDFViewRBAC:
    """ClientDataExportPDFView — GET, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,allowed",
        [
            ("admin_user", True),
            ("lead_user", True),
            ("staff_user", False),
            ("assistant_user", False),
        ],
    )
    def test_client_export_pdf(self, client, client_identified, user_fixture, allowed, request):
        _login(client, user_fixture, request)
        url = reverse("core:client_export_pdf", kwargs={"pk": client_identified.pk})
        response = client.get(url)
        if allowed:
            assert response.status_code == 200
        else:
            assert response.status_code == 403


# ---------------------------------------------------------------------------
# AdminRequiredMixin views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDSGVOPackageViewRBAC:
    """DSGVOPackageView — GET, AdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 403),
            ("staff_user", 403),
            ("assistant_user", 403),
        ],
    )
    def test_dsgvo_package(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:dsgvo_package"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestDSGVODocumentDownloadViewRBAC:
    """DSGVODocumentDownloadView — GET, AdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 403),
            ("staff_user", 403),
            ("assistant_user", 403),
        ],
    )
    def test_dsgvo_document_download(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:dsgvo_document", kwargs={"document": "verarbeitungsverzeichnis"})
        response = client.get(url)
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestAuditLogListViewRBAC:
    """AuditLogListView — GET, AdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 403),
            ("staff_user", 403),
            ("assistant_user", 403),
        ],
    )
    def test_audit_log_list(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:audit_log"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestAuditLogDetailViewRBAC:
    """AuditLogDetailView — GET, AdminRequiredMixin."""

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
class TestCaseListViewRBAC:
    """CaseListView — GET, StaffRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 403),
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
        ],
    )
    def test_client_update_get(self, client, client_identified, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:client_update", kwargs={"pk": client_identified.pk})
        response = client.get(url)
        assert response.status_code == expected_status


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
        ],
    )
    def test_workitem_update_get(self, client, sample_workitem, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk})
        response = client.get(url)
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestEventDeleteViewRBAC:
    """EventDeleteView — GET, StaffRequiredMixin (plus owner-or-lead check)."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            # staff_user is the creator of sample_event, so they pass the owner check
            ("staff_user", 200),
            ("assistant_user", 403),
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
class TestHandoverViewRBAC:
    """HandoverView — GET, AssistantOrAboveRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,expected_status",
        [
            ("admin_user", 200),
            ("lead_user", 200),
            ("staff_user", 200),
            ("assistant_user", 200),
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
        ],
    )
    def test_zeitstrom(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:zeitstrom"))
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
        ],
    )
    def test_client_detail(self, client, client_identified, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:client_detail", kwargs={"pk": client_identified.pk})
        response = client.get(url)
        assert response.status_code == expected_status


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
        ],
    )
    def test_workitem_detail(self, client, sample_workitem, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk})
        response = client.get(url)
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
