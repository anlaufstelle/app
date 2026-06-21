"""Tests für Rollenbezogene Arbeitszentrale (Refs #920).

Daten-Service pro Rolle: liefert einen ``dict``-Context mit den Karten/
Counts, die im rollenspezifischen Dashboard-Template gerendert werden.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from core.models import (
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

    def test_no_recent_clients_key(self, facility, staff_user):
        ctx = staff_dashboard_context(staff_user, facility)
        assert "recent_clients" not in ctx

    def test_surfaces_overdue_workitems_first(self, facility, staff_user, client_identified):
        today = timezone.localdate()
        WorkItem.objects.create(
            facility=facility,
            title="Ohne Termin",
            assigned_to=staff_user,
            client=client_identified,
            created_by=staff_user,
        )
        overdue = WorkItem.objects.create(
            facility=facility,
            title="Überfällig",
            assigned_to=staff_user,
            client=client_identified,
            created_by=staff_user,
            due_date=today - timedelta(days=3),
        )
        ctx = staff_dashboard_context(staff_user, facility)
        assert ctx["my_open_workitems"][0].pk == overdue.pk
        assert ctx["my_overdue_count"] == 1

    def test_no_overdue_when_due_dates_future(self, facility, staff_user, client_identified):
        WorkItem.objects.create(
            facility=facility,
            title="Künftig",
            assigned_to=staff_user,
            client=client_identified,
            created_by=staff_user,
            due_date=timezone.localdate() + timedelta(days=5),
        )
        ctx = staff_dashboard_context(staff_user, facility)
        assert ctx["my_overdue_count"] == 0


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

    def test_legal_hold_expiring_today_counts_as_active(self, facility, lead_user, client_identified):
        """Grenzfall: Ablaufdatum == heute zählt noch als aktiv (is_active nutzt striktes <)."""
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=client_identified.pk,
            reason="Läuft heute ab",
            created_by=lead_user,
            expires_at=date.today(),
        )

        ctx = lead_dashboard_context(lead_user, facility)

        assert ctx["active_legal_holds"] == 1

    def test_legal_hold_expired_yesterday_does_not_count(self, facility, lead_user, client_identified):
        """Grenzfall: gestern abgelaufener Hold zählt nicht mehr als aktiv."""
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=client_identified.pk,
            reason="Gestern abgelaufen",
            created_by=lead_user,
            expires_at=date.today() - timedelta(days=1),
        )

        ctx = lead_dashboard_context(lead_user, facility)

        assert ctx["active_legal_holds"] == 0

    def test_legal_hold_dismissed_does_not_count(self, facility, lead_user, client_identified):
        """Aufgehobener Hold zählt nicht, auch wenn das Ablaufdatum in der Zukunft liegt."""
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=client_identified.pk,
            reason="Aufgehoben trotz Zukunfts-Ablauf",
            created_by=lead_user,
            expires_at=date.today() + timedelta(days=30),
            dismissed_at=timezone.now(),
        )

        ctx = lead_dashboard_context(lead_user, facility)

        assert ctx["active_legal_holds"] == 0


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
