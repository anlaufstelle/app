"""Tests fuer das ``/system/``-Areal: Zugriffsschutz und SYSTEM_VIEW-Audit (Refs #867).

Enthaelt die Cluster:

* ``TestSystemDashboardAccess`` — Rollen-basierter Zugriff auf das Dashboard.
* ``TestSystemAuditListAccess`` — Audit-List Zugriff + NULL-Facility-Visibility.
* ``TestSystemViewAuditTrail`` — SYSTEM_VIEW-Audit-Schreibzugriffe.
* ``TestSystemOrganizationAccess`` — Organization-View Smoke-Test.
* ``TestSystemAuditDetailAccess`` — Audit-Detail-Zugriff Cross-Facility.
* ``TestSystemDashboardHealthCard`` — Health-Card im Dashboard (Refs #871).
* ``TestSystemMaintenanceView`` — Maintenance-Mode-Toggle (Refs #874).
"""

import uuid

import pytest
from django.db import connection
from django.urls import reverse

from core.models import AuditLog


@pytest.mark.django_db
class TestSystemDashboardAccess:
    """``GET /system/`` — Zugriffsschutz nach Rolle."""

    def test_anonymous_redirects_to_login(self, client):
        """Anonymer Zugriff muss zum Login redirecten (LoginRequiredMixin)."""
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        """Refs #867: facility_admin ist NICHT super_admin -> 403.

        Zentrales Trenn-Kriterium: nur ``role=SUPER_ADMIN`` darf in
        ``/system/``. Selbst ``facility_admin`` (mit ``is_superuser=True``
        im Test-Fixture) wird abgewiesen.
        """
        client.force_login(admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

    def test_lead_forbidden(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

    def test_staff_forbidden(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

    def test_assistant_forbidden(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

    def test_super_admin_can_access_dashboard(self, client, super_admin_user):
        """Super-Admin -> 200, Banner mit Cross-Facility-Hinweis sichtbar."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 200
        # Cross-Facility-Banner aus ``core/system/_banner.html`` enthaelt
        # den deutschen Schluesselbegriff "facility-übergreifend".
        content_text = response.content.decode("utf-8", errors="replace")
        assert "facility-übergreifend" in content_text, (
            "Cross-Facility-Banner fehlt in der Dashboard-Antwort. "
            "Pruefe, ob ``core/system/_banner.html`` ins Template eingebunden ist."
        )


@pytest.mark.django_db
class TestSystemAuditListAccess:
    """``GET /system/audit/`` — Zugriffsschutz und NULL-Facility-Visibility."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 403

    def test_super_admin_sees_null_facility_audit(self, client, super_admin_user, facility, admin_user):
        """Refs #867: SYSTEM-Audits mit ``facility=NULL`` (Pre-Auth oder
        SYSTEM_VIEW) sind im Cross-Facility-Audit-Log sichtbar.

        Die Sichtbarkeit kommt im Test-Setup nicht aus RLS (DB-User =
        Superuser, bypass), sondern aus dem View, der ohne ``for_facility``-
        Filter abfragt. In Produktion greift zusaetzlich der RLS-Bypass-
        Branch ``app.is_super_admin='true'``.
        """
        # Pre-Auth-Style: NULL-Facility-Audit (z.B. failed login by
        # unknown user). Wir nutzen Raw-SQL, weil Manager.create()
        # ohne facility den Service-Workflow nicht abbildet.
        marker = "system-null-audit-" + uuid.uuid4().hex[:8]
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO core_auditlog (id, facility_id, user_id, action, "
                "target_type, target_id, detail, ip_address, timestamp) "
                "VALUES (%s, NULL, %s, %s, '', %s, '{}', NULL, NOW())",
                [uuid.uuid4(), admin_user.pk, "login_failed", marker],
            )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 200

        page_obj = response.context["page_obj"]
        target_ids = [entry.target_id for entry in page_obj.object_list]
        assert marker in target_ids, (
            f"Super-Admin sieht NULL-Facility-Audit nicht im /system/audit/-Listing. Targets: {target_ids}"
        )


@pytest.mark.django_db
class TestSystemViewAuditTrail:
    """Refs #867: jeder System-View-Aufruf schreibt einen
    ``AuditLog.Action.SYSTEM_VIEW``-Eintrag mit ``facility=None``.

    Damit ist die DSGVO-Rechenschaftspflicht ueber facility-uebergreifende
    Lese-Zugriffe erfuellt — der super_admin hat zwar Bypass-Rechte, aber
    jeder einzelne Zugriff ist auditiert.
    """

    def test_dashboard_get_writes_system_view_audit(self, client, super_admin_user):
        """``GET /system/`` legt einen SYSTEM_VIEW-Audit mit
        ``facility=NULL`` und korrektem ``user`` an.
        """
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 200

        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before + 1, f"SYSTEM_VIEW-Audit nicht geschrieben. Vorher: {before}, nachher: {after}."

        latest = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).order_by("-timestamp").first()
        assert latest.facility is None, (
            "SYSTEM_VIEW-Audit muss facility=None tragen — System-Event ohne Facility-Bezug."
        )
        assert latest.user_id == super_admin_user.pk
        # ``target_type`` traegt den View-Klassennamen — Audit erlaubt
        # Differenzierung zwischen Dashboard, AuditList, etc.
        assert latest.target_type == "SystemDashboardView", (
            f"target_type sollte 'SystemDashboardView' sein, erhalten {latest.target_type!r}."
        )

    def test_audit_list_get_writes_system_view_audit(self, client, super_admin_user):
        """``GET /system/audit/`` schreibt ebenfalls einen SYSTEM_VIEW-Audit."""
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 200

        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before + 1

        latest = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).order_by("-timestamp").first()
        assert latest.target_type == "SystemAuditLogListView"

    def test_no_audit_for_unauthorized_access(self, client, admin_user):
        """Wenn der Zugriffsschutz greift (facility_admin -> 403), darf
        KEIN SYSTEM_VIEW-Audit geschrieben werden — sonst koennten
        unautorisierte Probings die Audit-Tabelle aufblasen.
        """
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before, (
            f"Unautorisierter 403-Zugriff darf keinen SYSTEM_VIEW-Audit schreiben. Vorher: {before}, nachher: {after}."
        )


@pytest.mark.django_db
class TestSystemOrganizationAccess:
    """``GET /system/organization/`` — Schmaler Smoke-Test analog zu
    Dashboard. Voraussetzungen + 200/403-Branches."""

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_organization"))
        assert response.status_code == 403

    def test_super_admin_ok(self, client, super_admin_user, organization):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_organization"))
        assert response.status_code == 200
        # Banner ist auch hier eingebunden.
        content = response.content.decode("utf-8", errors="replace")
        assert "facility-übergreifend" in content


@pytest.mark.django_db
class TestSystemAuditDetailAccess:
    """``GET /system/audit/<pk>/`` — Detail-Sicht eines AuditLog-Eintrags."""

    def test_facility_admin_forbidden(self, client, admin_user, facility):
        entry = AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.LOGIN,
        )
        client.force_login(admin_user)
        response = client.get(reverse("core:system_audit_detail", kwargs={"pk": entry.pk}))
        assert response.status_code == 403

    def test_super_admin_can_view_any_facility_entry(
        self, client, super_admin_user, facility, second_facility, admin_user
    ):
        """Super-Admin sieht AuditLogs *aller* Einrichtungen — keine
        Facility-Einschraenkung im View.
        """
        # Eintrag in zweiter Facility (nicht der des super_admin — er hat
        # keine).
        entry = AuditLog.objects.create(
            facility=second_facility,
            user=admin_user,
            action=AuditLog.Action.LOGIN,
        )
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_detail", kwargs={"pk": entry.pk}))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tier 1: Health-Card im Dashboard (Refs #871)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemDashboardHealthCard:
    """Refs #871: Dashboard zeigt eine Health-Card mit DB/Migrations/Disk/Backup/Versions."""

    def test_health_dict_in_context(self, client, super_admin_user):
        """Context enthaelt ``health`` mit allen erwarteten Keys."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 200
        health = response.context["health"]
        assert "db" in health
        assert "migrations_pending" in health
        assert "migrations_pending_count" in health
        assert "disk" in health
        assert "backup" in health
        assert "versions" in health
        # DB-Erreichbarkeit ist im Test-Setup True.
        assert health["db"] is True

    def test_health_card_rendered_in_template(self, client, super_admin_user):
        """Template enthaelt das Test-Selektor-Marker fuer die Health-Card."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        content = response.content.decode("utf-8", errors="replace")
        assert 'data-testid="system-health-card"' in content


# ---------------------------------------------------------------------------
# Tier 1: Maintenance-Mode-Toggle (Refs #874)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemMaintenanceView:
    """``GET/POST /system/maintenance/`` — Wartungsmodus-Toggle."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_maintenance"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_maintenance"))
        assert response.status_code == 403

    def test_get_shows_inactive_when_no_flag_file(self, client, super_admin_user, tmp_path):
        """Default: Flag-Datei existiert nicht -> ``is_active=False``."""
        from django.test import override_settings

        flag = tmp_path / "maintenance.flag"
        if flag.exists():
            flag.unlink()
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag)):
            client.force_login(super_admin_user)
            response = client.get(reverse("core:system_maintenance"))
            assert response.status_code == 200
            assert response.context["is_active"] is False
            assert response.context["configured"] is True

    def test_get_shows_active_when_flag_exists(self, client, super_admin_user, tmp_path):
        from django.test import override_settings

        flag = tmp_path / "maintenance.flag"
        flag.write_text("Test-Notiz")
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag)):
            client.force_login(super_admin_user)
            response = client.get(reverse("core:system_maintenance"))
            assert response.status_code == 200
            assert response.context["is_active"] is True
            assert response.context["note"] == "Test-Notiz"

    def test_get_shows_unconfigured_when_setting_none(self, client, super_admin_user):
        """``MAINTENANCE_FLAG_FILE=None`` -> Hinweis, kein Toggle."""
        from django.test import override_settings

        with override_settings(MAINTENANCE_FLAG_FILE=None):
            client.force_login(super_admin_user)
            response = client.get(reverse("core:system_maintenance"))
            assert response.status_code == 200
            assert response.context["configured"] is False

    def test_post_enable_creates_flag_and_audit(self, client, super_admin_user, tmp_path):
        from django.test import override_settings

        flag = tmp_path / "maintenance.flag"
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag)):
            client.force_login(super_admin_user)
            before = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).count()
            response = client.post(
                reverse("core:system_maintenance"),
                {"action": "enable", "note": "Testwartung"},
            )
            assert response.status_code == 302
            assert flag.exists(), "Flag-Datei wurde nicht angelegt."
            assert flag.read_text() == "Testwartung"
            after = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).count()
            assert after == before + 1
            latest = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).order_by("-timestamp").first()
            assert latest.detail.get("note") == "Testwartung"

    def test_post_disable_removes_flag_and_audit(self, client, super_admin_user, tmp_path):
        from django.test import override_settings

        flag = tmp_path / "maintenance.flag"
        flag.write_text("active")
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag)):
            client.force_login(super_admin_user)
            before = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_DISABLED).count()
            response = client.post(reverse("core:system_maintenance"), {"action": "disable"})
            assert response.status_code == 302
            assert not flag.exists(), "Flag-Datei sollte entfernt sein."
            after = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_DISABLED).count()
            assert after == before + 1

    def test_post_unconfigured_shows_error(self, client, super_admin_user):
        """Ohne Setting: POST darf keine Datei anlegen, kein AuditLog."""
        from django.test import override_settings

        with override_settings(MAINTENANCE_FLAG_FILE=None):
            client.force_login(super_admin_user)
            before_enabled = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).count()
            response = client.post(reverse("core:system_maintenance"), {"action": "enable"})
            assert response.status_code == 302
            after_enabled = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).count()
            assert after_enabled == before_enabled, "Ohne Setting darf KEIN MAINTENANCE_ENABLED-Audit entstehen."
