"""Refs #1053 (Variante A): Recht „Löschbestätigung" entkoppelt vom Rollenmodell.

Der Genehmiger-Pool für Vier-Augen-Löschungen wird kuratiert über das
einzeln vergebbare Recht ``can_confirm_deletion`` statt aus der Rolle
abgeleitet. Löst den Deadlock „einzelne Leitung stellt Antrag,
Anwendungsbetreuung abwesend": eine erfahrene Fachkraft kann
Lösch-Mitzeichnerin werden, ohne Leitung zu sein. Genehmiger ≠
Antragsteller bleibt auf allen drei Ebenen (View, Service-SSoT,
DB-Constraint) erzwungen.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event, LegalHold, User
from core.services.events import approve_deletion, reject_deletion, request_deletion


@pytest.fixture
def event_qualified(facility, client_qualified, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 20, "notiz": "Qualifiziert"},
        created_by=staff_user,
    )


@pytest.fixture
def confirmer_staff(facility):
    """Fachkraft mit dem Sonderrecht „Löschbestätigung" (keine Leitung)."""
    user = User.objects.create_user(
        username="confirmer",
        role=User.Role.STAFF,
        facility=facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.mark.django_db
class TestSingleLeadDeadlockResolved:
    """Akzeptanz: Einrichtung mit einer Leitung + benanntem Bestätiger
    schließt eine von der Leitung gestellte Löschung ohne
    Anwendungsbetreuung ab."""

    def test_staff_with_right_approves_leads_request(self, lead_user, confirmer_staff, event_qualified):
        dr = request_deletion(event_qualified, lead_user, "Art.-17-Antrag")
        approve_deletion(dr, confirmer_staff)
        dr.refresh_from_db()
        event_qualified.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert event_qualified.is_deleted is True

    def test_view_flow_for_confirmer(self, client, lead_user, confirmer_staff, event_qualified):
        dr = request_deletion(event_qualified, lead_user, "Art.-17-Antrag")
        client.force_login(confirmer_staff)
        assert client.get(reverse("core:deletion_request_list")).status_code == 200
        assert client.get(reverse("core:deletion_review", kwargs={"pk": dr.pk})).status_code == 200
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": dr.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED


@pytest.mark.django_db
class TestRightEnforcement:
    def test_service_rejects_reviewer_without_right(self, staff_user, lead_user, event_qualified):
        dr = request_deletion(event_qualified, lead_user, "Antrag")
        with pytest.raises(ValidationError, match="Löschbestätigung"):
            approve_deletion(dr, staff_user)

    def test_reject_service_rejects_distinct_reviewer_without_right(self, staff_user, lead_user, event_qualified):
        dr = request_deletion(event_qualified, lead_user, "Antrag")

        with pytest.raises(ValidationError) as exc_info:
            reject_deletion(dr, staff_user)

        assert exc_info.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REJECTED,
            target_id=str(dr.pk),
        ).exists()

    @pytest.mark.parametrize("role", list(User.Role.values))
    @pytest.mark.parametrize("has_right", [False, True], ids=["without-right", "with-right"])
    def test_reject_permission_depends_on_right_not_role(self, facility, lead_user, event_qualified, role, has_right):
        reviewer = User.objects.create_user(
            username=f"reject-{role}-{has_right}",
            role=role,
            facility=facility,
            is_staff=True,
            can_confirm_deletion=has_right,
        )
        dr = request_deletion(event_qualified, lead_user, "Antrag")

        if not has_right:
            with pytest.raises(ValidationError, match="Löschbestätigung"):
                reject_deletion(dr, reviewer)
        else:
            reject_deletion(dr, reviewer)

        dr.refresh_from_db()
        expected = DeletionRequest.Status.REJECTED if has_right else DeletionRequest.Status.PENDING
        assert dr.status == expected
        assert dr.reviewed_by_id == (reviewer.pk if has_right else None)

    def test_authorized_distinct_reviewer_can_reject_under_active_hold(
        self, facility, lead_user, confirmer_staff, event_qualified
    ):
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event_qualified.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        dr = request_deletion(event_qualified, lead_user, "Antrag")

        reject_deletion(dr, confirmer_staff)

        dr.refresh_from_db()
        hold.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
        assert dr.reviewed_by_id == confirmer_staff.pk
        assert hold.dismissed_at is None

    @pytest.mark.parametrize(
        "status",
        [
            DeletionRequest.Status.APPROVED,
            DeletionRequest.Status.REJECTED,
            DeletionRequest.Status.WITHDRAWN,
        ],
    )
    def test_authorized_distinct_reviewer_cannot_reject_decided_request(
        self, lead_user, confirmer_staff, event_qualified, status
    ):
        dr = request_deletion(event_qualified, lead_user, "Antrag")
        DeletionRequest.objects.filter(pk=dr.pk).update(
            status=status,
            reviewed_by=confirmer_staff if status != DeletionRequest.Status.WITHDRAWN else None,
            reviewed_at=timezone.now(),
        )

        with pytest.raises(ValidationError) as exc_info:
            reject_deletion(dr, confirmer_staff)

        assert exc_info.value.messages == ["Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."]
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REJECTED,
            target_id=str(dr.pk),
        ).exists()

    def test_lead_without_right_gets_403_on_review(self, client, lead_user, staff_user, event_qualified):
        dr = request_deletion(event_qualified, staff_user, "Antrag")
        lead_user.can_confirm_deletion = False
        lead_user.save()
        client.force_login(lead_user)
        assert client.get(reverse("core:deletion_review", kwargs={"pk": dr.pk})).status_code == 403

    def test_lead_fixture_has_right_by_default(self, lead_user, admin_user):
        # Spiegelt die Backfill-Migration: bestehende Leitungen/Admins
        # behalten ihre Genehmiger-Fähigkeit.
        assert lead_user.can_confirm_deletion is True
        assert admin_user.can_confirm_deletion is True

    def test_staff_without_right_cannot_access_list(self, client, staff_user):
        client.force_login(staff_user)
        assert client.get(reverse("core:deletion_request_list")).status_code == 403


@pytest.mark.django_db
class TestRejectServiceEnforcesFourEyes:
    """Refs #2082: Der Ereignis-Reject erzwingt „Reviewer != Antragsteller"
    selbst — nicht erst die View und nicht erst der DB-CHECK.

    ``reject_deletion`` war der einzige der vier Reject-Wege
    (Person/Fall/Zusammenfuehrung/Ereignis) ohne diesen Guard im Service.
    Abgefangen wurde der Eigenreview bis dahin von
    ``DeletionRequestReviewView`` und dahinter von der CHECK-Constraint
    ``deletion_request_different_reviewer`` — letztere aber als
    ``IntegrityError`` mitten in der Transaktion, also ohne die deutsche
    Meldung, die jeder andere Reject-Weg liefert. Der Service ist die Single
    Source of Truth (dasselbe Argument wie in ``approve_deletion``).
    """

    def test_service_rejects_self_review(self, lead_user, event_qualified):
        dr = request_deletion(event_qualified, lead_user, "Antrag")
        with pytest.raises(ValidationError, match="Vier-Augen"):
            reject_deletion(dr, lead_user)

    def test_self_review_leaves_request_event_and_audit_untouched(self, lead_user, event_qualified):
        """Kein Zustandswechsel, kein Reviewer-Stempel, keine Audit-Zeile —
        der Guard sitzt VOR der Sperre und damit vor jedem Schreibzugriff."""
        dr = request_deletion(event_qualified, lead_user, "Antrag")
        with pytest.raises(ValidationError, match="Vier-Augen"):
            reject_deletion(dr, lead_user)
        dr.refresh_from_db()
        event_qualified.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
        assert dr.reviewed_at is None
        assert event_qualified.is_deleted is False
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REJECTED,
            target_id=str(dr.pk),
        ).exists()

    def test_foreign_reviewer_still_rejects(self, lead_user, confirmer_staff, event_qualified):
        """Positivkontrolle: der zulaessige Fremd-Reviewer lehnt weiterhin ab
        — der Guard verengt den Weg nicht ueber das Vier-Augen-Prinzip hinaus."""
        dr = request_deletion(event_qualified, lead_user, "Antrag")
        reject_deletion(dr, confirmer_staff)
        dr.refresh_from_db()
        event_qualified.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
        assert dr.reviewed_by_id == confirmer_staff.pk
        assert event_qualified.is_deleted is False
        assert AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REJECTED,
            target_id=str(dr.pk),
        ).exists()


@pytest.mark.django_db
class TestListUI:
    def test_review_link_only_for_right_holders(self, client, lead_user, staff_user, confirmer_staff, event_qualified):
        request_deletion(event_qualified, staff_user, "Antrag")
        # Leitung ohne Recht: Liste ja (Transparenz), aber kein Prüfen-Link.
        lead_user.can_confirm_deletion = False
        lead_user.save()
        client.force_login(lead_user)
        content = client.get(reverse("core:deletion_request_list")).content.decode()
        assert "deletion-review-link" not in content
        # Bestätigerin sieht den Link.
        client.force_login(confirmer_staff)
        content = client.get(reverse("core:deletion_request_list")).content.decode()
        assert "deletion-review-link" in content

    def test_setup_guard_warns_when_no_eligible_reviewer(self, client, lead_user, event_qualified):
        # Einzige Person mit Recht ist die Antragstellerin selbst →
        # kein möglicher Genehmiger; der Antrag darf nicht still blocken.
        dr = request_deletion(event_qualified, lead_user, "Antrag")
        User.objects.exclude(pk=lead_user.pk).update(can_confirm_deletion=False)
        client.force_login(lead_user)
        content = client.get(reverse("core:deletion_request_list")).content.decode()
        assert "deletion-no-reviewer-warning" in content
        assert str(dr.pk) in content


@pytest.mark.django_db
class TestGrantRevokeAudit:
    def test_grant_and_revoke_are_audit_logged(self, staff_user):
        staff_user.can_confirm_deletion = True
        staff_user.save()
        grant = AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_CONFIRMER_CHANGED,
            target_id=str(staff_user.pk),
        ).latest("timestamp")
        assert grant.detail["new_value"] is True
        staff_user.can_confirm_deletion = False
        staff_user.save()
        revoke = AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_CONFIRMER_CHANGED,
            target_id=str(staff_user.pk),
        ).latest("timestamp")
        assert revoke.detail["new_value"] is False
        assert revoke.detail["old_value"] is True
