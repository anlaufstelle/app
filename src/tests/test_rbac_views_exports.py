"""RBAC-Matrix-Tests, TestStatistics-, CSV-, PDF-, Jugendamt- und DSGVO-Exports.

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
class TestStatisticsViewRBAC:
    """StatisticsView — GET, LeadOrAdminRequiredMixin."""

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
            ("super_admin_user", False),
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
            ("super_admin_user", False),
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
            ("super_admin_user", False),
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
class TestClientDataExportJSONViewRBAC:
    """ClientDataExportJSONView — GET, LeadOrAdminRequiredMixin."""

    @pytest.mark.parametrize(
        "user_fixture,allowed",
        [
            ("admin_user", True),
            ("lead_user", True),
            ("staff_user", False),
            ("assistant_user", False),
            ("super_admin_user", False),
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
            ("super_admin_user", False),
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
# FacilityAdminRequiredMixin views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDSGVOPackageViewRBAC:
    """DSGVOPackageView — GET, FacilityAdminRequiredMixin."""

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
    def test_dsgvo_package(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        response = client.get(reverse("core:dsgvo_package"))
        assert response.status_code == expected_status


@pytest.mark.django_db
class TestDSGVODocumentDownloadViewRBAC:
    """DSGVODocumentDownloadView — GET, FacilityAdminRequiredMixin."""

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
    def test_dsgvo_document_download(self, client, user_fixture, expected_status, request):
        _login(client, user_fixture, request)
        url = reverse("core:dsgvo_document", kwargs={"document": "verarbeitungsverzeichnis"})
        response = client.get(url)
        assert response.status_code == expected_status
