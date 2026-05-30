"""Coverage-Tests fuer ``core.views.system.audit`` Branches.

Deckt die Filter- und Export-Branches:

* :class:`SystemAuditLogListView`.get — action/user/facility/date_from/date_to Filter (Lines 50-76).
* :class:`SystemAuditLogDetailView`.get — Fallback ``get_object_or_404`` (Line 126).
* :func:`_filter_auditlog_queryset` — Spiegel-Filter im Export (Lines 144-169).
* :class:`SystemAuditLogExportView`.get — ``?format=`` Default csv (Line 214).

Refs Welle 10 / Bucket D — siehe #949.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestSystemAuditLogListViewFilters:
    """Liste mit den verschiedenen Filter-Kombinationen aufrufen."""

    def _login(self, client, super_admin_user):
        super_admin_user.set_password("testpass123")
        super_admin_user.save()
        client.force_login(super_admin_user)

    def _create_entry(self, **kwargs):
        """Erzeuge einen AuditLog-Eintrag mit Defaults — Kwargs ueberschreiben."""
        from core.models import AuditLog

        defaults = {
            "action": AuditLog.Action.LOGIN,
            "user": None,
            "facility": None,
            "target_type": "",
            "target_id": "",
        }
        defaults.update(kwargs)
        return AuditLog.objects.create(**defaults)

    def test_filter_by_action(self, client, super_admin_user, facility):
        self._login(client, super_admin_user)
        from core.models import AuditLog

        self._create_entry(action=AuditLog.Action.LOGIN)
        self._create_entry(action=AuditLog.Action.LOGIN_FAILED)
        response = client.get(
            reverse("core:system_audit_list") + f"?action={AuditLog.Action.LOGIN_FAILED}"
        )
        assert response.status_code == 200

    def test_filter_by_user(self, client, super_admin_user, staff_user):
        self._login(client, super_admin_user)
        self._create_entry(user=staff_user)
        response = client.get(reverse("core:system_audit_list") + f"?user={staff_user.pk}")
        assert response.status_code == 200

    def test_filter_by_facility_null_sentinel(self, client, super_admin_user):
        """Line 62: ``facility=__null__`` filtert System-Events (facility IS NULL)."""
        self._login(client, super_admin_user)
        self._create_entry(facility=None)
        response = client.get(reverse("core:system_audit_list") + "?facility=__null__")
        assert response.status_code == 200

    def test_filter_by_facility_id(self, client, super_admin_user, facility):
        """Line 64: ``facility=<id>`` filtert nach Facility."""
        self._login(client, super_admin_user)
        self._create_entry(facility=facility)
        response = client.get(reverse("core:system_audit_list") + f"?facility={facility.pk}")
        assert response.status_code == 200

    def test_filter_by_date_from_and_to(self, client, super_admin_user):
        """Lines 67-76: date_from + date_to Filter."""
        self._login(client, super_admin_user)
        self._create_entry()
        response = client.get(
            reverse("core:system_audit_list") + "?date_from=2020-01-01&date_to=2099-12-31"
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestSystemAuditLogDetailView:
    def test_detail_renders_entry(self, client, super_admin_user, facility):
        """Lines 122-127: Detail-View laedt + rendert AuditLog-Eintrag."""
        super_admin_user.set_password("testpass123")
        super_admin_user.save()
        client.force_login(super_admin_user)
        from core.models import AuditLog

        entry = AuditLog.objects.create(
            action=AuditLog.Action.LOGIN,
            user=None,
            facility=facility,
        )
        response = client.get(reverse("core:system_audit_detail", args=[entry.pk]))
        assert response.status_code == 200

    def test_detail_404_for_missing_entry(self, client, super_admin_user):
        """Line 126: Fallback ``get_object_or_404`` -> 404 bei unbekannter PK."""
        super_admin_user.set_password("testpass123")
        super_admin_user.save()
        client.force_login(super_admin_user)
        import uuid

        bogus = uuid.uuid4()
        response = client.get(reverse("core:system_audit_detail", args=[bogus]))
        assert response.status_code == 404


@pytest.mark.django_db
class TestSystemAuditLogExportView:
    def _login(self, client, super_admin_user):
        super_admin_user.set_password("testpass123")
        super_admin_user.save()
        client.force_login(super_admin_user)

    def test_export_default_format_is_csv(self, client, super_admin_user):
        """Lines 212-214: kein ?format -> CSV-Default."""
        self._login(client, super_admin_user)
        response = client.get(reverse("core:system_audit_export"))
        assert response.status_code == 200
        assert "csv" in response["Content-Type"]

    def test_export_invalid_format_falls_back_to_csv(self, client, super_admin_user):
        """Line 214: unbekanntes Format -> Fallback CSV."""
        self._login(client, super_admin_user)
        response = client.get(reverse("core:system_audit_export") + "?format=xml")
        assert response.status_code == 200
        assert "csv" in response["Content-Type"]

    def test_export_json_format(self, client, super_admin_user, facility):
        """Export als JSON: Stream ist ein valider JSON-Array."""
        self._login(client, super_admin_user)
        from core.models import AuditLog

        AuditLog.objects.create(
            action=AuditLog.Action.LOGIN, facility=facility
        )
        response = client.get(reverse("core:system_audit_export") + "?format=json")
        assert response.status_code == 200
        assert "json" in response["Content-Type"]
        # Stream konsumieren
        content = b"".join(response.streaming_content).decode("utf-8")
        assert content.startswith("[")
        assert content.endswith("]")

    def test_export_csv_with_filters_applied(self, client, super_admin_user, facility, staff_user):
        """Lines 144-169: ``_filter_auditlog_queryset`` mit allen Filtern."""
        self._login(client, super_admin_user)
        from core.models import AuditLog

        AuditLog.objects.create(
            action=AuditLog.Action.LOGIN,
            user=staff_user,
            facility=facility,
        )
        AuditLog.objects.create(action=AuditLog.Action.LOGIN_FAILED, facility=None)
        params = (
            f"?action={AuditLog.Action.LOGIN}"
            f"&user={staff_user.pk}"
            f"&facility={facility.pk}"
            "&date_from=2020-01-01"
            "&date_to=2099-12-31"
        )
        response = client.get(reverse("core:system_audit_export") + params)
        assert response.status_code == 200
        # Stream konsumieren
        content = b"".join(response.streaming_content).decode("utf-8")
        assert "timestamp" in content  # Header-Zeile

    def test_export_csv_with_facility_null_sentinel(self, client, super_admin_user):
        """Lines 154-156: Export-Filter mit ``facility=__null__`` Sentinel."""
        self._login(client, super_admin_user)
        from core.models import AuditLog

        AuditLog.objects.create(action=AuditLog.Action.LOGIN, facility=None)
        response = client.get(reverse("core:system_audit_export") + "?facility=__null__")
        assert response.status_code == 200
