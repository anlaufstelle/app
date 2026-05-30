"""Unit tests for the typed audit helpers in ``core.services.audit``.

Refs #901 / FND-002: Diese Tests sichern die Signatur und das Default-
Verhalten der fünf typed Helper (audit_event, audit_client_event,
audit_retention_decision, audit_security_violation, audit_system_view).
Migrations-Tests pro Domäne (clients, retention, file_vault, system)
laufen in den jeweiligen Domain-Test-Dateien — hier nur die
Helper-API selbst.
"""

from __future__ import annotations

import pytest

from core.models import AuditLog
from core.services.audit import (
    audit_client_event,
    audit_event,
    audit_retention_decision,
    audit_security_violation,
    audit_system_view,
)


@pytest.mark.django_db
class TestAuditEvent:
    """Generischer Service-/Cron-Helper ohne Request."""

    def test_writes_basic_entry(self, facility, staff_user):
        entry = audit_event(
            AuditLog.Action.CLIENT_UPDATE,
            user=staff_user,
            facility=facility,
            target_type="Client",
            target_id="abc-123",
            detail={"changed_fields": ["notes"]},
        )
        entry.refresh_from_db()
        assert entry.action == AuditLog.Action.CLIENT_UPDATE
        assert entry.facility == facility
        assert entry.user == staff_user
        assert entry.target_type == "Client"
        assert entry.target_id == "abc-123"
        assert entry.detail == {"changed_fields": ["notes"]}
        # Kein Request → kein ip_address.
        assert entry.ip_address in ("", None)

    def test_target_obj_resolves_type_and_id(self, sample_event, staff_user, facility):
        entry = audit_event(
            AuditLog.Action.EVENT_CREATE,
            user=staff_user,
            facility=facility,
            target_obj=sample_event,
        )
        entry.refresh_from_db()
        assert entry.target_type == sample_event.__class__.__name__
        assert entry.target_id == str(sample_event.pk)

    def test_user_and_facility_may_be_none(self, facility):
        # Cron-Pfade haben keinen User; system-wide Events keine Facility.
        entry = audit_event(
            AuditLog.Action.DELETE,
            user=None,
            facility=None,
            target_type="System",
            target_id="cron",
            detail={"category": "retention_cron"},
        )
        entry.refresh_from_db()
        assert entry.user is None
        assert entry.facility is None
        assert entry.detail == {"category": "retention_cron"}

    def test_default_detail_is_empty_dict(self, facility, staff_user):
        entry = audit_event(
            AuditLog.Action.CLIENT_UPDATE,
            user=staff_user,
            facility=facility,
            target_type="Client",
            target_id="x",
        )
        entry.refresh_from_db()
        assert entry.detail == {}


@pytest.mark.django_db
class TestAuditClientEvent:
    def test_writes_with_facility_from_client(self, client_identified, staff_user):
        entry = audit_client_event(
            client_identified,
            staff_user,
            AuditLog.Action.CLIENT_UPDATE,
            changed_fields=["notes"],
        )
        entry.refresh_from_db()
        assert entry.facility == client_identified.facility
        assert entry.user == staff_user
        assert entry.action == AuditLog.Action.CLIENT_UPDATE
        assert entry.target_type == "Client"
        assert entry.target_id == str(client_identified.pk)
        assert entry.detail == {"changed_fields": ["notes"]}

    def test_anonymization_pfad_user_none(self, client_identified):
        # Cron-Anonymisierung: kein User-Kontext.
        entry = audit_client_event(
            client_identified,
            None,
            AuditLog.Action.CLIENT_ANONYMIZED,
            category="retention_auto",
        )
        entry.refresh_from_db()
        assert entry.user is None
        assert entry.detail == {"category": "retention_auto"}


@pytest.mark.django_db
class TestAuditRetentionDecision:
    def test_proposal_approved(self, facility, sample_event, staff_user):
        entry = audit_retention_decision(
            facility,
            target_type="Event",
            target_id=sample_event.pk,
            action=AuditLog.Action.DELETE,
            category="retention_approved",
            user=staff_user,
            event_count=1,
        )
        entry.refresh_from_db()
        assert entry.facility == facility
        assert entry.user == staff_user
        assert entry.action == AuditLog.Action.DELETE
        assert entry.target_type == "Event"
        assert entry.target_id == str(sample_event.pk)
        assert entry.detail["category"] == "retention_approved"
        assert entry.detail["event_count"] == 1

    def test_bulk_cron_user_none(self, facility):
        entry = audit_retention_decision(
            facility,
            target_type="Event",
            target_id="bulk",
            action=AuditLog.Action.DELETE,
            category="retention_bulk",
            count=42,
        )
        entry.refresh_from_db()
        assert entry.user is None
        assert entry.detail == {"category": "retention_bulk", "count": 42}


@pytest.mark.django_db
class TestAuditSecurityViolation:
    def test_writes_with_reason_in_detail(self, facility, staff_user, sample_event):
        entry = audit_security_violation(
            facility,
            staff_user,
            target_type="EventAttachment",
            target_id=sample_event.pk,
            reason="virus_detected",
            filename="malware.pdf",
        )
        entry.refresh_from_db()
        assert entry.action == AuditLog.Action.SECURITY_VIOLATION
        assert entry.detail["reason"] == "virus_detected"
        assert entry.detail["filename"] == "malware.pdf"

    def test_target_id_may_be_none(self, facility, staff_user):
        # Extension-Verstoß vor dem Speichern: noch keine PK.
        entry = audit_security_violation(
            facility,
            staff_user,
            target_type="Upload",
            target_id=None,
            reason="extension_blocked",
            filename="data.exe",
        )
        entry.refresh_from_db()
        assert entry.target_id == ""
        assert entry.detail == {"reason": "extension_blocked", "filename": "data.exe"}


@pytest.mark.django_db
class TestAuditSystemView:
    def test_writes_with_facility_none(self, rf, staff_user):
        request = rf.get("/system/")
        request.user = staff_user
        entry = audit_system_view(
            request,
            AuditLog.Action.SYSTEM_VIEW,
            target_type="SystemDashboardView",
        )
        entry.refresh_from_db()
        assert entry.facility is None
        assert entry.user == staff_user
        assert entry.action == AuditLog.Action.SYSTEM_VIEW
        assert entry.target_type == "SystemDashboardView"

    def test_anonymous_request_drops_user(self, rf):
        from django.contrib.auth.models import AnonymousUser

        request = rf.get("/system/")
        request.user = AnonymousUser()
        entry = audit_system_view(
            request,
            AuditLog.Action.SYSTEM_VIEW,
            target_type="SystemDashboardView",
        )
        entry.refresh_from_db()
        assert entry.user is None


@pytest.fixture
def rf():
    from django.test import RequestFactory

    return RequestFactory()
