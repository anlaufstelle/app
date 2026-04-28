"""Audit-coverage regression tests (Refs #532).

Verify that:
- update_client / update_case / update_workitem services write AuditLog entries
  with action=*_UPDATE and a `changed_fields` detail payload (no PII values)
- Settings save via the admin layer writes a SETTINGS_CHANGE audit entry
"""

import pytest

from core.models import AuditLog, Case
from core.services.cases import update_case
from core.services.clients import update_client
from core.services.settings import log_settings_change, snapshot_settings, update_settings
from core.services.workitems import update_workitem


@pytest.mark.django_db
class TestClientUpdateAudit:
    def test_update_client_writes_audit_log(self, client_identified, staff_user):
        before = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE).count()
        update_client(client_identified, staff_user, notes="Geändert")
        after = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE).count()
        assert after == before + 1

    def test_update_client_audit_has_changed_field_names(self, client_identified, staff_user):
        update_client(client_identified, staff_user, notes="Neue Notiz")
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_UPDATE,
            target_id=str(client_identified.pk),
        ).latest("timestamp")
        assert "changed_fields" in entry.detail
        assert "notes" in entry.detail["changed_fields"]
        # No PII value in detail payload
        assert "Neue Notiz" not in str(entry.detail)

    def test_update_client_no_audit_when_nothing_changed(self, client_identified, staff_user):
        before = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE).count()
        update_client(client_identified, staff_user, notes=client_identified.notes)
        after = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE).count()
        assert after == before


@pytest.mark.django_db
class TestCaseUpdateAudit:
    def test_update_case_writes_audit_log(self, facility, client_identified, staff_user):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Original",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        before = AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE).count()
        update_case(case, staff_user, title="Geändert")
        after = AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE).count()
        assert after == before + 1

    def test_update_case_audit_has_changed_field_names(self, facility, client_identified, staff_user):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Original",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        update_case(case, staff_user, title="Neuer Titel")
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CASE_UPDATE, target_id=str(case.pk)
        ).latest("timestamp")
        assert "title" in entry.detail.get("changed_fields", [])
        assert "Neuer Titel" not in str(entry.detail)


@pytest.mark.django_db
class TestWorkItemUpdateAudit:
    def test_update_workitem_writes_audit_log(self, sample_workitem, staff_user):
        before = AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).count()
        update_workitem(sample_workitem, staff_user, title="Geändert")
        after = AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).count()
        assert after == before + 1

    def test_update_workitem_audit_has_changed_field_names(self, sample_workitem, staff_user):
        update_workitem(sample_workitem, staff_user, title="Neuer Titel")
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE,
            target_id=str(sample_workitem.pk),
        ).latest("timestamp")
        assert "title" in entry.detail.get("changed_fields", [])
        assert "Neuer Titel" not in str(entry.detail)


@pytest.mark.django_db
class TestSettingsChangeAudit:
    def test_update_settings_writes_audit_log(self, settings_obj, staff_user):
        before = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        update_settings(settings_obj, staff_user, session_timeout_minutes=42)
        after = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        assert after == before + 1

    def test_log_settings_change_only_when_diff(self, settings_obj, staff_user):
        snap = snapshot_settings(settings_obj)
        before = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        # No change → no audit entry
        log_settings_change(settings_obj, staff_user, snap)
        after_unchanged = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        assert after_unchanged == before
        # Mutate and log
        settings_obj.session_timeout_minutes = (settings_obj.session_timeout_minutes or 30) + 5
        settings_obj.save()
        log_settings_change(settings_obj, staff_user, snap)
        after_changed = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        assert after_changed == before + 1

    def test_settings_audit_does_not_leak_values(self, settings_obj, staff_user):
        update_settings(settings_obj, staff_user, facility_full_name="Geheime Einrichtung XYZ")
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.SETTINGS_CHANGE,
            target_id=str(settings_obj.pk),
        ).latest("timestamp")
        assert "Geheime Einrichtung XYZ" not in str(entry.detail)
        assert "facility_full_name" in entry.detail.get("changed_fields", [])
