"""Tests for SystemComplianceView (Refs #919)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from core.services.compliance import ComplianceCheck, ComplianceStatus


@pytest.fixture
def make_check():
    """Factory: ein ``ComplianceCheck`` mit Defaults."""

    def _make(key="k", label="L", category="System", status=ComplianceStatus.OK, message="m", detail=None, hint=None):
        return ComplianceCheck(
            key=key,
            label=label,
            category=category,
            status=status,
            message=message,
            detail=detail,
            action_hint=hint,
        )

    return _make


@pytest.mark.django_db
class TestSystemComplianceAccess:
    """Refs #919: Access-Pattern wie alle /system/-Views."""

    def test_anonymous_redirected_to_login(self, client):
        url = reverse("core:system_compliance")
        response = client.get(url)
        # 302 zum Login (oder 403, je nach Middleware-Reihenfolge).
        assert response.status_code in (302, 403)

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        url = reverse("core:system_compliance")
        response = client.get(url)
        assert response.status_code == 403

    def test_super_admin_can_access(self, client, super_admin_user):
        client.force_login(super_admin_user)
        url = reverse("core:system_compliance")
        response = client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestSystemComplianceRender:
    def test_renders_summary_counts(self, client, super_admin_user, make_check):
        client.force_login(super_admin_user)
        fake_checks = [
            make_check(key="ok1", status=ComplianceStatus.OK),
            make_check(key="ok2", status=ComplianceStatus.OK),
            make_check(key="warn1", status=ComplianceStatus.WARNING),
            make_check(key="crit1", status=ComplianceStatus.CRITICAL),
            make_check(key="unk1", status=ComplianceStatus.UNKNOWN),
        ]
        with patch("core.views.system.compliance.aggregate_checks", return_value=fake_checks):
            response = client.get(reverse("core:system_compliance"))
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "compliance-summary" in content
        assert "<strong>2</strong>" in content
        assert "compliance-group-system" in content

    def test_renders_check_label_and_message(self, client, super_admin_user, make_check):
        client.force_login(super_admin_user)
        fake_checks = [
            make_check(
                key="db_role_app_nosuperuser",
                label="App-DB-Rolle NOSUPERUSER",
                category="Datenbank",
                status=ComplianceStatus.OK,
                message="Korrekt konfiguriert.",
                detail="anlaufstelle: rolsuper=False",
            )
        ]
        with patch("core.views.system.compliance.aggregate_checks", return_value=fake_checks):
            response = client.get(reverse("core:system_compliance"))
        content = response.content.decode("utf-8")
        assert "App-DB-Rolle NOSUPERUSER" in content
        assert "Korrekt konfiguriert." in content
        assert "anlaufstelle: rolsuper=False" in content
        assert 'data-status="ok"' in content

    def test_renders_action_hint_for_warning(self, client, super_admin_user, make_check):
        client.force_login(super_admin_user)
        fake_checks = [
            make_check(
                key="x",
                status=ComplianceStatus.WARNING,
                message="Ist warnig",
                hint="Backup-Cron pruefen.",
            )
        ]
        with patch("core.views.system.compliance.aggregate_checks", return_value=fake_checks):
            response = client.get(reverse("core:system_compliance"))
        content = response.content.decode("utf-8")
        assert "Backup-Cron pruefen." in content

    def test_categories_in_expected_order(self, client, super_admin_user, make_check):
        """Datenbank kommt vor Backup, Audit kommt vor System."""
        client.force_login(super_admin_user)
        fake_checks = [
            make_check(key="a", category="System"),
            make_check(key="b", category="Datenbank"),
            make_check(key="c", category="Audit"),
            make_check(key="d", category="Backup"),
        ]
        with patch("core.views.system.compliance.aggregate_checks", return_value=fake_checks):
            response = client.get(reverse("core:system_compliance"))
        content = response.content.decode("utf-8")
        db_pos = content.find("compliance-group-datenbank")
        backup_pos = content.find("compliance-group-backup")
        audit_pos = content.find("compliance-group-audit")
        system_pos = content.find("compliance-group-system")
        # _CATEGORY_ORDER: Datenbank, Backup, Virus-Scan, Retention, MFA, Audit, System
        assert 0 <= db_pos < backup_pos < audit_pos < system_pos

    def test_writes_audit_log_on_access(self, client, super_admin_user):
        from core.models import AuditLog

        client.force_login(super_admin_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.get(reverse("core:system_compliance"))
        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before + 1
        entry = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).latest("timestamp")
        assert entry.target_type == "SystemComplianceView"
