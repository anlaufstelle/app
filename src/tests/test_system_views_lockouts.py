"""Tests fuer Sperrkonten, Unlock-Flow und AuditLog-Export im ``/system/``-Areal.

Enthaelt die Cluster:

* ``TestSystemLockoutListAccess`` — Sperrkonten-Liste (Refs #872).
* ``TestSystemUnlockView`` — Unlock-Flow (Refs #872).
* ``TestSystemAuditLogExport`` — CSV/JSON-Streaming-Exports (Refs #873).
"""

import pytest
from django.urls import reverse

from core.models import AuditLog

# ---------------------------------------------------------------------------
# Tier 1: Sperrkonten + Unlock (Refs #872)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemLockoutListAccess:
    """``GET /system/lockouts/`` — Zugriffsschutz und List-Logik."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_lockout_list"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        assert response.status_code == 403

    def test_super_admin_sees_locked_user(self, client, super_admin_user, staff_user, facility):
        """User mit >=THRESHOLD LOGIN_FAILED-Audits taucht in der Liste auf."""
        from core.services.security import LOCKOUT_THRESHOLD

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"username": staff_user.username},
            )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        assert response.status_code == 200
        rows = response.context["locked_rows"]
        assert any(r["user"].pk == staff_user.pk for r in rows), (
            f"Gesperrter User {staff_user.username!r} taucht nicht in locked_rows auf."
        )

    def test_super_admin_sees_only_locked_users(self, client, super_admin_user, staff_user, facility):
        """User mit weniger als Threshold-Fehlversuchen erscheint NICHT."""
        from core.services.security import LOCKOUT_THRESHOLD

        # Threshold - 1 -> nicht gesperrt.
        for _ in range(LOCKOUT_THRESHOLD - 1):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
            )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        rows = response.context["locked_rows"]
        assert all(r["user"].pk != staff_user.pk for r in rows), (
            "User mit count<THRESHOLD darf nicht in der Sperrkonten-Liste auftauchen."
        )

    def test_super_admin_excluded_from_list(self, client, super_admin_user):
        """Refs #872: super_admin selbst taucht nie als Sperrkonto auf —
        auch wenn theoretisch viele Failed-Audits existieren."""
        from core.services.security import LOCKOUT_THRESHOLD

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=None,  # super_admin hat keine Facility
                user=super_admin_user,
                action=AuditLog.Action.LOGIN_FAILED,
            )
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        rows = response.context["locked_rows"]
        assert all(r["user"].pk != super_admin_user.pk for r in rows)

    def test_unlock_resets_visibility(self, client, super_admin_user, staff_user, facility):
        """Nach einem LOGIN_UNLOCK ohne neue Failed-Audits ist der User
        nicht mehr in der Liste — Cutoff greift."""
        from core.services.security import LOCKOUT_THRESHOLD, unlock

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
            )
        unlock(staff_user, unlocked_by=super_admin_user)

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        rows = response.context["locked_rows"]
        assert all(r["user"].pk != staff_user.pk for r in rows)


@pytest.mark.django_db
class TestSystemUnlockView:
    """``POST /system/lockouts/unlock/`` — Unlock-Flow."""

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(reverse("core:system_unlock"), {"username": "any"})
        assert response.status_code == 403

    def test_super_admin_unlock_writes_audit(self, client, super_admin_user, staff_user, facility):
        """Refs #872: erfolgreicher Unlock schreibt LOGIN_UNLOCK mit
        ``unlocked_by=<super_admin.pk>``."""
        from core.services.security import LOCKOUT_THRESHOLD

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
            )

        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_unlock"), {"username": staff_user.username})
        assert response.status_code == 302
        assert reverse("core:system_lockout_list") in response.url

        unlocks = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlocks.count() == 1
        entry = unlocks.first()
        assert entry.detail.get("unlocked_by") == str(super_admin_user.pk)

    def test_unlock_unknown_user_redirects_with_error(self, client, super_admin_user):
        """Unbekannter Username -> Redirect ohne AuditLog-Schreib."""
        client.force_login(super_admin_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK).count()
        response = client.post(reverse("core:system_unlock"), {"username": "ghost_user"})
        assert response.status_code == 302
        after = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK).count()
        assert after == before, "Unbekannter User darf keinen LOGIN_UNLOCK-Audit schreiben."

    def test_unlock_super_admin_is_not_supported(self, client, super_admin_user):
        """Refs #872: super_admin-Selbstunlock ist konzeptionell nicht
        vorgesehen — der Service-Flow filtert ihn aus."""
        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_unlock"), {"username": super_admin_user.username})
        assert response.status_code == 302
        # Kein LOGIN_UNLOCK-Audit fuer super_admin.
        assert not AuditLog.objects.filter(user=super_admin_user, action=AuditLog.Action.LOGIN_UNLOCK).exists()


# ---------------------------------------------------------------------------
# Tier 1: AuditLog-Export CSV/JSON (Refs #873)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemAuditLogExport:
    """``GET /system/audit/export/?format=csv|json`` — Streaming-Exports."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_audit_export"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_audit_export"))
        assert response.status_code == 403

    def test_csv_export_has_header_and_rows(self, client, super_admin_user, facility, staff_user):
        """CSV-Export enthaelt Header-Zeile + jeden Audit-Eintrag."""
        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.LOGIN,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_export") + "?format=csv")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment" in response["Content-Disposition"]
        # Refs #1011 (CodeQL #31): Export ueber safe_download_response -> nosniff.
        assert response["X-Content-Type-Options"] == "nosniff"

        body = b"".join(response.streaming_content).decode("utf-8")
        # Header
        assert "timestamp,user,action" in body
        # Mindestens unsere Login-Action sollte drinstehen
        assert "login" in body

    def test_json_export_is_valid_json_array(self, client, super_admin_user, facility, staff_user):
        """JSON-Export liefert ein gueltiges JSON-Array."""
        import json

        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.LOGIN,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_export") + "?format=json")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        # Refs #1011 (CodeQL #31): Export ueber safe_download_response -> nosniff.
        assert response["X-Content-Type-Options"] == "nosniff"

        body = b"".join(response.streaming_content).decode("utf-8")
        data = json.loads(body)
        assert isinstance(data, list)
        # Mindestens ein Eintrag (unser ``LOGIN``)
        assert len(data) >= 1
        # Schema-Check fuer die Felder
        first = data[0]
        for key in ("timestamp", "user", "action", "target_type", "target_id", "facility", "ip_address", "detail"):
            assert key in first, f"Schluessel {key!r} fehlt im JSON-Export."

    def test_audit_export_writes_audit_entry(self, client, super_admin_user):
        """Refs #873: vor dem Streaming wird ein AUDIT_EXPORT-AuditLog
        geschrieben (DSGVO-Spur)."""
        client.force_login(super_admin_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.AUDIT_EXPORT).count()
        response = client.get(reverse("core:system_audit_export") + "?format=csv")
        # Konsumiere den Stream, damit der Request abgeschlossen ist.
        b"".join(response.streaming_content)
        after = AuditLog.objects.filter(action=AuditLog.Action.AUDIT_EXPORT).count()
        assert after == before + 1, "AUDIT_EXPORT-Audit wurde nicht geschrieben."

        latest = AuditLog.objects.filter(action=AuditLog.Action.AUDIT_EXPORT).order_by("-timestamp").first()
        assert latest.detail.get("format") == "csv"
        assert "filter_count" in latest.detail
        assert latest.facility is None  # System-Event

    def test_export_respects_action_filter(self, client, super_admin_user, facility, staff_user):
        """Filter ``?action=login`` reduziert die Export-Zeilen."""
        AuditLog.objects.create(facility=facility, user=staff_user, action=AuditLog.Action.LOGIN)
        AuditLog.objects.create(facility=facility, user=staff_user, action=AuditLog.Action.LOGOUT)

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_export") + "?format=csv&action=login")
        body = b"".join(response.streaming_content).decode("utf-8")
        # ``logout``-Audit darf in dieser gefilterten Variante nicht
        # auftauchen.
        assert ",logout," not in body
