"""Tests for DSGVO documentation package."""

import pytest
from django.test import Client as DjangoClient

from core.models import AuditLog, Facility, Organization, Settings, User
from core.services.dsgvo_package import get_document_list, render_document


@pytest.fixture
def facility(db):
    org = Organization.objects.create(name="DSGVO-Org")
    facility = Facility.objects.create(organization=org, name="DSGVO-Einrichtung")
    Settings.objects.create(
        facility=facility,
        facility_full_name="Anlaufstelle Musterstadt",
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )
    return facility


@pytest.fixture
def admin_user(facility):
    return User.objects.create_user(
        username="dsgvo_admin",
        password="test1234",
        role=User.Role.ADMIN,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def lead_user(facility):
    return User.objects.create_user(
        username="dsgvo_lead",
        password="test1234",
        role=User.Role.LEAD,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def staff_user(facility):
    return User.objects.create_user(
        username="dsgvo_staff",
        password="test1234",
        role=User.Role.STAFF,
        facility=facility,
        is_staff=True,
    )


@pytest.mark.django_db
class TestDSGVOPackageService:
    """Service-level tests."""

    def test_get_document_list_returns_5(self):
        docs = get_document_list()
        assert len(docs) == 5
        slugs = [d["slug"] for d in docs]
        assert "verarbeitungsverzeichnis" in slugs
        assert "dsfa" in slugs
        assert "av-vertrag" in slugs
        assert "toms" in slugs
        assert "informationspflichten" in slugs

    def test_render_replaces_facility_name(self, facility):
        content, filename = render_document("verarbeitungsverzeichnis", facility)
        assert "Anlaufstelle Musterstadt" in content
        assert "{{ facility_name }}" not in content

    def test_render_replaces_retention_days(self, facility):
        content, _ = render_document("informationspflichten", facility)
        assert "90 Tage" in content
        assert "365 Tage" in content
        assert "3650 Tage" in content
        assert "{{ retention_" not in content

    def test_render_replaces_date(self, facility):
        content, _ = render_document("dsfa", facility)
        assert "{{ date }}" not in content

    def test_render_unknown_slug_raises(self, facility):
        with pytest.raises(ValueError, match="Unknown document"):
            render_document("nonexistent", facility)

    def test_all_templates_render(self, facility):
        for doc in get_document_list():
            content, filename = render_document(doc["slug"], facility)
            assert len(content) > 100
            assert filename.endswith(".md")


@pytest.mark.django_db
class TestDSGVOPackageView:
    """View-level tests for package overview."""

    def test_admin_can_access(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get("/dsgvo/")
        assert response.status_code == 200

    def test_lead_gets_403(self, lead_user):
        client = DjangoClient()
        client.force_login(lead_user)
        response = client.get("/dsgvo/")
        assert response.status_code == 403

    def test_staff_gets_403(self, staff_user):
        client = DjangoClient()
        client.force_login(staff_user)
        response = client.get("/dsgvo/")
        assert response.status_code == 403

    def test_unauthenticated_redirects(self):
        client = DjangoClient()
        response = client.get("/dsgvo/")
        assert response.status_code == 302


@pytest.mark.django_db
class TestDSGVODocumentDownload:
    """View-level tests for document download."""

    def test_admin_can_download(self, admin_user, facility):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get("/dsgvo/verarbeitungsverzeichnis/")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/markdown; charset=utf-8"
        assert "attachment" in response["Content-Disposition"]
        assert b"Anlaufstelle Musterstadt" in response.content

    def test_creates_audit_log(self, admin_user, facility):
        client = DjangoClient()
        client.force_login(admin_user)
        client.get("/dsgvo/toms/")
        assert AuditLog.objects.filter(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="DSGVO-Dokument",
            target_id="toms",
        ).exists()

    def test_unknown_document_404(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get("/dsgvo/nonexistent/")
        assert response.status_code == 404

    def test_lead_gets_403(self, lead_user):
        client = DjangoClient()
        client.force_login(lead_user)
        response = client.get("/dsgvo/toms/")
        assert response.status_code == 403


# Refs #840 (C-73): Versionsstempel-Footer in jedem gerenderten Dokument.
class TestPackageVersionStamp:
    def test_footer_contains_settings_hash_and_software_version(self, facility, settings):
        settings.SOURCE_CODE_VERSION = "abc123def456"
        content, _ = render_document("toms", facility)
        assert "Settings-Hash:" in content
        assert "Software-Version: abc123de" in content
        assert "Generiert:" in content

    def test_settings_change_changes_hash(self, facility):
        content_before, _ = render_document("toms", facility)
        # Settings aendern
        s = facility.settings
        s.retention_anonymous_days = 180
        s.save()
        content_after, _ = render_document("toms", facility)

        # Beide enthalten einen Settings-Hash, aber unterschiedlich.
        import re

        h_before = re.search(r"Settings-Hash: ([0-9a-f]{8})", content_before).group(1)
        h_after = re.search(r"Settings-Hash: ([0-9a-f]{8})", content_after).group(1)
        assert h_before != h_after, "Settings-Aenderung muss Hash-Wechsel bewirken (Refs #840)."
