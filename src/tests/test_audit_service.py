"""Tests für services/audit.log_audit_event."""

import pytest
from django.test import RequestFactory

from core.models import AuditLog
from core.services.audit import log_audit_event


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.mark.django_db
class TestLogAuditEvent:
    def test_creates_entry_with_target_obj(self, rf, staff_user, client_identified):
        request = rf.get("/")
        request.user = staff_user
        request.current_facility = staff_user.facility
        request.META["REMOTE_ADDR"] = "10.1.2.3"

        entry = log_audit_event(request, AuditLog.Action.VIEW_QUALIFIED, target_obj=client_identified)

        assert entry.pk is not None
        assert entry.facility == staff_user.facility
        assert entry.user == staff_user
        assert entry.action == AuditLog.Action.VIEW_QUALIFIED
        assert entry.target_type == "Client"
        assert entry.target_id == str(client_identified.pk)
        assert entry.ip_address == "10.1.2.3"

    def test_target_type_override(self, rf, staff_user, client_identified):
        request = rf.get("/")
        request.user = staff_user
        request.current_facility = staff_user.facility
        request.META["REMOTE_ADDR"] = "10.1.2.3"

        entry = log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_obj=client_identified,
            target_type="Client-JSON",
            detail={"format": "JSON"},
        )

        assert entry.target_type == "Client-JSON"
        assert entry.target_id == str(client_identified.pk)
        assert entry.detail == {"format": "JSON"}

    def test_works_without_target(self, rf, staff_user):
        request = rf.get("/")
        request.user = staff_user
        request.current_facility = staff_user.facility
        request.META["REMOTE_ADDR"] = "127.0.0.1"

        entry = log_audit_event(request, AuditLog.Action.LOGIN, detail={"info": "test"})

        assert entry.action == AuditLog.Action.LOGIN
        assert entry.target_type == ""
        assert entry.target_id == ""
        assert entry.detail == {"info": "test"}

    def test_anonymous_user_no_user_attached(self, rf, facility):
        from django.contrib.auth.models import AnonymousUser

        request = rf.get("/")
        request.user = AnonymousUser()
        request.current_facility = facility
        request.META["REMOTE_ADDR"] = "1.2.3.4"

        entry = log_audit_event(request, AuditLog.Action.LOGIN_FAILED, detail={"username": "none"})

        assert entry.user is None
        assert entry.facility == facility

    def test_missing_current_facility(self, rf, staff_user):
        request = rf.get("/")
        request.user = staff_user
        # Kein current_facility gesetzt — Middleware hat nicht gegriffen.
        request.META["REMOTE_ADDR"] = "1.2.3.4"

        entry = log_audit_event(request, AuditLog.Action.LOGIN, detail={})

        assert entry.facility is None
