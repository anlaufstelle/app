"""Tests für Rollenbezogene Arbeitszentrale (Refs #920).

Daten-Service pro Rolle: liefert einen ``dict``-Context mit den Karten/
Counts, die im rollenspezifischen Dashboard-Template gerendert werden.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import (
    Client,
    DeletionRequest,
    Event,
    LegalHold,
    RetentionProposal,
    Settings,
    User,
    WorkItem,
)
from core.services.dashboard import (
    facility_admin_dashboard_context,
    lead_dashboard_context,
    staff_dashboard_context,
    super_admin_dashboard_context,
)


@pytest.mark.django_db
class TestStaffDashboardContext:
    def test_returns_today_events_count(self, facility, staff_user, doc_type_contact, client_identified):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timedelta(days=2),
            data_json={"dauer": 1},
            created_by=staff_user,
        )

        ctx = staff_dashboard_context(staff_user, facility)

        assert ctx["today_events_count"] == 1

    def test_returns_open_workitems_assigned_to_user(self, facility, staff_user, client_identified):
        WorkItem.objects.create(
            facility=facility,
            title="Meine Aufgabe",
            assigned_to=staff_user,
            client=client_identified,
            created_by=staff_user,
        )
        # Eine Aufgabe einer anderen Person — soll nicht gezählt werden
        other = User.objects.create_user(username="other", password="x", facility=facility, role=User.Role.STAFF)
        WorkItem.objects.create(
            facility=facility,
            title="Fremde Aufgabe",
            assigned_to=other,
            client=client_identified,
            created_by=other,
        )

        ctx = staff_dashboard_context(staff_user, facility)

        assert ctx["my_open_workitems_count"] == 1

    def test_returns_recent_clients_for_user(self, facility, staff_user, doc_type_contact):
        c1 = Client.objects.create(facility=facility, pseudonym="A1", created_by=staff_user)
        c2 = Client.objects.create(facility=facility, pseudonym="B2", created_by=staff_user)
        # Beide bekommen Events vom staff_user — c2 zuletzt
        Event.objects.create(
            facility=facility,
            client=c1,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timedelta(days=2),
            data_json={"dauer": 1},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=c2,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            created_by=staff_user,
        )

        ctx = staff_dashboard_context(staff_user, facility)

        recent = ctx["recent_clients"]
        assert len(recent) >= 2
        # c2 zuerst
        assert recent[0].pk == c2.pk


@pytest.mark.django_db
class TestLeadDashboardContext:
    def test_counts_pending_deletion_requests(self, facility, lead_user, staff_user, client_identified):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_identified.pk,
            reason="Test",
            requested_by=staff_user,
        )
        # Approved sollte nicht zählen
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_identified.pk,
            reason="Test 2",
            requested_by=staff_user,
            status=DeletionRequest.Status.APPROVED,
        )

        ctx = lead_dashboard_context(lead_user, facility)

        assert ctx["pending_deletion_requests"] == 1

    def test_counts_pending_retention_proposals(self, facility, lead_user, client_identified):
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=client_identified.pk,
            deletion_due_at=timezone.localdate(),
            retention_category="anonymous",
        )

        ctx = lead_dashboard_context(lead_user, facility)

        assert ctx["pending_retention_proposals"] == 1

    def test_counts_active_legal_holds(self, facility, lead_user, client_identified):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=client_identified.pk,
            reason="Test",
            created_by=lead_user,
        )
        # Aufgehobener Hold zählt nicht
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=client_identified.pk,
            reason="Test 2",
            created_by=lead_user,
            dismissed_at=timezone.now(),
        )

        ctx = lead_dashboard_context(lead_user, facility)

        assert ctx["active_legal_holds"] == 1


@pytest.mark.django_db
class TestFacilityAdminDashboardContext:
    def test_counts_users_without_mfa(self, facility, admin_user, staff_user):
        # admin_user ist facility_admin, staff_user ist staff — beide ohne MFA
        Settings.objects.create(facility=facility, mfa_enforced_facility_wide=True)

        ctx = facility_admin_dashboard_context(admin_user, facility)

        # Mindestens 2 (admin_user + staff_user) ohne TOTP-Device
        assert ctx["users_without_mfa"] >= 2

    def test_returns_settings_warnings_for_disabled_mfa(self, facility, admin_user):
        # Setting ohne MFA-Enforcement und ohne k-anon
        Settings.objects.create(
            facility=facility,
            mfa_enforced_facility_wide=False,
            retention_use_k_anonymization=False,
        )

        ctx = facility_admin_dashboard_context(admin_user, facility)

        warnings = ctx["settings_warnings"]
        assert any("MFA" in w or "2FA" in w for w in warnings)


@pytest.mark.django_db
class TestSuperAdminDashboardContext:
    def test_returns_facility_count(self, facility, super_admin_user):
        ctx = super_admin_dashboard_context(super_admin_user)

        assert ctx["facilities_count"] >= 1

    def test_returns_recent_audit_events_count(self, facility, super_admin_user, staff_user, client_identified):
        from core.models import AuditLog

        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.LOGIN,
            target_type="User",
            target_id=str(staff_user.pk),
        )

        ctx = super_admin_dashboard_context(super_admin_user)

        # Letzte 24 h: mindestens unser frisches AuditLog
        assert ctx["recent_audit_events_count"] >= 1
