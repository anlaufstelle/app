"""Tests für Rollenbezogene Arbeitszentrale (Refs #920).

Daten-Service pro Rolle: liefert einen ``dict``-Context mit den Karten/
Counts, die im rollenspezifischen Dashboard-Template gerendert werden.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import (
    DeletionRequest,
    Event,
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

    def test_active_legal_holds_not_in_context(self, facility, lead_user):
        """Refs #1759: Die Legal-Holds-Kachel ist aus dem Lead-Dashboard entfernt
        (Legal Holds bleiben reines Superadmin-Werkzeug hinter dem #1735-Guard,
        ``system_legal_hold_list``/``SuperAdminRequiredMixin``) — die tote
        Aggregation darf nicht mehr laufen."""
        ctx = lead_dashboard_context(lead_user, facility)

        assert "active_legal_holds" not in ctx


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

    def test_mfa_zaehler_skaliert_nicht_mit_der_nutzerzahl(self, facility, admin_user):
        """Refs #1816: Der MFA-Zaehler darf kein N+1 ausloesen.

        ``users_without_mfa`` lief ueber die Python-Property
        ``User.has_confirmed_totp_device``, die pro Nutzer eine eigene Query
        gegen ``EncryptedTOTPDevice`` absetzt. Die Kachel kostete damit eine
        Query je aktivem Nutzer der Einrichtung — auf ``/verwaltung/``, das
        jede Leitung bei jedem Aufruf sieht.

        Der Test pinnt keine absolute Query-Zahl (die driftet mit jeder
        Kachel), sondern die Invariante: dieselbe Zahl bei 1 und bei 8
        Nutzern.
        """
        Settings.objects.create(facility=facility, mfa_enforced_facility_wide=True)

        with CaptureQueriesContext(connection) as wenige:
            facility_admin_dashboard_context(admin_user, facility)

        for i in range(7):
            User.objects.create_user(
                username=f"mfa-last-{i}",
                role=User.Role.STAFF,
                facility=facility,
            )

        with CaptureQueriesContext(connection) as viele:
            ctx = facility_admin_dashboard_context(admin_user, facility)

        assert ctx["users_without_mfa"] == 8, "Der Zaehler muss alle acht Nutzer ohne TOTP-Device erfassen."
        assert len(viele) == len(wenige), (
            f"MFA-Zaehler skaliert mit der Nutzerzahl (N+1): {len(wenige)} Queries bei 1 Nutzer, "
            f"{len(viele)} bei 8. Erwartet ist eine konstante Zahl — die Pruefung auf ein "
            f"bestaetigtes TOTP-Device gehoert als Exists()-Annotation ins Queryset, "
            f"nicht in eine Python-Property je Nutzer (Refs #1816)."
        )


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
