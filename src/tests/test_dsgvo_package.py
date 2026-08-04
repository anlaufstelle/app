"""Tests for DSGVO documentation package."""

import pytest
from django.core.cache import cache
from django.test import Client as DjangoClient

from core.models import AuditLog, Facility, Organization, Settings, User
from core.services.client import get_document_list, render_document


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
        role=User.Role.FACILITY_ADMIN,
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
class TestDsfaExtendedRetentionPlaceholders:
    """Refs #1762: dsfa.md § 2.3 rendert alle sieben Aufbewahrungsfristen als
    tatsächlichen Ist-Wert — vorher waren es nur die ersten drei
    (``retention_{anonymous,identified,qualified}_days``), die restlichen
    vier standen als "Standardwert mit Prüfauftrag"-Prosa im Dokument."""

    def test_scalar_fields_render_configured_values(self, facility):
        settings_obj = facility.settings
        settings_obj.retention_activities_days = 200
        settings_obj.client_trash_days = 45
        settings_obj.auditlog_retention_months = 36
        settings_obj.save()

        content, _ = render_document("dsfa", facility)

        assert "200 Tage" in content
        assert "45 Tage" in content
        assert "36 Monate" in content
        assert "{{ retention_activities_days }}" not in content
        assert "{{ client_trash_days }}" not in content
        assert "{{ auditlog_retention_months }}" not in content

    def test_default_values_render_when_settings_use_model_defaults(self, facility):
        # `facility`-Fixture legt Settings ohne explizite activities/trash/
        # auditlog-Werte an -> Modell-Defaults 365/30/24 gelten.
        content, _ = render_document("dsfa", facility)

        assert "365 Tage" in content
        assert "30 Tage" in content
        assert "24 Monate" in content

    def test_check_prosa_is_removed(self, facility):
        """Die "Standardwert, vor Freigabe zu prüfen"-Prosa entfällt, weil
        jetzt alle sieben Fristen echte Ist-Werte sind."""
        content, _ = render_document("dsfa", facility)

        assert "Standardwert" not in content
        assert "Prüfauftrag" not in content

    def test_document_type_override_placeholder_is_replaced(self, facility):
        content, _ = render_document("dsfa", facility)

        assert "{{ retention_document_type_overrides }}" not in content
        # Die alte generische Beschreibung entfaellt zugunsten des Ist-Werts.
        assert "je Typ gesetzt, sonst keine" not in content
        # Kein Dokumentationstyp mit eigener Frist konfiguriert -> "keine".
        assert "keine" in content

    def test_document_type_override_shows_real_configured_type(self, facility):
        from core.models import DocumentType

        DocumentType.objects.create(facility=facility, name="Vermittlung", retention_days=180)

        content, _ = render_document("dsfa", facility)

        assert "Vermittlung: 180 Tage" in content
        assert "je Typ gesetzt, sonst keine" not in content


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


@pytest.mark.django_db
class TestDSGVORateLimit:
    """S3 (Refs #1084): RATELIMIT_BULK_ACTION (30/h/User) auf Package-Ansicht
    und Dokument-Download — Dokument-Rendering ist teuer, die Views waren die
    letzte Luecke im sonst dichten Drossel-Netz.

    Tests laufen mit ``RATELIMIT_ENABLE = False``; hier wird das Limit explizit
    aktiviert (Muster: ``test_offline_bundle_api.test_rate_limited_after_120_requests``).
    django-ratelimit mit ``block=True`` antwortet via ``handler403`` mit 429
    (Refs #1354).
    """

    @pytest.fixture(autouse=True)
    def _enable_ratelimit(self, settings):
        settings.RATELIMIT_ENABLE = True
        cache.clear()
        yield
        cache.clear()

    def test_package_view_blocked_after_30_requests(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        for _ in range(30):
            assert client.get("/dsgvo/").status_code == 200
        assert client.get("/dsgvo/").status_code == 429

    def test_document_download_blocked_after_30_requests(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        for _ in range(30):
            assert client.get("/dsgvo/toms/").status_code == 200
        assert client.get("/dsgvo/toms/").status_code == 429


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
